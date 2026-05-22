import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from generate_textures import (
    PATREON_URL,
    _create_panda_icon_image,
    _run_cli,
    analyze_image_content,
    apply_recommendations_by_auto_flags,
    build_complex_output_path,
    build_environment_mask_output_path,
    collect_source_textures,
    build_glow_output_path,
    build_normal_output_path,
    build_output_paths,
    generate_complex_material,
    generate_diffuse,
    generate_environment_mask,
    generate_glow,
    generate_msn,
    generate_normal,
    generate_parallax,
    generate_specular,
    recommend_generation_settings,
    run_batch_with_options,
    run_with_options,
)


def _sample_image() -> Image.Image:
    return Image.new("RGB", (8, 8), color=(60, 100, 140))


def _flat_dark_image() -> Image.Image:
    return Image.new("RGB", (16, 16), color=(28, 28, 28))


def _detailed_bright_image() -> Image.Image:
    image = Image.new("RGB", (16, 16))
    pixels = image.load()
    for y in range(16):
        for x in range(16):
            v = 245 if (x + y) % 2 == 0 else 35
            pixels[x, y] = (v, v, v)
    return image


def _uniform_bright_image() -> Image.Image:
    return Image.new("RGB", (24, 24), color=(220, 220, 220))


def _vertical_gradient_image() -> Image.Image:
    image = Image.new("RGB", (24, 24))
    pixels = image.load()
    for y in range(24):
        value = int((y / 23) * 255)
        for x in range(24):
            pixels[x, y] = (value, value, value)
    return image


def _sparse_highlight_image() -> Image.Image:
    image = Image.new("RGB", (24, 24), color=(20, 20, 20))
    pixels = image.load()
    for y in range(0, 24, 6):
        for x in range(0, 24, 6):
            pixels[x, y] = (255, 255, 255)
    return image


def _high_saturation_image() -> Image.Image:
    return Image.new("RGB", (24, 24), color=(245, 40, 40))


class GenerateTexturesTests(unittest.TestCase):
    def test_generate_diffuse_returns_rgb_same_size(self) -> None:
        diffuse = generate_diffuse(_sample_image())
        self.assertEqual(diffuse.mode, "RGB")
        self.assertEqual(diffuse.size, (8, 8))

    def test_generate_parallax_returns_l_same_size(self) -> None:
        parallax = generate_parallax(_sample_image())
        self.assertEqual(parallax.mode, "L")
        self.assertEqual(parallax.size, (8, 8))

    def test_generate_parallax_produces_non_flat_height_map(self) -> None:
        parallax = generate_parallax(_vertical_gradient_image(), strength=1.35)
        values = set(parallax.tobytes())
        self.assertGreater(len(values), 2)

    def test_generate_normal_returns_rgb_same_size(self) -> None:
        normal = generate_normal(_sample_image())
        self.assertEqual(normal.mode, "RGB")
        self.assertEqual(normal.size, (8, 8))

    def test_generate_normal_directx_green_channel_differs_from_opengl(self) -> None:
        gradient = _vertical_gradient_image()
        directx_normal = generate_normal(gradient, strength=1.0, directx=True)
        opengl_normal = generate_normal(gradient, strength=1.0, directx=False)
        _, directx_green, _ = directx_normal.split()
        _, opengl_green, _ = opengl_normal.split()
        sample_coord = (12, 12)
        self.assertNotEqual(directx_green.getpixel(sample_coord), opengl_green.getpixel(sample_coord))

    def test_generate_normal_defaults_to_directx_orientation(self) -> None:
        gradient = _vertical_gradient_image()
        default_normal = generate_normal(gradient, strength=1.0)
        directx_normal = generate_normal(gradient, strength=1.0, directx=True)
        self.assertEqual(default_normal.tobytes(), directx_normal.tobytes())

    def test_generate_normal_blue_channel_stays_in_skyrim_safe_range(self) -> None:
        gradient = _vertical_gradient_image()
        normal = generate_normal(gradient, strength=2.0)
        _, _, blue = normal.split()
        minimum_blue, _ = blue.getextrema()
        self.assertGreaterEqual(minimum_blue, 128)

    def test_generate_glow_returns_l_same_size(self) -> None:
        glow = generate_glow(_sample_image())
        self.assertEqual(glow.mode, "L")
        self.assertEqual(glow.size, (8, 8))

    def test_generate_environment_mask_returns_l_same_size(self) -> None:
        environment_mask = generate_environment_mask(_sample_image())
        self.assertEqual(environment_mask.mode, "L")
        self.assertEqual(environment_mask.size, (8, 8))

    def test_generate_complex_material_returns_l_same_size(self) -> None:
        complex_material = generate_complex_material(_sample_image())
        self.assertEqual(complex_material.mode, "L")
        self.assertEqual(complex_material.size, (8, 8))

    def test_generate_msn_returns_rgba_same_size(self) -> None:
        msn = generate_msn(_sample_image())
        self.assertEqual(msn.mode, "RGBA")
        self.assertEqual(msn.size, (8, 8))

    def test_generate_msn_rgb_matches_normal_and_alpha_matches_specular(self) -> None:
        source = _vertical_gradient_image()
        normal_strength = 1.8
        specular_strength = 1.2
        msn = generate_msn(source, normal_strength=normal_strength, specular_strength=specular_strength)
        expected_normal = generate_normal(source, strength=normal_strength)
        expected_specular = generate_specular(source, strength=specular_strength)
        self.assertEqual(msn.split()[:3], expected_normal.split())
        self.assertEqual(msn.split()[3].tobytes(), expected_specular.tobytes())

    def test_recommend_generation_settings_returns_valid_ranges(self) -> None:
        settings = recommend_generation_settings(_sample_image())
        self.assertIn("specular_strength", settings)
        self.assertIn("complex_strength", settings)
        self.assertIn("environment_mask_strength", settings)
        self.assertGreaterEqual(float(settings["normal_strength"]), 1.1)
        self.assertLessEqual(float(settings["normal_strength"]), 3.8)
        self.assertGreaterEqual(float(settings["parallax_strength"]), 0.8)
        self.assertLessEqual(float(settings["parallax_strength"]), 2.4)
        self.assertGreaterEqual(int(settings["glow_threshold"]), 140)
        self.assertLessEqual(int(settings["glow_threshold"]), 235)

    def test_analyze_image_content_exposes_expected_metrics(self) -> None:
        metrics = analyze_image_content(_sample_image())
        for key in (
            "brightness",
            "contrast",
            "edge_strength",
            "edge_variance",
            "detail_energy",
            "dynamic_range",
            "shadow_ratio",
            "highlight_ratio",
            "bright_cluster_ratio",
            "midtone_ratio",
            "p90_luma",
            "saturation_mean",
            "saturation_variance",
            "low_saturation_ratio",
            "color_variance",
        ):
            self.assertIn(key, metrics)

    def test_recommend_generation_settings_changes_by_image_content(self) -> None:
        flat_dark = recommend_generation_settings(_flat_dark_image())
        detailed_bright = recommend_generation_settings(_detailed_bright_image())
        self.assertNotEqual(int(flat_dark["glow_threshold"]), int(detailed_bright["glow_threshold"]))
        self.assertNotEqual(float(flat_dark["normal_strength"]), float(detailed_bright["normal_strength"]))
        self.assertNotEqual(float(flat_dark["specular_strength"]), float(detailed_bright["specular_strength"]))

    def test_recommend_generation_settings_glow_handles_sparse_highlights(self) -> None:
        uniform = recommend_generation_settings(_uniform_bright_image())
        sparse = recommend_generation_settings(_sparse_highlight_image())
        self.assertGreater(int(uniform["glow_threshold"]), int(sparse["glow_threshold"]))

    def test_recommend_generation_settings_uses_saturation_for_glow_threshold(self) -> None:
        low_saturation = recommend_generation_settings(_uniform_bright_image())
        high_saturation = recommend_generation_settings(_high_saturation_image())
        self.assertLess(int(high_saturation["glow_threshold"]), int(low_saturation["glow_threshold"]))

    def test_generate_glow_produces_graded_values_above_threshold(self) -> None:
        glow = generate_glow(_vertical_gradient_image(), threshold=180)
        values = set(glow.tobytes())
        self.assertGreater(len(values), 2)

    def test_apply_recommendations_by_auto_flags_applies_only_checked_values(self) -> None:
        current = {
            "normal_strength": 1.5,
            "parallax_strength": 1.3,
            "glow_threshold": 180,
            "environment_mask_strength": 1.2,
        }
        recommended = {
            "normal_strength": 2.4,
            "parallax_strength": 2.0,
            "glow_threshold": 210,
            "environment_mask_strength": 1.8,
        }
        auto_flags = {
            "normal_strength": True,
            "parallax_strength": False,
            "glow_threshold": True,
            "environment_mask_strength": False,
        }
        merged = apply_recommendations_by_auto_flags(current=current, recommended=recommended, auto_flags=auto_flags)
        self.assertEqual(float(merged["normal_strength"]), 2.4)
        self.assertEqual(float(merged["parallax_strength"]), 1.3)
        self.assertEqual(int(merged["glow_threshold"]), 210)
        self.assertEqual(float(merged["environment_mask_strength"]), 1.2)

    def test_build_output_paths_uses_skyrim_default_names_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            diffuse_path, parallax_path = build_output_paths(
                input_path=input_path,
                output_dir=temp_path / "out",
                diffuse_name=None,
                parallax_name=None,
            )

            self.assertEqual(diffuse_path.name, "brick.dds")
            self.assertEqual(parallax_path.name, "brick_p.dds")

    def test_build_output_paths_use_dds_extension_even_for_png_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            input_path.write_bytes(b"stub")

            diffuse_path, parallax_path = build_output_paths(
                input_path=input_path,
                output_dir=temp_path / "out",
                diffuse_name=None,
                parallax_name=None,
            )

            self.assertEqual(diffuse_path.name, "brick.dds")
            self.assertEqual(parallax_path.name, "brick_p.dds")

    def test_build_normal_output_path_uses_default_name_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            normal_path = build_normal_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                normal_name=None,
            )

            self.assertEqual(normal_path.name, "brick_n.dds")

    def test_build_glow_output_path_uses_default_name_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            glow_path = build_glow_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                glow_name=None,
            )

            self.assertEqual(glow_path.name, "brick_g.dds")

    def test_build_environment_mask_output_path_uses_default_name_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            environment_mask_path = build_environment_mask_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                environment_mask_name=None,
            )

            self.assertEqual(environment_mask_path.name, "brick_m.dds")

    def test_build_complex_output_path_uses_msn_default_name_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            complex_path = build_complex_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                complex_name=None,
                complex_format="msn",
            )

            self.assertEqual(complex_path.name, "brick_msn.dds")

    def test_build_complex_output_path_supports_cm_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            complex_path = build_complex_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                complex_name=None,
                complex_format="cm",
            )

            self.assertEqual(complex_path.name, "brick_cm.dds")

    def test_collect_source_textures_from_directory_skips_generated_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for name in ("brick.dds", "stone_wall.dds", "brick_n.dds", "brick_p.dds", "brick_msn.dds", "preview.png"):
                (temp_path / name).write_bytes(b"stub")

            collected = collect_source_textures(temp_path)

            self.assertEqual([path.name for path in collected], ["brick.dds", "stone_wall.dds"])

    def test_run_with_options_requires_at_least_one_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            _sample_image().save(input_path)

            with self.assertRaises(ValueError):
                run_with_options(
                    input_file=input_path,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                )

    def test_run_with_options_writes_openable_dds_files_for_png_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            outputs = run_with_options(
                input_file=input_path,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=True,
                include_parallax=True,
                include_glow=True,
                include_environment_mask=True,
                include_complex=True,
            )

            expected_names = {
                "diffuse": "brick.dds",
                "normal": "brick_n.dds",
                "parallax": "brick_p.dds",
                "glow": "brick_g.dds",
                "environment_mask": "brick_m.dds",
                "complex_material": "brick_msn.dds",
            }
            self.assertEqual({key: value.name for key, value in outputs.items()}, expected_names)

            for path in outputs.values():
                self.assertEqual(path.suffix.lower(), ".dds")
                with Image.open(path) as generated:
                    generated.load()
                    self.assertEqual(generated.size, (8, 8))

    def test_run_with_options_writes_msn_with_alpha_when_complex_format_is_msn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            outputs = run_with_options(
                input_file=input_path,
                output_dir=output_dir,
                include_diffuse=False,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=True,
                complex_format="msn",
            )

            with Image.open(outputs["complex_material"]) as generated:
                generated.load()
                self.assertIn(generated.mode, ("RGBA", "RGB"))

    def test_run_batch_with_options_processes_only_original_dds_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            input_dir.mkdir()

            for name in ("brick.dds", "stone_wall.dds", "brick_n.dds", "brick_p.dds", "stone_wall_msn.dds"):
                _sample_image().save(input_dir / name, format="DDS", pixel_format="DXT5")

            outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
            )

            self.assertEqual(sorted(path.name for path in outputs.keys()), ["brick.dds", "stone_wall.dds"])
            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), ["brick.dds", "stone_wall.dds"])

    def test_run_batch_with_options_can_continue_on_file_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            input_dir.mkdir()

            _sample_image().save(input_dir / "good.dds", format="DDS", pixel_format="DXT5")
            (input_dir / "bad.dds").write_bytes(b"this is not a valid dds")
            errors: list[tuple[str, str]] = []

            outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
                continue_on_error=True,
                error_callback=lambda _index, _total, current, exc: errors.append((current.name, str(exc))),
            )

            self.assertEqual(sorted(path.name for path in outputs.keys()), ["good.dds"])
            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), ["good.dds"])
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0][0], "bad.dds")

    def test_create_panda_icon_image_returns_rgba_square(self) -> None:
        for size in (16, 32, 64, 128, 256):
            icon = _create_panda_icon_image(size=size)
            self.assertEqual(icon.mode, "RGBA")
            self.assertEqual(icon.size, (size, size))

    def test_create_panda_icon_image_is_not_fully_transparent(self) -> None:
        icon = _create_panda_icon_image(size=128)
        opaque_count = 0
        for x in range(icon.width):
            for y in range(icon.height):
                if icon.getpixel((x, y))[3] == 255:
                    opaque_count += 1
        self.assertGreater(opaque_count, 0)

    def test_patreon_url_is_correct(self) -> None:
        self.assertEqual(PATREON_URL, "https://www.patreon.com/cw/DeadOnTheInside")

    def test_run_cli_handles_missing_gui_dependencies_without_traceback(self) -> None:
        with mock.patch("generate_textures.main", side_effect=RuntimeError("GUI dependencies are unavailable in this environment.")):
            with mock.patch("sys.stderr") as mock_stderr:
                exit_code = _run_cli()
        self.assertEqual(exit_code, 1)
        self.assertIn("GUI dependencies are unavailable", "".join(call.args[0] for call in mock_stderr.write.call_args_list))

    def test_run_cli_handles_generic_runtime_error(self) -> None:
        with mock.patch("generate_textures.main", side_effect=RuntimeError("boom")):
            with mock.patch("sys.stderr") as mock_stderr:
                exit_code = _run_cli()
        self.assertEqual(exit_code, 1)
        self.assertIn("Error: boom", "".join(call.args[0] for call in mock_stderr.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
