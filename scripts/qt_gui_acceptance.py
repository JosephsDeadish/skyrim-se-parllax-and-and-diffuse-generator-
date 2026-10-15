"""Opt-in acceptance driver for the application's real (including frozen) Qt GUI."""
from __future__ import annotations

from contextlib import contextmanager, redirect_stderr
from collections.abc import Sequence
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import traceback
import uuid

from PIL import Image, ImageDraw


SCENARIOS = (
    (800, 600, "0.9", False),
    (960, 700, "1.5", True),
    (900, 660, "1.15", False),
    (960, 700, "2.0", True),
)
SOURCE_DIMENSIONS = (512, 1024, 512, 2048, 1024, 512, 2048, 4096)
WINDOW_SIZE_TOLERANCE = 2


def _dimensions_within_tolerance(
    actual: Sequence[int],
    expected: Sequence[int],
    tolerance: int = WINDOW_SIZE_TOLERANCE,
) -> bool:
    return (
        tolerance >= 0
        and len(actual) == len(expected)
        and all(abs(actual_value - expected_value) <= tolerance
                for actual_value, expected_value in zip(actual, expected))
    )


def _write_source(path: Path, dimension: int, seed: int = 0) -> None:
    image = Image.new("RGB", (dimension, dimension), (31 + seed, 73, 121))
    draw = ImageDraw.Draw(image)
    for offset in range(0, dimension, max(1, dimension // 32)):
        draw.rectangle(
            (offset, offset // 2, min(dimension - 1, offset + dimension // 16), dimension - 1),
            fill=((offset + seed * 17) % 256, (offset // 3 + 83) % 256, 167),
        )
    image.save(path, format="DDS")
    image.close()


def _fingerprints(directory: Path) -> dict[str, dict[str, object]]:
    result = {}
    for path in sorted(directory.glob("*.dds")):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        result[path.name] = {
            "sha256": digest.hexdigest(),
            "mtime_ns": path.stat().st_mtime_ns,
            "bytes": path.stat().st_size,
        }
    return result


def _assert_unchanged(before: dict, after: dict) -> None:
    for name, evidence in before.items():
        if after.get(name) != evidence:
            raise AssertionError(f"Successful output was rewritten or removed: {name}")


def _assert_failure_sources(failures: list, expected_sources: list[str]) -> None:
    if [failure[0] for failure in failures] != expected_sources:
        raise AssertionError("Wrong source failed")


@contextmanager
def _isolated_gui_state(generator):
    # Only this explicit automation entry point bypasses user preferences.
    load, save = generator.load_gui_state, generator.save_gui_state
    generator.load_gui_state = lambda: dict(generator._GUI_STATE_DEFAULTS)
    generator.save_gui_state = lambda *args, **kwargs: None
    try:
        yield
    finally:
        generator.load_gui_state, generator.save_gui_state = load, save


def run_acceptance(generator, report_path: str | Path, *, timeout_seconds: float = 780) -> dict:
    """Drive native widgets and workers; return and persist evidence even on failure.

    ``generator`` must be the running application's module, not a second import
    of generate_textures (particularly important in a PyInstaller executable).
    """
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    root = report_path.parent / f"qt-acceptance-{uuid.uuid4().hex[:12]}"
    root.mkdir()
    screenshots = root / "screenshots"
    screenshots.mkdir()
    started = time.monotonic()
    report = {
        "passed": False, "status": "running", "frozen": bool(getattr(sys, "frozen", False)),
        "scope": "synthetic", "real_mod_samples": False,
        "executable": sys.executable, "scratch_root": str(root),
        "screenshots": [], "runs": {}, "qt_messages": [],
        "checkpoint": str(root / "checkpoint.json"),
        "source_dimensions": list(SOURCE_DIMENSIONS),
    }
    stderr = io.StringIO()
    controller = None
    app = None
    old_handler = None
    handler_installed = False
    old_font = old_palette = None
    try:
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        if not generator.QT_GUI_AVAILABLE:
            raise RuntimeError(f"Qt unavailable: {generator.QT_GUI_IMPORT_ERROR}")
        core, gui, widgets = generator.QtCore, generator.QtGui, generator.QtWidgets
        app = widgets.QApplication.instance() or widgets.QApplication([])
        generator._qt_application = app
        old_font, old_palette = gui.QFont(app.font()), gui.QPalette(app.palette())
        old_handler = core.qInstallMessageHandler(
            lambda kind, context, message: report["qt_messages"].append(message)
        )
        handler_installed = True
        source, output = root / "source", root / "output"
        source.mkdir()
        output.mkdir()
        for index, dimension in enumerate(SOURCE_DIMENSIONS):
            _write_source(source / f"stone{index:02d}.dds", dimension, index)
        corrupt = source / "zz_corrupt.dds"
        corrupt.write_bytes(b"invalid DDS acceptance source")
        checkpoint = root / "checkpoint.json"
        report["fixture_seconds"] = time.monotonic() - started

        class Controller(core.QObject):
            def __init__(self, window):
                super().__init__(window)
                self.window = window
                self.phase = "appearance"
                self.scenario = self.tab = 0
                self.prepared = False
                self.next_action = 0.0
                self.baseline_font = app.font().pointSizeF() / float(
                    generator._GUI_STATE_DEFAULTS["ui_scale"]
                )
                self.cancel_snapshot = {}
                self.valid_snapshot = {}
                self.all_snapshot = {}
                self.run_started = 0.0
                self.failure_started = None
                self.finished = False
                self.timer = core.QTimer(self)
                self.timer.setInterval(20)
                self.timer.timeout.connect(self.tick)
                self.timer.start()

            def widget(self, name):
                control = self.window.findChild(core.QObject, name)
                if control is None:
                    raise AssertionError(f"Missing actual Qt control: {name}")
                return control

            def require(self, condition, message):
                if not condition:
                    raise AssertionError(message)

            def checkpoint_payload(self):
                if not checkpoint.exists():
                    return {}
                try:
                    return json.loads(checkpoint.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    # The worker may currently be writing its real checkpoint.
                    return {}

            def capture(self, name, expected=None):
                path = screenshots / f"{name}.png"
                pixmap = self.window.grab()
                self.require(pixmap.save(str(path), "PNG"), f"Cannot save screenshot: {path}")
                size = [pixmap.width(), pixmap.height()]
                logical_size = [self.window.width(), self.window.height()]
                if expected:
                    ratio = pixmap.devicePixelRatio()
                    self.require(
                        _dimensions_within_tolerance(logical_size, expected),
                        f"Window could not reach requested size: {logical_size}, expected {expected} "
                        f"(tolerance {WINDOW_SIZE_TOLERANCE}px)",
                    )
                    self.require(
                        size == [round(value * ratio) for value in logical_size],
                        f"Screenshot dimensions {size} do not match actual window size {logical_size} "
                        f"at DPR {ratio}",
                    )
                with Image.open(path) as image:
                    self.require(list(image.size) == size, "PNG decode size mismatch")
                report["screenshots"].append({
                    "path": str(path), "dimensions": size,
                    "logical_dimensions": logical_size,
                    "logical_size": logical_size,
                    "requested_logical_size": list(expected) if expected else None,
                    "device_pixel_ratio": pixmap.devicePixelRatio(),
                    "tab": self.widget("tabs").tabText(self.widget("tabs").currentIndex()),
                    "tab_name": self.widget("tabs").tabText(self.widget("tabs").currentIndex()),
                    "tab_index": self.widget("tabs").currentIndex(),
                    "scale": float(self.widget("ui_scale").currentText()),
                    "font_points": app.font().pointSizeF(),
                    "dark": self.widget("theme").isChecked(),
                    "palette_window": self.window.palette().color(gui.QPalette.ColorRole.Window).name(),
                    "font_scale_validated": abs(
                        app.font().pointSizeF()
                        - self.baseline_font * float(self.widget("ui_scale").currentText())
                    ) < 0.02,
                    "theme_validated": self.window.palette().color(gui.QPalette.ColorRole.Window).name()
                    == ("#20242b" if self.widget("theme").isChecked() else "#f5f6f8"),
                })

            def inspect_tab(self, tabs, dark, scale):
                self.require(tabs.currentIndex() == self.tab, "Requested Qt tab was not activated")
                title = tabs.tabText(self.tab)
                self.require(tabs.currentWidget().isVisible(), f"Hidden tab: {title}")
                self.require(
                    abs(app.font().pointSizeF() - self.baseline_font * float(scale)) < 0.02,
                    "UI font scaling was not applied",
                )
                self.require(
                    self.window.palette().color(gui.QPalette.ColorRole.Window).name()
                    == ("#20242b" if dark else "#f5f6f8"),
                    "Qt theme palette was not applied",
                )
                controls = {
                    "Generation": (
                        "browse_input", "browse_input_folder", "browse_output",
                        "render_profile", "include_diffuse", "include_normal", "include_parallax",
                    ),
                    "NIF Editor": ("nif_texture_diffuse", "nif_dry_run", "nif_editor_patch"),
                    "Batch/Resume": ("checkpoint_path", "resume_mode"),
                }.get(title, ())
                reachability = []
                page = tabs.currentWidget()
                for name in controls:
                    control = self.widget(name)
                    self.require(control.isEnabled() and control.isVisible(), f"Unavailable {name}")
                    if isinstance(page, widgets.QScrollArea):
                        page.ensureWidgetVisible(control)
                        app.processEvents()
                        rect = control.rect()
                        center = control.mapTo(page.viewport(), rect.center())
                        self.require(page.viewport().rect().contains(center), f"Unreachable {name}")
                    reachability.append(name)
                if title in {"NIF Editor", "Generation"}:
                    self.require(isinstance(page, widgets.QScrollArea), f"{title} is not scrollable")
                    for bar in (page.verticalScrollBar(), page.horizontalScrollBar()):
                        bar.setValue(bar.maximum())
                        self.require(bar.value() == bar.maximum(), f"{title} cannot scroll")
                        bar.setValue(0)
                report.setdefault("appearance", []).append({
                    "tab": title, "scale": float(scale), "dark": dark,
                    "font_points": app.font().pointSizeF(), "reachable_controls": reachability,
                    "scroll_maximum": page.verticalScrollBar().maximum()
                    if isinstance(page, widgets.QScrollArea) else None,
                    "horizontal_scroll_maximum": page.horizontalScrollBar().maximum()
                    if isinstance(page, widgets.QScrollArea) else None,
                })

            def start_run(self, phase, resume):
                self.window.setProperty("generation_summary", None)
                self.widget("resume_mode").setCurrentText("resume" if resume else "start_fresh")
                self.run_started = time.monotonic()
                self.phase = phase
                self.widget("generate").click()
                self.require(not self.widget("generate").isEnabled(), "GUI did not start worker")
                self.require(self.widget("cancel_generation").isEnabled(), "Cancel unavailable")
                self.require(not self.widget("nif_editor_patch").isEnabled(), "Editor not interlocked")

            def summary(self):
                value = self.window.property("generation_summary")
                if not isinstance(value, dict) or not self.widget("generate").isEnabled():
                    return None
                report["runs"][self.phase] = dict(value, duration_seconds=time.monotonic() - self.run_started)
                return value

            def fail(self, exc):
                if self.failure_started is not None:
                    return
                report["error"] = str(exc)
                report["status"] = "fail"
                report["traceback"] = traceback.format_exc()
                report["failure_phase"] = self.phase
                report["failure_generation_summary"] = self.window.property("generation_summary")
                report["failure_nif_editor_summary"] = self.window.property("nif_editor_summary")
                report["failure_checkpoint"] = self.checkpoint_payload()
                self.failure_started = time.monotonic()
                self.phase = "drain"
                try:
                    self.capture("failure")
                except Exception:
                    pass
                self.widget("cancel_generation").click()
                self.widget("nif_editor_cancel").click()
                self.widget("nif_scan_cancel").click()
                report["duration_seconds"] = time.monotonic() - started
                report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

            def finish(self):
                # Do not destroy any native worker thread while it is running.
                threads = self.window.findChildren(core.QThread)
                if any(thread.isRunning() for thread in threads):
                    return
                for thread in threads:
                    thread.wait()
                self.finished = True
                self.timer.stop()
                self.window.close()
                app.quit()

            @core.Slot()
            def tick(self):
                try:
                    for dialog in app.topLevelWidgets():
                        if isinstance(dialog, widgets.QMessageBox) and dialog.isVisible():
                            text = dialog.text()
                            dialog.accept()
                            raise AssertionError(f"Unexpected native Qt dialog: {text}")
                    if self.phase == "drain":
                        self.finish()
                        return
                    self.require(time.monotonic() - started < timeout_seconds, "Qt acceptance timed out")
                    if time.monotonic() < self.next_action:
                        return
                    if self.phase == "appearance":
                        tabs = self.widget("tabs")
                        width, height, scale, dark = SCENARIOS[self.scenario]
                        if not self.prepared:
                            self.widget("theme").setChecked(dark)
                            self.widget("ui_scale").setCurrentText(scale)
                            self.window.resize(width, height)
                            tabs.setCurrentIndex(self.tab)
                            self.prepared = True
                            self.next_action = time.monotonic() + 0.15
                            return
                        self.inspect_tab(tabs, dark, scale)
                        self.capture(f"appearance-{self.scenario}-{self.tab}", (width, height))
                        self.tab += 1
                        self.prepared = False
                        if self.tab == tabs.count():
                            self.tab = 0
                            self.scenario += 1
                        if self.scenario < len(SCENARIOS):
                            return
                        self.window.resize(960, 700)
                        self.widget("ui_scale").setCurrentText("1.0")
                        for index in range(tabs.count()):
                            if tabs.tabText(index) == "Generation":
                                tabs.setCurrentIndex(index)
                        self.widget("input_path").setText(str(source))
                        self.widget("output_dir").setText(str(output))
                        self.widget("checkpoint_path").setText(str(checkpoint))
                        self.widget("render_profile").setCurrentText("custom")
                        for name in ("include_diffuse", "include_normal", "include_parallax", "continue_on_error"):
                            self.require(self.widget(name).isChecked(), f"Default {name} not checked")
                        self.start_run("cancel_requested", False)
                    elif self.phase == "cancel_requested":
                        payload = self.checkpoint_payload()
                        if payload.get("completed_success_count", 0) >= 1:
                            self.widget("cancel_generation").click()
                            report["cancellation_requested_at_count"] = payload["completed_success_count"]
                            self.capture("cancel-requested")
                            self.phase = "cancelled"
                    elif self.phase == "cancelled":
                        summary = self.summary()
                        if summary is None:
                            return
                        count = self.checkpoint_payload()["completed_success_count"]
                        self.require(summary["cancelled"] and 1 <= count <= 7, "Cancellation did not interrupt real batch")
                        self.require(summary["success"] == count, "Cancelled checkpoint/summary mismatch")
                        report["cancelled_checkpoint"] = self.checkpoint_payload()
                        self.cancel_snapshot = _fingerprints(output)
                        self.require(len(self.cancel_snapshot) == count * 3, "Cancelled output count mismatch")
                        report["cancelled_output_fingerprints"] = self.cancel_snapshot
                        self.capture("cancelled")
                        self.start_run("resume_corrupt", True)
                    elif self.phase == "resume_corrupt":
                        summary = self.summary()
                        if summary is None:
                            return
                        self.require(summary["failed"] == 1 and not summary["cancelled"], "Corrupt source failure missing")
                        self.require(summary["success"] + summary["resumed_completed"] == 8, "Expected eight valid successes")
                        self.require(self.checkpoint_payload()["completed_success_count"] == 8, "Failed DDS entered checkpoint")
                        _assert_failure_sources(summary["failures"], [str(corrupt)])
                        self.valid_snapshot = _fingerprints(output)
                        _assert_unchanged(self.cancel_snapshot, self.valid_snapshot)
                        self.require(len(self.valid_snapshot) == 24, "Expected 24 valid outputs")
                        report["valid_output_fingerprints"] = self.valid_snapshot
                        report["corrupt_checkpoint"] = self.checkpoint_payload()
                        self.capture("corrupt-resume")
                        _write_source(corrupt, 512, 9)
                        self.start_run("resume_repaired", True)
                    elif self.phase == "resume_repaired":
                        summary = self.summary()
                        if summary is None:
                            return
                        self.require(
                            summary["success"] == 1 and summary["failed"] == 0
                            and summary["resumed_completed"] == 8, "Repair resume counts incorrect",
                        )
                        self.require(self.checkpoint_payload()["completed_success_count"] == 9, "Repair checkpoint count incorrect")
                        self.all_snapshot = _fingerprints(output)
                        _assert_unchanged(self.valid_snapshot, self.all_snapshot)
                        self.require(len(self.all_snapshot) == 27, "Expected exactly 27 output DDS files")
                        expected_names = {
                            f"{path.stem}{suffix}.dds"
                            for path in source.glob("*.dds") for suffix in ("", "_n", "_p")
                        }
                        self.require(set(self.all_snapshot) == expected_names, "Unexpected DDS output roles")
                        self.require(
                            set(self.checkpoint_payload()["completed_success_files"])
                            == {str(path.resolve()) for path in source.glob("*.dds")},
                            "Checkpoint did not record exactly the nine actual input paths",
                        )
                        decoded = []
                        for path in sorted(output.glob("*.dds")):
                            dimension = 512 if path.stem.startswith("zz_corrupt") else SOURCE_DIMENSIONS[int(path.stem[5:7])]
                            with Image.open(path) as image:
                                image.load()
                                self.require(image.format == "DDS" and image.size == (dimension, dimension), f"Invalid output DDS: {path}")
                                decoded.append({"path": str(path), "dimensions": list(image.size), "format": image.format})
                        report["decoded_outputs"] = decoded
                        report["final_output_fingerprints"] = self.all_snapshot
                        report["final_checkpoint"] = self.checkpoint_payload()
                        self.capture("repair-resume")
                        self.start_run("resume_noop", True)
                    elif self.phase == "resume_noop":
                        summary = self.summary()
                        if summary is None:
                            return
                        self.require(
                            summary["resumed_completed"] == 9 and summary["total"] == 0
                            and summary["success"] == 0 and summary["failed"] == 0,
                            "No-op resume did not skip all nine sources",
                        )
                        _assert_unchanged(self.all_snapshot, _fingerprints(output))
                        report["outputs_not_rewritten"] = True
                        self.capture("noop-resume")
                        self.phase = "done"
                        self.finish()
                    elif self.phase == "done":
                        self.finish()
                except Exception as exc:
                    self.fail(exc)

        def ready(window):
            nonlocal controller
            try:
                controller = Controller(window)
            except Exception as exc:
                report["error"] = str(exc)
                report["traceback"] = traceback.format_exc()
                window.close()
                app.quit()

        with _isolated_gui_state(generator), redirect_stderr(stderr):
            generator._launch_qt_migration_gui(on_ready=ready)
        if controller is None or not controller.finished:
            raise AssertionError("Qt acceptance event loop exited before completing")
        bad_messages = [
            message for message in report["qt_messages"]
            if any(term in message.lower() for term in (
                "different thread", "another thread", "cannot create children", "qthread: destroyed",
                "exception", "callback",
            ))
        ]
        if bad_messages or stderr.getvalue():
            raise AssertionError(f"Qt callback/thread errors: {bad_messages}; {stderr.getvalue()}")
        report["passed"] = controller.phase == "done" and "error" not in report
    except Exception as exc:
        report.setdefault("error", str(exc))
        report.setdefault("traceback", traceback.format_exc())
    finally:
        if controller is not None:
            for name in ("cancel_generation", "nif_editor_cancel", "nif_scan_cancel"):
                controller.widget(name).click()
            for thread in controller.window.findChildren(generator.QtCore.QThread):
                thread.quit()
                thread.wait()
            controller.window.deleteLater()
            generator.QtCore.QCoreApplication.sendPostedEvents(
                None, generator.QtCore.QEvent.Type.DeferredDelete,
            )
        if app is not None:
            app.processEvents()
            app.setFont(old_font)
            app.setPalette(old_palette)
        if handler_installed:
            generator.QtCore.qInstallMessageHandler(old_handler)
        report["stderr"] = stderr.getvalue()
        report["duration_seconds"] = time.monotonic() - started
        report["status"] = "pass" if report["passed"] else "fail"
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report
