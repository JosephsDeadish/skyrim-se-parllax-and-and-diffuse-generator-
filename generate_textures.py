from __future__ import annotations

import argparse
import queue
import threading
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps, ImageStat

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from PIL import ImageTk

    GUI_AVAILABLE = True
except Exception:
    GUI_AVAILABLE = False


DDS_EXTENSION = ".dds"
SUPPORTED_INPUT_EXTENSIONS = {DDS_EXTENSION, ".png", ".jpg", ".jpeg", ".tga", ".bmp"}
GENERATED_TEXTURE_SUFFIXES = ("_msn", "_cm", "_n", "_p", "_g", "_m")


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _histogram_percentile(histogram: list[int], percentile: float) -> float:
    target = (sum(histogram) * _clamp(percentile, 0.0, 1.0)) or 1.0
    cumulative = 0
    for value, count in enumerate(histogram):
        cumulative += count
        if cumulative >= target:
            return float(value)
    return float(len(histogram) - 1)


def analyze_image_content(source: Image.Image) -> dict[str, float]:
    grayscale = ImageOps.grayscale(source)
    edges = grayscale.filter(ImageFilter.FIND_EDGES)
    blurred = grayscale.filter(ImageFilter.GaussianBlur(radius=2.0))
    detail = ImageChops.difference(grayscale, blurred)

    grayscale_stats = ImageStat.Stat(grayscale)
    edge_stats = ImageStat.Stat(edges)
    detail_stats = ImageStat.Stat(detail)
    minimum, maximum = grayscale.getextrema()
    histogram = grayscale.histogram()
    total_pixels = float(sum(histogram)) or 1.0
    shadow_ratio = sum(histogram[:48]) / total_pixels
    highlight_ratio = sum(histogram[208:]) / total_pixels
    bright_cluster_ratio = sum(histogram[230:]) / total_pixels
    midtone_ratio = sum(histogram[80:176]) / total_pixels
    p90_luma = _histogram_percentile(histogram, 0.90)

    return {
        "brightness": float(grayscale_stats.mean[0]),
        "contrast": float(grayscale_stats.stddev[0]),
        "edge_strength": float(edge_stats.mean[0]),
        "edge_variance": float(edge_stats.stddev[0]),
        "detail_energy": float(detail_stats.mean[0]),
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


def recommend_generation_settings(source: Image.Image) -> dict[str, float | int]:
    analysis = analyze_image_content(source)
    brightness = analysis["brightness"]
    contrast = analysis["contrast"]
    edge_strength = analysis["edge_strength"]
    edge_variance = analysis["edge_variance"]
    detail_energy = analysis["detail_energy"]
    dynamic_range = analysis["dynamic_range"]
    shadow_ratio = analysis["shadow_ratio"]
    highlight_ratio = analysis["highlight_ratio"]
    bright_cluster_ratio = analysis["bright_cluster_ratio"]
    midtone_ratio = analysis["midtone_ratio"]
    p90_luma = analysis["p90_luma"]

    normal_strength = _clamp(
        1.1 + (edge_strength / 180.0) + (edge_variance / 220.0) + (detail_energy / 120.0),
        1.1,
        3.8,
    )
    parallax_strength = _clamp(
        0.9 + (detail_energy / 120.0) + (dynamic_range / 800.0) + ((0.25 - highlight_ratio) * 0.7),
        0.8,
        2.4,
    )
    environment_mask_strength = _clamp(
        0.9 + (contrast / 160.0) + (midtone_ratio * 0.55),
        0.9,
        2.4,
    )
    complex_strength = _clamp(
        1.0 + (edge_strength / 210.0) + (detail_energy / 110.0) + (dynamic_range / 1000.0),
        1.0,
        2.6,
    )
    specular_strength = _clamp(
        0.8 + (highlight_ratio * 1.6) + (edge_variance / 260.0) + ((0.3 - shadow_ratio) * 0.45),
        0.8,
        2.4,
    )
    glow_threshold = int(
        _clamp(
            (p90_luma * 0.78)
            + (brightness * 0.2)
            + (contrast * 0.25)
            + (highlight_ratio * 18.0)
            - (bright_cluster_ratio * 30.0)
            - (detail_energy * 0.12),
            140.0,
            235.0,
        )
    )

    return {
        "normal_strength": normal_strength,
        "parallax_strength": parallax_strength,
        "environment_mask_strength": environment_mask_strength,
        "complex_strength": complex_strength,
        "specular_strength": specular_strength,
        "glow_threshold": glow_threshold,
    }


def _prepare_height_map(source: Image.Image) -> Image.Image:
    grayscale = ImageOps.grayscale(source)
    broad = grayscale.filter(ImageFilter.GaussianBlur(radius=4.0))
    fine = grayscale.filter(ImageFilter.UnsharpMask(radius=1.6, percent=180, threshold=2))
    detail = ImageChops.subtract(fine, broad, scale=1.0, offset=128)
    merged = ImageChops.add(grayscale, detail, scale=1.55, offset=-36)
    return ImageOps.autocontrast(merged, cutoff=1)


def generate_diffuse(source: Image.Image) -> Image.Image:
    return ImageOps.autocontrast(source.convert("RGB"), cutoff=1)


def generate_parallax(source: Image.Image, strength: float = 1.35) -> Image.Image:
    height_map = _prepare_height_map(source)
    softened = height_map.filter(ImageFilter.GaussianBlur(radius=1.2))
    micro_detail = ImageChops.subtract(height_map, height_map.filter(ImageFilter.GaussianBlur(radius=8.0)), scale=1.0, offset=128)
    merged = ImageChops.add(softened, micro_detail, scale=1.35, offset=-20)
    contrasted = ImageEnhance.Contrast(merged).enhance(strength)
    return ImageOps.autocontrast(contrasted, cutoff=1)


def generate_normal(source: Image.Image, strength: float = 2.0) -> Image.Image:
    height_map = _prepare_height_map(source).filter(ImageFilter.GaussianBlur(radius=0.8))
    sobel_x = height_map.filter(ImageFilter.Kernel((3, 3), (-1, 0, 1, -2, 0, 2, -1, 0, 1), scale=1, offset=128))
    sobel_y = height_map.filter(ImageFilter.Kernel((3, 3), (-1, -2, -1, 0, 0, 0, 1, 2, 1), scale=1, offset=128))
    red = sobel_x.point(lambda value: int(_clamp(128.0 - ((value - 128.0) * strength), 0.0, 255.0)))
    green = sobel_y.point(lambda value: int(_clamp(128.0 - ((value - 128.0) * strength), 0.0, 255.0)))
    midpoint = Image.new("L", height_map.size, color=128)
    slope = ImageChops.add(
        ImageChops.difference(red, midpoint),
        ImageChops.difference(green, midpoint),
        scale=max(1.0, 1.35 + (strength * 0.3)),
    )
    blue = ImageOps.invert(slope).point(lambda value: int(_clamp(max(128.0, value), 0.0, 255.0)))
    return Image.merge("RGB", (red, green, blue))


def generate_glow(source: Image.Image, threshold: int = 190) -> Image.Image:
    grayscale = ImageOps.grayscale(source)
    boosted = ImageEnhance.Contrast(grayscale).enhance(1.25)
    return boosted.point(lambda v: 255 if v >= threshold else 0)


def generate_environment_mask(source: Image.Image, strength: float = 1.2) -> Image.Image:
    grayscale = ImageOps.grayscale(source)
    softened = grayscale.filter(ImageFilter.GaussianBlur(radius=1.0))
    return ImageEnhance.Contrast(softened).enhance(strength)


def generate_complex_material(source: Image.Image, strength: float = 1.15) -> Image.Image:
    grayscale = ImageOps.grayscale(source)
    edges = grayscale.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.UnsharpMask(radius=1.5, percent=200, threshold=2))
    highpass = ImageChops.subtract(grayscale, grayscale.filter(ImageFilter.GaussianBlur(radius=2.4)), scale=1.0, offset=128)
    shaped = ImageEnhance.Contrast(ImageChops.add(edges, highpass, scale=1.5)).enhance(strength)
    merged = ImageChops.add(grayscale, shaped, scale=1.45)
    return ImageOps.autocontrast(merged, cutoff=2)


def generate_specular(source: Image.Image, strength: float = 1.15) -> Image.Image:
    grayscale = ImageOps.grayscale(source)
    smoothed = grayscale.filter(ImageFilter.GaussianBlur(radius=0.8))
    edges = smoothed.filter(ImageFilter.FIND_EDGES)
    base = ImageEnhance.Contrast(smoothed).enhance(1.2)
    boosted_edges = ImageEnhance.Brightness(edges).enhance(0.7)
    specular = ImageChops.add(base, boosted_edges, scale=1.35)
    return ImageEnhance.Contrast(specular).enhance(strength)


def generate_msn(source: Image.Image, normal_strength: float = 2.0, specular_strength: float = 1.15) -> Image.Image:
    normal_rgb = generate_normal(source, strength=normal_strength)
    specular_alpha = generate_specular(source, strength=specular_strength)
    r, g, b = normal_rgb.split()
    return Image.merge("RGBA", (r, g, b, specular_alpha))


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
        for path in input_path.iterdir()
        if path.is_file() and path.suffix.lower() == DDS_EXTENSION and not _is_generated_texture(path)
    )
    if not source_files:
        raise ValueError(
            f"No source DDS textures found in {input_path}. "
            "Folder mode only processes original DDS files and skips generated *_n, *_p, *_g, *_m, *_msn, and *_cm variants."
        )
    return source_files


def _to_dds_compatible_image(image: Image.Image) -> Image.Image:
    if image.mode == "RGBA":
        return image
    if image.mode in {"RGB", "L", "LA"}:
        return image.convert("RGBA")
    return image.convert("RGBA")


def _save_with_dds_fallback(image: Image.Image, output_path: Path) -> Path:
    dds_image = _to_dds_compatible_image(image)
    try:
        dds_image.save(output_path.with_suffix(DDS_EXTENSION), format="DDS", pixel_format="DXT5")
        return output_path
    except Exception:
        fallback = output_path.with_suffix(".png")
        image.save(fallback, format="PNG")
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
        recommended = recommend_generation_settings(source)
        resolved_normal_strength = normal_strength if normal_strength is not None else float(recommended["normal_strength"])
        resolved_parallax_strength = parallax_strength if parallax_strength is not None else float(recommended["parallax_strength"])
        resolved_glow_threshold = glow_threshold if glow_threshold is not None else int(recommended["glow_threshold"])
        resolved_environment_mask_strength = (
            environment_mask_strength if environment_mask_strength is not None else float(recommended["environment_mask_strength"])
        )
        resolved_complex_strength = complex_strength if complex_strength is not None else float(recommended["complex_strength"])
        resolved_specular_strength = specular_strength if specular_strength is not None else float(recommended["specular_strength"])

        if include_diffuse:
            diffuse = generate_diffuse(source)
            diffuse_path, _ = build_output_paths(
                input_path=input_file,
                output_dir=output_dir,
                diffuse_name=diffuse_name,
                parallax_name=parallax_name,
            )
            outputs["diffuse"] = _save_with_dds_fallback(diffuse, diffuse_path)

        if include_normal:
            normal = generate_normal(source, strength=resolved_normal_strength)
            normal_path = build_normal_output_path(
                input_path=input_file,
                output_dir=output_dir,
                normal_name=normal_name,
            )
            outputs["normal"] = _save_with_dds_fallback(normal, normal_path)

        if include_parallax:
            parallax = generate_parallax(source, strength=resolved_parallax_strength)
            _, parallax_path = build_output_paths(
                input_path=input_file,
                output_dir=output_dir,
                diffuse_name=diffuse_name,
                parallax_name=parallax_name,
            )
            outputs["parallax"] = _save_with_dds_fallback(parallax, parallax_path)

        if include_glow:
            glow = generate_glow(source, threshold=resolved_glow_threshold)
            glow_path = build_glow_output_path(
                input_path=input_file,
                output_dir=output_dir,
                glow_name=glow_name,
            )
            outputs["glow"] = _save_with_dds_fallback(glow, glow_path)

        if include_environment_mask:
            environment_mask = generate_environment_mask(source, strength=resolved_environment_mask_strength)
            environment_mask_path = build_environment_mask_output_path(
                input_path=input_file,
                output_dir=output_dir,
                environment_mask_name=environment_mask_name,
            )
            outputs["environment_mask"] = _save_with_dds_fallback(environment_mask, environment_mask_path)

        if include_complex:
            if complex_format == "msn":
                complex_material = generate_msn(
                    source,
                    normal_strength=resolved_normal_strength,
                    specular_strength=resolved_specular_strength,
                )
            else:
                complex_material = generate_complex_material(source, strength=resolved_complex_strength)
            complex_path = build_complex_output_path(
                input_path=input_file,
                output_dir=output_dir,
                complex_name=complex_name,
                complex_format=complex_format,
            )
            outputs["complex_material"] = _save_with_dds_fallback(complex_material, complex_path)

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
    include_diffuse: bool = True,
    include_normal: bool = True,
    include_parallax: bool = True,
    include_glow: bool = False,
    include_environment_mask: bool = False,
    include_complex: bool = False,
    progress_callback: Callable[[int, int, Path], None] | None = None,
) -> dict[Path, dict[str, Path]]:
    input_files = collect_source_textures(input_path)
    results: dict[Path, dict[str, Path]] = {}
    total = len(input_files)

    for index, input_file in enumerate(input_files, start=1):
        if progress_callback is not None:
            progress_callback(index, total, input_file)
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
            include_diffuse=include_diffuse,
            include_normal=include_normal,
            include_parallax=include_parallax,
            include_glow=include_glow,
            include_environment_mask=include_environment_mask,
            include_complex=include_complex,
        )

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
        help="Complex material naming suffix: msn -> _msn, cm -> _cm.",
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
    parser.add_argument("--complex-material", action="store_true", help="Generate complex material output.")
    parser.add_argument("--gui", action="store_true", help="Launch graphical interface.")
    return parser.parse_args()


if GUI_AVAILABLE:
    class TextureGeneratorGUI:
        def __init__(self) -> None:
            self.root = tk.Tk()
            self.root.title("Skyrim Texture Generator")
            self.root.geometry("960x700")
            self.source_image: Image.Image | None = None
            self.preview_before: ImageTk.PhotoImage | None = None
            self.preview_after: ImageTk.PhotoImage | None = None
            self.selected_inputs: list[Path] = []
            self.processing_thread: threading.Thread | None = None
            self.processing_queue: queue.Queue[tuple[str, object]] = queue.Queue()
            self.is_processing = False

            self.input_var = tk.StringVar()
            self.output_var = tk.StringVar()
            self.normal_strength_var = tk.DoubleVar(value=2.0)
            self.parallax_strength_var = tk.DoubleVar(value=1.35)
            self.complex_strength_var = tk.DoubleVar(value=1.15)
            self.specular_strength_var = tk.DoubleVar(value=1.15)
            self.glow_threshold_var = tk.IntVar(value=190)
            self.environment_mask_strength_var = tk.DoubleVar(value=1.2)
            self.complex_format_var = tk.StringVar(value="msn")
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
            self.preview_mode_var = tk.StringVar(value="diffuse")
            self.status_var = tk.StringVar(value="Select a DDS file to begin.")

            self._build_ui()

        def _build_ui(self) -> None:
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

            ttk.Label(file_frame, text="Input DDS or folder").grid(row=0, column=0, sticky=tk.W, pady=4)
            ttk.Entry(file_frame, textvariable=self.input_var, width=80).grid(row=0, column=1, padx=6, pady=4, sticky=tk.EW)
            self.input_file_button = ttk.Button(file_frame, text="File", command=self._pick_input)
            self.input_file_button.grid(row=0, column=2, padx=4, pady=4)
            self.input_folder_button = ttk.Button(file_frame, text="Folder", command=self._pick_input_folder)
            self.input_folder_button.grid(row=0, column=3, padx=4, pady=4)

            ttk.Label(file_frame, text="Output folder").grid(row=1, column=0, sticky=tk.W, pady=4)
            ttk.Entry(file_frame, textvariable=self.output_var, width=80).grid(row=1, column=1, padx=6, pady=4, sticky=tk.EW)
            self.output_button = ttk.Button(file_frame, text="Browse", command=self._pick_output)
            self.output_button.grid(row=1, column=2, padx=4, pady=4)
            file_frame.columnconfigure(1, weight=1)

            options_frame = ttk.LabelFrame(wrapper, text="Generation Options", padding=10)
            options_frame.pack(fill=tk.X, padx=4, pady=4)

            ttk.Checkbutton(options_frame, text="Diffuse", variable=self.include_diffuse_var, command=self._refresh_preview).grid(row=0, column=0, sticky=tk.W)
            ttk.Checkbutton(options_frame, text="Normal / _n", variable=self.include_normal_var, command=self._refresh_preview).grid(row=0, column=1, sticky=tk.W)
            ttk.Checkbutton(options_frame, text="Parallax / _p", variable=self.include_parallax_var, command=self._refresh_preview).grid(row=0, column=2, sticky=tk.W)
            ttk.Checkbutton(options_frame, text="Glow / _g", variable=self.include_glow_var, command=self._refresh_preview).grid(row=1, column=0, sticky=tk.W)
            ttk.Checkbutton(options_frame, text="Environment mask / _m", variable=self.include_environment_mask_var, command=self._refresh_preview).grid(row=1, column=1, sticky=tk.W)
            ttk.Checkbutton(options_frame, text="Complex material", variable=self.include_complex_var, command=self._refresh_preview).grid(row=1, column=2, sticky=tk.W)
            ttk.Checkbutton(
                options_frame,
                text="Automatic suggestions (analyze image and set sliders)",
                variable=self.auto_suggestions_var,
                command=self._toggle_auto_suggestions,
            ).grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(6, 2))

            ttk.Label(options_frame, text="Complex naming").grid(row=3, column=0, sticky=tk.W, pady=8)
            complex_format = ttk.Combobox(
                options_frame,
                textvariable=self.complex_format_var,
                values=("msn", "cm"),
                state="readonly",
                width=20,
            )
            complex_format.grid(row=3, column=1, sticky=tk.W)

            ttk.Label(options_frame, text="Normal strength").grid(row=4, column=0, sticky=tk.W, pady=8)
            self.normal_scale = ttk.Scale(options_frame, from_=0.5, to=4.0, variable=self.normal_strength_var, command=lambda _: self._refresh_preview())
            self.normal_scale.grid(row=4, column=1, columnspan=2, sticky=tk.EW)
            ttk.Label(options_frame, textvariable=tk.StringVar(value="0.5 - 4.0")).grid(row=4, column=3, sticky=tk.W, padx=8)
            ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_normal_suggestion_var, command=self._on_auto_slider_preference_changed).grid(row=4, column=4, sticky=tk.W)

            ttk.Label(options_frame, text="Parallax strength").grid(row=5, column=0, sticky=tk.W, pady=8)
            self.parallax_scale = ttk.Scale(options_frame, from_=0.5, to=3.0, variable=self.parallax_strength_var, command=lambda _: self._refresh_preview())
            self.parallax_scale.grid(row=5, column=1, columnspan=2, sticky=tk.EW)
            ttk.Label(options_frame, textvariable=tk.StringVar(value="0.5 - 3.0")).grid(row=5, column=3, sticky=tk.W, padx=8)
            ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_parallax_suggestion_var, command=self._on_auto_slider_preference_changed).grid(row=5, column=4, sticky=tk.W)

            ttk.Label(options_frame, text="Glow threshold").grid(row=6, column=0, sticky=tk.W, pady=8)
            self.glow_scale = ttk.Scale(options_frame, from_=0, to=255, variable=self.glow_threshold_var, command=lambda _: self._refresh_preview())
            self.glow_scale.grid(row=6, column=1, columnspan=2, sticky=tk.EW)
            ttk.Label(options_frame, textvariable=tk.StringVar(value="0 - 255")).grid(row=6, column=3, sticky=tk.W, padx=8)
            ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_glow_suggestion_var, command=self._on_auto_slider_preference_changed).grid(row=6, column=4, sticky=tk.W)

            ttk.Label(options_frame, text="Environment mask strength").grid(row=7, column=0, sticky=tk.W, pady=8)
            self.environment_mask_scale = ttk.Scale(options_frame, from_=0.5, to=3.0, variable=self.environment_mask_strength_var, command=lambda _: self._refresh_preview())
            self.environment_mask_scale.grid(row=7, column=1, columnspan=2, sticky=tk.EW)
            ttk.Label(options_frame, textvariable=tk.StringVar(value="0.5 - 3.0")).grid(row=7, column=3, sticky=tk.W, padx=8)
            ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_environment_mask_suggestion_var, command=self._on_auto_slider_preference_changed).grid(row=7, column=4, sticky=tk.W)

            ttk.Label(options_frame, text="Complex strength").grid(row=8, column=0, sticky=tk.W, pady=8)
            self.complex_scale = ttk.Scale(options_frame, from_=0.5, to=3.0, variable=self.complex_strength_var, command=lambda _: self._refresh_preview())
            self.complex_scale.grid(row=8, column=1, columnspan=2, sticky=tk.EW)
            ttk.Label(options_frame, textvariable=tk.StringVar(value="0.5 - 3.0")).grid(row=8, column=3, sticky=tk.W, padx=8)
            ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_complex_suggestion_var, command=self._on_auto_slider_preference_changed).grid(row=8, column=4, sticky=tk.W)

            ttk.Label(options_frame, text="Specular strength (_msn alpha)").grid(row=9, column=0, sticky=tk.W, pady=8)
            self.specular_scale = ttk.Scale(options_frame, from_=0.5, to=3.0, variable=self.specular_strength_var, command=lambda _: self._refresh_preview())
            self.specular_scale.grid(row=9, column=1, columnspan=2, sticky=tk.EW)
            ttk.Label(options_frame, textvariable=tk.StringVar(value="0.5 - 3.0")).grid(row=9, column=3, sticky=tk.W, padx=8)
            ttk.Checkbutton(options_frame, text="Auto", variable=self.auto_specular_suggestion_var, command=self._on_auto_slider_preference_changed).grid(row=9, column=4, sticky=tk.W)

            ttk.Label(options_frame, text="Preview output").grid(row=10, column=0, sticky=tk.W, pady=8)
            preview_mode = ttk.Combobox(
                options_frame,
                textvariable=self.preview_mode_var,
                values=("diffuse", "normal", "parallax", "glow", "environment_mask", "complex_material"),
                state="readonly",
                width=20,
            )
            preview_mode.grid(row=10, column=1, sticky=tk.W)
            preview_mode.bind("<<ComboboxSelected>>", lambda _: self._refresh_preview())
            options_frame.columnconfigure(2, weight=1)
            self._update_slider_auto_states()

            preview_frame = ttk.LabelFrame(wrapper, text="Preview", padding=10)
            preview_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

            self.before_label = ttk.Label(preview_frame, text="Before")
            self.before_label.grid(row=0, column=0, padx=10, pady=4)
            self.after_label = ttk.Label(preview_frame, text="After")
            self.after_label.grid(row=0, column=1, padx=10, pady=4)

            self.before_image_label = ttk.Label(preview_frame, text="No source loaded")
            self.before_image_label.grid(row=1, column=0, padx=10, pady=8)
            self.after_image_label = ttk.Label(preview_frame, text="No preview")
            self.after_image_label.grid(row=1, column=1, padx=10, pady=8)
            preview_frame.columnconfigure(0, weight=1)
            preview_frame.columnconfigure(1, weight=1)

            actions = ttk.Frame(wrapper, padding=(4, 10))
            actions.pack(fill=tk.X)
            self.generate_button = ttk.Button(actions, text="Generate", command=self._generate)
            self.generate_button.pack(side=tk.LEFT)
            ttk.Label(actions, textvariable=self.status_var).pack(side=tk.LEFT, padx=14)

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

        def _pick_input(self) -> None:
            selected = filedialog.askopenfilename(
                title="Select input texture",
                filetypes=[("Texture files", "*.dds *.png *.jpg *.jpeg *.tga *.bmp"), ("All files", "*.*")],
            )
            if not selected:
                return
            self.input_var.set(selected)
            self._load_input_selection(Path(selected))

        def _pick_input_folder(self) -> None:
            selected = filedialog.askdirectory(title="Select folder with source DDS textures")
            if not selected:
                return
            self.input_var.set(selected)
            self._load_input_selection(Path(selected))

        def _pick_output(self) -> None:
            selected = filedialog.askdirectory(title="Select output folder")
            if selected:
                self.output_var.set(selected)

        def _load_input_selection(self, path: Path) -> None:
            try:
                input_files = collect_source_textures(path)
                preview_path = input_files[0]
                with Image.open(preview_path) as src:
                    self.source_image = src.convert("RGB")
                self.selected_inputs = input_files
                self.output_var.set(str(path if path.is_dir() else path.parent))
                self._apply_recommended_settings()
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
            self.output_button.configure(state=state)

        def _process_generation_batch(self, input_path: Path, generation_kwargs: dict[str, object]) -> None:
            try:
                results = run_batch_with_options(
                    input_path=input_path,
                    progress_callback=lambda index, total, current: self.processing_queue.put(
                        ("progress", (index, total, current.name))
                    ),
                    **generation_kwargs,
                )
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
                    index, total, filename = payload
                    self.status_var.set(f"Processing {index}/{total}: {filename}")
                elif event_type == "done":
                    results = payload
                    self._set_processing_state(False)
                    total_sources = len(results)
                    total_outputs = sum(len(output_set) for output_set in results.values())
                    self.status_var.set(f"Generated {total_outputs} file(s) from {total_sources} source texture(s).")
                    if total_sources == 1:
                        only_outputs = next(iter(results.values()))
                        lines = [f"{key.replace('_', ' ').title()}: {value}" for key, value in only_outputs.items()]
                    else:
                        lines = [
                            f"Processed {total_sources} source textures.",
                            f"Generated {total_outputs} files.",
                        ]
                    messagebox.showinfo("Generation complete", "\n".join(lines))
                    self._refresh_preview()
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
            recommended = recommend_generation_settings(self.source_image)
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
            self._update_slider_auto_states()

        def _photo_image(self, image: Image.Image, max_size: int = 340) -> ImageTk.PhotoImage:
            preview = image.copy()
            preview.thumbnail((max_size, max_size))
            if preview.mode != "RGB":
                preview = preview.convert("RGB")
            return ImageTk.PhotoImage(preview)

        def _refresh_preview(self) -> None:
            if self.source_image is None:
                return
            mode = self.preview_mode_var.get()

            transformed = self.source_image
            if mode == "diffuse":
                transformed = generate_diffuse(self.source_image)
            elif mode == "normal":
                transformed = generate_normal(self.source_image, strength=self.normal_strength_var.get())
            elif mode == "parallax":
                transformed = generate_parallax(self.source_image, strength=self.parallax_strength_var.get())
            elif mode == "glow":
                transformed = generate_glow(self.source_image, threshold=self.glow_threshold_var.get())
            elif mode == "environment_mask":
                transformed = generate_environment_mask(self.source_image, strength=self.environment_mask_strength_var.get())
            elif mode == "complex_material":
                if self.complex_format_var.get() == "msn":
                    transformed = generate_msn(
                        self.source_image,
                        normal_strength=self.normal_strength_var.get(),
                        specular_strength=self.specular_strength_var.get(),
                    )
                else:
                    transformed = generate_complex_material(self.source_image, strength=self.complex_strength_var.get())

            self.preview_before = self._photo_image(self.source_image)
            self.preview_after = self._photo_image(transformed)
            self.before_image_label.configure(image=self.preview_before, text="")
            self.after_image_label.configure(image=self.preview_after, text="")

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

            generation_kwargs = {
                "output_dir": Path(self.output_var.get()) if self.output_var.get().strip() else None,
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
                "include_diffuse": include_diffuse,
                "include_normal": include_normal,
                "include_parallax": include_parallax,
                "include_glow": include_glow,
                "include_environment_mask": include_environment_mask,
                "include_complex": include_complex,
            }
            self._set_processing_state(True)
            self.status_var.set(f"Queued {len(self.selected_inputs)} source texture(s) for processing...")
            self.processing_thread = threading.Thread(
                target=self._process_generation_batch,
                args=(input_path, generation_kwargs),
                daemon=True,
            )
            self.processing_thread.start()
            self.root.after(100, self._poll_processing_queue)

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
            include_diffuse=not args.no_diffuse,
            include_normal=not args.no_normal,
            include_parallax=not args.no_parallax,
            include_glow=args.glow_map,
            include_environment_mask=args.environment_mask,
            include_complex=args.complex_material,
        )
        for input_file, outputs in batch_outputs.items():
            print(f"[{input_file.name}]")
            for output_type, path in outputs.items():
                print(f"  {output_type.replace('_', ' ').title()} texture: {path}")
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


if __name__ == "__main__":
    raise SystemExit(main())
