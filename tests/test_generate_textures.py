import tempfile
import unittest
from pathlib import Path

from PIL import Image

from generate_textures import (
    build_complex_output_path,
    build_output_paths,
    generate_complex_material,
    generate_diffuse,
    generate_parallax,
    run_with_options,
)


def _sample_image() -> Image.Image:
    return Image.new("RGB", (8, 8), color=(60, 100, 140))


class GenerateTexturesTests(unittest.TestCase):
    def test_generate_diffuse_returns_rgb_same_size(self) -> None:
        diffuse = generate_diffuse(_sample_image())
        self.assertEqual(diffuse.mode, "RGB")
        self.assertEqual(diffuse.size, (8, 8))

    def test_generate_parallax_returns_l_same_size(self) -> None:
        parallax = generate_parallax(_sample_image())
        self.assertEqual(parallax.mode, "L")
        self.assertEqual(parallax.size, (8, 8))

    def test_generate_complex_material_returns_l_same_size(self) -> None:
        complex_material = generate_complex_material(_sample_image())
        self.assertEqual(complex_material.mode, "L")
        self.assertEqual(complex_material.size, (8, 8))

    def test_build_output_paths_uses_default_names_and_extension(self) -> None:
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

            self.assertEqual(diffuse_path.name, "brick_diffuse.dds")
            self.assertEqual(parallax_path.name, "brick_parallax.dds")

    def test_build_complex_output_path_uses_default_name_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            complex_path = build_complex_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                complex_name=None,
            )

            self.assertEqual(complex_path.name, "brick_complex_material.dds")

    def test_run_with_options_requires_at_least_one_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            _sample_image().save(input_path)

            with self.assertRaises(ValueError):
                run_with_options(
                    input_file=input_path,
                    include_diffuse=False,
                    include_parallax=False,
                    include_complex=False,
                )


if __name__ == "__main__":
    unittest.main()
