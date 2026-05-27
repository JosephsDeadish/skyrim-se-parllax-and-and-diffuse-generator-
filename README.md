# skyrim-se-parllax-and-and-diffuse-generator-

Current version: **0.5.5**

Texture generator that supports both GUI and command-line usage. It can generate:
- a diffuse texture
- a normal map (DirectX-style tangent-space, correct for Skyrim SE), with optional **emboss depth mode** for flat printed surfaces (cards/books/scrolls/posters)
- a grayscale parallax texture
- an ENBSeries POM-optimized parallax heightmap mode (**Parallax Occlusion**)
- a glow map
- an environment mask — **standard greyscale** (vanilla Skyrim SE, no ENB needed) or **complex RGBA** (`_m` for ENB-style packed env masks)
- a dedicated TruePBR packed map output — `_rmaos` (or `_ramos` alias) with generated JSON sidecar guidance
- a complex material output:
  - `_msn`: normal RGB with specular in alpha (ENBSeries complex material, Slot 1)
  - `_cm` / `_c`: packed Community Shaders **Extended Materials** map — RGBA: R=Environment reflection amount, G=Glossiness, B=Metallic, A=Height / mode-control alpha
  - `_cm` / `_c` / `_C` is the tool's Community Shaders Extended Materials output path; `_c` and `_C.dds` are treated as aliases

## Requirements

- Python 3.11+
- Dependencies in `requirements.txt`

## Installation via Mod Organizer 2 or Vortex (FoMod)

The release ZIP (`skyrim-texture-generator.zip`) includes a FoMod installer so the tool can be installed directly through Mod Organizer 2 (v2.4+) or Vortex like any other mod:

1. In **MO2**: drag-and-drop the ZIP onto the left pane (or use Install a new mod from an archive), follow the installer wizard, then go to **Tools → Executables → Add** and point it at `generate_textures.exe` in the mod's staging folder.
2. In **Vortex**: install the ZIP via the **Mods** tab as usual, then register `generate_textures.exe` under **Dashboard → Add Tool**.

Alternatively, simply extract `generate_textures.exe` anywhere and run it directly — no installation is required.

When the tool is launched from **MO2** or **Vortex**, it now tries to detect the current mod-manager context:
- **MO2**: reads the active profile and enabled mods from `modlist.txt`, then exposes detected loaded mod texture folders in the GUI.
- **Vortex**: looks for the active profile/staging layout and exposes detected managed texture folders in the GUI.
- The GUI shows the detected context under the file picker and adds a **Loaded Mod** button for faster browsing.

## Usage

### GUI (default)

```bash
python generate_textures.py
```

This opens a desktop interface where you can:
- select one input texture or an entire folder of source DDS textures
- pick an output folder
- see detected MO2/Vortex context and loaded-mod texture folders when launched through a mod manager
- use a **Use different output folder** toggle to switch between writing beside the input or writing to a custom location
- choose diffuse/normal/parallax/glow/environment mask/RMAOS/complex material outputs
- preview the **Before** source image and the currently selected output types (diffuse/normal/parallax/glow/environment mask/RMAOS/complex)
- tune normal/parallax/glow/environment mask/RMAOS/complex/specular strengths
- choose the **Env mask mode**: `standard` (greyscale, vanilla Skyrim SE) or `complex` (RGBA packed, renderer-specific)
- choose the **Target renderer** profile (**default: `experimental`**) — experimental is a blank/manual profile
- toggle **Emboss depth** for edge-ridge normal generation on flat printed assets (books/cards/scrolls/posters)
- choose **Parallax mode**: `standard` (vanilla / Community Shaders Extended Materials) or `occlusion (ENB/POM)` for smoother ENBSeries POM heightmaps
- scroll through all controls in smaller windows
- auto-update output folder when a different input texture is selected
- get adaptive recommended defaults based on richer image-content analysis
- use a clearly labeled **Automatic suggestions (analyze image and set sliders)** toggle to turn auto slider updates on/off
- use **Auto** checkboxes beside each slider to choose exactly which sliders receive automatic suggestions
- quickly see which sliders are auto-controlled: auto sliders are disabled, highlighted in blue, and marked with `◉ AUTO`
- apply extra-safe recommendation clamping for UI/interface/card-style paths (for example collectibles/book art) to avoid over-shiny/parallax-heavy outputs
- batch-process folder inputs in the background so the UI stays responsive on larger files or larger sets
- load oversized preview sources with automatic downscaling to keep the GUI responsive when opening very large textures
- switch preview source images in folder mode, with automatic preview switching to the current file while batch processing
- use the NIF Editor's resizable results log to review full untruncated scan/patch details while processing whole mesh folders — **NIF editing is an experimental feature; always keep backups of your NIF files**
- when a folder is selected, only process original `.dds` source textures and skip generated `_n`, `_p`, `_g`, `_m`, `_msn`, and `_cm` variants
- continue processing remaining files in folder mode even if one file is corrupt/unreadable
- toggle **dark mode** for low-light modding sessions
- get file-type sanity warnings (with per-warning “don’t show again”) for combinations that usually produce incorrect in-game results

### Command line

```bash
python generate_textures.py /path/to/input.dds --output-dir ./output --complex-material
```

Optional arguments:

- positional input may also be a folder; folder mode scans subfolders, processes only original `.dds` source textures, and skips generated `_n`, `_p`, `_g`, `_m`, `_msn`, and `_cm` variants

- `--diffuse-name` (default: `<input_stem>`, e.g. `stonewall.dds`)
- `--normal-name` (default: `<input_stem>_n`, e.g. `stonewall_n.dds`)
- `--parallax-name` (default: `<input_stem>_p`, e.g. `stonewall_p.dds`)
- `--glow-name` (default: `<input_stem>_g`, e.g. `stonewall_g.dds`)
- `--environment-mask-name` (default: `<input_stem>_m`)
- `--rmaos-name` / `--ramos-name` (default: `<input_stem>_rmaos`)
- `--complex-name` (default from format: `<input_stem>_msn` or `<input_stem>_cm`)
  - use `<input_stem>_c` here when a shader pack expects `_c.dds` naming
- `--complex-format` (`msn` or `cm`, default: `msn`)
- `--environment-mask-mode` (`standard` or `complex`, default: `standard`)
  - `standard` — greyscale `_m.dds` for vanilla Skyrim SE (Texture Slot 5, no ENB required)
  - `complex` — RGBA channel-packed `_m` texture for renderer-specific workflows (ENB-style packed env-mask output)
- `--emboss-mode` (normal-map emboss depth mode for flat printed assets)
- `--parallax-mode` (`standard` or `occlusion`, default: `standard`)
  - `standard` — vanilla-style parallax heightmap with micro-detail
  - `occlusion` — smooth ENBSeries POM-optimized heightmap
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
- `--rmaos` / `--ramos` (include dedicated TruePBR `_rmaos`/`_ramos` output + JSON sidecar)
- `--complex-material` (include complex material output)
- `--pbr-material` (shortcut for the app's Community Shaders Extended Materials packed output: enables complex material, forces `--complex-format cm`, and keeps compatible standard env/parallax modes; not an ENB workflow)
- `--batch-workers` (parallel workers for folder mode; `0` = automatic)
- `--gui` (force GUI mode)

### Community Shaders quick start (`_cm` / `_c` / `_C`)

This app supports the **Community Shaders Extended Materials** packed workflow via `_cm` output.

- In GUI:
  1. Set **Target renderer** to `community_shaders`
  2. Enable **Complex/PBR material**
  3. Set **Complex/PBR format** to `cm`
  4. Generate textures (you'll get `_cm` packed output by default, or `_c` if you set a custom complex name)
- In CLI:
  - `python generate_textures.py /path/to/input.dds --pbr-material`
  - or explicitly: `python generate_textures.py /path/to/input.dds --complex-material --complex-format cm`

``_cm` packs channels for Community Shaders Extended Materials: R=Environment reflection amount, G=Glossiness, B=Metallic, A=Height / mode-control alpha.  
Some packs use `_c.dds` (or `_C.dds` on Windows) for the same role — set `--complex-name <stem>_c` or `--complex-name <stem>_C` (or GUI custom naming) for that variant.

> **Important:** Community Shaders and ENB are separate renderer paths. Do **not** mix `_cm/_c/_C` with ENB `_msn/_m` in the same setup.  
> Community Shaders **TruePBR** may also use `_rmaos`, but that is a separate JSON-driven workflow and is **not** the same as this `_cm/_c/_C` preset.

### Community Shaders TruePBR quick start (`_rmaos` / `_ramos` + JSON)

This app now has a dedicated **TruePBR** renderer profile path for Community Shaders TruePBR workflows.

- In GUI:
  1. Set **Target renderer** to `truepbr`
  2. Keep **Normal / _n** enabled
  3. Enable **RMAOS / _rmaos**
  4. Generate textures; the tool writes `_rmaos` (or `_ramos`) plus a JSON sidecar to guide channel mapping
  5. Validate the generated JSON/channels against your installed TruePBR schema

TruePBR is JSON-driven and can vary by setup; treat `_rmaos`/`_ramos` channel semantics as config-dependent and verify against the specific TruePBR schema you use.

### ENB complex material quick start

Requires ENBSeries with `ComplexParallaxMaterial=true` in `enbseries.ini`.

- In GUI:
  1. Set **Target renderer** to `enb`
  2. Enable **Complex/PBR material**, set format to `msn`
  3. Enable **Environment mask**, set mode to `complex`
  4. Enable **Parallax** (occlusion mode recommended)
  5. Generate textures — you'll get `_msn.dds` (Slot 1 normal+spec) and `_m.dds` (Slot 5 RGBA env mask in ENB preset) by default
- In CLI:
  - `python generate_textures.py /path/to/input.dds --complex-material --complex-format msn --environment-mask --environment-mask-mode complex --parallax-mode occlusion`

`_msn` channel layout (Slot 1): R=Normal X, G=Normal Y, B=Normal Z, A=Specular intensity.  
`_m` channel layout in this app's ENB preset (Slot 5): R=Reflection/specular brightness, G=Glossiness, B=Metalness (cubemap tint), A=Parallax height.

Generated outputs default to `.dds` filenames regardless of the input format. Most outputs are written as DXT5 DDS for broad compatibility; standard (`--environment-mask-mode standard`) `_m` masks prefer DXT1 (with automatic DXT5 fallback if needed). `_cm` Community Shaders Extended Materials maps now prefer BC7 when available, then fall back to DXT5/DXT3 for compatibility. If DDS export is unavailable on the current Pillow build, the tool falls back to PNG output.

- click **❤ Support on Patreon** in the toolbar to open the creator's Patreon page

### Skyrim SE output conventions (fact-checked)

- Normal map output (`*_n`) uses **DirectX-style tangent-space orientation** by default (green channel flipped vs OpenGL workflows), which is what Skyrim expects.
- Neutral normal color remains centered around `RGB(128, 128,255)` so flat areas stay visually flat in-game.
- Parallax output (`*_p`) is generated as a grayscale height map (`L` mode), suitable for Skyrim SE parallax workflows.
- `--parallax-mode occlusion` generates a smoother `*_p` heightmap optimized for ENBSeries Parallax Occlusion Mapping (POM) while keeping the same filename/slot usage.
- `--emboss-mode` generates normal maps from edge-ridge detail so flat printed surfaces (cards/books/scrolls) gain embossed/debossed depth cues.
- Output generation now enforces per-map Skyrim-safe channel profiles during preview and file export (for example: normal maps always RGB with blue channel floor, standard env masks always greyscale, complex outputs always RGBA).
- **Environment mask** (`*_m` / `*_rmaos`) has two modes:
  - **Standard** (default) — greyscale `L`-mode texture. Texture Slot 5 in the NIF. Controls per-pixel environment/specular reflection intensity (brighter = more reflection). Requires `SLSF1_Environment_Mapping` shader flag. Works with vanilla Skyrim SE — **no ENBSeries required**. Typically stored as DXT1.
  - **Complex** (select in GUI or via `--environment-mask-mode complex`) — RGBA channel-packed for renderer-specific workflows. In this app's ENB preset: R=Reflection/specular brightness, G=Glossiness, B=Metalness (cubemap tint), A=Parallax height. In TruePBR workflows: R=Roughness, G=Metallic, B=Ambient Occlusion, A=Other/smoothness/height (JSON-driven). **ENBSeries required** with `ComplexParallaxMaterial=true` in `enbseries.ini` for ENB usage. The generator defaults naming to `*_rmaos.dds` for TruePBR and `*_m.dds` for ENB preset output (custom naming still works).
- For large/high-detail sources (2K/4K/8K), generation applies adaptive detail dampening to reduce over-sharpened normals/parallax and complex-material sparkle artifacts. Analysis and auto-recommendation calculations are automatically performed on a downscaled copy so large textures are processed faster without sacrificing output quality.
- Generation warnings now include extra guardrails for UI/interface texture paths and paper/card-like assets when map combinations are likely to look incorrect in-game.
- Specular generation uses numpy float32 arithmetic with percentile-based range normalisation so true-black hole artefacts cannot be introduced by integer rounding, regardless of texture size or content.
- `_msn` output stores normal RGB with specular in alpha (ENBSeries Slot 1); `_cm`/`_c`/`_C` stores packed Community Shaders Extended Materials channels (R=environment reflection amount, G=glossiness, B=metallic, A=height/mode-control alpha); `_rmaos` is reserved for TruePBR (R=roughness, G=metallic, B=AO, A=other/height), while this app's ENB preset writes complex ENB channel packing to `_m`.

## File name recognition

The tool recognises standard Skyrim SE texture naming conventions from the file name suffix:

| Suffix | Role | Notes |
|--------|------|-------|
| *(none)* | Diffuse / Albedo | Texture Slot 0 |
| `_n` | Normal Map | DirectX tangent-space, Slot 1 |
| `_p` | Parallax Heightmap | Greyscale, Slot 3, requires SKSE64 memory patch |
| `_g` | Glow / Emissive | Slot 2, requires `SLSF1_Own_Emit` flag |
| `_m` | Environment Mask | Greyscale reflection intensity, Slot 5 — vanilla Skyrim SE only |
| `_rmaos` | Complex Env Mask (TruePBR naming) | RGBA Slot 5 packed data map for Community Shaders TruePBR. Channels: R=Roughness, G=Metallic, B=AO, A=Other/smoothness/height (JSON-driven). |
| `_s` | Subsurface Scattering | Slot 6, skin/character textures |
| `_sk` | Skin Specular | Slot 7, character-specific |
| `_msn` | Complex Parallax Material (ENBSeries) | RGBA Slot 1 — replaces `_n` when ENBSeries complex material is active. Channels: R=Normal X, G=Normal Y, B=Normal Z, A=Specular intensity. **ENBSeries only — not vanilla Skyrim SE.** |
| `_cm` | Complex Material packed (Community Shaders Extended Materials) | RGBA Slot 5 — **Community Shaders Extended Materials** workflow. Channels: R=Environment reflection amount, G=Glossiness, B=Metallic, A=Height / mode-control alpha. **Not vanilla Skyrim SE.** |
| `_c` / `_C` | Complex Material packed alias | Identical channel layout and role as `_cm`. `_C.dds` (uppercase) is treated the same on Windows and by this tool. Prefer `_cm` for new mods unless the pack uses `_c` naming. |

Batch folder mode scans subfolders and automatically skips generated variants (`_n`, `_p`, `_g`, `_m`, `_rmaos`, `_ramos`, `_msn`, `_cm`, `_c`, `_C`) so it only processes original source textures.

## Renderer quick-reference

| Renderer | Required files | Notes |
|----------|---------------|-------|
| **Vanilla Skyrim SE** | diffuse + `_n` | Add `_m` (greyscale) for reflective materials; add `_p` for parallax meshes |
| **Community Shaders (Extended Materials)** | diffuse + `_n` + `_p` + `_cm` (or `_c` / `_C`) | `_cm`/`_c`/`_C`: R=Env reflection, G=Glossiness, B=Metallic, A=Height / mode-control alpha |
| **Community Shaders TruePBR** | diffuse + `_n` + `_rmaos` (+ optional `_p`) | JSON-driven workflow; channel interpretation can vary by TruePBR config |
| **ENBSeries complex (this app preset)** | diffuse + `_msn` + `_p` + `_m` | `_msn`: R=Nx, G=Ny, B=Nz, A=Spec; `_m` (complex preset): R=Reflection, G=Glossiness, B=Metalness, A=Height |

Community Shaders Extended Materials and ENB complex material are **mutually exclusive** workflows. Choose one target renderer for a given install/output set instead of trying to combine them.

### NIF Editor — Experimental Feature

The **NIF Editor** (accessible from the toolbar button) lets you patch BSLightingShaderProperty flags and texture slots in Skyrim SE mesh files.
**This is an experimental feature.** Always keep backups of your NIF files before patching.
The **Auto-patch NIFs after generation** checkbox (off by default) triggers NIF patching automatically after each generation run.
