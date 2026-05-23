# skyrim-se-parllax-and-and-diffuse-generator-

Texture generator that supports both GUI and command-line usage. It can generate:
- a diffuse texture
- a normal map (DirectX-style tangent-space, correct for Skyrim SE)
- a grayscale parallax texture
- a glow map
- an environment mask — **standard greyscale** (vanilla Skyrim SE, no ENB needed) or **complex RGBA** (ENBSeries only)
- a complex material output:
  - `_msn`: normal RGB with specular in alpha
  - `_cm`: grayscale complex material texture

## Requirements

- Python 3.11+
- Dependencies in `requirements.txt`

## Installation via Mod Organizer 2 or Vortex (FoMod)

The release ZIP (`skyrim-texture-generator.zip`) includes a FoMod installer so the tool can be installed directly through Mod Organizer 2 (v2.4+) or Vortex like any other mod:

1. In **MO2**: drag-and-drop the ZIP onto the left pane (or use Install a new mod from an archive), follow the installer wizard, then go to **Tools → Executables → Add** and point it at `generate_textures.exe` in the mod's staging folder.
2. In **Vortex**: install the ZIP via the **Mods** tab as usual, then register `generate_textures.exe` under **Dashboard → Add Tool**.

Alternatively, simply extract `generate_textures.exe` anywhere and run it directly — no installation is required.

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
- preview the **Before** source image and the currently selected output types (diffuse/normal/parallax/glow/environment mask/complex)
- tune normal/parallax/glow/environment mask/complex/specular strengths
- choose the **Env mask mode**: `standard` (greyscale, vanilla Skyrim SE) or `complex` (RGBA, ENBSeries only)
- scroll through all controls in smaller windows
- auto-update output folder when a different input texture is selected
- get adaptive recommended defaults based on richer image-content analysis
- use a clearly labeled **Automatic suggestions (analyze image and set sliders)** toggle to turn auto slider updates on/off
- use **Auto** checkboxes beside each slider to choose exactly which sliders receive automatic suggestions
- batch-process folder inputs in the background so the UI stays responsive on larger files or larger sets
- load oversized preview sources with automatic downscaling to keep the GUI responsive when opening very large textures
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
- `--environment-mask-mode` (`standard` or `complex`, default: `standard`)
  - `standard` — greyscale `_m.dds` for vanilla Skyrim SE (Texture Slot 5, no ENB required)
  - `complex` — RGBA channel-packed texture for ENBSeries Complex Parallax Material only
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
- Neutral normal color remains centered around `RGB(128, 128,255)` so flat areas stay visually flat in-game.
- Parallax output (`*_p`) is generated as a grayscale height map (`L` mode), suitable for Skyrim SE parallax workflows.
- **Environment mask** (`*_m`) has two modes:
  - **Standard** (default) — greyscale `L`-mode texture. Texture Slot 5 in the NIF. Controls per-pixel environment/specular reflection intensity (brighter = more reflection). Requires `SLSF1_Environment_Mapping` shader flag. Works with vanilla Skyrim SE — **no ENBSeries required**. Typically stored as DXT1.
  - **Complex** (select in GUI or via `--environment-mask-mode complex`) — RGBA channel-packed for ENBSeries Complex Parallax Material workflows: R=env amount, G=glossiness, B=metallic proxy, A=parallax height. **ENBSeries required** with complex material support enabled.
- For large/high-detail sources (2K/4K/8K), generation applies adaptive detail dampening to reduce over-sharpened normals/parallax and complex-material sparkle artifacts. Analysis and auto-recommendation calculations are automatically performed on a downscaled copy so large textures are processed faster without sacrificing output quality.
- Specular generation uses numpy float32 arithmetic with percentile-based range normalisation so true-black hole artefacts cannot be introduced by integer rounding, regardless of texture size or content.
- `_msn` output stores normal RGB with specular in alpha; `_cm` remains grayscale complex material.

## File name recognition

The tool recognises standard Skyrim SE texture naming conventions from the file name suffix:

| Suffix | Role | Notes |
|--------|------|-------|
| *(none)* | Diffuse / Albedo | Texture Slot 0 |
| `_n` | Normal Map | DirectX tangent-space, Slot 1 |
| `_p` | Parallax Heightmap | Greyscale, Slot 3, requires SKSE64 memory patch |
| `_g` | Glow / Emissive | Slot 2, requires `SLSF1_Own_Emit` flag |
| `_m` | Environment Mask | Greyscale reflection intensity, Slot 5 |
| `_s` | Subsurface Scattering | Slot 6, skin/character textures |
| `_sk` | Skin Specular | Slot 7, character-specific |
| `_msn` | Complex Parallax Material | ENBSeries only — **not vanilla Skyrim SE** |
| `_cm` | Complex Material (greyscale) | ENBSeries only — **not vanilla Skyrim SE** |

Batch folder mode automatically skips generated variants (`_n`, `_p`, `_g`, `_m`, `_msn`, `_cm`) so it only processes original source textures.

## GitHub Actions

The repository includes a CI workflow in `.github/workflows/build.yml` that:
- runs on pull requests
- can also be launched manually from the Actions tab (`workflow_dispatch`)

If you want pull request runs to require explicit approval before jobs execute, configure required reviewers on the `pr-build-approval` environment in repository settings.
