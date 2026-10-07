from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.pre_release_validation import _append_trend_history


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


if __name__ == "__main__":
    unittest.main()
