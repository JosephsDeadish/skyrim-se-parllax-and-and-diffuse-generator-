# skyrim-se-parllax-and-and-diffuse-generator-

Texture generator that supports both GUI and command-line usage. It can generate:
- a diffuse texture
- a normal map
- a grayscale parallax texture
- a glow map
- an environment mask
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
- choose diffuse/normal/parallax/glow/environment mask/complex material outputs
- preview before/after output
- tune normal/parallax/glow/environment mask/complex strengths

### Command line

```bash
python generate_textures.py /path/to/input.dds --output-dir ./output --complex-material
```

Optional arguments:

- `--diffuse-name` (default: `<input_stem>`, e.g. `stonewall.dds`)
- `--normal-name` (default: `<input_stem>_n`, e.g. `stonewall_n.dds`)
- `--parallax-name` (default: `<input_stem>_p`, e.g. `stonewall_p.dds`)
- `--glow-name` (default: `<input_stem>_g`, e.g. `stonewall_g.dds`)
- `--environment-mask-name` (default: `<input_stem>_m`, e.g. `stonewall_m.dds`)
- `--complex-name` (default from format: `<input_stem>_msn` or `<input_stem>_cm`)
- `--complex-format` (`msn` or `cm`, default: `msn`)
- `--normal-strength` (default: `2.0`)
- `--parallax-strength` (default: `1.35`)
- `--glow-threshold` (default: `190`)
- `--environment-mask-strength` (default: `1.2`)
- `--complex-strength` (default: `1.15`)
- `--no-diffuse` (skip diffuse output)
- `--no-normal` (skip normal output)
- `--no-parallax` (skip parallax output)
- `--glow-map` (include glow output)
- `--environment-mask` (include environment mask output)
- `--complex-material` (include complex material output)
- `--gui` (force GUI mode)

The tool attempts to write DDS files. If DDS export is unavailable on the current Pillow build, it falls back to PNG output.

## GitHub Actions

The repository includes a CI workflow in `.github/workflows/build.yml` that:
- runs on pull requests
- can also be launched manually from the Actions tab (`workflow_dispatch`)

If you want pull request runs to require explicit approval before jobs execute, configure required reviewers on the `pr-build-approval` environment in repository settings.
