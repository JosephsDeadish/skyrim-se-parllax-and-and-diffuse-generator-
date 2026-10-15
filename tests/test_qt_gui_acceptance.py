from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import types
import unittest
from unittest import mock
import uuid

from PIL import Image

import generate_textures as gt
from scripts import qt_gui_acceptance as acceptance


class TestQtGuiAcceptance(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / f".qt-acceptance-test-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)

    def test_workload_includes_real_4k_and_nonflat_decodable_dds(self):
        self.assertEqual(len(acceptance.SOURCE_DIMENSIONS), 8)
        self.assertEqual(set(acceptance.SOURCE_DIMENSIONS), {512, 1024, 2048, 4096})
        path = self.root / "source.dds"
        acceptance._write_source(path, 512, 1)
        with Image.open(path) as image:
            image.load()
            self.assertEqual(image.format, "DDS")
            self.assertEqual(image.size, (512, 512))
            self.assertTrue(any(low != high for low, high in image.getextrema()))

    def test_fingerprints_detect_content_and_mtime_changes(self):
        path = self.root / "source.dds"
        acceptance._write_source(path, 32)
        before = acceptance._fingerprints(self.root)
        acceptance._assert_unchanged(before, acceptance._fingerprints(self.root))
        self.assertEqual(len(before["source.dds"]["sha256"]), 64)
        self.assertGreater(before["source.dds"]["mtime_ns"], 0)
        acceptance._write_source(path, 32, 1)
        with self.assertRaisesRegex(AssertionError, "rewritten"):
            acceptance._assert_unchanged(before, acceptance._fingerprints(self.root))
        with self.assertRaisesRegex(AssertionError, "removed"):
            acceptance._assert_unchanged(before, {})

    def test_gui_state_is_restored_even_after_failure(self):
        load, save = gt.load_gui_state, gt.save_gui_state
        with self.assertRaisesRegex(RuntimeError, "test"):
            with acceptance._isolated_gui_state(gt):
                self.assertEqual(gt.load_gui_state(), gt._GUI_STATE_DEFAULTS)
                state = gt.load_gui_state()
                state["input_path"] = "changed"
                self.assertEqual(gt.load_gui_state(), gt._GUI_STATE_DEFAULTS)
                gt.save_gui_state(state)
                raise RuntimeError("test")
        self.assertIs(gt.load_gui_state, load)
        self.assertIs(gt.save_gui_state, save)

    def test_failure_sources_match_exact_windows_and_posix_paths(self):
        for source in (r"C:\acceptance\source\zz_corrupt.dds",
                       "/acceptance/source/zz_corrupt.dds"):
            for failures in ([(source, "Invalid DDS")], [[source, "Invalid DDS"]]):
                with self.subTest(source=source, failures=failures):
                    acceptance._assert_failure_sources(failures, [source])

    def test_failure_sources_reject_wrong_empty_and_extra_failures(self):
        for source in (r"C:\acceptance\source\zz_corrupt.dds",
                       "/acceptance/source/zz_corrupt.dds"):
            for failures in (
                [],
                [[source + ".other", "Invalid DDS"]],
                [["other.dds", f"Error mentions {source}"]],
                [[source, "Invalid DDS"], ["other.dds", "Invalid DDS"]],
                [[source, "Invalid DDS"], [source, "Invalid DDS"]],
            ):
                with self.subTest(source=source, failures=failures):
                    with self.assertRaisesRegex(AssertionError, "Wrong source failed"):
                        acceptance._assert_failure_sources(failures, [source])

    def test_unavailable_qt_still_persists_failure_evidence(self):
        generator = types.SimpleNamespace(QT_GUI_AVAILABLE=False, QT_GUI_IMPORT_ERROR="missing Qt")
        path = self.root / "report.json"
        report = acceptance.run_acceptance(generator, path)
        self.assertFalse(report["passed"])
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["scope"], "synthetic")
        self.assertFalse(report["real_mod_samples"])
        self.assertIn("missing Qt", report["error"])
        self.assertGreater(report["duration_seconds"], 0)
        self.assertEqual(json.loads(path.read_text()), report)
        self.assertEqual(Path(report["scratch_root"]).parent, self.root)

    def test_acceptance_cli_dispatch_passes_current_module_and_exits(self):
        for passed, exit_code in ((True, 0), (False, 1)):
            with self.subTest(passed=passed), \
                 mock.patch.object(gt, "parse_args", return_value=argparse.Namespace(
                     qt_acceptance_report=self.root / "report.json",
                 )), \
                 mock.patch.object(acceptance, "run_acceptance", return_value={"passed": passed}) as runner, \
                 mock.patch.object(gt, "_apply_cli_pbr_overrides") as overrides, \
                 mock.patch.object(gt, "_launch_qt_migration_gui") as launch:
                self.assertEqual(gt.main(), exit_code)
                runner.assert_called_once_with(sys.modules[gt.__name__], self.root / "report.json")
                overrides.assert_not_called()
                launch.assert_not_called()

    def test_acceptance_option_is_explicit_and_optional(self):
        with mock.patch.object(sys, "argv", ["generate_textures.py"]):
            self.assertIsNone(gt.parse_args().qt_acceptance_report)
        with mock.patch.object(sys, "argv", [
            "generate_textures_qt.exe", "--qt-acceptance-report", "acceptance/report.json",
        ]):
            self.assertEqual(gt.parse_args().qt_acceptance_report, Path("acceptance/report.json"))

    def test_four_distinct_requested_appearance_scenarios(self):
        self.assertEqual(acceptance.SCENARIOS, (
            (800, 600, "0.9", False), (960, 700, "1.5", True),
            (900, 660, "1.15", False), (960, 700, "2.0", True),
        ))

    def test_window_size_tolerance_only_allows_two_pixel_layout_rounding(self):
        self.assertTrue(acceptance._dimensions_within_tolerance((902, 660), (900, 660)))
        self.assertFalse(acceptance._dimensions_within_tolerance((903, 660), (900, 660)))
        self.assertFalse(acceptance._dimensions_within_tolerance((900,), (900, 660)))

    @unittest.skipUnless(gt.QT_GUI_AVAILABLE, "Optional PySide6 unavailable")
    def test_actual_qt_timeout_records_screenshot_and_safely_exits(self):
        path = self.root / "timeout.json"
        with mock.patch.object(acceptance, "SOURCE_DIMENSIONS", (32,) * 8):
            report = acceptance.run_acceptance(gt, path, timeout_seconds=0.01)
        self.assertFalse(report["passed"])
        self.assertIn("timed out", report["error"])
        self.assertEqual(report["failure_phase"], "appearance")
        self.assertTrue(report["screenshots"])
        self.assertTrue(Path(report["screenshots"][-1]["path"]).is_file())
        self.assertFalse(any("qthread: destroyed" in message.lower() for message in report["qt_messages"]))
        self.assertEqual(json.loads(path.read_text()), report)


if __name__ == "__main__":
    unittest.main()
