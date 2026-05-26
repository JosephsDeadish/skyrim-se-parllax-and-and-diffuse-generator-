"""Skyrim SE NIF file patcher (v0.6).

Reads Skyrim SE NIF files (format 20.2.0.7, user_version=12,
user_version_2=83) and patches ``BSLightingShaderProperty`` shader flags and
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
_SKYRIM_LE_USER_VERSION_2: int = 34

_HEADER_PREFIXES: tuple[bytes, ...] = (
    b"Gamebryo File Format, Version 20.2.0.7\n",
    b"NetImmerse File Format, Version 20.2.0.7\n",
)

# Shader Flags 1 (BSLightingShaderProperty)
SLSF1_SPECULAR: int = 0x00000001
SLSF1_SKINNED: int = 0x00000002
SLSF1_ENVIRONMENT_MAPPING: int = 0x00000080
SLSF1_RECEIVE_SHADOWS: int = 0x00000100
SLSF1_CAST_SHADOWS: int = 0x00000200
SLSF1_PARALLAX: int = 0x00000800
SLSF1_MODEL_SPACE_NORMALS: int = 0x00001000
SLSF1_PARALLAX_OCCLUSION: int = 0x10000000
SLSF1_ZBUFFER_TEST: int = 0x80000000

# Shader Flags 2
SLSF2_ZBUFFER_WRITE: int = 0x00000001
SLSF2_GLOW_MAP: int = 0x00000040
SLSF2_SOFT_LIGHTING: int = 0x02000000

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


# ---------------------------------------------------------------------------
# Patch options
# ---------------------------------------------------------------------------

@dataclass
class NifPatchOptions:
    """Options that control which changes are applied to each shader block.

    Attributes
    ----------
    enable_parallax:
        Set ``SLSF1_Parallax`` in Shader_Flags_1 and write
        *parallax_texture_path* into texture slot 3.
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
    parallax_texture_path:
        Relative texture path for slot 3 (e.g. ``textures\\arch\\stone_p.dds``).
    normal_texture_path:
        Relative path for slot 1.  Set to your ``_msn.dds`` for ENB complex
        material or ``_n.dds`` for a standard normal map.
    env_mask_texture_path:
        Relative path for slot 5 (environment mask / ``_m.dds``).
    cubemap_texture_path:
        Relative path for slot 4 (cube map for environment reflections).
    backup:
        Write a ``.nif.bak`` copy of the original before overwriting.
    dry_run:
        Analyse what would change but do not write any files.
    """

    enable_parallax: bool = False
    enable_pom: bool = False
    parallax_scale: float | None = None
    force_shader_type_3: bool = False
    enable_env_mapping: bool = False
    parallax_texture_path: str | None = None
    normal_texture_path: str | None = None
    env_mask_texture_path: str | None = None
    cubemap_texture_path: str | None = None
    backup: bool = True
    dry_run: bool = False


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

    @property
    def has_parallax_flag(self) -> bool:
        return bool(self.flags1 & SLSF1_PARALLAX)

    @property
    def has_pom_flag(self) -> bool:
        return bool(self.flags1 & SLSF1_PARALLAX_OCCLUSION)

    @property
    def has_env_mapping_flag(self) -> bool:
        return bool(self.flags1 & SLSF1_ENVIRONMENT_MAPPING)


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


@dataclass
class NifValidationResult:
    """Result of :func:`validate_nif_for_parallax`."""
    nif_path: Path
    valid: bool
    shader_count: int = 0
    ready_count: int = 0          # shaders that already have parallax enabled
    needs_patch_count: int = 0    # shaders present but missing flags/texture
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


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

    # --- in-place writes (never resize) ------------------------------------

    def write_u32_at(self, offset: int, value: int) -> None:
        self._require(4, offset=offset, context="u32 write")
        struct.pack_into("<I", self._b, offset, value)

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
        header_line_end = data.find(b"\n")
        if header_line_end == -1:
            diagnostics.append("The NIF header line is incomplete. Re-export or re-save the mesh before patching.")
            return diagnostics
        header_line = data[: header_line_end + 1]
        if not any(header_line.startswith(prefix) for prefix in _HEADER_PREFIXES):
            diagnostics.append("Header prefix is not a Skyrim/Gamebryo 20.2.0.7 NIF. This file is unsupported for auto-patching.")
            return diagnostics
        version_offset = header_line_end + 1
        version = struct.unpack_from("<I", data, version_offset)[0]
        if version != _NIF_VERSION_20_2_0_7:
            diagnostics.append(f"NIF version is 0x{version:08X}, not Skyrim SE 20.2.0.7.")
            return diagnostics
        user_version = struct.unpack_from("<I", data, version_offset + 5)[0]
        user_version_2 = struct.unpack_from("<I", data, version_offset + 13)[0]
        if user_version_2 == _SKYRIM_LE_USER_VERSION_2:
            diagnostics.append("This looks like a Skyrim Legendary Edition / Oldrim NIF. Convert it to SSE before patching.")
        elif user_version != _SKYRIM_USER_VERSION or user_version_2 != _SKYRIM_SE_USER_VERSION_2:
            diagnostics.append(
                f"Unexpected user version values ({user_version}, {user_version_2}). The file may use a different game/export format."
            )
    except Exception:
        pass
    diagnostics.append(
        "Resolution: open the mesh in NifSkope or the Creation Kit and re-save/export it as a clean Skyrim SE NIF, then run the patch again."
    )
    return diagnostics


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
        if b == ord("\n"):
            break
        if len(header_line) > 100:
            return None
    if not any(bytes(header_line).startswith(p) for p in _HEADER_PREFIXES):
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
    if user_version_2 not in (_SKYRIM_SE_USER_VERSION_2, _SKYRIM_LE_USER_VERSION_2):
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

    num_groups = buf.read_u32()
    for _ in range(num_groups):
        buf.read_u32()

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

    # Only valid when shader_type == SHADER_TYPE_HEIGHTMAP (3):
    parallax_max_passes_offset: int | None
    parallax_scale_offset: int | None
    parallax_max_passes: float | None
    parallax_scale: float | None


@dataclass
class _TextureSetBlock:
    """Parsed BSShaderTextureSet."""
    block_index: int
    block_start: int
    num_textures: int
    slot_offsets: list[int]    # byte offset of each slot's uint32 length prefix
    slot_paths: list[str]


def _parse_shader_prop(buf: _Buf, block_index: int, block_start: int,
                       block_size: int) -> _ShaderPropBlock | None:
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
    buf.seek(block_start)
    _name_ref = buf.read_u32()
    num_extra = buf.read_u32()
    for _ in range(num_extra):
        buf.read_u32()
    _controller = buf.read_i32()

    extra_shift = num_extra * 4

    flags1_offset = block_start + _OFFSET_FLAGS1 + extra_shift
    flags2_offset = block_start + _OFFSET_FLAGS2 + extra_shift
    shader_type_offset = block_start + _OFFSET_SHADER_TYPE + extra_shift
    texture_set_ref_offset = block_start + _OFFSET_TEXTURE_SET + extra_shift

    flags1 = buf.read_u32()
    flags2 = buf.read_u32()
    shader_type = buf.read_u32()

    # skip uv_offset (2×float), uv_scale (2×float), then read texture_set_ref
    buf.seek(block_start + _OFFSET_TEXTURE_SET + extra_shift)
    texture_set_ref = buf.read_i32()

    # Type-specific parallax fields (only when shader_type == 3)
    common_end = block_start + _COMMON_FIELDS_SIZE + extra_shift
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
        parallax_max_passes_offset=pmx_offset,
        parallax_scale_offset=psc_offset,
        parallax_max_passes=pmx_val,
        parallax_scale=psc_val,
    )


def _parse_texture_set(buf: _Buf, block_index: int,
                       block_start: int) -> _TextureSetBlock | None:
    """Parse a BSShaderTextureSet block."""
    buf.seek(block_start)
    num_textures = buf.read_u32()
    if num_textures < 1 or num_textures > 16:
        return None
    slot_offsets: list[int] = []
    slot_paths: list[str] = []
    for _ in range(num_textures):
        slot_offsets.append(buf.pos)
        slot_paths.append(buf.read_sstring_u32())
    return _TextureSetBlock(
        block_index=block_index,
        block_start=block_start,
        num_textures=num_textures,
        slot_offsets=slot_offsets,
        slot_paths=slot_paths,
    )


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
# Core patch engine
# ---------------------------------------------------------------------------

def _normalise_path(p: str) -> str:
    return p.replace("/", "\\")


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

        if "BSLightingShaderProperty" in btype:
            try:
                sp = _parse_shader_prop(buf, bi, bstart, bsize)
                if sp is not None:
                    shader_props.append(sp)
                else:
                    errors.append(f"Block {bi}: failed to parse BSLightingShaderProperty.")
            except (ValueError, struct.error, IndexError) as exc:
                errors.append(f"Block {bi}: shader parse error: {exc}")
        elif "BSShaderTextureSet" in btype:
            try:
                ts = _parse_texture_set(buf, bi, bstart)
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

    # 1. Write new shader_type = 3
    buf.write_u32_at(sp.shader_type_offset, SHADER_TYPE_HEIGHTMAP)

    # 2. Insert the two type-3 floats right after the common fields
    insert_offset = sp.block_start + _COMMON_FIELDS_SIZE + sp.num_extra * 4
    new_fields = struct.pack("<ff", _DEFAULT_PARALLAX_MAX_PASSES, parallax_scale)
    buf.insert_bytes_at(insert_offset, new_fields)

    # 3. Update block size in header
    bsize_field_offset = header.block_sizes_offset + block_index * 4
    old_size = buf.read_u32_at(bsize_field_offset)
    buf.write_u32_at(bsize_field_offset, old_size + 8)

    return buf.to_bytes()


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

    # --- Phase 1: block upgrades (type 0 → 3) ------------------------------
    if opts.force_shader_type_3 and effective_parallax and want_scale:
        # Must reparse after each upgrade because insert shifts later offsets.
        for sp in shader_props:
            if sp.shader_type == SHADER_TYPE_DEFAULT:
                data = _upgrade_block_to_type3(
                    data, header, sp, opts.parallax_scale or _DEFAULT_PARALLAX_SCALE, sp.block_index
                )
                upgraded += 1
        if upgraded:
            # Reparse with new data so subsequent patches use correct offsets.
            buf = _Buf(data)
            new_header = _read_header(buf)
            if new_header is None:
                raise RuntimeError("Header corrupted after type-3 upgrade.")
            shader_props, texture_sets, _ = _build_block_map(data, new_header)
            header = new_header

    # --- Phase 2: in-place flag + parallax scale patches --------------------
    buf = _Buf(data)
    props_patched = 0

    for sp in shader_props:
        new_flags1 = sp.flags1
        new_flags2 = sp.flags2

        if effective_parallax:
            new_flags1 |= SLSF1_PARALLAX
        if opts.enable_pom:
            new_flags1 |= SLSF1_PARALLAX_OCCLUSION
        if opts.enable_env_mapping:
            new_flags1 |= SLSF1_ENVIRONMENT_MAPPING

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
        buf_local.write_u32_at(texture_set.block_start, texture_set.num_textures + needed)
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
        if effective_parallax and opts.parallax_texture_path:
            requested_slots.append(TEXTURE_SLOT_PARALLAX)
        if opts.normal_texture_path:
            requested_slots.append(TEXTURE_SLOT_NORMAL)
        if opts.enable_env_mapping and opts.env_mask_texture_path:
            requested_slots.append(TEXTURE_SLOT_ENV_MASK)
        if opts.enable_env_mapping and opts.cubemap_texture_path:
            requested_slots.append(TEXTURE_SLOT_CUBEMAP)
        if requested_slots:
            max_slot = max(requested_slots)
            data, header, texture_sets, extended = _extend_texture_set_slots(data, header, ts, max_slot)
            if extended:
                sets_patched += 1
                ts = texture_sets.get(sp.texture_set_ref)
                if ts is None:
                    continue

        # (slot_index, old_path, new_path) triples for paths that need changing
        slot_changes: list[tuple[int, str, str]] = []

        def _want(slot: int, new_raw: str | None) -> None:
            if new_raw is None:
                return
            if slot >= ts.num_textures:
                return
            new = _normalise_path(new_raw)
            old = ts.slot_paths[slot] if slot < ts.num_textures else ""
            if old != new:
                slot_changes.append((slot, old, new))

        if effective_parallax:
            _want(TEXTURE_SLOT_PARALLAX, opts.parallax_texture_path)
        _want(TEXTURE_SLOT_NORMAL, opts.normal_texture_path)
        if opts.enable_env_mapping:
            _want(TEXTURE_SLOT_ENV_MASK, opts.env_mask_texture_path)
            _want(TEXTURE_SLOT_CUBEMAP, opts.cubemap_texture_path)

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
    if not effective_parallax and not opts.enable_env_mapping and \
            opts.normal_texture_path is None:
        result.message = "Nothing to patch — all options are disabled."
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
        result.errors.append("Not a supported Skyrim SE NIF (requires 20.2.0.7).")
        result.message = result.errors[-1]
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
        return [], ["Not a supported Skyrim SE NIF (requires 20.2.0.7)."]
    shader_props, texture_sets, parse_errors = _build_block_map(data, header)
    diagnostics.extend(parse_errors)
    if not shader_props:
        diagnostics.extend(_summarize_non_patchable_block_types(header))
    results: list[NifShaderInfo] = []
    for sp in shader_props:
        ts = texture_sets.get(sp.texture_set_ref)
        tex_paths: dict[int, str] = {}
        if ts:
            for i, p in enumerate(ts.slot_paths):
                if p:
                    tex_paths[i] = p
        results.append(NifShaderInfo(
            block_index=sp.block_index,
            shader_type=sp.shader_type,
            flags1=sp.flags1,
            flags2=sp.flags2,
            parallax_scale=sp.parallax_scale,
            texture_paths=tex_paths,
        ))
    return results, diagnostics


def scan_nif(nif_path: Path) -> list[NifShaderInfo]:
    """Return a list of :class:`NifShaderInfo` for every shader block in the NIF."""
    infos, _diagnostics = scan_nif_diagnostics(nif_path)
    return infos


def validate_nif_for_parallax(nif_path: Path) -> NifValidationResult:
    """Check whether a NIF is ready for parallax, and suggest fixes."""
    result = NifValidationResult(nif_path=nif_path, valid=False)
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

    result.shader_count = len(infos)
    for info in infos:
        has_flag = info.has_parallax_flag
        parallax_path = info.texture_paths.get(TEXTURE_SLOT_PARALLAX, "").strip()
        diffuse_path = info.texture_paths.get(TEXTURE_SLOT_DIFFUSE, "").strip()
        normal_path = info.texture_paths.get(TEXTURE_SLOT_NORMAL, "").strip()
        has_tex = bool(parallax_path)
        if has_flag and has_tex:
            result.ready_count += 1
        else:
            result.needs_patch_count += 1
            if not has_flag:
                _append_unique(
                    result.issues,
                    f"Block {info.block_index}: SLSF1_Parallax flag not set."
                )
                _append_unique(
                    result.suggestions,
                    "Run patch_nif with enable_parallax=True."
                )
            if not has_tex:
                _append_unique(
                    result.issues,
                    f"Block {info.block_index}: Texture slot 3 (parallax) is empty."
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
                    f"Block {info.block_index}: shader type is {info.shader_type} "
                    "(not Heightmap/3). Use force_shader_type_3=True to enable "
                    "the parallax_scale field for stronger in-game depth."
                )
        if parallax_path:
            normalized_parallax = parallax_path.lower().replace("/", "\\")
            if not normalized_parallax.startswith("textures\\"):
                _append_unique(
                    result.issues,
                    f"Block {info.block_index}: parallax path '{parallax_path}' is not a Skyrim-relative textures\\ path."
                )
                _append_unique(
                    result.suggestions,
                    "Patch slot 3 with a Skyrim-relative path such as textures\\architecture\\example_p.dds."
                )
            if not normalized_parallax.endswith("_p.dds"):
                _append_unique(
                    result.issues,
                    f"Block {info.block_index}: parallax path '{parallax_path}' does not use the expected _p.dds naming."
                )
                _append_unique(
                    result.suggestions,
                    "Use a dedicated _p.dds height map in texture slot 3 so Skyrim/ENB parallax reads the correct file."
                )
            if diffuse_path and normalized_parallax == diffuse_path.lower().replace("/", "\\"):
                _append_unique(
                    result.issues,
                    f"Block {info.block_index}: parallax slot 3 points at the diffuse texture instead of a _p.dds height map."
                )
            if normal_path and normalized_parallax == normal_path.lower().replace("/", "\\"):
                _append_unique(
                    result.issues,
                    f"Block {info.block_index}: parallax slot 3 points at the normal texture instead of a dedicated height map."
                )
        if not normal_path:
            _append_unique(
                result.suggestions,
                f"Block {info.block_index}: slot 1 normal map is empty; parallax will still patch, but lighting usually looks wrong without a matching _n.dds or _msn.dds."
            )
        if info.has_pom_flag and info.shader_type not in (SHADER_TYPE_HEIGHTMAP, SHADER_TYPE_PARALLAX_OCC):
            _append_unique(
                result.issues,
                f"Block {info.block_index}: POM flag is set on shader type {info.shader_type}; ENB parallax occlusion is more reliable on Heightmap/3 blocks."
            )
        if info.parallax_scale is not None and info.parallax_scale < 0.35:
            _append_unique(
                result.suggestions,
                f"Block {info.block_index}: parallax scale is only {info.parallax_scale:.2f}; increase it if the mesh patches successfully but depth is still invisible in game."
            )

    result.valid = result.shader_count > 0
    return result


def find_nif_files(root: Path) -> list[Path]:
    """Return all ``.nif`` files under *root* (recursive, sorted)."""
    return sorted(root.rglob("*.nif"))


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
    parser.add_argument("--env-mask", metavar="PATH",
                        help="Environment mask texture path (slot 5).")
    parser.add_argument("--env-mapping", action="store_true",
                        help="Set SLSF1_Environment_Mapping flag.")
    parser.add_argument("--no-backup", action="store_true",
                        help="Skip .nif.bak backup.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing.")
    parser.add_argument("--validate", action="store_true",
                        help="Just validate and report, do not patch.")
    args = parser.parse_args()

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
        parallax_texture_path=args.parallax,
        normal_texture_path=args.normal,
        env_mask_texture_path=args.env_mask,
        backup=not args.no_backup,
        dry_run=args.dry_run,
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
