import tempfile
import unittest
from pathlib import Path

from PIL import Image

from generate_textures import build_output_paths, generate_diffuse, generate_parallax


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


if __name__ == "__main__":
    unittest.main()
