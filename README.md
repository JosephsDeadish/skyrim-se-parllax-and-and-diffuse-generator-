# skyrim-se-parllax-and-and-diffuse-generator-

Command-line app that takes an input DDS texture and generates:
- a diffuse texture
- a grayscale parallax texture

## Requirements

- Python 3.11+
- Dependencies in `requirements.txt`

## Usage

```bash
python generate_textures.py /path/to/input.dds --output-dir ./output
```

Optional arguments:

- `--diffuse-name` (default: `<input_stem>_diffuse`)
- `--parallax-name` (default: `<input_stem>_parallax`)
- `--parallax-strength` (default: `1.35`)

The tool attempts to write DDS files. If DDS export is unavailable on the current Pillow build, it falls back to PNG output.

## GitHub Actions

The repository includes a CI workflow in `.github/workflows/build.yml` that:
- runs on pull requests
- can also be launched manually from the Actions tab (`workflow_dispatch`)

If you want pull request runs to require explicit approval before jobs execute, configure required reviewers on the `pr-build-approval` environment in repository settings.
