import tempfile
import unittest
from pathlib import Path

from PIL import Image

from generate_textures import (
    analyze_image_content,
    build_complex_output_path,
    build_environment_mask_output_path,
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
    recommend_generation_settings,
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


class GenerateTexturesTests(unittest.TestCase):
    def test_generate_diffuse_returns_rgb_same_size(self) -> None:
        diffuse = generate_diffuse(_sample_image())
        self.assertEqual(diffuse.mode, "RGB")
        self.assertEqual(diffuse.size, (8, 8))

    def test_generate_parallax_returns_l_same_size(self) -> None:
        parallax = generate_parallax(_sample_image())
        self.assertEqual(parallax.mode, "L")
        self.assertEqual(parallax.size, (8, 8))

    def test_generate_normal_returns_rgb_same_size(self) -> None:
        normal = generate_normal(_sample_image())
        self.assertEqual(normal.mode, "RGB")
        self.assertEqual(normal.size, (8, 8))

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
            "midtone_ratio",
        ):
            self.assertIn(key, metrics)

    def test_recommend_generation_settings_changes_by_image_content(self) -> None:
        flat_dark = recommend_generation_settings(_flat_dark_image())
        detailed_bright = recommend_generation_settings(_detailed_bright_image())
        self.assertNotEqual(int(flat_dark["glow_threshold"]), int(detailed_bright["glow_threshold"]))
        self.assertNotEqual(float(flat_dark["normal_strength"]), float(detailed_bright["normal_strength"]))
        self.assertNotEqual(float(flat_dark["specular_strength"]), float(detailed_bright["specular_strength"]))

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


if __name__ == "__main__":
    unittest.main()
