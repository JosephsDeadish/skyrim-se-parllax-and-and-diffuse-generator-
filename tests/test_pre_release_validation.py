from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from scripts.pre_release_validation import (
    _append_trend_history,
    _collect_localization_coverage,
    _load_realmod_pack_payload,
    _packaged_smoke_scenarios,
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
        self.assertTrue(any(str(pack.get("id", "")) == "external_pack" for pack in packs if isinstance(pack, dict)))


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
