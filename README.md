# skyrim-se-parllax-and-and-diffuse-generator-

Texture generator that supports both GUI and command-line usage. It can generate:
- a diffuse texture
- a normal map
- a grayscale parallax texture
- a glow map
- an environment mask (complex-parallax packed RGBA)
- a complex material output:
  - `_msn`: normal RGB with specular in alpha
  - `_cm`: grayscale complex material texture

## Requirements

- Python 3.11+
- Dependencies in `requirements.txt`

## Usage

### GUI (default)

```bash
python generate_textures.py
```

This opens a desktop interface where you can:
- select one input texture or an entire folder of source DDS textures
- pick an output folder
- use a **Use different output folder** toggle to switch between writing beside the input or writing to a custom location
- choose diffuse/normal/parallax/glow/environment mask/complex material outputs
- preview the **Before** source image and all output types at once (diffuse/normal/parallax/glow/environment mask/complex)
- tune normal/parallax/glow/environment mask/complex/specular strengths
- scroll through all controls in smaller windows
- auto-update output folder when a different input texture is selected
- get adaptive recommended defaults based on richer image-content analysis
- use a clearly labeled **Automatic suggestions (analyze image and set sliders)** toggle to turn auto slider updates on/off
- use **Auto** checkboxes beside each slider to choose exactly which sliders receive automatic suggestions
- batch-process folder inputs in the background so the UI stays responsive on larger files or larger sets
- switch preview source images in folder mode, with automatic preview switching to the current file while batch processing
- when a folder is selected, only process original `.dds` source textures and skip generated `_n`, `_p`, `_g`, `_m`, `_msn`, and `_cm` variants
- continue processing remaining files in folder mode even if one file is corrupt/unreadable

### Command line

```bash
python generate_textures.py /path/to/input.dds --output-dir ./output --complex-material
```

Optional arguments:

- positional input may also be a folder; folder mode processes only original `.dds` source textures and skips generated `_n`, `_p`, `_g`, `_m`, `_msn`, and `_cm` variants

- `--diffuse-name` (default: `<input_stem>`, e.g. `stonewall.dds`)
- `--normal-name` (default: `<input_stem>_n`, e.g. `stonewall_n.dds`)
- `--parallax-name` (default: `<input_stem>_p`, e.g. `stonewall_p.dds`)
- `--glow-name` (default: `<input_stem>_g`, e.g. `stonewall_g.dds`)
- `--environment-mask-name` (default: `<input_stem>_m`, e.g. `stonewall_m.dds`)
- `--complex-name` (default from format: `<input_stem>_msn` or `<input_stem>_cm`)
- `--complex-format` (`msn` or `cm`, default: `msn`)
- `--normal-strength` (default: adaptive from image)
- `--parallax-strength` (default: adaptive from image)
- `--glow-threshold` (default: adaptive from image)
- `--environment-mask-strength` (default: adaptive from image)
- `--complex-strength` (default: adaptive from image)
- `--specular-strength` (default: adaptive from image, used for `_msn` alpha)
- `--no-diffuse` (skip diffuse output)
- `--no-normal` (skip normal output)
- `--no-parallax` (skip parallax output)
- `--glow-map` (include glow output)
- `--environment-mask` (include environment mask output)
- `--complex-material` (include complex material output)
- `--gui` (force GUI mode)

Generated outputs default to `.dds` filenames regardless of the input format. The tool writes DDS files using DXT5 compression for broader compatibility with external viewers/converters; if DDS export is unavailable on the current Pillow build, it falls back to PNG output.

- click **❤ Support on Patreon** in the toolbar to open the creator's Patreon page

### Skyrim SE output conventions (fact-checked)

- Normal map output (`*_n`) uses **DirectX-style tangent-space orientation** by default (green channel flipped vs OpenGL workflows), which is what Skyrim expects.
- Neutral normal color remains centered around `RGB(128, 128, 255)` so flat areas stay visually flat in-game.
- Parallax output (`*_p`) is generated as a grayscale height map (`L` mode), suitable for Skyrim SE parallax workflows.
- Environment mask output (`*_m`) is channel-packed for complex parallax style workflows:
  - **R**: environment map amount
  - **G**: glossiness (kept above compression-black thresholds)
  - **B**: metallic proxy
  - **A**: parallax height
- `_msn` output stores normal RGB with specular in alpha; `_cm` remains grayscale complex material.

## GitHub Actions

The repository includes a CI workflow in `.github/workflows/build.yml` that:
- runs on pull requests
- can also be launched manually from the Actions tab (`workflow_dispatch`)

If you want pull request runs to require explicit approval before jobs execute, configure required reviewers on the `pr-build-approval` environment in repository settings.
