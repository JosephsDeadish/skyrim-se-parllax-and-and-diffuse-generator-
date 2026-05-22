# skyrim-se-parllax-and-and-diffuse-generator-

Texture generator that supports both GUI and command-line usage. It can generate:
- a diffuse texture
- a grayscale parallax texture
- a grayscale complex material texture

## Requirements

- Python 3.11+
- Dependencies in `requirements.txt`

## Usage

### GUI (default)

```bash
python generate_textures.py
```

This opens a desktop interface where you can:
- select input DDS texture
- pick an output folder
- choose diffuse/parallax/complex material outputs
- preview before/after output
- tune parallax and complex material strength

### Command line

```bash
python generate_textures.py /path/to/input.dds --output-dir ./output --complex-material
```

Optional arguments:

- `--diffuse-name` (default: `<input_stem>_diffuse`)
- `--parallax-name` (default: `<input_stem>_parallax`)
- `--complex-name` (default: `<input_stem>_complex_material`)
- `--parallax-strength` (default: `1.35`)
- `--complex-strength` (default: `1.15`)
- `--no-diffuse` (skip diffuse output)
- `--no-parallax` (skip parallax output)
- `--complex-material` (include complex material output)
- `--gui` (force GUI mode)

The tool attempts to write DDS files. If DDS export is unavailable on the current Pillow build, it falls back to PNG output.

## GitHub Actions

The repository includes a CI workflow in `.github/workflows/build.yml` that:
- runs on pull requests
- can also be launched manually from the Actions tab (`workflow_dispatch`)

If you want pull request runs to require explicit approval before jobs execute, configure required reviewers on the `pr-build-approval` environment in repository settings.
