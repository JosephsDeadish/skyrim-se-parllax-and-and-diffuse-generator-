from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


def generate_diffuse(source: Image.Image) -> Image.Image:
    return ImageOps.autocontrast(source.convert("RGB"), cutoff=1)


def generate_parallax(source: Image.Image, strength: float = 1.35) -> Image.Image:
    grayscale = ImageOps.grayscale(source)
    detail = grayscale.filter(ImageFilter.UnsharpMask(radius=2, percent=200, threshold=3))
    return ImageEnhance.Contrast(detail).enhance(strength)


def build_output_paths(
    input_path: Path,
    output_dir: Path | None,
    diffuse_name: str | None,
    parallax_name: str | None,
) -> tuple[Path, Path]:
    base_output_dir = output_dir or input_path.parent
    base_output_dir.mkdir(parents=True, exist_ok=True)
    ext = input_path.suffix.lower() if input_path.suffix else ".dds"

    diffuse_stem = diffuse_name or f"{input_path.stem}_diffuse"
    parallax_stem = parallax_name or f"{input_path.stem}_parallax"
    return base_output_dir / f"{diffuse_stem}{ext}", base_output_dir / f"{parallax_stem}{ext}"


def _save_with_dds_fallback(image: Image.Image, output_path: Path) -> Path:
    try:
        image.save(output_path, format="DDS")
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
    with Image.open(input_file) as source:
        diffuse = generate_diffuse(source)
        parallax = generate_parallax(source, strength=parallax_strength)

    diffuse_path, parallax_path = build_output_paths(
        input_path=input_file,
        output_dir=output_dir,
        diffuse_name=diffuse_name,
        parallax_name=parallax_name,
    )

    return _save_with_dds_fallback(diffuse, diffuse_path), _save_with_dds_fallback(parallax, parallax_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate diffuse and parallax textures from an input DDS texture."
    )
    parser.add_argument("input_file", type=Path, help="Path to input texture file (DDS recommended).")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory for generated files.")
    parser.add_argument("--diffuse-name", type=str, default=None, help="Diffuse output file stem.")
    parser.add_argument("--parallax-name", type=str, default=None, help="Parallax output file stem.")
    parser.add_argument(
        "--parallax-strength",
        type=float,
        default=1.35,
        help="Parallax contrast strength factor.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    diffuse_path, parallax_path = run(
        input_file=args.input_file,
        output_dir=args.output_dir,
        diffuse_name=args.diffuse_name,
        parallax_name=args.parallax_name,
        parallax_strength=args.parallax_strength,
    )
    print(f"Diffuse texture: {diffuse_path}")
    print(f"Parallax texture: {parallax_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
