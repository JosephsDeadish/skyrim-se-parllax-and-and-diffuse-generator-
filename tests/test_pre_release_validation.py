from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from scripts.pre_release_validation import (
    _DEFAULT_REALMOD_DELTA_SEED,
    _assert_realmod_fallback_drift_within_limit,
    _assert_bucket_distribution_drift_within_limit,
    _append_trend_history,
    _build_realmod_fallback_drift_report,
    _collect_localization_coverage,
    _load_prior_bucket_distribution,
    _load_realmod_pack_payload,
    _packaged_smoke_scenarios,
    _resolve_seed_realmod_delta_files,
    _run_repository_hygiene_scan,
    _resolve_packaged_smoke_binary,
)


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
