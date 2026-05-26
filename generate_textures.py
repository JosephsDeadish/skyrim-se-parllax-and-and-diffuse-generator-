from __future__ import annotations

import argparse
import concurrent.futures
import gc
import math
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import uuid
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
    GUI_AVAILABLE = False


DDS_EXTENSION = ".dds"
APP_VERSION = "0.5"
SUPPORTED_INPUT_EXTENSIONS = {DDS_EXTENSION, ".png", ".jpg", ".jpeg", ".tga", ".bmp"}
GENERATED_TEXTURE_SUFFIXES = ("_msn", "_cm", "_n", "_p", "_g", "_m")
PREVIEW_MAX_DIMENSION = 1024
PREVIEW_SIZE_PRESETS: dict[str, tuple[int, int]] = {
    "XS": (160, 120),
    "Small": (240, 180),
    "Medium": (340, 250),
    "Large": (460, 340),
    "XL": (620, 460),
}


@dataclass(frozen=True)
class ModManagerContext:
    manager: str | None = None
    profile_name: str | None = None
    game_id: str | None = None
    instance_root: Path | None = None
    staging_root: Path | None = None
    output_dir: Path | None = None
    loaded_mods: tuple[str, ...] = ()
    loaded_texture_dirs: tuple[Path, ...] = ()

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
        return " — ".join(details)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


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


def _find_manager_instance_root(start: Path, required_children: tuple[str, ...]) -> Path | None:
    for candidate in (start, *start.parents):
        if all((candidate / child).exists() for child in required_children):
            return candidate
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
    loaded_texture_dirs: tuple[Path, ...] = ()
    if instance_root is not None and resolved_profile:
        modlist_path = instance_root / "profiles" / resolved_profile / "modlist.txt"
        loaded_mods = _parse_enabled_modlist(modlist_path)
        loaded_texture_dirs = _unique_existing_paths(
            [instance_root / "mods" / mod_name / "textures" for mod_name in loaded_mods]
        )

    output_dir = instance_root / "overwrite" if instance_root is not None else None
    return ModManagerContext(
        manager="Mod Organizer 2",
        profile_name=resolved_profile,
        game_id="skyrimse",
        instance_root=instance_root,
        output_dir=output_dir,
        loaded_mods=loaded_mods,
        loaded_texture_dirs=loaded_texture_dirs,
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
    if staging_root is not None and loaded_mods:
        texture_dirs = _unique_existing_paths([staging_root / mod_name / "textures" for mod_name in loaded_mods])
    elif staging_root is not None:
        texture_dirs = _unique_existing_paths(
            [path / "textures" for path in staging_root.iterdir() if path.is_dir() and (path / "textures").exists()]
        )
    else:
        texture_dirs = ()

    return ModManagerContext(
        manager="Vortex",
        profile_name=profile_name,
        game_id=game_id,
        staging_root=staging_root,
        output_dir=executable_path.parent / "generated_textures",
        loaded_mods=loaded_mods,
        loaded_texture_dirs=texture_dirs,
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
    "stone": ("stone", "brick", "rock", "cobble", "slate", "granite", "marble", "limestone", "pebble", "rubble", "dungeon", "wall", "cave", "cliff"),
    "wood": ("wood", "timber", "plank", "log", "bark", "trunk", "beam", "wooden", "oak", "pine", "lumber"),
    "plants": ("leaf", "leaves", "grass", "vine", "plant", "moss", "fern", "weed", "shrub", "bush", "flora", "foliage", "lichen"),
    "metal": ("metal", "iron", "steel", "copper", "bronze", "gold", "silver", "ore", "chain", "blade", "sword", "axe", "armor", "helmet", "shield"),
    "glass": ("glass", "crystal", "gem", "jewel", "diamond", "ruby", "sapphire", "emerald", "amethyst", "quartz"),
    "cloth": ("cloth", "fabric", "silk", "linen", "wool", "robe", "cloak", "cape", "leather", "hide", "fur"),
    "skin": ("skin", "body", "face", "head", "hand", "flesh", "creature", "humanoid"),
    "snow": ("snow", "ice", "frost", "frozen", "blizzard", "glacial"),
    "sand": ("sand", "dirt", "mud", "earth", "soil", "ground", "terrain", "dust"),
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
        "waifu",
        "bbr",
        "poster",
    ),
}


def classify_material_type(path: Path) -> str:
    """Classify the likely Skyrim material category from a texture file path.

    Returns one of: ``'stone'``, ``'wood'``, ``'plants'``, ``'metal'``,
    ``'glass'``, ``'cloth'``, ``'skin'``, ``'snow'``, ``'sand'``, or
    ``'general'``.
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
    """Fine-tune generated slider recommendations for common Skyrim material categories."""
    adjusted = dict(recommended)
    if material_type == "stone":
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 1.1, 1.1, 3.8)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 1.1, 0.8, 2.4)
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.85, 0.9, 2.4)
        adjusted["glow_threshold"] = int(_clamp(float(adjusted["glow_threshold"]) * 1.05, 140, 235))
    elif material_type == "wood":
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 1.05, 1.1, 3.8)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 1.0, 0.8, 2.4)
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.7, 0.9, 2.4)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.75, 0.9, 2.2)
    elif material_type == "plants":
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 0.85, 1.1, 3.8)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.6, 0.8, 2.4)
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.5, 0.9, 2.4)
        adjusted["glow_threshold"] = int(_clamp(float(adjusted["glow_threshold"]) * 1.1, 140, 235))
    elif material_type == "metal":
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 1.15, 1.1, 3.8)
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 1.45, 0.9, 2.4)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 1.4, 0.9, 2.2)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.9, 0.8, 2.4)
    elif material_type == "glass":
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 1.6, 0.9, 2.4)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 1.5, 0.9, 2.2)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.7, 0.8, 2.4)
    elif material_type == "cloth":
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.55, 0.9, 2.4)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.65, 0.9, 2.2)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.75, 0.8, 2.4)
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 0.9, 1.1, 3.8)
    elif material_type == "skin":
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.65, 0.9, 2.4)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.8, 0.9, 2.2)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.5, 0.8, 2.4)
    elif material_type == "snow":
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 1.2, 0.9, 2.4)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 1.15, 0.8, 2.4)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 1.1, 0.9, 2.2)
    elif material_type == "sand":
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 1.05, 0.8, 2.4)
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.8, 0.9, 2.4)
    elif material_type == "paper":
        adjusted["normal_strength"] = _clamp(float(adjusted["normal_strength"]) * 0.8, 1.1, 2.2)
        adjusted["parallax_strength"] = _clamp(float(adjusted["parallax_strength"]) * 0.55, 0.8, 1.35)
        adjusted["environment_mask_strength"] = _clamp(float(adjusted["environment_mask_strength"]) * 0.45, 0.9, 1.3)
        adjusted["specular_strength"] = _clamp(float(adjusted["specular_strength"]) * 0.6, 0.9, 1.3)
        adjusted["complex_strength"] = _clamp(float(adjusted["complex_strength"]) * 0.75, 1.0, 1.6)
        adjusted["glow_threshold"] = int(_clamp(float(adjusted["glow_threshold"]) * 1.1, 160, 235))
    return adjusted


_WORKFLOW_PROFILE_TOKENS: dict[str, tuple[str, ...]] = {
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
    role_adjusted = _adjust_recommendations_for_role(recommended, detected_role)
    material_adjusted = _adjust_recommendations_for_material_type(role_adjusted, material_type)
    workflow_adjusted = _adjust_recommendations_for_workflow_profile(material_adjusted, workflow_profile)
    return _adjust_recommendations_for_role(workflow_adjusted, detected_role)


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
    grayscale = ImageOps.grayscale(source)
    resolution_scale = _clamp(math.sqrt((source.width * source.height) / (1024.0 * 1024.0)), 1.0, 2.6)
    broad = grayscale.filter(ImageFilter.GaussianBlur(radius=4.0 * resolution_scale))
    fine = grayscale.filter(
        ImageFilter.UnsharpMask(
            radius=1.2 + (resolution_scale * 0.45),
            percent=170,
            threshold=int(2 + (resolution_scale * 2)),
        )
    )
    detail = ImageChops.subtract(fine, broad, scale=1.0, offset=128)
    merged = ImageChops.add(
        grayscale,
        detail,
        scale=_clamp(1.55 - ((resolution_scale - 1.0) * 0.18), 1.18, 1.55),
        offset=int(_clamp(-36.0 + ((resolution_scale - 1.0) * 8.0), -36.0, -22.0)),
    )
    return ImageOps.autocontrast(merged, cutoff=1)


def _lift_black_floor(image: Image.Image, floor: int = 16) -> Image.Image:
    return image.point(lambda value: int(_clamp(max(float(floor), float(value)), 0.0, 255.0)))


def _detail_pressure(source: Image.Image) -> float:
    # Downsample to max 512 px for performance — the pressure signal is a scalar
    # statistic that does not require full resolution to be representative.
    analysis = source
    if max(source.width, source.height) > 512:
        analysis = source.copy()
        analysis.thumbnail((512, 512), Image.Resampling.NEAREST)
    grayscale = ImageOps.grayscale(analysis)
    high_freq = ImageChops.difference(grayscale, grayscale.filter(ImageFilter.GaussianBlur(radius=1.2)))
    return _clamp((float(ImageStat.Stat(high_freq).mean[0]) - 8.0) / 22.0, 0.0, 1.0)


def generate_diffuse(source: Image.Image) -> Image.Image:
    return ImageOps.autocontrast(source.convert("RGB"), cutoff=1)


def generate_parallax(source: Image.Image, strength: float = 1.35) -> Image.Image:
    pressure = _detail_pressure(source)
    height_map = _prepare_height_map(source)
    softened = height_map.filter(ImageFilter.GaussianBlur(radius=1.2))
    micro_detail = ImageChops.subtract(
        height_map,
        height_map.filter(ImageFilter.GaussianBlur(radius=8.0 + (pressure * 6.0))),
        scale=1.0,
        offset=128,
    )
    merged = ImageChops.add(softened, micro_detail, scale=1.35 - (pressure * 0.35), offset=int(-20 + (pressure * 10.0)))
    contrasted = ImageEnhance.Contrast(merged).enhance(_clamp(strength * (1.0 - (pressure * 0.18)), 0.8, 2.4))
    return ImageOps.autocontrast(contrasted, cutoff=1)


def generate_normal(source: Image.Image, strength: float = 2.0, directx: bool = True) -> Image.Image:
    source_grayscale = ImageOps.grayscale(source)
    source_min, source_max = source_grayscale.getextrema()
    if (source_max - source_min) <= 2:
        return Image.new("RGB", source.size, color=(128, 128, 255))

    pressure = _detail_pressure(source)
    resolution_scale = _clamp(math.sqrt((source.width * source.height) / (1024.0 * 1024.0)), 1.0, 2.8)
    effective_strength = _clamp(strength * (1.0 - (pressure * 0.2)), 0.9, 3.8)
    height_map = _prepare_height_map(source).filter(
        ImageFilter.GaussianBlur(radius=0.8 + (pressure * 0.9) + ((resolution_scale - 1.0) * 0.35))
    )
    sobel_x = height_map.filter(ImageFilter.Kernel((3, 3), (-1, 0, 1, -2, 0, 2, -1, 0, 1), scale=8, offset=128))
    sobel_y = height_map.filter(ImageFilter.Kernel((3, 3), (-1, -2, -1, 0, 0, 0, 1, 2, 1), scale=8, offset=128))
    green_sign = 1.0 if directx else -1.0

    # Vectorised numpy computation — orders of magnitude faster than a Python loop
    # for large textures (2 K / 4 K / 8 K) and produces identical pixel values.
    sx = np.frombuffer(sobel_x.tobytes(), dtype=np.uint8).astype(np.float32)
    sy = np.frombuffer(sobel_y.tobytes(), dtype=np.uint8).astype(np.float32)

    red_arr = np.clip(128.0 - (sx - 128.0) * effective_strength, 0.0, 255.0).astype(np.uint8)
    green_arr = np.clip(128.0 + (sy - 128.0) * effective_strength * green_sign, 0.0, 255.0).astype(np.uint8)

    normal_x = (red_arr.astype(np.float32) - 128.0) / 127.0
    normal_y = (green_arr.astype(np.float32) - 128.0) / 127.0
    horizontal_sq = normal_x * normal_x + normal_y * normal_y
    normal_z = np.sqrt(np.clip(1.0 - np.clip(horizontal_sq, 0.0, 1.0), 0.0, None))
    blue_arr = np.clip(128.0 + normal_z * 127.0, 128.0, 255.0).astype(np.uint8)

    h, w = height_map.size[1], height_map.size[0]
    red = Image.fromarray(red_arr.reshape(h, w), mode="L")
    green = Image.fromarray(green_arr.reshape(h, w), mode="L")
    blue = Image.fromarray(blue_arr.reshape(h, w), mode="L")
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
        ``"complex"`` — RGBA texture for ENBSeries Complex Parallax Material.
        Channel layout: R=env reflection amount, G=glossiness, B=metallic proxy,
        A=parallax height.  Requires ENBSeries with complex material support.

        ``"standard"`` (default) — Greyscale (L mode) texture for the vanilla Skyrim SE
        ``_m.dds`` texture slot (Slot 5 in the NIF).  Controls environment/specular
        reflection intensity only.  Works without any mods or ENBSeries.
        Brighter = more environment reflection.
    """
    if mode == "standard":
        return _generate_standard_env_mask(source, strength)

    rgb_source = source.convert("RGB")
    grayscale = ImageOps.grayscale(rgb_source).filter(ImageFilter.GaussianBlur(radius=0.8))
    red, green, blue = rgb_source.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
    chroma = ImageChops.subtract(maximum, minimum)

    env_amount = ImageEnhance.Contrast(grayscale).enhance(0.9 + (strength * 0.35))
    env_amount = _lift_black_floor(ImageOps.autocontrast(env_amount, cutoff=1), floor=10)

    glossiness = generate_specular(rgb_source, strength=max(0.9, min(2.0, strength + 0.15)))
    glossiness = ImageEnhance.Contrast(glossiness).enhance(0.9 + (strength * 0.25))
    glossiness = _lift_black_floor(glossiness, floor=5)

    metallic_proxy = ImageOps.invert(chroma).filter(ImageFilter.GaussianBlur(radius=0.9))
    metallic = Image.blend(grayscale, metallic_proxy, alpha=0.7)
    metallic = ImageEnhance.Contrast(metallic).enhance(0.85 + (strength * 0.3))
    metallic = _lift_black_floor(ImageOps.autocontrast(metallic, cutoff=1), floor=6)

    raw_height = generate_parallax(rgb_source, strength=max(0.85, min(2.0, strength)))
    midpoint = Image.new("L", raw_height.size, color=127)
    height_alpha = Image.blend(midpoint, raw_height, alpha=0.75)

    return Image.merge("RGBA", (env_amount, glossiness, metallic, height_alpha))


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
    specular = generate_specular(rgb_source, strength=max(0.9, min(2.0, strength)))
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
        ao = _lift_black_floor(Image.new("L", grayscale.size, color=214), floor=24)
        roughness = _lift_black_floor(Image.new("L", grayscale.size, color=182), floor=32)
        metallic = _lift_black_floor(Image.new("L", grayscale.size, color=18), floor=4)
        height_or_spec = _lift_black_floor(Image.new("L", grayscale.size, color=127), floor=24)
        return Image.merge("RGBA", (ao, roughness, metallic, height_or_spec))

    red, green, blue = rgb_source.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
    chroma = ImageChops.subtract(maximum, minimum)

    specular_drive = max(0.9, min(2.4, 0.7 + (strength * 0.6)))
    specular = generate_specular(rgb_source, strength=specular_drive)

    roughness_contrast = 0.7 + (strength * 0.38)
    roughness = ImageOps.invert(specular)
    roughness = ImageEnhance.Contrast(roughness).enhance(roughness_contrast)
    roughness = _lift_black_floor(ImageOps.autocontrast(roughness, cutoff=0), floor=20)

    metallic_contrast = 0.7 + (strength * 0.4)
    metallic = Image.blend(chroma, specular, alpha=_clamp(0.2 + (strength * 0.1), 0.2, 0.5))
    metallic = ImageEnhance.Contrast(metallic).enhance(metallic_contrast)
    metallic = _lift_black_floor(ImageOps.autocontrast(metallic, cutoff=0), floor=3)

    cavity_contrast = 1.1 + (strength * 0.28)
    local_detail = ImageChops.difference(grayscale, grayscale.filter(ImageFilter.GaussianBlur(radius=2.4)))
    cavity = ImageOps.invert(ImageEnhance.Contrast(local_detail).enhance(cavity_contrast))
    ao_alpha = _clamp(0.5 + (strength * 0.14), 0.5, 0.92)
    ao = Image.blend(grayscale, cavity, alpha=ao_alpha)
    ao = _lift_black_floor(ImageOps.autocontrast(ao, cutoff=0), floor=24)

    height_specular_alpha = _clamp(0.2 + (strength * 0.12), 0.2, 0.56)
    raw_height = generate_parallax(rgb_source, strength=max(0.85, min(2.0, strength)))
    height_or_spec = Image.blend(raw_height, specular, alpha=height_specular_alpha)
    blend_alpha = _clamp(0.65 + (strength * 0.1), 0.65, 0.95)
    height_or_spec = Image.blend(Image.new("L", raw_height.size, color=127), height_or_spec, alpha=blend_alpha)
    height_or_spec = _lift_black_floor(height_or_spec, floor=8)

    return Image.merge("RGBA", (ao, roughness, metallic, height_or_spec))


def generate_specular(source: Image.Image, strength: float = 1.15) -> Image.Image:
    grayscale = ImageOps.grayscale(source)
    grayscale_min, grayscale_max = grayscale.getextrema()
    if (grayscale_max - grayscale_min) <= 2:
        return _lift_black_floor(grayscale, floor=8)

    pressure = _detail_pressure(source)
    base_blur_radius = 1.0 + (pressure * 1.0)
    broad_blur_radius = 2.4 + (pressure * 1.4)
    smoothed = grayscale.filter(ImageFilter.GaussianBlur(radius=base_blur_radius))
    # abs-difference (PIL ImageChops.difference is always ≥ 0)
    local_detail = ImageChops.difference(grayscale, grayscale.filter(ImageFilter.GaussianBlur(radius=broad_blur_radius)))

    # --- numpy path: float32 throughout so no integer-rounding holes ---
    h, w = grayscale.size[1], grayscale.size[0]
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
    specular_img = Image.fromarray(specular_arr.reshape(h, w).astype(np.uint8), mode="L")
    softened = specular_img.filter(ImageFilter.GaussianBlur(radius=soft_radius))
    soft_arr = np.frombuffer(softened.tobytes(), dtype=np.uint8).astype(np.float32)

    # Hard floor before contrast so no downstream step can create true-black holes
    soft_arr = np.maximum(soft_arr, 12.0)

    # Contrast enhancement
    effective_strength = _clamp(strength * (1.0 - (pressure * 0.18)), 0.9, 2.2)
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

    return Image.fromarray(normalized_arr.reshape(h, w).astype(np.uint8), mode="L")


def generate_msn(source: Image.Image, normal_strength: float = 2.0, specular_strength: float = 1.15) -> Image.Image:
    normal_rgb = generate_normal(source, strength=normal_strength)
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
    include_diffuse: bool,
    include_normal: bool,
    include_parallax: bool,
    include_glow: bool,
    include_environment_mask: bool,
    include_complex: bool,
) -> dict[str, Image.Image]:
    outputs: dict[str, Image.Image] = {}
    if include_diffuse:
        outputs["diffuse"] = enforce_skyrim_output_profile("diffuse", generate_diffuse(source))
    if include_normal:
        outputs["normal"] = enforce_skyrim_output_profile(
            "normal", generate_normal(source, strength=normal_strength)
        )
    if include_parallax:
        outputs["parallax"] = enforce_skyrim_output_profile(
            "parallax", generate_parallax(source, strength=parallax_strength)
        )
    if include_glow:
        outputs["glow"] = enforce_skyrim_output_profile("glow", generate_glow(source, threshold=glow_threshold))
    if include_environment_mask:
        outputs["environment_mask"] = enforce_skyrim_output_profile(
            "environment_mask",
            generate_environment_mask(source, strength=environment_mask_strength, mode=env_mask_mode),
            env_mask_mode=env_mask_mode,
        )
    if include_complex:
        outputs["complex_material"] = enforce_skyrim_output_profile(
            "complex_material",
            (
                generate_msn(source, normal_strength=normal_strength, specular_strength=specular_strength)
                if complex_format == "msn"
                else generate_complex_material(source, strength=complex_strength)
            ),
            complex_format=complex_format,
        )
    return outputs


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
    diffuse_name: str | None,
    parallax_name: str | None,
) -> tuple[Path, Path]:
    base_output_dir = output_dir or input_path.parent
    base_output_dir.mkdir(parents=True, exist_ok=True)
    ext = DDS_EXTENSION

    diffuse_stem = diffuse_name or input_path.stem
    parallax_stem = parallax_name or f"{input_path.stem}_p"
    return base_output_dir / f"{diffuse_stem}{ext}", base_output_dir / f"{parallax_stem}{ext}"


def build_normal_output_path(
    input_path: Path,
    output_dir: Path | None,
    normal_name: str | None,
) -> Path:
    base_output_dir = output_dir or input_path.parent
    base_output_dir.mkdir(parents=True, exist_ok=True)
    ext = DDS_EXTENSION
    normal_stem = normal_name or f"{input_path.stem}_n"
    return base_output_dir / f"{normal_stem}{ext}"


def build_glow_output_path(
    input_path: Path,
    output_dir: Path | None,
    glow_name: str | None,
) -> Path:
    base_output_dir = output_dir or input_path.parent
    base_output_dir.mkdir(parents=True, exist_ok=True)
    ext = DDS_EXTENSION
    glow_stem = glow_name or f"{input_path.stem}_g"
    return base_output_dir / f"{glow_stem}{ext}"


def build_environment_mask_output_path(
    input_path: Path,
    output_dir: Path | None,
    environment_mask_name: str | None,
) -> Path:
    base_output_dir = output_dir or input_path.parent
    base_output_dir.mkdir(parents=True, exist_ok=True)
    ext = DDS_EXTENSION
    mask_stem = environment_mask_name or f"{input_path.stem}_m"
    return base_output_dir / f"{mask_stem}{ext}"


def build_complex_output_path(
    input_path: Path,
    output_dir: Path | None,
    complex_name: str | None,
    complex_format: str = "msn",
) -> Path:
    base_output_dir = output_dir or input_path.parent
    base_output_dir.mkdir(parents=True, exist_ok=True)
    ext = DDS_EXTENSION
    if complex_format not in {"msn", "cm"}:
        raise ValueError("complex_format must be 'msn' or 'cm'.")
    suffix = "_msn" if complex_format == "msn" else "_cm"
    complex_stem = complex_name or f"{input_path.stem}{suffix}"
    return base_output_dir / f"{complex_stem}{ext}"


def _is_supported_input_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_EXTENSIONS


def _is_generated_texture(path: Path) -> bool:
    stem = path.stem.lower()
    return stem.endswith("_") or any(stem.endswith(suffix) for suffix in GENERATED_TEXTURE_SUFFIXES)


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
    "_g": (
        "glow",
        "Glow / Emissive Map",
        "Glow/emissive map. Texture Slot 2 in the NIF. "
        "Controls per-pixel self-illumination strength. "
        "Requires SLSF1_Own_Emit (0x40) shader flag on the BSLightingShaderProperty.",
    ),
    "_m": (
        "environment_mask",
        "Environment Mask",
        "Greyscale environment reflection intensity mask. Texture Slot 5 in the NIF. "
        "Higher (brighter) values produce more environment/specular reflection. "
        "Requires SLSF1_Environment_Mapping (0x80) shader flag. "
        "Standard vanilla Skyrim SE format; typically stored as DXT1 (no alpha).",
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
        "Complex Material Packed (Community Shaders / ENB workflows)",
        "NOT a vanilla Skyrim SE texture. Channel-packed complex material output. "
        "Generated by this tool as RGBA: R=ambient-occlusion proxy, G=roughness proxy, "
        "B=metalness proxy, A=height/specular proxy. Intended for modern complex-material "
        "workflows (for example Community Shaders packs or ENB-adjacent authoring), not vanilla Skyrim SE.",
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
    "glow": "_g",
    "environment_mask": "_m",
    "subsurface": "_s",
    "skin_specular": "_sk",
    "complex_material": "_msn",
    "complex_material_cm": "_cm",
}

_SKYRIM_ROLE_TOKEN_HINTS: dict[str, tuple[str, ...]] = {
    "normal": ("normal", "normalmap", "nrm", "nor", "bump"),
    "parallax": ("parallax", "height", "heightmap", "displace", "displacement"),
    "glow": ("glow", "emissive", "emit", "emission"),
    "environment_mask": ("env", "envmask", "cubemask", "reflectionmask", "specmask"),
    "subsurface": ("subsurface", "sss"),
    "skin_specular": ("skinspec", "skinspecular"),
    "complex_material": ("complex", "complexmaterial", "msn"),
    "complex_material_cm": ("complexcm", "cmaterial", "complexgray"),
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
    include_diffuse: bool = False,
    include_normal: bool = False,
    include_glow: bool,
    include_environment_mask: bool,
    env_mask_mode: str,
    env_mask_strength: float,
    include_parallax: bool,
    include_complex: bool,
) -> list[tuple[str, str]]:
    """Return a list of (warning_id, human-readable message) pairs for suspicious generation choices.

    The caller is responsible for filtering out dismissed warnings and
    presenting remaining ones to the user.  Warning IDs are stable strings
    so they can be stored in a ``dismissed_warnings`` set.
    """
    warnings: list[tuple[str, str]] = []

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
            "Complex material is designed for hard surfaces with distinct PBR channels "
            "(AO, roughness, metallic). Organic surfaces like skin and cloth rarely benefit "
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
            "Tip: Use an albedo/diffuse source texture (no _n/_p/_g/_m/_msn/_cm suffix) for best results.",
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
            "Input already looks like a glow/emissive map (_g).\n\n"
            "Regenerating glow from a glow map can over-compress emissive range.\n\n"
            "Tip: Generate glow from diffuse/albedo unless intentionally post-processing a glow texture.",
        ))
    if include_environment_mask and resolved_source_role == "environment_mask":
        warnings.append((
            "env_mask_from_env_mask_source",
            "Input already looks like an environment mask (_m).\n\n"
            "Regenerating a mask from a mask can flatten reflection response.\n\n"
            "Tip: Build environment masks from diffuse/albedo sources for better material separation.",
        ))
    if include_complex and resolved_source_role in {"complex_material", "complex_material_cm"}:
        warnings.append((
            "complex_from_complex_source",
            "Input already looks like a complex material map (_msn/_cm).\n\n"
            "Regenerating complex material from packed complex inputs often damages channel meaning.\n\n"
            "Tip: Start from diffuse/albedo source when creating new complex materials.",
        ))
    hint_text = (source_hint or "").lower()
    if "ui/interface texture" in hint_text and (include_parallax or include_environment_mask or include_complex):
        warnings.append((
            "ui_texture_advanced_maps",
            "Input path looks like a UI/interface texture.\n\n"
            "Parallax, environment mask, and complex material outputs are usually meant for in-world 3D surfaces, not menu/interface assets.\n\n"
            "Tip: For UI/interface textures, prefer diffuse and only add glow when intentionally needed.",
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
            "Folder mode scans subfolders, processes original DDS files, and skips generated *_n, *_p, *_g, *_m, *_msn, and *_cm variants."
        )
    return source_files


def _to_dds_compatible_image(image: Image.Image) -> Image.Image:
    if image.mode == "RGBA":
        return image
    if image.mode in {"RGB", "L", "LA"}:
        return image.convert("RGBA")
    return image.convert("RGBA")


def _save_with_dds_fallback(
    image: Image.Image,
    output_path: Path,
    *,
    preferred_pixel_formats: tuple[str, ...] = ("DXT5",),
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

    dds_image = _to_dds_compatible_image(image)
    dds_target = output_path.with_suffix(DDS_EXTENSION)
    for pixel_format in preferred_pixel_formats:
        try:
            _atomic_save(dds_target, dds_image, format="DDS", pixel_format=pixel_format)
            return output_path
        except Exception:
            continue
    fallback = output_path.with_suffix(".png")
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
    complex_name: str | None = None,
    normal_strength: float | None = None,
    parallax_strength: float | None = None,
    glow_threshold: int | None = None,
    environment_mask_strength: float | None = None,
    complex_strength: float | None = None,
    specular_strength: float | None = None,
    complex_format: str = "msn",
    env_mask_mode: str = "standard",
    include_diffuse: bool = True,
    include_normal: bool = True,
    include_parallax: bool = True,
    include_glow: bool = False,
    include_environment_mask: bool = False,
    include_complex: bool = False,
) -> dict[str, Path]:
    if not any((include_diffuse, include_normal, include_parallax, include_glow, include_environment_mask, include_complex)):
        raise ValueError("Select at least one output.")

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
        resolved_normal_strength = normal_strength if normal_strength is not None else float(recommended["normal_strength"])
        resolved_parallax_strength = parallax_strength if parallax_strength is not None else float(recommended["parallax_strength"])
        resolved_glow_threshold = glow_threshold if glow_threshold is not None else int(recommended["glow_threshold"])
        resolved_environment_mask_strength = (
            environment_mask_strength if environment_mask_strength is not None else float(recommended["environment_mask_strength"])
        )
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
            outputs["diffuse"] = _save_with_dds_fallback(diffuse, diffuse_path)

        if include_normal:
            normal = enforce_skyrim_output_profile("normal", generate_normal(source, strength=resolved_normal_strength))
            normal_path = build_normal_output_path(
                input_path=input_file,
                output_dir=output_dir,
                normal_name=normal_name,
            )
            outputs["normal"] = _save_with_dds_fallback(normal, normal_path)

        if include_parallax:
            parallax = enforce_skyrim_output_profile("parallax", generate_parallax(source, strength=resolved_parallax_strength))
            _, parallax_path = build_output_paths(
                input_path=input_file,
                output_dir=output_dir,
                diffuse_name=diffuse_name,
                parallax_name=parallax_name,
            )
            outputs["parallax"] = _save_with_dds_fallback(parallax, parallax_path)

        if include_glow:
            glow = enforce_skyrim_output_profile("glow", generate_glow(source, threshold=resolved_glow_threshold))
            glow_path = build_glow_output_path(
                input_path=input_file,
                output_dir=output_dir,
                glow_name=glow_name,
            )
            outputs["glow"] = _save_with_dds_fallback(glow, glow_path)

        if include_environment_mask:
            environment_mask = enforce_skyrim_output_profile(
                "environment_mask",
                generate_environment_mask(source, strength=resolved_environment_mask_strength, mode=env_mask_mode),
                env_mask_mode=env_mask_mode,
            )
            environment_mask_path = build_environment_mask_output_path(
                input_path=input_file,
                output_dir=output_dir,
                environment_mask_name=environment_mask_name,
            )
            env_formats = ("DXT1", "DXT5") if env_mask_mode == "standard" else ("DXT5",)
            outputs["environment_mask"] = _save_with_dds_fallback(
                environment_mask,
                environment_mask_path,
                preferred_pixel_formats=env_formats,
            )

        if include_complex:
            if complex_format == "msn":
                complex_material = enforce_skyrim_output_profile(
                    "complex_material",
                    generate_msn(
                        source,
                        normal_strength=resolved_normal_strength,
                        specular_strength=resolved_specular_strength,
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
            outputs["complex_material"] = _save_with_dds_fallback(complex_material, complex_path)

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
    complex_name: str | None = None,
    normal_strength: float | None = None,
    parallax_strength: float | None = None,
    glow_threshold: int | None = None,
    environment_mask_strength: float | None = None,
    complex_strength: float | None = None,
    specular_strength: float | None = None,
    complex_format: str = "msn",
    env_mask_mode: str = "standard",
    include_diffuse: bool = True,
    include_normal: bool = True,
    include_parallax: bool = True,
    include_glow: bool = False,
    include_environment_mask: bool = False,
    include_complex: bool = False,
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
                    complex_name=complex_name,
                    normal_strength=normal_strength,
                    parallax_strength=parallax_strength,
                    glow_threshold=glow_threshold,
                    environment_mask_strength=environment_mask_strength,
                    complex_strength=complex_strength,
                    specular_strength=specular_strength,
                    complex_format=complex_format,
                    env_mask_mode=env_mask_mode,
                    include_diffuse=include_diffuse,
                    include_normal=include_normal,
                    include_parallax=include_parallax,
                    include_glow=include_glow,
                    include_environment_mask=include_environment_mask,
                    include_complex=include_complex,
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
            complex_name=complex_name,
            normal_strength=normal_strength,
            parallax_strength=parallax_strength,
            glow_threshold=glow_threshold,
            environment_mask_strength=environment_mask_strength,
            complex_strength=complex_strength,
            specular_strength=specular_strength,
            complex_format=complex_format,
            env_mask_mode=env_mask_mode,
            include_diffuse=include_diffuse,
            include_normal=include_normal,
            include_parallax=include_parallax,
            include_glow=include_glow,
            include_environment_mask=include_environment_mask,
            include_complex=include_complex,
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
    parser.add_argument("--environment-mask-name", type=str, default=None, help="Environment mask output file stem.")
    parser.add_argument("--complex-name", type=str, default=None, help="Complex material output file stem.")
    parser.add_argument(
        "--complex-format",
        choices=("msn", "cm"),
        default="msn",
        help="Complex material format/suffix: msn -> _msn (normal+spec alpha), cm -> _cm (packed AO/rough/metal/height-spec).",
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
        "--environment-mask-mode",
        choices=("standard", "complex"),
        default="standard",
        help=(
            "Environment mask output mode. "
            "'standard' (default) = greyscale _m.dds for vanilla Skyrim SE (Texture Slot 5, no ENB required). "
            "'complex' = RGBA channel-packed texture for ENBSeries Complex Parallax Material."
        ),
    )
    parser.add_argument("--complex-material", action="store_true", help="Generate complex material output.")
    parser.add_argument(
        "--batch-workers",
        type=int,
        default=0,
        help="Parallel workers for folder batch mode (0 = automatic).",
    )
    parser.add_argument("--gui", action="store_true", help="Launch graphical interface.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    return parser.parse_args()


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
            self.dark_mode_var = tk.BooleanVar(value=False)
            self._tooltip_bg = _LIGHT_THEME["tooltip_bg"]
            self._tooltip_fg = _LIGHT_THEME["tooltip_fg"]
            self.dismissed_warnings: set[str] = set()

            self.input_var = tk.StringVar()
            self.output_var = tk.StringVar()
            self.use_custom_output_var = tk.BooleanVar(value=False)
            self.preview_source_name_var = tk.StringVar(value="No source loaded")
            self.detected_context_var = tk.StringVar(value=self.manager_context.summary)
            self.normal_strength_var = tk.DoubleVar(value=2.0)
            self.parallax_strength_var = tk.DoubleVar(value=1.35)
            self.complex_strength_var = tk.DoubleVar(value=1.15)
            self.specular_strength_var = tk.DoubleVar(value=1.15)
            self.glow_threshold_var = tk.IntVar(value=190)
            self.environment_mask_strength_var = tk.DoubleVar(value=1.2)
            self.normal_strength_display_var = tk.StringVar()
            self.parallax_strength_display_var = tk.StringVar()
            self.glow_threshold_display_var = tk.StringVar()
            self.environment_mask_strength_display_var = tk.StringVar()
            self.complex_strength_display_var = tk.StringVar()
            self.specular_strength_display_var = tk.StringVar()
            self.complex_format_var = tk.StringVar(value="msn")
            self.env_mask_mode_var = tk.StringVar(value="standard")
            self.auto_suggestions_var = tk.BooleanVar(value=True)
            self.auto_normal_suggestion_var = tk.BooleanVar(value=True)
            self.auto_parallax_suggestion_var = tk.BooleanVar(value=True)
            self.auto_glow_suggestion_var = tk.BooleanVar(value=True)
            self.auto_environment_mask_suggestion_var = tk.BooleanVar(value=True)
            self.auto_complex_suggestion_var = tk.BooleanVar(value=True)
            self.auto_specular_suggestion_var = tk.BooleanVar(value=True)
            self.include_diffuse_var = tk.BooleanVar(value=True)
            self.include_normal_var = tk.BooleanVar(value=True)
            self.include_parallax_var = tk.BooleanVar(value=True)
            self.include_glow_var = tk.BooleanVar(value=False)
            self.include_environment_mask_var = tk.BooleanVar(value=False)
            self.include_complex_var = tk.BooleanVar(value=False)
            self.status_var = tk.StringVar(
                value=self.manager_context.summary if self.manager_context.manager is not None else "Select a DDS file to begin."
            )
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
            ttk.Label(
                file_frame,
                textvariable=self.detected_context_var,
                foreground="gray",
                wraplength=760,
            ).grid(row=3, column=0, columnspan=5, sticky=tk.W, pady=(4, 0))
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
            _glow_check = ttk.Checkbutton(options_frame, text="Glow / _g", variable=self.include_glow_var, command=self._refresh_preview)
            _glow_check.grid(row=1, column=0, sticky=tk.W)
            self._add_tooltip(_glow_check, "✨ Generate a glow map. Bright pixels glow in the dark.\nPerfect for making your cave look like a disco.")
            _env_mask_check = ttk.Checkbutton(options_frame, text="Environment mask / _m", variable=self.include_environment_mask_var, command=self._refresh_preview)
            _env_mask_check.grid(row=1, column=1, sticky=tk.W)
            self._add_tooltip(_env_mask_check, "🪞 Generate an environment mask for reflections.\nTells Skyrim which parts of a surface are shiny. Science!")
            _complex_check = ttk.Checkbutton(options_frame, text="Complex material", variable=self.include_complex_var, command=self._refresh_preview)
            _complex_check.grid(row=1, column=2, sticky=tk.W)
            self._add_tooltip(_complex_check, "🔮 Generate complex material for ENBSeries parallax.\nRequires ENB. If you don't know what ENB is, you will soon, and there's no going back.")
            _auto_sugg_check = ttk.Checkbutton(
                options_frame,
                text="Automatic suggestions (analyze image and set sliders)",
                variable=self.auto_suggestions_var,
                command=self._toggle_auto_suggestions,
            )
            _auto_sugg_check.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(6, 2))
            self._add_tooltip(_auto_sugg_check, "🤖 Let the AI™ (actually just math) pick slider values.\nUncheck if you think YOU know better than the algorithm. Spoiler: maybe you do.")

            _complex_fmt_label = ttk.Label(options_frame, text="Complex naming")
            _complex_fmt_label.grid(row=3, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(_complex_fmt_label, "🏷 Output filename suffix for complex material.\n'msn' = _msn.dds (ENB normal+specular), 'cm' = _cm.dds (packed AO/rough/metal/height-spec).")
            complex_format = ttk.Combobox(
                options_frame,
                textvariable=self.complex_format_var,
                values=("msn", "cm"),
                state="readonly",
                width=20,
            )
            complex_format.grid(row=3, column=1, sticky=tk.W)
            self._add_tooltip(complex_format, "🏷 Choose 'msn' for RGBA normal+specular, 'cm' for packed AO/rough/metal/height-spec.\nWhen in doubt, use the format your target shader expects.")

            _env_mode_label = ttk.Label(options_frame, text="Env mask mode")
            _env_mode_label.grid(row=3, column=2, sticky=tk.W, padx=(20, 4), pady=8)
            self._add_tooltip(_env_mode_label, "🌍 How to encode the environment mask.\n'standard' = vanilla Skyrim. 'complex' = ENBSeries channel-packed RGBA. Choose wisely.")
            env_mask_mode_combo = ttk.Combobox(
                options_frame,
                textvariable=self.env_mask_mode_var,
                values=("standard", "complex"),
                state="readonly",
                width=20,
            )
            env_mask_mode_combo.grid(row=3, column=3, sticky=tk.W)
            self._add_tooltip(env_mask_mode_combo, "🌍 'standard' = single grey channel for vanilla Skyrim SE.\n'complex' = RGBA for ENBSeries. Using complex without ENB produces… nothing useful.")
            ttk.Label(
                options_frame,
                text="standard = vanilla Skyrim SE  |  complex = ENBSeries RGBA",
                foreground="gray",
            ).grid(row=3, column=4, sticky=tk.W, padx=(4, 0))

            _normal_label = ttk.Label(options_frame, text="Normal strength")
            _normal_label.grid(row=4, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(_normal_label, "💪 Controls normal-map intensity.\nHigher = sharper fake detail. Lower = smooth potato mode.")
            self.normal_scale = ttk.Scale(options_frame, from_=0.5, to=4.0, variable=self.normal_strength_var, command=lambda _: self._on_slider_changed())
            self.normal_scale.grid(row=4, column=1, columnspan=2, sticky=tk.EW)
            self._add_tooltip(self.normal_scale, "💪 Drag right for epic bumps, left for subtle detail.\nLive value is shown next to the slider so you can stop guessing.")
            ttk.Label(options_frame, textvariable=self.normal_strength_display_var).grid(row=4, column=3, sticky=tk.W, padx=8)
            _auto_normal = ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_normal_suggestion_var, command=self._on_auto_slider_preference_changed)
            _auto_normal.grid(row=4, column=4, sticky=tk.W)
            self._add_tooltip(_auto_normal, "🤖 Let the app analyse the image and choose this value.\nUncheck to manually control, as the control freak you truly are.")

            _parallax_label = ttk.Label(options_frame, text="Parallax strength")
            _parallax_label.grid(row=5, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(_parallax_label, "🏔 Controls parallax depth contrast.\nToo high and your pebble becomes a canyon. Too low and your canyon becomes toast.")
            self.parallax_scale = ttk.Scale(options_frame, from_=0.5, to=3.0, variable=self.parallax_strength_var, command=lambda _: self._on_slider_changed())
            self.parallax_scale.grid(row=5, column=1, columnspan=2, sticky=tk.EW)
            self._add_tooltip(self.parallax_scale, "🏔 Slide right for deeper depth illusion, left for subtle relief.\nYes, this can absolutely make stones look dramatic.")
            ttk.Label(options_frame, textvariable=self.parallax_strength_display_var).grid(row=5, column=3, sticky=tk.W, padx=8)
            _auto_parallax = ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_parallax_suggestion_var, command=self._on_auto_slider_preference_changed)
            _auto_parallax.grid(row=5, column=4, sticky=tk.W)
            self._add_tooltip(_auto_parallax, "🤖 Automatic parallax strength suggestion.\nBased on actual image analysis, not a horoscope.")

            _glow_label = ttk.Label(options_frame, text="Glow threshold")
            _glow_label.grid(row=6, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(_glow_label, "💡 Brightness cutoff for glow.\nLower = more glow. Higher = only brightest bits glow like tiny supernovas.")
            self.glow_scale = ttk.Scale(options_frame, from_=0, to=255, variable=self.glow_threshold_var, command=lambda _: self._on_slider_changed())
            self.glow_scale.grid(row=6, column=1, columnspan=2, sticky=tk.EW)
            self._add_tooltip(self.glow_scale, "💡 0 means everything glows like a rave. 255 means almost nothing glows.\nUse the live value display to tune precisely.")
            ttk.Label(options_frame, textvariable=self.glow_threshold_display_var).grid(row=6, column=3, sticky=tk.W, padx=8)
            _auto_glow = ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_glow_suggestion_var, command=self._on_auto_slider_preference_changed)
            _auto_glow.grid(row=6, column=4, sticky=tk.W)
            self._add_tooltip(_auto_glow, "🤖 Auto-detect the ideal glow threshold.\nBased on luminance analysis. The computer is trying its best.")

            _env_mask_label = ttk.Label(options_frame, text="Environment mask strength")
            _env_mask_label.grid(row=7, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(_env_mask_label, "🪞 Controls environment-mask contrast.\nHigher = stronger shiny-vs-matte separation. Great for dramatic materials.")
            self.environment_mask_scale = ttk.Scale(options_frame, from_=0.5, to=3.0, variable=self.environment_mask_strength_var, command=lambda _: self._on_slider_changed())
            self.environment_mask_scale.grid(row=7, column=1, columnspan=2, sticky=tk.EW)
            self._add_tooltip(self.environment_mask_scale, "🪞 Slide right for stronger reflection contrast.\nSlide left for chill, less dramatic materials.")
            ttk.Label(options_frame, textvariable=self.environment_mask_strength_display_var).grid(row=7, column=3, sticky=tk.W, padx=8)
            _auto_env_mask = ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_environment_mask_suggestion_var, command=self._on_auto_slider_preference_changed)
            _auto_env_mask.grid(row=7, column=4, sticky=tk.W)
            self._add_tooltip(_auto_env_mask, "🤖 Auto-select environment mask strength.\nThe machine will judge your texture's reflective potential.")

            _complex_label = ttk.Label(options_frame, text="Complex strength")
            _complex_label.grid(row=8, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(_complex_label, "🔮 Controls complex-material contrast.\nHigher = punchier ENB material response. Lower = subtle, civilized vibes.")
            self.complex_scale = ttk.Scale(options_frame, from_=0.5, to=3.0, variable=self.complex_strength_var, command=lambda _: self._on_slider_changed())
            self.complex_scale.grid(row=8, column=1, columnspan=2, sticky=tk.EW)
            self._add_tooltip(self.complex_scale, "🔮 Right = louder material definition.\nLeft = quieter output for restrained legends.")
            ttk.Label(options_frame, textvariable=self.complex_strength_display_var).grid(row=8, column=3, sticky=tk.W, padx=8)
            _auto_complex = ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_complex_suggestion_var, command=self._on_auto_slider_preference_changed)
            _auto_complex.grid(row=8, column=4, sticky=tk.W)
            self._add_tooltip(_auto_complex, "🤖 Auto-set complex strength. Let the algorithm\nscrutinise your texture's material complexity.")

            _specular_label = ttk.Label(options_frame, text="Specular strength (_msn alpha)")
            _specular_label.grid(row=9, column=0, sticky=tk.W, pady=8)
            self._add_tooltip(_specular_label, "✨ Controls specular highlight intensity in _msn alpha.\nHigher = shinier. Lower = dusty realism.")
            self.specular_scale = ttk.Scale(options_frame, from_=0.5, to=3.0, variable=self.specular_strength_var, command=lambda _: self._on_slider_changed())
            self.specular_scale.grid(row=9, column=1, columnspan=2, sticky=tk.EW)
            self._add_tooltip(self.specular_scale, "✨ Turn it up for glorious shine, down for ancient weathered stone.\nLive value shown beside slider.")
            ttk.Label(options_frame, textvariable=self.specular_strength_display_var).grid(row=9, column=3, sticky=tk.W, padx=8)
            _auto_specular = ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_specular_suggestion_var, command=self._on_auto_slider_preference_changed)
            _auto_specular.grid(row=9, column=4, sticky=tk.W)
            self._add_tooltip(_auto_specular, "🤖 Auto-set specular strength. The AI ponders how shiny\nyour texture DESERVES to be.")

            options_frame.columnconfigure(2, weight=1)
            self._update_slider_auto_states()

            preview_frame = ttk.LabelFrame(wrapper, text="Preview", padding=10)
            preview_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

            ttk.Label(preview_frame, text="Before").grid(row=0, column=0, columnspan=2, padx=10, pady=(2, 4))
            self.before_image_label = ttk.Label(preview_frame, text="No source loaded")
            self.before_image_label.grid(row=1, column=0, columnspan=2, padx=6, pady=4)
            self._add_tooltip(self.before_image_label, "👀 Your raw source texture before the magic happens.\nLook upon it. Appreciate its unprocessed beauty.")

            source_controls = ttk.Frame(preview_frame)
            source_controls.grid(row=2, column=0, columnspan=2, pady=(0, 8))
            self.prev_source_button = ttk.Button(source_controls, text="◀ Prev", command=self._show_previous_preview_source)
            self.prev_source_button.pack(side=tk.LEFT, padx=4)
            self._add_tooltip(self.prev_source_button, "⏮ Preview the previous texture in your batch.\nBecause forward isn't always the right direction.")
            ttk.Label(source_controls, textvariable=self.preview_source_name_var).pack(side=tk.LEFT, padx=8)
            self.next_source_button = ttk.Button(source_controls, text="Next ▶", command=self._show_next_preview_source)
            self.next_source_button.pack(side=tk.LEFT, padx=4)
            self._add_tooltip(self.next_source_button, "⏭ Preview the next texture in your batch.\nOnward! To the next texture frontier!")
            _preview_size_label = ttk.Label(source_controls, text="Preview size")
            _preview_size_label.pack(side=tk.LEFT, padx=(14, 4))
            self._add_tooltip(_preview_size_label, "📐 How large the preview thumbnails appear.\nBigger = easier to see details. Smaller = fits more on screen. Life is full of trade-offs.")
            preview_size_combo = ttk.Combobox(
                source_controls,
                textvariable=self.preview_size_var,
                values=tuple(PREVIEW_SIZE_PRESETS.keys()),
                state="readonly",
                width=10,
            )
            preview_size_combo.pack(side=tk.LEFT)
            preview_size_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_preview_size_changed())
            self._add_tooltip(preview_size_combo, "📐 Choose thumbnail size: XS, Small, Medium (default), Large, XL.\nNote: XL does not make your textures better, just easier to admire.")
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

            self.preview_output_labels: dict[str, ttk.Label] = {}
            output_grid = ttk.Frame(preview_frame)
            output_grid.grid(row=3, column=0, columnspan=2, sticky=tk.NSEW)
            output_specs = (
                ("diffuse", "Diffuse"),
                ("normal", "Normal"),
                ("parallax", "Parallax"),
                ("glow", "Glow"),
                ("environment_mask", "Environment Mask"),
                ("complex_material", "Complex Material"),
            )
            _output_tooltips = {
                "diffuse": "🎨 Preview of the diffuse (colour) output.\nIf this looks wrong you should probably re-examine your life choices.",
                "normal": "🗻 Preview of the normal map output.\nThat psychedelic blue-purple soup is actually geometric data. Science!",
                "parallax": "🏔 Preview of the parallax/height map.\nDarker = deeper, lighter = higher. A greyscale height map of greatness.",
                "glow": "✨ Preview of the glow map.\nWhite = glows in darkness. Black = does not glow. Simple, powerful, disco.",
                "environment_mask": "🪞 Preview of the environment mask.\nTells the engine where to apply reflections. Grey = shiny, black = matte.",
                "complex_material": "🔮 Preview of the complex material output.\nRequires ENB to see in-game. Without ENB it just… sits there, looking important.",
            }
            for index, (output_key, output_label) in enumerate(output_specs):
                row = (index // 2) * 2
                column = index % 2
                _out_title = ttk.Label(output_grid, text=output_label)
                _out_title.grid(row=row, column=column, padx=6, pady=(2, 1))
                self._add_tooltip(_out_title, _output_tooltips.get(output_key, f"Preview of {output_label} output."))
                label = ttk.Label(output_grid, text="No preview")
                label.grid(row=row + 1, column=column, padx=6, pady=(0, 4))
                self._add_tooltip(label, _output_tooltips.get(output_key, f"Preview of {output_label} output."))
                self.preview_output_labels[output_key] = label

            preview_frame.columnconfigure(0, weight=1)
            preview_frame.columnconfigure(1, weight=1)
            output_grid.columnconfigure(0, weight=1)
            output_grid.columnconfigure(1, weight=1)
            self._update_preview_navigation_state()

            actions = ttk.Frame(wrapper, padding=(4, 10))
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
            _theme_check = ttk.Checkbutton(
                actions,
                text="🌙 Dark mode",
                variable=self.dark_mode_var,
                command=self._toggle_theme,
            )
            _theme_check.pack(side=tk.LEFT, padx=(12, 4))
            self._add_tooltip(_theme_check, "🌙 Toggle dark/light mode.\nEasy on the eyes during those 3am modding sessions.")
            ttk.Label(actions, textvariable=self.status_var).pack(side=tk.LEFT, padx=14)
            _patreon_button = ttk.Button(
                actions,
                text="❤ Support on Patreon",
                command=lambda: webbrowser.open(PATREON_URL),
            )
            _patreon_button.pack(side=tk.RIGHT, padx=4)
            self._add_tooltip(_patreon_button, "❤ Support the developer on Patreon.\nBecause caffeine and texture generation both cost money.")
            self._apply_theme()

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

        def _add_tooltip(self, widget: tk.Widget, text: str) -> None:
            tip_window: list[tk.Toplevel | None] = [None]

            def _show(_event: object) -> None:
                if tip_window[0] is not None:
                    return
                try:
                    x = widget.winfo_rootx() + 20
                    y = widget.winfo_rooty() + widget.winfo_height() + 4
                    tip = tk.Toplevel(widget)
                    tip.wm_overrideredirect(True)
                    tip.wm_geometry(f"+{x}+{y}")
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
            widget.bind("<Leave>", _hide, add="+")
            widget.bind("<ButtonPress>", _hide, add="+")

        def _apply_theme(self) -> None:
            colors = _DARK_THEME if self.dark_mode_var.get() else _LIGHT_THEME
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
            style.configure("TScrollbar", background=colors["button_bg"], troughcolor=colors["bg"])
            style.map("TScrollbar", background=[("active", colors["trough"])])

        def _toggle_theme(self) -> None:
            self._apply_theme()
            theme_name = "dark" if self.dark_mode_var.get() else "light"
            self.status_var.set(f"Switched to {theme_name} mode.")

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

        def _load_input_selection(self, path: Path) -> None:
            try:
                input_files = collect_source_textures(path)
                self.selected_inputs = input_files
                self.last_input_browse_dir = path if path.is_dir() else path.parent
                self.current_preview_index = 0
                self._set_preview_source(0, apply_recommendations=True)
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
                self._update_preview_navigation_state()
                messagebox.showerror("Unable to open texture", str(exc))

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
                "complex_material": bool(generation_kwargs["include_complex"]),
            }
            for input_file in input_files:
                expected_paths: list[Path] = []
                if includes["diffuse"]:
                    diffuse_path, _ = build_output_paths(
                        input_path=input_file,
                        output_dir=generation_kwargs["output_dir"],
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
                for candidate in expected_paths:
                    for possible in (candidate, candidate.with_suffix(".png")):
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
                for index, input_file in enumerate(input_files, start=1):
                    if self.cancel_requested:
                        self.processing_queue.put(("cancelled", results))
                        return
                    self.processing_queue.put(("progress", (index, total, input_file)))
                    try:
                        outputs = run_with_options(
                            input_file=input_file,
                            **generation_kwargs,
                        )
                        results[input_file] = outputs
                        self._record_created_outputs(list(outputs.values()))
                    except Exception as exc:
                        self.processing_queue.put(("file_error", (index, total, input_file.name, str(exc))))
                self.processing_queue.put(("done", results))
            except Exception as exc:
                self.processing_queue.put(("error", str(exc)))

        def _poll_processing_queue(self) -> None:
            keep_polling = self.is_processing
            while True:
                try:
                    event_type, payload = self.processing_queue.get_nowait()
                except queue.Empty:
                    break

                if event_type == "progress":
                    index, total, current_path = payload
                    self.status_var.set(f"Processing {index}/{total}: {current_path.name}")
                    if self.show_batch_preview_var.get():
                        self._set_preview_source_by_path(current_path)
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
                        if total_failed:
                            lines.append(f"Skipped {total_failed} failed source texture(s).")
                            for filename, error_message in self.batch_failures[:5]:
                                lines.append(f"- {filename}: {error_message}")
                            if total_failed > 5:
                                lines.append(f"...and {total_failed - 5} more.")
                    messagebox.showinfo("Generation complete", "\n".join(lines))
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
                    )
                    keep_polling = False
                elif event_type == "error":
                    self._set_processing_state(False)
                    messagebox.showerror("Generation failed", str(payload))
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
                self.status_var.set("Automatic suggestions disabled. Manual slider values will be kept.")
            self._update_slider_auto_states()
            self._refresh_preview()

        def _on_slider_changed(self) -> None:
            self._update_slider_value_labels()
            self._request_preview_refresh()

        def _update_slider_value_labels(self) -> None:
            self.normal_strength_display_var.set(f"{float(self.normal_strength_var.get()):.2f} (0.5–4.0)")
            self.parallax_strength_display_var.set(f"{float(self.parallax_strength_var.get()):.2f} (0.5–3.0)")
            self.glow_threshold_display_var.set(f"{int(self.glow_threshold_var.get())} (0–255)")
            self.environment_mask_strength_display_var.set(
                f"{float(self.environment_mask_strength_var.get()):.2f} (0.5–3.0)"
            )
            self.complex_strength_display_var.set(f"{float(self.complex_strength_var.get()):.2f} (0.5–3.0)")
            self.specular_strength_display_var.set(f"{float(self.specular_strength_var.get()):.2f} (0.5–3.0)")

        def _on_batch_preview_toggle(self) -> None:
            if self.show_batch_preview_var.get():
                self.status_var.set("Batch live preview enabled. Heads-up: this can slow processing on large batches.")
            else:
                self.status_var.set("Batch live preview disabled for faster processing.")

        def _set_all_auto_slider_flags(self, value: bool) -> None:
            self.auto_normal_suggestion_var.set(value)
            self.auto_parallax_suggestion_var.set(value)
            self.auto_glow_suggestion_var.set(value)
            self.auto_environment_mask_suggestion_var.set(value)
            self.auto_complex_suggestion_var.set(value)
            self.auto_specular_suggestion_var.set(value)

        def _on_auto_slider_preference_changed(self) -> None:
            self._update_slider_auto_states()
            if self.auto_suggestions_var.get():
                self._apply_recommended_settings()
                self.status_var.set("Automatic suggestions updated for checked sliders.")
            self._refresh_preview()

        def _update_slider_auto_states(self) -> None:
            auto_enabled = self.auto_suggestions_var.get()
            slider_specs = (
                (self.normal_scale, self.auto_normal_suggestion_var),
                (self.parallax_scale, self.auto_parallax_suggestion_var),
                (self.glow_scale, self.auto_glow_suggestion_var),
                (self.environment_mask_scale, self.auto_environment_mask_suggestion_var),
                (self.complex_scale, self.auto_complex_suggestion_var),
                (self.specular_scale, self.auto_specular_suggestion_var),
            )
            for slider, auto_var in slider_specs:
                if auto_enabled and auto_var.get():
                    slider.configure(state=tk.DISABLED)
                else:
                    slider.configure(state=tk.NORMAL)

        def _apply_recommended_settings(self) -> None:
            if self.source_image is None or not self.auto_suggestions_var.get():
                return
            preview_path = self.selected_inputs[self.current_preview_index] if self.selected_inputs else None
            recommended = recommend_generation_settings(self.source_image, input_path=preview_path)
            current = {
                "normal_strength": float(self.normal_strength_var.get()),
                "parallax_strength": float(self.parallax_strength_var.get()),
                "glow_threshold": int(self.glow_threshold_var.get()),
                "environment_mask_strength": float(self.environment_mask_strength_var.get()),
                "complex_strength": float(self.complex_strength_var.get()),
                "specular_strength": float(self.specular_strength_var.get()),
            }
            auto_flags = {
                "normal_strength": self.auto_normal_suggestion_var.get(),
                "parallax_strength": self.auto_parallax_suggestion_var.get(),
                "glow_threshold": self.auto_glow_suggestion_var.get(),
                "environment_mask_strength": self.auto_environment_mask_suggestion_var.get(),
                "complex_strength": self.auto_complex_suggestion_var.get(),
                "specular_strength": self.auto_specular_suggestion_var.get(),
            }
            resolved = apply_recommendations_by_auto_flags(current=current, recommended=recommended, auto_flags=auto_flags)
            self.normal_strength_var.set(float(resolved["normal_strength"]))
            self.parallax_strength_var.set(float(resolved["parallax_strength"]))
            self.glow_threshold_var.set(int(resolved["glow_threshold"]))
            self.environment_mask_strength_var.set(float(resolved["environment_mask_strength"]))
            self.complex_strength_var.set(float(resolved["complex_strength"]))
            self.specular_strength_var.set(float(resolved["specular_strength"]))
            self._update_slider_value_labels()
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
                self._update_preview_navigation_state()
                return
            resolved_index = max(0, min(index, len(self.selected_inputs) - 1))
            preview_path = self.selected_inputs[resolved_index]
            with Image.open(preview_path) as src:
                self.source_image = prepare_preview_source(src)
            self.current_preview_index = resolved_index
            if len(self.selected_inputs) > 1:
                self.preview_source_name_var.set(
                    f"{resolved_index + 1}/{len(self.selected_inputs)}: {preview_path.name}"
                )
            else:
                self.preview_source_name_var.set(preview_path.name)
            if apply_recommendations:
                self._apply_recommended_settings()
            self._update_preview_navigation_state()

        def _set_preview_source_by_path(self, path: Path) -> None:
            for index, selected in enumerate(self.selected_inputs):
                if selected == path:
                    self._set_preview_source(index, apply_recommendations=self.auto_suggestions_var.get())
                    self._request_preview_refresh()
                    return

        def _show_previous_preview_source(self) -> None:
            if not self.selected_inputs:
                return
            self._set_preview_source(
                self.current_preview_index - 1, apply_recommendations=self.auto_suggestions_var.get()
            )
            self._refresh_preview()

        def _show_next_preview_source(self) -> None:
            if not self.selected_inputs:
                return
            self._set_preview_source(
                self.current_preview_index + 1, apply_recommendations=self.auto_suggestions_var.get()
            )
            self._refresh_preview()

        def _update_preview_navigation_state(self) -> None:
            has_multiple = len(self.selected_inputs) > 1
            can_navigate = has_multiple and not self.is_processing
            state = tk.NORMAL if can_navigate else tk.DISABLED
            self.prev_source_button.configure(state=state)
            self.next_source_button.configure(state=state)

        def _refresh_preview(self) -> None:
            self.preview_refresh_after_id = None
            if self.source_image is None:
                return
            try:
                outputs = generate_preview_outputs(
                    self.source_image,
                    normal_strength=float(self.normal_strength_var.get()),
                    parallax_strength=float(self.parallax_strength_var.get()),
                    glow_threshold=int(self.glow_threshold_var.get()),
                    environment_mask_strength=float(self.environment_mask_strength_var.get()),
                    complex_strength=float(self.complex_strength_var.get()),
                    specular_strength=float(self.specular_strength_var.get()),
                    complex_format=self.complex_format_var.get(),
                    env_mask_mode=self.env_mask_mode_var.get(),
                    include_diffuse=self.include_diffuse_var.get(),
                    include_normal=self.include_normal_var.get(),
                    include_parallax=self.include_parallax_var.get(),
                    include_glow=self.include_glow_var.get(),
                    include_environment_mask=self.include_environment_mask_var.get(),
                    include_complex=self.include_complex_var.get(),
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
                    photo = self._photo_image(output_image, max_size=output_max)
                    self.preview_output_images[output_key] = photo
                    label.configure(image=photo, text="")
            except Exception as exc:
                self.status_var.set(f"Preview update failed: {exc}")

        def _check_and_show_generation_warnings(
            self,
            material_type: str,
            source_role: str | None,
            source_hint: str | None,
            include_diffuse: bool,
            include_normal: bool,
            include_glow: bool,
            include_environment_mask: bool,
            env_mask_mode: str,
            env_mask_strength: float,
            include_parallax: bool,
            include_complex: bool,
        ) -> bool:
            """Show any applicable sanity warnings. Returns False if user chose to abort."""
            warnings = get_generation_warnings(
                material_type,
                source_role=source_role,
                source_hint=source_hint,
                include_diffuse=include_diffuse,
                include_normal=include_normal,
                include_glow=include_glow,
                include_environment_mask=include_environment_mask,
                env_mask_mode=env_mask_mode,
                env_mask_strength=env_mask_strength,
                include_parallax=include_parallax,
                include_complex=include_complex,
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
            input_value = self.input_var.get().strip()
            if not input_value:
                messagebox.showwarning("Missing input", "Please choose an input DDS texture first.")
                return
            if self.is_processing:
                return

            include_diffuse = self.include_diffuse_var.get()
            include_normal = self.include_normal_var.get()
            include_parallax = self.include_parallax_var.get()
            include_glow = self.include_glow_var.get()
            include_environment_mask = self.include_environment_mask_var.get()
            include_complex = self.include_complex_var.get()
            if not any((include_diffuse, include_normal, include_parallax, include_glow, include_environment_mask, include_complex)):
                messagebox.showwarning("No outputs selected", "Select at least one output type.")
                return

            try:
                input_path = Path(input_value)
                self.selected_inputs = collect_source_textures(input_path)
            except Exception as exc:
                messagebox.showerror("Generation failed", str(exc))
                return

            output_dir: Path | None = None
            if self.use_custom_output_var.get():
                output_value = self.output_var.get().strip()
                if not output_value:
                    messagebox.showwarning("Missing output folder", "Choose an output folder or disable custom output location.")
                    return
                output_dir = Path(output_value)

            _material_type = classify_material_type(Path(input_value))
            _role_info = identify_skyrim_texture_role(self.selected_inputs[0]) if self.selected_inputs else None
            _source_role = _role_info["role"] if _role_info is not None else None
            _source_hint = _role_info["hint"] if _role_info is not None else None
            if not self._check_and_show_generation_warnings(
                _material_type,
                source_role=_source_role,
                source_hint=_source_hint,
                include_diffuse=include_diffuse,
                include_normal=include_normal,
                include_glow=include_glow,
                include_environment_mask=include_environment_mask,
                env_mask_mode=self.env_mask_mode_var.get(),
                env_mask_strength=float(self.environment_mask_strength_var.get()),
                include_parallax=include_parallax,
                include_complex=include_complex,
            ):
                return

            generation_kwargs = {
                "output_dir": output_dir,
                "normal_strength": self._resolve_generation_value(
                    self.normal_strength_var.get(), self.auto_normal_suggestion_var
                ),
                "parallax_strength": self._resolve_generation_value(
                    self.parallax_strength_var.get(), self.auto_parallax_suggestion_var
                ),
                "glow_threshold": self._resolve_generation_value(
                    self.glow_threshold_var.get(), self.auto_glow_suggestion_var
                ),
                "environment_mask_strength": self._resolve_generation_value(
                    self.environment_mask_strength_var.get(), self.auto_environment_mask_suggestion_var
                ),
                "complex_strength": self._resolve_generation_value(
                    self.complex_strength_var.get(), self.auto_complex_suggestion_var
                ),
                "specular_strength": self._resolve_generation_value(
                    self.specular_strength_var.get(), self.auto_specular_suggestion_var
                ),
                "complex_format": self.complex_format_var.get(),
                "env_mask_mode": self.env_mask_mode_var.get(),
                "include_diffuse": include_diffuse,
                "include_normal": include_normal,
                "include_parallax": include_parallax,
                "include_glow": include_glow,
                "include_environment_mask": include_environment_mask,
                "include_complex": include_complex,
            }
            self.batch_failures = []
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

        def run(self) -> None:
            self.root.mainloop()
else:
    class TextureGeneratorGUI:
        def run(self) -> None:
            raise RuntimeError("GUI dependencies are unavailable in this environment.")


def main() -> int:
    args = parse_args()
    if args.gui or args.input_file is None:
        if not GUI_AVAILABLE:
            raise RuntimeError("GUI dependencies are unavailable in this environment.")
        TextureGeneratorGUI().run()
        return 0

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
            complex_name=args.complex_name,
            normal_strength=args.normal_strength,
            parallax_strength=args.parallax_strength,
            glow_threshold=args.glow_threshold,
            environment_mask_strength=args.environment_mask_strength,
            complex_strength=args.complex_strength,
            specular_strength=args.specular_strength,
            complex_format=args.complex_format,
            env_mask_mode=args.environment_mask_mode,
            include_diffuse=not args.no_diffuse,
            include_normal=not args.no_normal,
            include_parallax=not args.no_parallax,
            include_glow=args.glow_map,
            include_environment_mask=args.environment_mask,
            include_complex=args.complex_material,
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
        complex_name=args.complex_name,
        normal_strength=args.normal_strength,
        parallax_strength=args.parallax_strength,
        glow_threshold=args.glow_threshold,
        environment_mask_strength=args.environment_mask_strength,
        complex_strength=args.complex_strength,
        specular_strength=args.specular_strength,
        complex_format=args.complex_format,
        env_mask_mode=args.environment_mask_mode,
        include_diffuse=not args.no_diffuse,
        include_normal=not args.no_normal,
        include_parallax=not args.no_parallax,
        include_glow=args.glow_map,
        include_environment_mask=args.environment_mask,
        include_complex=args.complex_material,
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
