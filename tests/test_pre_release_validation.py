from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from scripts.pre_release_validation import (
    REPO_ROOT,
    _realistic_batch_inventory,
    _validate_batch_acceptance_dds,
    _write_batch_acceptance_dds,
    main,
    run_realistic_batch_acceptance,
    _DEFAULT_REALMOD_DELTA_SEED,
    _assert_realmod_fallback_drift_within_limit,
    _assert_bucket_distribution_drift_within_limit,
    _append_trend_history,
    _build_realmod_fallback_drift_report,
    _collect_localization_coverage,
    _load_prior_bucket_distribution,
    _load_realmod_pack_payload,
    _packaged_smoke_dimensions,
    _packaged_smoke_resume_args,
    _packaged_smoke_scenarios,
    _mutate_batch_acceptance_dds_preserving_metadata,
    _read_packaged_smoke_dds_dimensions,
    _resolve_seed_realmod_delta_files,
    _run_repository_hygiene_scan,
    _resolve_packaged_smoke_binary,
    _validate_packaged_checkpoint_fingerprints,
    _write_packaged_smoke_png,
    _write_packaged_smoke_dds,
)


class TestRealisticBatchAcceptance(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.calls: list[list[str]] = []
        self.inventory = {
            name: (1024, 1024) if name == "striped_05.dds" else (16, 16)
            for name in _realistic_batch_inventory(12)
        }
        self.mutation = ""

    def tearDown(self) -> None:
        self._td.cleanup()

    def fake_cli(self, command, **kwargs):
        self.calls.append(command)
        phase = len(self.calls) - 1
        input_dir = Path(command[1] if command[0].endswith("binary") else command[2])
        output_dir = Path(command[command.index("--output-dir") + 1])
        checkpoint = Path(command[command.index("--checkpoint-file") + 1])
        telemetry = Path(command[command.index("--batch-telemetry-file") + 1])
        names = list(self.inventory)
        if phase:
            names.append("corrupt.dds")
        changed_source_name = next(name for name, size in self.inventory.items() if size == (1024, 1024))
        generated = (
            names if phase == 0 else
            ["corrupt.dds"] if phase == 1 else
            [changed_source_name] if phase == 3 else
            []
        )
        for name in generated:
            for suffix in ("", "_n", "_p"):
                size = (1024, 1024) if name == "corrupt.dds" else self.inventory[name]
                _write_batch_acceptance_dds(
                    output_dir / f"{Path(name).stem}{suffix}.dds",
                    size,
                    variant=1 if phase == 3 else 0,
                )
        completed_file_fingerprints = {}
        for name in names:
            source_path = input_dir / name
            stat_result = source_path.stat()
            completed_file_fingerprints[str(source_path.resolve())] = {
                "size_bytes": stat_result.st_size,
                "mtime_ns": stat_result.st_mtime_ns,
                "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            }
        state = {
            "completed_success_count": len(names),
            "resumed_completed_count": (0, 12, 13, 12)[phase],
            "completed_success_files": sorted(str((input_dir / name).resolve()) for name in names),
            "completed_file_fingerprints": completed_file_fingerprints,
        }
        if self.mutation == "checkpoint":
            state["completed_success_files"][0] = str(input_dir / "unknown.dds")
        if phase < 2 or phase == 3:
            checkpoint.write_text(json.dumps(state), encoding="utf-8")
        summary = {
            "workers": 2 if phase == 0 else 1, "total_files_considered": 13,
            "processed_files": (13, 1, 0, 1)[phase], "successful_files": (12, 1, 0, 1)[phase],
            "failed_files": (1, 0, 0, 0)[phase], "resumed_completed_count": (0, 12, 13, 12)[phase],
            "checkpoint_enabled": True, "resumed_from_checkpoint": phase > 0,
            "source_files_4k_or_above": 2, "max_source_dimension": 4096,
        }
        if self.mutation == "telemetry":
            summary["successful_files"] = 99
        telemetry.write_text(json.dumps({"summary": summary}), encoding="utf-8")
        if phase == 0:
            failure = {"summary": {"failed_files": 1, "successful_files": 12, "successful_outputs": 36},
                       "failures": [{"file": str(input_dir / "corrupt.dds"), "error": "Invalid DDS"}]}
            (output_dir / "batch_failure_report.json").write_text(json.dumps(failure), encoding="utf-8")
            (output_dir / "batch_failure_report.csv").write_text("file,error\ncorrupt.dds,Invalid DDS\n", encoding="utf-8")
        if self.mutation == "mtime" and phase == 1:
            import os
            target = output_dir / "striped_00.dds"
            stamp = target.stat().st_mtime_ns + 1_000_000
            os.utime(target, ns=(stamp, stamp))
        return subprocess.CompletedProcess(command, 1 if phase == 0 else 0)

    def run_mocked(self, binary=None):
        with unittest.mock.patch("scripts.pre_release_validation._realistic_batch_inventory", return_value=self.inventory.copy()), \
             unittest.mock.patch("scripts.pre_release_validation.subprocess.run", side_effect=self.fake_cli):
            return run_realistic_batch_acceptance(self.root, binary=binary, workers=3, timeout=7, count=12)

    def test_source_orchestration_recovers_then_noops_and_preserves_outputs(self) -> None:
        report = self.run_mocked()
        self.assertEqual(report["status"], "pass", report.get("error"))
        self.assertEqual([row["output_count"] for row in report["phases"]], [36, 39, 39, 39])
        self.assertEqual([row["unchanged_output_count"] for row in report["phases"]], [0, 36, 39, 36])
        self.assertEqual(report["phases"][-1]["changed_source"], "striped_05.dds")
        self.assertEqual(report["phases"][-1]["source_mutation"]["size_bytes"], 1024 * 1024 * 3 + 128)
        self.assertNotEqual(
            report["phases"][-1]["source_mutation"]["original_sha256"],
            report["phases"][-1]["source_mutation"]["changed_sha256"],
        )
        self.assertEqual(
            report["phases"][-1]["source_mutation"]["changed_sha256"],
            report["phases"][-1]["checkpoint"]["completed_file_fingerprints"][
                str((Path(report["artifact_dir"]) / "input" / "striped_05.dds").resolve())
            ]["sha256"],
        )
        self.assertEqual(report["cli_cooperative_cancellation"], "unsupported")
        self.assertIn("not_mod_scale", report["scope"])
        self.assertTrue(self.calls[0][1].endswith("generate_textures.py"))
        self.assertNotIn("--resume-checkpoint", self.calls[0])
        self.assertTrue(all("--resume-checkpoint" in command for command in self.calls[1:]))
        for row in report["phases"]:
            self.assertTrue(Path(row["log_path"]).exists())
            self.assertTrue(Path(row["telemetry_path"]).exists())
        self.assertEqual(json.loads(Path(report["report_path"]).read_text())["status"], "pass")

    def test_packaged_binary_uses_existing_cli_and_unique_artifacts(self) -> None:
        binary = self.root / "binary"
        first = self.run_mocked(binary)
        self.assertEqual(first["status"], "pass", first.get("error"))
        self.assertEqual(self.calls[0][0], str(binary.resolve()))
        self.calls.clear()
        second = self.run_mocked(binary)
        self.assertNotEqual(first["report_path"], second["report_path"])

    def test_rejects_wrong_telemetry_checkpoint_and_modified_resume_output(self) -> None:
        for mutation in ("telemetry", "checkpoint", "mtime"):
            with self.subTest(mutation=mutation):
                self.calls.clear()
                self.mutation = mutation
                report = self.run_mocked()
                self.assertEqual(report["status"], "fail")
                self.assertTrue(report.get("error"))
                self.assertTrue(Path(report["report_path"]).exists())

    def test_timeout_retains_failure_report_log_and_missing_telemetry_evidence(self) -> None:
        with unittest.mock.patch("scripts.pre_release_validation._realistic_batch_inventory", return_value=self.inventory), \
             unittest.mock.patch("scripts.pre_release_validation.subprocess.run",
                                 side_effect=subprocess.TimeoutExpired("cli", 0.1)):
            report = run_realistic_batch_acceptance(self.root, timeout=0.1, count=12)
        self.assertEqual(report["status"], "fail")
        self.assertIn("TimeoutExpired", report["error"])
        row = report["phases"][0]
        self.assertTrue(Path(row["log_path"]).exists())
        self.assertEqual(json.loads(Path(row["telemetry_path"]).read_text())["status"], "unavailable")

    def test_inventory_and_real_4k_payload_is_nonflat_decodable_and_complete(self) -> None:
        from collections import Counter
        from PIL import Image

        inventory = _realistic_batch_inventory(12)
        self.assertEqual(Counter(inventory.values()), {
            (4096, 4096): 2, (2048, 2048): 3, (1024, 1024): 4,
            (2048, 1024): 1, (1024, 2048): 1, (512, 1024): 1,
        })
        path = self.root / "payload.dds"
        _write_batch_acceptance_dds(path, (4096, 4096))
        evidence = _validate_batch_acceptance_dds(path, (4096, 4096))
        self.assertEqual(evidence["payload_bytes"], 4096 * 4096 * 3)
        with Image.open(path) as image:
            image.load()
            self.assertTrue(all(low < high for low, high in image.getextrema()))
        with path.open("ab") as stream:
            stream.write(b"unexpected trailing payload")
        with self.assertRaisesRegex(ValueError, "payload length"):
            _validate_batch_acceptance_dds(path, (4096, 4096))

    def test_configurable_inventory_keeps_two_4k_inputs_and_repeats_mixed_queue_workload(self) -> None:
        from collections import Counter

        self.assertEqual(len(_realistic_batch_inventory()), 64)
        for count in (12, 64, 500):
            inventory = _realistic_batch_inventory(count)
            self.assertEqual(len(inventory), count)
            self.assertEqual(list(inventory.values())[:12], list(_realistic_batch_inventory(12).values()))
            sizes = Counter(inventory.values())
            self.assertEqual(sizes[(4096, 4096)], 2)
            if count > 12:
                self.assertEqual(max(max(size) for size in list(inventory.values())[12:]), 2048)
            self.assertTrue(all(name.endswith(".dds") for name in inventory))
        with self.assertRaisesRegex(ValueError, "at least 12"):
            _realistic_batch_inventory(11)

    def test_invalid_count_persists_failure_evidence_without_running_cli(self) -> None:
        with unittest.mock.patch("scripts.pre_release_validation.subprocess.run") as cli:
            report = run_realistic_batch_acceptance(self.root, count=11)
        self.assertEqual(report["status"], "fail")
        self.assertIn("at least 12", report["error"])
        self.assertTrue(Path(report["report_path"]).is_file())
        cli.assert_not_called()

    def test_acceptance_only_bypasses_regular_gates_and_propagates_failure(self) -> None:
        for status, expected in (("pass", 0), ("fail", 1)):
            with unittest.mock.patch("sys.argv", ["validation", "--batch-acceptance-only",
                                                  "--artifact-dir", str(self.root)]), \
                 unittest.mock.patch("scripts.pre_release_validation.run_realistic_batch_acceptance",
                                     return_value={"status": status}) as acceptance, \
                 unittest.mock.patch("scripts.pre_release_validation._run_step") as gate:
                self.assertEqual(main(), expected)
                acceptance.assert_called_once_with(self.root, None, 2, 900, count=64)
                gate.assert_not_called()

    def test_cli_passes_explicit_synthetic_count_and_rejects_too_small_inventory(self) -> None:
        with unittest.mock.patch("sys.argv", ["validation", "--batch-acceptance-only",
                                              "--artifact-dir", str(self.root), "--batch-acceptance-count", "500"]), \
             unittest.mock.patch("scripts.pre_release_validation.run_realistic_batch_acceptance",
                                 return_value={"status": "pass"}) as acceptance:
            self.assertEqual(main(), 0)
            acceptance.assert_called_once_with(self.root, None, 2, 900, count=500)
        with unittest.mock.patch("sys.argv", ["validation", "--batch-acceptance-count", "11"]), \
             unittest.mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit) as exc:
                main()
            self.assertEqual(exc.exception.code, 2)


def _snapshot(stamp: str) -> dict[str, object]:
    return {"generated_at_utc": stamp, "packs": []}


class TestPreReleaseValidationHistory(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_append_trend_history_rolls_forward_seed_history_and_deduplicates(self) -> None:
        seed_history = self.tmp / "seed_history.json"
        local_history = self.tmp / "local_history.json"
        seed_history.write_text(
            json.dumps({"history": [_snapshot("2026-01-01T00:00:00Z"), _snapshot("2026-01-02T00:00:00Z")]}, indent=2),
            encoding="utf-8",
        )
        local_history.write_text(
            json.dumps({"history": [_snapshot("2026-01-02T00:00:00Z"), _snapshot("2026-01-03T00:00:00Z")]}, indent=2),
            encoding="utf-8",
        )

        _append_trend_history(
            _snapshot("2026-01-04T00:00:00Z"),
            local_history,
            seed_history_files=[seed_history],
        )
        payload = json.loads(local_history.read_text(encoding="utf-8"))
        history = payload.get("history", [])
        self.assertEqual(
            [row.get("generated_at_utc") for row in history],
            [
                "2026-01-01T00:00:00Z",
                "2026-01-02T00:00:00Z",
                "2026-01-03T00:00:00Z",
                "2026-01-04T00:00:00Z",
            ],
        )

    def test_append_trend_history_keeps_last_240_rows(self) -> None:
        seed_history = self.tmp / "seed_history_large.json"
        seed_rows = [_snapshot(f"2026-01-01T00:{idx:02d}:00Z") for idx in range(250)]
        seed_history.write_text(json.dumps({"history": seed_rows}, indent=2), encoding="utf-8")
        history_file = self.tmp / "history.json"

        _append_trend_history(
            _snapshot("2026-01-01T10:00:00Z"),
            history_file,
            seed_history_files=[seed_history],
        )
        payload = json.loads(history_file.read_text(encoding="utf-8"))
        history = payload.get("history", [])
        self.assertEqual(len(history), 240)
        self.assertEqual(history[-1].get("generated_at_utc"), "2026-01-01T10:00:00Z")


class TestPreReleaseValidationLocalization(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_collect_localization_coverage_reports_missing_and_extra_keys(self) -> None:
        translations = self.tmp / "translations"
        translations.mkdir(parents=True, exist_ok=True)
        (translations / "en.json").write_text(
            json.dumps({"strings": {"A": "A", "B": "B", "C": "C"}}),
            encoding="utf-8",
        )
        (translations / "es.json").write_text(
            json.dumps({"strings": {"A": "A_es", "B": "B_es", "EXTRA": "x"}}),
            encoding="utf-8",
        )
        report = _collect_localization_coverage(translations)
        rows = {str(row.get("language")): row for row in report.get("languages", [])}
        self.assertIn("en", rows)
        self.assertIn("es", rows)
        self.assertEqual(int(rows["es"].get("missing_count", 0)), 1)
        self.assertEqual(int(rows["es"].get("extra_count", 0)), 1)
        self.assertGreater(float(rows["en"].get("identical_to_base_ratio", 0.0) or 0.0), 0.99)
        self.assertLess(float(rows["es"].get("identical_to_base_ratio", 0.0) or 0.0), 1.0)
        summary = report.get("summary", {})
        self.assertEqual(int(summary.get("base_string_count", 0)), 3)
        self.assertEqual(int(summary.get("languages_with_missing_strings", 0)), 1)
        self.assertEqual(int(summary.get("languages_with_identical_to_base_majority", 0)), 0)

    def test_collect_localization_coverage_requires_en_catalog(self) -> None:
        translations = self.tmp / "translations"
        translations.mkdir(parents=True, exist_ok=True)
        (translations / "es.json").write_text(
            json.dumps({"strings": {"A": "A_es"}}),
            encoding="utf-8",
        )
        with self.assertRaises(SystemExit):
            _collect_localization_coverage(translations)


class TestPreReleaseValidationRealmodPayload(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_load_realmod_pack_payload_merges_extra_packs(self) -> None:
        extra = self.tmp / "extra_realmod_pack.json"
        extra.write_text(
            json.dumps(
                {
                    "packs": [
                        {
                            "id": "external_pack",
                            "cases": [
                                {
                                    "id": "external_case",
                                    "profile": "skyrim",
                                    "shader_layout": "legacy",
                                    "user_ver2": 83,
                                }
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        payload = _load_realmod_pack_payload([extra])
        packs = payload.get("packs", [])
        self.assertIsInstance(packs, list)
        self.assertTrue(
            any(str(pack.get("id", "")) == "external_broken_longtail_pack" for pack in packs if isinstance(pack, dict))
        )
        self.assertTrue(
            any(str(pack.get("id", "")) == "external_broken_additional_pack" for pack in packs if isinstance(pack, dict))
        )
        self.assertTrue(
            any(str(pack.get("id", "")) == "external_broken_large_pack" for pack in packs if isinstance(pack, dict))
        )
        self.assertTrue(any(str(pack.get("id", "")) == "external_pack" for pack in packs if isinstance(pack, dict)))


class TestPreReleaseValidationFallbackDrift(unittest.TestCase):
    def test_fallback_drift_report_builds_from_seed_delta_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            seed = tmp / "seed_delta.json"
            seed.write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "pack_id": "pack_alpha",
                                "detected_conflict_codes": [
                                    "fallback_or_unknown.skyrim.legacy",
                                    "path_slot_parallax.wrong_suffix.skyrim.legacy",
                                ],
                            },
                            {
                                "pack_id": "pack_alpha",
                                "detected_conflict_codes": [
                                    "path_slot_normal.wrong_suffix.skyrim.legacy",
                                ],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            current_report = {
                "packs": [
                    {
                        "pack_id": "pack_alpha",
                        "total_conflict_groups": 4,
                        "fallback_or_unknown_groups": 1,
                    }
                ]
            }
            report = _build_realmod_fallback_drift_report(
                current_fallback_guard_report=current_report,
                seed_realmod_delta_files=[seed],
            )
            self.assertTrue(bool(report.get("baseline_found", False)))
            rows = report.get("packs", [])
            self.assertIsInstance(rows, list)
            self.assertTrue(rows)
            first = rows[0]
            self.assertEqual(str(first.get("pack_id", "")), "pack_alpha")
            self.assertTrue(bool(first.get("has_prior_baseline", False)))
            self.assertEqual(int(first.get("prior_fallback_or_unknown_groups", 0)), 1)
            self.assertAlmostEqual(float(first.get("prior_fallback_ratio", 0.0)), 1.0 / 3.0, places=6)

    def test_fallback_drift_guard_fails_when_ratio_or_group_drift_exceeds_threshold(self) -> None:
        report = {
            "baseline_found": True,
            "packs": [
                {
                    "pack_id": "pack_alpha",
                    "has_prior_baseline": True,
                    "fallback_ratio_drift": 0.2,
                    "fallback_group_drift": 3,
                }
            ],
        }
        with self.assertRaises(SystemExit):
            _assert_realmod_fallback_drift_within_limit(
                report=report,
                max_fallback_ratio_drift=0.05,
                max_fallback_group_drift=1,
            )

    def test_fallback_drift_guard_skips_when_no_baseline(self) -> None:
        report = {"baseline_found": False, "packs": []}
        _assert_realmod_fallback_drift_within_limit(
            report=report,
            max_fallback_ratio_drift=0.01,
            max_fallback_group_drift=0,
        )

    def test_fallback_drift_prefers_usable_seed_with_cases_when_newer_seed_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            newer = tmp / "newer_seed.json"
            older = tmp / "older_seed.json"
            newer.write_text(json.dumps({"generated_at_utc": "2026-01-03T00:00:00Z"}), encoding="utf-8")
            older.write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "pack_id": "pack_alpha",
                                "detected_conflict_codes": [
                                    "fallback_or_unknown.skyrim.legacy",
                                    "path_slot_parallax.wrong_suffix.skyrim.legacy",
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            current_report = {
                "packs": [
                    {
                        "pack_id": "pack_alpha",
                        "total_conflict_groups": 4,
                        "fallback_or_unknown_groups": 1,
                    }
                ]
            }
            report = _build_realmod_fallback_drift_report(
                current_fallback_guard_report=current_report,
                seed_realmod_delta_files=[newer, older],
            )
            self.assertTrue(bool(report.get("baseline_found", False)))
            rows = report.get("packs", [])
            self.assertIsInstance(rows, list)
            self.assertTrue(any(bool(row.get("has_prior_baseline", False)) for row in rows if isinstance(row, dict)))


class TestPreReleaseValidationSeedResolution(unittest.TestCase):
    def test_seed_realmod_delta_resolution_includes_repo_default_seed(self) -> None:
        resolved = _resolve_seed_realmod_delta_files([])
        self.assertIn(_DEFAULT_REALMOD_DELTA_SEED, resolved)

    def test_seed_realmod_delta_resolution_deduplicates_entries(self) -> None:
        resolved = _resolve_seed_realmod_delta_files([
            _DEFAULT_REALMOD_DELTA_SEED,
            _DEFAULT_REALMOD_DELTA_SEED,
        ])
        self.assertEqual(
            sum(1 for path in resolved if path == _DEFAULT_REALMOD_DELTA_SEED),
            1,
        )

    def test_load_prior_bucket_distribution_prefers_seed_with_bucket_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            newer = tmp / "newer_seed.json"
            older = tmp / "older_seed.json"
            newer.write_text(json.dumps({"generated_at_utc": "2026-01-04T00:00:00Z"}), encoding="utf-8")
            older.write_text(
                json.dumps({"intended_difference_bucket_counts": {"none": 6, "safety_first": 2}}),
                encoding="utf-8",
            )
            distribution = _load_prior_bucket_distribution([newer, older])
            self.assertGreater(distribution.get("none", 0.0), 0.0)
            _assert_bucket_distribution_drift_within_limit(
                current_report={"intended_difference_bucket_counts": {"none": 3, "safety_first": 1}},
                baseline_distribution=distribution,
                max_drift_ratio=1.0,
            )


class TestPreReleaseValidationPackagingSmoke(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_resolve_packaged_smoke_binary_prefers_platform_specific_candidate(self) -> None:
        dist = self.tmp / "packaging_smoke" / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        binary = dist / "generate_textures_smoke.exe"
        binary.write_bytes(b"")
        resolved = _resolve_packaged_smoke_binary(self.tmp)
        self.assertEqual(resolved, binary)

    def test_resolve_packaged_smoke_binary_raises_when_missing(self) -> None:
        with self.assertRaises(SystemExit):
            _resolve_packaged_smoke_binary(self.tmp)

    def test_packaged_smoke_resume_uses_checkpoint_output_directory(self) -> None:
        command = _packaged_smoke_resume_args(
            self.tmp / "app.exe",
            self.tmp / "input",
            self.tmp / "checkpoint-output",
            ["--render-profile", "vanilla"],
            self.tmp / "resume-telemetry.json",
        )
        self.assertEqual(command[command.index("--output-dir") + 1], str(self.tmp / "checkpoint-output"))
        self.assertIn("--resume-checkpoint", command)
        self.assertEqual(command[command.index("--batch-telemetry-file") + 1], str(self.tmp / "resume-telemetry.json"))

    def test_read_packaged_smoke_dds_dimensions(self) -> None:
        dds_path = self.tmp / "sample.dds"
        dds_path.write_bytes(
            b"DDS "
            + struct.pack("<I", 124)
            + bytes(4)
            + struct.pack("<II", 96, 128)
        )
        self.assertEqual(_read_packaged_smoke_dds_dimensions(dds_path), (128, 96))

    def test_packaged_smoke_png_writer_accepts_rectangular_dimensions(self) -> None:
        png_path = self.tmp / "sample.png"
        _write_packaged_smoke_png(png_path, (48, 32))
        self.assertEqual(struct.unpack(">II", png_path.read_bytes()[16:24]), (48, 32))
        self.assertEqual(_packaged_smoke_dimensions(24), (24, 24))

    def test_packaged_checkpoint_validation_and_content_mutation(self) -> None:
        source = self.tmp / "sample.dds"
        _write_packaged_smoke_dds(source, 32)
        stat_result = source.stat()
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        source_key = str(source.resolve())
        payload = {
            "completed_success_files": [source_key],
            "completed_file_fingerprints": {
                source_key: {
                    "size_bytes": stat_result.st_size,
                    "mtime_ns": stat_result.st_mtime_ns,
                    "sha256": original_hash,
                }
            },
        }

        validated = _validate_packaged_checkpoint_fingerprints(payload)
        self.assertEqual(validated[source_key]["sha256"], original_hash)

        mutation = _mutate_batch_acceptance_dds_preserving_metadata(
            source,
            (32, 32),
            pixel_format="DXT5",
        )
        self.assertEqual(mutation["size_bytes"], stat_result.st_size)
        self.assertEqual(mutation["mtime_ns"], stat_result.st_mtime_ns)
        self.assertEqual(mutation["original_sha256"], original_hash)
        self.assertNotEqual(mutation["changed_sha256"], original_hash)
        with self.assertRaisesRegex(ValueError, "does not match source"):
            _validate_packaged_checkpoint_fingerprints(payload)

    def test_packaged_smoke_scenarios_define_required_outputs(self) -> None:
        scenarios = _packaged_smoke_scenarios()
        scenario_by_name = {
            str(entry.get("name", "")).strip(): entry
            for entry in scenarios
            if isinstance(entry, dict)
        }
        for expected in (
            "vanilla",
            "terrain",
            "community_shaders",
            "truepbr",
            "pbr_material_shortcut",
            "enb",
            "performance_core",
            "custom_glow_env",
            "community_shaders_wet_snow",
            "community_shaders_high_resolution_wet_snow",
            "truepbr_plus_aux_maps",
            "vr_safe_core",
            "enb_glow_combo",
            "truepbr_wet_snow_combo",
            "terrain_no_parallax_env",
            "enb_no_parallax_glow",
            "community_shaders_aux_wet_only",
            "truepbr_no_parallax",
            "vanilla_glow_env_combo",
            "community_shaders_no_parallax_wet_snow",
            "architecture_base",
            "fallout4_core_naming",
            "fallout4_batch_checkpoint_resume",
            "fallout76_surface_batch_checkpoint_resume",
            "fallout76_extended_parallel_recovery",
        ):
            self.assertIn(expected, scenario_by_name)
            entry = scenario_by_name[expected]
            args = entry.get("args", [])
            self.assertIsInstance(args, list)
            self.assertGreater(len(args), 0)
            self.assertGreaterEqual(int(entry.get("min_outputs", 0) or 0), 1)
            required_suffixes = entry.get("required_suffixes", ())
            self.assertTrue(required_suffixes)
            forbidden_suffixes = entry.get("forbidden_suffixes", ())
            self.assertIsInstance(forbidden_suffixes, tuple)
            required_families = entry.get("required_output_families", ())
            self.assertIsInstance(required_families, tuple)
            self.assertGreater(len(required_families), 0)
        fallout4_suffixes = {
            str(value).lower()
            for value in scenario_by_name["fallout4_core_naming"].get("required_suffixes", ())
        }
        self.assertEqual(fallout4_suffixes, {"_d.dds", "_n.dds"})
        self.assertIn("--target-game", scenario_by_name["fallout4_core_naming"].get("args", []))
        self.assertIn("--environment-mask", scenario_by_name["architecture_base"].get("args", []))
        resume_scenario = scenario_by_name["fallout4_batch_checkpoint_resume"]
        self.assertEqual(resume_scenario.get("input_count"), 2)
        self.assertEqual(resume_scenario.get("input_size"), 32)
        self.assertEqual(resume_scenario.get("input_format"), "dds")
        self.assertTrue(resume_scenario.get("checkpoint_resume"))
        self.assertTrue(resume_scenario.get("interruption_resume"))
        self.assertTrue(resume_scenario.get("verify_content_change_resume"))
        self.assertEqual(
            resume_scenario.get("required_exact_output_family_counts"),
            {"diffuse": 2, "normal": 2},
        )
        fallout76_scenario = scenario_by_name["fallout76_surface_batch_checkpoint_resume"]
        fallout76_args = fallout76_scenario.get("args", [])
        self.assertIn("fallout76", fallout76_args)
        self.assertIn("--environment-mask", fallout76_args)
        self.assertEqual(fallout76_scenario.get("input_count"), 6)
        self.assertEqual(fallout76_scenario.get("input_dimensions"), (64, 48))
        self.assertEqual(fallout76_scenario.get("input_format"), "dds")
        self.assertTrue(fallout76_scenario.get("checkpoint_resume"))
        self.assertTrue(fallout76_scenario.get("interruption_resume"))
        self.assertEqual(fallout76_scenario.get("interruption_index"), 3)
        self.assertEqual(
            fallout76_scenario.get("required_exact_suffix_counts"),
            {"_d.dds": 6, "_n.dds": 6, "_s.dds": 6},
        )
        self.assertEqual(
            fallout76_scenario.get("required_exact_output_family_counts"),
            {"diffuse": 6, "normal": 6, "env_mask": 6},
        )
        extended_recovery = scenario_by_name["fallout76_extended_parallel_recovery"]
        self.assertEqual(extended_recovery.get("input_count"), 24)
        self.assertEqual(extended_recovery.get("batch_workers"), 4)
        self.assertEqual(extended_recovery.get("interruption_indices"), (7, 18))
        self.assertTrue(extended_recovery.get("checkpoint_resume"))
        self.assertTrue(extended_recovery.get("interruption_resume"))
        self.assertEqual(
            extended_recovery.get("required_exact_output_family_counts"),
            {"diffuse": 24, "normal": 24, "env_mask": 24},
        )
        self.assertEqual(scenario_by_name["vanilla"].get("input_size"), 16)
        self.assertEqual(scenario_by_name["community_shaders"].get("input_size"), 64)
        self.assertEqual(scenario_by_name["truepbr"].get("input_size"), 32)
        high_resolution_scenario = scenario_by_name["community_shaders_high_resolution_wet_snow"]
        self.assertEqual(high_resolution_scenario.get("input_dimensions"), (128, 96))
        self.assertEqual(
            high_resolution_scenario.get("required_exact_output_family_counts"),
            {
                "diffuse": 1,
                "normal": 1,
                "parallax": 1,
                "complex_cm": 1,
                "env_mask": 1,
                "wetness": 1,
                "snow": 1,
            },
        )
        truepbr_suffixes = {
            str(value).lower() for value in scenario_by_name["truepbr"].get("required_suffixes", ())
        }
        self.assertIn("_rmaos.dds", truepbr_suffixes)
        truepbr_sidecars = {
            str(value).lower() for value in scenario_by_name["truepbr"].get("required_sidecar_suffixes", ())
        }
        self.assertIn("_rmaos.json", truepbr_sidecars)
        truepbr_keys = {
            str(value) for value in scenario_by_name["truepbr"].get("required_sidecar_json_keys", ())
        }
        self.assertIn("parallax", truepbr_keys)
        self.assertIn("displacement_scale", truepbr_keys)
        truepbr_key_types = {
            str(key): str(value).lower()
            for key, value in dict(scenario_by_name["truepbr"].get("required_sidecar_key_types", {})).items()
        }
        self.assertEqual(truepbr_key_types.get("parallax"), "bool")
        self.assertEqual(truepbr_key_types.get("displacement_scale"), "number")
        self.assertEqual(truepbr_key_types.get("texture"), "string_nonempty")
        truepbr_exact_counts = {
            str(key).lower(): int(value)
            for key, value in dict(scenario_by_name["truepbr"].get("required_exact_suffix_counts", {})).items()
        }
        self.assertEqual(truepbr_exact_counts.get("_rmaos.dds"), 1)
        self.assertEqual(truepbr_exact_counts.get("_rmaos.json"), 1)
        truepbr_family_counts = {
            str(key).lower(): int(value)
            for key, value in dict(
                scenario_by_name["truepbr"].get("required_exact_output_family_counts", {})
            ).items()
        }
        self.assertEqual(truepbr_family_counts.get("diffuse"), 1)
        self.assertEqual(truepbr_family_counts.get("normal"), 1)
        self.assertEqual(truepbr_family_counts.get("rmaos"), 1)
        truepbr_wet_snow_exact_counts = {
            str(key).lower(): int(value)
            for key, value in dict(
                scenario_by_name["truepbr_wet_snow_combo"].get("required_exact_suffix_counts", {})
            ).items()
        }
        self.assertEqual(truepbr_wet_snow_exact_counts.get("_wt.dds"), 1)
        self.assertEqual(truepbr_wet_snow_exact_counts.get("_sm.dds"), 1)
        truepbr_wet_snow_family_counts = {
            str(key).lower(): int(value)
            for key, value in dict(
                scenario_by_name["truepbr_wet_snow_combo"].get("required_exact_output_family_counts", {})
            ).items()
        }
        self.assertEqual(truepbr_wet_snow_family_counts.get("rmaos"), 1)
        self.assertEqual(truepbr_wet_snow_family_counts.get("wetness"), 1)
        self.assertEqual(truepbr_wet_snow_family_counts.get("snow"), 1)
        self.assertFalse(scenario_by_name["vanilla"].get("required_sidecar_suffixes"))
        self.assertFalse(scenario_by_name["enb"].get("required_sidecar_suffixes"))


class TestPreReleaseValidationRepositoryHygiene(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_repository_hygiene_scan_flags_suspicious_root_artifact_filename(self) -> None:
        tracked = self.repo / "=1.26.0"
        tracked.write_text("placeholder", encoding="utf-8")

        with unittest.mock.patch("scripts.pre_release_validation.REPO_ROOT", self.repo):
            with unittest.mock.patch(
                "scripts.pre_release_validation._iter_repo_files",
                return_value=[tracked],
            ):
                with self.assertRaises(SystemExit):
                    _run_repository_hygiene_scan()

    def test_repository_hygiene_scan_allows_normal_root_files(self) -> None:
        tracked = self.repo / "README.md"
        tracked.write_text("# ok\n", encoding="utf-8")

        with unittest.mock.patch("scripts.pre_release_validation.REPO_ROOT", self.repo):
            with unittest.mock.patch(
                "scripts.pre_release_validation._iter_repo_files",
                return_value=[tracked],
            ):
                _run_repository_hygiene_scan()


if __name__ == "__main__":
    unittest.main()
