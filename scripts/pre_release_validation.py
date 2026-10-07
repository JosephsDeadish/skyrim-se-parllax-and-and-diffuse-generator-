#!/usr/bin/env python3
"""Pre-release validation gate for local runs and CI."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
PYTHON = sys.executable

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
)

SECRET_SCAN_EXCLUDES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".dds",
    ".nif",
    ".zip",
    ".exe",
    ".dll",
    ".so",
    ".pyd",
}


def _run_step(step_name: str, command: list[str]) -> None:
    print(f"\n=== {step_name} ===")
    print("$", " ".join(command))
    completed = subprocess.run(command, cwd=REPO_ROOT)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def _iter_repo_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    files: list[Path] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        file_path = REPO_ROOT / line.strip()
        if file_path.is_file():
            files.append(file_path)
    return files


def _run_secret_scan() -> None:
    print("\n=== Secret scan ===")
    findings: list[str] = []
    for file_path in _iter_repo_files():
        if file_path.suffix.lower() in SECRET_SCAN_EXCLUDES:
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = file_path.relative_to(REPO_ROOT)
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                findings.append(f"{relative}:{name}")
                break
    if findings:
        print(f"Potential secrets detected in {len(findings)} tracked file(s).")
        print("Resolve secret-scan findings before release.")
        raise SystemExit(1)
    print("No obvious secrets detected.")


def _run_packaging_smoke(artifact_dir: Path) -> None:
    print("\n=== Packaging smoke build (PyInstaller) ===")
    smoke_root = artifact_dir / "packaging_smoke"
    if smoke_root.exists():
        shutil.rmtree(smoke_root, ignore_errors=True)
    dist_dir = smoke_root / "dist"
    work_dir = smoke_root / "build"
    spec_dir = smoke_root / "spec"
    dist_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)
    _run_step(
        "Packaging smoke build",
        [
            PYTHON,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--name",
            "generate_textures_smoke",
            "--distpath",
            str(dist_dir),
            "--workpath",
            str(work_dir),
            "--specpath",
            str(spec_dir),
            "generate_textures.py",
        ],
    )


def _conflict_base_code(conflict_code: str) -> str:
    parts = conflict_code.split(".")
    if len(parts) >= 3:
        return ".".join(parts[:-2])
    return conflict_code


def _build_realmod_family_trend_snapshot() -> dict[str, object]:
    from nif_patcher import validate_nif_for_parallax
    from tests.test_nif_patcher import (
        _FIXTURE_REALMOD_SAMPLE_PACKS,
        _load_fixture_corpus_payload,
        _materialize_fixture_corpus,
    )

    payload = _load_fixture_corpus_payload(_FIXTURE_REALMOD_SAMPLE_PACKS)
    packs = payload.get("packs", [])
    pack_rows: list[dict[str, object]] = []
    if not isinstance(packs, list):
        packs = []
    with tempfile.TemporaryDirectory() as td:
        temp_root = Path(td)
        for pack in packs:
            if not isinstance(pack, dict):
                continue
            pack_id = str(pack.get("id", "pack")).strip() or "pack"
            cases = pack.get("cases", [])
            if not isinstance(cases, list):
                continue
            pack_root = temp_root / pack_id
            pack_root.mkdir(parents=True, exist_ok=True)
            corpus = _materialize_fixture_corpus(pack_root, {"cases": cases})
            validations = [validate_nif_for_parallax(path) for path in corpus]

            case_map: dict[str, dict[str, object]] = {}
            for case in cases:
                if not isinstance(case, dict):
                    continue
                case_id = str(case.get("id", "")).strip()
                if case_id:
                    case_map[case_id] = case

            family_rows: dict[str, dict[str, object]] = {}
            for nif_path, validation in zip(corpus, validations):
                case = case_map.get(nif_path.stem, {})
                family = str(case.get("family", "unknown")).strip() or "unknown"
                expected_prefixes = case.get("expected_prefixes", []) if isinstance(case, dict) else []
                expected_absent_prefixes = case.get("expected_absent_prefixes", []) if isinstance(case, dict) else []
                codes = [group.code for group in validation.conflict_report]
                base_codes = [_conflict_base_code(code) for code in codes]

                family_payload = family_rows.setdefault(
                    family,
                    {
                        "family": family,
                        "case_count": 0,
                        "pass_count": 0,
                        "fail_count": 0,
                        "top_conflicts": {},
                    },
                )
                family_payload["case_count"] = int(family_payload["case_count"]) + 1
                case_ok = True
                if isinstance(expected_prefixes, list):
                    for prefix in expected_prefixes:
                        if not any(code.startswith(str(prefix)) for code in codes):
                            case_ok = False
                            break
                if case_ok and isinstance(expected_absent_prefixes, list):
                    for prefix in expected_absent_prefixes:
                        if any(code.startswith(str(prefix)) for code in codes):
                            case_ok = False
                            break
                if case_ok:
                    family_payload["pass_count"] = int(family_payload["pass_count"]) + 1
                else:
                    family_payload["fail_count"] = int(family_payload["fail_count"]) + 1
                conflict_counts = family_payload["top_conflicts"]
                if not isinstance(conflict_counts, dict):
                    conflict_counts = {}
                    family_payload["top_conflicts"] = conflict_counts
                for base in base_codes:
                    conflict_counts[base] = int(conflict_counts.get(base, 0)) + 1

            normalized_families: list[dict[str, object]] = []
            for family_name, row in sorted(family_rows.items(), key=lambda item: item[0]):
                top_conflicts = row.get("top_conflicts", {})
                sorted_conflicts: list[dict[str, object]] = []
                if isinstance(top_conflicts, dict):
                    for base_code, count in sorted(top_conflicts.items(), key=lambda item: (-int(item[1]), item[0]))[:5]:
                        sorted_conflicts.append({"base_code": str(base_code), "count": int(count)})
                normalized_families.append(
                    {
                        "family": family_name,
                        "case_count": int(row.get("case_count", 0)),
                        "pass_count": int(row.get("pass_count", 0)),
                        "fail_count": int(row.get("fail_count", 0)),
                        "top_conflicts": sorted_conflicts,
                    }
                )
            pack_rows.append(
                {
                    "pack_id": pack_id,
                    "family_rows": normalized_families,
                }
            )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "packs": pack_rows,
    }


def _build_parity_matrix_feature_report() -> dict[str, object]:
    from nif_patcher import (
        build_auto_remediation_patch_options,
        validate_nif_for_parallax,
    )
    from tests.test_nif_patcher import (
        _FIXTURE_PARITY_SAMPLE_MATRIX,
        _load_fixture_corpus_payload,
        _materialize_fixture_corpus,
    )

    payload = _load_fixture_corpus_payload(_FIXTURE_PARITY_SAMPLE_MATRIX)
    cases = payload.get("cases", [])
    if not isinstance(cases, list):
        cases = []
    case_rows: list[dict[str, object]] = []

    with tempfile.TemporaryDirectory() as td:
        temp_root = Path(td)
        corpus = _materialize_fixture_corpus(temp_root, payload)
        validations = [validate_nif_for_parallax(path) for path in corpus]

        for case, validation in zip(cases, validations):
            if not isinstance(case, dict):
                continue
            code_rows = [group.code for group in validation.conflict_report]
            base_families = sorted({_conflict_base_code(code) for code in code_rows})
            _, rem_steps_tuple = build_auto_remediation_patch_options(
                validation.nif_path,
                code_rows,
                backup=False,
            )
            rem_steps = [str(step) for step in rem_steps_tuple]
            expected_steps = [str(step) for step in case.get("expected_remediation_steps", []) if str(step).strip()]
            expected_absent_steps = [
                str(step) for step in case.get("expected_absent_remediation_steps", []) if str(step).strip()
            ]
            missing_expected_steps = [step for step in expected_steps if step not in rem_steps]
            unexpected_present_steps = [step for step in expected_absent_steps if step in rem_steps]

            has_rebuild_steps = any(step.startswith("set_slot") for step in rem_steps)
            has_disable_steps = any(step.startswith("disable_") or step.startswith("clear_") for step in rem_steps)
            has_enable_steps = any(step.startswith("enable_") for step in rem_steps)
            if has_rebuild_steps and has_disable_steps:
                remediation_mode = "mixed_rebuild_and_disable"
            elif has_rebuild_steps:
                remediation_mode = "rebuild"
            elif has_disable_steps:
                remediation_mode = "disable"
            elif has_enable_steps:
                remediation_mode = "enable_or_flag_only"
            else:
                remediation_mode = "manual_or_noop"

            case_rows.append(
                {
                    "case_id": str(case.get("id", validation.nif_path.stem)),
                    "profile": str(case.get("profile", "")),
                    "shader_layout": str(case.get("shader_layout", "")),
                    "detected_conflict_families": base_families,
                    "detected_conflict_codes": code_rows,
                    "remediation_steps": rem_steps,
                    "remediation_mode": remediation_mode,
                    "fallback_used": bool(has_disable_steps),
                    "expected_remediation_steps": expected_steps,
                    "expected_absent_remediation_steps": expected_absent_steps,
                    "missing_expected_remediation_steps": missing_expected_steps,
                    "unexpected_present_remediation_steps": unexpected_present_steps,
                    "pgpatcher_strategy": str(case.get("pgpatcher_strategy", "") or ""),
                    "local_strategy": str(case.get("local_strategy", "") or ""),
                    "intentional_strategy_difference": bool(case.get("intentional_strategy_difference", False)),
                    "safety_difference_note": str(case.get("safety_difference_note", "") or ""),
                }
            )

    mode_counts: dict[str, int] = {}
    fallback_count = 0
    for row in case_rows:
        mode = str(row.get("remediation_mode", "manual_or_noop"))
        mode_counts[mode] = int(mode_counts.get(mode, 0)) + 1
        if bool(row.get("fallback_used", False)):
            fallback_count += 1

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_count": len(case_rows),
        "fallback_case_count": fallback_count,
        "remediation_mode_counts": dict(sorted(mode_counts.items())),
        "cases": case_rows,
    }


def _render_parity_feature_report_markdown(report: dict[str, object]) -> str:
    rows = report.get("cases", [])
    lines = [
        "# NIF parity feature report",
        "",
        f"- Generated: {report.get('generated_at_utc', '')}",
        f"- Cases: {report.get('case_count', 0)}",
        f"- Cases using disable/clear fallback: {report.get('fallback_case_count', 0)}",
        "",
        "| Case | Profile/layout | Detected family count | Remediation mode | Fallback used | Intentional strategy diff |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            case_id = str(row.get("case_id", "")).replace("|", "\\|")
            profile_layout = f"{row.get('profile', '?')}/{row.get('shader_layout', '?')}".replace("|", "\\|")
            family_count = len(row.get("detected_conflict_families", [])) if isinstance(
                row.get("detected_conflict_families"), list
            ) else 0
            mode = str(row.get("remediation_mode", "manual_or_noop")).replace("|", "\\|")
            fallback = "yes" if bool(row.get("fallback_used", False)) else "no"
            strategy_diff = "yes" if bool(row.get("intentional_strategy_difference", False)) else "no"
            lines.append(
                f"| `{case_id}` | `{profile_layout}` | {family_count} | `{mode}` | {fallback} | {strategy_diff} |"
            )
    lines.extend(
        [
            "",
            "Use the JSON artifact for full per-case details (detected families/codes, remediation steps, expected-step deltas, and safety-difference notes).",
            "",
        ]
    )
    return "\n".join(lines)


def _write_release_artifacts(
    *,
    artifact_dir: Path,
    step_status: list[tuple[str, str]],
    trend_snapshot: dict[str, object],
    parity_report: dict[str, object],
) -> tuple[Path, Path, Path, Path]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    trend_path = artifact_dir / "nif_family_trend_snapshot.json"
    trend_path.write_text(json.dumps(trend_snapshot, indent=2, sort_keys=True), encoding="utf-8")
    parity_json_path = artifact_dir / "nif_parity_feature_report.json"
    parity_json_path.write_text(json.dumps(parity_report, indent=2, sort_keys=True), encoding="utf-8")
    parity_md_path = artifact_dir / "nif_parity_feature_report.md"
    parity_md_path.write_text(_render_parity_feature_report_markdown(parity_report), encoding="utf-8")

    checklist_path = artifact_dir / "release_checklist.md"
    lines = [
        "# Release Validation Checklist",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    for name, status in step_status:
        marker = "x" if status == "pass" else " "
        lines.append(f"- [{marker}] {name}")
    lines.extend(
        [
            "",
            "## NIF real-sample family trend snapshot",
            "",
            f"- Snapshot JSON: `{trend_path.name}`",
            "",
            "## NIF parity feature report snapshot",
            "",
            f"- Parity JSON: `{parity_json_path.name}`",
            f"- Parity markdown: `{parity_md_path.name}`",
        ]
    )
    for pack in trend_snapshot.get("packs", []):
        if not isinstance(pack, dict):
            continue
        lines.append(f"- Pack `{pack.get('pack_id', 'pack')}`")
        families = pack.get("family_rows", [])
        if not isinstance(families, list):
            continue
        for family in families:
            if not isinstance(family, dict):
                continue
            lines.append(
                f"  - {family.get('family')}: pass {family.get('pass_count', 0)}/{family.get('case_count', 0)}, fail {family.get('fail_count', 0)}"
            )
    checklist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checklist_path, trend_path, parity_json_path, parity_md_path


def _load_history_rows(history_file: Path) -> list[dict[str, object]]:
    if not history_file.exists():
        return []
    try:
        loaded = json.loads(history_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(loaded, dict):
        return []
    rows = loaded.get("history", [])
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, object]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(row)
    return normalized


def _merge_history_rows(*history_groups: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    seen: set[str] = set()
    for group in history_groups:
        for row in group:
            key = json.dumps(row, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
    return merged


def _append_trend_history(
    snapshot: dict[str, object],
    history_file: Path,
    *,
    seed_history_files: list[Path] | None = None,
) -> Path:
    history_file.parent.mkdir(parents=True, exist_ok=True)
    seed_rows: list[dict[str, object]] = []
    for seed in seed_history_files or []:
        seed_rows.extend(_load_history_rows(seed))
    existing_rows = _load_history_rows(history_file)
    history_rows = _merge_history_rows(seed_rows, existing_rows)
    history_rows.append(snapshot)
    history_payload: dict[str, object] = {"history": history_rows[-240:]}
    history_file.write_text(json.dumps(history_payload, indent=2, sort_keys=True), encoding="utf-8")
    return history_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Run pre-release validation gate with artifacts.")
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=REPO_ROOT / "artifacts" / "release_readiness",
        help="Directory where release checklist and trend artifacts are written.",
    )
    parser.add_argument(
        "--history-file",
        type=Path,
        default=None,
        help="Optional JSON file to append family trend snapshots over time.",
    )
    parser.add_argument(
        "--seed-history-file",
        action="append",
        type=Path,
        default=[],
        help=(
            "Optional prior history file(s) used to seed/roll-forward trend timelines across CI runs. "
            "Can be passed multiple times."
        ),
    )
    parser.add_argument(
        "--skip-packaging-smoke",
        action="store_true",
        help="Skip local PyInstaller packaging smoke build.",
    )
    args = parser.parse_args()

    step_status: list[tuple[str, str]] = []
    _run_step(
        "Full unittest suite",
        [PYTHON, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
    )
    step_status.append(("Full unittest suite", "pass"))
    _run_step(
        "Targeted NIF fixture and conflict stress checks",
        [
            PYTHON,
            "-m",
            "unittest",
            "-v",
            "tests.test_nif_patcher.TestFixtureCorpusBaselinePack",
            "tests.test_nif_patcher.TestParitySampleMatrix",
            "tests.test_nif_patcher.TestRealModSamplePacks",
            "tests.test_nif_patcher.TestValidateNifForParallax.test_conflict_report_can_emit_multi_conflict_mixed_states",
            "tests.test_nif_patcher.TestAutoRemediationExecutor.test_auto_remediation_build_options_sets_env_mask_for_missing_envmap_slot5_when_guessable",
        ],
    )
    step_status.append(("Targeted NIF fixture/parity stress checks", "pass"))
    _run_step(
        "Compile check",
        [PYTHON, "-m", "compileall", "generate_textures.py", "nif_patcher.py", "tests"],
    )
    step_status.append(("Compile check", "pass"))
    _run_secret_scan()
    step_status.append(("Tracked-file secret scan", "pass"))
    if not args.skip_packaging_smoke:
        _run_packaging_smoke(args.artifact_dir)
        step_status.append(("Packaging smoke build", "pass"))
    trend_snapshot = _build_realmod_family_trend_snapshot()
    parity_report = _build_parity_matrix_feature_report()
    checklist_path, trend_path, parity_json_path, parity_md_path = _write_release_artifacts(
        artifact_dir=args.artifact_dir,
        step_status=step_status,
        trend_snapshot=trend_snapshot,
        parity_report=parity_report,
    )
    history_path = None
    if args.history_file is not None:
        history_path = _append_trend_history(
            trend_snapshot,
            args.history_file,
            seed_history_files=[path for path in args.seed_history_file if path is not None],
        )
    print(f"\nRelease checklist artifact: {checklist_path}")
    print(f"NIF trend snapshot artifact: {trend_path}")
    print(f"NIF parity feature report JSON artifact: {parity_json_path}")
    print(f"NIF parity feature report markdown artifact: {parity_md_path}")
    if history_path is not None:
        print(f"NIF trend history artifact: {history_path}")
    print("\nPre-release validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
