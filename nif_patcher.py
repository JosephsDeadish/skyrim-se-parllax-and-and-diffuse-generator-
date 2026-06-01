"""Skyrim SE NIF file patcher (v0.8).

Reads Skyrim SE NIF files (format 20.2.0.7, user_version=12,
user_version_2=83/100) and patches ``BSLightingShaderProperty`` shader flags and
``BSShaderTextureSet`` texture paths to enable parallax, environment
mapping, and ENB complex-material effects on meshes that shipped without
those flags.

Key features
------------
* Set ``SLSF1_Parallax`` + write texture slot 3 for standard parallax.
* Set ``SLSF1_Parallax_Occlusion`` for ENB POM (deeper depth, screen-space).
* Write ``Parallax Scale`` for NIFs already using shader type 3 (Heightmap).
* Upgrade a shader from type 0 (Default) to type 3 (Heightmap) and insert
  the ``Parallax Max Passes`` / ``Parallax Scale`` fields so any scale value
  takes effect in-game (``force_shader_type_3=True``).
* Set ``SLSF1_Environment_Mapping`` + write texture slots 4/5.
* Write any texture slot path (normal/MSN, cubemap, env mask, …).
* Dry-run mode: preview what would change without writing.
* Backup original files before overwriting.

No third-party libraries required — only stdlib ``struct`` and ``pathlib``.

Usage (programmatic)::

    from nif_patcher import patch_nif, NifPatchOptions
    result = patch_nif(
        Path("Data/meshes/arch/stone.nif"),
        NifPatchOptions(
            enable_parallax=True,
            parallax_texture_path="textures\\arch\\stone_p.dds",
            parallax_scale=2.5,
            force_shader_type_3=True,
        ),
    )
    print(result.message)

Usage (CLI)::

    python nif_patcher.py mesh.nif --parallax textures/arch/stone_p.dds --parallax-scale 2.5
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# NIF format constants
# ---------------------------------------------------------------------------

_NIF_VERSION_20_2_0_7: int = 0x14020007
_SKYRIM_USER_VERSION: int = 12
_SKYRIM_SE_USER_VERSION_2: int = 83
_SKYRIM_SE_USER_VERSION_2_ALT: int = 100
_SKYRIM_LE_USER_VERSION_2: int = 34
_SUPPORTED_USER_VERSION_2: tuple[int, ...] = (
    _SKYRIM_SE_USER_VERSION_2,
    _SKYRIM_SE_USER_VERSION_2_ALT,
    _SKYRIM_LE_USER_VERSION_2,
)

_HEADER_PREFIXES: tuple[bytes, ...] = (
    b"Gamebryo File Format, Version 20.2.0.7",
    b"NetImmerse File Format, Version 20.2.0.7",
)

# Shader Flags 1 (BSLightingShaderProperty)
SLSF1_SPECULAR: int = 0x00000001
SLSF1_SKINNED: int = 0x00000002
SLSF1_LOW_DETAIL: int = 0x00000004
SLSF1_VERTEX_ALPHA: int = 0x00000008
SLSF1_UNKNOWN_1: int = 0x00000010
SLSF1_SINGLE_PASS: int = 0x00000020
SLSF1_EMPTY: int = 0x00000040
SLSF1_ENVIRONMENT_MAPPING: int = 0x00000080
SLSF1_RECEIVE_SHADOWS: int = 0x00000100
SLSF1_CAST_SHADOWS: int = 0x00000200
SLSF1_FACEGEN_DETAIL: int = 0x00000400
SLSF1_PARALLAX: int = 0x00000800
SLSF1_MODEL_SPACE_NORMALS: int = 0x00001000
SLSF1_NON_PROJECTIVE_SHADOWS: int = 0x00002000
SLSF1_LANDSCAPE: int = 0x00004000
SLSF1_REFRACTION: int = 0x00008000
SLSF1_FIRE_REFRACTION: int = 0x00010000
SLSF1_EYE_ENVIRONMENT_MAPPING: int = 0x00020000
SLSF1_HAIR_SOFT_LIGHTING: int = 0x00040000
SLSF1_SCREENDOOR_ALPHA_FADE: int = 0x00080000
SLSF1_LOCALMAP_HIDE_SECRET: int = 0x00100000
SLSF1_FACEGEN_RGB_TINT: int = 0x00200000
SLSF1_OWN_EMIT: int = 0x00400000
SLSF1_PROJECTED_UV: int = 0x00800000
SLSF1_MULTIPLE_TEXTURES: int = 0x01000000
SLSF1_REMAPPABLE_TEXTURES: int = 0x02000000
SLSF1_DECAL: int = 0x04000000
SLSF1_DYNAMIC_DECAL: int = 0x08000000
SLSF1_PARALLAX_OCCLUSION: int = 0x10000000
SLSF1_EXTERNAL_EMITTANCE: int = 0x20000000
SLSF1_SOFT_EFFECT: int = 0x40000000
SLSF1_ZBUFFER_TEST: int = 0x80000000

# Shader Flags 2
SLSF2_ZBUFFER_WRITE: int = 0x00000001
SLSF2_LOD_LANDSCAPE: int = 0x00000002
SLSF2_LOD_OBJECTS: int = 0x00000004
SLSF2_NO_FADE: int = 0x00000008
SLSF2_DOUBLE_SIDED: int = 0x00000010
SLSF2_VERTEX_COLORS: int = 0x00000020
SLSF2_GLOW_MAP: int = 0x00000040
SLSF2_ASSUME_SHADOWMASK: int = 0x00000080
SLSF2_PACKED_TANGENT: int = 0x00000100
SLSF2_MULTI_INDEX_SNOW: int = 0x00000200
SLSF2_VERTEX_LIGHTING: int = 0x00000400
SLSF2_UNIFORM_SCALE: int = 0x00000800
SLSF2_FIT_SLOPE: int = 0x00001000
SLSF2_BILLBOARD: int = 0x00002000
SLSF2_NO_LOD_LAND_BLEND: int = 0x00004000
SLSF2_ENVMAP_LIGHT_FADE: int = 0x00008000
SLSF2_WIREFRAME: int = 0x00010000
SLSF2_WEAPON_BLOOD: int = 0x00020000
SLSF2_HIDE_ON_LOCAL_MAP: int = 0x00040000
SLSF2_PREMULT_ALPHA: int = 0x00080000
SLSF2_CLOUD_LOD: int = 0x00100000
SLSF2_ANISOTROPIC_LIGHTING: int = 0x00200000
SLSF2_NO_TRANSPARENCY_MULTISAMPLING: int = 0x00400000
SLSF2_UNUSED01: int = 0x00800000
SLSF2_MULTI_LAYER_PARALLAX: int = 0x01000000
SLSF2_SOFT_LIGHTING: int = 0x02000000
SLSF2_RIM_LIGHTING: int = 0x04000000
SLSF2_BACK_LIGHTING: int = 0x08000000
SLSF2_UNUSED02: int = 0x10000000
SLSF2_TREE_ANIM: int = 0x20000000
SLSF2_EFFECT_LIGHTING: int = 0x40000000
SLSF2_HD_LOD_OBJECTS: int = 0x80000000

# Texture slot indices inside BSShaderTextureSet
TEXTURE_SLOT_DIFFUSE: int = 0
TEXTURE_SLOT_NORMAL: int = 1
TEXTURE_SLOT_GLOW: int = 2
TEXTURE_SLOT_PARALLAX: int = 3
TEXTURE_SLOT_CUBEMAP: int = 4
TEXTURE_SLOT_ENV_MASK: int = 5
TEXTURE_SLOT_SUBSURFACE: int = 6
TEXTURE_SLOT_WRINKLE: int = 7
TEXTURE_SLOT_BACKLIGHT: int = 8
TEXTURE_SLOT_COUNT: int = 9

_DIFFUSE_SLOT_DISALLOWED_SUFFIXES: tuple[str, ...] = (
    "_n.dds",
    "_msn.dds",
    "_p.dds",
    "_g.dds",
    "_sk.dds",
    "_s.dds",
    "_m.dds",
    "_mask.dds",
    "_envmask.dds",
    "_ao.dds",
    "_ambientocclusion.dds",
    "_rough.dds",
    "_roughness.dds",
    "_metal.dds",
    "_metallic.dds",
    "_metalness.dds",
    "_em.dds",
    "_emis.dds",
    "_cm.dds",
    "_c.dds",
    "_rmaos.dds",
    "_ramos.dds",
    "_orm.dds",
    "_orms.dds",
    "_mrao.dds",
    "_mra.dds",
    "_e.dds",
    "_cube.dds",
    "_env.dds",
    "_envmap.dds",
)
_CUBEMAP_SLOT_EXPECTED_SUFFIXES: tuple[str, ...] = (
    "_e.dds",
    "_cube.dds",
    "_env.dds",
    "_envmap.dds",
    "_cubemap.dds",
)
_CUBEMAP_SLOT_WRONG_SUFFIXES: tuple[str, ...] = (
    "_n.dds",
    "_msn.dds",
    "_p.dds",
    "_g.dds",
    "_em.dds",
    "_emis.dds",
    "_m.dds",
    "_ao.dds",
    "_ambientocclusion.dds",
    "_rough.dds",
    "_roughness.dds",
    "_metal.dds",
    "_metallic.dds",
    "_metalness.dds",
    "_cm.dds",
    "_c.dds",
    "_rmaos.dds",
    "_ramos.dds",
    "_orm.dds",
    "_orms.dds",
    "_mrao.dds",
    "_mra.dds",
)
_NORMAL_SLOT_DISALLOWED_SUFFIXES: tuple[str, ...] = (
    "_p.dds",
    "_g.dds",
    "_sk.dds",
    "_m.dds",
    "_mask.dds",
    "_envmask.dds",
    "_ao.dds",
    "_ambientocclusion.dds",
    "_rough.dds",
    "_roughness.dds",
    "_metal.dds",
    "_metallic.dds",
    "_metalness.dds",
    "_em.dds",
    "_emis.dds",
    "_rmaos.dds",
    "_ramos.dds",
    "_orm.dds",
    "_orms.dds",
    "_mrao.dds",
    "_mra.dds",
    "_cm.dds",
    "_c.dds",
    "_e.dds",
    "_env.dds",
    "_envmap.dds",
    "_cube.dds",
    "_cubemap.dds",
)
_ENV_MASK_SLOT_EXPECTED_SUFFIXES: tuple[str, ...] = (
    "_m.dds",
    "_mask.dds",
    "_envmask.dds",
    "_rmaos.dds",
    "_ramos.dds",
    "_orm.dds",
    "_orms.dds",
    "_mrao.dds",
    "_mra.dds",
    "_cm.dds",
    "_c.dds",
)
_TRUEPBR_ENV_MASK_SUFFIXES: tuple[str, ...] = (
    "_rmaos.dds",
    "_ramos.dds",
)
_LEGACY_TRUEPBR_ENV_MASK_SUFFIXES: tuple[str, ...] = (
    "_orm.dds",
    "_orms.dds",
    "_mrao.dds",
    "_mra.dds",
)
_GENERIC_AUTHORING_PACKED_SUFFIXES: tuple[str, ...] = (
    "_orm.dds",
    "_orms.dds",
    "_mrao.dds",
    "_mra.dds",
    "_rough.dds",
    "_roughness.dds",
    "_metal.dds",
    "_metallic.dds",
    "_metalness.dds",
    "_ao.dds",
    "_ambientocclusion.dds",
)

# BSLightingShaderPropertyShaderType values
SHADER_TYPE_DEFAULT: int = 0
SHADER_TYPE_ENVMAP: int = 1
SHADER_TYPE_GLOW: int = 2
SHADER_TYPE_HEIGHTMAP: int = 3       # Standard offset parallax + scale
SHADER_TYPE_FACE_TINT: int = 4
SHADER_TYPE_SKIN_TINT: int = 5
SHADER_TYPE_HAIR_TINT: int = 6
SHADER_TYPE_PARALLAX_OCC: int = 7   # (rarely used directly)
SHADER_TYPE_MULTILAYER: int = 11    # Multi-layer parallax
_KNOWN_SHADER_TYPES: set[int] = {
    SHADER_TYPE_DEFAULT,
    SHADER_TYPE_ENVMAP,
    SHADER_TYPE_GLOW,
    SHADER_TYPE_HEIGHTMAP,
    SHADER_TYPE_FACE_TINT,
    SHADER_TYPE_SKIN_TINT,
    SHADER_TYPE_HAIR_TINT,
    SHADER_TYPE_PARALLAX_OCC,
    SHADER_TYPE_MULTILAYER,
}

# Human-readable names for BSLightingShaderPropertyShaderType values
SHADER_TYPE_NAMES: dict[int, str] = {
    SHADER_TYPE_DEFAULT: "Default",
    SHADER_TYPE_ENVMAP: "Environment Map",
    SHADER_TYPE_GLOW: "Glow/Emit",
    SHADER_TYPE_HEIGHTMAP: "Heightmap (Parallax)",
    SHADER_TYPE_FACE_TINT: "Face Tint",
    SHADER_TYPE_SKIN_TINT: "Skin Tint",
    SHADER_TYPE_HAIR_TINT: "Hair Tint",
    SHADER_TYPE_PARALLAX_OCC: "Parallax Occlusion",
    SHADER_TYPE_MULTILAYER: "Multi-Layer Parallax",
}

# Default parallax field values when upgrading a block to type 3
_DEFAULT_PARALLAX_MAX_PASSES: float = 4.0
_DEFAULT_PARALLAX_SCALE: float = 1.0

# Byte size of the common BSLightingShaderProperty fields that precede
# type-specific fields, measured from the start of the block and assuming
# zero extra-data entries.  For each extra-data entry, add 4 bytes.
#
# Breakdown:
#   NiObjectNET : name_ref(4) + num_extra(4) + controller_ref(4) = 12
#   BSShaderProp: flags1(4) + flags2(4)                          =  8
#   shader_type : (4)                                            =  4
#   uv_offset   : (8)  uv_scale: (8)                            = 16
#   texture_set : (4)                                            =  4
#   emissive_col: (12) emissive_mul: (4)                        = 16
#   clamp_mode  : (4) alpha: (4) refraction: (4) glossiness: (4)= 16
#   spec_color  : (12) spec_strength: (4)                       = 16
#   light_eff1  : (4) light_eff2: (4)                           =  8
#
#   Total common = 100  (no extra-data; add 4 × num_extra)
_COMMON_FIELDS_SIZE: int = 100

# Offsets within the common section (from block_start, 0 extra-data)
_OFFSET_FLAGS1: int = 12        # block_start + 12 + 4*num_extra
_OFFSET_FLAGS2: int = 16
_OFFSET_SHADER_TYPE: int = 20
_OFFSET_TEXTURE_SET: int = 40   # after shader_type + uv_offset + uv_scale
_OFFSET_GLOSSINESS: int = 72    # clamp_mode(4)+alpha(4)+refraction(4)+glossiness(4)
_OFFSET_SPEC_COLOR: int = 76    # 3 × float32 = 12 bytes (R,G,B)
_OFFSET_SPEC_STRENGTH: int = 88 # immediately after spec_color
_OFFSET_LIGHT_EFF1: int = 92    # lighting effect 1 (soft lighting)
_OFFSET_LIGHT_EFF2: int = 96    # lighting effect 2

# For SHADER_TYPE_ENVMAP (1) blocks, env_map_scale is the first type-specific
# float immediately after the 100-byte common section.
_ENVMAP_SCALE_OFFSET_FROM_COMMON: int = 0

# BSTriShape / NiTriShape shape-block layout for Skyrim SE (NIF 20.2.0.7,
# user_version_2 = 83 / 100). After the NiObjectNET prefix:
#
#   NiAVObject  : flags(2) + transform(52) + collision_ref(4)  = 58 bytes
#   BoundingSphere: center(12) + radius(4)                      = 16 bytes
#   → skin_instance_ref (4 B), shader_property_ref (4 B), alpha_property_ref (4 B)
#
# Total from block_start to skin_instance_ref with zero extra-data = 12+58+16 = 86.
_SHAPE_FIXED_PRE_REFS: int = 86


# ---------------------------------------------------------------------------
# Patch options
# ---------------------------------------------------------------------------

@dataclass
class NifPatchOptions:
    """Options that control which changes are applied to each shader block.

    Attributes
    ----------
    enable_parallax:
        Set ``SLSF1_Parallax`` in Shader_Flags_1.
    enable_pom:
        Also set ``SLSF1_Parallax_Occlusion`` for ENB Parallax Occlusion
        Mapping (deeper, screen-space parallax).  Implies *enable_parallax*.
    parallax_scale:
        In-game parallax depth multiplier.  Written into the
        ``Parallax Scale`` field of shader-type-3 blocks.  Values > 1.0
        produce visibly deeper parallax; extreme values (5–10) create a
        dramatic relief/bas-relief appearance.  Has no effect unless the
        block's shader type is already 3 or *force_shader_type_3* is True.
    force_shader_type_3:
        When True, upgrade any shader block whose type is 0 (Default) to
        type 3 (Heightmap/Parallax) by inserting the ``Parallax Max Passes``
        and ``Parallax Scale`` fields into the block.  This is the only way
        to set a custom *parallax_scale* on vanilla/default-shader NIFs.
        The NIF block structure is expanded in place and the header block
        sizes are updated accordingly.
    enable_env_mapping:
        Set ``SLSF1_Environment_Mapping`` in Shader_Flags_1.
    enable_glow_map:
        Set ``SLSF2_Glow_Map`` in Shader_Flags_2.  Set this together with
        *glow_texture_path* to activate an emissive/glow map in-game.
    enable_pbr:
        Set ``SLSF2 Unused01`` / Community Shaders TruePBR flag on the
        ``BSLightingShaderProperty`` so ``_rmaos`` + JSON workflows render.
    parallax_texture_path:
        Texture path for slot 3. Absolute ``Data\\Textures`` picks are normalized
        to Skyrim-relative form (e.g. ``textures\\arch\\stone_p.dds``).
    normal_texture_path:
        Relative path for slot 1.  Set to your ``_msn.dds`` for ENB complex
        material or ``_n.dds`` for a standard normal map.
    glow_texture_path:
        Relative path for slot 2 (glow/emissive map, ``_g.dds``).  Pair with
        *enable_glow_map* to activate the emissive effect on the mesh.
    diffuse_texture_path:
        Relative path for slot 0 (diffuse/albedo).  In vanilla Skyrim SE the
        diffuse texture has **no** suffix — it is just ``textures\\arch\\stone.dds``,
        not ``stone_d.dds`` or ``stone_diffuse.dds``.  Blender exports ``_d`` /
        ``_diffuse`` / ``_albedo`` by default but those names are not used by
        Skyrim SE.  Useful for re-pointing a NIF's diffuse slot after a rename.
    env_mask_texture_path:
        Relative path for slot 5 (environment mask / ``_m.dds``).
    cubemap_texture_path:
        Relative path for slot 4 (cube map for environment reflections).
    backup:
        Write a ``.nif.bak`` copy of the original before overwriting.
    dry_run:
        Analyse what would change but do not write any files.
    disable_parallax:
        Clear ``SLSF1_Parallax`` and ``SLSF1_Parallax_Occlusion``.
    disable_pom:
        Clear only ``SLSF1_Parallax_Occlusion``.
    disable_env_mapping:
        Clear ``SLSF1_Environment_Mapping``.
    disable_glow_map:
        Clear ``SLSF2_Glow_Map`` in Shader_Flags_2.
    disable_pbr:
        Clear ``SLSF2 Unused01`` / Community Shaders TruePBR flag.
    clear_parallax_texture_path:
        Empty texture slot 3 (parallax map).
    clear_normal_texture_path:
        Empty texture slot 1 (normal/MSN map).
    clear_glow_texture_path:
        Empty texture slot 2 (glow/emissive map).
    clear_diffuse_texture_path:
        Empty texture slot 0 (diffuse/albedo map).
    clear_env_mask_texture_path:
        Empty texture slot 5 (environment mask).
    clear_cubemap_texture_path:
        Empty texture slot 4 (cubemap).
    """

    enable_parallax: bool = False
    enable_pom: bool = False
    parallax_scale: float | None = None
    force_shader_type_3: bool = False
    enable_env_mapping: bool = False
    enable_glow_map: bool = False
    enable_pbr: bool = False
    parallax_texture_path: str | None = None
    normal_texture_path: str | None = None
    glow_texture_path: str | None = None
    diffuse_texture_path: str | None = None
    env_mask_texture_path: str | None = None
    cubemap_texture_path: str | None = None
    backup: bool = True
    dry_run: bool = False
    disable_parallax: bool = False
    disable_pom: bool = False
    disable_env_mapping: bool = False
    disable_glow_map: bool = False
    disable_pbr: bool = False
    clear_parallax_texture_path: bool = False
    clear_normal_texture_path: bool = False
    clear_glow_texture_path: bool = False
    clear_diffuse_texture_path: bool = False
    clear_env_mask_texture_path: bool = False
    clear_cubemap_texture_path: bool = False

    # ----- Safety skip conditions -----
    skip_incompatible_shader_types: bool = True
    """Skip shader blocks whose type is not Default(0), Parallax(3), or
    EnvMap(1).  Prevents accidental corruption of face-tint, skin-tint, hair,
    and multi-layer parallax shaders."""

    skip_decal: bool = True
    """Skip shader blocks that carry ``SLSF1_Decal`` or ``SLSF1_Dynamic_Decal``
    flags when enabling parallax.  Parallax + decal causes visual glitches
    in Skyrim SE."""

    skip_lighting_effects: bool = True
    """Skip shader blocks with ``SLSF2_Soft_Lighting``, ``SLSF2_Rim_Lighting``,
    or ``SLSF2_Back_Lighting`` flags when enabling parallax.  These mesh-lighting
    modes are incompatible with the parallax shader."""

    skip_anisotropic: bool = True
    """Skip shader blocks with ``SLSF2_Anisotropic_Lighting`` when enabling
    parallax.  Anisotropic-lit shapes produce incorrect results with parallax."""

    skip_if_havok: bool = True
    """Skip all parallax patching in NIFs that contain a
    ``BSBehaviorGraphExtraData`` block (attached Havok skeleton).  Parallax
    on Havok-animated meshes causes ``EXCEPTION_ACCESS_VIOLATION`` crashes."""

    skip_if_skinned: bool = True
    """Skip shader blocks whose parent shape has a skin instance
    (``NiSkinInstance`` / ``BSDismemberSkinInstance``).  Skinned meshes such
    as armour and clothing crash with parallax enabled."""

    skip_if_alpha: bool = True
    """Skip shader blocks whose parent shape has an ``NiAlphaProperty``
    (alpha-blended or alpha-tested).  These shapes produce rendering glitches
    with parallax enabled."""

    # ----- Mesh lighting fix -----
    fix_mesh_lighting: bool = False
    """When True, clamp the shader's ``lighting effect 1`` (soft lighting)
    field to 0.6 if its current value exceeds that threshold.  This corrects
    the over-bright glowing mesh problem seen with ENB.  Community Shaders
    fixes this at the engine level and does not require this option."""

    # ----- Additional shader field patches for complex material -----
    env_map_scale: float | None = None
    """Set the environment-map scale field on ``SHADER_TYPE_ENVMAP`` (1)
    blocks.  Recommended value is ``1.0`` for complex-material shapes.
    Has no effect on other shader types."""

    spec_strength: float | None = None
    """Set the specular-strength field.  Recommended value is ``1.0`` for
    complex material.  Typical range is 0.0–1.0."""

    spec_color: tuple[float, float, float] | None = None
    """Set the specular colour ``(R, G, B)`` each in 0.0–1.0.  Use
    ``(1.0, 1.0, 1.0)`` (white) for complex-material textures that contain
    metalness data (non-black blue channel)."""





# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class NifShaderInfo:
    """Summary of a single shader block found during scanning."""
    block_index: int
    shader_type: int
    flags1: int
    flags2: int
    parallax_scale: float | None   # None if block is not shader-type 3
    texture_paths: dict[int, str]  # slot → path

    # Shape-context fields — populated by scan_nif_diagnostics when a parent
    # BSTriShape-family block can be matched to this shader property.
    parent_block_type: str = ""       # e.g. "BSTriShape", "BSLODTriShape"
    is_lod_shape: bool = False        # True when parent is a LOD geometry type
    is_skinned: bool = False          # True when parent has a skin instance
    has_alpha_property: bool = False  # True when parent has NiAlphaProperty

    # ------------------------------------------------------------------ flags

    @property
    def has_parallax_flag(self) -> bool:
        return bool(self.flags1 & SLSF1_PARALLAX)

    @property
    def has_pom_flag(self) -> bool:
        return bool(self.flags1 & SLSF1_PARALLAX_OCCLUSION)

    @property
    def has_env_mapping_flag(self) -> bool:
        return bool(self.flags1 & SLSF1_ENVIRONMENT_MAPPING)

    @property
    def has_glow_map_flag(self) -> bool:
        return bool(self.flags2 & SLSF2_GLOW_MAP)

    @property
    def has_model_space_normals_flag(self) -> bool:
        """True when ``SLSF1_Model_Space_Normals`` is set (required for ENB _msn workflow)."""
        return bool(self.flags1 & SLSF1_MODEL_SPACE_NORMALS)

    @property
    def has_landscape_flag(self) -> bool:
        """True when ``SLSF1_Landscape`` is set.

        Landscape shaders use a separate parallax mechanism; standard parallax
        flags set on a landscape shader have no visible effect in-game.
        """
        return bool(self.flags1 & SLSF1_LANDSCAPE)

    @property
    def has_skinned_flag(self) -> bool:
        """True when ``SLSF1_Skinned`` is set in the shader block itself."""
        return bool(self.flags1 & SLSF1_SKINNED)

    @property
    def has_decal_flag(self) -> bool:
        """True when ``SLSF1_Decal`` or ``SLSF1_Dynamic_Decal`` is set.

        Parallax combined with a decal flag produces visible rendering glitches
        in Skyrim SE.
        """
        return bool(self.flags1 & (SLSF1_DECAL | SLSF1_DYNAMIC_DECAL))

    @property
    def has_soft_lighting_flag(self) -> bool:
        return bool(self.flags2 & SLSF2_SOFT_LIGHTING)

    @property
    def has_rim_lighting_flag(self) -> bool:
        return bool(self.flags2 & SLSF2_RIM_LIGHTING)

    @property
    def has_back_lighting_flag(self) -> bool:
        return bool(self.flags2 & SLSF2_BACK_LIGHTING)

    @property
    def has_anisotropic_flag(self) -> bool:
        """True when ``SLSF2_Anisotropic_Lighting`` is set.

        Anisotropic-lit shapes produce incorrect results when parallax is
        enabled.
        """
        return bool(self.flags2 & SLSF2_ANISOTROPIC_LIGHTING)

    @property
    def has_vertex_colors_flag(self) -> bool:
        """True when ``SLSF2_Vertex_Colors`` is set.

        Skyrim SE requires this flag to be present on parallax-enabled meshes
        for correct rendering.
        """
        return bool(self.flags2 & SLSF2_VERTEX_COLORS)

    @property
    def has_multi_layer_parallax_flag(self) -> bool:
        """True when ``SLSF2_Multi_Layer_Parallax`` is set.

        Multi-layer parallax (shader type 11) uses a completely different
        rendering path and is mutually exclusive with standard parallax.
        """
        return bool(self.flags2 & SLSF2_MULTI_LAYER_PARALLAX)

    @property
    def has_pbr_flag(self) -> bool:
        """True when ``SLSF2_Unused01`` is set — the Community Shaders TruePBR flag."""
        return bool(self.flags2 & SLSF2_UNUSED01)

    @property
    def shader_type_name(self) -> str:
        """Human-readable name of the shader type (e.g. ``'Heightmap (Parallax)'``)."""
        return SHADER_TYPE_NAMES.get(self.shader_type, f"Unknown ({self.shader_type})")


@dataclass
class NifPatchResult:
    """Outcome of a :func:`patch_nif` call."""
    nif_path: Path
    success: bool
    already_up_to_date: bool = False
    shader_properties_found: int = 0
    shader_properties_patched: int = 0
    texture_sets_patched: int = 0
    blocks_upgraded_to_type3: int = 0
    message: str = ""
    backup_path: Path | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class NifValidationResult:
    """Result of :func:`validate_nif_for_parallax`."""
    nif_path: Path
    valid: bool
    shader_count: int = 0
    ready_count: int = 0          # shaders that already have parallax enabled
    needs_patch_count: int = 0    # shaders present but missing flags/texture
    skip_count: int = 0           # shaders skipped due to hard incompatibilities
    has_havok: bool = False       # True if a BSBehaviorGraphExtraData block exists
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    skip_reasons: list[str] = field(default_factory=list)
    """Per-block reasons why parallax patching was or would be skipped.

    Each entry names the specific incompatibility, for example:
    ``"Block 2: skinned mesh (NiSkinInstance) — parallax causes CTD on animated geometry"``.
    """
    renderer_notes: dict[str, list[str]] = field(default_factory=dict)
    """Per-renderer compatibility notes keyed by renderer name.

    Keys are ``'vanilla'``, ``'enb'``, ``'community_shaders'``, and
    ``'truepbr'``.  Each value is a list of human-readable status strings for
    all shader blocks combined, describing what is ready and what needs to
    change for that renderer.
    """


# ---------------------------------------------------------------------------
# Low-level binary buffer
# ---------------------------------------------------------------------------

class _Buf:
    """Mutable byte buffer with read/write helpers (little-endian throughout)."""

    def __init__(self, data: bytes | bytearray) -> None:
        self._b = bytearray(data)
        self._pos: int = 0

    # --- position helpers ---------------------------------------------------

    @property
    def pos(self) -> int:
        return self._pos

    def seek(self, pos: int) -> None:
        self._pos = pos

    def remaining(self) -> int:
        return len(self._b) - self._pos

    def _require(self, size: int, *, offset: int | None = None, context: str = "buffer read") -> int:
        start = self._pos if offset is None else offset
        end = start + size
        if start < 0 or end > len(self._b):
            raise ValueError(
                f"{context} out of range at offset {start} (need {size} byte(s), buffer size {len(self._b)})"
            )
        return start

    # --- reads --------------------------------------------------------------

    def read_u8(self) -> int:
        self._require(1, context="u8 read")
        v = self._b[self._pos]
        self._pos += 1
        return v

    def read_u16(self) -> int:
        self._require(2, context="u16 read")
        v = struct.unpack_from("<H", self._b, self._pos)[0]
        self._pos += 2
        return v

    def read_u32(self) -> int:
        self._require(4, context="u32 read")
        v = struct.unpack_from("<I", self._b, self._pos)[0]
        self._pos += 4
        return v

    def read_i32(self) -> int:
        self._require(4, context="i32 read")
        v = struct.unpack_from("<i", self._b, self._pos)[0]
        self._pos += 4
        return v

    def read_float(self) -> float:
        self._require(4, context="float read")
        v = struct.unpack_from("<f", self._b, self._pos)[0]
        self._pos += 4
        return v

    def read_sstring_u8(self) -> str:
        n = self.read_u8()
        self._require(n, context="short string read")
        raw = bytes(self._b[self._pos: self._pos + n])
        self._pos += n
        return raw.decode("latin-1").rstrip("\x00")

    def read_sstring_u32(self) -> str:
        n = self.read_u32()
        self._require(n, context="string read")
        raw = bytes(self._b[self._pos: self._pos + n])
        self._pos += n
        return raw.decode("latin-1")

    def read_u32_at(self, offset: int) -> int:
        start = self._require(4, offset=offset, context="u32 read")
        return struct.unpack_from("<I", self._b, start)[0]

    def read_u16_at(self, offset: int) -> int:
        start = self._require(2, offset=offset, context="u16 read")
        return struct.unpack_from("<H", self._b, start)[0]

    # --- in-place writes (never resize) ------------------------------------

    def write_u32_at(self, offset: int, value: int) -> None:
        self._require(4, offset=offset, context="u32 write")
        struct.pack_into("<I", self._b, offset, value)

    def write_u16_at(self, offset: int, value: int) -> None:
        self._require(2, offset=offset, context="u16 write")
        struct.pack_into("<H", self._b, offset, value)

    def write_float_at(self, offset: int, value: float) -> None:
        self._require(4, offset=offset, context="float write")
        struct.pack_into("<f", self._b, offset, value)

    # --- resize helpers -----------------------------------------------------

    def insert_bytes_at(self, offset: int, data: bytes) -> None:
        """Insert *data* at byte *offset*, shifting everything after it."""
        self._b[offset:offset] = bytearray(data)
        if self._pos >= offset:
            self._pos += len(data)

    def replace_sstring_u32_at(self, offset: int, old_text: str, new_text: str) -> None:
        """Replace the uint32-prefixed string at *offset* (may change length)."""
        old_enc = old_text.encode("latin-1")
        new_enc = new_text.encode("latin-1")
        old_len = self.read_u32_at(offset)
        self._require(4 + old_len, offset=offset, context="string replace")
        old_total = 4 + old_len
        new_total = 4 + len(new_enc)
        new_chunk = struct.pack("<I", len(new_enc)) + new_enc
        self._b[offset: offset + old_total] = bytearray(new_chunk)
        if self._pos > offset + old_total:
            self._pos += new_total - old_total

    def to_bytes(self) -> bytes:
        return bytes(self._b)


# ---------------------------------------------------------------------------
# NIF header
# ---------------------------------------------------------------------------

@dataclass
class _NifHeader:
    version: int
    user_version: int
    user_version_2: int
    num_blocks: int
    block_types: list[str]
    block_type_indices: list[int]
    block_sizes: list[int]
    strings: list[str]
    blocks_start: int        # byte offset where block data begins
    block_sizes_offset: int  # byte offset of block_sizes[0] in the file


def _diagnose_header_parse_failure(data: bytes, exc: Exception) -> list[str]:
    diagnostics = [f"Malformed or truncated NIF: {exc}"]
    if len(data) < 64:
        diagnostics.append(
            "The file is shorter than a normal Skyrim NIF header. It is probably truncated, corrupt, or not really a NIF."
        )
        return diagnostics
    try:
        header_line_end = _find_header_terminator(data)
        if header_line_end is None:
            diagnostics.append("The NIF header line is incomplete. Re-export or re-save the mesh before patching.")
            return diagnostics
        header_line = data[:header_line_end]
        if not _has_supported_header_prefix(header_line):
            diagnostics.append("Header prefix is not a Skyrim/Gamebryo 20.2.0.7 NIF. This file is unsupported for auto-patching.")
            return diagnostics
        version_offset = header_line_end
        version = struct.unpack_from("<I", data, version_offset)[0]
        if version != _NIF_VERSION_20_2_0_7:
            diagnostics.append(f"NIF version is 0x{version:08X}, not Skyrim SE 20.2.0.7.")
            return diagnostics
        user_version = struct.unpack_from("<I", data, version_offset + 5)[0]
        user_version_2 = struct.unpack_from("<I", data, version_offset + 13)[0]
        if user_version_2 == _SKYRIM_LE_USER_VERSION_2:
            diagnostics.append("This looks like a Skyrim Legendary Edition / Oldrim NIF. Convert it to SSE before patching.")
        elif user_version != _SKYRIM_USER_VERSION or user_version_2 not in _SUPPORTED_USER_VERSION_2:
            diagnostics.append(
                f"Unexpected user version values ({user_version}, {user_version_2}). The file may use a different game/export format."
            )
    except Exception:
        pass
    diagnostics.append(
        "Resolution: open the mesh in NifSkope or the Creation Kit and re-save/export it as a clean Skyrim SE NIF, then run the patch again."
    )
    return diagnostics


def _find_header_terminator(data: bytes) -> int | None:
    """Return byte offset immediately after the header terminator, if found."""
    candidates = [pos for pos in (data.find(b"\n"), data.find(b"\x00")) if pos != -1]
    if not candidates:
        return None
    return min(candidates) + 1


def _has_supported_header_prefix(header_line: bytes) -> bool:
    """Return True when the header line starts with a known Skyrim NIF prefix."""
    normalized = header_line.rstrip(b"\r\n\x00 ")
    return any(normalized.startswith(prefix) for prefix in _HEADER_PREFIXES)


def _summarize_non_patchable_block_types(header: _NifHeader) -> list[str]:
    used_types = sorted(
        {
            header.block_types[type_idx]
            for type_idx in header.block_type_indices
            if 0 <= type_idx < len(header.block_types)
        }
    )
    if not used_types:
        return []
    shaderish = [name for name in used_types if "Shader" in name or "TexturingProperty" in name]
    diagnostics: list[str] = []
    if shaderish:
        diagnostics.append(f"Detected shader/material blocks: {', '.join(shaderish[:5])}.")
    if any("BSShaderPPLightingProperty" in name for name in used_types):
        diagnostics.append(
            "This mesh uses BSShaderPPLightingProperty instead of BSLightingShaderProperty, so this app cannot patch parallax automatically."
        )
        diagnostics.append(
            "Resolution: convert the mesh to use BSLightingShaderProperty in NifSkope/CK, then patch it again."
        )
    elif any("NiTexturingProperty" in name for name in used_types):
        diagnostics.append(
            "This mesh uses legacy NiTexturingProperty blocks instead of Skyrim shader properties, so Skyrim SE parallax cannot be auto-patched here."
        )
        diagnostics.append(
            "Resolution: re-export or modernize the mesh so it uses BSLightingShaderProperty before patching."
        )
    return diagnostics


def _read_header(buf: _Buf) -> _NifHeader | None:
    """Parse the NIF header; return ``None`` if not a supported Skyrim SE NIF."""
    header_line: bytearray = bytearray()
    while buf.remaining() > 0:
        b = buf.read_u8()
        header_line.append(b)
        if b in (ord("\n"), 0):
            break
        if len(header_line) > 100:
            return None
    if not _has_supported_header_prefix(bytes(header_line)):
        return None

    version = buf.read_u32()
    if version != _NIF_VERSION_20_2_0_7:
        return None

    endianness = buf.read_u8()
    if endianness != 1:
        return None

    user_version = buf.read_u32()
    if user_version != _SKYRIM_USER_VERSION:
        return None

    num_blocks = buf.read_u32()

    user_version_2 = buf.read_u32()
    if user_version_2 not in _SUPPORTED_USER_VERSION_2:
        return None
    for _ in range(3):
        buf.read_sstring_u8()  # export strings

    num_block_types = buf.read_u16()
    block_types = [buf.read_sstring_u32() for _ in range(num_block_types)]
    block_type_indices = [buf.read_u16() for _ in range(num_blocks)]

    block_sizes_offset = buf.pos
    block_sizes = [buf.read_u32() for _ in range(num_blocks)]

    num_strings = buf.read_u32()
    _max_str_len = buf.read_u32()
    strings = [buf.read_sstring_u32() for _ in range(num_strings)]

    # Per the NIF 20.2.0.7 format specification, a num_groups u32 field
    # follows the string table when user_version_2 < 130.  Skyrim SE uses
    # user_version_2 = 83 or 100 (both < 130), so this field is always
    # present in real game meshes.  It is always 0 for Skyrim SE NIFs, but
    # must be consumed here so that blocks_start is positioned correctly.
    # Omitting this read shifts every block offset 4 bytes early, which
    # causes _upgrade_block_to_type3 to insert type-3 fields at the wrong
    # position and corrupts the block — producing exactly the kind of
    # EXCEPTION_ACCESS_VIOLATION crash seen in game.
    num_groups = buf.read_u32()
    for _ in range(num_groups):
        buf.read_u32()  # group_sizes — always empty for Skyrim SE

    return _NifHeader(
        version=version,
        user_version=user_version,
        user_version_2=user_version_2,
        num_blocks=num_blocks,
        block_types=block_types,
        block_type_indices=block_type_indices,
        block_sizes=block_sizes,
        strings=strings,
        blocks_start=buf.pos,
        block_sizes_offset=block_sizes_offset,
    )


# ---------------------------------------------------------------------------
# Block parsers
# ---------------------------------------------------------------------------

@dataclass
class _ShaderPropBlock:
    """Parsed BSLightingShaderProperty fields."""
    block_index: int
    block_start: int
    num_extra: int             # number of extra-data refs in NiObjectNET

    flags1_offset: int
    flags2_offset: int
    shader_type_offset: int
    texture_set_ref_offset: int

    flags1: int
    flags2: int
    shader_type: int
    texture_set_ref: int       # block index of linked BSShaderTextureSet
    layout_shift: int
    common_end_offset: int

    # Only valid when shader_type == SHADER_TYPE_HEIGHTMAP (3):
    parallax_max_passes_offset: int | None
    parallax_scale_offset: int | None
    parallax_max_passes: float | None
    parallax_scale: float | None

    # Additional always-present fields used by shader patching
    spec_color_offset: int           # offset of 3-float spec_color (R,G,B)
    spec_strength_offset: int
    light_eff1_offset: int
    spec_color: tuple[float, float, float]
    spec_strength: float
    light_eff1: float

    # Only valid when shader_type == SHADER_TYPE_ENVMAP (1):
    env_map_scale_offset: int | None
    env_map_scale: float | None


@dataclass
class _TextureSetBlock:
    """Parsed BSShaderTextureSet."""
    block_index: int
    block_start: int
    count_offset: int
    count_size: int           # 4 for u32, 2 for u16
    num_textures: int
    slot_offsets: list[int]    # byte offset of each slot's uint32 length prefix
    slot_paths: list[str]


def _parse_shader_prop(buf: _Buf, block_index: int, block_start: int,
                       block_size: int, num_blocks: int) -> _ShaderPropBlock | None:
    """Parse a BSLightingShaderProperty block.

    Layout for NIF 20.2.0.7 / user_version=12 (zero extra-data):
      NiObjectNET  12 B   name_ref + num_extra + controller_ref
      flags1/2      8 B
      shader_type   4 B
      uv_offset/scale 16 B
      texture_set   4 B   (Ref — block index, i32)
      emissive_col 12 B + emissive_mul 4 B
      clamp/alpha/refraction/glossiness 16 B
      spec_color 12 B + spec_strength 4 B
      light_eff1+2 8 B
      ------- 100 B common -------
      [type-3 only] parallax_max_passes 4 B + parallax_scale 4 B
    """
    block_end = block_start + block_size
    if block_size < 16 or block_end > len(buf._b):
        return None
    if block_start + 12 > block_end:
        return None

    buf.seek(block_start)
    _name_ref = buf.read_u32()
    num_extra = buf.read_u32()
    max_extra_refs = max((block_size - 12) // 4, 0)
    if num_extra > max_extra_refs:
        return None
    if block_start + 12 + num_extra * 4 > block_end:
        return None
    for _ in range(num_extra):
        buf.read_u32()
    if buf.pos + 4 > block_end:
        return None
    _controller = buf.read_i32()

    extra_shift = num_extra * 4

    def _decode_shader_type(raw_value: int) -> int:
        if raw_value in _KNOWN_SHADER_TYPES:
            return raw_value
        low8 = raw_value & 0xFF
        if low8 in _KNOWN_SHADER_TYPES:
            return low8
        low16 = raw_value & 0xFFFF
        if low16 in _KNOWN_SHADER_TYPES:
            return low16
        return raw_value

    # Always use layout_shift=0 for Skyrim SE NIFs (user_version_2=83/100).
    # A speculative layout_shift=4 heuristic was previously attempted but
    # its scoring was unreliable: on standard NIFs it could mis-select the
    # shifted layout, causing _upgrade_block_to_type3 to insert type-3 float
    # fields at the wrong offset and corrupt the adjacent block — which
    # manifested as an EXCEPTION_ACCESS_VIOLATION in-game when Skyrim read
    # the garbled BSLightingShaderMaterial.
    layout_shift = 0
    flags1_offset = block_start + _OFFSET_FLAGS1 + extra_shift + layout_shift
    flags2_offset = block_start + _OFFSET_FLAGS2 + extra_shift + layout_shift
    shader_type_offset = block_start + _OFFSET_SHADER_TYPE + extra_shift + layout_shift
    texture_set_ref_offset = block_start + _OFFSET_TEXTURE_SET + extra_shift + layout_shift
    if (
        flags1_offset + 4 > block_end
        or flags2_offset + 4 > block_end
        or shader_type_offset + 4 > block_end
        or texture_set_ref_offset + 4 > block_end
    ):
        return None
    flags1 = buf.read_u32_at(flags1_offset)
    flags2 = buf.read_u32_at(flags2_offset)
    raw_shader_type = buf.read_u32_at(shader_type_offset)
    shader_type = _decode_shader_type(raw_shader_type)
    texture_set_ref = struct.unpack_from("<i", buf._b, texture_set_ref_offset)[0]

    # Type-specific parallax fields (only when shader_type == 3)
    common_end = block_start + _COMMON_FIELDS_SIZE + extra_shift + layout_shift
    pmx_offset: int | None = None
    psc_offset: int | None = None
    pmx_val: float | None = None
    psc_val: float | None = None

    if shader_type == SHADER_TYPE_HEIGHTMAP and block_size >= _COMMON_FIELDS_SIZE + extra_shift + 8:
        pmx_offset = common_end
        psc_offset = common_end + 4
        buf.seek(pmx_offset)
        pmx_val = buf.read_float()
        psc_val = buf.read_float()

    # Additional always-present fields (spec_color, spec_strength, light_eff1)
    spec_color_off = block_start + _OFFSET_SPEC_COLOR + extra_shift + layout_shift
    spec_strength_off = block_start + _OFFSET_SPEC_STRENGTH + extra_shift + layout_shift
    light_eff1_off = block_start + _OFFSET_LIGHT_EFF1 + extra_shift + layout_shift

    spec_r, spec_g, spec_b = 1.0, 1.0, 1.0
    spec_strength_val: float = 1.0
    light_eff1_val: float = 0.3

    if spec_color_off + 12 <= block_end:
        spec_r = struct.unpack_from("<f", buf._b, spec_color_off)[0]
        spec_g = struct.unpack_from("<f", buf._b, spec_color_off + 4)[0]
        spec_b = struct.unpack_from("<f", buf._b, spec_color_off + 8)[0]
    if spec_strength_off + 4 <= block_end:
        spec_strength_val = struct.unpack_from("<f", buf._b, spec_strength_off)[0]
    if light_eff1_off + 4 <= block_end:
        light_eff1_val = struct.unpack_from("<f", buf._b, light_eff1_off)[0]

    # Env map scale: only for SHADER_TYPE_ENVMAP (1), first field after common
    envmap_scale_off: int | None = None
    envmap_scale_val: float | None = None
    if shader_type == SHADER_TYPE_ENVMAP:
        esc_off = common_end + _ENVMAP_SCALE_OFFSET_FROM_COMMON
        if esc_off + 4 <= block_end:
            envmap_scale_off = esc_off
            envmap_scale_val = struct.unpack_from("<f", buf._b, esc_off)[0]

    return _ShaderPropBlock(
        block_index=block_index,
        block_start=block_start,
        num_extra=num_extra,
        flags1_offset=flags1_offset,
        flags2_offset=flags2_offset,
        shader_type_offset=shader_type_offset,
        texture_set_ref_offset=texture_set_ref_offset,
        flags1=flags1,
        flags2=flags2,
        shader_type=shader_type,
        texture_set_ref=texture_set_ref,
        layout_shift=layout_shift,
        common_end_offset=common_end,
        parallax_max_passes_offset=pmx_offset,
        parallax_scale_offset=psc_offset,
        parallax_max_passes=pmx_val,
        parallax_scale=psc_val,
        spec_color_offset=spec_color_off,
        spec_strength_offset=spec_strength_off,
        light_eff1_offset=light_eff1_off,
        spec_color=(spec_r, spec_g, spec_b),
        spec_strength=spec_strength_val,
        light_eff1=light_eff1_val,
        env_map_scale_offset=envmap_scale_off,
        env_map_scale=envmap_scale_val,
    )


def _parse_texture_set(buf: _Buf, block_index: int,
                       block_start: int, block_size: int) -> _TextureSetBlock | None:
    """Parse a BSShaderTextureSet block.

    Tries multiple count-field formats to handle both Skyrim SE (u32 count)
    and Skyrim LE / mixed-export NIFs (u16 count).  In each case the texture
    path strings are u32-prefixed SizedString values.
    """
    block_end = block_start + block_size
    if block_size < 4 or block_end > len(buf._b):
        return None

    def _parse_with_format(shift: int, count_size: int) -> _TextureSetBlock | None:
        """Try to parse with *shift* bytes of leading padding and a count
        field of *count_size* bytes (2 = u16, 4 = u32)."""
        if shift not in (0, 4):
            return None
        if count_size not in (2, 4):
            return None
        if block_start + shift + count_size > block_end:
            return None
        if count_size == 2:
            num_textures = buf.read_u16_at(block_start + shift)
        else:
            num_textures = buf.read_u32_at(block_start + shift)
        if num_textures == 0 or num_textures > 64:
            return None
        buf.seek(block_start + shift + count_size)
        slot_offsets: list[int] = []
        slot_paths: list[str] = []
        for _ in range(num_textures):
            slot_offsets.append(buf.pos)
            if buf.pos + 4 > block_end:
                return None
            n = buf.read_u32()
            if n > 512:
                return None
            if buf.pos + n > block_end:
                return None
            raw = bytes(buf._b[buf.pos: buf.pos + n])
            buf.seek(buf.pos + n)
            slot_paths.append(raw.decode("latin-1"))
        if buf.pos > block_end:
            return None
        return _TextureSetBlock(
            block_index=block_index,
            block_start=block_start,
            count_offset=block_start + shift,
            count_size=count_size,
            num_textures=num_textures,
            slot_offsets=slot_offsets,
            slot_paths=slot_paths,
        )

    # Collect all candidates: (shift × count_size) combinations
    # Prefer SE format (u32) over LE (u16); prefer no shift over shift=4.
    all_formats = [(0, 4), (4, 4), (0, 2), (4, 2)]
    candidates = [c for c in (
        _parse_with_format(sh, cs) for sh, cs in all_formats
    ) if c is not None]
    if not candidates:
        return None

    def _score(ts: _TextureSetBlock) -> int:
        score = 0
        if ts.num_textures > 0:
            score += 3
        if ts.num_textures in (6, 9):  # standard Skyrim slot counts
            score += 3
        elif ts.num_textures >= TEXTURE_SLOT_COUNT:
            score += 2
        non_empty = [p for p in ts.slot_paths if p]
        if non_empty:
            score += 2
        # Reward paths that look like Skyrim-relative texture paths
        if any(p.lower().startswith("textures\\") or p.lower().startswith("textures/")
               for p in non_empty):
            score += 3
        if ts.count_offset == block_start:   # no shift preferred
            score += 1
        if ts.count_size == 4:               # u32 count (SE native) preferred
            score += 1
        return score

    candidates.sort(key=_score, reverse=True)
    return candidates[0]


# ---------------------------------------------------------------------------
# Block-start computation
# ---------------------------------------------------------------------------

def _compute_block_starts(blocks_start: int, block_sizes: list[int]) -> list[int]:
    starts: list[int] = []
    cursor = blocks_start
    for size in block_sizes:
        starts.append(cursor)
        cursor += size
    return starts


# ---------------------------------------------------------------------------
# Shape-block parsing  (skip-condition support)
# ---------------------------------------------------------------------------

#: Block type names that indicate a BSTriShape-family node capable of carrying
#: a shader property and a skin instance.
_SHAPE_BLOCK_TYPES: frozenset[str] = frozenset({
    "BSTriShape",
    "NiTriShape",
    "BSLODTriShape",
    "BSMeshLODTriShape",
    "BSDynamicTriShape",
})

#: Subset of shape block types that are LOD geometry.  Parallax is visible at
#: normal viewing distances on LOD shapes but is absent at the LOD/distant view
#: transition — worth noting to the user but not a hard incompatibility.
_LOD_SHAPE_BLOCK_TYPES: frozenset[str] = frozenset({
    "BSLODTriShape",
    "BSMeshLODTriShape",
})


@dataclass
class _ShapeBlock:
    """Lightweight parsed representation of a BSTriShape-family block."""
    block_index: int
    shader_property_ref: int   # block index; -1 if none
    skin_instance_ref: int     # block index; -1 if none (indicates skinned mesh)
    alpha_property_ref: int    # block index; -1 if none
    block_type: str = ""       # e.g. "BSTriShape", "BSLODTriShape"


def _parse_shape_block(
    data: bytes,
    block_index: int,
    block_start: int,
    block_size: int,
    num_blocks: int,
) -> _ShapeBlock | None:
    """Parse a BSTriShape-family block to extract skin/shader/alpha refs.

    The Skyrim SE binary layout (NIF 20.2.0.7, user_version_2 ∈ {83, 100}):

    NiObjectNET  : name_ref(4) + num_extra(4) + extra_refs(num_extra×4) + controller(4)
    NiAVObject   : flags(2) + transform(52) + collision_ref(4)
    BoundingSphere: 16 bytes
    → skin_instance_ref   (i32)
    → shader_property_ref (i32)
    → alpha_property_ref  (i32)

    Without extra-data refs: fixed prefix = 4+4+4 + 2+52+4 + 16 = 86 bytes.
    """
    block_end = block_start + block_size
    buf = _Buf(data)
    if block_start + _SHAPE_FIXED_PRE_REFS + 4 > block_end:
        return None

    # Read num_extra from NiObjectNET header (offset 4 from block_start)
    if block_start + 8 > block_end:
        return None
    num_extra = struct.unpack_from("<I", data, block_start + 4)[0]
    if num_extra > 512:
        return None

    extra_shift = num_extra * 4
    refs_start = block_start + _SHAPE_FIXED_PRE_REFS + extra_shift
    if refs_start + 12 > block_end:
        return None

    skin_ref = struct.unpack_from("<i", data, refs_start)[0]
    shader_ref = struct.unpack_from("<i", data, refs_start + 4)[0]
    alpha_ref = struct.unpack_from("<i", data, refs_start + 8)[0]

    def _valid(ref: int) -> int:
        """Return ref if it is a valid non-negative block index, else -1."""
        if 0 <= ref < num_blocks:
            return ref
        return -1

    return _ShapeBlock(
        block_index=block_index,
        shader_property_ref=_valid(shader_ref),
        skin_instance_ref=_valid(skin_ref),
        alpha_property_ref=_valid(alpha_ref),
    )


def _build_shape_map(
    data: bytes,
    header: _NifHeader,
) -> tuple[dict[int, _ShapeBlock], bool]:
    """Build a mapping of ``shader_property_ref → _ShapeBlock``.

    Also returns *has_havok*: ``True`` if any block is a
    ``BSBehaviorGraphExtraData`` node (indicating a Havok-animated NIF where
    parallax would cause CTD).

    Returns ``(shader_to_shape, has_havok)``.
    """
    block_starts = _compute_block_starts(header.blocks_start, header.block_sizes)
    shader_to_shape: dict[int, _ShapeBlock] = {}
    has_havok = False

    for bi, type_idx in enumerate(header.block_type_indices):
        btype = header.block_types[type_idx] if type_idx < len(header.block_types) else ""

        if "BSBehaviorGraphExtraData" in btype:
            has_havok = True

        if any(st in btype for st in _SHAPE_BLOCK_TYPES):
            bstart = block_starts[bi]
            bsize = header.block_sizes[bi]
            shape = _parse_shape_block(data, bi, bstart, bsize, header.num_blocks)
            if shape is not None and shape.shader_property_ref >= 0:
                shape.block_type = btype
                shader_to_shape[shape.shader_property_ref] = shape

    return shader_to_shape, has_havok


def _shape_block_type_names(header: _NifHeader) -> set[str]:
    """Return the set of block type names present in *header*."""
    return {header.block_types[i] for i in header.block_type_indices
            if i < len(header.block_types)}


# ---------------------------------------------------------------------------
# Core patch engine
# ---------------------------------------------------------------------------

def _normalise_path(p: str) -> str:
    normalized = p.strip().strip("\"").replace("/", "\\")
    if not normalized:
        return ""
    lowered = normalized.lower()
    if lowered.startswith(".\\"):
        normalized = normalized[2:]
        lowered = normalized.lower()
    if lowered.startswith("data\\textures\\"):
        normalized = "textures\\" + normalized[len("data\\textures\\"):]
        lowered = normalized.lower()
    elif lowered.startswith("data\\texture\\"):
        normalized = "textures\\" + normalized[len("data\\texture\\"):]
        lowered = normalized.lower()
    if lowered.startswith("textures\\"):
        marker_index = 0
    elif lowered.startswith("texture\\"):
        normalized = "textures\\" + normalized[len("texture\\"):]
        lowered = normalized.lower()
        marker_index = 0
    else:
        marker_plural = "\\textures\\"
        marker_singular = "\\texture\\"
        marker_index = max(lowered.rfind(marker_plural), lowered.rfind(marker_singular))
        if marker_index != -1:
            marker_len = len(marker_plural) if lowered.startswith(marker_plural, marker_index) else len(marker_singular)
            normalized = "textures\\" + normalized[marker_index + marker_len:]
            lowered = normalized.lower()
            marker_index = 0
    if marker_index == -1:
        marker_index = lowered.find("textures\\")
        if marker_index == -1:
            marker_index = lowered.find("texture\\")
            marker_len = len("texture\\")
        else:
            marker_len = len("textures\\")
        if marker_index > 0:
            normalized = "textures\\" + normalized[marker_index + marker_len:]
            lowered = normalized.lower()
    parts: list[str] = []
    for part in normalized.split("\\"):
        segment = part.strip()
        if not segment or segment == ".":
            continue
        if segment == "..":
            if len(parts) > 1 and parts[-1] != "..":
                parts.pop()
            continue
        parts.append(segment)
    collapsed = "\\".join(parts).lstrip("\\")
    lowered_collapsed = collapsed.lower()
    if lowered_collapsed.startswith("texture\\"):
        collapsed = "textures\\" + collapsed[len("texture\\"):]
        lowered_collapsed = collapsed.lower()
    elif lowered_collapsed == "texture":
        collapsed = "textures"
        lowered_collapsed = "textures"
    if lowered_collapsed.startswith("textures\\"):
        tail = collapsed[len("textures\\"):]
        while tail.lower().startswith("textures\\"):
            tail = tail[len("textures\\"):]
        while tail.lower().startswith("texture\\"):
            tail = tail[len("texture\\"):]
        return f"textures\\{tail}" if tail else "textures"
    raise ValueError(
        f"Invalid texture path '{p}'. Expected a Skyrim-relative path under 'textures\\'."
    )


def _normalise_slot_path(path: str) -> str:
    return path.strip().lower().replace("/", "\\")


def _shader_type_mesh_hint(shader_type: int) -> str | None:
    """Return a concise mesh-context hint for a shader type."""
    if shader_type == SHADER_TYPE_FACE_TINT:
        return "Face Tint (4) is for head/face tint materials; parallax workflows are generally incompatible."
    if shader_type == SHADER_TYPE_SKIN_TINT:
        return "Skin Tint (5) is for skin/body tint materials; parallax and complex material flags are usually unsafe."
    if shader_type == SHADER_TYPE_HAIR_TINT:
        return "Hair Tint (6) is a dedicated hair shader path; parallax/complex workflows are not typically used."
    if shader_type == SHADER_TYPE_GLOW:
        return "Glow (2) shader type is emissive-focused and is not a standard parallax mesh workflow."
    if shader_type == SHADER_TYPE_PARALLAX_OCC:
        return "Parallax Occlusion (7) is uncommon; most Skyrim setups use Heightmap (3) + flags instead."
    if shader_type == SHADER_TYPE_MULTILAYER:
        return "MultiLayer Parallax (11) is a specialized landscape/terrain-oriented path."
    return None


def _path_has_dds_extension(path: str) -> bool:
    normalized = _normalise_slot_path(path)
    return normalized.endswith(".dds")


def _build_block_map(
    data: bytes,
    header: _NifHeader,
) -> tuple[list[_ShaderPropBlock], dict[int, _TextureSetBlock], list[str]]:
    """Return (shader_props, texture_sets, errors)."""
    block_starts = _compute_block_starts(header.blocks_start, header.block_sizes)
    shader_props: list[_ShaderPropBlock] = []
    texture_sets: dict[int, _TextureSetBlock] = {}
    errors: list[str] = []

    for bi, type_idx in enumerate(header.block_type_indices):
        btype = header.block_types[type_idx] if type_idx < len(header.block_types) else ""
        bstart = block_starts[bi]
        bsize = header.block_sizes[bi]
        buf = _Buf(data)

        if btype == "BSLightingShaderProperty":
            try:
                sp = _parse_shader_prop(buf, bi, bstart, bsize, header.num_blocks)
                if sp is not None:
                    shader_props.append(sp)
                else:
                    errors.append(f"Block {bi}: failed to parse BSLightingShaderProperty.")
            except (ValueError, struct.error, IndexError) as exc:
                errors.append(f"Block {bi}: shader parse error: {exc}")
        elif btype == "BSShaderTextureSet":
            try:
                ts = _parse_texture_set(buf, bi, bstart, bsize)
                if ts is not None:
                    texture_sets[bi] = ts
                else:
                    errors.append(f"Block {bi}: failed to parse BSShaderTextureSet.")
            except (ValueError, struct.error, IndexError) as exc:
                errors.append(f"Block {bi}: texture-set parse error: {exc}")

    return shader_props, texture_sets, errors


def _upgrade_block_to_type3(
    data: bytes,
    header: _NifHeader,
    sp: _ShaderPropBlock,
    parallax_scale: float,
    block_index: int,
) -> bytes:
    """Expand a shader-type-0 block to shader-type-3 by inserting the
    ``Parallax Max Passes`` and ``Parallax Scale`` float fields.

    Procedure
    ---------
    1. Change ``shader_type`` to 3 (Heightmap) in the existing data.
    2. Insert 8 bytes at the end of the common-fields section:
       [max_passes (float32)] [scale (float32)].
    3. Update the block size in the header.

    Returns the modified data bytes.
    """
    buf = _Buf(data)

    # Safety guard: block must be exactly the expected type-0 size before
    # inserting the type-3 extra fields.  If the stored block size does not
    # match the layout we parsed, the common_end_offset would be wrong and
    # the byte-insert would corrupt the adjacent block.
    bsize_field_offset = header.block_sizes_offset + block_index * 4
    recorded_size = buf.read_u32_at(bsize_field_offset)
    expected_type0_size = _COMMON_FIELDS_SIZE + sp.num_extra * 4
    if recorded_size != expected_type0_size:
        raise ValueError(
            f"Block {block_index}: recorded block size {recorded_size} does not match "
            f"expected type-0 size {expected_type0_size}; refusing to insert type-3 "
            f"fields to avoid corrupting the NIF."
        )

    # 1. Write new shader_type = 3
    buf.write_u32_at(sp.shader_type_offset, SHADER_TYPE_HEIGHTMAP)

    # 2. Insert the two type-3 floats right after the common fields
    insert_offset = sp.common_end_offset
    new_fields = struct.pack("<ff", _DEFAULT_PARALLAX_MAX_PASSES, parallax_scale)
    buf.insert_bytes_at(insert_offset, new_fields)

    # 3. Update block size in header
    buf.write_u32_at(bsize_field_offset, recorded_size + 8)

    return buf.to_bytes()


def _should_enable_parallax_on_shader(
    sp: _ShaderPropBlock,
    *,
    effective_parallax: bool,
    opts: NifPatchOptions,
    shader_to_shape: dict[int, _ShapeBlock],
    has_havok: bool,
) -> bool:
    if not effective_parallax or opts.disable_parallax:
        return False
    if has_havok and opts.skip_if_havok:
        return False
    if opts.skip_incompatible_shader_types and sp.shader_type not in (
        SHADER_TYPE_DEFAULT,
        SHADER_TYPE_HEIGHTMAP,
        SHADER_TYPE_ENVMAP,
    ):
        return False
    if opts.skip_decal and ((sp.flags1 & SLSF1_DECAL) or (sp.flags1 & SLSF1_DYNAMIC_DECAL)):
        return False
    if opts.skip_lighting_effects and (
        (sp.flags2 & SLSF2_SOFT_LIGHTING)
        or (sp.flags2 & SLSF2_RIM_LIGHTING)
        or (sp.flags2 & SLSF2_BACK_LIGHTING)
    ):
        return False
    if opts.skip_anisotropic and (sp.flags2 & SLSF2_ANISOTROPIC_LIGHTING):
        return False
    shape = shader_to_shape.get(sp.block_index)
    if opts.skip_if_skinned and shape is not None and shape.skin_instance_ref >= 0:
        return False
    if opts.skip_if_alpha and shape is not None and shape.alpha_property_ref >= 0:
        return False
    return True


def _apply_patches(
    data: bytes,
    header: _NifHeader,
    shader_props: list[_ShaderPropBlock],
    texture_sets: dict[int, _TextureSetBlock],
    opts: NifPatchOptions,
) -> tuple[bytes, int, int, int]:
    """Return (new_data, props_patched, sets_patched, blocks_upgraded)."""
    upgraded = 0
    effective_parallax = opts.enable_parallax or opts.enable_pom
    want_scale = opts.parallax_scale is not None and opts.parallax_scale > 0
    shader_to_shape: dict[int, _ShapeBlock] = {}
    has_havok = False
    need_shape_map = (
        opts.skip_if_havok
        or opts.skip_if_skinned
        or opts.skip_if_alpha
        or (opts.force_shader_type_3 and effective_parallax and want_scale)
    )
    if need_shape_map:
        shader_to_shape, has_havok = _build_shape_map(data, header)

    # --- Phase 1: block upgrades (type 0 → 3) ------------------------------
    if opts.force_shader_type_3 and effective_parallax and want_scale:
        # Reparse after EACH upgrade because each byte-insert shifts the
        # offsets of every block that follows it in the file.  Using stale
        # offsets from a previous parse pass would corrupt the NIF when
        # there are two or more type-0 shader blocks to upgrade.
        while True:
            buf_check = _Buf(data)
            chk_header = _read_header(buf_check)
            if chk_header is None:
                raise RuntimeError("Header corrupted after type-3 upgrade.")
            fresh_props, _, _ = _build_block_map(data, chk_header)
            sp_to_upgrade = next(
                (
                    sp
                    for sp in fresh_props
                    if sp.shader_type == SHADER_TYPE_DEFAULT
                    and _should_enable_parallax_on_shader(
                        sp,
                        effective_parallax=effective_parallax,
                        opts=opts,
                        shader_to_shape=shader_to_shape,
                        has_havok=has_havok,
                    )
                ),
                None,
            )
            if sp_to_upgrade is None:
                break
            data = _upgrade_block_to_type3(
                data, chk_header, sp_to_upgrade,
                opts.parallax_scale or _DEFAULT_PARALLAX_SCALE,
                sp_to_upgrade.block_index,
            )
            upgraded += 1
        if upgraded:
            # Final reparse to give phase 2 fresh offsets.
            buf = _Buf(data)
            new_header = _read_header(buf)
            if new_header is None:
                raise RuntimeError("Header corrupted after type-3 upgrade.")
            shader_props, texture_sets, _ = _build_block_map(data, new_header)
            header = new_header

    # --- Phase 2: in-place flag + parallax scale + field patches --------------
    buf = _Buf(data)
    props_patched = 0

    # NIFs with Havok-animated skeletons must not receive parallax — doing so
    # causes an EXCEPTION_ACCESS_VIOLATION crash at runtime.
    if has_havok and opts.skip_if_havok and effective_parallax:
        # Clear effective_parallax so Phase 2 won't enable parallax flags,
        # but still allow disable/env/glow operations.
        effective_parallax = False

    for sp in shader_props:
        new_flags1 = sp.flags1
        new_flags2 = sp.flags2

        # ---- Determine whether parallax is safe to enable on this block ----
        enabling_parallax = _should_enable_parallax_on_shader(
            sp,
            effective_parallax=effective_parallax,
            opts=opts,
            shader_to_shape=shader_to_shape,
            has_havok=has_havok,
        )

        # ---- Apply flag changes ----
        if enabling_parallax:
            new_flags1 |= SLSF1_PARALLAX
            new_flags2 &= ~SLSF2_MULTI_LAYER_PARALLAX
            # Vertex colours must be set for parallax meshes to render correctly
            new_flags2 |= SLSF2_VERTEX_COLORS
        if opts.enable_pom and enabling_parallax:
            new_flags1 |= SLSF1_PARALLAX_OCCLUSION
        if opts.enable_env_mapping:
            new_flags1 |= SLSF1_ENVIRONMENT_MAPPING
            new_flags2 &= ~SLSF2_MULTI_LAYER_PARALLAX
        if opts.enable_glow_map:
            new_flags2 |= SLSF2_GLOW_MAP
        if opts.enable_pbr:
            new_flags2 |= SLSF2_UNUSED01
        if opts.disable_parallax:
            new_flags1 &= ~SLSF1_PARALLAX
            new_flags1 &= ~SLSF1_PARALLAX_OCCLUSION
        elif opts.disable_pom:
            new_flags1 &= ~SLSF1_PARALLAX_OCCLUSION
        if opts.disable_env_mapping:
            new_flags1 &= ~SLSF1_ENVIRONMENT_MAPPING
        if opts.disable_glow_map:
            new_flags2 &= ~SLSF2_GLOW_MAP
        if opts.disable_pbr:
            new_flags2 &= ~SLSF2_UNUSED01

        flags_changed = (new_flags1 != sp.flags1) or (new_flags2 != sp.flags2)
        if flags_changed:
            buf.write_u32_at(sp.flags1_offset, new_flags1)
            buf.write_u32_at(sp.flags2_offset, new_flags2)
            props_patched += 1

        # Parallax scale — only when block is (now) type 3
        if want_scale and sp.shader_type in (SHADER_TYPE_HEIGHTMAP, SHADER_TYPE_PARALLAX_OCC):
            if sp.parallax_scale_offset is not None:
                new_scale = opts.parallax_scale or _DEFAULT_PARALLAX_SCALE
                if sp.parallax_scale is None or abs(sp.parallax_scale - new_scale) > 1e-5:
                    buf.write_float_at(sp.parallax_scale_offset, float(new_scale))
                    if not flags_changed:
                        props_patched += 1

        # ---- Fix mesh lighting (ENB pre-patcher) ----
        if opts.fix_mesh_lighting:
            if sp.light_eff1_offset and sp.light_eff1 > 0.6:
                buf.write_float_at(sp.light_eff1_offset, 0.6)
                if not flags_changed:
                    props_patched += 1

        # ---- Spec color, spec strength, env map scale patches ----
        if opts.spec_color is not None:
            r, g, b = opts.spec_color
            if sp.spec_color_offset and sp.spec_color_offset + 12 <= len(data):
                buf.write_float_at(sp.spec_color_offset, float(r))
                buf.write_float_at(sp.spec_color_offset + 4, float(g))
                buf.write_float_at(sp.spec_color_offset + 8, float(b))
                if not flags_changed:
                    props_patched += 1

        if opts.spec_strength is not None:
            if sp.spec_strength_offset and sp.spec_strength_offset + 4 <= len(data):
                buf.write_float_at(sp.spec_strength_offset, float(opts.spec_strength))
                if not flags_changed:
                    props_patched += 1

        if opts.env_map_scale is not None:
            if sp.env_map_scale_offset is not None and sp.env_map_scale_offset + 4 <= len(data):
                buf.write_float_at(sp.env_map_scale_offset, float(opts.env_map_scale))
                if not flags_changed:
                    props_patched += 1

    data = buf.to_bytes()

    # --- Phase 3: texture path patches (may change data length) -------------
    sets_patched = 0

    def _extend_texture_set_slots(
        current_data: bytes,
        current_header: _NifHeader,
        texture_set: _TextureSetBlock,
        target_slot: int,
    ) -> tuple[bytes, _NifHeader, dict[int, _TextureSetBlock], bool]:
        if target_slot < texture_set.num_textures:
            return current_data, current_header, texture_sets, False
        needed = (target_slot + 1) - texture_set.num_textures
        if needed <= 0:
            return current_data, current_header, texture_sets, False

        block_end = texture_set.block_start + current_header.block_sizes[texture_set.block_index]
        buf_local = _Buf(current_data)
        buf_local.insert_bytes_at(block_end, b"\x00\x00\x00\x00" * needed)
        new_count = texture_set.num_textures + needed
        if texture_set.count_size == 2:
            buf_local.write_u16_at(texture_set.count_offset, new_count)
        else:
            buf_local.write_u32_at(texture_set.count_offset, new_count)
        ts_bsize_off = current_header.block_sizes_offset + texture_set.block_index * 4
        old_ts_size = buf_local.read_u32_at(ts_bsize_off)
        buf_local.write_u32_at(ts_bsize_off, old_ts_size + (needed * 4))
        new_data_local = buf_local.to_bytes()

        new_header_local = _read_header(_Buf(new_data_local))
        if new_header_local is None:
            raise RuntimeError("Header corrupted after texture slot extension.")
        _, new_texture_sets, _ = _build_block_map(new_data_local, new_header_local)
        return new_data_local, new_header_local, new_texture_sets, True

    for sp in shader_props:
        ts = texture_sets.get(sp.texture_set_ref)
        if ts is None:
            continue

        requested_slots: list[int] = []
        if opts.parallax_texture_path:
            requested_slots.append(TEXTURE_SLOT_PARALLAX)
        if opts.normal_texture_path:
            requested_slots.append(TEXTURE_SLOT_NORMAL)
        if opts.env_mask_texture_path:
            requested_slots.append(TEXTURE_SLOT_ENV_MASK)
        if opts.cubemap_texture_path:
            requested_slots.append(TEXTURE_SLOT_CUBEMAP)
        if opts.glow_texture_path:
            requested_slots.append(TEXTURE_SLOT_GLOW)
        if opts.diffuse_texture_path:
            requested_slots.append(TEXTURE_SLOT_DIFFUSE)
        if requested_slots:
            max_slot = max(requested_slots)
            if ts.num_textures < TEXTURE_SLOT_COUNT:
                max_slot = max(max_slot, TEXTURE_SLOT_COUNT - 1)
            data, header, texture_sets, extended = _extend_texture_set_slots(data, header, ts, max_slot)
            if extended:
                sets_patched += 1
                ts = texture_sets.get(sp.texture_set_ref)
                if ts is None:
                    continue

        # (slot_index, old_path, new_path) triples for paths that need changing
        slot_changes: list[tuple[int, str, str]] = []

        def _want(slot: int, new_raw: str | None, *, clear: bool = False) -> None:
            if new_raw is None and not clear:
                return
            if slot >= ts.num_textures:
                return
            new = "" if clear else _normalise_path(new_raw or "")
            old = ts.slot_paths[slot]
            if old != new:
                slot_changes.append((slot, old, new))

        _want(TEXTURE_SLOT_PARALLAX, opts.parallax_texture_path)
        if opts.clear_parallax_texture_path:
            _want(TEXTURE_SLOT_PARALLAX, None, clear=True)
        _want(TEXTURE_SLOT_NORMAL, opts.normal_texture_path)
        if opts.clear_normal_texture_path:
            _want(TEXTURE_SLOT_NORMAL, None, clear=True)
        _want(TEXTURE_SLOT_GLOW, opts.glow_texture_path)
        if opts.clear_glow_texture_path:
            _want(TEXTURE_SLOT_GLOW, None, clear=True)
        _want(TEXTURE_SLOT_DIFFUSE, opts.diffuse_texture_path)
        if opts.clear_diffuse_texture_path:
            _want(TEXTURE_SLOT_DIFFUSE, None, clear=True)
        _want(TEXTURE_SLOT_ENV_MASK, opts.env_mask_texture_path)
        _want(TEXTURE_SLOT_CUBEMAP, opts.cubemap_texture_path)
        if opts.clear_env_mask_texture_path:
            _want(TEXTURE_SLOT_ENV_MASK, None, clear=True)
        if opts.clear_cubemap_texture_path:
            _want(TEXTURE_SLOT_CUBEMAP, None, clear=True)

        if not slot_changes:
            continue

        # Sort high-offset-first so earlier offsets remain valid during replacement.
        indexed: list[tuple[int, int, str, str]] = []  # (file_offset, slot, old, new)
        for slot_idx, old_path, new_path in slot_changes:
            indexed.append((ts.slot_offsets[slot_idx], slot_idx, old_path, new_path))
        indexed.sort(key=lambda x: x[0], reverse=True)

        buf2 = _Buf(data)
        size_delta = 0
        for file_offset, _slot_idx, old_path, new_path in indexed:
            buf2.replace_sstring_u32_at(file_offset, old_path, new_path)
            size_delta += (
                len(new_path.encode("latin-1")) - len(old_path.encode("latin-1"))
            )

        # Update the BSShaderTextureSet block size in the header so subsequent
        # reparsing computes correct block starts for all following blocks.
        ts_bsize_off = header.block_sizes_offset + ts.block_index * 4
        old_ts_size = buf2.read_u32_at(ts_bsize_off)
        buf2.write_u32_at(ts_bsize_off, old_ts_size + size_delta)

        data = buf2.to_bytes()

        # Reparse so subsequent shader props (if any) get fresh offsets.
        buf3 = _Buf(data)
        new_header = _read_header(buf3)
        if new_header:
            _, texture_sets, _ = _build_block_map(data, new_header)
            header = new_header
        sets_patched += 1

    return data, props_patched, sets_patched, upgraded


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def patch_nif(nif_path: Path, opts: NifPatchOptions) -> NifPatchResult:
    """Patch a Skyrim SE NIF file.

    Always returns a :class:`NifPatchResult`; never raises.
    """
    result = NifPatchResult(nif_path=nif_path, success=False)

    effective_parallax = opts.enable_parallax or opts.enable_pom
    has_any_toggle = any(
        (
            effective_parallax,
            opts.parallax_scale is not None and opts.parallax_scale > 0,
            opts.enable_env_mapping,
            opts.enable_glow_map,
            opts.enable_pbr,
            opts.normal_texture_path is not None,
            opts.parallax_texture_path is not None,
            opts.glow_texture_path is not None,
            opts.diffuse_texture_path is not None,
            opts.env_mask_texture_path is not None,
            opts.cubemap_texture_path is not None,
            opts.disable_parallax,
            opts.disable_pom,
            opts.disable_env_mapping,
            opts.disable_glow_map,
            opts.disable_pbr,
            opts.clear_parallax_texture_path,
            opts.clear_normal_texture_path,
            opts.clear_glow_texture_path,
            opts.clear_diffuse_texture_path,
            opts.clear_env_mask_texture_path,
            opts.clear_cubemap_texture_path,
            opts.fix_mesh_lighting,
            opts.spec_strength is not None,
            opts.spec_color is not None,
            opts.env_map_scale is not None,
        )
    )
    if not has_any_toggle:
        result.message = "Nothing to patch — all options are disabled."
        return result

    for field_name, path_value in (
        ("parallax_texture_path", opts.parallax_texture_path),
        ("normal_texture_path", opts.normal_texture_path),
        ("glow_texture_path", opts.glow_texture_path),
        ("diffuse_texture_path", opts.diffuse_texture_path),
        ("env_mask_texture_path", opts.env_mask_texture_path),
        ("cubemap_texture_path", opts.cubemap_texture_path),
    ):
        if path_value is not None and not path_value.strip():
            result.errors.append(f"{field_name} cannot be empty or whitespace-only.")
            result.message = f"Invalid {field_name}."
            return result

    try:
        original_data = nif_path.read_bytes()
    except OSError as exc:
        result.errors.append(f"Cannot read: {exc}")
        result.message = str(exc)
        return result

    buf = _Buf(original_data)
    try:
        header = _read_header(buf)
    except (ValueError, struct.error, IndexError) as exc:
        header_diagnostics = _diagnose_header_parse_failure(original_data, exc)
        result.errors.extend(header_diagnostics)
        result.message = header_diagnostics[0]
        return result
    if header is None:
        header_diagnostics = _diagnose_header_parse_failure(
            original_data, ValueError("Unsupported Skyrim NIF header values")
        )
        result.errors.extend(header_diagnostics)
        result.message = header_diagnostics[0]
        return result

    shader_props, texture_sets, parse_errors = _build_block_map(original_data, header)
    result.errors.extend(parse_errors)
    result.shader_properties_found = len(shader_props)

    if not shader_props:
        if parse_errors:
            result.message = f"No patchable BSLightingShaderProperty blocks found ({parse_errors[0]})."
        else:
            result.message = "No BSLightingShaderProperty blocks found — nothing to patch."
        extra_hints = _summarize_non_patchable_block_types(header)
        if extra_hints:
            result.errors.extend(extra_hints)
            result.message = f"{result.message} {extra_hints[0]}"
        return result

    try:
        new_data, props_patched, sets_patched, upgraded = _apply_patches(
            original_data, header, shader_props, texture_sets, opts
        )
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"Patch error: {exc}")
        result.message = str(exc)
        return result

    result.shader_properties_patched = props_patched
    result.texture_sets_patched = sets_patched
    result.blocks_upgraded_to_type3 = upgraded

    if new_data == original_data and upgraded == 0:
        result.success = True
        result.already_up_to_date = True
        result.message = "Already up-to-date — no changes needed."
        return result

    if opts.dry_run:
        result.success = True
        result.message = (
            f"[DRY RUN] Would patch {props_patched} shader(s), "
            f"{sets_patched} texture set(s), upgrade {upgraded} block(s) to type 3."
        )
        return result

    if opts.backup:
        backup_path = nif_path.with_suffix(".nif.bak")
        if backup_path.exists():
            result.warnings.append(
                f"Backup skipped — '{backup_path.name}' already exists. "
                "Delete or rename it first to create a fresh backup."
            )
        else:
            try:
                backup_path.write_bytes(original_data)
                result.backup_path = backup_path
            except OSError as exc:
                result.errors.append(f"Backup failed (continuing): {exc}")

    try:
        nif_path.write_bytes(new_data)
    except OSError as exc:
        result.errors.append(f"Cannot write patched file: {exc}")
        result.message = str(exc)
        return result

    result.success = True
    parts: list[str] = []
    if props_patched:
        parts.append(f"{props_patched} shader{'s' if props_patched != 1 else ''} patched")
    if sets_patched:
        parts.append(f"{sets_patched} texture set{'s' if sets_patched != 1 else ''} updated")
    if upgraded:
        parts.append(f"{upgraded} block{'s' if upgraded != 1 else ''} upgraded to type 3")
    result.message = "; ".join(parts) + "." if parts else "Patched."
    return result


def scan_nif_diagnostics(nif_path: Path) -> tuple[list[NifShaderInfo], list[str]]:
    """Return ``(shader_infos, diagnostics)`` for every shader block in the NIF."""
    diagnostics: list[str] = []
    try:
        data = nif_path.read_bytes()
    except OSError as exc:
        return [], [f"Cannot read NIF: {exc}"]
    buf = _Buf(data)
    try:
        header = _read_header(buf)
    except (ValueError, struct.error, IndexError) as exc:
        return [], _diagnose_header_parse_failure(data, exc)
    if header is None:
        return [], _diagnose_header_parse_failure(
            data, ValueError("Unsupported Skyrim NIF header values")
        )
    shader_props, texture_sets, parse_errors = _build_block_map(data, header)
    diagnostics.extend(parse_errors)
    if not shader_props:
        diagnostics.extend(_summarize_non_patchable_block_types(header))

    # Build shape map so we can annotate each NifShaderInfo with its mesh context.
    shader_to_shape, _has_havok = _build_shape_map(data, header)

    results: list[NifShaderInfo] = []
    for sp in shader_props:
        ts = texture_sets.get(sp.texture_set_ref)
        tex_paths: dict[int, str] = {}
        if ts:
            for i, p in enumerate(ts.slot_paths):
                if p:
                    tex_paths[i] = p
        shape = shader_to_shape.get(sp.block_index)
        info = NifShaderInfo(
            block_index=sp.block_index,
            shader_type=sp.shader_type,
            flags1=sp.flags1,
            flags2=sp.flags2,
            parallax_scale=sp.parallax_scale,
            texture_paths=tex_paths,
        )
        if shape is not None:
            info.parent_block_type = shape.block_type
            info.is_lod_shape = shape.block_type in _LOD_SHAPE_BLOCK_TYPES
            info.is_skinned = shape.skin_instance_ref >= 0
            info.has_alpha_property = shape.alpha_property_ref >= 0
        results.append(info)
    return results, diagnostics


def scan_nif(nif_path: Path) -> list[NifShaderInfo]:
    """Return a list of :class:`NifShaderInfo` for every shader block in the NIF."""
    infos, _diagnostics = scan_nif_diagnostics(nif_path)
    return infos


def _renderer_compatibility(info: NifShaderInfo) -> dict[str, list[str]]:
    """Return per-renderer compatibility notes for a single shader block.

    Keys are ``'vanilla'``, ``'enb'``, ``'community_shaders'``, and
    ``'truepbr'``.  Each value is a list of human-readable status strings that
    describe what is already correct and what needs to change for that renderer.
    """
    notes: dict[str, list[str]] = {
        "vanilla": [],
        "enb": [],
        "community_shaders": [],
        "truepbr": [],
    }
    bname = f"Block {info.block_index}"
    mesh_hint = _shader_type_mesh_hint(info.shader_type)
    if mesh_hint:
        for renderer_notes in notes.values():
            renderer_notes.append(f"{bname}: mesh-type note — {mesh_hint}")

    # ------------------------------------------------------------------ vanilla
    # Requirements: shader_type Default(0) or Heightmap(3),
    #               SLSF1_Parallax flag set, _p.dds in slot 3.
    v = notes["vanilla"]
    if info.shader_type not in (SHADER_TYPE_DEFAULT, SHADER_TYPE_HEIGHTMAP):
        v.append(
            f"{bname}: shader type is {info.shader_type_name!r}; vanilla parallax "
            f"requires Default (0) or Heightmap/Parallax (3)"
        )
    else:
        v.append(f"{bname}: shader type {info.shader_type_name!r} ✓")
    if not info.has_parallax_flag:
        v.append(f"{bname}: SLSF1_Parallax flag not set — must be enabled for vanilla parallax")
    else:
        v.append(f"{bname}: SLSF1_Parallax flag ✓")
    slot3 = info.texture_paths.get(3, "")
    if not slot3:
        v.append(f"{bname}: slot 3 (height-map) is empty — needs a _p.dds parallax texture")
    elif not slot3.lower().endswith("_p.dds"):
        v.append(f"{bname}: slot 3 is {slot3!r}; expected a _p.dds height-map texture")
    else:
        v.append(f"{bname}: slot 3 {slot3!r} ✓")
    if info.has_model_space_normals_flag:
        v.append(
            f"{bname}: SLSF1_Model_Space_Normals is set; vanilla parallax requires "
            f"tangent-space normals (clear this flag)"
        )
    if info.has_decal_flag:
        v.append(f"{bname}: decal flag set — parallax + decal causes glitches in-game")
    if info.has_landscape_flag:
        v.append(f"{bname}: landscape shader — vanilla parallax flags have no effect here")
    if info.is_skinned:
        v.append(f"{bname}: skinned/animated mesh — parallax will crash or glitch in-game")
    if info.has_alpha_property:
        v.append(f"{bname}: alpha-blended/tested mesh — parallax + alpha causes glitches")
    if info.is_lod_shape:
        v.append(f"{bname}: LOD geometry — parallax works at normal distance but absent at LOD transition")
    if info.has_pom_flag:
        v.append(f"{bname}: SLSF1_Parallax_Occlusion (POM) requires ENB or Community Shaders; vanilla ignores it")

    # ------------------------------------------------------------------ enb complex material
    e = notes["enb"]
    if info.shader_type != SHADER_TYPE_ENVMAP:
        e.append(
            f"{bname}: ENB Complex Material requires shader type EnvMap (1), "
            f"currently {info.shader_type_name!r}"
        )
    else:
        e.append(f"{bname}: shader type EnvMap ✓")
    if not info.has_env_mapping_flag:
        e.append(f"{bname}: SLSF1_Environment_Mapping not set — required for ENB Complex Material")
    else:
        e.append(f"{bname}: SLSF1_Environment_Mapping ✓")
    if not info.has_model_space_normals_flag:
        e.append(f"{bname}: SLSF1_Model_Space_Normals not set — required for ENB _msn workflow")
    else:
        e.append(f"{bname}: SLSF1_Model_Space_Normals ✓")
    slot1 = info.texture_paths.get(1, "")
    if not (slot1.lower().endswith("_msn.dds") or slot1.lower().endswith("_n.dds")):
        e.append(f"{bname}: slot 1 normal should be _msn.dds (model-space) for ENB Complex Material, got {slot1!r}")
    else:
        e.append(f"{bname}: slot 1 normal {slot1!r} ✓")
    slot5 = info.texture_paths.get(5, "")
    slot5_normalized = slot5.lower()
    if not slot5:
        e.append(f"{bname}: slot 5 (env-mask) is empty — ENB Complex Material needs an _m.dds map")
    elif slot5_normalized.endswith(("_m.dds", "_mask.dds", "_envmask.dds")):
        e.append(f"{bname}: slot 5 env-mask {slot5!r} ✓")
    elif slot5_normalized.endswith(_TRUEPBR_ENV_MASK_SUFFIXES + _LEGACY_TRUEPBR_ENV_MASK_SUFFIXES):
        e.append(f"{bname}: slot 5 (env-mask) needs a _m.dds complex material map, got {slot5!r}")
        e.append(
            f"{bname}: slot 5 looks TruePBR/generic-packed ({slot5!r}); ENB Complex Material expects ENB-style _m.dds data."
        )
    elif slot5_normalized.endswith(("_cm.dds", "_c.dds")):
        e.append(
            f"{bname}: slot 5 uses Community Shaders Extended Materials naming ({slot5!r}); ENB expects _m.dds."
        )
    else:
        e.append(f"{bname}: slot 5 (env-mask) needs a _m.dds complex material map, got {slot5!r}")
    if info.has_parallax_flag:
        e.append(
            f"{bname}: SLSF1_Parallax flag set — for pure ENB Complex Material, clear this; "
            f"set SLSF1_Parallax_Occlusion for POM inside ENB instead"
        )
    if info.has_pom_flag:
        e.append(f"{bname}: SLSF1_Parallax_Occlusion (POM) enabled ✓ (ENB will render POM)")

    # ------------------------------------------------------------------ community shaders
    cs = notes["community_shaders"]
    if info.shader_type not in (SHADER_TYPE_DEFAULT, SHADER_TYPE_HEIGHTMAP):
        cs.append(
            f"{bname}: Community Shaders standard parallax requires Default (0) or "
            f"Heightmap (3) shader type, currently {info.shader_type_name!r}"
        )
    else:
        cs.append(f"{bname}: shader type {info.shader_type_name!r} ✓")
    if not info.has_parallax_flag:
        cs.append(f"{bname}: SLSF1_Parallax not set — must be enabled")
    else:
        cs.append(f"{bname}: SLSF1_Parallax ✓")
    if not slot3:
        cs.append(f"{bname}: slot 3 height-map is empty — needs _p.dds")
    elif not slot3.lower().endswith("_p.dds"):
        cs.append(f"{bname}: slot 3 {slot3!r} — expected _p.dds")
    else:
        cs.append(f"{bname}: slot 3 {slot3!r} ✓")
    if info.has_pom_flag:
        cs.append(
            f"{bname}: SLSF1_Parallax_Occlusion set — Community Shaders handles POM "
            f"internally; this flag is unnecessary but harmless"
        )
    cpm_slot5 = info.texture_paths.get(5, "")
    cpm_slot5_normalized = cpm_slot5.lower()
    if cpm_slot5_normalized.endswith(("_cm.dds", "_c.dds")):
        cs.append(f"{bname}: slot 5 CM map {cpm_slot5!r} detected — Community Shaders Extended Materials active ✓")
    elif cpm_slot5_normalized.endswith(("_m.dds", "_mask.dds", "_envmask.dds")):
        cs.append(
            f"{bname}: slot 5 uses ENB/vanilla-style _m naming ({cpm_slot5!r}); CS Extended Materials expects _cm/_c."
        )
    if info.has_pbr_flag:
        cs.append(f"{bname}: SLSF2_Unused01 (TruePBR) flag present — this overrides standard parallax in CS")
    if info.is_skinned:
        cs.append(f"{bname}: skinned mesh — parallax still not safe on animated geometry even with CS")
    if info.has_alpha_property:
        cs.append(f"{bname}: alpha property — parallax + alpha may glitch even with CS")

    # ------------------------------------------------------------------ truepbr
    pb = notes["truepbr"]
    if not info.has_pbr_flag:
        pb.append(
            f"{bname}: SLSF2_Unused01 (TruePBR flag) not set — TruePBR requires this flag "
            f"plus a matching .json nif entry file"
        )
    else:
        pb.append(f"{bname}: SLSF2_Unused01 TruePBR flag ✓")
    slot1_pb = info.texture_paths.get(1, "")
    if slot1_pb.lower().endswith("_msn.dds"):
        pb.append(f"{bname}: slot 1 is _msn.dds (model-space); TruePBR expects _n.dds (tangent-space)")
    elif slot1_pb.lower().endswith("_n.dds"):
        pb.append(f"{bname}: slot 1 normal {slot1_pb!r} ✓")
    else:
        pb.append(f"{bname}: slot 1 normal {slot1_pb!r} — TruePBR expects _n.dds")
    rmaos = info.texture_paths.get(5, "")
    if not rmaos:
        pb.append(f"{bname}: slot 5 (roughness/metallic/AO/specular) is empty — TruePBR needs _rmaos.dds")
    elif rmaos.lower().endswith(_TRUEPBR_ENV_MASK_SUFFIXES):
        pb.append(f"{bname}: slot 5 {rmaos!r} ✓")
    elif rmaos.lower().endswith(_LEGACY_TRUEPBR_ENV_MASK_SUFFIXES):
        pb.append(
            f"{bname}: slot 5 {rmaos!r} uses a generic packed alias (_orm/_mrao) commonly exported by Blender/Substance; "
            f"rename/repack to canonical _rmaos for clearer TruePBR handling."
        )
    elif rmaos.lower().endswith(("_cm.dds", "_c.dds")):
        pb.append(f"{bname}: slot 5 {rmaos!r} looks like Community Shaders Extended Materials (_cm/_c), not TruePBR.")
    elif rmaos.lower().endswith(("_m.dds", "_mask.dds", "_envmask.dds")):
        pb.append(f"{bname}: slot 5 {rmaos!r} looks like ENB/vanilla _m workflow, not TruePBR _rmaos.")
    else:
        pb.append(f"{bname}: slot 5 {rmaos!r} — TruePBR expects _rmaos.dds")
    pb.append(
        f"{bname}: TruePBR also requires a .json nif entry alongside the mesh — "
        f"this patcher cannot generate that file"
    )

    return notes


def validate_nif_for_parallax(nif_path: Path) -> NifValidationResult:
    """Check whether a NIF is ready for parallax, and suggest fixes.

    The result now carries per-block :attr:`~NifValidationResult.skip_reasons`
    (explaining exactly why each shader will be skipped by the patcher),
    :attr:`~NifValidationResult.has_havok` (whether the NIF contains a
    behaviour-graph block), and per-renderer
    :attr:`~NifValidationResult.renderer_notes` for vanilla, ENB, Community
    Shaders, and TruePBR.
    """
    result = NifValidationResult(nif_path=nif_path, valid=False)

    # ------------------------------------------------------------------
    # Fast binary check for BSBehaviorGraphExtraData (Havok-animated mesh).
    # We do this before the full parse because it is a NIF-level flag that
    # unconditionally skips ALL shader blocks.
    # ------------------------------------------------------------------
    try:
        raw = nif_path.read_bytes()
    except OSError as exc:
        result.issues.append(f"Cannot read NIF: {exc}")
        return result
    result.has_havok = b"BSBehaviorGraphExtraData" in raw

    infos, diagnostics = scan_nif_diagnostics(nif_path)
    guessed_parallax = guess_parallax_path_for_nif(nif_path)

    def _append_unique(items: list[str], message: str) -> None:
        if message not in items:
            items.append(message)

    if diagnostics:
        result.issues.extend(diagnostics[:6])
    if not infos:
        if not diagnostics:
            result.issues.append("No BSLightingShaderProperty blocks found or not a Skyrim SE NIF.")
        for diagnostic in diagnostics:
            lowered = diagnostic.lower()
            if "resolution:" in lowered or "convert" in lowered or "re-save" in lowered or "re-export" in lowered:
                _append_unique(result.suggestions, diagnostic)
        return result

    if result.has_havok:
        _append_unique(
            result.skip_reasons,
            "NIF contains BSBehaviorGraphExtraData (Havok animation graph) — "
            "patching parallax onto Havok-animated meshes causes CTD/crashes in-game."
        )

    result.shader_count = len(infos)

    # ---- aggregate renderer notes across all shader blocks ----
    agg_renderer_notes: dict[str, list[str]] = {
        "vanilla": [],
        "enb": [],
        "community_shaders": [],
        "truepbr": [],
    }

    for info in infos:
        bname = f"Block {info.block_index}"

        # ---- determine skip conditions for this block ----
        block_skipped = False

        if result.has_havok:
            # Already reported at NIF level; every block is skipped.
            block_skipped = True

        if info.shader_type not in (SHADER_TYPE_DEFAULT, SHADER_TYPE_HEIGHTMAP, SHADER_TYPE_ENVMAP):
            mesh_hint = _shader_type_mesh_hint(info.shader_type)
            hint_text = f" {mesh_hint}" if mesh_hint else ""
            reason = (
                f"{bname}: incompatible shader type {info.shader_type_name!r} "
                f"({info.shader_type}) — patcher only supports Default (0), "
                f"Heightmap/Parallax (3), and EnvMap (1)"
                f"{hint_text}"
            )
            _append_unique(result.skip_reasons, reason)
            block_skipped = True

        if info.has_decal_flag:
            reason = (
                f"{bname}: decal flag (SLSF1_Decal or SLSF1_Dynamic_Decal) is set — "
                f"parallax combined with decal rendering causes visual glitches"
            )
            _append_unique(result.skip_reasons, reason)
            block_skipped = True

        if info.has_soft_lighting_flag or info.has_rim_lighting_flag or info.has_back_lighting_flag:
            active = []
            if info.has_soft_lighting_flag:
                active.append("SLSF2_Soft_Lighting")
            if info.has_rim_lighting_flag:
                active.append("SLSF2_Rim_Lighting")
            if info.has_back_lighting_flag:
                active.append("SLSF2_Back_Lighting")
            reason = (
                f"{bname}: subsurface-scattering lighting flags active "
                f"({', '.join(active)}) — these are mutually exclusive with parallax"
            )
            _append_unique(result.skip_reasons, reason)
            block_skipped = True

        if info.has_anisotropic_flag:
            reason = (
                f"{bname}: SLSF2_Anisotropic_Lighting is set — "
                f"anisotropic shading and parallax produce incorrect results together"
            )
            _append_unique(result.skip_reasons, reason)
            block_skipped = True

        if info.is_skinned or info.has_skinned_flag:
            sources = []
            if info.is_skinned:
                sources.append("NiSkinInstance on parent shape")
            if info.has_skinned_flag:
                sources.append("SLSF1_Skinned in shader flags")
            reason = (
                f"{bname}: skinned/animated mesh ({'; '.join(sources)}) — "
                f"parallax on skinned geometry causes CTD in Skyrim SE"
            )
            _append_unique(result.skip_reasons, reason)
            block_skipped = True

        if info.has_alpha_property:
            reason = (
                f"{bname}: NiAlphaProperty on parent shape — "
                f"parallax combined with alpha-blending/testing produces rendering artifacts"
            )
            _append_unique(result.skip_reasons, reason)
            block_skipped = True

        if info.has_landscape_flag:
            reason = (
                f"{bname}: SLSF1_Landscape is set — landscape shaders use a separate "
                f"parallax mechanism; standard parallax flags have no effect here"
            )
            _append_unique(result.skip_reasons, reason)
            block_skipped = True

        if info.is_lod_shape:
            _append_unique(
                result.skip_reasons,
                f"{bname}: LOD geometry ({info.parent_block_type!r}) — parallax works "
                f"at normal viewing distance but disappears at the LOD transition; "
                f"patcher will still patch it, but this is worth noting"
            )
            # LOD is a warning, not a hard skip for counting purposes

        if block_skipped:
            result.skip_count += 1
        else:
            has_flag = info.has_parallax_flag
            parallax_path = info.texture_paths.get(TEXTURE_SLOT_PARALLAX, "").strip()
            has_tex = bool(parallax_path)
            if has_flag and has_tex:
                result.ready_count += 1
            else:
                result.needs_patch_count += 1
                if not has_flag:
                    _append_unique(
                        result.issues,
                        f"{bname}: SLSF1_Parallax flag not set."
                    )
                    _append_unique(
                        result.suggestions,
                        "Run patch_nif with enable_parallax=True."
                    )
                if not has_tex:
                    _append_unique(
                        result.issues,
                        f"{bname}: Texture slot 3 (parallax) is empty."
                    )
                    _append_unique(
                        result.suggestions,
                        (
                            f"Supply parallax_texture_path pointing to a _p.dds height map"
                            f"{f' (for example {guessed_parallax})' if guessed_parallax else ''}."
                        )
                    )
                if info.shader_type != SHADER_TYPE_HEIGHTMAP:
                    _append_unique(
                        result.suggestions,
                        f"{bname}: shader type is {info.shader_type} "
                        "(not Heightmap/3). Use force_shader_type_3=True to enable "
                        "the parallax_scale field for stronger in-game depth."
                    )

        diffuse_path = info.texture_paths.get(TEXTURE_SLOT_DIFFUSE, "").strip()
        normal_path = info.texture_paths.get(TEXTURE_SLOT_NORMAL, "").strip()
        parallax_path = info.texture_paths.get(TEXTURE_SLOT_PARALLAX, "").strip()

        if diffuse_path:
            normalized_diffuse = _normalise_slot_path(diffuse_path)
            if not _path_has_dds_extension(diffuse_path):
                _append_unique(
                    result.issues,
                    f"{bname}: slot 0 diffuse path '{diffuse_path}' is not a .dds texture path."
                )
                _append_unique(
                    result.suggestions,
                    "Convert/export diffuse textures to .dds for Skyrim; Blender/Substance authoring extensions (.png/.tga/.exr) are source assets, not final game paths."
                )
            if normalized_diffuse.endswith(_DIFFUSE_SLOT_DISALLOWED_SUFFIXES):
                _append_unique(
                    result.issues,
                    f"{bname}: slot 0 diffuse path '{diffuse_path}' looks like a non-diffuse map."
                )
                _append_unique(
                    result.suggestions,
                    "Use slot 0 for diffuse/albedo textures and move _n/_p/_g/_m/_cm/cubemap files to slots 1/3/2/5/4 as appropriate."
                )
        if parallax_path:
            normalized_parallax = _normalise_slot_path(parallax_path)
            if not _path_has_dds_extension(parallax_path):
                _append_unique(
                    result.issues,
                    f"{bname}: slot 3 parallax path '{parallax_path}' is not a .dds texture path."
                )
                _append_unique(
                    result.suggestions,
                    "Convert parallax/height textures to .dds before patching NIF slot 3."
                )
            if not normalized_parallax.startswith("textures\\"):
                _append_unique(
                    result.issues,
                    f"{bname}: parallax path '{parallax_path}' is not a Skyrim-relative textures\\ path."
                )
                _append_unique(
                    result.suggestions,
                    "Patch slot 3 with a Skyrim-relative path such as textures\\architecture\\example_p.dds."
                )
            if not normalized_parallax.endswith("_p.dds"):
                _append_unique(
                    result.issues,
                    f"{bname}: parallax path '{parallax_path}' does not use the expected _p.dds naming."
                )
                _append_unique(
                    result.suggestions,
                    "Use a dedicated _p.dds height map in texture slot 3 so Skyrim/ENB parallax reads the correct file."
                )
            if normalized_parallax.endswith(_CUBEMAP_SLOT_EXPECTED_SUFFIXES):
                _append_unique(
                    result.issues,
                    f"{bname}: slot 3 parallax path '{parallax_path}' looks like a cubemap path."
                )
                _append_unique(
                    result.suggestions,
                    "Use slot 3 for _p.dds height maps and move cubemap/environment textures to slot 4."
                )
            if diffuse_path and normalized_parallax == _normalise_slot_path(diffuse_path):
                _append_unique(
                    result.issues,
                    f"{bname}: parallax slot 3 points at the diffuse texture instead of a _p.dds height map."
                )
            if normal_path and normalized_parallax == _normalise_slot_path(normal_path):
                _append_unique(
                    result.issues,
                    f"{bname}: parallax slot 3 points at the normal texture instead of a dedicated height map."
                )
        if normal_path:
            normalized_normal = _normalise_slot_path(normal_path)
            if not _path_has_dds_extension(normal_path):
                _append_unique(
                    result.issues,
                    f"{bname}: slot 1 normal path '{normal_path}' is not a .dds texture path."
                )
                _append_unique(
                    result.suggestions,
                    "Use a .dds normal map in slot 1 (_n.dds or _msn.dds). Generic Blender source files should be converted first."
                )
            if normalized_normal.endswith(_NORMAL_SLOT_DISALLOWED_SUFFIXES):
                _append_unique(
                    result.issues,
                    f"{bname}: slot 1 normal path '{normal_path}' does not look like a normal map path."
                )
                _append_unique(
                    result.suggestions,
                    "Use slot 1 for _n.dds or _msn.dds normal maps; keep _p/_g/_m textures in slots 3/2/5."
                )
        if not normal_path:
            _append_unique(
                result.suggestions,
                f"{bname}: slot 1 normal map is empty; parallax will still patch, but lighting usually looks wrong without a matching _n.dds or _msn.dds."
            )
        if info.has_pom_flag and info.shader_type not in (SHADER_TYPE_HEIGHTMAP, SHADER_TYPE_PARALLAX_OCC):
            _append_unique(
                result.issues,
                f"{bname}: POM flag is set on shader type {info.shader_type}; ENB parallax occlusion is more reliable on Heightmap/3 blocks."
            )
        if info.has_pom_flag and not info.has_parallax_flag:
            _append_unique(
                result.issues,
                f"{bname}: POM flag is enabled without the base SLSF1_Parallax flag."
            )
            _append_unique(
                result.suggestions,
                "Enable standard parallax alongside POM, or disable POM for this block."
            )
        env_mask_path = info.texture_paths.get(TEXTURE_SLOT_ENV_MASK, "").strip()
        if env_mask_path:
            normalized_env_mask = _normalise_slot_path(env_mask_path)
            if not _path_has_dds_extension(env_mask_path):
                _append_unique(
                    result.issues,
                    f"{bname}: slot 5 environment-mask path '{env_mask_path}' is not a .dds texture path."
                )
                _append_unique(
                    result.suggestions,
                    "Convert packed masks to .dds before assigning slot 5; source authoring files are not valid Skyrim runtime paths."
                )
            if not normalized_env_mask.endswith(_ENV_MASK_SLOT_EXPECTED_SUFFIXES):
                _append_unique(
                    result.issues,
                    f"{bname}: slot 5 environment-mask path '{env_mask_path}' does not look like an environment mask."
                )
                _append_unique(
                    result.suggestions,
                    "Use slot 5 for _m.dds (vanilla/ENB), _cm/_c (Community Shaders Extended Materials), or _rmaos (TruePBR)."
                )
                if normalized_env_mask.endswith(_GENERIC_AUTHORING_PACKED_SUFFIXES):
                    _append_unique(
                        result.suggestions,
                        "This suffix looks like a generic Blender/Substance packed map naming; do not assume it is ENB/CS/TruePBR-ready until it is repacked into the target workflow format."
                    )
            if normalized_env_mask.endswith(_TRUEPBR_ENV_MASK_SUFFIXES + _LEGACY_TRUEPBR_ENV_MASK_SUFFIXES) and not normalized_env_mask.startswith("textures\\pbr\\"):
                _append_unique(
                    result.suggestions,
                    "TruePBR _rmaos workflows are usually placed under textures\\pbr\\... and paired with a matching PBRNifPatcher JSON entry."
                )
            if normalized_env_mask.endswith(_LEGACY_TRUEPBR_ENV_MASK_SUFFIXES):
                _append_unique(
                    result.suggestions,
                    "Slot 5 uses a generic packed alias suffix (_orm/_mrao) often exported by DCC tools; for explicit TruePBR workflows prefer canonical _rmaos/_ramos naming."
                )
            if normalized_env_mask.endswith(("_cm.dds", "_c.dds")):
                _append_unique(
                    result.suggestions,
                    "Slot 5 _cm/_c usually indicates Community Shaders Extended Materials; avoid mixing those files with ENB _msn workflows."
                )
        if env_mask_path and not info.has_env_mapping_flag:
            _append_unique(
                result.issues,
                f"{bname}: texture slot 5 is filled ('{env_mask_path}') but SLSF1_Environment_Mapping is not enabled."
            )
            _append_unique(
                result.suggestions,
                "Enable environment mapping in BSLightingShaderProperty or clear slot 5 if this mesh should not be reflective."
            )
        if info.has_env_mapping_flag and not env_mask_path:
            _append_unique(
                result.suggestions,
                f"{bname}: SLSF1_Environment_Mapping is enabled but slot 5 is empty; add an _m.dds mask or disable the flag."
            )
        if info.parallax_scale is not None and info.parallax_scale < 0.35:
            _append_unique(
                result.suggestions,
                f"{bname}: parallax scale is only {info.parallax_scale:.2f}; increase it if the mesh patches successfully but depth is still invisible in game."
            )
        glow_path = info.texture_paths.get(TEXTURE_SLOT_GLOW, "").strip()
        if glow_path:
            normalized_glow = _normalise_slot_path(glow_path)
            if not _path_has_dds_extension(glow_path):
                _append_unique(
                    result.issues,
                    f"{bname}: slot 2 glow path '{glow_path}' is not a .dds texture path."
                )
                _append_unique(
                    result.suggestions,
                    "Convert emissive textures to .dds for Skyrim slot 2 usage."
                )
            if not normalized_glow.endswith(("_g.dds", "_sk.dds")):
                _append_unique(
                    result.issues,
                    f"{bname}: slot 2 glow path '{glow_path}' does not look like an emissive/glow texture."
                )
                _append_unique(
                    result.suggestions,
                    "Use slot 2 for emissive textures and prefer _g.dds for new outputs. Legacy aliases such as _em/_emit/_glow should usually be normalized back to _g."
                )
        if glow_path and not info.has_glow_map_flag:
            _append_unique(
                result.issues,
                f"{bname}: texture slot 2 is filled ('{glow_path}') but SLSF2_Glow_Map is not set — the emissive map will not be visible."
            )
            _append_unique(
                result.suggestions,
                "Enable the glow map flag with enable_glow_map=True or clear slot 2 if this mesh should not glow."
            )
        if info.has_glow_map_flag and not glow_path:
            _append_unique(
                result.suggestions,
                f"{bname}: SLSF2_Glow_Map is set but slot 2 is empty; add a _g.dds emissive map or disable the flag."
            )
        cubemap_path = info.texture_paths.get(TEXTURE_SLOT_CUBEMAP, "").strip()
        if cubemap_path:
            normalized_cubemap = _normalise_slot_path(cubemap_path)
            if not _path_has_dds_extension(cubemap_path):
                _append_unique(
                    result.issues,
                    f"{bname}: slot 4 cubemap path '{cubemap_path}' is not a .dds texture path."
                )
                _append_unique(
                    result.suggestions,
                    "Use .dds cubemap textures for slot 4."
                )
            if normalized_cubemap.endswith(_CUBEMAP_SLOT_WRONG_SUFFIXES):
                _append_unique(
                    result.issues,
                    f"{bname}: slot 4 cubemap path '{cubemap_path}' looks like a non-cubemap texture."
                )
                _append_unique(
                    result.suggestions,
                    "Use slot 4 for cubemap/environment textures (_e/_env/_cube naming) and keep _n/_p/_g/_m/_cm maps in their standard slots."
                )
            elif "cubemap" not in normalized_cubemap and not normalized_cubemap.endswith(_CUBEMAP_SLOT_EXPECTED_SUFFIXES):
                _append_unique(
                    result.suggestions,
                    f"{bname}: slot 4 path '{cubemap_path}' does not use common cubemap naming (_e/_env/_cube); verify the file is the intended reflection cubemap."
                )

        # ---- per-renderer compatibility notes ----
        block_renderer_notes = _renderer_compatibility(info)
        for renderer, notes in block_renderer_notes.items():
            agg_renderer_notes[renderer].extend(notes)

    result.renderer_notes = agg_renderer_notes
    result.valid = result.shader_count > 0
    return result


def find_nif_files(root: Path) -> list[Path]:
    """Return all ``.nif`` files under *root* (recursive, case-insensitive, sorted)."""
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() == ".nif"
    )


def guess_parallax_path_for_nif(nif_path: Path) -> str | None:
    """Guess the ``_p.dds`` parallax texture path from a NIF's diffuse slot.

    Returns a Windows-style relative path, or ``None`` if no diffuse path is
    found in the NIF.
    """
    infos = scan_nif(nif_path)
    for info in infos:
        diffuse = info.texture_paths.get(TEXTURE_SLOT_DIFFUSE, "").strip()
        if not diffuse:
            continue
        diffuse_p = Path(diffuse.replace("\\", "/"))
        stem = diffuse_p.stem
        for suffix in ("_d", "_diff", "_diffuse", "_albedo"):
            if stem.lower().endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        return str(diffuse_p.parent / (stem + "_p.dds")).replace("/", "\\")
    return None


def guess_normal_path_for_nif(nif_path: Path, *, msn: bool = False) -> str | None:
    """Guess the normal-map path from a NIF's diffuse slot.

    Parameters
    ----------
    msn:
        When ``True``, return a ``_msn.dds`` path (ENB complex material);
        otherwise return a standard ``_n.dds`` path.
    """
    infos = scan_nif(nif_path)
    for info in infos:
        existing = info.texture_paths.get(TEXTURE_SLOT_NORMAL, "").strip()
        if existing:
            p = Path(existing.replace("\\", "/"))
            stem = p.stem
            for s in ("_n", "_normal", "_msn"):
                if stem.lower().endswith(s):
                    stem = stem[: -len(s)]
                    break
            suffix = "_msn.dds" if msn else "_n.dds"
            return str(p.parent / (stem + suffix)).replace("/", "\\")
        diffuse = info.texture_paths.get(TEXTURE_SLOT_DIFFUSE, "").strip()
        if not diffuse:
            continue
        p = Path(diffuse.replace("\\", "/"))
        stem = p.stem
        for s in ("_d", "_diff", "_diffuse", "_albedo"):
            if stem.lower().endswith(s):
                stem = stem[: -len(s)]
                break
        suffix = "_msn.dds" if msn else "_n.dds"
        return str(p.parent / (stem + suffix)).replace("/", "\\")
    return None


def guess_glow_path_for_nif(nif_path: Path) -> str | None:
    """Guess the glow/emissive-map path from a NIF's diffuse or glow slot.

    Returns a Windows-style relative ``_g.dds`` path, or ``None`` if no
    suitable texture path is found in the NIF.
    """
    infos = scan_nif(nif_path)
    for info in infos:
        existing = info.texture_paths.get(TEXTURE_SLOT_GLOW, "").strip()
        if existing:
            p = Path(existing.replace("\\", "/"))
            stem = p.stem
            for s in ("_g", "_glow", "_em", "_emis", "_emissive", "_emit", "_sk"):
                if stem.lower().endswith(s):
                    stem = stem[: -len(s)]
                    break
            return str(p.parent / (stem + "_g.dds")).replace("/", "\\")
        diffuse = info.texture_paths.get(TEXTURE_SLOT_DIFFUSE, "").strip()
        if not diffuse:
            continue
        p = Path(diffuse.replace("\\", "/"))
        stem = p.stem
        for s in ("_d", "_diff", "_diffuse", "_albedo"):
            if stem.lower().endswith(s):
                stem = stem[: -len(s)]
                break
        return str(p.parent / (stem + "_g.dds")).replace("/", "\\")
    return None


def guess_cubemap_path_for_nif(nif_path: Path) -> str | None:
    """Guess a cubemap/environment path from slot 4 or the diffuse slot.

    Returns a Windows-style relative ``_e.dds`` path, or ``None`` if no
    suitable texture path is found in the NIF.
    """
    infos = scan_nif(nif_path)
    for info in infos:
        existing = info.texture_paths.get(TEXTURE_SLOT_CUBEMAP, "").strip()
        if existing:
            p = Path(existing.replace("\\", "/"))
            stem = p.stem
            for s in ("_cubemap", "_cube", "_envmap", "_env", "_e"):
                if stem.lower().endswith(s):
                    stem = stem[: -len(s)]
                    break
            return str(p.parent / (stem + "_e.dds")).replace("/", "\\")
        diffuse = info.texture_paths.get(TEXTURE_SLOT_DIFFUSE, "").strip()
        if not diffuse:
            continue
        p = Path(diffuse.replace("\\", "/"))
        stem = p.stem
        for s in ("_d", "_diff", "_diffuse", "_albedo"):
            if stem.lower().endswith(s):
                stem = stem[: -len(s)]
                break
        return str(p.parent / (stem + "_e.dds")).replace("/", "\\")
    return None


def guess_env_mask_path_for_nif(nif_path: Path, *, preferred_suffix: str = "_m.dds") -> str | None:
    """Guess the environment-mask path from a NIF's diffuse or env-mask slot.

    Returns a Windows-style relative path using *preferred_suffix* (default:
    ``_m.dds``), or ``None`` if no suitable texture path is found in the NIF.
    """
    suffix_aliases = {
        "_m": "_m.dds",
        "_m.dds": "_m.dds",
        "_cm": "_cm.dds",
        "_cm.dds": "_cm.dds",
        "_rmaos": "_rmaos.dds",
        "_rmaos.dds": "_rmaos.dds",
        "_ramos": "_rmaos.dds",
        "_ramos.dds": "_rmaos.dds",
        "_orm": "_rmaos.dds",
        "_orm.dds": "_rmaos.dds",
        "_orms": "_rmaos.dds",
        "_orms.dds": "_rmaos.dds",
        "_mrao": "_rmaos.dds",
        "_mrao.dds": "_rmaos.dds",
        "_mra": "_rmaos.dds",
        "_mra.dds": "_rmaos.dds",
    }
    resolved_suffix = suffix_aliases.get(str(preferred_suffix or "").strip().lower(), "_m.dds")
    infos = scan_nif(nif_path)
    for info in infos:
        existing = info.texture_paths.get(TEXTURE_SLOT_ENV_MASK, "").strip()
        if existing:
            p = Path(existing.replace("\\", "/"))
            stem = p.stem
            for s in (
                "_m",
                "_mask",
                "_envmask",
                "_rmaos",
                "_ramos",
                "_orm",
                "_orms",
                "_mrao",
                "_mra",
                "_cm",
                "_c",
            ):
                if stem.lower().endswith(s):
                    stem = stem[: -len(s)]
                    break
            return str(p.parent / (stem + resolved_suffix)).replace("/", "\\")
        diffuse = info.texture_paths.get(TEXTURE_SLOT_DIFFUSE, "").strip()
        if not diffuse:
            continue
        p = Path(diffuse.replace("\\", "/"))
        stem = p.stem
        for s in ("_d", "_diff", "_diffuse", "_albedo"):
            if stem.lower().endswith(s):
                stem = stem[: -len(s)]
                break
        return str(p.parent / (stem + resolved_suffix)).replace("/", "\\")
    return None


def batch_patch_nif(
    nif_paths: list[Path],
    opts: NifPatchOptions,
) -> list[NifPatchResult]:
    """Patch multiple NIF files with the same options.

    Returns one :class:`NifPatchResult` per input path in the same order.
    Never raises — individual errors are captured in each result's
    ``errors`` list and ``success`` flag.

    Parameters
    ----------
    nif_paths:
        NIF files to patch.
    opts:
        Patch options applied uniformly to every file.
    """
    return [patch_nif(p, opts) for p in nif_paths]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:  # pragma: no cover
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Patch Skyrim SE NIF files to enable parallax / env mapping.",
    )
    parser.add_argument("nif", nargs="+", type=Path,
                        help="NIF file(s) or folder(s) to patch.")
    parser.add_argument("--parallax", metavar="PATH",
                        help="Parallax height-map texture path (slot 3).")
    parser.add_argument("--parallax-scale", type=float, default=None,
                        metavar="N",
                        help="Parallax depth multiplier (e.g. 2.5). Requires "
                             "shader type 3 or --force-type3.")
    parser.add_argument("--pom", action="store_true",
                        help="Also set SLSF1_Parallax_Occlusion for ENB POM.")
    parser.add_argument("--force-type3", action="store_true",
                        help="Upgrade shader type 0→3 to enable parallax scale.")
    parser.add_argument("--normal", metavar="PATH",
                        help="Normal / MSN texture path (slot 1).")
    parser.add_argument("--diffuse", metavar="PATH",
                        help="Diffuse / albedo texture path (slot 0).")
    parser.add_argument("--glow", metavar="PATH",
                        help="Glow / emissive texture path (slot 2).")
    parser.add_argument("--cubemap", metavar="PATH",
                        help="Cubemap texture path (slot 4).")
    parser.add_argument("--env-mask", metavar="PATH",
                        help="Environment mask texture path (slot 5).")
    parser.add_argument("--env-mapping", action="store_true",
                        help="Set SLSF1_Environment_Mapping flag.")
    parser.add_argument("--enable-glow-map", action="store_true",
                        help="Set SLSF2_Glow_Map flag.")
    parser.add_argument("--disable-parallax", action="store_true",
                        help="Clear SLSF1_Parallax and SLSF1_Parallax_Occlusion flags.")
    parser.add_argument("--disable-pom", action="store_true",
                        help="Clear SLSF1_Parallax_Occlusion flag only.")
    parser.add_argument("--disable-env-mapping", action="store_true",
                        help="Clear SLSF1_Environment_Mapping flag.")
    parser.add_argument("--disable-glow-map", action="store_true",
                        help="Clear SLSF2_Glow_Map flag.")
    parser.add_argument("--clear-parallax", action="store_true",
                        help="Clear slot 3 parallax texture path.")
    parser.add_argument("--clear-normal", action="store_true",
                        help="Clear slot 1 normal/MSN texture path.")
    parser.add_argument("--clear-glow", action="store_true",
                        help="Clear slot 2 glow/emissive texture path.")
    parser.add_argument("--clear-diffuse", action="store_true",
                        help="Clear slot 0 diffuse/albedo texture path.")
    parser.add_argument("--clear-env-mask", action="store_true",
                        help="Clear slot 5 environment mask texture path.")
    parser.add_argument("--clear-cubemap", action="store_true",
                        help="Clear slot 4 cubemap texture path.")
    parser.add_argument("--no-backup", action="store_true",
                        help="Skip .nif.bak backup.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing.")
    parser.add_argument("--validate", action="store_true",
                        help="Just validate and report, do not patch.")

    # ---- Safety skip options ----
    parser.add_argument("--no-skip-incompatible", action="store_true",
                        help="Disable shader-type compatibility check (default: skip "
                             "non-Default/Parallax/EnvMap shader types).")
    parser.add_argument("--no-skip-decal", action="store_true",
                        help="Disable decal-flag skip (default: skip decal shaders "
                             "when enabling parallax).")
    parser.add_argument("--no-skip-lighting-effects", action="store_true",
                        help="Disable lighting-effects skip (default: skip soft/rim/"
                             "back-lit shaders when enabling parallax).")
    parser.add_argument("--no-skip-anisotropic", action="store_true",
                        help="Disable anisotropic-lighting skip when enabling parallax.")
    parser.add_argument("--no-skip-havok", action="store_true",
                        help="Disable Havok-NIF skip (default: skip parallax patching "
                             "for NIFs with BSBehaviorGraphExtraData blocks).")
    parser.add_argument("--no-skip-skinned", action="store_true",
                        help="Disable skinned-mesh skip (default: skip shapes with "
                             "NiSkinInstance / BSDismemberSkinInstance).")
    parser.add_argument("--no-skip-alpha", action="store_true",
                        help="Disable alpha-property skip (default: skip shapes with "
                             "NiAlphaProperty).")

    # ---- Mesh lighting fix ----
    parser.add_argument("--fix-mesh-lighting", action="store_true",
                        help="Clamp lighting-effect-1 (soft lighting) to 0.6 on any "
                             "shader block where it exceeds that value. Fixes the "
                             "glowing-mesh problem under ENB.")

    # ---- Complex material shader field patches ----
    parser.add_argument("--env-map-scale", type=float, default=None,
                        metavar="SCALE",
                        help="Set the environment-map scale field (SHADER_TYPE_ENVMAP "
                             "blocks only). Recommended: 1.0 for complex material.")
    parser.add_argument("--spec-strength", type=float, default=None,
                        metavar="STRENGTH",
                        help="Set the specular-strength field. Recommended: 1.0 for "
                             "complex material.")
    parser.add_argument("--spec-color", type=str, default=None,
                        metavar="R,G,B",
                        help="Set the specular colour as three 0.0–1.0 floats "
                             "separated by commas, e.g. '1.0,1.0,1.0'.")

    args = parser.parse_args()

    # Parse --spec-color
    parsed_spec_color: tuple[float, float, float] | None = None
    if args.spec_color:
        try:
            parts = [float(v.strip()) for v in args.spec_color.split(",")]
            if len(parts) != 3:
                raise ValueError
            parsed_spec_color = (parts[0], parts[1], parts[2])
        except ValueError:
            print(
                f"Error: --spec-color must be three comma-separated floats, e.g. '1.0,1.0,1.0'",
                file=sys.stderr,
            )
            sys.exit(1)

    nif_files: list[Path] = []
    for p in args.nif:
        if p.is_dir():
            nif_files.extend(find_nif_files(p))
        elif p.suffix.lower() == ".nif":
            nif_files.append(p)
        else:
            print(f"Skipping (not a .nif): {p}", file=sys.stderr)

    if not nif_files:
        print("No NIF files found.", file=sys.stderr)
        sys.exit(1)

    if args.validate:
        for nif in nif_files:
            v = validate_nif_for_parallax(nif)
            status = "READY" if v.ready_count == v.shader_count else "NEEDS PATCH"
            print(f"[{status}] {nif.name}: "
                  f"{v.ready_count}/{v.shader_count} shaders ready for parallax")
            for issue in v.issues:
                print(f"  ⚠ {issue}")
            for sug in v.suggestions:
                print(f"  → {sug}")
        return

    opts = NifPatchOptions(
        enable_parallax=bool(args.parallax) or args.pom,
        enable_pom=args.pom,
        parallax_scale=args.parallax_scale,
        force_shader_type_3=args.force_type3,
        enable_env_mapping=args.env_mapping,
        enable_glow_map=args.enable_glow_map,
        parallax_texture_path=args.parallax,
        normal_texture_path=args.normal,
        glow_texture_path=args.glow,
        diffuse_texture_path=args.diffuse,
        env_mask_texture_path=args.env_mask,
        cubemap_texture_path=args.cubemap,
        backup=not args.no_backup,
        dry_run=args.dry_run,
        disable_parallax=args.disable_parallax,
        disable_pom=args.disable_pom,
        disable_env_mapping=args.disable_env_mapping,
        disable_glow_map=args.disable_glow_map,
        clear_parallax_texture_path=args.clear_parallax,
        clear_normal_texture_path=args.clear_normal,
        clear_glow_texture_path=args.clear_glow,
        clear_diffuse_texture_path=args.clear_diffuse,
        clear_env_mask_texture_path=args.clear_env_mask,
        clear_cubemap_texture_path=args.clear_cubemap,
        skip_incompatible_shader_types=not args.no_skip_incompatible,
        skip_decal=not args.no_skip_decal,
        skip_lighting_effects=not args.no_skip_lighting_effects,
        skip_anisotropic=not args.no_skip_anisotropic,
        skip_if_havok=not args.no_skip_havok,
        skip_if_skinned=not args.no_skip_skinned,
        skip_if_alpha=not args.no_skip_alpha,
        fix_mesh_lighting=args.fix_mesh_lighting,
        env_map_scale=args.env_map_scale,
        spec_strength=args.spec_strength,
        spec_color=parsed_spec_color,
    )

    ok = 0
    for nif in nif_files:
        res = patch_nif(nif, opts)
        status = "OK" if res.success else "FAIL"
        print(f"[{status}] {nif.name}: {res.message}")
        for err in res.errors:
            print(f"       {err}", file=sys.stderr)
        if res.success and not res.already_up_to_date:
            ok += 1

    print(f"\n{ok}/{len(nif_files)} files modified.")


if __name__ == "__main__":
    _main()
