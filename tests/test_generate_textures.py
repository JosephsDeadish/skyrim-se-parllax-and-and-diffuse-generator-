import csv
import json
import io
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from types import SimpleNamespace
from unittest import mock

import numpy as np
from PIL import Image, ImageOps, ImageStat

from generate_textures import (
    APP_VERSION,
    MODDING_WIKI_SKYRIM_URL,
    PATREON_URL,
    _create_panda_icon_image,
    _map_parallax_strength_to_nif_scale,
    _compute_tooltip_position,
    _compute_qt_initial_window_size,
    _compute_safe_startup_geometry,
    _candidate_nif_patcher_search_dirs,
    compute_effective_ui_scale,
    _compute_nif_editor_controls_pane_height,
    _compute_nif_editor_result_details_height,
    _drain_nif_editor_ui_updates,
    _make_batched_nif_result_enqueuer,
    _make_debounced_callback,
    _make_latest_progress_update_enqueuer,
    _connect_qt_worker_thread_lifecycle,
    _configure_qt_field_label,
    _nif_editor_result_matches_filters,
    _slice_nif_editor_result_page,
    _load_qt_preview_png,
    _recommend_qt_render_profile,
    _bind_tk_main_window_shortcuts,
    _batch_source_display_label,
    _describe_qt_output_channels,
    _choose_qt_input_path,
    _should_render_nif_editor_results,
    _set_nif_editor_controls_active,
    _format_nif_result_row_details,
    _should_refresh_qt_nif_scan_page,
    _format_qt_nif_scan_page_summary,
    _qt_nif_scan_status_color,
    _nif_editor_confirmation_title,
    _preview_qt_nif_auto_remediation,
    discover_plugin_conflict_context_from_manager,
    discover_ui_languages,
    extract_mesh_paths_from_plugin_bytes,
    extract_plugin_mesh_record_refs,
    load_ui_translations,
    _normalize_nif_editor_texture_input_path,
    _load_nif_patcher_exports,
    ModManagerContext,
    _normalize_render_profile,
    NIF_PATCHER_AVAILABLE,
    _normalize_nif_result_details,
    _preferred_dds_formats_for_output,
    _RENDER_PROFILE_GUI_VALUES,
    _run_cli,
    _normalize_gui_state,
    _merge_qt_generation_gui_state,
    _qt_profile_output_defaults,
    _qt_render_profile_modes,
    _qt_checkpoint_generation_options,
    _qt_resume_completed_files,
    _qt_resume_mode_guidance,
    _qt_truepbr_material_overrides,
    _apply_qt_profile_output_defaults,
    _QT_OUTPUT_GROUPS,
    _describe_qt_profile_guidance,
    _profile_setup_memory_items,
    _profile_setup_memory_key,
    _detect_renderer_version_hints,
    _format_short_guidance_tooltip,
    _render_profile_family_matrix_text,
    _render_profile_family_quick_check_text,
    _render_profile_family_tooltip,
    _render_profile_guidance,
    _format_log_export_text,
    _format_qt_preview_channel_note,
    _format_qt_log_export_text,
    _qt_migration_status_text,
    _resolve_app_icon_source_path,
    _nif_editor_confirmation_text,
    _nif_editor_safety_banner_text,
    compare_nif_backups,
    _workflow_preset_onboarding_text,
    _workflow_quick_start_steps_text,
    _bind_tk_quick_start_shortcuts,
    _build_truepbr_version_note,
    resolve_startup_guidance_action,
    _qt_preview_channel_options,
    _inspect_qt_output_image,
    _DARK_THEME,
    _HIGH_CONTRAST_THEME,
    _LIGHT_THEME,
    _THEME_MODE_LABELS,
    _resolve_batch_workers,
    _summarize_batch_texture_dimensions,
    _visible_preview_output_keys,
    build_batch_bottleneck_hints,
    summarize_batch_nif_patch_activity,
    _summarize_batch_source_folders,
    build_mod_manager_profile_setup_hint,
    build_startup_guidance_text,
    compute_checkpoint_mismatch_flags,
    compute_checkpoint_resume_counts,
    compute_processing_queue_event_budget,
    compute_deferred_preview_tile_interval_ms,
    compute_preview_refresh_delay_ms,
    _save_with_dds_fallback,
    _to_dds_compatible_image,
    TextureGeneratorGUI,
    analyze_image_content,
    auto_patch_related_nifs_for_texture,
    apply_recommendations_by_auto_flags,
    build_nif_patch_options_for_generated_outputs,
    build_nif_patch_options_for_nif_editor,
    build_mod_manager_context_hint,
    build_render_profile_brief_message,
    build_render_profile_recommendation_message,
    build_render_profile_family_mismatch_warning,
    build_complex_preview_image,
    build_complex_output_path,
    build_environment_mask_output_path,
    build_nif_editor_action_guidance,
    build_rmaos_output_path,
    classify_material_type,
    collect_source_textures,
    compute_wrapped_preview_index,
    ModManagerContext,
    describe_render_profile_default_outputs,
    describe_render_profile_files_to_create,
    detect_workflow_profile,
    detect_mod_manager_context,
    detect_render_profile_from_mod_manager_context,
    enforce_skyrim_output_profile,
    find_related_nif_files_for_texture,
    build_glow_output_path,
    build_normal_output_path,
    build_output_paths,
    generate_complex_material,
    generate_diffuse,
    generate_environment_mask,
    generate_environment_mask_for_workflow,
    generate_glow,
    generate_msn,
    generate_normal,
    generate_parallax,
    generate_parallax_occlusion,
    generate_preview_outputs,
    generate_specular,
    get_generation_warnings,
    get_nif_patch_option_warnings,
    get_preview_size_limits,
    identify_skyrim_texture_role,
    prepare_preview_source,
    parse_preview_jump_input,
    recommend_generation_settings,
    recommend_render_profile,
    recommend_render_profile_details,
    RenderProfileRecommendation,
    resolve_nif_patch_defaults_for_render_profile,
    resolve_render_profile_guardrails,
    resolve_render_profile_mode_control_states,
    resolve_render_profile_mode_selection,
    resolve_render_profile_output_defaults,
    resolve_render_profile_options,
    build_render_profile_mode_controls_hint,
    render_profile_has_locked_controls,
    restore_nif_backups,
    resolve_nif_editor_targets,
    _scan_nif_validation_rows,
    _write_qt_nif_scan_csv,
    _format_qt_nif_scan_detail,
    resolve_env_mask_complex_workflow,
    resolve_nif_editor_patch_options_for_target,
    load_gui_state,
    export_project_settings,
    export_project_settings_state,
    import_project_settings,
    main,
    parse_args,
    run_batch_with_options,
    run_with_options,
    resolve_lazy_preview_deferred_outputs,
    save_gui_state,
    should_apply_preview_recommendations,
    should_update_batch_status_line,
    should_update_live_batch_preview,
    select_generation_context_source,
    translate_ui_text,
    get_output_folder_format_warnings,
    write_batch_failure_artifacts,
    generate_ambient_occlusion,
    generate_roughness,
    analyze_material_type_from_image,
    _prefilter_for_mipmap_stability,
    generate_wetness_mask,
    generate_snow_mask,
    build_wetness_mask_output_path,
    build_snow_mask_output_path,
    build_ao_output_path,
    build_roughness_output_path,
    GENERATED_TEXTURE_SUFFIXES,
    recommend_output_resolution,
    write_rmaos_json_sidecar,
)


def _sample_image() -> Image.Image:
    return Image.new("RGB", (8, 8), color=(60, 100, 140))


class TestUiLocalizationAssetResolution(unittest.TestCase):
    def test_discover_ui_languages_uses_meipass_assets_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_root = Path(temp_dir)
            translations_dir = bundle_root / "assets" / "translations"
            translations_dir.mkdir(parents=True)
            (translations_dir / "de.json").write_text('{"strings": {}}', encoding="utf-8")
            (translations_dir / "en.json").write_text('{"strings": {}}', encoding="utf-8")
            with mock.patch("generate_textures.sys._MEIPASS", str(bundle_root), create=True):
                self.assertEqual(discover_ui_languages(), ("de", "en"))
                self.assertEqual(load_ui_translations("en"), {})


class TestNifPatcherLoading(unittest.TestCase):
    def test_candidate_nif_patcher_search_dirs_deduplicates(self) -> None:
        base = Path("/tmp/skyrim-texture-generator")
        self.assertEqual(
            _candidate_nif_patcher_search_dirs(
                script_path=base / "generate_textures.py",
                executable_path=base / "generate_textures.exe",
                meipass_path=base,
            ),
            (base,),
        )

    def test_load_nif_patcher_exports_from_explicit_search_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module_path = Path(temp_dir) / "nif_patcher.py"
            module_path.write_text(
                "\n".join(
                    [
                        "class NifPluginConflictRef:",
                        "    def __init__(self, plugin_name, record_id='', record_type=''):",
                        "        self.plugin_name = plugin_name",
                        "        self.record_id = record_id",
                        "        self.record_type = record_type",
                        "class NifPatchOptions: pass",
                        "def auto_remediate_nif_conflicts(*args, **kwargs): return (None, ())",
                        "def find_nif_files(*args, **kwargs): return []",
                        "def guess_cubemap_path_for_nif(*args, **kwargs): return None",
                        "def guess_env_mask_path_for_nif(*args, **kwargs): return None",
                        "def guess_glow_path_for_nif(*args, **kwargs): return None",
                        "def guess_normal_path_for_nif(*args, **kwargs): return None",
                        "def guess_parallax_path_for_nif(*args, **kwargs): return None",
                        "def patch_nif(*args, **kwargs): return None",
                        "def scan_nif(*args, **kwargs): return None",
                        "def summarize_plugin_aware_validation_conflicts(*args, **kwargs): return []",
                        "def summarize_validation_conflicts(*args, **kwargs): return []",
                        "def validate_nif_for_parallax(*args, **kwargs): return None",
                    ]
                ),
                encoding="utf-8",
            )
            original_sys_path = list(sys.path)
            try:
                sys.modules.pop("nif_patcher", None)
                exports = _load_nif_patcher_exports((Path(temp_dir),))
            finally:
                sys.modules.pop("nif_patcher", None)
                sys.path[:] = original_sys_path
            self.assertIn("patch_nif", exports)
            self.assertTrue(callable(exports["patch_nif"]))


class TestNifEditorLayoutSizing(unittest.TestCase):
    def test_controls_pane_preserves_large_controls_area(self) -> None:
        self.assertEqual(
            _compute_nif_editor_controls_pane_height(
                window_height=820,
                controls_requested_height=960,
                footer_height=72,
            ),
            568,
        )

    def test_controls_pane_keeps_minimum_height_when_window_is_tight(self) -> None:
        self.assertEqual(
            _compute_nif_editor_controls_pane_height(
                window_height=560,
                controls_requested_height=120,
                footer_height=80,
            ),
            340,
        )

    def test_result_details_height_leaves_room_for_results_list(self) -> None:
        self.assertEqual(
            _compute_nif_editor_result_details_height(
                results_height=300,
                details_requested_height=220,
            ),
            160,
        )


class TestNifEditorUiUpdateQueue(unittest.TestCase):
    def test_qt_worker_completion_and_failure_stop_thread_before_cleanup(self) -> None:
        class FakeSignal:
            def __init__(self) -> None:
                self.callbacks: list[object] = []

            def connect(self, callback: object) -> None:
                self.callbacks.append(callback)

            def emit(self) -> None:
                for callback in self.callbacks:
                    callback()  # type: ignore[operator]

        class FakeWorker:
            def __init__(self) -> None:
                self.finished = FakeSignal()
                self.failed = FakeSignal()
                self.deleted = False

            def deleteLater(self) -> None:
                self.deleted = True

        class FakeThread:
            def __init__(self) -> None:
                self.finished = FakeSignal()
                self.quit_requests = 0

            def quit(self) -> None:
                self.quit_requests += 1

        worker = FakeWorker()
        thread = FakeThread()
        cleanup: list[str] = []
        _connect_qt_worker_thread_lifecycle(worker, thread, lambda: cleanup.append("finished"))

        worker.finished.emit()
        worker.failed.emit()
        self.assertEqual(thread.quit_requests, 2)
        self.assertFalse(worker.deleted)
        self.assertEqual(cleanup, [])

        thread.finished.emit()
        self.assertTrue(worker.deleted)
        self.assertEqual(cleanup, ["finished"])

    def test_debounced_callback_runs_only_after_latest_request(self) -> None:
        scheduled: dict[int, tuple[int, object]] = {}
        callbacks: dict[int, object] = {}
        cancelled: list[int] = []
        calls: list[str] = []
        next_id = [0]

        def _schedule(delay_ms: int, callback: object) -> int:
            next_id[0] += 1
            scheduled[next_id[0]] = (delay_ms, callback)
            callbacks[next_id[0]] = callback
            return next_id[0]

        def _cancel(token: object) -> None:
            cancelled.append(int(token))
            scheduled.pop(int(token), None)

        request, cancel_pending = _make_debounced_callback(
            _schedule,
            _cancel,
            lambda: calls.append("render"),
            delay_ms=120,
        )

        request()
        request()

        self.assertEqual(cancelled, [1])
        self.assertEqual(list(scheduled), [2])
        self.assertEqual(scheduled[2][0], 120)
        self.assertEqual(calls, [])
        scheduled.pop(2)
        callbacks[1]()  # type: ignore[operator]
        self.assertEqual(calls, [])
        callbacks[2]()  # type: ignore[operator]
        self.assertEqual(calls, ["render"])

        request()
        cancel_pending()
        self.assertEqual(len(cancelled), 2)
        self.assertEqual(scheduled, {})

    def test_result_updates_batch_with_bounded_ui_work_and_defer_completion(self) -> None:
        updates: queue.Queue = queue.Queue()
        applied: list[tuple[str, str, str, int]] = []
        completion_threads: list[int] = []
        ui_thread_id = threading.get_ident()
        enqueue_row, after_rows = _make_batched_nif_result_enqueuer(
            updates,
            lambda rows: applied.extend((*row, threading.get_ident()) for row in rows),
            max_batch_size=25,
        )

        def _produce_rows() -> None:
            for index in range(1000):
                enqueue_row("OK", f"{index:04d}.nif", "scanned")
            after_rows(lambda: completion_threads.append(threading.get_ident()))

        worker = threading.Thread(target=_produce_rows)
        worker.start()
        worker.join()

        self.assertEqual(updates.qsize(), 1)
        self.assertEqual(_drain_nif_editor_ui_updates(updates, limit=1), 1)
        self.assertEqual(len(applied), 25)
        self.assertEqual(updates.qsize(), 1)
        self.assertEqual(completion_threads, [])

        while not updates.empty():
            self.assertEqual(_drain_nif_editor_ui_updates(updates, limit=1), 1)

        self.assertEqual([row[1] for row in applied], [f"{index:04d}.nif" for index in range(1000)])
        self.assertTrue(all(row[3] == ui_thread_id for row in applied))
        self.assertEqual(completion_threads, [ui_thread_id])

    def test_completion_without_pending_results_stays_queued(self) -> None:
        updates: queue.Queue = queue.Queue()
        completed: list[int] = []
        ui_thread_id = threading.get_ident()
        _enqueue_row, after_rows = _make_batched_nif_result_enqueuer(updates, lambda _rows: None)

        worker = threading.Thread(target=lambda: after_rows(lambda: completed.append(threading.get_ident())))
        worker.start()
        worker.join()

        self.assertEqual(completed, [])
        self.assertEqual(_drain_nif_editor_ui_updates(updates), 1)
        self.assertEqual(completed, [ui_thread_id])

    def test_progress_updates_coalesce_to_latest_value_on_ui_thread(self) -> None:
        updates: queue.Queue = queue.Queue()
        applied: list[tuple[float, int]] = []
        ui_thread_id = threading.get_ident()
        enqueue_progress = _make_latest_progress_update_enqueuer(
            updates,
            lambda value: applied.append((value, threading.get_ident())),
        )

        def _enqueue_from_worker() -> None:
            for value in range(1000):
                enqueue_progress(float(value))

        worker = threading.Thread(target=_enqueue_from_worker)
        worker.start()
        worker.join()

        self.assertEqual(updates.qsize(), 1)
        self.assertEqual(_drain_nif_editor_ui_updates(updates), 1)
        self.assertEqual(applied, [(999.0, ui_thread_id)])

    def test_drains_callbacks_in_fifo_batches_on_calling_thread(self) -> None:
        updates: queue.Queue = queue.Queue()
        completed: list[tuple[int, int]] = []
        ui_thread_id = threading.get_ident()

        def _enqueue_from_worker() -> None:
            for index in range(3):
                updates.put(lambda i=index: completed.append((i, threading.get_ident())))

        worker = threading.Thread(target=_enqueue_from_worker)
        worker.start()
        worker.join()

        self.assertEqual(_drain_nif_editor_ui_updates(updates, limit=2), 2)
        self.assertEqual([index for index, _ in completed], [0, 1])
        self.assertTrue(all(thread_id == ui_thread_id for _, thread_id in completed))
        self.assertEqual(_drain_nif_editor_ui_updates(updates), 1)
        self.assertEqual([index for index, _ in completed], [0, 1, 2])

    def test_callback_error_does_not_prevent_later_updates(self) -> None:
        updates: queue.Queue = queue.Queue()
        completed: list[str] = []
        errors: list[str] = []

        def _fail() -> None:
            raise RuntimeError("callback failed")

        updates.put(_fail)
        updates.put(lambda: completed.append("later"))

        drained = _drain_nif_editor_ui_updates(
            updates,
            on_error=lambda exc: errors.append(str(exc)),
        )

        self.assertEqual(drained, 2)
        self.assertEqual(errors, ["callback failed"])
        self.assertEqual(completed, ["later"])

    def test_results_redraw_periodically_during_backlog_and_immediately_when_drained(self) -> None:
        self.assertFalse(
            _should_render_nif_editor_results(
                dirty=True,
                queue_empty=False,
                seconds_since_render=0.1,
            )
        )
        self.assertTrue(
            _should_render_nif_editor_results(
                dirty=True,
                queue_empty=False,
                seconds_since_render=0.25,
            )
        )
        self.assertTrue(
            _should_render_nif_editor_results(
                dirty=True,
                queue_empty=True,
                seconds_since_render=0.0,
            )
        )
        self.assertFalse(
            _should_render_nif_editor_results(
                dirty=False,
                queue_empty=False,
                seconds_since_render=1.0,
            )
        )

    def test_result_filters_match_status_and_search_fields(self) -> None:
        row = {
            "status": "WARN",
            "file_name": "stone01.nif",
            "details": "Missing parallax",
            "preview": "needs patch",
        }
        self.assertTrue(
            _nif_editor_result_matches_filters(
                row,
                keyword="STONE01",
                status_filter="ALL",
            )
        )
        self.assertTrue(
            _nif_editor_result_matches_filters(
                row,
                keyword="parallax",
                status_filter="WARN",
            )
        )
        self.assertFalse(
            _nif_editor_result_matches_filters(
                row,
                keyword="",
                status_filter="FAIL",
            )
        )
        self.assertFalse(
            _nif_editor_result_matches_filters(
                row,
                keyword="absent",
                status_filter="ALL",
            )
        )

    def test_result_pages_slice_and_clamp_indices(self) -> None:
        rows = [{"file_name": f"mesh-{index}.nif"} for index in range(5)]

        page, page_index, page_count = _slice_nif_editor_result_page(rows, 1, page_size=2)

        self.assertEqual([row["file_name"] for row in page], ["mesh-2.nif", "mesh-3.nif"])
        self.assertEqual((page_index, page_count), (1, 3))

        last_page, page_index, page_count = _slice_nif_editor_result_page(rows, 99, page_size=2)
        self.assertEqual([row["file_name"] for row in last_page], ["mesh-4.nif"])
        self.assertEqual((page_index, page_count), (2, 3))

    def test_empty_results_have_single_empty_page_and_size_is_at_least_one(self) -> None:
        page, page_index, page_count = _slice_nif_editor_result_page([], -1, page_size=0)
        self.assertEqual(page, [])
        self.assertEqual((page_index, page_count), (0, 1))


class TestNifEditorOperationControlStates(unittest.TestCase):
    class FakeControl:
        def __init__(self, state: str) -> None:
            self.state = state

        def cget(self, option: str) -> str:
            if option != "state":
                raise ValueError(option)
            return self.state

        def configure(self, *, state: str) -> None:
            self.state = state

    def test_disables_controls_and_restores_each_original_state(self) -> None:
        controls = [
            self.FakeControl("normal"),
            self.FakeControl("readonly"),
            self.FakeControl("disabled"),
        ]
        saved_states: dict[object, str] = {}

        _set_nif_editor_controls_active(controls, saved_states, active=False)
        self.assertEqual([control.state for control in controls], ["disabled"] * 3)

        _set_nif_editor_controls_active(controls, saved_states, active=True)
        self.assertEqual([control.state for control in controls], ["normal", "readonly", "disabled"])
        self.assertEqual(saved_states, {})


class TestNifEditorTargetResolution(unittest.TestCase):
    def test_single_file_mode_rejects_folder_even_if_it_contains_nifs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "mesh.nif").write_bytes(b"nif")
            targets, error = resolve_nif_editor_targets(str(root), "file")

        self.assertEqual(targets, [])
        self.assertIn("Single NIF mode", str(error))

    def test_folder_mode_rejects_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            nif_path = Path(temp_dir) / "mesh.nif"
            nif_path.write_bytes(b"nif")
            targets, error = resolve_nif_editor_targets(str(nif_path), "folder")

        self.assertEqual(targets, [])
        self.assertIn("Folder mode", str(error))

    def test_single_file_mode_accepts_existing_nif(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            nif_path = Path(temp_dir) / "mesh.nif"
            nif_path.write_bytes(b"nif")
            targets, error = resolve_nif_editor_targets(str(nif_path), "file")

        self.assertEqual(targets, [nif_path])
        self.assertIsNone(error)

    def test_folder_mode_finds_nifs_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "nested"
            nested.mkdir()
            (root / "root.nif").write_bytes(b"nif")
            (nested / "child.nif").write_bytes(b"nif")
            targets, error = resolve_nif_editor_targets(str(root), "folder")

        self.assertEqual(set(targets), {root / "root.nif", nested / "child.nif"})
        self.assertIsNone(error)

    def test_missing_target_returns_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            targets, error = resolve_nif_editor_targets(str(Path(temp_dir) / "missing.nif"), "file")

        self.assertEqual(targets, [])
        self.assertIn("does not exist", str(error))


class TestQtNifValidationScan(unittest.TestCase):
    def test_scan_status_colors_are_theme_aware(self) -> None:
        self.assertEqual(_qt_nif_scan_status_color("OK", dark_mode=True), "#7ee2a8")
        self.assertEqual(_qt_nif_scan_status_color("warn", dark_mode=False), "#8a5a00")
        self.assertEqual(
            _qt_nif_scan_status_color("PROBABLY INCOMPATIBLE", dark_mode=True),
            "#f97316",
        )
        self.assertEqual(_qt_nif_scan_status_color("SKIP", dark_mode=True), "#b2bac5")
        self.assertEqual(_qt_nif_scan_status_color("FAIL", dark_mode=False), "#b42318")
        self.assertIsNone(_qt_nif_scan_status_color("UNKNOWN", dark_mode=False))

    def test_scan_page_summary_counts_filtered_statuses(self) -> None:
        summary = _format_qt_nif_scan_page_summary(
            [
                {"status": "OK"},
                {"status": "warn"},
                {"status": "WARN"},
                {"status": "PROBABLY INCOMPATIBLE"},
                {"status": "SKIP"},
                {"status": "FAIL"},
                {"status": "UNKNOWN"},
            ],
            1,
            3,
        )
        self.assertEqual(
            summary,
            "Page 2 of 3 · 7 result(s) · OK 1, WARN 2, PROBABLY INCOMPATIBLE 1, SKIP 1, FAIL 1",
        )

    def test_scan_page_summary_handles_empty_filtered_results(self) -> None:
        self.assertEqual(
            _format_qt_nif_scan_page_summary([], 0, 0),
            "Page 1 of 1 · 0 result(s)",
        )

    def test_scan_page_refresh_cadence_matches_page_size(self) -> None:
        self.assertFalse(_should_refresh_qt_nif_scan_page(0))
        self.assertFalse(_should_refresh_qt_nif_scan_page(25))
        self.assertFalse(_should_refresh_qt_nif_scan_page(199))
        self.assertTrue(_should_refresh_qt_nif_scan_page(200))
        self.assertTrue(_should_refresh_qt_nif_scan_page(400))
        self.assertTrue(_should_refresh_qt_nif_scan_page(8, page_size=4))

    def test_remediation_preview_reports_non_destructive_steps_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            nif_path = Path(temp_dir) / "mesh.nif"
            nif_path.write_bytes(b"fixture")
            validation = SimpleNamespace(
                conflict_report=[
                    SimpleNamespace(code="shader_state.envmap_missing_slot5"),
                    SimpleNamespace(code="shader_state.envmap_missing_slot5"),
                ]
            )
            patch_result = SimpleNamespace(
                message="Dry run complete.",
                warnings=["No texture path could be guessed."],
                errors=[],
            )
            with (
                mock.patch("generate_textures.NIF_PATCHER_AVAILABLE", True),
                mock.patch("generate_textures.validate_nif_for_parallax", return_value=validation),
                mock.patch(
                    "generate_textures.auto_remediate_nif_conflicts",
                    return_value=(patch_result, ("Would disable environment mapping.",)),
                ) as remediate,
            ):
                preview = _preview_qt_nif_auto_remediation(nif_path)

        self.assertIn("Read-only remediation preview", preview)
        self.assertIn("No NIF file was written.", preview)
        self.assertIn("shader_state.envmap_missing_slot5", preview)
        self.assertEqual(preview.count("Conflict:"), 1)
        self.assertIn("What this file needs:", preview)
        self.assertIn("Safe fix path:", preview)
        self.assertIn("Rollback path:", preview)
        self.assertIn("Rollback path: no sibling .nif.bak backup found", preview)
        self.assertIn("Would disable environment mapping.", preview)
        self.assertIn("Dry run complete.", preview)
        remediate.assert_called_once_with(
            nif_path,
            ["shader_state.envmap_missing_slot5"],
            target_game="auto",
            experimental_fallout_write=False,
            allow_destructive=False,
            backup=False,
            dry_run=True,
        )

    def test_remediation_preview_reports_when_no_conflicts_exist(self) -> None:
        with (
            mock.patch("generate_textures.NIF_PATCHER_AVAILABLE", True),
            mock.patch(
                "generate_textures.validate_nif_for_parallax",
                return_value=SimpleNamespace(conflict_report=[]),
            ),
            mock.patch("generate_textures.auto_remediate_nif_conflicts") as remediate,
        ):
            preview = _preview_qt_nif_auto_remediation("mesh.nif")

        self.assertIn("No conflict-code remediation is available", preview)
        self.assertIn("What this file needs:", preview)
        self.assertIn("Rollback path:", preview)
        self.assertIn("Rollback path: no sibling .nif.bak backup found", preview)
        remediate.assert_not_called()

    def test_remediation_preview_reports_when_no_safe_steps_are_proposed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            nif_path = Path(temp_dir) / "mesh.nif"
            with (
                mock.patch("generate_textures.NIF_PATCHER_AVAILABLE", True),
                mock.patch(
                    "generate_textures.validate_nif_for_parallax",
                    return_value=SimpleNamespace(
                        conflict_report=[SimpleNamespace(code="shader_state.unknown")]
                    ),
                ),
                mock.patch(
                    "generate_textures.auto_remediate_nif_conflicts",
                    return_value=(None, ()),
                ),
            ):
                preview = _preview_qt_nif_auto_remediation(nif_path)

        self.assertIn("No non-destructive automatic remediation is available.", preview)
        self.assertIn("Safe fix path:", preview)
        self.assertIn("Rollback path:", preview)
        self.assertIn("Rollback path: no sibling .nif.bak backup found", preview)

    def test_remediation_preview_requires_nif_patcher(self) -> None:
        with mock.patch("generate_textures.NIF_PATCHER_AVAILABLE", False):
            with self.assertRaisesRegex(RuntimeError, "NIF patcher is unavailable"):
                _preview_qt_nif_auto_remediation("mesh.nif")

    def test_scan_selected_result_details_include_full_path_and_diagnostics(self) -> None:
        details = _format_qt_nif_scan_detail(
            {
                "status": "WARN",
                "file_name": "mesh.nif",
                "preview": "C:/mods/example/meshes/mesh.nif",
                "details": "Issue: missing texture slot\nConflict: shader_state.flag",
            }
        )
        self.assertIn("Status: WARN", details)
        self.assertIn("C:/mods/example/meshes/mesh.nif", details)
        self.assertIn("Issue: missing texture slot\nConflict: shader_state.flag", details)

    def test_scan_validation_rows_include_file_needs_and_fix_paths(self) -> None:
        validation = SimpleNamespace(
            shader_count=2,
            ready_count=1,
            needs_patch_count=1,
            skip_count=0,
            detected_game_profile="skyrim",
            has_havok=False,
            skip_reasons=["Block 0: skinned/animated mesh (NiSkinInstance) — parallax on skinned geometry causes CTD in Skyrim SE"],
            issues=["Block 1: Texture slot 5 is filled ('textures\\architecture\\stone_m.dds') but SLSF1_Environment_Mapping is not enabled."],
            suggestions=["Enable environment mapping in BSLightingShaderProperty or clear slot 5 if this mesh should not be reflective."],
            renderer_verdicts={
                "vanilla": "Vanilla parallax: Mesh-side setup looks ready.",
                "enb": "ENB complex material/POM: Won't work yet: slot 5 is missing.",
                "community_shaders": "Community Shaders parallax: Mesh-side setup looks ready.",
                "truepbr": "TruePBR: Mesh-side setup looks ready, but TruePBR still needs a matching JSON entry that this scan cannot verify.",
            },
            conflict_report=[
                SimpleNamespace(
                    code="shader_state.envmap_missing_slot5",
                    count=2,
                    game_profile="skyrim",
                    shader_layout="real",
                    examples=("Block 1: slot 5 is filled without environment mapping.",),
                    suggested_actions=("Enable environment mapping", "Use a matching _m.dds mask."),
                )
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            nif_path = Path(temp_dir) / "mesh.nif"
            nif_path.write_bytes(b"fixture")
            nif_path.with_suffix(".nif.bak").write_bytes(b"backup")
            with mock.patch("generate_textures.validate_nif_for_parallax", return_value=validation):
                rows, cancelled = _scan_nif_validation_rows([nif_path])

        self.assertFalse(cancelled)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "WARN")
        details = rows[0]["details"]
        self.assertIn("What this file needs:", details)
        self.assertIn("Slot / workflow state:", details)
        self.assertIn("Shader-family fit:", details)
        self.assertIn("Probably incompatible", details)
        self.assertIn("Safe fix path:", details)
        self.assertIn("Experimental fix path:", details)
        self.assertIn("Grouped conflict codes:", details)
        self.assertIn("examples:", details)
        self.assertIn("fix actions:", details)
        self.assertIn("Rollback path: sibling backup found", details)

    def test_scan_validation_rows_mark_fully_ready_uncertain_results_as_probably_incompatible(self) -> None:
        validation = SimpleNamespace(
            shader_count=1,
            ready_count=1,
            needs_patch_count=0,
            skip_count=0,
            detected_game_profile="skyrim",
            has_havok=False,
            skip_reasons=[],
            issues=[],
            suggestions=[],
            renderer_verdicts={
                "truepbr": "TruePBR: Mesh-side setup looks ready, but TruePBR still needs a matching JSON entry that this scan cannot verify.",
            },
            conflict_report=[],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            nif_path = Path(temp_dir) / "mesh.nif"
            nif_path.write_bytes(b"fixture")
            with mock.patch("generate_textures.validate_nif_for_parallax", return_value=validation):
                rows, cancelled = _scan_nif_validation_rows([nif_path])

        self.assertFalse(cancelled)
        self.assertEqual(rows[0]["status"], "PROBABLY INCOMPATIBLE")
        self.assertIn("Probably incompatible", rows[0]["details"])
        self.assertIn("Rollback path: no sibling .nif.bak backup found", rows[0]["details"])

    def test_scan_selected_result_details_have_empty_selection_guidance(self) -> None:
        self.assertIn("Select a scan result", _format_qt_nif_scan_detail(None))

    def test_scan_csv_export_round_trips_full_diagnostics_and_paths(self) -> None:
        rows = [
            {
                "status": "WARN",
                "file_name": "mesh, variant.nif",
                "preview": "/mods/mesh, variant.nif",
                "details": "Issue: missing slot\nConflict: shader_state.flag",
                "extra": "ignored",
            }
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "scan.csv"
            exported_count = _write_qt_nif_scan_csv(csv_path, rows)
            with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                exported_rows = list(csv.DictReader(csv_file))

        self.assertEqual(exported_count, 1)
        self.assertEqual(
            exported_rows,
            [
                {
                    "status": "WARN",
                    "file_name": "mesh, variant.nif",
                    "preview": "/mods/mesh, variant.nif",
                    "details": "Issue: missing slot\nConflict: shader_state.flag",
                }
            ],
        )

    def test_scan_csv_export_writes_headers_for_empty_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "empty.csv"
            self.assertEqual(_write_qt_nif_scan_csv(csv_path, []), 0)
            with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                reader = csv.reader(csv_file)
                self.assertEqual(next(reader), ["status", "file_name", "preview", "details"])
                self.assertEqual(list(reader), [])

    def test_scan_rows_classify_results_and_include_diagnostic_context(self) -> None:
        validations = [
            SimpleNamespace(
                shader_count=2,
                ready_count=2,
                skip_count=0,
                detected_game_profile="fallout4",
                has_havok=False,
                issues=[],
                conflict_report=[SimpleNamespace(code="shader_state.flag", count=1)],
            ),
            SimpleNamespace(
                shader_count=2,
                ready_count=1,
                skip_count=0,
                detected_game_profile="skyrim",
                has_havok=True,
                issues=["missing parallax slot"],
                conflict_report=[],
            ),
            SimpleNamespace(
                shader_count=0,
                ready_count=0,
                skip_count=0,
                detected_game_profile="unknown",
                has_havok=False,
                issues=[],
                conflict_report=[],
            ),
        ]
        progress: list[tuple[int, int, str]] = []
        nif_paths = [Path("ready.nif"), Path("warn.nif"), Path("empty.nif"), Path("bad.nif")]
        with mock.patch(
            "generate_textures.validate_nif_for_parallax",
            side_effect=[*validations, OSError("corrupt mesh")],
        ):
            rows, cancelled = _scan_nif_validation_rows(
                nif_paths,
                on_progress=lambda *values: progress.append(values),
            )
        self.assertFalse(cancelled)
        self.assertEqual([row["status"] for row in rows], ["OK", "WARN", "SKIP", "FAIL"])
        self.assertIn("Detected profile: fallout4", rows[0]["details"])
        self.assertIn("Conflict shader_state.flag: 1", rows[0]["details"])
        self.assertIn("Havok animation graph", rows[1]["details"])
        self.assertIn("missing parallax slot", rows[1]["details"])
        self.assertIn("No BSLightingShaderProperty", rows[2]["details"])
        self.assertIn("corrupt mesh", rows[3]["details"])
        self.assertEqual(len(progress), len(nif_paths))
        self.assertEqual(progress[-1][:2], (4, 4))

    def test_scan_can_cancel_after_current_mesh_and_preserve_partial_rows(self) -> None:
        cancelled = [False]
        emitted: list[dict[str, str]] = []
        progress: list[tuple[int, int, str]] = []
        validation = SimpleNamespace(
            shader_count=1,
            ready_count=1,
            skip_count=0,
            detected_game_profile="skyrim",
            has_havok=False,
            issues=[],
            conflict_report=[],
        )

        def request_cancel_after_row(row: dict[str, str]) -> None:
            emitted.append(row)
            cancelled[0] = True

        with mock.patch("generate_textures.validate_nif_for_parallax", return_value=validation):
            rows, was_cancelled = _scan_nif_validation_rows(
                [Path("one.nif"), Path("two.nif"), Path("three.nif")],
                is_cancelled=lambda: cancelled[0],
                on_progress=lambda *values: progress.append(values),
                on_row=request_cancel_after_row,
            )
        self.assertTrue(was_cancelled)
        self.assertEqual([row["file_name"] for row in rows], ["one.nif"])
        self.assertEqual(emitted, rows)
        self.assertEqual(len(progress), 1)


def _flat_dark_image() -> Image.Image:
    return Image.new("RGB", (16, 16), color=(28, 28, 28))


def _detailed_bright_image() -> Image.Image:
    image = Image.new("RGB", (16, 16))
    pixels = image.load()
    for y in range(16):
        for x in range(16):
            v = 245 if (x + y) % 2 == 0 else 35
            pixels[x, y] = (v, v, v)
    return image


def _uniform_bright_image() -> Image.Image:
    return Image.new("RGB", (24, 24), color=(220, 220, 220))


def _vertical_gradient_image() -> Image.Image:
    image = Image.new("RGB", (24, 24))
    pixels = image.load()
    for y in range(24):
        value = int((y / 23) * 255)
        for x in range(24):
            pixels[x, y] = (value, value, value)
    return image


def _sparse_highlight_image() -> Image.Image:
    image = Image.new("RGB", (24, 24), color=(20, 20, 20))
    pixels = image.load()
    for y in range(0, 24, 6):
        for x in range(0, 24, 6):
            pixels[x, y] = (255, 255, 255)
    return image


def _high_saturation_image() -> Image.Image:
    return Image.new("RGB", (24, 24), color=(245, 40, 40))


def _detailed_low_saturation_image() -> Image.Image:
    image = Image.new("RGB", (24, 24))
    pixels = image.load()
    for y in range(24):
        for x in range(24):
            v = 230 if (x + y) % 2 == 0 else 18
            pixels[x, y] = (v, v, v)
    return image


def _large_high_detail_image() -> Image.Image:
    image = Image.new("RGB", (512, 512))
    pixels = image.load()
    for y in range(512):
        for x in range(512):
            value = ((x * 37) + (y * 91) + ((x * y) % 251)) % 256
            pixels[x, y] = (value, (value * 3) % 256, (255 - value))
    return image


def _normal_like_image() -> Image.Image:
    image = Image.new("RGB", (24, 24))
    pixels = image.load()
    for y in range(24):
        for x in range(24):
            r = 120 + ((x + y) % 12)
            g = 120 + ((x * 2 + y) % 12)
            b = 210 + ((x * y) % 25)
            pixels[x, y] = (r, g, b)
    return image


def _emboss_pattern_image() -> Image.Image:
    image = Image.new("RGB", (48, 48), color=(132, 132, 132))
    pixels = image.load()
    for y in range(8, 40):
        for x in range(8, 40):
            if x in (8, 16, 24, 32, 39) or y in (8, 16, 24, 32, 39):
                pixels[x, y] = (215, 215, 215)
            elif (x + y) % 7 == 0:
                pixels[x, y] = (95, 95, 95)
    return image


def _color_sign_image() -> Image.Image:
    image = Image.new("RGB", (48, 48), color=(255, 0, 0))
    pixels = image.load()
    for y in range(6, 42):
        for x in range(6, 42):
            if x in (6, 41) or y in (6, 41):
                pixels[x, y] = (0, 129, 0)
            elif 16 <= x <= 31 and 16 <= y <= 31:
                pixels[x, y] = (0, 129, 0)
    return image


def _shield_art_image() -> Image.Image:
    image = Image.new("RGB", (64, 64), color=(90, 40, 30))
    pixels = image.load()
    centre = 32
    for y in range(64):
        for x in range(64):
            dx = x - centre
            dy = y - centre
            distance_sq = (dx * dx) + (dy * dy)
            if distance_sq <= 26 * 26:
                pixels[x, y] = (140, 110, 58)
            if distance_sq <= 18 * 18 and abs(dx) <= 2:
                pixels[x, y] = (200, 28, 28)
            if distance_sq <= 18 * 18 and abs(dy) <= 2:
                pixels[x, y] = (230, 220, 175)
            if 9 * 9 <= distance_sq <= 11 * 11:
                pixels[x, y] = (30, 30, 30)
    return image


class GenerateTexturesTests(unittest.TestCase):
    def test_compute_safe_startup_geometry_scales_to_screen_size(self) -> None:
        default_width, default_height, min_width, min_height = _compute_safe_startup_geometry(1920, 1080)
        self.assertEqual(default_width, 960)
        self.assertEqual(default_height, 700)
        self.assertEqual(min_width, 720)
        self.assertEqual(min_height, 520)

        narrow_width, narrow_height, narrow_min_width, narrow_min_height = _compute_safe_startup_geometry(800, 600)
        self.assertEqual(narrow_width, 720)
        self.assertEqual(narrow_height, 520)
        self.assertEqual(narrow_min_width, 720)
        self.assertEqual(narrow_min_height, 520)
        self.assertLessEqual(narrow_width, 800)
        self.assertLessEqual(narrow_height, 600)
        self.assertLessEqual(narrow_min_width, narrow_width)
        self.assertLessEqual(narrow_min_height, narrow_height)

    def test_qt_field_label_adds_mnemonic_and_focus_buddy(self) -> None:
        class FakeLabel:
            text = ""
            buddy = None

            def setText(self, text: str) -> None:
                self.text = text

            def setBuddy(self, control: object) -> None:
                self.buddy = control

        label = FakeLabel()
        control = object()
        _configure_qt_field_label(label, "&Input file or folder", control)
        self.assertEqual(label.text, "&Input file or folder")
        self.assertIs(label.buddy, control)

    def test_qt_output_groups_cover_each_map_choice_once(self) -> None:
        flattened = [key for _, keys in _QT_OUTPUT_GROUPS for key in keys]
        self.assertEqual(len(flattened), 11)
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertEqual(
            set(flattened),
            {
                "diffuse",
                "normal",
                "parallax",
                "glow",
                "environment_mask",
                "rmaos",
                "complex",
                "wetness_mask",
                "snow_mask",
                "ao",
                "roughness",
            },
        )
        self.assertEqual(_QT_OUTPUT_GROUPS[0][0], "Core maps")
        self.assertIn("standalone", _QT_OUTPUT_GROUPS[1][0].lower())

    def test_qt_input_picker_routes_file_and_folder_actions_and_preserves_cancel(self) -> None:
        selections = []
        applied = []

        result = _choose_qt_input_path(
            select_file=lambda: selections.append("file") or "/textures/a.dds",
            select_folder=lambda: selections.append("folder") or "/textures/batch",
            set_path=applied.append,
        )
        self.assertTrue(result)
        self.assertEqual(selections, ["file"])
        self.assertEqual(applied, ["/textures/a.dds"])

        result = _choose_qt_input_path(
            select_file=lambda: selections.append("unexpected file") or "",
            select_folder=lambda: selections.append("folder") or "/textures/batch",
            set_path=applied.append,
            folder=True,
        )
        self.assertTrue(result)
        self.assertEqual(selections[-1], "folder")
        self.assertEqual(applied[-1], "/textures/batch")

        result = _choose_qt_input_path(
            select_file=lambda: "",
            select_folder=lambda: "",
            set_path=applied.append,
            folder=True,
        )
        self.assertFalse(result)
        self.assertEqual(applied, ["/textures/a.dds", "/textures/batch"])

    def _skip_unavailable_tk_display(self) -> None:
        if not hasattr(TextureGeneratorGUI, "_apply_persisted_gui_state"):
            self.skipTest("Tk GUI is unavailable in this environment.")
        if sys.platform not in {"win32", "darwin"} and not os.environ.get("DISPLAY"):
            self.skipTest("Tk GUI tests require a display.")

    def test_app_version_is_0_9(self) -> None:
        self.assertEqual(APP_VERSION, "0.9")

    def test_tk_main_shortcuts_dispatch_actions_and_cancel_only_during_processing(self) -> None:
        class FakeRoot:
            def __init__(self) -> None:
                self.bindings = {}

            def bind(self, sequence, callback, add=None) -> None:
                self.bindings[sequence] = callback

        root = FakeRoot()
        calls = []
        processing = [False]
        _bind_tk_main_window_shortcuts(
            root,
            open_file=lambda: calls.append("file"),
            open_folder=lambda: calls.append("folder"),
            generate=lambda: calls.append("generate"),
            cancel=lambda: calls.append("cancel"),
            is_processing=lambda: processing[0],
        )
        self.assertEqual(set(root.bindings), {
            "<Control-o>",
            "<Control-Shift-o>",
            "<Control-Return>",
            "<Escape>",
        })
        for sequence, expected_action in (
            ("<Control-o>", "file"),
            ("<Control-Shift-o>", "folder"),
            ("<Control-Return>", "generate"),
        ):
            self.assertEqual(root.bindings[sequence](object()), "break")
            self.assertEqual(calls[-1], expected_action)

        self.assertEqual(root.bindings["<Escape>"](object()), "break")
        self.assertNotIn("cancel", calls)
        processing[0] = True
        self.assertEqual(root.bindings["<Escape>"](object()), "break")
        self.assertEqual(calls[-1], "cancel")

    def test_tk_quick_start_shortcuts_dispatch_safe_preset_and_hide_actions(self) -> None:
        class FakeRoot:
            def __init__(self) -> None:
                self.bindings = {}

            def bind(self, sequence, callback, add=None) -> None:
                self.bindings[sequence] = callback

        root = FakeRoot()
        calls = []
        _bind_tk_quick_start_shortcuts(
            root,
            apply_preset=lambda profile_key: calls.append(profile_key),
            dismiss=lambda: calls.append("dismiss"),
        )
        self.assertEqual(set(root.bindings), {
            "<Control-1>",
            "<Control-2>",
            "<Control-3>",
            "<Control-4>",
            "<Control-Shift-h>",
        })
        for sequence, expected_action in (
            ("<Control-1>", "vanilla"),
            ("<Control-2>", "enb"),
            ("<Control-3>", "community_shaders"),
            ("<Control-4>", "truepbr"),
        ):
            self.assertEqual(root.bindings[sequence](object()), "break")
            self.assertEqual(calls[-1], expected_action)

        self.assertEqual(root.bindings["<Control-Shift-h>"](object()), "break")
        self.assertEqual(calls[-1], "dismiss")

    def test_theme_text_colors_meet_wcag_aa_contrast(self) -> None:
        def relative_luminance(color: str) -> float:
            channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [
                channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
                for channel in channels
            ]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        for theme in (_LIGHT_THEME, _DARK_THEME, _HIGH_CONTRAST_THEME):
            background = relative_luminance(theme["bg"])
            for key in ("muted_fg", "info_fg", "success_fg", "warning_fg", "error_fg"):
                foreground = relative_luminance(theme[key])
                contrast = (max(background, foreground) + 0.05) / (min(background, foreground) + 0.05)
                with self.subTest(theme=theme["bg"], color=key):
                    self.assertGreaterEqual(contrast, 4.5)

    def test_tk_muted_and_status_styles_follow_light_and_dark_themes(self) -> None:
        self._skip_unavailable_tk_display()
        gui = TextureGeneratorGUI()
        try:
            style = gui.root.tk.call
            with mock.patch("generate_textures.save_gui_state"):
                gui._set_theme_mode("light")
                self.assertEqual(style("ttk::style", "lookup", "Muted.TLabel", "-foreground"), _LIGHT_THEME["muted_fg"])
                self.assertEqual(style("ttk::style", "lookup", "Status.Error.TLabel", "-foreground"), _LIGHT_THEME["error_fg"])

                gui._set_theme_mode("dark")
                self.assertEqual(style("ttk::style", "lookup", "Muted.TLabel", "-foreground"), _DARK_THEME["muted_fg"])
                self.assertEqual(style("ttk::style", "lookup", "Status.Error.TLabel", "-foreground"), _DARK_THEME["error_fg"])

                gui._set_theme_mode("high_contrast")
                self.assertEqual(style("ttk::style", "lookup", "Muted.TLabel", "-foreground"), _HIGH_CONTRAST_THEME["muted_fg"])
                self.assertEqual(style("ttk::style", "lookup", "Status.Error.TLabel", "-foreground"), _HIGH_CONTRAST_THEME["error_fg"])
        finally:
            gui.root.destroy()

    def test_normalize_gui_state_turns_off_individual_auto_flags_when_master_off(self) -> None:
        normalized = _normalize_gui_state(
            {
                "auto_suggestions": False,
                "auto_normal": True,
                "auto_parallax": True,
                "auto_glow": True,
                "auto_environment_mask": True,
                "auto_complex": True,
                "auto_specular": True,
            }
        )
        self.assertFalse(bool(normalized["auto_suggestions"]))
        self.assertFalse(bool(normalized["auto_normal"]))
        self.assertFalse(bool(normalized["auto_parallax"]))
        self.assertFalse(bool(normalized["auto_glow"]))
        self.assertFalse(bool(normalized["auto_environment_mask"]))
        self.assertFalse(bool(normalized["auto_complex"]))
        self.assertFalse(bool(normalized["auto_specular"]))

    def test_normalize_gui_state_prevents_emboss_relief_conflict(self) -> None:
        normalized = _normalize_gui_state({"emboss_mode": True, "relief_mode": True})
        self.assertFalse(bool(normalized["emboss_mode"]))
        self.assertTrue(bool(normalized["relief_mode"]))

    def test_normalize_gui_state_clears_missing_input_path(self) -> None:
        normalized = _normalize_gui_state({"input_path": "/definitely/not/a/real/path.dds"})
        self.assertEqual(normalized["input_path"], "")

    def test_normalize_gui_state_disables_custom_output_without_output_path(self) -> None:
        normalized = _normalize_gui_state({"use_custom_output": True, "output_path": ""})
        self.assertFalse(bool(normalized["use_custom_output"]))

    def test_normalize_gui_state_tracks_theme_mode(self) -> None:
        normalized = _normalize_gui_state({"theme_mode": "High Contrast", "dark_mode": False})
        self.assertEqual(str(normalized["theme_mode"]), "high_contrast")
        self.assertTrue(bool(normalized["dark_mode"]))
        fallback = _normalize_gui_state({"dark_mode": True})
        self.assertEqual(str(fallback["theme_mode"]), "dark")

    def test_normalize_gui_state_preserves_last_mod_context_memory(self) -> None:
        normalized = _normalize_gui_state(
            {
                "last_mod_manager": "  Mod Organizer 2  ",
                "last_mod_profile": "  Main  ",
                "last_renderer": "  community_shaders  ",
                "last_output_target": "  next to input  ",
            }
        )
        self.assertEqual(normalized["last_mod_manager"], "Mod Organizer 2")
        self.assertEqual(normalized["last_mod_profile"], "Main")
        self.assertEqual(normalized["last_renderer"], "community_shaders")
        self.assertEqual(normalized["last_output_target"], "next to input")

    def test_normalize_gui_state_preserves_profile_setup_memory(self) -> None:
        normalized = _normalize_gui_state(
            {
                "profile_setup_memory": {
                    "Mod Organizer 2::Main": {
                        "render_profile": "community shaders",
                        "output_target": "D:/Skyrim/Output",
                        "use_custom_output": True,
                        "workflow_mode": "advanced",
                        "target_game": "fallout4",
                    }
                }
            }
        )
        memory = normalized["profile_setup_memory"]
        self.assertIn("Mod Organizer 2::Main", memory)
        entry = memory["Mod Organizer 2::Main"]
        self.assertEqual(entry["render_profile"], "community_shaders")
        self.assertEqual(entry["output_target"], "D:/Skyrim/Output")
        self.assertTrue(entry["use_custom_output"])
        self.assertEqual(entry["workflow_mode"], "advanced")
        self.assertEqual(entry["target_game"], "fallout4")

    def test_load_gui_state_defaults_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = load_gui_state(Path(temp_dir) / "missing-state.json")
        self.assertEqual(state["input_path"], "")
        self.assertEqual(state["output_path"], "")
        self.assertEqual(state["last_mod_manager"], "")
        self.assertEqual(state["last_mod_profile"], "")
        self.assertEqual(state["last_renderer"], "")
        self.assertEqual(state["last_output_target"], "")
        self.assertEqual(state["profile_setup_memory"], {})
        self.assertFalse(bool(state["dark_mode"]))
        self.assertEqual(str(state["theme_mode"]), "light")
        self.assertEqual(str(state["workflow_mode"]), "beginner")
        self.assertTrue(bool(state["simplified_main_layout"]))
        self.assertTrue(bool(state["show_startup_onboarding"]))
        self.assertTrue(bool(state["auto_suggestions"]))
        self.assertFalse(bool(state["qt_show_advanced_map_strengths"]))
        self.assertFalse(bool(state["qt_show_truepbr_sidecar_settings"]))

    def test_build_mod_manager_context_hint_includes_remembered_setup_when_current_detection_missing(self) -> None:
        message = build_mod_manager_context_hint(
            None,
            last_mod_manager="Mod Organizer 2",
            last_mod_profile="Main",
            last_renderer="community_shaders",
            last_output_target="next to input",
        )
        self.assertIn("not detected", message.lower())
        self.assertIn("Mod Organizer 2", message)
        self.assertIn("Main", message)
        self.assertIn("renderer Community Shaders", message)
        self.assertIn("output target next to input", message)

    def test_build_mod_manager_context_hint_includes_truepbr_version_warning(self) -> None:
        context = ModManagerContext(
            manager="Mod Organizer 2",
            profile_name="Main",
            renderer_version_hints=("TruePBR 0.9.0",),
        )
        message = build_mod_manager_context_hint(context)
        self.assertIn("TruePBR version hint(s) TruePBR 0.9.0", message)
        self.assertIn("probably incompatible", message)

    def test_build_mod_manager_profile_setup_hint_tracks_current_profile_setup(self) -> None:
        hint = build_mod_manager_profile_setup_hint(
            {
                "Mod Organizer 2::Main": {
                    "render_profile": "community_shaders",
                    "output_target": "D:/Skyrim/Output",
                    "use_custom_output": True,
                    "workflow_mode": "advanced",
                    "target_game": "fallout4",
                }
            },
            manager="Mod Organizer 2",
            profile="Main",
        )
        self.assertIn("renderer Community Shaders", hint)
        self.assertIn("output D:/Skyrim/Output", hint)
        self.assertIn("workflow mode Advanced", hint)
        self.assertIn("target game fallout4", hint)
        message = build_mod_manager_context_hint(None, profile_setup_hint=hint)
        self.assertIn("Remembered setup for this profile", message)
        self.assertIn(hint, message)

    def test_startup_guidance_prefers_saved_setup_then_detected_folder_then_suggestion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            detected_folder = Path(temp_dir) / "mods" / "Texture Pack"
            detected_folder.mkdir(parents=True)
            saved_state = {
                "Mod Organizer 2::Main": {
                    "render_profile": "community_shaders",
                    "output_target": "D:/Skyrim/Output",
                    "use_custom_output": True,
                    "workflow_mode": "advanced",
                    "target_game": "fallout4",
                }
            }
            context = ModManagerContext(
                manager="Mod Organizer 2",
                profile_name="Main",
                loaded_texture_dirs=(detected_folder,),
            )
            restore_action = resolve_startup_guidance_action(
                context,
                profile_setup_memory=saved_state,
                current_render_profile="custom",
                current_workflow_mode="beginner",
                current_output_target="",
                current_use_custom_output=False,
                current_target_game="skyrim",
            )
            self.assertEqual(restore_action, "restore_profile_setup")
            restore_text = build_startup_guidance_text(
                context,
                profile_setup_memory=saved_state,
                selected_render_profile="custom",
                current_workflow_mode="beginner",
                current_output_target="",
                current_use_custom_output=False,
                current_target_game="skyrim",
            )
            self.assertIn("Recommended next step: restore the remembered setup", restore_text)
            self.assertIn("Compare against the sibling .nif.bak", restore_text)
            self.assertIn("Why not the others:", restore_text)

            detected_action = resolve_startup_guidance_action(
                context,
                profile_setup_memory={},
                input_path="",
            )
            self.assertEqual(detected_action, "pick_detected_mod_folder")
            detected_text = build_startup_guidance_text(
                context,
                profile_setup_memory={},
                input_path="",
            )
            self.assertIn("use the detected mod folder first", detected_text)

            suggested_source = Path(temp_dir) / "textures" / "enb" / "stone_msn.dds"
            suggested_action = resolve_startup_guidance_action(
                None,
                profile_setup_memory={},
                input_path=str(suggested_source),
            )
            self.assertEqual(suggested_action, "apply_suggested_preset")
            suggested_text = build_startup_guidance_text(
                None,
                profile_setup_memory={},
                input_path=str(suggested_source),
                selected_render_profile="community_shaders",
            )
            self.assertIn("apply the suggested renderer family", suggested_text)
            self.assertIn("Why this was recommended:", suggested_text)
            self.assertIn("Why not the others:", suggested_text)

    def test_profile_setup_memory_items_include_descriptive_labels(self) -> None:
        items = _profile_setup_memory_items(
            {
                "Mod Organizer 2::Main": {
                    "render_profile": "truepbr",
                    "output_target": "D:/Skyrim/Output",
                    "use_custom_output": True,
                    "workflow_mode": "advanced",
                    "target_game": "fallout4",
                },
                "Mod Organizer 2::Alt": {
                    "render_profile": "community_shaders",
                    "output_target": "",
                    "use_custom_output": False,
                    "workflow_mode": "beginner",
                    "target_game": "skyrim",
                },
            }
        )
        self.assertEqual([key for key, _ in items], ["Mod Organizer 2::Alt", "Mod Organizer 2::Main"])
        self.assertIn("renderer Community Shaders", items[0][1])
        self.assertIn("workflow mode Beginner", items[0][1])
        self.assertIn("renderer Community Shaders PBR", items[1][1])
        self.assertIn("output D:/Skyrim/Output", items[1][1])
        self.assertIn("workflow mode Advanced", items[1][1])
        self.assertIn("target game fallout4", items[1][1])

    def test_qt_generation_settings_merge_with_shared_gui_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "source.dds"
            input_path.write_bytes(b"source")
            merged = _merge_qt_generation_gui_state(
                {"dark_mode": True, "show_advanced_workflow_outputs": True, "workflow_mode": "standard"},
                input_path=str(input_path),
                output_path=str(Path(temp_dir) / "out"),
                include_diffuse=False,
                include_normal=True,
                include_parallax=False,
                include_glow=True,
                include_environment_mask=True,
                resume_mode="resume",
                checkpoint_path=str(Path(temp_dir) / "checkpoint.json"),
                include_rmaos=True,
                include_complex=True,
                include_wetness_mask=True,
                include_snow_mask=True,
                include_ao=True,
                include_roughness=True,
                render_profile="terrain",
                complex_format="cm",
                env_mask_mode="complex",
                parallax_mode="occlusion",
                normal_strength=3.25,
                parallax_strength=2.5,
                glow_threshold=140,
                environment_mask_strength=1.8,
                rmaos_strength=2.1,
                complex_strength=1.7,
                wetness_mask_strength=1.3,
                snow_mask_strength=0.8,
                ao_strength=2.2,
                roughness_strength=1.4,
                truepbr_sidecar_overrides_enabled=True,
                truepbr_sidecar_parallax=False,
                truepbr_sidecar_displacement_scale=0.14,
                truepbr_sidecar_specular_level=0.07,
                truepbr_sidecar_roughness_scale=1.3,
                dark_mode=True,
                theme_mode="high_contrast",
                ui_scale=1.3,
                workflow_mode="advanced",
                show_advanced_map_strengths=True,
                show_truepbr_sidecar_settings=False,
                target_game="fo4",
            )

        self.assertEqual(merged["input_path"], str(input_path))
        self.assertEqual(merged["output_path"], str(Path(temp_dir) / "out"))
        self.assertTrue(bool(merged["dark_mode"]))
        self.assertEqual(str(merged["theme_mode"]), "high_contrast")
        self.assertTrue(bool(merged["show_advanced_workflow_outputs"]))
        self.assertEqual(str(merged["workflow_mode"]), "advanced")
        self.assertEqual(str(merged["target_game"]), "fallout4")
        self.assertTrue(bool(merged["qt_show_advanced_map_strengths"]))
        self.assertFalse(bool(merged["qt_show_truepbr_sidecar_settings"]))
        self.assertFalse(bool(merged["include_diffuse"]))
        self.assertTrue(bool(merged["include_normal"]))
        self.assertFalse(bool(merged["include_parallax"]))
        self.assertTrue(bool(merged["include_glow"]))
        self.assertTrue(bool(merged["include_environment_mask"]))
        self.assertTrue(bool(merged["include_rmaos"]))
        self.assertTrue(bool(merged["include_complex"]))
        self.assertTrue(bool(merged["include_wetness_mask"]))
        self.assertTrue(bool(merged["include_snow_mask"]))
        self.assertTrue(bool(merged["include_ao"]))
        self.assertTrue(bool(merged["include_roughness"]))
        self.assertEqual(merged["qt_render_profile"], "terrain")
        self.assertEqual(merged["render_profile"], "custom")
        self.assertEqual(merged["complex_format"], "cm")
        self.assertEqual(merged["env_mask_mode"], "complex")
        self.assertEqual(merged["parallax_mode"], "occlusion (ENB/POM)")
        self.assertEqual(merged["normal_strength"], 3.25)
        self.assertEqual(merged["parallax_strength"], 2.5)
        self.assertEqual(merged["glow_threshold"], 140)
        self.assertEqual(merged["environment_mask_strength"], 1.8)
        self.assertEqual(merged["rmaos_strength"], 2.1)
        self.assertEqual(merged["complex_strength"], 1.7)
        self.assertAlmostEqual(merged["wetness_mask_strength"], 1.3)
        self.assertAlmostEqual(merged["snow_mask_strength"], 0.8)
        self.assertAlmostEqual(merged["ao_strength"], 2.2)
        self.assertAlmostEqual(merged["roughness_strength"], 1.4)
        self.assertTrue(merged["truepbr_sidecar_overrides_enabled"])
        self.assertFalse(merged["truepbr_sidecar_parallax"])
        self.assertAlmostEqual(merged["truepbr_sidecar_displacement_scale"], 0.14)
        self.assertAlmostEqual(merged["truepbr_sidecar_specular_level"], 0.07)
        self.assertAlmostEqual(merged["truepbr_sidecar_roughness_scale"], 1.3)
        self.assertTrue(bool(merged["dark_mode"]))
        self.assertAlmostEqual(float(merged["ui_scale"]), 1.3)
        self.assertEqual(merged["batch_resume_mode"], "resume")
        self.assertEqual(merged["batch_checkpoint_path"], str(Path(temp_dir) / "checkpoint.json"))

    def test_qt_checkpoint_generation_options_ignore_paths_and_preview_only_settings(self) -> None:
        options = {
            "input_path": "/input",
            "output_dir": "/output",
            "checkpoint_path": "/state.json",
            "resume_mode": "resume",
            "lazy_preview": True,
            "force_full_preview": False,
            "include_normal": True,
            "normal_strength": 2.0,
        }
        self.assertEqual(
            _qt_checkpoint_generation_options(options),
            {"include_normal": True, "normal_strength": 2.0},
        )

    def test_qt_resume_mode_guidance_explains_checkpoint_matching(self) -> None:
        resume_guidance = _qt_resume_mode_guidance("resume").lower()
        self.assertIn("input", resume_guidance)
        self.assertIn("output folder", resume_guidance)
        self.assertIn("generation settings", resume_guidance)
        self.assertIn("start fresh", resume_guidance)
        self.assertEqual(
            _qt_resume_mode_guidance("start_fresh"),
            "Start fresh ignores prior checkpoint entries.",
        )

    def test_qt_resume_checkpoint_requires_matching_roots_and_generation_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_root = root / "input"
            output_root = root / "output"
            options = {"include_normal": True, "normal_strength": 2.0}
            payload = {
                "input_root": str(input_root.resolve()),
                "output_root": str(output_root.resolve()),
                "generation_options": options,
                "completed_success_files": [str((input_root / "done.dds").resolve()), 3],
            }
            self.assertEqual(
                _qt_resume_completed_files(
                    payload,
                    input_root=input_root,
                    output_dir=output_root,
                    generation_options=options,
                ),
                {str((input_root / "done.dds").resolve())},
            )

            invalid_cases = (
                (
                    {**payload, "input_root": str((root / "other-input").resolve())},
                    "input path differs",
                ),
                (
                    {**payload, "output_root": str((root / "other-output").resolve())},
                    "output folder differs",
                ),
                (
                    {**payload, "generation_options": {"include_normal": False}},
                    "generation settings differ",
                ),
                (
                    {key: value for key, value in payload.items() if key != "generation_options"},
                    "no valid generation settings snapshot",
                ),
                (
                    {**payload, "completed_success_files": "not-a-list"},
                    "completed-file list is invalid",
                ),
            )
            for invalid_payload, message in invalid_cases:
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    _qt_resume_completed_files(
                        invalid_payload,
                        input_root=input_root,
                        output_dir=output_root,
                        generation_options=options,
                    )

    def test_qt_profile_recommendation_uses_existing_filename_and_path_hints(self) -> None:
        self.assertEqual(
            _recommend_qt_render_profile("mods/PBRNifPatcher/textures/architecture/stone.dds"),
            "truepbr",
        )
        self.assertEqual(
            _recommend_qt_render_profile("textures/architecture/stone_cm.dds"),
            "community_shaders",
        )

    def test_qt_profile_recommendation_is_not_collapsed_for_batch_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "textures"
            folder.mkdir()
            self.assertIsNone(_recommend_qt_render_profile(str(folder)))
        self.assertIsNone(_recommend_qt_render_profile(""))

    def test_qt_profile_recommendation_uses_mod_manager_context(self) -> None:
        context = ModManagerContext(
            manager="MO2",
            profile_name="Default",
            loaded_mods=("Community Shaders",),
        )
        self.assertEqual(
            _recommend_qt_render_profile(
                "textures/misc/neutral_asset.dds",
                manager_context=context,
            ),
            "community_shaders",
        )

    def test_qt_profile_output_defaults_match_renderer_workflows(self) -> None:
        truepbr = _qt_profile_output_defaults("truepbr")
        self.assertTrue(truepbr["include_rmaos"])
        self.assertFalse(truepbr["include_environment_mask"])
        self.assertFalse(truepbr["include_complex"])

        community_shaders = _qt_profile_output_defaults("community_shaders")
        self.assertTrue(community_shaders["include_complex"])
        self.assertFalse(community_shaders["include_rmaos"])
        self.assertFalse(community_shaders["include_environment_mask"])

        self.assertFalse(any(_qt_profile_output_defaults("custom").values()))
        self.assertTrue(_qt_profile_output_defaults("vanilla")["include_diffuse"])

    def test_generation_log_helpers_append_copy_and_export(self) -> None:
        self.assertEqual(
            _format_log_export_text("First line\nSecond line\n\n"),
            "First line\nSecond line\n",
        )
        if not hasattr(TextureGeneratorGUI, "_append_generation_log"):
            self.skipTest("Tk generation log helpers are unavailable in this environment.")

        class FakeText:
            def __init__(self) -> None:
                self.content = ""
                self.seen: list[str] = []
                self.state_changes: list[object] = []

            def configure(self, **kwargs) -> None:
                self.state_changes.append(kwargs.get("state"))

            def get(self, start: str, end: str) -> str:
                return self.content

            def insert(self, index: str, text: str) -> None:
                self.content += text

            def see(self, index: str) -> None:
                self.seen.append(index)

        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

            def get(self) -> str:
                return self.value

        fake_log = FakeText()
        fake_status = FakeVar()
        copied: list[str] = []
        clipboard_root = SimpleNamespace(
            clipboard_clear=lambda: copied.append("clear"),
            clipboard_append=lambda text: copied.append(text),
        )
        fake_gui = SimpleNamespace(
            generation_log_text=fake_log,
            status_var=fake_status,
            root=clipboard_root,
            _default_output_browse_dir=lambda: Path(tempfile.gettempdir()),
            _tr=lambda text: text,
        )

        TextureGeneratorGUI._append_generation_log(fake_gui, "First line")
        TextureGeneratorGUI._append_generation_log(fake_gui, ["Second line", "Third line"])
        self.assertEqual(fake_log.content, "First line\nSecond line\nThird line")
        self.assertEqual(fake_log.seen, ["end", "end"])

        TextureGeneratorGUI._copy_generation_log(fake_gui)
        self.assertEqual(copied, ["clear", "First line\nSecond line\nThird line"])
        self.assertEqual(fake_status.get(), "Copied generation log to clipboard.")

        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "generation.log"
            fake_gui._default_output_browse_dir = lambda: Path(temp_dir)
            with mock.patch("generate_textures.filedialog.asksaveasfilename", return_value=str(export_path)):
                TextureGeneratorGUI._export_generation_log(fake_gui)
            self.assertEqual(export_path.read_text(encoding="utf-8"), "First line\nSecond line\nThird line\n")
            self.assertIn("Exported generation log to", fake_status.get())

    def test_qt_truepbr_material_overrides_are_opt_in(self) -> None:
        self.assertIsNone(
            _qt_truepbr_material_overrides(
                False,
                parallax=True,
                displacement_scale=0.4,
                specular_level=0.06,
                roughness_scale=1.2,
            )
        )
        self.assertEqual(
            _qt_truepbr_material_overrides(
                True,
                parallax=False,
                displacement_scale=0.4,
                specular_level=0.06,
                roughness_scale=1.2,
            ),
            {
                "parallax": False,
                "displacement_scale": 0.4,
                "specular_level": 0.06,
                "roughness_scale": 1.2,
            },
        )

    def test_qt_renderer_modes_follow_presets_and_preserve_custom_choices(self) -> None:
        current = {
            "complex_format": "cm",
            "env_mask_mode": "complex",
            "parallax_mode": "occlusion",
        }
        self.assertEqual(
            _qt_render_profile_modes("enb", current),
            {
                "selected_profile": "enb",
                "effective_profile": "enb",
                "complex_format": "msn",
                "env_mask_mode": "complex",
                "parallax_mode": "occlusion",
            },
        )
        self.assertEqual(
            _qt_render_profile_modes("community_shaders", current)["complex_format"],
            "cm",
        )
        self.assertEqual(
            _qt_render_profile_modes("custom", current),
            {
                "selected_profile": "custom",
                "effective_profile": "custom",
                **current,
            },
        )

    def test_qt_profile_output_defaults_preserve_unrelated_optional_maps(self) -> None:
        current = {
            "include_diffuse": True,
            "include_normal": True,
            "include_parallax": False,
            "include_glow": False,
            "include_environment_mask": False,
            "include_rmaos": False,
            "include_complex": False,
            "include_wetness_mask": True,
            "include_snow_mask": True,
            "include_ao": True,
            "include_roughness": True,
        }
        applied = _apply_qt_profile_output_defaults(
            current,
            _qt_profile_output_defaults("community_shaders"),
        )
        self.assertTrue(applied["include_wetness_mask"])
        self.assertTrue(applied["include_snow_mask"])
        self.assertTrue(applied["include_ao"])
        self.assertTrue(applied["include_roughness"])
        self.assertTrue(applied["include_complex"])
        self.assertFalse(applied["include_environment_mask"])
        self.assertFalse(applied["include_rmaos"])

    def test_qt_profile_output_defaults_resolve_conflicting_slot_five_maps(self) -> None:
        applied = _apply_qt_profile_output_defaults(
            {
                "include_environment_mask": True,
                "include_rmaos": False,
            },
            {"include_environment_mask": True, "include_rmaos": True},
        )
        self.assertTrue(applied["include_environment_mask"])
        self.assertFalse(applied["include_rmaos"])

    def test_qt_profile_guidance_documents_recommended_outputs_and_terrain_scope(self) -> None:
        terrain_guidance = _describe_qt_profile_guidance("terrain").lower()
        self.assertIn("<stem>_p.dds", terrain_guidance)
        self.assertIn("does not build tiled landscape layers", terrain_guidance)
        self.assertIn("profile selection does not change checked outputs", terrain_guidance)
        self.assertIn("never mix enb / community shaders / truepbr families", terrain_guidance)

        truepbr_guidance = _describe_qt_profile_guidance("truepbr").lower()
        self.assertIn("<stem>_rmaos.dds", truepbr_guidance)
        self.assertIn("apply profile map defaults", truepbr_guidance)
        self.assertIn("if you're unsure", truepbr_guidance)

    def test_workflow_preset_onboarding_text_mentions_safe_family_selection(self) -> None:
        onboarding = _workflow_preset_onboarding_text().lower()
        self.assertIn("vanilla", onboarding)
        self.assertIn("community shaders", onboarding)
        self.assertIn("truepbr", onboarding)
        self.assertIn("never mix enb / community shaders / truepbr families", onboarding)

    def test_render_profile_guidance_data_captures_family_specific_rules(self) -> None:
        truepbr = _render_profile_guidance("truepbr")
        self.assertIsNotNone(truepbr)
        self.assertIn("_rmaos", truepbr.what_it_does)
        self.assertIn("json", truepbr.when_to_use_it.lower())
        self.assertTrue(any("Not ENB" in text for text in truepbr.why_not_others))
        self.assertIn("_rmaos.dds", truepbr.canonical_outputs[0])

    def test_render_profile_family_tooltip_uses_structured_guidance(self) -> None:
        tooltip = _render_profile_family_tooltip("community_shaders").lower()
        self.assertIn("what it does:", tooltip)
        self.assertIn("when to use it:", tooltip)
        self.assertIn("when not to use it:", tooltip)
        self.assertIn("_cm.dds", tooltip)
        self.assertIn("_c.dds", tooltip)

    def test_render_profile_family_quick_check_text_lists_renderer_families(self) -> None:
        quick_check = _render_profile_family_quick_check_text()
        self.assertIn("Quick check:", quick_check)
        self.assertIn("Vanilla =", quick_check)
        self.assertIn("ENB =", quick_check)
        self.assertIn("Community Shaders =", quick_check)
        self.assertIn("TruePBR =", quick_check)

    def test_render_profile_family_matrix_text_lists_canonical_outputs(self) -> None:
        matrix = _render_profile_family_matrix_text()
        self.assertIn("Family matrix:", matrix)
        self.assertIn("Vanilla: _m.dds, _n.dds, _p.dds", matrix)
        self.assertIn("ENB: _msn.dds, _m.dds", matrix)
        self.assertIn("Community Shaders: _cm.dds, _c.dds", matrix)
        self.assertIn("TruePBR: _rmaos.dds, _ramos.dds, JSON sidecars", matrix)
        self.assertIn("Custom: manual-only", matrix)

    def test_workflow_quick_start_steps_text_mentions_step_flow(self) -> None:
        quick_start = _workflow_quick_start_steps_text().lower()
        self.assertIn("if you're unsure", quick_start)
        self.assertIn("family matrix:", quick_start)
        self.assertIn("step 1:", quick_start)
        self.assertIn("step 2:", quick_start)
        self.assertIn("step 3:", quick_start)
        self.assertIn("step 4:", quick_start)
        self.assertIn("step 5:", quick_start)
        self.assertIn("next safe step", quick_start)
        self.assertIn("suggested", quick_start)
        self.assertIn("compare against the sibling .nif.bak backup", quick_start)
        self.assertIn("restore from it instead of forcing a risky change", quick_start)
        self.assertIn("never mix enb / community shaders / truepbr families", quick_start)

    def test_short_guidance_tooltip_uses_three_sections(self) -> None:
        tooltip = _format_short_guidance_tooltip(
            "switches a control",
            "you need the safe preset path",
            "you want full manual control",
        ).lower()
        self.assertEqual(len(tooltip.splitlines()), 3)
        self.assertIn("what it does: switches a control", tooltip)
        self.assertIn("when to use it: you need the safe preset path", tooltip)
        self.assertIn("when not to use it: you want full manual control", tooltip)

    def test_qt_preview_channel_note_formatter_includes_legends_and_viewing_state(self) -> None:
        note = _format_qt_preview_channel_note(
            "complex_material",
            "enb",
            inspection="channels RGBA",
            viewing_label="Alpha",
        ).lower()
        self.assertIn("channel meanings:", note)
        self.assertIn("enb _msn", note)
        self.assertIn("inspection: channels rgba", note)
        self.assertIn("viewing: alpha.", note)

    def test_qt_preview_channel_note_warns_when_output_family_mismatches(self) -> None:
        note = _format_qt_preview_channel_note(
            "complex_material",
            "truepbr",
            inspection="channels RGBA",
            viewing_label="Composite",
        ).lower()
        self.assertIn("probably incompatible", note)
        self.assertIn("truepbr does not use complex material outputs", note)
        self.assertIn("channel meanings:", note)

    def test_qt_log_export_text_ends_with_single_newline_when_non_empty(self) -> None:
        self.assertEqual(_format_qt_log_export_text("line 1\nline 2\n"), "line 1\nline 2\n")
        self.assertEqual(_format_qt_log_export_text(""), "")

    def test_nif_editor_safety_banner_mentions_backup_rollback_and_fallout(self) -> None:
        banner = _nif_editor_safety_banner_text().lower()
        self.assertIn("dry-run", banner)
        self.assertIn("backup originals", banner)
        self.assertIn("compare against .bak", banner)
        self.assertIn("rollback path", banner)
        self.assertIn("fallout writes stay off unless you opt in", banner)

    def test_nif_editor_safety_banner_switches_for_fallout_write_mode(self) -> None:
        banner = _nif_editor_safety_banner_text(
            "fallout",
            dry_run=False,
            experimental_fallout_write=True,
        ).lower()
        self.assertIn("🚨 write mode is on", banner)
        self.assertIn("🧪 fallout writes are experimental best-effort only", banner)

    def test_nif_editor_safety_banner_highlights_missing_backup(self) -> None:
        banner = _nif_editor_safety_banner_text(dry_run=False, backup=False).lower()
        self.assertIn("backup originals are off", banner)
        self.assertIn("no new .nif.bak rollback copy will be created", banner)

    def test_qt_migration_status_text_summarizes_scope_and_remaining_parity(self) -> None:
        status = _qt_migration_status_text().lower()
        self.assertIn("qt migration preview scope", status)
        self.assertIn("overview, generation, nif scan, nif editor, batch/resume, and status tabs are available", status)
        self.assertIn("restore/compare flows", status)
        self.assertIn("generation rows and queue/preview parity are still being ported from tkinter", status)
        self.assertIn("tkinter ui remains the full-featured release path", status)

    def test_nif_editor_confirmation_text_emphasizes_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backed_up = root / "with_backup.nif"
            backed_up.write_bytes(b"nif")
            backed_up.with_suffix(".nif.bak").write_bytes(b"backup")
            missing_backup = root / "missing_backup.nif"
            missing_backup.write_bytes(b"nif")

            patch_message = _nif_editor_confirmation_text(
                "patch",
                2,
                nif_paths=[backed_up, missing_backup],
                experimental_fallout_write=True,
            ).lower()
            self.assertIn("what it does: apply patch edits to 2 nif files and write them to disk with sibling .nif.bak backups for rollback", patch_message)
            self.assertIn("when to use it: the dry-run preview looks correct", patch_message)
            self.assertIn("when not to use it: you need to keep the current mesh untouched", patch_message)
            self.assertIn("rollback path: 1/2 selected nifs have sibling .nif.bak backups", patch_message)
            self.assertIn("rollback path: write mode modifies the meshes on disk and relies on .nif.bak backups for rollback.", patch_message)
            self.assertIn("dry-run first and backups enabled are the safe default.", patch_message)
            self.assertIn("best-effort", patch_message)
            self.assertIn("🚨 write mode", patch_message)

            risky_patch_message = _nif_editor_confirmation_text(
                "patch",
                2,
                nif_paths=[backed_up, missing_backup],
                backup=False,
                experimental_fallout_write=True,
            ).lower()
            self.assertIn("backup originals are disabled", risky_patch_message)
            self.assertIn("no new .nif.bak rollback copy will be created", risky_patch_message)
            self.assertIn("continue with write mode?", risky_patch_message)

            safe_fix_message = _nif_editor_confirmation_text(
                "remediate",
                2,
                nif_paths=[backed_up, missing_backup],
                backup=False,
            ).lower()
            self.assertIn("safe-fix write", safe_fix_message)
            self.assertIn("apply grouped safe fixes", safe_fix_message)
            self.assertIn("continue with safe-fix write mode?", safe_fix_message)

            restore_message = _nif_editor_confirmation_text(
                "restore",
                1,
                nif_paths=[backed_up],
            ).lower()
            self.assertIn("what it does: restore 1 nif file from sibling .nif.bak backups and overwrite the current .nif files", restore_message)
            self.assertIn("when to use it: you are rolling back a write preview", restore_message)
            self.assertIn("when not to use it: you need to preserve the current file contents", restore_message)
            self.assertIn("rollback path: all selected nifs have sibling .nif.bak backups", restore_message)
            self.assertIn("rollback path: restore replaces the current mesh with the sibling backup.", restore_message)
            self.assertIn("⚠ restore / rollback", restore_message)

            unpatch_message = _nif_editor_confirmation_text(
                "unpatch",
                3,
                nif_paths=[backed_up, missing_backup],
                experimental_fallout_write=True,
            ).lower()
            self.assertIn("what it does: remove the selected features from 3 nif files and write the changes to disk", unpatch_message)
            self.assertIn("when to use it: you intentionally want to strip features", unpatch_message)
            self.assertIn("when not to use it: you want to preserve the mesh", unpatch_message)
            self.assertIn("rollback path: 1/2 selected nifs have sibling .nif.bak backups", unpatch_message)
            self.assertIn("🚨 destructive write", unpatch_message)
            self.assertIn("🧪 fallout writes are experimental best-effort only.", unpatch_message)
            self.assertIn("continue with destructive write mode?", unpatch_message)

    def test_nif_editor_confirmation_title_matches_action(self) -> None:
        self.assertEqual(_nif_editor_confirmation_title("patch"), "Confirm NIF write mode")
        self.assertEqual(_nif_editor_confirmation_title("patch", backup=False), "Confirm NIF write without backup")
        self.assertEqual(_nif_editor_confirmation_title("unpatch"), "Confirm destructive unpatch")
        self.assertEqual(_nif_editor_confirmation_title("unpatch", backup=False), "Confirm destructive unpatch without backup")
        self.assertEqual(_nif_editor_confirmation_title("remediate"), "Confirm safe-fix writes")
        self.assertEqual(_nif_editor_confirmation_title("remediate", backup=False), "Confirm safe-fix writes without backup")
        self.assertEqual(_nif_editor_confirmation_title("restore"), "Confirm NIF restore")

    def test_qt_preview_loader_decodes_and_bounds_image_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.png"
            Image.new("RGB", (1600, 800), color=(120, 80, 40)).save(source_path)

            preview_bytes = _load_qt_preview_png(source_path, max_dimension=256)

        with Image.open(io.BytesIO(preview_bytes)) as preview:
            self.assertEqual(preview.format, "PNG")
            self.assertEqual(preview.size, (256, 128))

    def test_qt_preview_loader_can_inspect_rgba_channels(self) -> None:
        image = Image.new("RGBA", (2, 1))
        image.putdata([(10, 40, 90, 180), (20, 50, 100, 200)])
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "channels.png"
            image.save(image_path)
            channel_png = _load_qt_preview_png(image_path, channel="A")
            with Image.open(io.BytesIO(channel_png)) as channel:
                self.assertEqual(channel.mode, "L")
                self.assertEqual(list(channel.getdata()), [180, 200])
            rgb_path = image_path.with_name("rgb.png")
            Image.new("RGB", (2, 1), (1, 2, 3)).save(rgb_path)
            with self.assertRaisesRegex(ValueError, "no alpha channel"):
                _load_qt_preview_png(rgb_path, channel="A")

    def test_qt_preview_channel_options_follow_actual_image_bands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            rgb_path = Path(temp_dir) / "rgb.png"
            Image.new("RGB", (1, 1), (1, 2, 3)).save(rgb_path)
            self.assertEqual(
                _qt_preview_channel_options(rgb_path),
                (
                    ("Composite", None),
                    ("R channel", "R"),
                    ("G channel", "G"),
                    ("B channel", "B"),
                ),
            )
            grayscale_path = Path(temp_dir) / "gray.png"
            Image.new("L", (1, 1), 128).save(grayscale_path)
            self.assertEqual(
                _qt_preview_channel_options(grayscale_path),
                (("Composite", None), ("Grayscale", "L")),
            )
            grayscale_preview = _load_qt_preview_png(grayscale_path, channel="L")
            with Image.open(io.BytesIO(grayscale_preview)) as preview:
                self.assertEqual(preview.mode, "L")
                self.assertEqual(list(preview.getdata()), [128])
            with self.assertRaisesRegex(ValueError, "no separate RGB channels"):
                _load_qt_preview_png(grayscale_path, channel="R")

    def test_qt_output_inspection_validates_truepbr_rgba_channels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            rgba_path = Path(temp_dir) / "rmaos.png"
            Image.new("RGBA", (4, 3), (10, 20, 30, 40)).save(rgba_path)
            valid_message = _inspect_qt_output_image(rgba_path, "rmaos")
            self.assertIn("Validation passed", valid_message)
            self.assertIn("4 × 3 px", valid_message)
            self.assertIn("R, G, B, A", valid_message)
            self.assertEqual(
                _qt_preview_channel_options(rgba_path, "rmaos"),
                (
                    ("Composite", None),
                    ("R — Roughness", "R"),
                    ("G — Metallic", "G"),
                    ("B — Ambient occlusion", "B"),
                    ("A — Non-metal reflectance", "A"),
                ),
            )

            rgb_path = Path(temp_dir) / "rmaos_without_alpha.png"
            Image.new("RGB", (4, 3), (10, 20, 30)).save(rgb_path)
            warning_message = _inspect_qt_output_image(rgb_path, "rmaos")
            self.assertIn("Validation warning", warning_message)
            self.assertIn("should contain RGBA channels", warning_message)
            self.assertNotIn(
                ("A — Non-metal reflectance", "A"),
                _qt_preview_channel_options(rgb_path, "rmaos"),
            )

    def test_qt_output_inspection_does_not_impose_rgba_on_grayscale_masks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mask_path = Path(temp_dir) / "ao.png"
            Image.new("L", (2, 2), 128).save(mask_path)
            inspection = _inspect_qt_output_image(mask_path, "ao")
        self.assertNotIn("Validation warning", inspection)
        self.assertIn("channels L", inspection)

    def test_qt_channel_guidance_matches_supported_workflow_contracts(self) -> None:
        self.assertIn(
            "A non-metal reflectance",
            _describe_qt_output_channels("rmaos", "truepbr"),
        )
        self.assertIn(
            "ENB _msn",
            _describe_qt_output_channels("complex_material", "enb"),
        )
        self.assertIn(
            "not a universal vanilla map",
            _describe_qt_output_channels("wetness_mask", "community_shaders"),
        )

    def test_save_gui_state_round_trips_expected_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = Path(temp_dir) / "in.dds"
            input_file.write_bytes(b"fake")
            state_file = Path(temp_dir) / "gui-state.json"
            save_gui_state(
                {
                    "input_path": str(input_file),
                    "output_path": "/tmp/out",
                    "use_custom_output": True,
                    "theme_mode": "high_contrast",
                    "show_batch_preview": True,
                    "show_startup_onboarding": False,
                    "workflow_mode": "advanced",
                    "simplified_main_layout": False,
                    "show_advanced_generation_controls": True,
                    "show_advanced_workflow_outputs": True,
                    "preview_size": "XL",
                    "complex_format": "cm",
                    "env_mask_mode": "complex",
                    "parallax_mode": "occlusion",
                    "render_profile": "community_shaders",
                    "last_mod_manager": "Mod Organizer 2",
                    "last_mod_profile": "Main",
                    "last_renderer": "community_shaders",
                    "last_output_target": "/tmp/out",
                    "emboss_mode": True,
                    "include_diffuse": False,
                    "include_normal": True,
                    "include_parallax": False,
                    "include_glow": True,
                    "include_environment_mask": True,
                    "include_complex": True,
                    "auto_suggestions": True,
                    "auto_normal": False,
                    "auto_parallax": True,
                    "auto_glow": False,
                    "auto_environment_mask": True,
                    "auto_complex": False,
                    "auto_specular": True,
                    "normal_strength": 3.5,
                    "parallax_strength": 2.25,
                    "glow_threshold": 140,
                    "environment_mask_strength": 2.4,
                    "complex_strength": 2.2,
                    "specular_strength": 2.3,
                },
                state_file,
            )
            loaded = load_gui_state(state_file)
        self.assertEqual(loaded["input_path"], str(input_file))
        self.assertEqual(loaded["output_path"], "/tmp/out")
        self.assertTrue(bool(loaded["use_custom_output"]))
        self.assertEqual(str(loaded["theme_mode"]), "high_contrast")
        self.assertTrue(bool(loaded["dark_mode"]))
        self.assertTrue(bool(loaded["show_batch_preview"]))
        self.assertFalse(bool(loaded["show_startup_onboarding"]))
        self.assertFalse(bool(loaded["simplified_main_layout"]))
        self.assertTrue(bool(loaded["show_advanced_generation_controls"]))
        self.assertTrue(bool(loaded["show_advanced_workflow_outputs"]))
        self.assertEqual(str(loaded["workflow_mode"]), "advanced")
        self.assertEqual(str(loaded["preview_size"]), "XL")
        self.assertEqual(str(loaded["complex_format"]), "cm")
        self.assertEqual(str(loaded["env_mask_mode"]), "complex")
        self.assertEqual(str(loaded["parallax_mode"]), "occlusion (ENB/POM)")
        self.assertEqual(str(loaded["render_profile"]), "community_shaders")
        self.assertEqual(str(loaded["last_mod_manager"]), "Mod Organizer 2")
        self.assertEqual(str(loaded["last_mod_profile"]), "Main")
        self.assertEqual(str(loaded["last_renderer"]), "community_shaders")
        self.assertEqual(str(loaded["last_output_target"]), "/tmp/out")
        self.assertTrue(bool(loaded["emboss_mode"]))
        self.assertFalse(bool(loaded["include_diffuse"]))
        self.assertTrue(bool(loaded["include_normal"]))
        self.assertFalse(bool(loaded["include_parallax"]))
        self.assertTrue(bool(loaded["include_glow"]))
        self.assertTrue(bool(loaded["include_environment_mask"]))
        self.assertTrue(bool(loaded["include_complex"]))
        self.assertTrue(bool(loaded["auto_suggestions"]))
        self.assertFalse(bool(loaded["auto_normal"]))
        self.assertTrue(bool(loaded["auto_parallax"]))
        self.assertFalse(bool(loaded["auto_glow"]))
        self.assertTrue(bool(loaded["auto_environment_mask"]))
        self.assertFalse(bool(loaded["auto_complex"]))
        self.assertTrue(bool(loaded["auto_specular"]))
        self.assertAlmostEqual(float(loaded["normal_strength"]), 3.5)
        self.assertAlmostEqual(float(loaded["parallax_strength"]), 2.25)
        self.assertEqual(int(loaded["glow_threshold"]), 140)
        self.assertAlmostEqual(float(loaded["environment_mask_strength"]), 2.4)
        self.assertAlmostEqual(float(loaded["complex_strength"]), 2.2)
        self.assertAlmostEqual(float(loaded["specular_strength"]), 2.3)

    def test_project_settings_import_and_export_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_input = temp_path / "source.dds"
            source_input.write_bytes(b"fake")
            source_state = temp_path / "source-state.json"
            imported_state = temp_path / "imported-state.json"
            exported_state = temp_path / "exported-state.json"
            save_gui_state(
                {
                    "input_path": str(source_input),
                    "output_path": "/tmp/out",
                    "use_custom_output": True,
                    "theme_mode": "dark",
                    "last_mod_manager": "Mod Organizer 2",
                    "last_mod_profile": "Main",
                    "last_renderer": "community_shaders",
                    "last_output_target": "/tmp/out",
                    "render_profile": "truepbr",
                    "show_advanced_workflow_outputs": True,
                    "show_startup_onboarding": False,
                },
                source_state,
            )

            import_project_settings(source_state, destination_state_file=imported_state)
            imported = load_gui_state(imported_state)
            self.assertEqual(imported["input_path"], str(source_input))
            self.assertEqual(imported["output_path"], "/tmp/out")
            self.assertTrue(bool(imported["use_custom_output"]))
            self.assertEqual(str(imported["theme_mode"]), "dark")
            self.assertTrue(bool(imported["dark_mode"]))
            self.assertEqual(imported["last_mod_manager"], "Mod Organizer 2")
            self.assertEqual(imported["last_mod_profile"], "Main")
            self.assertEqual(imported["last_renderer"], "community_shaders")
            self.assertEqual(imported["last_output_target"], "/tmp/out")
            self.assertFalse(bool(imported["show_startup_onboarding"]))

            export_project_settings(exported_state, source_state_file=imported_state)
            exported = load_gui_state(exported_state)
            self.assertEqual(exported["input_path"], str(source_input))
            self.assertEqual(exported["output_path"], "/tmp/out")
            self.assertTrue(bool(exported["show_advanced_workflow_outputs"]))
            self.assertFalse(bool(exported["show_startup_onboarding"]))

    def test_export_project_settings_raises_on_invalid_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_state = temp_path / "broken-state.json"
            source_state.write_text("not json", encoding="utf-8")
            export_state = temp_path / "exported-settings.json"
            with self.assertRaises(json.JSONDecodeError):
                export_project_settings(export_state, source_state_file=source_state)
            self.assertFalse(export_state.exists())

    def test_export_project_settings_state_writes_live_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_input = temp_path / "source.dds"
            source_input.write_bytes(b"fake")
            export_state = temp_path / "live-export.json"
            export_project_settings_state(
                {
                    "input_path": str(source_input),
                    "output_path": "/tmp/out",
                    "use_custom_output": True,
                    "theme_mode": "high_contrast",
                    "last_mod_manager": "Vortex",
                    "last_mod_profile": "Default",
                    "last_renderer": "community_shaders",
                    "last_output_target": "/tmp/out",
                    "show_advanced_workflow_outputs": True,
                },
                export_state,
            )
            loaded = load_gui_state(export_state)
            self.assertEqual(loaded["input_path"], str(source_input))
            self.assertEqual(loaded["output_path"], "/tmp/out")
            self.assertTrue(bool(loaded["use_custom_output"]))
            self.assertEqual(str(loaded["theme_mode"]), "high_contrast")
            self.assertTrue(bool(loaded["dark_mode"]))
            self.assertEqual(loaded["last_renderer"], "community_shaders")
            self.assertEqual(loaded["last_output_target"], "/tmp/out")
            self.assertEqual(str(loaded["workflow_mode"]), "advanced")
            self.assertTrue(bool(loaded["show_advanced_workflow_outputs"]))

    def test_tk_advanced_workflow_visibility_round_trips_saved_settings(self) -> None:
        self._skip_unavailable_tk_display()

        def find_label_frame(root, title: str):
            pending = list(root.winfo_children())
            while pending:
                widget = pending.pop()
                if widget.winfo_class() == "TLabelframe" and widget.cget("text") == title:
                    return widget
                pending.extend(widget.winfo_children())
            self.fail(f"Could not find Tk section '{title}'.")

        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "gui-state.json"
            saved_state = load_gui_state(state_file)
            saved_state["simplified_main_layout"] = False
            saved_state["show_advanced_workflow_outputs"] = True
            saved_state["workflow_mode"] = "advanced"
            first_gui = None
            restored_gui = None
            try:
                with mock.patch("generate_textures.load_gui_state", return_value=saved_state):
                    first_gui = TextureGeneratorGUI()
                first_gui.root.update_idletasks()
                pbr_section = find_label_frame(first_gui.root, "Community Shaders PBR Workflow")
                self.assertEqual(first_gui.workflow_mode_var.get(), "Advanced")
                self.assertTrue(first_gui.show_advanced_workflow_outputs_var.get())
                self.assertFalse(first_gui.simplified_main_layout_var.get())
                self.assertTrue(pbr_section.grid_info())

                first_gui.show_advanced_workflow_outputs_var.set(False)
                first_gui.root.update_idletasks()
                self.assertEqual(first_gui.workflow_mode_var.get(), "Standard")
                self.assertFalse(pbr_section.grid_info())
                first_gui.show_advanced_workflow_outputs_var.set(True)
                first_gui.root.geometry("1120x820")
                first_gui.root.update_idletasks()
                self.assertEqual(first_gui.workflow_mode_var.get(), "Advanced")
                self.assertTrue(pbr_section.grid_info())

                save_gui_state(first_gui._build_gui_state(), state_file)
                persisted_state = load_gui_state(state_file)
                self.assertFalse(bool(persisted_state["simplified_main_layout"]))
                self.assertTrue(bool(persisted_state["show_advanced_workflow_outputs"]))
                self.assertEqual(str(persisted_state["workflow_mode"]), "advanced")
            finally:
                if first_gui is not None:
                    first_gui.root.destroy()

            try:
                with mock.patch("generate_textures.load_gui_state", return_value=persisted_state):
                    restored_gui = TextureGeneratorGUI()
                restored_gui.root.update_idletasks()
                restored_pbr_section = find_label_frame(restored_gui.root, "Community Shaders PBR Workflow")
                self.assertEqual(restored_gui.workflow_mode_var.get(), "Advanced")
                self.assertTrue(restored_gui.show_advanced_workflow_outputs_var.get())
                self.assertTrue(restored_pbr_section.grid_info())
                restored_gui.simplified_main_layout_var.set(True)
                restored_gui.root.update_idletasks()
                self.assertEqual(restored_gui.workflow_mode_var.get(), "Beginner")
                self.assertFalse(restored_pbr_section.grid_info())
            finally:
                if restored_gui is not None:
                    restored_gui.root.destroy()

    def test_tk_workflow_mode_selection_updates_layout_toggles(self) -> None:
        self._skip_unavailable_tk_display()
        with tempfile.TemporaryDirectory() as temp_dir:
            saved_state = load_gui_state(Path(temp_dir) / "missing-state.json")
            gui = None
            try:
                with mock.patch("generate_textures.load_gui_state", return_value=saved_state):
                    gui = TextureGeneratorGUI()
                gui.root.update_idletasks()
                self.assertEqual(gui.workflow_mode_var.get(), "Beginner")

                gui._apply_workflow_mode("advanced")
                gui.root.update_idletasks()
                self.assertFalse(gui.simplified_main_layout_var.get())
                self.assertTrue(gui.show_advanced_generation_var.get())
                self.assertTrue(gui.show_advanced_workflow_outputs_var.get())
                self.assertEqual(gui.workflow_mode_var.get(), "Advanced")

                gui._apply_workflow_mode("standard")
                gui.root.update_idletasks()
                self.assertFalse(gui.simplified_main_layout_var.get())
                self.assertFalse(gui.show_advanced_generation_var.get())
                self.assertFalse(gui.show_advanced_workflow_outputs_var.get())
                self.assertEqual(gui.workflow_mode_var.get(), "Standard")
            finally:
                if gui is not None:
                    gui.root.destroy()

    def test_tk_profile_setup_hint_label_shows_remembered_setup(self) -> None:
        self._skip_unavailable_tk_display()

        with tempfile.TemporaryDirectory() as temp_dir:
            saved_state = load_gui_state(Path(temp_dir) / "missing-state.json")
            saved_state["last_mod_manager"] = "Mod Organizer 2"
            saved_state["last_mod_profile"] = "Main"
            saved_state["profile_setup_memory"] = {
                "Mod Organizer 2::Main": {
                    "render_profile": "community_shaders",
                    "output_target": "D:/Skyrim/Output",
                    "use_custom_output": True,
                    "workflow_mode": "advanced",
                    "target_game": "fallout4",
                }
            }
            gui = None
            try:
                with mock.patch("generate_textures.load_gui_state", return_value=saved_state), mock.patch(
                    "generate_textures.detect_mod_manager_context",
                    return_value=ModManagerContext(manager="Mod Organizer 2", profile_name="Main"),
                ):
                    gui = TextureGeneratorGUI()
                gui.root.update_idletasks()
                hint = gui.profile_setup_hint_var.get()
                self.assertIn("Remembered setup for this profile", hint)
                self.assertIn("renderer Community Shaders", hint)
                self.assertIn("output D:/Skyrim/Output", hint)
                self.assertTrue(gui.profile_setup_restore_button.instate(("!disabled",)))
            finally:
                if gui is not None:
                    gui.root.destroy()

    def test_tk_profile_setup_switcher_restores_selected_saved_setup(self) -> None:
        self._skip_unavailable_tk_display()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()
            manager = "Mod Organizer 2"
            profile = "Main"
            current_key = _profile_setup_memory_key(manager, profile)
            alternate_key = _profile_setup_memory_key(manager, "Alt")
            saved_state = load_gui_state(Path(temp_dir) / "missing-state.json")
            saved_state["last_mod_manager"] = manager
            saved_state["last_mod_profile"] = profile
            saved_state["profile_setup_memory"] = {
                current_key: {
                    "render_profile": "truepbr",
                    "output_target": str(output_dir),
                    "use_custom_output": True,
                    "workflow_mode": "advanced",
                    "target_game": "fallout4",
                },
                alternate_key: {
                    "render_profile": "community_shaders",
                    "output_target": "",
                    "use_custom_output": False,
                    "workflow_mode": "standard",
                    "target_game": "skyrim",
                },
            }
            gui = None
            try:
                with mock.patch("generate_textures.load_gui_state", return_value=saved_state), mock.patch(
                    "generate_textures.detect_mod_manager_context",
                    return_value=ModManagerContext(manager=manager, profile_name=profile),
                ):
                    gui = TextureGeneratorGUI()
                gui.root.update_idletasks()
                values = tuple(gui.profile_setup_choice_combo["values"])
                self.assertEqual(len(values), 2)
                self.assertTrue(any("renderer TruePBR" in value for value in values))
                self.assertTrue(any("renderer Community Shaders" in value for value in values))
                alt_label = next(value for value in values if "renderer Community Shaders" in value)
                gui.profile_setup_choice_var.set(alt_label)
                gui._update_profile_setup_restore_controls()
                self.assertTrue(gui.profile_setup_restore_button.instate(("!disabled",)))
                gui.profile_setup_restore_button.invoke()
                gui.root.update_idletasks()
                self.assertEqual(gui.render_profile_var.get(), "community_shaders")
                self.assertEqual(gui.workflow_mode_var.get(), "Standard")
                self.assertFalse(gui.use_custom_output_var.get())
                self.assertEqual(gui.output_var.get(), "")
                self.assertEqual(gui.target_game_var.get(), "skyrim")
                restored_state = gui._build_gui_state()
                restored_entry = restored_state["profile_setup_memory"][current_key]
                self.assertEqual(restored_entry["render_profile"], "community_shaders")
                self.assertEqual(restored_entry["workflow_mode"], "standard")
                self.assertFalse(restored_entry["use_custom_output"])
                self.assertEqual(restored_entry["output_target"], "next to input")
                self.assertEqual(restored_entry["target_game"], "skyrim")
            finally:
                if gui is not None:
                    gui.root.destroy()

    def test_tk_startup_onboarding_banner_can_be_dismissed(self) -> None:
        self._skip_unavailable_tk_display()

        def find_label_frame(root, title: str):
            pending = list(root.winfo_children())
            while pending:
                widget = pending.pop()
                if widget.winfo_class() == "TLabelframe" and widget.cget("text") == title:
                    return widget
                pending.extend(widget.winfo_children())
            self.fail(f"Could not find Tk section '{title}'.")

        def find_button(root, title: str):
            pending = list(root.winfo_children())
            while pending:
                widget = pending.pop()
                if widget.winfo_class() == "TButton" and str(widget.cget("text")) == title:
                    return widget
                pending.extend(widget.winfo_children())
            self.fail(f"Could not find Tk button '{title}'.")

        with tempfile.TemporaryDirectory() as temp_dir:
            saved_state = load_gui_state(Path(temp_dir) / "missing-state.json")
            gui = None
            try:
                with mock.patch("generate_textures.load_gui_state", return_value=saved_state), \
                     mock.patch("generate_textures.save_gui_state") as save_state:
                    gui = TextureGeneratorGUI()
                    gui.root.update_idletasks()
                    banner = find_label_frame(gui.root, "Quick start")
                    self.assertTrue(banner.winfo_ismapped())
                    label_texts = [
                        str(child.cget("text"))
                        for child in banner.winfo_children()
                        if child.winfo_class() in {"TLabel", "Label"}
                    ]
                    self.assertTrue(any("Recommended next step:" in text for text in label_texts))
                    self.assertTrue(any("choose an input file or folder" in text for text in label_texts))
                    self.assertTrue(any("If you're unsure" in text for text in label_texts))
                    self.assertTrue(any("Step 1:" in text for text in label_texts))
                    self.assertTrue(any("Step 4:" in text for text in label_texts))
                    self.assertTrue(any("Step 5:" in text for text in label_texts))
                    find_button(banner, "Hide").invoke()
                    gui.root.update_idletasks()
                    self.assertFalse(gui.show_startup_onboarding_var.get())
                    self.assertFalse(banner.winfo_ismapped())
                    show_button = find_button(gui.root, "Show quick start")
                    self.assertTrue(show_button.winfo_ismapped())
                    show_button.invoke()
                    gui.root.update_idletasks()
                    self.assertTrue(gui.show_startup_onboarding_var.get())
                    self.assertTrue(banner.winfo_ismapped())
                    save_state.assert_called()
                    persisted = save_state.call_args.args[0]
                    self.assertTrue(bool(persisted["show_startup_onboarding"]))
            finally:
                if gui is not None:
                    gui.root.destroy()

    def test_tk_startup_onboarding_guided_step_restores_saved_setup_before_detected_folder(self) -> None:
        self._skip_unavailable_tk_display()

        def find_label_frame(root, title: str):
            pending = list(root.winfo_children())
            while pending:
                widget = pending.pop()
                if widget.winfo_class() == "TLabelframe" and widget.cget("text") == title:
                    return widget
                pending.extend(widget.winfo_children())
            self.fail(f"Could not find Tk section '{title}'.")

        def find_button(root, title: str):
            pending = list(root.winfo_children())
            while pending:
                widget = pending.pop()
                if widget.winfo_class() == "TButton" and str(widget.cget("text")) == title:
                    return widget
                pending.extend(widget.winfo_children())
            self.fail(f"Could not find Tk button '{title}'.")

        with tempfile.TemporaryDirectory() as temp_dir:
            saved_state = load_gui_state(Path(temp_dir) / "missing-state.json")
            manager = "Mod Organizer 2"
            profile = "Main"
            profile_key = _profile_setup_memory_key(manager, profile)
            saved_state["last_mod_manager"] = manager
            saved_state["last_mod_profile"] = profile
            saved_state["profile_setup_memory"] = {
                profile_key: {
                    "render_profile": "community_shaders",
                    "output_target": "D:/Skyrim/Output",
                    "use_custom_output": True,
                    "workflow_mode": "advanced",
                    "target_game": "fallout4",
                }
            }
            detected_folder = Path(temp_dir) / "mods" / "Texture Pack" / "textures"
            detected_folder.mkdir(parents=True)
            context = ModManagerContext(
                manager=manager,
                profile_name=profile,
                loaded_texture_dirs=(detected_folder,),
            )
            gui = None
            try:
                with mock.patch("generate_textures.load_gui_state", return_value=saved_state), \
                     mock.patch("generate_textures.detect_mod_manager_context", return_value=context), \
                     mock.patch("generate_textures.save_gui_state") as save_state:
                    gui = TextureGeneratorGUI()
                    gui.root.update_idletasks()
                    banner = find_label_frame(gui.root, "Quick start")
                    label_texts = [
                        str(child.cget("text"))
                        for child in banner.winfo_children()
                        if child.winfo_class() in {"TLabel", "Label"}
                    ]
                    self.assertTrue(any("restore the remembered setup" in text for text in label_texts))
                    find_button(banner, "Next safe step").invoke()
                    gui.root.update_idletasks()
                    self.assertEqual(gui.render_profile_var.get(), "community_shaders")
                    self.assertEqual(gui.workflow_mode_var.get(), "Advanced")
                    self.assertTrue(gui.use_custom_output_var.get())
                    self.assertEqual(gui.output_var.get(), "D:/Skyrim/Output")
                    self.assertEqual(gui.target_game_var.get(), "fallout4")
                    refreshed_texts = [
                        str(child.cget("text"))
                        for child in banner.winfo_children()
                        if child.winfo_class() in {"TLabel", "Label"}
                    ]
                    self.assertTrue(any("use the detected mod folder first" in text for text in refreshed_texts))
                    save_state.assert_called()
            finally:
                if gui is not None:
                    gui.root.destroy()

    def test_tk_startup_onboarding_suggested_button_uses_manager_context(self) -> None:
        self._skip_unavailable_tk_display()

        def find_label_frame(root, title: str):
            pending = list(root.winfo_children())
            while pending:
                widget = pending.pop()
                if widget.winfo_class() == "TLabelframe" and widget.cget("text") == title:
                    return widget
                pending.extend(widget.winfo_children())
            self.fail(f"Could not find Tk section '{title}'.")

        def find_button(root, title: str):
            pending = list(root.winfo_children())
            while pending:
                widget = pending.pop()
                if widget.winfo_class() == "TButton" and str(widget.cget("text")) == title:
                    return widget
                pending.extend(widget.winfo_children())
            self.fail(f"Could not find Tk button '{title}'.")

        with tempfile.TemporaryDirectory() as temp_dir:
            saved_state = load_gui_state(Path(temp_dir) / "missing-state.json")
            gui = None
            context = ModManagerContext(manager="Mod Organizer 2", loaded_mods=("ENBSeries",))
            try:
                with mock.patch("generate_textures.load_gui_state", return_value=saved_state), \
                     mock.patch("generate_textures.detect_mod_manager_context", return_value=context), \
                     mock.patch("generate_textures.save_gui_state") as save_state:
                    gui = TextureGeneratorGUI()
                    gui.root.update_idletasks()
                    banner = find_label_frame(gui.root, "Quick start")
                    label_texts = [
                        str(child.cget("text"))
                        for child in banner.winfo_children()
                        if child.winfo_class() in {"TLabel", "Label"}
                    ]
                    self.assertTrue(any("Step 3:" in text for text in label_texts))
                    find_button(banner, "Suggested").invoke()
                    gui.root.update_idletasks()
                    self.assertEqual(gui.render_profile_var.get(), "enb")
                    self.assertFalse(gui.show_startup_onboarding_var.get())
                    self.assertFalse(banner.winfo_ismapped())
                    save_state.assert_called()
                    persisted = save_state.call_args.args[0]
                    self.assertEqual(persisted["render_profile"], "enb")
                    self.assertFalse(bool(persisted["show_startup_onboarding"]))
            finally:
                if gui is not None:
                    gui.root.destroy()

    def test_tk_generation_log_copy_and_export(self) -> None:
        self._skip_unavailable_tk_display()

        with tempfile.TemporaryDirectory() as temp_dir:
            saved_state = load_gui_state(Path(temp_dir) / "missing-state.json")
            export_path = Path(temp_dir) / "generation.log"
            gui = None
            try:
                with mock.patch("generate_textures.load_gui_state", return_value=saved_state), \
                     mock.patch("generate_textures.save_gui_state"), \
                     mock.patch("generate_textures.detect_mod_manager_context", return_value=ModManagerContext()):
                    gui = TextureGeneratorGUI()
                gui.root.update_idletasks()
                self.assertIsNotNone(gui.generation_log_text)
                expected_text = (
                    "Queued 3 source texture(s).\n"
                    "Processing 1/3: stone.dds (elapsed 0.5s, eta 1.0s)\n"
                    "Run summary: processed 3/3."
                )
                gui._append_generation_log("Queued 3 source texture(s).")
                gui._append_generation_log(
                    [
                        "Processing 1/3: stone.dds (elapsed 0.5s, eta 1.0s)",
                        "Run summary: processed 3/3.",
                    ]
                )
                self.assertEqual(gui.generation_log_text.get("1.0", "end-1c"), expected_text)
                with mock.patch.object(gui.root, "clipboard_clear") as clear_clipboard, \
                     mock.patch.object(gui.root, "clipboard_append") as append_clipboard:
                    gui._copy_generation_log()
                clear_clipboard.assert_called_once()
                append_clipboard.assert_called_once_with(expected_text)
                self.assertIn("Copied generation log to clipboard.", gui.status_var.get())
                with mock.patch("generate_textures.filedialog.asksaveasfilename", return_value=str(export_path)):
                    gui._export_generation_log()
                self.assertEqual(export_path.read_text(encoding="utf-8"), expected_text + "\n")
                self.assertIn("Exported generation log to", gui.status_var.get())
            finally:
                if gui is not None:
                    gui.root.destroy()

    def test_tk_theme_scale_and_window_sizes_remain_usable(self) -> None:
        self._skip_unavailable_tk_display()
        state_file = Path(tempfile.mkdtemp()) / "gui-state.json"
        gui = None
        try:
            with mock.patch("generate_textures.GUI_STATE_FILE", state_file):
                gui = TextureGeneratorGUI()
                try:
                    gui.root.geometry("800x600")
                    gui.root.update_idletasks()
                    self.assertGreater(gui.root.winfo_width(), 0)
                    self.assertGreater(gui.root.winfo_height(), 0)

                    for geometry in ("800x600", "960x700", "1280x900"):
                        gui.root.geometry(geometry)
                        gui.root.update_idletasks()
                        self.assertGreater(gui.root.winfo_width(), 0)
                        self.assertGreater(gui.root.winfo_height(), 0)

                    gui.ui_scale_var.set(2.0)
                    gui._set_theme_mode("dark")
                    gui._apply_ui_scaling()
                    gui._apply_theme()
                    gui._restore_default_startup_geometry()
                    gui.root.update_idletasks()
                    self.assertEqual(gui.root.winfo_width(), 960)
                    self.assertEqual(gui.root.winfo_height(), 700)

                    style = gui.root.tk.call
                    with mock.patch("generate_textures.save_gui_state"):
                        for scale in ("0.9", "1.0", "1.15", "1.3", "1.5"):
                            gui.ui_scale_combo.set(scale)
                            gui._on_ui_scale_changed()
                            self.assertAlmostEqual(float(gui.ui_scale_var.get()), float(scale))
                            for theme_mode, palette in (("light", _LIGHT_THEME), ("dark", _DARK_THEME), ("high_contrast", _HIGH_CONTRAST_THEME)):
                                gui._set_theme_mode(theme_mode)
                                gui._restore_default_startup_geometry()
                                self.assertEqual(
                                    style("ttk::style", "lookup", "Muted.TLabel", "-foreground"),
                                    palette["muted_fg"],
                                )
                                self.assertEqual(
                                    style("ttk::style", "lookup", "Status.Error.TLabel", "-foreground"),
                                    palette["error_fg"],
                                )
                finally:
                    if gui is not None:
                        gui.root.destroy()
        finally:
            state_file.unlink(missing_ok=True)

    def test_build_nif_editor_action_guidance_is_profile_aware(self) -> None:
        vanilla_guidance = build_nif_editor_action_guidance("vanilla")
        self.assertIn("Patch recipe", vanilla_guidance)
        self.assertIn("standard parallax", vanilla_guidance)
        self.assertIn("Likely mismatch", vanilla_guidance)
        self.assertIn("Selected NIF", vanilla_guidance)
        self.assertIn("Safety default: start in dry-run mode", vanilla_guidance)
        self.assertIn("_cm.dds", build_nif_editor_action_guidance("community_shaders"))
        self.assertIn("PBR flag", build_nif_editor_action_guidance("truepbr"))
        self.assertIn("ENB POM", build_nif_editor_action_guidance("enb"))
        self.assertIn("Fallout writes are guarded", build_nif_editor_action_guidance("vanilla", target_game="fallout"))
        self.assertIn("wrong renderer workflow", build_nif_editor_action_guidance("community_shaders"))

    def test_build_nif_editor_action_guidance_reports_detected_workflow_hints(self) -> None:
        guidance = build_nif_editor_action_guidance("vanilla", nif_path="textures/character/armor_n.dds")
        self.assertIn("Detected workflow: Characters / Skin", guidance)
        self.assertIn("safe patch path", guidance)

        mismatch = build_nif_editor_action_guidance("enb", nif_path="textures/architecture/stone_cm.dds")
        self.assertIn("Detected workflow: Community Shaders", mismatch)
        self.assertIn("do not force the mesh onto", mismatch)

    def test_tk_nif_editor_opens_all_tabs_and_closes_cleanly(self) -> None:
        self._skip_unavailable_tk_display()
        if not NIF_PATCHER_AVAILABLE:
            self.skipTest("NIF patcher is unavailable in this environment.")

        gui = None
        try:
            with mock.patch(
                "generate_textures.load_gui_state",
                return_value=load_gui_state(Path("/missing/gui-state.json")),
            ):
                gui = TextureGeneratorGUI()
            gui._open_nif_editor()
            gui.root.update()
            editors = [
                widget for widget in gui.root.winfo_children()
                if widget.winfo_class() == "Toplevel"
            ]
            self.assertEqual(len(editors), 1)
            editor = editors[0]
            notebooks = []
            pending = list(editor.winfo_children())
            while pending:
                widget = pending.pop()
                if widget.winfo_class() == "TNotebook":
                    notebooks.append(widget)
                pending.extend(widget.winfo_children())
            self.assertEqual(len(notebooks), 1)
            self.assertEqual(
                [notebooks[0].tab(tab_id, "text") for tab_id in notebooks[0].tabs()],
                ["Patch setup", "Texture slots", "Disable / clear", "Help"],
            )
            self.assertEqual(str(gui.root.grab_current()), str(editor))
            path_label = None
            scan_button = None
            results_tree = None
            pending = list(editor.winfo_children())
            while pending:
                widget = pending.pop()
                if widget.winfo_class() == "TLabel" and widget.cget("text") == "Path:":
                    path_label = widget
                elif widget.winfo_class() == "TButton" and widget.cget("text") == "Scan NIFs":
                    scan_button = widget
                elif widget.winfo_class() == "Treeview":
                    results_tree = widget
                pending.extend(widget.winfo_children())
            self.assertIsNotNone(path_label)
            self.assertIsNotNone(scan_button)
            self.assertIsNotNone(results_tree)
            path_entries = [
                widget for widget in path_label.master.winfo_children()
                if widget.winfo_class() == "TEntry"
            ]
            self.assertEqual(len(path_entries), 1)
            with tempfile.TemporaryDirectory() as temp_dir:
                nif_path = Path(temp_dir) / "scan_failure.nif"
                nif_path.write_bytes(b"fixture")
                gui.root.setvar(str(path_entries[0].cget("textvariable")), str(nif_path))
                with mock.patch(
                    "generate_textures.discover_plugin_conflict_context_from_manager",
                    side_effect=RuntimeError("simulated startup failure"),
                ):
                    scan_button.invoke()
                    deadline = time.monotonic() + 5.0
                    while scan_button.instate(("disabled",)) and time.monotonic() < deadline:
                        gui.root.update()
                        time.sleep(0.01)
                    gui.root.update()
                self.assertFalse(scan_button.instate(("disabled",)))
                result_values = [
                    results_tree.item(item_id, "values")
                    for item_id in results_tree.get_children()
                ]
                self.assertTrue(
                    any(
                        values[0] == "FAIL"
                        and values[1] == "NIF Editor"
                        and "simulated startup failure" in values[2]
                        for values in result_values
                    )
                )
            close_buttons = []
            pending = list(editor.winfo_children())
            while pending:
                widget = pending.pop()
                if widget.winfo_class() == "TButton" and widget.cget("text") == "Close":
                    close_buttons.append(widget)
                pending.extend(widget.winfo_children())
            self.assertEqual(len(close_buttons), 1)
            close_buttons[0].invoke()
            gui.root.update()
            self.assertFalse(bool(editor.winfo_exists()))
            self.assertIsNone(gui.root.grab_current())
        finally:
            if gui is not None:
                gui.root.destroy()

    def test_normalize_gui_state_clamps_slider_values_and_sanitizes_modes(self) -> None:
        normalized = _normalize_gui_state(
            {
                "preview_size": "NOPE",
                "complex_format": "bad",
                "env_mask_mode": "invalid",
                "parallax_mode": "occlusion",
                "render_profile": "mystery",
                "nif_render_profile": "mystery",
                "auto_optimize_large_batches": 0,
                "lazy_preview_mode": 0,
                "staged_preview_mode": 0,
                "batch_resume_mode": "unsupported",
                "simplified_main_layout": 0,
                "show_advanced_generation_controls": 1,
                "show_advanced_workflow_outputs": 1,
                "workflow_mode": "advanced",
                "normal_strength": 500,
                "parallax_strength": -10,
                "glow_threshold": 999,
                "environment_mask_strength": 99,
                "complex_strength": -99,
                "specular_strength": 0,
            }
        )
        self.assertEqual(str(normalized["preview_size"]), "Medium")
        self.assertEqual(str(normalized["complex_format"]), "msn")
        self.assertEqual(str(normalized["env_mask_mode"]), "standard")
        self.assertEqual(str(normalized["parallax_mode"]), "occlusion (ENB/POM)")
        self.assertEqual(str(normalized["render_profile"]), "custom")
        self.assertEqual(str(normalized["nif_render_profile"]), "custom")
        self.assertFalse(bool(normalized["auto_optimize_large_batches"]))
        self.assertFalse(bool(normalized["lazy_preview_mode"]))
        self.assertFalse(bool(normalized["staged_preview_mode"]))
        self.assertEqual(str(normalized["batch_resume_mode"]), "start_fresh")
        self.assertEqual(str(normalized["workflow_mode"]), "advanced")
        self.assertFalse(bool(normalized["simplified_main_layout"]))
        self.assertTrue(bool(normalized["show_advanced_generation_controls"]))
        self.assertTrue(bool(normalized["show_advanced_workflow_outputs"]))
        self.assertAlmostEqual(float(normalized["normal_strength"]), 12.0)
        self.assertAlmostEqual(float(normalized["parallax_strength"]), 0.1)
        self.assertEqual(int(normalized["glow_threshold"]), 255)
        self.assertAlmostEqual(float(normalized["environment_mask_strength"]), 12.0)
        self.assertAlmostEqual(float(normalized["complex_strength"]), 0.1)
        self.assertAlmostEqual(float(normalized["specular_strength"]), 0.1)

    def test_discover_ui_languages_includes_default_and_detects_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            translations_dir = Path(temp_dir)
            (translations_dir / "de.json").write_text(
                json.dumps({"meta": {"display_name": "Deutsch"}}), encoding="utf-8"
            )
            languages = discover_ui_languages(translations_dir)
        self.assertIn("en", languages)
        self.assertIn("de", languages)

    def test_load_ui_translations_reads_string_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            translations_dir = Path(temp_dir)
            (translations_dir / "en.json").write_text(
                json.dumps({"strings": {"Hello": "Hi"}}), encoding="utf-8"
            )
            translations = load_ui_translations("en", translations_dir)
        self.assertEqual(translations.get("Hello"), "Hi")

    def test_default_translation_catalog_offers_multiple_languages(self) -> None:
        languages = discover_ui_languages()
        self.assertIn("en", languages)
        self.assertIn("de", languages)
        self.assertIn("es", languages)

    def test_non_english_translation_catalogs_match_en_keyset(self) -> None:
        translations_dir = Path(__file__).resolve().parents[1] / "assets" / "translations"
        en_payload = json.loads((translations_dir / "en.json").read_text(encoding="utf-8"))
        en_strings = en_payload.get("strings", {})
        self.assertIsInstance(en_strings, dict)
        en_keys = set(en_strings.keys())
        self.assertGreater(len(en_keys), 0)

        for catalog_path in sorted(translations_dir.glob("*.json")):
            if catalog_path.name == "en.json":
                continue
            payload = json.loads(catalog_path.read_text(encoding="utf-8"))
            strings = payload.get("strings", {})
            self.assertIsInstance(strings, dict, f"{catalog_path.name}: strings must be an object")
            keys = set(strings.keys())
            missing = sorted(en_keys - keys)
            extras = sorted(keys - en_keys)
            self.assertEqual(
                missing,
                [],
                f"{catalog_path.name}: missing translation keys: {missing[:10]}",
            )
            self.assertEqual(
                extras,
                [],
                f"{catalog_path.name}: stale/unknown translation keys: {extras[:10]}",
            )

    def test_translate_ui_text_formats_tokens(self) -> None:
        self.assertEqual(
            translate_ui_text("Ready {value}", {"Ready {value}": "Done {value}"}),
            "Done {value}",
        )

    def test_compute_effective_ui_scale_clamps_range(self) -> None:
        self.assertAlmostEqual(compute_effective_ui_scale(pixels_per_inch=40, user_scale=0.1), 0.8)
        self.assertAlmostEqual(compute_effective_ui_scale(pixels_per_inch=800, user_scale=5), 2.5)
        self.assertAlmostEqual(compute_effective_ui_scale(pixels_per_inch=96, user_scale=1.25), 1.25)

    def test_qt_initial_window_size_fits_screen_with_safe_margins(self) -> None:
        self.assertEqual(_compute_qt_initial_window_size(1920, 1080), (1080, 760))
        self.assertEqual(_compute_qt_initial_window_size(1280, 720), (1080, 624))
        self.assertEqual(_compute_qt_initial_window_size(800, 600), (704, 504))
        self.assertEqual(_compute_qt_initial_window_size(40, 30), (1, 1))

    def test_extract_mesh_paths_from_plugin_bytes_parses_nif_paths(self) -> None:
        blob = (
            b"meshes\\architecture\\stone.nif\x00"
            b"meshes/architecture/stone.nif\x00"
            b"meshes\\effects\\bad.txt\x00"
        )
        paths = extract_mesh_paths_from_plugin_bytes(blob)
        self.assertEqual(paths, ("meshes\\architecture\\stone.nif",))

    def test_extract_plugin_mesh_record_refs_parses_record_id_and_type(self) -> None:
        mesh_bytes = b"meshes\\architecture\\stone.nif\x00"
        record_payload = (
            b"EDID"
            + (4).to_bytes(2, "little")
            + b"Test"
            + b"MODL"
            + len(mesh_bytes).to_bytes(2, "little")
            + mesh_bytes
        )
        record = (
            b"STAT"
            + len(record_payload).to_bytes(4, "little")
            + (0).to_bytes(4, "little")
            + int("1234ABCD", 16).to_bytes(4, "little")
            + (0).to_bytes(4, "little")
            + (0).to_bytes(4, "little")
            + record_payload
        )
        refs = extract_plugin_mesh_record_refs(record)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].mesh_path, "meshes\\architecture\\stone.nif")
        self.assertEqual(refs[0].record_id, "1234ABCD")
        self.assertEqual(refs[0].record_type, "STAT")

    def test_discover_plugin_conflict_context_from_manager_collects_mesh_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            game_root = Path(temp_dir) / "Skyrim"
            plugin_path = game_root / "Data" / "Example.esp"
            plugin_path.parent.mkdir(parents=True)
            plugin_path.write_bytes(b"meshes\\architecture\\stone.nif\x00")
            context = ModManagerContext(
                manager="Vortex",
                game_root=game_root,
                enabled_plugins=("Example.esp",),
            )
            discovered = discover_plugin_conflict_context_from_manager(
                [Path("meshes/architecture/stone.nif")],
                context,
            )
        self.assertIn("meshes/architecture/stone.nif", discovered)
        self.assertEqual(
            discovered["meshes/architecture/stone.nif"][0].plugin_name,
            "Example.esp",
        )
        self.assertEqual(
            discovered["meshes/architecture/stone.nif"][0].record_type,
            "",
        )

    def test_discover_plugin_conflict_context_from_manager_records_metadata_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            game_root = Path(temp_dir) / "Skyrim"
            plugin_path = game_root / "Data" / "Example.esp"
            plugin_path.parent.mkdir(parents=True)
            mesh_bytes = b"meshes\\architecture\\stone.nif\x00"
            payload = (
                b"MODL"
                + len(mesh_bytes).to_bytes(2, "little")
                + mesh_bytes
            )
            plugin_path.write_bytes(
                b"STAT"
                + len(payload).to_bytes(4, "little")
                + (0).to_bytes(4, "little")
                + int("00000ABC", 16).to_bytes(4, "little")
                + (0).to_bytes(4, "little")
                + (0).to_bytes(4, "little")
                + payload
            )
            context = ModManagerContext(
                manager="Vortex",
                game_root=game_root,
                enabled_plugins=("Example.esp",),
            )
            discovered = discover_plugin_conflict_context_from_manager(
                [Path("meshes/architecture/stone.nif")],
                context,
            )
        self.assertEqual(discovered["meshes/architecture/stone.nif"][0].record_id, "00000ABC")
        self.assertEqual(discovered["meshes/architecture/stone.nif"][0].record_type, "STAT")

    def test_discover_plugin_conflict_context_normalizes_starred_plugin_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            game_root = Path(temp_dir) / "Skyrim"
            plugin_path = game_root / "Data" / "Example.esp"
            plugin_path.parent.mkdir(parents=True)
            plugin_path.write_bytes(b"meshes\\architecture\\stone.nif\x00")
            context = ModManagerContext(
                manager="Vortex",
                game_root=game_root,
                enabled_plugins=("*Example.esp",),
                load_order=("Example.esp # active",),
            )
            discovered = discover_plugin_conflict_context_from_manager(
                [Path("meshes/architecture/stone.nif")],
                context,
            )
        self.assertIn("meshes/architecture/stone.nif", discovered)
        self.assertEqual(discovered["meshes/architecture/stone.nif"][0].plugin_name, "Example.esp")

    def test_discover_plugin_conflict_context_resolves_case_insensitive_plugin_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            game_root = Path(temp_dir) / "Skyrim"
            plugin_path = game_root / "Data" / "Example.ESP"
            plugin_path.parent.mkdir(parents=True)
            plugin_path.write_bytes(b"meshes\\architecture\\stone.nif\x00")
            context = ModManagerContext(
                manager="Vortex",
                game_root=game_root,
                enabled_plugins=("example.esp",),
            )
            discovered = discover_plugin_conflict_context_from_manager(
                [Path("meshes/architecture/stone.nif")],
                context,
            )
        self.assertIn("meshes/architecture/stone.nif", discovered)
        self.assertEqual(discovered["meshes/architecture/stone.nif"][0].plugin_name.lower(), "example.esp")

    def test_normalize_gui_state_accepts_truepbr_alias(self) -> None:
        normalized = _normalize_gui_state({"render_profile": "true pbr"})
        self.assertEqual(str(normalized["render_profile"]), "truepbr")

    def test_normalize_gui_state_maps_non_renderer_aliases_to_custom(self) -> None:
        normalized = _normalize_gui_state({"render_profile": "experimental"})
        self.assertEqual(str(normalized["render_profile"]), "custom")

    def test_normalize_gui_state_maps_performance_and_vr_profiles_to_custom(self) -> None:
        performance = _normalize_gui_state({"render_profile": "performance"})
        vr = _normalize_gui_state({"render_profile": "vr"})
        self.assertEqual(str(performance["render_profile"]), "custom")
        self.assertEqual(str(vr["render_profile"]), "custom")

    def test_normalize_gui_state_maps_extended_non_renderer_profiles_to_custom(self) -> None:
        terrain = _normalize_gui_state({"render_profile": "terrain"})
        architecture = _normalize_gui_state({"render_profile": "architecture"})
        characters = _normalize_gui_state({"render_profile": "characters"})
        self.assertEqual(str(terrain["render_profile"]), "custom")
        self.assertEqual(str(architecture["render_profile"]), "custom")
        self.assertEqual(str(characters["render_profile"]), "custom")

    def test_gui_render_profile_values_only_include_renderer_choices(self) -> None:
        self.assertEqual(
            _RENDER_PROFILE_GUI_VALUES,
            ("custom", "vanilla", "community_shaders", "truepbr", "enb"),
        )

    def test_generate_diffuse_returns_rgb_same_size(self) -> None:
        diffuse = generate_diffuse(_sample_image())
        self.assertEqual(diffuse.mode, "RGB")
        self.assertEqual(diffuse.size, (8, 8))

    def test_generate_parallax_returns_l_same_size(self) -> None:
        parallax = generate_parallax(_sample_image())
        self.assertEqual(parallax.mode, "L")
        self.assertEqual(parallax.size, (8, 8))

    def test_generate_parallax_produces_non_flat_height_map(self) -> None:
        parallax = generate_parallax(_vertical_gradient_image(), strength=1.35)
        values = set(parallax.tobytes())
        self.assertGreater(len(values), 2)

    def test_generate_parallax_strength_slider_has_stronger_min_max_separation(self) -> None:
        source = _detailed_bright_image()
        low = generate_parallax(source, strength=0.2)
        high = generate_parallax(source, strength=6.0)
        low_range = low.getextrema()[1] - low.getextrema()[0]
        high_range = high.getextrema()[1] - high.getextrema()[0]
        self.assertGreater(high_range, low_range + 20)

    def test_generate_normal_returns_rgb_same_size(self) -> None:
        normal = generate_normal(_sample_image())
        self.assertEqual(normal.mode, "RGB")
        self.assertEqual(normal.size, (8, 8))

    def test_generate_normal_directx_green_channel_differs_from_opengl(self) -> None:
        gradient = _vertical_gradient_image()
        directx_normal = generate_normal(gradient, strength=1.0, directx=True)
        opengl_normal = generate_normal(gradient, strength=1.0, directx=False)
        _, directx_green, _ = directx_normal.split()
        _, opengl_green, _ = opengl_normal.split()
        sample_coord = (12, 12)
        self.assertNotEqual(directx_green.getpixel(sample_coord), opengl_green.getpixel(sample_coord))

    def test_generate_normal_defaults_to_directx_orientation(self) -> None:
        gradient = _vertical_gradient_image()
        default_normal = generate_normal(gradient, strength=1.0)
        directx_normal = generate_normal(gradient, strength=1.0, directx=True)
        self.assertEqual(default_normal.tobytes(), directx_normal.tobytes())

    def test_generate_normal_blue_channel_stays_in_skyrim_safe_range(self) -> None:
        gradient = _vertical_gradient_image()
        normal = generate_normal(gradient, strength=2.0)
        _, _, blue = normal.split()
        minimum_blue, _ = blue.getextrema()
        self.assertGreaterEqual(minimum_blue, 128)

    def test_enforce_skyrim_output_profile_normal_lifts_blue_floor(self) -> None:
        bad_normal = Image.new("RGB", (8, 8), color=(120, 120, 40))
        conformed = enforce_skyrim_output_profile("normal", bad_normal)
        _, _, blue = conformed.split()
        minimum_blue, _ = blue.getextrema()
        self.assertEqual(conformed.mode, "RGB")
        self.assertGreaterEqual(minimum_blue, 128)

    def test_enforce_skyrim_output_profile_standard_env_mask_is_l_mode(self) -> None:
        rgba_mask = Image.new("RGBA", (8, 8), color=(120, 80, 40, 255))
        conformed = enforce_skyrim_output_profile("environment_mask", rgba_mask, env_mask_mode="standard")
        self.assertEqual(conformed.mode, "L")

    def test_enforce_skyrim_output_profile_complex_material_is_rgba(self) -> None:
        rgb_complex = Image.new("RGB", (8, 8), color=(40, 60, 80))
        conformed = enforce_skyrim_output_profile("complex_material", rgb_complex, complex_format="cm")
        self.assertEqual(conformed.mode, "RGBA")

    def test_generate_normal_flat_surface_stays_near_neutral(self) -> None:
        normal = generate_normal(_flat_dark_image(), strength=2.0)
        red, green, blue = normal.split()
        red_min, red_max = red.getextrema()
        green_min, green_max = green.getextrema()
        blue_min, blue_max = blue.getextrema()
        self.assertLessEqual(red_max - red_min, 4)
        self.assertLessEqual(green_max - green_min, 4)
        self.assertLessEqual(blue_max - blue_min, 4)
        self.assertGreaterEqual(red.getpixel((8, 8)), 120)
        self.assertLessEqual(red.getpixel((8, 8)), 136)
        self.assertGreaterEqual(green.getpixel((8, 8)), 120)
        self.assertLessEqual(green.getpixel((8, 8)), 136)
        self.assertGreaterEqual(blue.getpixel((8, 8)), 245)

    def test_generate_glow_returns_l_same_size(self) -> None:
        glow = generate_glow(_sample_image())
        self.assertEqual(glow.mode, "L")
        self.assertEqual(glow.size, (8, 8))

    def test_generate_environment_mask_returns_l_same_size(self) -> None:
        environment_mask = generate_environment_mask(_sample_image())
        self.assertEqual(environment_mask.mode, "L")
        self.assertEqual(environment_mask.size, (8, 8))

    def test_generate_environment_mask_flat_surface_avoids_black_holes(self) -> None:
        environment_mask = generate_environment_mask(_flat_dark_image(), strength=2.2)
        env_min, env_max = environment_mask.getextrema()
        self.assertGreaterEqual(env_min, 10)
        self.assertLessEqual(env_max - env_min, 80)

    def test_generate_complex_material_returns_rgba_same_size(self) -> None:
        complex_material = generate_complex_material(_sample_image())
        self.assertEqual(complex_material.mode, "RGBA")
        self.assertEqual(complex_material.size, (8, 8))

    def test_generate_complex_material_flat_surface_avoids_black_holes(self) -> None:
        complex_material = generate_complex_material(_flat_dark_image(), strength=2.2)
        extrema = complex_material.getextrema()
        for minimum, maximum in extrema:
            self.assertGreaterEqual(minimum, 3)
            self.assertLessEqual(maximum - minimum, 90)

    def test_generate_complex_material_is_not_normal_map_like(self) -> None:
        source = _vertical_gradient_image()
        complex_material = generate_complex_material(source, strength=1.5)
        normal = generate_normal(source, strength=1.5)
        self.assertNotEqual(complex_material.split()[:3], normal.split())

    def test_generate_complex_material_channel_order_matches_packed_contract(self) -> None:
        source = _vertical_gradient_image()
        complex_material = generate_complex_material(source, strength=1.4)
        env_reflection, glossiness, metallic, height = complex_material.split()
        self.assertNotEqual(env_reflection.tobytes(), glossiness.tobytes())
        self.assertNotEqual(glossiness.tobytes(), metallic.tobytes())
        self.assertNotEqual(height.tobytes(), env_reflection.tobytes())

    def test_generate_msn_returns_rgba_same_size(self) -> None:
        msn = generate_msn(_sample_image())
        self.assertEqual(msn.mode, "RGBA")
        self.assertEqual(msn.size, (8, 8))

    def test_generate_msn_rgb_matches_normal_and_alpha_matches_specular(self) -> None:
        source = _vertical_gradient_image()
        normal_strength = 1.8
        specular_strength = 1.2
        msn = generate_msn(source, normal_strength=normal_strength, specular_strength=specular_strength)
        expected_normal = generate_normal(source, strength=normal_strength)
        expected_specular = generate_specular(source, strength=specular_strength)
        self.assertEqual(msn.split()[:3], expected_normal.split())
        self.assertEqual(msn.split()[3].tobytes(), expected_specular.tobytes())

    def test_generate_ambient_occlusion_returns_l_mode_same_size(self) -> None:
        source = _sample_image()
        ao = generate_ambient_occlusion(source)
        self.assertEqual(ao.mode, "L")
        self.assertEqual(ao.size, source.size)

    def test_generate_ambient_occlusion_has_no_pure_black_holes(self) -> None:
        source = _sample_image()
        ao = generate_ambient_occlusion(source, strength=1.0)
        min_val = min(ao.getdata())
        self.assertGreater(min_val, 0, "AO should have no pure-black pixels")

    def test_generate_ambient_occlusion_higher_strength_increases_contrast(self) -> None:
        source = _vertical_gradient_image()
        ao_low = generate_ambient_occlusion(source, strength=0.5)
        ao_high = generate_ambient_occlusion(source, strength=3.0)
        import numpy as np
        std_low = float(np.std(list(ao_low.getdata())))
        std_high = float(np.std(list(ao_high.getdata())))
        self.assertGreater(std_high, std_low)

    def test_generate_roughness_returns_l_mode_same_size(self) -> None:
        source = _sample_image()
        roughness = generate_roughness(source)
        self.assertEqual(roughness.mode, "L")
        self.assertEqual(roughness.size, source.size)

    def test_generate_roughness_has_no_pure_black_holes(self) -> None:
        source = _sample_image()
        roughness = generate_roughness(source, strength=1.0)
        min_val = min(roughness.getdata())
        self.assertGreater(min_val, 0, "Roughness should have no pure-black pixels")

    def test_generate_roughness_metal_is_smoother_than_stone(self) -> None:
        source = _vertical_gradient_image()
        roughness_metal = generate_roughness(source, material_type="metal")
        roughness_stone = generate_roughness(source, material_type="stone")
        mean_metal = sum(roughness_metal.getdata()) / (source.width * source.height)
        mean_stone = sum(roughness_stone.getdata()) / (source.width * source.height)
        self.assertLess(mean_metal, mean_stone, "Metal should have lower roughness than stone")

    def test_prefilter_mipmap_stability_normal_preserves_size_and_mode(self) -> None:
        source = _sample_image().convert("RGB")
        result = _prefilter_for_mipmap_stability(source, "normal")
        self.assertEqual(result.size, source.size)
        self.assertEqual(result.mode, "RGB")

    def test_prefilter_mipmap_stability_normal_blue_channel_raised(self) -> None:
        # Pre-filter blends blue toward 255 (flat normal) — mean should rise slightly.
        source = _sample_image().convert("RGB")
        original_b = list(source.getchannel("B").getdata())
        result = _prefilter_for_mipmap_stability(source, "normal")
        filtered_b = list(result.getchannel("B").getdata())
        mean_orig = sum(original_b) / len(original_b)
        mean_filtered = sum(filtered_b) / len(filtered_b)
        self.assertGreaterEqual(mean_filtered, mean_orig - 1)  # should not darken blue

    def test_prefilter_mipmap_stability_parallax_preserves_size_and_mode(self) -> None:
        source = _sample_image().convert("L")
        result = _prefilter_for_mipmap_stability(source, "parallax")
        self.assertEqual(result.size, source.size)
        self.assertEqual(result.mode, "L")

    def test_prefilter_mipmap_stability_passthrough_for_diffuse(self) -> None:
        source = _sample_image().convert("RGB")
        result = _prefilter_for_mipmap_stability(source, "diffuse")
        self.assertEqual(result.tobytes(), source.tobytes())

    def test_prefilter_mipmap_stability_passthrough_for_unknown_kind(self) -> None:
        source = _sample_image().convert("RGB")
        result = _prefilter_for_mipmap_stability(source, "")
        self.assertEqual(result.tobytes(), source.tobytes())

    def test_analyze_material_type_from_image_returns_none_or_valid_type(self) -> None:
        valid_types = {
            "metal", "stone", "wood", "leather", "fur", "cloth", "glass", "skin",
            "snow", "plants", "terrain", "dirt", "sand", "architecture", "paper", "general", None,
        }
        result = analyze_material_type_from_image(_sample_image())
        self.assertIn(result, valid_types)

    def test_analyze_material_type_from_image_accepts_rgba_input(self) -> None:
        source = _sample_image().convert("RGBA")
        result = analyze_material_type_from_image(source)
        # Should not raise; result is None or a valid type string
        valid_types = {
            "metal", "stone", "wood", "leather", "fur", "cloth", "glass", "skin",
            "snow", "plants", "terrain", "dirt", "sand", "architecture", "paper", "general", None,
        }
        self.assertIn(result, valid_types)

    def test_recommend_generation_settings_returns_valid_ranges(self) -> None:
        settings = recommend_generation_settings(_sample_image())
        self.assertIn("specular_strength", settings)
        self.assertIn("complex_strength", settings)
        self.assertIn("environment_mask_strength", settings)
        self.assertGreaterEqual(float(settings["normal_strength"]), 1.1)
        self.assertLessEqual(float(settings["normal_strength"]), 3.8)
        self.assertGreaterEqual(float(settings["parallax_strength"]), 0.8)
        self.assertLessEqual(float(settings["parallax_strength"]), 2.4)
        self.assertGreaterEqual(float(settings["specular_strength"]), 0.9)
        self.assertLessEqual(float(settings["specular_strength"]), 2.2)
        self.assertGreaterEqual(int(settings["glow_threshold"]), 140)
        self.assertLessEqual(int(settings["glow_threshold"]), 235)

    def test_classify_material_type_returns_architecture_for_brick_path(self) -> None:
        # textures/architecture/ folder is now its own category; stone is the fallback
        # when only the filename has stone tokens but no architecture folder token.
        self.assertEqual(classify_material_type(Path("textures/architecture/brick_wall.dds")), "architecture")

    def test_classify_material_type_returns_stone_for_non_architecture_brick_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/dungeons/brick_floor.dds")), "stone")

    def test_classify_material_type_returns_metal_for_iron_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/armor/iron_helmet.dds")), "metal")

    def test_classify_material_type_returns_plants_for_leaf_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/plants/leaf01.dds")), "plants")

    def test_classify_material_type_returns_wood_for_timber_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/clutter/timber_plank.dds")), "wood")

    def test_classify_material_type_returns_general_for_unknown_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/misc/unknown.dds")), "general")

    def test_classify_material_type_returns_leather_for_hide_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/armor/leather/hide_strap.dds")), "leather")

    def test_classify_material_type_returns_fur_for_pelt_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/armor/fur/wolf_pelt.dds")), "fur")

    def test_classify_material_type_returns_dirt_for_mud_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/clutter/mud_pile.dds")), "dirt")

    def test_classify_material_type_returns_sand_for_dune_token(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/environment/dune_surface.dds")), "sand")

    def test_classify_material_type_returns_sand_for_sand_token(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/misc/sand_pile.dds")), "sand")

    def test_classify_material_type_dirt_path_classifies_as_dirt_not_sand(self) -> None:
        # "dirt" is a dirt token; sand no longer includes it so a dirt-named path stays dirt
        self.assertEqual(classify_material_type(Path("textures/clutter/dirt_road.dds")), "dirt")

    def test_classify_material_type_returns_paper_for_cards_path(self) -> None:
        self.assertEqual(
            classify_material_type(Path("textures/interface/cards/collectible_waifu_card_01.dds")),
            "paper",
        )

    def test_classify_material_type_returns_paper_for_sign_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/signs/inn_sign_painted.dds")), "paper")

    def test_classify_material_type_returns_terrain_for_landscape_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/landscape/dirt01.dds")), "terrain")

    def test_classify_material_type_returns_terrain_for_ground_token_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/ground/rocky_terrain.dds")), "terrain")

    def test_classify_material_type_returns_architecture_for_whiterun_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/whiterun/wrbuildings01.dds")), "architecture")

    def test_classify_material_type_returns_architecture_for_dwemer_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/dwemer/dwmplatform01.dds")), "architecture")

    def test_classify_material_type_returns_architecture_for_nordic_ruin(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/nordic/norwall01.dds")), "architecture")

    def test_detect_workflow_profile_detects_interface_paths(self) -> None:
        self.assertEqual(
            detect_workflow_profile(Path("textures/interface/cards/deck01.dds")),
            "interface",
        )

    def test_interface_render_profile_is_normalized_to_vanilla(self) -> None:
        self.assertEqual(_normalize_render_profile("interface"), "vanilla")
        self.assertEqual(recommend_render_profile(Path("textures/interface/cards/collectible.dds")), "vanilla")
        self.assertIn("safe patch path: do not force the mesh onto Vanilla", build_nif_editor_action_guidance("vanilla", nif_path="textures/interface/cards/collectible.dds"))

    def test_detect_workflow_profile_detects_renderer_path_hints_for_selected_nif(self) -> None:
        self.assertEqual(
            detect_workflow_profile(Path("mods/Dynamic Cube Maps/textures/architecture/stone.dds")),
            "community_shaders",
        )
        self.assertEqual(
            detect_workflow_profile(Path("mods/Community Shaders/textures/architecture/stone.dds")),
            "community_shaders",
        )
        self.assertEqual(
            detect_workflow_profile(Path("mods/ENBSeries/textures/architecture/stone.dds")),
            "enb",
        )
        self.assertEqual(
            detect_workflow_profile(Path("mods/True PBR/textures/architecture/stone.dds")),
            "truepbr",
        )
        self.assertEqual(
            detect_workflow_profile(Path("mods/TruePBR/textures/architecture/stone.dds")),
            "truepbr",
        )
        self.assertEqual(
            detect_workflow_profile(Path("Data/PBRTextureSets/LandscapeDirt01.json")),
            "truepbr",
        )

    def test_detect_workflow_profile_detects_renderer_and_content_profiles(self) -> None:
        self.assertEqual(detect_workflow_profile(Path("textures/vr/armor/helmet.dds")), "vr")
        self.assertEqual(detect_workflow_profile(Path("textures/performance/stone.dds")), "performance")
        self.assertEqual(detect_workflow_profile(Path("textures/architecture/ruins/wall.dds")), "architecture")

    def test_recommend_render_profile_detects_enb_from_path_hint(self) -> None:
        self.assertEqual(
            recommend_render_profile(Path("mods/ENBSeries/textures/architecture/stone.dds")),
            "enb",
        )

    def test_recommend_render_profile_detects_community_shaders_from_cm_role(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("textures/architecture/stone_cm.dds"),
                detected_role="complex_material_cm",
            ),
            "community_shaders",
        )

    def test_recommend_render_profile_prefers_detected_renderer_over_architecture_material_hint(self) -> None:
        self.assertEqual(
            recommend_render_profile(Path("textures/architecture/stone_cm.dds")),
            "community_shaders",
        )
        self.assertEqual(
            recommend_render_profile(Path("textures/architecture/stone_rmaos.dds")),
            "truepbr",
        )
        self.assertEqual(
            recommend_render_profile(Path("mods/ENBSeries/textures/architecture/stone.dds")),
            "enb",
        )

    def test_recommend_render_profile_prefers_vanilla_for_interface_or_paper(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("textures/interface/cards/collectible.dds"),
                material_type="paper",
                workflow_profile="interface",
            ),
            "vanilla",
        )

    def test_recommend_render_profile_defaults_to_vanilla_without_renderer_hints(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("textures/clutter/misc/metalplate.dds"),
                source=_detailed_low_saturation_image(),
            ),
            "vanilla",
        )

    def test_recommend_render_profile_infers_content_profile_from_path_when_unspecified(self) -> None:
        self.assertEqual(
            recommend_render_profile(Path("textures/architecture/stone/wall.dds")),
            "architecture",
        )
        self.assertEqual(
            recommend_render_profile(Path("textures/landscape/mountain.dds")),
            "terrain",
        )
        self.assertEqual(
            recommend_render_profile(Path("textures/actors/character/face.dds")),
            "characters",
        )

    def test_recommend_render_profile_uses_extended_profiles_for_neutral_material_hints(self) -> None:
        self.assertEqual(
            recommend_render_profile(Path("textures/landscape/mountain.dds"), material_type="terrain"),
            "terrain",
        )
        self.assertEqual(
            recommend_render_profile(Path("textures/architecture/stone/wall.dds"), material_type="architecture"),
            "architecture",
        )
        self.assertEqual(
            recommend_render_profile(Path("textures/actors/character/face.dds"), material_type="skin"),
            "characters",
        )

    def test_recommend_render_profile_uses_workflow_profile_hints(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("textures/architecture/stone.dds"),
                workflow_profile="vr",
            ),
            "vr",
        )
        self.assertEqual(
            recommend_render_profile(
                Path("textures/architecture/stone.dds"),
                workflow_profile="performance",
            ),
            "performance",
        )

    def test_recommend_render_profile_uses_mod_manager_context_hint_when_path_is_neutral(self) -> None:
        context = ModManagerContext(
            manager="MO2",
            loaded_mods=("Community Shaders", "Some Texture Pack"),
        )
        self.assertEqual(
            recommend_render_profile(
                Path("textures/architecture/metalplate.dds"),
                source=_detailed_low_saturation_image(),
                manager_context=context,
            ),
            "community_shaders",
        )

    def test_recommend_render_profile_detects_truepbr_from_rmaos_suffix(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("textures/architecture/stone_rmaos.dds"),
                detected_role="environment_mask",
                detected_suffix="_rmaos",
            ),
            "truepbr",
        )

    def test_recommend_render_profile_detects_truepbr_from_ramos_suffix(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("textures/architecture/stone_ramos.dds"),
                detected_role="environment_mask",
                detected_suffix="_ramos",
            ),
            "truepbr",
        )

    def test_detect_workflow_profile_detects_legacy_truepbr_alias_suffixes(self) -> None:
        for suffix in ("_orm", "_orms", "_mrao", "_mra"):
            self.assertEqual(detect_workflow_profile(Path(f"textures/architecture/stone{suffix}.dds")), "truepbr")

    def test_recommend_render_profile_detects_truepbr_from_orm_suffix(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("textures/architecture/stone_orm.dds"),
                detected_role="environment_mask",
                detected_suffix="_orm",
            ),
            "truepbr",
        )

    def test_recommend_render_profile_details_scores_truepbr_suffix_high(self) -> None:
        details = recommend_render_profile_details(
            Path("textures/architecture/stone_rmaos.dds"),
            detected_role="environment_mask",
            detected_suffix="_rmaos",
        )
        self.assertEqual(details.profile, "truepbr")
        self.assertGreaterEqual(details.confidence, 0.95)
        self.assertIn("TruePBR", details.recommendation_reason)
        self.assertTrue(any("ENB" in text for text in details.why_not_others))
        self.assertTrue(any("Community Shaders" in text for text in details.why_not_others))

    def test_recommend_render_profile_details_warns_on_outdated_truepbr_metadata(self) -> None:
        context = ModManagerContext(
            manager="MO2",
            loaded_mods=("PBRNifPatcher 0.9.0",),
            renderer_version_hints=("Community Shaders PBR 0.9.0",),
        )
        details = recommend_render_profile_details(
            Path("textures/architecture/stone_rmaos.dds"),
            detected_role="environment_mask",
            detected_suffix="_rmaos",
            manager_context=context,
        )
        self.assertEqual(details.profile, "truepbr")
        self.assertIn("release-aligned baseline", details.recommendation_reason)
        self.assertLess(details.confidence, 0.95)

    def test_recommend_render_profile_details_scores_mod_manager_markers_high(self) -> None:
        context = ModManagerContext(
            manager="MO2",
            loaded_mods=("Community Shaders", "Texture Pack"),
        )
        details = recommend_render_profile_details(
            Path("textures/misc/neutral_asset.dds"),
            manager_context=context,
        )
        self.assertEqual(details.profile, "community_shaders")
        self.assertGreaterEqual(details.confidence, 0.85)
        self.assertIn("mod-manager", details.recommendation_reason.lower())
        self.assertTrue(any("TruePBR" in text for text in details.why_not_others))

    def test_recommend_generation_settings_leather_is_less_reflective_than_metal(self) -> None:
        source = _detailed_low_saturation_image()
        leather = recommend_generation_settings(source, Path("textures/armor/leather/hide_strap.dds"))
        metal = recommend_generation_settings(source, Path("textures/armor/steel/plate.dds"))
        self.assertLess(float(leather["environment_mask_strength"]), float(metal["environment_mask_strength"]))
        self.assertLess(float(leather["specular_strength"]), float(metal["specular_strength"]))

    def test_recommend_render_profile_detects_truepbr_from_pbrnifpatcher_path_hint(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("mods/PBRNifPatcher/textures/architecture/stone.dds"),
            ),
            "truepbr",
        )

    def test_recommend_render_profile_detects_truepbr_from_textures_pbr_path_hint(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("mods/MyPack/textures/pbr/architecture/stone.dds"),
            ),
            "truepbr",
        )

    def test_detect_render_profile_from_mod_manager_context_prefers_enb_when_truepbr_and_enb_are_both_detected(self) -> None:
        context = ModManagerContext(
            manager="MO2",
            loaded_mods=("Community Shaders", "PBRNifPatcher", "ENB Light"),
        )
        self.assertEqual(detect_render_profile_from_mod_manager_context(context), "enb")

    def test_detect_render_profile_from_mod_manager_context_prefers_enb_when_enb_and_cs_both_detected(self) -> None:
        context = ModManagerContext(
            manager="Vortex",
            loaded_mods=("Community Shaders", "ENB Light"),
        )
        self.assertEqual(detect_render_profile_from_mod_manager_context(context), "enb")

    def test_detect_render_profile_from_mod_manager_context_uses_plugin_and_load_order_hints(self) -> None:
        context = ModManagerContext(
            manager="MO2",
            enabled_plugins=("CommunityShaders.esp",),
            load_order=("PBRNifPatcher.esp",),
        )
        self.assertEqual(detect_render_profile_from_mod_manager_context(context), "truepbr")

    def test_detect_render_profile_from_mod_manager_context_detects_enb_from_runtime_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "d3d11.dll").write_bytes(b"")
            context = ModManagerContext(
                manager="MO2",
                instance_root=root,
            )
            self.assertEqual(detect_render_profile_from_mod_manager_context(context), "enb")

    def test_detect_render_profile_from_mod_manager_context_detects_cs_from_skse_plugin_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin_dir = root / "SKSE" / "Plugins"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "CommunityShaders.dll").write_bytes(b"")
            context = ModManagerContext(
                manager="Vortex",
                staging_root=root,
            )
            self.assertEqual(detect_render_profile_from_mod_manager_context(context), "community_shaders")

    def test_detect_render_profile_from_mod_manager_context_detects_cs_from_dynamic_cubemap_hint(self) -> None:
        for mod_name, plugin_name in (
            ("Dynamic Cubemap", "DynamicCubemap.esp"),
            ("Dynamic Cubemaps", "DynamicCubemaps.esp"),
            ("Dynamic Cube Map", "DynamicCubeMap.esp"),
            ("Dynamic Cube Maps", "DynamicCubeMaps.esp"),
        ):
            context = ModManagerContext(
                manager="MO2",
                loaded_mods=(mod_name,),
                enabled_plugins=(plugin_name,),
            )
            self.assertEqual(detect_render_profile_from_mod_manager_context(context), "community_shaders")

    def test_recommend_render_profile_detects_cs_from_dynamic_cubemap_path_hint(self) -> None:
        self.assertEqual(
            recommend_render_profile(Path("mods/Dynamic Cube Maps/textures/architecture/stone.dds")),
            "community_shaders",
        )
        self.assertEqual(
            recommend_render_profile(Path("mods/DynamicCubemaps/textures/architecture/stone.dds")),
            "community_shaders",
        )

    def test_detect_render_profile_from_mod_manager_context_detects_cs_from_singular_dynamic_cubemap_runtime_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin_dir = root / "SKSE" / "Plugins"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "DynamicCubemap.dll").write_bytes(b"")
            context = ModManagerContext(
                manager="Vortex",
                staging_root=root,
            )
            self.assertEqual(detect_render_profile_from_mod_manager_context(context), "community_shaders")

    def test_detect_render_profile_from_mod_manager_context_detects_cs_from_plural_dynamic_cubemap_runtime_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin_dir = root / "SKSE" / "Plugins"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "DynamicCubemaps.dll").write_bytes(b"")
            context = ModManagerContext(
                manager="Vortex",
                staging_root=root,
            )
            self.assertEqual(detect_render_profile_from_mod_manager_context(context), "community_shaders")

    def test_detect_render_profile_from_mod_manager_context_detects_truepbr_from_textures_pbr_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pbr_dir = root / "textures" / "pbr"
            pbr_dir.mkdir(parents=True)
            context = ModManagerContext(
                manager="MO2",
                instance_root=root,
            )
            self.assertEqual(detect_render_profile_from_mod_manager_context(context), "truepbr")

    def test_detect_render_profile_from_mod_manager_context_detects_enb_from_game_root_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            game_root = Path(temp_dir) / "SkyrimSE"
            game_root.mkdir(parents=True)
            (game_root / "d3d11.dll").write_bytes(b"")
            context = ModManagerContext(
                manager="MO2",
                game_root=game_root,
            )
            self.assertEqual(detect_render_profile_from_mod_manager_context(context), "enb")

    def test_detect_render_profile_from_mod_manager_context_detects_cs_from_game_data_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            game_root = Path(temp_dir) / "SkyrimSE"
            plugin_dir = game_root / "Data" / "SKSE" / "Plugins"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "CommunityShaders.dll").write_bytes(b"")
            context = ModManagerContext(
                manager="Vortex",
                game_root=game_root,
            )
            self.assertEqual(detect_render_profile_from_mod_manager_context(context), "community_shaders")

    def test_detect_render_profile_from_mod_manager_context_uses_character_fallback_for_body_hints(self) -> None:
        context = ModManagerContext(
            manager="MO2",
            detected_body_profiles=("cbbe",),
        )
        self.assertEqual(detect_render_profile_from_mod_manager_context(context), "characters")

    def test_detect_render_profile_from_mod_manager_context_ignores_fsmp_mcm_helper_mod(self) -> None:
        context = ModManagerContext(
            manager="MO2",
            loaded_mods=("FSMPM - The FSMP MCM",),
            enabled_plugins=("FSMPM - The FSMP MCM.esp",),
            load_order=("FSMPM - The FSMP MCM.esp",),
        )
        self.assertIsNone(detect_render_profile_from_mod_manager_context(context))

    def test_recommend_render_profile_uses_character_fallback_for_mod_manager_body_hints(self) -> None:
        context = ModManagerContext(
            manager="MO2",
            detected_body_profiles=("himbo",),
            detected_skeleton_profiles=("xpmsse",),
        )
        self.assertEqual(
            recommend_render_profile(
                Path("textures/misc/neutral_asset.dds"),
                manager_context=context,
            ),
            "characters",
        )

    def test_recommend_render_profile_ignores_fsmp_mcm_helper_mod(self) -> None:
        context = ModManagerContext(
            manager="MO2",
            loaded_mods=("FSMPM - The FSMP MCM",),
            enabled_plugins=("FSMPM - The FSMP MCM.esp",),
        )
        self.assertEqual(
            recommend_render_profile(
                Path("textures/misc/neutral_asset.dds"),
                manager_context=context,
            ),
            "vanilla",
        )

    def test_resolve_render_profile_options_returns_expected_modes(self) -> None:
        enb = resolve_render_profile_options("enb")
        self.assertEqual(enb["complex_format"], "msn")
        self.assertEqual(enb["env_mask_mode"], "complex")
        self.assertEqual(enb["parallax_mode"], "occlusion")

        performance = resolve_render_profile_options("performance")
        self.assertEqual(performance["complex_format"], "msn")
        self.assertEqual(performance["env_mask_mode"], "standard")
        self.assertEqual(performance["parallax_mode"], "standard")

        vr = resolve_render_profile_options("vr")
        self.assertEqual(vr["complex_format"], "msn")
        self.assertEqual(vr["env_mask_mode"], "standard")
        self.assertEqual(vr["parallax_mode"], "standard")

        terrain = resolve_render_profile_options("terrain")
        self.assertEqual(terrain["complex_format"], "msn")
        self.assertEqual(terrain["env_mask_mode"], "standard")
        self.assertEqual(terrain["parallax_mode"], "standard")

        architecture = resolve_render_profile_options("architecture")
        self.assertEqual(architecture["complex_format"], "msn")
        self.assertEqual(architecture["env_mask_mode"], "standard")
        self.assertEqual(architecture["parallax_mode"], "standard")

        characters = resolve_render_profile_options("characters")
        self.assertEqual(characters["complex_format"], "msn")
        self.assertEqual(characters["env_mask_mode"], "standard")
        self.assertEqual(characters["parallax_mode"], "standard")

        cs = resolve_render_profile_options("community_shaders")
        self.assertEqual(cs["complex_format"], "cm")
        self.assertEqual(cs["env_mask_mode"], "standard")
        self.assertEqual(cs["parallax_mode"], "standard")

        truepbr = resolve_render_profile_options("truepbr")
        self.assertEqual(truepbr["complex_format"], "cm")
        self.assertEqual(truepbr["env_mask_mode"], "complex")
        self.assertEqual(truepbr["parallax_mode"], "standard")

        auto = resolve_render_profile_options("auto", recommended_profile="enb")
        self.assertEqual(auto["effective_profile"], "enb")
        # Auto-detected ENB keeps parallax in standard mode so sign/artwork textures
        # keep their pop-out effect. Occlusion is only used when ENB is explicitly chosen.
        self.assertEqual(auto["parallax_mode"], "standard")
        self.assertEqual(auto["complex_format"], "msn")
        self.assertEqual(auto["env_mask_mode"], "complex")

    def test_resolve_render_profile_guardrails_enforces_locked_profile_modes(self) -> None:
        guarded = resolve_render_profile_guardrails(
            selected_profile="enb",
            complex_format="cm",
            env_mask_mode="standard",
            parallax_mode="standard",
        )
        self.assertEqual(str(guarded["effective_profile"]), "enb")
        self.assertEqual(str(guarded["complex_format"]), "msn")
        self.assertEqual(str(guarded["env_mask_mode"]), "complex")
        self.assertEqual(str(guarded["parallax_mode"]), "occlusion")
        self.assertEqual(len(list(guarded["changes"])), 3)

    def test_resolve_render_profile_guardrails_keeps_auto_modes_without_recommendation(self) -> None:
        guarded = resolve_render_profile_guardrails(
            selected_profile="auto",
            recommended_profile=None,
            complex_format="cm",
            env_mask_mode="complex",
            parallax_mode="occlusion",
        )
        self.assertEqual(str(guarded["effective_profile"]), "auto")
        self.assertEqual(str(guarded["complex_format"]), "cm")
        self.assertEqual(str(guarded["env_mask_mode"]), "complex")
        self.assertEqual(str(guarded["parallax_mode"]), "occlusion")
        self.assertEqual(list(guarded["changes"]), [])

    def test_resolve_render_profile_output_defaults_returns_expected_checkboxes(self) -> None:
        vanilla = resolve_render_profile_output_defaults("vanilla")
        self.assertTrue(bool(vanilla["include_diffuse"]))
        self.assertTrue(bool(vanilla["include_normal"]))
        self.assertFalse(bool(vanilla["include_parallax"]))
        self.assertFalse(bool(vanilla["include_complex"]))
        self.assertFalse(bool(vanilla["include_wetness_mask"]))
        self.assertFalse(bool(vanilla["include_snow_mask"]))

        performance = resolve_render_profile_output_defaults("performance")
        self.assertTrue(bool(performance["include_diffuse"]))
        self.assertTrue(bool(performance["include_normal"]))
        self.assertFalse(bool(performance["include_parallax"]))
        self.assertFalse(bool(performance["include_complex"]))
        self.assertFalse(bool(performance["include_wetness_mask"]))
        self.assertFalse(bool(performance["include_snow_mask"]))

        terrain = resolve_render_profile_output_defaults("terrain")
        self.assertTrue(bool(terrain["include_parallax"]))
        self.assertFalse(bool(terrain["include_environment_mask"]))
        self.assertFalse(bool(terrain["include_complex"]))
        self.assertFalse(bool(terrain["include_wetness_mask"]))
        self.assertFalse(bool(terrain["include_snow_mask"]))

        architecture = resolve_render_profile_output_defaults("architecture")
        self.assertTrue(bool(architecture["include_parallax"]))
        self.assertTrue(bool(architecture["include_environment_mask"]))
        self.assertFalse(bool(architecture["include_complex"]))
        self.assertFalse(bool(architecture["include_wetness_mask"]))
        self.assertFalse(bool(architecture["include_snow_mask"]))

        characters = resolve_render_profile_output_defaults("characters")
        self.assertFalse(bool(characters["include_parallax"]))
        self.assertFalse(bool(characters["include_environment_mask"]))
        self.assertFalse(bool(characters["include_complex"]))
        self.assertFalse(bool(characters["include_wetness_mask"]))
        self.assertFalse(bool(characters["include_snow_mask"]))

        enb = resolve_render_profile_output_defaults("enb")
        self.assertTrue(bool(enb["include_parallax"]))
        self.assertTrue(bool(enb["include_environment_mask"]))
        self.assertTrue(bool(enb["include_complex"]))
        self.assertFalse(bool(enb["include_normal"]))
        self.assertFalse(bool(enb["include_wetness_mask"]))
        self.assertFalse(bool(enb["include_snow_mask"]))

        truepbr = resolve_render_profile_output_defaults("truepbr")
        self.assertTrue(bool(truepbr["include_parallax"]))
        self.assertFalse(bool(truepbr["include_environment_mask"]))
        self.assertTrue(bool(truepbr["include_rmaos"]))
        self.assertFalse(bool(truepbr["include_complex"]))
        self.assertTrue(bool(truepbr["include_normal"]))
        self.assertFalse(bool(truepbr["include_wetness_mask"]))
        self.assertFalse(bool(truepbr["include_snow_mask"]))

    def test_render_profile_has_locked_controls_only_in_custom_mode(self) -> None:
        self.assertFalse(render_profile_has_locked_controls("custom"))
        self.assertTrue(render_profile_has_locked_controls("auto"))
        self.assertTrue(render_profile_has_locked_controls("enb"))

    def test_resolve_render_profile_mode_control_states_locks_all_modes_for_non_custom_profile(self) -> None:
        states = resolve_render_profile_mode_control_states(
            selected_profile="enb",
            include_complex=True,
            include_environment_mask=True,
            include_parallax=True,
        )
        self.assertTrue(bool(states["locked"]))
        self.assertEqual(str(states["complex_format"]), "disabled")
        self.assertEqual(str(states["env_mask_mode"]), "disabled")
        self.assertEqual(str(states["parallax_mode"]), "disabled")

    def test_resolve_render_profile_mode_control_states_only_enables_modes_for_checked_outputs(self) -> None:
        states = resolve_render_profile_mode_control_states(
            selected_profile="custom",
            include_complex=False,
            include_environment_mask=True,
            include_parallax=False,
        )
        self.assertFalse(bool(states["locked"]))
        self.assertEqual(str(states["complex_format"]), "disabled")
        self.assertEqual(str(states["env_mask_mode"]), "readonly")
        self.assertEqual(str(states["parallax_mode"]), "disabled")

    def test_build_render_profile_mode_controls_hint_reports_disabled_outputs_in_custom_mode(self) -> None:
        hint = build_render_profile_mode_controls_hint(
            {
                "locked": False,
                "complex_format": "disabled",
                "env_mask_mode": "readonly",
                "parallax_mode": "disabled",
            }
        )
        self.assertIn("Custom mode: enable", hint)
        self.assertIn("ENB Complex Material", hint)
        self.assertIn("Parallax", hint)
        self.assertIn("Family matrix:", hint)
        self.assertIn("TruePBR: _rmaos.dds, _ramos.dds, JSON sidecars", hint)
        self.assertIn("Parallax note:", hint)

    def test_build_render_profile_mode_controls_hint_reports_locked_state(self) -> None:
        hint = build_render_profile_mode_controls_hint(
            {
                "locked": True,
                "complex_format": "disabled",
                "env_mask_mode": "disabled",
                "parallax_mode": "disabled",
            }
        )
        self.assertIn("locks mode selectors", hint)
        self.assertIn("Family matrix:", hint)
        self.assertIn("Community Shaders: _cm.dds, _c.dds", hint)
        self.assertIn("Parallax note:", hint)

    def test_resolve_nif_patch_defaults_for_render_profile_returns_expected_toggles(self) -> None:
        vanilla = resolve_nif_patch_defaults_for_render_profile("vanilla")
        self.assertTrue(bool(vanilla["enable_parallax"]))
        self.assertFalse(bool(vanilla["enable_pom"]))
        self.assertFalse(bool(vanilla["enable_env_mapping"]))
        self.assertFalse(bool(vanilla["enable_pbr"]))
        self.assertFalse(bool(vanilla["force_shader_type_3"]))
        self.assertFalse(bool(vanilla["prefer_msn_normal"]))

        performance = resolve_nif_patch_defaults_for_render_profile("performance")
        self.assertFalse(bool(performance["enable_parallax"]))
        self.assertFalse(bool(performance["enable_pom"]))
        self.assertFalse(bool(performance["enable_env_mapping"]))
        self.assertFalse(bool(performance["enable_pbr"]))
        self.assertFalse(bool(performance["force_shader_type_3"]))
        self.assertFalse(bool(performance["prefer_msn_normal"]))

        vr = resolve_nif_patch_defaults_for_render_profile("vr")
        self.assertFalse(bool(vr["enable_parallax"]))
        self.assertFalse(bool(vr["enable_pom"]))
        self.assertFalse(bool(vr["enable_env_mapping"]))
        self.assertFalse(bool(vr["enable_pbr"]))
        self.assertFalse(bool(vr["force_shader_type_3"]))
        self.assertFalse(bool(vr["prefer_msn_normal"]))

        terrain = resolve_nif_patch_defaults_for_render_profile("terrain")
        self.assertTrue(bool(terrain["enable_parallax"]))
        self.assertFalse(bool(terrain["enable_pom"]))
        self.assertFalse(bool(terrain["enable_env_mapping"]))
        self.assertFalse(bool(terrain["enable_pbr"]))

        architecture = resolve_nif_patch_defaults_for_render_profile("architecture")
        self.assertTrue(bool(architecture["enable_parallax"]))
        self.assertFalse(bool(architecture["enable_pom"]))
        self.assertTrue(bool(architecture["enable_env_mapping"]))
        self.assertFalse(bool(architecture["enable_pbr"]))

        characters = resolve_nif_patch_defaults_for_render_profile("characters")
        self.assertFalse(bool(characters["enable_parallax"]))
        self.assertFalse(bool(characters["enable_pom"]))
        self.assertFalse(bool(characters["enable_env_mapping"]))
        self.assertFalse(bool(characters["enable_pbr"]))

        enb = resolve_nif_patch_defaults_for_render_profile("enb")
        self.assertTrue(bool(enb["enable_parallax"]))
        self.assertTrue(bool(enb["enable_pom"]))
        self.assertTrue(bool(enb["enable_env_mapping"]))
        self.assertFalse(bool(enb["enable_pbr"]))
        self.assertTrue(bool(enb["force_shader_type_3"]))
        self.assertTrue(bool(enb["prefer_msn_normal"]))

        truepbr = resolve_nif_patch_defaults_for_render_profile("truepbr")
        self.assertTrue(bool(truepbr["enable_parallax"]))
        self.assertFalse(bool(truepbr["enable_pom"]))
        self.assertTrue(bool(truepbr["enable_env_mapping"]))
        self.assertTrue(bool(truepbr["enable_pbr"]))
        self.assertFalse(bool(truepbr["force_shader_type_3"]))
        self.assertFalse(bool(truepbr["prefer_msn_normal"]))

    def test_get_nif_patch_option_warnings_reports_disabled_feature_path_combos(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="vanilla",
            enable_parallax=False,
            enable_pom=False,
            enable_env_mapping=False,
            force_shader_type_3=False,
            parallax_texture_path="textures\\arch\\stone_p.dds",
            env_mask_texture_path="textures\\arch\\stone_m.dds",
        )
        self.assertTrue(any("Parallax slot path set" in text for text in warnings))
        self.assertTrue(any("Environment mask path set" in text for text in warnings))

    def test_get_nif_patch_option_warnings_highlights_write_mode(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="vanilla",
            enable_parallax=False,
            enable_pom=False,
            enable_env_mapping=False,
            force_shader_type_3=False,
            dry_run=False,
        )
        self.assertTrue(any("write mode is enabled" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_highlights_missing_backup(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="vanilla",
            enable_parallax=False,
            enable_pom=False,
            enable_env_mapping=False,
            force_shader_type_3=False,
            dry_run=False,
            backup=False,
        )
        self.assertTrue(any("backup originals are disabled" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_skips_empty_env_mask_warning_for_enb(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="enb",
            enable_parallax=True,
            enable_pom=True,
            enable_env_mapping=True,
            force_shader_type_3=True,
            env_mask_texture_path="",
        )
        self.assertFalse(any("empty slot 5 path" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_truepbr_requires_pbr_flag(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="truepbr",
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=True,
            enable_pbr=False,
            force_shader_type_3=False,
            env_mask_texture_path="textures\\pbr\\stone_rmaos.dds",
        )
        self.assertTrue(any("pbr flag" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_uses_recommended_profile_when_selected_is_auto(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="auto",
            recommended_profile="enb",
            enable_parallax=True,
            enable_pom=True,
            enable_env_mapping=True,
            force_shader_type_3=True,
            env_mask_texture_path="",
        )
        self.assertFalse(any("vanilla meshes" in text.lower() for text in warnings))
        self.assertFalse(any("empty slot 5 path" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_reports_absolute_and_non_textures_paths(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="enb",
            enable_parallax=True,
            enable_pom=True,
            enable_env_mapping=True,
            force_shader_type_3=True,
            parallax_texture_path=r"C:\Mods\Data\Textures\architecture\stone\stone.dds",
            normal_texture_path="stone_n.dds",
            env_mask_texture_path=r"D:\mod\masks\stone_mask.dds",
        )
        self.assertTrue(any("absolute" in text.lower() for text in warnings))
        self.assertTrue(any("start with textures\\" in text.lower() for text in warnings))
        self.assertTrue(any("_p.dds" in text for text in warnings))
        self.assertTrue(any("env mask slot" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_reports_non_dds_paths(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="community_shaders",
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=True,
            force_shader_type_3=False,
            parallax_texture_path="textures\\architecture\\stone\\stone_p.png",
        )
        self.assertTrue(any(".dds filename" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_accepts_c_env_mask_suffix(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="community_shaders",
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=True,
            force_shader_type_3=True,
            env_mask_texture_path="textures\\architecture\\stone\\stone_c.dds",
        )
        self.assertFalse(any("env mask slot path should usually end" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_reports_diffuse_slot_misuse(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="vanilla",
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=False,
            force_shader_type_3=False,
            diffuse_texture_path="textures\\architecture\\stone\\stone_n.dds",
            cubemap_texture_path="textures\\architecture\\stone\\stone_n.dds",
        )
        self.assertTrue(any("slot 0" in text.lower() or "diffuse slot" in text.lower() for text in warnings))
        self.assertTrue(any("cubemap slot" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_reports_diffuse_slot_misuse_for_emissive_suffix(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="vanilla",
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=False,
            force_shader_type_3=False,
            diffuse_texture_path="textures\\architecture\\stone\\stone_em.dds",
        )
        self.assertTrue(any("slot 0" in text.lower() or "diffuse slot" in text.lower() for text in warnings))
        self.assertTrue(any("_g" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_reports_blender_authoring_suffixes(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="truepbr",
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=True,
            enable_pbr=True,
            force_shader_type_3=False,
            diffuse_texture_path="textures\\architecture\\stone\\stone_albedo.dds",
            env_mask_texture_path="textures\\architecture\\stone\\stone_roughness.dds",
        )
        self.assertTrue(any("blender/authoring suffix naming" in text.lower() for text in warnings))
        self.assertTrue(any("standalone blender authoring outputs" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_reports_renderer_specific_suffix_mismatch(self) -> None:
        enb_warnings = get_nif_patch_option_warnings(
            selected_profile="enb",
            enable_parallax=True,
            enable_pom=True,
            enable_env_mapping=True,
            force_shader_type_3=True,
            normal_texture_path="textures\\architecture\\stone\\stone_n.dds",
            env_mask_texture_path="textures\\architecture\\stone\\stone_cm.dds",
        )
        self.assertTrue(any("_msn" in text.lower() for text in enb_warnings))
        self.assertTrue(any("slot 5" in text.lower() and "_m" in text.lower() for text in enb_warnings))

        truepbr_warnings = get_nif_patch_option_warnings(
            selected_profile="truepbr",
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=True,
            force_shader_type_3=True,
            normal_texture_path="textures\\pbr\\stone_msn.dds",
            env_mask_texture_path="textures\\pbr\\stone_cm.dds",
        )
        self.assertTrue(any("_n" in text.lower() and "_msn" in text.lower() for text in truepbr_warnings))
        self.assertTrue(any("_rmaos" in text.lower() for text in truepbr_warnings))

    def test_build_render_profile_recommendation_message_lists_renderer_guidance(self) -> None:
        message = build_render_profile_recommendation_message("community_shaders")
        self.assertIn("Suggested target: Community Shaders", message)
        self.assertIn("Renderer quick guide:", message)
        self.assertIn("Vanilla:", message)
        self.assertIn("Terrain:", message)
        self.assertIn("Architecture:", message)
        self.assertIn("Characters / Skin:", message)
        self.assertIn("Community Shaders PBR:", message)
        self.assertIn("ENB:", message)
        self.assertIn("Auto-check:", message)
        self.assertIn("_c.dds", message)
        self.assertIn("How files should look:", message)

    def test_build_render_profile_recommendation_message_avoids_experimental_inaccuracy_copy(self) -> None:
        message = build_render_profile_recommendation_message("community_shaders")
        lowered = message.lower()
        self.assertNotIn("experimental", lowered)
        self.assertNotIn("may be inaccurate", lowered)

    def test_build_render_profile_recommendation_message_includes_truepbr_reference_notes(self) -> None:
        message = build_render_profile_recommendation_message("truepbr")
        self.assertIn("TruePBR essentials (Community Shaders):", message)
        self.assertIn("For players/users", message)
        self.assertIn("Landscape workflow:", message)
        self.assertIn("PBRTextureSets", message)
        self.assertIn("PBRMaterialObjects", message)
        self.assertIn("single-pass records", message)
        self.assertIn("TruePBR Manager", message)
        self.assertIn("channel packing", message)
        self.assertIn("Feature compatibility:", message)
        self.assertIn("<stem>_rmaos.dds", message)
        self.assertIn("do not swap in standalone blender-style _rough/_metallic/_ao names", message.lower())
        self.assertIn(MODDING_WIKI_SKYRIM_URL, message)

    def test_build_render_profile_recommendation_message_includes_confidence_block(self) -> None:
        recommendation = recommend_render_profile_details(
            Path("textures/architecture/stone_rmaos.dds"),
            detected_role="environment_mask",
            detected_suffix="_rmaos",
        )
        message = build_render_profile_recommendation_message(
            recommendation.profile,
            recommendation=recommendation,
        )
        self.assertIn("Confidence / rationale:", message)
        self.assertIn("Why this was recommended:", message)
        self.assertIn("Why not the others:", message)

    def test_build_render_profile_brief_message_includes_confidence_summary(self) -> None:
        recommendation = recommend_render_profile_details(
            Path("textures/architecture/stone_rmaos.dds"),
            detected_role="environment_mask",
            detected_suffix="_rmaos",
        )
        message = build_render_profile_brief_message(
            recommendation.profile,
            recommendation=recommendation,
        )
        self.assertIn("Confidence:", message)
        self.assertIn("Why this was recommended:", message)
        self.assertIn("Why not the others:", message)

    def test_build_render_profile_messages_warn_on_low_confidence(self) -> None:
        recommendation = RenderProfileRecommendation(
            profile="vanilla",
            confidence=0.56,
            confidence_label="low",
            recommendation_reason="No renderer-specific markers were found, so vanilla-safe defaults are the least risky choice.",
            why_not_others=("Not ENB: ENB uses `_msn` or `_m`, not `_cm/_c`.", "Not TruePBR: TruePBR uses `_rmaos/_ramos` plus JSON sidecars, not `_cm/_c`."),
            evidence=("no renderer-family markers",),
            source="default fallback",
        )

        brief_message = build_render_profile_brief_message(recommendation.profile, recommendation=recommendation)
        detailed_message = build_render_profile_recommendation_message(recommendation.profile, recommendation=recommendation)

        self.assertIn("probably incompatible", brief_message.lower())
        self.assertIn("probably incompatible", detailed_message.lower())

    def test_build_render_profile_brief_message_warns_on_selected_family_mismatch(self) -> None:
        recommendation = recommend_render_profile_details(
            Path("textures/architecture/stone_rmaos.dds"),
            detected_role="environment_mask",
            detected_suffix="_rmaos",
        )
        message = build_render_profile_brief_message(
            recommendation.profile,
            recommendation=recommendation,
            selected_profile="enb",
        )
        self.assertIn("probably incompatible", message.lower())
        self.assertIn("Switch families before export.", message)

    def test_build_render_profile_family_mismatch_warning_marks_probable_incompatibility(self) -> None:
        recommendation = recommend_render_profile_details(
            Path("textures/architecture/stone_rmaos.dds"),
            detected_role="environment_mask",
            detected_suffix="_rmaos",
        )
        message = build_render_profile_family_mismatch_warning("enb", recommendation)
        self.assertIsNotNone(message)
        self.assertIn("probably incompatible", message.lower())
        self.assertIn("ENB", message)
        self.assertIn("TruePBR", message)
        self.assertIn("Quick check:", message)

    def test_describe_render_profile_default_outputs_mentions_auto_checked_outputs(self) -> None:
        summary = describe_render_profile_default_outputs("enb")
        self.assertIn("Auto-check:", summary)
        self.assertIn("parallax/_p", summary)

    def test_describe_render_profile_default_outputs_includes_wetness_snow_in_disabled_list(self) -> None:
        for profile in ("vanilla", "terrain", "architecture", "community_shaders", "enb", "truepbr"):
            with self.subTest(profile=profile):
                summary = describe_render_profile_default_outputs(profile)
                self.assertIn("community shaders wetness/_wt", summary)
                self.assertIn("community shaders snow/_sm", summary)

    def test_describe_render_profile_default_outputs_glow_uses_g_suffix(self) -> None:
        """Glow output label in profile summaries must use _g for Skyrim SE."""
        for profile in ("vanilla", "enb", "community_shaders", "truepbr"):
            with self.subTest(profile=profile):
                summary = describe_render_profile_default_outputs(profile)
                # The summary string should contain "glow/_g" wherever glow appears
                if "glow/" in summary:
                    self.assertIn("glow/_g", summary)
                    self.assertNotIn("glow/_em", summary)

    def test_describe_render_profile_files_to_create_mentions_enb_msn_outputs(self) -> None:
        summary = describe_render_profile_files_to_create("enb")
        self.assertIn("<stem>_msn.dds", summary)
        self.assertIn("<stem>_m.dds", summary)
        self.assertNotIn("<stem>_n.dds", summary)

    def test_describe_render_profile_files_to_create_mentions_truepbr_outputs(self) -> None:
        summary = describe_render_profile_files_to_create("truepbr")
        self.assertIn("<stem>_rmaos.dds", summary)
        self.assertIn("<stem>_n.dds", summary)
        self.assertNotIn("<stem>_ao.dds", summary)
        self.assertNotIn("<stem>_rough.dds", summary)

    def test_get_nif_patch_option_warnings_truepbr_flags_legacy_orm_alias(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="truepbr",
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=True,
            enable_pbr=True,
            force_shader_type_3=False,
            env_mask_texture_path="textures\\pbr\\stone_orm.dds",
        )
        joined = "\n".join(warnings).lower()
        self.assertIn("legacy generic alias", joined)
        self.assertIn("_rmaos/_ramos", joined)

    def test_get_nif_patch_option_warnings_flags_legacy_env_mask_alias_for_non_truepbr(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="community_shaders",
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=True,
            force_shader_type_3=False,
            env_mask_texture_path="textures\\architecture\\stone_orm.dds",
        )
        joined = "\n".join(warnings).lower()
        self.assertIn("legacy/generic alias", joined)
        self.assertIn("_m.dds", joined)
        self.assertIn("_cm.dds", joined)
        self.assertIn("_rmaos.dds", joined)

    def test_get_nif_patch_option_warnings_reports_glow_and_cubemap_clear_write_conflicts(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="custom",
            enable_parallax=False,
            enable_pom=False,
            enable_env_mapping=False,
            force_shader_type_3=False,
            glow_texture_path="textures\\arch\\stone_g.dds",
            diffuse_texture_path="textures\\arch\\stone.dds",
            cubemap_texture_path="textures\\arch\\stone_env.dds",
            disable_glow_map=True,
            clear_glow_texture_path=True,
            clear_diffuse_texture_path=True,
            clear_cubemap_texture_path=True,
        )
        self.assertTrue(any("disable and write a slot 2 path" in text.lower() for text in warnings))
        self.assertTrue(any("glow slot is set to both clear and write a path" in text.lower() for text in warnings))
        self.assertTrue(any("diffuse slot is set to both clear and write a path" in text.lower() for text in warnings))
        self.assertTrue(any("cubemap slot is set to both clear and write a path" in text.lower() for text in warnings))

    def test_resolve_render_profile_mode_selection_preserves_current_modes_without_preset_apply(self) -> None:
        resolved = resolve_render_profile_mode_selection(
            {
                "complex_format": "cm",
                "env_mask_mode": "standard",
                "parallax_mode": "occlusion (ENB/POM)",
            },
            selected_profile="auto",
            recommended_profile="vanilla",
            apply_preset=False,
        )
        self.assertEqual(resolved["effective_profile"], "vanilla")
        self.assertEqual(resolved["complex_format"], "cm")
        self.assertEqual(resolved["env_mask_mode"], "standard")
        self.assertEqual(resolved["parallax_mode"], "occlusion")

    def test_parse_preview_jump_input_validates_bounds(self) -> None:
        self.assertEqual(parse_preview_jump_input("1", 5), 0)
        self.assertEqual(parse_preview_jump_input("5", 5), 4)
        self.assertIsNone(parse_preview_jump_input("0", 5))
        self.assertIsNone(parse_preview_jump_input("6", 5))
        self.assertIsNone(parse_preview_jump_input("abc", 5))

    def test_normalize_nif_result_details_falls_back_for_blank_input(self) -> None:
        self.assertEqual(_normalize_nif_result_details(" \n "), "(no details)")

    def test_format_nif_result_row_details_keeps_full_text_without_truncation(self) -> None:
        original = "Line one\n" + ("Very long detail chunk " * 20) + "\nLine three"
        formatted = _format_nif_result_row_details(original)
        self.assertIn("Line one ↩ ", formatted)
        self.assertIn("Line three", formatted)
        self.assertNotIn("...", formatted)
        self.assertGreater(len(formatted), 190)

    def test_compute_wrapped_preview_index_wraps_at_boundaries(self) -> None:
        self.assertEqual(compute_wrapped_preview_index(-1, 3), 2)
        self.assertEqual(compute_wrapped_preview_index(3, 3), 0)
        self.assertEqual(compute_wrapped_preview_index(1, 3), 1)

    def test_restore_nif_backups_restores_from_nif_bak(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mesh_path = Path(tmpdir) / "test_mesh.nif"
            backup_path = mesh_path.with_suffix(".nif.bak")
            mesh_path.write_bytes(b"new-data")
            backup_path.write_bytes(b"old-data")

            rows = restore_nif_backups([mesh_path])

            self.assertEqual(rows[0][0], "OK")
            self.assertEqual(mesh_path.read_bytes(), b"old-data")

    def test_restore_nif_backups_skips_when_backup_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mesh_path = Path(tmpdir) / "test_mesh.nif"
            mesh_path.write_bytes(b"current-data")

            rows = restore_nif_backups([mesh_path])

            self.assertEqual(rows[0], ("SKIP", "test_mesh.nif", "No .nif.bak backup found."))
            self.assertEqual(mesh_path.read_bytes(), b"current-data")

    def test_restore_nif_backups_stops_before_next_file_when_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            first_mesh = Path(tmpdir) / "first.nif"
            second_mesh = Path(tmpdir) / "second.nif"
            first_mesh.write_bytes(b"new-first")
            first_mesh.with_suffix(".nif.bak").write_bytes(b"old-first")
            second_mesh.write_bytes(b"new-second")
            second_mesh.with_suffix(".nif.bak").write_bytes(b"old-second")
            cancel_checks = 0

            def cancel_after_first_restore() -> bool:
                nonlocal cancel_checks
                cancel_checks += 1
                return cancel_checks > 1

            rows = restore_nif_backups(
                [first_mesh, second_mesh],
                should_cancel=cancel_after_first_restore,
            )

            self.assertEqual([row[1] for row in rows], ["first.nif"])
            self.assertEqual(first_mesh.read_bytes(), b"old-first")
            self.assertEqual(second_mesh.read_bytes(), b"new-second")

    def test_compare_nif_backups_reports_match_diff_and_missing_backup_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            match_mesh = root / "match.nif"
            match_mesh.write_bytes(b"same")
            match_mesh.with_suffix(".nif.bak").write_bytes(b"same")

            diff_mesh = root / "diff.nif"
            diff_mesh.write_bytes(b"current")
            diff_mesh.with_suffix(".nif.bak").write_bytes(b"backup")

            missing_mesh = root / "missing.nif"
            missing_mesh.write_bytes(b"current")

            rows = compare_nif_backups([match_mesh, diff_mesh, missing_mesh])

        self.assertEqual(rows[0], ("OK", "match.nif", "Current file matches match.nif.bak; restore would be a no-op."))
        self.assertEqual(rows[1][0], "WARN")
        self.assertIn("Current file differs from diff.nif.bak", rows[1][2])
        self.assertIn("Compare before restoring or writing.", rows[1][2])
        self.assertEqual(rows[2], ("SKIP", "missing.nif", "No .nif.bak backup found."))

    def test_should_apply_preview_recommendations_disabled_when_processing(self) -> None:
        self.assertFalse(should_apply_preview_recommendations(
            auto_suggestions_enabled=True,
            is_processing=True,
        ))

    def test_should_apply_preview_recommendations_enabled_when_idle_and_auto_on(self) -> None:
        self.assertTrue(should_apply_preview_recommendations(
            auto_suggestions_enabled=True,
            is_processing=False,
        ))

    def test_should_apply_preview_recommendations_disabled_when_auto_off(self) -> None:
        self.assertFalse(should_apply_preview_recommendations(
            auto_suggestions_enabled=False,
            is_processing=False,
        ))

    def test_resolve_lazy_preview_deferred_outputs_defers_niche_outputs_for_large_sets(self) -> None:
        include_flags = {
            "diffuse": True,
            "normal": True,
            "rmaos": True,
            "wetness_mask": True,
            "snow_mask": True,
            "ao": True,
            "roughness": True,
            "complex_material": True,
        }
        deferred = resolve_lazy_preview_deferred_outputs(
            lazy_enabled=True,
            force_full_once=False,
            total_sources=100,
            source_pixels=1024 * 1024,
            include_flags=include_flags,
        )
        self.assertIn("rmaos", deferred)
        self.assertIn("complex_material", deferred)
        self.assertNotIn("diffuse", deferred)
        self.assertNotIn("normal", deferred)

    def test_resolve_lazy_preview_deferred_outputs_defers_more_outputs_for_very_large_sets(self) -> None:
        include_flags = {
            "diffuse": True,
            "normal": True,
            "parallax": True,
            "glow": True,
            "environment_mask": True,
            "rmaos": True,
            "wetness_mask": True,
            "snow_mask": True,
            "ao": True,
            "roughness": True,
            "complex_material": True,
        }
        deferred = resolve_lazy_preview_deferred_outputs(
            lazy_enabled=True,
            force_full_once=False,
            total_sources=300,
            source_pixels=4096 * 4096,
            include_flags=include_flags,
        )
        self.assertIn("parallax", deferred)
        self.assertIn("glow", deferred)
        self.assertIn("environment_mask", deferred)
        self.assertNotIn("diffuse", deferred)
        self.assertNotIn("normal", deferred)

    def test_compute_preview_refresh_delay_ms_throttles_for_huge_batches(self) -> None:
        delay = compute_preview_refresh_delay_ms(
            is_processing=True,
            show_batch_preview=True,
            total_sources=1400,
            source_pixels=8192 * 8192,
            lazy_preview_enabled=True,
            staged_preview_enabled=True,
        )
        self.assertGreaterEqual(delay, 320)

    def test_compute_preview_refresh_delay_ms_is_fast_when_idle(self) -> None:
        delay = compute_preview_refresh_delay_ms(
            is_processing=False,
            show_batch_preview=False,
            total_sources=2000,
            source_pixels=8192 * 8192,
            lazy_preview_enabled=True,
            staged_preview_enabled=True,
        )
        self.assertEqual(delay, 75)

    def test_compute_deferred_preview_tile_interval_ms_increases_for_huge_batches(self) -> None:
        interval = compute_deferred_preview_tile_interval_ms(
            total_sources=1200,
            source_pixels=4096 * 4096,
        )
        self.assertGreaterEqual(interval, 140)

    def test_should_update_live_batch_preview_throttles_dense_updates_for_huge_batches(self) -> None:
        self.assertFalse(
            should_update_live_batch_preview(
                total_sources=1200,
                current_index=101,
                last_index=100,
                seconds_since_last_update=0.08,
            )
        )
        self.assertTrue(
            should_update_live_batch_preview(
                total_sources=1200,
                current_index=107,
                last_index=100,
                seconds_since_last_update=0.60,
            )
        )

    def test_should_update_live_batch_preview_uses_stricter_throttle_for_extreme_batches(self) -> None:
        self.assertFalse(
            should_update_live_batch_preview(
                total_sources=2200,
                current_index=506,
                last_index=500,
                seconds_since_last_update=0.40,
            )
        )
        self.assertTrue(
            should_update_live_batch_preview(
                total_sources=2200,
                current_index=510,
                last_index=500,
                seconds_since_last_update=0.80,
            )
        )

    def test_should_update_live_batch_preview_uses_stricter_throttle_for_8k_heavy_batches(self) -> None:
        self.assertFalse(
            should_update_live_batch_preview(
                total_sources=1100,
                current_index=507,
                last_index=500,
                seconds_since_last_update=0.50,
                source_pixels=8192 * 8192,
                high_res_8k_count=10,
            )
        )
        self.assertTrue(
            should_update_live_batch_preview(
                total_sources=1100,
                current_index=512,
                last_index=500,
                seconds_since_last_update=0.90,
                source_pixels=8192 * 8192,
                high_res_8k_count=10,
            )
        )

    def test_should_update_batch_status_line_throttles_dense_updates_for_huge_batches(self) -> None:
        self.assertFalse(
            should_update_batch_status_line(
                total_sources=1500,
                current_index=204,
                last_index=200,
                seconds_since_last_update=0.15,
                source_pixels=8192 * 8192,
                high_res_8k_count=12,
            )
        )
        self.assertTrue(
            should_update_batch_status_line(
                total_sources=1500,
                current_index=212,
                last_index=200,
                seconds_since_last_update=0.70,
                source_pixels=8192 * 8192,
                high_res_8k_count=12,
            )
        )

    def test_should_update_batch_status_line_always_allows_first_and_last_updates(self) -> None:
        self.assertTrue(
            should_update_batch_status_line(
                total_sources=2200,
                current_index=1,
                last_index=0,
                seconds_since_last_update=0.01,
            )
        )
        self.assertTrue(
            should_update_batch_status_line(
                total_sources=2200,
                current_index=2200,
                last_index=2190,
                seconds_since_last_update=0.01,
            )
        )

    def test_compute_checkpoint_mismatch_flags_detects_input_output_and_selection_drift(self) -> None:
        mismatch = compute_checkpoint_mismatch_flags(
            checkpoint_payload={
                "input_root": "/a/input",
                "output_root": "/a/output",
                "total_files_considered": 20,
            },
            current_input_root="/b/input",
            current_output_root="/b/output",
            planned_total=30,
        )
        self.assertIn("checkpoint input path differs", mismatch)
        self.assertIn("checkpoint output path differs", mismatch)
        self.assertIn("checkpoint file count differs", mismatch)

    def test_compute_checkpoint_resume_counts_reports_matching_completed_and_remaining(self) -> None:
        selected = [Path("/input/a.dds"), Path("/input/b.dds"), Path("/input/c.dds")]
        completed = {str(selected[0].resolve()), str(selected[2].resolve()), "/stale/old.dds"}
        self.assertEqual(
            compute_checkpoint_resume_counts(
                selected_files=selected,
                completed_success_files=completed,
            ),
            (2, 1),
        )

    def test_basic_preview_outputs_hide_advanced_workflow_tiles(self) -> None:
        self.assertEqual(
            _visible_preview_output_keys(show_advanced_workflow_outputs=False),
            ("diffuse", "normal", "parallax", "glow", "environment_mask"),
        )
        advanced = _visible_preview_output_keys(show_advanced_workflow_outputs=True)
        self.assertIn("rmaos", advanced)
        self.assertIn("complex_material", advanced)
        self.assertEqual(len(advanced), 11)

    def test_compute_processing_queue_event_budget_increases_for_huge_runs_when_preview_off(self) -> None:
        budget = compute_processing_queue_event_budget(
            queue_backlog=24,
            total_sources=2200,
            show_batch_preview=False,
            source_pixels=8192 * 8192,
        )
        self.assertGreaterEqual(budget, 128)

    def test_compute_processing_queue_event_budget_keeps_default_budget_for_small_backlog(self) -> None:
        budget = compute_processing_queue_event_budget(
            queue_backlog=24,
            total_sources=80,
            show_batch_preview=True,
            source_pixels=2048 * 2048,
        )
        self.assertEqual(budget, 32)

    def test_build_batch_bottleneck_hints_includes_large_run_resume_guidance(self) -> None:
        hints = build_batch_bottleneck_hints(
            avg_file_seconds=2.0,
            max_file_seconds=8.0,
            high_res_4k_count=120,
            high_res_8k_count=4,
            max_source_dimension=8192,
            max_source_megapixels=67.1,
            resumed_completed_count=0,
            total_failed=2,
            total_sources=1500,
        )
        self.assertTrue(any("Resume mode" in hint for hint in hints))
        self.assertTrue(any("8K-heavy" in hint for hint in hints))
        self.assertTrue(any("Very large source textures" in hint for hint in hints))

    def test_build_batch_bottleneck_hints_includes_queue_backlog_guidance(self) -> None:
        hints = build_batch_bottleneck_hints(
            avg_file_seconds=1.3,
            max_file_seconds=3.2,
            high_res_4k_count=8,
            high_res_8k_count=0,
            max_source_dimension=4096,
            max_source_megapixels=16.0,
            resumed_completed_count=200,
            total_failed=0,
            total_sources=1200,
            peak_queue_backlog=420,
        )
        self.assertTrue(any("Queue backlog spiked" in hint for hint in hints))

    def test_build_batch_bottleneck_hints_respects_advanced_workflow_visibility(self) -> None:
        shared = {
            "avg_file_seconds": 1.0,
            "max_file_seconds": 2.0,
            "high_res_4k_count": 60,
            "high_res_8k_count": 0,
            "max_source_dimension": 4096,
            "max_source_megapixels": 16.0,
            "resumed_completed_count": 0,
            "total_failed": 0,
            "total_sources": 500,
        }
        advanced_hints = build_batch_bottleneck_hints(
            **shared,
            show_advanced_workflow_outputs=True,
        )
        basic_hints = build_batch_bottleneck_hints(
            **shared,
            show_advanced_workflow_outputs=False,
        )

        self.assertTrue(any("advanced/niche outputs" in hint for hint in advanced_hints))
        self.assertTrue(any("enable Advanced workflow outputs" in hint for hint in basic_hints))
        self.assertFalse(any("advanced/niche outputs" in hint for hint in basic_hints))

    def test_build_batch_bottleneck_hints_prioritizes_queue_backlog_in_dense_huge_run(self) -> None:
        hints = build_batch_bottleneck_hints(
            avg_file_seconds=2.1,
            max_file_seconds=9.1,
            high_res_4k_count=120,
            high_res_8k_count=6,
            max_source_dimension=8192,
            max_source_megapixels=67.1,
            resumed_completed_count=30,
            total_failed=4,
            total_sources=1800,
            peak_queue_backlog=384,
        )
        self.assertTrue(hints)
        self.assertIn("Queue backlog spiked", hints[0])

    def test_summarize_batch_nif_patch_activity_reports_repaired_and_failed_counts(self) -> None:
        summary = summarize_batch_nif_patch_activity(
            [
                ("mesh_a.nif", 2, 1),
                ("mesh_b.nif", 0, 3),
            ]
        )
        self.assertEqual(summary, "Automatic NIF patching: 2 succeeded, 4 failed.")
        self.assertIsNone(summarize_batch_nif_patch_activity([]))

    def test_get_generation_warnings_glow_on_stone_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_glow=True,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("glow_non_magical", ids)

    def test_get_generation_warnings_glow_on_plants_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "plants",
            include_glow=True,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("glow_non_magical", ids)

    def test_get_generation_warnings_high_env_mask_on_plants(self) -> None:
        warnings = get_generation_warnings(
            "plants",
            include_glow=False,
            include_environment_mask=True,
            env_mask_mode="standard",
            env_mask_strength=1.8,
            include_parallax=False,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("high_env_mask_organic", ids)

    def test_get_generation_warnings_no_false_positives_for_metal(self) -> None:
        warnings = get_generation_warnings(
            "metal",
            include_glow=False,
            include_environment_mask=True,
            env_mask_mode="standard",
            env_mask_strength=1.8,
            include_parallax=False,
            include_complex=False,
        )
        self.assertEqual(warnings, [])

    def test_get_generation_warnings_parallax_on_plants(self) -> None:
        warnings = get_generation_warnings(
            "plants",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("parallax_flat_plants", ids)

    def test_get_generation_warnings_parallax_on_terrain_always_warns(self) -> None:
        # Terrain shimmer warning must fire regardless of source_hint content
        warnings = get_generation_warnings(
            "terrain",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("parallax_landscape_shimmer", ids)

    def test_get_generation_warnings_parallax_on_terrain_fires_without_landscape_hint(self) -> None:
        # Previously required "landscape" in source_hint — now fires on material_type alone
        warnings_no_hint = get_generation_warnings(
            "terrain",
            source_hint=None,
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            include_complex=False,
        )
        warnings_unrelated_hint = get_generation_warnings(
            "terrain",
            source_hint="forest path texture",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            include_complex=False,
        )
        self.assertIn("parallax_landscape_shimmer", [w[0] for w in warnings_no_hint])
        self.assertIn("parallax_landscape_shimmer", [w[0] for w in warnings_unrelated_hint])

    def test_get_generation_warnings_parallax_high_strength_snow_warns(self) -> None:
        warnings = get_generation_warnings(
            "snow",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            parallax_strength=2.0,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("parallax_high_strength_snow", ids)

    def test_get_generation_warnings_parallax_low_strength_snow_no_warning(self) -> None:
        warnings = get_generation_warnings(
            "snow",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            parallax_strength=1.2,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_high_strength_snow", ids)

    def test_get_generation_warnings_parallax_high_strength_dirt_warns(self) -> None:
        warnings = get_generation_warnings(
            "dirt",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            parallax_strength=2.5,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("parallax_high_strength_ground", ids)

    def test_get_generation_warnings_parallax_high_strength_sand_warns(self) -> None:
        warnings = get_generation_warnings(
            "sand",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            parallax_strength=3.0,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("parallax_high_strength_ground", ids)

    def test_get_generation_warnings_parallax_within_threshold_ground_no_warning(self) -> None:
        for mat in ("dirt", "sand"):
            warnings = get_generation_warnings(
                mat,
                include_glow=False,
                include_environment_mask=False,
                env_mask_mode="standard",
                env_mask_strength=1.0,
                include_parallax=True,
                parallax_strength=1.8,
                include_complex=False,
            )
            ids = [w[0] for w in warnings]
            self.assertNotIn("parallax_high_strength_ground", ids, msg=f"Unexpected warning for {mat}")

    def test_get_generation_warnings_parallax_strength_none_no_false_positives(self) -> None:
        # When parallax_strength is not provided the strength-based warnings must not fire
        for mat in ("snow", "dirt", "sand"):
            warnings = get_generation_warnings(
                mat,
                include_glow=False,
                include_environment_mask=False,
                env_mask_mode="standard",
                env_mask_strength=1.0,
                include_parallax=True,
                parallax_strength=None,
                include_complex=False,
            )
            ids = [w[0] for w in warnings]
            self.assertNotIn("parallax_high_strength_snow", ids, msg=f"Unexpected snow warning for {mat}")
            self.assertNotIn("parallax_high_strength_ground", ids, msg=f"Unexpected ground warning for {mat}")

    def test_get_generation_warnings_diffuse_from_normal_source(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            source_role="normal",
            include_diffuse=True,
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("diffuse_from_derived_source", ids)
        warning_lookup = dict(warnings)
        self.assertIn("_g", warning_lookup["diffuse_from_derived_source"])
        self.assertIn("_rmaos", warning_lookup["diffuse_from_derived_source"])
        self.assertNotIn("_em", warning_lookup["diffuse_from_derived_source"])

    def test_get_generation_warnings_normal_from_normal_source(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            source_role="normal",
            include_normal=True,
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("normal_from_normal_source", ids)

    def test_get_generation_warnings_ui_hint_flags_advanced_maps(self) -> None:
        warnings = get_generation_warnings(
            "general",
            source_hint="UI/interface texture (not for in-world use)",
            include_glow=False,
            include_environment_mask=True,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("ui_texture_advanced_maps", ids)

    def test_get_generation_warnings_orm_source_triggers_truepbr_suffix_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            source_suffix="_orm",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("rmaos_source_requires_renderer_check", ids)

    def test_recommend_generation_settings_clamps_interface_workflow_strengths(self) -> None:
        settings = recommend_generation_settings(
            _detailed_bright_image(),
            input_path=Path("textures/interface/cards/collectible_waifu_card_01.dds"),
        )
        self.assertLessEqual(float(settings["parallax_strength"]), 1.0)
        self.assertLessEqual(float(settings["environment_mask_strength"]), 1.15)
        self.assertLessEqual(float(settings["specular_strength"]), 1.15)

    def test_recommend_generation_settings_clamps_performance_workflow_strengths(self) -> None:
        settings = recommend_generation_settings(
            _detailed_bright_image(),
            input_path=Path("textures/performance/armor/steelarmor.dds"),
        )
        self.assertLessEqual(float(settings["parallax_strength"]), 1.25)
        self.assertLessEqual(float(settings["complex_strength"]), 1.7)
        self.assertLessEqual(float(settings["rmaos_strength"]), 1.75)

    def test_recommend_generation_settings_clamps_vr_workflow_strengths(self) -> None:
        settings = recommend_generation_settings(
            _detailed_bright_image(),
            input_path=Path("textures/vr/architecture/whiterunwall.dds"),
        )
        self.assertLessEqual(float(settings["parallax_strength"]), 1.15)
        self.assertLessEqual(float(settings["normal_strength"]), 2.1)
        self.assertLessEqual(float(settings["specular_strength"]), 1.3)

    def test_recommend_generation_settings_clamps_terrain_workflow_strengths(self) -> None:
        settings = recommend_generation_settings(
            _detailed_bright_image(),
            input_path=Path("textures/terrain/landscape/mountainslab.dds"),
        )
        self.assertLessEqual(float(settings["parallax_strength"]), 1.2)
        self.assertLessEqual(float(settings["environment_mask_strength"]), 1.3)
        self.assertLessEqual(float(settings["specular_strength"]), 1.3)

    def test_recommend_generation_settings_clamps_characters_workflow_strengths(self) -> None:
        settings = recommend_generation_settings(
            _detailed_bright_image(),
            input_path=Path("textures/characters/skin/femalebody_1.dds"),
        )
        self.assertLessEqual(float(settings["parallax_strength"]), 1.1)
        self.assertLessEqual(float(settings["normal_strength"]), 2.0)
        self.assertLessEqual(float(settings["specular_strength"]), 1.4)

    def test_recommend_generation_settings_clamps_architecture_workflow_strengths(self) -> None:
        settings = recommend_generation_settings(
            _detailed_bright_image(),
            input_path=Path("textures/architecture/whiterun/stonewall.dds"),
        )
        self.assertLessEqual(float(settings["parallax_strength"]), 1.5)
        self.assertLessEqual(float(settings["environment_mask_strength"]), 1.6)
        self.assertLessEqual(float(settings["complex_strength"]), 2.15)

    def test_recommend_generation_settings_uses_material_path_hints_for_metal_reflectivity(self) -> None:
        source = _detailed_bright_image()
        iron = recommend_generation_settings(source, input_path=Path("textures/armor/iron/ironplate.dds"))
        polished = recommend_generation_settings(source, input_path=Path("textures/armor/steel/polished_steel_plate.dds"))
        self.assertLess(float(iron["environment_mask_strength"]), float(polished["environment_mask_strength"]))
        self.assertLess(float(iron["specular_strength"]), float(polished["specular_strength"]))

    def test_recommend_generation_settings_boosts_wet_stone_reflectivity(self) -> None:
        source = _detailed_bright_image()
        dry_stone = recommend_generation_settings(source, input_path=Path("textures/dungeons/stone/rough_rock.dds"))
        wet_stone = recommend_generation_settings(source, input_path=Path("textures/dungeons/stone/wet_rock.dds"))
        self.assertGreater(float(wet_stone["environment_mask_strength"]), float(dry_stone["environment_mask_strength"]))
        self.assertGreater(float(wet_stone["specular_strength"]), float(dry_stone["specular_strength"]))

    def test_generate_complex_material_slider_produces_visible_change(self) -> None:
        source = _large_high_detail_image()
        low_strength = generate_complex_material(source, strength=0.5)
        high_strength = generate_complex_material(source, strength=3.0)
        low_r, _, _, _ = low_strength.split()
        high_r, _, _, _ = high_strength.split()
        import numpy as np

        low_arr = np.asarray(low_r, dtype=np.float32)
        high_arr = np.asarray(high_r, dtype=np.float32)
        mean_diff = float(np.abs(low_arr - high_arr).mean())
        self.assertGreater(mean_diff, 2.0)

    def test_analyze_image_content_exposes_expected_metrics(self) -> None:
        metrics = analyze_image_content(_sample_image())
        for key in (
            "megapixels",
            "brightness",
            "contrast",
            "edge_strength",
            "edge_variance",
            "detail_energy",
            "dynamic_range",
            "shadow_ratio",
            "highlight_ratio",
            "bright_cluster_ratio",
            "midtone_ratio",
            "p90_luma",
            "saturation_mean",
            "saturation_variance",
            "low_saturation_ratio",
            "color_variance",
        ):
            self.assertIn(key, metrics)

    def test_recommend_generation_settings_changes_by_image_content(self) -> None:
        flat_dark = recommend_generation_settings(_flat_dark_image())
        detailed_bright = recommend_generation_settings(_detailed_bright_image())
        self.assertNotEqual(int(flat_dark["glow_threshold"]), int(detailed_bright["glow_threshold"]))
        self.assertNotEqual(float(flat_dark["normal_strength"]), float(detailed_bright["normal_strength"]))
        self.assertNotEqual(float(flat_dark["specular_strength"]), float(detailed_bright["specular_strength"]))

    def test_recommend_generation_settings_glow_handles_sparse_highlights(self) -> None:
        uniform = recommend_generation_settings(_uniform_bright_image())
        sparse = recommend_generation_settings(_sparse_highlight_image())
        self.assertGreater(int(uniform["glow_threshold"]), int(sparse["glow_threshold"]))

    def test_recommend_generation_settings_uses_saturation_for_glow_threshold(self) -> None:
        low_saturation = recommend_generation_settings(_uniform_bright_image())
        high_saturation = recommend_generation_settings(_high_saturation_image())
        self.assertLess(int(high_saturation["glow_threshold"]), int(low_saturation["glow_threshold"]))

    def test_recommend_generation_settings_dampens_highly_detailed_large_inputs(self) -> None:
        settings = recommend_generation_settings(_large_high_detail_image())
        self.assertLess(float(settings["parallax_strength"]), 2.2)
        self.assertLess(float(settings["complex_strength"]), 2.5)
        self.assertLess(float(settings["specular_strength"]), 2.1)

    def test_recommend_generation_settings_adjusts_for_normal_input_filename(self) -> None:
        source = _sample_image()
        baseline = recommend_generation_settings(source)
        normal_named = recommend_generation_settings(source, input_path=Path("textures/architecture/stone_n.dds"))
        self.assertLessEqual(float(normal_named["normal_strength"]), float(baseline["normal_strength"]))
        self.assertLessEqual(float(normal_named["parallax_strength"]), float(baseline["parallax_strength"]))

    def test_recommend_generation_settings_can_infer_normal_like_image(self) -> None:
        baseline = recommend_generation_settings(_sample_image())
        inferred = recommend_generation_settings(_normal_like_image())
        self.assertLessEqual(float(inferred["normal_strength"]), float(baseline["normal_strength"]))
        self.assertGreaterEqual(int(inferred["glow_threshold"]), int(baseline["glow_threshold"]))

    def test_generate_environment_mask_glossiness_stays_above_complex_parallax_floor(self) -> None:
        environment_mask = generate_environment_mask(_large_high_detail_image(), strength=2.2, mode="complex")
        _, metallic, _, _ = environment_mask.split()
        minimum, _ = metallic.getextrema()
        self.assertGreater(minimum, 4)

    def test_generate_environment_mask_complex_mode_returns_rgba_same_size(self) -> None:
        environment_mask = generate_environment_mask(_sample_image(), mode="complex")
        self.assertEqual(environment_mask.mode, "RGBA")
        self.assertEqual(environment_mask.size, (8, 8))

    def test_generate_environment_mask_complex_mode_flat_surface_avoids_black_holes(self) -> None:
        environment_mask = generate_environment_mask(_flat_dark_image(), strength=2.2, mode="complex")
        roughness, metallic, ao, nonmetal_reflectance = environment_mask.split()
        rough_min, rough_max = roughness.getextrema()
        metallic_min, _ = metallic.getextrema()
        ao_min, _ = ao.getextrema()
        alpha_min, alpha_max = nonmetal_reflectance.getextrema()
        self.assertGreaterEqual(rough_min, 24)
        self.assertLessEqual(rough_max - rough_min, 80)
        self.assertGreaterEqual(metallic_min, 6)
        self.assertGreaterEqual(ao_min, 24)
        expected_reflectance = generate_specular(_flat_dark_image(), strength=2.35)
        self.assertEqual(nonmetal_reflectance.tobytes(), expected_reflectance.tobytes())
        self.assertGreaterEqual(alpha_min, 8)
        self.assertLessEqual(alpha_max, 255)

    def test_generate_environment_mask_complex_mode_channel_order_matches_rmaos_contract(self) -> None:
        environment_mask = generate_environment_mask(_flat_dark_image(), strength=2.2, mode="complex")
        roughness, metallic, ao, nonmetal_reflectance = environment_mask.split()
        self.assertGreater(ImageStat.Stat(roughness).mean[0], ImageStat.Stat(metallic).mean[0])
        self.assertGreater(ImageStat.Stat(ao).mean[0], ImageStat.Stat(roughness).mean[0])
        expected_reflectance = generate_specular(_flat_dark_image(), strength=2.35)
        self.assertEqual(nonmetal_reflectance.tobytes(), expected_reflectance.tobytes())

    def test_generate_environment_mask_rmaos_alpha_is_nonmetal_reflectance(self) -> None:
        source = _sample_image()
        environment_mask = generate_environment_mask(source, strength=1.2, mode="complex")
        expected_reflectance = generate_specular(source, strength=1.35)
        self.assertEqual(environment_mask.getchannel("A").tobytes(), expected_reflectance.tobytes())

    def test_generate_environment_mask_for_workflow_enb_uses_reflection_gloss_metal_height_layout(self) -> None:
        environment_mask = generate_environment_mask_for_workflow(
            _flat_dark_image(),
            strength=2.2,
            mode="complex",
            complex_workflow="enb",
        )
        reflection, glossiness, metalness, height = environment_mask.split()
        self.assertGreater(ImageStat.Stat(glossiness).mean[0], ImageStat.Stat(reflection).mean[0])
        self.assertGreater(ImageStat.Stat(reflection).mean[0], ImageStat.Stat(metalness).mean[0])
        self.assertGreater(ImageStat.Stat(height).mean[0], ImageStat.Stat(metalness).mean[0])

    def test_resolve_env_mask_complex_workflow_prefers_render_profile(self) -> None:
        self.assertEqual(
            resolve_env_mask_complex_workflow(env_mask_mode="complex", complex_format="cm", render_profile="enb"),
            "enb",
        )
        self.assertEqual(
            resolve_env_mask_complex_workflow(env_mask_mode="complex", complex_format="msn", render_profile="truepbr"),
            "truepbr",
        )

    def test_resolve_env_mask_complex_workflow_community_shaders_returns_enb(self) -> None:
        # Community Shaders Extended Materials uses ENB-compatible RGBA channel packing
        # (R=env reflection, G=glossiness, B=metallic, A=height) — confirmed from CS Lighting.hlsl.
        # Must NOT fall through to "truepbr" (R=roughness, G=metallic, B=AO — different layout).
        self.assertEqual(
            resolve_env_mask_complex_workflow(env_mask_mode="complex", complex_format="cm", render_profile="community_shaders"),
            "enb",
        )
        self.assertEqual(
            resolve_env_mask_complex_workflow(env_mask_mode="complex", complex_format="msn", render_profile="community_shaders"),
            "enb",
        )
        self.assertEqual(
            resolve_env_mask_complex_workflow(env_mask_mode="complex", render_profile="cs"),
            "enb",
        )

    def test_resolve_env_mask_complex_workflow_falls_back_to_complex_format(self) -> None:
        self.assertEqual(
            resolve_env_mask_complex_workflow(env_mask_mode="complex", complex_format="msn", render_profile="auto"),
            "enb",
        )
        self.assertEqual(
            resolve_env_mask_complex_workflow(env_mask_mode="complex", complex_format="cm", render_profile="auto"),
            "truepbr",
        )
        self.assertEqual(
            resolve_env_mask_complex_workflow(
                env_mask_mode="complex",
                complex_format="msn",
                render_profile="auto",
                include_complex=False,
            ),
            "enb",
        )

    def test_prepare_preview_source_downscales_large_images(self) -> None:
        source = Image.new("RGB", (4096, 2048), color=(64, 96, 128))
        preview = prepare_preview_source(source, max_dimension=1024)
        self.assertEqual(preview.mode, "RGB")
        self.assertLessEqual(max(preview.size), 1024)

    def test_get_preview_size_limits_returns_expected_preset_sizes(self) -> None:
        self.assertEqual(get_preview_size_limits("XS"), (160, 120))
        self.assertEqual(get_preview_size_limits("Small"), (240, 180))
        self.assertEqual(get_preview_size_limits("Medium"), (340, 250))
        self.assertEqual(get_preview_size_limits("Large"), (460, 340))
        self.assertEqual(get_preview_size_limits("XL"), (620, 460))

    def test_get_preview_size_limits_falls_back_to_medium_for_unknown_values(self) -> None:
        self.assertEqual(get_preview_size_limits("Unknown"), (340, 250))

    def test_generate_preview_outputs_only_includes_requested_outputs(self) -> None:
        outputs = generate_preview_outputs(
            _sample_image(),
            normal_strength=2.0,
            parallax_strength=1.35,
            glow_threshold=190,
            environment_mask_strength=1.2,
            complex_strength=1.15,
            specular_strength=1.15,
            complex_format="msn",
            include_diffuse=True,
            include_normal=False,
            include_parallax=True,
            include_glow=False,
            include_environment_mask=False,
            include_complex=False,
        )
        self.assertEqual(set(outputs.keys()), {"diffuse", "parallax"})

    def test_generate_preview_outputs_supports_complex_formats(self) -> None:
        msn_outputs = generate_preview_outputs(
            _sample_image(),
            normal_strength=2.0,
            parallax_strength=1.35,
            glow_threshold=190,
            environment_mask_strength=1.2,
            complex_strength=1.15,
            specular_strength=1.15,
            complex_format="msn",
            include_diffuse=False,
            include_normal=False,
            include_parallax=False,
            include_glow=False,
            include_environment_mask=False,
            include_complex=True,
        )
        cm_outputs = generate_preview_outputs(
            _sample_image(),
            normal_strength=2.0,
            parallax_strength=1.35,
            glow_threshold=190,
            environment_mask_strength=1.2,
            complex_strength=1.15,
            specular_strength=1.15,
            complex_format="cm",
            include_diffuse=False,
            include_normal=False,
            include_parallax=False,
            include_glow=False,
            include_environment_mask=False,
            include_complex=True,
        )
        self.assertEqual(msn_outputs["complex_material"].mode, "RGBA")
        self.assertEqual(cm_outputs["complex_material"].mode, "RGBA")

    def test_build_complex_preview_image_msn_visualizes_rgb_and_alpha_side_by_side(self) -> None:
        source = _sample_image()
        msn = generate_msn(source)
        preview = build_complex_preview_image(msn, complex_format="msn")
        self.assertEqual(preview.mode, "RGB")
        self.assertEqual(preview.size, (source.width * 2 + 2, source.height))
        left_sample = preview.getpixel((0, 0))
        right_sample = preview.getpixel((source.width + 2, 0))
        self.assertNotEqual(left_sample, right_sample)

    def test_build_complex_preview_image_cm_returns_original_image(self) -> None:
        cm = generate_complex_material(_sample_image())
        preview = build_complex_preview_image(cm, complex_format="cm")
        self.assertIs(preview, cm)

    def test_generate_preview_outputs_defaults_environment_mask_to_standard_skyrim_se(self) -> None:
        outputs = generate_preview_outputs(
            _sample_image(),
            normal_strength=2.0,
            parallax_strength=1.35,
            glow_threshold=190,
            environment_mask_strength=1.2,
            complex_strength=1.15,
            specular_strength=1.15,
            complex_format="msn",
            include_diffuse=False,
            include_normal=False,
            include_parallax=False,
            include_glow=False,
            include_environment_mask=True,
            include_complex=False,
        )
        self.assertEqual(outputs["environment_mask"].mode, "L")

    def test_generate_glow_produces_graded_values_above_threshold(self) -> None:
        glow = generate_glow(_vertical_gradient_image(), threshold=180)
        values = set(glow.tobytes())
        self.assertGreater(len(values), 2)

    def test_apply_recommendations_by_auto_flags_applies_only_checked_values(self) -> None:
        current = {
            "normal_strength": 1.5,
            "parallax_strength": 1.3,
            "glow_threshold": 180,
            "environment_mask_strength": 1.2,
        }
        recommended = {
            "normal_strength": 2.4,
            "parallax_strength": 2.0,
            "glow_threshold": 210,
            "environment_mask_strength": 1.8,
        }
        auto_flags = {
            "normal_strength": True,
            "parallax_strength": False,
            "glow_threshold": True,
            "environment_mask_strength": False,
        }
        merged = apply_recommendations_by_auto_flags(current=current, recommended=recommended, auto_flags=auto_flags)
        self.assertEqual(float(merged["normal_strength"]), 2.4)
        self.assertEqual(float(merged["parallax_strength"]), 1.3)
        self.assertEqual(int(merged["glow_threshold"]), 210)
        self.assertEqual(float(merged["environment_mask_strength"]), 1.2)

    def test_build_output_paths_uses_skyrim_default_names_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            diffuse_path, parallax_path = build_output_paths(
                input_path=input_path,
                output_dir=temp_path / "out",
                diffuse_name=None,
                parallax_name=None,
            )

            self.assertEqual(diffuse_path.name, "brick.dds")
            self.assertEqual(parallax_path.name, "brick_p.dds")

    def test_build_output_paths_use_dds_extension_even_for_png_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            input_path.write_bytes(b"stub")

            diffuse_path, parallax_path = build_output_paths(
                input_path=input_path,
                output_dir=temp_path / "out",
                diffuse_name=None,
                parallax_name=None,
            )

            self.assertEqual(diffuse_path.name, "brick.dds")
            self.assertEqual(parallax_path.name, "brick_p.dds")

    def test_build_output_paths_fallout4_defaults_to_d_diffuse_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            diffuse_path, parallax_path = build_output_paths(
                input_path=input_path,
                output_dir=temp_path / "out",
                target_game="fallout4",
            )

            self.assertEqual(diffuse_path.name, "brick_d.dds")
            self.assertEqual(parallax_path.name, "brick_p.dds")

    def test_custom_output_paths_preserve_textures_subfolders_for_skyrim_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "mod" / "textures" / "architecture" / "stone" / "brick.dds"
            input_path.parent.mkdir(parents=True)
            input_path.write_bytes(b"stub")
            output_root = temp_path / "out"

            diffuse_path, parallax_path = build_output_paths(input_path=input_path, output_dir=output_root)
            normal_path = build_normal_output_path(input_path=input_path, output_dir=output_root)
            environment_mask_path = build_environment_mask_output_path(input_path=input_path, output_dir=output_root)
            complex_path = build_complex_output_path(input_path=input_path, output_dir=output_root, complex_format="msn")

            self.assertEqual(diffuse_path.relative_to(output_root).as_posix(), "textures/architecture/stone/brick.dds")
            self.assertEqual(parallax_path.relative_to(output_root).as_posix(), "textures/architecture/stone/brick_p.dds")
            self.assertEqual(normal_path.relative_to(output_root).as_posix(), "textures/architecture/stone/brick_n.dds")
            self.assertEqual(environment_mask_path.relative_to(output_root).as_posix(), "textures/architecture/stone/brick_m.dds")
            self.assertEqual(complex_path.relative_to(output_root).as_posix(), "textures/architecture/stone/brick_msn.dds")

    def test_build_output_paths_accepts_omitted_name_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            diffuse_path, parallax_path = build_output_paths(
                input_path=input_path,
                output_dir=temp_path / "out",
            )

            self.assertEqual(diffuse_path.name, "brick.dds")
            self.assertEqual(parallax_path.name, "brick_p.dds")

    def test_build_normal_output_path_uses_default_name_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            normal_path = build_normal_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                normal_name=None,
            )

            self.assertEqual(normal_path.name, "brick_n.dds")

    def test_build_glow_output_path_uses_default_name_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            glow_path = build_glow_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                glow_name=None,
            )

            self.assertEqual(glow_path.name, "brick_g.dds")

    def test_build_environment_mask_output_path_uses_standard_default_name_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            environment_mask_path = build_environment_mask_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                environment_mask_name=None,
                env_mask_mode="standard",
            )

            self.assertEqual(environment_mask_path.name, "brick_m.dds")

    def test_build_environment_mask_output_path_fallout4_defaults_to_s_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            environment_mask_path = build_environment_mask_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                environment_mask_name=None,
                target_game="fallout4",
            )

            self.assertEqual(environment_mask_path.name, "brick_s.dds")

    def test_build_environment_mask_output_path_uses_m_name_for_enb_complex_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            environment_mask_path = build_environment_mask_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                environment_mask_name=None,
                env_mask_mode="complex",
                complex_format="msn",
                render_profile="enb",
            )

            self.assertEqual(environment_mask_path.name, "brick_m.dds")

    def test_build_environment_mask_output_path_keeps_m_name_for_truepbr_complex_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            environment_mask_path = build_environment_mask_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                environment_mask_name=None,
                env_mask_mode="complex",
                complex_format="cm",
                render_profile="truepbr",
            )

            self.assertEqual(environment_mask_path.name, "brick_m.dds")

    def test_build_rmaos_output_path_uses_rmaos_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            rmaos_path = build_rmaos_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                rmaos_name=None,
            )

            self.assertEqual(rmaos_path.name, "brick_rmaos.dds")

    def test_build_complex_output_path_uses_msn_default_name_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            complex_path = build_complex_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                complex_name=None,
                complex_format="msn",
            )

            self.assertEqual(complex_path.name, "brick_msn.dds")

    def test_build_complex_output_path_supports_cm_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            complex_path = build_complex_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                complex_name=None,
                complex_format="cm",
            )

            self.assertEqual(complex_path.name, "brick_cm.dds")

    def test_build_single_output_paths_accept_omitted_name_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")
            output_dir = temp_path / "out"

            normal_path = build_normal_output_path(input_path=input_path, output_dir=output_dir)
            glow_path = build_glow_output_path(input_path=input_path, output_dir=output_dir)
            environment_mask_path = build_environment_mask_output_path(input_path=input_path, output_dir=output_dir)
            complex_path = build_complex_output_path(input_path=input_path, output_dir=output_dir, complex_format="msn")

            self.assertEqual(normal_path.name, "brick_n.dds")
            self.assertEqual(glow_path.name, "brick_g.dds")
            self.assertEqual(environment_mask_path.name, "brick_m.dds")
            self.assertEqual(complex_path.name, "brick_msn.dds")

    def test_collect_source_textures_from_directory_skips_generated_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for name in (
                "brick.dds",
                "stone_wall.dds",
                "brick_n.dds",
                "brick_p.dds",
                "brick_msn.dds",
                "brick_c.dds",
                "brick_rmaos.dds",
                "brick_em.dds",
                "brick_emissive.dds",
                "brick_s.dds",
                "brick_sk.dds",
                "preview.png",
            ):
                (temp_path / name).write_bytes(b"stub")

            collected = collect_source_textures(temp_path)

            self.assertEqual([path.name for path in collected], ["brick.dds", "stone_wall.dds"])

    def test_detect_mod_manager_context_reads_mo2_profile_and_loaded_texture_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            instance_root = temp_path / "MO2"
            textures_dir = instance_root / "mods" / "Texture Pack" / "textures"
            meshes_dir = instance_root / "mods" / "Texture Pack" / "meshes"
            tool_dir = instance_root / "mods" / "Skyrim Texture Generator"
            profile_dir = instance_root / "profiles" / "Default"
            textures_dir.mkdir(parents=True)
            meshes_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)
            profile_dir.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+Texture Pack\n-Disabled Mod\n", encoding="utf-8")
            (profile_dir / "plugins.txt").write_text("*Skyrim.esm\n*CommunityShaders.esp\n", encoding="utf-8")
            (profile_dir / "loadorder.txt").write_text("Skyrim.esm\nCommunityShaders.esp\n", encoding="utf-8")

            context = detect_mod_manager_context(
                {"MO_PROFILE": "Default"},
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.manager, "Mod Organizer 2")
            self.assertEqual(context.profile_name, "Default")
            self.assertEqual(context.loaded_mods, ("Texture Pack",))
            self.assertEqual(context.enabled_plugins, ("Skyrim.esm", "CommunityShaders.esp"))
            self.assertEqual(context.load_order, ("Skyrim.esm", "CommunityShaders.esp"))
            self.assertEqual(context.loaded_texture_dirs, (textures_dir.resolve(),))
            self.assertEqual(context.loaded_mesh_dirs, (meshes_dir.resolve(),))
            self.assertEqual(context.output_dir, (instance_root / "overwrite"))
            self.assertTrue(context.summary.startswith("Detected Mod (Hint): detected Mod Organizer 2 context"))
            self.assertIn("plugin(s)", context.summary)
            self.assertIn("load-order", context.summary)

    def test_detect_mod_manager_context_flags_nonstandard_mo2_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            instance_root = temp_path / "Portable MO2"
            textures_dir = instance_root / "mods" / "Texture Pack" / "textures"
            tool_dir = instance_root / "mods" / "Skyrim Texture Generator"
            profile_dir = instance_root / "profiles" / "Default"
            textures_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)
            profile_dir.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+Texture Pack\n", encoding="utf-8")

            context = detect_mod_manager_context(
                {"MO_PROFILE": "Default"},
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.manager, "Mod Organizer 2")
            self.assertIn("nonstandard MO2 layout detected at Portable MO2", context.summary)

    def test_detect_mod_manager_context_detects_body_and_skeleton_hints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            instance_root = temp_path / "MO2"
            textures_dir = instance_root / "mods" / "CBBE 3BA" / "textures"
            tool_dir = instance_root / "mods" / "Skyrim Texture Generator"
            profile_dir = instance_root / "profiles" / "Default"
            textures_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)
            profile_dir.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+CBBE 3BA\n+XPMSSE\n", encoding="utf-8")
            (profile_dir / "plugins.txt").write_text("*Skyrim.esm\n", encoding="utf-8")
            (profile_dir / "loadorder.txt").write_text("Skyrim.esm\n", encoding="utf-8")

            context = detect_mod_manager_context(
                {"MO_PROFILE": "Default"},
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.detected_body_profiles, ("CBBE", "3BA"))
            self.assertEqual(context.detected_skeleton_profiles, ("XPMSSE",))
            self.assertIn("body hints CBBE, 3BA", context.summary)
            self.assertIn("skeleton hints XPMSSE", context.summary)

    def test_detect_mod_manager_context_reads_vortex_profile_and_staging_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            appdata_dir = temp_path / "AppData" / "Roaming"
            profile_dir = appdata_dir / "Vortex" / "skyrimse" / "profiles" / "Main"
            profile_dir.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+Texture Pack\n", encoding="utf-8")
            (profile_dir / "plugins.txt").write_text("*Skyrim.esm\n*PBRNifPatcher.esp\n", encoding="utf-8")
            (profile_dir / "loadorder.txt").write_text("Skyrim.esm\nPBRNifPatcher.esp\n", encoding="utf-8")

            staging_root = temp_path / "Vortex Mods" / "skyrimse"
            textures_dir = staging_root / "Texture Pack" / "textures"
            meshes_dir = staging_root / "Texture Pack" / "meshes"
            tool_dir = staging_root / "Skyrim Texture Generator"
            textures_dir.mkdir(parents=True)
            meshes_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)

            context = detect_mod_manager_context(
                {
                    "APPDATA": str(appdata_dir),
                    "VORTEX_PROFILE": "Main",
                },
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.manager, "Vortex")
            self.assertEqual(context.profile_name, "Main")
            self.assertEqual(context.loaded_mods, ("Texture Pack",))
            self.assertEqual(context.enabled_plugins, ("Skyrim.esm", "PBRNifPatcher.esp"))
            self.assertEqual(context.load_order, ("Skyrim.esm", "PBRNifPatcher.esp"))
            self.assertEqual(context.loaded_texture_dirs, (textures_dir.resolve(),))
            self.assertEqual(context.loaded_mesh_dirs, (meshes_dir.resolve(),))
            self.assertEqual(context.staging_root, staging_root.resolve())
            self.assertEqual(context.output_dir, (tool_dir / "generated_textures").resolve())
            self.assertTrue(context.summary.startswith("Detected Mod (Hint): detected Vortex context"))

    def test_detect_mod_manager_context_handles_portable_vortex_alias_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            profile_root = temp_path / "Portable Vortex Profiles"
            profile_dir = profile_root / "Skyrim Special Edition" / "profiles" / "Main"
            profile_dir.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+Texture Pack\n", encoding="utf-8")
            (profile_dir / "plugins.txt").write_text("*Skyrim.esm\n", encoding="utf-8")
            (profile_dir / "loadorder.txt").write_text("Skyrim.esm\n", encoding="utf-8")

            staging_root = temp_path / "Vortex Mods (Portable)" / "Skyrim Special Edition"
            textures_dir = staging_root / "Texture Pack" / "textures"
            meshes_dir = staging_root / "Texture Pack" / "meshes"
            tool_dir = staging_root / "Skyrim Texture Generator"
            textures_dir.mkdir(parents=True)
            meshes_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)

            context = detect_mod_manager_context(
                {
                    "VORTEX_PROFILE_DIR": str(profile_root),
                    "VORTEX_PROFILE": "Main",
                    "VORTEX_GAME_ID": "skyrimse",
                },
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.manager, "Vortex")
            self.assertEqual(context.profile_name, "Main")
            self.assertEqual(context.loaded_mods, ("Texture Pack",))
            self.assertEqual(context.loaded_texture_dirs, (textures_dir.resolve(),))
            self.assertEqual(context.loaded_mesh_dirs, (meshes_dir.resolve(),))
            self.assertEqual(context.staging_root, staging_root.resolve())

    def test_detect_mod_manager_context_finds_portable_vortex_profiles_near_staging_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            profile_root = temp_path / "Vortex Profiles (Portable)"
            profile_dir = profile_root / "Skyrim Special Edition" / "profiles" / "Main"
            profile_dir.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+Texture Pack\n", encoding="utf-8")
            (profile_dir / "plugins.txt").write_text("*Skyrim.esm\n", encoding="utf-8")
            (profile_dir / "loadorder.txt").write_text("Skyrim.esm\n", encoding="utf-8")

            staging_root = temp_path / "Vortex Mods (Portable)" / "Skyrim Special Edition"
            textures_dir = staging_root / "Texture Pack" / "textures"
            meshes_dir = staging_root / "Texture Pack" / "meshes"
            tool_dir = staging_root / "Skyrim Texture Generator"
            textures_dir.mkdir(parents=True)
            meshes_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)

            context = detect_mod_manager_context(
                {},
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.manager, "Vortex")
            self.assertEqual(context.profile_name, "Main")
            self.assertEqual(context.loaded_mods, ("Texture Pack",))
            self.assertEqual(context.loaded_texture_dirs, (textures_dir.resolve(),))
            self.assertEqual(context.loaded_mesh_dirs, (meshes_dir.resolve(),))
            self.assertEqual(context.staging_root, staging_root.resolve())
            self.assertIn("portable Vortex profile root detected near the executable", context.summary)

    def test_detect_mod_manager_context_resolves_vortex_profile_root_to_nested_profile_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            profile_root = temp_path / "Portable Vortex"
            profile_dir = profile_root / "profiles" / "Main"
            profile_dir.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+Texture Pack\n", encoding="utf-8")
            (profile_dir / "plugins.txt").write_text("*Skyrim.esm\n", encoding="utf-8")
            (profile_dir / "loadorder.txt").write_text("Skyrim.esm\n", encoding="utf-8")

            staging_root = temp_path / "Vortex Mods" / "skyrimse"
            textures_dir = staging_root / "Texture Pack" / "textures"
            meshes_dir = staging_root / "Texture Pack" / "meshes"
            tool_dir = staging_root / "Skyrim Texture Generator"
            textures_dir.mkdir(parents=True)
            meshes_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)

            context = detect_mod_manager_context(
                {
                    "VORTEX_PROFILE_DIR": str(profile_root),
                    "VORTEX_STAGING_DIR": str(staging_root),
                    "VORTEX_GAME_ID": "skyrimse",
                },
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.manager, "Vortex")
            self.assertEqual(context.profile_name, "Main")
            self.assertEqual(context.loaded_mods, ("Texture Pack",))
            self.assertEqual(context.loaded_texture_dirs, (textures_dir.resolve(),))
            self.assertEqual(context.loaded_mesh_dirs, (meshes_dir.resolve(),))
            self.assertEqual(context.staging_root, staging_root.resolve())

    def test_detect_mod_manager_context_vortex_falls_back_to_staging_scan_when_mod_names_do_not_match_folder_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            appdata_dir = temp_path / "AppData" / "Roaming"
            profile_dir = appdata_dir / "Vortex" / "skyrimse" / "profiles" / "Main"
            profile_dir.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+Texture Pack Pretty Name\n", encoding="utf-8")

            staging_root = temp_path / "Vortex Mods" / "skyrimse"
            textures_dir = staging_root / "Texture Pack-1234" / "textures"
            meshes_dir = staging_root / "Texture Pack-1234" / "meshes"
            tool_dir = staging_root / "Skyrim Texture Generator"
            textures_dir.mkdir(parents=True)
            meshes_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)

            context = detect_mod_manager_context(
                {
                    "APPDATA": str(appdata_dir),
                    "VORTEX_PROFILE": "Main",
                },
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.manager, "Vortex")
            self.assertEqual(context.loaded_mods, ("Texture Pack Pretty Name",))
            self.assertEqual(context.loaded_texture_dirs, (textures_dir.resolve(),))
            self.assertEqual(context.loaded_mesh_dirs, (meshes_dir.resolve(),))

    def test_mod_manager_context_summary_is_explicitly_advisory_without_detection(self) -> None:
        context = ModManagerContext()

        self.assertEqual(
            context.summary,
            "Detected Mod (Hint): not detected — this is only a suggestion; your chosen input/output paths still take priority.",
        )

    def test_mod_manager_context_summary_includes_renderer_version_hints(self) -> None:
        context = ModManagerContext(
            manager="Mod Organizer 2",
            profile_name="Default",
            renderer_version_hints=("Community Shaders 1.9.1", "Community Shaders PBR 1.0.0"),
        )

        self.assertIn("version hints Community Shaders 1.9.1, Community Shaders PBR 1.0.0", context.summary)

    def test_detect_renderer_version_hints_prefers_family_specific_versions(self) -> None:
        hints = _detect_renderer_version_hints(
            (
                "Community Shaders v1.9.1",
                "PBRNifPatcher 0.9.0",
                "ENBSeries 0.495",
            )
        )

        self.assertIn("Community Shaders 1.9.1", hints)
        self.assertIn("Community Shaders PBR 0.9.0", hints)

    def test_build_truepbr_version_note_warns_on_outdated_metadata(self) -> None:
        context = ModManagerContext(
            manager="Mod Organizer 2",
            loaded_mods=("PBRNifPatcher 0.9.0",),
            renderer_version_hints=("Community Shaders PBR 0.9.0",),
        )

        note = _build_truepbr_version_note(context)

        self.assertIsNotNone(note)
        self.assertIn("fall below the release-aligned baseline", note or "")
        self.assertIn("probably incompatible", note or "")

    def test_detect_mod_manager_context_includes_mo2_overwrite_dirs_and_game_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            instance_root = temp_path / "MO2"
            textures_dir = instance_root / "mods" / "Texture Pack" / "textures"
            meshes_dir = instance_root / "mods" / "Texture Pack" / "meshes"
            overwrite_textures_dir = instance_root / "overwrite" / "textures"
            overwrite_meshes_dir = instance_root / "overwrite" / "meshes"
            tool_dir = instance_root / "mods" / "Skyrim Texture Generator"
            profile_dir = instance_root / "profiles" / "Default"
            game_root = temp_path / "SkyrimSE"
            textures_dir.mkdir(parents=True)
            meshes_dir.mkdir(parents=True)
            overwrite_textures_dir.mkdir(parents=True)
            overwrite_meshes_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)
            profile_dir.mkdir(parents=True)
            game_root.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+Texture Pack\n", encoding="utf-8")

            context = detect_mod_manager_context(
                {"MO_PROFILE": "Default", "MO2_GAME_PATH": str(game_root)},
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.game_root, game_root)
            self.assertEqual(
                context.loaded_texture_dirs,
                (overwrite_textures_dir.resolve(), textures_dir.resolve()),
            )
            self.assertEqual(
                context.loaded_mesh_dirs,
                (overwrite_meshes_dir.resolve(), meshes_dir.resolve()),
            )

    def test_find_related_nif_files_for_texture_matches_family_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            nif_path = temp_path / "meshes" / "sign01.nif"
            nif_path.parent.mkdir(parents=True)
            nif_path.write_bytes(b"")
            source = temp_path / "textures" / "sign01_d.dds"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"")

            class _FakeInfo:
                def __init__(self, texture_paths: dict[int, str]) -> None:
                    self.texture_paths = texture_paths

            related = find_related_nif_files_for_texture(
                source,
                candidate_roots=(nif_path.parent,),
                nif_info_provider=lambda _: [_FakeInfo({0: "textures\\sign01.dds"})],
            )

            self.assertEqual(related, (nif_path.resolve(),))

    def test_find_related_nif_files_for_texture_matches_alias_family_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            nif_path = temp_path / "meshes" / "sign01.nif"
            nif_path.parent.mkdir(parents=True)
            nif_path.write_bytes(b"")
            source = temp_path / "textures" / "sign01_envmask.dds"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"")

            class _FakeInfo:
                def __init__(self, texture_paths: dict[int, str]) -> None:
                    self.texture_paths = texture_paths

            related = find_related_nif_files_for_texture(
                source,
                candidate_roots=(nif_path.parent,),
                nif_info_provider=lambda _: [_FakeInfo({0: "textures\\sign01.dds"})],
            )

            self.assertEqual(related, (nif_path.resolve(),))

    def test_find_related_nif_files_for_texture_falls_back_to_nif_name_when_scan_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            nif_path = temp_path / "meshes" / "sign01.nif"
            nif_path.parent.mkdir(parents=True)
            nif_path.write_bytes(b"")
            source = temp_path / "textures" / "sign01_d.dds"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"")

            related = find_related_nif_files_for_texture(
                source,
                candidate_roots=(nif_path.parent,),
                nif_info_provider=lambda _: (_ for _ in ()).throw(ValueError("parse failed")),
            )

            self.assertEqual(related, (nif_path.resolve(),))

    def test_find_related_nif_files_for_texture_filename_fallback_avoids_unrelated_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            nif_path = temp_path / "meshes" / "different_mesh.nif"
            nif_path.parent.mkdir(parents=True)
            nif_path.write_bytes(b"")
            source = temp_path / "textures" / "sign01_d.dds"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"")

            related = find_related_nif_files_for_texture(
                source,
                candidate_roots=(nif_path.parent,),
                nif_info_provider=lambda _: (_ for _ in ()).throw(ValueError("parse failed")),
            )

            self.assertEqual(related, ())

    def test_find_related_nif_files_for_texture_searches_source_parent_when_not_under_textures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_dir = temp_path / "custom_assets"
            source_dir.mkdir(parents=True)

            source = source_dir / "sign01_d.dds"
            source.write_bytes(b"")

            nif_path = source_dir / "sign01.nif"
            nif_path.write_bytes(b"")

            related = find_related_nif_files_for_texture(
                source,
                nif_info_provider=lambda _: (_ for _ in ()).throw(ValueError("parse failed")),
            )

            self.assertEqual(related, (nif_path.resolve(),))

    def test_build_nif_patch_options_for_generated_outputs_prefers_msn_for_complex_parallax(self) -> None:
        outputs = {
            "parallax": Path("/tmp/textures/sign01_p.dds"),
            "complex_material": Path("/tmp/textures/sign01_msn.dds"),
            "environment_mask": Path("/tmp/textures/sign01_m.dds"),
        }

        options = build_nif_patch_options_for_generated_outputs(
            None,
            outputs,
            complex_format="msn",
            env_mask_mode="complex",
            parallax_mode="occlusion",
            parallax_scale=4.0,
            render_profile="community_shaders",
        )

        self.assertTrue(options.enable_parallax)
        self.assertTrue(options.enable_pom)
        self.assertTrue(options.enable_env_mapping)
        self.assertFalse(options.force_shader_type_3)
        self.assertEqual(options.normal_texture_path, "textures\\sign01_msn.dds")
        self.assertEqual(options.parallax_texture_path, "textures\\sign01_p.dds")
        self.assertEqual(options.env_mask_texture_path, "textures\\sign01_m.dds")
        self.assertEqual(options.parallax_scale, 4.0)

    def test_build_nif_patch_options_for_generated_outputs_accepts_gui_occlusion_label(self) -> None:
        outputs = {"parallax": Path("/tmp/textures/sign01_p.dds")}

        options = build_nif_patch_options_for_generated_outputs(
            None,
            outputs,
            complex_format="msn",
            env_mask_mode="standard",
            parallax_mode="occlusion (ENB/POM)",
            parallax_scale=4.0,
            render_profile="enb",
        )

        self.assertTrue(options.enable_parallax)
        self.assertTrue(options.enable_pom)

    def test_build_nif_patch_options_render_profile_vanilla_no_type3_upgrade(self) -> None:
        outputs = {"parallax": Path("/tmp/textures/brick_p.dds")}
        for profile in ("vanilla", "auto"):
            with self.subTest(profile=profile):
                options = build_nif_patch_options_for_generated_outputs(
                    None,
                    outputs,
                    complex_format="msn",
                    env_mask_mode="standard",
                    parallax_mode="standard",
                    parallax_scale=2.0,
                    render_profile=profile,
                )
                self.assertFalse(
                    options.force_shader_type_3,
                    msg=f"render_profile={profile!r} should not force shader type-3 upgrade",
                )
                self.assertTrue(options.enable_parallax)

    def test_build_nif_patch_options_render_profile_community_shaders_enables_type3(self) -> None:
        outputs = {"parallax": Path("/tmp/textures/brick_p.dds")}
        options = build_nif_patch_options_for_generated_outputs(
            None,
            outputs,
            complex_format="msn",
            env_mask_mode="standard",
            parallax_mode="standard",
            parallax_scale=2.0,
            render_profile="community_shaders",
        )
        self.assertFalse(options.force_shader_type_3)
        self.assertTrue(options.enable_parallax)

    def test_build_nif_patch_options_render_profile_enb_enables_type3(self) -> None:
        outputs = {"parallax": Path("/tmp/textures/brick_p.dds")}
        options = build_nif_patch_options_for_generated_outputs(
            None,
            outputs,
            complex_format="msn",
            env_mask_mode="standard",
            parallax_mode="standard",
            parallax_scale=2.0,
            render_profile="enb",
        )
        self.assertTrue(options.force_shader_type_3)
        self.assertTrue(options.enable_parallax)

    def test_build_nif_patch_options_render_profile_truepbr_enables_type3(self) -> None:
        outputs = {"parallax": Path("/tmp/textures/brick_p.dds")}
        options = build_nif_patch_options_for_generated_outputs(
            None,
            outputs,
            complex_format="cm",
            env_mask_mode="complex",
            parallax_mode="standard",
            parallax_scale=2.0,
            render_profile="truepbr",
        )
        self.assertFalse(options.force_shader_type_3)
        self.assertTrue(options.enable_parallax)
        self.assertTrue(options.enable_pbr)

    def test_build_nif_patch_options_auto_enables_pbr_for_rmaos_output(self) -> None:
        options = build_nif_patch_options_for_generated_outputs(
            None,
            {"rmaos": Path("/tmp/textures/brick_rmaos.dds")},
            complex_format="cm",
            env_mask_mode="complex",
            parallax_mode="standard",
            parallax_scale=None,
            render_profile="auto",
        )
        self.assertTrue(options.enable_pbr)

    def test_build_nif_patch_options_for_generated_outputs_uses_cm_for_env_slot(self) -> None:
        outputs = {"complex_material": Path("/tmp/textures/stone_cm.dds")}
        options = build_nif_patch_options_for_generated_outputs(
            None,
            outputs,
            complex_format="cm",
            env_mask_mode="standard",
            parallax_mode="standard",
            parallax_scale=None,
            render_profile="community_shaders",
        )
        self.assertTrue(options.enable_env_mapping)
        self.assertEqual(options.env_mask_texture_path, "textures\\stone_cm.dds")

    def test_build_nif_patch_options_no_parallax_never_forces_type3(self) -> None:
        for profile in ("vanilla", "community_shaders", "truepbr", "enb", "auto"):
            with self.subTest(profile=profile):
                options = build_nif_patch_options_for_generated_outputs(
                    None,
                    {},
                    complex_format="msn",
                    env_mask_mode="standard",
                    parallax_mode="standard",
                    parallax_scale=None,
                    render_profile=profile,
                )
                self.assertFalse(
                    options.force_shader_type_3,
                    msg=f"No parallax output — force_shader_type_3 must be False for {profile!r}",
                )

    def test_build_nif_patch_options_for_generated_outputs_falls_back_to_source_texture_resource_dir(self) -> None:
        source_texture = Path("/tmp/mod/textures/architecture/stone/brick.dds")
        outputs = {
            "parallax": Path("/tmp/generated/brick_p.dds"),
            "normal": Path("/tmp/generated/brick_n.dds"),
        }

        options = build_nif_patch_options_for_generated_outputs(
            source_texture,
            outputs,
            complex_format="msn",
            env_mask_mode="standard",
            parallax_mode="standard",
            parallax_scale=2.0,
        )

        self.assertEqual(options.parallax_texture_path, "textures\\architecture\\stone\\brick_p.dds")
        self.assertEqual(options.normal_texture_path, "textures\\architecture\\stone\\brick_n.dds")

    def test_build_nif_patch_options_for_generated_outputs_defaults_to_textures_prefix_without_source_context(self) -> None:
        outputs = {
            "parallax": Path("/tmp/generated/brick_p.dds"),
            "normal": Path("/tmp/generated/brick_n.dds"),
        }
        options = build_nif_patch_options_for_generated_outputs(
            None,
            outputs,
            complex_format="msn",
            env_mask_mode="standard",
            parallax_mode="standard",
            parallax_scale=2.0,
        )
        self.assertEqual(options.parallax_texture_path, "textures\\brick_p.dds")
        self.assertEqual(options.normal_texture_path, "textures\\brick_n.dds")

    def test_build_nif_patch_options_for_generated_outputs_coerces_non_dds_slots_to_dds(self) -> None:
        source_texture = Path("/tmp/mod/textures/architecture/stone/brick.dds")
        outputs = {
            "parallax": Path("/tmp/generated/brick_p.png"),
            "normal": Path("/tmp/generated/brick_n.png"),
            "environment_mask": Path("/tmp/generated/brick_m.png"),
        }

        options = build_nif_patch_options_for_generated_outputs(
            source_texture,
            outputs,
            complex_format="msn",
            env_mask_mode="standard",
            parallax_mode="standard",
            parallax_scale=2.0,
        )

        self.assertEqual(options.parallax_texture_path, "textures\\architecture\\stone\\brick_p.dds")
        self.assertEqual(options.normal_texture_path, "textures\\architecture\\stone\\brick_n.dds")
        self.assertEqual(options.env_mask_texture_path, "textures\\architecture\\stone\\brick_m.dds")

    def test_build_nif_patch_options_respects_disabled_parallax_output_setting(self) -> None:
        options = build_nif_patch_options_for_generated_outputs(
            None,
            {},
            complex_format="msn",
            env_mask_mode="standard",
            parallax_mode="standard",
            parallax_scale=2.0,
            include_parallax=False,
            include_environment_mask=True,
        )
        self.assertTrue(options.disable_parallax)
        self.assertTrue(options.clear_parallax_texture_path)
        self.assertFalse(options.enable_parallax)
        self.assertFalse(options.enable_pom)

    def test_build_nif_patch_options_respects_disabled_env_mask_output_setting(self) -> None:
        options = build_nif_patch_options_for_generated_outputs(
            None,
            {},
            complex_format="msn",
            env_mask_mode="standard",
            parallax_mode="standard",
            parallax_scale=2.0,
            include_parallax=True,
            include_environment_mask=False,
        )
        self.assertTrue(options.disable_env_mapping)
        self.assertTrue(options.clear_env_mask_texture_path)
        self.assertFalse(options.enable_env_mapping)

    def test_build_nif_patch_options_for_nif_editor_preserves_disable_and_clear_flags(self) -> None:
        options = build_nif_patch_options_for_nif_editor(
            enable_parallax=False,
            enable_pom=False,
            enable_env_mapping=False,
            enable_glow_map=False,
            enable_pbr=True,
            parallax_scale=3.5,
            force_shader_type_3=True,
            diffuse_texture_path=" textures\\arch\\stone.dds ",
            parallax_texture_path=" textures\\arch\\stone_p.dds ",
            normal_texture_path="",
            glow_texture_path=" ",
            env_mask_texture_path="textures\\arch\\stone_m.dds",
            cubemap_texture_path="textures\\cubemaps\\chrome_e.dds",
            backup=False,
            dry_run=True,
            dry_run_diff=True,
            disable_parallax=True,
            disable_pom=True,
            disable_env_mapping=True,
            disable_glow_map=True,
            disable_pbr=True,
            clear_parallax_texture_path=True,
            clear_normal_texture_path=True,
            clear_env_mask_texture_path=True,
            clear_glow_texture_path=True,
            clear_diffuse_texture_path=True,
            clear_cubemap_texture_path=True,
        )

        self.assertIsNone(options.parallax_scale)
        self.assertEqual(options.diffuse_texture_path, "textures\\arch\\stone.dds")
        self.assertEqual(options.parallax_texture_path, "textures\\arch\\stone_p.dds")
        self.assertIsNone(options.normal_texture_path)
        self.assertIsNone(options.glow_texture_path)
        self.assertEqual(options.env_mask_texture_path, "textures\\arch\\stone_m.dds")
        self.assertEqual(options.cubemap_texture_path, "textures\\cubemaps\\chrome_e.dds")
        self.assertTrue(options.enable_pbr)
        self.assertTrue(options.disable_parallax)
        self.assertTrue(options.disable_pom)
        self.assertTrue(options.disable_env_mapping)
        self.assertTrue(options.disable_glow_map)
        self.assertTrue(options.disable_pbr)
        self.assertTrue(options.clear_parallax_texture_path)
        self.assertTrue(options.clear_normal_texture_path)
        self.assertTrue(options.clear_env_mask_texture_path)
        self.assertTrue(options.clear_glow_texture_path)
        self.assertTrue(options.clear_diffuse_texture_path)
        self.assertTrue(options.clear_cubemap_texture_path)
        self.assertFalse(options.backup)
        self.assertTrue(options.dry_run)
        self.assertTrue(options.dry_run_diff)

    def test_build_nif_patch_options_for_nif_editor_normalizes_manual_paths_to_dds(self) -> None:
        options = build_nif_patch_options_for_nif_editor(
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=True,
            enable_glow_map=False,
            enable_pbr=False,
            parallax_scale=2.0,
            force_shader_type_3=False,
            diffuse_texture_path=r"Data\Textures\architecture\stone\stone.png",
            parallax_texture_path=r"C:\Mod\Data\Textures\architecture\stone\stone_p.tga",
            normal_texture_path=r"textures\architecture\stone\stone_n.jpeg",
            glow_texture_path="",
            env_mask_texture_path=r"textures\architecture\stone\stone_cm.bmp",
            cubemap_texture_path=r"textures\cubemaps\chrome_e.PNG",
        )

        self.assertEqual(options.diffuse_texture_path, r"textures\architecture\stone\stone.dds")
        self.assertEqual(options.parallax_texture_path, r"textures\architecture\stone\stone_p.dds")
        self.assertEqual(options.normal_texture_path, r"textures\architecture\stone\stone_n.dds")
        self.assertEqual(options.env_mask_texture_path, r"textures\architecture\stone\stone_cm.dds")
        self.assertEqual(options.cubemap_texture_path, r"textures\cubemaps\chrome_e.dds")

    def test_build_nif_patch_options_for_nif_editor_carries_fallout_gate_flags(self) -> None:
        options = build_nif_patch_options_for_nif_editor(
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=False,
            enable_glow_map=False,
            enable_pbr=False,
            parallax_scale=2.0,
            force_shader_type_3=False,
            target_game="fallout",
            experimental_fallout_write=True,
            fallout_allow_parallax_scale=True,
            fallout_allow_fix_mesh_lighting=True,
            fallout_allow_spec_strength=True,
            fallout_allow_spec_color=True,
            fallout_allow_env_map_scale=True,
        )
        self.assertTrue(options.fallout_allow_parallax_scale)
        self.assertTrue(options.fallout_allow_fix_mesh_lighting)
        self.assertTrue(options.fallout_allow_spec_strength)
        self.assertTrue(options.fallout_allow_spec_color)
        self.assertTrue(options.fallout_allow_env_map_scale)

    def test_build_nif_patch_options_for_nif_editor_prefixes_relative_paths_with_textures(self) -> None:
        options = build_nif_patch_options_for_nif_editor(
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=True,
            enable_glow_map=False,
            enable_pbr=False,
            parallax_scale=2.0,
            force_shader_type_3=False,
            diffuse_texture_path=r"architecture\stone\stone.dds",
            parallax_texture_path=r"stone_p.dds",
            normal_texture_path=r"meshes\shared\stone_n.dds",
            env_mask_texture_path=r"architecture\stone\stone_m.dds",
            cubemap_texture_path=r"cubemaps\chrome_e.dds",
        )
        self.assertEqual(options.diffuse_texture_path, r"textures\architecture\stone\stone.dds")
        self.assertEqual(options.parallax_texture_path, r"textures\stone_p.dds")
        self.assertEqual(options.normal_texture_path, r"textures\meshes\shared\stone_n.dds")
        self.assertEqual(options.env_mask_texture_path, r"textures\architecture\stone\stone_m.dds")
        self.assertEqual(options.cubemap_texture_path, r"textures\cubemaps\chrome_e.dds")

    def test_normalize_nif_editor_texture_input_path_returns_none_for_blank(self) -> None:
        self.assertIsNone(_normalize_nif_editor_texture_input_path("   "))

    def test_resolve_nif_editor_patch_options_for_target_autofills_missing_paths(self) -> None:
        base_options = build_nif_patch_options_for_nif_editor(
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=True,
            enable_glow_map=True,
            enable_pbr=True,
            parallax_scale=2.0,
            force_shader_type_3=False,
            backup=False,
        )

        with mock.patch("generate_textures.guess_parallax_path_for_nif", return_value=r"textures\arch\stone_p.dds"), \
             mock.patch("generate_textures.guess_normal_path_for_nif", return_value=r"textures\arch\stone_msn.dds"), \
             mock.patch("generate_textures.guess_glow_path_for_nif", return_value=r"textures\arch\stone_g.dds"), \
             mock.patch("generate_textures.guess_env_mask_path_for_nif", return_value=r"textures\arch\stone_rmaos.dds"), \
             mock.patch("generate_textures.guess_cubemap_path_for_nif", return_value=r"textures\cubemaps\stone_e.dds"):
            resolved, notes = resolve_nif_editor_patch_options_for_target(
                Path("example.nif"),
                base_options,
                prefer_msn_normal=True,
                preferred_env_mask_suffix="_rmaos.dds",
            )

        self.assertEqual(resolved.parallax_texture_path, r"textures\arch\stone_p.dds")
        self.assertEqual(resolved.normal_texture_path, r"textures\arch\stone_msn.dds")
        self.assertEqual(resolved.glow_texture_path, r"textures\arch\stone_g.dds")
        self.assertEqual(resolved.env_mask_texture_path, r"textures\arch\stone_rmaos.dds")
        self.assertEqual(resolved.cubemap_texture_path, r"textures\cubemaps\stone_e.dds")
        self.assertTrue(any("slot 3 parallax path" in note for note in notes))

    def test_resolve_nif_editor_patch_options_for_target_keeps_manual_paths(self) -> None:
        base_options = build_nif_patch_options_for_nif_editor(
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=False,
            enable_glow_map=False,
            enable_pbr=False,
            parallax_scale=2.0,
            force_shader_type_3=False,
            parallax_texture_path=r"textures\manual\stone_p.dds",
            normal_texture_path=r"textures\manual\stone_n.dds",
            backup=False,
        )

        with mock.patch("generate_textures.guess_parallax_path_for_nif") as guess_parallax, \
             mock.patch("generate_textures.guess_normal_path_for_nif") as guess_normal:
            resolved, notes = resolve_nif_editor_patch_options_for_target(Path("example.nif"), base_options)

        self.assertEqual(resolved.parallax_texture_path, r"textures\manual\stone_p.dds")
        self.assertEqual(resolved.normal_texture_path, r"textures\manual\stone_n.dds")
        self.assertEqual(notes, ())
        guess_parallax.assert_not_called()
        guess_normal.assert_not_called()

    def test_auto_patch_related_nifs_for_texture_patches_all_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "textures" / "sign01.dds"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"")
            nif_path = temp_path / "meshes" / "sign01.nif"
            nif_path.parent.mkdir(parents=True)
            nif_path.write_bytes(b"")
            outputs = {"parallax": temp_path / "textures" / "sign01_p.dds"}
            outputs["parallax"].write_bytes(b"")

            with mock.patch("generate_textures.find_related_nif_files_for_texture", return_value=(nif_path,)), mock.patch(
                "generate_textures.patch_nif", return_value=mock.Mock(success=True)
            ) as patch_nif_mock:
                results = auto_patch_related_nifs_for_texture(
                    source,
                    outputs,
                    output_dir=None,
                    manager_context=None,
                    complex_format="msn",
                    env_mask_mode="standard",
                    parallax_mode="standard",
                    parallax_scale=2.0,
                    include_parallax=True,
                    include_environment_mask=False,
                )

            self.assertEqual(len(results), 1)
            patch_nif_mock.assert_called_once()
            passed_options = patch_nif_mock.call_args.args[1]
            self.assertFalse(bool(passed_options.disable_parallax))
            self.assertTrue(bool(passed_options.disable_env_mapping))

    def test_run_with_options_requires_at_least_one_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            _sample_image().save(input_path)

            with self.assertRaises(ValueError):
                run_with_options(
                    input_file=input_path,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                )

    def test_run_with_options_writes_openable_dds_files_for_png_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            outputs = run_with_options(
                input_file=input_path,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=True,
                include_parallax=True,
                include_glow=True,
                include_environment_mask=True,
                include_complex=True,
            )

            expected_names = {
                "diffuse": "brick.dds",
                "normal": "brick_n.dds",
                "parallax": "brick_p.dds",
                "glow": "brick_g.dds",
                "environment_mask": "brick_m.dds",
                "complex_material": "brick_msn.dds",
            }
            self.assertEqual({key: value.name for key, value in outputs.items()}, expected_names)

            for path in outputs.values():
                self.assertEqual(path.suffix.lower(), ".dds")
                with Image.open(path) as generated:
                    generated.load()
                    self.assertEqual(generated.size, (8, 8))

    def test_run_with_options_writes_msn_with_alpha_when_complex_format_is_msn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            outputs = run_with_options(
                input_file=input_path,
                output_dir=output_dir,
                include_diffuse=False,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=True,
                complex_format="msn",
            )

            with Image.open(outputs["complex_material"]) as generated:
                generated.load()
                self.assertIn(generated.mode, ("RGBA", "RGB"))

    def test_run_batch_with_options_processes_only_original_dds_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            input_dir.mkdir()

            for name in ("brick.dds", "stone_wall.dds", "brick_n.dds", "brick_p.dds", "stone_wall_msn.dds"):
                _sample_image().save(input_dir / name, format="DDS", pixel_format="DXT5")

            outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
            )

            self.assertEqual(sorted(path.name for path in outputs.keys()), ["brick.dds", "stone_wall.dds"])
            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), ["brick.dds", "stone_wall.dds"])

    def test_collect_source_textures_scans_subfolders_and_skips_generated_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "input"
            nested = root / "nested"
            deeper = nested / "deeper"
            deeper.mkdir(parents=True)

            _sample_image().save(root / "top.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(nested / "brick.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_n.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_c.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_rmaos.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_em.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_emissive.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_sk.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_specular.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_height.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_envmask.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_roughness.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_metalness.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_ao.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_orm.dds", format="DDS", pixel_format="DXT5")

            discovered = collect_source_textures(root)
            self.assertEqual(
                sorted(path.relative_to(root).as_posix() for path in discovered),
                ["nested/brick.dds", "nested/deeper/stone.dds", "top.dds"],
            )

    def test_collect_source_textures_includes_uppercase_dds_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "input"
            nested = root / "nested"
            nested.mkdir(parents=True)

            _sample_image().save(root / "top.DDS", format="DDS", pixel_format="DXT5")
            _sample_image().save(nested / "stone.DDS", format="DDS", pixel_format="DXT5")
            _sample_image().save(nested / "stone_n.DDS", format="DDS", pixel_format="DXT5")

            discovered = collect_source_textures(root)
            self.assertEqual(
                sorted(path.relative_to(root).as_posix() for path in discovered),
                ["nested/stone.DDS", "top.DDS"],
            )

    def test_select_generation_context_source_prefers_first_selected_input(self) -> None:
        folder_path = Path("textures/architecture")
        selected = [
            Path("textures/architecture/stone.dds"),
            Path("textures/architecture/brick.dds"),
        ]
        self.assertEqual(select_generation_context_source(folder_path, selected), selected[0])

    def test_select_generation_context_source_falls_back_to_input_path(self) -> None:
        input_path = Path("textures/architecture/stone.dds")
        self.assertEqual(select_generation_context_source(input_path, []), input_path)

    def test_run_batch_with_options_processes_subfolder_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            nested = input_dir / "nested"
            output_dir = temp_path / "out"
            nested.mkdir(parents=True)

            _sample_image().save(input_dir / "top.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(nested / "inner.dds", format="DDS", pixel_format="DXT5")

            outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
                batch_workers=1,
            )

            self.assertEqual(sorted(path.name for path in outputs.keys()), ["inner.dds", "top.dds"])
            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), ["inner.dds", "top.dds"])

    def test_run_batch_with_options_can_continue_on_file_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            input_dir.mkdir()

            _sample_image().save(input_dir / "good.dds", format="DDS", pixel_format="DXT5")
            (input_dir / "bad.dds").write_bytes(b"this is not a valid dds")
            errors: list[tuple[str, str]] = []

            outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
                continue_on_error=True,
                error_callback=lambda _index, _total, current, exc: errors.append((current.name, str(exc))),
            )

            self.assertEqual(sorted(path.name for path in outputs.keys()), ["good.dds"])
            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), ["good.dds"])
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0][0], "bad.dds")

    def test_run_batch_with_options_supports_parallel_workers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            input_dir.mkdir()

            for name in ("a.dds", "b.dds", "c.dds", "d.dds"):
                _sample_image().save(input_dir / name, format="DDS", pixel_format="DXT5")

            outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
                batch_workers=2,
            )

            self.assertEqual(sorted(path.name for path in outputs.keys()), ["a.dds", "b.dds", "c.dds", "d.dds"])
            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), ["a.dds", "b.dds", "c.dds", "d.dds"])

    def test_run_batch_with_options_writes_checkpoint_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            checkpoint = temp_path / "state" / "batch_checkpoint.json"
            input_dir.mkdir()
            for name in ("a.dds", "b.dds"):
                _sample_image().save(input_dir / name, format="DDS", pixel_format="DXT5")

            outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
                batch_workers=1,
                checkpoint_file=checkpoint,
            )

            payload = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], APP_VERSION)
            self.assertEqual(payload["completed_success_count"], 2)
            self.assertEqual(payload["resumed_completed_count"], 0)
            self.assertEqual(len(payload["completed_success_files"]), 2)
            self.assertEqual(payload["output_root"], str(output_dir.resolve()))
            self.assertTrue(payload["generation_options"]["include_diffuse"])
            self.assertEqual(len(outputs), 2)

    def test_run_batch_with_options_rejects_resume_with_changed_output_or_generation_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            checkpoint = temp_path / "batch_checkpoint.json"
            input_dir.mkdir()
            _sample_image().save(input_dir / "a.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(input_dir / "b.dds", format="DDS", pixel_format="DXT5")
            baseline_options = {
                "include_diffuse": True,
                "include_normal": False,
                "include_parallax": False,
                "include_glow": False,
                "include_environment_mask": False,
                "include_complex": False,
                "batch_workers": 1,
                "checkpoint_file": checkpoint,
            }
            run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                **baseline_options,
            )

            with self.assertRaisesRegex(ValueError, "generation settings differ"):
                run_batch_with_options(
                    input_path=input_dir,
                    output_dir=output_dir,
                    **{**baseline_options, "include_normal": True, "resume_from_checkpoint": True},
                )

            with self.assertRaisesRegex(ValueError, "output folder differs"):
                run_batch_with_options(
                    input_path=input_dir,
                    output_dir=temp_path / "different-output",
                    **{**baseline_options, "resume_from_checkpoint": True},
                )

    def test_run_batch_with_options_rejects_corrupt_resume_checkpoint_without_overwriting(self) -> None:
        malformed_checkpoints = (
            ("invalid-json", b'{"completed_success_files": ['),
            ("non-object-json", b"[]"),
            ("invalid-completed-list", b'{"completed_success_files": ["valid", 3]}'),
            ("wrong-completed-type", b'{"completed_success_files": "valid"}'),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            input_dir.mkdir()
            _sample_image().save(input_dir / "a.dds", format="DDS", pixel_format="DXT5")
            for case_name, checkpoint_contents in malformed_checkpoints:
                with self.subTest(case=case_name):
                    checkpoint = temp_path / f"{case_name}.json"
                    checkpoint.write_bytes(checkpoint_contents)
                    with mock.patch("generate_textures.run_with_options") as generate_mock:
                        with self.assertRaisesRegex(ValueError, "Checkpoint"):
                            run_batch_with_options(
                                input_path=input_dir,
                                output_dir=temp_path / "out",
                                include_diffuse=True,
                                include_normal=False,
                                include_parallax=False,
                                include_glow=False,
                                include_environment_mask=False,
                                include_complex=False,
                                checkpoint_file=checkpoint,
                                resume_from_checkpoint=True,
                            )
                    generate_mock.assert_not_called()
                    self.assertEqual(checkpoint.read_bytes(), checkpoint_contents)

    def test_run_batch_with_options_resume_checkpoint_skips_completed_successes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            checkpoint = temp_path / "batch_checkpoint.json"
            input_dir.mkdir()
            a_path = input_dir / "a.dds"
            b_path = input_dir / "b.dds"
            _sample_image().save(a_path, format="DDS", pixel_format="DXT5")
            _sample_image().save(b_path, format="DDS", pixel_format="DXT5")
            checkpoint.write_text(
                json.dumps(
                    {
                        "completed_success_files": [str(a_path.resolve())],
                    }
                ),
                encoding="utf-8",
            )

            outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
                batch_workers=1,
                checkpoint_file=checkpoint,
                resume_from_checkpoint=True,
            )

            self.assertEqual(sorted(path.name for path in outputs.keys()), ["b.dds"])
            payload = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(payload["resumed_completed_count"], 1)
            self.assertEqual(payload["completed_success_count"], 2)

    def test_run_batch_with_options_recovers_interrupted_multiresolution_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            checkpoint = temp_path / "batch_checkpoint.json"
            input_dir.mkdir()
            dimensions = {
                f"source_{index:02d}": (
                    8 + (index % 4) * 4,
                    8 + (index % 3) * 4,
                )
                for index in range(12)
            }
            for name, size in dimensions.items():
                Image.new("RGB", size, color=(60, 100, 140)).save(
                    input_dir / f"{name}.dds",
                    format="DDS",
                    pixel_format="DXT5",
                )

            original_run_with_options = run_with_options
            first_attempts: list[str] = []

            def interrupt_after_first_success(**kwargs: object) -> dict[str, Path]:
                input_file = kwargs["input_file"]
                assert isinstance(input_file, Path)
                first_attempts.append(input_file.stem)
                if input_file.stem == "source_04":
                    raise RuntimeError("synthetic interruption")
                return original_run_with_options(**kwargs)

            with mock.patch(
                "generate_textures.run_with_options",
                side_effect=interrupt_after_first_success,
            ):
                with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
                    run_batch_with_options(
                        input_path=input_dir,
                        output_dir=output_dir,
                        include_diffuse=True,
                        include_normal=True,
                        include_parallax=True,
                        include_glow=False,
                        include_environment_mask=False,
                        include_complex=False,
                        batch_workers=1,
                        checkpoint_file=checkpoint,
                    )

            interrupted_state = json.loads(checkpoint.read_text(encoding="utf-8"))
            first_completed = [str((input_dir / f"source_{index:02d}.dds").resolve()) for index in range(4)]
            self.assertEqual(interrupted_state["completed_success_files"], first_completed)
            self.assertEqual(first_attempts, [f"source_{index:02d}" for index in range(5)])

            second_attempts: list[str] = []

            def interrupt_second_resume(**kwargs: object) -> dict[str, Path]:
                input_file = kwargs["input_file"]
                assert isinstance(input_file, Path)
                second_attempts.append(input_file.stem)
                if input_file.stem == "source_08":
                    raise RuntimeError("synthetic second interruption")
                return original_run_with_options(**kwargs)

            with mock.patch(
                "generate_textures.run_with_options",
                side_effect=interrupt_second_resume,
            ):
                with self.assertRaisesRegex(RuntimeError, "synthetic second interruption"):
                    run_batch_with_options(
                        input_path=input_dir,
                        output_dir=output_dir,
                        include_diffuse=True,
                        include_normal=True,
                        include_parallax=True,
                        include_glow=False,
                        include_environment_mask=False,
                        include_complex=False,
                        batch_workers=1,
                        checkpoint_file=checkpoint,
                        resume_from_checkpoint=True,
                    )

            second_state = json.loads(checkpoint.read_text(encoding="utf-8"))
            all_completed_before_second_interrupt = [
                str((input_dir / f"source_{index:02d}.dds").resolve())
                for index in range(8)
            ]
            self.assertEqual(second_state["completed_success_files"], all_completed_before_second_interrupt)
            self.assertEqual(second_state["resumed_completed_count"], 4)
            self.assertEqual(second_attempts, [f"source_{index:02d}" for index in range(4, 9)])

            final_attempts: list[str] = []

            def record_final_resume(**kwargs: object) -> dict[str, Path]:
                input_file = kwargs["input_file"]
                assert isinstance(input_file, Path)
                final_attempts.append(input_file.stem)
                return original_run_with_options(**kwargs)

            with mock.patch(
                "generate_textures.run_with_options",
                side_effect=record_final_resume,
            ):
                resumed_outputs = run_batch_with_options(
                    input_path=input_dir,
                    output_dir=output_dir,
                    include_diffuse=True,
                    include_normal=True,
                    include_parallax=True,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                    batch_workers=1,
                    checkpoint_file=checkpoint,
                    resume_from_checkpoint=True,
                )

            self.assertEqual(final_attempts, [f"source_{index:02d}" for index in range(8, 12)])
            self.assertEqual(
                sorted(path.name for path in resumed_outputs),
                [f"source_{index:02d}.dds" for index in range(8, 12)],
            )
            self.assertEqual(
                sorted(path.name for path in output_dir.iterdir()),
                sorted(
                    f"{name}{suffix}.dds"
                    for name in dimensions
                    for suffix in ("", "_n", "_p")
                ),
            )
            for name, size in dimensions.items():
                for suffix in ("", "_n", "_p"):
                    with Image.open(output_dir / f"{name}{suffix}.dds") as output_image:
                        self.assertEqual(output_image.size, size)
            resumed_state = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(resumed_state["resumed_completed_count"], 8)
            self.assertEqual(resumed_state["completed_success_count"], 12)

    def test_gui_batch_worker_cancellation_checkpoint_resumes_long_run(self) -> None:
        if not all(
            hasattr(TextureGeneratorGUI, name)
            for name in (
                "_process_generation_batch",
                "_record_created_outputs",
                "_write_batch_checkpoint_state",
            )
        ):
            self.skipTest("Tk batch worker is unavailable in this environment.")

        class _BatchWorker:
            _record_created_outputs = TextureGeneratorGUI._record_created_outputs
            _write_batch_checkpoint_state = TextureGeneratorGUI._write_batch_checkpoint_state

            def __init__(self) -> None:
                self.cancel_requested = False
                self.processing_queue = queue.Queue()
                self.last_generation_backups: dict[Path, Path] = {}
                self.last_generation_created_files: set[Path] = set()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "input"
            output_dir = root / "out"
            checkpoint = root / "batch_checkpoint.json"
            input_dir.mkdir()
            dimensions = {
                f"source_{index:02d}": (8 + (index % 4) * 4, 8 + (index % 3) * 4)
                for index in range(48)
            }
            input_files = []
            for name, size in dimensions.items():
                input_file = input_dir / f"{name}.dds"
                Image.new("RGB", size, color=(60, 100, 140)).save(
                    input_file,
                    format="DDS",
                    pixel_format="DXT5",
                )
                input_files.append(input_file)

            worker = _BatchWorker()
            interrupted_after = 11
            first_attempts: list[str] = []

            def cancel_after_completed_files(**kwargs: object) -> dict[str, Path]:
                input_file = kwargs["input_file"]
                assert isinstance(input_file, Path)
                first_attempts.append(input_file.stem)
                if len(first_attempts) == interrupted_after:
                    worker.cancel_requested = True
                return {
                    "diffuse": output_dir / f"{input_file.stem}.dds",
                    "normal": output_dir / f"{input_file.stem}_n.dds",
                }

            with mock.patch(
                "generate_textures.run_with_options",
                side_effect=cancel_after_completed_files,
            ):
                TextureGeneratorGUI._process_generation_batch(
                    worker,
                    input_files,
                    {"output_dir": output_dir},
                    input_dir,
                    checkpoint,
                    set(),
                    0,
                )

            interrupted_events = []
            while not worker.processing_queue.empty():
                interrupted_events.append(worker.processing_queue.get_nowait())
            self.assertEqual(
                [event_type for event_type, _payload in interrupted_events],
                ["progress"] * interrupted_after + ["cancelled"],
            )
            cancelled_event = interrupted_events[-1]
            self.assertEqual(cancelled_event[0], "cancelled")
            cancelled_results, cancellation_telemetry = cancelled_event[1]
            self.assertEqual(first_attempts, [f"source_{index:02d}" for index in range(interrupted_after)])
            self.assertEqual(len(cancelled_results), interrupted_after)
            self.assertEqual(cancellation_telemetry["checkpoint_path"], str(checkpoint))
            interrupted_state = json.loads(checkpoint.read_text(encoding="utf-8"))
            completed_before_resume = [
                str((input_dir / f"source_{index:02d}.dds").resolve())
                for index in range(interrupted_after)
            ]
            self.assertEqual(interrupted_state["completed_success_files"], completed_before_resume)

            remaining_files = [
                input_file
                for input_file in input_files
                if str(input_file.resolve()) not in interrupted_state["completed_success_files"]
            ]
            resumed_attempts: list[str] = []

            def complete_remaining_files(**kwargs: object) -> dict[str, Path]:
                input_file = kwargs["input_file"]
                assert isinstance(input_file, Path)
                resumed_attempts.append(input_file.stem)
                return {
                    "diffuse": output_dir / f"{input_file.stem}.dds",
                    "normal": output_dir / f"{input_file.stem}_n.dds",
                }

            worker.cancel_requested = False
            with mock.patch(
                "generate_textures.run_with_options",
                side_effect=complete_remaining_files,
            ):
                TextureGeneratorGUI._process_generation_batch(
                    worker,
                    remaining_files,
                    {"output_dir": output_dir},
                    input_dir,
                    checkpoint,
                    set(interrupted_state["completed_success_files"]),
                    interrupted_after,
                )

            resumed_events = []
            while not worker.processing_queue.empty():
                resumed_events.append(worker.processing_queue.get_nowait())
            self.assertEqual(
                [event_type for event_type, _payload in resumed_events],
                ["progress"] * (len(dimensions) - interrupted_after) + ["done"],
            )
            done_event = resumed_events[-1]
            self.assertEqual(done_event[0], "done")
            resumed_results, resumed_telemetry = done_event[1]
            self.assertEqual(
                resumed_attempts,
                [f"source_{index:02d}" for index in range(interrupted_after, len(dimensions))],
            )
            self.assertEqual(len(resumed_results), len(dimensions) - interrupted_after)
            self.assertEqual(resumed_telemetry["resumed_completed_count"], interrupted_after)
            completed_state = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(completed_state["completed_success_count"], len(dimensions))
            self.assertEqual(completed_state["resumed_completed_count"], interrupted_after)

    def test_run_batch_with_options_recovers_failed_file_under_parallel_queue_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            checkpoint = temp_path / "batch_checkpoint.json"
            telemetry = temp_path / "batch_telemetry.json"
            input_dir.mkdir()
            input_count = 40
            dimensions = {
                f"source_{index:02d}": (8 + (index % 4) * 4, 8 + (index % 3) * 4)
                for index in range(input_count)
            }
            for name, size in dimensions.items():
                Image.new("RGB", size, color=(60, 100, 140)).save(
                    input_dir / f"{name}.dds",
                    format="DDS",
                    pixel_format="DXT5",
                )
            failed_source = input_dir / "source_07.dds"
            failed_source.write_bytes(b"invalid DDS source")
            first_progress: list[tuple[int, int, str]] = []
            first_errors: list[str] = []

            first_outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=True,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
                continue_on_error=True,
                batch_workers=4,
                checkpoint_file=checkpoint,
                batch_telemetry_file=telemetry,
                progress_callback=lambda index, total, path: first_progress.append(
                    (index, total, path.name)
                ),
                error_callback=lambda _index, _total, path, _exc: first_errors.append(path.name),
            )

            self.assertEqual(len(first_outputs), input_count - 1)
            self.assertEqual(first_errors, [failed_source.name])
            self.assertEqual(len(first_progress), input_count)
            self.assertEqual({total for _index, total, _name in first_progress}, {input_count})
            first_state = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(first_state["completed_success_count"], input_count - 1)
            self.assertNotIn(str(failed_source.resolve()), first_state["completed_success_files"])
            first_telemetry = json.loads(telemetry.read_text(encoding="utf-8"))["summary"]
            self.assertEqual(first_telemetry["processed_files"], input_count)
            self.assertEqual(first_telemetry["successful_files"], input_count - 1)
            self.assertEqual(first_telemetry["failed_files"], 1)

            Image.new("RGB", dimensions[failed_source.stem], color=(60, 100, 140)).save(
                failed_source,
                format="DDS",
                pixel_format="DXT5",
            )
            resumed_progress: list[tuple[int, int, str]] = []
            resumed_outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=True,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
                batch_workers=4,
                checkpoint_file=checkpoint,
                resume_from_checkpoint=True,
                batch_telemetry_file=telemetry,
                progress_callback=lambda index, total, path: resumed_progress.append(
                    (index, total, path.name)
                ),
            )

            self.assertEqual([name for _index, _total, name in resumed_progress], [failed_source.name])
            self.assertEqual({total for _index, total, _name in resumed_progress}, {1})
            self.assertEqual([path.name for path in resumed_outputs], [failed_source.name])
            expected_outputs = sorted(
                output_name
                for name in dimensions
                for output_name in (f"{name}.dds", f"{name}_n.dds")
            )
            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), expected_outputs)
            for output_path in output_dir.glob("*.dds"):
                source_name = output_path.stem.removesuffix("_n")
                with Image.open(output_path) as output_image:
                    self.assertEqual(output_image.size, dimensions[source_name])
            resumed_state = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(resumed_state["resumed_completed_count"], input_count - 1)
            self.assertEqual(resumed_state["completed_success_count"], input_count)
            resumed_telemetry = json.loads(telemetry.read_text(encoding="utf-8"))["summary"]
            self.assertEqual(resumed_telemetry["processed_files"], 1)
            self.assertEqual(resumed_telemetry["successful_files"], 1)
            self.assertEqual(resumed_telemetry["failed_files"], 0)
            self.assertEqual(resumed_telemetry["resumed_completed_count"], input_count - 1)

    def test_run_batch_with_options_resumes_truepbr_feature_map_combination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "input"
            output_dir = root / "out"
            checkpoint = root / "checkpoint.json"
            input_dir.mkdir()
            source_sizes = {
                "01_good": (12, 10),
                "02_repair": (16, 12),
            }
            for stem, size in source_sizes.items():
                Image.new("RGB", size, color=(60, 100, 140)).save(
                    input_dir / f"{stem}.dds",
                    format="DDS",
                    pixel_format="DXT5",
                )
            repair_path = input_dir / "02_repair.dds"
            repair_path.write_bytes(b"corrupt source DDS")
            options = {
                "input_path": input_dir,
                "output_dir": output_dir,
                "include_diffuse": True,
                "include_normal": True,
                "include_parallax": False,
                "include_glow": True,
                "include_environment_mask": False,
                "include_rmaos": True,
                "include_complex": False,
                "render_profile": "truepbr",
                "include_wetness_mask": True,
                "include_snow_mask": True,
                "include_ao": True,
                "include_roughness": True,
                "continue_on_error": True,
                "batch_workers": 1,
                "checkpoint_file": checkpoint,
            }
            errors: list[str] = []
            initial_outputs = run_batch_with_options(
                **options,
                error_callback=lambda _index, _total, path, _error: errors.append(path.name),
            )
            self.assertEqual(errors, [repair_path.name])
            self.assertEqual([path.name for path in initial_outputs], ["01_good.dds"])
            initial_maps = initial_outputs[input_dir / "01_good.dds"]
            self.assertEqual(
                set(initial_maps),
                {
                    "diffuse",
                    "normal",
                    "glow",
                    "rmaos",
                    "rmaos_json",
                    "wetness_mask",
                    "snow_mask",
                    "ao",
                    "roughness",
                },
            )
            checkpoint_after_failure = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(checkpoint_after_failure["completed_success_count"], 1)
            self.assertEqual(
                checkpoint_after_failure["generation_options"]["include_rmaos"],
                True,
            )
            self.assertEqual(
                checkpoint_after_failure["generation_options"]["include_wetness_mask"],
                True,
            )
            self.assertNotIn(str(repair_path.resolve()), checkpoint_after_failure["completed_success_files"])

            Image.new("RGB", source_sizes["02_repair"], color=(60, 100, 140)).save(
                repair_path,
                format="DDS",
                pixel_format="DXT5",
            )
            resumed_outputs = run_batch_with_options(
                **options,
                resume_from_checkpoint=True,
            )
            self.assertEqual([path.name for path in resumed_outputs], ["02_repair.dds"])
            resumed_maps = resumed_outputs[repair_path]
            self.assertEqual(set(resumed_maps), set(initial_maps))
            self.assertEqual(
                {key: Path(path).name for key, path in resumed_maps.items()},
                {
                    "diffuse": "02_repair.dds",
                    "normal": "02_repair_n.dds",
                    "glow": "02_repair_g.dds",
                    "rmaos": "02_repair_rmaos.dds",
                    "rmaos_json": "02_repair_rmaos.json",
                    "wetness_mask": "02_repair_wt.dds",
                    "snow_mask": "02_repair_sm.dds",
                    "ao": "02_repair_ao.dds",
                    "roughness": "02_repair_rough.dds",
                },
            )
            self.assertTrue(Path(resumed_maps["rmaos_json"]).is_file())
            final_checkpoint = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(final_checkpoint["resumed_completed_count"], 1)
            self.assertEqual(final_checkpoint["completed_success_count"], 2)

    def test_run_batch_with_options_writes_batch_telemetry_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            telemetry = temp_path / "reports" / "batch_telemetry.json"
            input_dir.mkdir()
            for name in ("a.dds", "b.dds"):
                _sample_image().save(input_dir / name, format="DDS", pixel_format="DXT5")

            outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
                batch_workers=1,
                batch_telemetry_file=telemetry,
            )

            self.assertEqual(len(outputs), 2)
            payload = json.loads(telemetry.read_text(encoding="utf-8"))
            self.assertEqual(payload["tool"], "generate_textures")
            self.assertEqual(payload["version"], APP_VERSION)
            summary = payload["summary"]
            self.assertEqual(summary["processed_files"], 2)
            self.assertEqual(summary["successful_files"], 2)
            self.assertEqual(summary["failed_files"], 0)
            self.assertEqual(summary["workers"], 1)
            self.assertEqual(summary["sampled_resolution_files"], 2)
            self.assertGreaterEqual(summary["max_source_dimension"], 8)
            self.assertGreaterEqual(summary["max_source_megapixels"], 0.0)
            self.assertGreaterEqual(summary["total_duration_seconds"], 0.0)
            self.assertEqual(len(payload["per_file_duration_seconds"]), 2)

    def test_run_batch_with_options_records_failed_files_in_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            telemetry = temp_path / "batch_telemetry.json"
            input_dir.mkdir()
            _sample_image().save(input_dir / "good.dds", format="DDS", pixel_format="DXT5")
            (input_dir / "bad.dds").write_bytes(b"this is not a valid dds")

            outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
                continue_on_error=True,
                batch_workers=1,
                batch_telemetry_file=telemetry,
            )

            self.assertEqual(sorted(path.name for path in outputs.keys()), ["good.dds"])
            payload = json.loads(telemetry.read_text(encoding="utf-8"))
            summary = payload["summary"]
            self.assertEqual(summary["processed_files"], 2)
            self.assertEqual(summary["successful_files"], 1)
            self.assertEqual(summary["failed_files"], 1)
            self.assertEqual(len(payload["per_file_duration_seconds"]), 2)

    def test_resolve_batch_workers_throttles_for_8k_textures(self) -> None:
        self.assertEqual(_resolve_batch_workers(None, 10, max_megapixels=64.0), 1)
        self.assertEqual(_resolve_batch_workers(8, 10, max_megapixels=64.0), 1)
        self.assertEqual(_resolve_batch_workers(None, 10, max_megapixels=20.0), 2)

    def test_summarize_batch_texture_dimensions_counts_4k_and_8k(self) -> None:
        paths = [Path("/tmp/a.dds"), Path("/tmp/b.dds")]
        with mock.patch(
            "generate_textures._probe_image_dimensions",
            side_effect=[(4096, 2048), (8192, 4096)],
        ):
            summary = _summarize_batch_texture_dimensions(paths)
        self.assertEqual(int(summary["sampled_files"]), 2)
        self.assertEqual(int(summary["high_res_4k_count"]), 2)
        self.assertEqual(int(summary["high_res_8k_count"]), 1)
        self.assertEqual(int(summary["max_dimension"]), 8192)
        self.assertGreater(float(summary["max_megapixels"]), 30.0)

    def test_write_batch_failure_artifacts_writes_structured_json_and_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            failures = [
                (root / "textures" / "b.dds", "decode error"),
                (root / "textures" / "a.dds", "unsupported format"),
            ]
            outputs = {root / "textures" / "ok.dds": {"diffuse": root / "out" / "ok.dds"}}
            json_path, csv_path = write_batch_failure_artifacts(
                artifact_dir=root,
                failures=failures,
                batch_outputs=outputs,
            )
            self.assertTrue(json_path.exists())
            self.assertTrue(csv_path.exists())
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], APP_VERSION)
            self.assertEqual(payload["summary"]["failed_files"], 2)
            self.assertEqual(payload["summary"]["successful_files"], 1)
            self.assertEqual(payload["summary"]["successful_outputs"], 1)
            self.assertEqual([Path(row["file"]).name for row in payload["failures"]], ["a.dds", "b.dds"])
            csv_lines = csv_path.read_text(encoding="utf-8").splitlines()
            self.assertIn("file,error,action,conflict_code,conflict_action,outputs_generated", csv_lines[0])
            self.assertTrue(any("a.dds" in line for line in csv_lines[1:]))
            self.assertTrue(any("b.dds" in line for line in csv_lines[1:]))

    def test_main_writes_failure_artifacts_for_batch_directory_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "textures"
            input_dir.mkdir()
            good = input_dir / "good.dds"
            good.write_bytes(b"dds")
            args = mock.Mock(
                gui=False,
                input_file=input_dir,
                output_dir=None,
                diffuse_name=None,
                normal_name=None,
                parallax_name=None,
                glow_name=None,
                environment_mask_name=None,
                rmaos_name=None,
                complex_name=None,
                normal_strength=None,
                parallax_strength=None,
                glow_threshold=None,
                environment_mask_strength=None,
                rmaos_strength=None,
                complex_strength=None,
                specular_strength=None,
                complex_format="msn",
                environment_mask_mode="standard",
                emboss_mode=False,
                relief_mode=False,
                parallax_mode="standard",
                no_diffuse=False,
                no_normal=True,
                no_parallax=True,
                glow_map=False,
                environment_mask=False,
                rmaos=False,
                wetness_mask=False,
                wetness_mask_strength=None,
                snow_mask=False,
                snow_mask_strength=None,
                ao_map=False,
                ao_strength=None,
                roughness_map=False,
                roughness_strength=None,
                complex_material=False,
                pbr_material=False,
                render_profile="auto",
                batch_workers=1,
            )

            def _fake_run_batch_with_options(**kwargs):
                kwargs["error_callback"](1, 2, input_dir / "bad.dds", RuntimeError("decode error"))
                return {good: {"diffuse": root / "out" / "good.dds"}}

            with mock.patch("generate_textures.parse_args", return_value=args):
                with mock.patch("generate_textures.run_batch_with_options", side_effect=_fake_run_batch_with_options):
                    exit_code = main()

            self.assertEqual(exit_code, 1)
            json_report = input_dir / "batch_failure_report.json"
            csv_report = input_dir / "batch_failure_report.csv"
            self.assertTrue(json_report.exists())
            self.assertTrue(csv_report.exists())
            payload = json.loads(json_report.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["failed_files"], 1)
            self.assertEqual(Path(payload["failures"][0]["file"]).name, "bad.dds")

    def test_main_passes_checkpoint_options_to_batch_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "textures"
            input_dir.mkdir()
            checkpoint = root / "checkpoint.json"
            args = mock.Mock(
                gui=False,
                input_file=input_dir,
                output_dir=None,
                diffuse_name=None,
                normal_name=None,
                parallax_name=None,
                glow_name=None,
                environment_mask_name=None,
                rmaos_name=None,
                complex_name=None,
                normal_strength=None,
                parallax_strength=None,
                glow_threshold=None,
                environment_mask_strength=None,
                rmaos_strength=None,
                complex_strength=None,
                specular_strength=None,
                complex_format="msn",
                environment_mask_mode="standard",
                emboss_mode=False,
                relief_mode=False,
                parallax_mode="standard",
                no_diffuse=False,
                no_normal=True,
                no_parallax=True,
                glow_map=False,
                environment_mask=False,
                rmaos=False,
                wetness_mask=False,
                wetness_mask_strength=None,
                snow_mask=False,
                snow_mask_strength=None,
                ao_map=False,
                ao_strength=None,
                roughness_map=False,
                roughness_strength=None,
                complex_material=False,
                pbr_material=False,
                render_profile="auto",
                batch_workers=2,
                checkpoint_file=checkpoint,
                resume_checkpoint=True,
            )
            with mock.patch("generate_textures.parse_args", return_value=args):
                with mock.patch("generate_textures.run_batch_with_options", return_value={}) as batch_mock:
                    exit_code = main()
            self.assertEqual(exit_code, 0)
            self.assertEqual(batch_mock.call_args.kwargs["checkpoint_file"], checkpoint)
            self.assertTrue(batch_mock.call_args.kwargs["resume_from_checkpoint"])

    def test_create_panda_icon_image_returns_rgba_square(self) -> None:
        for size in (16, 32, 64, 128, 256):
            icon = _create_panda_icon_image(size=size)
            self.assertEqual(icon.mode, "RGBA")
            self.assertEqual(icon.size, (size, size))

    def test_create_panda_icon_image_is_not_fully_transparent(self) -> None:
        icon = _create_panda_icon_image(size=128)
        opaque_count = 0
        for x in range(icon.width):
            for y in range(icon.height):
                if icon.getpixel((x, y))[3] == 255:
                    opaque_count += 1
        self.assertGreater(opaque_count, 0)

    def test_create_panda_icon_image_uses_bundled_source_art(self) -> None:
        source_path = _resolve_app_icon_source_path()
        self.assertTrue(source_path.is_file(), source_path)
        with Image.open(source_path) as source:
            expected = source.convert("RGBA")
            if expected.width == expected.height:
                expected = expected.resize((64, 64), Image.Resampling.LANCZOS)
            else:
                fitted = ImageOps.contain(expected, (64, 64), method=Image.Resampling.LANCZOS)
                canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                canvas.paste(
                    fitted,
                    ((64 - fitted.width) // 2, (64 - fitted.height) // 2),
                    fitted if fitted.mode == "RGBA" else None,
                )
                expected = canvas
        icon = _create_panda_icon_image(size=64)
        self.assertEqual(icon.tobytes(), expected.tobytes())

    def test_patreon_url_is_correct(self) -> None:
        self.assertEqual(PATREON_URL, "https://www.patreon.com/cw/DeadOnTheInside")

    def test_modding_wiki_url_is_correct(self) -> None:
        self.assertEqual(MODDING_WIKI_SKYRIM_URL, "https://modding.wiki/en/skyrim")

    def test_parse_args_accepts_pbr_material_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = Path(temp_dir) / "brick.dds"
            input_file.write_bytes(b"dds")
            with mock.patch("sys.argv", ["generate_textures.py", str(input_file), "--pbr-material"]):
                args = parse_args()
        self.assertTrue(args.pbr_material)

    def test_parse_args_accepts_render_profile_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = Path(temp_dir) / "brick.dds"
            input_file.write_bytes(b"dds")
            with mock.patch("sys.argv", ["generate_textures.py", str(input_file), "--render-profile", "enb"]):
                args = parse_args()
        self.assertEqual(str(args.render_profile), "enb")

    def test_parse_args_accepts_extended_render_profile_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = Path(temp_dir) / "brick.dds"
            input_file.write_bytes(b"dds")
            with mock.patch("sys.argv", ["generate_textures.py", str(input_file), "--render-profile", "architecture"]):
                args = parse_args()
        self.assertEqual(str(args.render_profile), "architecture")

    def test_parse_args_accepts_checkpoint_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "textures"
            input_dir.mkdir()
            checkpoint = Path(temp_dir) / "checkpoint.json"
            with mock.patch(
                "sys.argv",
                [
                    "generate_textures.py",
                    str(input_dir),
                    "--checkpoint-file",
                    str(checkpoint),
                    "--resume-checkpoint",
                ],
            ):
                args = parse_args()
        self.assertEqual(args.checkpoint_file, checkpoint)
        self.assertTrue(args.resume_checkpoint)

    def test_parse_args_accepts_project_settings_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = Path(temp_dir) / "brick.dds"
            input_file.write_bytes(b"dds")
            import_path = Path(temp_dir) / "import.json"
            export_path = Path(temp_dir) / "export.json"
            with mock.patch(
                "sys.argv",
                [
                    "generate_textures.py",
                    str(input_file),
                    "--import-project-settings",
                    str(import_path),
                    "--export-project-settings",
                    str(export_path),
                ],
            ):
                args = parse_args()
        self.assertEqual(args.import_project_settings, import_path)
        self.assertEqual(args.export_project_settings, export_path)

    def test_parse_args_accepts_gui_backend_qt(self) -> None:
        with mock.patch("sys.argv", ["generate_textures.py", "--gui", "--gui-backend", "qt"]):
            args = parse_args()
        self.assertEqual(str(args.gui_backend), "qt")

    def test_main_routes_to_qt_backend_when_requested(self) -> None:
        args = mock.Mock(gui=True, input_file=None, gui_backend="qt")
        with mock.patch("generate_textures.parse_args", return_value=args):
            with mock.patch("generate_textures._launch_qt_migration_gui") as qt_launch:
                with mock.patch("generate_textures._launch_tk_gui") as tk_launch:
                    exit_code = main()
        self.assertEqual(exit_code, 0)
        qt_launch.assert_called_once()
        tk_launch.assert_not_called()

    def test_main_exports_project_settings_without_launching_gui(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_input = temp_path / "source.dds"
            source_input.write_bytes(b"fake")
            source_state = temp_path / "gui-state.json"
            export_state = temp_path / "exported-settings.json"
            save_gui_state(
                {
                    "input_path": str(source_input),
                    "dark_mode": True,
                    "show_advanced_workflow_outputs": True,
                    "render_profile": "community_shaders",
                },
                source_state,
            )
            args = SimpleNamespace(
                gui=False,
                input_file=None,
                gui_backend="auto",
                pbr_material=False,
                import_project_settings=None,
                export_project_settings=export_state,
            )
            with mock.patch("generate_textures.parse_args", return_value=args), \
                 mock.patch("generate_textures.GUI_STATE_FILE", source_state), \
                 mock.patch("generate_textures._launch_qt_migration_gui") as qt_launch, \
                 mock.patch("generate_textures._launch_tk_gui") as tk_launch:
                self.assertEqual(main(), 0)
            exported = load_gui_state(export_state)
            self.assertEqual(exported["input_path"], str(source_input))
            self.assertTrue(bool(exported["dark_mode"]))
            self.assertTrue(bool(exported["show_advanced_workflow_outputs"]))
            qt_launch.assert_not_called()
            tk_launch.assert_not_called()

    def test_main_auto_prefers_qt_when_tk_unavailable(self) -> None:
        args = mock.Mock(gui=True, input_file=None, gui_backend="auto")
        with mock.patch("generate_textures.parse_args", return_value=args):
            with mock.patch("generate_textures.GUI_AVAILABLE", False):
                with mock.patch("generate_textures.QT_GUI_AVAILABLE", True):
                    with mock.patch("generate_textures._launch_qt_migration_gui") as qt_launch:
                        exit_code = main()
        self.assertEqual(exit_code, 0)
        qt_launch.assert_called_once()

    def test_main_frozen_qt_executable_defaults_to_qt_on_double_click(self) -> None:
        args = SimpleNamespace(gui=False, input_file=None, gui_backend="auto", pbr_material=False)
        with mock.patch("generate_textures.parse_args", return_value=args), \
             mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "executable", "/packaged/generate_textures_qt.exe"), \
             mock.patch("generate_textures.GUI_AVAILABLE", True), \
             mock.patch("generate_textures.QT_GUI_AVAILABLE", True), \
             mock.patch("generate_textures._launch_qt_migration_gui") as qt_launch, \
             mock.patch("generate_textures._launch_tk_gui") as tk_launch:
            self.assertEqual(main(), 0)
        qt_launch.assert_called_once()
        tk_launch.assert_not_called()

    def test_main_frozen_qt_executable_honors_explicit_backend_overrides(self) -> None:
        for backend in ("tk", "qt"):
            with self.subTest(backend=backend):
                args = SimpleNamespace(gui=True, input_file=None, gui_backend=backend, pbr_material=False)
                with mock.patch("generate_textures.parse_args", return_value=args), \
                     mock.patch.object(sys, "frozen", True, create=True), \
                     mock.patch.object(sys, "executable", "/packaged/generate_textures_qt.exe"), \
                     mock.patch("generate_textures.GUI_AVAILABLE", True), \
                     mock.patch("generate_textures.QT_GUI_AVAILABLE", True), \
                     mock.patch("generate_textures._launch_qt_migration_gui") as qt_launch, \
                     mock.patch("generate_textures._launch_tk_gui") as tk_launch:
                    self.assertEqual(main(), 0)
                self.assertEqual(qt_launch.call_count, int(backend == "qt"))
                self.assertEqual(tk_launch.call_count, int(backend == "tk"))

    def test_main_normal_and_source_auto_backend_still_prefer_tk(self) -> None:
        for frozen, executable in (
            (True, "/packaged/generate_textures.exe"),
            (False, "/source/generate_textures_qt.exe"),
        ):
            with self.subTest(frozen=frozen, executable=executable):
                args = SimpleNamespace(gui=False, input_file=None, gui_backend="auto", pbr_material=False)
                with mock.patch("generate_textures.parse_args", return_value=args), \
                     mock.patch.object(sys, "frozen", frozen, create=True), \
                     mock.patch.object(sys, "executable", executable), \
                     mock.patch("generate_textures.GUI_AVAILABLE", True), \
                     mock.patch("generate_textures.QT_GUI_AVAILABLE", True), \
                     mock.patch("generate_textures._launch_qt_migration_gui") as qt_launch, \
                     mock.patch("generate_textures._launch_tk_gui") as tk_launch:
                    self.assertEqual(main(), 0)
                tk_launch.assert_called_once()
                qt_launch.assert_not_called()

    def test_main_pbr_material_forces_complex_material_cm_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = Path(temp_dir) / "brick.dds"
            input_file.write_bytes(b"dds")
            args = mock.Mock(
                gui=False,
                input_file=input_file,
                output_dir=None,
                diffuse_name=None,
                normal_name=None,
                parallax_name=None,
                glow_name=None,
                environment_mask_name=None,
                complex_name=None,
                normal_strength=None,
                parallax_strength=None,
                glow_threshold=None,
                environment_mask_strength=None,
                complex_strength=None,
                specular_strength=None,
                complex_format="msn",
                environment_mask_mode="complex",
                emboss_mode=False,
                relief_mode=False,
                parallax_mode="occlusion",
                no_diffuse=False,
                no_normal=False,
                no_parallax=False,
                glow_map=False,
                environment_mask=False,
                complex_material=False,
                pbr_material=True,
                render_profile="auto",
            )
            with mock.patch("generate_textures.parse_args", return_value=args):
                with mock.patch("generate_textures.run_with_options", return_value={}) as run_with_options_mock:
                    exit_code = main()
        self.assertEqual(exit_code, 0)
        self.assertTrue(args.complex_material)
        self.assertEqual(args.complex_format, "cm")
        self.assertEqual(args.environment_mask_mode, "standard")
        self.assertEqual(args.parallax_mode, "standard")
        run_with_options_mock.assert_called_once()
        self.assertTrue(bool(run_with_options_mock.call_args.kwargs["include_complex"]))
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["complex_format"]), "cm")
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["render_profile"]), "auto")

    def test_main_passes_selected_render_profile_to_run_with_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = Path(temp_dir) / "brick.dds"
            input_file.write_bytes(b"dds")
            args = mock.Mock(
                gui=False,
                input_file=input_file,
                output_dir=None,
                diffuse_name=None,
                normal_name=None,
                parallax_name=None,
                glow_name=None,
                environment_mask_name=None,
                rmaos_name=None,
                complex_name=None,
                normal_strength=None,
                parallax_strength=None,
                glow_threshold=None,
                environment_mask_strength=None,
                rmaos_strength=None,
                complex_strength=None,
                specular_strength=None,
                complex_format="msn",
                environment_mask_mode="standard",
                emboss_mode=False,
                relief_mode=False,
                parallax_mode="standard",
                no_diffuse=False,
                no_normal=False,
                no_parallax=False,
                glow_map=False,
                environment_mask=False,
                rmaos=False,
                complex_material=False,
                pbr_material=False,
                render_profile="enb",
            )
            with mock.patch("generate_textures.parse_args", return_value=args):
                with mock.patch("generate_textures.run_with_options", return_value={}) as run_with_options_mock:
                    exit_code = main()
        self.assertEqual(exit_code, 0)
        run_with_options_mock.assert_called_once()
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["render_profile"]), "enb")
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["complex_format"]), "msn")
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["env_mask_mode"]), "complex")
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["parallax_mode"]), "occlusion")

    def test_main_auto_render_profile_uses_mod_manager_context_from_input_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            instance_root = temp_path / "MO2"
            input_file = instance_root / "overwrite" / "textures" / "brick.dds"
            (instance_root / "mods").mkdir(parents=True)
            profile_dir = instance_root / "profiles" / "Default"
            profile_dir.mkdir(parents=True)
            input_file.parent.mkdir(parents=True, exist_ok=True)
            input_file.write_bytes(b"dds")
            (profile_dir / "modlist.txt").write_text("+Community Shaders\n", encoding="utf-8")
            (profile_dir / "plugins.txt").write_text("*Skyrim.esm\n", encoding="utf-8")
            (profile_dir / "loadorder.txt").write_text("Skyrim.esm\n", encoding="utf-8")
            args = mock.Mock(
                gui=False,
                input_file=input_file,
                output_dir=None,
                diffuse_name=None,
                normal_name=None,
                parallax_name=None,
                glow_name=None,
                environment_mask_name=None,
                rmaos_name=None,
                complex_name=None,
                normal_strength=None,
                parallax_strength=None,
                glow_threshold=None,
                environment_mask_strength=None,
                rmaos_strength=None,
                complex_strength=None,
                specular_strength=None,
                complex_format="msn",
                environment_mask_mode="standard",
                emboss_mode=False,
                relief_mode=False,
                parallax_mode="standard",
                no_diffuse=False,
                no_normal=False,
                no_parallax=False,
                glow_map=False,
                environment_mask=False,
                rmaos=False,
                complex_material=False,
                pbr_material=False,
                render_profile="auto",
            )
            stderr = io.StringIO()
            with mock.patch("generate_textures.parse_args", return_value=args), \
                 mock.patch("generate_textures.run_with_options", return_value={}) as run_with_options_mock, \
                 mock.patch("sys.stderr", stderr):
                exit_code = main()
        self.assertEqual(exit_code, 0)
        self.assertIn("Detected Mod (Hint): detected Mod Organizer 2 context", stderr.getvalue())
        self.assertIn("advisory only", stderr.getvalue())
        run_with_options_mock.assert_called_once()
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["render_profile"]), "auto")
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["complex_format"]), "cm")
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["env_mask_mode"]), "standard")

    def test_main_auto_render_profile_keeps_vanilla_defaults_without_mod_manager_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_file = temp_path / "brick.dds"
            input_file.write_bytes(b"dds")
            args = mock.Mock(
                gui=False,
                input_file=input_file,
                output_dir=None,
                diffuse_name=None,
                normal_name=None,
                parallax_name=None,
                glow_name=None,
                environment_mask_name=None,
                rmaos_name=None,
                complex_name=None,
                normal_strength=None,
                parallax_strength=None,
                glow_threshold=None,
                environment_mask_strength=None,
                rmaos_strength=None,
                complex_strength=None,
                specular_strength=None,
                complex_format="msn",
                environment_mask_mode="standard",
                emboss_mode=False,
                relief_mode=False,
                parallax_mode="standard",
                no_diffuse=False,
                no_normal=False,
                no_parallax=False,
                glow_map=False,
                environment_mask=False,
                rmaos=False,
                complex_material=False,
                pbr_material=False,
                render_profile="auto",
            )
            stderr = io.StringIO()
            with mock.patch("generate_textures.parse_args", return_value=args), \
                 mock.patch("generate_textures.detect_mod_manager_context", return_value=ModManagerContext()), \
                 mock.patch("generate_textures.run_with_options", return_value={}) as run_with_options_mock, \
                 mock.patch("sys.stderr", stderr):
                exit_code = main()
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        run_with_options_mock.assert_called_once()
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["render_profile"]), "auto")
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["complex_format"]), "msn")

    def test_run_cli_handles_missing_gui_dependencies_without_traceback(self) -> None:
        with mock.patch("generate_textures.main", side_effect=RuntimeError("GUI dependencies are unavailable in this environment.")):
            with mock.patch("sys.stderr") as mock_stderr:
                exit_code = _run_cli()
        self.assertEqual(exit_code, 1)
        self.assertIn("GUI dependencies are unavailable", "".join(call.args[0] for call in mock_stderr.write.call_args_list))

    def test_run_cli_handles_generic_runtime_error(self) -> None:
        with mock.patch("generate_textures.main", side_effect=RuntimeError("boom")):
            with mock.patch("sys.stderr") as mock_stderr:
                exit_code = _run_cli()
        self.assertEqual(exit_code, 1)
        self.assertIn("Error: boom", "".join(call.args[0] for call in mock_stderr.write.call_args_list))

    def test_run_cli_reports_headless_gui_startup_failure(self) -> None:
        class FakeTclError(Exception):
            pass

        args = mock.Mock(gui=True, input_file=None)
        with mock.patch("generate_textures.parse_args", return_value=args):
            with mock.patch("generate_textures.GUI_AVAILABLE", True):
                with mock.patch("generate_textures.tk", mock.Mock(TclError=FakeTclError)):
                    with mock.patch("generate_textures.TextureGeneratorGUI", side_effect=FakeTclError("no display")):
                        with mock.patch("sys.stderr") as mock_stderr:
                            exit_code = _run_cli()
        self.assertEqual(exit_code, 1)
        self.assertIn("no desktop display", "".join(call.args[0] for call in mock_stderr.write.call_args_list))

    def _build_fake_gui_for_queue_smoke(
        self,
        *,
        show_batch_preview: bool = False,
        is_processing: bool = True,
    ) -> object:
        class _Var:
            def __init__(self, value=None) -> None:
                self._value = value

            def get(self):
                return self._value

            def set(self, value) -> None:
                self._value = value

        class _Root:
            def __init__(self) -> None:
                self.after_calls: list[tuple[int, object]] = []

            def after(self, delay: int, callback) -> None:
                self.after_calls.append((delay, callback))

        fake_gui = mock.Mock(spec=TextureGeneratorGUI)
        fake_gui.is_processing = is_processing
        fake_gui.processing_queue = queue.Queue()
        fake_gui.status_var = _Var("")
        fake_gui.batch_perf_hint_var = _Var("")
        fake_gui.show_batch_preview_var = _Var(show_batch_preview)
        fake_gui.batch_nif_patch_results = []
        fake_gui.batch_failures = []
        fake_gui.root = _Root()
        fake_gui._set_preview_source_by_path = mock.Mock()
        fake_gui._set_processing_state = mock.Mock(
            side_effect=lambda active: setattr(fake_gui, "is_processing", bool(active))
        )
        fake_gui._refresh_preview = mock.Mock()
        fake_gui._tr = lambda text: text
        return fake_gui

    def test_gui_processing_queue_smoke_done_event_reports_outputs_failures_and_autopatch(self) -> None:
        if not hasattr(TextureGeneratorGUI, "_poll_processing_queue"):
            self.skipTest("GUI queue polling is unavailable in this environment.")
        fake_gui = self._build_fake_gui_for_queue_smoke(show_batch_preview=False)
        fake_gui.batch_nif_patch_results = [("stone.dds", 2, 1)]
        fake_gui.batch_failures = [("broken.dds", "decode error")]
        fake_gui.processing_queue.put((
            "done",
            (
                {Path("stone.dds"): {"diffuse": Path("stone_out.dds")}},
                {
                    "folder_summary_lines": [
                        "Folder summary: 1 source texture(s) across 1 top-level folder(s).",
                        "Top folders: (root) (1).",
                    ],
                    "elapsed_seconds": 1.2,
                    "avg_file_seconds": 1.2,
                    "max_file_seconds": 1.2,
                    "high_res_4k_count": 0,
                    "high_res_8k_count": 0,
                    "max_source_dimension": 0,
                    "max_source_megapixels": 0.0,
                    "resumed_completed_count": 0,
                    "checkpoint_path": "",
                },
            ),
        ))

        with mock.patch("generate_textures.messagebox.showinfo") as showinfo:
            TextureGeneratorGUI._poll_processing_queue(fake_gui)

        self.assertFalse(fake_gui.is_processing)
        self.assertIn("processed 1/1, generated 1", fake_gui.status_var.get())
        self.assertIn("failed 1", fake_gui.status_var.get())
        showinfo.assert_called_once()
        shown_message = showinfo.call_args.args[1]
        self.assertIn("Automatic NIF patching: 2 succeeded, 1 failed.", shown_message)
        self.assertIn("broken.dds: decode error", shown_message)
        self.assertIn("Folder summary: 1 source texture(s) across 1 top-level folder(s).", shown_message)
        fake_gui._refresh_preview.assert_called_once()

    def test_batch_source_helpers_report_relative_labels_and_folder_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            architecture = root / "architecture"
            clutter = root / "clutter"
            architecture.mkdir()
            clutter.mkdir()
            source_paths = [
                architecture / "stone.dds",
                architecture / "trim.dds",
                clutter / "barrel.dds",
                root / "root_file.dds",
            ]
            for path in source_paths:
                path.write_text("x", encoding="utf-8")

            self.assertEqual(
                Path(_batch_source_display_label(root, source_paths[0])),
                Path("architecture") / "stone.dds",
            )

            summary_lines = _summarize_batch_source_folders(source_paths, root)
            self.assertEqual(
                summary_lines[0],
                "Folder summary: 4 source texture(s) across 3 top-level folder(s).",
            )
            self.assertIn("architecture (2)", summary_lines[1])
            self.assertIn("clutter (1)", summary_lines[1])
            self.assertIn("(root) (1)", summary_lines[1])

    def test_gui_processing_queue_progress_and_failure_labels_prefer_relative_paths(self) -> None:
        if not hasattr(TextureGeneratorGUI, "_poll_processing_queue"):
            self.skipTest("GUI queue polling is unavailable in this environment.")
        fake_gui = self._build_fake_gui_for_queue_smoke(show_batch_preview=False)
        fake_gui.processing_queue.put(
            ("file_error", (2, 3, "broken.dds", "decode error", "textures/architecture/broken.dds"))
        )

        TextureGeneratorGUI._poll_processing_queue(fake_gui)

        self.assertEqual(
            fake_gui.batch_failures,
            [("textures/architecture/broken.dds", "decode error")],
        )
        self.assertIn("Skipped failed file 2/3: textures/architecture/broken.dds", fake_gui.status_var.get())
        self.assertEqual(fake_gui.root.after_calls[0][0], 100)

    def test_gui_processing_queue_smoke_progress_event_updates_status_and_preview(self) -> None:
        if not hasattr(TextureGeneratorGUI, "_poll_processing_queue"):
            self.skipTest("GUI queue polling is unavailable in this environment.")
        fake_gui = self._build_fake_gui_for_queue_smoke(show_batch_preview=True)
        current = Path("textures/stone.dds")
        fake_gui.processing_queue.put(("progress", (1, 3, current, 0.5, 1.0, "textures/stone.dds")))

        TextureGeneratorGUI._poll_processing_queue(fake_gui)

        self.assertIn("Processing 1/3: textures/stone.dds", fake_gui.status_var.get())
        fake_gui._set_preview_source_by_path.assert_called_once_with(current)
        self.assertEqual(fake_gui.root.after_calls[0][0], 100)

    def test_gui_processing_queue_reports_recovery_after_high_backlog_drains(self) -> None:
        if not hasattr(TextureGeneratorGUI, "_poll_processing_queue"):
            self.skipTest("GUI queue polling is unavailable in this environment.")
        fake_gui = self._build_fake_gui_for_queue_smoke(show_batch_preview=False, is_processing=True)
        fake_gui._queue_high_backlog_seen = True
        fake_gui.processing_queue.put(("progress", (10, 100, Path("test.dds"))))

        TextureGeneratorGUI._poll_processing_queue(fake_gui)

        self.assertIn("Queue recovered to normal", fake_gui.batch_perf_hint_var.get())
        self.assertFalse(fake_gui._queue_high_backlog_seen)
        self.assertEqual(fake_gui.root.after_calls[0][0], 100)

    def test_gui_processing_queue_smoke_nif_patch_event_updates_status_and_counts(self) -> None:
        if not hasattr(TextureGeneratorGUI, "_poll_processing_queue"):
            self.skipTest("GUI queue polling is unavailable in this environment.")
        fake_gui = self._build_fake_gui_for_queue_smoke(show_batch_preview=False)
        fake_gui.processing_queue.put(("nif_patch", ("stone.dds", 3, 1)))

        TextureGeneratorGUI._poll_processing_queue(fake_gui)

        self.assertEqual(fake_gui.batch_nif_patch_results, [("stone.dds", 3, 1)])
        self.assertIn("Patched related NIFs for stone.dds: 3 succeeded, 1 failed.", fake_gui.status_var.get())
        self.assertEqual(fake_gui.root.after_calls[0][0], 100)

    def test_gui_processing_queue_smoke_file_error_event_tracks_failure(self) -> None:
        if not hasattr(TextureGeneratorGUI, "_poll_processing_queue"):
            self.skipTest("GUI queue polling is unavailable in this environment.")
        fake_gui = self._build_fake_gui_for_queue_smoke(show_batch_preview=False)
        fake_gui.processing_queue.put(("file_error", (2, 4, "broken.dds", "decode error")))

        TextureGeneratorGUI._poll_processing_queue(fake_gui)

        self.assertEqual(fake_gui.batch_failures, [("broken.dds", "decode error")])
        self.assertIn("Skipped failed file 2/4: broken.dds", fake_gui.status_var.get())
        self.assertEqual(fake_gui.root.after_calls[0][0], 100)

    def test_gui_processing_queue_smoke_cancelled_event_stops_polling(self) -> None:
        if not hasattr(TextureGeneratorGUI, "_poll_processing_queue"):
            self.skipTest("GUI queue polling is unavailable in this environment.")
        fake_gui = self._build_fake_gui_for_queue_smoke(show_batch_preview=False, is_processing=True)
        fake_gui.processing_queue.put(("cancelled", {Path("stone.dds"): {"diffuse": Path("stone_out.dds")}}))

        with mock.patch("generate_textures.messagebox.showinfo") as showinfo:
            TextureGeneratorGUI._poll_processing_queue(fake_gui)

        fake_gui._set_processing_state.assert_called_once_with(False)
        showinfo.assert_called_once()
        self.assertFalse(fake_gui.root.after_calls)

    def test_gui_processing_queue_smoke_error_event_stops_polling_and_shows_error(self) -> None:
        if not hasattr(TextureGeneratorGUI, "_poll_processing_queue"):
            self.skipTest("GUI queue polling is unavailable in this environment.")
        fake_gui = self._build_fake_gui_for_queue_smoke(show_batch_preview=False, is_processing=True)
        fake_gui.processing_queue.put(("error", "boom"))

        with mock.patch("generate_textures.messagebox.showerror") as showerror:
            TextureGeneratorGUI._poll_processing_queue(fake_gui)

        fake_gui._set_processing_state.assert_called_once_with(False)
        showerror.assert_called_once()
        self.assertFalse(fake_gui.root.after_calls)

    def test_gui_processing_queue_smoke_terminal_done_event_short_circuits_remaining_events(self) -> None:
        if not hasattr(TextureGeneratorGUI, "_poll_processing_queue"):
            self.skipTest("GUI queue polling is unavailable in this environment.")
        fake_gui = self._build_fake_gui_for_queue_smoke(show_batch_preview=False, is_processing=True)
        fake_gui.processing_queue.put(("done", {Path("stone.dds"): {"diffuse": Path("stone_out.dds")}}))
        fake_gui.processing_queue.put(("error", "should-not-run"))

        with mock.patch("generate_textures.messagebox.showinfo") as showinfo:
            with mock.patch("generate_textures.messagebox.showerror") as showerror:
                TextureGeneratorGUI._poll_processing_queue(fake_gui)

        showinfo.assert_called_once()
        showerror.assert_not_called()
        self.assertEqual(fake_gui.processing_queue.qsize(), 1)
        self.assertFalse(fake_gui.root.after_calls)

    def test_gui_processing_queue_smoke_terminal_error_event_short_circuits_remaining_events(self) -> None:
        if not hasattr(TextureGeneratorGUI, "_poll_processing_queue"):
            self.skipTest("GUI queue polling is unavailable in this environment.")
        fake_gui = self._build_fake_gui_for_queue_smoke(show_batch_preview=False, is_processing=True)
        fake_gui.processing_queue.put(("error", "boom"))
        fake_gui.processing_queue.put(("progress", (1, 1, Path("textures/stone.dds"))))

        with mock.patch("generate_textures.messagebox.showerror") as showerror:
            TextureGeneratorGUI._poll_processing_queue(fake_gui)

        showerror.assert_called_once()
        fake_gui._set_preview_source_by_path.assert_not_called()
        self.assertEqual(fake_gui.processing_queue.qsize(), 1)
        self.assertFalse(fake_gui.root.after_calls)

    def test_revert_last_generation_removes_created_and_restores_backups(self) -> None:
        if not hasattr(TextureGeneratorGUI, "_revert_last_generation"):
            self.skipTest("GUI revert workflow is unavailable in this environment.")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            created = root / "created.dds"
            created.write_text("new", encoding="utf-8")
            original = root / "original.dds"
            original.write_text("modified", encoding="utf-8")
            backup_dir = root / "backup"
            backup_dir.mkdir()
            backup_copy = backup_dir / "original.dds"
            backup_copy.write_text("original", encoding="utf-8")

            class _Button:
                def __init__(self) -> None:
                    self.configure = mock.Mock()

            class _FakeGui:
                def __init__(self) -> None:
                    self.is_processing = False
                    self.revert_button = _Button()
                    self.last_generation_backup_dir = backup_dir
                    self.last_generation_backups = {original: backup_copy}
                    self.last_generation_created_files = {created}
                    self.status_var = mock.Mock()

                def _has_revert_snapshot(self) -> bool:
                    return bool(self.last_generation_backups or self.last_generation_created_files)

                def _discard_last_generation_snapshot(self) -> None:
                    TextureGeneratorGUI._discard_last_generation_snapshot(self)

                def _set_processing_state(self, _processing: bool) -> None:
                    return None

                def _refresh_preview(self) -> None:
                    return None

            fake_gui = _FakeGui()
            with mock.patch("generate_textures.messagebox.askyesno", return_value=True):
                with mock.patch("generate_textures.messagebox.showinfo") as showinfo:
                    with mock.patch("generate_textures.messagebox.showerror") as showerror:
                        TextureGeneratorGUI._revert_last_generation(fake_gui)
            self.assertFalse(created.exists())
            self.assertEqual(original.read_text(encoding="utf-8"), "original")
            self.assertFalse(fake_gui.last_generation_backups)
            self.assertFalse(fake_gui.last_generation_created_files)
            self.assertIsNone(fake_gui.last_generation_backup_dir)
            showinfo.assert_called_once()
            showerror.assert_not_called()

    def test_revert_last_generation_reports_partial_failures_but_restores_remaining_files(self) -> None:
        if not hasattr(TextureGeneratorGUI, "_revert_last_generation"):
            self.skipTest("GUI revert workflow is unavailable in this environment.")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            keep_created = root / "created_keep.dds"
            fail_created = root / "created_fail.dds"
            keep_created.write_text("new-a", encoding="utf-8")
            fail_created.write_text("new-b", encoding="utf-8")
            original = root / "original.dds"
            original.write_text("modified", encoding="utf-8")
            backup_dir = root / "backup"
            backup_dir.mkdir()
            backup_copy = backup_dir / "original.dds"
            backup_copy.write_text("original", encoding="utf-8")

            class _Button:
                def __init__(self) -> None:
                    self.configure = mock.Mock()

            class _FakeGui:
                def __init__(self) -> None:
                    self.is_processing = False
                    self.revert_button = _Button()
                    self.last_generation_backup_dir = backup_dir
                    self.last_generation_backups = {original: backup_copy}
                    self.last_generation_created_files = {keep_created, fail_created}
                    self.status_var = mock.Mock()

                def _has_revert_snapshot(self) -> bool:
                    return bool(self.last_generation_backups or self.last_generation_created_files)

                def _discard_last_generation_snapshot(self) -> None:
                    TextureGeneratorGUI._discard_last_generation_snapshot(self)

                def _set_processing_state(self, _processing: bool) -> None:
                    return None

                def _refresh_preview(self) -> None:
                    return None

            fake_gui = _FakeGui()
            original_unlink = Path.unlink

            def _unlink_with_failure(path: Path, *args, **kwargs):
                if path == fail_created:
                    raise PermissionError("locked")
                return original_unlink(path, *args, **kwargs)

            with mock.patch("pathlib.Path.unlink", autospec=True, side_effect=_unlink_with_failure):
                with mock.patch("generate_textures.messagebox.askyesno", return_value=True):
                    with mock.patch("generate_textures.messagebox.showinfo") as showinfo:
                        with mock.patch("generate_textures.messagebox.showerror") as showerror:
                            TextureGeneratorGUI._revert_last_generation(fake_gui)
            self.assertFalse(keep_created.exists())
            self.assertTrue(fail_created.exists())
            self.assertEqual(original.read_text(encoding="utf-8"), "original")
            self.assertFalse(fake_gui.last_generation_backups)
            self.assertFalse(fake_gui.last_generation_created_files)
            self.assertIsNone(fake_gui.last_generation_backup_dir)
            showinfo.assert_not_called()
            showerror.assert_called_once()

    def test_generate_normal_raises_clear_error_for_buffer_size_mismatch(self) -> None:
        with mock.patch("generate_textures.np.frombuffer", return_value=np.zeros(1, dtype=np.uint8)):
            with self.assertRaisesRegex(RuntimeError, "Normal map red buffer size mismatch"):
                generate_normal(_detailed_bright_image())

    def test_generate_specular_raises_clear_error_for_buffer_size_mismatch(self) -> None:
        with mock.patch("generate_textures.np.frombuffer", return_value=np.zeros(1, dtype=np.uint8)):
            with self.assertRaisesRegex(RuntimeError, "Specular buffer size mismatch"):
                generate_specular(_detailed_bright_image())

    def test_to_dds_compatible_image_uses_rgb_for_dxt1(self) -> None:
        converted = _to_dds_compatible_image(Image.new("L", (4, 4), color=128), pixel_format="DXT1")
        self.assertEqual(converted.mode, "RGB")

    def test_to_dds_compatible_image_uses_rgba_for_dxt5(self) -> None:
        converted = _to_dds_compatible_image(Image.new("L", (4, 4), color=128), pixel_format="DXT5")
        self.assertEqual(converted.mode, "RGBA")

    def test_save_with_dds_fallback_returns_dds_path_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "stone.dds"

            def fake_save(_self, fp, format=None, **_kwargs):
                Path(fp).write_bytes(f"{format}".encode("utf-8"))

            with mock.patch.object(Image.Image, "save", autospec=True, side_effect=fake_save):
                saved_path = _save_with_dds_fallback(_sample_image(), target)

        self.assertEqual(saved_path, target)

    def test_save_with_dds_fallback_uses_rgb_for_dxt1_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "stone.dds"

            def fake_save(image_self, fp, format=None, **kwargs):
                self.assertEqual(format, "DDS")
                self.assertEqual(kwargs.get("pixel_format"), "DXT1")
                self.assertEqual(image_self.mode, "RGB")
                Path(fp).write_bytes(b"DDS")

            with mock.patch.object(Image.Image, "save", autospec=True, side_effect=fake_save):
                saved_path = _save_with_dds_fallback(
                    Image.new("L", (8, 8), color=120),
                    target,
                    preferred_pixel_formats=("DXT1",),
                )

        self.assertEqual(saved_path, target)

    def test_save_with_dds_fallback_returns_png_path_when_dds_save_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "stone.dds"

            def fake_save(_self, fp, format=None, **_kwargs):
                if format == "DDS":
                    raise OSError("dds unavailable")
                Path(fp).write_bytes(f"{format}".encode("utf-8"))

            with mock.patch.object(Image.Image, "save", autospec=True, side_effect=fake_save):
                saved_path = _save_with_dds_fallback(_sample_image(), target)

        self.assertEqual(saved_path, target.with_suffix(".png"))

    def test_save_with_dds_fallback_normalizes_and_deduplicates_preferred_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "stone.dds"
            attempted_formats: list[str] = []

            def fake_save(_self, fp, format=None, **kwargs):
                if format == "DDS":
                    pixel_format = str(kwargs.get("pixel_format"))
                    attempted_formats.append(pixel_format)
                    if pixel_format == "DXT1":
                        raise OSError("dxt1 unavailable")
                Path(fp).write_bytes(f"{format}".encode("utf-8"))

            with mock.patch.object(Image.Image, "save", autospec=True, side_effect=fake_save):
                saved_path = _save_with_dds_fallback(
                    _sample_image(),
                    target,
                    preferred_pixel_formats=(" dxt1 ", "DXT1", "dXt5"),
                )

        self.assertEqual(saved_path, target)
        self.assertEqual(attempted_formats, ["DXT1", "DXT5"])


    def test_generate_specular_no_black_holes_on_detailed_image(self) -> None:
        specular = generate_specular(_large_high_detail_image(), strength=2.0)
        self.assertEqual(specular.mode, "L")
        minimum, _ = specular.getextrema()
        self.assertGreaterEqual(minimum, 8)

    def test_generate_environment_mask_standard_mode_returns_l_same_size(self) -> None:
        env_mask = generate_environment_mask(_sample_image(), mode="standard")
        self.assertEqual(env_mask.mode, "L")
        self.assertEqual(env_mask.size, (8, 8))

    def test_generate_environment_mask_standard_mode_stays_above_floor(self) -> None:
        env_mask = generate_environment_mask(_flat_dark_image(), strength=2.2, mode="standard")
        minimum, _ = env_mask.getextrema()
        self.assertGreaterEqual(minimum, 8)

    def test_identify_skyrim_texture_role_normal_map(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/architecture/stonewall_n.dds"))
        self.assertEqual(result["role"], "normal")
        self.assertEqual(result["suffix"], "_n")
        self.assertIn("DirectX", result["notes"])

    def test_identify_skyrim_texture_role_parallax(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/landscape/dirt_p.dds"))
        self.assertEqual(result["role"], "parallax")
        self.assertEqual(result["suffix"], "_p")

    def test_identify_skyrim_texture_role_em_is_glow(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/effects/fire_em.dds"))
        self.assertEqual(result["role"], "glow")
        self.assertEqual(result["suffix"], "_em")
        self.assertIn("Slot 2", result["notes"])

    def test_identify_skyrim_texture_role_emis_is_glow_alias(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/effects/fire_emis.dds"))
        self.assertEqual(result["role"], "glow")
        self.assertEqual(result["suffix"], "_emis")
        self.assertIn("Slot 2", result["notes"])

    def test_identify_skyrim_texture_role_environment_mask(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_m.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_m")
        self.assertIn("Slot 5", result["notes"])

    def test_identify_skyrim_texture_role_rmaos_environment_mask(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_rmaos.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_rmaos")

    def test_identify_skyrim_texture_role_specular_alias_maps_to_environment_mask(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_specular.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_specular")
        self.assertIn("alias", result["description"].lower())

    def test_identify_skyrim_texture_role_roughness_alias_maps_to_environment_mask(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_roughness.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_roughness")
        self.assertIn("alias", result["description"].lower())

    def test_identify_skyrim_texture_role_ao_alias_maps_to_environment_mask(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_ao.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_ao")
        self.assertIn("alias", result["description"].lower())

    def test_identify_skyrim_texture_role_orm_alias_maps_to_rmaos_environment_mask(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_orm.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_orm")
        self.assertIn("alias", result["description"].lower())

    def test_identify_skyrim_texture_role_c_as_complex_material(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/architecture/brick_c.dds"))
        self.assertEqual(result["role"], "complex_material_cm")
        self.assertEqual(result["suffix"], "_c")

    def test_identify_skyrim_texture_role_diffuse_fallback(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/clutter/candle.dds"))
        self.assertEqual(result["role"], "diffuse")
        self.assertEqual(result["suffix"], "")

    def test_identify_skyrim_texture_role_path_hint(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/landscape/grass01.dds"))
        self.assertIn("landscape", result["hint"].lower())

    def test_identify_skyrim_texture_role_msn_is_enb_only(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/architecture/brick_msn.dds"))
        self.assertEqual(result["role"], "complex_material")
        self.assertIn("ENBSeries", result["notes"])

    def test_identify_skyrim_texture_role_rmaos_has_channel_notes(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_rmaos.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_rmaos")
        # Notes must describe the TruePBR RMAOS channel layout.
        self.assertIn("Roughness", result["notes"])
        self.assertIn("Metallic", result["notes"])
        self.assertIn("Ambient Occlusion", result["notes"])
        self.assertIn("TruePBR", result["notes"])

    def test_identify_skyrim_texture_role_cm_has_channel_notes(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/architecture/brick_cm.dds"))
        self.assertEqual(result["role"], "complex_material_cm")
        self.assertEqual(result["suffix"], "_cm")
        self.assertIn("Environment reflection amount", result["notes"])
        self.assertIn("Glossiness", result["notes"])
        self.assertIn("Metallic", result["notes"])
        self.assertIn("Extended Materials", result["notes"])

    def test_identify_skyrim_texture_role_c_has_channel_notes(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/architecture/brick_c.dds"))
        self.assertEqual(result["role"], "complex_material_cm")
        self.assertEqual(result["suffix"], "_c")
        self.assertIn("Environment reflection amount", result["notes"])
        self.assertIn("Glossiness", result["notes"])
        self.assertIn("Metallic", result["notes"])

    def test_identify_skyrim_texture_role_uppercase_C_is_complex_material(self) -> None:
        # _C.dds (uppercase) must be treated identically to _c.dds — stem is lowercased before lookup
        result = identify_skyrim_texture_role(Path("textures/architecture/brick_C.dds"))
        self.assertEqual(result["role"], "complex_material_cm")
        self.assertEqual(result["suffix"], "_c")

    def test_identify_skyrim_texture_role_infers_from_filename_tokens(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/architecture/wall_normalmap.dds"))
        self.assertEqual(result["role"], "normal")
        self.assertEqual(result["suffix"], "")
        self.assertIn("inferred", result["description"].lower())

    def test_run_with_options_env_mask_standard_mode_produces_l_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            outputs = run_with_options(
                input_file=input_path,
                output_dir=output_dir,
                include_diffuse=False,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=True,
                include_complex=False,
                env_mask_mode="standard",
            )

            self.assertIn("environment_mask", outputs)
            with Image.open(outputs["environment_mask"]) as generated:
                generated.load()
                # Standard mode must produce a greyscale-compatible output (L or RGBA
                # that decodes to a single channel — pillow DDS may store as RGBA).
                self.assertIn(generated.mode, ("L", "RGBA", "RGB"))

    def test_run_with_options_standard_env_mask_prefers_dxt1_then_dxt5(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=True,
                    include_complex=False,
                    env_mask_mode="standard",
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.kwargs["preferred_pixel_formats"], ("DXT1", "DXT5"))

    def test_run_with_options_normal_prefers_bc5_then_legacy_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=True,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.kwargs["preferred_pixel_formats"], ("BC5", "DXT5", "DXT3"))

    def test_run_with_options_parallax_prefers_dxt1_then_dxt5(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=True,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.kwargs["preferred_pixel_formats"], ("DXT1", "DXT5"))

    def test_preferred_dds_formats_for_opaque_diffuse_uses_bc7_then_dxt1(self) -> None:
        formats = _preferred_dds_formats_for_output("diffuse", Image.new("RGB", (4, 4), color=(10, 20, 30)))
        self.assertEqual(formats, ("BC7", "DXT1", "DXT5"))

    def test_preferred_dds_formats_for_alpha_diffuse_avoids_dxt1(self) -> None:
        image = Image.new("RGBA", (4, 4), color=(10, 20, 30, 255))
        image.putpixel((0, 0), (10, 20, 30, 80))
        formats = _preferred_dds_formats_for_output("diffuse", image)
        self.assertEqual(formats, ("BC7", "DXT5", "DXT3"))

    def test_run_with_options_complex_env_mask_uses_dxt5_and_defaults_to_m_for_enb_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=True,
                    include_complex=True,
                    env_mask_mode="complex",
                    complex_format="msn",
                    render_profile="enb",
                )

            self.assertEqual(save_mock.call_count, 2)
            env_calls = [call for call in save_mock.call_args_list if call.args[1].name.endswith("_m.dds")]
            self.assertEqual(len(env_calls), 1)
            self.assertEqual(env_calls[0].kwargs["preferred_pixel_formats"], ("BC7", "DXT5", "DXT3"))

    def test_run_with_options_truepbr_complex_env_mask_defaults_to_m_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=True,
                    include_complex=False,
                    env_mask_mode="complex",
                    complex_format="cm",
                    render_profile="truepbr",
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.kwargs["preferred_pixel_formats"], ("BC7", "DXT5", "DXT3"))
            self.assertEqual(save_mock.call_args.args[1].name, "brick_m.dds")

    def test_run_with_options_rmaos_writes_rmaos_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_rmaos=True,
                    include_complex=False,
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.kwargs["preferred_pixel_formats"], ("BC7", "DXT5", "DXT3"))
            self.assertEqual(save_mock.call_args.args[1].name, "brick_rmaos.dds")

    def test_run_with_options_renderer_output_matrix_has_exact_keys_and_paths(self) -> None:
        cases = (
            (
                "vanilla-core",
                {
                    "include_diffuse": True,
                    "include_normal": True,
                    "include_parallax": True,
                    "render_profile": "vanilla",
                },
                {
                    "diffuse": "brick.dds",
                    "normal": "brick_n.dds",
                    "parallax": "brick_p.dds",
                },
            ),
            (
                "enb-complex",
                {
                    "include_diffuse": False,
                    "include_normal": False,
                    "include_parallax": False,
                    "include_environment_mask": True,
                    "include_complex": True,
                    "env_mask_mode": "complex",
                    "complex_format": "msn",
                    "render_profile": "enb",
                },
                {
                    "environment_mask": "brick_m.dds",
                    "complex_material": "brick_msn.dds",
                },
            ),
            (
                "truepbr-plus-core-and-ao",
                {
                    "include_diffuse": True,
                    "include_normal": True,
                    "include_parallax": False,
                    "include_glow": True,
                    "include_rmaos": True,
                    "include_ao": True,
                    "render_profile": "truepbr",
                },
                {
                    "diffuse": "brick.dds",
                    "normal": "brick_n.dds",
                    "glow": "brick_g.dds",
                    "rmaos": "brick_rmaos.dds",
                    "ao": "brick_ao.dds",
                    "rmaos_json": "brick_rmaos.json",
                },
            ),
            (
                "optional-feature-maps",
                {
                    "include_diffuse": False,
                    "include_normal": False,
                    "include_parallax": False,
                    "include_wetness_mask": True,
                    "include_snow_mask": True,
                    "include_ao": True,
                    "include_roughness": True,
                },
                {
                    "wetness_mask": "brick_wt.dds",
                    "snow_mask": "brick_sm.dds",
                    "ao": "brick_ao.dds",
                    "roughness": "brick_rough.dds",
                },
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            for source_size in ((1, 1), (4, 4), (16, 8), (17, 9), (32, 32)):
                Image.new("RGB", source_size, color=(60, 100, 140)).save(input_path)
                for case_name, options, expected_names in cases:
                    saved_image_sizes: dict[str, tuple[int, int]] = {}

                    def record_saved_image(image: Image.Image, path: Path, **_kwargs: object) -> Path:
                        saved_image_sizes[path.name] = image.size
                        return path

                    with self.subTest(case=case_name, size=source_size), mock.patch(
                        "generate_textures._save_with_dds_fallback",
                        side_effect=record_saved_image,
                    ):
                        outputs = run_with_options(
                            input_file=input_path,
                            output_dir=temp_path / f"{case_name}-{source_size[0]}x{source_size[1]}",
                            **options,
                        )
                    label = f"{case_name} at {source_size}"
                    self.assertEqual(set(outputs), set(expected_names), label)
                    self.assertEqual(
                        {key: Path(path).name for key, path in outputs.items()},
                        expected_names,
                        label,
                    )
                    expected_image_names = {
                        name for name in expected_names.values() if name.endswith(".dds")
                    }
                    self.assertEqual(set(saved_image_sizes), expected_image_names, label)
                    self.assertTrue(
                        all(size == source_size for size in saved_image_sizes.values()),
                        f"{label}: {saved_image_sizes}",
                    )

    def test_run_with_options_rejects_env_mask_with_rmaos_combination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            _sample_image().save(input_path)

            with self.assertRaises(ValueError) as err:
                run_with_options(
                    input_file=input_path,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=True,
                    include_rmaos=True,
                    include_complex=False,
                )
            self.assertIn("cannot be generated together", str(err.exception))

    def test_run_with_options_rmaos_writes_json_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            outputs = run_with_options(
                input_file=input_path,
                output_dir=output_dir,
                include_diffuse=False,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_rmaos=True,
                include_complex=False,
            )

            self.assertIn("rmaos", outputs)
            self.assertIn("rmaos_json", outputs)
            self.assertTrue(outputs["rmaos_json"].exists())
            self.assertEqual(outputs["rmaos_json"].parent.name, "PBRNifPatcher")
            payload = json.loads(outputs["rmaos_json"].read_text(encoding="utf-8"))
            self.assertIsInstance(payload, list)
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["texture"], "brick")
            self.assertFalse(bool(payload[0]["parallax"]))
            self.assertFalse(bool(payload[0]["emissive"]))
            self.assertFalse(bool(payload[0]["subsurface"]))
            self.assertFalse(bool(payload[0]["subsurface_foliage"]))
            self.assertEqual(payload[0]["subsurface_color"], [1.0, 1.0, 1.0])
            self.assertAlmostEqual(float(payload[0]["subsurface_opacity"]), 1.0)
            self.assertAlmostEqual(float(payload[0]["specular_level"]), 0.04)

    def test_run_with_options_applies_explicit_truepbr_sidecar_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "skin.png"
            _sample_image().save(input_path)
            outputs = run_with_options(
                input_file=input_path,
                output_dir=temp_path / "out",
                include_diffuse=False,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_rmaos=True,
                include_complex=False,
                truepbr_material_overrides={
                    "parallax": True,
                    "displacement_scale": 0.35,
                    "specular_level": 0.08,
                    "roughness_scale": 0.7,
                },
            )
            payload = json.loads(outputs["rmaos_json"].read_text(encoding="utf-8"))[0]
            self.assertTrue(payload["parallax"])
            self.assertAlmostEqual(payload["displacement_scale"], 0.35)
            self.assertAlmostEqual(payload["specular_level"], 0.08)
            self.assertAlmostEqual(payload["roughness_scale"], 0.7)

    def test_run_with_options_routes_optional_map_strengths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "stone.png"
            _sample_image().save(input_path)
            generated_map = Image.new("L", (8, 8), color=127)
            with (
                mock.patch("generate_textures.generate_wetness_mask", return_value=generated_map) as wetness,
                mock.patch("generate_textures.generate_snow_mask", return_value=generated_map) as snow,
                mock.patch("generate_textures.generate_ambient_occlusion", return_value=generated_map) as ao,
                mock.patch("generate_textures.generate_roughness", return_value=generated_map) as roughness,
                mock.patch(
                    "generate_textures._save_with_dds_fallback",
                    side_effect=lambda _image, path, **_kwargs: path,
                ),
            ):
                run_with_options(
                    input_file=input_path,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_wetness_mask=True,
                    include_snow_mask=True,
                    include_ao=True,
                    include_roughness=True,
                    wetness_mask_strength=1.3,
                    snow_mask_strength=0.8,
                    ao_strength=2.2,
                    roughness_strength=1.4,
                )
            self.assertEqual(wetness.call_args.kwargs["strength"], 1.3)
            self.assertEqual(snow.call_args.kwargs["strength"], 0.8)
            self.assertEqual(ao.call_args.kwargs["strength"], 2.2)
            self.assertEqual(roughness.call_args.kwargs["strength"], 1.4)

    def test_run_with_options_rmaos_writes_sidecar_next_to_textures_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "mod" / "textures" / "trees" / "treepineforestbarkcomp.dds"
            output_dir = temp_path / "generated"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            _sample_image().save(input_path)

            outputs = run_with_options(
                input_file=input_path,
                output_dir=output_dir,
                include_diffuse=False,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_rmaos=True,
                include_complex=False,
            )

            expected_sidecar = (
                output_dir
                / "PBRNifPatcher"
                / "trees"
                / "treepineforestbarkcomp_rmaos.json"
            )
            self.assertEqual(outputs["rmaos_json"], expected_sidecar)
            self.assertTrue(expected_sidecar.exists())
            payload = json.loads(expected_sidecar.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["texture"], "trees/treepineforestbarkcomp")
            self.assertFalse(bool(payload[0]["parallax"]))

    def test_run_with_options_ramos_alias_writes_json_with_family_texture_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            outputs = run_with_options(
                input_file=input_path,
                output_dir=output_dir,
                include_diffuse=False,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_rmaos=True,
                include_complex=False,
                rmaos_name="brick_ramos",
            )

            self.assertEqual(outputs["rmaos"].name, "brick_ramos.dds")
            payload = json.loads(outputs["rmaos_json"].read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["texture"], "brick")


class EmbossNormalTests(unittest.TestCase):
    def test_emboss_normal_returns_rgb_same_size(self) -> None:
        result = generate_normal(_sample_image(), emboss_mode=True)
        self.assertEqual(result.mode, "RGB")
        self.assertEqual(result.size, (8, 8))

    def test_emboss_normal_flat_image_returns_flat_normal(self) -> None:
        flat = Image.new("RGB", (16, 16), color=(128, 128, 128))
        result = generate_normal(flat, strength=2.0, emboss_mode=True)
        self.assertEqual(result.mode, "RGB")
        self.assertEqual(result.size, (16, 16))
        # Flat uniform input → Sobel gradients are zero → flat normal (128, 128, 255).
        r, g, b = result.split()
        self.assertLessEqual(r.getextrema()[1] - r.getextrema()[0], 4)
        self.assertLessEqual(g.getextrema()[1] - g.getextrema()[0], 4)
        self.assertGreaterEqual(b.getextrema()[0], 245)

    def test_emboss_normal_differs_from_standard_on_detailed_image(self) -> None:
        detail = _detailed_bright_image()
        standard = generate_normal(detail, strength=2.0, emboss_mode=False)
        emboss = generate_normal(detail, strength=2.0, emboss_mode=True)
        self.assertNotEqual(standard.tobytes(), emboss.tobytes())

    def test_emboss_normal_blue_channel_stays_in_skyrim_safe_range(self) -> None:
        detail = _detailed_bright_image()
        result = generate_normal(detail, strength=2.0, emboss_mode=True)
        _, _, blue = result.split()
        self.assertGreaterEqual(blue.getextrema()[0], 128)

    def test_emboss_mode_threads_through_generate_msn(self) -> None:
        detail = _detailed_bright_image()
        standard_msn = generate_msn(detail, normal_strength=2.0, emboss_mode=False)
        emboss_msn = generate_msn(detail, normal_strength=2.0, emboss_mode=True)
        # RGB channels encode the normal; they should differ between modes.
        std_r, std_g, _, _ = standard_msn.split()
        emb_r, emb_g, _, _ = emboss_msn.split()
        self.assertNotEqual(std_r.tobytes(), emb_r.tobytes())

    def test_emboss_mode_threads_through_run_with_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "book_cover.png"
            _detailed_bright_image().save(input_path)
            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ):
                outputs = run_with_options(
                    input_file=input_path,
                    output_dir=temp_path / "out",
                    include_diffuse=False,
                    include_normal=True,
                    include_parallax=False,
                    emboss_mode=True,
                )
            self.assertIn("normal", outputs)

    def test_emboss_mode_produces_stronger_edge_response_for_printed_pattern(self) -> None:
        pattern = _emboss_pattern_image()
        standard = generate_normal(pattern, strength=2.0, emboss_mode=False)
        emboss = generate_normal(pattern, strength=2.0, emboss_mode=True)
        std_r, std_g, _ = standard.split()
        emb_r, emb_g, _ = emboss.split()
        standard_variation = sum(ImageStat.Stat(channel).stddev[0] for channel in (std_r, std_g))
        emboss_variation = sum(ImageStat.Stat(channel).stddev[0] for channel in (emb_r, emb_g))
        self.assertGreater(emboss_variation, standard_variation)

    def test_emboss_mode_low_detail_input_stays_close_to_flat_normal(self) -> None:
        low_detail = Image.new("RGB", (24, 24), color=(132, 132, 132))
        result = generate_normal(low_detail, strength=2.0, emboss_mode=True)
        r, g, b = result.split()
        self.assertLessEqual(r.getextrema()[1] - r.getextrema()[0], 4)
        self.assertLessEqual(g.getextrema()[1] - g.getextrema()[0], 4)
        self.assertGreaterEqual(b.getextrema()[0], 245)


class ReliefModeTests(unittest.TestCase):
    def test_relief_normal_returns_rgb_same_size(self) -> None:
        result = generate_normal(_sample_image(), relief_mode=True)
        self.assertEqual(result.mode, "RGB")
        self.assertEqual(result.size, (8, 8))

    def test_relief_normal_flat_image_returns_flat_normal(self) -> None:
        flat = Image.new("RGB", (16, 16), color=(128, 128, 128))
        result = generate_normal(flat, strength=2.0, relief_mode=True)
        self.assertEqual(result.mode, "RGB")
        self.assertEqual(result.size, (16, 16))
        r, g, b = result.split()
        self.assertLessEqual(r.getextrema()[1] - r.getextrema()[0], 4)
        self.assertLessEqual(g.getextrema()[1] - g.getextrema()[0], 4)
        self.assertGreaterEqual(b.getextrema()[0], 245)

    def test_relief_normal_blue_channel_stays_in_skyrim_safe_range(self) -> None:
        detail = _detailed_bright_image()
        result = generate_normal(detail, strength=2.0, relief_mode=True)
        _, _, blue = result.split()
        self.assertGreaterEqual(blue.getextrema()[0], 128)

    def test_relief_normal_differs_from_standard_on_detailed_image(self) -> None:
        detail = _detailed_bright_image()
        standard = generate_normal(detail, strength=2.0, relief_mode=False)
        relief = generate_normal(detail, strength=2.0, relief_mode=True)
        self.assertNotEqual(standard.tobytes(), relief.tobytes())

    def test_relief_mode_takes_priority_over_emboss_mode(self) -> None:
        detail = _detailed_bright_image()
        emboss_only = generate_normal(detail, strength=2.0, emboss_mode=True, relief_mode=False)
        relief_only = generate_normal(detail, strength=2.0, emboss_mode=False, relief_mode=True)
        both = generate_normal(detail, strength=2.0, emboss_mode=True, relief_mode=True)
        # relief_mode=True should take priority: result with both matches relief_only.
        self.assertEqual(both.tobytes(), relief_only.tobytes())
        self.assertNotEqual(both.tobytes(), emboss_only.tobytes())

    def test_relief_parallax_returns_l_same_size(self) -> None:
        from generate_textures import generate_parallax

        result = generate_parallax(_sample_image(), relief_mode=True)
        self.assertEqual(result.mode, "L")
        self.assertEqual(result.size, (8, 8))

    def test_relief_parallax_differs_from_standard(self) -> None:
        from generate_textures import generate_parallax

        detail = _detailed_bright_image()
        standard = generate_parallax(detail, strength=1.35, relief_mode=False)
        relief = generate_parallax(detail, strength=1.35, relief_mode=True)
        self.assertNotEqual(standard.tobytes(), relief.tobytes())

    def test_relief_mode_threads_through_generate_msn(self) -> None:
        detail = _detailed_bright_image()
        standard_msn = generate_msn(detail, normal_strength=2.0, relief_mode=False)
        relief_msn = generate_msn(detail, normal_strength=2.0, relief_mode=True)
        std_r, std_g, _, _ = standard_msn.split()
        rel_r, rel_g, _, _ = relief_msn.split()
        self.assertNotEqual(std_r.tobytes(), rel_r.tobytes())

    def test_relief_mode_threads_through_generate_preview_outputs(self) -> None:
        detail = _detailed_bright_image()
        standard_outputs = generate_preview_outputs(
            detail,
            normal_strength=2.0,
            parallax_strength=1.35,
            glow_threshold=190,
            environment_mask_strength=1.2,
            complex_strength=1.15,
            specular_strength=1.15,
            complex_format="msn",
            relief_mode=False,
            include_diffuse=False,
            include_normal=True,
            include_parallax=False,
            include_glow=False,
            include_environment_mask=False,
            include_complex=False,
        )
        relief_outputs = generate_preview_outputs(
            detail,
            normal_strength=2.0,
            parallax_strength=1.35,
            glow_threshold=190,
            environment_mask_strength=1.2,
            complex_strength=1.15,
            specular_strength=1.15,
            complex_format="msn",
            relief_mode=True,
            include_diffuse=False,
            include_normal=True,
            include_parallax=False,
            include_glow=False,
            include_environment_mask=False,
            include_complex=False,
        )
        self.assertNotEqual(
            standard_outputs["normal"].tobytes(),
            relief_outputs["normal"].tobytes(),
        )

    def test_relief_mode_threads_through_run_with_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "painting.png"
            _detailed_bright_image().save(input_path)
            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ):
                outputs = run_with_options(
                    input_file=input_path,
                    output_dir=temp_path / "out",
                    include_diffuse=False,
                    include_normal=True,
                    include_parallax=False,
                    relief_mode=True,
                )
            self.assertIn("normal", outputs)

    def test_analyze_image_content_includes_bg_uniformity(self) -> None:
        uniform = Image.new("RGB", (64, 64), color=(200, 200, 200))
        result = analyze_image_content(uniform)
        self.assertIn("bg_uniformity", result)
        self.assertGreaterEqual(float(result["bg_uniformity"]), 0.0)
        self.assertLessEqual(float(result["bg_uniformity"]), 1.0)

    def test_scharr_normal_differs_from_old_sobel_for_diagonal_gradient(self) -> None:
        # Create a diagonal gradient image — Scharr has better diagonal accuracy.
        diag = Image.new("RGB", (24, 24))
        px = diag.load()
        for y in range(24):
            for x in range(24):
                v = int((x + y) / 46.0 * 255)
                px[x, y] = (v, v, v)
        result = generate_normal(diag, strength=2.0)
        self.assertEqual(result.mode, "RGB")
        # Verify blue channel is in Skyrim-safe range.
        _, _, blue = result.split()
        self.assertGreaterEqual(blue.getextrema()[0], 128)

    def test_generate_normal_color_sign_image_is_not_near_flat(self) -> None:
        normal = generate_normal(_color_sign_image(), strength=3.2, relief_mode=True)
        red, green, blue = normal.split()
        self.assertGreater(red.getextrema()[1] - red.getextrema()[0], 8)
        self.assertGreater(green.getextrema()[1] - green.getextrema()[0], 8)
        self.assertGreaterEqual(blue.getextrema()[0], 128)

    def test_generate_specular_color_sign_image_is_not_flat(self) -> None:
        specular = generate_specular(_color_sign_image(), strength=2.0)
        minimum, maximum = specular.getextrema()
        self.assertGreater(maximum - minimum, 10)

    def test_generate_normal_shield_art_image_is_not_near_flat(self) -> None:
        normal = generate_normal(_shield_art_image(), strength=3.0)
        red, green, blue = normal.split()
        self.assertGreater(red.getextrema()[1] - red.getextrema()[0], 12)
        self.assertGreater(green.getextrema()[1] - green.getextrema()[0], 12)
        self.assertGreaterEqual(blue.getextrema()[0], 128)


class TooltipPositionTests(unittest.TestCase):
    def test_compute_tooltip_position_uses_cursor_offset_when_room_exists(self) -> None:
        x, y = _compute_tooltip_position(
            pointer_x=120,
            pointer_y=150,
            tip_width=200,
            tip_height=60,
            screen_width=1920,
            screen_height=1080,
        )
        self.assertEqual((x, y), (136, 170))

    def test_compute_tooltip_position_clamps_inside_screen_bounds(self) -> None:
        x, y = _compute_tooltip_position(
            pointer_x=1910,
            pointer_y=1070,
            tip_width=260,
            tip_height=120,
            screen_width=1920,
            screen_height=1080,
        )
        self.assertGreaterEqual(x, 10)
        self.assertGreaterEqual(y, 10)
        self.assertLessEqual(x + 260, 1910)
        self.assertLessEqual(y + 120, 1070)


class ParallaxOcclusionTests(unittest.TestCase):
    def test_generate_parallax_occlusion_returns_l_same_size(self) -> None:
        result = generate_parallax_occlusion(_sample_image())
        self.assertEqual(result.mode, "L")
        self.assertEqual(result.size, (8, 8))

    def test_generate_parallax_occlusion_produces_non_flat_output(self) -> None:
        result = generate_parallax_occlusion(_vertical_gradient_image(), strength=1.35)
        values = set(result.tobytes())
        self.assertGreater(len(values), 2)

    def test_parallax_occlusion_differs_from_standard_on_detailed_image(self) -> None:
        detail = _detailed_bright_image()
        standard = generate_parallax(detail, strength=1.35)
        occlusion = generate_parallax_occlusion(detail, strength=1.35)
        self.assertNotEqual(standard.tobytes(), occlusion.tobytes())

    def test_parallax_occlusion_smoother_than_standard(self) -> None:
        """POM heightmap should have smaller pixel-to-pixel variance (smoother gradients)."""
        import statistics
        detail = _detailed_bright_image()
        std_bytes = list(generate_parallax(detail, strength=1.35).tobytes())
        pom_bytes = list(generate_parallax_occlusion(detail, strength=1.35).tobytes())
        std_diffs = [abs(std_bytes[i + 1] - std_bytes[i]) for i in range(len(std_bytes) - 1)]
        pom_diffs = [abs(pom_bytes[i + 1] - pom_bytes[i]) for i in range(len(pom_bytes) - 1)]
        self.assertLessEqual(statistics.mean(pom_diffs), statistics.mean(std_diffs))

    def test_parallax_occlusion_keeps_mean_depth_near_standard(self) -> None:
        detail = _detailed_bright_image()
        standard_mean = ImageStat.Stat(generate_parallax(detail, strength=1.35)).mean[0]
        pom_mean = ImageStat.Stat(generate_parallax_occlusion(detail, strength=1.35)).mean[0]
        self.assertLess(abs(pom_mean - standard_mean), 40.0)

    def test_generate_parallax_occlusion_strength_slider_has_stronger_min_max_separation(self) -> None:
        source = _detailed_bright_image()
        low = generate_parallax_occlusion(source, strength=0.2)
        high = generate_parallax_occlusion(source, strength=10.0)
        low_range = low.getextrema()[1] - low.getextrema()[0]
        high_range = high.getextrema()[1] - high.getextrema()[0]
        self.assertGreater(high_range, low_range + 18)

    def test_map_parallax_strength_to_nif_scale_boosts_in_game_depth(self) -> None:
        self.assertAlmostEqual(float(_map_parallax_strength_to_nif_scale(0.1) or 0.0), 0.34, places=2)
        self.assertGreater(float(_map_parallax_strength_to_nif_scale(1.35) or 0.0), 2.0)
        self.assertAlmostEqual(float(_map_parallax_strength_to_nif_scale(10.0) or 0.0), 10.0, places=3)

    def test_parallax_occlusion_flat_input_returns_mid_gray(self) -> None:
        flat = Image.new("RGB", (16, 16), color=(128, 128, 128))
        result = generate_parallax_occlusion(flat, strength=1.35)
        self.assertEqual(result.getextrema(), (127, 127))

    def test_parallax_mode_occlusion_threads_through_preview_outputs(self) -> None:
        detail = _detailed_bright_image()
        standard_out = generate_preview_outputs(
            detail,
            normal_strength=2.0, parallax_strength=1.35, glow_threshold=200,
            environment_mask_strength=1.0, complex_strength=1.0, specular_strength=1.15,
            complex_format="msn", parallax_mode="standard",
            include_diffuse=False, include_normal=False, include_parallax=True,
            include_glow=False, include_environment_mask=False, include_complex=False,
        )
        occlusion_out = generate_preview_outputs(
            detail,
            normal_strength=2.0, parallax_strength=1.35, glow_threshold=200,
            environment_mask_strength=1.0, complex_strength=1.0, specular_strength=1.15,
            complex_format="msn", parallax_mode="occlusion",
            include_diffuse=False, include_normal=False, include_parallax=True,
            include_glow=False, include_environment_mask=False, include_complex=False,
        )
        self.assertNotEqual(
            standard_out["parallax"].tobytes(),
            occlusion_out["parallax"].tobytes(),
        )

    def test_parallax_mode_occlusion_threads_through_run_with_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "stone.png"
            _detailed_bright_image().save(input_path)
            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ):
                outputs = run_with_options(
                    input_file=input_path,
                    output_dir=temp_path / "out",
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=True,
                    parallax_mode="occlusion",
                )
            self.assertIn("parallax", outputs)

    def test_generate_preview_outputs_rejects_invalid_parallax_mode(self) -> None:
        with self.assertRaises(ValueError):
            generate_preview_outputs(
                _sample_image(),
                normal_strength=2.0,
                parallax_strength=1.35,
                glow_threshold=200,
                environment_mask_strength=1.0,
                complex_strength=1.0,
                specular_strength=1.15,
                complex_format="msn",
                parallax_mode="invalid-mode",
                include_diffuse=False,
                include_normal=False,
                include_parallax=True,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
            )

    def test_run_with_options_rejects_invalid_parallax_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "stone.png"
            _sample_image().save(input_path)
            with self.assertRaises(ValueError):
                run_with_options(
                    input_file=input_path,
                    output_dir=temp_path / "out",
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=True,
                    parallax_mode="broken",
                )

    def test_run_with_options_uses_resolved_truepbr_env_mask_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "stone.png"
            _sample_image().save(input_path)
            captured_workflows: list[str] = []
            original_generate = generate_environment_mask_for_workflow

            def _spy(source, *, strength=1.2, mode="standard", complex_workflow="truepbr"):
                captured_workflows.append(str(complex_workflow))
                return original_generate(
                    source,
                    strength=strength,
                    mode=mode,
                    complex_workflow=complex_workflow,
                )

            with mock.patch("generate_textures.generate_environment_mask_for_workflow", side_effect=_spy), \
                 mock.patch(
                     "generate_textures._save_with_dds_fallback",
                     side_effect=lambda _image, path, **_kwargs: path,
                 ):
                run_with_options(
                    input_file=input_path,
                    output_dir=temp_path / "out",
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=True,
                    include_complex=True,
                    complex_format="cm",
                    env_mask_mode="complex",
                    render_profile="auto",
                )
            self.assertEqual(captured_workflows, ["truepbr"])

    def test_generate_preview_outputs_uses_resolved_truepbr_env_mask_workflow(self) -> None:
        source = _sample_image()
        captured_workflows: list[str] = []
        original_generate = generate_environment_mask_for_workflow

        def _spy(source_image, *, strength=1.2, mode="standard", complex_workflow="truepbr"):
            captured_workflows.append(str(complex_workflow))
            return original_generate(
                source_image,
                strength=strength,
                mode=mode,
                complex_workflow=complex_workflow,
            )

        with mock.patch("generate_textures.generate_environment_mask_for_workflow", side_effect=_spy):
            generate_preview_outputs(
                source,
                normal_strength=2.0,
                parallax_strength=1.35,
                glow_threshold=190,
                environment_mask_strength=1.2,
                complex_strength=1.2,
                specular_strength=1.1,
                complex_format="cm",
                env_mask_mode="complex",
                include_diffuse=False,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=True,
                include_complex=True,
                render_profile="auto",
            )
        self.assertEqual(captured_workflows, ["truepbr"])

    def test_run_with_options_rejects_conflicting_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "stone.png"
            _sample_image().save(input_path)
            with self.assertRaisesRegex(ValueError, "Conflicting output filenames detected"):
                run_with_options(
                    input_file=input_path,
                    output_dir=temp_path / "out",
                    diffuse_name="stone_shared",
                    normal_name="stone_shared",
                    include_diffuse=True,
                    include_normal=True,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_rmaos=False,
                    include_complex=False,
                )

    def test_generate_parallax_occlusion_relief_mode_differs_from_standard(self) -> None:
        """Relief mode should change the occlusion heightmap (luminosity-as-height)."""
        source = _detailed_bright_image()
        standard = generate_parallax_occlusion(source, strength=1.35, relief_mode=False)
        relief = generate_parallax_occlusion(source, strength=1.35, relief_mode=True)
        self.assertEqual(standard.mode, "L")
        self.assertEqual(relief.mode, "L")
        self.assertNotEqual(standard.tobytes(), relief.tobytes())

    def test_generate_parallax_occlusion_relief_flat_returns_l(self) -> None:
        """relief_mode on a flat input should still return an L-mode image of the same size."""
        flat = Image.new("RGB", (16, 16), color=(128, 128, 128))
        result = generate_parallax_occlusion(flat, strength=1.35, relief_mode=True)
        self.assertEqual(result.mode, "L")
        self.assertEqual(result.size, (16, 16))

    def test_run_with_options_parallax_occlusion_relief_mode_threads_through(self) -> None:
        """run_with_options should pass relief_mode to generate_parallax_occlusion."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "sign.png"
            _detailed_bright_image().save(input_path)
            captured: list[dict] = []

            original_gen = generate_parallax_occlusion

            def _spy(source, strength=1.35, *, relief_mode=False):
                captured.append({"relief_mode": relief_mode})
                return original_gen(source, strength=strength, relief_mode=relief_mode)

            with mock.patch("generate_textures.generate_parallax_occlusion", side_effect=_spy), \
                 mock.patch(
                     "generate_textures._save_with_dds_fallback",
                     side_effect=lambda _img, path, **_kw: path,
                 ):
                run_with_options(
                    input_file=input_path,
                    output_dir=temp_path / "out",
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=True,
                    parallax_mode="occlusion",
                    relief_mode=True,
                )
            self.assertEqual(len(captured), 1)
            self.assertTrue(captured[0]["relief_mode"])

    def test_generate_preview_outputs_parallax_occlusion_relief_mode_threads_through(self) -> None:
        """generate_preview_outputs should pass relief_mode to generate_parallax_occlusion."""
        source = _detailed_bright_image()
        standard = generate_preview_outputs(
            source,
            normal_strength=2.0, parallax_strength=1.35, glow_threshold=200,
            environment_mask_strength=1.0, complex_strength=1.0, specular_strength=1.15,
            complex_format="msn", parallax_mode="occlusion", relief_mode=False,
            include_diffuse=False, include_normal=False, include_parallax=True,
            include_glow=False, include_environment_mask=False, include_complex=False,
        )
        relief = generate_preview_outputs(
            source,
            normal_strength=2.0, parallax_strength=1.35, glow_threshold=200,
            environment_mask_strength=1.0, complex_strength=1.0, specular_strength=1.15,
            complex_format="msn", parallax_mode="occlusion", relief_mode=True,
            include_diffuse=False, include_normal=False, include_parallax=True,
            include_glow=False, include_environment_mask=False, include_complex=False,
        )
        self.assertNotEqual(
            standard["parallax"].tobytes(),
            relief["parallax"].tobytes(),
        )



    """Tests for conflicting-option and output-folder-format warnings."""

    def _base_kwargs(self) -> dict:
        return dict(
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            include_complex=False,
        )

    def test_emboss_and_relief_both_enabled_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            emboss_mode=True,
            relief_mode=True,
            **self._base_kwargs(),
        )
        ids = [w[0] for w in warnings]
        self.assertIn("emboss_and_relief_both_enabled", ids)

    def test_emboss_and_relief_not_both_enabled_no_conflict_warning(self) -> None:
        for emboss, relief in [(True, False), (False, True), (False, False)]:
            warnings = get_generation_warnings(
                "stone",
                include_normal=True,
                emboss_mode=emboss,
                relief_mode=relief,
                **self._base_kwargs(),
            )
            ids = [w[0] for w in warnings]
            self.assertNotIn("emboss_and_relief_both_enabled", ids)

    def test_emboss_mode_without_normal_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=False,
            emboss_mode=True,
            relief_mode=False,
            **self._base_kwargs(),
        )
        ids = [w[0] for w in warnings]
        self.assertIn("depth_mode_without_normal", ids)

    def test_relief_mode_without_normal_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=False,
            emboss_mode=False,
            relief_mode=True,
            **self._base_kwargs(),
        )
        ids = [w[0] for w in warnings]
        self.assertIn("depth_mode_without_normal", ids)

    def test_depth_mode_with_normal_enabled_no_warning(self) -> None:
        for emboss, relief in [(True, False), (False, True)]:
            warnings = get_generation_warnings(
                "stone",
                include_normal=True,
                emboss_mode=emboss,
                relief_mode=relief,
                **self._base_kwargs(),
            )
            ids = [w[0] for w in warnings]
            self.assertNotIn("depth_mode_without_normal", ids)

    def test_emboss_non_paper_material_triggers_warning(self) -> None:
        """Emboss mode on a solid material (stone, terrain, metal…) should warn."""
        for material in ("stone", "terrain", "metal", "wood", "skin"):
            with self.subTest(material=material):
                warnings = get_generation_warnings(
                    material,
                    include_normal=True,
                    emboss_mode=True,
                    relief_mode=False,
                    **self._base_kwargs(),
                )
                ids = [w[0] for w in warnings]
                self.assertIn("emboss_non_paper_material", ids)

    def test_emboss_paper_no_false_positive_for_non_paper_warning(self) -> None:
        """Emboss mode on paper is correct usage — should NOT trigger the non-paper warning."""
        warnings = get_generation_warnings(
            "paper",
            include_normal=True,
            emboss_mode=True,
            relief_mode=False,
            **self._base_kwargs(),
        )
        ids = [w[0] for w in warnings]
        self.assertNotIn("emboss_non_paper_material", ids)

    def test_emboss_non_paper_warning_not_raised_when_emboss_off(self) -> None:
        """No false positive when emboss_mode is False on a non-paper material."""
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            emboss_mode=False,
            relief_mode=False,
            **self._base_kwargs(),
        )
        ids = [w[0] for w in warnings]
        self.assertNotIn("emboss_non_paper_material", ids)

    def test_env_mask_with_complex_material_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_environment_mask=True,
            include_complex=True,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="cm",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("env_mask_with_complex_material", ids)

    def test_env_mask_with_enb_complex_combo_does_not_trigger_conflict_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_environment_mask=True,
            include_complex=True,
            include_glow=False,
            env_mask_mode="complex",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="msn",
        )
        ids = [w[0] for w in warnings]
        self.assertNotIn("env_mask_with_complex_material", ids)

    def test_env_mask_alone_no_complex_conflict_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_environment_mask=True,
            include_complex=False,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
        )
        ids = [w[0] for w in warnings]
        self.assertNotIn("env_mask_with_complex_material", ids)

    def test_msn_with_standard_env_mode_triggers_renderer_mismatch_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=False,
            include_complex=True,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="msn",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("msn_with_standard_env_mode", ids)

    def test_cm_with_normal_enabled_does_not_trigger_msn_explanatory_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=False,
            include_complex=True,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="cm",
        )
        ids = [w[0] for w in warnings]
        self.assertNotIn("msn_with_standard_env_mode", ids)

    def test_cm_with_complex_env_mode_triggers_renderer_mismatch_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=True,
            include_complex=True,
            include_glow=False,
            env_mask_mode="complex",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="cm",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("cm_with_complex_env_mode", ids)

    def test_cm_without_normal_map_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=False,
            include_environment_mask=False,
            include_complex=True,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="cm",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("cm_without_normal_map", ids)

    def test_rmaos_without_normal_map_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=False,
            include_environment_mask=False,
            include_rmaos=True,
            include_complex=False,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="cm",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("rmaos_without_normal_map", ids)

    def test_rmaos_with_msn_complex_triggers_workflow_mix_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=True,
            include_rmaos=True,
            include_complex=True,
            include_glow=False,
            env_mask_mode="complex",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="msn",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("rmaos_with_msn_mix", ids)

    def test_get_generation_warnings_rmaos_with_env_mask_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=True,
            include_rmaos=True,
            include_complex=False,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("rmaos_with_env_mask", ids)

    def test_get_generation_warnings_rmaos_without_env_mask_no_false_env_mask_warnings(self) -> None:
        """RMAOS alone must not trigger env-mask-specific warnings (false-positive guard)."""
        warnings = get_generation_warnings(
            "organic",
            include_normal=True,
            include_environment_mask=False,
            include_rmaos=True,
            include_complex=False,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.8,
            include_parallax=False,
        )
        ids = [w[0] for w in warnings]
        env_mask_specific = {
            "high_env_mask_organic",
            "high_env_mask_glass",
            "low_env_mask_metal",
            "high_env_mask_strength",
        }
        for warning_id in env_mask_specific:
            self.assertNotIn(warning_id, ids, f"False-positive: {warning_id} fired with RMAOS only")

    def test_msn_with_normal_output_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=True,
            include_complex=True,
            include_glow=False,
            env_mask_mode="complex",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="msn",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("msn_with_normal_output", ids)

    def test_complex_env_without_complex_material_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=True,
            include_complex=False,
            include_glow=False,
            env_mask_mode="complex",
            env_mask_strength=1.2,
            include_parallax=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("complex_env_without_msn", ids)

    def test_cm_with_occlusion_parallax_triggers_workflow_mix_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=False,
            include_complex=True,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=True,
            complex_format="cm",
            parallax_mode="occlusion",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("cm_with_enb_pom", ids)

    def test_get_output_folder_format_warnings_msn_vs_cm_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "stone_msn.dds").write_bytes(b"fake")
            warnings = get_output_folder_format_warnings(
                temp_path, include_complex=True, complex_format="cm"
            )
            ids = [w[0] for w in warnings]
            self.assertIn("output_folder_has_msn_files", ids)

    def test_get_output_folder_format_warnings_cm_vs_msn_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "stone_cm.dds").write_bytes(b"fake")
            warnings = get_output_folder_format_warnings(
                temp_path, include_complex=True, complex_format="msn"
            )
            ids = [w[0] for w in warnings]
            self.assertIn("output_folder_has_cm_files", ids)

    def test_get_output_folder_format_warnings_c_vs_msn_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "stone_c.dds").write_bytes(b"fake")
            warnings = get_output_folder_format_warnings(
                temp_path, include_complex=True, complex_format="msn"
            )
            ids = [w[0] for w in warnings]
            self.assertIn("output_folder_has_cm_files", ids)

    def test_get_output_folder_format_warnings_uppercase_C_vs_msn_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "stone_C.dds").write_bytes(b"fake")
            warnings = get_output_folder_format_warnings(
                temp_path, include_complex=True, complex_format="msn"
            )
            ids = [w[0] for w in warnings]
            self.assertIn("output_folder_has_cm_files", ids)

    def test_get_output_folder_format_warnings_matching_format_no_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "stone_msn.dds").write_bytes(b"fake")
            warnings = get_output_folder_format_warnings(
                temp_path, include_complex=True, complex_format="msn"
            )
            self.assertEqual(warnings, [])

    def test_get_output_folder_format_warnings_include_complex_false_no_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "stone_msn.dds").write_bytes(b"fake")
            warnings = get_output_folder_format_warnings(
                temp_path, include_complex=False, complex_format="cm"
            )
            self.assertEqual(warnings, [])

    def test_get_output_folder_format_warnings_empty_folder_no_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            warnings = get_output_folder_format_warnings(
                temp_path, include_complex=True, complex_format="cm"
            )
            self.assertEqual(warnings, [])

    def test_normalize_gui_state_persists_dismissed_warnings(self) -> None:
        from generate_textures import _normalize_gui_state
        state = _normalize_gui_state({"dismissed_warnings": ["glow_non_magical", "parallax_flat_plants"]})
        self.assertIn("glow_non_magical", state["dismissed_warnings"])
        self.assertIn("parallax_flat_plants", state["dismissed_warnings"])

    def test_normalize_gui_state_dismissed_warnings_defaults_to_empty(self) -> None:
        from generate_textures import _normalize_gui_state
        state = _normalize_gui_state({})
        self.assertEqual(state["dismissed_warnings"], [])

    def test_auto_patch_nifs_default_is_false(self) -> None:
        from generate_textures import _GUI_STATE_DEFAULTS
        self.assertFalse(
            _GUI_STATE_DEFAULTS["auto_patch_nifs"],
            "auto_patch_nifs must default to False so NIF auto-patching is opt-in",
        )

    def test_normalize_gui_state_auto_patch_nifs_defaults_false_when_missing(self) -> None:
        from generate_textures import _normalize_gui_state
        state = _normalize_gui_state({})
        self.assertFalse(state["auto_patch_nifs"])

    # --- Wetness mask tests ---

    def test_generate_wetness_mask_returns_l_mode_same_size(self) -> None:
        source = _sample_image()
        result = generate_wetness_mask(source, strength=1.0)
        self.assertEqual(result.mode, "L")
        self.assertEqual(result.size, source.size)

    def test_generate_wetness_mask_no_pure_black(self) -> None:
        source = _sample_image()
        result = generate_wetness_mask(source, strength=1.0)
        pixels = list(result.get_flattened_data())
        self.assertTrue(all(v > 0 for v in pixels), "Wetness mask should have no pure-black pixels (floor-lifted)")

    def test_generate_wetness_mask_higher_strength_increases_contrast(self) -> None:
        source = Image.new("RGB", (32, 32))
        pixels = source.load()
        for y in range(32):
            for x in range(32):
                pixels[x, y] = (x * 8, y * 8, 0)
        low = generate_wetness_mask(source, strength=0.5)
        high = generate_wetness_mask(source, strength=2.5)
        low_vals = list(low.get_flattened_data())
        high_vals = list(high.get_flattened_data())
        low_range = max(low_vals) - min(low_vals)
        high_range = max(high_vals) - min(high_vals)
        self.assertGreaterEqual(high_range, low_range, "Higher strength should produce >= contrast range")

    # --- Snow mask tests ---

    def test_generate_snow_mask_returns_l_mode_same_size(self) -> None:
        source = _sample_image()
        result = generate_snow_mask(source, strength=1.0)
        self.assertEqual(result.mode, "L")
        self.assertEqual(result.size, source.size)

    def test_generate_snow_mask_no_pure_black(self) -> None:
        source = _sample_image()
        result = generate_snow_mask(source, strength=1.0)
        pixels = list(result.get_flattened_data())
        self.assertTrue(all(v > 0 for v in pixels), "Snow mask should have no pure-black pixels (floor-lifted)")

    def test_generate_snow_mask_higher_strength_increases_brightness(self) -> None:
        source = _sample_image()
        low = generate_snow_mask(source, strength=0.5)
        high = generate_snow_mask(source, strength=2.5)
        low_mean = sum(low.get_flattened_data()) / (source.width * source.height)
        high_mean = sum(high.get_flattened_data()) / (source.width * source.height)
        self.assertGreaterEqual(high_mean, low_mean, "Higher snow mask strength should produce >= mean brightness")

    # --- Output path builder tests ---

    def test_build_wetness_mask_output_path_uses_wt_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "stone.dds"
            input_path.write_bytes(b"stub")
            result = build_wetness_mask_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                wetness_name=None,
            )
            self.assertEqual(result.name, "stone_wt.dds")

    def test_build_snow_mask_output_path_uses_sm_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "stone.dds"
            input_path.write_bytes(b"stub")
            result = build_snow_mask_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                snow_name=None,
            )
            self.assertEqual(result.name, "stone_sm.dds")

    # --- GENERATED_TEXTURE_SUFFIXES tests ---

    def test_wt_in_generated_texture_suffixes(self) -> None:
        self.assertIn("_wt", GENERATED_TEXTURE_SUFFIXES)

    def test_sm_in_generated_texture_suffixes(self) -> None:
        self.assertIn("_sm", GENERATED_TEXTURE_SUFFIXES)

    # --- run_with_options wetness/snow integration tests ---

    def test_run_with_options_generates_wetness_mask(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                    include_wetness_mask=True,
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.args[1].name, "brick_wt.dds")

    def test_run_with_options_generates_snow_mask(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                    include_snow_mask=True,
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.args[1].name, "brick_sm.dds")

    # --- Generation warnings for snow/wetness tests ---

    def test_snow_mask_on_glass_raises_warning(self) -> None:
        warnings = get_generation_warnings(
            "glass",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
            include_snow_mask=True,
        )
        warning_ids = [w[0] for w in warnings]
        self.assertIn("snow_mask_on_non_accumulating_material", warning_ids)

    def test_snow_mask_on_stone_raises_no_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
            include_snow_mask=True,
        )
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("snow_mask_on_non_accumulating_material", warning_ids)

    def test_wetness_mask_on_glass_raises_warning(self) -> None:
        warnings = get_generation_warnings(
            "glass",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
            include_wetness_mask=True,
        )
        warning_ids = [w[0] for w in warnings]
        self.assertIn("wetness_mask_on_reflective_material", warning_ids)

    def test_wetness_mask_on_stone_raises_no_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
            include_wetness_mask=True,
        )
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("wetness_mask_on_reflective_material", warning_ids)


# ---------------------------------------------------------------------------
# Parallax warnings — glass and skin
# ---------------------------------------------------------------------------


class ParallaxGlassSkinWarningTests(unittest.TestCase):
    """Tests for new parallax_flat_glass and parallax_character_skin warnings."""

    def _base_kwargs(self) -> dict:
        return dict(
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            include_complex=False,
        )

    def test_parallax_glass_raises_warning(self) -> None:
        warnings = get_generation_warnings("glass", **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertIn("parallax_flat_glass", warning_ids)

    def test_parallax_glass_no_warning_when_parallax_off(self) -> None:
        kwargs = self._base_kwargs()
        kwargs["include_parallax"] = False
        warnings = get_generation_warnings("glass", **kwargs)
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_flat_glass", warning_ids)

    def test_parallax_skin_raises_warning(self) -> None:
        warnings = get_generation_warnings("skin", **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertIn("parallax_character_skin", warning_ids)

    def test_parallax_skin_no_warning_when_parallax_off(self) -> None:
        kwargs = self._base_kwargs()
        kwargs["include_parallax"] = False
        warnings = get_generation_warnings("skin", **kwargs)
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_character_skin", warning_ids)

    def test_parallax_stone_raises_no_glass_or_skin_warning(self) -> None:
        warnings = get_generation_warnings("stone", **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_flat_glass", warning_ids)
        self.assertNotIn("parallax_character_skin", warning_ids)


# ---------------------------------------------------------------------------
# recommend_output_resolution
# ---------------------------------------------------------------------------


class RecommendOutputResolutionTests(unittest.TestCase):
    """Tests for recommend_output_resolution(width, height, material_type)."""

    def test_terrain_max_is_2048(self) -> None:
        max_dim, _ = recommend_output_resolution(4096, 4096, "terrain")
        self.assertEqual(max_dim, 2048)

    def test_skin_max_is_2048(self) -> None:
        max_dim, _ = recommend_output_resolution(4096, 4096, "skin")
        self.assertEqual(max_dim, 2048)

    def test_plants_max_is_1024(self) -> None:
        max_dim, _ = recommend_output_resolution(2048, 2048, "plants")
        self.assertEqual(max_dim, 1024)

    def test_paper_max_is_1024(self) -> None:
        max_dim, _ = recommend_output_resolution(2048, 2048, "paper")
        self.assertEqual(max_dim, 1024)

    def test_architecture_max_is_4096(self) -> None:
        max_dim, _ = recommend_output_resolution(8192, 8192, "architecture")
        self.assertEqual(max_dim, 4096)

    def test_general_max_is_4096(self) -> None:
        max_dim, _ = recommend_output_resolution(8192, 8192, "general")
        self.assertEqual(max_dim, 4096)

    def test_unknown_material_max_is_4096(self) -> None:
        max_dim, _ = recommend_output_resolution(8192, 8192, "unknownmaterial")
        self.assertEqual(max_dim, 4096)

    def test_returns_reason_string(self) -> None:
        _, reason = recommend_output_resolution(2048, 2048, "general")
        self.assertIsInstance(reason, str)
        self.assertTrue(len(reason) > 0)

    def test_small_source_returns_max_dim_unchanged(self) -> None:
        max_dim, _ = recommend_output_resolution(512, 512, "terrain")
        self.assertEqual(max_dim, 2048)

    def test_8k_source_reason_mentions_resolution(self) -> None:
        _, reason = recommend_output_resolution(16384, 16384, "general")
        self.assertIn("16384", reason)


# ---------------------------------------------------------------------------
# Source resolution warning in get_generation_warnings
# ---------------------------------------------------------------------------


class SourceResolutionWarningTests(unittest.TestCase):
    """Tests that get_generation_warnings emits source_resolution_excessive when appropriate."""

    def _base_kwargs(self) -> dict:
        return dict(
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
        )

    def test_terrain_8k_source_raises_resolution_warning(self) -> None:
        warnings = get_generation_warnings("terrain", source_size=(8192, 8192), **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertIn("source_resolution_excessive", warning_ids)

    def test_terrain_2k_source_no_resolution_warning(self) -> None:
        warnings = get_generation_warnings("terrain", source_size=(2048, 2048), **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("source_resolution_excessive", warning_ids)

    def test_general_4k_source_no_resolution_warning(self) -> None:
        warnings = get_generation_warnings("general", source_size=(4096, 4096), **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("source_resolution_excessive", warning_ids)

    def test_general_8k_source_raises_resolution_warning(self) -> None:
        warnings = get_generation_warnings("general", source_size=(8192, 4096), **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertIn("source_resolution_excessive", warning_ids)

    def test_no_source_size_no_resolution_warning(self) -> None:
        warnings = get_generation_warnings("terrain", source_size=None, **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("source_resolution_excessive", warning_ids)


# ---------------------------------------------------------------------------
# Material-aware write_rmaos_json_sidecar
# ---------------------------------------------------------------------------


class RmaosJsonMaterialTypeTests(unittest.TestCase):
    """Tests that write_rmaos_json_sidecar applies per-material PBR overrides."""

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_and_read(
        self,
        material_type: str,
        material_overrides: dict[str, object] | None = None,
    ) -> dict:
        import json
        from pathlib import Path

        rmaos_path = Path(self._tmpdir) / f"test_{material_type}_rmaos.dds"
        write_rmaos_json_sidecar(
            rmaos_path,
            parallax_enabled=False,
            material_type=material_type,
            material_overrides=material_overrides,
        )
        # The function writes into a PBRNifPatcher sub-directory.
        json_path = Path(self._tmpdir) / "PBRNifPatcher" / f"{rmaos_path.stem}.json"
        with open(json_path) as f:
            raw = json.load(f)
        # Payload is a list; return the first (and only) entry for convenience.
        return raw[0] if isinstance(raw, list) else raw

    def test_sidecar_uses_separate_truepbr_material_parameters(self) -> None:
        data = self._write_and_read("general")
        schema_fields = {
            "texture",
            "emissive",
            "emissive_scale",
            "parallax",
            "displacement_scale",
            "specular_level",
            "roughness_scale",
            "smooth_angle",
            "subsurface",
            "subsurface_foliage",
            "subsurface_color",
            "subsurface_opacity",
            "vertex_color_lum_mult",
        }
        self.assertTrue(schema_fields.issubset(data))
        self.assertIsInstance(data["parallax"], bool)

    def test_explicit_sidecar_overrides_replace_material_defaults(self) -> None:
        data = self._write_and_read(
            "skin",
            {
                "parallax": True,
                "displacement_scale": 0.35,
                "specular_level": 0.08,
                "roughness_scale": 0.7,
            },
        )
        self.assertTrue(data["parallax"])
        self.assertAlmostEqual(data["displacement_scale"], 0.35)
        self.assertAlmostEqual(data["specular_level"], 0.08)
        self.assertAlmostEqual(data["roughness_scale"], 0.7)

    def test_sidecar_rejects_invalid_material_override(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 0.0 and 1.0"):
            self._write_and_read("general", {"specular_level": 2.0})

    def test_general_uses_defaults(self) -> None:
        data = self._write_and_read("general")
        # General should not enable subsurface
        self.assertFalse(data.get("subsurface", False))
        self.assertFalse(data.get("subsurface_foliage", False))

    def test_skin_enables_subsurface(self) -> None:
        data = self._write_and_read("skin")
        self.assertTrue(data.get("subsurface", False))
        self.assertAlmostEqual(data["subsurface_color"][0], 1.0, places=3)
        self.assertAlmostEqual(data["subsurface_color"][1], 0.85, places=3)
        self.assertAlmostEqual(data["subsurface_color"][2], 0.7, places=3)
        self.assertAlmostEqual(data["subsurface_opacity"], 0.65, places=3)
        self.assertAlmostEqual(data["displacement_scale"], 0.0, places=3)

    def test_plants_enables_foliage_subsurface(self) -> None:
        data = self._write_and_read("plants")
        self.assertTrue(data.get("subsurface_foliage", False))
        self.assertAlmostEqual(data["displacement_scale"], 0.0, places=3)

    def test_metal_has_high_specular(self) -> None:
        data = self._write_and_read("metal")
        self.assertAlmostEqual(data["specular_level"], 0.5, places=3)

    def test_glass_has_low_roughness(self) -> None:
        data = self._write_and_read("glass")
        self.assertAlmostEqual(data["roughness_scale"], 0.35, places=3)

    def test_terrain_has_shallow_displacement(self) -> None:
        data = self._write_and_read("terrain")
        self.assertAlmostEqual(data["displacement_scale"], 0.08, places=3)

    def test_snow_roughness_set(self) -> None:
        data = self._write_and_read("snow")
        self.assertAlmostEqual(data["roughness_scale"], 0.75, places=3)

    def test_cloth_low_specular(self) -> None:
        data = self._write_and_read("cloth")
        self.assertAlmostEqual(data["specular_level"], 0.016, places=4)

    def test_fur_has_very_high_roughness_and_very_low_specular(self) -> None:
        data = self._write_and_read("fur")
        self.assertGreater(data["roughness_scale"], 1.2)
        self.assertLess(data["specular_level"], 0.02)

    def test_paper_has_low_specular_and_shallow_displacement(self) -> None:
        data = self._write_and_read("paper")
        self.assertLess(data["specular_level"], 0.02)
        self.assertLess(data["displacement_scale"], 0.1)

    def test_dirt_has_high_roughness(self) -> None:
        data = self._write_and_read("dirt")
        self.assertGreater(data["roughness_scale"], 1.1)

    def test_sand_has_low_specular(self) -> None:
        data = self._write_and_read("sand")
        self.assertLess(data["specular_level"], 0.03)

    def test_architecture_has_moderate_roughness(self) -> None:
        data = self._write_and_read("architecture")
        self.assertGreater(data["roughness_scale"], 1.0)
        self.assertLess(data["roughness_scale"], 1.2)

    def test_fur_displacement_is_shallow(self) -> None:
        data = self._write_and_read("fur")
        self.assertLess(data["displacement_scale"], 0.15)

    def test_dirt_no_subsurface(self) -> None:
        data = self._write_and_read("dirt")
        self.assertFalse(data.get("subsurface", False))
        self.assertFalse(data.get("subsurface_foliage", False))

    def test_sand_no_subsurface(self) -> None:
        data = self._write_and_read("sand")
        self.assertFalse(data.get("subsurface", False))
        self.assertFalse(data.get("subsurface_foliage", False))

    def test_hair_has_notable_specular_and_no_displacement(self) -> None:
        data = self._write_and_read("hair")
        self.assertGreater(data["specular_level"], 0.04)
        self.assertAlmostEqual(data["displacement_scale"], 0.0, places=3)

    def test_hair_no_subsurface(self) -> None:
        data = self._write_and_read("hair")
        self.assertFalse(data.get("subsurface", False))
        self.assertFalse(data.get("subsurface_foliage", False))

    def test_glass_has_high_emissive_scale(self) -> None:
        data = self._write_and_read("glass")
        self.assertGreater(data["emissive_scale"], 0.4)

    def test_metal_has_elevated_emissive_scale(self) -> None:
        data = self._write_and_read("metal")
        self.assertGreater(data["emissive_scale"], 0.25)

    def test_emissive_scale_present_for_all_material_types(self) -> None:
        for material_type in (
            "skin", "plants", "metal", "glass", "snow", "terrain", "cloth",
            "leather", "stone", "wood", "fur", "paper", "dirt", "sand",
            "architecture", "hair", "general",
        ):
            with self.subTest(material_type=material_type):
                data = self._write_and_read(material_type)
                self.assertIn("emissive_scale", data)
                self.assertIsInstance(data["emissive_scale"], float)

    def test_vertex_color_lum_mult_present_for_all_material_types(self) -> None:
        for material_type in (
            "skin", "plants", "metal", "glass", "snow", "terrain", "cloth",
            "leather", "stone", "wood", "fur", "paper", "dirt", "sand",
            "architecture", "hair", "general",
        ):
            with self.subTest(material_type=material_type):
                data = self._write_and_read(material_type)
                self.assertIn("vertex_color_lum_mult", data)


class RmaosJsonGlowEnabledTests(unittest.TestCase):
    """Tests that write_rmaos_json_sidecar threads glow_enabled into the emissive flag."""

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_and_read(self, *, glow_enabled: bool, material_type: str = "general") -> dict:
        import json
        from pathlib import Path

        rmaos_path = Path(self._tmpdir) / "test_rmaos.dds"
        write_rmaos_json_sidecar(rmaos_path, parallax_enabled=False, material_type=material_type, glow_enabled=glow_enabled)
        json_path = Path(self._tmpdir) / "PBRNifPatcher" / f"{rmaos_path.stem}.json"
        with open(json_path) as f:
            raw = json.load(f)
        return raw[0] if isinstance(raw, list) else raw

    def test_glow_disabled_sets_emissive_false(self) -> None:
        data = self._write_and_read(glow_enabled=False)
        self.assertFalse(bool(data["emissive"]))

    def test_glow_enabled_sets_emissive_true(self) -> None:
        data = self._write_and_read(glow_enabled=True)
        self.assertTrue(bool(data["emissive"]))

    def test_glow_enabled_emissive_scale_inherits_material_override(self) -> None:
        glass_data = self._write_and_read(glow_enabled=True, material_type="glass")
        general_data = self._write_and_read(glow_enabled=True, material_type="general")
        self.assertGreater(glass_data["emissive_scale"], general_data["emissive_scale"])

    def test_glow_disabled_emissive_scale_still_present(self) -> None:
        data = self._write_and_read(glow_enabled=False)
        self.assertIn("emissive_scale", data)


class ClassifyHairMaterialTypeTests(unittest.TestCase):
    """Tests that hair-related texture paths classify as 'hair'."""

    def test_hair_token_classifies_as_hair(self) -> None:
        result = classify_material_type(Path("textures/actors/character/hair/hairfemalenord.dds"))
        self.assertEqual(result, "hair")

    def test_eyebrow_classifies_as_hair(self) -> None:
        result = classify_material_type(Path("textures/actors/character/eyebrow.dds"))
        self.assertEqual(result, "hair")

    def test_beard_classifies_as_hair(self) -> None:
        result = classify_material_type(Path("textures/actors/character/beard.dds"))
        self.assertEqual(result, "hair")

    def test_lash_classifies_as_hair(self) -> None:
        result = classify_material_type(Path("textures/actors/character/eyelash.dds"))
        self.assertEqual(result, "hair")





class TerrainParallaxStrengthWarningTests(unittest.TestCase):
    """Tests for the terrain-parallax high-strength warning added to get_generation_warnings."""

    def _base_kwargs(self) -> dict:
        return {
            "include_glow": False,
            "include_environment_mask": False,
            "env_mask_mode": "standard",
            "env_mask_strength": 1.2,
            "include_parallax": True,
            "include_complex": False,
        }

    def test_terrain_parallax_below_threshold_no_high_strength_warning(self) -> None:
        warnings = get_generation_warnings(
            "terrain",
            parallax_strength=0.9,
            **self._base_kwargs(),
        )
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_high_strength_terrain", warning_ids)

    def test_terrain_parallax_at_threshold_no_high_strength_warning(self) -> None:
        warnings = get_generation_warnings(
            "terrain",
            parallax_strength=1.0,
            **self._base_kwargs(),
        )
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_high_strength_terrain", warning_ids)

    def test_terrain_parallax_above_threshold_raises_warning(self) -> None:
        warnings = get_generation_warnings(
            "terrain",
            parallax_strength=1.5,
            **self._base_kwargs(),
        )
        warning_ids = [w[0] for w in warnings]
        self.assertIn("parallax_high_strength_terrain", warning_ids)

    def test_terrain_parallax_high_strength_warning_includes_value(self) -> None:
        warnings = get_generation_warnings(
            "terrain",
            parallax_strength=2.0,
            **self._base_kwargs(),
        )
        messages = {w[0]: w[1] for w in warnings}
        self.assertIn("parallax_high_strength_terrain", messages)
        self.assertIn("2.00", messages["parallax_high_strength_terrain"])

    def test_terrain_parallax_still_emits_shimmer_warning_alongside_high_strength(self) -> None:
        warnings = get_generation_warnings(
            "terrain",
            parallax_strength=1.5,
            **self._base_kwargs(),
        )
        warning_ids = [w[0] for w in warnings]
        self.assertIn("parallax_landscape_shimmer", warning_ids)
        self.assertIn("parallax_high_strength_terrain", warning_ids)

    def test_terrain_parallax_disabled_no_high_strength_warning(self) -> None:
        kwargs = dict(self._base_kwargs())
        kwargs["include_parallax"] = False
        warnings = get_generation_warnings(
            "terrain",
            parallax_strength=2.5,
            **kwargs,
        )
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_high_strength_terrain", warning_ids)

    def test_non_terrain_material_no_terrain_high_strength_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            parallax_strength=3.0,
            **self._base_kwargs(),
        )
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_high_strength_terrain", warning_ids)


# ---------------------------------------------------------------------------
# Glow map mipmap pre-filter
# ---------------------------------------------------------------------------


class GlowMipmapPrefilterTests(unittest.TestCase):
    """Tests for the glow-map branch added to _prefilter_for_mipmap_stability."""

    def _solid_l(self, value: int = 200, size: tuple[int, int] = (64, 64)) -> Image.Image:
        return Image.new("L", size, value)

    def _solid_rgb(self, value: tuple[int, int, int] = (200, 150, 50), size: tuple[int, int] = (64, 64)) -> Image.Image:
        return Image.new("RGB", size, value)

    def test_glow_l_mode_preserved_size_and_mode(self) -> None:
        src = self._solid_l()
        result = _prefilter_for_mipmap_stability(src, "glow")
        self.assertEqual(result.size, src.size)
        self.assertEqual(result.mode, "L")

    def test_glow_rgb_mode_preserved(self) -> None:
        src = self._solid_rgb()
        result = _prefilter_for_mipmap_stability(src, "glow")
        self.assertEqual(result.size, src.size)
        self.assertEqual(result.mode, "RGB")

    def test_glow_rgba_mode_preserved(self) -> None:
        src = Image.new("RGBA", (64, 64), (200, 150, 50, 255))
        result = _prefilter_for_mipmap_stability(src, "glow")
        self.assertEqual(result.size, src.size)
        self.assertEqual(result.mode, "RGBA")

    def test_glow_flat_image_value_unchanged(self) -> None:
        src = self._solid_l(200)
        result = _prefilter_for_mipmap_stability(src, "glow")
        pixels = list(result.getdata())
        self.assertTrue(all(abs(p - 200) <= 2 for p in pixels))

    def test_glow_noisy_image_is_smoothed(self) -> None:
        import random
        rng = random.Random(42)
        noisy = Image.new("L", (64, 64))
        pixels = [rng.randint(0, 255) for _ in range(64 * 64)]
        noisy.putdata(pixels)
        result = _prefilter_for_mipmap_stability(noisy, "glow")
        orig_std = float(np.std(np.array(noisy, dtype=np.float32)))
        result_std = float(np.std(np.array(result, dtype=np.float32)))
        self.assertLess(result_std, orig_std)

    def test_unknown_mode_glow_passthrough(self) -> None:
        src = Image.new("P", (64, 64))
        result = _prefilter_for_mipmap_stability(src, "glow")
        self.assertIs(result, src)

    def test_non_glow_kind_passthrough(self) -> None:
        src = self._solid_l()
        result = _prefilter_for_mipmap_stability(src, "diffuse")
        self.assertIs(result, src)

    def test_glow_kind_is_applied_not_passthrough(self) -> None:
        import random
        rng = random.Random(7)
        noisy = Image.new("L", (64, 64))
        noisy.putdata([rng.randint(0, 255) for _ in range(64 * 64)])
        result = _prefilter_for_mipmap_stability(noisy, "glow")
        self.assertIsNot(result, noisy)


class AoRoughnessPathTests(unittest.TestCase):
    def test_build_ao_output_path_uses_ao_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            ao_path = build_ao_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
            )

            self.assertEqual(ao_path.name, "brick_ao.dds")

    def test_build_ao_output_path_places_file_in_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "subdir" / "stone.dds"
            output_dir = temp_path / "out"

            ao_path = build_ao_output_path(input_path=input_path, output_dir=output_dir)

            self.assertEqual(ao_path.parent, output_dir)

    def test_build_roughness_output_path_uses_rough_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            rough_path = build_roughness_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
            )

            self.assertEqual(rough_path.name, "brick_rough.dds")

    def test_build_roughness_output_path_places_file_in_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "subdir" / "stone.dds"
            output_dir = temp_path / "out"

            rough_path = build_roughness_output_path(input_path=input_path, output_dir=output_dir)

            self.assertEqual(rough_path.parent, output_dir)


class AoRoughnessRunWithOptionsTests(unittest.TestCase):
    def test_run_with_options_generates_ao_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                    include_ao=True,
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.args[1].name, "brick_ao.dds")

    def test_run_with_options_generates_roughness_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                    include_roughness=True,
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.args[1].name, "brick_rough.dds")


class NormalizeGuiStateAoRoughnessTests(unittest.TestCase):
    def test_normalize_gui_state_includes_ao_roughness_defaults(self) -> None:
        normalized = _normalize_gui_state({})
        self.assertIn("include_ao", normalized)
        self.assertIn("include_roughness", normalized)
        self.assertIn("auto_ao", normalized)
        self.assertIn("auto_roughness", normalized)
        self.assertIn("ao_strength", normalized)
        self.assertIn("roughness_strength", normalized)

    def test_normalize_gui_state_coerces_ao_roughness_booleans(self) -> None:
        normalized = _normalize_gui_state({"include_ao": 1, "include_roughness": 0, "auto_ao": 1, "auto_roughness": 0})
        self.assertIs(normalized["include_ao"], True)
        self.assertIs(normalized["include_roughness"], False)
        self.assertIs(normalized["auto_ao"], True)
        self.assertIs(normalized["auto_roughness"], False)

    def test_normalize_gui_state_coerces_ao_roughness_strengths(self) -> None:
        normalized = _normalize_gui_state({"ao_strength": "2.5", "roughness_strength": "0.8"})
        self.assertAlmostEqual(float(normalized["ao_strength"]), 2.5)
        self.assertAlmostEqual(float(normalized["roughness_strength"]), 0.8)


class RecommendGenerationSettingsAoRoughnessTests(unittest.TestCase):
    def test_recommend_generation_settings_includes_ao_roughness_keys(self) -> None:
        image = _sample_image()
        result = recommend_generation_settings(image)
        self.assertIn("ao_strength", result)
        self.assertIn("roughness_strength", result)

    def test_recommend_generation_settings_ao_roughness_in_valid_range(self) -> None:
        image = _sample_image()
        result = recommend_generation_settings(image)
        ao_strength = float(result["ao_strength"])
        roughness_strength = float(result["roughness_strength"])
        self.assertGreater(ao_strength, 0.0)
        self.assertLessEqual(ao_strength, 8.0)
        self.assertGreater(roughness_strength, 0.0)
        self.assertLessEqual(roughness_strength, 8.0)


# ---------------------------------------------------------------------------
# Per-material parallax override (parallax vs displacement_scale distinction)
# ---------------------------------------------------------------------------


class RmaosJsonParallaxVsDisplacementTests(unittest.TestCase):
    """Tests that parallax (POM flag) and displacement_scale are independent controls.

    CS TruePBR distinguishes:
    - parallax (bool): whether the POM shader feature is enabled on the mesh.
    - displacement_scale (float): height/depth multiplier; has no effect unless parallax=True.

    Materials whose geometry makes POM harmful or irrelevant force parallax=False regardless
    of the parallax_enabled argument passed to write_rmaos_json_sidecar.
    """

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_and_read(self, material_type: str, parallax_enabled: bool = True) -> dict:
        import json
        from pathlib import Path

        rmaos_path = Path(self._tmpdir) / f"test_{material_type}_rmaos.dds"
        write_rmaos_json_sidecar(rmaos_path, parallax_enabled=parallax_enabled, material_type=material_type)
        json_path = Path(self._tmpdir) / "PBRNifPatcher" / f"{rmaos_path.stem}.json"
        with open(json_path) as f:
            raw = json.load(f)
        return raw[0] if isinstance(raw, list) else raw

    # --- materials that always disable parallax regardless of parallax_enabled ---

    def test_skin_forces_parallax_false_even_when_requested(self) -> None:
        data = self._write_and_read("skin", parallax_enabled=True)
        self.assertFalse(bool(data["parallax"]))

    def test_plants_forces_parallax_false_even_when_requested(self) -> None:
        data = self._write_and_read("plants", parallax_enabled=True)
        self.assertFalse(bool(data["parallax"]))

    def test_hair_forces_parallax_false_even_when_requested(self) -> None:
        data = self._write_and_read("hair", parallax_enabled=True)
        self.assertFalse(bool(data["parallax"]))

    def test_terrain_forces_parallax_false_even_when_requested(self) -> None:
        """Terrain CS TruePBR parallax is driven by PBRTextureSets JSON, not PBRNifPatcher."""
        data = self._write_and_read("terrain", parallax_enabled=True)
        self.assertFalse(bool(data["parallax"]))

    # --- displacement_scale is present and meaningful even when parallax is False ---

    def test_terrain_has_displacement_scale_when_parallax_disabled(self) -> None:
        data = self._write_and_read("terrain", parallax_enabled=True)
        self.assertFalse(bool(data["parallax"]))
        self.assertGreater(data["displacement_scale"], 0.0)

    def test_skin_has_zero_displacement_and_parallax_false(self) -> None:
        data = self._write_and_read("skin", parallax_enabled=True)
        self.assertFalse(bool(data["parallax"]))
        self.assertAlmostEqual(data["displacement_scale"], 0.0, places=3)

    def test_hair_has_zero_displacement_and_parallax_false(self) -> None:
        data = self._write_and_read("hair", parallax_enabled=True)
        self.assertFalse(bool(data["parallax"]))
        self.assertAlmostEqual(data["displacement_scale"], 0.0, places=3)

    # --- materials without a parallax override honour the caller's flag ---

    def test_stone_honours_parallax_enabled_true(self) -> None:
        data = self._write_and_read("stone", parallax_enabled=True)
        self.assertTrue(bool(data["parallax"]))

    def test_stone_honours_parallax_enabled_false(self) -> None:
        data = self._write_and_read("stone", parallax_enabled=False)
        self.assertFalse(bool(data["parallax"]))

    def test_metal_honours_parallax_enabled_true(self) -> None:
        data = self._write_and_read("metal", parallax_enabled=True)
        self.assertTrue(bool(data["parallax"]))

    def test_general_honours_parallax_enabled_true(self) -> None:
        data = self._write_and_read("general", parallax_enabled=True)
        self.assertTrue(bool(data["parallax"]))

    def test_general_honours_parallax_enabled_false(self) -> None:
        data = self._write_and_read("general", parallax_enabled=False)
        self.assertFalse(bool(data["parallax"]))

    # --- parallax and displacement_scale are both always present in the JSON ---

    def test_both_keys_present_for_all_materials(self) -> None:
        for material_type in (
            "skin", "plants", "metal", "glass", "snow", "terrain", "cloth",
            "leather", "stone", "wood", "fur", "paper", "dirt", "sand",
            "architecture", "hair", "general",
        ):
            with self.subTest(material_type=material_type):
                data = self._write_and_read(material_type)
                self.assertIn("parallax", data)
                self.assertIn("displacement_scale", data)
                self.assertIsInstance(data["parallax"], bool)
                self.assertIsInstance(data["displacement_scale"], float)

    # --- texture key is present and non-empty (identifier, not a type label) ---

    def test_texture_key_is_present_and_non_empty(self) -> None:
        data = self._write_and_read("stone")
        self.assertIn("texture", data)
        self.assertIsInstance(data["texture"], str)
        self.assertTrue(len(data["texture"]) > 0)


class TestPackagedStartupErrorReporting(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.log = self.home / ".skyrim_texture_generator_startup_error.log"

    def test_frozen_windowed_errors_append_original_traceback_and_guidance(self) -> None:
        startup_error = RuntimeError("GUI could not start")
        startup_error.__cause__ = ValueError("original Tcl initialization detail")
        errors = [
            startup_error, OSError("startup resource unavailable"),
            RuntimeError("GUI dependencies are unavailable in this environment."),
        ]
        for error in errors:
            with mock.patch("generate_textures.main", side_effect=error), \
                 mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "stderr", None), \
                 mock.patch("generate_textures.Path.home", return_value=self.home):
                self.assertEqual(_run_cli(), 1)
        content = self.log.read_text(encoding="utf-8")
        self.assertEqual(content.count("] Error:"), 3)
        self.assertIn("Traceback (most recent call last)", content)
        self.assertIn("ValueError: original Tcl initialization detail", content)
        self.assertIn("RuntimeError: GUI could not start", content)
        self.assertIn("OSError: startup resource unavailable", content)
        self.assertIn("Install tkinter/PySide6 support", content)

    def test_frozen_console_errors_use_stderr_without_creating_log(self) -> None:
        stderr = io.StringIO()
        with mock.patch("generate_textures.main", side_effect=RuntimeError("console detail")), \
             mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "stderr", stderr), \
             mock.patch("generate_textures.Path.home", return_value=self.home):
            self.assertEqual(_run_cli(), 1)
        self.assertIn("Error: console detail", stderr.getvalue())
        self.assertFalse(self.log.exists())

    def test_non_frozen_missing_stderr_does_not_create_packaged_log(self) -> None:
        with mock.patch("generate_textures.main", side_effect=RuntimeError("source detail")), \
             mock.patch.object(sys, "frozen", False, create=True), \
             mock.patch.object(sys, "stderr", None), \
             mock.patch.object(sys, "stdout", io.StringIO()), \
             mock.patch("generate_textures.Path.home", return_value=self.home):
            self.assertEqual(_run_cli(), 1)
        self.assertFalse(self.log.exists())

    def test_unwritable_packaged_error_log_does_not_mask_original_failure(self) -> None:
        with mock.patch("generate_textures.main", side_effect=RuntimeError("startup failed")), \
             mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "stderr", None), \
             mock.patch("generate_textures.Path.home", return_value=self.home), \
             mock.patch.object(Path, "open", side_effect=PermissionError("Home is read-only")):
            self.assertEqual(_run_cli(), 1)
        self.assertFalse(self.log.exists())


class TestQtNifEditorRunner(unittest.TestCase):
    def setUp(self) -> None:
        from tests.test_nif_patcher import _build_minimal_nif
        self.build_nif = _build_minimal_nif
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_nif(self, name="mesh.nif", **kwargs):
        path = self.root / name
        path.write_bytes(self.build_nif(**kwargs))
        return path

    def options(self, **kwargs):
        from generate_textures import NifPatchOptions
        return NifPatchOptions(**kwargs)

    def run_operation(self, paths, options, **kwargs):
        from generate_textures import run_nif_editor_operation
        return run_nif_editor_operation(paths, options, **kwargs)

    def test_skyrim_dry_run_write_type3_slots_and_restore(self) -> None:
        from nif_patcher import scan_nif
        path = self.make_nif(texture_paths=["textures\\stone.dds", "textures\\stone_n.dds"] + [""] * 7)
        original = path.read_bytes()
        options = self.options(
            enable_parallax=True, force_shader_type_3=True, parallax_scale=2.0,
            parallax_texture_path="textures\\stone_p.dds", diffuse_texture_path="textures\\new.dds",
            clear_normal_texture_path=True, backup=False, dry_run=True,
        )
        self.assertEqual(self.run_operation([path], options)["success"], 1)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(path.with_suffix(".nif.bak").exists())
        options.dry_run = False
        summary = self.run_operation([path], options)
        self.assertEqual(summary["failed"], 0, summary)
        self.assertEqual(path.with_suffix(".nif.bak").read_bytes(), original)
        info = scan_nif(path)[0]
        self.assertEqual(info.shader_type, 3)
        self.assertAlmostEqual(info.parallax_scale, 2.0)
        self.assertEqual(info.texture_paths[0], "textures\\new.dds")
        self.assertEqual(info.texture_paths.get(1, ""), "")
        patched = path.read_bytes()
        self.run_operation([path], self.options(dry_run=True), operation="restore")
        self.assertEqual(path.read_bytes(), patched)
        self.assertEqual(self.run_operation([path], self.options(), operation="restore")["success"], 1)
        self.assertEqual(path.read_bytes(), original)

    def test_compare_operation_reports_matching_backup_without_writing(self) -> None:
        path = self.make_nif()
        original = path.read_bytes()
        path.with_suffix(".nif.bak").write_bytes(original)

        summary = self.run_operation([path], self.options(dry_run=False), operation="compare")

        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["operation"], "compare")
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["warnings"], 0)
        self.assertEqual(summary["results"][0]["status"], "OK")
        self.assertIn("restore would be a no-op", summary["results"][0]["message"])
        self.assertEqual(path.read_bytes(), original)

    def test_tristate_keep_enable_disable_and_all_texture_slots(self) -> None:
        from nif_patcher import scan_nif
        path = self.make_nif()
        options = build_nif_patch_options_for_nif_editor(
            enable_parallax=True, enable_pom=True, enable_env_mapping=True,
            enable_glow_map=True, enable_pbr=True, parallax_scale=1.5, force_shader_type_3=True,
            **{f"{slot}_texture_path": f"textures\\{slot}.dds" for slot in
               ("diffuse", "normal", "parallax", "glow", "env_mask", "cubemap")},
        )
        summary = self.run_operation([path], options)
        self.assertEqual(summary["success"], 1, summary)
        info = scan_nif(path)[0]
        self.assertTrue(info.has_parallax_flag and info.has_pom_flag and info.has_env_mapping_flag and info.has_glow_map_flag)
        before = path.read_bytes()
        self.run_operation([path], self.options())
        self.assertEqual(path.read_bytes(), before)
        options = self.options(**{f"disable_{flag}": True for flag in
                                  ("parallax", "pom", "env_mapping", "glow_map", "pbr")},
                               **{f"clear_{slot}_texture_path": True for slot in
                                  ("diffuse", "normal", "parallax", "glow", "env_mask", "cubemap")})
        summary = self.run_operation([path], options)
        self.assertEqual(summary["success"], 1, summary)
        info = scan_nif(path)[0]
        self.assertFalse(info.has_parallax_flag or info.has_pom_flag or info.has_env_mapping_flag or info.has_glow_map_flag)
        self.assertEqual(info.flags2 & (1 << 31), 0)
        self.assertTrue(all(info.texture_paths.get(slot, "") == "" for slot in range(6)))

    def test_fallout_opt_in_operation_gates_write_and_restore(self) -> None:
        from tests.test_nif_patcher import _rewrite_user_version
        from nif_patcher import scan_nif
        path = self.make_nif(user_ver2=130, shader_layout="real", shader_type=3, parallax_scale=1.0)
        _rewrite_user_version(path, 11)
        original = path.read_bytes()
        options = self.options(
            target_game="fallout", enable_parallax=True, parallax_scale=2.0,
            parallax_texture_path="textures\\fallout_p.dds",
        )
        for dry_run in (True, False):
            options.dry_run = dry_run
            self.assertEqual(self.run_operation([path], options)["failed"], 1)
            self.assertEqual(path.read_bytes(), original)
        options.experimental_fallout_write = True
        self.assertEqual(self.run_operation([path], options)["failed"], 1)
        self.assertEqual(path.read_bytes(), original)
        options.fallout_allow_parallax_scale = True
        options.dry_run = True
        self.assertEqual(self.run_operation([path], options)["success"], 1)
        self.assertEqual(path.read_bytes(), original)
        options.dry_run = False
        summary = self.run_operation([path], options)
        self.assertEqual(summary["success"], 1, summary)
        self.assertAlmostEqual(scan_nif(path)[0].parallax_scale, 2.0)
        self.assertEqual(path.with_suffix(".nif.bak").read_bytes(), original)
        options.force_shader_type_3 = True
        self.assertEqual(self.run_operation([path], options)["failed"], 1)
        self.run_operation([path], self.options(), operation="restore")
        self.assertEqual(path.read_bytes(), original)

    def test_safe_remediation_dry_run_and_backup(self) -> None:
        from nif_patcher import scan_nif
        path = self.make_nif(texture_paths=["textures\\stone.dds", "", "textures\\stone_g.dds",
                                                       "textures\\stone_p.dds", "", "textures\\stone_m.dds"] + [""] * 3)
        original = path.read_bytes()
        summary = self.run_operation([path], self.options(dry_run=True), operation="remediate")
        self.assertEqual(summary["success"], 1, summary)
        self.assertEqual(path.read_bytes(), original)
        summary = self.run_operation([path], self.options(backup=False), operation="remediate")
        self.assertEqual(summary["success"], 1, summary)
        self.assertTrue(scan_nif(path)[0].has_parallax_flag)
        self.assertEqual(path.with_suffix(".nif.bak").read_bytes(), original)

    def test_backup_failures_refuse_actual_runner_writes_and_continue(self) -> None:
        denied = self.make_nif("denied.nif")
        directory = self.make_nif("directory.nif")
        corrupt = self.make_nif("corrupt.nif")
        good = self.make_nif("good.nif")
        originals = {path: path.read_bytes() for path in (denied, directory, corrupt, good)}
        directory.with_suffix(".nif.bak").mkdir()
        corrupt.with_suffix(".nif.bak").write_bytes(b"invalid backup")
        denied_backup = denied.with_suffix(".nif.bak")
        real_open = Path.open

        def failing_open(path, *args, **kwargs):
            if path == denied_backup:
                raise PermissionError("Backup cannot be created")
            return real_open(path, *args, **kwargs)

        options = self.options(diffuse_texture_path="textures\\edited.dds", backup=False, dry_run=True)
        with mock.patch.object(Path, "open", failing_open):
            dry_summary = self.run_operation([denied, directory, corrupt, good], options)
            self.assertEqual(dry_summary["success"], 4, dry_summary)
            self.assertTrue(all(path.read_bytes() == data for path, data in originals.items()))
            options.dry_run = False
            summary = self.run_operation([denied, directory, corrupt, good], options)
        self.assertEqual((summary["failed"], summary["success"]), (3, 1), summary)
        for path in (denied, directory, corrupt):
            self.assertEqual(path.read_bytes(), originals[path])
        self.assertTrue(directory.with_suffix(".nif.bak").is_dir())
        self.assertEqual(corrupt.with_suffix(".nif.bak").read_bytes(), b"invalid backup")
        self.assertFalse(denied_backup.exists())
        self.assertNotEqual(good.read_bytes(), originals[good])
        self.assertEqual(good.with_suffix(".nif.bak").read_bytes(), originals[good])

    def test_per_file_failure_continue_and_between_file_cancellation(self) -> None:
        bad = self.root / "broken.nif"
        bad.write_bytes(b"broken")
        good = self.make_nif("good.nif")
        summary = self.run_operation([bad, good], self.options(enable_glow_map=True))
        self.assertEqual((summary["failed"], summary["success"]), (1, 1), summary)
        untouched = self.make_nif("untouched.nif")
        original = untouched.read_bytes()
        rows = []
        summary = self.run_operation(
            [good, untouched], self.options(enable_pbr=True),
            is_cancelled=lambda: bool(rows), on_result=rows.append,
        )
        self.assertTrue(summary["cancelled"])
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(untouched.read_bytes(), original)
        self.assertEqual(self.run_operation([untouched], self.options(), operation="restore")["skipped"], 1)
        with self.assertRaises(ValueError):
            self.run_operation([], self.options(), operation="invalid")


class TestQtNifEditorEventLoop(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import generate_textures as gt
        if not gt.QT_GUI_AVAILABLE:
            raise unittest.SkipTest("Optional PySide6 unavailable")
        cls.qt = gt
        cls.app = gt.QtWidgets.QApplication.instance() or gt.QtWidgets.QApplication([])
        gt._qt_application = cls.app

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.messages = []
        old_handler = self.qt.QtCore.qInstallMessageHandler(
            lambda kind, context, message: self.messages.append(message)
        )
        self.addCleanup(lambda: self.qt.QtCore.qInstallMessageHandler(old_handler))

    def widget(self, window, name):
        widget = window.findChild(self.qt.QtCore.QObject, name)
        self.assertIsNotNone(widget, name)
        return widget

    def run_window(self, scenario, *, load_state=None, save_side_effect=None, extra_patches=()):
        from contextlib import ExitStack, redirect_stderr
        failures = []
        windows = []
        stderr = io.StringIO()
        started = time.monotonic()
        finished = False

        def ready(window):
            windows.append(window)
            timer = self.qt.QtCore.QTimer(window)
            timer.setInterval(5)

            def tick():
                nonlocal finished
                try:
                    if failures:
                        finished = True
                    if not finished:
                        finished = bool(scenario(window))
                    if time.monotonic() - started > 30:
                        raise AssertionError(
                            f"Qt scenario timed out: {window.property('generation_summary')}; "
                            f"editor={window.property('nif_editor_summary')}"
                        )
                except BaseException as exc:
                    failures.append(exc)
                    finished = True
                    window.close()
                if finished and self.widget(window, "generate").isEnabled():
                    timer.stop()
                    window.close()
                    self.app.quit()

            timer.timeout.connect(tick)
            timer.start()

        def unexpected_dialog(*args):
            failures.append(AssertionError(f"Unexpected Qt dialog: {args[1:]}"))
            return self.qt.QtWidgets.QMessageBox.StandardButton.Ok

        load_state_value = dict(self.qt._GUI_STATE_DEFAULTS) if load_state is None else load_state
        with ExitStack() as stack, redirect_stderr(stderr):
            stack.enter_context(mock.patch("generate_textures.load_gui_state", return_value=load_state_value))
            stack.enter_context(mock.patch("generate_textures.save_gui_state", side_effect=save_side_effect))
            stack.enter_context(mock.patch.object(self.qt.QtWidgets.QMessageBox, "critical", side_effect=unexpected_dialog))
            stack.enter_context(mock.patch.object(self.qt.QtWidgets.QMessageBox, "warning", side_effect=unexpected_dialog))
            for patcher in extra_patches:
                stack.enter_context(patcher)
            self.qt._launch_qt_migration_gui(on_ready=ready)
        for window in windows:
            window.deleteLater()
        self.qt.QtCore.QCoreApplication.sendPostedEvents(None, self.qt.QtCore.QEvent.Type.DeferredDelete)
        self.app.processEvents()
        if failures:
            raise failures[0]
        self.assertEqual(stderr.getvalue(), "", "Qt callback errors must not be silently ignored")
        bad_messages = [message for message in self.messages if any(
            term in message.lower() for term in ("different thread", "cannot create children", "qthread: destroyed", "callback", "exception")
        )]
        self.assertEqual(bad_messages, [])

    def test_actual_editor_dry_run_write_confirmation_scan_and_restore(self) -> None:
        from tests.test_nif_patcher import _build_minimal_nif
        path = self.root / "mesh.nif"
        original = _build_minimal_nif()
        path.write_bytes(original)
        path.with_suffix(".nif.bak").write_bytes(original)
        state = {"step": 0}
        confirmations = []

        def confirm(*args):
            confirmations.append(args[-1])
            return self.qt.QtWidgets.QMessageBox.StandardButton.No if len(confirmations) == 1 else self.qt.QtWidgets.QMessageBox.StandardButton.Yes

        def scenario(window):
            w = lambda name: self.widget(window, name)
            step = state["step"]
            if step == 0:
                self.assertTrue(w("nif_dry_run").isChecked())
                banner = w("nif_editor_safety_banner").text().lower()
                self.assertIn("dry-run is on", banner)
                self.assertIn("compare against .bak first", banner)
                self.assertIn("fallout writes stay off unless you opt in", banner)
                self.assertIn("experimental fallout writes", w("nif_experimental").toolTip().lower())
                self.assertIn("dry-run", w("nif_dry_run").toolTip().lower())
                self.assertIn("experimental fallout parallax scale", w("nif_fallout_allow_parallax_scale").toolTip().lower())
                self.assertIn("qt migration preview scope", self.widget(window, "qt_migration_status").toPlainText().lower())
                self.assertIsNotNone(self.widget(window, "generation_log"))
                self.assertIsNotNone(self.widget(window, "generation_log_copy"))
                self.assertIsNotNone(self.widget(window, "generation_log_export"))
                self.assertIn("dry-run first", w("nif_editor_patch").text().lower())
                self.assertIn("read-only", w("nif_editor_compare").text().lower())
                self.assertIn("restore", w("nif_editor_restore").text().lower())
                for theme_mode, scale, size in (
                    ("light", "0.9", (640, 480)),
                    ("dark", "1.5", (1100, 760)),
                    ("high_contrast", "1.15", (800, 600)),
                ):
                    w("theme_mode").setCurrentText(_THEME_MODE_LABELS[theme_mode])
                    self.assertEqual(w("theme").isChecked(), theme_mode != "light")
                    w("ui_scale").setCurrentText(scale)
                    window.resize(*size)
                    self.app.processEvents()
                w("nif_scan_path").setText(str(path))
                w("nif_texture_diffuse").setText("textures\\edited.dds")
                w("nif_editor_compare").click()
                self.assertFalse(w("generate").isEnabled())
                self.assertFalse(w("nif_scan_start").isEnabled())
                state["step"] = 0.5
            elif step == 0.5 and w("generate").isEnabled():
                self.assertEqual(window.property("nif_editor_summary")["operation"], "compare")
                self.assertEqual(window.property("nif_editor_summary")["results"][0]["status"], "OK")
                self.assertIn("restore would be a no-op", w("nif_editor_results").toPlainText())
                w("nif_editor_patch").click()
                self.assertFalse(w("generate").isEnabled())
                self.assertFalse(w("nif_scan_start").isEnabled())
                state["step"] = 1
            elif step == 1 and w("generate").isEnabled():
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(window.property("nif_editor_summary")["success"], 1)
                w("nif_dry_run").setChecked(False)
                w("nif_editor_patch").click()
                self.assertEqual(path.read_bytes(), original)
                self.assertTrue(w("generate").isEnabled())
                w("nif_editor_patch").click()
                state["step"] = 2
            elif step == 2 and w("generate").isEnabled():
                self.assertNotEqual(path.read_bytes(), original)
                self.assertEqual(path.with_suffix(".nif.bak").read_bytes(), original)
                w("nif_scan_start").click()
                state["step"] = 3
            elif step == 3 and w("generate").isEnabled():
                self.assertEqual(w("nif_scan_results").topLevelItemCount(), 1)
                w("nif_scan_results").setCurrentItem(w("nif_scan_results").topLevelItem(0))
                w("nif_scan_preview").click()
                self.assertFalse(w("generate").isEnabled())
                self.assertFalse(w("nif_editor_patch").isEnabled())
                state["step"] = 4
            elif step == 4 and w("generate").isEnabled():
                w("nif_editor_restore").click()
                state["step"] = 5
            elif step == 5 and w("generate").isEnabled():
                self.assertEqual(path.read_bytes(), original)
                self.assertTrue(all(value == self.qt.QtWidgets.QMessageBox.StandardButton.No for value in confirmations))
                return True
            return False

        with mock.patch.object(self.qt.QtWidgets.QMessageBox, "question", side_effect=confirm):
            self.run_window(scenario)

    def test_qt_detected_mod_hint_and_profile_restore(self) -> None:
        detected_mod_dir = self.root / "mods" / "Detected Mod" / "textures"
        detected_mod_dir.mkdir(parents=True)
        output_dir = self.root / "output"
        output_dir.mkdir()
        manager = "Mod Organizer 2"
        profile = "Main"
        profile_key = self.qt._profile_setup_memory_key(manager, profile)
        saved_state = dict(self.qt._GUI_STATE_DEFAULTS)
        saved_state.update(
            {
                "last_mod_manager": manager,
                "last_mod_profile": profile,
                "profile_setup_memory": {
                    profile_key: {
                        "render_profile": "truepbr",
                        "use_custom_output": True,
                        "output_target": str(output_dir),
                        "workflow_mode": "advanced",
                        "target_game": "fallout4",
                    }
                },
            }
        )
        saved_states = []
        context = self.qt.ModManagerContext(
            manager=manager,
            profile_name=profile,
            loaded_texture_dirs=(detected_mod_dir,),
        )

        def capture_state(state):
            saved_states.append(json.loads(json.dumps(state)))

        state = {"step": 0}

        def scenario(window):
            w = lambda name: self.widget(window, name)
            step = state["step"]
            if step == 0:
                self.assertTrue(w("detected_mod").isEnabled())
                self.assertTrue(w("restore_profile_setup").isEnabled())
                w("detected_mod").click()
                self.assertEqual(w("input_path").text(), str(detected_mod_dir))
                self.assertIn("Loaded detected mod texture folder", w("generation_status").text())
                state["step"] = 1
            elif step == 1:
                w("restore_profile_setup").click()
                state["step"] = 2
            elif step == 2 and w("generate").isEnabled():
                self.assertEqual(w("render_profile").currentText(), "truepbr")
                self.assertEqual(w("target_game").currentText(), "fallout4")
                self.assertEqual(w("workflow_mode").currentText(), self.qt._WORKFLOW_MODE_LABELS["advanced"])
                self.assertEqual(w("output_dir").text(), str(output_dir))
                self.assertIn("target game fallout4", w("generation_status").text())
                self.assertTrue(saved_states)
                restored_entry = saved_states[-1]["profile_setup_memory"][profile_key]
                self.assertEqual(restored_entry["render_profile"], "truepbr")
                self.assertEqual(restored_entry["output_target"], str(output_dir))
                self.assertEqual(restored_entry["workflow_mode"], "advanced")
                self.assertEqual(restored_entry["target_game"], "fallout4")
                return True
            return False

        self.run_window(
            scenario,
            load_state=saved_state,
            save_side_effect=capture_state,
            extra_patches=(mock.patch("generate_textures.detect_mod_manager_context", return_value=context),),
        )

    def test_qt_profile_setup_switcher_restores_selected_saved_setup(self) -> None:
        manager = "Mod Organizer 2"
        profile = "Main"
        current_key = self.qt._profile_setup_memory_key(manager, profile)
        alternate_key = self.qt._profile_setup_memory_key(manager, "Alt")
        saved_state = dict(self.qt._GUI_STATE_DEFAULTS)
        saved_state.update(
            {
                "last_mod_manager": manager,
                "last_mod_profile": profile,
                "profile_setup_memory": {
                    current_key: {
                        "render_profile": "truepbr",
                        "use_custom_output": True,
                        "output_target": "/tmp/output",
                        "workflow_mode": "advanced",
                        "target_game": "fallout4",
                    },
                    alternate_key: {
                        "render_profile": "community_shaders",
                        "use_custom_output": False,
                        "output_target": "",
                        "workflow_mode": "standard",
                        "target_game": "skyrim",
                    },
                },
            }
        )
        saved_states = []
        context = self.qt.ModManagerContext(manager=manager, profile_name=profile, loaded_texture_dirs=())
        state = {"step": 0}

        def capture_state(state_data):
            saved_states.append(json.loads(json.dumps(state_data)))

        def scenario(window):
            w = lambda name: self.widget(window, name)
            combo = w("profile_setup_choice")
            step = state["step"]
            if step == 0:
                self.assertGreaterEqual(combo.count(), 2)
                labels = [combo.itemText(i) for i in range(combo.count())]
                self.assertTrue(any("renderer Community Shaders" in label for label in labels))
                alternate_index = combo.findData(alternate_key)
                self.assertGreaterEqual(alternate_index, 0)
                combo.setCurrentIndex(alternate_index)
                self.assertTrue(w("restore_profile_setup").isEnabled())
                w("restore_profile_setup").click()
                state["step"] = 1
            elif step == 1 and w("generate").isEnabled():
                self.assertEqual(w("render_profile").currentText(), "community_shaders")
                self.assertEqual(w("workflow_mode").currentText(), self.qt._WORKFLOW_MODE_LABELS["standard"])
                self.assertEqual(w("target_game").currentText(), "skyrim")
                self.assertEqual(w("output_dir").text(), "")
                self.assertIn("Restored remembered setup for", w("generation_status").text())
                self.assertIn("Mod Organizer 2 / Alt", w("generation_status").text())
                self.assertTrue(saved_states)
                restored_entry = saved_states[-1]["profile_setup_memory"][current_key]
                self.assertEqual(restored_entry["render_profile"], "community_shaders")
                self.assertEqual(restored_entry["workflow_mode"], "standard")
                self.assertFalse(restored_entry["use_custom_output"])
                self.assertEqual(restored_entry["output_target"], "next to input")
                self.assertEqual(restored_entry["target_game"], "skyrim")
                return True
            return False

        self.run_window(
            scenario,
            load_state=saved_state,
            save_side_effect=capture_state,
            extra_patches=(mock.patch("generate_textures.detect_mod_manager_context", return_value=context),),
        )

    def test_qt_startup_quick_start_banner_applies_safe_preset_and_hides(self) -> None:
        saved_states = []
        state = {"step": 0}

        def capture_state(state_data):
            saved_states.append(json.loads(json.dumps(state_data)))

        def scenario(window):
            w = lambda name: self.widget(window, name)
            step = state["step"]
            if step == 0:
                self.assertTrue(w("startup_onboarding").isVisible())
                self.assertIn("If you're unsure", w("startup_onboarding_text").text())
                self.assertIn("Recommended next step:", w("startup_onboarding_guidance").text())
                self.assertIn("choose an input file or folder", w("startup_onboarding_guidance").text())
                self.assertIn("Step 1:", w("startup_onboarding_text").text())
                self.assertIn("Step 4:", w("startup_onboarding_text").text())
                self.assertIn("Step 5:", w("startup_onboarding_text").text())
                self.assertEqual(w("render_profile").currentText(), "custom")
                w("startup_onboarding_vanilla").click()
                self.app.processEvents()
                self.assertEqual(w("render_profile").currentText(), "vanilla")
                self.assertFalse(w("startup_onboarding").isVisible())
                self.assertTrue(w("startup_onboarding_show").isVisible())
                self.assertEqual(w("tabs").tabText(w("tabs").currentIndex()), "Generation")
                self.assertEqual(w("generation_status").text(), "Quick start applied: Vanilla.")
                state["step"] = 1
                return False
            if step == 1 and w("generate").isEnabled():
                self.assertFalse(w("startup_onboarding").isVisible())
                w("startup_onboarding_show").click()
                self.app.processEvents()
                self.assertTrue(w("startup_onboarding").isVisible())
                self.assertFalse(w("startup_onboarding_show").isVisible())
                self.assertEqual(w("generation_status").text(), "Quick start reopened.")
                state["step"] = 2
                return False
            if step == 2 and w("generate").isEnabled():
                self.assertTrue(saved_states)
                self.assertTrue(saved_states[-1]["show_startup_onboarding"])
                self.assertEqual(saved_states[-1]["render_profile"], "vanilla")
                return True
            return False

        self.run_window(scenario, save_side_effect=capture_state)

    def test_qt_startup_quick_start_suggested_button_uses_context(self) -> None:
        saved_states = []
        state = {"step": 0}
        context = ModManagerContext(manager="Mod Organizer 2", loaded_mods=("ENBSeries",))

        def capture_state(state_data):
            saved_states.append(json.loads(json.dumps(state_data)))

        def scenario(window):
            w = lambda name: self.widget(window, name)
            step = state["step"]
            if step == 0:
                self.assertTrue(w("startup_onboarding").isVisible())
                self.assertIn("If you're unsure", w("startup_onboarding_text").text())
                self.assertIn("Step 3:", w("startup_onboarding_text").text())
                self.assertIn("Step 4:", w("startup_onboarding_text").text())
                self.assertIn("Step 5:", w("startup_onboarding_text").text())
                w("startup_onboarding_suggested").click()
                self.app.processEvents()
                self.assertEqual(w("render_profile").currentText(), "enb")
                self.assertFalse(w("startup_onboarding").isVisible())
                self.assertEqual(w("generation_status").text(), "Quick start applied: ENB.")
                state["step"] = 1
                return False
            if step == 1 and w("generate").isEnabled():
                self.assertTrue(saved_states)
                self.assertFalse(saved_states[-1]["show_startup_onboarding"])
                self.assertEqual(saved_states[-1]["render_profile"], "enb")
                return True
            return False

        self.run_window(
            scenario,
            save_side_effect=capture_state,
            extra_patches=(mock.patch("generate_textures.detect_mod_manager_context", return_value=context),),
        )

    def test_qt_startup_quick_start_guided_button_restores_saved_setup_before_detected_folder(self) -> None:
        saved_states = []
        state = {"step": 0}
        manager = "Mod Organizer 2"
        profile = "Main"
        profile_key = self.qt._profile_setup_memory_key(manager, profile)
        context = ModManagerContext(
            manager=manager,
            profile_name=profile,
            loaded_texture_dirs=(self.root / "mods" / "Texture Pack" / "textures",),
        )
        saved_state = dict(self.qt._GUI_STATE_DEFAULTS)
        saved_state.update(
            {
                "last_mod_manager": manager,
                "last_mod_profile": profile,
                "profile_setup_memory": {
                    profile_key: {
                        "render_profile": "truepbr",
                        "use_custom_output": True,
                        "output_target": "/tmp/output",
                        "workflow_mode": "advanced",
                        "target_game": "fallout4",
                    }
                },
            }
        )

        def capture_state(state_data):
            saved_states.append(json.loads(json.dumps(state_data)))

        def scenario(window):
            w = lambda name: self.widget(window, name)
            step = state["step"]
            if step == 0:
                self.assertTrue(w("startup_onboarding").isVisible())
                self.assertIn("restore the remembered setup", w("startup_onboarding_guidance").text())
                w("startup_onboarding_guided").click()
                self.app.processEvents()
                self.assertEqual(w("render_profile").currentText(), "truepbr")
                self.assertEqual(w("target_game").currentText(), "fallout4")
                self.assertEqual(w("workflow_mode").currentText(), self.qt._WORKFLOW_MODE_LABELS["advanced"])
                self.assertEqual(w("output_dir").text(), "/tmp/output")
                self.assertIn("use the detected mod folder first", w("startup_onboarding_guidance").text())
                self.assertTrue(w("startup_onboarding").isVisible())
                state["step"] = 1
                return False
            if step == 1 and w("generate").isEnabled():
                self.assertTrue(saved_states)
                self.assertEqual(saved_states[-1]["render_profile"], "truepbr")
                self.assertEqual(saved_states[-1]["output_path"], "/tmp/output")
                return True
            return False

        self.run_window(
            scenario,
            load_state=saved_state,
            save_side_effect=capture_state,
            extra_patches=(mock.patch("generate_textures.detect_mod_manager_context", return_value=context),),
        )

    def test_actual_fallout_editor_opt_in_and_scale_gate(self) -> None:
        from tests.test_nif_patcher import _build_minimal_nif, _rewrite_user_version
        from nif_patcher import scan_nif
        path = self.root / "fallout.nif"
        path.write_bytes(_build_minimal_nif(user_ver2=130, shader_layout="real", shader_type=3, parallax_scale=1.0))
        _rewrite_user_version(path, 11)
        original = path.read_bytes()
        state = {"step": 0}

        def scenario(window):
            w = lambda name: self.widget(window, name)
            step = state["step"]
            if step == 0:
                w("nif_scan_path").setText(str(path))
                w("nif_profile").setCurrentText("fallout")
                w("nif_flag_parallax").setCurrentText("Enable")
                w("nif_scale_enabled").setChecked(True)
                w("nif_scale").setValue(2.0)
                self.assertFalse(w("nif_fallout_allow_parallax_scale").isEnabled())
                w("nif_editor_patch").click()
                state["step"] = 1
            elif step == 1 and w("generate").isEnabled():
                self.assertEqual(window.property("nif_editor_summary")["failed"], 1)
                self.assertEqual(path.read_bytes(), original)
                w("nif_experimental").setChecked(True)
                self.assertTrue(w("nif_fallout_allow_parallax_scale").isEnabled())
                w("nif_editor_patch").click()
                state["step"] = 2
            elif step == 2 and w("generate").isEnabled():
                self.assertEqual(window.property("nif_editor_summary")["failed"], 1)
                self.assertEqual(path.read_bytes(), original)
                w("nif_fallout_allow_parallax_scale").setChecked(True)
                w("nif_dry_run").setChecked(False)
                w("nif_editor_patch").click()
                state["step"] = 3
            elif step == 3 and w("generate").isEnabled():
                self.assertEqual(window.property("nif_editor_summary")["success"], 1)
                self.assertAlmostEqual(scan_nif(path)[0].parallax_scale, 2.0)
                self.assertEqual(path.with_suffix(".nif.bak").read_bytes(), original)
                return True
            return False

        with mock.patch.object(self.qt.QtWidgets.QMessageBox, "question", return_value=self.qt.QtWidgets.QMessageBox.StandardButton.Yes):
            self.run_window(scenario)

    def test_qt_nif_scan_handoff_prefills_renderer_and_warns_on_mismatch(self) -> None:
        from tests.test_nif_patcher import _build_minimal_nif

        path = self.root / "mods" / "enbseries" / "stone.nif"
        path.parent.mkdir(parents=True)
        path.write_bytes(_build_minimal_nif())
        saved_states = []
        state = {"step": 0}

        def capture_state(state_data):
            saved_states.append(json.loads(json.dumps(state_data)))

        def scenario(window):
            w = lambda name: self.widget(window, name)
            step = state["step"]
            if step == 0:
                w("nif_scan_path").setText(str(path))
                w("nif_scan_start").click()
                state["step"] = 1
                return False
            if step == 1 and w("generate").isEnabled():
                self.assertGreaterEqual(w("nif_scan_results").topLevelItemCount(), 1)
                w("nif_scan_results").setCurrentItem(w("nif_scan_results").topLevelItem(0))
                w("nif_scan_open_editor").click()
                state["step"] = 2
                return False
            if step == 2 and w("generate").isEnabled():
                self.assertEqual(w("tabs").tabText(w("tabs").currentIndex()), "NIF Editor")
                self.assertEqual(w("nif_scan_path").text(), str(path))
                self.assertEqual(w("nif_render_profile").currentText(), "enb")
                self.assertIn("safe patch path", w("nif_editor_guidance").text())
                self.assertIn("ENB POM", w("nif_editor_guidance").text())
                self.assertTrue(saved_states)
                self.assertEqual(saved_states[-1]["nif_render_profile"], "enb")
                w("nif_render_profile").setCurrentText("vanilla")
                self.app.processEvents()
                self.assertIn("Probably incompatible", w("nif_editor_option_warning").text())
                self.assertIn("ENB", w("nif_editor_option_warning").text())
                self.assertEqual(saved_states[-1]["nif_render_profile"], "vanilla")
                return True
            return False

        self.run_window(scenario, save_side_effect=capture_state)

    def test_actual_editor_close_cancels_folder_between_files(self) -> None:
        from tests.test_nif_patcher import _build_minimal_nif
        original = _build_minimal_nif()
        for index in range(6):
            (self.root / f"{index}.nif").write_bytes(original)
        state = {"step": 0}
        real_patch = self.qt.patch_nif

        def slow_patch(*args, **kwargs):
            time.sleep(0.05)
            return real_patch(*args, **kwargs)

        def scenario(window):
            w = lambda name: self.widget(window, name)
            if state["step"] == 0:
                w("nif_scan_mode").setCurrentIndex(1)
                w("nif_scan_path").setText(str(self.root))
                w("nif_texture_diffuse").setText("textures\\edited.dds")
                w("nif_editor_patch").click()
                self.assertTrue(w("nif_editor_cancel").isEnabled())
                self.assertFalse(window.close())
                self.assertTrue(window.isVisible())
                state["step"] = 1
            elif w("generate").isEnabled():
                summary = window.property("nif_editor_summary")
                self.assertTrue(summary["cancelled"])
                self.assertLess(summary["processed"], 6)
                self.assertTrue(all(path.read_bytes() == original for path in self.root.glob("*.nif")))
                return True
            return False

        with mock.patch("generate_textures.patch_nif", side_effect=slow_patch):
            self.run_window(scenario)

    def test_actual_six_file_generation_cancel_and_checkpoint_resume(self) -> None:
        sources = self.root / "source"
        outputs = self.root / "output"
        sources.mkdir()
        checkpoint = self.root / "checkpoint.json"
        for index in range(6):
            Image.new("RGB", (8, 8), (index * 30, 80, 120)).save(sources / f"stone{index}.dds")
        state = {"step": 0}
        real_generate = self.qt.run_with_options

        def slow_generate(*args, **kwargs):
            time.sleep(0.05)
            return real_generate(*args, **kwargs)

        def scenario(window):
            w = lambda name: self.widget(window, name)
            step = state["step"]
            if step == 0:
                w("input_path").setText(str(sources))
                w("output_dir").setText(str(outputs))
                w("checkpoint_path").setText(str(checkpoint))
                w("generate").click()
                self.assertFalse(w("nif_editor_patch").isEnabled())
                self.assertFalse(w("nif_scan_start").isEnabled())
                state["step"] = 1
            elif step == 1 and checkpoint.exists():
                w("cancel_generation").click()
                self.assertFalse(window.close())
                self.assertTrue(window.isVisible())
                state["step"] = 2
            elif step == 2 and w("generate").isEnabled():
                summary = window.property("generation_summary")
                self.assertTrue(summary["cancelled"], summary)
                self.assertGreater(summary["success"], 0)
                self.assertLess(summary["success"], 6)
                state["completed"] = summary["success"]
                state["checkpoint"] = json.loads(checkpoint.read_text())
                self.assertTrue(list(outputs.rglob("*.dds")))
                w("resume_mode").setCurrentText("resume")
                w("generate").click()
                state["step"] = 3
            elif step == 3 and w("generate").isEnabled():
                summary = window.property("generation_summary")
                self.assertFalse(summary["cancelled"])
                self.assertEqual(summary["failed"], 0, summary)
                self.assertEqual(summary["resumed_completed"], state["completed"])
                self.assertEqual(summary["success"] + summary["resumed_completed"], 6)
                self.assertEqual(len(json.loads(checkpoint.read_text())["completed_success_files"]), 6)
                return True
            return False

        with mock.patch("generate_textures.run_with_options", side_effect=slow_generate):
            self.run_window(scenario)

    def test_qt_dismissible_warning_dialog_remembers_and_skips_repeats(self) -> None:
        parent = self.qt.QtWidgets.QWidget()
        self.addCleanup(parent.deleteLater)
        dismissed_warnings: set[str] = set()
        persisted: list[list[str]] = []

        def persist() -> None:
            persisted.append(sorted(dismissed_warnings))

        with mock.patch.object(
            self.qt.QtWidgets.QDialog,
            "exec",
            return_value=self.qt.QtWidgets.QDialog.DialogCode.Accepted,
        ), mock.patch.object(self.qt.QtWidgets.QCheckBox, "isChecked", return_value=True):
            self.assertTrue(
                self.qt._show_qt_dismissible_warning_dialog(
                    parent,
                    "Generation Warning",
                    "glow_non_magical",
                    "Glow map enabled for a stone texture.",
                    dismissed_warnings,
                    persist,
                )
            )

        self.assertIn("glow_non_magical", dismissed_warnings)
        self.assertEqual(persisted, [["glow_non_magical"]])
        with mock.patch.object(
            self.qt.QtWidgets.QDialog,
            "exec",
            side_effect=AssertionError("dismissed warnings should not show the dialog again"),
        ):
            self.assertTrue(
                self.qt._show_qt_dismissible_warning_dialog(
                    parent,
                    "Generation Warning",
                    "glow_non_magical",
                    "Glow map enabled for a stone texture.",
                    dismissed_warnings,
                    persist,
                )
            )

    def test_qt_generation_sanity_warning_blocks_before_worker_start(self) -> None:
        recorded_warnings: list[tuple[str, str, str]] = []

        def capture_warning(parent, title, warning_id, message, dismissed_warnings, persist):
            recorded_warnings.append((title, warning_id, message))
            return False

        state = dict(self.qt._GUI_STATE_DEFAULTS)
        state.update(
            {
                "input_path": str(self.root / "stone_wall.dds"),
                "include_glow": True,
                "qt_render_profile": "custom",
            }
        )

        def scenario(window):
            widget = lambda name: self.widget(window, name)
            widget("generate").click()
            self.assertEqual(len(recorded_warnings), 1)
            self.assertEqual(recorded_warnings[0][0], "Generation Warning")
            self.assertEqual(recorded_warnings[0][1], "glow_non_magical")
            self.assertIn("stone", recorded_warnings[0][2].lower())
            self.assertTrue(widget("generate").isEnabled())
            return True

        self.run_window(
            scenario,
            load_state=state,
            extra_patches=(mock.patch("generate_textures._show_qt_dismissible_warning_dialog", side_effect=capture_warning),),
        )

    def test_qt_generation_output_folder_warning_blocks_before_worker_start(self) -> None:
        output_dir = self.root / "output"
        output_dir.mkdir()
        (output_dir / "legacy_msn.dds").write_bytes(b"")
        recorded_warnings: list[tuple[str, str, str]] = []

        def capture_warning(parent, title, warning_id, message, dismissed_warnings, persist):
            recorded_warnings.append((title, warning_id, message))
            return False

        state = dict(self.qt._GUI_STATE_DEFAULTS)
        state.update(
            {
                "input_path": str(self.root / "plain_source.dds"),
                "output_path": str(output_dir),
                "include_complex": True,
                "include_normal": True,
                "complex_format": "cm",
                "qt_render_profile": "custom",
            }
        )

        def scenario(window):
            widget = lambda name: self.widget(window, name)
            widget("generate").click()
            self.assertEqual(len(recorded_warnings), 1)
            self.assertEqual(recorded_warnings[0][0], "Output Folder Warning")
            self.assertEqual(recorded_warnings[0][1], "output_folder_has_msn_files")
            self.assertIn("_msn", recorded_warnings[0][2])
            self.assertTrue(widget("generate").isEnabled())
            return True

        self.run_window(
            scenario,
            load_state=state,
            extra_patches=(mock.patch("generate_textures._show_qt_dismissible_warning_dialog", side_effect=capture_warning),),
        )


if __name__ == "__main__":
    unittest.main()
