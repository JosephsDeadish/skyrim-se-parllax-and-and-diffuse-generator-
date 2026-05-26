"""Skyrim SE NIF file patcher.

Reads Skyrim SE NIF files (format version 20.2.0.7, user_version=12,
user_version_2=83) and patches ``BSLightingShaderProperty`` shader flags
plus ``BSShaderTextureSet`` texture paths to enable parallax, environment
mapping, and ENB complex-material effects on meshes that shipped without
those flags.

Usage (programmatic)::

    from nif_patcher import patch_nif, NifPatchOptions
    result = patch_nif(Path("mesh.nif"), NifPatchOptions(enable_parallax=True))
    if result.success:
        print(f"Patched {result.shader_properties_patched} shader(s).")

Usage (CLI)::

    python nif_patcher.py mesh.nif --parallax textures/arch/stone_p.dds

No third-party libraries are required — only stdlib ``struct`` and ``pathlib``.
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
_SKYRIM_LE_USER_VERSION_2: int = 34  # Skyrim LE (for awareness; not patched here)

_HEADER_PREFIXES: tuple[bytes, ...] = (
    b"Gamebryo File Format, Version 20.2.0.7\n",
    b"NetImmerse File Format, Version 20.2.0.7\n",
)

# BSLightingShaderProperty – Shader Flags 1
SLSF1_SPECULAR: int = 0x00000001
SLSF1_SKINNED: int = 0x00000002
SLSF1_ENVIRONMENT_MAPPING: int = 0x00000080
SLSF1_CAST_SHADOWS: int = 0x00000200
SLSF1_PARALLAX: int = 0x00000800
SLSF1_MODEL_SPACE_NORMALS: int = 0x00001000
SLSF1_PARALLAX_OCCLUSION: int = 0x10000000
SLSF1_ZBUFFER_TEST: int = 0x80000000

# BSLightingShaderProperty – Shader Flags 2
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
TEXTURE_SLOT_COUNT: int = 9

# BSLightingShaderPropertyShaderType values
SHADER_TYPE_DEFAULT: int = 0
SHADER_TYPE_ENVMAP: int = 1
SHADER_TYPE_HEIGHTMAP: int = 3   # simple offset parallax


# ---------------------------------------------------------------------------
# Patch options
# ---------------------------------------------------------------------------

@dataclass
class NifPatchOptions:
    """Options controlling which shader changes are applied.

    Attributes
    ----------
    enable_parallax:
        Set ``SLSF1_Parallax`` in Shader_Flags_1 and write
        *parallax_texture_path* into texture slot 3.
    enable_env_mapping:
        Set ``SLSF1_Environment_Mapping`` in Shader_Flags_1.
    parallax_texture_path:
        Relative texture path (e.g. ``textures\\arch\\stone_p.dds``).
        Only used when *enable_parallax* is ``True``.  If the NIF already
        contains a texture in slot 3 and this is ``None``, the existing
        path is kept.
    env_mask_texture_path:
        Relative path for the environment mask (slot 5).  Only written
        when *enable_env_mapping* is ``True``.  Existing path kept if
        ``None``.
    normal_texture_path:
        Relative path for the normal map (slot 1).  Set this to your
        ``_msn.dds`` or ``_n.dds`` path; if ``None`` the existing slot is
        kept.
    backup:
        When ``True`` (default), write a ``*.nif.bak`` copy of the
        original file before overwriting.
    """

    enable_parallax: bool = False
    enable_env_mapping: bool = False
    parallax_texture_path: str | None = None
    env_mask_texture_path: str | None = None
    normal_texture_path: str | None = None
    backup: bool = True


# ---------------------------------------------------------------------------
# Patch result
# ---------------------------------------------------------------------------

@dataclass
class NifPatchResult:
    """Outcome of a :func:`patch_nif` call."""

    nif_path: Path
    success: bool
    shader_properties_found: int = 0
    shader_properties_patched: int = 0
    texture_sets_patched: int = 0
    message: str = ""
    backup_path: Path | None = None
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Low-level binary reader / writer
# ---------------------------------------------------------------------------

class _Buf:
    """Mutable byte buffer with read/write helpers."""

    def __init__(self, data: bytes) -> None:
        self._buf = bytearray(data)
        self._pos: int = 0

    # --- position -----------------------------------------------------------

    @property
    def pos(self) -> int:
        return self._pos

    def seek(self, pos: int) -> None:
        self._pos = pos

    def remaining(self) -> int:
        return len(self._buf) - self._pos

    # --- reads --------------------------------------------------------------

    def read_u8(self) -> int:
        v = self._buf[self._pos]
        self._pos += 1
        return v

    def read_u16(self) -> int:
        v = struct.unpack_from("<H", self._buf, self._pos)[0]
        self._pos += 2
        return v

    def read_u32(self) -> int:
        v = struct.unpack_from("<I", self._buf, self._pos)[0]
        self._pos += 4
        return v

    def read_i32(self) -> int:
        v = struct.unpack_from("<i", self._buf, self._pos)[0]
        self._pos += 4
        return v

    def read_sstring_u8(self) -> str:
        """uint8-length-prefixed string (block type names in the header)."""
        n = self.read_u8()
        raw = bytes(self._buf[self._pos: self._pos + n])
        self._pos += n
        return raw.decode("latin-1")

    def read_sstring_u32(self) -> str:
        """uint32-length-prefixed string (texture paths in BSShaderTextureSet)."""
        n = self.read_u32()
        raw = bytes(self._buf[self._pos: self._pos + n])
        self._pos += n
        return raw.decode("latin-1")

    # --- writes (in-place, never resizes) -----------------------------------

    def write_u32_at(self, offset: int, value: int) -> None:
        struct.pack_into("<I", self._buf, offset, value)

    def write_sstring_u32_at(self, offset: int, text: str) -> int:
        """Overwrite a uint32-prefixed string *in place*.

        The new string MUST be the same byte-length as the old one; call
        :func:`_resize_sstring_u32` when lengths differ.  Returns bytes
        consumed (4 + len(text)).
        """
        encoded = text.encode("latin-1")
        struct.pack_into("<I", self._buf, offset, len(encoded))
        self._buf[offset + 4: offset + 4 + len(encoded)] = encoded
        return 4 + len(encoded)

    def replace_sstring_u32_at(self, offset: int, old_text: str, new_text: str) -> bytearray:
        """Return a new bytearray with the string at *offset* replaced.

        Unlike :meth:`write_sstring_u32_at`, this handles length changes by
        rebuilding the buffer.  All byte offsets after *offset* shift
        accordingly; callers must reparse block offsets after calling this.
        """
        old_enc = old_text.encode("latin-1")
        new_enc = new_text.encode("latin-1")
        old_total = 4 + len(old_enc)
        new_total = 4 + len(new_enc)
        new_len_bytes = struct.pack("<I", len(new_enc))
        new_buf = (
            self._buf[:offset]
            + bytearray(new_len_bytes)
            + bytearray(new_enc)
            + self._buf[offset + old_total:]
        )
        return new_buf

    def to_bytes(self) -> bytes:
        return bytes(self._buf)


# ---------------------------------------------------------------------------
# NIF header parser
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
    blocks_start: int   # byte offset where block data begins


def _read_header(buf: _Buf) -> _NifHeader | None:
    """Parse the NIF file header; return ``None`` if not a supported Skyrim SE NIF."""
    # Header string ends with '\n'
    header_line = bytearray()
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
    if endianness != 1:   # 1 = little-endian
        return None

    user_version = buf.read_u32()
    if user_version != _SKYRIM_USER_VERSION:
        return None

    num_blocks = buf.read_u32()

    # BSStreamHeader: user_version_2 + 3 export strings
    user_version_2 = buf.read_u32()
    if user_version_2 not in (_SKYRIM_SE_USER_VERSION_2, _SKYRIM_LE_USER_VERSION_2):
        return None
    for _ in range(3):
        buf.read_sstring_u8()

    # Block type table
    num_block_types = buf.read_u16()
    block_types = [buf.read_sstring_u8() for _ in range(num_block_types)]

    # One uint16 type-index per block
    block_type_indices = [buf.read_u16() for _ in range(num_blocks)]

    # One uint32 size per block (v20.2.0.7+)
    block_sizes = [buf.read_u32() for _ in range(num_blocks)]

    # String table
    num_strings = buf.read_u32()
    _max_string_len = buf.read_u32()
    strings = [buf.read_sstring_u8() for _ in range(num_strings)]

    # Groups (usually 0)
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
    )


# ---------------------------------------------------------------------------
# Block-level readers
# ---------------------------------------------------------------------------

@dataclass
class _ShaderPropBlock:
    """Parsed BSLightingShaderProperty fields we care about."""
    block_index: int
    block_start: int        # byte offset in the file
    flags1_offset: int      # byte offset of shader_flags_1 field
    flags2_offset: int      # byte offset of shader_flags_2 field
    flags1: int
    flags2: int
    texture_set_ref: int    # block index of linked BSShaderTextureSet (-1 = none)


@dataclass
class _TextureSetBlock:
    """Parsed BSShaderTextureSet layout."""
    block_index: int
    block_start: int
    num_textures: int
    # slot_offsets[i] = byte offset of the uint32 length prefix for slot i
    slot_offsets: list[int]
    slot_paths: list[str]


def _parse_shader_prop(buf: _Buf, block_index: int, block_start: int) -> _ShaderPropBlock | None:
    """Parse a BSLightingShaderProperty block starting at *block_start*.

    Layout (NIF 20.2.0.7, user_version=12):
      NiObjectNET  : name_ref(u32) + num_extra(u32) + [extra_refs(u32)*N] + controller_ref(i32)
      BSShaderProperty: flags1(u32) + flags2(u32)
      BSLightingShaderProperty: shader_type(u32) + uv_offset(2f) + uv_scale(2f) + texture_set_ref(i32)
    """
    buf.seek(block_start)
    _name_ref = buf.read_u32()
    num_extra = buf.read_u32()
    for _ in range(num_extra):
        buf.read_u32()   # skip extra-data refs
    _controller_ref = buf.read_i32()

    flags1_offset = buf.pos
    flags1 = buf.read_u32()
    flags2_offset = buf.pos
    flags2 = buf.read_u32()

    _shader_type = buf.read_u32()
    buf.read_u32()  # uv_offset_u (float, read as u32 — we don't care about value)
    buf.read_u32()  # uv_offset_v
    buf.read_u32()  # uv_scale_u
    buf.read_u32()  # uv_scale_v

    texture_set_ref = buf.read_i32()
    return _ShaderPropBlock(
        block_index=block_index,
        block_start=block_start,
        flags1_offset=flags1_offset,
        flags2_offset=flags2_offset,
        flags1=flags1,
        flags2=flags2,
        texture_set_ref=texture_set_ref,
    )


def _parse_texture_set(buf: _Buf, block_index: int, block_start: int) -> _TextureSetBlock | None:
    """Parse a BSShaderTextureSet block starting at *block_start*.

    Layout: num_textures(u32) + N × SizedString(u32 len + chars).
    """
    buf.seek(block_start)
    num_textures = buf.read_u32()
    if num_textures < 1 or num_textures > 16:
        return None  # sanity check

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
# Core patch logic
# ---------------------------------------------------------------------------

def _normalize_texture_path(path: str) -> str:
    """Normalise a texture path to use backslashes (Skyrim convention)."""
    return path.replace("/", "\\")


def _apply_patches(
    data: bytes,
    header: _NifHeader,
    shader_props: list[_ShaderPropBlock],
    texture_sets: dict[int, _TextureSetBlock],
    opts: NifPatchOptions,
) -> tuple[bytes, int, int]:
    """Apply flag and texture-path patches; return (new_data, props_patched, sets_patched)."""
    buf = _Buf(data)
    props_patched = 0
    sets_patched = 0

    for sp in shader_props:
        new_flags1 = sp.flags1
        new_flags2 = sp.flags2

        if opts.enable_parallax:
            new_flags1 |= SLSF1_PARALLAX
        if opts.enable_env_mapping:
            new_flags1 |= SLSF1_ENVIRONMENT_MAPPING

        changed = (new_flags1 != sp.flags1) or (new_flags2 != sp.flags2)
        if changed:
            buf.write_u32_at(sp.flags1_offset, new_flags1)
            buf.write_u32_at(sp.flags2_offset, new_flags2)
            props_patched += 1

        # Patch texture paths in the linked BSShaderTextureSet
        ts = texture_sets.get(sp.texture_set_ref)
        if ts is None:
            continue

        slot_changes: list[tuple[int, str, str]] = []  # (slot, old_path, new_path)

        if opts.enable_parallax and opts.parallax_texture_path is not None:
            old = ts.slot_paths[TEXTURE_SLOT_PARALLAX] if TEXTURE_SLOT_PARALLAX < ts.num_textures else ""
            new = _normalize_texture_path(opts.parallax_texture_path)
            if old != new:
                slot_changes.append((TEXTURE_SLOT_PARALLAX, old, new))

        if opts.normal_texture_path is not None and TEXTURE_SLOT_NORMAL < ts.num_textures:
            old = ts.slot_paths[TEXTURE_SLOT_NORMAL]
            new = _normalize_texture_path(opts.normal_texture_path)
            if old != new:
                slot_changes.append((TEXTURE_SLOT_NORMAL, old, new))

        if opts.enable_env_mapping and opts.env_mask_texture_path is not None:
            old = ts.slot_paths[TEXTURE_SLOT_ENV_MASK] if TEXTURE_SLOT_ENV_MASK < ts.num_textures else ""
            new = _normalize_texture_path(opts.env_mask_texture_path)
            if old != new:
                slot_changes.append((TEXTURE_SLOT_ENV_MASK, old, new))

        if not slot_changes:
            continue

        # Apply string replacements in sorted order (high offset first so
        # earlier offsets stay valid as we rebuild the buffer).
        slot_changes_with_offsets = []
        for slot_idx, old_path, new_path in slot_changes:
            slot_changes_with_offsets.append((ts.slot_offsets[slot_idx], old_path, new_path))
        slot_changes_with_offsets.sort(key=lambda x: x[0], reverse=True)

        for offset, old_path, new_path in slot_changes_with_offsets:
            new_raw = buf.replace_sstring_u32_at(offset, old_path, new_path)
            buf = _Buf(bytes(new_raw))

        sets_patched += 1

    return buf.to_bytes(), props_patched, sets_patched


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def patch_nif(nif_path: Path, opts: NifPatchOptions) -> NifPatchResult:
    """Patch a Skyrim SE NIF file according to *opts*.

    Parameters
    ----------
    nif_path:
        Absolute or relative path to the ``.nif`` file.
    opts:
        What to patch.  At least one of ``enable_parallax`` or
        ``enable_env_mapping`` must be ``True``.

    Returns
    -------
    NifPatchResult
        Always returned (never raises).  Check ``.success`` and
        ``.errors`` for details.
    """
    result = NifPatchResult(nif_path=nif_path, success=False)

    if not opts.enable_parallax and not opts.enable_env_mapping and \
            opts.normal_texture_path is None:
        result.message = "No patches requested (all options disabled)."
        return result

    try:
        data = nif_path.read_bytes()
    except OSError as exc:
        result.errors.append(f"Cannot read file: {exc}")
        result.message = str(exc)
        return result

    buf = _Buf(data)
    header = _read_header(buf)
    if header is None:
        result.errors.append("Not a supported Skyrim SE NIF (version 20.2.0.7 required).")
        result.message = result.errors[-1]
        return result

    # Build per-block start offsets
    block_starts: list[int] = []
    cursor = header.blocks_start
    for size in header.block_sizes:
        block_starts.append(cursor)
        cursor += size

    # Identify and parse BSLightingShaderProperty / BSShaderTextureSet blocks
    shader_props: list[_ShaderPropBlock] = []
    texture_sets: dict[int, _TextureSetBlock] = {}

    for bi, type_idx in enumerate(header.block_type_indices):
        block_type = header.block_types[type_idx] if type_idx < len(header.block_types) else ""
        bstart = block_starts[bi]
        buf_local = _Buf(data)

        if "BSLightingShaderProperty" in block_type:
            result.shader_properties_found += 1
            sp = _parse_shader_prop(buf_local, bi, bstart)
            if sp is not None:
                shader_props.append(sp)
            else:
                result.errors.append(f"Block {bi}: failed to parse BSLightingShaderProperty.")

        elif "BSShaderTextureSet" in block_type:
            ts = _parse_texture_set(buf_local, bi, bstart)
            if ts is not None:
                texture_sets[bi] = ts
            else:
                result.errors.append(f"Block {bi}: failed to parse BSShaderTextureSet.")

    if not shader_props:
        result.message = "No BSLightingShaderProperty blocks found — nothing to patch."
        return result

    # Apply patches
    try:
        new_data, props_patched, sets_patched = _apply_patches(
            data, header, shader_props, texture_sets, opts
        )
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"Patch error: {exc}")
        result.message = str(exc)
        return result

    if props_patched == 0 and sets_patched == 0:
        result.success = True
        result.message = "Already up-to-date — no changes needed."
        return result

    # Backup original
    if opts.backup:
        backup_path = nif_path.with_suffix(".nif.bak")
        try:
            backup_path.write_bytes(data)
            result.backup_path = backup_path
        except OSError as exc:
            result.errors.append(f"Backup failed (continuing): {exc}")

    # Write patched file
    try:
        nif_path.write_bytes(new_data)
    except OSError as exc:
        result.errors.append(f"Cannot write patched file: {exc}")
        result.message = str(exc)
        return result

    result.success = True
    result.shader_properties_patched = props_patched
    result.texture_sets_patched = sets_patched
    result.message = (
        f"Patched {props_patched} shader propert{'y' if props_patched == 1 else 'ies'}, "
        f"{sets_patched} texture set{'s' if sets_patched != 1 else ''}."
    )
    return result


def scan_nif_texture_paths(nif_path: Path) -> dict[int, str]:
    """Return ``{slot_index: texture_path}`` for the first shader found in a NIF.

    Returns an empty dict if the file cannot be parsed.
    """
    try:
        data = nif_path.read_bytes()
    except OSError:
        return {}

    buf = _Buf(data)
    header = _read_header(buf)
    if header is None:
        return {}

    block_starts: list[int] = []
    cursor = header.blocks_start
    for size in header.block_sizes:
        block_starts.append(cursor)
        cursor += size

    shader_props: list[_ShaderPropBlock] = []
    texture_sets: dict[int, _TextureSetBlock] = {}

    for bi, type_idx in enumerate(header.block_type_indices):
        block_type = header.block_types[type_idx] if type_idx < len(header.block_types) else ""
        bstart = block_starts[bi]
        buf_local = _Buf(data)

        if "BSLightingShaderProperty" in block_type:
            sp = _parse_shader_prop(buf_local, bi, bstart)
            if sp is not None:
                shader_props.append(sp)
        elif "BSShaderTextureSet" in block_type:
            ts = _parse_texture_set(buf_local, bi, bstart)
            if ts is not None:
                texture_sets[bi] = ts

    result: dict[int, str] = {}
    for sp in shader_props[:1]:  # first shader only
        ts = texture_sets.get(sp.texture_set_ref)
        if ts:
            for i, path in enumerate(ts.slot_paths):
                if path:
                    result[i] = path
    return result


def find_nif_files(root: Path) -> list[Path]:
    """Return all ``.nif`` files under *root* (recursive)."""
    return sorted(root.rglob("*.nif"))


def guess_parallax_path_for_nif(nif_path: Path, textures_root: Path | None = None) -> str | None:
    """Heuristically guess the ``_p.dds`` path for a NIF.

    Looks at the diffuse texture currently set in the NIF's first shader and
    derives a ``<stem>_p.dds`` sibling path.  Returns ``None`` when no
    diffuse path is found.
    """
    paths = scan_nif_texture_paths(nif_path)
    diffuse = paths.get(TEXTURE_SLOT_DIFFUSE, "")
    if not diffuse:
        return None
    diffuse_path = Path(diffuse.replace("\\", "/"))
    stem = diffuse_path.stem
    # Remove common diffuse suffixes (_d, _diff, _diffuse) if present
    for suffix in ("_d", "_diff", "_diffuse"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    parallax_name = stem + "_p.dds"
    return str(diffuse_path.parent / parallax_name).replace("/", "\\")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main() -> None:  # pragma: no cover
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Patch Skyrim SE NIF files to enable parallax / env mapping.",
    )
    parser.add_argument("nif", nargs="+", type=Path, help="NIF file(s) or folder(s) to patch.")
    parser.add_argument("--parallax", metavar="PATH", help="Parallax texture path (slot 3).")
    parser.add_argument("--normal", metavar="PATH", help="Normal/MSN texture path (slot 1).")
    parser.add_argument("--env-mask", metavar="PATH", help="Environment mask texture path (slot 5).")
    parser.add_argument("--enable-env-mapping", action="store_true", help="Set SLSF1_Environment_Mapping flag.")
    parser.add_argument("--no-backup", action="store_true", help="Skip writing .nif.bak backup.")
    args = parser.parse_args()

    opts = NifPatchOptions(
        enable_parallax=bool(args.parallax),
        enable_env_mapping=args.enable_env_mapping,
        parallax_texture_path=args.parallax,
        env_mask_texture_path=args.env_mask,
        normal_texture_path=args.normal,
        backup=not args.no_backup,
    )

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

    ok = 0
    for nif in nif_files:
        res = patch_nif(nif, opts)
        status = "OK" if res.success else "FAIL"
        print(f"[{status}] {nif.name}: {res.message}")
        for err in res.errors:
            print(f"       {err}", file=sys.stderr)
        if res.success:
            ok += 1

    print(f"\n{ok}/{len(nif_files)} files patched.")
    if ok < len(nif_files):
        sys.exit(1)


if __name__ == "__main__":
    _main()
