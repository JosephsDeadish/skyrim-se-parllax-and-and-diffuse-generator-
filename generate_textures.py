from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from PIL import ImageTk

    GUI_AVAILABLE = True
except Exception:
    GUI_AVAILABLE = False


def generate_diffuse(source: Image.Image) -> Image.Image:
    return ImageOps.autocontrast(source.convert("RGB"), cutoff=1)


def generate_parallax(source: Image.Image, strength: float = 1.35) -> Image.Image:
    grayscale = ImageOps.grayscale(source)
    detail = grayscale.filter(ImageFilter.UnsharpMask(radius=2, percent=200, threshold=3))
    return ImageEnhance.Contrast(detail).enhance(strength)


def generate_complex_material(source: Image.Image, strength: float = 1.15) -> Image.Image:
    grayscale = ImageOps.grayscale(source)
    edged = grayscale.filter(ImageFilter.FIND_EDGES)
    shaped = ImageEnhance.Contrast(edged).enhance(strength)
    merged = ImageChops.add(grayscale, shaped, scale=1.6)
    return ImageOps.autocontrast(merged, cutoff=2)


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


def build_complex_output_path(
    input_path: Path,
    output_dir: Path | None,
    complex_name: str | None,
) -> Path:
    base_output_dir = output_dir or input_path.parent
    base_output_dir.mkdir(parents=True, exist_ok=True)
    ext = input_path.suffix.lower() if input_path.suffix else ".dds"
    complex_stem = complex_name or f"{input_path.stem}_complex_material"
    return base_output_dir / f"{complex_stem}{ext}"


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


def run_with_options(
    input_file: Path,
    output_dir: Path | None = None,
    diffuse_name: str | None = None,
    parallax_name: str | None = None,
    complex_name: str | None = None,
    parallax_strength: float = 1.35,
    complex_strength: float = 1.15,
    include_diffuse: bool = True,
    include_parallax: bool = True,
    include_complex: bool = False,
) -> dict[str, Path]:
    if not any((include_diffuse, include_parallax, include_complex)):
        raise ValueError("Select at least one output: diffuse, parallax, or complex material.")

    outputs: dict[str, Path] = {}

    with Image.open(input_file) as source:
        if include_diffuse:
            diffuse = generate_diffuse(source)
            diffuse_path, _ = build_output_paths(
                input_path=input_file,
                output_dir=output_dir,
                diffuse_name=diffuse_name,
                parallax_name=parallax_name,
            )
            outputs["diffuse"] = _save_with_dds_fallback(diffuse, diffuse_path)

        if include_parallax:
            parallax = generate_parallax(source, strength=parallax_strength)
            _, parallax_path = build_output_paths(
                input_path=input_file,
                output_dir=output_dir,
                diffuse_name=diffuse_name,
                parallax_name=parallax_name,
            )
            outputs["parallax"] = _save_with_dds_fallback(parallax, parallax_path)

        if include_complex:
            complex_material = generate_complex_material(source, strength=complex_strength)
            complex_path = build_complex_output_path(
                input_path=input_file,
                output_dir=output_dir,
                complex_name=complex_name,
            )
            outputs["complex_material"] = _save_with_dds_fallback(complex_material, complex_path)

    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate diffuse, parallax, and complex material textures from an input DDS texture."
    )
    parser.add_argument("input_file", nargs="?", type=Path, help="Path to input texture file (DDS recommended).")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory for generated files.")
    parser.add_argument("--diffuse-name", type=str, default=None, help="Diffuse output file stem.")
    parser.add_argument("--parallax-name", type=str, default=None, help="Parallax output file stem.")
    parser.add_argument("--complex-name", type=str, default=None, help="Complex material output file stem.")
    parser.add_argument(
        "--parallax-strength",
        type=float,
        default=1.35,
        help="Parallax contrast strength factor.",
    )
    parser.add_argument(
        "--complex-strength",
        type=float,
        default=1.15,
        help="Complex material contrast strength factor.",
    )
    parser.add_argument("--no-diffuse", action="store_true", help="Skip diffuse output generation.")
    parser.add_argument("--no-parallax", action="store_true", help="Skip parallax output generation.")
    parser.add_argument("--complex-material", action="store_true", help="Generate complex material output.")
    parser.add_argument("--gui", action="store_true", help="Launch graphical interface.")
    return parser.parse_args()


class TextureGeneratorGUI:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Skyrim Texture Generator")
        self.root.geometry("960x700")
        self.source_image: Image.Image | None = None
        self.preview_before: ImageTk.PhotoImage | None = None
        self.preview_after: ImageTk.PhotoImage | None = None

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.parallax_strength_var = tk.DoubleVar(value=1.35)
        self.complex_strength_var = tk.DoubleVar(value=1.15)
        self.include_diffuse_var = tk.BooleanVar(value=True)
        self.include_parallax_var = tk.BooleanVar(value=True)
        self.include_complex_var = tk.BooleanVar(value=False)
        self.preview_mode_var = tk.StringVar(value="diffuse")
        self.status_var = tk.StringVar(value="Select a DDS file to begin.")

        self._build_ui()

    def _build_ui(self) -> None:
        wrapper = ttk.Frame(self.root, padding=12)
        wrapper.pack(fill=tk.BOTH, expand=True)

        file_frame = ttk.LabelFrame(wrapper, text="Files", padding=10)
        file_frame.pack(fill=tk.X, padx=4, pady=4)

        ttk.Label(file_frame, text="Input DDS").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Entry(file_frame, textvariable=self.input_var, width=80).grid(row=0, column=1, padx=6, pady=4, sticky=tk.EW)
        ttk.Button(file_frame, text="Browse", command=self._pick_input).grid(row=0, column=2, padx=4, pady=4)

        ttk.Label(file_frame, text="Output folder").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(file_frame, textvariable=self.output_var, width=80).grid(row=1, column=1, padx=6, pady=4, sticky=tk.EW)
        ttk.Button(file_frame, text="Browse", command=self._pick_output).grid(row=1, column=2, padx=4, pady=4)
        file_frame.columnconfigure(1, weight=1)

        options_frame = ttk.LabelFrame(wrapper, text="Generation Options", padding=10)
        options_frame.pack(fill=tk.X, padx=4, pady=4)

        ttk.Checkbutton(options_frame, text="Diffuse", variable=self.include_diffuse_var, command=self._refresh_preview).grid(row=0, column=0, sticky=tk.W)
        ttk.Checkbutton(options_frame, text="Parallax", variable=self.include_parallax_var, command=self._refresh_preview).grid(row=0, column=1, sticky=tk.W)
        ttk.Checkbutton(options_frame, text="Complex material", variable=self.include_complex_var, command=self._refresh_preview).grid(row=0, column=2, sticky=tk.W)

        ttk.Label(options_frame, text="Parallax strength").grid(row=1, column=0, sticky=tk.W, pady=8)
        ttk.Scale(options_frame, from_=0.5, to=3.0, variable=self.parallax_strength_var, command=lambda _: self._refresh_preview()).grid(row=1, column=1, columnspan=2, sticky=tk.EW)
        ttk.Label(options_frame, textvariable=tk.StringVar(value="0.5 - 3.0")).grid(row=1, column=3, sticky=tk.W, padx=8)

        ttk.Label(options_frame, text="Complex strength").grid(row=2, column=0, sticky=tk.W, pady=8)
        ttk.Scale(options_frame, from_=0.5, to=3.0, variable=self.complex_strength_var, command=lambda _: self._refresh_preview()).grid(row=2, column=1, columnspan=2, sticky=tk.EW)
        ttk.Label(options_frame, textvariable=tk.StringVar(value="0.5 - 3.0")).grid(row=2, column=3, sticky=tk.W, padx=8)

        ttk.Label(options_frame, text="Preview output").grid(row=3, column=0, sticky=tk.W, pady=8)
        preview_mode = ttk.Combobox(
            options_frame,
            textvariable=self.preview_mode_var,
            values=("diffuse", "parallax", "complex_material"),
            state="readonly",
            width=20,
        )
        preview_mode.grid(row=3, column=1, sticky=tk.W)
        preview_mode.bind("<<ComboboxSelected>>", lambda _: self._refresh_preview())
        options_frame.columnconfigure(2, weight=1)

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
        ttk.Button(actions, text="Generate", command=self._generate).pack(side=tk.LEFT)
        ttk.Label(actions, textvariable=self.status_var).pack(side=tk.LEFT, padx=14)

    def _pick_input(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select input texture",
            filetypes=[("Texture files", "*.dds *.png *.jpg *.jpeg *.tga *.bmp"), ("All files", "*.*")],
        )
        if not selected:
            return
        self.input_var.set(selected)
        self._load_source(Path(selected))

    def _pick_output(self) -> None:
        selected = filedialog.askdirectory(title="Select output folder")
        if selected:
            self.output_var.set(selected)

    def _load_source(self, path: Path) -> None:
        try:
            with Image.open(path) as src:
                self.source_image = src.copy()
            if not self.output_var.get():
                self.output_var.set(str(path.parent))
            self.status_var.set(f"Loaded: {path.name}")
            self._refresh_preview()
        except Exception as exc:
            self.source_image = None
            messagebox.showerror("Unable to open texture", str(exc))

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
        elif mode == "parallax":
            transformed = generate_parallax(self.source_image, strength=self.parallax_strength_var.get())
        elif mode == "complex_material":
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

        include_diffuse = self.include_diffuse_var.get()
        include_parallax = self.include_parallax_var.get()
        include_complex = self.include_complex_var.get()
        if not any((include_diffuse, include_parallax, include_complex)):
            messagebox.showwarning("No outputs selected", "Select at least one output type.")
            return

        try:
            outputs = run_with_options(
                input_file=Path(input_value),
                output_dir=Path(self.output_var.get()) if self.output_var.get().strip() else None,
                parallax_strength=self.parallax_strength_var.get(),
                complex_strength=self.complex_strength_var.get(),
                include_diffuse=include_diffuse,
                include_parallax=include_parallax,
                include_complex=include_complex,
            )
        except Exception as exc:
            messagebox.showerror("Generation failed", str(exc))
            return

        lines = [f"{key.replace('_', ' ').title()}: {value}" for key, value in outputs.items()]
        self.status_var.set(f"Generated {len(outputs)} file(s).")
        messagebox.showinfo("Generation complete", "\n".join(lines))
        self._refresh_preview()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    args = parse_args()
    if args.gui or args.input_file is None:
        if not GUI_AVAILABLE:
            raise RuntimeError("GUI dependencies are unavailable in this environment.")
        TextureGeneratorGUI().run()
        return 0

    outputs = run_with_options(
        input_file=args.input_file,
        output_dir=args.output_dir,
        diffuse_name=args.diffuse_name,
        parallax_name=args.parallax_name,
        complex_name=args.complex_name,
        parallax_strength=args.parallax_strength,
        complex_strength=args.complex_strength,
        include_diffuse=not args.no_diffuse,
        include_parallax=not args.no_parallax,
        include_complex=args.complex_material,
    )
    for output_type, path in outputs.items():
        print(f"{output_type.replace('_', ' ').title()} texture: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
