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


DDS_EXTENSION = ".dds"


def generate_diffuse(source: Image.Image) -> Image.Image:
    return ImageOps.autocontrast(source.convert("RGB"), cutoff=1)


def generate_parallax(source: Image.Image, strength: float = 1.35) -> Image.Image:
    grayscale = ImageOps.grayscale(source)
    detail = grayscale.filter(ImageFilter.UnsharpMask(radius=2, percent=200, threshold=3))
    return ImageEnhance.Contrast(detail).enhance(strength)


def generate_normal(source: Image.Image, strength: float = 2.0) -> Image.Image:
    height = ImageOps.grayscale(source).filter(ImageFilter.GaussianBlur(radius=1.0))
    width, height_px = height.size
    src = height.load()
    normal = Image.new("RGB", (width, height_px))
    dst = normal.load()

    for y in range(height_px):
        y_prev = y - 1 if y > 0 else 0
        y_next = y + 1 if y < height_px - 1 else height_px - 1
        for x in range(width):
            x_prev = x - 1 if x > 0 else 0
            x_next = x + 1 if x < width - 1 else width - 1

            dx = ((src[x_next, y] - src[x_prev, y]) / 255.0) * strength
            dy = ((src[x, y_next] - src[x, y_prev]) / 255.0) * strength
            dz = 1.0

            nx = -dx
            ny = -dy
            length = (nx * nx + ny * ny + dz * dz) ** 0.5 or 1.0
            nx /= length
            ny /= length
            nz = dz / length

            dst[x, y] = (
                int((nx * 0.5 + 0.5) * 255),
                int((ny * 0.5 + 0.5) * 255),
                int((nz * 0.5 + 0.5) * 255),
            )

    return normal


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
    normal_name: str | None = None,
    parallax_name: str | None = None,
    glow_name: str | None = None,
    environment_mask_name: str | None = None,
    complex_name: str | None = None,
    normal_strength: float = 2.0,
    parallax_strength: float = 1.35,
    glow_threshold: int = 190,
    environment_mask_strength: float = 1.2,
    complex_strength: float = 1.15,
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

        if include_normal:
            normal = generate_normal(source, strength=normal_strength)
            normal_path = build_normal_output_path(
                input_path=input_file,
                output_dir=output_dir,
                normal_name=normal_name,
            )
            outputs["normal"] = _save_with_dds_fallback(normal, normal_path)

        if include_parallax:
            parallax = generate_parallax(source, strength=parallax_strength)
            _, parallax_path = build_output_paths(
                input_path=input_file,
                output_dir=output_dir,
                diffuse_name=diffuse_name,
                parallax_name=parallax_name,
            )
            outputs["parallax"] = _save_with_dds_fallback(parallax, parallax_path)

        if include_glow:
            glow = generate_glow(source, threshold=glow_threshold)
            glow_path = build_glow_output_path(
                input_path=input_file,
                output_dir=output_dir,
                glow_name=glow_name,
            )
            outputs["glow"] = _save_with_dds_fallback(glow, glow_path)

        if include_environment_mask:
            environment_mask = generate_environment_mask(source, strength=environment_mask_strength)
            environment_mask_path = build_environment_mask_output_path(
                input_path=input_file,
                output_dir=output_dir,
                environment_mask_name=environment_mask_name,
            )
            outputs["environment_mask"] = _save_with_dds_fallback(environment_mask, environment_mask_path)

        if include_complex:
            complex_material = generate_complex_material(source, strength=complex_strength)
            complex_path = build_complex_output_path(
                input_path=input_file,
                output_dir=output_dir,
                complex_name=complex_name,
                complex_format=complex_format,
            )
            outputs["complex_material"] = _save_with_dds_fallback(complex_material, complex_path)

    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Skyrim texture maps from an input texture."
    )
    parser.add_argument("input_file", nargs="?", type=Path, help="Path to input texture file (DDS recommended).")
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
        default=2.0,
        help="Normal map detail strength factor.",
    )
    parser.add_argument(
        "--parallax-strength",
        type=float,
        default=1.35,
        help="Parallax contrast strength factor.",
    )
    parser.add_argument(
        "--glow-threshold",
        type=int,
        default=190,
        help="Glow brightness threshold (0-255).",
    )
    parser.add_argument(
        "--environment-mask-strength",
        type=float,
        default=1.2,
        help="Environment mask contrast strength factor.",
    )
    parser.add_argument(
        "--complex-strength",
        type=float,
        default=1.15,
        help="Complex material contrast strength factor.",
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

            self.input_var = tk.StringVar()
            self.output_var = tk.StringVar()
            self.normal_strength_var = tk.DoubleVar(value=2.0)
            self.parallax_strength_var = tk.DoubleVar(value=1.35)
            self.complex_strength_var = tk.DoubleVar(value=1.15)
            self.glow_threshold_var = tk.IntVar(value=190)
            self.environment_mask_strength_var = tk.DoubleVar(value=1.2)
            self.complex_format_var = tk.StringVar(value="msn")
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
            ttk.Checkbutton(options_frame, text="Normal / _n", variable=self.include_normal_var, command=self._refresh_preview).grid(row=0, column=1, sticky=tk.W)
            ttk.Checkbutton(options_frame, text="Parallax / _p", variable=self.include_parallax_var, command=self._refresh_preview).grid(row=0, column=2, sticky=tk.W)
            ttk.Checkbutton(options_frame, text="Glow / _g", variable=self.include_glow_var, command=self._refresh_preview).grid(row=1, column=0, sticky=tk.W)
            ttk.Checkbutton(options_frame, text="Environment mask / _m", variable=self.include_environment_mask_var, command=self._refresh_preview).grid(row=1, column=1, sticky=tk.W)
            ttk.Checkbutton(options_frame, text="Complex material", variable=self.include_complex_var, command=self._refresh_preview).grid(row=1, column=2, sticky=tk.W)

            ttk.Label(options_frame, text="Complex naming").grid(row=2, column=0, sticky=tk.W, pady=8)
            complex_format = ttk.Combobox(
                options_frame,
                textvariable=self.complex_format_var,
                values=("msn", "cm"),
                state="readonly",
                width=20,
            )
            complex_format.grid(row=2, column=1, sticky=tk.W)

            ttk.Label(options_frame, text="Normal strength").grid(row=3, column=0, sticky=tk.W, pady=8)
            ttk.Scale(options_frame, from_=0.5, to=4.0, variable=self.normal_strength_var, command=lambda _: self._refresh_preview()).grid(row=3, column=1, columnspan=2, sticky=tk.EW)
            ttk.Label(options_frame, textvariable=tk.StringVar(value="0.5 - 4.0")).grid(row=3, column=3, sticky=tk.W, padx=8)

            ttk.Label(options_frame, text="Parallax strength").grid(row=4, column=0, sticky=tk.W, pady=8)
            ttk.Scale(options_frame, from_=0.5, to=3.0, variable=self.parallax_strength_var, command=lambda _: self._refresh_preview()).grid(row=4, column=1, columnspan=2, sticky=tk.EW)
            ttk.Label(options_frame, textvariable=tk.StringVar(value="0.5 - 3.0")).grid(row=4, column=3, sticky=tk.W, padx=8)

            ttk.Label(options_frame, text="Glow threshold").grid(row=5, column=0, sticky=tk.W, pady=8)
            ttk.Scale(options_frame, from_=0, to=255, variable=self.glow_threshold_var, command=lambda _: self._refresh_preview()).grid(row=5, column=1, columnspan=2, sticky=tk.EW)
            ttk.Label(options_frame, textvariable=tk.StringVar(value="0 - 255")).grid(row=5, column=3, sticky=tk.W, padx=8)

            ttk.Label(options_frame, text="Environment mask strength").grid(row=6, column=0, sticky=tk.W, pady=8)
            ttk.Scale(options_frame, from_=0.5, to=3.0, variable=self.environment_mask_strength_var, command=lambda _: self._refresh_preview()).grid(row=6, column=1, columnspan=2, sticky=tk.EW)
            ttk.Label(options_frame, textvariable=tk.StringVar(value="0.5 - 3.0")).grid(row=6, column=3, sticky=tk.W, padx=8)

            ttk.Label(options_frame, text="Complex strength").grid(row=7, column=0, sticky=tk.W, pady=8)
            ttk.Scale(options_frame, from_=0.5, to=3.0, variable=self.complex_strength_var, command=lambda _: self._refresh_preview()).grid(row=7, column=1, columnspan=2, sticky=tk.EW)
            ttk.Label(options_frame, textvariable=tk.StringVar(value="0.5 - 3.0")).grid(row=7, column=3, sticky=tk.W, padx=8)

            ttk.Label(options_frame, text="Preview output").grid(row=8, column=0, sticky=tk.W, pady=8)
            preview_mode = ttk.Combobox(
                options_frame,
                textvariable=self.preview_mode_var,
                values=("diffuse", "normal", "parallax", "glow", "environment_mask", "complex_material"),
                state="readonly",
                width=20,
            )
            preview_mode.grid(row=8, column=1, sticky=tk.W)
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
            elif mode == "normal":
                transformed = generate_normal(self.source_image, strength=self.normal_strength_var.get())
            elif mode == "parallax":
                transformed = generate_parallax(self.source_image, strength=self.parallax_strength_var.get())
            elif mode == "glow":
                transformed = generate_glow(self.source_image, threshold=self.glow_threshold_var.get())
            elif mode == "environment_mask":
                transformed = generate_environment_mask(self.source_image, strength=self.environment_mask_strength_var.get())
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
            include_normal = self.include_normal_var.get()
            include_parallax = self.include_parallax_var.get()
            include_glow = self.include_glow_var.get()
            include_environment_mask = self.include_environment_mask_var.get()
            include_complex = self.include_complex_var.get()
            if not any((include_diffuse, include_normal, include_parallax, include_glow, include_environment_mask, include_complex)):
                messagebox.showwarning("No outputs selected", "Select at least one output type.")
                return

            try:
                outputs = run_with_options(
                    input_file=Path(input_value),
                    output_dir=Path(self.output_var.get()) if self.output_var.get().strip() else None,
                    normal_strength=self.normal_strength_var.get(),
                    parallax_strength=self.parallax_strength_var.get(),
                    glow_threshold=self.glow_threshold_var.get(),
                    environment_mask_strength=self.environment_mask_strength_var.get(),
                    complex_strength=self.complex_strength_var.get(),
                    complex_format=self.complex_format_var.get(),
                    include_diffuse=include_diffuse,
                    include_normal=include_normal,
                    include_parallax=include_parallax,
                    include_glow=include_glow,
                    include_environment_mask=include_environment_mask,
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
