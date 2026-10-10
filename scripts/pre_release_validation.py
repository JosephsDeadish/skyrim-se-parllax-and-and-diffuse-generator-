#!/usr/bin/env python3
"""Pre-release validation gate for local runs and CI."""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
PYTHON = sys.executable
_DEFAULT_REALMOD_DELTA_SEED = REPO_ROOT / "tests" / "fixtures" / "nif_realmod_parity_delta_seed.json"

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


_SUSPICIOUS_TRACKED_FILENAME = re.compile(r"^=\d")


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


def _run_repository_hygiene_scan() -> None:
    print("\n=== Repository hygiene scan ===")
    suspicious_files: list[str] = []
    for file_path in _iter_repo_files():
        relative = file_path.relative_to(REPO_ROOT)
        if _SUSPICIOUS_TRACKED_FILENAME.match(relative.name):
            suspicious_files.append(str(relative))
            continue
        if relative.suffix.lower() in SECRET_SCAN_EXCLUDES:
            continue
        if relative.parent != Path("."):
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lowered = content.lower()
        if "defaulting to user installation because normal site-packages is not writeable" in lowered and "collecting " in lowered:
            suspicious_files.append(str(relative))
    if suspicious_files:
        print("Repository hygiene scan found suspicious tracked artifact file(s):")
        for item in suspicious_files:
            print(f"- {item}")
        print("Remove local pip/tool output artifacts from tracked files before release.")
        raise SystemExit(1)
    print("No suspicious tracked artifact files detected.")


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
            "--collect-all",
            "numpy",
            "--add-data",
            f"{REPO_ROOT / 'nif_patcher.py'}:.",
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


def _resolve_packaged_smoke_binary(artifact_dir: Path) -> Path:
    smoke_root = artifact_dir / "packaging_smoke" / "dist"
    candidates = [
        smoke_root / "generate_textures_smoke",
        smoke_root / "generate_textures_smoke.exe",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    raise SystemExit("Packaging smoke binary was not produced at expected dist path.")


def _packaged_smoke_scenarios() -> list[dict[str, object]]:
    """Return packaged-smoke scenarios with output + semantic assertions."""
    return [
        {
            "name": "vanilla",
            "args": [
                "--render-profile",
                "vanilla",
                "--normal-strength",
                "1.2",
                "--parallax-strength",
                "1.0",
            ],
            "min_outputs": 3,
            "required_suffixes": ("_n.dds", "_p.dds"),
            "forbidden_suffixes": ("_rmaos.dds", "_cm.dds", "_msn.dds"),
            "required_output_families": ("diffuse", "normal", "parallax"),
            "forbidden_output_families": ("rmaos", "complex_cm", "complex_msn"),
        },
        {
            "name": "terrain",
            "args": [
                "--render-profile",
                "terrain",
                "--environment-mask",
                "--environment-mask-mode",
                "standard",
            ],
            "min_outputs": 4,
            "required_suffixes": ("_n.dds", "_p.dds", "_m.dds"),
            "forbidden_suffixes": ("_rmaos.dds", "_cm.dds", "_msn.dds"),
            "required_output_families": ("diffuse", "normal", "parallax", "env_mask"),
            "forbidden_output_families": ("rmaos", "complex_cm", "complex_msn"),
        },
        {
            "name": "community_shaders",
            "args": [
                "--render-profile",
                "community_shaders",
                "--complex-material",
                "--complex-format",
                "cm",
                "--environment-mask",
                "--environment-mask-mode",
                "complex",
            ],
            "min_outputs": 5,
            "required_suffixes": ("_cm.dds", "_m.dds"),
            "forbidden_suffixes": ("_rmaos.dds", "_msn.dds"),
            "required_output_families": ("diffuse", "normal", "complex_cm", "env_mask"),
            "forbidden_output_families": ("rmaos", "complex_msn"),
        },
        {
            "name": "truepbr",
            "args": [
                "--render-profile",
                "truepbr",
                "--rmaos",
                "--normal-strength",
                "1.1",
            ],
            "min_outputs": 4,
            "required_suffixes": ("_rmaos.dds",),
            "required_sidecar_suffixes": ("_rmaos.json",),
            "required_sidecar_json_keys": ("parallax", "displacement_scale", "texture"),
            "required_sidecar_key_types": {
                "parallax": "bool",
                "displacement_scale": "number",
                "texture": "string_nonempty",
            },
            "required_exact_suffix_counts": {"_rmaos.dds": 1, "_rmaos.json": 1},
            "forbidden_suffixes": ("_cm.dds", "_msn.dds"),
            "required_output_families": ("diffuse", "normal", "rmaos"),
            "forbidden_output_families": ("complex_cm", "complex_msn"),
        },
        {
            "name": "pbr_material_shortcut",
            "args": [
                "--render-profile",
                "custom",
                "--pbr-material",
                "--environment-mask",
            ],
            "min_outputs": 5,
            "required_suffixes": ("_cm.dds", "_m.dds", "_p.dds"),
            "forbidden_suffixes": ("_rmaos.dds", "_msn.dds"),
            "required_output_families": ("diffuse", "normal", "parallax", "complex_cm", "env_mask"),
            "forbidden_output_families": ("rmaos", "complex_msn"),
        },
        {
            "name": "enb",
            "args": [
                "--render-profile",
                "enb",
                "--complex-material",
                "--complex-format",
                "msn",
                "--environment-mask",
                "--environment-mask-mode",
                "complex",
                "--parallax-mode",
                "occlusion",
            ],
            "min_outputs": 5,
            "required_suffixes": ("_msn.dds", "_m.dds", "_p.dds"),
            "forbidden_suffixes": ("_rmaos.dds", "_cm.dds"),
            "required_output_families": ("diffuse", "normal", "parallax", "complex_msn", "env_mask"),
            "forbidden_output_families": ("rmaos", "complex_cm"),
        },
        {
            "name": "performance_core",
            "args": [
                "--render-profile",
                "performance",
                "--no-parallax",
            ],
            "min_outputs": 2,
            "required_suffixes": ("_n.dds",),
            "forbidden_suffixes": ("_p.dds", "_rmaos.dds", "_cm.dds", "_msn.dds"),
            "required_output_families": ("diffuse", "normal"),
            "forbidden_output_families": ("parallax", "rmaos", "complex_cm", "complex_msn"),
        },
        {
            "name": "custom_glow_env",
            "args": [
                "--render-profile",
                "custom",
                "--no-parallax",
                "--glow-map",
                "--environment-mask",
                "--environment-mask-mode",
                "standard",
            ],
            "min_outputs": 4,
            "required_suffixes": ("_g.dds", "_m.dds", "_n.dds"),
            "forbidden_suffixes": ("_rmaos.dds", "_cm.dds", "_msn.dds"),
            "required_output_families": ("diffuse", "normal", "glow", "env_mask"),
            "forbidden_output_families": ("parallax", "rmaos", "complex_cm", "complex_msn"),
        },
        {
            "name": "community_shaders_wet_snow",
            "args": [
                "--render-profile",
                "community_shaders",
                "--complex-material",
                "--complex-format",
                "cm",
                "--environment-mask",
                "--environment-mask-mode",
                "complex",
                "--wetness-mask",
                "--snow-mask",
            ],
            "min_outputs": 6,
            "required_suffixes": ("_cm.dds", "_m.dds", "_wt.dds", "_sm.dds"),
            "required_exact_suffix_counts": {"_wt.dds": 1, "_sm.dds": 1, "_cm.dds": 1},
            "forbidden_suffixes": ("_rmaos.dds", "_msn.dds"),
            "required_output_families": ("diffuse", "normal", "complex_cm", "env_mask", "wetness", "snow"),
            "forbidden_output_families": ("rmaos", "complex_msn"),
        },
        {
            "name": "truepbr_plus_aux_maps",
            "args": [
                "--render-profile",
                "truepbr",
                "--rmaos",
                "--ao-map",
                "--roughness-map",
            ],
            "min_outputs": 6,
            "required_suffixes": ("_rmaos.dds", "_ao.dds", "_rough.dds"),
            "required_sidecar_suffixes": ("_rmaos.json",),
            "required_sidecar_json_keys": ("parallax", "displacement_scale", "texture"),
            "required_sidecar_key_types": {
                "parallax": "bool",
                "displacement_scale": "number",
                "texture": "string_nonempty",
            },
            "required_exact_suffix_counts": {
                "_rmaos.dds": 1,
                "_rmaos.json": 1,
                "_ao.dds": 1,
                "_rough.dds": 1,
            },
            "forbidden_suffixes": ("_cm.dds", "_msn.dds"),
            "required_output_families": ("diffuse", "normal", "rmaos", "ao", "roughness"),
            "forbidden_output_families": ("complex_cm", "complex_msn"),
        },
        {
            "name": "vr_safe_core",
            "args": [
                "--render-profile",
                "vr",
                "--no-parallax",
            ],
            "min_outputs": 2,
            "required_suffixes": ("_n.dds",),
            "forbidden_suffixes": ("_p.dds", "_rmaos.dds", "_cm.dds", "_msn.dds"),
            "required_output_families": ("diffuse", "normal"),
            "forbidden_output_families": ("parallax", "rmaos", "complex_cm", "complex_msn"),
        },
        {
            "name": "enb_glow_combo",
            "args": [
                "--render-profile",
                "enb",
                "--complex-material",
                "--complex-format",
                "msn",
                "--environment-mask",
                "--environment-mask-mode",
                "complex",
                "--glow-map",
                "--parallax-mode",
                "occlusion",
            ],
            "min_outputs": 6,
            "required_suffixes": ("_msn.dds", "_m.dds", "_g.dds", "_p.dds"),
            "forbidden_suffixes": ("_rmaos.dds", "_cm.dds"),
            "required_output_families": ("diffuse", "normal", "parallax", "glow", "complex_msn", "env_mask"),
            "forbidden_output_families": ("rmaos", "complex_cm"),
        },
        {
            "name": "truepbr_wet_snow_combo",
            "args": [
                "--render-profile",
                "truepbr",
                "--rmaos",
                "--wetness-mask",
                "--snow-mask",
                "--ao-map",
                "--roughness-map",
            ],
            "min_outputs": 8,
            "required_suffixes": ("_rmaos.dds", "_wt.dds", "_sm.dds", "_ao.dds", "_rough.dds"),
            "required_sidecar_suffixes": ("_rmaos.json",),
            "required_sidecar_json_keys": ("parallax", "displacement_scale", "texture"),
            "required_sidecar_key_types": {
                "parallax": "bool",
                "displacement_scale": "number",
                "texture": "string_nonempty",
            },
            "required_exact_suffix_counts": {
                "_rmaos.dds": 1,
                "_rmaos.json": 1,
                "_wt.dds": 1,
                "_sm.dds": 1,
                "_ao.dds": 1,
                "_rough.dds": 1,
            },
            "forbidden_suffixes": ("_cm.dds", "_msn.dds"),
            "required_output_families": ("diffuse", "normal", "rmaos", "wetness", "snow", "ao", "roughness"),
            "forbidden_output_families": ("complex_cm", "complex_msn"),
        },
        {
            "name": "terrain_no_parallax_env",
            "args": [
                "--render-profile",
                "terrain",
                "--no-parallax",
                "--environment-mask",
                "--environment-mask-mode",
                "standard",
            ],
            "min_outputs": 3,
            "required_suffixes": ("_n.dds", "_m.dds"),
            "forbidden_suffixes": ("_p.dds", "_rmaos.dds", "_cm.dds", "_msn.dds"),
            "required_output_families": ("diffuse", "normal", "env_mask"),
            "forbidden_output_families": ("parallax", "rmaos", "complex_cm", "complex_msn"),
        },
        {
            "name": "enb_no_parallax_glow",
            "args": [
                "--render-profile",
                "enb",
                "--complex-material",
                "--complex-format",
                "msn",
                "--environment-mask",
                "--environment-mask-mode",
                "complex",
                "--glow-map",
                "--no-parallax",
            ],
            "min_outputs": 5,
            "required_suffixes": ("_msn.dds", "_m.dds", "_g.dds"),
            "forbidden_suffixes": ("_p.dds", "_rmaos.dds", "_cm.dds"),
            "required_output_families": ("diffuse", "normal", "glow", "complex_msn", "env_mask"),
            "forbidden_output_families": ("parallax", "rmaos", "complex_cm"),
        },
        {
            "name": "community_shaders_aux_wet_only",
            "args": [
                "--render-profile",
                "community_shaders",
                "--complex-material",
                "--complex-format",
                "cm",
                "--environment-mask",
                "--environment-mask-mode",
                "complex",
                "--wetness-mask",
            ],
            "min_outputs": 5,
            "required_suffixes": ("_cm.dds", "_m.dds", "_wt.dds"),
            "required_exact_suffix_counts": {"_wt.dds": 1, "_cm.dds": 1},
            "forbidden_suffixes": ("_sm.dds", "_rmaos.dds", "_msn.dds"),
            "required_output_families": ("diffuse", "normal", "complex_cm", "env_mask", "wetness"),
            "forbidden_output_families": ("snow", "rmaos", "complex_msn"),
        },
        {
            "name": "truepbr_no_parallax",
            "args": [
                "--render-profile",
                "truepbr",
                "--rmaos",
                "--no-parallax",
            ],
            "min_outputs": 3,
            "required_suffixes": ("_rmaos.dds", "_n.dds"),
            "required_sidecar_suffixes": ("_rmaos.json",),
            "required_sidecar_json_keys": ("parallax", "displacement_scale", "texture"),
            "required_sidecar_key_types": {
                "parallax": "bool",
                "displacement_scale": "number",
                "texture": "string_nonempty",
            },
            "required_exact_suffix_counts": {"_rmaos.dds": 1, "_rmaos.json": 1},
            "forbidden_suffixes": ("_p.dds", "_cm.dds", "_msn.dds"),
            "required_output_families": ("diffuse", "normal", "rmaos"),
            "forbidden_output_families": ("parallax", "complex_cm", "complex_msn"),
        },
        {
            "name": "custom_diffuse_only",
            "args": [
                "--render-profile",
                "custom",
                "--no-normal",
                "--no-parallax",
            ],
            "min_outputs": 1,
            "required_exact_suffix_counts": {
                ".dds": 1,
            },
            "forbidden_suffixes": (
                "_n.dds",
                "_p.dds",
                "_g.dds",
                "_m.dds",
                "_rmaos.dds",
                "_cm.dds",
                "_msn.dds",
                "_wt.dds",
                "_sm.dds",
                "_ao.dds",
                "_rough.dds",
            ),
            "required_output_families": ("diffuse",),
            "forbidden_output_families": (
                "normal",
                "parallax",
                "glow",
                "env_mask",
                "rmaos",
                "complex_cm",
                "complex_msn",
                "wetness",
                "snow",
                "ao",
                "roughness",
            ),
        },
    ]


def _run_packaged_executable_smoke(artifact_dir: Path, *, loops: int = 1) -> None:
    print("\n=== Packaged executable smoke run ===")
    binary = _resolve_packaged_smoke_binary(artifact_dir)
    completed = subprocess.run(
        [str(binary), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        print(completed.stdout)
        print(completed.stderr)
        raise SystemExit(completed.returncode)
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    if "--render-profile" not in output or "--checkpoint-file" not in output:
        raise SystemExit(
            "Packaged executable smoke run did not expose expected CLI options in --help output."
        )

    smoke_io = artifact_dir / "packaging_smoke" / "io"
    smoke_io.mkdir(parents=True, exist_ok=True)
    smoke_input = smoke_io / "sample_input.png"
    smoke_input.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR4nGP8z8Dwn4GBgYGJAQoAHxcCAr7cGDwAAAAASUVORK5CYII="
        )
    )
    loops = max(1, int(loops))
    scenarios = _packaged_smoke_scenarios()
    for loop_index in range(1, loops + 1):
        for scenario in scenarios:
            scenario_name = str(scenario.get("name", "scenario")).strip() or "scenario"
            scenario_args = [str(arg) for arg in (scenario.get("args", []) or [])]
            min_outputs = max(1, int(scenario.get("min_outputs", 1) or 1))
            required_suffixes = tuple(
                str(suffix).strip().lower()
                for suffix in (scenario.get("required_suffixes", ()) or ())
                if str(suffix).strip()
            )
            forbidden_suffixes = tuple(
                str(suffix).strip().lower()
                for suffix in (scenario.get("forbidden_suffixes", ()) or ())
                if str(suffix).strip()
            )
            required_sidecar_suffixes = tuple(
                str(suffix).strip().lower()
                for suffix in (scenario.get("required_sidecar_suffixes", ()) or ())
                if str(suffix).strip()
            )
            required_sidecar_json_keys = tuple(
                str(key).strip()
                for key in (scenario.get("required_sidecar_json_keys", ()) or ())
                if str(key).strip()
            )
            required_sidecar_key_types: dict[str, str] = {}
            raw_sidecar_key_types = scenario.get("required_sidecar_key_types", {}) or {}
            if isinstance(raw_sidecar_key_types, Mapping):
                for raw_key, raw_kind in raw_sidecar_key_types.items():
                    key = str(raw_key).strip()
                    kind = str(raw_kind).strip().lower()
                    if key and kind:
                        required_sidecar_key_types[key] = kind
            required_exact_suffix_counts: dict[str, int] = {}
            raw_exact_counts = scenario.get("required_exact_suffix_counts", {}) or {}
            if isinstance(raw_exact_counts, Mapping):
                for suffix, expected_count in raw_exact_counts.items():
                    suffix_value = str(suffix).strip().lower()
                    if not suffix_value:
                        continue
                    try:
                        parsed_count = int(expected_count)
                    except (TypeError, ValueError):
                        continue
                    if parsed_count >= 0:
                        required_exact_suffix_counts[suffix_value] = parsed_count
            smoke_out = smoke_io / f"out_loop{loop_index}_{scenario_name}"
            smoke_out.mkdir(parents=True, exist_ok=True)
            run_completed = subprocess.run(
                [
                    str(binary),
                    str(smoke_input),
                    "--output-dir",
                    str(smoke_out),
                    *scenario_args,
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            if run_completed.returncode != 0:
                print(run_completed.stdout)
                print(run_completed.stderr)
                raise SystemExit(run_completed.returncode)
            produced_dds = sorted(smoke_out.glob("*.dds"))
            if len(produced_dds) < min_outputs:
                raise SystemExit(
                    f"Packaged executable smoke run for '{scenario_name}' produced "
                    f"{len(produced_dds)} DDS file(s); expected at least {min_outputs}."
                )
            for output_path in produced_dds:
                if output_path.stat().st_size <= 0:
                    raise SystemExit(
                        f"Packaged executable smoke run for '{scenario_name}' produced empty output file '{output_path.name}'."
                    )
            produced_names = [path.name.lower() for path in produced_dds]
            specialized_suffixes = (
                "_n.dds",
                "_p.dds",
                "_g.dds",
                "_m.dds",
                "_rmaos.dds",
                "_ramos.dds",
                "_cm.dds",
                "_c.dds",
                "_msn.dds",
                "_wt.dds",
                "_sm.dds",
                "_ao.dds",
                "_rough.dds",
            )
            produced_families: set[str] = set()
            for name in produced_names:
                if name.endswith("_n.dds"):
                    produced_families.add("normal")
                elif name.endswith("_p.dds"):
                    produced_families.add("parallax")
                elif name.endswith("_g.dds"):
                    produced_families.add("glow")
                elif name.endswith("_rmaos.dds") or name.endswith("_ramos.dds"):
                    produced_families.add("rmaos")
                elif name.endswith("_cm.dds") or name.endswith("_c.dds"):
                    produced_families.add("complex_cm")
                elif name.endswith("_msn.dds"):
                    produced_families.add("complex_msn")
                elif name.endswith("_m.dds"):
                    produced_families.add("env_mask")
                elif name.endswith("_wt.dds"):
                    produced_families.add("wetness")
                elif name.endswith("_sm.dds"):
                    produced_families.add("snow")
                elif name.endswith("_ao.dds"):
                    produced_families.add("ao")
                elif name.endswith("_rough.dds"):
                    produced_families.add("roughness")
                elif name.endswith(".dds") and not name.endswith(specialized_suffixes):
                    produced_families.add("diffuse")
                elif name.endswith("_d.dds"):
                    produced_families.add("diffuse")
            required_output_families = tuple(
                str(value).strip().lower()
                for value in (scenario.get("required_output_families", ()) or ())
                if str(value).strip()
            )
            forbidden_output_families = tuple(
                str(value).strip().lower()
                for value in (scenario.get("forbidden_output_families", ()) or ())
                if str(value).strip()
            )
            for suffix in required_suffixes:
                if not any(name.endswith(suffix) for name in produced_names):
                    raise SystemExit(
                        f"Packaged executable smoke run for '{scenario_name}' is missing required output suffix '{suffix}'. "
                        f"Produced: {produced_names}"
                    )
            for suffix in forbidden_suffixes:
                if any(name.endswith(suffix) for name in produced_names):
                    raise SystemExit(
                        f"Packaged executable smoke run for '{scenario_name}' produced forbidden output suffix '{suffix}'. "
                        f"Produced: {produced_names}"
                    )
            for family in required_output_families:
                if family not in produced_families:
                    raise SystemExit(
                        f"Packaged executable smoke run for '{scenario_name}' is missing required output family "
                        f"'{family}'. Produced families: {sorted(produced_families)}"
                    )
            for family in forbidden_output_families:
                if family in produced_families:
                    raise SystemExit(
                        f"Packaged executable smoke run for '{scenario_name}' produced forbidden output family "
                        f"'{family}'. Produced families: {sorted(produced_families)}"
                    )
            if required_sidecar_suffixes:
                produced_all = [path.name.lower() for path in smoke_out.rglob("*") if path.is_file()]
                for suffix in required_sidecar_suffixes:
                    if not any(name.endswith(suffix) for name in produced_all):
                        raise SystemExit(
                            f"Packaged executable smoke run for '{scenario_name}' is missing required sidecar suffix '{suffix}'. "
                            f"Produced files: {produced_all}"
                        )
            if required_exact_suffix_counts:
                produced_all = [path.name.lower() for path in smoke_out.rglob("*") if path.is_file()]
                for suffix, expected_count in required_exact_suffix_counts.items():
                    actual_count = sum(1 for name in produced_all if name.endswith(suffix))
                    if actual_count != expected_count:
                        raise SystemExit(
                            f"Packaged executable smoke run for '{scenario_name}' expected exactly {expected_count} "
                            f"output file(s) with suffix '{suffix}' but found {actual_count}. Produced files: {produced_all}"
                        )
            if required_sidecar_json_keys:
                sidecar_jsons = sorted(path for path in smoke_out.rglob("*.json") if path.is_file())
                if not sidecar_jsons:
                    raise SystemExit(
                        f"Packaged executable smoke run for '{scenario_name}' expected JSON sidecars with keys "
                        f"{required_sidecar_json_keys}, but none were produced."
                    )
                for sidecar_path in sidecar_jsons:
                    try:
                        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
                    except Exception as exc:
                        raise SystemExit(
                            f"Packaged executable smoke run for '{scenario_name}' produced invalid JSON sidecar "
                            f"'{sidecar_path.name}': {exc}"
                        ) from exc
                    if isinstance(payload, Mapping):
                        materials = payload.get("materials", [])
                        if isinstance(materials, list) and materials:
                            probe = materials[0]
                        else:
                            probe = payload
                    elif isinstance(payload, list) and payload and isinstance(payload[0], Mapping):
                        probe = payload[0]
                    else:
                        probe = {}
                    if not isinstance(probe, Mapping):
                        raise SystemExit(
                            f"Packaged executable smoke run for '{scenario_name}' sidecar '{sidecar_path.name}' "
                            "does not contain an object payload."
                        )
                    missing_keys = [key for key in required_sidecar_json_keys if key not in probe]
                    if missing_keys:
                        raise SystemExit(
                            f"Packaged executable smoke run for '{scenario_name}' sidecar '{sidecar_path.name}' "
                            f"is missing keys: {missing_keys}"
                        )
                    for key, expected_kind in required_sidecar_key_types.items():
                        if key not in probe:
                            continue
                        value = probe.get(key)
                        if expected_kind == "bool" and not isinstance(value, bool):
                            raise SystemExit(
                                f"Packaged executable smoke run for '{scenario_name}' sidecar '{sidecar_path.name}' "
                                f"key '{key}' expected bool, got {type(value).__name__}."
                            )
                        if expected_kind == "number" and not isinstance(value, (int, float)):
                            raise SystemExit(
                                f"Packaged executable smoke run for '{scenario_name}' sidecar '{sidecar_path.name}' "
                                f"key '{key}' expected number, got {type(value).__name__}."
                            )
                        if expected_kind == "string_nonempty" and (
                            not isinstance(value, str) or not value.strip()
                        ):
                            raise SystemExit(
                                f"Packaged executable smoke run for '{scenario_name}' sidecar '{sidecar_path.name}' "
                                f"key '{key}' expected non-empty string."
                            )


def _run_packaged_runtime_env_stress(artifact_dir: Path) -> list[dict[str, object]]:
    """Run packaged CLI smoke probes across runtime environment variants."""
    print("\n=== Packaged runtime environment stress probes ===")
    binary = _resolve_packaged_smoke_binary(artifact_dir)
    smoke_io = artifact_dir / "packaging_smoke" / "runtime_env_stress"
    smoke_io.mkdir(parents=True, exist_ok=True)
    smoke_input = smoke_io / "sample_input.png"
    smoke_input.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR4nGP8z8Dwn4GBgYGJAQoAHxcCAr7cGDwAAAAASUVORK5CYII="
        )
    )
    probes: tuple[tuple[str, dict[str, str]], ...] = (
        ("default", {}),
        ("utf8_mode", {"PYTHONUTF8": "1"}),
        ("c_locale", {"LC_ALL": "C"}),
    )
    scenario = next(
        (entry for entry in _packaged_smoke_scenarios() if str(entry.get("name", "")) == "vanilla"),
        _packaged_smoke_scenarios()[0],
    )
    scenario_args = [str(arg) for arg in (scenario.get("args", []) or [])]
    results: list[dict[str, object]] = []
    for name, env_overrides in probes:
        env = dict(subprocess.os.environ)
        env.update(env_overrides)
        help_run = subprocess.run(
            [str(binary), "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
        )
        if help_run.returncode != 0:
            print(help_run.stdout)
            print(help_run.stderr)
            raise SystemExit(help_run.returncode)
        help_output = (help_run.stdout or "") + "\n" + (help_run.stderr or "")
        if "--render-profile" not in help_output:
            raise SystemExit(
                f"Packaged runtime env probe '{name}' did not expose expected --help options."
            )
        out_dir = smoke_io / f"out_{name}"
        out_dir.mkdir(parents=True, exist_ok=True)
        run = subprocess.run(
            [
                str(binary),
                str(smoke_input),
                "--output-dir",
                str(out_dir),
                *scenario_args,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
        )
        if run.returncode != 0:
            print(run.stdout)
            print(run.stderr)
            raise SystemExit(run.returncode)
        produced = sorted(out_dir.glob("*.dds"))
        if not produced:
            raise SystemExit(
                f"Packaged runtime env probe '{name}' produced no DDS outputs for scenario '{scenario.get('name', 'scenario')}'."
            )
        results.append(
            {
                "probe": name,
                "env_overrides": env_overrides,
                "scenario": str(scenario.get("name", "scenario")),
                "dds_count": len(produced),
                "status": "pass",
            }
        )
    return results


def _write_packaged_accessibility_acceptance_artifacts(
    *,
    artifact_dir: Path,
    step_status: list[tuple[str, str]],
    runtime_env_probes: list[dict[str, object]],
) -> tuple[Path, Path]:
    generated_at = datetime.now(timezone.utc).isoformat()
    manual_checks: list[dict[str, object]] = [
        {
            "id": "keyboard_focus_order",
            "platform": "windows_packaged",
            "status": "pending_manual",
            "acceptance": "Keyboard-only traversal reaches all primary controls in logical order with visible focus.",
        },
        {
            "id": "screen_reader_control_naming",
            "platform": "windows_packaged",
            "status": "pending_manual",
            "acceptance": "Screen reader announces main controls, toggles, and status labels with understandable names.",
        },
        {
            "id": "status_announcement_clarity",
            "platform": "windows_packaged",
            "status": "pending_manual",
            "acceptance": "Progress/status updates are understandable and non-ambiguous during long batch runs.",
        },
        {
            "id": "high_dpi_scaling",
            "platform": "windows_packaged",
            "status": "pending_manual",
            "acceptance": "UI remains readable and usable at common high-DPI scales (125/150/200%).",
        },
    ]
    automated = [dict(row) for row in runtime_env_probes]
    step_rows = [
        {"name": name, "status": status}
        for name, status in step_status
        if name.startswith("Packaging smoke build")
        or name.startswith("Packaged executable smoke run")
        or name.startswith("Packaged runtime environment stress")
    ]
    payload: dict[str, object] = {
        "generated_at_utc": generated_at,
        "automated_checks": automated,
        "related_release_steps": step_rows,
        "manual_checks": manual_checks,
    }
    json_path = artifact_dir / "packaged_accessibility_acceptance.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# Packaged Accessibility & Native Display Acceptance",
        "",
        f"Generated: {generated_at}",
        "",
        "## Automated qualification probes",
        "",
        "| Probe | Scenario | DDS outputs | Status |",
        "| --- | --- | ---: | --- |",
    ]
    for row in automated:
        lines.append(
            f"| `{row.get('probe', '')}` | `{row.get('scenario', '')}` | {int(row.get('dds_count', 0) or 0)} | {row.get('status', 'unknown')} |"
        )
    lines.extend(
        [
            "",
            "## Manual acceptance checklist (Windows packaged app first)",
            "",
        ]
    )
    for row in manual_checks:
        lines.append(f"- [ ] **{row['id']}** — {row['acceptance']}")
    md_path = artifact_dir / "packaged_accessibility_acceptance.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def _collect_localization_coverage(
    translations_dir: Path,
) -> dict[str, object]:
    report: dict[str, object] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "translations_dir": str(translations_dir),
        "languages": [],
        "summary": {},
    }
    if not translations_dir.exists():
        raise SystemExit(f"Translations directory does not exist: {translations_dir}")
    catalogs: dict[str, dict[str, str]] = {}
    for catalog_path in sorted(translations_dir.glob("*.json")):
        language = catalog_path.stem.strip().lower()
        try:
            payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SystemExit(f"Invalid translation JSON for {catalog_path.name}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise SystemExit(f"Translation catalog {catalog_path.name} must contain an object root.")
        raw_strings = payload.get("strings", {})
        if not isinstance(raw_strings, Mapping):
            raise SystemExit(f"Translation catalog {catalog_path.name} must define an object at 'strings'.")
        catalogs[language] = {
            str(key): str(value)
            for key, value in raw_strings.items()
            if isinstance(key, str) and isinstance(value, str)
        }
    if "en" not in catalogs:
        raise SystemExit("Missing required base translation catalog: assets/translations/en.json")
    base_keys = set(catalogs["en"].keys())
    language_rows: list[dict[str, object]] = []
    for language, strings in sorted(catalogs.items()):
        keys = set(strings.keys())
        missing = sorted(base_keys - keys)
        extra = sorted(keys - base_keys)
        coverage = 1.0 if not base_keys else ((len(base_keys) - len(missing)) / len(base_keys))
        identical_to_base = sum(
            1
            for key in keys & base_keys
            if strings.get(key, "") == catalogs["en"].get(key, "")
        )
        identical_ratio = (
            0.0
            if not base_keys
            else float(identical_to_base) / float(len(base_keys))
        )
        language_rows.append(
            {
                "language": language,
                "string_count": len(keys),
                "coverage_ratio": round(float(coverage), 4),
                "identical_to_base_count": int(identical_to_base),
                "identical_to_base_ratio": round(float(identical_ratio), 4),
                "missing_count": len(missing),
                "extra_count": len(extra),
                "missing_examples": missing[:20],
                "extra_examples": extra[:20],
            }
        )
    report["languages"] = language_rows
    report["summary"] = {
        "base_language": "en",
        "base_string_count": len(base_keys),
        "language_count": len(language_rows),
        "languages_with_missing_strings": sum(
            1 for row in language_rows if int(row.get("missing_count", 0) or 0) > 0
        ),
        "languages_with_identical_to_base_majority": sum(
            1
            for row in language_rows
            if str(row.get("language", "")) != "en"
            and float(row.get("identical_to_base_ratio", 0.0) or 0.0) >= 0.5
        ),
    }
    return report


def _render_localization_coverage_markdown(report: dict[str, object]) -> str:
    lines = [
        "# Localization Coverage Report",
        "",
        f"Generated: {report.get('generated_at_utc', '')}",
        "",
        "| Language | Strings | Coverage | Shared with `en` | Missing | Extra |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    rows = report.get("languages", [])
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            coverage_ratio = float(row.get("coverage_ratio", 0.0) or 0.0) * 100.0
            identical_ratio = float(row.get("identical_to_base_ratio", 0.0) or 0.0) * 100.0
            lines.append(
                f"| `{row.get('language', '')}` | {int(row.get('string_count', 0) or 0)} | "
                f"{coverage_ratio:.1f}% | {identical_ratio:.1f}% | {int(row.get('missing_count', 0) or 0)} | "
                f"{int(row.get('extra_count', 0) or 0)} |"
            )
            missing_examples = row.get("missing_examples", [])
            if isinstance(missing_examples, list) and missing_examples:
                lines.append(
                    f"|  |  |  |  | missing examples: `{', '.join(str(v) for v in missing_examples[:5])}` |  |"
                )
    return "\n".join(lines) + "\n"


def _run_localization_sweep(
    artifact_dir: Path,
    *,
    strict_completeness: bool = False,
) -> tuple[dict[str, object], Path, Path]:
    print("\n=== Localization sweep ===")
    report = _collect_localization_coverage(REPO_ROOT / "assets" / "translations")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    json_path = artifact_dir / "localization_coverage_report.json"
    md_path = artifact_dir / "localization_coverage_report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_render_localization_coverage_markdown(report), encoding="utf-8")
    if strict_completeness:
        rows = report.get("languages", [])
        if isinstance(rows, list):
            incomplete = [
                str(row.get("language", ""))
                for row in rows
                if isinstance(row, Mapping)
                and str(row.get("language", "")) != "en"
                and int(row.get("missing_count", 0) or 0) > 0
            ]
            if incomplete:
                raise SystemExit(
                    "Strict localization completeness enabled and missing strings remain for: "
                    + ", ".join(incomplete)
                )
    return report, json_path, md_path


def _conflict_base_code(conflict_code: str) -> str:
    parts = conflict_code.split(".")
    if len(parts) >= 3:
        return ".".join(parts[:-2])
    return conflict_code


def _derive_remediation_mode(remediation_steps: list[str]) -> str:
    has_rebuild_steps = any(step.startswith("set_slot") for step in remediation_steps)
    has_disable_steps = any(step.startswith("disable_") or step.startswith("clear_") for step in remediation_steps)
    has_enable_steps = any(step.startswith("enable_") for step in remediation_steps)
    if has_rebuild_steps and has_disable_steps:
        return "mixed_rebuild_and_disable"
    if has_rebuild_steps:
        return "rebuild"
    if has_disable_steps:
        return "disable"
    if has_enable_steps:
        return "enable_or_flag_only"
    return "manual_or_noop"


def _classify_intended_difference_bucket(
    *,
    profile: str,
    remediation_mode: str,
    fallback_used: bool,
    intentional_strategy_difference: bool,
    local_strategy: str,
    safety_difference_note: str,
) -> str:
    lowered_profile = profile.lower()
    lowered_local = local_strategy.lower()
    lowered_note = safety_difference_note.lower()
    if (
        "partial recover" in lowered_local
        or "partial recover" in lowered_note
        or "partial-recover" in lowered_local
        or "partial-recover" in lowered_note
    ):
        return "partial_recoverability_guarded"
    if "fallout" in lowered_profile or "guarded fallout" in lowered_local or "fallout" in lowered_note:
        return "guarded_fallout"
    if "destructive" in lowered_local or "destructive" in lowered_note:
        return "destructive_disabled"
    if fallback_used or remediation_mode in {"disable", "mixed_rebuild_and_disable"}:
        return "safety_first"
    if intentional_strategy_difference:
        return "safety_first"
    return "none"


def _load_realmod_pack_payload(
    extra_pack_files: list[Path] | None = None,
) -> dict[str, object]:
    from tests.test_nif_patcher import (
        _FIXTURE_EXTERNAL_BROKEN_PACK_DELTA_SWEEP,
        _FIXTURE_EXTERNAL_BROKEN_PACK_DELTA_SWEEP_ADDITIONAL,
        _FIXTURE_REALMOD_SAMPLE_PACKS,
        _load_fixture_corpus_payload,
    )

    payload = _load_fixture_corpus_payload(_FIXTURE_REALMOD_SAMPLE_PACKS)
    packs = payload.get("packs", [])
    merged_packs = [pack for pack in packs if isinstance(pack, dict)] if isinstance(packs, list) else []
    for external_fixture in (
        _FIXTURE_EXTERNAL_BROKEN_PACK_DELTA_SWEEP,
        _FIXTURE_EXTERNAL_BROKEN_PACK_DELTA_SWEEP_ADDITIONAL,
    ):
        external_payload = _load_fixture_corpus_payload(external_fixture)
        external_packs = external_payload.get("packs", [])
        if isinstance(external_packs, list):
            for pack in external_packs:
                if isinstance(pack, dict):
                    merged_packs.append(pack)
    for pack_file in extra_pack_files or []:
        if not pack_file.exists():
            raise SystemExit(f"Extra realmod pack file does not exist: {pack_file}")
        extra_payload = _load_fixture_corpus_payload(pack_file)
        extra_packs = extra_payload.get("packs", [])
        if not isinstance(extra_packs, list):
            raise SystemExit(f"Extra realmod pack file {pack_file} must define a 'packs' list.")
        for pack in extra_packs:
            if not isinstance(pack, dict):
                continue
            merged_packs.append(pack)
    return {"packs": merged_packs}


def _build_realmod_family_trend_snapshot(
    *,
    extra_pack_files: list[Path] | None = None,
) -> dict[str, object]:
    from nif_patcher import validate_nif_for_parallax
    from tests.test_nif_patcher import (
        _materialize_fixture_corpus,
    )

    payload = _load_realmod_pack_payload(extra_pack_files=extra_pack_files)
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
                        "strategy_aligned_count": 0,
                        "strategy_annotated_count": 0,
                        "top_conflicts": {},
                    },
                )
                family_payload["case_count"] = int(family_payload["case_count"]) + 1
                pgpatcher_strategy = str(case.get("pgpatcher_strategy", "") or "").strip()
                local_strategy = str(case.get("local_strategy", "") or "").strip()
                intentional_strategy_difference = bool(case.get("intentional_strategy_difference", False))
                if pgpatcher_strategy and local_strategy:
                    family_payload["strategy_annotated_count"] = int(family_payload["strategy_annotated_count"]) + 1
                    if not intentional_strategy_difference:
                        family_payload["strategy_aligned_count"] = int(family_payload["strategy_aligned_count"]) + 1
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
                        "strategy_aligned_count": int(row.get("strategy_aligned_count", 0)),
                        "strategy_annotated_count": int(row.get("strategy_annotated_count", 0)),
                        "strategy_alignment_ratio": (
                            float(int(row.get("strategy_aligned_count", 0)))
                            / float(int(row.get("strategy_annotated_count", 0)))
                            if int(row.get("strategy_annotated_count", 0)) > 0
                            else 0.0
                        ),
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


def _build_realmod_side_by_side_delta_report(
    *,
    extra_pack_files: list[Path] | None = None,
) -> dict[str, object]:
    from nif_patcher import (
        build_auto_remediation_patch_options,
        validate_nif_for_parallax,
    )
    from tests.test_nif_patcher import (
        _materialize_fixture_corpus,
    )

    payload = _load_realmod_pack_payload(extra_pack_files=extra_pack_files)
    packs = payload.get("packs", [])
    if not isinstance(packs, list):
        packs = []
    rows: list[dict[str, object]] = []

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

            for nif_path, validation in zip(corpus, validations):
                case = case_map.get(nif_path.stem, {})
                code_rows = [group.code for group in validation.conflict_report]
                base_families = sorted({_conflict_base_code(code) for code in code_rows})
                _, rem_steps_tuple = build_auto_remediation_patch_options(
                    validation.nif_path,
                    code_rows,
                    backup=False,
                )
                rem_steps = [str(step) for step in rem_steps_tuple]
                remediation_mode = _derive_remediation_mode(rem_steps)
                fallback_used = remediation_mode in {"disable", "mixed_rebuild_and_disable"}
                profile = str(case.get("profile", validation.detected_game_profile or ""))
                local_strategy = str(case.get("local_strategy", "") or "")
                intentional_strategy_difference = bool(case.get("intentional_strategy_difference", False))
                safety_difference_note = str(case.get("safety_difference_note", "") or "")
                difference_bucket = _classify_intended_difference_bucket(
                    profile=profile,
                    remediation_mode=remediation_mode,
                    fallback_used=fallback_used,
                    intentional_strategy_difference=intentional_strategy_difference,
                    local_strategy=local_strategy,
                    safety_difference_note=safety_difference_note,
                )
                rows.append(
                    {
                        "pack_id": pack_id,
                        "case_id": str(case.get("id", nif_path.stem)),
                        "family": str(case.get("family", "unknown") or "unknown"),
                        "profile": profile,
                        "shader_layout": str(case.get("shader_layout", "") or ""),
                        "detected_conflict_families": base_families,
                        "detected_conflict_codes": code_rows,
                        "remediation_steps": rem_steps,
                        "remediation_mode": remediation_mode,
                        "fallback_used": fallback_used,
                        "pgpatcher_strategy": str(case.get("pgpatcher_strategy", "") or ""),
                        "local_strategy": local_strategy,
                        "intentional_strategy_difference": intentional_strategy_difference,
                        "safety_difference_note": safety_difference_note,
                        "intended_difference_bucket": difference_bucket,
                    }
                )

    bucket_counts: dict[str, int] = {}
    for row in rows:
        bucket = str(row.get("intended_difference_bucket", "none") or "none")
        bucket_counts[bucket] = int(bucket_counts.get(bucket, 0)) + 1

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_count": len(rows),
        "pack_count": len(packs),
        "intended_difference_bucket_counts": dict(sorted(bucket_counts.items())),
        "cases": rows,
    }


def _build_realmod_delta_mismatch_summary(
    realmod_delta_report: dict[str, object],
) -> dict[str, object]:
    rows = realmod_delta_report.get("cases", [])
    pack_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    remediation_mode_counts: dict[str, int] = {}
    base_conflict_counts: dict[str, int] = {}
    bucket_counts: dict[str, int] = {}
    fallback_case_count = 0
    strategy_diff_count = 0
    case_count = 0
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            case_count += 1
            pack_id = str(row.get("pack_id", "pack") or "pack")
            family = str(row.get("family", "unknown") or "unknown")
            remediation_mode = str(row.get("remediation_mode", "manual_or_noop") or "manual_or_noop")
            bucket = str(row.get("intended_difference_bucket", "none") or "none")
            pack_counts[pack_id] = int(pack_counts.get(pack_id, 0)) + 1
            family_counts[family] = int(family_counts.get(family, 0)) + 1
            remediation_mode_counts[remediation_mode] = int(remediation_mode_counts.get(remediation_mode, 0)) + 1
            bucket_counts[bucket] = int(bucket_counts.get(bucket, 0)) + 1
            if bool(row.get("fallback_used", False)):
                fallback_case_count += 1
            if bool(row.get("intentional_strategy_difference", False)):
                strategy_diff_count += 1
            codes = row.get("detected_conflict_codes", [])
            if isinstance(codes, list):
                for code in codes:
                    base = _conflict_base_code(str(code))
                    base_conflict_counts[base] = int(base_conflict_counts.get(base, 0)) + 1
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_count": case_count,
        "fallback_case_count": fallback_case_count,
        "intentional_strategy_difference_count": strategy_diff_count,
        "pack_case_counts": dict(sorted(pack_counts.items(), key=lambda item: (-int(item[1]), item[0]))),
        "family_case_counts": dict(sorted(family_counts.items(), key=lambda item: (-int(item[1]), item[0]))),
        "remediation_mode_counts": dict(
            sorted(remediation_mode_counts.items(), key=lambda item: (-int(item[1]), item[0]))
        ),
        "difference_bucket_counts": dict(sorted(bucket_counts.items(), key=lambda item: (-int(item[1]), item[0]))),
        "top_conflict_families": [
            {"base_code": code, "count": int(count)}
            for code, count in sorted(base_conflict_counts.items(), key=lambda item: (-int(item[1]), item[0]))[:20]
        ],
    }


def _render_realmod_delta_mismatch_summary_markdown(summary: dict[str, object]) -> str:
    top_conflicts = summary.get("top_conflict_families", [])
    remediation_mode_counts = summary.get("remediation_mode_counts", {})
    bucket_counts = summary.get("difference_bucket_counts", {})
    lines = [
        "# NIF real-sample parity mismatch summary",
        "",
        f"- Generated: {summary.get('generated_at_utc', '')}",
        f"- Cases: {summary.get('case_count', 0)}",
        f"- Cases using fallback remediation modes: {summary.get('fallback_case_count', 0)}",
        f"- Intentional strategy-difference cases: {summary.get('intentional_strategy_difference_count', 0)}",
        "",
        "## Remediation modes",
        "",
        "| Mode | Cases |",
        "| --- | ---: |",
    ]
    if isinstance(remediation_mode_counts, dict):
        for mode, count in remediation_mode_counts.items():
            lines.append(f"| `{str(mode).replace('|', '\\|')}` | {int(count)} |")
    lines.extend(
        [
            "",
            "## Difference buckets",
            "",
            "| Bucket | Cases |",
            "| --- | ---: |",
        ]
    )
    if isinstance(bucket_counts, dict):
        for bucket, count in bucket_counts.items():
            lines.append(f"| `{str(bucket).replace('|', '\\|')}` | {int(count)} |")
    lines.extend(
        [
            "",
            "## Top conflict families",
            "",
            "| Conflict family | Count |",
            "| --- | ---: |",
        ]
    )
    if isinstance(top_conflicts, list):
        for row in top_conflicts:
            if not isinstance(row, dict):
                continue
            code = str(row.get("base_code", "")).replace("|", "\\|")
            count = int(row.get("count", 0) or 0)
            lines.append(f"| `{code}` | {count} |")
    lines.append("")
    return "\n".join(lines)


def _build_realmod_fallback_guard_report(
    *,
    realmod_delta_report: dict[str, object],
    extra_pack_files: list[Path] | None = None,
) -> dict[str, object]:
    payload = _load_realmod_pack_payload(extra_pack_files=extra_pack_files)
    packs = payload.get("packs", [])
    pack_thresholds: dict[str, dict[str, object]] = {}
    if isinstance(packs, list):
        for pack in packs:
            if not isinstance(pack, dict):
                continue
            pack_id = str(pack.get("id", "pack")).strip() or "pack"
            pack_thresholds[pack_id] = {
                "expected_max_fallback_ratio": float(pack.get("expected_max_fallback_ratio", 0.02)),
                "expected_max_fallback_groups": pack.get("expected_max_fallback_groups"),
            }
    per_pack: dict[str, dict[str, object]] = {}
    cases = realmod_delta_report.get("cases", [])
    if isinstance(cases, list):
        for row in cases:
            if not isinstance(row, dict):
                continue
            pack_id = str(row.get("pack_id", "pack")).strip() or "pack"
            payload_row = per_pack.setdefault(
                pack_id,
                {
                    "pack_id": pack_id,
                    "case_count": 0,
                    "total_conflict_groups": 0,
                    "fallback_or_unknown_groups": 0,
                    "generic_unsupported_header_groups": 0,
                },
            )
            payload_row["case_count"] = int(payload_row["case_count"]) + 1
            codes = row.get("detected_conflict_codes", [])
            if isinstance(codes, list):
                payload_row["total_conflict_groups"] = int(payload_row["total_conflict_groups"]) + len(codes)
                payload_row["fallback_or_unknown_groups"] = int(payload_row["fallback_or_unknown_groups"]) + sum(
                    1 for code in codes if str(code).startswith("fallback_or_unknown.")
                )
                payload_row["generic_unsupported_header_groups"] = int(
                    payload_row["generic_unsupported_header_groups"]
                ) + sum(
                    1
                    for code in codes
                    if str(code).startswith("unsupported_header.")
                    and len(str(code).split(".")) == 3
                )
    rows: list[dict[str, object]] = []
    violations: list[dict[str, object]] = []
    for pack_id, observed in sorted(per_pack.items(), key=lambda item: item[0]):
        thresholds = pack_thresholds.get(pack_id, {})
        ratio = float(thresholds.get("expected_max_fallback_ratio", 0.02))
        explicit_max = thresholds.get("expected_max_fallback_groups")
        total_conflict_groups = int(observed.get("total_conflict_groups", 0) or 0)
        fallback_groups = int(observed.get("fallback_or_unknown_groups", 0) or 0)
        generic_unsupported = int(observed.get("generic_unsupported_header_groups", 0) or 0)
        if explicit_max is None:
            allowed_fallback_groups = int(total_conflict_groups * ratio)
        else:
            allowed_fallback_groups = int(explicit_max)
        pass_guard = fallback_groups <= allowed_fallback_groups and generic_unsupported == 0
        row = {
            **observed,
            "expected_max_fallback_ratio": ratio,
            "expected_max_fallback_groups": explicit_max,
            "allowed_fallback_groups": allowed_fallback_groups,
            "pass": pass_guard,
        }
        rows.append(row)
        if not pass_guard:
            violations.append(
                {
                    "pack_id": pack_id,
                    "fallback_or_unknown_groups": fallback_groups,
                    "allowed_fallback_groups": allowed_fallback_groups,
                    "generic_unsupported_header_groups": generic_unsupported,
                }
            )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pack_count": len(rows),
        "packs": rows,
        "violations": violations,
    }


def _render_realmod_fallback_guard_markdown(report: dict[str, object]) -> str:
    rows = report.get("packs", [])
    lines = [
        "# NIF real-sample fallback regression guard",
        "",
        f"- Generated: {report.get('generated_at_utc', '')}",
        f"- Pack count: {report.get('pack_count', 0)}",
        f"- Violations: {len(report.get('violations', [])) if isinstance(report.get('violations'), list) else 0}",
        "",
        "| Pack | Cases | Conflict groups | Fallback groups | Allowed fallback groups | Generic unsupported groups | Pass |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            pack_id = str(row.get("pack_id", "pack")).replace("|", "\\|")
            lines.append(
                f"| `{pack_id}` | {int(row.get('case_count', 0) or 0)} | "
                f"{int(row.get('total_conflict_groups', 0) or 0)} | "
                f"{int(row.get('fallback_or_unknown_groups', 0) or 0)} | "
                f"{int(row.get('allowed_fallback_groups', 0) or 0)} | "
                f"{int(row.get('generic_unsupported_header_groups', 0) or 0)} | "
                f"{'yes' if bool(row.get('pass', False)) else 'no'} |"
            )
    lines.append("")
    return "\n".join(lines)


def _assert_realmod_fallback_guard(report: dict[str, object]) -> None:
    violations = report.get("violations", [])
    if not isinstance(violations, list) or not violations:
        return
    first = violations[0]
    if not isinstance(first, dict):
        raise SystemExit("Realmod fallback guard failed with malformed violation payload.")
    raise SystemExit(
        "Realmod fallback guard failed: "
        f"{len(violations)} pack(s) exceeded fallback thresholds or emitted generic unsupported_header groups. "
        f"First violation: pack={first.get('pack_id')}, "
        f"fallback={first.get('fallback_or_unknown_groups')}/{first.get('allowed_fallback_groups')}, "
        f"generic_unsupported={first.get('generic_unsupported_header_groups')}."
    )


def _load_latest_seed_report(
    seed_files: list[Path] | None,
    *,
    required_top_level_keys: tuple[str, ...] = (),
) -> dict[str, object]:
    if not seed_files:
        return {}
    candidates: list[tuple[float, dict[str, object]]] = []
    for seed in seed_files:
        if seed is None or not seed.exists():
            continue
        try:
            mtime = seed.stat().st_mtime
            payload = json.loads(seed.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        candidates.append((mtime, payload))
    if not candidates:
        return {}
    candidates.sort(key=lambda item: item[0], reverse=True)
    if required_top_level_keys:
        required = tuple(str(key).strip() for key in required_top_level_keys if str(key).strip())
        for _, payload in candidates:
            if all(key in payload for key in required):
                return payload
    return candidates[0][1]


def _resolve_seed_realmod_delta_files(seed_files: list[Path] | None) -> list[Path]:
    resolved: list[Path] = []
    seen: set[str] = set()
    for seed in seed_files or []:
        if seed is None:
            continue
        key = str(seed)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(seed)
    if _DEFAULT_REALMOD_DELTA_SEED.exists():
        seed_key = str(_DEFAULT_REALMOD_DELTA_SEED)
        if seed_key not in seen:
            resolved.append(_DEFAULT_REALMOD_DELTA_SEED)
    return resolved


def _build_realmod_fallback_drift_report(
    *,
    current_fallback_guard_report: dict[str, object],
    seed_realmod_delta_files: list[Path] | None = None,
    extra_pack_files: list[Path] | None = None,
) -> dict[str, object]:
    current_rows = current_fallback_guard_report.get("packs", [])
    if not isinstance(current_rows, list):
        current_rows = []
    current_by_pack: dict[str, dict[str, object]] = {}
    for row in current_rows:
        if not isinstance(row, dict):
            continue
        pack_id = str(row.get("pack_id", "pack")).strip() or "pack"
        current_by_pack[pack_id] = row

    prior_delta_report = _load_latest_seed_report(
        seed_realmod_delta_files,
        required_top_level_keys=("cases",),
    )
    prior_by_pack: dict[str, dict[str, object]] = {}
    if prior_delta_report:
        prior_fallback_report = _build_realmod_fallback_guard_report(
            realmod_delta_report=prior_delta_report,
            extra_pack_files=extra_pack_files,
        )
        prior_rows = prior_fallback_report.get("packs", [])
        if isinstance(prior_rows, list):
            for row in prior_rows:
                if not isinstance(row, dict):
                    continue
                pack_id = str(row.get("pack_id", "pack")).strip() or "pack"
                prior_by_pack[pack_id] = row

    rows: list[dict[str, object]] = []
    for pack_id in sorted(set(current_by_pack) | set(prior_by_pack)):
        current = current_by_pack.get(pack_id, {})
        prior = prior_by_pack.get(pack_id, {})
        current_total = int(current.get("total_conflict_groups", 0) or 0)
        current_fallback = int(current.get("fallback_or_unknown_groups", 0) or 0)
        prior_total = int(prior.get("total_conflict_groups", 0) or 0)
        prior_fallback = int(prior.get("fallback_or_unknown_groups", 0) or 0)
        current_ratio = (float(current_fallback) / float(current_total)) if current_total > 0 else 0.0
        prior_ratio = (float(prior_fallback) / float(prior_total)) if prior_total > 0 else 0.0
        rows.append(
            {
                "pack_id": pack_id,
                "current_total_conflict_groups": current_total,
                "current_fallback_or_unknown_groups": current_fallback,
                "current_fallback_ratio": current_ratio,
                "prior_total_conflict_groups": prior_total,
                "prior_fallback_or_unknown_groups": prior_fallback,
                "prior_fallback_ratio": prior_ratio,
                "fallback_group_drift": current_fallback - prior_fallback,
                "fallback_ratio_drift": current_ratio - prior_ratio,
                "has_prior_baseline": pack_id in prior_by_pack,
            }
        )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_found": bool(prior_by_pack),
        "pack_count": len(rows),
        "packs": rows,
    }


def _render_realmod_fallback_drift_markdown(report: dict[str, object]) -> str:
    rows = report.get("packs", [])
    lines = [
        "# NIF real-sample fallback drift report",
        "",
        f"- Generated: {report.get('generated_at_utc', '')}",
        f"- Baseline found: {'yes' if bool(report.get('baseline_found', False)) else 'no'}",
        f"- Pack count: {report.get('pack_count', 0)}",
        "",
        "| Pack | Current fallback | Current ratio | Prior fallback | Prior ratio | Group drift | Ratio drift | Prior baseline |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            pack_id = str(row.get("pack_id", "pack")).replace("|", "\\|")
            lines.append(
                f"| `{pack_id}` | "
                f"{int(row.get('current_fallback_or_unknown_groups', 0) or 0)}/"
                f"{int(row.get('current_total_conflict_groups', 0) or 0)} | "
                f"{float(row.get('current_fallback_ratio', 0.0) or 0.0):.3f} | "
                f"{int(row.get('prior_fallback_or_unknown_groups', 0) or 0)}/"
                f"{int(row.get('prior_total_conflict_groups', 0) or 0)} | "
                f"{float(row.get('prior_fallback_ratio', 0.0) or 0.0):.3f} | "
                f"{int(row.get('fallback_group_drift', 0) or 0)} | "
                f"{float(row.get('fallback_ratio_drift', 0.0) or 0.0):.3f} | "
                f"{'yes' if bool(row.get('has_prior_baseline', False)) else 'no'} |"
            )
    lines.append("")
    return "\n".join(lines)


def _assert_realmod_fallback_drift_within_limit(
    *,
    report: dict[str, object],
    max_fallback_ratio_drift: float,
    max_fallback_group_drift: int,
) -> None:
    if max_fallback_ratio_drift < 0:
        raise SystemExit("--max-fallback-ratio-drift must be >= 0.")
    if max_fallback_group_drift < 0:
        raise SystemExit("--max-fallback-group-drift must be >= 0.")
    if not bool(report.get("baseline_found", False)):
        print("Fallback-drift gate skipped (no prior realmod parity-delta seed report found).")
        return
    rows = report.get("packs", [])
    if not isinstance(rows, list):
        return
    violations: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not bool(row.get("has_prior_baseline", False)):
            continue
        ratio_drift = float(row.get("fallback_ratio_drift", 0.0) or 0.0)
        group_drift = int(row.get("fallback_group_drift", 0) or 0)
        if ratio_drift > max_fallback_ratio_drift or group_drift > max_fallback_group_drift:
            violations.append(
                f"{row.get('pack_id')}: ratio_drift={ratio_drift:.3f}, group_drift={group_drift}"
            )
    if violations:
        print("Fallback-drift gate failed:")
        for row in violations:
            print(" -", row)
        raise SystemExit(1)
    print(
        "Fallback-drift gate passed "
        f"(max ratio drift {max_fallback_ratio_drift:.3f}, "
        f"max group drift {max_fallback_group_drift})."
    )


def _normalize_bucket_distribution(
    bucket_counts: dict[str, int] | None,
) -> dict[str, float]:
    if not isinstance(bucket_counts, dict):
        return {}
    normalized_counts: dict[str, int] = {}
    total = 0
    for key, value in bucket_counts.items():
        bucket = str(key).strip() or "none"
        count = max(0, int(value))
        normalized_counts[bucket] = normalized_counts.get(bucket, 0) + count
        total += count
    if total <= 0:
        return {}
    return {
        bucket: (float(count) / float(total))
        for bucket, count in sorted(normalized_counts.items())
    }


def _load_prior_bucket_distribution(
    seed_files: list[Path] | None,
) -> dict[str, float]:
    latest_payload = _load_latest_seed_report(
        seed_files,
        required_top_level_keys=("intended_difference_bucket_counts",),
    )
    if not latest_payload:
        return {}
    return _normalize_bucket_distribution(
        latest_payload.get("intended_difference_bucket_counts"),
    )


def _assert_bucket_distribution_drift_within_limit(
    *,
    current_report: dict[str, object],
    baseline_distribution: dict[str, float],
    max_drift_ratio: float,
) -> None:
    if max_drift_ratio < 0:
        raise SystemExit("--max-bucket-drift-ratio must be >= 0.")
    if not baseline_distribution:
        print("Bucket-drift gate skipped (no prior parity-delta seed distribution found).")
        return
    current_distribution = _normalize_bucket_distribution(
        current_report.get("intended_difference_bucket_counts"),
    )
    if not current_distribution:
        print("Bucket-drift gate skipped (current parity-delta report has no bucket counts).")
        return
    buckets = sorted(set(current_distribution) | set(baseline_distribution))
    violations: list[str] = []
    for bucket in buckets:
        current_ratio = float(current_distribution.get(bucket, 0.0))
        baseline_ratio = float(baseline_distribution.get(bucket, 0.0))
        drift = abs(current_ratio - baseline_ratio)
        if drift > max_drift_ratio:
            violations.append(
                f"{bucket}: baseline={baseline_ratio:.3f}, current={current_ratio:.3f}, drift={drift:.3f}"
            )
    if violations:
        print("Bucket-drift gate failed:")
        for row in violations:
            print(" -", row)
        raise SystemExit(1)
    print(
        f"Bucket-drift gate passed (max drift {max_drift_ratio:.3f}, "
        f"{len(buckets)} bucket(s) compared)."
    )


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

            remediation_mode = _derive_remediation_mode(rem_steps)
            has_disable_steps = remediation_mode in {"disable", "mixed_rebuild_and_disable"}
            local_strategy = str(case.get("local_strategy", "") or "")
            intentional_strategy_difference = bool(case.get("intentional_strategy_difference", False))
            safety_difference_note = str(case.get("safety_difference_note", "") or "")
            difference_bucket = _classify_intended_difference_bucket(
                profile=str(case.get("profile", "")),
                remediation_mode=remediation_mode,
                fallback_used=has_disable_steps,
                intentional_strategy_difference=intentional_strategy_difference,
                local_strategy=local_strategy,
                safety_difference_note=safety_difference_note,
            )

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
                    "local_strategy": local_strategy,
                    "intentional_strategy_difference": intentional_strategy_difference,
                    "safety_difference_note": safety_difference_note,
                    "intended_difference_bucket": difference_bucket,
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
        "| Case | Profile/layout | Detected family count | Remediation mode | Fallback used | Intentional strategy diff | Difference bucket |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
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
            difference_bucket = str(row.get("intended_difference_bucket", "none")).replace("|", "\\|")
            lines.append(
                f"| `{case_id}` | `{profile_layout}` | {family_count} | `{mode}` | {fallback} | {strategy_diff} | `{difference_bucket}` |"
            )
    lines.extend(
        [
            "",
            "Use the JSON artifact for full per-case details (detected families/codes, remediation steps, expected-step deltas, and safety-difference notes).",
            "",
        ]
    )
    return "\n".join(lines)


def _render_realmod_side_by_side_delta_markdown(report: dict[str, object]) -> str:
    rows = report.get("cases", [])
    lines = [
        "# NIF real-sample side-by-side parity delta",
        "",
        f"- Generated: {report.get('generated_at_utc', '')}",
        f"- Cases: {report.get('case_count', 0)}",
        "",
        "| Pack | Case | Family | Profile/layout | Conflict family count | Local remediation mode | Difference bucket |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            pack_id = str(row.get("pack_id", "")).replace("|", "\\|")
            case_id = str(row.get("case_id", "")).replace("|", "\\|")
            family = str(row.get("family", "")).replace("|", "\\|")
            profile_layout = f"{row.get('profile', '?')}/{row.get('shader_layout', '?')}".replace("|", "\\|")
            family_count = len(row.get("detected_conflict_families", [])) if isinstance(
                row.get("detected_conflict_families"), list
            ) else 0
            mode = str(row.get("remediation_mode", "manual_or_noop")).replace("|", "\\|")
            bucket = str(row.get("intended_difference_bucket", "none")).replace("|", "\\|")
            lines.append(
                f"| `{pack_id}` | `{case_id}` | `{family}` | `{profile_layout}` | {family_count} | `{mode}` | `{bucket}` |"
            )
    lines.extend(
        [
            "",
            "Bucket legend: `safety_first` = conservative disable/guard fallback, `guarded_fallout` = Fallout safety policy divergence, `partial_recoverability_guarded` = intentionally limited rebuild path with guarded fallback, `destructive_disabled` = intentionally avoids destructive cleanup paths.",
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
    realmod_delta_report: dict[str, object],
    realmod_delta_mismatch_summary: dict[str, object],
    realmod_fallback_guard_report: dict[str, object],
    realmod_fallback_drift_report: dict[str, object],
    localization_report: dict[str, object] | None = None,
    localization_json_path: Path | None = None,
    localization_md_path: Path | None = None,
) -> tuple[Path, Path, Path, Path, Path, Path, Path, Path, Path, Path, Path, Path]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    trend_path = artifact_dir / "nif_family_trend_snapshot.json"
    trend_path.write_text(json.dumps(trend_snapshot, indent=2, sort_keys=True), encoding="utf-8")
    parity_json_path = artifact_dir / "nif_parity_feature_report.json"
    parity_json_path.write_text(json.dumps(parity_report, indent=2, sort_keys=True), encoding="utf-8")
    parity_md_path = artifact_dir / "nif_parity_feature_report.md"
    parity_md_path.write_text(_render_parity_feature_report_markdown(parity_report), encoding="utf-8")
    realmod_delta_json_path = artifact_dir / "nif_realmod_parity_delta_report.json"
    realmod_delta_json_path.write_text(json.dumps(realmod_delta_report, indent=2, sort_keys=True), encoding="utf-8")
    realmod_delta_md_path = artifact_dir / "nif_realmod_parity_delta_report.md"
    realmod_delta_md_path.write_text(
        _render_realmod_side_by_side_delta_markdown(realmod_delta_report),
        encoding="utf-8",
    )
    realmod_delta_mismatch_json_path = artifact_dir / "nif_realmod_parity_mismatch_summary.json"
    realmod_delta_mismatch_json_path.write_text(
        json.dumps(realmod_delta_mismatch_summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    realmod_delta_mismatch_md_path = artifact_dir / "nif_realmod_parity_mismatch_summary.md"
    realmod_delta_mismatch_md_path.write_text(
        _render_realmod_delta_mismatch_summary_markdown(realmod_delta_mismatch_summary),
        encoding="utf-8",
    )
    realmod_fallback_guard_json_path = artifact_dir / "nif_realmod_fallback_guard_report.json"
    realmod_fallback_guard_json_path.write_text(
        json.dumps(realmod_fallback_guard_report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    realmod_fallback_guard_md_path = artifact_dir / "nif_realmod_fallback_guard_report.md"
    realmod_fallback_guard_md_path.write_text(
        _render_realmod_fallback_guard_markdown(realmod_fallback_guard_report),
        encoding="utf-8",
    )
    realmod_fallback_drift_json_path = artifact_dir / "nif_realmod_fallback_drift_report.json"
    realmod_fallback_drift_json_path.write_text(
        json.dumps(realmod_fallback_drift_report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    realmod_fallback_drift_md_path = artifact_dir / "nif_realmod_fallback_drift_report.md"
    realmod_fallback_drift_md_path.write_text(
        _render_realmod_fallback_drift_markdown(realmod_fallback_drift_report),
        encoding="utf-8",
    )

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
            "",
            "## NIF real-sample side-by-side parity delta snapshot",
            "",
            f"- Real-sample parity delta JSON: `{realmod_delta_json_path.name}`",
            f"- Real-sample parity delta markdown: `{realmod_delta_md_path.name}`",
            f"- Real-sample parity mismatch summary JSON: `{realmod_delta_mismatch_json_path.name}`",
            f"- Real-sample parity mismatch summary markdown: `{realmod_delta_mismatch_md_path.name}`",
            f"- Real-sample fallback guard JSON: `{realmod_fallback_guard_json_path.name}`",
            f"- Real-sample fallback guard markdown: `{realmod_fallback_guard_md_path.name}`",
            f"- Real-sample fallback drift JSON: `{realmod_fallback_drift_json_path.name}`",
            f"- Real-sample fallback drift markdown: `{realmod_fallback_drift_md_path.name}`",
            "",
            "## Manual release-candidate verification (post-CI artifact review)",
            "",
            "- [ ] Review `release-readiness-artifacts` in CI and confirm no unexpected conflict-family drift.",
            "- [ ] Review packaged executable artifacts and verify CLI `--help` plus expected files are present.",
            "- [ ] Review `packaged-smoke-artifacts` in CI and confirm end-to-end smoke outputs (`sample_input.png` + generated `.dds`) are present.",
            "- [ ] Run representative in-game smoke checks (at least one Skyrim set and one guarded Fallout set) and confirm no new visual/CTD regressions.",
        ]
    )
    if localization_report is not None and localization_json_path is not None and localization_md_path is not None:
        summary = localization_report.get("summary", {})
        base_count = 0
        missing_langs = 0
        if isinstance(summary, Mapping):
            base_count = int(summary.get("base_string_count", 0) or 0)
            missing_langs = int(summary.get("languages_with_missing_strings", 0) or 0)
        lines.extend(
            [
                "",
                "## Localization sweep snapshot",
                "",
                f"- Localization JSON: `{localization_json_path.name}`",
                f"- Localization markdown: `{localization_md_path.name}`",
                f"- Base (en) strings: {base_count}",
                f"- Non-en catalogs with missing strings: {missing_langs}",
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
            strategy_annotated = int(family.get("strategy_annotated_count", 0) or 0)
            strategy_aligned = int(family.get("strategy_aligned_count", 0) or 0)
            strategy_ratio = float(family.get("strategy_alignment_ratio", 0.0) or 0.0)
            lines.append(
                f"  - {family.get('family')}: pass {family.get('pass_count', 0)}/{family.get('case_count', 0)}, "
                f"fail {family.get('fail_count', 0)}, strategy alignment {strategy_aligned}/{strategy_annotated} "
                f"({strategy_ratio:.2f})"
            )
    checklist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return (
        checklist_path,
        trend_path,
        parity_json_path,
        parity_md_path,
        realmod_delta_json_path,
        realmod_delta_md_path,
        realmod_delta_mismatch_json_path,
        realmod_delta_mismatch_md_path,
        realmod_fallback_guard_json_path,
        realmod_fallback_guard_md_path,
        realmod_fallback_drift_json_path,
        realmod_fallback_drift_md_path,
    )


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
        "--seed-realmod-delta-file",
        action="append",
        type=Path,
        default=[],
        help=(
            "Optional prior nif_realmod_parity_delta_report.json artifact(s) used as "
            "baseline for intended-difference bucket drift checks."
        ),
    )
    parser.add_argument(
        "--extra-realmod-pack-file",
        action="append",
        type=Path,
        default=[],
        help=(
            "Optional additional realmod sample-pack JSON file(s) to include in parity trend and "
            "side-by-side delta reports. Useful for larger external broken-mod sweeps."
        ),
    )
    parser.add_argument(
        "--max-bucket-drift-ratio",
        type=float,
        default=0.25,
        help=(
            "Maximum allowed absolute drift for intended-difference bucket ratios "
            "versus prior realmod parity-delta baseline."
        ),
    )
    parser.add_argument(
        "--max-fallback-ratio-drift",
        type=float,
        default=0.05,
        help=(
            "Maximum allowed increase in fallback_or_unknown ratio per realmod pack "
            "versus the prior seeded parity-delta report."
        ),
    )
    parser.add_argument(
        "--max-fallback-group-drift",
        type=int,
        default=2,
        help=(
            "Maximum allowed increase in fallback_or_unknown conflict-group count per realmod pack "
            "versus the prior seeded parity-delta report."
        ),
    )
    parser.add_argument(
        "--skip-packaging-smoke",
        action="store_true",
        help="Skip local PyInstaller packaging smoke build.",
    )
    parser.add_argument(
        "--repeat-validation-loops",
        type=int,
        default=1,
        help="Number of consecutive release-like validation loops to run before artifact generation.",
    )
    parser.add_argument(
        "--strict-localization-completeness",
        action="store_true",
        help="Fail when non-English translation catalogs are missing keys from en.json.",
    )
    parser.add_argument(
        "--packaged-smoke-loops",
        type=int,
        default=2,
        help="Number of consecutive packaged executable smoke loops per workflow scenario.",
    )
    args = parser.parse_args()
    loops = max(1, int(args.repeat_validation_loops))

    step_status: list[tuple[str, str]] = []
    for loop_index in range(1, loops + 1):
        suffix = f" (loop {loop_index}/{loops})" if loops > 1 else ""
        _run_step(
            f"Full unittest suite{suffix}",
            [PYTHON, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
        )
        _run_step(
            f"Targeted NIF fixture and conflict stress checks{suffix}",
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
        _run_step(
            f"Compile check{suffix}",
            [PYTHON, "-m", "compileall", "generate_textures.py", "nif_patcher.py", "tests"],
        )
        _run_step(
            f"Large real-sample batch verification{suffix}",
            [
                PYTHON,
                "-m",
                "unittest",
                "-v",
                "tests.test_generate_textures.GenerateTexturesTests.test_compute_preview_refresh_delay_ms_throttles_for_huge_batches",
                "tests.test_generate_textures.GenerateTexturesTests.test_compute_deferred_preview_tile_interval_ms_increases_for_huge_batches",
                "tests.test_generate_textures.GenerateTexturesTests.test_should_update_live_batch_preview_throttles_dense_updates_for_huge_batches",
                "tests.test_generate_textures.GenerateTexturesTests.test_build_batch_bottleneck_hints_includes_large_run_resume_guidance",
                "tests.test_generate_textures.GenerateTexturesTests.test_run_batch_with_options_writes_checkpoint_file",
                "tests.test_generate_textures.GenerateTexturesTests.test_run_batch_with_options_resume_checkpoint_skips_completed_successes",
                "tests.test_generate_textures.GenerateTexturesTests.test_run_batch_with_options_writes_batch_telemetry_file",
                "tests.test_generate_textures.GenerateTexturesTests.test_gui_processing_queue_smoke_done_event_reports_outputs_failures_and_autopatch",
            ],
        )
    step_status.append((f"Full unittest suite x{loops}", "pass"))
    step_status.append((f"Targeted NIF fixture/parity stress checks x{loops}", "pass"))
    step_status.append((f"Compile check x{loops}", "pass"))
    step_status.append((f"Large real-sample batch verification x{loops}", "pass"))
    _run_repository_hygiene_scan()
    step_status.append(("Repository hygiene scan", "pass"))
    _run_secret_scan()
    step_status.append(("Tracked-file secret scan", "pass"))
    if not args.skip_packaging_smoke:
        _run_packaging_smoke(args.artifact_dir)
        step_status.append(("Packaging smoke build", "pass"))
        packaged_smoke_loops = max(1, int(args.packaged_smoke_loops))
        _run_packaged_executable_smoke(args.artifact_dir, loops=packaged_smoke_loops)
        step_status.append((f"Packaged executable smoke run x{packaged_smoke_loops}", "pass"))
        runtime_env_probes = _run_packaged_runtime_env_stress(args.artifact_dir)
        step_status.append(("Packaged runtime environment stress probes", "pass"))
    else:
        runtime_env_probes = []
    (
        localization_report,
        localization_json_path,
        localization_md_path,
    ) = _run_localization_sweep(
        args.artifact_dir,
        strict_completeness=bool(args.strict_localization_completeness),
    )
    step_status.append(("Localization sweep", "pass"))
    extra_realmod_pack_files = [path for path in args.extra_realmod_pack_file if path is not None]
    trend_snapshot = _build_realmod_family_trend_snapshot(
        extra_pack_files=extra_realmod_pack_files,
    )
    parity_report = _build_parity_matrix_feature_report()
    realmod_delta_report = _build_realmod_side_by_side_delta_report(
        extra_pack_files=extra_realmod_pack_files,
    )
    realmod_delta_mismatch_summary = _build_realmod_delta_mismatch_summary(realmod_delta_report)
    realmod_fallback_guard_report = _build_realmod_fallback_guard_report(
        realmod_delta_report=realmod_delta_report,
        extra_pack_files=extra_realmod_pack_files,
    )
    _assert_realmod_fallback_guard(realmod_fallback_guard_report)
    step_status.append(("Realmod fallback/generic-subcode regression guard", "pass"))
    resolved_seed_realmod_delta_files = _resolve_seed_realmod_delta_files(
        [path for path in args.seed_realmod_delta_file if path is not None],
    )
    realmod_fallback_drift_report = _build_realmod_fallback_drift_report(
        current_fallback_guard_report=realmod_fallback_guard_report,
        seed_realmod_delta_files=resolved_seed_realmod_delta_files,
        extra_pack_files=extra_realmod_pack_files,
    )
    _assert_realmod_fallback_drift_within_limit(
        report=realmod_fallback_drift_report,
        max_fallback_ratio_drift=float(args.max_fallback_ratio_drift),
        max_fallback_group_drift=max(0, int(args.max_fallback_group_drift)),
    )
    step_status.append(("Realmod fallback drift guard", "pass"))
    (
        checklist_path,
        trend_path,
        parity_json_path,
        parity_md_path,
        realmod_delta_json_path,
        realmod_delta_md_path,
        realmod_delta_mismatch_json_path,
        realmod_delta_mismatch_md_path,
        realmod_fallback_guard_json_path,
        realmod_fallback_guard_md_path,
        realmod_fallback_drift_json_path,
        realmod_fallback_drift_md_path,
    ) = _write_release_artifacts(
        artifact_dir=args.artifact_dir,
        step_status=step_status,
        trend_snapshot=trend_snapshot,
        parity_report=parity_report,
        realmod_delta_report=realmod_delta_report,
        realmod_delta_mismatch_summary=realmod_delta_mismatch_summary,
        realmod_fallback_guard_report=realmod_fallback_guard_report,
        realmod_fallback_drift_report=realmod_fallback_drift_report,
        localization_report=localization_report,
        localization_json_path=localization_json_path,
        localization_md_path=localization_md_path,
    )
    (
        packaged_accessibility_json_path,
        packaged_accessibility_md_path,
    ) = _write_packaged_accessibility_acceptance_artifacts(
        artifact_dir=args.artifact_dir,
        step_status=step_status,
        runtime_env_probes=runtime_env_probes,
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
    print(f"NIF real-sample parity delta JSON artifact: {realmod_delta_json_path}")
    print(f"NIF real-sample parity delta markdown artifact: {realmod_delta_md_path}")
    print(f"NIF real-sample parity mismatch summary JSON artifact: {realmod_delta_mismatch_json_path}")
    print(f"NIF real-sample parity mismatch summary markdown artifact: {realmod_delta_mismatch_md_path}")
    print(f"NIF real-sample fallback guard JSON artifact: {realmod_fallback_guard_json_path}")
    print(f"NIF real-sample fallback guard markdown artifact: {realmod_fallback_guard_md_path}")
    print(f"NIF real-sample fallback drift JSON artifact: {realmod_fallback_drift_json_path}")
    print(f"NIF real-sample fallback drift markdown artifact: {realmod_fallback_drift_md_path}")
    print(f"Localization coverage JSON artifact: {localization_json_path}")
    print(f"Localization coverage markdown artifact: {localization_md_path}")
    print(f"Packaged accessibility acceptance JSON artifact: {packaged_accessibility_json_path}")
    print(f"Packaged accessibility acceptance markdown artifact: {packaged_accessibility_md_path}")
    if history_path is not None:
        print(f"NIF trend history artifact: {history_path}")
    prior_bucket_distribution = _load_prior_bucket_distribution(
        resolved_seed_realmod_delta_files,
    )
    _assert_bucket_distribution_drift_within_limit(
        current_report=realmod_delta_report,
        baseline_distribution=prior_bucket_distribution,
        max_drift_ratio=float(args.max_bucket_drift_ratio),
    )
    print("\nPre-release validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
