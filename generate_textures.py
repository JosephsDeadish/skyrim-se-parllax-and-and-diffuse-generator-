from __future__ import annotations

import argparse
import concurrent.futures
import gc
import json
import math
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

import webbrowser

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageStat

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from PIL import ImageTk

    GUI_AVAILABLE = True
except Exception:
    tk = None
    filedialog = None
    messagebox = None
    ttk = None
    ImageTk = None
    GUI_AVAILABLE = False

try:
    from nif_patcher import (
        NifPatchOptions,
        find_nif_files,
        guess_env_mask_path_for_nif,
        guess_glow_path_for_nif,
        guess_normal_path_for_nif,
        guess_parallax_path_for_nif,
        patch_nif,
        scan_nif,
        validate_nif_for_parallax,
    )
    NIF_PATCHER_AVAILABLE = True
except ImportError:
    NIF_PATCHER_AVAILABLE = False


DDS_EXTENSION = ".dds"
APP_VERSION = "0.7"
SUPPORTED_INPUT_EXTENSIONS = {DDS_EXTENSION, ".png", ".jpg", ".jpeg", ".tga", ".bmp"}
GENERATED_TEXTURE_SUFFIXES = (
    "_msn",
    "_cm",
    "_c",
    "_rmaos",
    "_ramos",
    "_orm",
    "_orms",
    "_mrao",
    "_mra",
    "_n",
    "_p",
    "_g",
    "_glow",
    "_em",
    "_emis",
    "_emit",
    "_emissive",
    "_m",
    "_ao",
    "_ambientocclusion",
    "_rough",
    "_roughness",
    "_metal",
    "_metallic",
    "_metalness",
    "_s",
    "_sk",
    "_wt",
    "_sm",
)
PREVIEW_MAX_DIMENSION = 1024
PREVIEW_SIZE_PRESETS: dict[str, tuple[int, int]] = {
    "XS": (160, 120),
    "Small": (240, 180),
    "Medium": (340, 250),
    "Large": (460, 340),
    "XL": (620, 460),
}
GUI_STATE_FILE = Path.home() / ".skyrim_texture_generator_gui_state.json"
_GUI_STATE_DEFAULTS: dict[str, object] = {
    "input_path": "",
    "output_path": "",
    "use_custom_output": False,
    "dark_mode": False,
    "show_batch_preview": False,
    "auto_patch_nifs": False,
    "preview_size": "Medium",
    "complex_format": "msn",
    "env_mask_mode": "standard",
    "parallax_mode": "standard",
    "render_profile": "custom",
    "emboss_mode": False,
    "relief_mode": False,
    "include_diffuse": True,
    "include_normal": True,
    "include_parallax": True,
    "include_glow": False,
    "include_environment_mask": False,
    "include_rmaos": False,
    "include_complex": False,
    "include_wetness_mask": False,
    "include_snow_mask": False,
    "auto_suggestions": True,
    "auto_normal": True,
    "auto_parallax": True,
    "auto_glow": True,
    "auto_environment_mask": True,
    "auto_rmaos": True,
    "auto_complex": True,
    "auto_specular": True,
    "normal_strength": 2.0,
    "parallax_strength": 1.35,
    "glow_threshold": 190,
    "environment_mask_strength": 1.2,
    "rmaos_strength": 1.2,
    "complex_strength": 1.15,
    "specular_strength": 1.15,
    "wetness_mask_strength": 1.0,
    "snow_mask_strength": 1.0,
    "dismissed_warnings": [],
}


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_float(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, resolved))


def _coerce_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, resolved))


def compute_wrapped_preview_index(index: int, total: int) -> int:
    if total <= 0:
        return 0
    return index % total


def parse_preview_jump_input(value: str, total: int) -> int | None:
    if total <= 0:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        requested = int(stripped)
    except ValueError:
        return None
    if requested < 1 or requested > total:
        return None
    return requested - 1


def _normalize_nif_result_details(details: str) -> str:
    normalized = details.strip()
    return normalized or "(no details)"


def _format_nif_result_row_details(details: str) -> str:
    return _normalize_nif_result_details(details).replace("\n", " ↩ ")


def restore_nif_backups(nif_paths: list[Path]) -> list[tuple[str, str, str]]:
    """Restore ``.nif`` files from sibling ``.nif.bak`` backups."""
    results: list[tuple[str, str, str]] = []
    for nif_path in nif_paths:
        backup_path = nif_path.with_suffix(".nif.bak")
        if not backup_path.exists():
            results.append(("SKIP", nif_path.name, "No .nif.bak backup found."))
            continue
        try:
            shutil.copy2(backup_path, nif_path)
        except Exception as exc:
            results.append(("FAIL", nif_path.name, f"Restore failed: {exc}"))
            continue
        results.append(("OK", nif_path.name, f"Restored from {backup_path.name}."))
    return results


def should_apply_preview_recommendations(*, auto_suggestions_enabled: bool, is_processing: bool) -> bool:
    """Return whether preview navigation should auto-apply recommendations.

    During active generation/batch processing, preview updates should not mutate
    user-controlled toggles (for example emboss/relief) while files are running.
    """
    return auto_suggestions_enabled and not is_processing


def _compute_tooltip_position(
    *,
    pointer_x: int,
    pointer_y: int,
    tip_width: int,
    tip_height: int,
    screen_width: int,
    screen_height: int,
    cursor_offset_x: int = 16,
    cursor_offset_y: int = 20,
    screen_margin: int = 10,
) -> tuple[int, int]:
    desired_x = pointer_x + cursor_offset_x
    desired_y = pointer_y + cursor_offset_y
    min_x = max(0, screen_margin)
    min_y = max(0, screen_margin)
    max_x = max(min_x, screen_width - tip_width - screen_margin)
    max_y = max(min_y, screen_height - tip_height - screen_margin)
    return (
        int(_clamp(float(desired_x), float(min_x), float(max_x))),
        int(_clamp(float(desired_y), float(min_y), float(max_y))),
    )


def _normalize_gui_state(raw: Mapping[str, object] | None) -> dict[str, object]:
    state = dict(_GUI_STATE_DEFAULTS)
    if raw is None:
        return state
    state["input_path"] = str(raw.get("input_path", state["input_path"]) or "")
    state["output_path"] = str(raw.get("output_path", state["output_path"]) or "")
    for key in (
        "use_custom_output",
        "dark_mode",
        "show_batch_preview",
        "emboss_mode",
        "relief_mode",
        "include_diffuse",
        "include_normal",
        "include_parallax",
        "include_glow",
        "include_environment_mask",
        "include_rmaos",
        "include_complex",
        "include_wetness_mask",
        "include_snow_mask",
        "auto_suggestions",
        "auto_normal",
        "auto_parallax",
        "auto_glow",
        "auto_environment_mask",
        "auto_rmaos",
        "auto_complex",
        "auto_specular",
    ):
        state[key] = _coerce_bool(raw.get(key), bool(state[key]))
    if not bool(state["auto_suggestions"]):
        state["auto_normal"] = False
        state["auto_parallax"] = False
        state["auto_glow"] = False
        state["auto_environment_mask"] = False
        state["auto_rmaos"] = False
        state["auto_complex"] = False
        state["auto_specular"] = False
    input_path = str(state["input_path"]).strip()
    output_path = str(state["output_path"]).strip()
    if input_path and not Path(input_path).exists():
        input_path = ""
    if bool(state["use_custom_output"]) and not output_path:
        state["use_custom_output"] = False
    state["input_path"] = input_path
    state["output_path"] = output_path
    preview_size = str(raw.get("preview_size", state["preview_size"]) or state["preview_size"])
    if preview_size not in PREVIEW_SIZE_PRESETS:
        preview_size = str(_GUI_STATE_DEFAULTS["preview_size"])
    state["preview_size"] = preview_size
    complex_format = str(raw.get("complex_format", state["complex_format"]) or state["complex_format"]).strip().lower()
    state["complex_format"] = complex_format if complex_format in {"msn", "cm"} else str(_GUI_STATE_DEFAULTS["complex_format"])
    env_mask_mode = str(raw.get("env_mask_mode", state["env_mask_mode"]) or state["env_mask_mode"]).strip().lower()
    state["env_mask_mode"] = env_mask_mode if env_mask_mode in {"standard", "complex"} else str(_GUI_STATE_DEFAULTS["env_mask_mode"])
    parallax_mode = str(raw.get("parallax_mode", state["parallax_mode"]) or state["parallax_mode"]).strip().lower()
    if "occlusion" in parallax_mode:
        state["parallax_mode"] = "occlusion (ENB/POM)"
    elif parallax_mode == "standard":
        state["parallax_mode"] = "standard"
    else:
        state["parallax_mode"] = str(_GUI_STATE_DEFAULTS["parallax_mode"])
    render_profile = str(raw.get("render_profile", state["render_profile"]) or state["render_profile"]).strip().lower()
    if render_profile in {
        "auto",
        "custom",
        "experimental",
        "vanilla",
        "performance",
        "vr",
        "terrain",
        "architecture",
        "characters",
        "community_shaders",
        "community shaders",
        "truepbr",
        "true pbr",
        "enb",
    }:
        if render_profile == "community shaders":
            state["render_profile"] = "community_shaders"
        elif render_profile == "true pbr":
            state["render_profile"] = "truepbr"
        elif render_profile == "experimental":
            state["render_profile"] = "custom"
        else:
            state["render_profile"] = render_profile
    else:
        state["render_profile"] = str(_GUI_STATE_DEFAULTS["render_profile"])
    state["normal_strength"] = _coerce_float(raw.get("normal_strength"), float(state["normal_strength"]), 0.1, 8.0)
    state["parallax_strength"] = _coerce_float(raw.get("parallax_strength"), float(state["parallax_strength"]), 0.1, 6.0)
    state["glow_threshold"] = _coerce_int(raw.get("glow_threshold"), int(state["glow_threshold"]), 0, 255)
    state["auto_patch_nifs"] = _coerce_bool(raw.get("auto_patch_nifs"), bool(state["auto_patch_nifs"]))
    state["environment_mask_strength"] = _coerce_float(
        raw.get("environment_mask_strength"), float(state["environment_mask_strength"]), 0.1, 8.0
    )
    state["rmaos_strength"] = _coerce_float(
        raw.get("rmaos_strength"), float(state["rmaos_strength"]), 0.1, 8.0
    )
    state["complex_strength"] = _coerce_float(raw.get("complex_strength"), float(state["complex_strength"]), 0.1, 8.0)
    state["specular_strength"] = _coerce_float(raw.get("specular_strength"), float(state["specular_strength"]), 0.1, 8.0)
    state["wetness_mask_strength"] = _coerce_float(
        raw.get("wetness_mask_strength"), float(state["wetness_mask_strength"]), 0.1, 3.0
    )
    state["snow_mask_strength"] = _coerce_float(
        raw.get("snow_mask_strength"), float(state["snow_mask_strength"]), 0.1, 3.0
    )
    raw_dismissed = raw.get("dismissed_warnings", [])
    if isinstance(raw_dismissed, list):
        state["dismissed_warnings"] = [str(w) for w in raw_dismissed if isinstance(w, str)]
    else:
        state["dismissed_warnings"] = []
    return state


def load_gui_state(state_file: Path = GUI_STATE_FILE) -> dict[str, object]:
    try:
        if not state_file.exists():
            return dict(_GUI_STATE_DEFAULTS)
        raw = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            return dict(_GUI_STATE_DEFAULTS)
        return _normalize_gui_state(raw)
    except Exception:
        return dict(_GUI_STATE_DEFAULTS)


def save_gui_state(state: Mapping[str, object], state_file: Path = GUI_STATE_FILE) -> None:
    normalized = _normalize_gui_state(state)
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


@dataclass(frozen=True)
class ModManagerContext:
    manager: str | None = None
    profile_name: str | None = None
    game_id: str | None = None
    game_root: Path | None = None
    instance_root: Path | None = None
    staging_root: Path | None = None
    output_dir: Path | None = None
    loaded_mods: tuple[str, ...] = ()
    enabled_plugins: tuple[str, ...] = ()
    load_order: tuple[str, ...] = ()
    loaded_texture_dirs: tuple[Path, ...] = ()
    loaded_mesh_dirs: tuple[Path, ...] = ()
    detected_body_profiles: tuple[str, ...] = ()
    detected_skeleton_profiles: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        if self.manager is None:
            return "No MO2/Vortex context detected."
        details: list[str] = [f"Detected {self.manager}"]
        if self.profile_name:
            details.append(f"profile {self.profile_name}")
        if self.loaded_texture_dirs:
            details.append(f"{len(self.loaded_texture_dirs)} loaded mod texture folder(s)")
        elif self.loaded_mods:
            details.append(f"{len(self.loaded_mods)} loaded mod(s)")
        if self.enabled_plugins:
            details.append(f"{len(self.enabled_plugins)} plugin(s)")
        if self.load_order:
            details.append(f"{len(self.load_order)} load-order entry/entries")
        if self.loaded_mesh_dirs:
            details.append(f"{len(self.loaded_mesh_dirs)} mesh folder(s)")
        if self.detected_body_profiles:
            details.append(f"body hints {', '.join(self.detected_body_profiles)}")
        if self.detected_skeleton_profiles:
            details.append(f"skeleton hints {', '.join(self.detected_skeleton_profiles)}")
        if self.game_root is not None:
            details.append(f"game root {self.game_root}")
        return " — ".join(details)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _reshape_image_channel(array: np.ndarray, size: tuple[int, int], *, label: str) -> np.ndarray:
    width, height = size
    expected = width * height
    if int(array.size) != expected:
        raise RuntimeError(f"{label} buffer size mismatch: expected {expected} pixels, got {int(array.size)}.")
    return array.reshape(height, width)


def get_preview_size_limits(size_preset: str) -> tuple[int, int]:
    return PREVIEW_SIZE_PRESETS.get(size_preset, PREVIEW_SIZE_PRESETS["Medium"])


def _unique_existing_paths(paths: list[Path]) -> tuple[Path, ...]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        key = os.path.normcase(str(resolved))
        if key in seen or not resolved.exists():
            continue
        seen.add(key)
        unique.append(resolved)
    return tuple(unique)


def _parse_enabled_modlist(modlist_path: Path) -> tuple[str, ...]:
    enabled_mods: list[str] = []
    if not modlist_path.exists():
        return ()
    for raw_line in modlist_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        prefix = line[0]
        if prefix not in {"+", "*"}:
            continue
        mod_name = line[1:].strip()
        if mod_name:
            enabled_mods.append(mod_name)
    return tuple(enabled_mods)


def _parse_enabled_plugins(plugins_path: Path) -> tuple[str, ...]:
    plugins: list[str] = []
    if not plugins_path.exists():
        return ()
    for raw_line in plugins_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line[0] in {";", "-"}:
            continue
        if line[0] in {"+", "*"}:
            line = line[1:].strip()
        if line:
            plugins.append(line)
    return tuple(plugins)


def _parse_load_order(loadorder_path: Path) -> tuple[str, ...]:
    entries: list[str] = []
    if not loadorder_path.exists():
        return ()
    for raw_line in loadorder_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line[0] in {"+", "*", "-"}:
            line = line[1:].strip()
        if line:
            entries.append(line)
    return tuple(entries)


_BODY_PROFILE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("CBBE", ("cbbe", "caliente")),
    ("3BA", ("3ba", "3bbb")),
    ("UNP", ("unp",)),
    ("BHUNP", ("bhunp",)),
    ("HIMBO", ("himbo",)),
    ("SAM", ("schlongs of skyrim", "sam light", "shape atlas for men", "sam")),
)

_SKELETON_PROFILE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("XPMSSE", ("xpmsse", "xpmse", "xp32")),
)


def _normalize_profile_hint_source(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _detect_profile_hints(
    sources: tuple[str, ...], hint_map: tuple[tuple[str, tuple[str, ...]], ...]
) -> tuple[str, ...]:
    if not sources:
        return ()
    normalized_sources = tuple(_normalize_profile_hint_source(source) for source in sources if source)
    detected: list[str] = []
    for profile, hints in hint_map:
        if any(any(hint in source for hint in hints) for source in normalized_sources):
            detected.append(profile)
    return tuple(detected)


def _find_manager_instance_root(start: Path, required_children: tuple[str, ...]) -> Path | None:
    for candidate in (start, *start.parents):
        if all((candidate / child).exists() for child in required_children):
            return candidate
    return None


def _resolve_game_root_from_env(env: Mapping[str, str]) -> Path | None:
    for key in (
        "MO2_GAME_PATH",
        "MO_GAME_PATH",
        "MO2_GAME_DIRECTORY",
        "VORTEX_GAME_PATH",
        "GAME_PATH",
        "SKYRIMSE_PATH",
        "SKYRIM_GAME_PATH",
    ):
        raw = str(env.get(key, "")).strip()
        if not raw:
            continue
        return Path(raw).expanduser()
    return None


def _candidate_vortex_profile_dirs(env: Mapping[str, str], game_id: str | None) -> tuple[Path, ...]:
    candidates: list[Path] = []
    profile_dir = env.get("VORTEX_PROFILE_DIR")
    if profile_dir:
        candidates.append(Path(profile_dir))

    appdata = env.get("APPDATA")
    if appdata:
        roaming = Path(appdata)
        search_roots = [roaming / "Vortex", roaming / "Black Tree Gaming Ltd" / "Vortex"]
        for root in search_roots:
            if not root.exists():
                continue
            patterns = []
            if game_id:
                patterns.extend(
                    [
                        root / game_id / "profiles",
                        root / "profiles" / game_id,
                    ]
                )
            patterns.extend([root / "profiles", root])
            for base in patterns:
                if not base.exists():
                    continue
                if (base / "modlist.txt").exists():
                    candidates.append(base)
                    continue
                candidates.extend(path for path in base.iterdir() if path.is_dir() and (path / "modlist.txt").exists())

    ordered = sorted(
        _unique_existing_paths(candidates),
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    )
    return tuple(path for path in ordered if (path / "modlist.txt").exists())


def _detect_mo2_context(
    env: Mapping[str, str],
    *,
    executable_path: Path,
) -> ModManagerContext:
    game_root = _resolve_game_root_from_env(env)
    profile_name = env.get("MO_PROFILE") or env.get("MO2_PROFILE")
    instance_root = _find_manager_instance_root(executable_path.parent, ("mods", "profiles"))
    if instance_root is None and not profile_name:
        return ModManagerContext()

    resolved_profile = profile_name
    if instance_root is not None and not resolved_profile:
        profile_dirs = sorted(path for path in (instance_root / "profiles").iterdir() if path.is_dir())
        if len(profile_dirs) == 1:
            resolved_profile = profile_dirs[0].name

    loaded_mods: tuple[str, ...] = ()
    enabled_plugins: tuple[str, ...] = ()
    load_order: tuple[str, ...] = ()
    loaded_texture_candidates: list[Path] = []
    if instance_root is not None and resolved_profile:
        modlist_path = instance_root / "profiles" / resolved_profile / "modlist.txt"
        plugins_path = instance_root / "profiles" / resolved_profile / "plugins.txt"
        loadorder_path = instance_root / "profiles" / resolved_profile / "loadorder.txt"
        loaded_mods = _parse_enabled_modlist(modlist_path)
        enabled_plugins = _parse_enabled_plugins(plugins_path)
        load_order = _parse_load_order(loadorder_path)
        loaded_texture_candidates.extend(instance_root / "mods" / mod_name / "textures" for mod_name in loaded_mods)
    loaded_mesh_candidates = [instance_root / "mods" / mod_name / "meshes" for mod_name in loaded_mods] if instance_root is not None else []
    if instance_root is not None:
        loaded_texture_candidates.insert(0, instance_root / "overwrite" / "textures")
        loaded_mesh_candidates.insert(0, instance_root / "overwrite" / "meshes")
    loaded_texture_dirs = _unique_existing_paths(loaded_texture_candidates)
    loaded_mesh_dirs = _unique_existing_paths(loaded_mesh_candidates)
    detection_sources = tuple((*loaded_mods, *enabled_plugins, *load_order))
    detected_body_profiles = _detect_profile_hints(detection_sources, _BODY_PROFILE_HINTS)
    detected_skeleton_profiles = _detect_profile_hints(detection_sources, _SKELETON_PROFILE_HINTS)

    output_dir = instance_root / "overwrite" if instance_root is not None else None
    return ModManagerContext(
        manager="Mod Organizer 2",
        profile_name=resolved_profile,
        game_id="skyrimse",
        game_root=game_root,
        instance_root=instance_root,
        output_dir=output_dir,
        loaded_mods=loaded_mods,
        enabled_plugins=enabled_plugins,
        load_order=load_order,
        loaded_texture_dirs=loaded_texture_dirs,
        loaded_mesh_dirs=loaded_mesh_dirs,
        detected_body_profiles=detected_body_profiles,
        detected_skeleton_profiles=detected_skeleton_profiles,
    )


def _infer_vortex_staging_root(executable_path: Path, game_id: str | None) -> Path | None:
    for candidate in (executable_path.parent, *executable_path.parents):
        if game_id and candidate.name.lower() == game_id.lower():
            return candidate
        if candidate.name.lower() == "vortex mods":
            for child in candidate.iterdir():
                if child.is_dir() and (not game_id or child.name.lower() == game_id.lower()):
                    return child
    return None


def _detect_vortex_context(
    env: Mapping[str, str],
    *,
    executable_path: Path,
) -> ModManagerContext:
    env_signals = any(key.startswith("VORTEX") for key in env)
    game_root = _resolve_game_root_from_env(env)
    game_id = env.get("VORTEX_GAME_ID") or env.get("GAME_ID") or "skyrimse"
    profile_dirs = _candidate_vortex_profile_dirs(env, game_id)
    staging_root = None
    if env.get("VORTEX_STAGING_DIR"):
        staging_root = Path(env["VORTEX_STAGING_DIR"])
    else:
        staging_root = _infer_vortex_staging_root(executable_path, game_id)

    if not env_signals and staging_root is None:
        return ModManagerContext()

    profile_dir = profile_dirs[0] if profile_dirs else None
    profile_name = env.get("VORTEX_PROFILE") or (profile_dir.name if profile_dir is not None else None)
    loaded_mods = _parse_enabled_modlist(profile_dir / "modlist.txt") if profile_dir is not None else ()
    enabled_plugins = _parse_enabled_plugins(profile_dir / "plugins.txt") if profile_dir is not None else ()
    load_order = _parse_load_order(profile_dir / "loadorder.txt") if profile_dir is not None else ()
    if staging_root is not None and loaded_mods:
        texture_dirs = _unique_existing_paths([staging_root / mod_name / "textures" for mod_name in loaded_mods])
        mesh_dirs = _unique_existing_paths([staging_root / mod_name / "meshes" for mod_name in loaded_mods])
    elif staging_root is not None:
        texture_dirs = _unique_existing_paths(
            [path / "textures" for path in staging_root.iterdir() if path.is_dir() and (path / "textures").exists()]
        )
        mesh_dirs = _unique_existing_paths(
            [path / "meshes" for path in staging_root.iterdir() if path.is_dir() and (path / "meshes").exists()]
        )
    else:
        texture_dirs = ()
        mesh_dirs = ()
    detection_sources = tuple((*loaded_mods, *enabled_plugins, *load_order))
    detected_body_profiles = _detect_profile_hints(detection_sources, _BODY_PROFILE_HINTS)
    detected_skeleton_profiles = _detect_profile_hints(detection_sources, _SKELETON_PROFILE_HINTS)

    return ModManagerContext(
        manager="Vortex",
        profile_name=profile_name,
        game_id=game_id,
        game_root=game_root,
        staging_root=staging_root,
        output_dir=executable_path.parent / "generated_textures",
        loaded_mods=loaded_mods,
        enabled_plugins=enabled_plugins,
        load_order=load_order,
        loaded_texture_dirs=texture_dirs,
        loaded_mesh_dirs=mesh_dirs,
        detected_body_profiles=detected_body_profiles,
        detected_skeleton_profiles=detected_skeleton_profiles,
    )


def detect_mod_manager_context(
    env: Mapping[str, str] | None = None,
    *,
    executable_path: Path | None = None,
) -> ModManagerContext:
    resolved_env = env if env is not None else os.environ
    resolved_executable = (executable_path or Path(sys.executable)).resolve()
    mo2_context = _detect_mo2_context(resolved_env, executable_path=resolved_executable)
    if mo2_context.manager is not None:
        return mo2_context
    return _detect_vortex_context(resolved_env, executable_path=resolved_executable)


def _histogram_percentile(histogram: list[int], percentile: float) -> float:
    target = (sum(histogram) * _clamp(percentile, 0.0, 1.0)) or 1.0
    cumulative = 0
    for value, count in enumerate(histogram):
        cumulative += count
        if cumulative >= target:
            return float(value)
    return float(len(histogram) - 1)


def analyze_image_content(source: Image.Image) -> dict[str, float]:
    analysis_source = source
    if max(source.width, source.height) > 1024:
        analysis_source = source.copy()
        analysis_source.thumbnail((1024, 1024), Image.Resampling.BILINEAR)
    rgb_source = analysis_source.convert("RGB")
    grayscale = ImageOps.grayscale(rgb_source)
    denoised_grayscale = grayscale.filter(ImageFilter.MedianFilter(size=3))
    saturation = rgb_source.convert("HSV").split()[1]
    edges = denoised_grayscale.filter(ImageFilter.FIND_EDGES)
    blurred = denoised_grayscale.filter(ImageFilter.GaussianBlur(radius=2.0))
    detail = ImageChops.difference(denoised_grayscale, blurred)

    # Multi-scale edge detection via Difference-of-Gaussians (DoG)
    dog_fine = ImageChops.difference(
        denoised_grayscale.filter(ImageFilter.GaussianBlur(radius=0.8)),
        denoised_grayscale.filter(ImageFilter.GaussianBlur(radius=2.5)),
    )
    dog_medium = ImageChops.difference(
        denoised_grayscale.filter(ImageFilter.GaussianBlur(radius=1.5)),
        denoised_grayscale.filter(ImageFilter.GaussianBlur(radius=5.0)),
    )
    # Laplacian-like high-frequency energy (approximated via unsharp minus original)
    laplacian_approx = ImageChops.difference(
        denoised_grayscale,
        denoised_grayscale.filter(ImageFilter.GaussianBlur(radius=1.0)),
    )

    grayscale_stats = ImageStat.Stat(grayscale)
    saturation_stats = ImageStat.Stat(saturation)
    rgb_stats = ImageStat.Stat(rgb_source)
    edge_stats = ImageStat.Stat(edges)
    detail_stats = ImageStat.Stat(detail)
    dog_fine_stats = ImageStat.Stat(dog_fine)
    dog_medium_stats = ImageStat.Stat(dog_medium)
    laplacian_stats = ImageStat.Stat(laplacian_approx)
    minimum, maximum = grayscale.getextrema()
    histogram = grayscale.histogram()
    total_pixels = float(sum(histogram)) or 1.0
    saturation_histogram = saturation.histogram()
    saturation_total = float(sum(saturation_histogram)) or 1.0
    shadow_ratio = sum(histogram[:48]) / total_pixels
    highlight_ratio = sum(histogram[208:]) / total_pixels
    bright_cluster_ratio = sum(histogram[230:]) / total_pixels
    midtone_ratio = sum(histogram[80:176]) / total_pixels
    low_saturation_ratio = sum(saturation_histogram[:72]) / saturation_total
    p90_luma = _histogram_percentile(histogram, 0.90)
    color_variance = float(sum(rgb_stats.stddev) / 3.0)
    megapixels = float((source.width * source.height) / 1_000_000.0)

    # Background uniformity: downscale to 64×64 and measure std-dev of very-blurred image.
    # Low std-dev over blurred = large flat background areas → painting/artwork-like composition.
    _thumb = grayscale.copy()
    _thumb.thumbnail((64, 64), Image.Resampling.BILINEAR)
    _thumb_blur = _thumb.filter(ImageFilter.GaussianBlur(radius=4.0))
    bg_uniformity = float(max(0.0, 60.0 - ImageStat.Stat(_thumb_blur).stddev[0]) / 60.0)

    return {
        "brightness": float(grayscale_stats.mean[0]),
        "contrast": float(grayscale_stats.stddev[0]),
        "saturation_mean": float(saturation_stats.mean[0]),
        "saturation_variance": float(saturation_stats.stddev[0]),
        "low_saturation_ratio": float(low_saturation_ratio),
        "color_variance": float(color_variance),
        "megapixels": float(megapixels),
        "edge_strength": float(edge_stats.mean[0]),
        "edge_variance": float(edge_stats.stddev[0]),
        "detail_energy": float(detail_stats.mean[0]),
        "dog_fine_energy": float(dog_fine_stats.mean[0]),
        "dog_medium_energy": float(dog_medium_stats.mean[0]),
        "laplacian_energy": float(laplacian_stats.mean[0]),
        "dynamic_range": float(maximum - minimum),
        "shadow_ratio": float(shadow_ratio),
        "highlight_ratio": float(highlight_ratio),
        "bright_cluster_ratio": float(bright_cluster_ratio),
        "midtone_ratio": float(midtone_ratio),
        "p90_luma": float(p90_luma),
        "bg_uniformity": float(bg_uniformity),
    }


def apply_recommendations_by_auto_flags(
    current: dict[str, float | int],
    recommended: dict[str, float | int],
    auto_flags: dict[str, bool],
) -> dict[str, float | int]:
    merged = dict(current)
    for key, enabled in auto_flags.items():
        if enabled and key in recommended:
            merged[key] = recommended[key]
    return merged


def _infer_likely_role_from_image(source: Image.Image) -> str | None:
    rgb = source.convert("RGB")
    arr = np.asarray(rgb, dtype=np.float32)
    if arr.size == 0:
        return None
    mean_r = float(arr[:, :, 0].mean())
    mean_g = float(arr[:, :, 1].mean())
    mean_b = float(arr[:, :, 2].mean())
    std_b = float(arr[:, :, 2].std())
    rg_delta = abs(mean_r - 128.0) + abs(mean_g - 128.0)
    if mean_b > 150.0 and rg_delta < 85.0 and std_b < 85.0:
        return "normal"
    return None


def analyze_material_type_from_image(source: Image.Image) -> str | None:
    """Infer a Skyrim material category from image colour and texture statistics.

    Returns one of the material category strings (e.g. ``'metal'``, ``'skin'``,
    ``'snow'``, ``'glass'``) if a confident match is found, or ``None`` when the
    image content is ambiguous.  This is a *supplemental* hint — filename-based
    :func:`classify_material_type` always takes priority.

    Heuristic rules:
    * Very low saturation + high brightness → snow/ice or metal candidate.
    * Very high brightness + very low saturation → snow.
    * High saturation + warm hue bias (red/orange range) → metal (gold/copper) or
      skin candidate.
    * Cool neutral colours with a warm-grey average → stone/architecture.
    * High saturation + concentrated green channel → plants.
    * Very pale, warm, low-contrast image → skin.
    * High specular highlight cluster (bright white spots) + low-saturation base
      → glass/crystal or polished metal.
    """
    rgb = source.convert("RGB")
    analysis_source = rgb
    if max(rgb.width, rgb.height) > 512:
        analysis_source = rgb.copy()
        analysis_source.thumbnail((512, 512), Image.Resampling.BILINEAR)

    arr = np.asarray(analysis_source, dtype=np.float32)
    if arr.size == 0:
        return None

    r_mean = float(arr[:, :, 0].mean())
    g_mean = float(arr[:, :, 1].mean())
    b_mean = float(arr[:, :, 2].mean())
    overall_mean = (r_mean + g_mean + b_mean) / 3.0
    r_std = float(arr[:, :, 0].std())
    g_std = float(arr[:, :, 1].std())
    b_std = float(arr[:, :, 2].std())
    overall_std = (r_std + g_std + b_std) / 3.0

    hsv_arr = np.asarray(analysis_source.convert("HSV"), dtype=np.float32)
    sat_mean = float(hsv_arr[:, :, 1].mean())
    val_mean = float(hsv_arr[:, :, 2].mean())
    hue_mean = float(hsv_arr[:, :, 0].mean())

    brightness_ratio = overall_mean / 255.0
    saturation_ratio = sat_mean / 255.0

    # Bright cluster detection: fraction of pixels brighter than 230
    bright_pixels = float(np.mean(arr.max(axis=2) > 230.0))

    # --- Snow / Ice ---
    # Very bright, very low saturation, cool tone
    if brightness_ratio > 0.72 and saturation_ratio < 0.12 and b_mean >= r_mean:
        return "snow"

    # --- Glass / Crystal (polished transparent material) ---
    # High bright cluster, very low saturation, moderate overall brightness
    if bright_pixels > 0.18 and saturation_ratio < 0.15 and 0.3 < brightness_ratio < 0.85:
        return "glass"

    # --- Metal (grey-scale high-value with low saturation) ---
    if saturation_ratio < 0.18 and 0.25 < brightness_ratio < 0.78 and overall_std < 60.0:
        return "metal"

    # --- Skin (warm peachy / flesh tones) ---
    # Red channel dominant, warm hue (10–40 degrees in HSV), moderate saturation
    warm_hue = 10.0 <= (hue_mean / 255.0 * 360.0) <= 45.0
    if warm_hue and 0.12 < saturation_ratio < 0.45 and 0.3 < brightness_ratio < 0.72 and r_mean > g_mean > b_mean:
        return "skin"

    # --- Plants (green-dominant with moderate saturation) ---
    if g_mean > r_mean and g_mean > b_mean and saturation_ratio > 0.20:
        return "plants"

    # --- Leather (warm brown with medium texture contrast) ---
    if (
        r_mean > g_mean > b_mean
        and 0.07 < saturation_ratio < 0.35
        and 0.20 < brightness_ratio < 0.68
        and 16.0 < overall_std < 95.0
    ):
        return "leather"

    # --- Fur (warm/neutral but noisy micro-variation and low shine) ---
    if (
        r_mean >= g_mean >= b_mean
        and saturation_ratio < 0.28
        and 0.18 < brightness_ratio < 0.66
        and overall_std > 34.0
        and bright_pixels < 0.08
    ):
        return "fur"

    # --- Dirt / Mud (earth tones, darker and less reflective than stone) ---
    if (
        (r_mean + g_mean) > (b_mean * 2.0)
        and saturation_ratio < 0.30
        and 0.14 < brightness_ratio < 0.56
        and 15.0 < overall_std < 85.0
    ):
        return "dirt"

    # --- Stone / Architecture (cool-neutral grey-brown with low saturation) ---
    if saturation_ratio < 0.22 and 0.2 < brightness_ratio < 0.65 and overall_std > 18.0:
        return "stone"

    return None


def _adjust_recommendations_for_role(
    recommended: dict[str, float | int], detected_role: str | None
) -> dict[str, float | int]:
    if detected_role is None:
        return recommended
    adjusted = dict(recommended)
    if detected_role == "normal":
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]), 1.1, 1.8)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]), 0.8, 1.2)
        adjusted["complex_strength"] = _clamp(float(adjusted["complex_strength"]), 1.0, 1.5)
        adjusted["glow_threshold"] = int(_clamp(float(adjusted["glow_threshold"]), 190.0, 235.0))
    elif detected_role == "parallax":
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]), 0.8, 1.25)
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]), 1.1, 2.4)
    elif detected_role == "glow":
        adjusted["glow_threshold"] = int(_clamp(float(adjusted["glow_threshold"]), 140.0, 180.0))
    elif detected_role == "environment_mask":
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]), 0.9, 1.35)
    elif detected_role in {"complex_material", "complex_material_cm"}:
        adjusted["complex_strength"] = _clamp(float(adjusted["complex_strength"]), 1.0, 1.6)
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]), 1.1, 2.0)
    return adjusted


_MATERIAL_CATEGORY_TOKENS: dict[str, tuple[str, ...]] = {
    "architecture": (
        "architecture",
        "whiterun",
        "windhelm",
        "solitude",
        "riften",
        "markarth",
        "falkreath",
        "dwemer",
        "imperial",
        "nordic",
        "castle",
        "ruin",
        "ruins",
        "pillar",
        "column",
        "facade",
        "cornerstone",
        "battlement",
        "tower",
        "staircase",
        "stair",
        "crypt",
        "cathedral",
        "fortress",
    ),
    "terrain": ("landscape", "land", "terrain", "ground"),
    "stone": ("stone", "brick", "rock", "cobble", "slate", "granite", "marble", "limestone", "pebble", "rubble", "dungeon", "wall", "cave", "cliff"),
    "wood": ("wood", "timber", "plank", "log", "bark", "trunk", "beam", "wooden", "oak", "pine", "lumber"),
    "plants": ("leaf", "leaves", "grass", "vine", "plant", "moss", "fern", "weed", "shrub", "bush", "flora", "foliage", "lichen"),
    "leather": ("leather", "hide", "strap", "straps", "belt", "belts"),
    "fur": ("fur", "pelt", "pelts", "fleece", "mane"),
    "metal": ("metal", "iron", "steel", "copper", "bronze", "gold", "silver", "ore", "chain", "blade", "sword", "axe", "armor", "helmet", "shield"),
    "glass": ("glass", "crystal", "gem", "jewel", "diamond", "ruby", "sapphire", "emerald", "amethyst", "quartz"),
    "cloth": ("cloth", "fabric", "silk", "linen", "wool", "robe", "cloak", "cape", "canvas", "tapestry"),
    "skin": ("skin", "body", "face", "head", "hand", "flesh", "creature", "humanoid"),
    "snow": ("snow", "ice", "frost", "frozen", "blizzard", "glacial"),
    "dirt": ("dirt", "mud", "soil", "earth", "grime", "silt"),
    "sand": ("sand", "dune", "arid", "dust"),
    "paper": (
        "paper",
        "parchment",
        "scroll",
        "note",
        "book",
        "bookart",
        "card",
        "cards",
        "collectible",
        "sign",
        "signs",
        "painting",
        "paintings",
        "mural",
        "murals",
        "banner",
        "banners",
        "plaque",
        "plaques",
        "fresco",
        "waifu",
        "bbr",
        "poster",
    ),
}


def classify_material_type(path: Path) -> str:
    """Classify the likely Skyrim material category from a texture file path.

    Returns one of: ``'architecture'``, ``'stone'``, ``'wood'``, ``'plants'``,
    ``'metal'``, ``'glass'``, ``'leather'``, ``'fur'``, ``'cloth'``, ``'skin'``,
    ``'snow'``, ``'dirt'``, ``'terrain'``, ``'sand'``, ``'paper'``, or
    ``'general'``.

    Priority order matches ``_MATERIAL_CATEGORY_TOKENS`` dictionary ordering so
    more specific categories (``'architecture'``) are checked before broader ones
    (``'stone'``).
    """
    combined = " ".join(path.parts).lower()
    words = tuple(token for token in re.split(r"[^a-z0-9]+", combined) if token)
    for category, tokens in _MATERIAL_CATEGORY_TOKENS.items():
        for token in tokens:
            if any((word == token) or word.startswith(token) for word in words):
                return category
    return "general"


def _adjust_recommendations_for_material_type(
    recommended: dict[str, float | int], material_type: str
) -> dict[str, float | int]:
    """Fine-tune generated slider recommendations for common Skyrim material categories.

    Cubemap/environment-mask strength is tuned to match realistic reflectivity per
    the Skyrim material guide: glass/ice = very high, polished metal/gold = high,
    wet stone = medium-high, dry stone/wood = medium-low, cloth/skin = very low.
    """
    adjusted = dict(recommended)
    if material_type == "architecture":
        # Architecture: stable medium-form normals, conservative parallax to avoid
        # tiling artefacts, modest env mask (stone/plaster is not highly reflective).
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 0.95, 1.1, 2.8)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.9, 0.8, 1.8)
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.8, 0.9, 1.8)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.85, 0.9, 1.6)
        adjusted["glow_threshold"] = int(_clamp(float(adjusted["glow_threshold"]) * 1.05, 140, 235))
    elif material_type == "stone":
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 1.1, 1.1, 3.8)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 1.1, 0.8, 2.4)
        # Stone is not highly reflective; medium-low cubemap strength
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.85, 0.9, 2.0)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.85, 0.9, 1.8)
        adjusted["glow_threshold"] = int(_clamp(float(adjusted["glow_threshold"]) * 1.05, 140, 235))
    elif material_type == "wood":
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 1.05, 1.1, 3.8)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 1.0, 0.8, 2.4)
        # Wood is low-reflectivity; suppress cubemap/specular
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.7, 0.9, 1.6)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.75, 0.9, 1.6)
    elif material_type == "plants":
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 0.85, 1.1, 3.8)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.6, 0.8, 2.4)
        # Plants have no meaningful cubemap reflection
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.5, 0.9, 1.4)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.55, 0.9, 1.4)
        adjusted["glow_threshold"] = int(_clamp(float(adjusted["glow_threshold"]) * 1.1, 140, 235))
    elif material_type == "metal":
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 1.15, 1.1, 3.8)
        # Metal has high cubemap reflection; boost env mask and specular significantly
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 1.45, 0.9, 2.4)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 1.4, 0.9, 2.2)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.9, 0.8, 2.4)
    elif material_type == "glass":
        # Glass/crystal has very high cubemap reflection; maximum env/specular boost
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 1.75, 0.9, 2.4)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 1.65, 0.9, 2.2)
        # Glass is mostly flat — minimal parallax depth
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.6, 0.8, 1.4)
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 0.9, 1.1, 2.4)
    elif material_type == "cloth":
        # Cloth has virtually no cubemap reflection; suppress both env mask and specular
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.5, 0.9, 1.4)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.55, 0.9, 1.4)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.75, 0.8, 2.0)
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 0.9, 1.1, 2.8)
    elif material_type == "leather":
        # Leather: slightly reflective highlights but far less than polished metal.
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.7, 0.9, 1.7)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.8, 0.9, 1.8)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.95, 0.8, 2.1)
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 0.95, 1.1, 3.0)
    elif material_type == "fur":
        # Fur/fleece should avoid harsh shiny highlights and deep parallax.
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.5, 0.9, 1.4)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.6, 0.9, 1.5)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.8, 0.8, 1.9)
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 0.9, 1.1, 2.8)
    elif material_type == "skin":
        # Skin has low, soft cubemap reflection; soften normals to avoid pore over-sharpening
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.65, 0.9, 1.6)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.8, 0.9, 1.6)
        # Skin normals should be soft — reduce strength to avoid harsh microdetail
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 0.82, 1.1, 2.4)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.5, 0.8, 1.4)
    elif material_type == "snow":
        # Ice has very high cubemap, snow surface itself medium-high
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 1.3, 0.9, 2.4)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 1.15, 0.8, 2.4)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 1.15, 0.9, 2.2)
        # Ice/snow normals need gentle treatment to avoid grain artefacts
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 0.95, 1.1, 3.0)
    elif material_type == "terrain":
        # Terrain/landscape: subtle parallax, stable normals, low cubemap to avoid shimmer
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.85, 0.8, 1.6)
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 0.95, 1.1, 2.6)
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.75, 0.9, 1.8)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.8, 0.9, 1.6)
    elif material_type == "dirt":
        # Dirt/mud should stay matte with minimal reflection sparkle.
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.95, 0.8, 2.0)
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 1.0, 1.1, 3.0)
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.7, 0.9, 1.7)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.7, 0.9, 1.6)
    elif material_type == "sand":
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 1.05, 0.8, 2.4)
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.8, 0.9, 1.8)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.75, 0.9, 1.6)
    elif material_type == "paper":
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 0.8, 1.1, 2.2)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.55, 0.8, 1.35)
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.45, 0.9, 1.3)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.6, 0.9, 1.3)
        adjusted["complex_strength"] = _clamp(float(adjusted["complex_strength"]) * 0.75, 1.0, 1.6)
        adjusted["glow_threshold"] = int(_clamp(float(adjusted["glow_threshold"]) * 1.1, 160, 235))
    return adjusted


_WORKFLOW_PROFILE_TOKENS: dict[str, tuple[str, ...]] = {
    "performance": (
        "performance",
        "perf",
        "lowend",
        "low-end",
        "low_end",
        "potato",
    ),
    "vr": (
        "vr",
        "virtualreality",
        "virtual_reality",
        "virtual-reality",
    ),
    "terrain": (
        "terrain",
        "landscape",
        "ground",
    ),
    "architecture": (
        "architecture",
        "building",
        "ruins",
        "wall",
    ),
    "characters": (
        "character",
        "characters",
        "skin",
        "npc",
        "actor",
    ),
    "interface": (
        "interface",
        "ui",
        "menu",
        "menus",
        "hud",
        "inventory",
        "bookart",
        "card",
        "cards",
        "collectible",
        "waifu",
        "bbr",
    ),
}


def detect_workflow_profile(path: Path) -> str | None:
    combined = " ".join(path.parts).lower()
    for profile, tokens in _WORKFLOW_PROFILE_TOKENS.items():
        if any(token in combined for token in tokens):
            return profile
    return None


_RENDER_PROFILE_PRESETS: dict[str, dict[str, str]] = {
    # Blank custom profile — user-controlled, no forced workflow assumptions.
    "custom": {
        "complex_format": "msn",
        "env_mask_mode": "standard",
        "parallax_mode": "standard",
    },
    # Baseline vanilla Skyrim SE-friendly defaults.
    "vanilla": {
        "complex_format": "msn",
        "env_mask_mode": "standard",
        "parallax_mode": "standard",
    },
    "performance": {
        "complex_format": "msn",
        "env_mask_mode": "standard",
        "parallax_mode": "standard",
    },
    "vr": {
        "complex_format": "msn",
        "env_mask_mode": "standard",
        "parallax_mode": "standard",
    },
    "terrain": {
        "complex_format": "msn",
        "env_mask_mode": "standard",
        "parallax_mode": "standard",
    },
    "architecture": {
        "complex_format": "msn",
        "env_mask_mode": "standard",
        "parallax_mode": "standard",
    },
    "characters": {
        "complex_format": "msn",
        "env_mask_mode": "standard",
        "parallax_mode": "standard",
    },
    # Community Shaders generally expects packed _cm assets while keeping vanilla-friendly
    # environment/parallax mode selections.
    "community_shaders": {
        "complex_format": "cm",
        "env_mask_mode": "standard",
        "parallax_mode": "standard",
    },
    # Community Shaders TruePBR commonly uses _rmaos plus JSON-driven material
    # metadata and keeps a standard _n normal map path.
    "truepbr": {
        "complex_format": "cm",
        "env_mask_mode": "complex",
        "parallax_mode": "standard",
    },
    # ENB profile favors complex env mask and POM height maps.
    "enb": {
        "complex_format": "msn",
        "env_mask_mode": "complex",
        "parallax_mode": "occlusion",
    },
}

_RENDER_PROFILE_LABELS: dict[str, str] = {
    "auto": "Auto-detect",
    "custom": "Custom",
    "vanilla": "Vanilla",
    "performance": "Performance (low-end)",
    "vr": "VR",
    "terrain": "Terrain",
    "architecture": "Architecture",
    "characters": "Characters / Skin",
    "community_shaders": "Community Shaders",
    "truepbr": "Community Shaders TruePBR",
    "enb": "ENB",
}
_RENDER_PROFILE_CLI_VALUES: tuple[str, ...] = tuple(_RENDER_PROFILE_LABELS.keys())
_RENDER_PROFILE_GUI_VALUES: tuple[str, ...] = ("custom",) + tuple(
    profile for profile in _RENDER_PROFILE_CLI_VALUES if profile != "custom"
)

_RENDER_PROFILE_OUTPUT_RECOMMENDATIONS: dict[str, str] = {
    "custom": (
        "Custom profile.\n"
        "No renderer assumptions are applied; this profile is intentionally blank so you can choose all options manually.\n"
        "Guidance text and channel hints are best-effort and may be inaccurate for your specific shader stack.\n"
        "For TruePBR workflows, use dedicated _rmaos/_ramos output plus its JSON sidecar and validate against your installed shader docs."
    ),
    "vanilla": (
        "Vanilla Skyrim SE — no PBR, no ENB required.\n"
        "Files: diffuse.dds + _n.dds (DirectX tangent-space normal).\n"
        "Add _p.dds (greyscale height) only for meshes patched for parallax (requires SKSE64 memory patch).\n"
        "Add _m.dds (greyscale, Slot 5) for reflective metals/armour — brighter = more environment reflection.\n"
        "Add _em.dds (legacy _g.dds also works) only for emissive/glowing assets.\n"
        "How files should look: _n stays purple/blue, _p is greyscale, _m is greyscale with brighter pixels on shinier areas.\n"
        "Do NOT generate _rmaos, _msn, _cm, or _c — those are ignored by the vanilla renderer."
    ),
    "performance": (
        "Performance-oriented Skyrim SE profile for lower-end GPUs.\n"
        "Recommended files: diffuse.dds + _n.dds.\n"
        "Parallax/complex outputs are disabled by default to reduce shader overhead and shimmer.\n"
        "Use extra maps only when needed and keep strengths conservative."
    ),
    "vr": (
        "VR-focused profile tuned for comfort and stability.\n"
        "Recommended files: diffuse.dds + _n.dds with conservative settings.\n"
        "Parallax is disabled by default to reduce depth shimmer and motion discomfort.\n"
        "Add extra maps only after in-headset validation."
    ),
    "terrain": (
        "Terrain profile tuned for stable large-scale surfaces.\n"
        "Recommended files: diffuse.dds + _n.dds + _p.dds.\n"
        "Uses conservative standard parallax to avoid horizon shimmer and texture swimming.\n"
        "Enable extra packed outputs only when your shader stack specifically needs them."
    ),
    "architecture": (
        "Architecture profile for walls, ruins, and structural assets.\n"
        "Recommended files: diffuse.dds + _n.dds + _p.dds + _m.dds.\n"
        "Keeps standard Skyrim-friendly naming with reflection control in slot 5.\n"
        "Use ENB/Community Shaders-specific packed outputs only when targeting those renderer presets."
    ),
    "characters": (
        "Character/skin profile focused on stable animated shading.\n"
        "Recommended files: diffuse.dds + _n.dds (+ optional _em.dds for emissive assets; legacy _g.dds also works).\n"
        "Parallax and complex env workflows are disabled by default to avoid fragile skin/face setups.\n"
        "Use dedicated skin/spec slots in your NIF workflow when needed."
    ),
    "community_shaders": (
        "Community Shaders Extended Materials workflow (not ENB, not TruePBR JSON).\n"
        "Files: diffuse.dds + _n.dds + _p.dds + _cm.dds (or _c.dds / _C.dds — identical channel layout).\n"
        "_cm/_c/_C RGBA channel layout: R=Environment reflection amount, "
        "G=Glossiness, B=Metallic, A=Height / mode-control alpha.\n"
        "How files should look: _n stays purple/blue, _p stays greyscale, and _cm/_c/_C should NOT look like a normal map — "
        "it should read as packed grayscale data with reflective areas bright in red, glossy areas bright in green, metallic areas bright in blue, and a non-white alpha height channel.\n"
        "Add _em.dds (legacy _g.dds also works) only for emissive assets.\n"
        "Do NOT generate _msn or TruePBR-style _rmaos for this preset. Community Shaders and ENB are separate renderer paths and should not be mixed.\n"
        "If you specifically need Community Shaders TruePBR _rmaos, that is a different JSON-driven workflow than this _cm/_c/_C preset."
    ),
    "truepbr": (
        "Community Shaders TruePBR workflow (JSON-driven material path).\n"
        "Typical files: textures/pbr/<name>.dds + textures/pbr/<name>_n.dds + textures/pbr/<name>_rmaos.dds (or _ramos.dds alias) + matching JSON sidecar in PBRNifPatcher/ (and optional _p.dds depending on your mesh/material setup).\n"
        "_rmaos/_ramos RGBA layout for this preset: R=Roughness, G=Metallic, B=Ambient Occlusion, A=Other/smoothness/height (config-driven).\n"
        "How files should look: _n stays purple/blue, _rmaos/_ramos should look like packed grayscale channels (not like a normal map).\n"
        "JSON sidecar should reference the generated base texture prefix (for example via 'texture'/'match_diffuse') and document channel mapping for your TruePBR setup.\n"
        "PGPatcher best-practice: keep PBR textures under textures/pbr and place your patcher JSON files under a top-level PBRNifPatcher/ folder.\n"
        "Do NOT treat this as Community Shaders Extended Materials _cm/_c/_C, and do NOT mix it with ENB _msn workflows."
    ),
    "enb": (
        "ENBSeries workflow presets in this tool target _msn plus complex env-mask mode.\n"
        "Some ENB material pipelines in the wild use different channel packing/naming, so verify against your ENB setup.\n"
        "Preset files here: diffuse.dds + _msn.dds + _p.dds + _m.dds.\n"
        "_msn RGBA channel layout (Slot 1, replaces _n): R=Normal X, G=Normal Y, B=Normal Z, "
        "A=Specular intensity.\n"
        "_m RGBA channel layout in this ENB preset (Slot 5): R=Reflection/specular brightness, "
        "G=Glossiness, B=Metalness (cubemap tint), A=Parallax height.\n"
        "How files should look: _msn should remain a purple/blue normal map with a useful alpha, while _m should look like packed grayscale data — "
        "red = reflectivity, green = gloss, blue = metalness, alpha = height. It should not look like a second normal map.\n"
        "Add _em.dds (legacy _g.dds also works) only for emissive assets.\n"
        "This is not Community Shaders and not 'ENB PBR' — it is ENB complex material. Do not mix it with _cm/_c/_C workflows.\n"
        "Do NOT generate vanilla _n.dds (replaced by _msn), _cm/_c, or TruePBR-style _rmaos for this preset."
    ),
}

_RENDER_PROFILE_NIF_PATCH_DEFAULTS: dict[str, dict[str, bool | str]] = {
    "custom": {
        "enable_parallax": True,
        "enable_pom": False,
        "enable_env_mapping": False,
        "force_shader_type_3": False,
        "prefer_msn_normal": False,
    },
    "vanilla": {
        "enable_parallax": True,
        "enable_pom": False,
        "enable_env_mapping": False,
        "force_shader_type_3": False,
        "prefer_msn_normal": False,
    },
    "performance": {
        "enable_parallax": False,
        "enable_pom": False,
        "enable_env_mapping": False,
        "force_shader_type_3": False,
        "prefer_msn_normal": False,
    },
    "vr": {
        "enable_parallax": False,
        "enable_pom": False,
        "enable_env_mapping": False,
        "force_shader_type_3": False,
        "prefer_msn_normal": False,
    },
    "terrain": {
        "enable_parallax": True,
        "enable_pom": False,
        "enable_env_mapping": False,
        "force_shader_type_3": False,
        "prefer_msn_normal": False,
    },
    "architecture": {
        "enable_parallax": True,
        "enable_pom": False,
        "enable_env_mapping": True,
        "force_shader_type_3": False,
        "prefer_msn_normal": False,
    },
    "characters": {
        "enable_parallax": False,
        "enable_pom": False,
        "enable_env_mapping": False,
        "force_shader_type_3": False,
        "prefer_msn_normal": False,
    },
    "community_shaders": {
        "enable_parallax": True,
        "enable_pom": False,
        "enable_env_mapping": True,
        "force_shader_type_3": True,
        "prefer_msn_normal": False,
    },
    "truepbr": {
        "enable_parallax": True,
        "enable_pom": False,
        "enable_env_mapping": True,
        "force_shader_type_3": True,
        "prefer_msn_normal": False,
    },
    "enb": {
        "enable_parallax": True,
        "enable_pom": True,
        "enable_env_mapping": True,
        "force_shader_type_3": True,
        "prefer_msn_normal": True,
    },
}

_RENDER_PROFILE_OUTPUT_DEFAULTS: dict[str, dict[str, bool]] = {
    "custom": {
        "include_diffuse": False,
        "include_normal": False,
        "include_parallax": False,
        "include_glow": False,
        "include_environment_mask": False,
        "include_rmaos": False,
        "include_complex": False,
    },
    "vanilla": {
        "include_diffuse": True,
        "include_normal": True,
        "include_parallax": False,
        "include_glow": False,
        "include_environment_mask": False,
        "include_rmaos": False,
        "include_complex": False,
    },
    "performance": {
        "include_diffuse": True,
        "include_normal": True,
        "include_parallax": False,
        "include_glow": False,
        "include_environment_mask": False,
        "include_rmaos": False,
        "include_complex": False,
    },
    "vr": {
        "include_diffuse": True,
        "include_normal": True,
        "include_parallax": False,
        "include_glow": False,
        "include_environment_mask": False,
        "include_rmaos": False,
        "include_complex": False,
    },
    "terrain": {
        "include_diffuse": True,
        "include_normal": True,
        "include_parallax": True,
        "include_glow": False,
        "include_environment_mask": False,
        "include_rmaos": False,
        "include_complex": False,
    },
    "architecture": {
        "include_diffuse": True,
        "include_normal": True,
        "include_parallax": True,
        "include_glow": False,
        "include_environment_mask": True,
        "include_rmaos": False,
        "include_complex": False,
    },
    "characters": {
        "include_diffuse": True,
        "include_normal": True,
        "include_parallax": False,
        "include_glow": False,
        "include_environment_mask": False,
        "include_rmaos": False,
        "include_complex": False,
    },
    "community_shaders": {
        "include_diffuse": True,
        "include_normal": True,
        "include_parallax": True,
        "include_glow": False,
        "include_environment_mask": False,
        "include_rmaos": False,
        "include_complex": True,
    },
    "truepbr": {
        "include_diffuse": True,
        "include_normal": True,
        "include_parallax": True,
        "include_glow": False,
        "include_environment_mask": False,
        "include_rmaos": True,
        "include_complex": False,
    },
    "enb": {
        "include_diffuse": True,
        "include_normal": False,
        "include_parallax": True,
        "include_glow": False,
        "include_environment_mask": True,
        "include_rmaos": False,
        "include_complex": True,
    },
}

_RENDER_PROFILE_OUTPUT_LABELS: dict[str, str] = {
    "include_diffuse": "diffuse",
    "include_normal": "normal/_n",
    "include_parallax": "parallax/_p",
    "include_glow": "glow/_g",
    "include_environment_mask": "env mask/_m",
    "include_rmaos": "rmaos/_rmaos/_ramos",
    "include_complex": "complex material",
}

_RENDER_PROFILE_PATH_HINTS: dict[str, tuple[str, ...]] = {
    "enb": ("enb", "enbseries"),
    "community_shaders": ("communityshaders", "community_shaders", "community-shaders", "cs"),
    "truepbr": ("truepbr", "true_pbr", "true-pbr", "pbrnifpatcher", "textures pbr"),
}

_RENDER_PROFILE_MANAGER_HINT_TOKENS: dict[str, tuple[str, ...]] = {
    "enb": ("enb", "enbseries"),
    "community_shaders": ("community shaders", "communityshaders", "complex material", "extended materials"),
    "truepbr": ("truepbr", "true pbr", "pbrnifpatcher", "pbr nif patcher", "rmaos", "ramos"),
}

_RENDER_PROFILE_WORKFLOW_HINTS: dict[str, str] = {
    "performance": "performance",
    "vr": "vr",
    "terrain": "terrain",
    "architecture": "architecture",
    "characters": "characters",
    "interface": "vanilla",
}

_RENDER_PROFILE_ENB_MARKERS: tuple[tuple[str, ...], ...] = (
    ("d3d11.dll",),
    ("d3dcompiler_46e.dll",),
    ("enbseries.ini",),
    ("enbseries",),
)

_RENDER_PROFILE_CS_MARKERS: tuple[tuple[str, ...], ...] = (
    ("skse", "plugins", "communityshaders.dll"),
    ("skse", "plugins", "complexmaterial.dll"),
    ("skse", "plugins", "terrainshadows.dll"),
    ("skse", "plugins", "dynamiccubemaps.dll"),
)

_RENDER_PROFILE_TRUEPBR_MARKERS: tuple[tuple[str, ...], ...] = (
    ("skse", "plugins", "pbrnifpatcher.dll"),
    ("pbrnifpatcher",),
    ("textures", "pbr"),
)


def _normalize_render_profile(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"community shaders", "community_shaders"}:
        return "community_shaders"
    if normalized in {"true pbr", "truepbr"}:
        return "truepbr"
    if normalized in {"character", "character_skin", "character-skin", "skin", "npc"}:
        return "characters"
    if normalized in {"arch", "building"}:
        return "architecture"
    if normalized == "experimental":
        return "custom"
    if normalized in {
        "auto",
        "custom",
        "vanilla",
        "performance",
        "vr",
        "terrain",
        "architecture",
        "characters",
        "community_shaders",
        "truepbr",
        "enb",
    }:
        return normalized
    return "custom"


def _path_exists_casefold(root: Path, parts: tuple[str, ...]) -> bool:
    current = root
    for part in parts:
        direct = current / part
        if direct.exists():
            current = direct
            continue
        try:
            matched = next((child for child in current.iterdir() if child.name.lower() == part.lower()), None)
        except Exception:
            return False
        if matched is None:
            return False
        current = matched
    return True


def resolve_env_mask_complex_workflow(
    *,
    env_mask_mode: str,
    complex_format: str = "msn",
    render_profile: str = "auto",
    include_complex: bool | None = None,
) -> str:
    """Resolve how complex env-mask channels should be packed."""
    if _normalize_env_mask_mode(env_mask_mode) != "complex":
        return "standard"
    normalized_profile = _normalize_render_profile(render_profile)
    if normalized_profile == "enb":
        return "enb"
    if normalized_profile == "truepbr":
        return "truepbr"
    if include_complex is False:
        return "enb"
    if include_complex is True:
        return "enb" if _normalize_complex_format(complex_format) == "msn" else "truepbr"
    return "enb" if _normalize_complex_format(complex_format) == "msn" else "truepbr"


def describe_render_profile_output_recommendation(profile: str) -> str:
    normalized = _normalize_render_profile(profile)
    if normalized == "auto":
        normalized = "vanilla"
    return _RENDER_PROFILE_OUTPUT_RECOMMENDATIONS.get(normalized, _RENDER_PROFILE_OUTPUT_RECOMMENDATIONS["vanilla"])


def resolve_nif_patch_defaults_for_render_profile(
    selected_profile: str,
    *,
    recommended_profile: str | None = None,
) -> dict[str, bool | str]:
    normalized_selected = _normalize_render_profile(selected_profile)
    normalized_recommended = _normalize_render_profile(recommended_profile)
    effective_profile = normalized_recommended if normalized_selected == "auto" else normalized_selected
    if effective_profile == "auto":
        effective_profile = "vanilla"
    defaults = _RENDER_PROFILE_NIF_PATCH_DEFAULTS.get(effective_profile, _RENDER_PROFILE_NIF_PATCH_DEFAULTS["vanilla"])
    return {"selected_profile": normalized_selected, "effective_profile": effective_profile, **defaults}


def get_nif_patch_option_warnings(
    *,
    selected_profile: str,
    recommended_profile: str | None = None,
    enable_parallax: bool,
    enable_pom: bool,
    enable_env_mapping: bool,
    force_shader_type_3: bool,
    parallax_texture_path: str = "",
    normal_texture_path: str = "",
    env_mask_texture_path: str = "",
    glow_texture_path: str = "",
    cubemap_texture_path: str = "",
    diffuse_texture_path: str = "",
    disable_parallax: bool = False,
    disable_pom: bool = False,
    disable_env_mapping: bool = False,
    disable_glow_map: bool = False,
    clear_parallax_texture_path: bool = False,
    clear_normal_texture_path: bool = False,
    clear_env_mask_texture_path: bool = False,
    clear_glow_texture_path: bool = False,
    clear_diffuse_texture_path: bool = False,
    clear_cubemap_texture_path: bool = False,
) -> list[str]:
    warnings: list[str] = []
    normalized_selected_profile = _normalize_render_profile(str(selected_profile or "auto"))
    normalized_recommended_profile = _normalize_render_profile(recommended_profile)
    effective_profile = (
        normalized_recommended_profile
        if normalized_selected_profile == "auto"
        else normalized_selected_profile
    )
    if effective_profile == "auto":
        effective_profile = "vanilla"
    if effective_profile == "vanilla" and enable_pom:
        warnings.append("ENB POM is usually incorrect for vanilla meshes.")
    if effective_profile in {"performance", "vr"} and enable_parallax:
        warnings.append("Performance/VR workflows usually keep parallax disabled to reduce shimmer and GPU cost.")
    if effective_profile == "characters" and enable_parallax:
        warnings.append("Character workflows usually keep parallax disabled to avoid unstable face/skin shading.")
    if enable_pom and not force_shader_type_3:
        warnings.append("POM works best with shader type 3 enabled.")
    if enable_env_mapping and not env_mask_texture_path.strip() and effective_profile != "enb":
        warnings.append("Environment mapping enabled with empty slot 5 path.")
    if parallax_texture_path.strip() and not (enable_parallax or enable_pom):
        warnings.append("Parallax slot path set, but parallax/POM flags are disabled.")
    if env_mask_texture_path.strip() and not enable_env_mapping:
        warnings.append("Environment mask path set, but environment mapping flag is disabled.")
    if disable_parallax and enable_parallax:
        warnings.append("Both enable and disable parallax are checked.")
    if disable_pom and enable_pom:
        warnings.append("Both enable and disable POM are checked.")
    if disable_env_mapping and enable_env_mapping:
        warnings.append("Both enable and disable environment mapping are checked.")
    if disable_glow_map and glow_texture_path.strip():
        warnings.append("Glow map is set to both disable and write a slot 2 path.")
    if clear_parallax_texture_path and parallax_texture_path.strip():
        warnings.append("Parallax slot is set to both clear and write a path.")
    if clear_normal_texture_path and normal_texture_path.strip():
        warnings.append("Normal slot is set to both clear and write a path.")
    if clear_env_mask_texture_path and env_mask_texture_path.strip():
        warnings.append("Environment mask slot is set to both clear and write a path.")
    if clear_glow_texture_path and glow_texture_path.strip():
        warnings.append("Glow slot is set to both clear and write a path.")
    if clear_diffuse_texture_path and diffuse_texture_path.strip():
        warnings.append("Diffuse slot is set to both clear and write a path.")
    if clear_cubemap_texture_path and cubemap_texture_path.strip():
        warnings.append("Cubemap slot is set to both clear and write a path.")

    path_rules = (
        ("Diffuse slot", diffuse_texture_path, ("_n.dds", "_msn.dds", "_p.dds", "_g.dds", "_m.dds", "_cm.dds", "_c.dds", "_rmaos.dds", "_ramos.dds"), True),
        ("Parallax slot", parallax_texture_path, ("_p.dds",)),
        ("Normal slot", normal_texture_path, ("_n.dds", "_msn.dds"), False),
        ("Glow slot", glow_texture_path, ("_em.dds", "_g.dds", "_glow.dds", "_emit.dds", "_emissive.dds"), False),
        ("Cubemap slot", cubemap_texture_path, ("_e.dds", "_cube.dds", "_env.dds", "_envmap.dds"), False),
        ("Env mask slot", env_mask_texture_path, ("_m.dds", "_mask.dds", "_envmask.dds", "_cm.dds", "_c.dds", "_rmaos.dds", "_ramos.dds"), False),
    )
    for rule in path_rules:
        label, raw_path, suffixes, *extra = rule
        is_diffuse_rule = bool(extra[0]) if extra else False
        normalized = raw_path.strip().replace("/", "\\")
        if not normalized:
            continue
        lowered = normalized.lower()
        if re.match(r"^[a-z]:\\", lowered):
            warnings.append(
                f"{label} path is absolute; use Skyrim-relative textures\\... paths to avoid broken in-game lookups."
            )
        if lowered.startswith("data\\textures\\"):
            warnings.append(
                f"{label} path should not include data\\ prefix; use textures\\... instead."
            )
        if not lowered.startswith("textures\\") and "textures\\" not in lowered:
            warnings.append(f"{label} path should start with textures\\ for Skyrim-relative lookups.")
        lowered_suffixes = tuple(s.lower() for s in suffixes)
        if is_diffuse_rule:
            if lowered.endswith(lowered_suffixes):
                warnings.append(
                    f"{label} path looks like a generated map type (_n/_p/_g/_m/_cm/_c/_rmaos/_ramos); slot 0 should usually be diffuse/albedo."
                )
            continue
        if not lowered.endswith(lowered_suffixes):
            warnings.append(f"{label} path should usually end with {', '.join(suffixes)}.")

    normal_lower = normal_texture_path.strip().replace("/", "\\").lower()
    env_lower = env_mask_texture_path.strip().replace("/", "\\").lower()
    if effective_profile == "enb":
        if normal_lower.endswith("_n.dds"):
            warnings.append("ENB workflows usually use _msn.dds in slot 1 instead of _n.dds.")
        if env_lower.endswith(("_cm.dds", "_c.dds", "_rmaos.dds", "_ramos.dds")):
            warnings.append("ENB slot 5 usually uses _m.dds, not _cm/_c/_rmaos variants.")
    elif effective_profile == "community_shaders":
        if normal_lower.endswith("_msn.dds"):
            warnings.append("Community Shaders workflows usually use _n.dds normals, not _msn.dds.")
        if env_lower.endswith(("_rmaos.dds", "_ramos.dds")):
            warnings.append("Community Shaders (non-TruePBR) slot 5 usually uses _cm/_c, not _rmaos/_ramos.")
    elif effective_profile == "truepbr":
        if normal_lower.endswith("_msn.dds"):
            warnings.append("TruePBR workflows usually use _n.dds normals, not _msn.dds.")
        if env_lower.endswith(("_cm.dds", "_c.dds")):
            warnings.append("TruePBR slot 5 usually uses _rmaos/_ramos, not _cm/_c.")

    return warnings


def resolve_render_profile_output_defaults(
    selected_profile: str,
    *,
    recommended_profile: str | None = None,
) -> dict[str, bool | str]:
    normalized_selected = _normalize_render_profile(selected_profile)
    normalized_recommended = _normalize_render_profile(recommended_profile)
    effective_profile = normalized_recommended if normalized_selected == "auto" else normalized_selected
    if effective_profile == "auto":
        effective_profile = "vanilla"
    defaults = _RENDER_PROFILE_OUTPUT_DEFAULTS.get(effective_profile, _RENDER_PROFILE_OUTPUT_DEFAULTS["vanilla"])
    return {"selected_profile": normalized_selected, "effective_profile": effective_profile, **defaults}


def describe_render_profile_default_outputs(profile: str) -> str:
    normalized = _normalize_render_profile(profile)
    if normalized == "auto":
        normalized = "vanilla"
    defaults = _RENDER_PROFILE_OUTPUT_DEFAULTS.get(normalized, _RENDER_PROFILE_OUTPUT_DEFAULTS["vanilla"])
    enabled = [
        label for key, label in _RENDER_PROFILE_OUTPUT_LABELS.items()
        if defaults.get(key, False)
    ]
    disabled = [
        label for key, label in _RENDER_PROFILE_OUTPUT_LABELS.items()
        if not defaults.get(key, False)
    ]
    enabled_text = ", ".join(enabled) if enabled else "nothing"
    disabled_text = ", ".join(disabled) if disabled else "nothing"
    return f"Auto-check: {enabled_text}. Auto-uncheck: {disabled_text}."


def describe_render_profile_files_to_create(profile: str) -> str:
    normalized = _normalize_render_profile(profile)
    if normalized == "auto":
        normalized = "vanilla"
    if normalized == "custom":
        return "Suggested files: custom/manual workflow (choose only the maps your shader stack expects)."

    defaults = _RENDER_PROFILE_OUTPUT_DEFAULTS.get(normalized, _RENDER_PROFILE_OUTPUT_DEFAULTS["vanilla"])
    suffix_map = {
        "include_diffuse": "<stem>.dds",
        "include_normal": "<stem>_n.dds",
        "include_parallax": "<stem>_p.dds",
        "include_glow": "<stem>_em.dds (legacy: <stem>_g.dds)",
        "include_environment_mask": "<stem>_m.dds",
        "include_rmaos": "<stem>_rmaos.dds (or <stem>_ramos.dds)",
        "include_complex": "<stem>_msn.dds",
    }
    if normalized == "community_shaders":
        suffix_map["include_environment_mask"] = "<stem>_cm.dds (or <stem>_c.dds)"
        suffix_map["include_complex"] = "<stem>_cm.dds (or <stem>_c.dds)"
    elif normalized == "truepbr":
        suffix_map["include_environment_mask"] = "<stem>_rmaos.dds (or <stem>_ramos.dds)"
        suffix_map["include_complex"] = "<stem>_rmaos.dds (or <stem>_ramos.dds)"
    elif normalized == "enb":
        suffix_map["include_normal"] = "<stem>_msn.dds"
        suffix_map["include_complex"] = "<stem>_msn.dds"

    files: list[str] = []
    for key in _RENDER_PROFILE_OUTPUT_LABELS:
        if defaults.get(key):
            file_hint = suffix_map.get(key)
            if file_hint and file_hint not in files:
                files.append(file_hint)
    if not files:
        return "Suggested files: none by default for this profile."
    return f"Suggested files: {', '.join(files)}."


def build_render_profile_recommendation_message(recommended_profile: str) -> str:
    normalized = _normalize_render_profile(recommended_profile)
    if normalized == "auto":
        normalized = "vanilla"
    label = _RENDER_PROFILE_LABELS.get(normalized, normalized.replace("_", " ").title())
    resolved = resolve_render_profile_options("auto", recommended_profile=normalized)
    workflow_hint = {
        "custom": "manual blank profile",
        "vanilla": "best for stock Skyrim SE / safest defaults",
        "performance": "best for lower-end systems and stability-first outputs",
        "vr": "best for VR comfort-focused conservative outputs",
        "terrain": "best for stable terrain outputs with conservative parallax",
        "architecture": "best for architecture/walls with standard slot-5 env mask defaults",
        "characters": "best for skin/character assets with safer non-parallax defaults",
        "community_shaders": "best for Community Shaders packed-material workflows",
        "truepbr": "best for Community Shaders TruePBR JSON-driven workflows",
        "enb": "best for ENB complex material + POM workflows",
    }.get(normalized, "recommended workflow")
    tuple_hint = (
        f"{resolved['complex_format']} / env {resolved['env_mask_mode']} / "
        f"parallax {resolved['parallax_mode']}"
    )
    lines = [
        "⚠ Render profile guidance is EXPERIMENTAL and may be inaccurate for some setups.",
        f"Suggested target: {label} ({workflow_hint}) → {tuple_hint}.",
        describe_render_profile_output_recommendation(normalized),
        describe_render_profile_default_outputs(normalized),
        "",
        "Renderer quick guide:",
    ]
    for profile in (
        "custom",
        "vanilla",
        "performance",
        "vr",
        "terrain",
        "architecture",
        "characters",
        "community_shaders",
        "truepbr",
        "enb",
    ):
        lines.append(
            f"- {_RENDER_PROFILE_LABELS[profile]}: "
            f"{describe_render_profile_default_outputs(profile)} "
            f"{describe_render_profile_output_recommendation(profile)}"
        )
    return "\n".join(lines)


def build_render_profile_brief_message(recommended_profile: str) -> str:
    normalized = _normalize_render_profile(recommended_profile)
    if normalized == "auto":
        normalized = "vanilla"
    label = _RENDER_PROFILE_LABELS.get(normalized, normalized.replace("_", " ").title())
    defaults = describe_render_profile_default_outputs(normalized)
    files = describe_render_profile_files_to_create(normalized)
    return (
        f"Suggested target: {label}. {defaults} {files} "
        "Auto-detect prioritizes installed renderer markers/suffix hints first, then falls back to content profile hints. "
        "Click Help for full renderer/channel guide."
    )


def recommend_render_profile(
    input_path: Path | None,
    *,
    source: Image.Image | None = None,
    detected_role: str | None = None,
    detected_suffix: str | None = None,
    material_type: str = "general",
    workflow_profile: str | None = None,
    manager_context: ModManagerContext | None = None,
) -> str:
    """Recommend target rendering profile for map format/mode defaults."""
    inferred_workflow = workflow_profile
    inferred_material_type = material_type
    if input_path is not None:
        if inferred_workflow is None:
            inferred_workflow = detect_workflow_profile(input_path)
        if inferred_material_type == "general":
            inferred_material_type = classify_material_type(input_path)
    normalized_suffix = (detected_suffix or "").strip().lower()
    if normalized_suffix in {"_cm", "_c"}:
        return "community_shaders"
    if normalized_suffix == "_msn":
        return "enb"
    if normalized_suffix in {"_rmaos", "_ramos"}:
        return "truepbr"
    if detected_role == "complex_material_cm":
        return "community_shaders"
    if detected_role == "complex_material":
        return "enb"
    explicit_workflow_hint = _RENDER_PROFILE_WORKFLOW_HINTS.get((workflow_profile or "").strip().lower())
    if explicit_workflow_hint is not None:
        return explicit_workflow_hint
    if inferred_material_type == "paper":
        return "vanilla"
    if input_path is not None:
        combined = " ".join(input_path.parts).lower()
        for profile, tokens in _RENDER_PROFILE_PATH_HINTS.items():
            if any(token in combined for token in tokens):
                return profile
    manager_profile = detect_render_profile_from_mod_manager_context(manager_context)
    if manager_profile is not None:
        return manager_profile
    inferred_workflow_hint = _RENDER_PROFILE_WORKFLOW_HINTS.get((inferred_workflow or "").strip().lower())
    if inferred_workflow_hint is not None:
        return inferred_workflow_hint
    if inferred_material_type in {"terrain", "snow", "dirt", "sand"}:
        return "terrain"
    if inferred_material_type == "architecture":
        return "architecture"
    if inferred_material_type in {"skin", "fur"}:
        return "characters"
    return "vanilla"


def detect_render_profile_from_mod_manager_context(context: ModManagerContext | None) -> str | None:
    """Infer installed renderer profile hints from detected MO2/Vortex context."""
    if context is None or context.manager is None:
        return None
    candidates: list[str] = []
    for mod_name in context.loaded_mods:
        normalized = str(mod_name).strip().lower()
        if normalized:
            candidates.append(normalized)
    for plugin_name in context.enabled_plugins:
        normalized = str(plugin_name).strip().lower()
        if normalized:
            candidates.append(normalized)
    for load_order_name in context.load_order:
        normalized = str(load_order_name).strip().lower()
        if normalized:
            candidates.append(normalized)
    for path in (*context.loaded_texture_dirs, *context.loaded_mesh_dirs):
        parent_name = path.parent.name.strip().lower()
        if parent_name:
            candidates.append(parent_name)
    has_truepbr = any(
        token in candidate
        for candidate in candidates
        for token in _RENDER_PROFILE_MANAGER_HINT_TOKENS["truepbr"]
    )
    has_cs = any(
        token in candidate
        for candidate in candidates
        for token in _RENDER_PROFILE_MANAGER_HINT_TOKENS["community_shaders"]
    )
    has_enb = any(
        token in candidate
        for candidate in candidates
        for token in _RENDER_PROFILE_MANAGER_HINT_TOKENS["enb"]
    )
    marker_roots: list[Path] = []
    marker_roots.extend(path for path in (context.game_root, context.instance_root, context.staging_root, context.output_dir) if path is not None)
    if context.game_root is not None:
        marker_roots.append(context.game_root / "Data")
    marker_roots.extend(path.parent for path in (*context.loaded_texture_dirs, *context.loaded_mesh_dirs))
    for root in _unique_existing_paths(marker_roots):
        for parts in _RENDER_PROFILE_ENB_MARKERS:
            if _path_exists_casefold(root, parts):
                has_enb = True
                break
        for parts in _RENDER_PROFILE_CS_MARKERS:
            if _path_exists_casefold(root, parts):
                has_cs = True
                break
        for parts in _RENDER_PROFILE_TRUEPBR_MARKERS:
            if _path_exists_casefold(root, parts):
                has_truepbr = True
                break
    if has_truepbr:
        return "truepbr"
    if has_cs:
        return "community_shaders"
    if has_enb and not has_cs:
        return "enb"
    return None


def resolve_render_profile_options(
    selected_profile: str,
    *,
    recommended_profile: str | None = None,
) -> dict[str, str]:
    """Resolve effective output mode settings for a selected/auto profile.

    When the caller has *explicitly* selected a profile (anything other than
    ``"auto"``), every setting from that preset is applied verbatim, including
    ``parallax_mode="occlusion"`` for the ENB preset.

    When the profile is ``"auto"`` the recommended profile controls
    ``complex_format`` and ``env_mask_mode`` (safe format changes), but
    ``parallax_mode`` is **always kept at ``"standard"``**.  Parallax occlusion
    (ENB POM) requires ENBSeries to be installed and its heavy smoothing can
    degrade the pop-out depth effect on signs and artwork that relied on the
    original standard height map.  Users who want POM occlusion should
    explicitly choose the ENB render profile.
    """
    normalized_selected = _normalize_render_profile(selected_profile)
    normalized_recommended = _normalize_render_profile(recommended_profile)
    effective_profile = normalized_recommended if normalized_selected == "auto" else normalized_selected
    if effective_profile == "auto":
        effective_profile = "vanilla"
    preset = _RENDER_PROFILE_PRESETS[effective_profile]
    # When auto-detecting, keep parallax in standard mode so that sign/artwork
    # textures keep their pop-out effect regardless of which renderer is detected.
    parallax_mode = preset["parallax_mode"] if normalized_selected != "auto" else "standard"
    return {
        "selected_profile": normalized_selected,
        "effective_profile": effective_profile,
        "complex_format": preset["complex_format"],
        "env_mask_mode": preset["env_mask_mode"],
        "parallax_mode": parallax_mode,
    }


def _normalize_complex_format(value: str | None, default: str = "msn") -> str:
    normalized = (value or "").strip().lower()
    return normalized if normalized in {"msn", "cm"} else default


def _normalize_env_mask_mode(value: str | None, default: str = "standard") -> str:
    normalized = (value or "").strip().lower()
    return normalized if normalized in {"standard", "complex"} else default


def _normalize_parallax_mode_key(value: str | None, default: str = "standard") -> str:
    normalized = (value or "").strip().lower()
    if "occlusion" in normalized:
        return "occlusion"
    if normalized == "standard":
        return "standard"
    return default


def resolve_render_profile_mode_selection(
    current_modes: Mapping[str, str],
    *,
    selected_profile: str,
    recommended_profile: str | None = None,
    apply_preset: bool,
) -> dict[str, str]:
    resolved = resolve_render_profile_options(selected_profile, recommended_profile=recommended_profile)
    if apply_preset:
        return resolved
    return {
        "selected_profile": resolved["selected_profile"],
        "effective_profile": resolved["effective_profile"],
        "complex_format": _normalize_complex_format(current_modes.get("complex_format"), resolved["complex_format"]),
        "env_mask_mode": _normalize_env_mask_mode(current_modes.get("env_mask_mode"), resolved["env_mask_mode"]),
        "parallax_mode": _normalize_parallax_mode_key(current_modes.get("parallax_mode"), resolved["parallax_mode"]),
    }


def resolve_render_profile_guardrails(
    *,
    selected_profile: str,
    recommended_profile: str | None = None,
    complex_format: str,
    env_mask_mode: str,
    parallax_mode: str,
) -> dict[str, object]:
    """Resolve/guard core workflow modes when a renderer profile is selected."""
    normalized_selected = _normalize_render_profile(selected_profile)
    has_recommended_profile = bool(str(recommended_profile or "").strip())
    normalized_recommended = _normalize_render_profile(recommended_profile) if has_recommended_profile else "auto"
    current_complex_format = _normalize_complex_format(complex_format)
    current_env_mask_mode = _normalize_env_mask_mode(env_mask_mode)
    current_parallax_mode = _normalize_parallax_mode_key(parallax_mode)
    if normalized_selected == "custom":
        return {
            "selected_profile": normalized_selected,
            "effective_profile": "custom",
            "complex_format": current_complex_format,
            "env_mask_mode": current_env_mask_mode,
            "parallax_mode": current_parallax_mode,
            "changes": [],
        }
    if normalized_selected == "auto" and normalized_recommended in {"custom", "auto"} and has_recommended_profile:
        normalized_recommended = "vanilla"
    should_enforce = not (normalized_selected == "auto" and not has_recommended_profile)
    if not should_enforce:
        return {
            "selected_profile": normalized_selected,
            "effective_profile": normalized_recommended if normalized_recommended != "auto" else "auto",
            "complex_format": current_complex_format,
            "env_mask_mode": current_env_mask_mode,
            "parallax_mode": current_parallax_mode,
            "changes": [],
        }
    resolved = resolve_render_profile_options(
        normalized_selected,
        recommended_profile=normalized_recommended if normalized_recommended != "auto" else None,
    )
    changes: list[str] = []
    if current_complex_format != resolved["complex_format"]:
        changes.append(
            f"complex format {current_complex_format} -> {resolved['complex_format']}"
        )
    if current_env_mask_mode != resolved["env_mask_mode"]:
        changes.append(
            f"env mask mode {current_env_mask_mode} -> {resolved['env_mask_mode']}"
        )
    if current_parallax_mode != resolved["parallax_mode"]:
        changes.append(
            f"parallax mode {current_parallax_mode} -> {resolved['parallax_mode']}"
        )
    return {
        "selected_profile": resolved["selected_profile"],
        "effective_profile": resolved["effective_profile"],
        "complex_format": resolved["complex_format"],
        "env_mask_mode": resolved["env_mask_mode"],
        "parallax_mode": resolved["parallax_mode"],
        "changes": changes,
    }


def _adjust_recommendations_for_workflow_profile(
    recommended: dict[str, float | int], workflow_profile: str | None
) -> dict[str, float | int]:
    if workflow_profile is None:
        return recommended
    adjusted = dict(recommended)
    if workflow_profile == "interface":
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]), 1.1, 1.5)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]), 0.8, 1.0)
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]), 0.9, 1.15)
        adjusted["complex_strength"] = _clamp(float(adjusted["complex_strength"]), 1.0, 1.35)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]), 0.9, 1.15)
        adjusted["glow_threshold"] = int(_clamp(float(adjusted["glow_threshold"]), 200.0, 235.0))
    return adjusted


def recommend_generation_settings(source: Image.Image, input_path: Path | None = None) -> dict[str, float | int]:
    analysis = analyze_image_content(source)
    brightness = analysis["brightness"]
    contrast = analysis["contrast"]
    edge_strength = analysis["edge_strength"]
    edge_variance = analysis["edge_variance"]
    detail_energy = analysis["detail_energy"]
    dog_fine_energy = analysis["dog_fine_energy"]
    dog_medium_energy = analysis["dog_medium_energy"]
    laplacian_energy = analysis["laplacian_energy"]
    dynamic_range = analysis["dynamic_range"]
    shadow_ratio = analysis["shadow_ratio"]
    highlight_ratio = analysis["highlight_ratio"]
    bright_cluster_ratio = analysis["bright_cluster_ratio"]
    midtone_ratio = analysis["midtone_ratio"]
    p90_luma = analysis["p90_luma"]
    saturation_mean = analysis["saturation_mean"]
    saturation_variance = analysis["saturation_variance"]
    low_saturation_ratio = analysis["low_saturation_ratio"]
    color_variance = analysis["color_variance"]
    megapixels = analysis["megapixels"]

    # Multi-scale edge signal is a better predictor than single-scale alone
    combined_edge = (edge_strength * 0.4) + (dog_fine_energy * 0.35) + (dog_medium_energy * 0.25)
    detail_guard = _clamp((detail_energy - 16.0) / 36.0, 0.0, 1.0)
    laplacian_guard = _clamp((laplacian_energy - 6.0) / 18.0, 0.0, 1.0)
    size_guard = _clamp((megapixels - 1.0) / 6.0, 0.0, 1.0)
    overdetail_guard = _clamp(
        (detail_guard * 0.55) + (laplacian_guard * 0.3) + (size_guard * 0.5), 0.0, 1.0
    )

    normal_strength = _clamp(
        1.1
        + (combined_edge / 155.0)
        + (edge_variance / 220.0)
        + (detail_energy / 120.0)
        + (color_variance / 900.0),
        1.1,
        3.8,
    )
    parallax_strength = _clamp(
        0.9
        + (detail_energy / 120.0)
        + (dynamic_range / 800.0)
        + ((0.25 - highlight_ratio) * 0.7)
        + (low_saturation_ratio * 0.25),
        0.8,
        2.4,
    )
    environment_mask_strength = _clamp(
        1.0
        + (contrast / 185.0)
        + (midtone_ratio * 0.35)
        + (low_saturation_ratio * 0.65)
        + ((1.0 - (saturation_mean / 255.0)) * 0.5)
        + (highlight_ratio * 0.3),
        0.9,
        2.4,
    )
    rmaos_strength = _clamp(
        environment_mask_strength
        + (detail_energy / 220.0)
        + (dynamic_range / 1600.0)
        + (low_saturation_ratio * 0.22),
        0.9,
        3.0,
    )
    complex_strength = _clamp(
        1.05
        + (combined_edge / 200.0)
        + (detail_energy / 120.0)
        + (dynamic_range / 1100.0)
        + (saturation_variance / 1050.0)
        + (highlight_ratio * 0.25),
        1.0,
        2.6,
    )
    specular_strength = _clamp(
        0.9
        + (highlight_ratio * 1.4)
        + (dog_fine_energy / 240.0)
        + (edge_variance / 310.0)
        + ((0.3 - shadow_ratio) * 0.35)
        + (low_saturation_ratio * 0.45)
        + ((1.0 - (saturation_mean / 255.0)) * 0.35),
        0.9,
        2.2,
    )
    normal_strength = _clamp(normal_strength * (1.0 - (overdetail_guard * 0.22)), 1.1, 3.8)
    parallax_strength = _clamp(parallax_strength * (1.0 - (overdetail_guard * 0.28)), 0.8, 2.4)
    environment_mask_strength = _clamp(environment_mask_strength * (1.0 - (overdetail_guard * 0.16)), 0.9, 2.4)
    rmaos_strength = _clamp(rmaos_strength * (1.0 - (overdetail_guard * 0.12)), 0.9, 3.0)
    complex_strength = _clamp(complex_strength * (1.0 - (overdetail_guard * 0.2)), 1.0, 2.6)
    specular_strength = _clamp(specular_strength * (1.0 - (overdetail_guard * 0.24)), 0.9, 2.2)
    glow_threshold = int(
        _clamp(
            (p90_luma * 0.78)
            + (brightness * 0.2)
            + (contrast * 0.25)
            + (highlight_ratio * 18.0)
            - (bright_cluster_ratio * 30.0)
            - (detail_energy * 0.12)
            - ((saturation_mean / 255.0) * 12.0),
            140.0,
            235.0,
        )
    )

    recommended: dict[str, float | int] = {
        "normal_strength": normal_strength,
        "parallax_strength": parallax_strength,
        "environment_mask_strength": environment_mask_strength,
        "rmaos_strength": rmaos_strength,
        "complex_strength": complex_strength,
        "specular_strength": specular_strength,
        "glow_threshold": glow_threshold,
    }
    detected_role: str | None = None
    material_type = "general"
    workflow_profile: str | None = None
    if input_path is not None:
        detected_role = identify_skyrim_texture_role(input_path)["role"]
        workflow_profile = detect_workflow_profile(input_path)
        if detected_role in {None, "diffuse"}:
            material_type = classify_material_type(input_path)
    if detected_role in {None, "diffuse"}:
        inferred_from_image = _infer_likely_role_from_image(source)
        if inferred_from_image is not None:
            detected_role = inferred_from_image
    # When the filename gives no specific material, fall back to image-content analysis.
    if material_type == "general":
        image_material = analyze_material_type_from_image(source)
        if image_material is not None:
            material_type = image_material
    role_adjusted = _adjust_recommendations_for_role(recommended, detected_role)
    material_adjusted = _adjust_recommendations_for_material_type(role_adjusted, material_type)
    workflow_adjusted = _adjust_recommendations_for_workflow_profile(material_adjusted, workflow_profile)
    final = _adjust_recommendations_for_role(workflow_adjusted, detected_role)
    final["rmaos_strength"] = _clamp(
        float(final.get("rmaos_strength", final["environment_mask_strength"])),
        0.9,
        3.0,
    )
    return final


def recommend_output_resolution(
    width: int,
    height: int,
    material_type: str = "general",
) -> tuple[int, str]:
    """Recommend a maximum texture dimension for Skyrim SE based on asset type.

    Returns ``(max_dimension, reason)`` where ``max_dimension`` is the largest
    side length that makes practical sense for the given material category and
    ``reason`` is a short human-readable explanation.

    Skyrim SE's renderer and VRAM budgets have well-established sweet spots:

    * **Terrain / landscape** — 2048 px maximum.  Landscape textures tile across
      large areas; going above 2 K delivers diminishing returns and eats VRAM on
      every outdoor cell.
    * **Characters / skin / faces** — 2048 px for body/clothing, 1024–2048 for
      face textures.  Animated meshes already limit visible texel density.
    * **Small clutter / paper / plants / fur** — 1024 px.  Items viewed from
      close range rarely exceed 512 texels on-screen; 2 K is wasted here.
    * **Architecture / stone / wood** — 4096 px acceptable for hero assets, 2048
      for tiling surfaces.
    * **All other materials** — 4096 px maximum.  8 K+ DDS is rarely beneficial
      given Skyrim's mipmap chain, LOD distances, and typical GPU VRAM limits.

    Parameters
    ----------
    width, height:
        Source image dimensions in pixels.
    material_type:
        Skyrim material category (from :func:`classify_material_type`).

    Returns
    -------
    tuple[int, str]
        ``(max_dimension, reason)`` — caller decides whether to downscale.
    """
    _MATERIAL_MAX_DIM: dict[str, tuple[int, str]] = {
        "terrain": (
            2048,
            "Terrain/landscape textures tile across large outdoor areas — 2048 px is the practical "
            "Skyrim SE ceiling beyond which VRAM cost rises without visible quality gain.",
        ),
        "skin": (
            2048,
            "Character skin and face textures are applied to animated meshes that limit visible "
            "texel density; 2048 px gives excellent results without excessive VRAM use.",
        ),
        "plants": (
            1024,
            "Foliage textures are applied to flat alpha-masked polygons typically viewed from "
            "mid-range; 1024 px is the Skyrim SE sweet spot for these assets.",
        ),
        "fur": (
            1024,
            "Fur/pelt textures have high-frequency micro-variation that is already blurred by "
            "mipmapping at typical viewing distances; 1024 px is sufficient.",
        ),
        "paper": (
            1024,
            "Book-art, card, and parchment textures are usually small on-screen and gain little "
            "from resolutions above 1024 px.",
        ),
        "cloth": (
            2048,
            "Cloth and fabric textures benefit from up to 2048 px on hero armor/robe pieces; "
            "generic tiling cloth looks best at 1024 px.",
        ),
        "dirt": (
            2048,
            "Ground-cover dirt/mud textures tile aggressively in Skyrim — 2048 px provides "
            "detail without the tiling artefacts that high-resolution exaggerates.",
        ),
        "sand": (
            2048,
            "Sand and arid-ground textures tile over large surfaces; 2048 px balances detail "
            "and VRAM cost for typical desert landscape use.",
        ),
        "architecture": (
            4096,
            "Hero architecture assets (unique buildings, major landmarks) can use up to 4096 px; "
            "generic tiling walls and floors are best kept at 2048 px.",
        ),
    }

    # Maximum dimension Skyrim SE ever meaningfully uses regardless of material type.
    _ABSOLUTE_MAX = 4096

    normalised = str(material_type or "general").lower().strip()
    max_dim, reason = _MATERIAL_MAX_DIM.get(normalised, (
        _ABSOLUTE_MAX,
        "4096 px is the practical maximum for most Skyrim SE materials — 8 K+ DDS files "
        "deliver no visible improvement given the engine's mipmap chain and typical VRAM budgets.",
    ))

    source_max = max(int(width), int(height))
    if source_max <= max_dim:
        return (max_dim, reason)

    # Distinguish between "above sweet spot" and "unnecessarily 8 K+".
    if source_max > 8192:
        reason = (
            f"Source resolution ({source_max} px) exceeds 8192 px — unusually large for "
            f"Skyrim SE.  {reason}"
        )
    elif source_max > _ABSOLUTE_MAX:
        reason = (
            f"Source resolution ({source_max} px) exceeds the practical 4096 px ceiling for "
            f"Skyrim SE.  {reason}"
        )
    return (max_dim, reason)


def _resolve_batch_workers(batch_workers: int | None, total: int) -> int:
    if total <= 1:
        return 1
    if batch_workers is not None and batch_workers > 0:
        return int(_clamp(float(batch_workers), 1.0, 16.0))
    if total < 4:
        return 1
    cpu_count = os.cpu_count() or 2
    return max(1, min(4, cpu_count // 2))


def _prepare_height_map(source: Image.Image) -> Image.Image:
    detail_source = _combined_detail_source(source)
    resolution_scale = _clamp(math.sqrt((source.width * source.height) / (1024.0 * 1024.0)), 1.0, 2.6)
    broad = detail_source.filter(ImageFilter.GaussianBlur(radius=4.0 * resolution_scale))
    fine = detail_source.filter(
        ImageFilter.UnsharpMask(
            radius=1.2 + (resolution_scale * 0.45),
            percent=170,
            threshold=int(2 + (resolution_scale * 2)),
        )
    )
    detail = ImageChops.subtract(fine, broad, scale=1.0, offset=128)
    edge_energy = detail_source.filter(ImageFilter.FIND_EDGES).filter(
        ImageFilter.GaussianBlur(radius=0.7 + (resolution_scale * 0.2))
    )
    edge_energy = ImageOps.autocontrast(edge_energy, cutoff=2)
    edge_energy = ImageEnhance.Contrast(edge_energy).enhance(1.18)
    merged = ImageChops.add(
        detail_source,
        detail,
        scale=_clamp(1.55 - ((resolution_scale - 1.0) * 0.18), 1.18, 1.55),
        offset=int(_clamp(-36.0 + ((resolution_scale - 1.0) * 8.0), -36.0, -22.0)),
    )
    merged = ImageChops.add(merged, edge_energy, scale=1.22, offset=-10)
    return ImageOps.autocontrast(merged, cutoff=1)


def _lift_black_floor(image: Image.Image, floor: int = 16) -> Image.Image:
    return image.point(lambda value: int(_clamp(max(float(floor), float(value)), 0.0, 255.0)))


def _split_detail_images(source: Image.Image) -> tuple[Image.Image, Image.Image, Image.Image]:
    rgb_source = source.convert("RGB")
    grayscale = ImageOps.grayscale(rgb_source)
    red, green, blue = rgb_source.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
    chroma = ImageChops.subtract(maximum, minimum)
    return rgb_source, grayscale, chroma


def _detail_spans(source: Image.Image) -> tuple[int, int]:
    _, grayscale, chroma = _split_detail_images(source)
    grayscale_min, grayscale_max = grayscale.getextrema()
    chroma_min, chroma_max = chroma.getextrema()
    return grayscale_max - grayscale_min, chroma_max - chroma_min


def _combined_detail_source(source: Image.Image) -> Image.Image:
    _, grayscale, chroma = _split_detail_images(source)
    boosted_chroma = ImageEnhance.Contrast(chroma).enhance(1.55)
    grayscale_edges = grayscale.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(radius=0.45))
    chroma_edges = boosted_chroma.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(radius=0.45))
    edge_drive = ImageChops.lighter(ImageChops.lighter(grayscale, boosted_chroma), ImageChops.lighter(grayscale_edges, chroma_edges))
    return Image.blend(grayscale, edge_drive, alpha=0.46)


def _detail_pressure(source: Image.Image) -> float:
    # Downsample to max 512 px for performance — the pressure signal is a scalar
    # statistic that does not require full resolution to be representative.
    analysis = source
    if max(source.width, source.height) > 512:
        analysis = source.copy()
        analysis.thumbnail((512, 512), Image.Resampling.NEAREST)
    detail_source = _combined_detail_source(analysis)
    high_freq = ImageChops.difference(detail_source, detail_source.filter(ImageFilter.GaussianBlur(radius=1.2)))
    edges = detail_source.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(radius=0.5))
    edge_mean = float(ImageStat.Stat(edges).mean[0])
    freq_mean = float(ImageStat.Stat(high_freq).mean[0])
    return _clamp(((freq_mean * 0.72) + (edge_mean * 0.42) - 8.0) / 24.0, 0.0, 1.0)


def generate_diffuse(source: Image.Image) -> Image.Image:
    return ImageOps.autocontrast(source.convert("RGB"), cutoff=1)


def generate_parallax(source: Image.Image, strength: float = 1.35, relief_mode: bool = False) -> Image.Image:
    """Generate a parallax height map (_p.dds).

    Parameters
    ----------
    source :
        Input diffuse texture.
    strength :
        Depth/contrast intensity.
    relief_mode :
        When ``True``, use luminosity-as-height so that bright subjects
        (paintings, signs, murals) appear to protrude from the surface in
        game.  Combine with ``relief_mode=True`` in :func:`generate_normal`
        for a full bas-relief effect.
    """
    pressure = _detail_pressure(source)
    normalized_strength = _clamp((float(strength) - 0.1) / (6.0 - 0.1), 0.0, 1.0)
    if relief_mode:
        height_map = _prepare_relief_height_map(source, pressure=pressure)
        smoothed = height_map.filter(ImageFilter.GaussianBlur(radius=0.9))
        contrasted = ImageEnhance.Contrast(smoothed).enhance(
            _clamp((strength * 1.16) * (1.0 - (pressure * 0.08)), 0.1, 6.0)
        )
        normalized = ImageOps.autocontrast(contrasted, cutoff=1)
        depth_blend = _clamp(0.34 + (normalized_strength * 0.9), 0.34, 1.0)
        tuned = Image.blend(Image.new("L", normalized.size, color=127), normalized, alpha=depth_blend)
        if normalized_strength > 0.48:
            tuned = ImageEnhance.Contrast(tuned).enhance(1.0 + ((normalized_strength - 0.48) * 1.15))
        return tuned
    height_map = _prepare_height_map(source)
    softened = height_map.filter(ImageFilter.GaussianBlur(radius=1.2))
    micro_detail = ImageChops.subtract(
        height_map,
        height_map.filter(ImageFilter.GaussianBlur(radius=8.0 + (pressure * 6.0))),
        scale=1.0,
        offset=128,
    )
    merged = ImageChops.add(softened, micro_detail, scale=1.35 - (pressure * 0.35), offset=int(-20 + (pressure * 10.0)))
    contrasted = ImageEnhance.Contrast(merged).enhance(_clamp((strength * 1.08) * (1.0 - (pressure * 0.14)), 0.1, 6.0))
    normalized = ImageOps.autocontrast(contrasted, cutoff=1)
    depth_blend = _clamp(0.2 + (normalized_strength * 0.9), 0.2, 1.0)
    tuned = Image.blend(Image.new("L", normalized.size, color=127), normalized, alpha=depth_blend)
    if normalized_strength > 0.78:
        tuned = ImageEnhance.Contrast(tuned).enhance(1.0 + ((normalized_strength - 0.78) * 1.6))
    return tuned


def generate_parallax_occlusion(source: Image.Image, strength: float = 1.35, *, relief_mode: bool = False) -> Image.Image:
    """Generate a heightmap optimised for Parallax Occlusion Mapping via ENBSeries.

    Unlike :func:`generate_parallax`, which preserves high-frequency micro-detail
    for simple offset parallax shaders, this function produces a smooth,
    gradient-rich heightmap that eliminates the sharp transitions that cause
    visible stair-stepping artefacts when ENBSeries performs ray-marched parallax
    occlusion (POM) at grazing view angles.

    The output naming and file role are identical to standard parallax (``_p.dds``),
    so the same NIF/material setup applies.  The improvement is purely in heightmap
    quality when read by ENBSeries with ``EnableParallax=true`` and parallax
    occlusion enabled.

    Parameters
    ----------
    source:
        Input diffuse texture.
    strength:
        Depth/contrast intensity (0.5–2.5).  Values of 1.0–1.5 give realistic
        depth for most surfaces.  Values above 2.0 can produce extreme depth at
        grazing angles with POM and are best reserved for strongly sculptured
        surfaces such as heavy carved stone.
    relief_mode:
        When ``True``, use luminosity-as-height so that bright subjects (paintings,
        signs, murals) appear to protrude from the surface.  Mirrors the behaviour
        of :func:`generate_parallax` with ``relief_mode=True`` but produces a
        smoother gradient suited for ENB POM ray-marching.
    """
    grayscale_span, chroma_span = _detail_spans(source)
    if grayscale_span <= 2 and chroma_span <= 8:
        return Image.new("L", source.size, color=127)

    base_height = _prepare_relief_height_map(source) if relief_mode else _prepare_height_map(source)
    pressure = _detail_pressure(source)
    resolution_scale = _clamp(math.sqrt((source.width * source.height) / (1024.0 * 1024.0)), 1.0, 2.6)
    normalized_strength = _clamp((float(strength) - 0.1) / (6.0 - 0.1), 0.0, 1.0)

    # Multi-scale smoothing keeps large silhouette/macro gradients while reducing
    # high-frequency stair-stepping artefacts under ENB POM ray-marching.
    broad = base_height.filter(ImageFilter.GaussianBlur(radius=(2.7 + (pressure * 1.35)) * resolution_scale))
    medium = base_height.filter(ImageFilter.GaussianBlur(radius=(1.0 + (pressure * 0.35)) * resolution_scale))
    macro = Image.blend(broad, medium, alpha=_clamp(0.35 + (pressure * 0.14), 0.35, 0.5))
    slope = ImageChops.difference(medium, broad)
    slope = ImageEnhance.Contrast(slope).enhance(_clamp(0.55 - (pressure * 0.2), 0.3, 0.55))
    blended = ImageChops.add(macro, slope, scale=1.0, offset=-16)

    # Full 0–255 dynamic range gives ENB POM the widest usable depth field.
    normalized = ImageOps.autocontrast(blended, cutoff=0)

    # Contrast enhancement proportional to requested strength, clamped so that
    # extreme values do not produce depth artefacts at steep view angles.
    contrast_drive = strength * (1.04 if relief_mode else 0.98)
    contrasted = ImageEnhance.Contrast(normalized).enhance(_clamp(contrast_drive * (0.98 - (pressure * 0.12)), 0.1, 6.0))

    # Final light smooth pass removes any residual pixel-edge artefacts.
    final = contrasted.filter(ImageFilter.GaussianBlur(radius=0.65 + (pressure * 0.35)))
    normalized = ImageOps.autocontrast(final, cutoff=0)
    reference_depth = base_height.filter(ImageFilter.GaussianBlur(radius=1.4 + (pressure * 0.5)))
    normalized = Image.blend(reference_depth, normalized, alpha=0.72 if relief_mode else 0.68)
    depth_blend = _clamp((0.3 if relief_mode else 0.24) + (normalized_strength * (0.9 if relief_mode else 0.84)), 0.24, 1.0)
    normalized = Image.blend(Image.new("L", normalized.size, color=127), normalized, alpha=depth_blend)
    if pressure < 0.1:
        neutral = Image.new("L", source.size, color=127)
        return Image.blend(neutral, normalized, alpha=0.7)
    return normalized


def _map_parallax_strength_to_nif_scale(parallax_strength: float | None) -> float | None:
    """Map GUI/CLI parallax strength (0.1–6.0) to NIF parallax_scale (0.1–10.0)."""
    if parallax_strength is None:
        return None
    strength = _clamp(float(parallax_strength), 0.1, 6.0)
    if strength <= 1.0:
        mapped = 0.2 + (strength * 1.4)
    else:
        mapped = 1.6 + ((strength - 1.0) * (8.4 / 5.0))
    return _clamp(mapped, 0.1, 10.0)


def _prepare_emboss_height_map(source: Image.Image, pressure: float | None = None) -> Image.Image:
    """Prepare a height map optimised for emboss-style normal generation.

    Unlike :func:`_prepare_height_map`, which builds smooth terrain gradients
    from broad luminance variation, this function exaggerates *edges* —
    transitions between printed text, artwork borders, decorative elements, and
    the flat background on book/card/scroll surfaces.  Flat uniform areas
    remain at mid-grey and produce (128, 128, 255) flat normals; ridge/valley
    edges at design element boundaries produce strong directional normals that
    simulate physically embossed or debossed detail.
    """
    grayscale = _combined_detail_source(source).filter(ImageFilter.MedianFilter(size=3))
    minimum, maximum = grayscale.getextrema()
    _, chroma_span = _detail_spans(source)
    neutral = Image.new("L", source.size, color=128)
    if (maximum - minimum) <= 4 and chroma_span <= 8:
        return neutral
    resolved_pressure = _detail_pressure(source) if pressure is None else pressure
    if resolved_pressure < 0.08:
        return neutral
    softened = grayscale.filter(ImageFilter.GaussianBlur(radius=0.45))
    # Fine and medium unsharp passes catch both thin glyph strokes and broader ornaments.
    fine = softened.filter(ImageFilter.UnsharpMask(radius=0.7, percent=360, threshold=2))
    medium = softened.filter(ImageFilter.UnsharpMask(radius=1.9, percent=220, threshold=3))
    edge_energy = softened.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(radius=0.6))
    edge_energy = ImageOps.autocontrast(edge_energy, cutoff=3)
    edge_energy = ImageEnhance.Contrast(edge_energy).enhance(1.35)
    reinforced = ImageChops.add(Image.blend(fine, medium, alpha=0.45), edge_energy, scale=1.35, offset=0)
    normalized = ImageOps.autocontrast(reinforced, cutoff=1)
    # Keep flat regions close to neutral to avoid "always bumpy" paper surfaces.
    emboss_blend = _clamp(0.68 + (resolved_pressure * 0.18), 0.68, 0.86)
    return Image.blend(neutral, normalized, alpha=emboss_blend)


def _prepare_relief_height_map(source: Image.Image, pressure: float | None = None) -> Image.Image:
    """Prepare a luminosity-as-height map for bas-relief / pop-out normal generation.

    Unlike terrain height maps that encode broad surface gradients, and emboss
    maps that exaggerate printed edges, this function treats the image's own
    luminosity as the height field: bright subjects protrude toward the viewer
    while dark areas recede.  The result makes paintings, murals, signs, and
    decorative plaques appear as if they are physically extruded from the wall
    surface — a bas-relief effect that reads correctly under Skyrim's parallax
    and normal-map shading.

    Parameters
    ----------
    source :
        Input diffuse texture (painting, mural, sign, card, etc.).
    pressure :
        Pre-computed detail pressure scalar; computed if not supplied.
    """
    grayscale = ImageOps.grayscale(source)
    detail_source = _combined_detail_source(source)
    minimum, maximum = grayscale.getextrema()
    _, chroma_span = _detail_spans(source)
    if (maximum - minimum) <= 4 and chroma_span <= 8:
        return Image.new("L", source.size, color=128)
    resolved_pressure = _detail_pressure(source) if pressure is None else pressure

    # Use full luminosity range as height — subjects that are brighter than the
    # background will naturally protrude.  A gentle bilateral-like blur (median
    # then Gaussian) preserves subject silhouettes while smoothing in-region noise.
    smoothed = Image.blend(grayscale, detail_source, alpha=0.28).filter(ImageFilter.MedianFilter(size=3)).filter(
        ImageFilter.GaussianBlur(radius=0.8 + (resolved_pressure * 0.6))
    )
    # Subject-edge sharpening: DoG emphasises subject/background boundaries so
    # the transition from protrusion to recess is crisp.
    dog = ImageChops.subtract(
        smoothed,
        smoothed.filter(ImageFilter.GaussianBlur(radius=3.5)),
        scale=1.0,
        offset=128,
    )
    edge_mask = detail_source.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(radius=0.6))
    edge_mask = ImageEnhance.Contrast(ImageOps.autocontrast(edge_mask, cutoff=2)).enhance(1.25)
    ridge = ImageEnhance.Contrast(ImageChops.lighter(dog, edge_mask)).enhance(1.6)
    blended = ImageChops.add(smoothed, ridge, scale=1.8, offset=-40)
    normalized = ImageOps.autocontrast(blended, cutoff=1)
    # Blend toward neutral to avoid punching through very flat regions.
    relief_blend = _clamp(0.72 + (resolved_pressure * 0.14), 0.72, 0.86)
    return Image.blend(Image.new("L", source.size, color=128), normalized, alpha=relief_blend)


def generate_normal(
    source: Image.Image,
    strength: float = 2.0,
    directx: bool = True,
    emboss_mode: bool = False,
    relief_mode: bool = False,
) -> Image.Image:
    """Generate a DirectX-style (default) or OpenGL-style normal map.

    Parameters
    ----------
    source:
        Input diffuse texture.
    strength:
        Normal map intensity (higher = more pronounced surface detail).
    directx:
        When ``True`` (default) the green channel is DirectX-oriented (Y-up),
        matching the convention expected by Skyrim SE.  Pass ``False`` for
        OpenGL-style Y-down.
    emboss_mode:
        When ``True``, use an edge-ridge height map instead of a smooth
        gradient height map.  This gives flat surfaces (books, cards, scrolls,
        posters) physically plausible embossed/debossed depth detail by
        raising the edges of printed artwork, text, and borders into the normal
        map while leaving uniform background areas as flat normals.
    relief_mode:
        When ``True``, use luminosity-as-height to generate a bas-relief normal
        map.  Bright subjects protrude toward the viewer while dark areas
        recede, making paintings, murals, signs, and decorated plaques appear
        physically extruded from the surface.  This is ideal for any flat
        artwork that should look 3-D when viewed in game.  ``emboss_mode`` and
        ``relief_mode`` are mutually exclusive; ``relief_mode`` takes priority.
    """
    grayscale_span, chroma_span = _detail_spans(source)
    if grayscale_span <= 2 and chroma_span <= 8:
        return Image.new("RGB", source.size, color=(128, 128, 255))

    pressure = _detail_pressure(source)
    if relief_mode:
        effective_strength = _clamp(strength * (1.0 - (pressure * 0.06)), 0.1, 8.0)
        height_map = _prepare_relief_height_map(source, pressure=pressure).filter(
            ImageFilter.GaussianBlur(radius=0.25 + (pressure * 0.25))
        )
    elif emboss_mode:
        effective_strength = _clamp(strength * (1.0 - (pressure * 0.08)), 0.1, 8.0)
        # Slight adaptive blur keeps crisp embossed ridges while suppressing pixel chatter.
        height_map = _prepare_emboss_height_map(source, pressure=pressure).filter(
            ImageFilter.GaussianBlur(radius=0.30 + (pressure * 0.30))
        )
    else:
        resolution_scale = _clamp(math.sqrt((source.width * source.height) / (1024.0 * 1024.0)), 1.0, 2.8)
        effective_strength = _clamp(strength * (1.0 - (pressure * 0.2)), 0.1, 8.0)
        height_map = _prepare_height_map(source).filter(
            ImageFilter.GaussianBlur(radius=0.8 + (pressure * 0.9) + ((resolution_scale - 1.0) * 0.35))
        )

    # Scharr operator — superior rotational accuracy over standard Sobel,
    # especially for diagonal surface features and fine curved details.
    # Kernel: [[-3, 0, 3], [-10, 0, 10], [-3, 0, 3]], scale = 32.
    scharr_x = height_map.filter(ImageFilter.Kernel((3, 3), (-3, 0, 3, -10, 0, 10, -3, 0, 3), scale=32, offset=128))
    scharr_y = height_map.filter(ImageFilter.Kernel((3, 3), (-3, -10, -3, 0, 0, 0, 3, 10, 3), scale=32, offset=128))
    green_sign = 1.0 if directx else -1.0

    # Vectorised numpy computation — orders of magnitude faster than a Python loop
    # for large textures (2 K / 4 K / 8 K) and produces identical pixel values.
    sx = np.frombuffer(scharr_x.tobytes(), dtype=np.uint8).astype(np.float32)
    sy = np.frombuffer(scharr_y.tobytes(), dtype=np.uint8).astype(np.float32)

    red_arr = np.clip(128.0 - (sx - 128.0) * effective_strength, 0.0, 255.0).astype(np.uint8)
    green_arr = np.clip(128.0 + (sy - 128.0) * effective_strength * green_sign, 0.0, 255.0).astype(np.uint8)

    normal_x = (red_arr.astype(np.float32) - 128.0) / 127.0
    normal_y = (green_arr.astype(np.float32) - 128.0) / 127.0
    horizontal_sq = normal_x * normal_x + normal_y * normal_y
    normal_z = np.sqrt(np.clip(1.0 - np.clip(horizontal_sq, 0.0, 1.0), 0.0, None))
    blue_arr = np.clip(128.0 + normal_z * 127.0, 128.0, 255.0).astype(np.uint8)

    red = Image.fromarray(_reshape_image_channel(red_arr, height_map.size, label="Normal map red"), mode="L")
    green = Image.fromarray(_reshape_image_channel(green_arr, height_map.size, label="Normal map green"), mode="L")
    blue = Image.fromarray(_reshape_image_channel(blue_arr, height_map.size, label="Normal map blue"), mode="L")
    return Image.merge("RGB", (red, green, blue))


def generate_glow(source: Image.Image, threshold: int = 190) -> Image.Image:
    grayscale = ImageOps.grayscale(source)
    boosted = ImageEnhance.Contrast(grayscale).enhance(1.35)
    denominator = max(1.0, 255.0 - float(threshold))
    rolled = boosted.point(lambda value: int(_clamp(((float(value) - threshold) / denominator) * 255.0, 0.0, 255.0)))
    softened = rolled.filter(ImageFilter.GaussianBlur(radius=0.9))
    return ImageEnhance.Brightness(softened).enhance(1.05).point(lambda value: int(_clamp(value, 0.0, 255.0)))


def generate_environment_mask(source: Image.Image, strength: float = 1.2, mode: str = "standard") -> Image.Image:
    """Generate an environment mask for Skyrim SE.

    Parameters
    ----------
    source:
        Input diffuse texture.
    strength:
        Contrast/intensity strength factor (higher = stronger reflection/glossiness).
    mode:
        ``"complex"`` — RGBA texture using TruePBR-style RMAOS channel packing
        (R=Roughness, G=Metallic, B=AO, A=Other/height). Use
        :func:`generate_environment_mask_for_workflow` when you need explicit
        ENB channel packing.

        ``"standard"`` (default) — Greyscale (L mode) texture for the vanilla Skyrim SE
        ``_m.dds`` texture slot (Slot 5 in the NIF).  Controls environment/specular
        reflection intensity only.  Works without any mods or ENBSeries.
        Brighter = more environment reflection.
    """
    return generate_environment_mask_for_workflow(
        source,
        strength=strength,
        mode=mode,
        complex_workflow="truepbr",
    )


def generate_environment_mask_for_workflow(
    source: Image.Image,
    *,
    strength: float = 1.2,
    mode: str = "standard",
    complex_workflow: str = "truepbr",
) -> Image.Image:
    if mode == "standard":
        return _generate_standard_env_mask(source, strength)

    normalized_complex_workflow = str(complex_workflow or "truepbr").strip().lower()
    if normalized_complex_workflow not in {"enb", "truepbr"}:
        normalized_complex_workflow = "truepbr"

    rgb_source = source.convert("RGB")
    grayscale = ImageOps.grayscale(rgb_source).filter(ImageFilter.GaussianBlur(radius=0.8))
    grayscale_min, grayscale_max = grayscale.getextrema()
    if (grayscale_max - grayscale_min) <= 2:
        if normalized_complex_workflow == "enb":
            reflection = _lift_black_floor(Image.new("L", grayscale.size, color=112), floor=12)
            glossiness = _lift_black_floor(Image.new("L", grayscale.size, color=132), floor=8)
            metalness = _lift_black_floor(Image.new("L", grayscale.size, color=18), floor=4)
            height = _lift_black_floor(Image.new("L", grayscale.size, color=127), floor=24)
            return Image.merge("RGBA", (reflection, glossiness, metalness, height))
        roughness = _lift_black_floor(Image.new("L", grayscale.size, color=182), floor=24)
        metallic = _lift_black_floor(Image.new("L", grayscale.size, color=18), floor=6)
        ao = _lift_black_floor(Image.new("L", grayscale.size, color=214), floor=24)
        specular_height = _lift_black_floor(Image.new("L", grayscale.size, color=127), floor=24)
        return Image.merge("RGBA", (roughness, metallic, ao, specular_height))

    red, green, blue = rgb_source.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
    chroma = ImageChops.subtract(maximum, minimum)

    specular = generate_specular(rgb_source, strength=max(0.1, min(8.0, strength + 0.15)))

    metallic = Image.blend(chroma, specular, alpha=_clamp(0.22 + (strength * 0.1), 0.22, 0.85))
    metallic = ImageEnhance.Contrast(metallic).enhance(0.75 + (strength * 0.34))
    metallic = _lift_black_floor(ImageOps.autocontrast(metallic, cutoff=0), floor=6)

    raw_height = generate_parallax(rgb_source, strength=max(0.85, min(2.0, strength)))
    specular_height = Image.blend(raw_height, specular, alpha=_clamp(0.28 + (strength * 0.1), 0.28, 0.82))
    specular_height = Image.blend(Image.new("L", raw_height.size, color=127), specular_height, alpha=0.74)
    specular_height = _lift_black_floor(specular_height, floor=24)
    specular_height = specular_height.point(lambda value: int(_clamp(float(value), 24.0, 224.0)))
    if normalized_complex_workflow == "enb":
        reflection = Image.blend(grayscale, specular, alpha=_clamp(0.56 + (strength * 0.06), 0.56, 0.88))
        reflection = ImageEnhance.Contrast(reflection).enhance(0.84 + (strength * 0.2))
        reflection = _lift_black_floor(ImageOps.autocontrast(reflection, cutoff=0), floor=12)
        glossiness = ImageEnhance.Contrast(specular).enhance(0.88 + (strength * 0.28))
        glossiness = _lift_black_floor(ImageOps.autocontrast(glossiness, cutoff=0), floor=8)
        metalness = _lift_black_floor(metallic, floor=4)
        return Image.merge("RGBA", (reflection, glossiness, metalness, specular_height))

    roughness = ImageOps.invert(specular)
    roughness = ImageEnhance.Contrast(roughness).enhance(0.85 + (strength * 0.32))
    roughness = _lift_black_floor(ImageOps.autocontrast(roughness, cutoff=0), floor=12)
    local_detail = ImageChops.difference(grayscale, grayscale.filter(ImageFilter.GaussianBlur(radius=2.4)))
    cavity = ImageOps.invert(ImageEnhance.Contrast(local_detail).enhance(1.05 + (strength * 0.24)))
    ao = Image.blend(grayscale, cavity, alpha=_clamp(0.52 + (strength * 0.12), 0.52, 0.92))
    ao = _lift_black_floor(ImageOps.autocontrast(ao, cutoff=0), floor=24)
    return Image.merge("RGBA", (roughness, metallic, ao, specular_height))


def _generate_standard_env_mask(source: Image.Image, strength: float = 1.2) -> Image.Image:
    """Standard Skyrim SE environment mask (Texture Slot 5, ``_m.dds``).

    Returns a greyscale ``L``-mode image where pixel brightness controls environment
    reflection intensity — brighter areas reflect more.  This is the correct format
    for vanilla Skyrim SE without ENBSeries.
    """
    rgb_source = source.convert("RGB")
    grayscale = ImageOps.grayscale(rgb_source)
    # Derive reflection intensity from specular features of the diffuse texture.
    # Bright/shiny-looking areas in the diffuse are more reflective.
    specular = generate_specular(rgb_source, strength=max(0.1, min(8.0, strength)))
    # Blend base luminance with the specular highlight estimate.
    env_mask = Image.blend(grayscale, specular, alpha=0.62)
    env_mask = ImageEnhance.Contrast(env_mask).enhance(0.8 + (strength * 0.28))
    env_mask = _lift_black_floor(ImageOps.autocontrast(env_mask, cutoff=1), floor=14)
    return env_mask


def generate_complex_material(source: Image.Image, strength: float = 1.15) -> Image.Image:
    rgb_source = source.convert("RGB")
    grayscale = ImageOps.grayscale(rgb_source).filter(ImageFilter.GaussianBlur(radius=0.8))
    grayscale_min, grayscale_max = grayscale.getextrema()
    if (grayscale_max - grayscale_min) <= 2:
        env_reflection = _lift_black_floor(Image.new("L", grayscale.size, color=112), floor=16)
        glossiness = _lift_black_floor(Image.new("L", grayscale.size, color=132), floor=8)
        metallic = _lift_black_floor(Image.new("L", grayscale.size, color=18), floor=4)
        height = _lift_black_floor(Image.new("L", grayscale.size, color=127), floor=24)
        return Image.merge("RGBA", (env_reflection, glossiness, metallic, height))

    red, green, blue = rgb_source.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
    chroma = ImageChops.subtract(maximum, minimum)

    specular_drive = max(0.1, min(8.0, 0.7 + (strength * 0.6)))
    specular = generate_specular(rgb_source, strength=specular_drive)

    env_reflection = Image.blend(grayscale, specular, alpha=_clamp(0.56 + (strength * 0.06), 0.56, 0.86))
    env_reflection = ImageEnhance.Contrast(env_reflection).enhance(0.82 + (strength * 0.22))
    env_reflection = _lift_black_floor(ImageOps.autocontrast(env_reflection, cutoff=0), floor=12)

    glossiness = ImageEnhance.Contrast(specular).enhance(0.86 + (strength * 0.28))
    glossiness = _lift_black_floor(ImageOps.autocontrast(glossiness, cutoff=0), floor=6)

    metallic_contrast = 0.7 + (strength * 0.4)
    metallic = Image.blend(chroma, specular, alpha=_clamp(0.2 + (strength * 0.1), 0.2, 0.85))
    metallic = ImageEnhance.Contrast(metallic).enhance(metallic_contrast)
    metallic = _lift_black_floor(ImageOps.autocontrast(metallic, cutoff=0), floor=3)

    height_specular_alpha = _clamp(0.16 + (strength * 0.1), 0.16, 0.82)
    raw_height = generate_parallax(rgb_source, strength=max(0.1, min(8.0, strength)))
    height = Image.blend(raw_height, specular, alpha=height_specular_alpha)
    blend_alpha = _clamp(0.64 + (strength * 0.1), 0.64, 0.92)
    height = Image.blend(Image.new("L", raw_height.size, color=127), height, alpha=blend_alpha)
    height = _lift_black_floor(height, floor=8)
    height = height.point(lambda value: int(_clamp(float(value), 8.0, 224.0)))

    return Image.merge("RGBA", (env_reflection, glossiness, metallic, height))


def generate_specular(source: Image.Image, strength: float = 1.15) -> Image.Image:
    detail_source = _combined_detail_source(source)
    grayscale = ImageOps.grayscale(source)
    _, _, chroma = _split_detail_images(source)
    grayscale_min, grayscale_max = grayscale.getextrema()
    chroma_min, chroma_max = chroma.getextrema()
    if (grayscale_max - grayscale_min) <= 2 and (chroma_max - chroma_min) <= 8:
        return _lift_black_floor(detail_source, floor=8)

    pressure = _detail_pressure(source)
    base_blur_radius = 1.0 + (pressure * 1.0)
    broad_blur_radius = 2.4 + (pressure * 1.4)
    smoothed = detail_source.filter(ImageFilter.GaussianBlur(radius=base_blur_radius))
    # abs-difference (PIL ImageChops.difference is always ≥ 0)
    local_detail = ImageChops.difference(detail_source, detail_source.filter(ImageFilter.GaussianBlur(radius=broad_blur_radius)))

    # --- numpy path: float32 throughout so no integer-rounding holes ---
    sm_arr = np.frombuffer(smoothed.tobytes(), dtype=np.uint8).astype(np.float32)
    ld_arr = np.frombuffer(local_detail.tobytes(), dtype=np.uint8).astype(np.float32)

    # Contrast-enhance the smoothed base (ImageEnhance.Contrast equivalent)
    contrast_factor = 1.08 - (pressure * 0.1)
    sm_mean = sm_arr.mean()
    base_arr = np.clip((sm_arr - sm_mean) * contrast_factor + sm_mean, 0.0, 255.0)

    # Brightness-scale the local detail (ImageEnhance.Brightness equivalent)
    brightness_factor = 0.55 - (pressure * 0.2)
    detail_arr = np.clip(ld_arr * brightness_factor, 0.0, 255.0)

    # Combine (ImageChops.add with scale and offset)
    scale = 1.45 + (pressure * 0.2)
    specular_arr = np.clip((base_arr + detail_arr) / scale + 4.0, 0.0, 255.0)

    # GaussianBlur softening step — bounce through PIL (no native numpy FFT)
    soft_radius = 1.2 + (pressure * 0.4)
    specular_img = Image.fromarray(
        _reshape_image_channel(specular_arr, grayscale.size, label="Specular").astype(np.uint8),
        mode="L",
    )
    softened = specular_img.filter(ImageFilter.GaussianBlur(radius=soft_radius))
    soft_arr = np.frombuffer(softened.tobytes(), dtype=np.uint8).astype(np.float32)

    # Hard floor before contrast so no downstream step can create true-black holes
    soft_arr = np.maximum(soft_arr, 12.0)

    # Contrast enhancement
    effective_strength = _clamp(strength * (1.0 - (pressure * 0.18)), 0.1, 6.0)
    soft_mean = soft_arr.mean()
    contrasted_arr = np.clip((soft_arr - soft_mean) * effective_strength + soft_mean, 0.0, 255.0)

    # Percentile-based range stretch (replaces PIL autocontrast with cutoff=2).
    # Float32 arithmetic guarantees the lifted floor value (12.0) is never
    # accidentally pushed to zero by integer truncation inside autocontrast.
    p2 = float(np.percentile(contrasted_arr, 2.0))
    p98 = float(np.percentile(contrasted_arr, 98.0))
    if p98 > p2 + 1.0:
        normalized_arr = np.clip((contrasted_arr - p2) / (p98 - p2) * 255.0, 0.0, 255.0)
    else:
        normalized_arr = contrasted_arr

    # Absolute final floor — no pixel may be a true black hole
    normalized_arr = np.maximum(normalized_arr, 8.0)

    return Image.fromarray(
        _reshape_image_channel(normalized_arr, grayscale.size, label="Specular").astype(np.uint8),
        mode="L",
    )


def generate_ambient_occlusion(source: Image.Image, strength: float = 1.2) -> Image.Image:
    """Generate an ambient occlusion (AO) map from a diffuse texture.

    Approximates self-shadowing by detecting local cavity regions — areas where
    surrounding surface curvature would block ambient light.  The result is a
    greyscale ``L``-mode image where dark pixels indicate recessed/shadowed areas
    and bright pixels indicate exposed/open areas.

    The AO signal is intentionally subtle: crushed blacks and harsh cavity rings
    are avoided because Skyrim's lighting engine is not path-traced and an
    over-aggressive AO bake will make surfaces look dirty rather than shaded.

    Parameters
    ----------
    source:
        Input diffuse texture (any size/mode; converted to RGB internally).
    strength:
        Intensity multiplier (0.1–4.0).  Values around 1.0–1.5 give natural
        results for most materials.  Values above 2.5 suit carved stone or
        heavily worn metal.
    """
    rgb_source = source.convert("RGB")
    grayscale = ImageOps.grayscale(rgb_source)
    resolution_scale = _clamp(math.sqrt((source.width * source.height) / (1024.0 * 1024.0)), 1.0, 2.6)
    clamped_strength = _clamp(float(strength), 0.1, 4.0)

    # Broad ambient light field — represents the large-scale illumination envelope.
    broad = grayscale.filter(ImageFilter.GaussianBlur(radius=(4.5 + (clamped_strength * 0.8)) * resolution_scale))

    # Medium-scale cavity signal: surface dips that are too small to affect the
    # broad field but deep enough to trap ambient light.
    medium = grayscale.filter(ImageFilter.GaussianBlur(radius=(1.8 + (clamped_strength * 0.4)) * resolution_scale))
    cavity_medium = ImageChops.subtract(broad, medium, scale=1.0, offset=128)
    cavity_medium = ImageEnhance.Contrast(cavity_medium).enhance(0.9 + (clamped_strength * 0.35))

    # Fine-scale cavity: micro-pores, cracks, grout lines, fabric weave.
    fine = grayscale.filter(ImageFilter.GaussianBlur(radius=0.9 * resolution_scale))
    cavity_fine = ImageChops.subtract(medium, fine, scale=1.0, offset=128)
    cavity_fine = ImageEnhance.Contrast(cavity_fine).enhance(0.6 + (clamped_strength * 0.2))

    # Blend cavity scales: medium carries more weight than fine micro-detail.
    ao_raw = Image.blend(cavity_medium, cavity_fine, alpha=0.32)
    ao_blurred = ao_raw.filter(ImageFilter.GaussianBlur(radius=0.8 * resolution_scale))

    # Autocontrast stretches the signal to the full 0–255 range.
    ao_contrasted = ImageOps.autocontrast(ao_blurred, cutoff=1)

    # Blend toward a bright neutral so AO stays subtle (not pitch-black cavities).
    ao_final = Image.blend(Image.new("L", ao_contrasted.size, color=200), ao_contrasted, alpha=_clamp(0.3 + (clamped_strength * 0.18), 0.3, 0.82))

    # Hard floor to avoid pure-black pixels that look like missing textures.
    return _lift_black_floor(ao_final, floor=28)


def generate_roughness(source: Image.Image, strength: float = 1.0, material_type: str = "general") -> Image.Image:
    """Generate a roughness map from a diffuse texture.

    Roughness is the inverse of glossiness/specularity: bright areas are rough
    (diffuse scatter), dark areas are smooth (specular reflection).  The output
    is a greyscale ``L``-mode image in the PBR roughness convention used by
    Community Shaders TruePBR and Unreal Engine / modern PBR tools.

    The algorithm inverts the specular estimate derived from the diffuse, then
    applies material-type corrections so that known-smooth materials (polished
    metal, glass, skin) come out with a lower base roughness while known-rough
    materials (stone, terrain, cloth) come out with a higher base roughness.

    Parameters
    ----------
    source:
        Input diffuse texture.
    strength:
        Contrast/intensity multiplier (0.1–4.0).  1.0 gives a neutral result;
        values above 1.5 sharpen the roughness variation.
    material_type:
        Optional Skyrim material category string (from :func:`classify_material_type`
        or :func:`analyze_material_type_from_image`).  Used to apply a material-
        specific roughness bias so that e.g. metal defaults to a lower baseline
        roughness than stone.
    """
    clamped_strength = _clamp(float(strength), 0.1, 4.0)

    # Start from the inverted specular (high specular = low roughness).
    specular = generate_specular(source, strength=max(0.9, min(2.5, clamped_strength * 0.85)))
    roughness = ImageOps.invert(specular)

    # Blend in some diffuse luminance to capture large-scale surface texture variation.
    luminance = ImageOps.grayscale(source.convert("RGB"))
    local_detail = ImageChops.difference(luminance, luminance.filter(ImageFilter.GaussianBlur(radius=2.5)))
    roughness = Image.blend(roughness, ImageOps.autocontrast(local_detail, cutoff=1), alpha=0.22)

    roughness = ImageEnhance.Contrast(roughness).enhance(0.8 + (clamped_strength * 0.3))
    roughness = ImageOps.autocontrast(roughness, cutoff=1)

    # Material-type roughness bias: shift overall brightness up (rougher) or down (smoother).
    _roughness_bias: dict[str, int] = {
        "metal": -30,       # Polished metal is smooth
        "glass": -45,       # Glass is very smooth
        "snow": -15,        # Ice/packed snow is somewhat smooth
        "skin": -12,        # Skin has moderate smoothness
        "stone": 15,        # Stone is rough
        "terrain": 20,      # Terrain is rough
        "dirt": 22,         # Dirt/mud is rough
        "sand": 18,         # Sand/dirt is rough
        "wood": 10,         # Wood is moderately rough
        "leather": 6,       # Leather is mid-roughness (less rough than cloth)
        "fur": 26,          # Fur is very rough / diffuse
        "cloth": 22,        # Cloth is rough
        "plants": 18,       # Plants/leaves are rough
        "architecture": 12, # Architecture surfaces are moderately rough
        "paper": 8,         # Parchment/paper is moderately rough
    }
    bias = _roughness_bias.get(str(material_type or "general").lower(), 0)
    if bias != 0:
        roughness = roughness.point(lambda v: int(_clamp(float(v) + bias, 0.0, 255.0)))

    return _lift_black_floor(roughness, floor=12)


def generate_wetness_mask(source: Image.Image, strength: float = 1.0) -> Image.Image:
    """Generate a surface wetness/puddle accumulation mask from a diffuse texture.

    Returns a greyscale ``L``-mode image where bright pixels indicate surfaces
    that collect and hold moisture (concave recesses, crevices, ground-level
    areas), and dark pixels indicate exposed, dry, or elevated surfaces.

    The mask is intended for Community Shaders wet-surface effects and similar
    environment-driven wetness overlays.  It is stored as ``_wt.dds``.

    Parameters
    ----------
    source:
        Input diffuse texture (any size/mode; converted to RGB internally).
    strength:
        Intensity multiplier (0.1–3.0).  Values around 1.0 give a natural
        moisture distribution.  Higher values exaggerate the wet/dry contrast.
    """
    rgb_source = source.convert("RGB")
    grayscale = ImageOps.grayscale(rgb_source)
    clamped_strength = _clamp(float(strength), 0.1, 3.0)
    resolution_scale = _clamp(math.sqrt((source.width * source.height) / (1024.0 * 1024.0)), 1.0, 2.4)

    # Broad-blur captures macro surface shape: concave low-points are naturally
    # darker than their surroundings in the diffuse, so inverting the luminance
    # gives a first approximation of where water pools.
    dark_bias = ImageOps.invert(grayscale)
    dark_bias = _lift_black_floor(ImageOps.autocontrast(dark_bias, cutoff=1), floor=6)

    # Concavity signal: blurred luminance minus sharp luminance is positive
    # where the local region is recessed relative to its neighbourhood.
    blur_radius = (3.5 + (clamped_strength * 0.6)) * resolution_scale
    blurred = grayscale.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    concavity = ImageChops.subtract(blurred, grayscale, scale=1.0, offset=64)
    concavity = ImageEnhance.Contrast(concavity).enhance(0.9 + (clamped_strength * 0.3))
    concavity = _lift_black_floor(ImageOps.autocontrast(concavity, cutoff=1), floor=6)

    # Blend dark-bias (macro wet areas) with concavity (local pockets).
    blend_alpha = _clamp(0.48 + (clamped_strength * 0.08), 0.48, 0.74)
    wetness = Image.blend(dark_bias, concavity, alpha=blend_alpha)
    wetness = ImageEnhance.Contrast(wetness).enhance(0.78 + (clamped_strength * 0.28))
    wetness = _lift_black_floor(ImageOps.autocontrast(wetness, cutoff=1), floor=8)
    return wetness.convert("L")


def generate_snow_mask(source: Image.Image, strength: float = 1.0) -> Image.Image:
    """Generate a dynamic snow/frost coverage mask from a diffuse texture.

    Returns a greyscale ``L``-mode image where bright pixels indicate surfaces
    that accumulate snow or frost (flat, upward-facing, or exposed high-points),
    and dark pixels indicate sheltered, vertical, or recessed surfaces.

    The mask is intended for Community Shaders Dynamic Snow and similar
    weather/season shader packs.  It is stored as ``_sm.dds``.

    Parameters
    ----------
    source:
        Input diffuse texture (any size/mode; converted to RGB internally).
    strength:
        Intensity multiplier (0.1–3.0).  Values around 1.0 produce a
        natural snow distribution.  Higher values exaggerate snow coverage
        on bright surfaces.
    """
    rgb_source = source.convert("RGB")
    grayscale = ImageOps.grayscale(rgb_source)
    clamped_strength = _clamp(float(strength), 0.1, 3.0)
    resolution_scale = _clamp(math.sqrt((source.width * source.height) / (1024.0 * 1024.0)), 1.0, 2.4)

    # Snow accumulates on bright, open surfaces — use luminance as a first
    # approximation of upward-facing or horizontally-exposed areas.
    brightness = ImageOps.autocontrast(grayscale, cutoff=1)

    # Smooth out micro-noise: snow covers macro-form shapes, not individual
    # pixel-level bumps.  A moderate blur preserves large exposed surfaces
    # while suppressing speckling that would create unrealistic snow freckles.
    blur_radius = (2.8 + (clamped_strength * 0.4)) * resolution_scale
    blurred = brightness.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    # Slight blend back to the original sharpened version so small-scale
    # exposed highlights are still represented.
    smooth_brightness = Image.blend(blurred, brightness, alpha=0.35)

    snow = ImageEnhance.Contrast(smooth_brightness).enhance(0.72 + (clamped_strength * 0.26))
    snow = _lift_black_floor(ImageOps.autocontrast(snow, cutoff=1), floor=6)
    return snow.convert("L")


def generate_msn(
    source: Image.Image,
    normal_strength: float = 2.0,
    specular_strength: float = 1.15,
    emboss_mode: bool = False,
    relief_mode: bool = False,
) -> Image.Image:
    normal_rgb = generate_normal(source, strength=normal_strength, emboss_mode=emboss_mode, relief_mode=relief_mode)
    specular_alpha = generate_specular(source, strength=specular_strength)
    r, g, b = normal_rgb.split()
    return Image.merge("RGBA", (r, g, b, specular_alpha))


def prepare_preview_source(source: Image.Image, max_dimension: int = PREVIEW_MAX_DIMENSION) -> Image.Image:
    preview_source = source.convert("RGB")
    if max(preview_source.width, preview_source.height) <= max_dimension:
        return preview_source
    resized = preview_source.copy()
    resized.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    return resized


def generate_preview_outputs(
    source: Image.Image,
    *,
    normal_strength: float,
    parallax_strength: float,
    glow_threshold: int,
    environment_mask_strength: float,
    complex_strength: float,
    specular_strength: float,
    complex_format: str,
    env_mask_mode: str = "standard",
    emboss_mode: bool = False,
    relief_mode: bool = False,
    parallax_mode: str = "standard",
    include_diffuse: bool,
    include_normal: bool,
    include_parallax: bool,
    include_glow: bool,
    include_environment_mask: bool,
    include_complex: bool,
    render_profile: str = "auto",
    include_rmaos: bool = False,
    rmaos_strength: float = 1.2,
    include_wetness_mask: bool = False,
    wetness_mask_strength: float = 1.0,
    include_snow_mask: bool = False,
    snow_mask_strength: float = 1.0,
) -> dict[str, Image.Image]:
    if parallax_mode not in {"standard", "occlusion"}:
        raise ValueError("parallax_mode must be 'standard' or 'occlusion'.")
    resolved_env_complex_workflow = resolve_env_mask_complex_workflow(
        env_mask_mode=env_mask_mode,
        complex_format=complex_format,
        render_profile=render_profile,
        include_complex=include_complex,
    )
    outputs: dict[str, Image.Image] = {}
    if include_diffuse:
        outputs["diffuse"] = enforce_skyrim_output_profile("diffuse", generate_diffuse(source))
    if include_normal:
        outputs["normal"] = enforce_skyrim_output_profile(
            "normal", generate_normal(source, strength=normal_strength, emboss_mode=emboss_mode, relief_mode=relief_mode)
        )
    if include_parallax:
        if parallax_mode == "occlusion":
            outputs["parallax"] = enforce_skyrim_output_profile(
                "parallax", generate_parallax_occlusion(source, strength=parallax_strength, relief_mode=relief_mode)
            )
        else:
            outputs["parallax"] = enforce_skyrim_output_profile(
                "parallax", generate_parallax(source, strength=parallax_strength, relief_mode=relief_mode)
            )
    if include_glow:
        outputs["glow"] = enforce_skyrim_output_profile("glow", generate_glow(source, threshold=glow_threshold))
    if include_environment_mask:
        outputs["environment_mask"] = enforce_skyrim_output_profile(
            "environment_mask",
            generate_environment_mask_for_workflow(
                source,
                strength=environment_mask_strength,
                mode=env_mask_mode,
                complex_workflow=resolved_env_complex_workflow,
            ),
            env_mask_mode=env_mask_mode,
        )
    if include_rmaos:
        outputs["rmaos"] = enforce_skyrim_output_profile(
            "environment_mask",
            generate_environment_mask_for_workflow(
                source,
                strength=rmaos_strength,
                mode="complex",
                complex_workflow="truepbr",
            ),
            env_mask_mode="complex",
        )
    if include_wetness_mask:
        outputs["wetness_mask"] = enforce_skyrim_output_profile(
            "parallax", generate_wetness_mask(source, strength=wetness_mask_strength)
        )
    if include_snow_mask:
        outputs["snow_mask"] = enforce_skyrim_output_profile(
            "parallax", generate_snow_mask(source, strength=snow_mask_strength)
        )
    if include_complex:
        outputs["complex_material"] = enforce_skyrim_output_profile(
            "complex_material",
            (
                generate_msn(
                    source,
                    normal_strength=normal_strength,
                    specular_strength=specular_strength,
                    emboss_mode=emboss_mode,
                    relief_mode=relief_mode,
                )
                if complex_format == "msn"
                else generate_complex_material(source, strength=complex_strength)
            ),
            complex_format=complex_format,
        )
    return outputs


def build_complex_preview_image(image: Image.Image, *, complex_format: str = "msn") -> Image.Image:
    """Build a human-readable preview for complex outputs without changing saved files.

    For ``msn`` format, RGB is the normal map and alpha is specular. Most image viewers
    hide alpha, so MSN can appear identical to the normal map. This helper creates a
    side-by-side preview with labels: normal RGB (left) and specular alpha visualisation
    (right).
    """
    if complex_format.strip().lower() != "msn":
        return image

    msn = image.convert("RGBA")
    red, green, blue, alpha = msn.split()
    normal_rgb = Image.merge("RGB", (red, green, blue))
    alpha_preview = ImageOps.colorize(alpha, black="#050505", white="#f2f2f2")
    separator = Image.new("RGB", (2, msn.height), color=(68, 114, 196))

    preview = Image.new("RGB", (msn.width * 2 + separator.width, msn.height))
    preview.paste(normal_rgb, (0, 0))
    preview.paste(separator, (msn.width, 0))
    preview.paste(alpha_preview, (msn.width + separator.width, 0))
    if msn.height >= 14:
        draw = ImageDraw.Draw(preview)
        label_top = 1
        label_bottom = min(msn.height - 1, 13)
        draw.rectangle((0, label_top, msn.width - 1, label_bottom), fill=(16, 16, 20))
        draw.rectangle((msn.width + separator.width, label_top, preview.width - 1, label_bottom), fill=(16, 16, 20))
        draw.text((3, label_top + 1), "RGB normal", fill=(235, 235, 245))
        draw.text((msn.width + separator.width + 3, label_top + 1), "A specular", fill=(235, 235, 245))
    return preview


def enforce_skyrim_output_profile(
    output_type: str,
    image: Image.Image,
    *,
    env_mask_mode: str = "standard",
    complex_format: str = "msn",
) -> Image.Image:
    """Normalize generated output to a Skyrim-safe channel/mode profile."""
    if output_type == "diffuse":
        return image.convert("RGB")
    if output_type == "normal":
        normal_rgb = image.convert("RGB")
        red, green, blue = normal_rgb.split()
        blue_floor = blue.point(lambda value: int(_clamp(float(value), 128.0, 255.0)))
        return Image.merge("RGB", (red, green, blue_floor))
    if output_type in {"parallax", "glow"}:
        return ImageOps.grayscale(image)
    if output_type == "environment_mask":
        if env_mask_mode == "standard":
            return ImageOps.grayscale(image)
        return image.convert("RGBA")
    if output_type == "complex_material":
        if complex_format not in {"msn", "cm"}:
            raise ValueError("complex_format must be 'msn' or 'cm'.")
        return image.convert("RGBA")
    return image


def build_output_paths(
    input_path: Path,
    output_dir: Path | None,
    diffuse_name: str | None = None,
    parallax_name: str | None = None,
) -> tuple[Path, Path]:
    base_output_dir = _resolve_output_base_dir(input_path, output_dir)
    base_output_dir.mkdir(parents=True, exist_ok=True)
    ext = DDS_EXTENSION

    diffuse_stem = diffuse_name or input_path.stem
    parallax_stem = parallax_name or f"{input_path.stem}_p"
    return base_output_dir / f"{diffuse_stem}{ext}", base_output_dir / f"{parallax_stem}{ext}"


def build_normal_output_path(
    input_path: Path,
    output_dir: Path | None,
    normal_name: str | None = None,
) -> Path:
    base_output_dir = _resolve_output_base_dir(input_path, output_dir)
    base_output_dir.mkdir(parents=True, exist_ok=True)
    ext = DDS_EXTENSION
    normal_stem = normal_name or f"{input_path.stem}_n"
    return base_output_dir / f"{normal_stem}{ext}"


def build_glow_output_path(
    input_path: Path,
    output_dir: Path | None,
    glow_name: str | None = None,
) -> Path:
    base_output_dir = _resolve_output_base_dir(input_path, output_dir)
    base_output_dir.mkdir(parents=True, exist_ok=True)
    ext = DDS_EXTENSION
    glow_stem = glow_name or f"{input_path.stem}_em"
    return base_output_dir / f"{glow_stem}{ext}"


def build_environment_mask_output_path(
    input_path: Path,
    output_dir: Path | None,
    environment_mask_name: str | None = None,
    env_mask_mode: str = "standard",
    complex_format: str = "msn",
    render_profile: str = "auto",
    include_complex: bool | None = None,
) -> Path:
    base_output_dir = _resolve_output_base_dir(input_path, output_dir)
    base_output_dir.mkdir(parents=True, exist_ok=True)
    ext = DDS_EXTENSION
    default_suffix = "_m"
    mask_stem = environment_mask_name or f"{input_path.stem}{default_suffix}"
    return base_output_dir / f"{mask_stem}{ext}"


def build_rmaos_output_path(
    input_path: Path,
    output_dir: Path | None,
    rmaos_name: str | None = None,
) -> Path:
    base_output_dir = _resolve_output_base_dir(input_path, output_dir)
    base_output_dir.mkdir(parents=True, exist_ok=True)
    ext = DDS_EXTENSION
    rmaos_stem = rmaos_name or f"{input_path.stem}_rmaos"
    return base_output_dir / f"{rmaos_stem}{ext}"


def build_wetness_mask_output_path(
    input_path: Path,
    output_dir: Path | None,
    wetness_name: str | None = None,
) -> Path:
    """Return the output path for a wetness/puddle accumulation mask (``_wt.dds``)."""
    base_output_dir = _resolve_output_base_dir(input_path, output_dir)
    base_output_dir.mkdir(parents=True, exist_ok=True)
    wetness_stem = wetness_name or f"{input_path.stem}_wt"
    return base_output_dir / f"{wetness_stem}{DDS_EXTENSION}"


def build_snow_mask_output_path(
    input_path: Path,
    output_dir: Path | None,
    snow_name: str | None = None,
) -> Path:
    """Return the output path for a dynamic snow/frost coverage mask (``_sm.dds``)."""
    base_output_dir = _resolve_output_base_dir(input_path, output_dir)
    base_output_dir.mkdir(parents=True, exist_ok=True)
    snow_stem = snow_name or f"{input_path.stem}_sm"
    return base_output_dir / f"{snow_stem}{DDS_EXTENSION}"


def _find_textures_root(path: Path) -> Path | None:
    for candidate in (path.parent, *path.parents):
        if candidate.name.lower() == "textures":
            return candidate
    return None


def _build_truepbr_texture_identifier(texture_path: Path, textures_root: Path | None) -> str:
    normalized_stem = _normalize_texture_family_stem(texture_path)
    if textures_root is None:
        return normalized_stem
    try:
        relative_no_ext = texture_path.relative_to(textures_root).with_suffix("")
    except ValueError:
        return normalized_stem
    relative_parts = list(relative_no_ext.parts)
    if not relative_parts:
        return normalized_stem
    relative_parts[-1] = normalized_stem
    return Path(*relative_parts).as_posix()


def write_rmaos_json_sidecar(
    texture_path: Path,
    *,
    parallax_enabled: bool = True,
    material_type: str = "general",
) -> Path:
    """Write a TruePBR sidecar JSON for PBRNifPatcher.

    The payload is adjusted based on ``material_type`` so that material-specific
    PBR properties (subsurface scattering, specular level, roughness scale, and
    displacement depth) are set to Skyrim-appropriate defaults for each category.

    Parameters
    ----------
    texture_path:
        Path to the generated ``_rmaos.dds`` file.
    parallax_enabled:
        Whether to enable parallax displacement in the sidecar.
    material_type:
        Skyrim material category string from :func:`classify_material_type`.
        Adjusts subsurface, specular_level, roughness_scale, and displacement_scale
        defaults in the output JSON.
    """
    textures_root = _find_textures_root(texture_path)
    if textures_root is None:
        json_dir = texture_path.parent / "PBRNifPatcher"
    else:
        relative_parent = texture_path.parent.relative_to(textures_root)
        json_dir = textures_root.parent / "PBRNifPatcher" / relative_parent
    texture_identifier = _build_truepbr_texture_identifier(texture_path, textures_root)

    # Material-type-specific PBR defaults.
    # These follow the TruePBR/PBRNifPatcher schema and Skyrim material behaviour:
    # - subsurface: True only for skin (translucent flesh).
    # - subsurface_foliage: True only for plants/foliage (two-sided leaf scattering).
    # - specular_level: mirrors real-world IOR — dielectrics ~0.02-0.06, metals/glass ~0.5.
    # - roughness_scale: glass is very smooth (<0.5), stone/terrain are rough (>=1.0).
    # - displacement_scale: terrain uses shallow depth to avoid horizon shimmer; skin disables it.
    # - smooth_angle: softens silhouette normals; lower for hard surfaces, higher for organic.
    _RMAOS_JSON_MATERIAL_OVERRIDES: dict[str, dict[str, object]] = {
        "skin": {
            "subsurface": True,
            "subsurface_foliage": False,
            "subsurface_color": [1.0, 0.85, 0.7],
            "subsurface_opacity": 0.65,
            "specular_level": 0.028,
            "roughness_scale": 1.0,
            "smooth_angle": 80,
            "displacement_scale": 0.0,   # parallax on animated skin causes artefacts
        },
        "plants": {
            "subsurface": False,
            "subsurface_foliage": True,
            "subsurface_color": [0.7, 1.0, 0.4],
            "subsurface_opacity": 0.5,
            "specular_level": 0.016,
            "roughness_scale": 1.1,
            "smooth_angle": 70,
            "displacement_scale": 0.0,   # foliage uses flat alpha cards
        },
        "metal": {
            "subsurface": False,
            "subsurface_foliage": False,
            "subsurface_color": [1.0, 1.0, 1.0],
            "subsurface_opacity": 1.0,
            "specular_level": 0.5,       # metals have high Fresnel/specular
            "roughness_scale": 0.9,
            "smooth_angle": 60,
            "displacement_scale": 0.15,
        },
        "glass": {
            "subsurface": False,
            "subsurface_foliage": False,
            "subsurface_color": [1.0, 1.0, 1.0],
            "subsurface_opacity": 1.0,
            "specular_level": 0.5,       # glass has high dielectric IOR
            "roughness_scale": 0.35,     # glass is very smooth
            "smooth_angle": 55,
            "displacement_scale": 0.05,
        },
        "snow": {
            "subsurface": False,
            "subsurface_foliage": False,
            "subsurface_color": [1.0, 1.0, 1.0],
            "subsurface_opacity": 1.0,
            "specular_level": 0.06,
            "roughness_scale": 0.75,     # packed snow/ice has moderate smoothness
            "smooth_angle": 75,
            "displacement_scale": 0.18,
        },
        "terrain": {
            "subsurface": False,
            "subsurface_foliage": False,
            "subsurface_color": [1.0, 1.0, 1.0],
            "subsurface_opacity": 1.0,
            "specular_level": 0.04,
            "roughness_scale": 1.1,
            "smooth_angle": 60,
            "displacement_scale": 0.08,  # shallow depth avoids horizon shimmer
        },
        "cloth": {
            "subsurface": False,
            "subsurface_foliage": False,
            "subsurface_color": [1.0, 1.0, 1.0],
            "subsurface_opacity": 1.0,
            "specular_level": 0.016,     # cloth is nearly non-specular
            "roughness_scale": 1.15,
            "smooth_angle": 75,
            "displacement_scale": 0.12,
        },
        "leather": {
            "subsurface": False,
            "subsurface_foliage": False,
            "subsurface_color": [1.0, 1.0, 1.0],
            "subsurface_opacity": 1.0,
            "specular_level": 0.025,
            "roughness_scale": 1.05,
            "smooth_angle": 72,
            "displacement_scale": 0.14,
        },
        "stone": {
            "subsurface": False,
            "subsurface_foliage": False,
            "subsurface_color": [1.0, 1.0, 1.0],
            "subsurface_opacity": 1.0,
            "specular_level": 0.04,
            "roughness_scale": 1.1,
            "smooth_angle": 65,
            "displacement_scale": 0.22,
        },
        "wood": {
            "subsurface": False,
            "subsurface_foliage": False,
            "subsurface_color": [1.0, 1.0, 1.0],
            "subsurface_opacity": 1.0,
            "specular_level": 0.03,
            "roughness_scale": 1.05,
            "smooth_angle": 68,
            "displacement_scale": 0.18,
        },
    }

    overrides = _RMAOS_JSON_MATERIAL_OVERRIDES.get(str(material_type).lower(), {})

    payload = [
        {
            "texture": texture_identifier,
            "emissive": False,
            "parallax": bool(parallax_enabled),
            "subsurface": bool(overrides.get("subsurface", False)),
            "subsurface_foliage": bool(overrides.get("subsurface_foliage", False)),
            "subsurface_color": list(overrides.get("subsurface_color", [1.0, 1.0, 1.0])),
            "subsurface_opacity": float(overrides.get("subsurface_opacity", 1.0)),
            "specular_level": float(overrides.get("specular_level", 0.04)),
            "roughness_scale": float(overrides.get("roughness_scale", 1.0)),
            "smooth_angle": int(overrides.get("smooth_angle", 75)),
            "displacement_scale": float(overrides.get("displacement_scale", 0.2)),
            "emissive_scale": 0.25,
            "vertex_color_lum_mult": 0.5,
        }
    ]
    json_path = json_dir / f"{texture_path.stem}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return json_path


def build_complex_output_path(
    input_path: Path,
    output_dir: Path | None,
    complex_name: str | None = None,
    complex_format: str = "msn",
) -> Path:
    base_output_dir = _resolve_output_base_dir(input_path, output_dir)
    base_output_dir.mkdir(parents=True, exist_ok=True)
    ext = DDS_EXTENSION
    if complex_format not in {"msn", "cm"}:
        raise ValueError("complex_format must be 'msn' or 'cm'.")
    suffix = "_msn" if complex_format == "msn" else "_cm"
    complex_stem = complex_name or f"{input_path.stem}{suffix}"
    return base_output_dir / f"{complex_stem}{ext}"


def _collect_planned_output_paths(
    *,
    input_file: Path,
    output_dir: Path | None,
    diffuse_name: str | None,
    normal_name: str | None,
    parallax_name: str | None,
    glow_name: str | None,
    environment_mask_name: str | None,
    rmaos_name: str | None,
    complex_name: str | None,
    complex_format: str,
    env_mask_mode: str,
    render_profile: str,
    include_diffuse: bool,
    include_normal: bool,
    include_parallax: bool,
    include_glow: bool,
    include_environment_mask: bool,
    include_rmaos: bool,
    include_complex: bool,
    wetness_name: str | None = None,
    snow_name: str | None = None,
    include_wetness_mask: bool = False,
    include_snow_mask: bool = False,
) -> dict[str, Path]:
    planned: dict[str, Path] = {}
    if include_diffuse or include_parallax:
        diffuse_path, parallax_path = build_output_paths(
            input_path=input_file,
            output_dir=output_dir,
            diffuse_name=diffuse_name,
            parallax_name=parallax_name,
        )
        if include_diffuse:
            planned["diffuse"] = diffuse_path
        if include_parallax:
            planned["parallax"] = parallax_path
    if include_normal:
        planned["normal"] = build_normal_output_path(
            input_path=input_file,
            output_dir=output_dir,
            normal_name=normal_name,
        )
    if include_glow:
        planned["glow"] = build_glow_output_path(
            input_path=input_file,
            output_dir=output_dir,
            glow_name=glow_name,
        )
    if include_environment_mask:
        planned["environment_mask"] = build_environment_mask_output_path(
            input_path=input_file,
            output_dir=output_dir,
            environment_mask_name=environment_mask_name,
            env_mask_mode=env_mask_mode,
            complex_format=complex_format,
            render_profile=render_profile,
            include_complex=include_complex,
        )
    if include_rmaos:
        planned["rmaos"] = build_rmaos_output_path(
            input_path=input_file,
            output_dir=output_dir,
            rmaos_name=rmaos_name,
        )
    if include_wetness_mask:
        planned["wetness_mask"] = build_wetness_mask_output_path(
            input_path=input_file,
            output_dir=output_dir,
            wetness_name=wetness_name,
        )
    if include_snow_mask:
        planned["snow_mask"] = build_snow_mask_output_path(
            input_path=input_file,
            output_dir=output_dir,
            snow_name=snow_name,
        )
    if include_complex:
        planned["complex_material"] = build_complex_output_path(
            input_path=input_file,
            output_dir=output_dir,
            complex_name=complex_name,
            complex_format=complex_format,
        )
    return planned


def _validate_output_path_conflicts(planned_paths: Mapping[str, Path]) -> None:
    by_target: dict[str, list[str]] = defaultdict(list)
    original_paths: dict[str, Path] = {}
    for output_key, output_path in planned_paths.items():
        normalized_key = str(output_path.resolve(strict=False)).casefold()
        by_target[normalized_key].append(output_key)
        original_paths.setdefault(normalized_key, output_path)

    conflicts = [
        (original_paths[path_key], sorted(output_keys))
        for path_key, output_keys in by_target.items()
        if len(output_keys) > 1
    ]
    if not conflicts:
        return

    details = "; ".join(
        f"{path} ({', '.join(output_keys)})"
        for path, output_keys in conflicts
    )
    raise ValueError(
        "Conflicting output filenames detected. Multiple outputs resolve to the same file path: "
        f"{details}. Use unique output names for each enabled map type."
    )


def _normalize_texture_family_stem(path_like: Path | str) -> str:
    normalized = str(path_like).replace("\\", "/").strip()
    stem = Path(normalized).stem.lower()
    role_info = identify_skyrim_texture_role(Path(normalized))
    detected_suffix = str(role_info.get("suffix", "")).strip().lower()
    if detected_suffix and stem.endswith(detected_suffix):
        return stem[: -len(detected_suffix)]
    for suffix in (
        "_diffuse",
        "_albedo",
        "_diff",
        "_d",
        "_normal",
        "_n",
        "_parallax",
        "_p",
        "_glow",
        "_g",
        "_mask",
        "_m",
        "_rmaos",
        "_ramos",
        "_msn",
        "_c",
        "_cm",
    ):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _relative_texture_subpath(path: Path) -> Path | None:
    parts = list(path.parts)
    lowered = [part.lower() for part in parts]
    if "textures" not in lowered:
        return None
    textures_index = lowered.index("textures")
    relative_parts = parts[textures_index + 1 : -1]
    return Path(*relative_parts) if relative_parts else Path()


def _resolve_output_base_dir(input_path: Path, output_dir: Path | None) -> Path:
    if output_dir is None:
        return input_path.parent
    relative_texture_subpath = _relative_texture_subpath(input_path)
    if relative_texture_subpath is None:
        return output_dir
    if output_dir.name.lower() == "textures":
        return output_dir / relative_texture_subpath
    return output_dir / "textures" / relative_texture_subpath


def _as_skyrim_resource_path(path: Path) -> str:
    parts = list(path.parts)
    lowered = [part.lower() for part in parts]
    if "textures" in lowered:
        start = lowered.index("textures")
        return "\\".join(parts[start:])
    return path.name.replace("/", "\\")


def _resolve_generated_skyrim_resource_path(generated_path: Path, *, source_texture: Path | None = None) -> str:
    direct_resource_path = _as_skyrim_resource_path(generated_path)
    if direct_resource_path.lower().startswith("textures\\"):
        return direct_resource_path
    if source_texture is not None:
        relative_texture_subpath = _relative_texture_subpath(source_texture)
        if relative_texture_subpath is not None:
            relative_parts = relative_texture_subpath.parts
            if relative_parts:
                return "\\".join(("textures", *relative_parts, generated_path.name))
            return "\\".join(("textures", generated_path.name))
    return direct_resource_path


def _coerce_generated_resource_path_to_dds(resource_path: str) -> str:
    normalized = resource_path.replace("/", "\\").strip()
    if not normalized:
        return normalized
    stem, suffix = os.path.splitext(normalized)
    if suffix.lower() != DDS_EXTENSION:
        return f"{stem}{DDS_EXTENSION}"
    return normalized


def _resolve_generated_dds_resource_path(generated_path: Path, *, source_texture: Path | None = None) -> str:
    resource_path = _resolve_generated_skyrim_resource_path(generated_path, source_texture=source_texture)
    return _coerce_generated_resource_path_to_dds(resource_path)


def _candidate_nif_search_roots(
    source_texture: Path,
    *,
    output_dir: Path | None = None,
    manager_context: ModManagerContext | None = None,
) -> tuple[Path, ...]:
    candidates: list[Path] = []
    if manager_context is not None:
        candidates.extend(manager_context.loaded_mesh_dirs)
    for base in filter(None, [output_dir, source_texture.parent]):
        current = base
        while True:
            if current.name.lower() == "textures":
                candidates.append(current.parent / "meshes")
                break
            if current.parent == current:
                break
            current = current.parent
    return _unique_existing_paths(candidates)


def find_related_nif_files_for_texture(
    source_texture: Path,
    *,
    output_dir: Path | None = None,
    manager_context: ModManagerContext | None = None,
    candidate_roots: tuple[Path, ...] | None = None,
    nif_info_provider: Callable[[Path], list[object]] | None = None,
) -> tuple[Path, ...]:
    if not NIF_PATCHER_AVAILABLE:
        return ()
    normalized_source_stem = _normalize_texture_family_stem(source_texture)
    roots = candidate_roots or _candidate_nif_search_roots(
        source_texture,
        output_dir=output_dir,
        manager_context=manager_context,
    )
    provider = nif_info_provider or scan_nif
    matches: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        for nif_path in find_nif_files(root):
            matched = False
            try:
                infos = provider(nif_path)
            except Exception:
                infos = []
            for info in infos:
                texture_paths = getattr(info, "texture_paths", {})
                for texture_path in texture_paths.values():
                    if _normalize_texture_family_stem(texture_path) == normalized_source_stem:
                        resolved = nif_path.resolve()
                        key = os.path.normcase(str(resolved))
                        if key not in seen:
                            seen.add(key)
                            matches.append(resolved)
                        matched = True
                        break
                else:
                    continue
                break
            if matched:
                continue
            if _normalize_texture_family_stem(nif_path) == normalized_source_stem:
                resolved = nif_path.resolve()
                key = os.path.normcase(str(resolved))
                if key not in seen:
                    seen.add(key)
                    matches.append(resolved)
    return tuple(matches)


def build_nif_patch_options_for_generated_outputs(
    source_texture: Path | None,
    outputs: Mapping[str, Path],
    *,
    complex_format: str,
    env_mask_mode: str,
    parallax_mode: str,
    parallax_scale: float | None,
    render_profile: str = "auto",
    include_parallax: bool | None = None,
    include_environment_mask: bool | None = None,
    include_glow: bool | None = None,
) -> NifPatchOptions:
    normalized_complex_format = _normalize_complex_format(complex_format)
    normalized_env_mask_mode = _normalize_env_mask_mode(env_mask_mode)
    normalized_parallax_mode = (
        "occlusion" if str(parallax_mode or "standard").strip().lower() == "occlusion" else "standard"
    )
    complex_output = outputs.get("complex_material")
    normal_output = outputs.get("normal")
    parallax_output = outputs.get("parallax")
    env_mask_output = outputs.get("environment_mask")
    rmaos_output = outputs.get("rmaos")
    glow_output = outputs.get("glow")
    normal_path = complex_output if complex_output is not None and normalized_complex_format == "msn" else normal_output
    parallax_disabled_by_options = include_parallax is False
    env_mask_disabled_by_options = include_environment_mask is False
    glow_disabled_by_options = include_glow is False
    enable_env_mapping = env_mask_output is not None
    if rmaos_output is not None:
        enable_env_mapping = True
    if normalized_env_mask_mode == "complex" and env_mask_output is None and not env_mask_disabled_by_options:
        enable_env_mapping = complex_output is not None and normalized_complex_format == "msn"
    enable_glow_map = glow_output is not None and not glow_disabled_by_options
    # Consult the render-profile NIF-patch defaults to decide whether
    # upgrading shader blocks to type-3 (HEIGHTMAP / parallax-scale) is
    # appropriate.  Vanilla Skyrim SE supports parallax via the flag +
    # texture slot 3 alone (no type-3 block upgrade required), while
    # Community Shaders and ENB workflows benefit from the explicit scale
    # field that type-3 provides.  Unconditionally upgrading to type-3
    # regardless of the selected renderer was the primary cause of the
    # EXCEPTION_ACCESS_VIOLATION crashes: the upgrade can corrupt the
    # block layout when the NIF header num_groups field was not yet
    # consumed correctly, so limiting upgrades to renderer profiles that
    # genuinely need them reduces the blast radius.
    profile_nif_defaults = resolve_nif_patch_defaults_for_render_profile(render_profile)
    force_shader_type_3 = (
        bool(profile_nif_defaults.get("force_shader_type_3", False)) and parallax_output is not None
    )
    return NifPatchOptions(
        enable_parallax=parallax_output is not None,
        enable_pom=parallax_output is not None and normalized_parallax_mode == "occlusion",
        parallax_scale=parallax_scale if parallax_output is not None else None,
        force_shader_type_3=force_shader_type_3,
        enable_env_mapping=enable_env_mapping,
        enable_glow_map=enable_glow_map,
        parallax_texture_path=(
            _resolve_generated_dds_resource_path(parallax_output, source_texture=source_texture)
            if parallax_output is not None else None
        ),
        normal_texture_path=(
            _resolve_generated_dds_resource_path(normal_path, source_texture=source_texture)
            if normal_path is not None else None
        ),
        env_mask_texture_path=(
            _resolve_generated_dds_resource_path(
                env_mask_output if env_mask_output is not None else rmaos_output,
                source_texture=source_texture,
            )
            if env_mask_output is not None or rmaos_output is not None else None
        ),
        glow_texture_path=(
            _resolve_generated_dds_resource_path(glow_output, source_texture=source_texture)
            if glow_output is not None and not glow_disabled_by_options else None
        ),
        disable_parallax=parallax_disabled_by_options,
        clear_parallax_texture_path=parallax_disabled_by_options,
        disable_env_mapping=env_mask_disabled_by_options,
        clear_env_mask_texture_path=env_mask_disabled_by_options,
        disable_glow_map=glow_disabled_by_options,
        clear_glow_texture_path=glow_disabled_by_options,
        backup=True,
        dry_run=False,
    )


def auto_patch_related_nifs_for_texture(
    source_texture: Path,
    outputs: Mapping[str, Path],
    *,
    output_dir: Path | None = None,
    manager_context: ModManagerContext | None = None,
    complex_format: str,
    env_mask_mode: str,
    parallax_mode: str,
    parallax_scale: float | None,
    render_profile: str = "auto",
    include_parallax: bool | None = None,
    include_environment_mask: bool | None = None,
    include_glow: bool | None = None,
) -> tuple[object, ...]:
    related_nifs = find_related_nif_files_for_texture(
        source_texture,
        output_dir=output_dir,
        manager_context=manager_context,
    )
    if not related_nifs:
        return ()
    patch_options = build_nif_patch_options_for_generated_outputs(
        source_texture,
        outputs,
        complex_format=complex_format,
        env_mask_mode=env_mask_mode,
        parallax_mode=parallax_mode,
        parallax_scale=parallax_scale,
        render_profile=render_profile,
        include_parallax=include_parallax,
        include_environment_mask=include_environment_mask,
        include_glow=include_glow,
    )
    results: list[object] = []
    for nif_path in related_nifs:
        results.append(patch_nif(nif_path, patch_options))
    return tuple(results)


def _is_supported_input_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_EXTENSIONS


def _is_generated_texture(path: Path) -> bool:
    stem = path.stem.lower()
    if stem.endswith("_") or any(stem.endswith(suffix) for suffix in GENERATED_TEXTURE_SUFFIXES):
        return True
    role_info = identify_skyrim_texture_role(path)
    return bool(role_info.get("suffix"))


# ---------------------------------------------------------------------------
# Skyrim SE texture role recognition
# ---------------------------------------------------------------------------

_SKYRIM_SE_SUFFIX_INFO: dict[str, tuple[str, str, str]] = {
    "_n": (
        "normal",
        "Normal Map",
        "Tangent-space DirectX-style normal map. Texture Slot 1 in the NIF. "
        "R=tangent X, G=tangent Y (DirectX convention, +Y up), B=tangent Z. "
        "Requires BSLightingShaderProperty. Standard in all Skyrim SE textures.",
    ),
    "_p": (
        "parallax",
        "Parallax Heightmap",
        "Greyscale height map for parallax occlusion mapping. Texture Slot 3 in the NIF. "
        "Requires BSLightingShaderProperty with Parallax shader flag set plus the SKSE64 "
        "memory patch (or ENBSeries) to work at runtime. Brighter = raised surface.",
    ),
    "_em": (
        "glow",
        "Glow / Emissive Map",
        "Glow/emissive map. Texture Slot 2 in the NIF. "
        "Controls per-pixel self-illumination strength. "
        "Requires SLSF1_Own_Emit (0x40) shader flag on the BSLightingShaderProperty.",
    ),
    "_emis": (
        "glow",
        "Glow / Emissive Map (_emis alias)",
        "Alias naming for glow/emissive maps. Texture Slot 2 in the NIF. "
        "Equivalent role to _em with the same SLSF1_Own_Emit shader-flag requirement.",
    ),
    "_g": (
        "glow",
        "Glow / Emissive Map (_g legacy alias)",
        "Legacy alias naming for glow/emissive maps. Texture Slot 2 in the NIF. "
        "Equivalent role to _em with the same SLSF1_Own_Emit shader-flag requirement.",
    ),
    "_glow": (
        "glow",
        "Glow / Emissive Map (_glow alias)",
        "Alias naming for glow/emissive maps. Texture Slot 2 in the NIF. "
        "Equivalent role to _em with the same SLSF1_Own_Emit shader-flag requirement.",
    ),
    "_emit": (
        "glow",
        "Glow / Emissive Map (_emit alias)",
        "Alias naming for glow/emissive maps. Texture Slot 2 in the NIF. "
        "Equivalent role to _em with the same SLSF1_Own_Emit shader-flag requirement.",
    ),
    "_emissive": (
        "glow",
        "Glow / Emissive Map (_emissive alias)",
        "Alias naming for glow/emissive maps. Texture Slot 2 in the NIF. "
        "Equivalent role to _em with the same SLSF1_Own_Emit shader-flag requirement.",
    ),
    "_m": (
        "environment_mask",
        "Environment Mask",
        "Greyscale environment reflection intensity mask. Texture Slot 5 in the NIF. "
        "Higher (brighter) values produce more environment/specular reflection. "
        "Requires SLSF1_Environment_Mapping (0x80) shader flag. "
        "Standard vanilla Skyrim SE format; typically stored as DXT1 (no alpha).",
    ),
    "_rmaos": (
        "environment_mask",
        "Community Shaders TruePBR RMAOS Map (_rmaos)",
        "Used for Community Shaders TruePBR workflows. "
        "RGBA channel layout: R=Roughness, G=Metallic, B=Ambient Occlusion, A=Other/smoothness/height (JSON-driven). "
        "In this generator, _rmaos naming is reserved for TruePBR. ENB preset output uses _m with ENB channel packing. "
        "Do NOT reuse TruePBR _rmaos maps in ENB complex-material setups.",
    ),
    "_ramos": (
        "environment_mask",
        "Community Shaders TruePBR RAMOS Map alias (_ramos)",
        "Alias naming for TruePBR packed map output in some pipelines. "
        "RGBA channel layout matches _rmaos: R=Roughness, G=Metallic, B=Ambient Occlusion, A=Other/smoothness/height (JSON-driven). "
        "In this generator, _ramos is treated the same as _rmaos and should be paired with a JSON sidecar. "
        "Do NOT reuse TruePBR _ramos maps in ENB complex-material setups.",
    ),
    "_s": (
        "subsurface",
        "Subsurface Scattering Map",
        "Subsurface scattering tint map. Texture Slot 6 in the NIF. "
        "Used primarily for skin on character and creature textures.",
    ),
    "_sk": (
        "skin_specular",
        "Skin Specular Map",
        "Specular map for character skin. Texture Slot 7 in the NIF. "
        "Character-specific shader slot for skin specularity.",
    ),
    "_wt": (
        "wetness_mask",
        "Surface Wetness / Puddle Accumulation Mask",
        "NOT a vanilla Skyrim SE texture. Greyscale mask where bright pixels indicate concave "
        "or recessed surfaces that collect moisture (puddles, crevice water). "
        "Used by Community Shaders wet-surface effect addons. "
        "Generated by this tool from diffuse luminance and local concavity analysis.",
    ),
    "_sm": (
        "snow_mask",
        "Dynamic Snow / Frost Coverage Mask",
        "NOT a vanilla Skyrim SE texture. Greyscale mask where bright pixels indicate flat, "
        "upward-facing, or exposed surfaces that accumulate snow or frost. "
        "Used by Community Shaders Dynamic Snow and season shader packs. "
        "Generated by this tool from diffuse luminance and surface brightness analysis.",
    ),
    "_msn": (
        "complex_material",
        "Complex Parallax Material (ENBSeries only)",
        "NOT a vanilla Skyrim SE texture. Used exclusively by ENBSeries Complex Parallax "
        "Material feature. Generated by this tool as RGBA: R=normal X, G=normal Y, "
        "B=normal Z, A=specular intensity. Requires ENBSeries with complexmaterial support enabled. "
        "Replaces the standard _n.dds when this ENB feature is active.",
    ),
    "_cm": (
        "complex_material_cm",
        "Complex Material Packed — Community Shaders Extended Materials (_cm)",
        "NOT a vanilla Skyrim SE texture. Channel-packed complex material output for Community Shaders Extended Materials. "
        "Texture Slot 5 in the NIF. "
        "RGBA channel layout: R=Environment reflection amount, "
        "G=Glossiness (0=matte, 255=glossy), "
        "B=Metallic proxy (0=dielectric, 255=full metal), "
        "A=Height / mode-control alpha. "
        "Requires Community Shaders Extended Materials. "
        "Typically paired with a standard _n.dds (normal map, Slot 1) and optional _p.dds (parallax, Slot 3). "
        "_c.dds is a naming alias with the identical channel layout — use _cm for new mods unless the pack explicitly uses _c. "
        "Do not mix this format with ENB _msn/_m workflows.",
    ),
    "_c": (
        "complex_material_cm",
        "Complex Material Packed — Community Shaders Extended Materials (_c / _C naming alias)",
        "Alternative naming for the Community Shaders Extended Materials packed map (_cm). "
        "Identical RGBA channel layout to _cm: R=Environment reflection amount, "
        "G=Glossiness (0=matte, 255=glossy), "
        "B=Metallic proxy (0=dielectric, 255=full metal), "
        "A=Height / mode-control alpha. "
        "Texture Slot 5 in the NIF. Requires Community Shaders Extended Materials. "
        "_C.dds (uppercase C) is treated identically on Windows (case-insensitive filesystem) and by this tool. "
        "Prefer _cm.dds for new mods unless the target shader pack specifically expects _c naming. "
        "Do not mix this format with ENB _msn/_m workflows.",
    ),
}

_SKYRIM_SE_PATH_HINTS: dict[str, str] = {
    "actors": "Character/creature texture (textures/actors/…)",
    "architecture": "Architecture/building texture",
    "landscape": "Terrain/landscape texture",
    "effects": "Visual-effect texture",
    "weapons": "Weapon texture",
    "armor": "Armor texture",
    "clothes": "Clothing/apparel texture",
    "furniture": "Furniture/interior-prop texture",
    "plants": "Vegetation/plant texture",
    "sky": "Sky texture",
    "water": "Water texture",
    "dungeons": "Dungeon-interior texture",
    "clutter": "Clutter/misc-object texture",
    "terrain": "Terrain/world-space texture",
    "dlc": "DLC content texture",
    "interface": "UI/interface texture (not for in-world use)",
}

_SKYRIM_ROLE_PRIMARY_SUFFIX: dict[str, str] = {
    "normal": "_n",
    "parallax": "_p",
    "glow": "_em",
    "environment_mask": "_m",
    "subsurface": "_s",
    "skin_specular": "_sk",
    "wetness_mask": "_wt",
    "snow_mask": "_sm",
    "complex_material": "_msn",
    "complex_material_cm": "_cm",
}

_SKYRIM_ROLE_TOKEN_HINTS: dict[str, tuple[str, ...]] = {
    "normal": ("normal", "normalmap", "nrm", "nor", "bump"),
    "parallax": ("parallax", "height", "heightmap", "displace", "displacement"),
    "glow": ("glow", "emissive", "emit", "emission"),
    "environment_mask": ("env", "envmask", "cubemask", "reflectionmask", "specmask", "rmaos", "ramos"),
    "subsurface": ("subsurface", "sss"),
    "skin_specular": ("skinspec", "skinspecular"),
    "wetness_mask": ("wetness", "wet", "puddle", "moisture"),
    "snow_mask": ("snowmask", "snowcoverage", "frostmask"),
    "complex_material": ("complex", "complexmaterial", "msn"),
    "complex_material_cm": ("complexcm", "cmaterial", "complexgray"),
}

_SKYRIM_SE_SUFFIX_ALIASES: dict[str, str] = {
    "_parallax": "_p",
    "_height": "_p",
    "_heightmap": "_p",
    "_emission": "_em",
    "_env": "_m",
    "_envmask": "_m",
    "_cubemask": "_m",
    "_spec": "_m",
    "_specular": "_m",
    "_ao": "_m",
    "_ambientocclusion": "_m",
    "_rough": "_m",
    "_roughness": "_m",
    "_metal": "_m",
    "_metallic": "_m",
    "_metalness": "_m",
    "_orm": "_rmaos",
    "_orms": "_rmaos",
    "_mrao": "_rmaos",
    "_mra": "_rmaos",
    "_wetness": "_wt",
    "_snow": "_sm",
}


def _get_skyrim_path_hint(path: Path) -> str:
    parts = [part.lower() for part in path.parts]
    for part in parts:
        for keyword, hint in _SKYRIM_SE_PATH_HINTS.items():
            if keyword in part:
                return hint
    return ""


def _infer_skyrim_role_from_name_tokens(stem: str) -> str | None:
    lowered = stem.lower()
    tokens = tuple(token for token in re.split(r"[^a-z0-9]+", lowered) if token)
    for role, hints in _SKYRIM_ROLE_TOKEN_HINTS.items():
        for hint in hints:
            if hint in tokens:
                return role
    return None


def identify_skyrim_texture_role(path: Path) -> dict[str, str]:
    """Identify the Skyrim SE role of a texture based on its filename and folder path.

    Returns a dict with keys:
    - ``role``:        Short role identifier (e.g. ``'diffuse'``, ``'normal'``).
    - ``suffix``:      Detected suffix (e.g. ``'_n'``), or ``''`` for diffuse.
    - ``description``: Human-readable name for the texture type.
    - ``notes``:       Factual Skyrim SE usage notes.
    - ``hint``:        Optional context hint derived from the folder path.
    """
    stem = path.stem.lower()
    for suffix, (role, description, notes) in _SKYRIM_SE_SUFFIX_INFO.items():
        if stem.endswith(suffix):
            return {
                "role": role,
                "suffix": suffix,
                "description": description,
                "notes": notes,
                "hint": _get_skyrim_path_hint(path),
            }
    for alias_suffix in sorted(_SKYRIM_SE_SUFFIX_ALIASES, key=len, reverse=True):
        if not stem.endswith(alias_suffix):
            continue
        canonical_suffix = _SKYRIM_SE_SUFFIX_ALIASES[alias_suffix]
        role, description, notes = _SKYRIM_SE_SUFFIX_INFO[canonical_suffix]
        return {
            "role": role,
            "suffix": alias_suffix,
            "description": f"{description} ({alias_suffix} alias)",
            "notes": notes,
            "hint": _get_skyrim_path_hint(path),
        }
    inferred_role = _infer_skyrim_role_from_name_tokens(stem)
    if inferred_role is not None:
        primary_suffix = _SKYRIM_ROLE_PRIMARY_SUFFIX[inferred_role]
        role, description, notes = _SKYRIM_SE_SUFFIX_INFO[primary_suffix]
        return {
            "role": role,
            "suffix": "",
            "description": f"{description} (inferred from filename)",
            "notes": notes,
            "hint": _get_skyrim_path_hint(path),
        }
    return {
        "role": "diffuse",
        "suffix": "",
        "description": "Diffuse / Albedo Texture",
        "notes": (
            "Source diffuse/albedo texture. Texture Slot 0 in the NIF. "
            "Contains the primary colour and surface detail. "
            "Alpha channel may store opacity or specular depending on the shader flags used."
        ),
        "hint": _get_skyrim_path_hint(path),
    }


def get_generation_warnings(
    material_type: str,
    *,
    source_role: str | None = None,
    source_hint: str | None = None,
    source_suffix: str | None = None,
    include_diffuse: bool = False,
    include_normal: bool = False,
    include_glow: bool,
    include_environment_mask: bool,
    include_rmaos: bool = False,
    env_mask_mode: str,
    env_mask_strength: float,
    include_parallax: bool,
    parallax_strength: float | None = None,
    include_complex: bool,
    complex_format: str = "msn",
    parallax_mode: str = "standard",
    emboss_mode: bool = False,
    relief_mode: bool = False,
    include_wetness_mask: bool = False,
    include_snow_mask: bool = False,
    source_size: tuple[int, int] | None = None,
) -> list[tuple[str, str]]:
    """Return a list of (warning_id, human-readable message) pairs for suspicious generation choices.

    The caller is responsible for filtering out dismissed warnings and
    presenting remaining ones to the user.  Warning IDs are stable strings
    so they can be stored in a ``dismissed_warnings`` set.
    """
    warnings: list[tuple[str, str]] = []
    normalized_complex_format = complex_format.strip().lower()
    normalized_parallax_mode = parallax_mode.strip().lower()
    normalized_source_suffix = (source_suffix or "").strip().lower()
    is_enb_complex_combo = include_complex and normalized_complex_format == "msn" and env_mask_mode == "complex"

    organic_types = {"plants", "cloth", "skin"}

    if include_glow and material_type in {"stone", "wood", "plants", "metal", "sand", "snow", "cloth"}:
        warnings.append((
            "glow_non_magical",
            f"Glow map enabled for a '{material_type}' texture.\n\n"
            "Most stone, wood, metal, foliage, and fabric textures don't glow in Skyrim — "
            "only emissive/magical surfaces should. The result may look strange in-game.\n\n"
            "Tip: Only use glow maps for fire, magic, candles, or intentionally glowing objects.",
        ))

    if include_environment_mask and material_type in organic_types and env_mask_strength > 1.4:
        warnings.append((
            "high_env_mask_organic",
            f"High environment mask strength ({env_mask_strength:.2f}) on a '{material_type}' texture.\n\n"
            "Plants, cloth, and skin surfaces are not shiny — high environment masks "
            "will make them look like polished metal or glass in-game.\n\n"
            "Tip: Keep environment mask strength below 1.3 for organic materials.",
        ))

    if include_environment_mask and env_mask_mode == "complex" and material_type in organic_types:
        warnings.append((
            "complex_env_mask_organic",
            f"Complex environment mask mode on a '{material_type}' texture.\n\n"
            "Complex mode generates an ENBSeries RGBA mask suited for hard, shiny surfaces. "
            "Organic surfaces like plants and cloth rarely benefit from complex mode and may look over-specced.\n\n"
            "Tip: Use 'standard' env mask mode for organic materials.",
        ))

    if include_parallax and material_type == "plants":
        warnings.append((
            "parallax_flat_plants",
            "Parallax height map enabled for a plant/foliage texture.\n\n"
            "Plant textures (leaves, grass, vines) are typically flat alpha-masked polygons — "
            "parallax height maps usually have no visible effect on them and only waste disk space.\n\n"
            "Tip: Disable parallax for plant and foliage textures.",
        ))

    if include_complex and material_type in organic_types:
        warnings.append((
            "complex_material_organic",
            f"Complex material enabled for a '{material_type}' texture.\n\n"
            "Complex material is designed for hard surfaces with distinct packed material channels. "
            "Organic surfaces like skin and cloth rarely benefit "
            "and the result may look incorrect without careful ENB configuration.\n\n"
            "Tip: Complex material works best on stone, metal, and glass.",
        ))

    if include_environment_mask and material_type == "glass" and env_mask_strength < 1.5:
        warnings.append((
            "low_env_mask_glass",
            f"Low environment mask strength ({env_mask_strength:.2f}) for a glass/crystal texture.\n\n"
            "Glass and crystal surfaces are highly reflective — a low environment mask strength "
            "will make them look dull and unrealistic in-game.\n\n"
            "Tip: Use environment mask strength above 1.6 for glass and crystal materials.",
        ))
    if include_environment_mask and material_type == "paper" and env_mask_strength > 1.2:
        warnings.append((
            "high_env_mask_paper",
            f"High environment mask strength ({env_mask_strength:.2f}) on a paper/card texture.\n\n"
            "Paper-based textures usually have low reflectivity in Skyrim. High mask strength can create unrealistic glossy highlights.\n\n"
            "Tip: Keep environment mask strength near 0.9–1.1 for paper/card assets.",
        ))
    if include_parallax and material_type == "paper":
        warnings.append((
            "parallax_flat_paper",
            "Parallax enabled for a paper/card texture.\n\n"
            "Most cards, notes, and book-art surfaces are effectively flat and gain little from parallax.\n\n"
            "Tip: Disable parallax unless the source has clear embossed depth detail.",
        ))

    if include_parallax and material_type == "glass":
        warnings.append((
            "parallax_flat_glass",
            "Parallax height map enabled for a glass/crystal texture.\n\n"
            "Glass and crystal surfaces in Skyrim (windows, bottles, gems) are transparent flat polygons — "
            "parallax depth maps have no visible effect on them and only increase file size.\n\n"
            "Tip: Disable parallax for glass and crystal textures. Use environment mask "
            "strength to control reflectivity instead.",
        ))

    if include_parallax and material_type == "skin":
        warnings.append((
            "parallax_character_skin",
            "Parallax height map enabled for a skin/character texture.\n\n"
            "Skin and character face textures are applied to animated meshes — parallax depth "
            "maps can cause visible shading seams, lighting artefacts, and flickering at the "
            "neck/wrist transition areas.\n\n"
            "Tip: Disable parallax for skin and character textures. The Characters render "
            "profile disables it automatically to avoid these issues.",
        ))

    resolved_parallax_strength = parallax_strength if parallax_strength is not None else 0.0

    if include_parallax and material_type == "terrain":
        warnings.append((
            "parallax_landscape_shimmer",
            "Parallax on a landscape/terrain texture may cause horizon shimmer.\n\n"
            "Skyrim SE's terrain mesh has lower polygon density than objects — "
            "parallax height maps on landscape textures can produce visible wavering "
            "artefacts at medium-to-far distances, especially near the horizon.\n\n"
            "Tip: Keep parallax strength below 1.3 for landscape/terrain textures, "
            "or use 'occlusion' parallax mode (POM) which handles terrain gradients better.",
        ))

    if include_parallax and material_type == "snow" and resolved_parallax_strength > 1.5:
        warnings.append((
            "parallax_high_strength_snow",
            f"High parallax strength ({resolved_parallax_strength:.2f}) on a snow/ice texture.\n\n"
            "Snow and ice surfaces have low-frequency height variation — strong parallax values "
            "amplify microdetail noise and cause mipmap instability at mid-to-far distances.\n\n"
            "Tip: Keep parallax strength at or below 1.5 for snow and ice textures.",
        ))

    if include_parallax and material_type in {"dirt", "sand"} and resolved_parallax_strength > 2.0:
        warnings.append((
            "parallax_high_strength_ground",
            f"High parallax strength ({resolved_parallax_strength:.2f}) on a '{material_type}' texture.\n\n"
            "Ground-cover textures (dirt, mud, sand) tile aggressively — very high parallax values "
            "amplify per-pixel noise and can produce distracting depth artefacts at close range.\n\n"
            "Tip: Keep parallax strength at or below 2.0 for ground-cover materials.",
        ))

    if include_environment_mask and material_type == "architecture" and env_mask_strength > 1.8:
        warnings.append((
            "high_env_mask_architecture",
            f"High environment mask strength ({env_mask_strength:.2f}) on an architecture texture.\n\n"
            "Nordic/Imperial stone and woodwork surfaces are rough rather than polished — "
            "very high cubemap reflections will make city walls look like wet metal.\n\n"
            "Tip: Use environment mask strength between 1.0–1.5 for architecture textures. "
            "Reserve values above 1.6 for polished materials like Dwemer brass.",
        ))

    resolved_source_role = source_role or "diffuse"
    derived_roles = {
        "normal",
        "parallax",
        "glow",
        "environment_mask",
        "subsurface",
        "skin_specular",
        "complex_material",
        "complex_material_cm",
    }
    if include_diffuse and resolved_source_role in derived_roles:
        warnings.append((
            "diffuse_from_derived_source",
            f"The selected input appears to be a '{resolved_source_role}' texture, not a diffuse/albedo source.\n\n"
            "Generating a diffuse output from an already derived map usually produces incorrect colours/shading in-game.\n\n"
            "Tip: Use an albedo/diffuse source texture (no _n/_p/_g/_m/_rmaos/_ramos/_msn/_cm/_c suffix) for best results.",
        ))
    if include_normal and resolved_source_role == "normal":
        warnings.append((
            "normal_from_normal_source",
            "Input already looks like a normal map (_n).\n\n"
            "Regenerating a normal map from a normal map compounds artifacts and can break lighting response.\n\n"
            "Tip: Generate normals from the diffuse/albedo source instead.",
        ))
    if include_parallax and resolved_source_role == "parallax":
        warnings.append((
            "parallax_from_parallax_source",
            "Input already looks like a parallax/height map (_p).\n\n"
            "Regenerating parallax from an existing height map can reduce useful depth range.\n\n"
            "Tip: Generate parallax from the diffuse source when possible.",
        ))
    if include_glow and resolved_source_role == "glow":
        warnings.append((
            "glow_from_glow_source",
            "Input already looks like a glow/emissive map (_g/_em/_emit/_emissive).\n\n"
            "Regenerating glow from a glow map can over-compress emissive range.\n\n"
            "Tip: Generate glow from diffuse/albedo unless intentionally post-processing a glow texture.",
        ))
    if include_environment_mask and resolved_source_role == "environment_mask":
        warnings.append((
            "env_mask_from_env_mask_source",
            "Input already looks like an environment mask (_m/_rmaos/_ramos).\n\n"
            "Regenerating a mask from a mask can flatten reflection response.\n\n"
            "Tip: Build environment masks from diffuse/albedo sources for better material separation.",
        ))
    if include_complex and resolved_source_role in {"complex_material", "complex_material_cm"}:
        warnings.append((
            "complex_from_complex_source",
            "Input already looks like a complex material map (_msn/_cm/_c).\n\n"
            "Regenerating complex material from packed complex inputs often damages channel meaning.\n\n"
            "Tip: Start from diffuse/albedo source when creating new complex materials.",
        ))
    if normalized_source_suffix in {"_rmaos", "_ramos"}:
        warnings.append((
            "rmaos_source_requires_renderer_check",
            "Input uses the '_rmaos' or '_ramos' suffix.\n\n"
            "_rmaos/_ramos is used for Community Shaders TruePBR JSON workflows and is NOT the same as Community Shaders _cm/_c Extended Materials.\n\n"
            "Tip: Use the TruePBR renderer/profile path for _rmaos/_ramos generation and avoid mixing this map into ENB complex-material setups.",
        ))
    hint_text = (source_hint or "").lower()
    if "ui/interface texture" in hint_text and (include_parallax or include_environment_mask or include_complex):
        warnings.append((
            "ui_texture_advanced_maps",
            "Input path looks like a UI/interface texture.\n\n"
            "Parallax, environment mask, and complex material outputs are usually meant for in-world 3D surfaces, not menu/interface assets.\n\n"
            "Tip: For UI/interface textures, prefer diffuse and only add glow when intentionally needed.",
        ))

    # --- Conflicting option combinations ---
    if emboss_mode and relief_mode:
        warnings.append((
            "emboss_and_relief_both_enabled",
            "Both 'Emboss depth' and 'Relief / Pop-out depth' modes are enabled at the same time.\n\n"
            "These modes use different algorithms to interpret surface depth — enabling both simultaneously is contradictory and the result will be unpredictable.\n\n"
            "Tip: Enable only one depth mode. Use Emboss for flat printed surfaces (books, scrolls), "
            "or Relief for painted artwork that should pop out of the surface.",
        ))

    if (emboss_mode or relief_mode) and not include_normal:
        warnings.append((
            "depth_mode_without_normal",
            f"{'Emboss' if emboss_mode else 'Relief'} depth mode is enabled but 'Normal map' output is unchecked.\n\n"
            "Depth modes only affect the generated normal map — with normal map generation disabled, "
            "this setting has no effect.\n\n"
            "Tip: Enable 'Normal map' output to use depth mode, or disable the depth mode toggle.",
        ))

    if include_environment_mask and include_complex and not is_enb_complex_combo:
        warnings.append((
            "env_mask_with_complex_material",
            "Both 'Environment mask' and 'Complex material' outputs are enabled.\n\n"
            "This combination is often redundant outside ENB complex-material workflows and can produce double-specular artefacts.\n\n"
            "Tip: For ENB-style complex workflows use the renderer-specific profile/settings; for Community Shaders use _cm/_c/_C or TruePBR JSON "
            "workflows separately instead of mixing paths.",
        ))

    if include_complex and normalized_complex_format == "cm" and env_mask_mode == "complex":
        warnings.append((
            "cm_with_complex_env_mode",
            "Complex material format is set to '_cm' while environment mask mode is set to 'complex'.\n\n"
            "_cm/_c/_C is the Community Shaders Extended Materials packed map, while complex env mode is renderer-specific packed env-mask output.\n\n"
            "Tip: Switch env mask mode to 'standard' for _cm/_c/_C, or switch complex format to '_msn' for ENB-style complex workflows. Community Shaders and ENB should not be mixed.",
        ))

    if include_complex and normalized_complex_format == "msn" and env_mask_mode == "standard":
        warnings.append((
            "msn_with_standard_env_mode",
            "Complex material format is set to '_msn' but environment mask mode is 'standard'.\n\n"
            "_msn is typically used in ENB complex-material setups, which usually pair with complex env mode.\n\n"
            "Tip: If targeting ENB complex workflows, switch env mask mode to 'complex'.",
        ))

    if include_complex and normalized_complex_format == "cm" and not include_normal:
        warnings.append((
            "cm_without_normal_map",
            "Community Shaders '_cm/_c/_C' output is enabled but normal-map output is disabled.\n\n"
            "Community Shaders Extended Materials expects a standard '_n.dds' alongside the packed Slot 5 map.\n\n"
            "Tip: Enable normal-map generation when using '_cm/_c/_C'.",
        ))

    if include_rmaos and not include_normal:
        warnings.append((
            "rmaos_without_normal_map",
            "TruePBR RMAOS output is enabled but normal-map output is disabled.\n\n"
            "Community Shaders TruePBR workflows typically pair '_rmaos/_ramos' with a standard '_n.dds' normal map.\n\n"
            "Tip: Enable normal-map generation for TruePBR outputs unless your material setup intentionally omits it.",
        ))

    if include_complex and normalized_complex_format == "msn" and include_normal:
        warnings.append((
            "msn_with_normal_output",
            "Both '_msn' complex material and regular normal-map output are enabled.\n\n"
            "ENB complex-material workflows normally use '_msn' instead of '_n.dds', not alongside it.\n\n"
            "Tip: Disable regular normal-map output when targeting ENB complex materials unless you intentionally need both variants in separate installs.",
        ))

    if include_rmaos and include_complex and normalized_complex_format == "msn":
        warnings.append((
            "rmaos_with_msn_mix",
            "Both TruePBR '_rmaos/_ramos' output and ENB '_msn' complex-material output are enabled.\n\n"
            "These are different renderer workflows and should not be combined in the same output set.\n\n"
            "Tip: For ENB use '_msn' workflow outputs; for TruePBR use '_n + _rmaos/_ramos' with the generated JSON sidecar.",
        ))

    if include_environment_mask and env_mask_mode == "complex" and not include_complex:
        warnings.append((
            "complex_env_without_msn",
            "Complex environment-mask mode is enabled but complex-material output is disabled.\n\n"
            "In this tool, TruePBR-style workflows typically use '_n + _rmaos/_ramos' with JSON config, while ENB preset output usually pairs '_m' complex env-mask data with '_msn'.\n\n"
            "Tip: If targeting ENB preset output, enable complex-material output; if targeting TruePBR, keep normal-map output and validate your JSON workflow.",
        ))

    if include_parallax and normalized_parallax_mode == "occlusion" and normalized_complex_format == "cm":
        warnings.append((
            "cm_with_enb_pom",
            "Parallax mode is set to 'occlusion (ENB/POM)' while complex format is '_cm/_c/_C'.\n\n"
            "ENB POM and Community Shaders Extended Materials are different workflows. This setup mixes ENB-only parallax with Community Shaders packed materials.\n\n"
            "Tip: Use standard parallax mode for '_cm/_c/_C', or switch the complex format to '_msn' for an ENB workflow.",
        ))

    # --- Wetness and snow mask warnings ---
    snow_incompatible_types = {"glass", "metal", "skin", "cloth", "plants"}
    if include_snow_mask and material_type in snow_incompatible_types:
        warnings.append((
            "snow_mask_on_non_accumulating_material",
            f"Snow mask enabled for a '{material_type}' texture.\n\n"
            "Dynamic snow shaders accumulate snow on rough, horizontal surfaces. "
            "Glass, polished metal, skin, cloth, and plant textures are unlikely to accumulate "
            "visible snow — the mask will produce unexpected brightening on non-snow-covered surfaces.\n\n"
            "Tip: Reserve snow masks for terrain, stone, wood, and architecture textures.",
        ))

    if include_snow_mask and material_type == "terrain":
        warnings.append((
            "snow_mask_terrain_check",
            "Snow mask enabled for a terrain/landscape texture.\n\n"
            "Terrain snow masks can cause horizon shimmer when used at high strengths. "
            "Make sure the target shader pack supports terrain-space snow masking.\n\n"
            "Tip: Keep snow mask strength at or below 1.2 for landscape/terrain textures.",
        ))

    wetness_incompatible_types = {"glass", "metal"}
    if include_wetness_mask and material_type in wetness_incompatible_types:
        warnings.append((
            "wetness_mask_on_reflective_material",
            f"Wetness mask enabled for a '{material_type}' texture.\n\n"
            "Wet-surface effects on glass or polished metal surfaces are typically "
            "driven by cubemap reflection intensity, not by a separate wetness mask. "
            "A standard wetness mask on these materials may produce double-wet visual artefacts.\n\n"
            "Tip: Use environment mask strength instead of a wetness mask for glass and polished metal.",
        ))

    # --- Source resolution warning ---
    if source_size is not None:
        src_w, src_h = int(source_size[0]), int(source_size[1])
        if src_w > 0 and src_h > 0:
            max_dim, res_reason = recommend_output_resolution(src_w, src_h, material_type)
            if max(src_w, src_h) > max_dim:
                warnings.append((
                    "source_resolution_excessive",
                    f"Source texture is {src_w}×{src_h} px — larger than the recommended "
                    f"{max_dim}×{max_dim} px maximum for '{material_type}' assets in Skyrim SE.\n\n"
                    f"{res_reason}\n\n"
                    "Tip: Consider resizing the source to the recommended maximum before generation "
                    "to reduce VRAM usage and file size without a visible quality loss.",
                ))

    return warnings


def get_output_folder_format_warnings(
    output_dir: Path,
    *,
    include_complex: bool,
    complex_format: str,
) -> list[tuple[str, str]]:
    """Return (warning_id, message) pairs when the output folder contains complex-material files
    that use a different naming convention than the one currently selected.

    Checks for the presence of *_msn.dds / *_cm.dds / *_c.dds files that conflict with
    ``complex_format``.
    """
    warnings: list[tuple[str, str]] = []
    if not include_complex or not output_dir.is_dir():
        return warnings

    normalized_format = complex_format.strip().lower()

    # Scan for existing complex material files in the output folder (non-recursive).
    msn_files = list(output_dir.glob("*_msn.dds")) + list(output_dir.glob("*_msn.png"))
    cm_files = (
        list(output_dir.glob("*_cm.dds"))
        + list(output_dir.glob("*_cm.png"))
        + list(output_dir.glob("*_c.dds"))
        + list(output_dir.glob("*_c.png"))
        + list(output_dir.glob("*_C.dds"))
        + list(output_dir.glob("*_C.png"))
    )

    if normalized_format == "cm" and msn_files:
        sample = msn_files[0].name
        count = len(msn_files)
        warnings.append((
            "output_folder_has_msn_files",
            f"The output folder contains {count} existing '_msn' complex-material file(s) (e.g. {sample}), "
            f"but you are about to generate packed '_cm'/'_c' format files.\n\n"
            "Mixing _msn and _cm/_c formats in the same folder can cause Skyrim SE / ENB to load the wrong variant, "
            "producing incorrect shading or missing effects in-game.\n\n"
            "Tip: Change the Complex Material Format to 'msn' to match existing files, "
            "or clear the output folder before switching formats.",
        ))
    elif normalized_format == "msn" and cm_files:
        sample = cm_files[0].name
        count = len(cm_files)
        warnings.append((
            "output_folder_has_cm_files",
            f"The output folder contains {count} existing '_cm'/'_c' complex-material file(s) (e.g. {sample}), "
            f"but you are about to generate '_msn' format files.\n\n"
            "Mixing _cm/_c and _msn formats in the same folder can cause Skyrim SE / ENB to load the wrong variant, "
            "producing incorrect shading or missing effects in-game.\n\n"
            "Tip: Change the Complex Material Format to 'cm' to match existing files, "
            "or clear the output folder before switching formats.",
        ))

    return warnings


def collect_source_textures(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if not _is_supported_input_file(input_path):
            raise ValueError(f"Unsupported input file: {input_path}")
        return [input_path]

    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")
    if not input_path.is_dir():
        raise ValueError(f"Input path must be a file or directory: {input_path}")

    source_files = sorted(
        path
        for path in input_path.rglob(f"*{DDS_EXTENSION}")
        if path.is_file() and path.suffix.lower() == DDS_EXTENSION and not _is_generated_texture(path)
    )
    if not source_files:
        raise ValueError(
            f"No source DDS textures found in {input_path}. "
            "Folder mode scans subfolders, processes original DDS files, and skips generated *_n, *_p, *_g, *_glow, *_em, *_emit, *_emissive, *_m, *_s, *_sk, *_wt, *_sm, *_rmaos, *_ramos, *_msn, *_cm, *_c, and *_C variants."
        )
    return source_files


def select_generation_context_source(input_path: Path, selected_inputs: list[Path]) -> Path:
    """Choose the source path used for generation warnings and role hints."""
    return selected_inputs[0] if selected_inputs else input_path


def _dds_pixel_format_uses_alpha(pixel_format: str) -> bool:
    normalized = pixel_format.strip().upper()
    return normalized not in {"DXT1", "BC1", "BC1_UNORM"}


def _prefilter_for_mipmap_stability(image: Image.Image, output_kind: str) -> Image.Image:
    """Apply a lightweight pre-filter that reduces shimmer in GPU-generated mipmaps.

    Pillow's built-in DDS writer does not generate custom mipmap chains.  When
    Skyrim's renderer builds the mip pyramid from the saved base-level texture it
    uses a simple box/bilinear filter.  High-frequency noise (fine grain in normal
    maps, repetitive micro-detail in parallax maps) survives into mid-level mipmaps
    and produces shimmering or moiré patterns at medium-to-far view distances.

    This function applies a subtle frequency-reduction pass that "pre-bakes" some of
    the mipmap quality work into the base texture, making the GPU-generated mip
    chain more stable without visibly softening the full-resolution view.

    Rules per output kind:
    * ``"normal"`` — Gently blend toward a flat neutral (128, 128, 255) normal so
      high-frequency micro-bumps don't create shimmer at distance.  The blend is
      very subtle (≈ 8 %) and the directional information is fully preserved at 1:1.
    * ``"parallax"`` — Apply a very light Gaussian pass to suppress random noise
      spikes that cause flickering under POM ray-marching at distance.
    * ``"rmaos"`` / ``"complex_material"`` — No pre-filtering; packed channels are
      left untouched to avoid channel cross-contamination.
    * All other kinds — Pass through unchanged.
    """
    normalized_kind = str(output_kind or "").strip().lower()

    if normalized_kind == "normal":
        if image.mode not in {"RGB", "RGBA"}:
            return image
        has_alpha = image.mode == "RGBA"
        rgb = image.convert("RGB")
        r, g, b = rgb.split()
        # Gentle directional smoothing — preserve the blue channel (hemisphere normal)
        # but lightly smooth R and G (XY tangent directions) to reduce micro-detail shimmer.
        smooth_radius = 0.5
        r_smoothed = r.filter(ImageFilter.GaussianBlur(radius=smooth_radius))
        g_smoothed = g.filter(ImageFilter.GaussianBlur(radius=smooth_radius))
        # Blue channel: blend toward 255 (flat-up vector) to reduce shimmer from noisy normals.
        b_arr = np.frombuffer(b.tobytes(), dtype=np.uint8).astype(np.float32)
        b_stabilised = np.clip(b_arr * 0.92 + 255.0 * 0.08, 0.0, 255.0).astype(np.uint8)
        b_new = Image.fromarray(b_stabilised.reshape(b.size[1], b.size[0]), mode="L")
        stable_rgb = Image.merge("RGB", (r_smoothed, g_smoothed, b_new))
        if has_alpha:
            alpha = image.getchannel("A")
            return Image.merge("RGBA", (*stable_rgb.split(), alpha))
        return stable_rgb

    if normalized_kind == "parallax":
        if image.mode != "L":
            return image
        # Very light Gaussian smoothing to damp random noise spikes.
        # Radius 0.4 at base resolution is nearly invisible at 1:1 but meaningfully
        # stabilises mid-level GPU mipmaps and reduces POM stair-stepping artefacts.
        return image.filter(ImageFilter.GaussianBlur(radius=0.4))

    return image


def _to_dds_compatible_image(image: Image.Image, *, pixel_format: str) -> Image.Image:
    if _dds_pixel_format_uses_alpha(pixel_format):
        if image.mode == "RGBA":
            return image
        if image.mode in {"RGB", "L", "LA"}:
            return image.convert("RGBA")
        return image.convert("RGBA")
    if image.mode == "RGB":
        return image
    return image.convert("RGB")


def _normalise_preferred_dds_formats(preferred_pixel_formats: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for pixel_format in preferred_pixel_formats:
        candidate = str(pixel_format).strip().upper()
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    if not normalized:
        return ("DXT5",)
    return tuple(normalized)


def _image_has_visible_alpha(image: Image.Image) -> bool:
    if image.mode not in {"RGBA", "LA", "PA"}:
        return False
    alpha = image.getchannel("A")
    alpha_min, alpha_max = alpha.getextrema()
    return alpha_min < alpha_max or alpha_max < 255


def _preferred_dds_formats_for_output(
    output_kind: str,
    image: Image.Image,
    *,
    complex_format: str = "msn",
    env_mask_mode: str = "standard",
) -> tuple[str, ...]:
    normalized_kind = str(output_kind or "").strip().lower()
    normalized_complex_format = _normalize_complex_format(complex_format)
    normalized_env_mask_mode = _normalize_env_mask_mode(env_mask_mode)

    if normalized_kind == "diffuse":
        if _image_has_visible_alpha(image):
            return ("BC7", "DXT5", "DXT3")
        return ("BC7", "DXT1", "DXT5")
    if normalized_kind == "normal":
        return ("BC5", "DXT5", "DXT3")
    if normalized_kind == "parallax":
        return ("DXT1", "DXT5")
    if normalized_kind == "glow":
        return ("DXT5", "DXT3") if _image_has_visible_alpha(image) else ("DXT1", "DXT5")
    if normalized_kind == "environment_mask":
        if normalized_env_mask_mode == "standard":
            return ("DXT1", "DXT5")
        return ("BC7", "DXT5", "DXT3")
    if normalized_kind == "rmaos":
        return ("BC7", "DXT5", "DXT3")
    if normalized_kind == "complex_material":
        return ("BC7", "DXT5", "DXT3") if normalized_complex_format == "cm" else ("DXT5", "DXT3")
    return ("DXT5",)


def _save_with_dds_fallback(
    image: Image.Image,
    output_path: Path,
    *,
    preferred_pixel_formats: tuple[str, ...] = ("DXT5",),
    output_kind: str = "",
) -> Path:
    def _atomic_save(target: Path, image_to_save: Image.Image, **save_kwargs: object) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target.with_name(f".{target.stem}.{uuid.uuid4().hex}{target.suffix}")
        try:
            image_to_save.save(temp_path, **save_kwargs)
            os.replace(temp_path, target)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    # Apply mipmap-stability pre-filter for normal and parallax outputs.
    # This reduces shimmer caused by GPU bilinear box-filter mip generation.
    image = _prefilter_for_mipmap_stability(image, output_kind)

    normalized_formats = _normalise_preferred_dds_formats(preferred_pixel_formats)
    dds_target = output_path.with_suffix(DDS_EXTENSION)
    dds_errors: list[str] = []
    for pixel_format in normalized_formats:
        try:
            dds_image = _to_dds_compatible_image(image, pixel_format=pixel_format)
            _atomic_save(dds_target, dds_image, format="DDS", pixel_format=pixel_format)
            return dds_target
        except Exception as exc:  # noqa: BLE001
            dds_errors.append(f"{pixel_format}: {exc}")
            continue
    fallback = output_path.with_suffix(".png")
    if dds_errors:
        print(
            f"[DDS fallback] Could not save {dds_target.name} as DDS ({'; '.join(dds_errors)}). Saved PNG fallback: {fallback.name}",
            file=sys.stderr,
        )
    _atomic_save(fallback, image, format="PNG")
    return fallback


def run(
    input_file: Path,
    output_dir: Path | None = None,
    diffuse_name: str | None = None,
    parallax_name: str | None = None,
    parallax_strength: float = 1.35,
) -> tuple[Path, Path]:
    with Image.open(input_file) as opened_source:
        source = opened_source.convert("RGB")
    diffuse = generate_diffuse(source)
    parallax = generate_parallax(source, strength=parallax_strength)

    diffuse_path, parallax_path = build_output_paths(
        input_path=input_file,
        output_dir=output_dir,
        diffuse_name=diffuse_name,
        parallax_name=parallax_name,
    )

    return _save_with_dds_fallback(diffuse, diffuse_path), _save_with_dds_fallback(parallax, parallax_path)


def run_with_options(
    input_file: Path,
    output_dir: Path | None = None,
    diffuse_name: str | None = None,
    normal_name: str | None = None,
    parallax_name: str | None = None,
    glow_name: str | None = None,
    environment_mask_name: str | None = None,
    rmaos_name: str | None = None,
    complex_name: str | None = None,
    normal_strength: float | None = None,
    parallax_strength: float | None = None,
    glow_threshold: int | None = None,
    environment_mask_strength: float | None = None,
    rmaos_strength: float | None = None,
    complex_strength: float | None = None,
    specular_strength: float | None = None,
    complex_format: str = "msn",
    env_mask_mode: str = "standard",
    emboss_mode: bool = False,
    relief_mode: bool = False,
    parallax_mode: str = "standard",
    include_diffuse: bool = True,
    include_normal: bool = True,
    include_parallax: bool = True,
    include_glow: bool = False,
    include_environment_mask: bool = False,
    include_rmaos: bool = False,
    include_complex: bool = False,
    render_profile: str = "auto",
    include_wetness_mask: bool = False,
    wetness_mask_strength: float | None = None,
    wetness_name: str | None = None,
    include_snow_mask: bool = False,
    snow_mask_strength: float | None = None,
    snow_name: str | None = None,
) -> dict[str, Path]:
    if not any((
        include_diffuse,
        include_normal,
        include_parallax,
        include_glow,
        include_environment_mask,
        include_rmaos,
        include_complex,
        include_wetness_mask,
        include_snow_mask,
    )):
        raise ValueError("Select at least one output.")
    if parallax_mode not in {"standard", "occlusion"}:
        raise ValueError("parallax_mode must be 'standard' or 'occlusion'.")
    planned_paths = _collect_planned_output_paths(
        input_file=input_file,
        output_dir=output_dir,
        diffuse_name=diffuse_name,
        normal_name=normal_name,
        parallax_name=parallax_name,
        glow_name=glow_name,
        environment_mask_name=environment_mask_name,
        rmaos_name=rmaos_name,
        complex_name=complex_name,
        wetness_name=wetness_name,
        snow_name=snow_name,
        complex_format=complex_format,
        env_mask_mode=env_mask_mode,
        render_profile=render_profile,
        include_diffuse=include_diffuse,
        include_normal=include_normal,
        include_parallax=include_parallax,
        include_glow=include_glow,
        include_environment_mask=include_environment_mask,
        include_rmaos=include_rmaos,
        include_complex=include_complex,
        include_wetness_mask=include_wetness_mask,
        include_snow_mask=include_snow_mask,
    )
    _validate_output_path_conflicts(planned_paths)
    resolved_env_complex_workflow = resolve_env_mask_complex_workflow(
        env_mask_mode=env_mask_mode,
        complex_format=complex_format,
        render_profile=render_profile,
        include_complex=include_complex,
    )

    outputs: dict[str, Path] = {}

    with Image.open(input_file) as opened_source:
        source = opened_source.convert("RGB")
        # Downsample to ≤512 px for analysis/recommendations on large textures —
        # auto-suggestions are a scalar signal and do not need full resolution.
        analysis_source = source
        if source.width * source.height > 512 * 512:
            analysis_source = source.copy()
            analysis_source.thumbnail((512, 512), Image.Resampling.NEAREST)
        recommended = recommend_generation_settings(analysis_source, input_path=input_file)
        del analysis_source
        # Derive material type for material-aware outputs (e.g. TruePBR JSON sidecar).
        resolved_material_type = classify_material_type(input_file)
        resolved_normal_strength = normal_strength if normal_strength is not None else float(recommended["normal_strength"])
        resolved_parallax_strength = parallax_strength if parallax_strength is not None else float(recommended["parallax_strength"])
        resolved_glow_threshold = glow_threshold if glow_threshold is not None else int(recommended["glow_threshold"])
        resolved_environment_mask_strength = (
            environment_mask_strength if environment_mask_strength is not None else float(recommended["environment_mask_strength"])
        )
        resolved_rmaos_strength = (
            rmaos_strength if rmaos_strength is not None else float(recommended["rmaos_strength"])
        )
        resolved_wetness_mask_strength = wetness_mask_strength if wetness_mask_strength is not None else 1.0
        resolved_snow_mask_strength = snow_mask_strength if snow_mask_strength is not None else 1.0
        resolved_complex_strength = complex_strength if complex_strength is not None else float(recommended["complex_strength"])
        resolved_specular_strength = specular_strength if specular_strength is not None else float(recommended["specular_strength"])

        if include_diffuse:
            diffuse = enforce_skyrim_output_profile("diffuse", generate_diffuse(source))
            diffuse_path, _ = build_output_paths(
                input_path=input_file,
                output_dir=output_dir,
                diffuse_name=diffuse_name,
                parallax_name=parallax_name,
            )
            outputs["diffuse"] = _save_with_dds_fallback(
                diffuse,
                diffuse_path,
                preferred_pixel_formats=_preferred_dds_formats_for_output("diffuse", diffuse),
            )

        if include_normal:
            normal = enforce_skyrim_output_profile(
                "normal", generate_normal(source, strength=resolved_normal_strength, emboss_mode=emboss_mode, relief_mode=relief_mode)
            )
            normal_path = build_normal_output_path(
                input_path=input_file,
                output_dir=output_dir,
                normal_name=normal_name,
            )
            outputs["normal"] = _save_with_dds_fallback(
                normal,
                normal_path,
                preferred_pixel_formats=_preferred_dds_formats_for_output("normal", normal),
                output_kind="normal",
            )

        if include_parallax:
            if parallax_mode == "occlusion":
                parallax = enforce_skyrim_output_profile(
                    "parallax", generate_parallax_occlusion(source, strength=resolved_parallax_strength, relief_mode=relief_mode)
                )
            else:
                parallax = enforce_skyrim_output_profile(
                    "parallax", generate_parallax(source, strength=resolved_parallax_strength, relief_mode=relief_mode)
                )
            _, parallax_path = build_output_paths(
                input_path=input_file,
                output_dir=output_dir,
                diffuse_name=diffuse_name,
                parallax_name=parallax_name,
            )
            outputs["parallax"] = _save_with_dds_fallback(
                parallax,
                parallax_path,
                preferred_pixel_formats=_preferred_dds_formats_for_output("parallax", parallax),
                output_kind="parallax",
            )

        if include_glow:
            glow = enforce_skyrim_output_profile("glow", generate_glow(source, threshold=resolved_glow_threshold))
            glow_path = build_glow_output_path(
                input_path=input_file,
                output_dir=output_dir,
                glow_name=glow_name,
            )
            outputs["glow"] = _save_with_dds_fallback(
                glow,
                glow_path,
                preferred_pixel_formats=_preferred_dds_formats_for_output("glow", glow),
            )

        if include_environment_mask:
            environment_mask = enforce_skyrim_output_profile(
                "environment_mask",
                generate_environment_mask_for_workflow(
                    source,
                    strength=resolved_environment_mask_strength,
                    mode=env_mask_mode,
                    complex_workflow=resolved_env_complex_workflow,
                ),
                env_mask_mode=env_mask_mode,
            )
            environment_mask_path = build_environment_mask_output_path(
                input_path=input_file,
                output_dir=output_dir,
                environment_mask_name=environment_mask_name,
                env_mask_mode=env_mask_mode,
                complex_format=complex_format,
                render_profile=render_profile,
                include_complex=include_complex,
            )
            outputs["environment_mask"] = _save_with_dds_fallback(
                environment_mask,
                environment_mask_path,
                preferred_pixel_formats=_preferred_dds_formats_for_output(
                    "environment_mask",
                    environment_mask,
                    env_mask_mode=env_mask_mode,
                ),
            )

        if include_rmaos:
            rmaos_map = enforce_skyrim_output_profile(
                "environment_mask",
                generate_environment_mask_for_workflow(
                    source,
                    strength=resolved_rmaos_strength,
                    mode="complex",
                    complex_workflow="truepbr",
                ),
                env_mask_mode="complex",
            )
            rmaos_path = build_rmaos_output_path(
                input_path=input_file,
                output_dir=output_dir,
                rmaos_name=rmaos_name,
            )
            outputs["rmaos"] = _save_with_dds_fallback(
                rmaos_map,
                rmaos_path,
                preferred_pixel_formats=_preferred_dds_formats_for_output("rmaos", rmaos_map),
            )
            outputs["rmaos_json"] = write_rmaos_json_sidecar(
                outputs["rmaos"],
                parallax_enabled=include_parallax,
                material_type=resolved_material_type,
            )

        if include_wetness_mask:
            wetness_map = enforce_skyrim_output_profile(
                "parallax", generate_wetness_mask(source, strength=resolved_wetness_mask_strength)
            )
            wetness_path = build_wetness_mask_output_path(
                input_path=input_file,
                output_dir=output_dir,
                wetness_name=wetness_name,
            )
            outputs["wetness_mask"] = _save_with_dds_fallback(
                wetness_map,
                wetness_path,
                preferred_pixel_formats=_preferred_dds_formats_for_output("parallax", wetness_map),
                output_kind="parallax",
            )

        if include_snow_mask:
            snow_map = enforce_skyrim_output_profile(
                "parallax", generate_snow_mask(source, strength=resolved_snow_mask_strength)
            )
            snow_path = build_snow_mask_output_path(
                input_path=input_file,
                output_dir=output_dir,
                snow_name=snow_name,
            )
            outputs["snow_mask"] = _save_with_dds_fallback(
                snow_map,
                snow_path,
                preferred_pixel_formats=_preferred_dds_formats_for_output("parallax", snow_map),
                output_kind="parallax",
            )

        if include_complex:
            if complex_format == "msn":
                complex_material = enforce_skyrim_output_profile(
                    "complex_material",
                    generate_msn(
                        source,
                        normal_strength=resolved_normal_strength,
                        specular_strength=resolved_specular_strength,
                        emboss_mode=emboss_mode,
                        relief_mode=relief_mode,
                    ),
                    complex_format=complex_format,
                )
            else:
                complex_material = enforce_skyrim_output_profile(
                    "complex_material",
                    generate_complex_material(source, strength=resolved_complex_strength),
                    complex_format=complex_format,
                )
            complex_path = build_complex_output_path(
                input_path=input_file,
                output_dir=output_dir,
                complex_name=complex_name,
                complex_format=complex_format,
            )
            outputs["complex_material"] = _save_with_dds_fallback(
                complex_material,
                complex_path,
                preferred_pixel_formats=_preferred_dds_formats_for_output(
                    "complex_material",
                    complex_material,
                    complex_format=complex_format,
                ),
            )

    gc.collect()
    return outputs


def run_batch_with_options(
    input_path: Path,
    output_dir: Path | None = None,
    diffuse_name: str | None = None,
    normal_name: str | None = None,
    parallax_name: str | None = None,
    glow_name: str | None = None,
    environment_mask_name: str | None = None,
    rmaos_name: str | None = None,
    complex_name: str | None = None,
    normal_strength: float | None = None,
    parallax_strength: float | None = None,
    glow_threshold: int | None = None,
    environment_mask_strength: float | None = None,
    rmaos_strength: float | None = None,
    complex_strength: float | None = None,
    specular_strength: float | None = None,
    complex_format: str = "msn",
    env_mask_mode: str = "standard",
    emboss_mode: bool = False,
    relief_mode: bool = False,
    parallax_mode: str = "standard",
    include_diffuse: bool = True,
    include_normal: bool = True,
    include_parallax: bool = True,
    include_glow: bool = False,
    include_environment_mask: bool = False,
    include_rmaos: bool = False,
    include_complex: bool = False,
    render_profile: str = "auto",
    include_wetness_mask: bool = False,
    wetness_mask_strength: float | None = None,
    wetness_name: str | None = None,
    include_snow_mask: bool = False,
    snow_mask_strength: float | None = None,
    snow_name: str | None = None,
    progress_callback: Callable[[int, int, Path], None] | None = None,
    error_callback: Callable[[int, int, Path, Exception], None] | None = None,
    continue_on_error: bool = False,
    batch_workers: int | None = None,
) -> dict[Path, dict[str, Path]]:
    input_files = collect_source_textures(input_path)
    results: dict[Path, dict[str, Path]] = {}
    total = len(input_files)
    workers = _resolve_batch_workers(batch_workers, total)

    if workers == 1:
        for index, input_file in enumerate(input_files, start=1):
            if progress_callback is not None:
                progress_callback(index, total, input_file)
            try:
                results[input_file] = run_with_options(
                    input_file=input_file,
                    output_dir=output_dir,
                    diffuse_name=diffuse_name,
                    normal_name=normal_name,
                    parallax_name=parallax_name,
                    glow_name=glow_name,
                    environment_mask_name=environment_mask_name,
                    rmaos_name=rmaos_name,
                    complex_name=complex_name,
                    normal_strength=normal_strength,
                    parallax_strength=parallax_strength,
                    glow_threshold=glow_threshold,
                    environment_mask_strength=environment_mask_strength,
                    rmaos_strength=rmaos_strength,
                    complex_strength=complex_strength,
                    specular_strength=specular_strength,
                    complex_format=complex_format,
                    env_mask_mode=env_mask_mode,
                    emboss_mode=emboss_mode,
                    relief_mode=relief_mode,
                    parallax_mode=parallax_mode,
                    include_diffuse=include_diffuse,
                    include_normal=include_normal,
                    include_parallax=include_parallax,
                    include_glow=include_glow,
                    include_environment_mask=include_environment_mask,
                    include_rmaos=include_rmaos,
                    include_complex=include_complex,
                    render_profile=render_profile,
                    include_wetness_mask=include_wetness_mask,
                    wetness_mask_strength=wetness_mask_strength,
                    wetness_name=wetness_name,
                    include_snow_mask=include_snow_mask,
                    snow_mask_strength=snow_mask_strength,
                    snow_name=snow_name,
                )
            except Exception as exc:
                if error_callback is not None:
                    error_callback(index, total, input_file, exc)
                if not continue_on_error:
                    raise
        return results

    def _process_one(target_file: Path) -> dict[str, Path]:
        return run_with_options(
            input_file=target_file,
            output_dir=output_dir,
            diffuse_name=diffuse_name,
            normal_name=normal_name,
            parallax_name=parallax_name,
            glow_name=glow_name,
            environment_mask_name=environment_mask_name,
            rmaos_name=rmaos_name,
            complex_name=complex_name,
            normal_strength=normal_strength,
            parallax_strength=parallax_strength,
            glow_threshold=glow_threshold,
            environment_mask_strength=environment_mask_strength,
            rmaos_strength=rmaos_strength,
            complex_strength=complex_strength,
            specular_strength=specular_strength,
            complex_format=complex_format,
            env_mask_mode=env_mask_mode,
            emboss_mode=emboss_mode,
            relief_mode=relief_mode,
            parallax_mode=parallax_mode,
            include_diffuse=include_diffuse,
            include_normal=include_normal,
            include_parallax=include_parallax,
            include_glow=include_glow,
            include_environment_mask=include_environment_mask,
            include_rmaos=include_rmaos,
            include_complex=include_complex,
            render_profile=render_profile,
            include_wetness_mask=include_wetness_mask,
            wetness_mask_strength=wetness_mask_strength,
            wetness_name=wetness_name,
            include_snow_mask=include_snow_mask,
            snow_mask_strength=snow_mask_strength,
            snow_name=snow_name,
        )

    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_file = {executor.submit(_process_one, input_file): input_file for input_file in input_files}
        for future in concurrent.futures.as_completed(future_to_file):
            input_file = future_to_file[future]
            completed += 1
            if progress_callback is not None:
                progress_callback(completed, total, input_file)
            try:
                results[input_file] = future.result()
            except Exception as exc:
                if error_callback is not None:
                    error_callback(completed, total, input_file, exc)
                if not continue_on_error:
                    raise

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Skyrim texture maps from an input texture or a folder of source DDS textures."
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        type=Path,
        help="Path to an input texture file or a folder of source DDS textures.",
    )
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory for generated files.")
    parser.add_argument("--diffuse-name", type=str, default=None, help="Diffuse output file stem.")
    parser.add_argument("--normal-name", type=str, default=None, help="Normal output file stem.")
    parser.add_argument("--parallax-name", type=str, default=None, help="Parallax output file stem.")
    parser.add_argument("--glow-name", type=str, default=None, help="Glow output file stem.")
    parser.add_argument(
        "--environment-mask-name",
        type=str,
        default=None,
        help="Environment mask output file stem (_m by default).",
    )
    parser.add_argument(
        "--rmaos-name",
        "--ramos-name",
        dest="rmaos_name",
        type=str,
        default=None,
        help="RMAOS output file stem (_rmaos by default; _ramos also supported).",
    )
    parser.add_argument("--complex-name", type=str, default=None, help="Complex material output file stem.")
    parser.add_argument(
        "--complex-format",
        choices=("msn", "cm"),
        default="msn",
        help=(
            "Complex material format/suffix: msn -> _msn (ENB normal+spec alpha), "
            "cm -> Community Shaders Extended Materials packed env/gloss/metal/height (_cm by default, or _c/_C via --complex-name)."
        ),
    )
    parser.add_argument(
        "--normal-strength",
        type=float,
        default=None,
        help="Normal map detail strength factor (auto if omitted).",
    )
    parser.add_argument(
        "--parallax-strength",
        type=float,
        default=None,
        help="Parallax contrast strength factor (auto if omitted).",
    )
    parser.add_argument(
        "--glow-threshold",
        type=int,
        default=None,
        help="Glow brightness threshold (0-255, auto if omitted).",
    )
    parser.add_argument(
        "--environment-mask-strength",
        type=float,
        default=None,
        help="Environment mask contrast strength factor (auto if omitted).",
    )
    parser.add_argument(
        "--complex-strength",
        type=float,
        default=None,
        help="Complex material contrast strength factor (auto if omitted).",
    )
    parser.add_argument(
        "--rmaos-strength",
        type=float,
        default=None,
        help="RMAOS channel packing strength factor (auto if omitted).",
    )
    parser.add_argument("--wetness-mask", action="store_true", default=False, help="Generate a wetness mask (_wt.dds) for Community Shaders wet surface support.")
    parser.add_argument("--wetness-mask-strength", type=float, default=None, metavar="STRENGTH", help="Wetness mask strength (0.1–3.0). Default: 1.0.")
    parser.add_argument("--snow-mask", action="store_true", default=False, help="Generate a dynamic snow mask (_sm.dds) for Community Shaders Dynamic Snow.")
    parser.add_argument("--snow-mask-strength", type=float, default=None, metavar="STRENGTH", help="Snow mask strength (0.1–3.0). Default: 1.0.")
    parser.add_argument(
        "--specular-strength",
        type=float,
        default=None,
        help="Specular alpha strength when --complex-format=msn (auto if omitted).",
    )
    parser.add_argument("--no-diffuse", action="store_true", help="Skip diffuse output generation.")
    parser.add_argument("--no-normal", action="store_true", help="Skip normal output generation.")
    parser.add_argument("--no-parallax", action="store_true", help="Skip parallax output generation.")
    parser.add_argument("--glow-map", action="store_true", help="Generate glow output.")
    parser.add_argument("--environment-mask", action="store_true", help="Generate environment mask output.")
    parser.add_argument(
        "--rmaos",
        "--ramos",
        dest="rmaos",
        action="store_true",
        help="Generate dedicated TruePBR RMAOS output (_rmaos or _ramos via custom name) with JSON sidecar.",
    )
    parser.add_argument(
        "--environment-mask-mode",
        choices=("standard", "complex"),
        default="standard",
        help=(
            "Environment mask output mode. "
            "'standard' (default) = greyscale _m.dds for vanilla Skyrim SE (Texture Slot 5, no ENB required). "
            "'complex' = RGBA channel-packed _m texture for ENB-style complex workflows. "
            "Use --rmaos for dedicated TruePBR _rmaos output."
        ),
    )
    parser.add_argument(
        "--complex-material",
        action="store_true",
        help="Generate complex material output (ENB _msn or Community Shaders Extended Materials packed output: _cm default, _c/_C optional via --complex-name).",
    )
    parser.add_argument(
        "--pbr-material",
        action="store_true",
        help=(
            "Enable Community Shaders packed-material shortcut. "
            "Automatically enables complex material generation and switches --complex-format to 'cm' "
            "(packed env/gloss/metal/height workflow; _cm default, _c/_C optional via --complex-name). "
            "This is not ENB and should not be mixed with ENB workflows."
        ),
    )
    parser.add_argument(
        "--emboss-mode",
        action="store_true",
        help=(
            "Enable emboss depth mode for normal map generation. "
            "Replaces smooth gradient height maps with edge-ridge height maps that exaggerate "
            "printed text, borders, and artwork detail on flat surfaces such as books, cards, "
            "scrolls, and posters — giving them physically plausible embossed/debossed depth. "
            "Recommended for paper/book/card textures; leave off for terrain, stone, and wood."
        ),
    )
    parser.add_argument(
        "--relief-mode",
        action="store_true",
        help=(
            "Enable relief depth mode for normal map and parallax generation. "
            "Uses luminosity-as-height so that bright subjects (paintings, murals, signs, "
            "decorative plaques) appear physically extruded from the surface — a bas-relief "
            "effect that makes flat artwork look 3-D in game.  Takes priority over --emboss-mode. "
            "Recommended for paintings, signs, illustrated cards, and decorative wall art."
        ),
    )
    parser.add_argument(
        "--parallax-mode",
        choices=("standard", "occlusion"),
        default="standard",
        help=(
            "Parallax heightmap output mode. "
            "'standard' (default) = micro-detail height map for the Skyrim SE offset parallax shader. "
            "'occlusion' = smooth gradient height map optimised for ENBSeries Parallax Occlusion Mapping (POM). "
            "Both modes write the same _p.dds file; the difference is in heightmap quality when read by ENB POM."
        ),
    )
    parser.add_argument(
        "--render-profile",
        choices=_RENDER_PROFILE_CLI_VALUES,
        default="auto",
        help=(
            "Target renderer/output profile. "
            "'auto' (default) infers workflow from file path/mod-manager context; "
            "other presets force profile-specific format/mode/output defaults."
        ),
    )
    parser.add_argument(
        "--batch-workers",
        type=int,
        default=0,
        help="Parallel workers for folder batch mode (0 = automatic).",
    )
    parser.add_argument("--gui", action="store_true", help="Launch graphical interface.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    return parser.parse_args()


def _apply_cli_pbr_overrides(args: argparse.Namespace) -> argparse.Namespace:
    if getattr(args, "pbr_material", False):
        args.complex_material = True
        args.complex_format = "cm"
        args.environment_mask_mode = "standard"
        if getattr(args, "parallax_mode", "standard") == "occlusion":
            args.parallax_mode = "standard"
    return args


PATREON_URL = "https://www.patreon.com/cw/DeadOnTheInside"


def _create_panda_icon_image(size: int = 128) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size // 2

    # White head / face
    fr = int(size * 0.44)
    d.ellipse([c - fr, c - fr, c + fr, c + fr], fill=(255, 255, 255, 255), outline=(40, 40, 40, 255), width=max(2, size // 64))

    # Black ears (upper-left and upper-right, behind head)
    er = int(size * 0.14)
    eo = int(size * 0.30)
    d.ellipse([c - eo - er, c - eo - er, c - eo + er, c - eo + er], fill=(30, 30, 30, 255))
    d.ellipse([c + eo - er, c - eo - er, c + eo + er, c - eo + er], fill=(30, 30, 30, 255))

    # Black eye patches
    epw = int(size * 0.14)
    eph = int(size * 0.16)
    exo = int(size * 0.17)
    eyo = int(size * -0.04)
    d.ellipse([c - exo - epw, c + eyo - eph, c - exo + epw, c + eyo + eph], fill=(30, 30, 30, 255))
    d.ellipse([c + exo - epw, c + eyo - eph, c + exo + epw, c + eyo + eph], fill=(30, 30, 30, 255))

    # White eye highlights inside patches
    eyr = int(size * 0.065)
    eycy = c + eyo
    d.ellipse([c - exo - eyr, eycy - eyr, c - exo + eyr, eycy + eyr], fill=(255, 255, 255, 255))
    d.ellipse([c + exo - eyr, eycy - eyr, c + exo + eyr, eycy + eyr], fill=(255, 255, 255, 255))

    # Black pupils
    pr = int(size * 0.035)
    d.ellipse([c - exo - pr, eycy - pr, c - exo + pr, eycy + pr], fill=(10, 10, 10, 255))
    d.ellipse([c + exo - pr, eycy - pr, c + exo + pr, eycy + pr], fill=(10, 10, 10, 255))

    # Nose (dark oval, centre)
    ny = c + int(size * 0.14)
    nrx = int(size * 0.06)
    nry = int(size * 0.035)
    d.ellipse([c - nrx, ny - nry, c + nrx, ny + nry], fill=(50, 50, 50, 255))

    # Mouth (simple downward arc)
    mw = int(size * 0.13)
    my0 = ny + int(size * 0.03)
    my1 = my0 + int(size * 0.09)
    d.arc([c - mw, my0, c + mw, my1], start=0, end=180, fill=(50, 50, 50, 255), width=max(1, size // 80))

    return img


_LIGHT_THEME: dict[str, str] = {
    "bg": "#f0f0f0",
    "fg": "#000000",
    "field_bg": "#ffffff",
    "button_bg": "#e0e0e0",
    "trough": "#c8c8c8",
    "auto_trough": "#66b7ff",
    "tooltip_bg": "#ffffcc",
    "tooltip_fg": "#000000",
    "disabled_fg": "#888888",
}
_DARK_THEME: dict[str, str] = {
    "bg": "#1e1e2e",
    "fg": "#cdd6f4",
    "field_bg": "#313244",
    "button_bg": "#45475a",
    "trough": "#585b70",
    "auto_trough": "#2b7da8",
    "tooltip_bg": "#313244",
    "tooltip_fg": "#cdd6f4",
    "disabled_fg": "#6c7086",
}


if GUI_AVAILABLE:
    class TextureGeneratorGUI:
        def __init__(self) -> None:
            self.root = tk.Tk()
            self.root.title(f"Skyrim Texture Generator v{APP_VERSION}")
            self.root.geometry("960x700")
            self.source_image: Image.Image | None = None
            self.preview_before: ImageTk.PhotoImage | None = None
            self.preview_output_images: dict[str, ImageTk.PhotoImage] = {}
            self.selected_inputs: list[Path] = []
            self.current_preview_index = 0
            self.batch_failures: list[tuple[str, str]] = []
            self.batch_nif_patch_results: list[tuple[str, int, int]] = []
            self.processing_thread: threading.Thread | None = None
            self.processing_queue: queue.Queue[tuple[str, object]] = queue.Queue()
            self.is_processing = False
            self.cancel_requested = False
            self.last_generation_backup_dir: Path | None = None
            self.last_generation_backups: dict[Path, Path] = {}
            self.last_generation_created_files: set[Path] = set()
            self.manager_context = detect_mod_manager_context()
            self.last_input_browse_dir: Path | None = None
            self.preview_size_var = tk.StringVar(value="Medium")
            self.preview_refresh_after_id: str | None = None
            self.show_batch_preview_var = tk.BooleanVar(value=False)
            self.auto_patch_nifs_var = tk.BooleanVar(value=False)
            self.dark_mode_var = tk.BooleanVar(value=False)
            self._tooltip_bg = _LIGHT_THEME["tooltip_bg"]
            self._tooltip_fg = _LIGHT_THEME["tooltip_fg"]
            self.dismissed_warnings: set[str] = set()

            self.input_var = tk.StringVar()
            self.output_var = tk.StringVar()
            self.use_custom_output_var = tk.BooleanVar(value=False)
            self.preview_source_name_var = tk.StringVar(value="No source loaded")
            self.preview_jump_var = tk.StringVar(value="")
            self.detected_context_var = tk.StringVar(value=self.manager_context.summary)
            self.normal_strength_var = tk.DoubleVar(value=2.0)
            self.parallax_strength_var = tk.DoubleVar(value=1.35)
            self.complex_strength_var = tk.DoubleVar(value=1.15)
            self.specular_strength_var = tk.DoubleVar(value=1.15)
            self.glow_threshold_var = tk.DoubleVar(value=190.0)
            self.environment_mask_strength_var = tk.DoubleVar(value=1.2)
            self.rmaos_strength_var = tk.DoubleVar(value=1.2)
            self.normal_strength_display_var = tk.StringVar()
            self.parallax_strength_display_var = tk.StringVar()
            self.glow_threshold_display_var = tk.StringVar()
            self.environment_mask_strength_display_var = tk.StringVar()
            self.rmaos_strength_display_var = tk.StringVar()
            self.complex_strength_display_var = tk.StringVar()
            self.specular_strength_display_var = tk.StringVar()
            self.complex_format_var = tk.StringVar(value="msn")
            self.env_mask_mode_var = tk.StringVar(value="standard")
            self.emboss_mode_var = tk.BooleanVar(value=False)
            self.relief_mode_var = tk.BooleanVar(value=False)
            self.emboss_mode_manual_override = False
            self.relief_mode_manual_override = False
            self.parallax_mode_var = tk.StringVar(value="standard")
            self.render_profile_var = tk.StringVar(value="custom")
            self.render_profile_suggestion_var = tk.StringVar(
                value=build_render_profile_brief_message("vanilla")
            )
            self.render_profile_help_window: tk.Toplevel | None = None
            self.auto_suggestions_var = tk.BooleanVar(value=True)
            self.auto_normal_suggestion_var = tk.BooleanVar(value=True)
            self.auto_parallax_suggestion_var = tk.BooleanVar(value=True)
            self.auto_glow_suggestion_var = tk.BooleanVar(value=True)
            self.auto_environment_mask_suggestion_var = tk.BooleanVar(value=True)
            self.auto_rmaos_suggestion_var = tk.BooleanVar(value=True)
            self.auto_complex_suggestion_var = tk.BooleanVar(value=True)
            self.auto_specular_suggestion_var = tk.BooleanVar(value=True)
            self.theme_mode_label_var = tk.StringVar(value="☀ Light mode")
            self.include_diffuse_var = tk.BooleanVar(value=True)
            self.include_normal_var = tk.BooleanVar(value=True)
            self.include_parallax_var = tk.BooleanVar(value=True)
            self.include_glow_var = tk.BooleanVar(value=False)
            self.include_environment_mask_var = tk.BooleanVar(value=False)
            self.include_rmaos_var = tk.BooleanVar(value=False)
            self.include_wetness_mask_var = tk.BooleanVar(value=False)
            self.include_snow_mask_var = tk.BooleanVar(value=False)
            self.include_complex_var = tk.BooleanVar(value=False)
            self.status_var = tk.StringVar(
                value=self.manager_context.summary if self.manager_context.manager is not None else "Select a DDS file to begin."
            )
            self._apply_persisted_gui_state()
            self._update_theme_toggle_text()
            self._update_slider_value_labels()

            self._set_app_icon()
            container = ttk.Frame(self.root)
            container.pack(fill=tk.BOTH, expand=True)

            canvas = tk.Canvas(container, highlightthickness=0)
            scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
            wrapper = ttk.Frame(canvas, padding=12)

            canvas.configure(yscrollcommand=scrollbar.set)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

            canvas_window = canvas.create_window((0, 0), window=wrapper, anchor="nw")

            def _sync_scroll_region(_: object | None = None) -> None:
                canvas.configure(scrollregion=canvas.bbox("all"))

            def _resize_window(event: tk.Event[tk.Misc]) -> None:
                canvas.itemconfigure(canvas_window, width=event.width)

            wrapper.bind("<Configure>", _sync_scroll_region)
            canvas.bind("<Configure>", _resize_window)
            self._bind_mousewheel(canvas)

            top_bar = ttk.Frame(wrapper, padding=(4, 0, 4, 6))
            top_bar.pack(fill=tk.X)
            top_bar_message = ttk.Label(
                top_bar,
                text="Generate Skyrim-ready texture maps from one source image (single file or full folder batch).",
                justify=tk.LEFT,
                anchor=tk.W,
            )
            top_bar_message.pack(side=tk.LEFT, fill=tk.X, expand=True)
            _patreon_button = ttk.Button(
                top_bar,
                text="❤ Support on Patreon",
                command=lambda: webbrowser.open(PATREON_URL),
            )
            _patreon_button.pack(side=tk.RIGHT, padx=4)
            _help_button = ttk.Button(
                top_bar,
                text="Help",
                command=self._open_render_profile_help,
            )
            _help_button.pack(side=tk.RIGHT, padx=4)
            _theme_top_check = ttk.Checkbutton(
                top_bar,
                textvariable=self.theme_mode_label_var,
                variable=self.dark_mode_var,
                command=self._toggle_theme,
            )
            _theme_top_check.pack(side=tk.RIGHT, padx=(0, 8))
            self._add_tooltip(_theme_top_check, "🌙 Toggle dark/light mode.\nEasy on the eyes during those 3am modding sessions.")
            self._add_tooltip(
                _patreon_button,
                "❤ Fuel the project on Patreon.\n"
                "Your support buys bug-fixing time, feature upgrades, and enough caffeine to keep the texture goblin alive.",
            )
            self._add_tooltip(
                _help_button,
                "❓ Open the full renderer/channel guide.\n"
                "Includes ENB vs Community Shaders channel mappings and workflow do/don't notes.",
            )

            file_frame = ttk.LabelFrame(wrapper, text="Files", padding=10)
            file_frame.pack(fill=tk.X, padx=4, pady=4)

            _input_label = ttk.Label(file_frame, text="Input DDS or folder")
            _input_label.grid(row=0, column=0, sticky=tk.W, pady=4)
            self._add_tooltip(_input_label, "📂 Paste a .dds file path here, or use the buttons below.\nAKA: 'Where did I put that rock texture again?'")
            _input_entry = ttk.Entry(file_frame, textvariable=self.input_var, width=80)
            _input_entry.grid(row=0, column=1, padx=6, pady=4, sticky=tk.EW)
            self._add_tooltip(_input_entry, "📂 The sacred path to your source texture.\nTip: Drag & drop doesn't work here, use the buttons. Yes, I know. Sorry.")
            self.input_file_button = ttk.Button(file_frame, text="File", command=self._pick_input)
            self.input_file_button.grid(row=0, column=2, padx=4, pady=4)
            self._add_tooltip(self.input_file_button, "🗂 Open a single DDS/PNG/JPG texture.\nFor when you only have ONE texture and you're very proud of it.")
            self.input_folder_button = ttk.Button(file_frame, text="Folder", command=self._pick_input_folder)
            self.input_folder_button.grid(row=0, column=3, padx=4, pady=4)
            self._add_tooltip(self.input_folder_button, "📁 Select a whole folder of textures for MAXIMUM CHAOS.\nBatch mode: because doing things one at a time is for cowards.")
            self.detected_mod_button = ttk.Button(file_frame, text="Loaded Mod", command=self._pick_detected_mod_folder)
            self.detected_mod_button.grid(row=0, column=4, padx=4, pady=4)
            self._add_tooltip(self.detected_mod_button, "🧙 Auto-detected MO2/Vortex mod folder.\nIf this button is greyed out, your mod manager is playing hide and seek.")

            _output_label = ttk.Label(file_frame, text="Output folder")
            _output_label.grid(row=1, column=0, sticky=tk.W, pady=4)
            self._add_tooltip(_output_label, "📤 Where the generated textures will be deposited.\nDefault: same folder as input, so they're never far from home.")
            self.output_entry = ttk.Entry(file_frame, textvariable=self.output_var, width=80)
            self.output_entry.grid(row=1, column=1, padx=6, pady=4, sticky=tk.EW)
            self._add_tooltip(self.output_entry, "📤 Destination for generated files.\nLeave blank and outputs land right next to the source. Very tidy.")
            self.output_button = ttk.Button(file_frame, text="Browse", command=self._pick_output)
            self.output_button.grid(row=1, column=2, padx=4, pady=4)
            self._add_tooltip(self.output_button, "🗺 Browse for an output folder.\nOnly available when 'Use different output folder' is checked.")
            _custom_out_check = ttk.Checkbutton(
                file_frame,
                text="Use different output folder",
                variable=self.use_custom_output_var,
                command=self._toggle_custom_output_location,
            )
            _custom_out_check.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(2, 0))
            self._add_tooltip(_custom_out_check, "📦 Check this if you want your outputs somewhere other than the input folder.\nUseful when you have strong opinions about folder organisation.")
            _detected_context_label = ttk.Label(
                file_frame,
                textvariable=self.detected_context_var,
                foreground="gray",
                justify=tk.LEFT,
                anchor=tk.W,
            )
            _detected_context_label.grid(row=3, column=0, columnspan=5, sticky=tk.EW, pady=(4, 0))
            file_frame.columnconfigure(1, weight=1)
            self._update_output_location_controls()
            self.detected_mod_button.configure(
                state=(tk.NORMAL if self.manager_context.loaded_texture_dirs else tk.DISABLED)
            )

            options_frame = ttk.LabelFrame(wrapper, text="Generation Options", padding=10)
            options_frame.pack(fill=tk.X, padx=4, pady=4)

            _diffuse_check = ttk.Checkbutton(options_frame, text="Diffuse", variable=self.include_diffuse_var, command=self._refresh_preview)
            _diffuse_check.grid(row=0, column=0, sticky=tk.W)
            self._add_tooltip(_diffuse_check, "🎨 Generate the diffuse (colour) texture.\nThis is the one that makes your rock look like a rock and not a void of existential dread.")
            _normal_check = ttk.Checkbutton(options_frame, text="Normal / _n", variable=self.include_normal_var, command=self._refresh_preview)
            _normal_check.grid(row=0, column=1, sticky=tk.W)
            self._add_tooltip(_normal_check, "🗻 Generate a normal map for fake 3D depth.\nSkyrim's favourite optical illusion since 2011.")
            _parallax_check = ttk.Checkbutton(options_frame, text="Parallax / _p", variable=self.include_parallax_var, command=self._refresh_preview)
            _parallax_check.grid(row=0, column=2, sticky=tk.W)
            self._add_tooltip(_parallax_check, "🌊 Generate a parallax (height) map.\nMakes surfaces look EXTRA bumpy. Your GPU will feel it, but it's worth it.")
            _glow_check = ttk.Checkbutton(options_frame, text="Glow / _em", variable=self.include_glow_var, command=self._refresh_preview)
            _glow_check.grid(row=1, column=0, sticky=tk.W)
            self._add_tooltip(_glow_check, "✨ Generate a glow map. Bright pixels glow in the dark.\nPerfect for making your cave look like a disco.")
            _env_mask_check = ttk.Checkbutton(options_frame, text="Environment mask / _m", variable=self.include_environment_mask_var, command=self._refresh_preview)
            _env_mask_check.grid(row=1, column=1, sticky=tk.W)
            self._add_tooltip(_env_mask_check, "🪞 Generate an environment mask for reflections.\nTells Skyrim which parts of a surface are shiny. Science!")
            _rmaos_check = ttk.Checkbutton(options_frame, text="RMAOS / _rmaos", variable=self.include_rmaos_var, command=self._refresh_preview)
            _rmaos_check.grid(row=1, column=2, sticky=tk.W)
            self._add_tooltip(_rmaos_check, "🧩 Generate a dedicated TruePBR RMAOS map (_rmaos/_ramos) plus JSON sidecar in PBRNifPatcher/. Separate from vanilla/ENB _m environment masks.")
            _wetness_mask_check = ttk.Checkbutton(options_frame, text="Wetness mask / _wt", variable=self.include_wetness_mask_var, command=self._refresh_preview)
            _wetness_mask_check.grid(row=0, column=3, sticky=tk.W)
            self._add_tooltip(_wetness_mask_check, "Generate a Community Shaders wetness mask (_wt.dds). Like giving surfaces their own rain puddle detector — dark areas get wetter.")
            _snow_mask_check = ttk.Checkbutton(options_frame, text="Snow mask / _sm", variable=self.include_snow_mask_var, command=self._refresh_preview)
            _snow_mask_check.grid(row=1, column=3, sticky=tk.W)
            self._add_tooltip(_snow_mask_check, "Generate a Community Shaders Dynamic Snow mask (_sm.dds). Marks which pixels snow will accumulate on — bright = buried, dark = sheltered.")
            _complex_check = ttk.Checkbutton(options_frame, text="Complex/PBR material", variable=self.include_complex_var, command=self._refresh_preview)
            _complex_check.grid(row=1, column=4, sticky=tk.W)
            self._add_tooltip(
                _complex_check,
                "🔮 Generate complex/PBR material output.\n"
                "For Community Shaders Extended Materials use format 'cm' (or custom name ending with _c/_C).\n"
                "For ENB complex material workflows use format 'msn'.\n"
                "Do not mix Community Shaders and ENB outputs in the same install.",
            )
            _auto_sugg_check = ttk.Checkbutton(
                options_frame,
                text="Automatic suggestions (master switch: enables per-slider Auto toggles)",
                variable=self.auto_suggestions_var,
                command=self._toggle_auto_suggestions,
            )
            _auto_sugg_check.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(6, 2))
            self._add_tooltip(_auto_sugg_check, "🤖 Let the AI™ (actually just math) pick slider values.\nUncheck if you think YOU know better than the algorithm. Spoiler: maybe you do.")

            _render_profile_label = ttk.Label(options_frame, text="Target renderer")
            _render_profile_label.grid(row=3, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(
                _render_profile_label,
                "🎯 Select target renderer preset.\n"
                "custom = blank manual mode (nothing auto-enforced).\n"
                "vanilla = safest stock Skyrim SE setup.\n"
                "performance = low-end focused profile (lightweight defaults).\n"
                "vr = conservative VR-focused profile.\n"
                "terrain = conservative terrain-friendly defaults.\n"
                "architecture = wall/ruin-friendly defaults with slot-5 mask enabled.\n"
                "characters = skin/character-safe defaults with parallax disabled.\n"
                "community_shaders = Community Shaders Extended Materials _cm/_c/_C workflow.\n"
                "truepbr = Community Shaders TruePBR JSON-driven _rmaos/_ramos workflow.\n"
                "enb = tool ENB preset (_msn + complex env-mask mode + optional POM).\n"
                "Render-profile guidance is experimental and some info may be inaccurate for your setup.\n"
                "Community Shaders and ENB are separate workflows and should not be combined.\n"
                "Changing this is the only thing that should auto-switch the mode combos.",
            )
            _render_profile_combo = ttk.Combobox(
                options_frame,
                textvariable=self.render_profile_var,
                values=_RENDER_PROFILE_GUI_VALUES,
                state="readonly",
                width=20,
            )
            _render_profile_combo.grid(row=3, column=1, sticky=tk.W)
            _render_profile_combo.bind("<<ComboboxSelected>>", self._on_render_profile_changed)
            self._add_tooltip(
                _render_profile_combo,
                "🎯 custom = blank manual mode.\n"
                "auto = pick the best renderer preset for the current texture, but only when you change this control.\n"
                "vanilla = safest defaults; performance = lightweight defaults; vr = conservative VR defaults; "
                "terrain = stable terrain defaults; architecture = structure-focused defaults; characters = skin-safe defaults; "
                "community_shaders = _cm/_c/_C; truepbr = _n + _rmaos/_ramos + JSON path; enb = tool ENB preset _msn + complex env + optional POM.\n"
                "Render-profile guidance is experimental and may be inaccurate for some stacks.\n"
                "Community Shaders and ENB are separate workflows and should not be mixed.",
            )
            self.render_profile_hint_label = ttk.Label(
                options_frame,
                textvariable=self.render_profile_suggestion_var,
                foreground="gray",
                justify=tk.LEFT,
                anchor=tk.W,
                wraplength=520,
            )
            self.render_profile_hint_label.grid(row=3, column=2, columnspan=3, sticky=tk.EW, padx=(4, 0))

            _complex_fmt_label = ttk.Label(options_frame, text="Complex/PBR format")
            _complex_fmt_label.grid(row=4, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(
                _complex_fmt_label,
                "🏷 Output format for complex/PBR maps.\n"
                "'cm' = Community Shaders Extended Materials packed map (env reflection / glossiness / metallic / height; _cm default, _c/_C via custom name).\n"
                "'msn' = ENB complex material (normal RGB + specular alpha).",
            )
            complex_format = ttk.Combobox(
                options_frame,
                textvariable=self.complex_format_var,
                values=("msn", "cm"),
                state="readonly",
                width=20,
            )
            complex_format.grid(row=4, column=1, sticky=tk.W)
            complex_format.bind("<<ComboboxSelected>>", self._on_complex_format_changed)
            self._add_tooltip(
                complex_format,
                "🏷 Choose map type:\n"
                "'cm' for Community Shaders Extended Materials setups (_cm default, _c/_C optional via custom naming).\n"
                "'msn' for ENB complex material setups.\n"
                "Quick start for Community Shaders: set Target renderer=community_shaders, enable Complex/PBR material, keep format=cm.\n"
                "Do not use 'cm' together with ENB _m/_msn outputs.",
            )

            _env_mode_row = ttk.Frame(options_frame)
            _env_mode_row.grid(row=4, column=2, columnspan=2, sticky=tk.W, padx=(20, 4), pady=8)
            _env_mode_label = ttk.Label(_env_mode_row, text="Env mask mode")
            _env_mode_label.pack(side=tk.LEFT)
            self._add_tooltip(_env_mode_label, "🌍 How to encode the environment mask.\n'standard' = vanilla Skyrim.\n'complex' = packed RGBA (ENB and TruePBR interpret channels differently).\nUse Target renderer + Help for the correct channel mapping.")
            env_mask_mode_combo = ttk.Combobox(
                _env_mode_row,
                textvariable=self.env_mask_mode_var,
                values=("standard", "complex"),
                state="readonly",
                width=20,
            )
            env_mask_mode_combo.pack(side=tk.LEFT, padx=(6, 0))
            env_mask_mode_combo.bind("<<ComboboxSelected>>", self._on_env_mask_mode_changed)
            self._add_tooltip(env_mask_mode_combo, "🌍 'standard' = vanilla Skyrim SE reflections.\n'complex' = packed RGBA for ENB/TruePBR workflows.\nAlways match this with your selected Target renderer.")
            ttk.Label(
                options_frame,
                text="standard = vanilla Skyrim SE  |  complex = packed RGBA (renderer-specific channels)",
                foreground="gray",
            ).grid(row=4, column=4, sticky=tk.W, padx=(4, 0))

            _normal_label = ttk.Label(options_frame, text="Normal strength")
            _normal_label.grid(row=5, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(_normal_label, "💪 Controls normal-map intensity.\nHigher = sharper fake detail. Lower = smooth potato mode.")
            self.normal_scale = ttk.Scale(options_frame, from_=0.1, to=8.0, variable=self.normal_strength_var, command=lambda _: self._on_slider_changed())
            self.normal_scale.grid(row=5, column=1, columnspan=2, sticky=tk.EW)
            self._add_tooltip(self.normal_scale, "💪 Drag right for epic bumps, left for subtle detail.\nLive value is shown next to the slider so you can stop guessing.")
            self.normal_strength_display_label = ttk.Label(options_frame, textvariable=self.normal_strength_display_var)
            self.normal_strength_display_label.grid(row=5, column=3, sticky=tk.W, padx=8)
            self.auto_normal_check = ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_normal_suggestion_var, command=self._on_auto_slider_preference_changed)
            self.auto_normal_check.grid(row=5, column=4, sticky=tk.W)
            self._add_tooltip(self.auto_normal_check, "🤖 Let the app analyse the image and choose this value.\nUncheck to manually control, as the control freak you truly are.")

            _parallax_label = ttk.Label(options_frame, text="Parallax strength")
            _parallax_label.grid(row=6, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(_parallax_label, "🏔 Controls parallax depth contrast.\nToo high and your pebble becomes a canyon. Too low and your canyon becomes toast.")
            self.parallax_scale = ttk.Scale(options_frame, from_=0.1, to=6.0, variable=self.parallax_strength_var, command=lambda _: self._on_slider_changed())
            self.parallax_scale.grid(row=6, column=1, columnspan=2, sticky=tk.EW)
            self._add_tooltip(self.parallax_scale, "🏔 Slide right for deeper depth illusion, left for subtle relief.\nYes, this can absolutely make stones look dramatic.")
            self.parallax_strength_display_label = ttk.Label(options_frame, textvariable=self.parallax_strength_display_var)
            self.parallax_strength_display_label.grid(row=6, column=3, sticky=tk.W, padx=8)
            self.auto_parallax_check = ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_parallax_suggestion_var, command=self._on_auto_slider_preference_changed)
            self.auto_parallax_check.grid(row=6, column=4, sticky=tk.W)
            self._add_tooltip(self.auto_parallax_check, "🤖 Automatic parallax strength suggestion.\nBased on actual image analysis, not a horoscope.")

            _glow_label = ttk.Label(options_frame, text="Glow threshold")
            _glow_label.grid(row=7, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(_glow_label, "💡 Brightness cutoff for glow.\nLower = more glow. Higher = only brightest bits glow like tiny supernovas.")
            self.glow_scale = ttk.Scale(options_frame, from_=0, to=255, variable=self.glow_threshold_var, command=lambda _: self._on_slider_changed())
            self.glow_scale.grid(row=7, column=1, columnspan=2, sticky=tk.EW)
            self._add_tooltip(self.glow_scale, "💡 0 means everything glows like a rave. 255 means almost nothing glows.\nUse the live value display to tune precisely.")
            self.glow_threshold_display_label = ttk.Label(options_frame, textvariable=self.glow_threshold_display_var)
            self.glow_threshold_display_label.grid(row=7, column=3, sticky=tk.W, padx=8)
            self.auto_glow_check = ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_glow_suggestion_var, command=self._on_auto_slider_preference_changed)
            self.auto_glow_check.grid(row=7, column=4, sticky=tk.W)
            self._add_tooltip(self.auto_glow_check, "🤖 Auto-detect the ideal glow threshold.\nBased on luminance analysis. The computer is trying its best.")

            _env_mask_label = ttk.Label(options_frame, text="Environment mask strength")
            _env_mask_label.grid(row=8, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(_env_mask_label, "🪞 Controls environment-mask contrast.\nHigher = stronger shiny-vs-matte separation. Great for dramatic materials.")
            self.environment_mask_scale = ttk.Scale(options_frame, from_=0.1, to=8.0, variable=self.environment_mask_strength_var, command=lambda _: self._on_slider_changed())
            self.environment_mask_scale.grid(row=8, column=1, columnspan=2, sticky=tk.EW)
            self._add_tooltip(self.environment_mask_scale, "🪞 Slide right for stronger reflection contrast.\nSlide left for chill, less dramatic materials.")
            self.environment_mask_strength_display_label = ttk.Label(options_frame, textvariable=self.environment_mask_strength_display_var)
            self.environment_mask_strength_display_label.grid(row=8, column=3, sticky=tk.W, padx=8)
            self.auto_environment_mask_check = ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_environment_mask_suggestion_var, command=self._on_auto_slider_preference_changed)
            self.auto_environment_mask_check.grid(row=8, column=4, sticky=tk.W)
            self._add_tooltip(self.auto_environment_mask_check, "🤖 Auto-select environment mask strength.\nThe machine will judge your texture's reflective potential.")

            _rmaos_label = ttk.Label(options_frame, text="RMAOS strength")
            _rmaos_label.grid(row=9, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(_rmaos_label, "🧩 Controls TruePBR _rmaos channel contrast/intensity packing.")
            self.rmaos_scale = ttk.Scale(options_frame, from_=0.1, to=8.0, variable=self.rmaos_strength_var, command=lambda _: self._on_slider_changed())
            self.rmaos_scale.grid(row=9, column=1, columnspan=2, sticky=tk.EW)
            self._add_tooltip(self.rmaos_scale, "🧩 Higher values push stronger channel separation for _rmaos output.")
            self.rmaos_strength_display_label = ttk.Label(options_frame, textvariable=self.rmaos_strength_display_var)
            self.rmaos_strength_display_label.grid(row=9, column=3, sticky=tk.W, padx=8)
            self.auto_rmaos_check = ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_rmaos_suggestion_var, command=self._on_auto_slider_preference_changed)
            self.auto_rmaos_check.grid(row=9, column=4, sticky=tk.W)
            self._add_tooltip(self.auto_rmaos_check, "🤖 Auto-select dedicated RMAOS strength.")

            _complex_label = ttk.Label(options_frame, text="Complex strength")
            _complex_label.grid(row=10, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(_complex_label, "🔮 Controls complex-material contrast.\nHigher = punchier ENB material response. Lower = subtle, civilized vibes.")
            self.complex_scale = ttk.Scale(options_frame, from_=0.1, to=8.0, variable=self.complex_strength_var, command=lambda _: self._on_slider_changed())
            self.complex_scale.grid(row=10, column=1, columnspan=2, sticky=tk.EW)
            self._add_tooltip(self.complex_scale, "🔮 Right = louder material definition.\nLeft = quieter output for restrained legends.")
            self.complex_strength_display_label = ttk.Label(options_frame, textvariable=self.complex_strength_display_var)
            self.complex_strength_display_label.grid(row=10, column=3, sticky=tk.W, padx=8)
            self.auto_complex_check = ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_complex_suggestion_var, command=self._on_auto_slider_preference_changed)
            self.auto_complex_check.grid(row=10, column=4, sticky=tk.W)
            self._add_tooltip(self.auto_complex_check, "🤖 Auto-set complex strength. Let the algorithm\nscrutinise your texture's material complexity.")

            _specular_label = ttk.Label(options_frame, text="Specular strength (_msn alpha)")
            _specular_label.grid(row=11, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(_specular_label, "✨ Controls specular highlight intensity in _msn alpha.\nHigher = shinier. Lower = dusty realism.")
            self.specular_scale = ttk.Scale(options_frame, from_=0.1, to=8.0, variable=self.specular_strength_var, command=lambda _: self._on_slider_changed())
            self.specular_scale.grid(row=11, column=1, columnspan=2, sticky=tk.EW)
            self._add_tooltip(self.specular_scale, "✨ Turn it up for glorious shine, down for ancient weathered stone.\nLive value shown beside slider.")
            self.specular_strength_display_label = ttk.Label(options_frame, textvariable=self.specular_strength_display_var)
            self.specular_strength_display_label.grid(row=11, column=3, sticky=tk.W, padx=8)
            self.auto_specular_check = ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_specular_suggestion_var, command=self._on_auto_slider_preference_changed)
            self.auto_specular_check.grid(row=11, column=4, sticky=tk.W)
            self._add_tooltip(self.auto_specular_check, "🤖 Auto-set specular strength. The AI ponders how shiny\nyour texture DESERVES to be.")

            # --- Emboss depth + relief depth + parallax mode options ---
            _emboss_check = ttk.Checkbutton(
                options_frame,
                text="Emboss depth  (books, cards, scrolls — raises printed/artwork edges in normal map)",
                variable=self.emboss_mode_var,
                command=self._on_emboss_mode_changed,
            )
            _emboss_check.grid(row=12, column=0, columnspan=4, sticky=tk.W, pady=(8, 2))
            self._add_tooltip(
                _emboss_check,
                "📜 Emboss mode generates normals from edge ridges instead of smooth gradients.\n"
                "Perfect for books, notes, cards, posters — anything flat with printed detail.\n"
                "Uncheck for terrain, stone, wood, and any organic 3D surface.",
            )

            _relief_check = ttk.Checkbutton(
                options_frame,
                text="Relief depth  (paintings, murals, signs — subjects pop out as bas-relief)",
                variable=self.relief_mode_var,
                command=self._on_relief_mode_changed,
            )
            _relief_check.grid(row=12, column=4, columnspan=4, sticky=tk.W, pady=(8, 2))
            self._add_tooltip(
                _relief_check,
                "🖼 Relief mode uses the image's own luminosity as a height field so that\n"
                "bright subjects (painted figures, murals, heraldic signs, decorative plaques)\n"
                "physically protrude from the surface in game — a bas-relief effect.\n"
                "Takes priority over Emboss depth when both are enabled.\n"
                "Best combined with parallax output for full 3-D pop-out depth.",
            )

            _parallax_mode_label = ttk.Label(options_frame, text="Parallax mode")
            _parallax_mode_label.grid(row=13, column=0, sticky=tk.W, pady=(2, 8))
            self._add_tooltip(
                _parallax_mode_label,
                "🏔 Heightmap style for _p.dds output.\n"
                "'standard' = micro-detail for vanilla Skyrim SE parallax.\n"
                "'occlusion (ENB/POM)' = smooth gradient for ENBSeries Parallax Occlusion Mapping.",
            )
            _parallax_mode_combo = ttk.Combobox(
                options_frame,
                textvariable=self.parallax_mode_var,
                values=("standard", "occlusion (ENB/POM)"),
                state="readonly",
                width=24,
            )
            _parallax_mode_combo.grid(row=13, column=1, columnspan=2, sticky=tk.W)
            _parallax_mode_combo.bind("<<ComboboxSelected>>", self._on_parallax_mode_changed)
            self._add_tooltip(
                _parallax_mode_combo,
                "🏔 'standard' = vanilla/community-shaders-friendly heightmap for the normal Skyrim parallax setup.\n"
                "'occlusion (ENB/POM)' = ENB-only smooth POM heightmap — use this only when the mesh/material is actually set up for ENB parallax occlusion.",
            )
            ttk.Label(
                options_frame,
                text="standard = vanilla  |  occlusion = ENBSeries POM",
                foreground="gray",
            ).grid(row=13, column=3, columnspan=2, sticky=tk.W, padx=(4, 0))

            options_frame.columnconfigure(2, weight=1)
            options_frame.columnconfigure(3, weight=1)
            options_frame.columnconfigure(4, weight=1)
            self._update_slider_auto_states()

            actions = ttk.Frame(wrapper, padding=(4, 8, 4, 4))
            actions.pack(fill=tk.X)
            self.generate_button = ttk.Button(actions, text="Generate", command=self._generate)
            self.generate_button.pack(side=tk.LEFT)
            self._add_tooltip(self.generate_button, "🚀 ENGAGE! Click to process your textures.\nWARNING: May cause excitement, temporary CPU warming, and beautiful Skyrim textures.")
            self.cancel_button = ttk.Button(actions, text="Cancel Process", command=self._cancel_processing, state=tk.DISABLED)
            self.cancel_button.pack(side=tk.LEFT, padx=(6, 0))
            self._add_tooltip(self.cancel_button, "🛑 Ask the current batch to stop after the active file completes.\nUseful when you realize things have gone terribly wrong.")
            self.revert_button = ttk.Button(actions, text="Revert Process", command=self._revert_last_generation, state=tk.DISABLED)
            self.revert_button.pack(side=tk.LEFT, padx=(6, 0))
            self._add_tooltip(self.revert_button, "↩ Restore files from the most recent generation run.\nDisabled until a generation run has something to undo.")
            nif_editor_button = ttk.Button(actions, text="NIF Editor… [Experimental]", command=self._open_nif_editor)
            nif_editor_button.pack(side=tk.LEFT, padx=(12, 0))
            self._add_tooltip(
                nif_editor_button,
                "🔧 Open the NIF Editor window. ⚠ Experimental feature.\n"
                "Patch BSLightingShaderProperty flags and texture slots in Skyrim SE\n"
                "mesh files so mods that shipped without parallax/ENB support gain it.\n"
                "Always keep backups before patching NIFs.",
            )
            _status_label = ttk.Label(actions, textvariable=self.status_var, justify=tk.LEFT, anchor=tk.W)
            _status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=14)
            self._add_tooltip(
                _status_label,
                "📢 Live status feed.\n"
                "If something explodes, this line tells you what and where before panic mode fully activates.",
            )
            self._bind_responsive_wrap(top_bar, top_bar_message, horizontal_padding=260, min_wrap=220)
            self._bind_responsive_wrap(file_frame, _detected_context_label, horizontal_padding=28, min_wrap=220)
            self._bind_responsive_wrap(actions, _status_label, horizontal_padding=360, min_wrap=200)
            self._bind_responsive_wrap(options_frame, self.render_profile_hint_label, horizontal_padding=350, min_wrap=220)

            preview_frame = ttk.LabelFrame(wrapper, text="Preview (Source vs Generated)", padding=10)
            preview_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

            _before_title = ttk.Label(preview_frame, text="Before (source texture)", anchor=tk.CENTER, justify=tk.CENTER)
            _before_title.grid(
                row=0, column=0, columnspan=2, padx=6, pady=(2, 3), sticky=""
            )
            self._add_tooltip(
                _before_title,
                "🧾 Source preview header.\n"
                "This is your control sample — the 'before' shot before sliders and wizardry get involved.",
            )
            self.before_image_label = ttk.Label(preview_frame, text="No source loaded", anchor=tk.CENTER, justify=tk.CENTER)
            self.before_image_label.grid(row=2, column=0, columnspan=2, padx=6, pady=(0, 3), sticky="")
            self._add_tooltip(
                self.before_image_label,
                "👀 This is the original input texture.\n"
                "Use it as your baseline: if the generated maps look weird, compare here first before blaming your GPU, ENB, or moon phases.",
            )

            source_controls = ttk.Frame(preview_frame)
            source_controls.grid(row=1, column=0, columnspan=2, pady=(0, 4), sticky="")
            self.prev_source_button = ttk.Button(source_controls, text="◀ Prev", command=self._show_previous_preview_source)
            self.prev_source_button.pack(side=tk.LEFT, padx=4)
            self._add_tooltip(
                self.prev_source_button,
                "⏮ Show the previous source file in folder mode.\n"
                "Perfect for side-eyeing what your last texture looked like before your artistic decisions escalated.",
            )
            _source_name_label = ttk.Label(source_controls, textvariable=self.preview_source_name_var)
            _source_name_label.pack(side=tk.LEFT, padx=8)
            self._add_tooltip(
                _source_name_label,
                "🏷 Shows which source file you're previewing right now.\n"
                "In folder mode it's index/total, so you can keep your sanity during big batches.",
            )
            self.next_source_button = ttk.Button(source_controls, text="Next ▶", command=self._show_next_preview_source)
            self.next_source_button.pack(side=tk.LEFT, padx=4)
            self._add_tooltip(
                self.next_source_button,
                "⏭ Show the next source file in folder mode.\n"
                "Use this to QA your batch without opening fifty windows like a chaos wizard.",
            )
            _jump_label = ttk.Label(source_controls, text="Go to #")
            _jump_label.pack(side=tk.LEFT, padx=(12, 4))
            self._add_tooltip(
                _jump_label,
                "🔢 Jump directly to a source preview number (1-based).\n"
                "Example: type 12 and press Enter to jump to preview #12.",
            )
            self.preview_jump_entry = ttk.Entry(source_controls, textvariable=self.preview_jump_var, width=6)
            self.preview_jump_entry.pack(side=tk.LEFT, padx=(0, 4))
            self.preview_jump_entry.bind("<Return>", self._on_preview_jump_submit)
            self._add_tooltip(
                self.preview_jump_entry,
                "🔢 Type a preview index and press Enter.\n"
                "Valid range is 1 to total loaded previews.",
            )
            self.preview_jump_button = ttk.Button(source_controls, text="Go", command=self._jump_to_preview_source)
            self.preview_jump_button.pack(side=tk.LEFT, padx=(0, 4))
            self._add_tooltip(
                self.preview_jump_button,
                "🚀 Jump to the typed preview number.\n"
                "Great for large folders where clicking Next 200 times is cruel and unusual punishment.",
            )
            _preview_size_label = ttk.Label(source_controls, text="Preview size")
            _preview_size_label.pack(side=tk.LEFT, padx=(14, 4))
            self._add_tooltip(
                _preview_size_label,
                "📐 Controls preview thumbnail scale only (not output resolution).\n"
                "Large helps inspection; small helps fit more panes. Your exported DDS quality stays the same either way.",
            )
            preview_size_combo = ttk.Combobox(
                source_controls,
                textvariable=self.preview_size_var,
                values=tuple(PREVIEW_SIZE_PRESETS.keys()),
                state="readonly",
                width=10,
            )
            preview_size_combo.pack(side=tk.LEFT)
            preview_size_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_preview_size_changed())
            self._add_tooltip(
                preview_size_combo,
                "📐 XS→XL changes how big previews look in this window.\n"
                "It does NOT change generated file quality, but XL can make you feel like a very serious texture scientist.",
            )
            _batch_prev_check = ttk.Checkbutton(
                source_controls,
                text="Show preview during batch",
                variable=self.show_batch_preview_var,
                command=self._on_batch_preview_toggle,
            )
            _batch_prev_check.pack(side=tk.LEFT, padx=(14, 4))
            self._add_tooltip(
                _batch_prev_check,
                "🎬 Live preview while batch-processing.\n"
                "Heads-up: enabling this can slow processing, especially with big textures and huge folders.\n"
                "Default is OFF for speed; enable only when you want to watch the magic happen.",
            )
            _auto_patch_nifs_check = ttk.Checkbutton(
                source_controls,
                text="Auto-patch NIFs after generation [Experimental]",
                variable=self.auto_patch_nifs_var,
                command=self._on_auto_patch_nifs_toggle,
            )
            _auto_patch_nifs_check.pack(side=tk.LEFT, padx=(10, 4))
            self._add_tooltip(
                _auto_patch_nifs_check,
                "🔧 Automatically scan matching mesh/NIF files and patch them after textures are generated. ⚠ Experimental feature.\n"
                "Useful for mods that shipped without parallax, ENB POM, or complex parallax flags.\n"
                "The app will look in detected mod-manager mesh folders and nearby meshes folders.\n"
                "Off by default — always keep NIF backups before enabling.",
            )

            _generated_title = ttk.Label(preview_frame, text="Generated outputs (after processing)")
            _generated_title.grid(
                row=3, column=0, columnspan=2, padx=6, pady=(2, 2), sticky=""
            )
            self._add_tooltip(
                _generated_title,
                "🧪 These are previews of what will be written to disk.\n"
                "If one pane looks cursed, fix settings now instead of discovering it in-game three load screens later.",
            )
            self.preview_output_labels: dict[str, ttk.Label] = {}
            output_grid = ttk.Frame(preview_frame)
            output_grid.grid(row=4, column=0, columnspan=2, sticky="")
            output_specs = (
                ("diffuse", "Diffuse"),
                ("normal", "Normal"),
                ("parallax", "Parallax"),
                ("glow", "Glow"),
                ("environment_mask", "Environment Mask"),
                ("rmaos", "RMAOS"),
                ("wetness_mask", "Wetness Mask"),
                ("snow_mask", "Snow Mask"),
                ("complex_material", "Complex Material"),
            )
            _output_tooltips = {
                "diffuse": "🎨 Final colour/albedo preview.\nIf this looks off, every other map will inherit the drama. Start here.",
                "normal": "🗻 Normal-map preview (fake surface depth).\nBlue-purple space magic that tells light where bumps should pretend to exist.",
                "parallax": "🏔 Height/parallax preview.\nDarker = lower, lighter = higher. Think of it as tiny grayscale topography for your texture.",
                "glow": "✨ Emissive/glow preview.\nBright pixels glow in darkness; dark pixels mind their own business like respectable citizens.",
                "environment_mask": "🪞 Reflection mask preview.\nBrighter = shinier, darker = matte. Basically a \"where may I sparkle\" permit.",
                "rmaos": "🧩 TruePBR RMAOS preview (_rmaos/_ramos).\nPacked grayscale channels (not purple normal-map colors). Generator also writes a JSON sidecar in PBRNifPatcher/.",
                "wetness_mask": "🌧 Wetness-mask preview (_wt).\nDarker areas are more rain-friendly; lighter areas stay less puddly.",
                "snow_mask": "❄ Dynamic-snow mask preview (_sm).\nBrighter areas collect more snow; darker zones stay more sheltered.",
                "complex_material": "🔮 Complex-material preview.\nFor MSN format this pane is split: LEFT = RGB normal channels, RIGHT = alpha/specular channel.\nFor CM format it shows the packed texture directly. Not a bug — just advanced wizard math.",
            }
            for index, (output_key, output_label) in enumerate(output_specs):
                row = (index // 2) * 2
                column = index % 2
                _out_title = ttk.Label(output_grid, text=output_label, anchor=tk.CENTER, justify=tk.CENTER)
                _out_title.grid(row=row, column=column, padx=3, pady=(1, 0), sticky="")
                self._add_tooltip(_out_title, _output_tooltips.get(output_key, f"Preview of {output_label} output."))
                label = ttk.Label(output_grid, text="No preview", anchor=tk.CENTER, justify=tk.CENTER)
                label.grid(row=row + 1, column=column, padx=3, pady=(0, 2), sticky="")
                self._add_tooltip(label, _output_tooltips.get(output_key, f"Preview of {output_label} output."))
                self.preview_output_labels[output_key] = label

            preview_frame.columnconfigure(0, weight=1)
            preview_frame.columnconfigure(1, weight=1)
            output_grid.columnconfigure(0, weight=0)
            output_grid.columnconfigure(1, weight=0)
            self._update_preview_navigation_state()

            self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
            self._apply_theme()
            self._restore_startup_selection()

        def _set_app_icon(self) -> None:
            try:
                icon_image = _create_panda_icon_image(size=128)
                self._panda_icon_photo = ImageTk.PhotoImage(icon_image)
                self.root.iconphoto(True, self._panda_icon_photo)
            except Exception:
                pass

        def _bind_mousewheel(self, canvas: tk.Canvas) -> None:
            def _on_mousewheel(event: tk.Event[tk.Misc]) -> None:
                delta = 0
                if getattr(event, "delta", 0):
                    delta = -int(event.delta / 120)
                elif getattr(event, "num", None) == 4:
                    delta = -1
                elif getattr(event, "num", None) == 5:
                    delta = 1
                if delta:
                    canvas.yview_scroll(delta, "units")

            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            canvas.bind_all("<Button-4>", _on_mousewheel)
            canvas.bind_all("<Button-5>", _on_mousewheel)

        def _bind_responsive_wrap(
            self,
            container: tk.Widget,
            label: tk.Label | ttk.Label,
            *,
            horizontal_padding: int,
            min_wrap: int = 200,
        ) -> None:
            def _update_wrap(_: tk.Event[tk.Misc] | None = None) -> None:
                width = max(min_wrap, container.winfo_width() - horizontal_padding)
                label.configure(wraplength=width)

            container.bind("<Configure>", _update_wrap, add="+")
            self.root.after_idle(_update_wrap)

        def _add_tooltip(self, widget: tk.Widget, text: str) -> None:
            tip_window: list[tk.Toplevel | None] = [None]

            def _position_tip(tip: tk.Toplevel, pointer_x: int, pointer_y: int) -> None:
                tip.update_idletasks()
                x, y = _compute_tooltip_position(
                    pointer_x=pointer_x,
                    pointer_y=pointer_y,
                    tip_width=tip.winfo_reqwidth(),
                    tip_height=tip.winfo_reqheight(),
                    screen_width=self.root.winfo_screenwidth(),
                    screen_height=self.root.winfo_screenheight(),
                )
                tip.wm_geometry(f"+{x}+{y}")

            def _show(event: object) -> None:
                try:
                    tip = tip_window[0]
                    if tip is None:
                        tip = tk.Toplevel(widget)
                        tip.wm_overrideredirect(True)
                        try:
                            tip.wm_attributes("-topmost", True)
                        except Exception:
                            pass
                        label = tk.Label(
                            tip,
                            text=text,
                            justify=tk.LEFT,
                            background=self._tooltip_bg,
                            foreground=self._tooltip_fg,
                            relief=tk.SOLID,
                            borderwidth=1,
                            font=("TkDefaultFont", 9),
                            wraplength=340,
                            padx=6,
                            pady=4,
                        )
                        label.pack()
                        tip_window[0] = tip
                    pointer_x = int(getattr(event, "x_root", widget.winfo_pointerx()))
                    pointer_y = int(getattr(event, "y_root", widget.winfo_pointery()))
                    _position_tip(tip, pointer_x, pointer_y)
                except Exception:
                    tip_window[0] = None

            def _hide(_event: object) -> None:
                tip = tip_window[0]
                tip_window[0] = None
                if tip is not None:
                    try:
                        tip.destroy()
                    except Exception:
                        pass

            widget.bind("<Enter>", _show, add="+")
            widget.bind("<Motion>", _show, add="+")
            widget.bind("<Leave>", _hide, add="+")
            widget.bind("<ButtonPress>", _hide, add="+")

        def _apply_theme(self) -> None:
            colors = _DARK_THEME if self.dark_mode_var.get() else _LIGHT_THEME
            self._update_theme_toggle_text()
            self._tooltip_bg = colors["tooltip_bg"]
            self._tooltip_fg = colors["tooltip_fg"]
            style = ttk.Style(self.root)
            style.theme_use("clam")
            self.root.configure(background=colors["bg"])
            style.configure(".", background=colors["bg"], foreground=colors["fg"])
            style.configure("TFrame", background=colors["bg"])
            style.configure("TLabelFrame", background=colors["bg"], foreground=colors["fg"])
            style.configure("TLabelFrame.Label", background=colors["bg"], foreground=colors["fg"])
            style.configure("TLabel", background=colors["bg"], foreground=colors["fg"])
            style.configure("TButton", background=colors["button_bg"], foreground=colors["fg"])
            style.map("TButton", background=[("active", colors["trough"])])
            style.configure("TCheckbutton", background=colors["bg"], foreground=colors["fg"])
            style.map("TCheckbutton", background=[("active", colors["bg"])])
            style.configure("TCombobox", fieldbackground=colors["field_bg"], foreground=colors["fg"], background=colors["button_bg"])
            style.map("TCombobox", fieldbackground=[("readonly", colors["field_bg"])], foreground=[("readonly", colors["fg"])])
            style.configure("TEntry", fieldbackground=colors["field_bg"], foreground=colors["fg"])
            style.configure("Horizontal.TScale", background=colors["bg"], troughcolor=colors["trough"])
            style.configure("Manual.Horizontal.TScale", background=colors["bg"], troughcolor=colors["trough"])
            style.configure("Auto.Horizontal.TScale", background=colors["bg"], troughcolor=colors["auto_trough"])
            style.configure("TScrollbar", background=colors["button_bg"], troughcolor=colors["bg"])
            style.map("TScrollbar", background=[("active", colors["trough"])])
            self._update_slider_auto_states()

        def _toggle_theme(self) -> None:
            self._apply_theme()
            theme_name = "dark" if self.dark_mode_var.get() else "light"
            self.status_var.set(f"Switched to {theme_name} mode.")

        def _update_theme_toggle_text(self) -> None:
            self.theme_mode_label_var.set("🌙 Dark mode" if self.dark_mode_var.get() else "☀ Light mode")

        def _apply_persisted_gui_state(self) -> None:
            state = load_gui_state()
            self.input_var.set(str(state["input_path"]))
            self.output_var.set(str(state["output_path"]))
            self.use_custom_output_var.set(bool(state["use_custom_output"]))
            self.dark_mode_var.set(bool(state["dark_mode"]))
            self.show_batch_preview_var.set(bool(state["show_batch_preview"]))
            self.auto_patch_nifs_var.set(bool(state["auto_patch_nifs"]))
            self.preview_size_var.set(str(state["preview_size"]))
            self.complex_format_var.set(str(state["complex_format"]))
            self.env_mask_mode_var.set(str(state["env_mask_mode"]))
            self.parallax_mode_var.set(str(state["parallax_mode"]))
            self.render_profile_var.set(str(state["render_profile"]))
            self.emboss_mode_var.set(bool(state["emboss_mode"]))
            self.relief_mode_var.set(bool(state["relief_mode"]))
            self.include_diffuse_var.set(bool(state["include_diffuse"]))
            self.include_normal_var.set(bool(state["include_normal"]))
            self.include_parallax_var.set(bool(state["include_parallax"]))
            self.include_glow_var.set(bool(state["include_glow"]))
            self.include_environment_mask_var.set(bool(state["include_environment_mask"]))
            self.include_rmaos_var.set(bool(state["include_rmaos"]))
            self.include_wetness_mask_var.set(bool(state["include_wetness_mask"]))
            self.include_snow_mask_var.set(bool(state["include_snow_mask"]))
            self.include_complex_var.set(bool(state["include_complex"]))
            self.auto_suggestions_var.set(bool(state["auto_suggestions"]))
            self.auto_normal_suggestion_var.set(bool(state["auto_normal"]))
            self.auto_parallax_suggestion_var.set(bool(state["auto_parallax"]))
            self.auto_glow_suggestion_var.set(bool(state["auto_glow"]))
            self.auto_environment_mask_suggestion_var.set(bool(state["auto_environment_mask"]))
            self.auto_rmaos_suggestion_var.set(bool(state["auto_rmaos"]))
            self.auto_complex_suggestion_var.set(bool(state["auto_complex"]))
            self.auto_specular_suggestion_var.set(bool(state["auto_specular"]))
            self.normal_strength_var.set(float(state["normal_strength"]))
            self.parallax_strength_var.set(float(state["parallax_strength"]))
            self.glow_threshold_var.set(float(state["glow_threshold"]))
            self.environment_mask_strength_var.set(float(state["environment_mask_strength"]))
            self.rmaos_strength_var.set(float(state["rmaos_strength"]))
            self.complex_strength_var.set(float(state["complex_strength"]))
            self.specular_strength_var.set(float(state["specular_strength"]))
            dismissed = state.get("dismissed_warnings", [])
            if isinstance(dismissed, list):
                self.dismissed_warnings = set(str(w) for w in dismissed if isinstance(w, str))

        def _build_gui_state(self) -> dict[str, object]:
            return {
                "input_path": self.input_var.get().strip(),
                "output_path": self.output_var.get().strip(),
                "use_custom_output": self.use_custom_output_var.get(),
                "dark_mode": self.dark_mode_var.get(),
                "show_batch_preview": self.show_batch_preview_var.get(),
                "auto_patch_nifs": self.auto_patch_nifs_var.get(),
                "preview_size": self.preview_size_var.get(),
                "complex_format": self.complex_format_var.get(),
                "env_mask_mode": self.env_mask_mode_var.get(),
                "parallax_mode": self.parallax_mode_var.get(),
                "render_profile": _normalize_render_profile(self.render_profile_var.get()),
                "emboss_mode": self.emboss_mode_var.get(),
                "relief_mode": self.relief_mode_var.get(),
                "include_diffuse": self.include_diffuse_var.get(),
                "include_normal": self.include_normal_var.get(),
                "include_parallax": self.include_parallax_var.get(),
                "include_glow": self.include_glow_var.get(),
                "include_environment_mask": self.include_environment_mask_var.get(),
                "include_rmaos": self.include_rmaos_var.get(),
                "include_wetness_mask": self.include_wetness_mask_var.get(),
                "include_snow_mask": self.include_snow_mask_var.get(),
                "include_complex": self.include_complex_var.get(),
                "auto_suggestions": self.auto_suggestions_var.get(),
                "auto_normal": self.auto_normal_suggestion_var.get(),
                "auto_parallax": self.auto_parallax_suggestion_var.get(),
                "auto_glow": self.auto_glow_suggestion_var.get(),
                "auto_environment_mask": self.auto_environment_mask_suggestion_var.get(),
                "auto_rmaos": self.auto_rmaos_suggestion_var.get(),
                "auto_complex": self.auto_complex_suggestion_var.get(),
                "auto_specular": self.auto_specular_suggestion_var.get(),
                "normal_strength": self.normal_strength_var.get(),
                "parallax_strength": self.parallax_strength_var.get(),
                "glow_threshold": int(round(self.glow_threshold_var.get())),
                "environment_mask_strength": self.environment_mask_strength_var.get(),
                "rmaos_strength": self.rmaos_strength_var.get(),
                "complex_strength": self.complex_strength_var.get(),
                "specular_strength": self.specular_strength_var.get(),
                "dismissed_warnings": sorted(self.dismissed_warnings),
            }

        def _save_persisted_gui_state(self) -> None:
            save_gui_state(self._build_gui_state())

        def _on_window_close(self) -> None:
            self._save_persisted_gui_state()
            self.root.destroy()

        def _restore_startup_selection(self) -> None:
            saved_input = self.input_var.get().strip()
            if not saved_input:
                return
            saved_input_path = Path(saved_input)
            if not saved_input_path.exists():
                self.input_var.set("")
                self.status_var.set(f"Saved input path not found: {saved_input_path}")
                return
            self._load_input_selection(saved_input_path, show_error=False)

        def _pick_input(self) -> None:
            selected = filedialog.askopenfilename(
                title="Select input texture",
                filetypes=[("Texture files", "*.dds *.png *.jpg *.jpeg *.tga *.bmp"), ("All files", "*.*")],
                initialdir=str(self._default_input_browse_dir()),
            )
            if not selected:
                return
            self.input_var.set(selected)
            self._load_input_selection(Path(selected))

        def _pick_input_folder(self) -> None:
            selected = filedialog.askdirectory(
                title="Select folder with source DDS textures",
                initialdir=str(self._default_input_browse_dir()),
            )
            if not selected:
                return
            self.input_var.set(selected)
            self._load_input_selection(Path(selected))

        def _pick_detected_mod_folder(self) -> None:
            if not self.manager_context.loaded_texture_dirs:
                messagebox.showinfo(
                    "No detected mod folders",
                    "No loaded mod texture folders were detected for the current MO2/Vortex context.",
                )
                return
            if len(self.manager_context.loaded_texture_dirs) == 1:
                selected_path = self.manager_context.loaded_texture_dirs[0]
            else:
                selected = filedialog.askdirectory(
                    title="Select detected loaded mod texture folder",
                    initialdir=str(self._default_input_browse_dir()),
                )
                if not selected:
                    return
                selected_path = Path(selected)
            self.input_var.set(str(selected_path))
            self._load_input_selection(selected_path)

        def _pick_output(self) -> None:
            if not self.use_custom_output_var.get():
                return
            selected = filedialog.askdirectory(
                title="Select output folder",
                initialdir=str(self._default_output_browse_dir()),
            )
            if selected:
                self.output_var.set(selected)

        def _default_output_dir_for_path(self, path: Path) -> Path:
            return path if path.is_dir() else path.parent

        def _default_input_browse_dir(self) -> Path:
            if self.last_input_browse_dir is not None and self.last_input_browse_dir.exists():
                return self.last_input_browse_dir
            input_value = self.input_var.get().strip()
            if input_value:
                candidate = Path(input_value)
                resolved = candidate if candidate.is_dir() else candidate.parent
                if resolved.exists():
                    return resolved
            if self.manager_context.loaded_texture_dirs:
                candidate = self.manager_context.loaded_texture_dirs[0]
                if candidate.exists():
                    return candidate
            if self.manager_context.instance_root is not None:
                candidate = self.manager_context.instance_root / "mods"
                if candidate.exists():
                    return candidate
            if self.manager_context.staging_root is not None:
                candidate = self.manager_context.staging_root
                if candidate.exists():
                    return candidate
            return Path.cwd()

        def _default_output_browse_dir(self) -> Path:
            if self.output_var.get().strip():
                candidate = Path(self.output_var.get().strip())
                if candidate.exists():
                    return candidate
            if self.manager_context.output_dir is not None:
                candidate = self.manager_context.output_dir
                if candidate.exists():
                    return candidate
            input_value = self.input_var.get().strip()
            if input_value:
                candidate = self._default_output_dir_for_path(Path(input_value))
                if candidate.exists():
                    return candidate
            return Path.cwd()

        def _update_output_location_controls(self) -> None:
            is_custom = self.use_custom_output_var.get()
            can_edit = is_custom and not self.is_processing
            self.output_entry.configure(state="normal" if can_edit else "disabled")
            self.output_button.configure(state=tk.NORMAL if can_edit else tk.DISABLED)

        def _toggle_custom_output_location(self) -> None:
            input_value = self.input_var.get().strip()
            if self.use_custom_output_var.get():
                if not self.output_var.get().strip():
                    if self.manager_context.output_dir is not None:
                        self.output_var.set(str(self.manager_context.output_dir))
                    elif input_value:
                        input_path = Path(input_value)
                        self.output_var.set(str(self._default_output_dir_for_path(input_path)))
                self.status_var.set("Custom output folder enabled.")
            else:
                if input_value:
                    input_path = Path(input_value)
                    self.output_var.set(str(self._default_output_dir_for_path(input_path)))
                self.status_var.set("Output will be written next to the input.")
            self._update_output_location_controls()

        def _load_input_selection(self, path: Path, *, show_error: bool = True) -> None:
            try:
                input_files = collect_source_textures(path)
                self.selected_inputs = input_files
                self.last_input_browse_dir = path if path.is_dir() else path.parent
                self.current_preview_index = 0
                self.emboss_mode_manual_override = False
                self.relief_mode_manual_override = False
                self._set_preview_source(0, apply_recommendations=True)
                if self.render_profile_var.get() not in {"auto", "custom"}:
                    self._apply_render_profile_modes(self.render_profile_var.get(), apply_preset=False)
                if not self.use_custom_output_var.get():
                    self.output_var.set(str(self._default_output_dir_for_path(path)))
                preview_path = self.selected_inputs[self.current_preview_index]
                if path.is_dir():
                    self.status_var.set(
                        f"Loaded {len(input_files)} source DDS file(s) from {path.name}. Previewing {preview_path.name}."
                    )
                elif self.auto_suggestions_var.get():
                    self.status_var.set(f"Loaded: {path.name} (automatic suggestions applied)")
                else:
                    self.status_var.set(f"Loaded: {path.name} (automatic suggestions off)")
                self._refresh_preview()
            except Exception as exc:
                self.source_image = None
                self.selected_inputs = []
                self.current_preview_index = 0
                self.preview_source_name_var.set("No source loaded")
                self.preview_jump_var.set("")
                self._update_preview_navigation_state()
                if show_error:
                    messagebox.showerror("Unable to open texture", str(exc))
                else:
                    self.status_var.set(f"Unable to restore saved input: {exc}")

        def _resolve_generation_value(self, current_value: float | int, auto_var: tk.BooleanVar) -> float | int | None:
            if self.auto_suggestions_var.get() and auto_var.get():
                return None
            return current_value

        def _set_processing_state(self, processing: bool) -> None:
            self.is_processing = processing
            state = tk.DISABLED if processing else tk.NORMAL
            self.generate_button.configure(state=state)
            self.input_file_button.configure(state=state)
            self.input_folder_button.configure(state=state)
            self.cancel_button.configure(state=(tk.NORMAL if processing else tk.DISABLED))
            self.revert_button.configure(
                state=(tk.DISABLED if processing or not self._has_revert_snapshot() else tk.NORMAL)
            )
            self.detected_mod_button.configure(
                state=(tk.DISABLED if processing or not self.manager_context.loaded_texture_dirs else tk.NORMAL)
            )
            self._update_output_location_controls()
            self._update_preview_navigation_state()

        def _has_revert_snapshot(self) -> bool:
            return bool(self.last_generation_backups or self.last_generation_created_files)

        def _discard_last_generation_snapshot(self) -> None:
            if self.last_generation_backup_dir is not None:
                shutil.rmtree(self.last_generation_backup_dir, ignore_errors=True)
            self.last_generation_backup_dir = None
            self.last_generation_backups = {}
            self.last_generation_created_files = set()

        def _prepare_generation_snapshot(self, input_files: list[Path], generation_kwargs: dict[str, object]) -> None:
            self._discard_last_generation_snapshot()
            backup_dir = Path(tempfile.mkdtemp(prefix="skyrim-texture-revert-"))
            backups: dict[Path, Path] = {}
            includes = {
                "diffuse": bool(generation_kwargs["include_diffuse"]),
                "normal": bool(generation_kwargs["include_normal"]),
                "parallax": bool(generation_kwargs["include_parallax"]),
                "glow": bool(generation_kwargs["include_glow"]),
                "environment_mask": bool(generation_kwargs["include_environment_mask"]),
                "rmaos": bool(generation_kwargs["include_rmaos"]),
                "complex_material": bool(generation_kwargs["include_complex"]),
            }
            for input_file in input_files:
                expected_paths: list[Path] = []
                if includes["diffuse"]:
                    diffuse_path, _ = build_output_paths(
                        input_path=input_file,
                        output_dir=generation_kwargs["output_dir"],
                        diffuse_name=generation_kwargs.get("diffuse_name"),
                        parallax_name=generation_kwargs.get("parallax_name"),
                    )
                    expected_paths.append(diffuse_path)
                if includes["normal"]:
                    expected_paths.append(
                        build_normal_output_path(
                            input_path=input_file,
                            output_dir=generation_kwargs["output_dir"],
                        )
                    )
                if includes["parallax"]:
                    _, parallax_path = build_output_paths(
                        input_path=input_file,
                        output_dir=generation_kwargs["output_dir"],
                        diffuse_name=generation_kwargs.get("diffuse_name"),
                        parallax_name=generation_kwargs.get("parallax_name"),
                    )
                    expected_paths.append(parallax_path)
                if includes["glow"]:
                    expected_paths.append(
                        build_glow_output_path(
                            input_path=input_file,
                            output_dir=generation_kwargs["output_dir"],
                        )
                    )
                if includes["environment_mask"]:
                    expected_paths.append(
                        build_environment_mask_output_path(
                            input_path=input_file,
                            output_dir=generation_kwargs["output_dir"],
                            env_mask_mode=str(generation_kwargs.get("env_mask_mode", "standard")),
                            complex_format=str(generation_kwargs.get("complex_format", "msn")),
                            render_profile=str(generation_kwargs.get("render_profile", "auto")),
                            include_complex=bool(generation_kwargs.get("include_complex", False)),
                        )
                    )
                if includes["complex_material"]:
                    expected_paths.append(
                        build_complex_output_path(
                            input_path=input_file,
                            output_dir=generation_kwargs["output_dir"],
                            complex_format=str(generation_kwargs["complex_format"]),
                        )
                    )
                if includes["rmaos"]:
                    expected_paths.append(
                        build_rmaos_output_path(
                            input_path=input_file,
                            output_dir=generation_kwargs["output_dir"],
                        )
                    )
                for candidate in expected_paths:
                    for possible in (candidate, candidate.with_suffix(".png"), candidate.with_suffix(".json")):
                        if not possible.exists() or possible in backups:
                            continue
                        backup_target = backup_dir / f"{len(backups):05d}{possible.suffix}"
                        backup_target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(possible, backup_target)
                        backups[possible] = backup_target
            self.last_generation_backup_dir = backup_dir
            self.last_generation_backups = backups
            self.last_generation_created_files = set()

        def _record_created_outputs(self, output_paths: list[Path]) -> None:
            for output_path in output_paths:
                if output_path not in self.last_generation_backups:
                    self.last_generation_created_files.add(output_path)

        def _process_generation_batch(self, input_files: list[Path], generation_kwargs: dict[str, object]) -> None:
            try:
                total = len(input_files)
                results: dict[Path, dict[str, Path]] = {}
                generation_call_kwargs = dict(generation_kwargs)
                auto_patch_nifs = bool(generation_call_kwargs.pop("auto_patch_nifs", False))
                manager_context = generation_call_kwargs.pop("manager_context", None)
                for index, input_file in enumerate(input_files, start=1):
                    if self.cancel_requested:
                        self.processing_queue.put(("cancelled", results))
                        return
                    self.processing_queue.put(("progress", (index, total, input_file)))
                    try:
                        outputs = run_with_options(
                            input_file=input_file,
                            **generation_call_kwargs,
                        )
                        if auto_patch_nifs:
                            nif_patch_results = auto_patch_related_nifs_for_texture(
                                input_file,
                                outputs,
                                output_dir=generation_call_kwargs.get("output_dir"),
                                manager_context=manager_context if isinstance(manager_context, ModManagerContext) else None,
                                complex_format=str(generation_call_kwargs.get("complex_format", "msn")),
                                env_mask_mode=str(generation_call_kwargs.get("env_mask_mode", "standard")),
                                parallax_mode=str(generation_call_kwargs.get("parallax_mode", "standard")),
                                render_profile=str(generation_call_kwargs.get("render_profile", "auto")),
                                parallax_scale=_map_parallax_strength_to_nif_scale(
                                    float(generation_call_kwargs.get("parallax_strength", 1.35) or 1.35)
                                ),
                                include_parallax=bool(generation_call_kwargs.get("include_parallax", True)),
                                include_environment_mask=bool(
                                    generation_call_kwargs.get("include_environment_mask", False)
                                    or generation_call_kwargs.get("include_rmaos", False)
                                ),
                            )
                            patched = sum(1 for result in nif_patch_results if getattr(result, "success", False))
                            failed = sum(1 for result in nif_patch_results if not getattr(result, "success", False))
                            self.processing_queue.put(("nif_patch", (input_file.name, patched, failed)))
                        results[input_file] = outputs
                        self._record_created_outputs(list(outputs.values()))
                    except Exception as exc:
                        self.processing_queue.put(("file_error", (index, total, input_file.name, str(exc))))
                self.processing_queue.put(("done", results))
            except Exception as exc:
                self.processing_queue.put(("error", str(exc)))

        def _poll_processing_queue(self) -> None:
            keep_polling = self.is_processing
            processed_events = 0
            max_events_per_poll = 32
            while True:
                if processed_events >= max_events_per_poll:
                    break
                try:
                    event_type, payload = self.processing_queue.get_nowait()
                except queue.Empty:
                    break
                processed_events += 1

                if event_type == "progress":
                    index, total, current_path = payload
                    self.status_var.set(f"Processing {index}/{total}: {current_path.name}")
                    if self.show_batch_preview_var.get():
                        self._set_preview_source_by_path(current_path)
                elif event_type == "nif_patch":
                    filename, patched, failed = payload
                    self.batch_nif_patch_results.append((filename, patched, failed))
                    if patched or failed:
                        self.status_var.set(
                            f"Patched related NIFs for {filename}: {patched} succeeded, {failed} failed."
                        )
                elif event_type == "file_error":
                    index, total, filename, error_message = payload
                    self.batch_failures.append((filename, error_message))
                    self.status_var.set(f"Skipped failed file {index}/{total}: {filename}")
                elif event_type == "done":
                    results = payload
                    self._set_processing_state(False)
                    total_sources = len(results)
                    total_failed = len(self.batch_failures)
                    total_outputs = sum(len(output_set) for output_set in results.values())
                    self.status_var.set(
                        f"Generated {total_outputs} file(s) from {total_sources} source texture(s). "
                        f"Failed: {total_failed}."
                    )
                    if total_sources == 1 and total_failed == 0:
                        only_outputs = next(iter(results.values()))
                        lines = [f"{key.replace('_', ' ').title()}: {value}" for key, value in only_outputs.items()]
                    else:
                        lines = [
                            f"Processed {total_sources} source textures.",
                            f"Generated {total_outputs} files.",
                        ]
                        total_nif_patched = sum(patched for _, patched, _ in self.batch_nif_patch_results)
                        total_nif_failed = sum(failed for _, _, failed in self.batch_nif_patch_results)
                        if total_nif_patched or total_nif_failed:
                            lines.append(
                                f"Automatic NIF patching: {total_nif_patched} succeeded, {total_nif_failed} failed."
                            )
                        if total_failed:
                            lines.append(f"Skipped {total_failed} failed source texture(s).")
                            for filename, error_message in self.batch_failures[:5]:
                                lines.append(f"- {filename}: {error_message}")
                            if total_failed > 5:
                                lines.append(f"...and {total_failed - 5} more.")
                    messagebox.showinfo("Generation complete", "\n".join(lines), parent=self.root)
                    self._refresh_preview()
                    keep_polling = False
                elif event_type == "cancelled":
                    results = payload
                    self._set_processing_state(False)
                    total_sources = len(results)
                    total_outputs = sum(len(output_set) for output_set in results.values())
                    self.status_var.set(
                        f"Generation cancelled. Finished {total_sources} source texture(s) and wrote {total_outputs} file(s)."
                    )
                    messagebox.showinfo(
                        "Generation cancelled",
                        "Processing was cancelled.\nUse Revert Process to undo files from this run if needed.",
                        parent=self.root,
                    )
                    keep_polling = False
                elif event_type == "error":
                    self._set_processing_state(False)
                    messagebox.showerror("Generation failed", str(payload), parent=self.root)
                    keep_polling = False

            if keep_polling and self.is_processing:
                self.root.after(100, self._poll_processing_queue)

        def _toggle_auto_suggestions(self) -> None:
            if self.auto_suggestions_var.get():
                self._set_all_auto_slider_flags(True)
                self._apply_recommended_settings()
                if self.source_image is not None:
                    self.status_var.set("Automatic suggestions enabled for all sliders. Uncheck any Auto box to keep manual values.")
            else:
                self._set_all_auto_slider_flags(False)
                self.status_var.set("Automatic suggestions disabled. Per-slider Auto toggles are now off; all sliders are manual.")
            self._update_slider_auto_states()
            self._refresh_preview()

        def _on_slider_changed(self) -> None:
            self._update_slider_value_labels()
            self._request_preview_refresh()

        def _update_slider_value_labels(self) -> None:
            auto_all = self.auto_suggestions_var.get()

            def _fmt(value: str, auto_var: tk.BooleanVar) -> str:
                return f"{value}  ◉ AUTO" if (auto_all and auto_var.get()) else value

            self.normal_strength_display_var.set(_fmt(f"{float(self.normal_strength_var.get()):.2f} (0.1–8.0)", self.auto_normal_suggestion_var))
            self.parallax_strength_display_var.set(_fmt(f"{float(self.parallax_strength_var.get()):.2f} (0.1–6.0)", self.auto_parallax_suggestion_var))
            self.glow_threshold_display_var.set(_fmt(f"{int(round(self.glow_threshold_var.get()))} (0–255)", self.auto_glow_suggestion_var))
            self.environment_mask_strength_display_var.set(
                _fmt(f"{float(self.environment_mask_strength_var.get()):.2f} (0.1–8.0)", self.auto_environment_mask_suggestion_var)
            )
            self.rmaos_strength_display_var.set(
                _fmt(f"{float(self.rmaos_strength_var.get()):.2f} (0.1–8.0)", self.auto_rmaos_suggestion_var)
            )
            self.complex_strength_display_var.set(_fmt(f"{float(self.complex_strength_var.get()):.2f} (0.1–8.0)", self.auto_complex_suggestion_var))
            self.specular_strength_display_var.set(_fmt(f"{float(self.specular_strength_var.get()):.2f} (0.1–8.0)", self.auto_specular_suggestion_var))

        def _on_batch_preview_toggle(self) -> None:
            if self.show_batch_preview_var.get():
                self.status_var.set("Batch live preview enabled. Heads-up: this can slow processing on large batches.")
            else:
                self.status_var.set("Batch live preview disabled for faster processing.")

        def _on_auto_patch_nifs_toggle(self) -> None:
            if self.auto_patch_nifs_var.get():
                self.status_var.set(
                    "Auto NIF patching enabled [Experimental]. Matching meshes will be patched for parallax/complex workflows after generation. Keep backups!"
                )
            else:
                self.status_var.set("Auto NIF patching disabled.")

        def _set_all_auto_slider_flags(self, value: bool) -> None:
            self.auto_normal_suggestion_var.set(value)
            self.auto_parallax_suggestion_var.set(value)
            self.auto_glow_suggestion_var.set(value)
            self.auto_environment_mask_suggestion_var.set(value)
            self.auto_rmaos_suggestion_var.set(value)
            self.auto_complex_suggestion_var.set(value)
            self.auto_specular_suggestion_var.set(value)
            self._update_slider_auto_states()

        def _on_auto_slider_preference_changed(self) -> None:
            self._update_slider_auto_states()
            if self.auto_suggestions_var.get():
                self._apply_recommended_settings()
                self.status_var.set("Automatic suggestions updated for checked sliders.")
            self._request_preview_refresh()

        def _on_emboss_mode_changed(self) -> None:
            self.emboss_mode_manual_override = True
            if self.emboss_mode_var.get():
                self.status_var.set("Emboss depth mode enabled for flat printed surfaces (books/cards/scrolls).")
            else:
                self.status_var.set("Emboss depth mode disabled; using standard normal-map generation.")
            self._request_preview_refresh()

        def _on_relief_mode_changed(self) -> None:
            self.relief_mode_manual_override = True
            if self.relief_mode_var.get():
                self.status_var.set("Relief depth mode enabled — paintings/signs/murals will pop out as bas-relief.")
            else:
                self.status_var.set("Relief depth mode disabled; using standard normal-map generation.")
            self._request_preview_refresh()

        def _current_preview_path(self) -> Path | None:
            if not self.selected_inputs:
                return None
            return self.selected_inputs[max(0, min(self.current_preview_index, len(self.selected_inputs) - 1))]

        def _recommended_render_profile_for_preview(self, preview_path: Path | None) -> str:
            if preview_path is None:
                return "vanilla"
            role_info = identify_skyrim_texture_role(preview_path)
            workflow_profile = detect_workflow_profile(preview_path)
            material_type = classify_material_type(preview_path)
            return recommend_render_profile(
                preview_path,
                source=self.source_image,
                detected_role=role_info["role"] if role_info is not None else None,
                detected_suffix=role_info["suffix"] if role_info is not None else None,
                material_type=material_type,
                workflow_profile=workflow_profile,
                manager_context=self.manager_context,
            )

        def _apply_render_profile_modes(
            self,
            selected_profile: str,
            recommended_profile: str | None = None,
            *,
            apply_preset: bool,
        ) -> str:
            resolved = resolve_render_profile_mode_selection(
                {
                    "complex_format": self.complex_format_var.get(),
                    "env_mask_mode": self.env_mask_mode_var.get(),
                    "parallax_mode": self.parallax_mode_var.get(),
                },
                selected_profile=selected_profile,
                recommended_profile=recommended_profile,
                apply_preset=apply_preset,
            )
            self.complex_format_var.set(resolved["complex_format"])
            self.env_mask_mode_var.set(resolved["env_mask_mode"])
            self.parallax_mode_var.set(
                "occlusion (ENB/POM)" if resolved["parallax_mode"] == "occlusion" else "standard"
            )
            return resolved["effective_profile"]

        def _apply_render_profile_output_toggles(
            self,
            selected_profile: str,
            recommended_profile: str | None = None,
        ) -> str:
            resolved = resolve_render_profile_output_defaults(
                selected_profile,
                recommended_profile=recommended_profile,
            )
            self.include_diffuse_var.set(bool(resolved["include_diffuse"]))
            self.include_normal_var.set(bool(resolved["include_normal"]))
            self.include_parallax_var.set(bool(resolved["include_parallax"]))
            self.include_glow_var.set(bool(resolved["include_glow"]))
            self.include_environment_mask_var.set(bool(resolved["include_environment_mask"]))
            self.include_rmaos_var.set(bool(resolved["include_rmaos"]))
            self.include_complex_var.set(bool(resolved["include_complex"]))
            return str(resolved["effective_profile"])

        def _update_render_profile_recommendation(self, *, apply_auto: bool) -> str:
            preview_path = self._current_preview_path()
            recommended_profile = self._recommended_render_profile_for_preview(preview_path)
            message = build_render_profile_brief_message(recommended_profile)
            selected_profile = _normalize_render_profile(self.render_profile_var.get())
            if selected_profile == "auto":
                self.render_profile_suggestion_var.set(message)
                if apply_auto:
                    effective = self._apply_render_profile_modes(
                        "auto",
                        recommended_profile=recommended_profile,
                        apply_preset=True,
                    )
                    self._apply_render_profile_output_toggles(
                        "auto",
                        recommended_profile=recommended_profile,
                    )
                    if effective == "enb":
                        self.emboss_mode_var.set(False)
            elif selected_profile == "custom":
                self.render_profile_suggestion_var.set(
                    "Custom mode: manual controls are active; auto renderer suggestions are disabled."
                )
            else:
                selected_label = _RENDER_PROFILE_LABELS.get(selected_profile, selected_profile.replace("_", " ").title())
                self.render_profile_suggestion_var.set(
                    f"Target renderer locked to {selected_label}. Auto suggestions are disabled for locked presets."
                )
            return recommended_profile

        def _open_render_profile_help(self) -> None:
            preview_path = self._current_preview_path()
            recommended_profile = self._recommended_render_profile_for_preview(preview_path)
            help_text = build_render_profile_recommendation_message(recommended_profile)
            if self.render_profile_help_window is not None and self.render_profile_help_window.winfo_exists():
                self.render_profile_help_window.deiconify()
                self.render_profile_help_window.lift()
                self.render_profile_help_window.focus_force()
                text_widget = getattr(self.render_profile_help_window, "_help_text_widget", None)
                if text_widget is not None:
                    text_widget.configure(state=tk.NORMAL)
                    text_widget.delete("1.0", tk.END)
                    text_widget.insert("1.0", help_text)
                    text_widget.configure(state=tk.DISABLED)
                return

            win = tk.Toplevel(self.root)
            win.title("Renderer / Channel Help")
            win.geometry("860x620")
            win.minsize(620, 420)
            win.transient(self.root)
            self.render_profile_help_window = win

            container = ttk.Frame(win, padding=10)
            container.pack(fill=tk.BOTH, expand=True)
            ttk.Label(
                container,
                text="Renderer/channel guidance [Experimental] (ENB vs Community Shaders)",
                justify=tk.LEFT,
                anchor=tk.W,
            ).pack(fill=tk.X, pady=(0, 8))
            text = tk.Text(container, wrap=tk.WORD)
            y_scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=text.yview)
            text.configure(yscrollcommand=y_scroll.set)
            text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
            text.insert("1.0", help_text)
            text.configure(state=tk.DISABLED)
            win._help_text_widget = text  # type: ignore[attr-defined]
            def _on_close() -> None:
                self.render_profile_help_window = None
                win.destroy()

            ttk.Button(win, text="Close", command=_on_close).pack(anchor=tk.E, padx=10, pady=(0, 10))
            win.protocol("WM_DELETE_WINDOW", _on_close)

        def _on_render_profile_changed(self, _event: object | None = None) -> None:
            selected = _normalize_render_profile(self.render_profile_var.get())
            self.render_profile_var.set(selected)
            recommended_profile = self._update_render_profile_recommendation(apply_auto=False)
            if selected == "custom":
                self.status_var.set(
                    "Target renderer set to Custom. Leaving all toggles/sliders/modes unchanged for manual control."
                )
                self._request_preview_refresh()
                return
            effective = self._apply_render_profile_modes(
                selected,
                recommended_profile=recommended_profile,
                apply_preset=True,
            )
            self._apply_render_profile_output_toggles(
                selected,
                recommended_profile=recommended_profile,
            )
            if selected == "auto":
                self.status_var.set(
                    f"Target renderer set to auto-detect; applied the current {_RENDER_PROFILE_LABELS.get(effective, effective)} preset and matching output checkboxes. "
                    f"{describe_render_profile_files_to_create(effective)}"
                )
            else:
                self.status_var.set(
                    f"Target renderer set to {_RENDER_PROFILE_LABELS.get(effective, effective)}. "
                    f"Complex naming, env mask mode, parallax mode, and output checkboxes were updated to match that renderer. "
                    f"{describe_render_profile_files_to_create(effective)} "
                    f"{describe_render_profile_default_outputs(effective)}"
                )
            self._request_preview_refresh()

        def _on_parallax_mode_changed(self, _event: object | None = None) -> None:
            if "occlusion" in self.parallax_mode_var.get():
                self.status_var.set("Parallax mode: occlusion — ENB-only smooth POM heightmap for meshes/materials set up for ENB parallax.")
            else:
                self.status_var.set("Parallax mode: standard — best default for vanilla Skyrim SE and Community Shaders Extended Materials workflows.")
            self._request_preview_refresh()

        def _on_complex_format_changed(self, _event: object | None = None) -> None:
            selected = self.complex_format_var.get().strip().lower()
            if selected not in {"msn", "cm"}:
                selected = "msn"
                self.complex_format_var.set(selected)
            if selected == "msn":
                self.status_var.set("Complex naming: msn (_msn) — ENB-style normal RGB + specular alpha workflow.")
            else:
                self.status_var.set("Complex naming: cm (_cm default; _c/_C optional with custom name) — Community Shaders Extended Materials env/gloss/metal/height workflow.")
            self._request_preview_refresh()

        def _on_env_mask_mode_changed(self, _event: object | None = None) -> None:
            selected = self.env_mask_mode_var.get().strip().lower()
            if selected not in {"standard", "complex"}:
                selected = "standard"
                self.env_mask_mode_var.set(selected)
            if selected == "complex":
                self.status_var.set(
                    "Environment mask mode: complex — RGBA packed output (ENB and TruePBR use different channel meanings; use the renderer preset + Help guide)."
                )
            else:
                self.status_var.set("Environment mask mode: standard — vanilla Skyrim SE grayscale reflection mask.")
            self._request_preview_refresh()

        def _update_slider_auto_states(self) -> None:
            auto_enabled = self.auto_suggestions_var.get()
            # Colours for AUTO vs manual labels — chosen to stand out in both themes.
            auto_fg = "#1e88e5"   # vivid blue: "the computer owns this value"
            manual_fg = ""        # reset to theme default
            slider_specs = (
                (self.normal_scale, self.auto_normal_suggestion_var, self.normal_strength_display_label, self.auto_normal_check),
                (self.parallax_scale, self.auto_parallax_suggestion_var, self.parallax_strength_display_label, self.auto_parallax_check),
                (self.glow_scale, self.auto_glow_suggestion_var, self.glow_threshold_display_label, self.auto_glow_check),
                (
                    self.environment_mask_scale,
                    self.auto_environment_mask_suggestion_var,
                    self.environment_mask_strength_display_label,
                    self.auto_environment_mask_check,
                ),
                (
                    self.rmaos_scale,
                    self.auto_rmaos_suggestion_var,
                    self.rmaos_strength_display_label,
                    self.auto_rmaos_check,
                ),
                (self.complex_scale, self.auto_complex_suggestion_var, self.complex_strength_display_label, self.auto_complex_check),
                (self.specular_scale, self.auto_specular_suggestion_var, self.specular_strength_display_label, self.auto_specular_check),
            )
            for slider, auto_var, display_label, auto_check in slider_specs:
                is_auto = auto_enabled and auto_var.get()
                slider.configure(state=tk.DISABLED if is_auto else tk.NORMAL)
                slider.configure(style="Auto.Horizontal.TScale" if is_auto else "Manual.Horizontal.TScale")
                display_label.configure(foreground=auto_fg if is_auto else manual_fg)
                auto_check.configure(state=tk.NORMAL if auto_enabled else tk.DISABLED)
            self._update_slider_value_labels()

        def _apply_recommended_settings(self, *, update_toggles: bool = True) -> None:
            """Apply per-image recommended settings to sliders and (optionally) mode toggles.

            Parameters
            ----------
            update_toggles:
                When ``True`` (default), also update render-profile, emboss, and
                relief mode toggles based on the image.  Pass ``False`` during batch
                processing so that slider values stay current in the preview panel
                without mutating the mode toggles that the running batch already
                captured in its own ``generation_kwargs`` snapshot.
            """
            if self.source_image is None or not self.auto_suggestions_var.get():
                return
            preview_path = self.selected_inputs[self.current_preview_index] if self.selected_inputs else None
            recommended = recommend_generation_settings(self.source_image, input_path=preview_path)
            current = {
                "normal_strength": float(self.normal_strength_var.get()),
                "parallax_strength": float(self.parallax_strength_var.get()),
                "glow_threshold": int(round(self.glow_threshold_var.get())),
                "environment_mask_strength": float(self.environment_mask_strength_var.get()),
                "rmaos_strength": float(self.rmaos_strength_var.get()),
                "complex_strength": float(self.complex_strength_var.get()),
                "specular_strength": float(self.specular_strength_var.get()),
            }
            auto_flags = {
                "normal_strength": self.auto_normal_suggestion_var.get(),
                "parallax_strength": self.auto_parallax_suggestion_var.get(),
                "glow_threshold": self.auto_glow_suggestion_var.get(),
                "environment_mask_strength": self.auto_environment_mask_suggestion_var.get(),
                "rmaos_strength": self.auto_rmaos_suggestion_var.get(),
                "complex_strength": self.auto_complex_suggestion_var.get(),
                "specular_strength": self.auto_specular_suggestion_var.get(),
            }
            resolved = apply_recommendations_by_auto_flags(current=current, recommended=recommended, auto_flags=auto_flags)
            self.normal_strength_var.set(float(resolved["normal_strength"]))
            self.parallax_strength_var.set(float(resolved["parallax_strength"]))
            self.glow_threshold_var.set(float(resolved["glow_threshold"]))
            self.environment_mask_strength_var.set(float(resolved["environment_mask_strength"]))
            self.rmaos_strength_var.set(float(resolved["rmaos_strength"]))
            self.complex_strength_var.set(float(resolved["complex_strength"]))
            self.specular_strength_var.set(float(resolved["specular_strength"]))
            if update_toggles:
                self._update_render_profile_recommendation(apply_auto=False)
                # Auto-suggest emboss/relief mode based on material type and image content.
                if preview_path is not None:
                    material_type = classify_material_type(preview_path)
                    if material_type == "paper":
                        if not self.emboss_mode_manual_override:
                            self.emboss_mode_var.set(True)
                        # For paintings/illustrated art (high saturation + bg uniformity),
                        # also suggest relief mode for the pop-out effect.
                        if self.source_image is not None:
                            analysis = analyze_image_content(self.source_image)
                            saturation_mean = float(analysis.get("saturation_mean", 0.0))
                            bg_uniformity = float(analysis.get("bg_uniformity", 0.0))
                            # High saturation + uniform background suggests illustrated/painted art.
                            if (
                                saturation_mean >= 65.0
                                and bg_uniformity >= 0.35
                                and not self.relief_mode_manual_override
                            ):
                                self.relief_mode_var.set(True)
            self._update_slider_auto_states()

        def _photo_image(self, image: Image.Image, max_size: int = 260) -> ImageTk.PhotoImage:
            preview = image.copy()
            preview.thumbnail((max_size, max_size))
            if preview.mode != "RGB":
                preview = preview.convert("RGB")
            return ImageTk.PhotoImage(preview)

        def _request_preview_refresh(self) -> None:
            if self.preview_refresh_after_id is not None:
                self.root.after_cancel(self.preview_refresh_after_id)
            self.preview_refresh_after_id = self.root.after(75, self._refresh_preview)

        def _on_preview_size_changed(self) -> None:
            self.status_var.set(f"Preview size set to {self.preview_size_var.get()}.")
            self._refresh_preview()

        def _set_preview_source(self, index: int, apply_recommendations: bool = False) -> None:
            if not self.selected_inputs:
                self.source_image = None
                self.current_preview_index = 0
                self.preview_source_name_var.set("No source loaded")
                self.preview_jump_var.set("")
                self._update_preview_navigation_state()
                return
            resolved_index = max(0, min(index, len(self.selected_inputs) - 1))
            preview_path = self.selected_inputs[resolved_index]
            try:
                with Image.open(preview_path) as src:
                    self.source_image = prepare_preview_source(src)
            except Exception as exc:
                self.source_image = None
                self.current_preview_index = resolved_index
                self.preview_source_name_var.set(f"{preview_path.name} (preview load failed)")
                self.before_image_label.configure(image="", text="Preview unavailable")
                self.preview_output_images.clear()
                for label in self.preview_output_labels.values():
                    label.configure(image="", text="No preview")
                self.status_var.set(f"Could not load preview for {preview_path.name}: {exc}")
                self._update_preview_navigation_state()
                return
            self.current_preview_index = resolved_index
            self.preview_jump_var.set(str(resolved_index + 1))
            if len(self.selected_inputs) > 1:
                self.preview_source_name_var.set(
                    f"{resolved_index + 1}/{len(self.selected_inputs)}: {preview_path.name}"
                )
            else:
                self.preview_source_name_var.set(preview_path.name)
            if apply_recommendations:
                self._apply_recommended_settings()
            else:
                self._update_render_profile_recommendation(apply_auto=False)
            self._update_preview_navigation_state()

        def _set_preview_source_by_path(self, path: Path) -> None:
            for index, selected in enumerate(self.selected_inputs):
                if selected == path:
                    apply_recs = should_apply_preview_recommendations(
                        auto_suggestions_enabled=self.auto_suggestions_var.get(),
                        is_processing=self.is_processing,
                    )
                    self._set_preview_source(index, apply_recommendations=apply_recs)
                    # During batch processing (apply_recs=False), update slider values
                    # without mutating mode toggles so the display stays current
                    # per-image while the running batch keeps its own settings snapshot.
                    if not apply_recs and self.auto_suggestions_var.get() and self.is_processing:
                        self._apply_recommended_settings(update_toggles=False)
                    self._request_preview_refresh()
                    return

        def _show_previous_preview_source(self) -> None:
            if not self.selected_inputs:
                return
            wrapped_index = compute_wrapped_preview_index(
                self.current_preview_index - 1, len(self.selected_inputs)
            )
            self._set_preview_source(
                wrapped_index, apply_recommendations=self.auto_suggestions_var.get()
            )
            self._refresh_preview()

        def _show_next_preview_source(self) -> None:
            if not self.selected_inputs:
                return
            wrapped_index = compute_wrapped_preview_index(
                self.current_preview_index + 1, len(self.selected_inputs)
            )
            self._set_preview_source(
                wrapped_index, apply_recommendations=self.auto_suggestions_var.get()
            )
            self._refresh_preview()

        def _on_preview_jump_submit(self, _event: object | None = None) -> None:
            self._jump_to_preview_source()

        def _jump_to_preview_source(self) -> None:
            total = len(self.selected_inputs)
            target = parse_preview_jump_input(self.preview_jump_var.get(), total)
            if target is None:
                if total <= 0:
                    self.status_var.set("Load textures first before using preview jump.")
                else:
                    self.status_var.set(f"Invalid preview number. Enter a value from 1 to {total}.")
                return
            self._set_preview_source(target, apply_recommendations=self.auto_suggestions_var.get())
            self._refresh_preview()

        def _update_preview_navigation_state(self) -> None:
            has_multiple = len(self.selected_inputs) > 1
            can_navigate = has_multiple and not self.is_processing
            state = tk.NORMAL if can_navigate else tk.DISABLED
            self.prev_source_button.configure(state=state)
            self.next_source_button.configure(state=state)
            has_inputs = len(self.selected_inputs) > 0
            jump_state = tk.NORMAL if (has_inputs and not self.is_processing) else tk.DISABLED
            self.preview_jump_entry.configure(state=jump_state)
            self.preview_jump_button.configure(state=jump_state)

        def _refresh_preview(self) -> None:
            self.preview_refresh_after_id = None
            if self.source_image is None:
                return
            try:
                # Map the GUI parallax mode combo value to the internal key.
                _pm_raw = self.parallax_mode_var.get()
                _parallax_mode = "occlusion" if "occlusion" in _pm_raw else "standard"
                preview_state = load_gui_state()
                outputs = generate_preview_outputs(
                    self.source_image,
                    normal_strength=float(self.normal_strength_var.get()),
                    parallax_strength=float(self.parallax_strength_var.get()),
                    glow_threshold=int(round(self.glow_threshold_var.get())),
                    environment_mask_strength=float(self.environment_mask_strength_var.get()),
                    rmaos_strength=float(self.rmaos_strength_var.get()),
                    complex_strength=float(self.complex_strength_var.get()),
                    specular_strength=float(self.specular_strength_var.get()),
                    complex_format=self.complex_format_var.get(),
                    env_mask_mode=self.env_mask_mode_var.get(),
                    emboss_mode=self.emboss_mode_var.get(),
                    relief_mode=self.relief_mode_var.get(),
                    parallax_mode=_parallax_mode,
                    include_diffuse=self.include_diffuse_var.get(),
                    include_normal=self.include_normal_var.get(),
                    include_parallax=self.include_parallax_var.get(),
                    include_glow=self.include_glow_var.get(),
                    include_environment_mask=self.include_environment_mask_var.get(),
                    include_rmaos=self.include_rmaos_var.get(),
                    include_wetness_mask=self.include_wetness_mask_var.get(),
                    wetness_mask_strength=float(preview_state.get("wetness_mask_strength", 1.0)),
                    include_snow_mask=self.include_snow_mask_var.get(),
                    snow_mask_strength=float(preview_state.get("snow_mask_strength", 1.0)),
                    include_complex=self.include_complex_var.get(),
                    render_profile=self.render_profile_var.get(),
                )

                before_max, output_max = get_preview_size_limits(self.preview_size_var.get())
                self.preview_before = self._photo_image(self.source_image, max_size=before_max)
                self.before_image_label.configure(image=self.preview_before, text="")
                for output_key, label in self.preview_output_labels.items():
                    output_image = outputs.get(output_key)
                    if output_image is None:
                        self.preview_output_images.pop(output_key, None)
                        label.configure(image="", text="No preview")
                        continue
                    display_image = output_image
                    if output_key == "complex_material":
                        display_image = build_complex_preview_image(
                            output_image,
                            complex_format=self.complex_format_var.get(),
                        )
                    photo = self._photo_image(display_image, max_size=output_max)
                    self.preview_output_images[output_key] = photo
                    label.configure(image=photo, text="")
            except Exception as exc:
                self.status_var.set(f"Preview update failed: {exc}")

        def _check_and_show_generation_warnings(
            self,
            material_type: str,
            source_role: str | None,
            source_hint: str | None,
            source_suffix: str | None,
            include_diffuse: bool,
            include_normal: bool,
            include_glow: bool,
            include_environment_mask: bool,
            include_rmaos: bool,
            include_wetness_mask: bool,
            include_snow_mask: bool,
            env_mask_mode: str,
            env_mask_strength: float,
            include_parallax: bool,
            include_complex: bool,
            parallax_mode: str,
            emboss_mode: bool = False,
            relief_mode: bool = False,
            parallax_strength: float | None = None,
        ) -> bool:
            """Show any applicable sanity warnings. Returns False if user chose to abort."""
            warnings = get_generation_warnings(
                material_type,
                source_role=source_role,
                source_hint=source_hint,
                source_suffix=source_suffix,
                include_diffuse=include_diffuse,
                include_normal=include_normal,
                include_glow=include_glow,
                include_environment_mask=(include_environment_mask or include_rmaos),
                include_rmaos=include_rmaos,
                include_wetness_mask=include_wetness_mask,
                include_snow_mask=include_snow_mask,
                env_mask_mode=env_mask_mode,
                env_mask_strength=env_mask_strength,
                include_parallax=include_parallax,
                parallax_strength=parallax_strength,
                include_complex=include_complex,
                complex_format=self.complex_format_var.get(),
                parallax_mode=self.parallax_mode_var.get(),
                emboss_mode=emboss_mode,
                relief_mode=relief_mode,
            )
            for warning_id, message in warnings:
                if warning_id in self.dismissed_warnings:
                    continue
                dismiss_var = tk.BooleanVar(value=False)
                dialog = tk.Toplevel(self.root)
                dialog.title("Generation Warning")
                dialog.transient(self.root)
                dialog.resizable(False, False)
                dialog_result: list[bool] = [True]
                colors = _DARK_THEME if self.dark_mode_var.get() else _LIGHT_THEME
                dialog.configure(background=colors["bg"])
                tk.Label(
                    dialog,
                    text=f"⚠ {message}",
                    justify=tk.LEFT,
                    wraplength=420,
                    padx=14,
                    pady=10,
                    background=colors["bg"],
                    foreground=colors["fg"],
                ).pack(anchor=tk.W)
                check_frame = tk.Frame(dialog, background=colors["bg"])
                check_frame.pack(anchor=tk.W, padx=14, pady=(0, 6))
                tk.Checkbutton(
                    check_frame,
                    text="Don't show this warning again",
                    variable=dismiss_var,
                    background=colors["bg"],
                    foreground=colors["fg"],
                    activebackground=colors["bg"],
                    selectcolor=colors["field_bg"],
                ).pack(side=tk.LEFT)
                btn_frame = tk.Frame(dialog, background=colors["bg"])
                btn_frame.pack(pady=(4, 12), padx=14, anchor=tk.E)

                def _continue(d: tk.Toplevel = dialog, wid: str = warning_id) -> None:
                    if dismiss_var.get():
                        self.dismissed_warnings.add(wid)
                    d.destroy()

                def _abort(d: tk.Toplevel = dialog) -> None:
                    dialog_result[0] = False
                    d.destroy()

                ttk.Button(btn_frame, text="Continue anyway", command=_continue).pack(side=tk.LEFT, padx=(0, 8))
                ttk.Button(btn_frame, text="Cancel generation", command=_abort).pack(side=tk.LEFT)
                dialog.grab_set()
                self.root.wait_window(dialog)
                if not dialog_result[0]:
                    return False
            return True

        def _generate(self) -> None:
            try:
                input_value = self.input_var.get().strip()
                if not input_value:
                    messagebox.showwarning("Missing input", "Please choose an input DDS texture first.", parent=self.root)
                    return
                if self.is_processing:
                    self.status_var.set("Generation already running. Please wait for the current batch to finish.")
                    messagebox.showwarning(
                        "Generation already running",
                        "A generation task is already in progress. Please wait for it to finish or cancel it first.",
                        parent=self.root,
                    )
                    return

                include_diffuse = self.include_diffuse_var.get()
                include_normal = self.include_normal_var.get()
                include_parallax = self.include_parallax_var.get()
                include_glow = self.include_glow_var.get()
                include_environment_mask = self.include_environment_mask_var.get()
                include_rmaos = self.include_rmaos_var.get()
                include_wetness_mask = bool(self.include_wetness_mask_var.get())
                include_snow_mask = bool(self.include_snow_mask_var.get())
                include_complex = self.include_complex_var.get()
                if not any((
                    include_diffuse,
                    include_normal,
                    include_parallax,
                    include_glow,
                    include_environment_mask,
                    include_rmaos,
                    include_wetness_mask,
                    include_snow_mask,
                    include_complex,
                )):
                    messagebox.showwarning("No outputs selected", "Select at least one output type.", parent=self.root)
                    return

                input_path = Path(input_value)
                self.selected_inputs = collect_source_textures(input_path)
                if not self.selected_inputs:
                    self.status_var.set("No source textures found to process.")
                    messagebox.showwarning("No source textures found", "No valid source textures were found for the selected input.", parent=self.root)
                    return

                output_dir: Path | None = None
                state = load_gui_state()
                if self.use_custom_output_var.get():
                    output_value = self.output_var.get().strip()
                    if not output_value:
                        messagebox.showwarning(
                            "Missing output folder",
                            "Choose an output folder or disable custom output location.",
                            parent=self.root,
                        )
                        return
                    output_dir = Path(output_value)

                _context_source = select_generation_context_source(Path(input_value), self.selected_inputs)
                _material_type = classify_material_type(_context_source)
                _role_info = identify_skyrim_texture_role(_context_source)
                _source_role = _role_info["role"] if _role_info is not None else None
                _source_hint = _role_info["hint"] if _role_info is not None else None
                _source_suffix = _role_info["suffix"] if _role_info is not None else None
                _recommended_profile = self._recommended_render_profile_for_preview(_context_source)
                _guardrails = resolve_render_profile_guardrails(
                    selected_profile=self.render_profile_var.get(),
                    recommended_profile=_recommended_profile,
                    complex_format=self.complex_format_var.get(),
                    env_mask_mode=self.env_mask_mode_var.get(),
                    parallax_mode=self.parallax_mode_var.get(),
                )
                _guardrail_changes = list(_guardrails["changes"])
                if _guardrail_changes:
                    self.complex_format_var.set(str(_guardrails["complex_format"]))
                    self.env_mask_mode_var.set(str(_guardrails["env_mask_mode"]))
                    self.parallax_mode_var.set(
                        "occlusion (ENB/POM)" if str(_guardrails["parallax_mode"]) == "occlusion" else "standard"
                    )
                    _effective_label = _RENDER_PROFILE_LABELS.get(
                        str(_guardrails["effective_profile"]),
                        str(_guardrails["effective_profile"]).replace("_", " ").title(),
                    )
                    self.status_var.set(
                        f"Renderer guardrails applied for {_effective_label}: " + "; ".join(_guardrail_changes) + "."
                    )
                if not self._check_and_show_generation_warnings(
                    _material_type,
                    source_role=_source_role,
                    source_hint=_source_hint,
                    source_suffix=_source_suffix,
                    include_diffuse=include_diffuse,
                    include_normal=include_normal,
                    include_glow=include_glow,
                    include_environment_mask=include_environment_mask,
                    include_rmaos=include_rmaos,
                    include_wetness_mask=include_wetness_mask,
                    include_snow_mask=include_snow_mask,
                    env_mask_mode=self.env_mask_mode_var.get(),
                    env_mask_strength=float(self.environment_mask_strength_var.get()),
                    include_parallax=include_parallax,
                    parallax_strength=float(self.parallax_strength_var.get()),
                    include_complex=include_complex,
                    parallax_mode=self.parallax_mode_var.get(),
                    emboss_mode=self.emboss_mode_var.get(),
                    relief_mode=self.relief_mode_var.get(),
                ):
                    return

                # Warn if the output folder already has complex-material files in a different format.
                _effective_output_dir = output_dir if output_dir is not None else (
                    self.selected_inputs[0].parent if self.selected_inputs else None
                )
                if _effective_output_dir is not None:
                    folder_warnings = get_output_folder_format_warnings(
                        _effective_output_dir,
                        include_complex=include_complex,
                        complex_format=self.complex_format_var.get(),
                    )
                    for warning_id, message in folder_warnings:
                        if warning_id in self.dismissed_warnings:
                            continue
                        dismiss_var = tk.BooleanVar(value=False)
                        dialog = tk.Toplevel(self.root)
                        dialog.title("Output Folder Warning")
                        dialog.transient(self.root)
                        dialog.resizable(False, False)
                        dialog_result: list[bool] = [True]
                        colors = _DARK_THEME if self.dark_mode_var.get() else _LIGHT_THEME
                        dialog.configure(background=colors["bg"])
                        tk.Label(
                            dialog,
                            text=f"⚠ {message}",
                            justify=tk.LEFT,
                            wraplength=420,
                            padx=14,
                            pady=10,
                            background=colors["bg"],
                            foreground=colors["fg"],
                        ).pack(anchor=tk.W)
                        check_frame = tk.Frame(dialog, background=colors["bg"])
                        check_frame.pack(anchor=tk.W, padx=14, pady=(0, 6))
                        tk.Checkbutton(
                            check_frame,
                            text="Don't show this warning again",
                            variable=dismiss_var,
                            background=colors["bg"],
                            foreground=colors["fg"],
                            activebackground=colors["bg"],
                            selectcolor=colors["field_bg"],
                        ).pack(side=tk.LEFT)
                        btn_frame = tk.Frame(dialog, background=colors["bg"])
                        btn_frame.pack(pady=(4, 12), padx=14, anchor=tk.E)

                        def _fw_continue(d: tk.Toplevel = dialog, wid: str = warning_id) -> None:
                            if dismiss_var.get():
                                self.dismissed_warnings.add(wid)
                            d.destroy()

                        def _fw_abort(d: tk.Toplevel = dialog) -> None:
                            dialog_result[0] = False
                            d.destroy()

                        ttk.Button(btn_frame, text="Continue anyway", command=_fw_continue).pack(side=tk.LEFT, padx=(0, 8))
                        ttk.Button(btn_frame, text="Cancel generation", command=_fw_abort).pack(side=tk.LEFT)
                        dialog.grab_set()
                        self.root.wait_window(dialog)
                        if not dialog_result[0]:
                            return

                _pm_raw = self.parallax_mode_var.get()
                _parallax_mode_key = "occlusion" if "occlusion" in _pm_raw else "standard"
                generation_kwargs = {
                    "output_dir": output_dir,
                    "normal_strength": self._resolve_generation_value(
                        self.normal_strength_var.get(), self.auto_normal_suggestion_var
                    ),
                    "parallax_strength": self._resolve_generation_value(
                        self.parallax_strength_var.get(), self.auto_parallax_suggestion_var
                    ),
                    "glow_threshold": self._resolve_generation_value(
                        int(round(self.glow_threshold_var.get())), self.auto_glow_suggestion_var
                    ),
                    "environment_mask_strength": self._resolve_generation_value(
                        self.environment_mask_strength_var.get(), self.auto_environment_mask_suggestion_var
                    ),
                    "rmaos_strength": self._resolve_generation_value(
                        self.rmaos_strength_var.get(), self.auto_rmaos_suggestion_var
                    ),
                    "complex_strength": self._resolve_generation_value(
                        self.complex_strength_var.get(), self.auto_complex_suggestion_var
                    ),
                    "specular_strength": self._resolve_generation_value(
                        self.specular_strength_var.get(), self.auto_specular_suggestion_var
                    ),
                    "complex_format": self.complex_format_var.get(),
                    "env_mask_mode": self.env_mask_mode_var.get(),
                    "emboss_mode": self.emboss_mode_var.get(),
                    "relief_mode": self.relief_mode_var.get(),
                    "parallax_mode": _parallax_mode_key,
                    "auto_patch_nifs": self.auto_patch_nifs_var.get(),
                    "manager_context": self.manager_context,
                    "render_profile": self.render_profile_var.get(),
                    "include_diffuse": include_diffuse,
                    "include_normal": include_normal,
                    "include_parallax": include_parallax,
                    "include_glow": include_glow,
                    "include_environment_mask": include_environment_mask,
                    "include_rmaos": include_rmaos,
                    "include_wetness_mask": include_wetness_mask,
                    "wetness_mask_strength": float(state.get("wetness_mask_strength", 1.0)),
                    "include_snow_mask": include_snow_mask,
                    "snow_mask_strength": float(state.get("snow_mask_strength", 1.0)),
                    "include_complex": include_complex,
                }
                self.batch_failures = []
                self.batch_nif_patch_results = []
                self.cancel_requested = False
                self._prepare_generation_snapshot(self.selected_inputs, generation_kwargs)
                self._set_processing_state(True)
                if self.show_batch_preview_var.get():
                    self.status_var.set(
                        f"Queued {len(self.selected_inputs)} source texture(s). Live batch preview is ON and may slow processing."
                    )
                else:
                    self.status_var.set(f"Queued {len(self.selected_inputs)} source texture(s) for processing...")
                self.processing_thread = threading.Thread(
                    target=self._process_generation_batch,
                    args=(self.selected_inputs.copy(), generation_kwargs),
                    daemon=True,
                )
                self.processing_thread.start()
                self.root.after(100, self._poll_processing_queue)
            except Exception as exc:
                self._set_processing_state(False)
                self.status_var.set(f"Generation failed before start: {exc}")
                messagebox.showerror("Generation failed", str(exc), parent=self.root)

        def _cancel_processing(self) -> None:
            if not self.is_processing:
                return
            self.cancel_requested = True
            self.cancel_button.configure(state=tk.DISABLED)
            self.status_var.set("Cancellation requested. Current file will finish, then processing stops.")

        def _revert_last_generation(self) -> None:
            if self.is_processing:
                return
            if not self._has_revert_snapshot():
                self.revert_button.configure(state=tk.DISABLED)
                return
            if not messagebox.askyesno(
                "Revert generated files",
                "Revert files from the last generation run?\nThis will delete newly generated files and restore overwritten files.",
            ):
                return
            errors: list[str] = []
            for generated_path in sorted(self.last_generation_created_files):
                try:
                    generated_path.unlink(missing_ok=True)
                except Exception as exc:
                    errors.append(f"Could not remove {generated_path.name}: {exc}")
            for original_path, backup_path in self.last_generation_backups.items():
                try:
                    original_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_path, original_path)
                except Exception as exc:
                    errors.append(f"Could not restore {original_path.name}: {exc}")
            if errors:
                messagebox.showerror("Revert incomplete", "\n".join(errors[:8]))
                self.status_var.set("Revert completed with errors.")
            else:
                messagebox.showinfo("Revert complete", "Last generation outputs were reverted.")
                self.status_var.set("Reverted files from the last generation run.")
            self._discard_last_generation_snapshot()
            self._set_processing_state(False)
            self._refresh_preview()

        def _open_nif_editor(self) -> None:
            """Open the NIF Editor in a separate Toplevel window."""
            if not NIF_PATCHER_AVAILABLE:
                messagebox.showerror(
                    "NIF Editor unavailable",
                    "nif_patcher.py was not found alongside generate_textures.py.\n"
                    "Make sure nif_patcher.py is in the same folder.",
                )
                return

            win = tk.Toplevel(self.root)
            win.title(f"NIF Editor [Experimental] — Skyrim Texture Generator v{APP_VERSION}")
            win.geometry("1200x900")
            win.minsize(960, 720)
            win.resizable(True, True)
            win.grab_set()
            try:
                dark = self.dark_mode_var.get()
                bg = _DARK_THEME["bg"] if dark else _LIGHT_THEME["bg"]
                fg = _DARK_THEME["fg"] if dark else _LIGHT_THEME["fg"]
                entry_bg = _DARK_THEME["field_bg"] if dark else _LIGHT_THEME["field_bg"]
                win.configure(bg=bg)

                tk.Label(
                    win,
                    text=(
                        "⚠ Experimental feature — always keep backups of your NIF files before patching.\n\n"
                        "Patch Skyrim SE NIF files so generated textures work in-game.\n"
                        "Quick start: choose your target renderer first, then use Scan NIFs to review, "
                        "Apply patch to enable features, Remove features to disable them, and Restore backups "
                        "to roll back .nif.bak copies."
                    ),
                    justify=tk.LEFT,
                    wraplength=740,
                    padx=10,
                    pady=8,
                    background=bg,
                    foreground=fg,
                ).pack(fill="x")

                path_frame = ttk.LabelFrame(win, text="NIF Target", padding=6)
                path_frame.pack(fill="x", padx=10, pady=(2, 4))

                nif_path_var = tk.StringVar()
                nif_scan_mode = tk.StringVar(value="file")
                if self.manager_context.loaded_mesh_dirs:
                    nif_path_var.set(str(self.manager_context.loaded_mesh_dirs[0]))
                    nif_scan_mode.set("folder")

                row0 = ttk.Frame(path_frame)
                row0.pack(fill="x")
                single_nif_radio = ttk.Radiobutton(row0, text="Single NIF file", variable=nif_scan_mode, value="file")
                single_nif_radio.pack(side="left")
                folder_nif_radio = ttk.Radiobutton(row0, text="Whole mesh folder (recursive)", variable=nif_scan_mode, value="folder")
                folder_nif_radio.pack(side="left", padx=(8, 0))
                self._add_tooltip(single_nif_radio, "🎯 Patch one mesh when you already know the troublemaker.")
                self._add_tooltip(folder_nif_radio, "🧹 Recursive mode for when the whole folder needs a tactical reality check.")

                row1 = ttk.Frame(path_frame)
                row1.pack(fill="x", pady=(4, 0))
                path_label = ttk.Label(row1, text="Path:")
                path_label.pack(side="left")
                nif_path_entry = ttk.Entry(row1, textvariable=nif_path_var)
                nif_path_entry.pack(side="left", fill="x", expand=True, padx=(4, 4))

                def _browse_nif() -> None:
                    if nif_scan_mode.get() == "folder":
                        selected = filedialog.askdirectory(title="Select mesh folder")
                    else:
                        selected = filedialog.askopenfilename(
                            title="Select NIF file",
                            filetypes=[("NIF files", "*.nif"), ("All files", "*.*")],
                        )
                    if selected:
                        nif_path_var.set(selected)

                browse_nif_button = ttk.Button(row1, text="Browse…", command=_browse_nif)
                browse_nif_button.pack(side="left")
                self._add_tooltip(path_label, "📍 Pick the NIF file/folder you want to scan or patch.")
                self._add_tooltip(nif_path_entry, "⌨ Paste a full path here. Yes, even that scary MO2 path with 400 folders.")
                self._add_tooltip(browse_nif_button, "🧭 Opens file/folder picker so your fingers don’t have to type all that.")

                opt_frame = ttk.LabelFrame(win, text="Patch Options (what to enable)", padding=6)
                opt_frame.pack(fill="x", padx=10, pady=4)
                renderer_profile_var = tk.StringVar(value=_normalize_render_profile(self.render_profile_var.get()))
                if renderer_profile_var.get() not in _RENDER_PROFILE_LABELS:
                    renderer_profile_var.set("auto")
                enable_parallax_var = tk.BooleanVar(value=True)
                enable_pom_var = tk.BooleanVar(value=False)
                enable_env_var = tk.BooleanVar(value=False)
                enable_glow_var = tk.BooleanVar(value=False)
                force_type3_var = tk.BooleanVar(value=False)
                disable_parallax_var = tk.BooleanVar(value=False)
                disable_pom_var = tk.BooleanVar(value=False)
                disable_env_var = tk.BooleanVar(value=False)
                disable_glow_var = tk.BooleanVar(value=False)
                clear_parallax_var = tk.BooleanVar(value=False)
                clear_normal_var = tk.BooleanVar(value=False)
                clear_env_var = tk.BooleanVar(value=False)
                clear_glow_var = tk.BooleanVar(value=False)
                clear_diffuse_var = tk.BooleanVar(value=False)
                clear_cubemap_var = tk.BooleanVar(value=False)
                backup_var = tk.BooleanVar(value=True)
                dry_run_var = tk.BooleanVar(value=False)
                option_warning_var = tk.StringVar(value="")

                render_row = ttk.Frame(opt_frame)
                render_row.pack(fill="x", pady=(0, 4))
                renderer_label = ttk.Label(render_row, text="Target renderer:")
                renderer_label.pack(side="left")
                renderer_combo = ttk.Combobox(
                    render_row,
                    textvariable=renderer_profile_var,
                    values=_RENDER_PROFILE_GUI_VALUES,
                    state="readonly",
                    width=20,
                )
                renderer_combo.pack(side="left", padx=(6, 6))

                flag_row = ttk.Frame(opt_frame)
                flag_row.pack(fill="x")
                enable_parallax_check = ttk.Checkbutton(
                    flag_row,
                    text="Enable standard parallax (vanilla/CS)",
                    variable=enable_parallax_var,
                )
                enable_parallax_check.pack(side="left")
                enable_pom_check = ttk.Checkbutton(
                    flag_row,
                    text="Enable ENB POM / occlusion (ENB only)",
                    variable=enable_pom_var,
                )
                enable_pom_check.pack(side="left", padx=(12, 0))

                flag_row2 = ttk.Frame(opt_frame)
                flag_row2.pack(fill="x", pady=(2, 0))
                enable_env_check = ttk.Checkbutton(
                    flag_row2,
                    text="Enable environment mapping (_m slot)",
                    variable=enable_env_var,
                )
                enable_env_check.pack(side="left")
                force_type3_check = ttk.Checkbutton(
                    flag_row2,
                    text="Force shader type 3 (required for stronger parallax scale on some meshes)",
                    variable=force_type3_var,
                )
                force_type3_check.pack(side="left", padx=(12, 0))
                flag_row3 = ttk.Frame(opt_frame)
                flag_row3.pack(fill="x", pady=(2, 0))
                enable_glow_check = ttk.Checkbutton(
                    flag_row3,
                    text="Enable glow-map flag (_g slot)",
                    variable=enable_glow_var,
                )
                enable_glow_check.pack(side="left")

                misc_row = ttk.Frame(opt_frame)
                misc_row.pack(fill="x", pady=(4, 0))
                backup_check = ttk.Checkbutton(misc_row, text="Backup originals (.nif.bak)", variable=backup_var)
                backup_check.pack(side="left")
                dry_run_check = ttk.Checkbutton(misc_row, text="Dry run (scan/preview only)", variable=dry_run_var)
                dry_run_check.pack(side="left", padx=(12, 0))
                guide_label = ttk.Label(
                    opt_frame,
                    text=(
                        "Option guide: Slot 0=diffuse/albedo, 1=normal/_n or _msn, 2=glow/_g, "
                        "3=parallax/_p, 4=cubemap, 5=environment mask/_m or _cm/_c or _rmaos. "
                        "Enable standard parallax for vanilla/community shaders/truepbr workflows. Enable ENB POM only for ENB setups. "
                        "Enable environment mapping only when using slot 5 (_m/_cm/_c/_rmaos). Enable glow-map when slot 2 points at an emissive texture. "
                        "Force shader type 3 lets the tool write stronger parallax scale values."
                    ),
                    justify=tk.LEFT,
                    wraplength=860,
                )
                guide_label.pack(fill="x", pady=(4, 0))
                option_warning_label = ttk.Label(
                    opt_frame,
                    textvariable=option_warning_var,
                    justify=tk.LEFT,
                    wraplength=860,
                    foreground="#b44",
                )
                option_warning_label.pack(fill="x", pady=(2, 0))

                unpatch_frame = ttk.LabelFrame(win, text="Remove Features (what to disable)", padding=6)
                unpatch_frame.pack(fill="x", padx=10, pady=(2, 4))
                unpatch_row = ttk.Frame(unpatch_frame)
                unpatch_row.pack(fill="x")
                disable_parallax_check = ttk.Checkbutton(
                    unpatch_row,
                    text="Disable parallax flags",
                    variable=disable_parallax_var,
                )
                disable_parallax_check.pack(side="left")
                disable_pom_check = ttk.Checkbutton(
                    unpatch_row,
                    text="Disable POM flag only",
                    variable=disable_pom_var,
                )
                disable_pom_check.pack(side="left", padx=(12, 0))
                disable_env_check = ttk.Checkbutton(
                    unpatch_row,
                    text="Disable environment mapping flag",
                    variable=disable_env_var,
                )
                disable_env_check.pack(side="left", padx=(12, 0))
                disable_glow_check = ttk.Checkbutton(
                    unpatch_row,
                    text="Disable glow-map flag",
                    variable=disable_glow_var,
                )
                disable_glow_check.pack(side="left", padx=(12, 0))
                unpatch_row2 = ttk.Frame(unpatch_frame)
                unpatch_row2.pack(fill="x", pady=(2, 0))
                clear_parallax_check = ttk.Checkbutton(unpatch_row2, text="Clear slot 3 (_p)", variable=clear_parallax_var)
                clear_parallax_check.pack(side="left")
                clear_normal_check = ttk.Checkbutton(unpatch_row2, text="Clear slot 1 (_n/_msn)", variable=clear_normal_var)
                clear_normal_check.pack(side="left", padx=(12, 0))
                clear_env_check = ttk.Checkbutton(unpatch_row2, text="Clear slot 5 (_m/_cm/_rmaos)", variable=clear_env_var)
                clear_env_check.pack(side="left", padx=(12, 0))
                unpatch_row3 = ttk.Frame(unpatch_frame)
                unpatch_row3.pack(fill="x", pady=(2, 0))
                clear_glow_check = ttk.Checkbutton(unpatch_row3, text="Clear slot 2 (_em/_g)", variable=clear_glow_var)
                clear_glow_check.pack(side="left")
                clear_diffuse_check = ttk.Checkbutton(unpatch_row3, text="Clear slot 0 (diffuse)", variable=clear_diffuse_var)
                clear_diffuse_check.pack(side="left", padx=(12, 0))
                clear_cubemap_check = ttk.Checkbutton(unpatch_row3, text="Clear slot 4 (cubemap)", variable=clear_cubemap_var)
                clear_cubemap_check.pack(side="left", padx=(12, 0))

                def _recommended_nif_editor_profile() -> str:
                    selected = _normalize_render_profile(renderer_profile_var.get())
                    if selected != "auto":
                        return selected
                    path_value = nif_path_var.get().strip()
                    input_path = Path(path_value) if path_value else None
                    return recommend_render_profile(
                        input_path,
                        manager_context=self.manager_context,
                    )

                def _preferred_env_mask_suffix_for_profile() -> str:
                    effective = _normalize_render_profile(_recommended_nif_editor_profile())
                    if effective == "truepbr":
                        return "_rmaos.dds"
                    if effective == "community_shaders":
                        return "_cm.dds"
                    return "_m.dds"

                def _apply_renderer_defaults(*_: object) -> None:
                    recommended_profile = _recommended_nif_editor_profile()
                    defaults = resolve_nif_patch_defaults_for_render_profile(
                        renderer_profile_var.get(),
                        recommended_profile=recommended_profile,
                    )
                    enable_parallax_var.set(bool(defaults["enable_parallax"]))
                    enable_pom_var.set(bool(defaults["enable_pom"]))
                    enable_env_var.set(bool(defaults["enable_env_mapping"]))
                    enable_glow_var.set(False)
                    force_type3_var.set(bool(defaults["force_shader_type_3"]))
                    option_warning_var.set("")

                def _update_checkbox_warnings(*_: object) -> None:
                    recommended_profile = _recommended_nif_editor_profile()
                    warnings = get_nif_patch_option_warnings(
                        selected_profile=str(renderer_profile_var.get() or "auto"),
                        recommended_profile=recommended_profile,
                        enable_parallax=enable_parallax_var.get(),
                        enable_pom=enable_pom_var.get(),
                        enable_env_mapping=enable_env_var.get(),
                        force_shader_type_3=force_type3_var.get(),
                        diffuse_texture_path=diffuse_tex_var.get(),
                        parallax_texture_path=parallax_tex_var.get(),
                        normal_texture_path=normal_tex_var.get(),
                        glow_texture_path=glow_tex_var.get(),
                        env_mask_texture_path=env_mask_tex_var.get(),
                        cubemap_texture_path=cubemap_tex_var.get(),
                        disable_parallax=disable_parallax_var.get(),
                        disable_pom=disable_pom_var.get(),
                        disable_env_mapping=disable_env_var.get(),
                        disable_glow_map=disable_glow_var.get(),
                        clear_parallax_texture_path=clear_parallax_var.get(),
                        clear_normal_texture_path=clear_normal_var.get(),
                        clear_env_mask_texture_path=clear_env_var.get(),
                        clear_glow_texture_path=clear_glow_var.get(),
                        clear_diffuse_texture_path=clear_diffuse_var.get(),
                        clear_cubemap_texture_path=clear_cubemap_var.get(),
                    )
                    option_warning_var.set("⚠ " + " ".join(warnings[:3]) if warnings else "")

                _apply_renderer_defaults()
                renderer_combo.bind("<<ComboboxSelected>>", _apply_renderer_defaults)
                renderer_combo.bind("<<ComboboxSelected>>", _update_checkbox_warnings, add="+")
                def _on_nif_target_changed(*_: object) -> None:
                    if _normalize_render_profile(renderer_profile_var.get()) == "auto":
                        _apply_renderer_defaults()
                    _update_checkbox_warnings()

                nif_path_var.trace_add("write", _on_nif_target_changed)
                nif_scan_mode.trace_add("write", _on_nif_target_changed)
                for watch_var in (
                    enable_parallax_var,
                    enable_pom_var,
                    enable_env_var,
                    enable_glow_var,
                    force_type3_var,
                    disable_parallax_var,
                    disable_pom_var,
                    disable_env_var,
                    disable_glow_var,
                    clear_parallax_var,
                    clear_normal_var,
                    clear_env_var,
                    clear_glow_var,
                    clear_diffuse_var,
                    clear_cubemap_var,
                ):
                    watch_var.trace_add("write", _update_checkbox_warnings)
                self._add_tooltip(
                    renderer_label,
                    "🎮 Pick your target renderer to auto-apply sane NIF patch toggles for that workflow.",
                )
                self._add_tooltip(
                    renderer_combo,
                    "🎯 Renderer preset helper.\nAuto applies patch options for Vanilla, Community Shaders, TruePBR, or ENB.",
                )
                self._add_tooltip(enable_parallax_check, "🪨 Enables Skyrim parallax shader flag and slot-3 _p usage.")
                self._add_tooltip(enable_pom_check, "🌊 ENB-only parallax occlusion mode. Leave off for vanilla workflows.")
                self._add_tooltip(enable_env_check, "🪞 Enables environment-mapping shader flag for reflective materials.")
                self._add_tooltip(enable_glow_check, "✨ Enables glow/emissive flag so slot 2 _g textures render in-game.")
                self._add_tooltip(force_type3_check, "💪 Upgrades shader type so stronger parallax scale can be written.")
                self._add_tooltip(backup_check, "🧷 Writes .nif.bak safety copies before patching.")
                self._add_tooltip(dry_run_check, "🧪 Scan and simulate changes without writing file edits.")
                self._add_tooltip(guide_label, "📘 Fast BSLighting checkbox reference so you can patch without guessing.")
                self._add_tooltip(option_warning_label, "⚠ Compatibility warnings for current checkbox combinations.")
                self._add_tooltip(disable_parallax_check, "🚫 Removes parallax and POM flags from BSLightingShaderProperty.")
                self._add_tooltip(disable_pom_check, "🚫 Removes only ENB POM flag while keeping standard parallax if desired.")
                self._add_tooltip(disable_env_check, "🚫 Removes environment-mapping flag from BSLightingShaderProperty.")
                self._add_tooltip(disable_glow_check, "🚫 Removes emissive/glow shader flag from BSLightingShaderProperty.")
                self._add_tooltip(clear_parallax_check, "🧹 Clears texture slot 3 path from BSShaderTextureSet.")
                self._add_tooltip(clear_normal_check, "🧹 Clears texture slot 1 path from BSShaderTextureSet.")
                self._add_tooltip(clear_env_check, "🧹 Clears texture slot 5 path from BSShaderTextureSet.")
                self._add_tooltip(clear_glow_check, "🧹 Clears texture slot 2 path from BSShaderTextureSet.")
                self._add_tooltip(clear_diffuse_check, "🧹 Clears texture slot 0 path from BSShaderTextureSet.")
                self._add_tooltip(clear_cubemap_check, "🧹 Clears texture slot 4 path from BSShaderTextureSet.")

                scale_frame = ttk.LabelFrame(
                    win,
                    text="Parallax Scale (0.1 – 10.0 · higher = deeper / more extreme)",
                    padding=6,
                )
                scale_frame.pack(fill="x", padx=10, pady=4)
                pscale_var = tk.DoubleVar(value=1.5)
                pscale_label_var = tk.StringVar(value="1.50")

                def _on_pscale_change(*_: object) -> None:
                    pscale_label_var.set(f"{pscale_var.get():.2f}")

                pscale_var.trace_add("write", _on_pscale_change)
                pscale_row = ttk.Frame(scale_frame)
                pscale_row.pack(fill="x")
                ttk.Scale(pscale_row, from_=0.1, to=10.0, variable=pscale_var, orient="horizontal").pack(
                    side="left", fill="x", expand=True
                )
                ttk.Label(pscale_row, textvariable=pscale_label_var, width=5).pack(side="left", padx=(6, 0))
                self._add_tooltip(
                    scale_frame,
                    "Parallax Scale controls how strong the in-game depth effect is.\n"
                    "Use Force Shader Type 3 if the original NIF did not expose a parallax scale field.",
                )

                tex_frame = ttk.LabelFrame(
                    win,
                    text="Texture Paths (Skyrim-relative textures\\... ; blank keeps current NIF slot)",
                    padding=6,
                )
                tex_frame.pack(fill="x", padx=10, pady=4)
                diffuse_tex_var = tk.StringVar()
                parallax_tex_var = tk.StringVar()
                normal_tex_var = tk.StringVar()
                glow_tex_var = tk.StringVar()
                cubemap_tex_var = tk.StringVar()
                env_mask_tex_var = tk.StringVar()
                diffuse_tex_var.trace_add("write", _update_checkbox_warnings)
                parallax_tex_var.trace_add("write", _update_checkbox_warnings)
                normal_tex_var.trace_add("write", _update_checkbox_warnings)
                glow_tex_var.trace_add("write", _update_checkbox_warnings)
                cubemap_tex_var.trace_add("write", _update_checkbox_warnings)
                env_mask_tex_var.trace_add("write", _update_checkbox_warnings)

                def _normalize_nif_texture_input_path(raw_path: str) -> str:
                    normalized = raw_path.strip().replace("/", "\\")
                    lowered = normalized.lower()
                    marker = "\\textures\\"
                    marker_index = lowered.rfind(marker)
                    if marker_index != -1:
                        normalized = "textures\\" + normalized[marker_index + len(marker):]
                    elif lowered.startswith("data\\textures\\"):
                        normalized = "textures\\" + normalized[len("data\\textures\\"):]
                    return _coerce_generated_resource_path_to_dds(normalized)

                def _browse_texture_path(target_var: tk.StringVar, title: str) -> None:
                    selected = filedialog.askopenfilename(
                        title=title,
                        filetypes=[("DDS files", "*.dds"), ("All files", "*.*")],
                    )
                    if selected:
                        target_var.set(_normalize_nif_texture_input_path(selected))

                def _tex_row(parent: ttk.Frame, label: str, var: tk.StringVar, browse_title: str, tooltip: str) -> None:
                    row = ttk.Frame(parent)
                    row.pack(fill="x", pady=2)
                    row_label = ttk.Label(row, text=label, width=18)
                    row_label.pack(side="left")
                    row_entry = ttk.Entry(row, textvariable=var)
                    row_entry.pack(side="left", fill="x", expand=True, padx=(4, 4))
                    row_browse = ttk.Button(
                        row,
                        text="Browse…",
                        command=lambda: _browse_texture_path(var, browse_title),
                    )
                    row_browse.pack(side="left")
                    self._add_tooltip(row_label, tooltip)
                    self._add_tooltip(row_entry, tooltip)
                    self._add_tooltip(row_browse, tooltip)

                _tex_row(
                    tex_frame,
                    "Diffuse / base:",
                    diffuse_tex_var,
                    "Select diffuse texture",
                    "🧾 Slot 0 diffuse/albedo texture path. Usually the base <stem>.dds texture.",
                )
                _tex_row(
                    tex_frame,
                    "Parallax / _p.dds:",
                    parallax_tex_var,
                    "Select parallax texture",
                    "🗺 Slot 3 parallax texture path. Usually ends with _p.dds.",
                )
                _tex_row(
                    tex_frame,
                    "Normal / _n or _msn:",
                    normal_tex_var,
                    "Select normal or MSN texture",
                    "🧱 Slot 1 normal path. Use _n for vanilla/CS, or _msn for ENB complex material workflows.",
                )
                _tex_row(
                    tex_frame,
                    "Glow / _em.dds (legacy _g.dds):",
                    glow_tex_var,
                    "Select glow texture",
                    "✨ Slot 2 emissive/glow texture path. Prefer _em.dds; _g.dds is legacy-compatible.",
                )
                _tex_row(
                    tex_frame,
                    "Cubemap / _e/_env:",
                    cubemap_tex_var,
                    "Select cubemap texture",
                    "🧊 Slot 4 cubemap/environment texture path (often _e/_env/_cube naming).",
                )
                _tex_row(
                    tex_frame,
                    "Env mask / _m/_cm/_rmaos:",
                    env_mask_tex_var,
                    "Select environment mask texture",
                    "🪞 Slot 5 environment mask path. Usually _m.dds (standard), _cm/_c.dds (CS complex), or _rmaos.dds (TruePBR; usually under textures\\pbr\\...).",
                )

                def _auto_fill_paths() -> None:
                    path_value = nif_path_var.get().strip()
                    if not path_value:
                        status_var.set("Pick a NIF file or folder first, then use Auto-fill.")
                        return
                    recommended_profile = _recommended_nif_editor_profile()
                    defaults = resolve_nif_patch_defaults_for_render_profile(
                        renderer_profile_var.get(),
                        recommended_profile=recommended_profile,
                    )
                    prefer_msn = bool(defaults.get("prefer_msn_normal"))
                    env_mask_suffix = _preferred_env_mask_suffix_for_profile()
                    selected_path = Path(path_value)
                    nifs = list(selected_path.rglob("*.nif")) if selected_path.is_dir() else [selected_path]
                    guessed_from: Path | None = None
                    guessed_diffuse = ""
                    guessed_parallax = ""
                    guessed_normal = ""
                    guessed_glow = ""
                    guessed_cubemap = ""
                    guessed_env = ""
                    for nif_candidate in nifs:
                        if not nif_candidate.exists():
                            continue
                        candidate_parallax = guess_parallax_path_for_nif(nif_candidate) or ""
                        candidate_normal = guess_normal_path_for_nif(nif_candidate, msn=prefer_msn) or ""
                        candidate_env = ""
                        try:
                            infos = scan_nif(nif_candidate)
                        except Exception:
                            infos = []
                        for info in infos:
                            texture_paths = getattr(info, "texture_paths", {})
                            if not candidate_parallax:
                                candidate_parallax = str(texture_paths.get(3, "")).strip()
                            if not candidate_normal:
                                candidate_normal = str(texture_paths.get(1, "")).strip()
                            if not candidate_env:
                                candidate_env = str(texture_paths.get(5, "")).strip()
                            candidate_glow = str(texture_paths.get(2, "")).strip()
                            candidate_cubemap = str(texture_paths.get(4, "")).strip()
                            if not guessed_glow and candidate_glow:
                                guessed_glow = candidate_glow
                            if not guessed_cubemap and candidate_cubemap:
                                guessed_cubemap = candidate_cubemap
                            diffuse_path = str(texture_paths.get(0, "")).strip()
                            if diffuse_path:
                                guessed_diffuse = guessed_diffuse or diffuse_path
                                diffuse_like = Path(diffuse_path.replace("\\", "/"))
                                diffuse_stem = _normalize_texture_family_stem(diffuse_like)
                                if not candidate_parallax:
                                    candidate_parallax = str(diffuse_like.parent / f"{diffuse_stem}_p.dds").replace("/", "\\")
                                if not candidate_normal:
                                    normal_suffix = "_msn.dds" if prefer_msn else "_n.dds"
                                    candidate_normal = str(diffuse_like.parent / f"{diffuse_stem}{normal_suffix}").replace("/", "\\")
                                if not guessed_glow:
                                    guessed_glow = str(diffuse_like.parent / f"{diffuse_stem}_em.dds").replace("/", "\\")
                                if not candidate_env:
                                    candidate_env = str(diffuse_like.parent / f"{diffuse_stem}{env_mask_suffix}").replace("/", "\\")
                            if candidate_parallax and candidate_normal and candidate_env:
                                break
                        if not candidate_env:
                            candidate_env = (
                                guess_env_mask_path_for_nif(
                                    nif_candidate,
                                    preferred_suffix=env_mask_suffix,
                                )
                                or ""
                            )
                        candidate_parallax = candidate_parallax.replace("/", "\\")
                        candidate_normal = candidate_normal.replace("/", "\\")
                        candidate_env = candidate_env.replace("/", "\\")
                        guessed_glow = guessed_glow.replace("/", "\\")
                        guessed_cubemap = guessed_cubemap.replace("/", "\\")
                        guessed_diffuse = guessed_diffuse.replace("/", "\\")
                        if candidate_parallax or candidate_normal or candidate_env or guessed_diffuse or guessed_glow or guessed_cubemap:
                            guessed_from = nif_candidate
                            guessed_parallax = candidate_parallax
                            guessed_normal = candidate_normal
                            guessed_env = candidate_env
                            break
                    if not guessed_from:
                        status_var.set("Auto-fill could not find any usable diffuse/normal texture slots in the selected NIF(s).")
                        return
                    guessed_parallax = _normalize_nif_texture_input_path(guessed_parallax) if guessed_parallax else ""
                    guessed_normal = _normalize_nif_texture_input_path(guessed_normal) if guessed_normal else ""
                    guessed_env = _normalize_nif_texture_input_path(guessed_env) if guessed_env else ""
                    guessed_glow = _normalize_nif_texture_input_path(guessed_glow) if guessed_glow else ""
                    guessed_cubemap = _normalize_nif_texture_input_path(guessed_cubemap) if guessed_cubemap else ""
                    guessed_diffuse = _normalize_nif_texture_input_path(guessed_diffuse) if guessed_diffuse else ""
                    changed = 0
                    if guessed_diffuse:
                        if diffuse_tex_var.get().strip() != guessed_diffuse:
                            changed += 1
                        diffuse_tex_var.set(guessed_diffuse)
                    if guessed_parallax:
                        if parallax_tex_var.get().strip() != guessed_parallax:
                            changed += 1
                        parallax_tex_var.set(guessed_parallax)
                    if guessed_normal:
                        if normal_tex_var.get().strip() != guessed_normal:
                            changed += 1
                        normal_tex_var.set(guessed_normal)
                    if guessed_glow:
                        if glow_tex_var.get().strip() != guessed_glow:
                            changed += 1
                        glow_tex_var.set(guessed_glow)
                    if guessed_cubemap:
                        if cubemap_tex_var.get().strip() != guessed_cubemap:
                            changed += 1
                        cubemap_tex_var.set(guessed_cubemap)
                    env_mask_guess = guessed_env
                    if not env_mask_guess and guessed_parallax.lower().endswith("_p.dds"):
                        env_mask_guess = _normalize_nif_texture_input_path(guessed_parallax[:-6] + env_mask_suffix)
                    if env_mask_guess:
                        if env_mask_tex_var.get().strip() != env_mask_guess:
                            changed += 1
                        env_mask_tex_var.set(env_mask_guess)
                    if changed == 0:
                        status_var.set(f"Auto-fill checked {guessed_from.name}; current paths already match the best guess.")
                    else:
                        status_var.set(f"Auto-filled {changed} texture path(s) from {guessed_from.name}.")

                auto_fill_button = ttk.Button(tex_frame, text="Auto-fill paths from selected NIF", command=_auto_fill_paths)
                auto_fill_button.pack(anchor="w", pady=(4, 0))
                self._add_tooltip(
                    auto_fill_button,
                    "🧠 Guesses texture slots from the selected NIF.\nGreat for speed, still worth eyeballing before you hit Patch.",
                )
                self._add_tooltip(
                    tex_frame,
                    "Use textures\\... paths for slots. Browsing a file auto-converts Data\\Textures picks into Skyrim-relative paths.",
                )

                btn_frame = ttk.Frame(win)
                btn_frame.pack(fill="x", padx=10, pady=(4, 2))
                status_var = tk.StringVar(value="Ready. Pick a NIF file/folder, then Scan or Patch.")
                ttk.Label(win, textvariable=status_var, wraplength=860).pack(fill="x", padx=10, pady=(0, 4))
                progress_var = tk.DoubleVar(value=0.0)
                progress_bar = ttk.Progressbar(win, orient="horizontal", mode="determinate", variable=progress_var, maximum=100)
                progress_bar.pack(fill="x", padx=10, pady=(0, 6))

                res_frame = ttk.LabelFrame(
                    win,
                    text="Results Log (resizable; right-click rows to copy)",
                    padding=6,
                )
                res_frame.pack(fill="both", expand=True, padx=10, pady=(0, 8))
                results_pane = ttk.Panedwindow(res_frame, orient="vertical")
                results_pane.pack(fill="both", expand=True)
                results_list_frame = ttk.Frame(results_pane)
                results_details_frame = ttk.LabelFrame(results_pane, text="Selected row details", padding=6)
                results_pane.add(results_list_frame, weight=4)
                results_pane.add(results_details_frame, weight=3)
                style = ttk.Style(win)
                tree_style_name = "NifEditor.Treeview"
                tree_select_bg = "#5a7fd6" if dark else "#c8dafc"
                style.configure(
                    tree_style_name,
                    background=entry_bg,
                    fieldbackground=entry_bg,
                    foreground=fg,
                    rowheight=22,
                )
                style.configure(f"{tree_style_name}.Heading", background=bg, foreground=fg)
                style.map(
                    tree_style_name,
                    background=[("selected", tree_select_bg)],
                    foreground=[("selected", fg)],
                )
                results_tree = ttk.Treeview(
                    results_list_frame,
                    columns=("status", "file", "details"),
                    show="headings",
                    selectmode="browse",
                    style=tree_style_name,
                )
                results_tree.heading("status", text="Status")
                results_tree.heading("file", text="File")
                results_tree.heading("details", text="Details")
                results_tree.column("status", width=90, minwidth=80, anchor="center")
                results_tree.column("file", width=260, minwidth=200, anchor="w")
                results_tree.column("details", width=820, minwidth=320, anchor="w")
                results_scroll = ttk.Scrollbar(results_list_frame, command=results_tree.yview)
                results_scroll_x = ttk.Scrollbar(results_list_frame, command=results_tree.xview, orient="horizontal")
                results_tree.configure(yscrollcommand=results_scroll.set, xscrollcommand=results_scroll_x.set)
                results_scroll.pack(side="right", fill="y")
                results_scroll_x.pack(side="bottom", fill="x")
                results_tree.pack(fill="both", expand=True, side="left")
                result_details_text = tk.Text(
                    results_details_frame,
                    wrap="word",
                    height=8,
                    background=entry_bg,
                    foreground=fg,
                    insertbackground=fg,
                    relief="flat",
                    borderwidth=0,
                )
                result_details_scroll = ttk.Scrollbar(results_details_frame, command=result_details_text.yview)
                result_details_text.configure(yscrollcommand=result_details_scroll.set)
                result_details_scroll.pack(side="right", fill="y")
                result_details_text.pack(fill="both", expand=True, side="left")
                self._add_tooltip(
                    results_tree,
                    "Results log for scan/patch/restore actions. Resize the window or drag the splitter to inspect everything.",
                )
                self._add_tooltip(
                    result_details_text,
                    "Full untruncated details for the selected row.",
                )

                full_row_details: dict[str, str] = {}

                def _set_result_details_text(text: str) -> None:
                    result_details_text.configure(state="normal")
                    result_details_text.delete("1.0", "end")
                    result_details_text.insert("1.0", text)
                    result_details_text.configure(state="disabled")

                _set_result_details_text("Select a row to inspect full multi-line details without truncation.")

                def _add_result_row(status: str, file_name: str, details: str) -> None:
                    normalized_details = _normalize_nif_result_details(details)
                    row_preview = _format_nif_result_row_details(normalized_details)
                    item_id = results_tree.insert("", "end", values=(status, file_name, row_preview))
                    full_row_details[str(item_id)] = normalized_details
                    results_tree.selection_set(item_id)
                    results_tree.focus(item_id)
                    _set_result_details_text(f"[{status}] {file_name}\n\n{normalized_details}")
                    status_var.set(f"{status}: {file_name} — {normalized_details}")
                    results_tree.yview_moveto(1.0)

                def _clear_log() -> None:
                    for item in results_tree.get_children():
                        results_tree.delete(item)
                    full_row_details.clear()
                    _set_result_details_text("Select a row to inspect full multi-line details without truncation.")
                    status_var.set("Results cleared.")
                    progress_var.set(0.0)

                def _on_result_selected(_event: object | None = None) -> None:
                    selected = results_tree.selection()
                    if not selected:
                        return
                    row_values = results_tree.item(selected[0], "values")
                    if not row_values:
                        return
                    full_details = full_row_details.get(str(selected[0]), str(row_values[2]))
                    _set_result_details_text(f"[{row_values[0]}] {row_values[1]}\n\n{full_details}")
                    status_var.set(f"[{row_values[0]}] {row_values[1]} — {full_details}")

                def _copy_selected_result() -> None:
                    selected = results_tree.selection()
                    if not selected:
                        status_var.set("No result row selected to copy.")
                        return
                    row_values = results_tree.item(selected[0], "values")
                    if not row_values:
                        status_var.set("No result row selected to copy.")
                        return
                    full_details = full_row_details.get(str(selected[0]), str(row_values[2]))
                    text = f"[{row_values[0]}] {row_values[1]} — {full_details}"
                    win.clipboard_clear()
                    win.clipboard_append(text)
                    status_var.set("Copied selected result to clipboard.")

                def _copy_all_results() -> None:
                    items = results_tree.get_children()
                    if not items:
                        status_var.set("No results to copy yet.")
                        return
                    lines: list[str] = []
                    for item in items:
                        row_values = results_tree.item(item, "values")
                        if row_values:
                            full_details = full_row_details.get(str(item), str(row_values[2]))
                            lines.append(f"[{row_values[0]}] {row_values[1]} — {full_details}")
                    win.clipboard_clear()
                    win.clipboard_append("\n".join(lines))
                    status_var.set(f"Copied {len(lines)} result row(s) to clipboard.")

                context_menu = tk.Menu(win, tearoff=False)
                context_menu.add_command(label="Copy selected row", command=_copy_selected_result)
                context_menu.add_command(label="Copy all rows", command=_copy_all_results)

                def _show_tree_context_menu(event: tk.Event[tk.Misc]) -> None:
                    item_id = results_tree.identify_row(event.y)
                    if item_id:
                        results_tree.selection_set(item_id)
                        results_tree.focus(item_id)
                        _on_result_selected()
                    context_menu.tk_popup(event.x_root, event.y_root)

                results_tree.bind("<<TreeviewSelect>>", _on_result_selected)
                results_tree.bind("<Button-3>", _show_tree_context_menu)

                def _resolve_nifs() -> list[Path]:
                    path_value = nif_path_var.get().strip()
                    if not path_value:
                        return []
                    root_path = Path(path_value)
                    if root_path.is_dir():
                        return list(root_path.rglob("*.nif"))
                    if root_path.suffix.lower() == ".nif":
                        return [root_path]
                    return []

                def _scan_nifs() -> None:
                    nifs = _resolve_nifs()
                    _clear_log()
                    if not nifs:
                        _add_result_row("WARN", "—", "No NIF files found.")
                        return
                    status_var.set(f"Scanning {len(nifs)} NIF file(s)…")
                    progress_bar.configure(maximum=max(1, len(nifs)))
                    progress_var.set(0.0)
                    win.update_idletasks()
                    for index, nif in enumerate(nifs, start=1):
                        try:
                            validation = validate_nif_for_parallax(nif)
                            combined_detail_lines = list(validation.issues[:4])
                            if validation.suggestions:
                                combined_detail_lines.extend(f"Suggestion: {text}" for text in validation.suggestions[:2])
                            if validation.ready_count == validation.shader_count and validation.shader_count > 0:
                                _add_result_row(
                                    "OK",
                                    nif.name,
                                    f"{validation.ready_count}/{validation.shader_count} shader(s) already parallax-ready",
                                )
                            elif validation.shader_count == 0:
                                if combined_detail_lines:
                                    _add_result_row("SKIP", nif.name, "\n".join(combined_detail_lines))
                                else:
                                    _add_result_row("SKIP", nif.name, "No BSLightingShaderProperty found.")
                            else:
                                issue_text = "\n".join(combined_detail_lines[:6]) if combined_detail_lines else "Needs patching."
                                _add_result_row(
                                    "WARN",
                                    nif.name,
                                    f"{validation.ready_count}/{validation.shader_count} ready.\n{issue_text}",
                                )
                        except Exception as exc:
                            _add_result_row("FAIL", nif.name, f"Scan failed: {exc}")
                        progress_var.set(float(index))
                        win.update_idletasks()
                    status_var.set(f"Scan complete: {len(nifs)} file(s) reviewed.")

                def _run_patch() -> None:
                    nifs = _resolve_nifs()
                    _clear_log()
                    if not nifs:
                        _add_result_row("WARN", "—", "No NIF files found at the selected path.")
                        return
                    warnings_to_confirm = get_nif_patch_option_warnings(
                        selected_profile=str(renderer_profile_var.get() or "auto"),
                        recommended_profile=_recommended_nif_editor_profile(),
                        enable_parallax=enable_parallax_var.get(),
                        enable_pom=enable_pom_var.get(),
                        enable_env_mapping=enable_env_var.get(),
                        force_shader_type_3=force_type3_var.get(),
                        diffuse_texture_path=diffuse_tex_var.get(),
                        parallax_texture_path=parallax_tex_var.get(),
                        normal_texture_path=normal_tex_var.get(),
                        glow_texture_path=glow_tex_var.get(),
                        env_mask_texture_path=env_mask_tex_var.get(),
                        cubemap_texture_path=cubemap_tex_var.get(),
                        disable_parallax=disable_parallax_var.get(),
                        disable_pom=disable_pom_var.get(),
                        disable_env_mapping=disable_env_var.get(),
                        disable_glow_map=disable_glow_var.get(),
                        clear_parallax_texture_path=clear_parallax_var.get(),
                        clear_normal_texture_path=clear_normal_var.get(),
                        clear_env_mask_texture_path=clear_env_var.get(),
                        clear_glow_texture_path=clear_glow_var.get(),
                        clear_diffuse_texture_path=clear_diffuse_var.get(),
                        clear_cubemap_texture_path=clear_cubemap_var.get(),
                    )
                    if warnings_to_confirm:
                        proceed = messagebox.askyesno(
                            "Confirm patch options",
                            "Potential option mismatch detected:\n\n"
                            + "\n".join(w for w in warnings_to_confirm if w)
                            + "\n\nContinue anyway?",
                            parent=win,
                        )
                        if not proceed:
                            status_var.set("Patch cancelled so options can be adjusted.")
                            return
                    options = NifPatchOptions(
                        enable_parallax=enable_parallax_var.get(),
                        enable_pom=enable_pom_var.get(),
                        enable_env_mapping=enable_env_var.get(),
                        enable_glow_map=enable_glow_var.get(),
                        parallax_scale=pscale_var.get() if (enable_parallax_var.get() or enable_pom_var.get()) else None,
                        force_shader_type_3=force_type3_var.get(),
                        diffuse_texture_path=diffuse_tex_var.get().strip() or None,
                        parallax_texture_path=parallax_tex_var.get().strip() or None,
                        normal_texture_path=normal_tex_var.get().strip() or None,
                        glow_texture_path=glow_tex_var.get().strip() or None,
                        env_mask_texture_path=env_mask_tex_var.get().strip() or None,
                        cubemap_texture_path=cubemap_tex_var.get().strip() or None,
                        backup=backup_var.get(),
                        dry_run=dry_run_var.get(),
                    )
                    mode_label = "dry-run patching" if options.dry_run else "patching"
                    status_var.set(f"Starting {mode_label} for {len(nifs)} NIF file(s)…")
                    progress_bar.configure(maximum=max(1, len(nifs)))
                    progress_var.set(0.0)
                    win.update_idletasks()
                    ok = skip = fail = 0
                    for index, nif in enumerate(nifs, start=1):
                        try:
                            result = patch_nif(nif, options)
                            if result.already_up_to_date:
                                skip += 1
                                _add_result_row("SKIP", nif.name, "Already up-to-date.")
                            elif result.success:
                                ok += 1
                                _add_result_row("OK", nif.name, result.message)
                            else:
                                fail += 1
                                _add_result_row("FAIL", nif.name, result.message)
                                for err in result.errors:
                                    _add_result_row("FAIL", nif.name, err)
                        except Exception as exc:
                            fail += 1
                            _add_result_row("FAIL", nif.name, f"Patch failed: {exc}")
                        progress_var.set(float(index))
                        win.update_idletasks()
                    status_var.set(f"Done — {ok} patched, {skip} skipped, {fail} failed.")

                def _run_unpatch() -> None:
                    nifs = _resolve_nifs()
                    _clear_log()
                    if not nifs:
                        _add_result_row("WARN", "—", "No NIF files found at the selected path.")
                        return
                    selected_unpatch_actions = [
                        disable_parallax_var.get(),
                        disable_pom_var.get(),
                        disable_env_var.get(),
                        disable_glow_var.get(),
                        clear_parallax_var.get(),
                        clear_normal_var.get(),
                        clear_env_var.get(),
                        clear_glow_var.get(),
                        clear_diffuse_var.get(),
                        clear_cubemap_var.get(),
                    ]
                    if not any(selected_unpatch_actions):
                        _add_result_row("WARN", "—", "Pick at least one Remove Features checkbox before unpatching.")
                        return
                    options = NifPatchOptions(
                        disable_parallax=disable_parallax_var.get(),
                        disable_pom=disable_pom_var.get(),
                        disable_env_mapping=disable_env_var.get(),
                        disable_glow_map=disable_glow_var.get(),
                        clear_parallax_texture_path=clear_parallax_var.get(),
                        clear_normal_texture_path=clear_normal_var.get(),
                        clear_env_mask_texture_path=clear_env_var.get(),
                        clear_glow_texture_path=clear_glow_var.get(),
                        clear_diffuse_texture_path=clear_diffuse_var.get(),
                        clear_cubemap_texture_path=clear_cubemap_var.get(),
                        backup=backup_var.get(),
                        dry_run=dry_run_var.get(),
                    )
                    mode_label = "dry-run unpatching" if options.dry_run else "unpatching"
                    status_var.set(f"Starting {mode_label} for {len(nifs)} NIF file(s)…")
                    progress_bar.configure(maximum=max(1, len(nifs)))
                    progress_var.set(0.0)
                    win.update_idletasks()
                    ok = skip = fail = 0
                    for index, nif in enumerate(nifs, start=1):
                        try:
                            result = patch_nif(nif, options)
                            if result.already_up_to_date:
                                skip += 1
                                _add_result_row("SKIP", nif.name, "Already up-to-date.")
                            elif result.success:
                                ok += 1
                                _add_result_row("OK", nif.name, result.message)
                            else:
                                fail += 1
                                _add_result_row("FAIL", nif.name, result.message)
                                for err in result.errors:
                                    _add_result_row("FAIL", nif.name, err)
                        except Exception as exc:
                            fail += 1
                            _add_result_row("FAIL", nif.name, f"Unpatch failed: {exc}")
                        progress_var.set(float(index))
                        win.update_idletasks()
                    status_var.set(f"Done — {ok} unpatched, {skip} skipped, {fail} failed.")

                def _run_restore_backups() -> None:
                    nifs = _resolve_nifs()
                    _clear_log()
                    if not nifs:
                        _add_result_row("WARN", "—", "No NIF files found at the selected path.")
                        return
                    proceed = messagebox.askyesno(
                        "Restore from backups",
                        "Restore selected NIF file(s) from sibling .nif.bak backups?\n"
                        "This will overwrite current .nif files.",
                        parent=win,
                    )
                    if not proceed:
                        status_var.set("Restore cancelled.")
                        return
                    status_var.set(f"Restoring backups for {len(nifs)} NIF file(s)…")
                    progress_bar.configure(maximum=max(1, len(nifs)))
                    progress_var.set(0.0)
                    win.update_idletasks()
                    ok = skip = fail = 0
                    for index, (row_status, file_name, details) in enumerate(restore_nif_backups(nifs), start=1):
                        _add_result_row(row_status, file_name, details)
                        if row_status == "OK":
                            ok += 1
                        elif row_status == "SKIP":
                            skip += 1
                        else:
                            fail += 1
                        progress_var.set(float(index))
                        win.update_idletasks()
                    status_var.set(f"Restore complete — {ok} restored, {skip} skipped, {fail} failed.")

                scan_button = ttk.Button(btn_frame, text="Scan NIFs", command=_scan_nifs)
                scan_button.pack(side="left", padx=(0, 6))
                patch_button = ttk.Button(btn_frame, text="Apply patch", command=_run_patch)
                patch_button.pack(side="left", padx=(0, 6))
                unpatch_button = ttk.Button(btn_frame, text="Remove features (unpatch)", command=_run_unpatch)
                unpatch_button.pack(side="left", padx=(0, 6))
                restore_button = ttk.Button(btn_frame, text="Restore from .bak", command=_run_restore_backups)
                restore_button.pack(side="left", padx=(0, 6))
                clear_button = ttk.Button(btn_frame, text="Clear log", command=_clear_log)
                clear_button.pack(side="left")
                copy_selected_button = ttk.Button(btn_frame, text="Copy selected", command=_copy_selected_result)
                copy_selected_button.pack(side="left", padx=(6, 0))
                copy_all_button = ttk.Button(btn_frame, text="Copy all", command=_copy_all_results)
                copy_all_button.pack(side="left", padx=(6, 0))
                close_button = ttk.Button(btn_frame, text="Close", command=win.destroy)
                close_button.pack(side="right")
                self._add_tooltip(scan_button, "🔍 Read-only analysis pass. No file changes, just receipts.")
                self._add_tooltip(patch_button, "🛠 Actually writes patch changes. This is the button with consequences.")
                self._add_tooltip(unpatch_button, "↩ Removes selected flags/slots so you can undo or simplify prior NIF patching.")
                self._add_tooltip(restore_button, "♻ Restores .nif files from sibling .nif.bak backups.")
                self._add_tooltip(clear_button, "🧽 Clears rows so your brain can breathe again.")
                self._add_tooltip(copy_selected_button, "📎 Copies only the selected row — ideal for Discord bragging or bug reports.")
                self._add_tooltip(copy_all_button, "📦 Copies every row in one go for logs/changelists.")
                self._add_tooltip(close_button, "🚪 Closes this window. Your NIFs will not feel abandoned.")
                win.update_idletasks()
            except Exception as exc:
                try:
                    win.destroy()
                except Exception:
                    pass
                messagebox.showerror("NIF Editor failed", f"The NIF editor could not open correctly:\n\n{exc}", parent=self.root)

        def run(self) -> None:
            self.root.mainloop()
else:
    class TextureGeneratorGUI:
        def run(self) -> None:
            raise RuntimeError("GUI dependencies are unavailable in this environment.")


def main() -> int:
    args = _apply_cli_pbr_overrides(parse_args())
    if args.gui or args.input_file is None:
        if not GUI_AVAILABLE:
            raise RuntimeError("GUI dependencies are unavailable in this environment.")
        try:
            TextureGeneratorGUI().run()
        except Exception as exc:
            if tk is not None and isinstance(exc, tk.TclError):
                raise RuntimeError(
                    "GUI could not start because no desktop display is available in this environment."
                ) from exc
            raise
        return 0

    if getattr(args, "pbr_material", False):
        cli_recommended_profile: str | None = None
    elif args.input_file.is_dir():
        cli_recommended_profile: str | None = None
    else:
        role_info = identify_skyrim_texture_role(args.input_file)
        workflow_profile = detect_workflow_profile(args.input_file)
        material_type = classify_material_type(args.input_file)
        cli_recommended_profile = recommend_render_profile(
            args.input_file,
            detected_role=role_info["role"] if role_info is not None else None,
            detected_suffix=role_info["suffix"] if role_info is not None else None,
            material_type=material_type,
            workflow_profile=workflow_profile,
        )
    cli_guardrails = resolve_render_profile_guardrails(
        selected_profile=getattr(args, "render_profile", "auto"),
        recommended_profile=cli_recommended_profile,
        complex_format=args.complex_format,
        env_mask_mode=args.environment_mask_mode,
        parallax_mode=args.parallax_mode,
    )
    cli_guardrail_changes = list(cli_guardrails["changes"])
    args.complex_format = str(cli_guardrails["complex_format"])
    args.environment_mask_mode = str(cli_guardrails["env_mask_mode"])
    args.parallax_mode = str(cli_guardrails["parallax_mode"])
    if cli_guardrail_changes:
        effective_label = _RENDER_PROFILE_LABELS.get(
            str(cli_guardrails["effective_profile"]),
            str(cli_guardrails["effective_profile"]).replace("_", " ").title(),
        )
        print(
            f"[renderer guardrails] Applied {effective_label}: " + "; ".join(cli_guardrail_changes),
            file=sys.stderr,
        )

    if args.input_file.is_dir():
        failures: list[tuple[Path, str]] = []
        batch_outputs = run_batch_with_options(
            input_path=args.input_file,
            output_dir=args.output_dir,
            diffuse_name=args.diffuse_name,
            normal_name=args.normal_name,
            parallax_name=args.parallax_name,
            glow_name=args.glow_name,
            environment_mask_name=args.environment_mask_name,
            rmaos_name=args.rmaos_name,
            complex_name=args.complex_name,
            normal_strength=args.normal_strength,
            parallax_strength=args.parallax_strength,
            glow_threshold=args.glow_threshold,
            environment_mask_strength=args.environment_mask_strength,
            rmaos_strength=args.rmaos_strength,
            complex_strength=args.complex_strength,
            specular_strength=args.specular_strength,
            complex_format=args.complex_format,
            env_mask_mode=args.environment_mask_mode,
            emboss_mode=args.emboss_mode,
            relief_mode=args.relief_mode,
            parallax_mode=args.parallax_mode,
            include_diffuse=not args.no_diffuse,
            include_normal=not args.no_normal,
            include_parallax=not args.no_parallax,
            include_glow=args.glow_map,
            include_environment_mask=args.environment_mask,
            include_rmaos=args.rmaos,
            include_wetness_mask=args.wetness_mask,
            wetness_mask_strength=args.wetness_mask_strength,
            include_snow_mask=args.snow_mask,
            snow_mask_strength=args.snow_mask_strength,
            include_complex=args.complex_material,
            render_profile=getattr(args, "render_profile", "auto"),
            continue_on_error=True,
            batch_workers=args.batch_workers,
            error_callback=lambda _index, _total, current, exc: failures.append((current, str(exc))),
        )
        for input_file, outputs in batch_outputs.items():
            print(f"[{input_file.name}]")
            for output_type, path in outputs.items():
                print(f"  {output_type.replace('_', ' ').title()} texture: {path}")
        if failures:
            print("\nSome files failed during batch processing:", file=sys.stderr)
            for file_path, error_message in failures:
                print(f"- {file_path}: {error_message}", file=sys.stderr)
            return 1
        return 0

    outputs = run_with_options(
        input_file=args.input_file,
        output_dir=args.output_dir,
        diffuse_name=args.diffuse_name,
        normal_name=args.normal_name,
        parallax_name=args.parallax_name,
        glow_name=args.glow_name,
        environment_mask_name=args.environment_mask_name,
        rmaos_name=args.rmaos_name,
        complex_name=args.complex_name,
        normal_strength=args.normal_strength,
        parallax_strength=args.parallax_strength,
        glow_threshold=args.glow_threshold,
        environment_mask_strength=args.environment_mask_strength,
        rmaos_strength=args.rmaos_strength,
        complex_strength=args.complex_strength,
        specular_strength=args.specular_strength,
        complex_format=args.complex_format,
        env_mask_mode=args.environment_mask_mode,
        emboss_mode=args.emboss_mode,
        relief_mode=args.relief_mode,
        parallax_mode=args.parallax_mode,
        include_diffuse=not args.no_diffuse,
        include_normal=not args.no_normal,
        include_parallax=not args.no_parallax,
        include_glow=args.glow_map,
        include_environment_mask=args.environment_mask,
        include_rmaos=args.rmaos,
        include_wetness_mask=args.wetness_mask,
        wetness_mask_strength=args.wetness_mask_strength,
        include_snow_mask=args.snow_mask,
        snow_mask_strength=args.snow_mask_strength,
        include_complex=args.complex_material,
        render_profile=getattr(args, "render_profile", "auto"),
    )
    for output_type, path in outputs.items():
        print(f"{output_type.replace('_', ' ').title()} texture: {path}")
    return 0


def _run_cli() -> int:
    try:
        return main()
    except RuntimeError as exc:
        message = str(exc)
        if message == "GUI dependencies are unavailable in this environment.":
            print(
                "Error: GUI dependencies are unavailable in this environment. "
                "Install tkinter support or provide an input file to run in CLI mode.",
                file=sys.stderr,
            )
            return 1
        print(f"Error: {message}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_run_cli())
