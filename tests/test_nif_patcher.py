"""Tests for nif_patcher.py."""
from __future__ import annotations

import io
import json
import shutil
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from nif_patcher import (
    SLSF1_ENVIRONMENT_MAPPING,
    SLSF1_SINGLE_PASS,
    SLSF1_PARALLAX,
    SLSF1_PARALLAX_OCCLUSION,
    SLSF2_GLOW_MAP,
    SLSF2_UNUSED01,
    SLSF2_VERTEX_COLORS,
    SHADER_TYPE_DEFAULT,
    SHADER_TYPE_ENVMAP,
    SHADER_TYPE_GLOW,
    SHADER_TYPE_HEIGHTMAP,
    SHADER_TYPE_MULTILAYER,
    SHADER_TYPE_NAMES,
    TEXTURE_SLOT_DIFFUSE,
    TEXTURE_SLOT_CUBEMAP,
    TEXTURE_SLOT_ENV_MASK,
    TEXTURE_SLOT_GLOW,
    TEXTURE_SLOT_NORMAL,
    TEXTURE_SLOT_PARALLAX,
    NifPatchOptions,
    NifPatchResult,
    find_nif_files,
    guess_cubemap_path_for_nif,
    guess_env_mask_path_for_nif,
    guess_glow_path_for_nif,
    guess_normal_path_for_nif,
    guess_parallax_path_for_nif,
    batch_patch_nif,
    patch_nif,
    scan_nif,
    summarize_plugin_aware_validation_conflicts,
    summarize_validation_conflicts,
    build_auto_remediation_patch_options,
    build_compatibility_report_text,
    build_parity_delta_report_text,
    build_game_profile_support_matrix,
    auto_remediate_nif_conflicts,
    scan_nif_diagnostics,
    validate_nif_for_parallax,
    NifPluginConflictRef,
    _main as nif_patcher_main,
    _Buf,
    _build_block_map,
    _actions_for_conflict_code,
    _classify_conflict_code,
    _classify_shader_type_resolution,
    _is_retryable_force_type3_error,
    _renderer_compatibility,
    _read_header,
    RESOLUTION_RESOLVED,
    RESOLUTION_UNRESOLVED,
    RESOLUTION_WEAK,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_FIXTURE_CORPUS_MANIFEST = _FIXTURE_DIR / "nif_fixture_corpus.json"
_FIXTURE_CORPUS_BASELINE = _FIXTURE_DIR / "nif_fixture_corpus_baseline.json"
_FIXTURE_PARITY_SAMPLE_MATRIX = _FIXTURE_DIR / "nif_parity_sample_matrix.json"
_FIXTURE_REALMOD_SAMPLE_PACKS = _FIXTURE_DIR / "nif_realmod_sample_packs.json"


# ---------------------------------------------------------------------------
# Minimal synthetic Skyrim SE NIF builder
# ---------------------------------------------------------------------------

def _sstring_u8(text: str) -> bytes:
    enc = text.encode("latin-1")
    enc += b"\x00"
    return struct.pack("B", len(enc)) + enc


def _sstring_u32(text: str) -> bytes:
    enc = text.encode("latin-1")
    return struct.pack("<I", len(enc)) + enc


def _build_shader_block(
    *,
    shader_type: int = SHADER_TYPE_DEFAULT,
    flags1: int = 0,
    flags2: int = 0,
    parallax_scale: float | None = None,
    env_map_scale: float | None = None,
    texture_set_ref: int = 0,
) -> bytes:
    """Build a single BSLightingShaderProperty block body."""
    # Skyrim/SE BSLightingShaderProperty starts with NiObjectNET, then shader_type.
    # NiObjectNET: name_ref + num_extra + controller_ref
    nio = struct.pack("<IIi", 0, 0, -1)
    shader_type_field = struct.pack("<I", shader_type)
    flags = struct.pack("<II", flags1, flags2)
    uv = struct.pack("<ffff", 0.0, 0.0, 1.0, 1.0)
    tsref = struct.pack("<i", texture_set_ref)
    emit = struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0)
    misc = struct.pack("<Ifff", 3, 1.0, 0.0, 80.0)
    spec = struct.pack("<ffff", 1.0, 1.0, 1.0, 1.0)
    light = struct.pack("<ff", 0.3, 2.0)

    body = nio + shader_type_field + flags + uv + tsref + emit + misc + spec + light
    if shader_type == SHADER_TYPE_HEIGHTMAP:
        scale = parallax_scale if parallax_scale is not None else 1.0
        body += struct.pack("<ff", 4.0, scale)
    elif shader_type == SHADER_TYPE_ENVMAP:
        # Env map scale is the first type-specific field after the common section
        scale = env_map_scale if env_map_scale is not None else 1.0
        body += struct.pack("<f", scale)
    return body


def _build_real_shader_block(
    *,
    shader_type: int = SHADER_TYPE_DEFAULT,
    flags1: int = 0,
    flags2: int = 0,
    parallax_scale: float | None = None,
    env_map_scale: float | None = None,
    texture_set_ref: int = 0,
) -> bytes:
    """Build a Skyrim-style BSLightingShaderProperty without a standalone shader_type field."""
    nio = struct.pack("<IIi", 0, 0, -1)
    flags = struct.pack("<II", flags1, flags2)
    uv = struct.pack("<ffff", 0.0, 0.0, 1.0, 1.0)
    tsref = struct.pack("<i", texture_set_ref)
    emit = struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0)
    misc = struct.pack("<Ifff", 3, 1.0, 0.0, 80.0)
    spec = struct.pack("<ffff", 1.0, 1.0, 1.0, 1.0)
    light = struct.pack("<ff", 0.3, 2.0)

    body = nio + flags + uv + tsref + emit + misc + spec + light
    if shader_type == SHADER_TYPE_HEIGHTMAP:
        scale = parallax_scale if parallax_scale is not None else 1.0
        body += struct.pack("<ff", 4.0, scale)
    elif shader_type == SHADER_TYPE_ENVMAP:
        scale = env_map_scale if env_map_scale is not None else 1.0
        body += struct.pack("<f", scale)
    return body


def _build_texture_set_block(
    *,
    texture_paths: list[str] | None = None,
    texture_set_layout_shift: int = 0,
    texture_set_count_u16: bool = False,
) -> bytes:
    """Build a single BSShaderTextureSet block body."""
    if texture_paths is None:
        texture_paths = [""] * 9
    slot_count = len(texture_paths)
    layout_pad = b"\x00\x00\x00\x00" if texture_set_layout_shift == 4 else b""
    if texture_set_count_u16:
        count_bytes = struct.pack("<H", slot_count)
    else:
        count_bytes = struct.pack("<I", slot_count)
    body = layout_pad + count_bytes
    for path in texture_paths:
        body += _sstring_u32(path)
    return body


def _build_minimal_nif(
    *,
    shader_type: int = SHADER_TYPE_DEFAULT,
    flags1: int = 0,
    flags2: int = 0,
    parallax_scale: float | None = None,
    env_map_scale: float | None = None,
    texture_paths: list[str] | None = None,
    shader_block_type: str = "BSLightingShaderProperty",
    user_ver2: int = 83,
    header_line_ending: bytes = b"\n",
    texture_set_layout_shift: int = 0,
    texture_set_count_u16: bool = False,
    extra_shader_blocks: list[dict] | None = None,
    shader_layout: str = "legacy",
) -> bytes:
    """Build a minimal but structurally valid Skyrim SE NIF in memory.

    Contains at least two blocks:
      0 – BSShaderTextureSet   (9 texture slots)
      1 – BSLightingShaderProperty  (references block 0)

    When *extra_shader_blocks* is provided, each dict entry is passed as
    kwargs to :func:`_build_shader_block` and an additional BSShaderTextureSet
    (with all-empty slots) is added for each extra shader.  Block indices are
    assigned sequentially: TS0, SP0, TS1, SP1, ...

    When *texture_set_count_u16* is True the count field is written as a
    u16 (Skyrim LE / mixed-export format) instead of u32 (SE native).
    """
    if texture_paths is None:
        texture_paths = [""] * 9

    # --- Primary blocks -------------------------------------------------
    ts0_body = _build_texture_set_block(
        texture_paths=texture_paths,
        texture_set_layout_shift=texture_set_layout_shift,
        texture_set_count_u16=texture_set_count_u16,
    )
    shader_builder = _build_shader_block if shader_layout == "legacy" else _build_real_shader_block
    sp0_body = shader_builder(
        shader_type=shader_type,
        flags1=flags1,
        flags2=flags2,
        parallax_scale=parallax_scale,
        env_map_scale=env_map_scale,
        texture_set_ref=0,
    )

    # --- Extra shader blocks --------------------------------------------
    extra_bodies: list[tuple[bytes, bytes]] = []
    for extra in (extra_shader_blocks or []):
        extra_copy = dict(extra)
        extra_texture_paths_raw = extra_copy.pop("texture_paths", None)
        extra_texture_paths = (
            [str(path) for path in extra_texture_paths_raw]
            if isinstance(extra_texture_paths_raw, list) and len(extra_texture_paths_raw) == 9
            else None
        )
        extra_texture_set_layout_shift = int(extra_copy.pop("texture_set_layout_shift", texture_set_layout_shift))
        extra_texture_set_count_u16 = bool(extra_copy.pop("texture_set_count_u16", texture_set_count_u16))
        ts_idx = 2 + len(extra_bodies) * 2
        ts_body = _build_texture_set_block(
            texture_paths=extra_texture_paths,
            texture_set_layout_shift=extra_texture_set_layout_shift,
            texture_set_count_u16=extra_texture_set_count_u16,
        )
        sp_body = shader_builder(texture_set_ref=ts_idx, **extra_copy)
        extra_bodies.append((ts_body, sp_body))

    # --- Assemble block list --------------------------------------------
    # order: TS0, SP0, TS1, SP1, ...
    block_type_names = ["BSShaderTextureSet", shader_block_type]
    all_blocks: list[tuple[int, bytes]] = [
        (0, ts0_body),   # type_idx 0 = BSShaderTextureSet
        (1, sp0_body),   # type_idx 1 = shader_block_type
    ]
    for ts_body, sp_body in extra_bodies:
        all_blocks.append((0, ts_body))
        all_blocks.append((1, sp_body))

    num_blks = len(all_blocks)
    type_indices_bytes = b"".join(struct.pack("<H", ti) for ti, _ in all_blocks)
    block_sizes_bytes = b"".join(struct.pack("<I", len(body)) for _, body in all_blocks)

    # --- Header ---
    header_str = b"Gamebryo File Format, Version 20.2.0.7" + header_line_ending
    version = struct.pack("<I", 0x14020007)
    endian = struct.pack("B", 1)
    user_ver = struct.pack("<I", 12)
    num_blocks_bytes = struct.pack("<I", num_blks)
    bs_version = user_ver2
    user_ver2 = struct.pack("<I", bs_version)
    # BSStreamHeader export strings depend on BS version (user_ver2 field).
    # Skyrim SE commonly uses 83/100; CK-style exports can use 130.
    export = _sstring_u8("")  # author
    if bs_version > 130:
        export += struct.pack("<I", 0)  # unknown int (FO4+ style)
    if bs_version < 131:
        export += _sstring_u8("")  # process script
    export += _sstring_u8("")  # export script
    if bs_version >= 103:
        export += _sstring_u8("")  # max filepath
    num_block_types = struct.pack("<H", len(block_type_names))
    btypes = b"".join(_sstring_u32(t) for t in block_type_names)
    string_table = struct.pack("<II", 0, 0)
    # num_groups field: present in NIF 20.2.0.7 when user_version_2 < 130.
    # Skyrim SE (user_version_2=83 or 100) always has this field set to 0.
    # Without it the block-data offset is 4 bytes early, causing corrupted
    # patches and in-game crashes.
    num_groups = struct.pack("<I", 0)

    header = (
        header_str + version + endian + user_ver + num_blocks_bytes
        + user_ver2 + export + num_block_types + btypes
        + type_indices_bytes + block_sizes_bytes + string_table + num_groups
    )
    return header + b"".join(body for _, body in all_blocks)


def _write_nif(tmp_dir: Path, **kwargs: object) -> Path:
    p = tmp_dir / "test.nif"
    p.write_bytes(_build_minimal_nif(**kwargs))
    return p


def _rewrite_user_version(path: Path, value: int) -> None:
    raw = bytearray(path.read_bytes())
    user_version_offset = len(b"Gamebryo File Format, Version 20.2.0.7\n") + 4 + 1
    struct.pack_into("<I", raw, user_version_offset, value)
    path.write_bytes(bytes(raw))


def _rewrite_user_version_2(path: Path, value: int) -> None:
    raw = bytearray(path.read_bytes())
    user_version_2_offset = len(b"Gamebryo File Format, Version 20.2.0.7\n") + 4 + 1 + 4 + 4
    struct.pack_into("<I", raw, user_version_2_offset, value)
    path.write_bytes(bytes(raw))


def _rewrite_num_blocks(path: Path, delta: int) -> None:
    raw = bytearray(path.read_bytes())
    header_line_len = len(b"Gamebryo File Format, Version 20.2.0.7\n")
    num_blocks_offset = header_line_len + 4 + 1 + 4
    current = struct.unpack_from("<I", raw, num_blocks_offset)[0]
    patched = max(0, current + int(delta))
    struct.pack_into("<I", raw, num_blocks_offset, patched)
    path.write_bytes(bytes(raw))


def _shader_block_indices_for_fixture(path: Path) -> list[int]:
    data = path.read_bytes()
    header = _read_header(_Buf(data))
    if header is None:
        return []
    return [idx for idx, type_idx in enumerate(header.block_type_idx) if type_idx == 1]


def _patch_shader_size_delta(path: Path, shader_ordinal: int, delta: int) -> None:
    data = path.read_bytes()
    header = _read_header(_Buf(data))
    if header is None:
        return
    shader_blocks = _shader_block_indices_for_fixture(path)
    if shader_ordinal < 0 or shader_ordinal >= len(shader_blocks):
        return
    block_index = shader_blocks[shader_ordinal]
    offset = header.block_sizes_offset + block_index * 4
    current = struct.unpack_from("<I", data, offset)[0]
    patched = max(1, current + int(delta))
    raw = bytearray(data)
    struct.pack_into("<I", raw, offset, patched)
    path.write_bytes(bytes(raw))


def _patch_block_size_delta(path: Path, block_ordinal: int, delta: int) -> None:
    data = path.read_bytes()
    header = _read_header(_Buf(data))
    if header is None:
        return
    if block_ordinal < 0 or block_ordinal >= int(header.num_blocks):
        return
    offset = int(header.block_sizes_offset) + int(block_ordinal) * 4
    if offset + 4 > len(data):
        return
    current = struct.unpack_from("<I", data, offset)[0]
    patched = max(1, int(current) + int(delta))
    raw = bytearray(data)
    struct.pack_into("<I", raw, offset, patched)
    path.write_bytes(bytes(raw))


def _patch_shader_texture_set_ref(path: Path, shader_ordinal: int, texture_set_ref: int, *, shader_layout: str) -> None:
    data = path.read_bytes()
    header = _read_header(_Buf(data))
    if header is None:
        return
    shader_blocks = _shader_block_indices_for_fixture(path)
    if shader_ordinal < 0 or shader_ordinal >= len(shader_blocks):
        return
    block_starts = [header.blocks_start]
    for size in header.block_sizes[:-1]:
        block_starts.append(block_starts[-1] + size)
    block_index = shader_blocks[shader_ordinal]
    block_start = block_starts[block_index]
    ref_offset = block_start + (40 if shader_layout == "legacy" else 36)
    raw = bytearray(data)
    if ref_offset + 4 > len(raw):
        return
    struct.pack_into("<i", raw, ref_offset, int(texture_set_ref))
    path.write_bytes(bytes(raw))


def _locate_header_table_offsets(path: Path) -> tuple[int, int, int] | None:
    data = path.read_bytes()
    line_end = data.find(b"\n")
    if line_end < 0:
        return None
    pos = line_end + 1
    if pos + 17 > len(data):
        return None
    # version (4) + endian (1) + user_version (4) + num_blocks (4) + user_version_2 (4)
    pos += 4 + 1 + 4 + 4
    user_ver2 = struct.unpack_from("<I", data, pos)[0]
    pos += 4

    def _read_u8_string_offset(start: int) -> int:
        if start >= len(data):
            return len(data)
        slen = data[start]
        return start + 1 + int(slen)

    pos = _read_u8_string_offset(pos)  # author
    if user_ver2 > 130:
        pos += 4
    if user_ver2 < 131:
        pos = _read_u8_string_offset(pos)  # process script
    pos = _read_u8_string_offset(pos)  # export script
    if user_ver2 >= 103:
        pos = _read_u8_string_offset(pos)  # max filepath
    if pos + 2 > len(data):
        return None
    num_block_types_offset = pos
    num_block_types = struct.unpack_from("<H", data, pos)[0]
    pos += 2
    for _ in range(num_block_types):
        if pos + 4 > len(data):
            return None
        name_len = struct.unpack_from("<I", data, pos)[0]
        pos += 4 + int(name_len)
    block_type_indices_offset = pos
    header = _read_header(_Buf(data))
    if header is None:
        return None
    return num_block_types_offset, block_type_indices_offset, int(header.num_blocks)


def _patch_num_block_types_delta(path: Path, delta: int) -> None:
    offsets = _locate_header_table_offsets(path)
    if offsets is None:
        return
    num_block_types_offset, _, _ = offsets
    data = bytearray(path.read_bytes())
    current = struct.unpack_from("<H", data, num_block_types_offset)[0]
    patched = max(1, min(65535, int(current) + int(delta)))
    struct.pack_into("<H", data, num_block_types_offset, patched)
    path.write_bytes(bytes(data))


def _patch_block_type_index(path: Path, block_ordinal: int, value: int) -> None:
    offsets = _locate_header_table_offsets(path)
    if offsets is None:
        return
    _, block_type_indices_offset, num_blocks = offsets
    if block_ordinal < 0 or block_ordinal >= num_blocks:
        return
    data = bytearray(path.read_bytes())
    offset = block_type_indices_offset + block_ordinal * 2
    if offset + 2 > len(data):
        return
    struct.pack_into("<H", data, offset, max(0, min(65535, int(value))))
    path.write_bytes(bytes(data))


def _apply_fixture_post_mutations(target: Path, entry: dict[str, object], *, shader_layout: str) -> None:
    user_version_2_override = entry.get("user_ver2_override")
    if user_version_2_override is not None:
        _rewrite_user_version_2(target, int(user_version_2_override))
    num_blocks_delta = entry.get("num_blocks_delta")
    if num_blocks_delta is not None:
        _rewrite_num_blocks(target, int(num_blocks_delta))
    num_block_types_delta = entry.get("num_block_types_delta")
    if num_block_types_delta is not None:
        _patch_num_block_types_delta(target, int(num_block_types_delta))
    block_type_index_overrides = entry.get("block_type_index_overrides")
    if isinstance(block_type_index_overrides, list):
        for override in block_type_index_overrides:
            if not isinstance(override, dict):
                continue
            ordinal = int(override.get("ordinal", -1))
            value = int(override.get("value", 65535))
            _patch_block_type_index(target, ordinal, value)
    shader_size_delta = entry.get("shader_size_delta")
    if shader_size_delta is not None:
        _patch_shader_size_delta(target, 0, int(shader_size_delta))
    extra_shader_size_deltas = entry.get("extra_shader_size_deltas")
    if isinstance(extra_shader_size_deltas, list):
        for idx, delta in enumerate(extra_shader_size_deltas, start=1):
            _patch_shader_size_delta(target, idx, int(delta))
    block_size_deltas = entry.get("block_size_deltas")
    if isinstance(block_size_deltas, list):
        for idx, delta in enumerate(block_size_deltas):
            _patch_block_size_delta(target, idx, int(delta))
    block_size_delta_overrides = entry.get("block_size_delta_overrides")
    if isinstance(block_size_delta_overrides, list):
        for override in block_size_delta_overrides:
            if not isinstance(override, dict):
                continue
            ordinal = int(override.get("ordinal", -1))
            delta = int(override.get("delta", 0))
            _patch_block_size_delta(target, ordinal, delta)
    shader_texture_set_ref = entry.get("shader_texture_set_ref")
    if shader_texture_set_ref is not None:
        _patch_shader_texture_set_ref(
            target,
            0,
            int(shader_texture_set_ref),
            shader_layout=shader_layout,
        )
    extra_shader_texture_set_refs = entry.get("extra_shader_texture_set_refs")
    if isinstance(extra_shader_texture_set_refs, list):
        for idx, ref in enumerate(extra_shader_texture_set_refs, start=1):
            _patch_shader_texture_set_ref(
                target,
                idx,
                int(ref),
                shader_layout=shader_layout,
            )


def _load_fixture_corpus_payload(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise AssertionError(f"Invalid fixture payload in {path}: expected object root.")
    return raw


def _materialize_fixture_corpus(temp_root: Path, payload: dict[str, object]) -> list[Path]:
    cases = payload.get("cases", [])
    if not isinstance(cases, list):
        raise AssertionError("Fixture payload must include a list under 'cases'.")
    created: list[Path] = []
    for entry in cases:
        if not isinstance(entry, dict):
            continue
        case_id = str(entry.get("id", "case")).strip() or "case"
        target = temp_root / f"{case_id}.nif"
        if bool(entry.get("broken_header", False)):
            truncated = int(entry.get("truncated_bytes", 90))
            target.write_bytes(_build_minimal_nif()[: max(16, truncated)])
            created.append(target)
            continue

        line_ending = b"\r\n" if str(entry.get("header_line_ending", "lf")).lower() == "crlf" else b"\n"
        raw_texture_paths = entry.get("texture_paths")
        texture_paths = (
            [str(path) for path in raw_texture_paths]
            if isinstance(raw_texture_paths, list) and len(raw_texture_paths) == 9
            else ["textures\\arch\\stone.dds"] + [""] * 8
        )
        raw_extra_blocks = entry.get("extra_shader_blocks")
        extra_shader_blocks: list[dict[str, object]] = []
        if isinstance(raw_extra_blocks, list):
            for block in raw_extra_blocks:
                if isinstance(block, dict):
                    normalized_block: dict[str, object] = dict(block)
                    raw_block_paths = normalized_block.get("texture_paths")
                    if isinstance(raw_block_paths, list):
                        normalized_block["texture_paths"] = [str(path) for path in raw_block_paths]
                    extra_shader_blocks.append(normalized_block)
        shader_layout = str(entry.get("shader_layout", "legacy"))
        raw = _build_minimal_nif(
            shader_layout=shader_layout,
            shader_type=int(entry.get("shader_type", SHADER_TYPE_DEFAULT)),
            flags1=int(entry.get("flags1", 0)),
            flags2=int(entry.get("flags2", 0)),
            user_ver2=int(entry.get("user_ver2", 83)),
            header_line_ending=line_ending,
            texture_set_layout_shift=int(entry.get("texture_set_layout_shift", 0)),
            texture_set_count_u16=bool(entry.get("texture_set_count_u16", False)),
            texture_paths=texture_paths,
            extra_shader_blocks=extra_shader_blocks,
        )
        target.write_bytes(raw)
        user_version = entry.get("user_version")
        if user_version is not None:
            _rewrite_user_version(target, int(user_version))
        _apply_fixture_post_mutations(target, entry, shader_layout=shader_layout)
        created.append(target)
    return created


def _texture_set_slot_count(nif_path: Path) -> int:
    data = nif_path.read_bytes()
    header = _read_header(_Buf(data))
    if header is None:
        return 0
    _, texture_sets, _ = _build_block_map(data, header)
    if not texture_sets:
        return 0
    return next(iter(texture_sets.values())).num_textures


# ---------------------------------------------------------------------------
# Tests: basic parsing
# ---------------------------------------------------------------------------

class TestScanNif(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_scan_returns_one_shader_for_minimal_nif(self) -> None:
        nif = _write_nif(self.tmp)
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)

    def test_scan_accepts_user_version_2_100(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=100)
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)

    def test_scan_accepts_user_version_2_130(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130)
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)

    def test_scan_accepts_user_version_2_34(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=34)
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)

    def test_scan_rejects_unknown_user_version_2(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=155)
        infos, diagnostics = scan_nif_diagnostics(nif)
        self.assertEqual(infos, [])
        self.assertTrue(any("unexpected user version values" in d.lower() for d in diagnostics), diagnostics)

    def test_scan_reports_fallout_header_as_experimental(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130)
        _rewrite_user_version(nif, 11)
        infos, diagnostics = scan_nif_diagnostics(nif)
        self.assertEqual(len(infos), 1)
        joined = "\n".join(diagnostics).lower()
        self.assertIn("fallout", joined)
        self.assertIn("experimental", joined)
        self.assertNotIn("skipped unsupported-layout shader blocks", joined)

    def test_scan_fallout_real_layout_returns_shader_info(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130, shader_layout="real")
        _rewrite_user_version(nif, 11)
        infos, diagnostics = scan_nif_diagnostics(nif)
        self.assertEqual(len(infos), 1)
        joined = "\n".join(diagnostics).lower()
        self.assertIn("fallout", joined)

    def test_validate_detects_fallout_profile(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130)
        _rewrite_user_version(nif, 11)
        validation = validate_nif_for_parallax(nif)
        self.assertEqual(validation.detected_game_profile, "fallout")
        self.assertTrue(
            any("experimental_fallout_write" in s.lower() for s in validation.suggestions),
            validation.suggestions,
        )

    def test_validate_keeps_unknown_for_non_fallout_user11_combo(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=83)
        _rewrite_user_version(nif, 11)
        validation = validate_nif_for_parallax(nif)
        self.assertEqual(validation.detected_game_profile, "unknown")

    def test_validate_treats_user12_130_as_skyrim_profile(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130)
        validation = validate_nif_for_parallax(nif)
        self.assertEqual(validation.detected_game_profile, "skyrim")

    def test_validate_treats_user12_131_as_fallout_profile(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=131)
        validation = validate_nif_for_parallax(nif)
        self.assertEqual(validation.detected_game_profile, "fallout")

    def test_validate_treats_user11_133_as_fallout_profile(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=133)
        _rewrite_user_version(nif, 11)
        validation = validate_nif_for_parallax(nif)
        self.assertEqual(validation.detected_game_profile, "fallout")

    def test_validate_treats_user12_133_as_fallout_profile(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=133)
        validation = validate_nif_for_parallax(nif)
        self.assertEqual(validation.detected_game_profile, "fallout")

    def test_scan_accepts_crlf_header_line(self) -> None:
        nif = _write_nif(self.tmp, header_line_ending=b"\r\n")
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)

    def test_scan_detects_shader_type(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_HEIGHTMAP, parallax_scale=1.5)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_HEIGHTMAP)
        self.assertAlmostEqual(infos[0].parallax_scale or 0.0, 1.5, places=3)

    def test_scan_detects_shifted_texture_set_layout(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths, texture_set_layout_shift=4)
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].texture_paths.get(TEXTURE_SLOT_DIFFUSE), "textures\\arch\\stone.dds")

    def test_scan_decodes_packed_shader_type_value(self) -> None:
        nif = _write_nif(self.tmp, shader_type=0x82400301)
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_ENVMAP)

    def test_scan_prefers_real_skyrim_shader_layout(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_layout="real",
            flags1=0x82400301,
        )
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_DEFAULT)

    def test_scan_reads_env_map_scale_from_real_skyrim_layout(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_layout="real",
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=0x82400301,
            env_map_scale=2.5,
        )
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_ENVMAP)
        self.assertAlmostEqual(infos[0].env_map_scale or 0.0, 2.5, places=3)

    def test_scan_real_layout_tolerates_extended_shader_payload(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_layout="real",
            flags1=SLSF1_PARALLAX,
        )
        raw = bytearray(nif.read_bytes())
        header = _read_header(_Buf(bytes(raw)))
        self.assertIsNotNone(header)
        assert header is not None
        block_starts = [header.blocks_start]
        for size in header.block_sizes[:-1]:
            block_starts.append(block_starts[-1] + size)
        shader_block_index = 1
        shader_start = block_starts[shader_block_index]
        shader_end = shader_start + header.block_sizes[shader_block_index]
        raw[shader_end:shader_end] = struct.pack("<III", 1, 2, 3)
        shader_size_offset = header.block_sizes_offset + shader_block_index * 4
        struct.pack_into("<I", raw, shader_size_offset, header.block_sizes[shader_block_index] + 12)
        nif.write_bytes(bytes(raw))

        infos, diagnostics = scan_nif_diagnostics(nif)
        self.assertEqual(len(infos), 1)
        self.assertFalse(
            any("failed to parse BSLightingShaderProperty" in line for line in diagnostics),
            diagnostics,
        )

    def test_scan_real_layout_extended_envmap_payload_keeps_envmap(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_layout="real",
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=SLSF1_ENVIRONMENT_MAPPING,
            env_map_scale=1.0,
        )
        raw = bytearray(nif.read_bytes())
        header = _read_header(_Buf(bytes(raw)))
        self.assertIsNotNone(header)
        assert header is not None
        block_starts = [header.blocks_start]
        for size in header.block_sizes[:-1]:
            block_starts.append(block_starts[-1] + size)
        shader_block_index = 1
        shader_start = block_starts[shader_block_index]
        shader_end = shader_start + header.block_sizes[shader_block_index]
        raw[shader_end:shader_end] = struct.pack("<I", 1234)
        shader_size_offset = header.block_sizes_offset + shader_block_index * 4
        struct.pack_into("<I", raw, shader_size_offset, header.block_sizes[shader_block_index] + 4)
        nif.write_bytes(bytes(raw))

        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_ENVMAP)

    def test_scan_reads_texture_paths(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].texture_paths.get(TEXTURE_SLOT_DIFFUSE), "textures\\arch\\stone.dds")

    def test_scan_parses_u16_count_texture_set_with_empty_paths(self) -> None:
        """LE-format BSShaderTextureSet with u16 count and all-empty paths."""
        nif = _write_nif(self.tmp, texture_set_count_u16=True)
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)

    def test_scan_parses_u16_count_texture_set_with_nonempty_paths(self) -> None:
        """LE-format BSShaderTextureSet with u16 count and actual texture paths.

        Previously the u32 read of the count would incorporate path bytes,
        producing a count > 64 and causing ``failed to parse BSShaderTextureSet``.
        """
        paths = ["textures\\dungeons\\barrels\\barrel01_d.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths, texture_set_count_u16=True)
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_DIFFUSE),
            "textures\\dungeons\\barrels\\barrel01_d.dds",
        )

    def test_scan_u16_count_no_parse_error_in_diagnostics(self) -> None:
        """Scanning a u16-count NIF must not produce BSShaderTextureSet parse errors."""
        paths = ["textures\\things\\coin01_d.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths, texture_set_count_u16=True)
        _infos, diagnostics = scan_nif_diagnostics(nif)
        ts_parse_errors = [d for d in diagnostics if "failed to parse BSShaderTextureSet" in d]
        self.assertEqual(ts_parse_errors, [], msg=f"Unexpected parse errors: {ts_parse_errors}")

    def test_scan_ignores_shader_controller_block_name(self) -> None:
        """Only exact BSLightingShaderProperty blocks should be parsed as shader blocks."""
        nif = _write_nif(self.tmp, shader_block_type="BSLightingShaderPropertyFloatController")
        infos, diagnostics = scan_nif_diagnostics(nif)
        self.assertEqual(infos, [])
        self.assertFalse(any("shader parse error" in d.lower() for d in diagnostics))

    def test_scan_does_not_raise_out_of_range_for_invalid_num_extra(self) -> None:
        nif = _write_nif(self.tmp)
        raw = bytearray(nif.read_bytes())
        shader_header = struct.pack("<IIiI", 0, 0, -1, SHADER_TYPE_DEFAULT)
        shader_start = raw.find(shader_header)
        self.assertNotEqual(shader_start, -1)
        struct.pack_into("<I", raw, shader_start + 4, 0xFFFFFFFF)
        nif.write_bytes(raw)
        # Must not crash and must not produce a "u32 read out of range" error.
        # The block may be parsed successfully via the num_extra=0 fallback
        # (because the underlying data is still valid) or rejected with a
        # descriptive diagnostic — both outcomes are acceptable.
        _infos, diagnostics = scan_nif_diagnostics(nif)
        self.assertFalse(any("u32 read out of range" in d.lower() for d in diagnostics))

    def test_patch_recovers_from_invalid_num_extra_with_tolerant_fallback(self) -> None:
        nif = _write_nif(self.tmp)
        raw = bytearray(nif.read_bytes())
        shader_header = struct.pack("<IIiI", 0, 0, -1, SHADER_TYPE_DEFAULT)
        shader_start = raw.find(shader_header)
        self.assertNotEqual(shader_start, -1)
        struct.pack_into("<I", raw, shader_start + 4, 0xFFFFFFFF)
        nif.write_bytes(raw)

        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        self.assertTrue(result.success, result.errors)
        self.assertTrue(
            any("tolerant niobjectnet fallback" in warning.lower() for warning in result.warnings),
            result.warnings,
        )
        self.assertGreater(result.shader_properties_patched, 0)

    def test_tolerant_fallback_auto_restores_invalid_parallax_flags_when_slot_missing(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_HEIGHTMAP,
            flags1=SLSF1_PARALLAX | SLSF1_PARALLAX_OCCLUSION,
            texture_paths=["textures\\stone.dds"] + [""] * 8,
        )
        raw = bytearray(nif.read_bytes())
        shader_header = struct.pack("<IIiI", 0, 0, -1, SHADER_TYPE_HEIGHTMAP)
        shader_start = raw.find(shader_header)
        self.assertNotEqual(shader_start, -1)
        struct.pack_into("<I", raw, shader_start + 4, 0xFFFFFFFF)
        nif.write_bytes(raw)

        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertFalse(infos[0].has_parallax_flag)
        self.assertFalse(infos[0].has_pom_flag)
        self.assertTrue(
            any("tolerant niobjectnet fallback" in warning.lower() for warning in result.warnings),
            result.warnings,
        )
        self.assertTrue(any("auto-restored" in warning.lower() for warning in result.warnings), result.warnings)

    def test_scan_parses_legacy_block_with_null_shader_type(self) -> None:
        """Legacy BSLightingShaderProperty blocks where shader_type=0xFFFFFFFF
        (Bethesda null/unset sentinel) must be parsed successfully, not rejected
        with 'unsupported BSLightingShaderProperty layout'.

        This reproduces vanilla clutter assets like barrel01.nif / chest01.nif
        that use 0xFFFFFFFF as a null shader-type field.
        """
        nif = _write_nif(
            self.tmp,
            texture_paths=["textures\\dungeons\\barrels\\barrel01_d.dds"] + [""] * 8,
        )
        raw = bytearray(nif.read_bytes())
        # Find the shader_type field in the legacy block (NiObjectNET header is
        # 12 bytes, so shader_type is at block_start+12) and overwrite it.
        shader_header = struct.pack("<IIiI", 0, 0, -1, SHADER_TYPE_DEFAULT)
        shader_start = raw.find(shader_header)
        self.assertNotEqual(shader_start, -1, "could not locate legacy shader block")
        # Overwrite shader_type (offset +12 from block start) with 0xFFFFFFFF
        struct.pack_into("<I", raw, shader_start + 12, 0xFFFFFFFF)
        nif.write_bytes(bytes(raw))

        infos, diagnostics = scan_nif_diagnostics(nif)
        layout_errors = [d for d in diagnostics if "unsupported bslightingshaderproperty" in d.lower()]
        self.assertEqual(
            layout_errors, [],
            msg=f"Parser rejected 0xFFFFFFFF shader_type: {layout_errors}",
        )
        self.assertEqual(len(infos), 1, f"Expected 1 shader info, diagnostics: {diagnostics}")
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_DIFFUSE),
            "textures\\dungeons\\barrels\\barrel01_d.dds",
        )

    def test_patch_succeeds_on_legacy_block_with_null_shader_type(self) -> None:
        """patch_nif must be able to write texture paths on a legacy block
        whose shader_type was left as the 0xFFFFFFFF null sentinel (e.g. vanilla
        clutter NIFs such as barrel01.nif / coin01.nif).
        """
        nif = _write_nif(self.tmp)
        raw = bytearray(nif.read_bytes())
        shader_header = struct.pack("<IIiI", 0, 0, -1, SHADER_TYPE_DEFAULT)
        shader_start = raw.find(shader_header)
        self.assertNotEqual(shader_start, -1)
        struct.pack_into("<I", raw, shader_start + 12, 0xFFFFFFFF)
        nif.write_bytes(bytes(raw))

        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_texture_path="textures\\dungeons\\barrels\\barrel01_p.dds",
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_PARALLAX),
            "textures\\dungeons\\barrels\\barrel01_p.dds",
        )

    def test_scan_reports_unknown_shader_fallback(self) -> None:
        nif = _write_nif(self.tmp, shader_type=0x12345678)
        infos, diagnostics = scan_nif_diagnostics(nif)
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_DEFAULT)
        self.assertTrue(any("0x12345678" in d for d in diagnostics), diagnostics)

    def test_scan_uses_texture_suffix_inference_for_unknown_shader(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_type=0x12345678,
            texture_paths=["textures\\dungeons\\barrels\\barrel01.dds", "", "", "textures\\dungeons\\barrels\\barrel01_p.dds"] + [""] * 5,
        )
        infos, diagnostics = scan_nif_diagnostics(nif)
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_HEIGHTMAP)
        self.assertTrue(any("texture_suffix_parallax" in d for d in diagnostics), diagnostics)

    def test_mapping_table_overrides_existing_weak_classification(self) -> None:
        nif = _write_nif(self.tmp, shader_type=0x12340003)
        data = nif.read_bytes()
        header = _read_header(_Buf(data))
        self.assertIsNotNone(header)
        shader_props, _, _ = _build_block_map(
            data,
            header,  # type: ignore[arg-type]
            mapping_table={0x12340003: SHADER_TYPE_DEFAULT},
        )
        self.assertEqual(shader_props[0].shader_type_resolution, "mapping_table")
        self.assertEqual(shader_props[0].shader_type, SHADER_TYPE_DEFAULT)

    def test_patch_nif_with_u16_count_texture_set(self) -> None:
        """patch_nif must work correctly on a NIF whose texture set uses a u16 count."""
        paths = ["textures\\dungeons\\barrels\\barrel01_d.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths, texture_set_count_u16=True)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_texture_path="textures\\dungeons\\barrels\\barrel01_p.dds",
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_PARALLAX),
            "textures\\dungeons\\barrels\\barrel01_p.dds",
        )

    def test_scan_bad_file_returns_empty(self) -> None:
        bad = self.tmp / "bad.nif"
        bad.write_bytes(b"not a nif")
        self.assertEqual(scan_nif(bad), [])

    def test_scan_missing_file_returns_empty(self) -> None:
        self.assertEqual(scan_nif(self.tmp / "missing.nif"), [])

    def test_scan_parses_nif_with_num_groups_zero(self) -> None:
        """num_groups=0 is always present in real Skyrim SE NIFs (user_version_2 < 130).
        The test NIF builder includes this field; verify that a NIF built with it
        is parsed correctly and the block offsets are not shifted."""
        nif = _write_nif(self.tmp, texture_paths=["textures\\test_d.dds"] + [""] * 8)
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)
        self.assertIn(0, infos[0].texture_paths)
        self.assertEqual(infos[0].texture_paths[0], "textures\\test_d.dds")


# ---------------------------------------------------------------------------
# Tests: validation
# ---------------------------------------------------------------------------

class TestValidateNifForParallax(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_reports_missing_flag(self) -> None:
        nif = _write_nif(self.tmp)
        v = validate_nif_for_parallax(nif)
        self.assertTrue(v.valid)
        self.assertEqual(v.needs_patch_count, 1)
        self.assertTrue(any("flag" in i.lower() for i in v.issues))
        self.assertTrue(any(group.code.startswith("missing_parallax_flag.") for group in v.conflict_report))
        conflict = next(group for group in v.conflict_report if group.code.startswith("missing_parallax_flag."))
        self.assertEqual(conflict.game_profile, "skyrim")
        self.assertEqual(conflict.shader_layout, "legacy")

    def test_reports_single_pass_skip_reason(self) -> None:
        nif = _write_nif(self.tmp, flags1=SLSF1_SINGLE_PASS)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.skip_reasons).lower()
        self.assertIn("single_pass", joined)

    def test_skip_single_pass_reason_can_be_disabled(self) -> None:
        nif = _write_nif(self.tmp, flags1=SLSF1_SINGLE_PASS)
        v = validate_nif_for_parallax(nif, skip_single_pass=False)
        joined = "\n".join(v.skip_reasons).lower()
        self.assertNotIn("single_pass", joined)
        self.assertFalse(any(group.code.startswith("skip_single_pass.") for group in v.conflict_report))

    def test_conflict_report_uses_fallout_profile_suffix(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130, shader_layout="legacy")
        _rewrite_user_version(nif, 11)
        v = validate_nif_for_parallax(nif)
        self.assertTrue(any(".fallout." in group.code for group in v.conflict_report))

    def test_conflict_classifier_maps_fallout_experimental_notice(self) -> None:
        code = _classify_conflict_code(
            "Detected Fallout-era profile. Patch-write support is experimental; keep backups and verify in-game."
        )
        self.assertEqual(code, "fallout_profile.experimental_notice")

    def test_conflict_classifier_maps_reexport_resolution_notice(self) -> None:
        code = _classify_conflict_code(
            "Resolution: open the mesh in NifSkope or the Creation Kit and re-save/export it as a clean Skyrim or Fallout NIF, then run the patch again."
        )
        self.assertEqual(code, "unsupported_header.reexport_resolution")

    def test_conflict_classifier_maps_short_header_corruption_notice(self) -> None:
        code = _classify_conflict_code(
            "The file is shorter than a normal Skyrim NIF header. It is probably truncated, corrupt, or not really a NIF."
        )
        self.assertEqual(code, "unsupported_header.malformed_or_truncated")

    def test_conflict_classifier_maps_probably_truncated_corruption_notice(self) -> None:
        code = _classify_conflict_code(
            "This mesh is probably truncated, corrupt, or not really a NIF."
        )
        self.assertEqual(code, "unsupported_header.malformed_or_truncated")

    def test_conflict_classifier_maps_wrapped_unsupported_profile_drift_notice(self) -> None:
        code = _classify_conflict_code(
            "Malformed or truncated NIF: Unsupported NIF header/profile values"
        )
        self.assertEqual(code, "unsupported_header.profile_value_drift")

    def test_conflict_classifier_maps_wrapped_header_prefix_notice(self) -> None:
        code = _classify_conflict_code(
            "Malformed or truncated NIF: Header prefix is not a Skyrim/Gamebryo 20.2.0.7 NIF. This file is unsupported for auto-patching."
        )
        self.assertEqual(code, "unsupported_header.header_prefix_mismatch")

    def test_conflict_classifier_maps_cannot_read_nif_notice(self) -> None:
        code = _classify_conflict_code(
            "Cannot read NIF: [Errno 13] Permission denied: '/mods/meshes/bad.nif'"
        )
        self.assertEqual(code, "unsupported_header.read_failure")

    def test_conflict_classifier_maps_semantic_shader_resolution_notes(self) -> None:
        code = _classify_conflict_code(
            "Block 1: raw shader_type 0x00000080 resolved to Environment Map via semantic_flag_envmap (RESOLVED, confidence=0.95, method=semantic)."
        )
        self.assertEqual(code, "unknown_shader_type.semantic_resolved")

    def test_conflict_classifier_maps_shader_block_size_mismatch_notes(self) -> None:
        code = _classify_conflict_code(
            "Block 2: recorded block size 152 does not match expected type-0 size 128 before force_shader_type_3 expansion."
        )
        self.assertEqual(code, "unsupported_header.shader_block_size_mismatch")

    def test_conflict_classifier_maps_shader_block_too_small_parse_error(self) -> None:
        code = _classify_conflict_code(
            "Block 0: failed to parse BSLightingShaderProperty (strict): block too small for BSLightingShaderProperty: size=16 < 100 (block_start=0x1337)"
        )
        self.assertEqual(code, "unsupported_header.shader_block_too_small")

    def test_conflict_classifier_maps_shader_block_past_eof_parse_error(self) -> None:
        code = _classify_conflict_code(
            "Block 0: failed to parse BSLightingShaderProperty: block extends past end of file: block_end=0x12FF > file_size=768 (block_start=0x1000, block_size=767)"
        )
        self.assertEqual(code, "unsupported_header.shader_block_past_eof")

    def test_conflict_classifier_maps_texture_set_u16_count_out_of_range(self) -> None:
        code = _classify_conflict_code(
            "Block 1: texture-set parse error: u16 read out of range at offset 804 (need 2 byte(s), buffer size 805)"
        )
        self.assertEqual(code, "unsupported_header.texture_set_u16_count_out_of_range")

    def test_conflict_classifier_maps_tolerant_num_extra_recovery_notes(self) -> None:
        code = _classify_conflict_code(
            "Recovered shader-block scan using tolerant num_extra parsing for malformed NiObjectNET extra-data counts."
        )
        self.assertEqual(code, "unsupported_header.num_extra_recovery")

    def test_conflict_classifier_maps_strict_unknown_shader_failure(self) -> None:
        code = _classify_conflict_code("Strict unknown-shader check failed.")
        self.assertEqual(code, "unknown_shader_type.strict_violation")

    def test_conflict_classifier_maps_fallout_guarded_noop_when_no_compatible_blocks(self) -> None:
        code = _classify_conflict_code(
            "No Fallout-compatible BSLightingShaderProperty blocks found for experimental patch mode."
        )
        self.assertEqual(code, "fallout_profile.guarded_noop_no_compatible_blocks")

    def test_conflict_classifier_maps_fallout_guarded_noop_when_layout_policy_skips_all_blocks(self) -> None:
        code = _classify_conflict_code(
            "No supported shader layouts are available for profile 'fallout'; skipping all shader blocks."
        )
        self.assertEqual(code, "fallout_profile.guarded_noop_layout_policy_exhausted")

    def test_conflict_report_classifies_slot_specific_paths(self) -> None:
        paths = ["textures\\arch\\stone_n.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        self.assertTrue(any(group.code.startswith("path_slot_diffuse.wrong_suffix.") for group in v.conflict_report))

    def test_conflict_report_uses_granular_per_slot_path_codes(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        paths[TEXTURE_SLOT_NORMAL] = "textures\\arch\\stone_n.dds"
        paths[TEXTURE_SLOT_PARALLAX] = "textures\\arch\\stone_n.dds"
        nif = _write_nif(self.tmp, flags1=SLSF1_PARALLAX, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        codes = {group.code for group in v.conflict_report}
        self.assertTrue(any(code.startswith("path_slot_parallax.wrong_suffix.") for code in codes))
        self.assertTrue(any(code.startswith("path_slot_parallax.matches_normal.") for code in codes))

    def test_conflict_report_uses_granular_per_flag_codes(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_GLOW] = "textures\\arch\\stone_g.dds"
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\arch\\stone_m.dds"
        paths[TEXTURE_SLOT_CUBEMAP] = "textures\\arch\\stone_e.dds"
        paths[TEXTURE_SLOT_PARALLAX] = "textures\\arch\\stone_p.dds"
        nif = _write_nif(
            self.tmp,
            texture_paths=paths,
            flags1=SLSF1_PARALLAX_OCCLUSION,
            shader_type=SHADER_TYPE_DEFAULT,
        )
        v = validate_nif_for_parallax(nif)
        codes = {group.code for group in v.conflict_report}
        self.assertTrue(any(code.startswith("flag_glow_map.slot2_filled_without_flag.") for code in codes))
        self.assertTrue(any(code.startswith("flag_env_mapping.slot5_filled_without_flag.") for code in codes))
        self.assertTrue(any(code.startswith("flag_env_mapping.slot4_filled_without_flag.") for code in codes))
        self.assertTrue(any(code.startswith("flag_pom.without_base_parallax.") for code in codes))
        self.assertTrue(any(code.startswith("flag_pom.non_heightmap_shader.") for code in codes))

    def test_ready_when_flag_and_texture_set(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_PARALLAX] = "textures\\stone_p.dds"
        nif = _write_nif(self.tmp, flags1=SLSF1_PARALLAX, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        self.assertEqual(v.ready_count, 1)
        self.assertEqual(v.needs_patch_count, 0)

    def test_reports_non_skyrim_relative_parallax_path(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        paths[TEXTURE_SLOT_PARALLAX] = "stone_p.dds"
        nif = _write_nif(self.tmp, flags1=SLSF1_PARALLAX, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        self.assertTrue(any("not a skyrim-relative" in issue.lower() for issue in v.issues))

    def test_reports_low_parallax_scale(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_PARALLAX] = "textures\\stone_p.dds"
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_HEIGHTMAP,
            parallax_scale=0.2,
            flags1=SLSF1_PARALLAX,
            texture_paths=paths,
        )
        v = validate_nif_for_parallax(nif)
        self.assertTrue(any("parallax scale is only" in suggestion.lower() for suggestion in v.suggestions))

    def test_invalid_for_non_nif(self) -> None:
        bad = self.tmp / "bad.nif"
        bad.write_bytes(b"\x00\x00")
        v = validate_nif_for_parallax(bad)
        self.assertFalse(v.valid)

    def test_reports_actionable_resolution_for_truncated_header(self) -> None:
        nif = _write_nif(self.tmp)
        broken = self.tmp / "broken.nif"
        broken.write_bytes(nif.read_bytes()[:80])
        v = validate_nif_for_parallax(broken)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("re-save", joined)

    def test_reports_legacy_shader_property_when_no_bslighting_blocks_exist(self) -> None:
        nif = _write_nif(self.tmp, shader_block_type="BSShaderPPLightingProperty")
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("bsshaderpplightingproperty", joined)
        self.assertIn("convert", joined)

    def test_reports_env_mask_slot_without_env_mapping_flag(self) -> None:
        paths = [""] * 9
        paths[5] = "textures\\arch\\stone_m.dds"
        nif = _write_nif(self.tmp, texture_paths=paths, flags1=0)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 5", joined)
        self.assertIn("environment_mapping", joined)

    def test_reports_cubemap_slot_without_env_mapping_flag(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_CUBEMAP] = "textures\\arch\\stone_e.dds"
        nif = _write_nif(self.tmp, texture_paths=paths, flags1=0)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 4", joined)
        self.assertIn("environment_mapping", joined)

    def test_reports_pom_without_base_parallax_flag(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_PARALLAX] = "textures\\arch\\stone_p.dds"
        nif = _write_nif(
            self.tmp,
            texture_paths=paths,
            flags1=SLSF1_PARALLAX_OCCLUSION,
            shader_type=SHADER_TYPE_HEIGHTMAP,
        )
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("pom flag", joined)
        self.assertIn("base slsf1_parallax", joined)

    def test_conflict_report_flags_parallax_shader_type_with_missing_slot3(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_HEIGHTMAP,
            flags1=SLSF1_PARALLAX,
            texture_paths=["textures\\arch\\stone.dds"] + [""] * 8,
        )
        v = validate_nif_for_parallax(nif)
        self.assertTrue(
            any(group.code.startswith("shader_state.parallax_type_missing_slot3.") for group in v.conflict_report)
        )

    def test_conflict_report_flags_envmap_shader_with_missing_slots4_5(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=SLSF1_ENVIRONMENT_MAPPING,
            texture_paths=["textures\\arch\\stone.dds"] + [""] * 8,
        )
        v = validate_nif_for_parallax(nif)
        self.assertTrue(
            any(group.code.startswith("shader_state.envmap_missing_slots4_5.") for group in v.conflict_report)
        )

    def test_conflict_report_flags_envmap_shader_with_missing_slot4(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\arch\\stone_m.dds"
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=SLSF1_ENVIRONMENT_MAPPING,
            texture_paths=paths,
        )
        v = validate_nif_for_parallax(nif)
        self.assertTrue(
            any(group.code.startswith("shader_state.envmap_missing_slot4.") for group in v.conflict_report)
        )

    def test_conflict_report_flags_envmap_shader_with_missing_slot5(self) -> None:
        textures_root = self.tmp / "textures" / "cubemaps"
        textures_root.mkdir(parents=True, exist_ok=True)
        (textures_root / "stone_e.dds").write_bytes(b"dds")
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        paths[TEXTURE_SLOT_CUBEMAP] = "textures\\cubemaps\\stone_e.dds"
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\arch\\missing_mask_m.dds"
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=SLSF1_ENVIRONMENT_MAPPING,
            texture_paths=paths,
        )
        v = validate_nif_for_parallax(nif)
        self.assertTrue(
            any(group.code.startswith("shader_state.envmap_missing_slot5.") for group in v.conflict_report)
        )

    def test_conflict_report_can_emit_multi_conflict_mixed_states(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        paths[TEXTURE_SLOT_NORMAL] = "textures\\arch\\stone_n.dds"
        paths[TEXTURE_SLOT_GLOW] = "textures\\arch\\stone_n.dds"
        paths[TEXTURE_SLOT_PARALLAX] = "textures\\arch\\stone.dds"
        paths[TEXTURE_SLOT_CUBEMAP] = "textures\\arch\\stone_n.dds"
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\arch\\stone_orm.dds"
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=SLSF1_PARALLAX_OCCLUSION | SLSF1_ENVIRONMENT_MAPPING,
            flags2=SLSF2_GLOW_MAP,
            texture_paths=paths,
        )
        v = validate_nif_for_parallax(nif)
        codes = {group.code for group in v.conflict_report}
        self.assertTrue(any(code.startswith("path_slot_parallax.matches_diffuse.") for code in codes))
        self.assertTrue(any(code.startswith("path_slot_cubemap.wrong_suffix.") for code in codes))
        self.assertTrue(any(code.startswith("path_slot_env_mask.generic_alias_suffix.") for code in codes))
        self.assertTrue(any(code.startswith("path_slot_glow.wrong_suffix.") for code in codes))

    def test_conflict_report_flags_envmap_pom_with_unresolved_env_textures(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        paths[TEXTURE_SLOT_PARALLAX] = "textures\\arch\\stone_p.dds"
        paths[TEXTURE_SLOT_CUBEMAP] = "textures\\cubemaps\\missing_env_e.dds"
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=SLSF1_ENVIRONMENT_MAPPING | SLSF1_PARALLAX_OCCLUSION,
            texture_paths=paths,
        )
        v = validate_nif_for_parallax(nif)
        self.assertTrue(
            any(group.code.startswith("shader_state.envmap_pom_missing_env_slots.") for group in v.conflict_report)
        )

    def test_conflict_report_flags_envmap_glow_with_unresolved_slots(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        paths[TEXTURE_SLOT_CUBEMAP] = "textures\\cubemaps\\missing_env_e.dds"
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=SLSF1_ENVIRONMENT_MAPPING,
            flags2=SLSF2_GLOW_MAP,
            texture_paths=paths,
        )
        v = validate_nif_for_parallax(nif)
        self.assertTrue(
            any(group.code.startswith("shader_state.envmap_glow_missing_slots2_4_5.") for group in v.conflict_report)
        )

    def test_conflict_report_flags_envmap_glow_with_slot2_unresolved_and_env_slots_present(self) -> None:
        (self.tmp / "textures" / "cubemaps").mkdir(parents=True, exist_ok=True)
        (self.tmp / "textures" / "effects").mkdir(parents=True, exist_ok=True)
        (self.tmp / "textures" / "cubemaps" / "aura_e.dds").write_bytes(b"dds")
        (self.tmp / "textures" / "effects" / "aura_m.dds").write_bytes(b"dds")
        paths = ["textures\\effects\\aura.dds"] + [""] * 8
        paths[TEXTURE_SLOT_CUBEMAP] = "textures\\cubemaps\\aura_e.dds"
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\effects\\aura_m.dds"
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=SLSF1_ENVIRONMENT_MAPPING,
            flags2=SLSF2_GLOW_MAP,
            texture_paths=paths,
        )
        v = validate_nif_for_parallax(nif)
        self.assertTrue(
            any(group.code.startswith("shader_state.envmap_glow_missing_slot2.") for group in v.conflict_report)
        )

    def test_conflict_report_flags_parallax_envmap_with_unresolved_slots(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        paths[TEXTURE_SLOT_CUBEMAP] = "textures\\cubemaps\\missing_env_e.dds"
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=SLSF1_ENVIRONMENT_MAPPING | SLSF1_PARALLAX,
            texture_paths=paths,
        )
        v = validate_nif_for_parallax(nif)
        self.assertTrue(
            any(group.code.startswith("shader_state.parallax_envmap_missing_slots3_4_5.") for group in v.conflict_report)
        )

    def test_conflict_report_flags_parallax_envmap_glow_with_unresolved_slots(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=SLSF1_ENVIRONMENT_MAPPING | SLSF1_PARALLAX,
            flags2=SLSF2_GLOW_MAP,
            texture_paths=["textures\\arch\\stone.dds"] + [""] * 8,
        )
        v = validate_nif_for_parallax(nif)
        self.assertTrue(
            any(
                group.code.startswith("shader_state.parallax_envmap_glow_missing_slots2_3_4_5.")
                for group in v.conflict_report
            )
        )

    def test_reports_wrong_texture_type_in_normal_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_NORMAL] = "textures\\arch\\stone_p.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 1 normal path", joined)
        self.assertIn("_n.dds or _msn.dds", joined)

    def test_reports_wrong_texture_type_in_normal_slot_for_env_mask_suffix(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_NORMAL] = "textures\\arch\\stone_rmaos.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 1 normal path", joined)
        self.assertIn("_n.dds or _msn.dds", joined)

    def test_reports_wrong_texture_type_in_normal_slot_for_truepbr_alias_suffix(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_NORMAL] = "textures\\arch\\stone_orm.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 1 normal path", joined)
        self.assertIn("_n.dds or _msn.dds", joined)

    def test_reports_wrong_texture_type_in_glow_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_GLOW] = "textures\\arch\\stone_n.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 2 glow path", joined)
        self.assertIn("slot 2 for emissive textures", joined)

    def test_accepts_g_suffix_in_glow_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_GLOW] = "textures\\arch\\stone_g.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertNotIn("slot 2 glow path", joined)

    def test_reports_wrong_texture_type_in_env_mask_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\arch\\stone_g.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 5 environment-mask path", joined)
        self.assertIn("slot 5 for _m.dds", joined)

    def test_accepts_c_suffix_in_env_mask_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\arch\\stone_c.dds"
        nif = _write_nif(
            self.tmp,
            texture_paths=paths,
            flags1=SLSF1_ENVIRONMENT_MAPPING,
            shader_type=SHADER_TYPE_ENVMAP,
        )
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertNotIn("slot 5 environment-mask path", joined)

    def test_reports_generic_orm_suffix_in_env_mask_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\arch\\stone_orm.dds"
        nif = _write_nif(
            self.tmp,
            texture_paths=paths,
            flags1=SLSF1_ENVIRONMENT_MAPPING,
            shader_type=SHADER_TYPE_ENVMAP,
        )
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("generic packed alias suffix", joined)
        self.assertIn("target workflow is unambiguous", joined)

    def test_truepbr_rmaos_path_warns_when_not_in_textures_pbr(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\architecture\\stone_rmaos.dds"
        nif = _write_nif(
            self.tmp,
            texture_paths=paths,
            flags1=SLSF1_ENVIRONMENT_MAPPING,
            shader_type=SHADER_TYPE_ENVMAP,
        )
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("textures\\pbr\\", joined)
        self.assertIn("pbrnifpatcher json", joined)

    def test_truepbr_orm_path_warns_when_not_in_textures_pbr(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\architecture\\stone_orm.dds"
        nif = _write_nif(
            self.tmp,
            texture_paths=paths,
            flags1=SLSF1_ENVIRONMENT_MAPPING,
            shader_type=SHADER_TYPE_ENVMAP,
        )
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("textures\\pbr\\", joined)
        self.assertIn("pbrnifpatcher json", joined)
        self.assertIn("generic packed alias suffix", joined)
        self.assertIn("_rmaos/_ramos", joined)

    def test_reports_blender_style_diffuse_suffix(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_DIFFUSE] = "textures\\architecture\\stone_albedo.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("blender/authoring suffix naming", joined)
        self.assertIn("bare diffuse name", joined)

    def test_reports_non_dds_extension_in_parallax_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_PARALLAX] = "textures\\architecture\\stone_p.png"
        nif = _write_nif(self.tmp, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 3 parallax path", joined)
        self.assertIn("not a .dds texture path", joined)
        self.assertIn("convert parallax/height textures to .dds", joined)

    def test_truepbr_renderer_notes_distinguish_generic_orm_alias(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\architecture\\stone_orm.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        infos = scan_nif(nif)
        notes = _renderer_compatibility(infos[0])
        joined = "\n".join(notes["truepbr"]).lower()
        self.assertIn("generic packed alias", joined)
        self.assertIn("blender/substance", joined)

    def test_renderer_verdicts_explain_vanilla_ready_but_enb_not_ready(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_NORMAL] = "textures\\architecture\\stone_n.dds"
        paths[TEXTURE_SLOT_PARALLAX] = "textures\\architecture\\stone_p.dds"
        nif = _write_nif(
            self.tmp,
            texture_paths=paths,
            flags1=SLSF1_PARALLAX,
            shader_type=SHADER_TYPE_HEIGHTMAP,
        )
        v = validate_nif_for_parallax(nif)
        self.assertIn("mesh-side setup looks ready", v.renderer_verdicts["vanilla"].lower())
        enb_verdict = v.renderer_verdicts["enb"].lower()
        self.assertIn("won't work yet", enb_verdict)
        self.assertIn("envmap", enb_verdict)
        self.assertIn("model_space_normals", enb_verdict)

    def test_renderer_verdicts_flag_generic_truepbr_aliases(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_NORMAL] = "textures\\pbr\\architecture\\stone_n.dds"
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\pbr\\architecture\\stone_orm.dds"
        nif = _write_nif(self.tmp, texture_paths=paths, flags2=SLSF2_UNUSED01)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("generic packed alias suffix", joined)
        truepbr_verdict = v.renderer_verdicts["truepbr"].lower()
        self.assertIn("won't work yet", truepbr_verdict)
        self.assertIn("generic packed alias", truepbr_verdict)

    def test_reports_blender_style_env_mask_suffix_in_slot_five(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\architecture\\stone_rough.dds"
        nif = _write_nif(
            self.tmp,
            texture_paths=paths,
            flags1=SLSF1_ENVIRONMENT_MAPPING,
            shader_type=SHADER_TYPE_ENVMAP,
        )
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 5 environment-mask path", joined)
        self.assertIn("use slot 5 for _m.dds", joined)

    def test_reports_non_diffuse_texture_in_diffuse_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_DIFFUSE] = "textures\\arch\\stone_p.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 0 diffuse path", joined)
        self.assertIn("use slot 0 for diffuse/albedo", joined)

    def test_reports_env_mask_suffix_in_diffuse_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_DIFFUSE] = "textures\\arch\\stone_em.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 0 diffuse path", joined)
        self.assertIn("use slot 0 for diffuse/albedo", joined)

    def test_reports_truepbr_alias_suffix_in_diffuse_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_DIFFUSE] = "textures\\arch\\stone_orms.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 0 diffuse path", joined)
        self.assertIn("use slot 0 for diffuse/albedo", joined)

    def test_reports_skin_tint_suffix_in_diffuse_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_DIFFUSE] = "textures\\actors\\dragon\\dragon_sk.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 0 diffuse path", joined)
        self.assertIn("use slot 0 for diffuse/albedo", joined)

    def test_reports_non_cubemap_texture_in_cubemap_slot(self) -> None:
        paths = [""] * 9
        paths[4] = "textures\\arch\\stone_n.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 4 cubemap path", joined)
        self.assertIn("use slot 4 for cubemap/environment textures", joined)


# ---------------------------------------------------------------------------
# Tests: flag patching
# ---------------------------------------------------------------------------

class TestPatchNifFlags(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_enable_parallax_sets_flag(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertTrue(infos[0].has_parallax_flag)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_DEFAULT)

    def test_enable_pom_sets_both_parallax_and_occlusion_flags(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(nif, NifPatchOptions(enable_pom=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertTrue(infos[0].has_parallax_flag)
        self.assertTrue(infos[0].has_pom_flag)

    def test_enable_parallax_real_layout_keeps_inferred_shader_type(self) -> None:
        nif = _write_nif(self.tmp, shader_layout="real", shader_type=SHADER_TYPE_DEFAULT)
        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_DEFAULT)
        self.assertTrue(infos[0].has_parallax_flag)

    def test_real_layout_auto_restore_respects_strict_pre_write_validation(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_layout="real",
            shader_type=SHADER_TYPE_HEIGHTMAP,
            flags1=SLSF1_PARALLAX | SLSF1_PARALLAX_OCCLUSION,
            texture_paths=["textures\\stone.dds"] + [""] * 8,
        )
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                clear_parallax_texture_path=True,
                backup=False,
                strict_pre_write_validation=True,
            ),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertGreaterEqual(len(infos), 1)
        self.assertTrue(infos[0].has_parallax_flag)
        self.assertFalse(any("auto-restored" in warning.lower() for warning in result.warnings), result.warnings)

    def test_enable_parallax_does_not_retype_envmap_block(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_ENVMAP)
        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_ENVMAP)
        self.assertTrue(infos[0].has_parallax_flag)

    def test_enable_parallax_preserves_sentinel_default_block_type(self) -> None:
        nif = _write_nif(self.tmp)
        raw = bytearray(nif.read_bytes())
        shader_header = struct.pack("<IIiI", 0, 0, -1, SHADER_TYPE_DEFAULT)
        shader_start = raw.find(shader_header)
        self.assertNotEqual(shader_start, -1)
        struct.pack_into("<I", raw, shader_start + 12, 0xFFFFFFFF)
        nif.write_bytes(bytes(raw))

        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].raw_shader_type, 0xFFFFFFFF)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_DEFAULT)
        self.assertTrue(infos[0].has_parallax_flag)

    def test_enable_parallax_preserves_unknown_raw_shader_value(self) -> None:
        nif = _write_nif(self.tmp, shader_type=0x12345678)
        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos, diagnostics = scan_nif_diagnostics(nif)
        self.assertEqual(infos[0].raw_shader_type, 0x12345678)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_HEIGHTMAP)
        self.assertTrue(infos[0].has_parallax_flag)
        self.assertTrue(any("0x12345678" in d for d in diagnostics), diagnostics)

    def test_enable_env_mapping_sets_flag(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(nif, NifPatchOptions(enable_env_mapping=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertTrue(infos[0].has_env_mapping_flag)

    def test_auto_restores_heightmap_shader_when_parallax_slot_is_empty(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_HEIGHTMAP,
            flags1=SLSF1_PARALLAX | SLSF1_PARALLAX_OCCLUSION,
        )
        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_HEIGHTMAP)
        self.assertFalse(infos[0].has_parallax_flag)
        self.assertFalse(infos[0].has_pom_flag)
        self.assertTrue(any("auto-restored" in warning.lower() for warning in result.warnings))

    def test_auto_restores_parallax_flags_on_default_shader_when_slot_is_empty(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_DEFAULT,
            flags1=SLSF1_PARALLAX | SLSF1_PARALLAX_OCCLUSION,
            texture_paths=["textures\\stone.dds"] + [""] * 8,
        )
        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_DEFAULT)
        self.assertFalse(infos[0].has_parallax_flag)
        self.assertFalse(infos[0].has_pom_flag)
        self.assertTrue(any("auto-restored" in warning.lower() for warning in result.warnings))

    def test_auto_restores_parallax_flags_when_slot_path_file_is_missing_near_mesh(self) -> None:
        (self.tmp / "textures").mkdir(exist_ok=True)
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_HEIGHTMAP,
            flags1=SLSF1_PARALLAX | SLSF1_PARALLAX_OCCLUSION,
            texture_paths=["textures\\stone.dds", "", "", "textures\\architecture\\missing_p.dds"] + [""] * 5,
        )
        result = patch_nif(nif, NifPatchOptions(disable_pom=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertFalse(infos[0].has_parallax_flag)
        self.assertFalse(infos[0].has_pom_flag)
        self.assertTrue(any("empty or unresolved" in warning.lower() for warning in result.warnings), result.warnings)

    def test_auto_restores_envmap_flag_when_slot5_file_is_missing_near_mesh(self) -> None:
        (self.tmp / "textures").mkdir(exist_ok=True)
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=0,
            texture_paths=["textures\\stone.dds", "", "", "", "", "textures\\architecture\\missing_m.dds"] + [""] * 3,
        )
        result = patch_nif(nif, NifPatchOptions(enable_env_mapping=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertFalse(infos[0].has_env_mapping_flag)
        self.assertTrue(any("empty or unresolved" in warning.lower() for warning in result.warnings), result.warnings)

    def test_auto_restores_envmap_flag_when_envmap_shader_slot5_file_is_missing_even_with_slot4_set(self) -> None:
        cubemap_dir = self.tmp / "textures" / "cubemaps"
        cubemap_dir.mkdir(parents=True, exist_ok=True)
        (cubemap_dir / "stone_e.dds").write_bytes(b"dds")
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=0,
            texture_paths=[
                "textures\\stone.dds",
                "",
                "",
                "",
                "textures\\cubemaps\\stone_e.dds",
                "textures\\architecture\\missing_m.dds",
                "",
                "",
                "",
            ],
        )
        result = patch_nif(nif, NifPatchOptions(enable_env_mapping=True, backup=False))
        self.assertTrue(result.success, result.errors)
        info = scan_nif(nif)[0]
        self.assertFalse(info.has_env_mapping_flag)
        self.assertTrue(any("empty or unresolved" in warning.lower() for warning in result.warnings), result.warnings)

    def test_auto_restores_glow_flag_when_slot2_file_is_missing_near_mesh(self) -> None:
        (self.tmp / "textures").mkdir(exist_ok=True)
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_DEFAULT,
            flags1=0,
            flags2=SLSF2_GLOW_MAP,
            texture_paths=["textures\\stone.dds", "", "textures\\architecture\\missing_g.dds"] + [""] * 7,
        )
        result = patch_nif(nif, NifPatchOptions(enable_glow_map=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertFalse(infos[0].has_glow_map_flag)
        self.assertTrue(any("auto-restored" in warning.lower() for warning in result.warnings), result.warnings)

    def test_validate_reports_glow_flag_set_with_unresolved_slot2(self) -> None:
        (self.tmp / "textures").mkdir(exist_ok=True)
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_DEFAULT,
            flags2=SLSF2_GLOW_MAP,
            texture_paths=["textures\\stone.dds", "", "textures\\architecture\\missing_g.dds"] + [""] * 7,
        )
        validation = validate_nif_for_parallax(nif)
        codes = {group.code for group in validation.conflict_report}
        self.assertIn("flag_glow_map.flag_set_without_slot2.skyrim.legacy", codes)

    def test_auto_restores_envmap_shader_when_required_slots_are_empty(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=0,
            texture_paths=["textures\\stone.dds"] + [""] * 8,
        )
        result = patch_nif(nif, NifPatchOptions(enable_env_mapping=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_ENVMAP)
        self.assertFalse(infos[0].has_env_mapping_flag)
        self.assertTrue(any("auto-restored" in warning.lower() for warning in result.warnings))

    def test_auto_restore_mixed_multiblock_conflicts_preserve_valid_block_flags(self) -> None:
        cubemap_dir = self.tmp / "textures" / "cubemaps"
        cubemap_dir.mkdir(parents=True, exist_ok=True)
        (cubemap_dir / "env_e.dds").write_bytes(b"dds")
        parallax_dir = self.tmp / "textures" / "architecture"
        parallax_dir.mkdir(parents=True, exist_ok=True)
        (parallax_dir / "stone_p.dds").write_bytes(b"dds")
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            flags1=SLSF1_ENVIRONMENT_MAPPING,
            texture_paths=[
                "textures\\architecture\\stone.dds",
                "",
                "",
                "",
                "textures\\cubemaps\\env_e.dds",
                "textures\\architecture\\missing_m.dds",
                "",
                "",
                "",
            ],
            extra_shader_blocks=[
                {
                    "shader_type": SHADER_TYPE_HEIGHTMAP,
                    "flags1": SLSF1_PARALLAX,
                    "texture_paths": [
                        "textures\\architecture\\stone.dds",
                        "",
                        "",
                        "textures\\architecture\\stone_p.dds",
                        "",
                        "",
                        "",
                        "",
                        "",
                    ],
                }
            ],
        )
        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, enable_env_mapping=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 2)
        envmap_block = next(info for info in infos if info.shader_type == SHADER_TYPE_ENVMAP)
        heightmap_block = next(info for info in infos if info.shader_type == SHADER_TYPE_HEIGHTMAP)
        self.assertFalse(envmap_block.has_env_mapping_flag)
        self.assertTrue(heightmap_block.has_parallax_flag)
        self.assertTrue(any("auto-restored" in warning.lower() for warning in result.warnings), result.warnings)

    def test_patch_is_idempotent(self) -> None:
        # A fully patched parallax NIF must have both SLSF1_PARALLAX and
        # SLSF2_VERTEX_COLORS set.  Patching such a NIF again must be a no-op.
        nif = _write_nif(self.tmp, flags1=SLSF1_PARALLAX, flags2=SLSF2_VERTEX_COLORS)
        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        self.assertTrue(result.success)
        self.assertTrue(result.already_up_to_date)

    def test_dry_run_does_not_write(self) -> None:
        nif = _write_nif(self.tmp)
        original = nif.read_bytes()
        result = patch_nif(
            nif,
            NifPatchOptions(enable_parallax=True, backup=False, dry_run=True),
        )
        self.assertTrue(result.success)
        self.assertEqual(nif.read_bytes(), original)

    def test_dry_run_diff_reports_changed_ranges(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(
            nif,
            NifPatchOptions(enable_parallax=True, backup=False, dry_run=True, dry_run_diff=True),
        )
        self.assertTrue(result.success)
        self.assertIn("Diff:", result.message)
        self.assertIn("range(s)", result.message)

    def test_strict_pre_write_validation_blocks_write_on_validation_failure(self) -> None:
        nif = _write_nif(self.tmp)
        original = nif.read_bytes()
        with mock.patch.dict(
            patch_nif.__globals__,
            {"_validate_patched_bytes_before_write": lambda *args, **kwargs: ["Pre-write block-map warning/error: synthetic failure"]},
        ):
            result = patch_nif(
                nif,
                NifPatchOptions(enable_parallax=True, backup=False, strict_pre_write_validation=True),
            )
        self.assertFalse(result.success)
        self.assertIn("Pre-write validation failed", result.message)
        self.assertEqual(nif.read_bytes(), original)

    def test_backup_is_written(self) -> None:
        nif = _write_nif(self.tmp)
        original = nif.read_bytes()
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=True))
        bak = nif.with_suffix(".nif.bak")
        self.assertTrue(bak.exists())
        self.assertEqual(bak.read_bytes(), original)

    def test_no_options_returns_success_with_no_changes(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(nif, NifPatchOptions(backup=False))
        self.assertFalse(result.success)  # success=False when nothing requested

    def test_target_game_fallout_requires_opt_in(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130)
        _rewrite_user_version(nif, 11)
        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False, target_game="fallout"))
        self.assertFalse(result.success)
        self.assertIn("experimental_fallout_write is disabled", result.message.lower())

    def test_target_game_skyrim_rejects_fallout_header(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130)
        _rewrite_user_version(nif, 11)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                backup=False,
                target_game="skyrim",
            ),
        )
        self.assertFalse(result.success)
        self.assertIn("target_game='skyrim' requires skyrim-compatible headers", result.message.lower())

    def test_target_game_fallout_on_skyrim_header_warns_and_uses_fallout_policy(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                backup=False,
                target_game="fallout",
                experimental_fallout_write=True,
            ),
        )
        self.assertTrue(result.success, result.errors)
        self.assertTrue(
            any("differs from detected profile" in warning.lower() for warning in result.warnings),
            result.warnings,
        )

    def test_target_game_fallout_with_opt_in_patches_flags(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130, shader_layout="real")
        _rewrite_user_version(nif, 11)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                backup=False,
                target_game="fallout",
                experimental_fallout_write=True,
            ),
        )
        self.assertTrue(result.success, result.errors)
        self.assertEqual(result.detected_game_profile, "fallout")
        self.assertTrue(any("experimental fallout patch mode active" in w.lower() for w in result.warnings), result.warnings)
        infos = scan_nif(nif)
        self.assertTrue(infos[0].has_parallax_flag)

    def test_target_game_fallout_legacy_layout_with_opt_in_patches_flags(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130, shader_layout="legacy")
        _rewrite_user_version(nif, 11)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                backup=False,
                target_game="fallout",
                experimental_fallout_write=True,
            ),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertTrue(infos[0].has_parallax_flag)

    def test_target_game_fallout_rejects_parallax_scale(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130)
        _rewrite_user_version(nif, 11)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_scale=2.0,
                backup=False,
                target_game="fallout",
                experimental_fallout_write=True,
            ),
        )
        self.assertFalse(result.success)
        self.assertIn("does not support", result.message.lower())
        self.assertIn("parallax_scale", result.message)

    def test_target_game_fallout_allows_parallax_scale_with_safety_gate(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130, shader_type=SHADER_TYPE_DEFAULT)
        _rewrite_user_version(nif, 11)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_scale=2.0,
                backup=False,
                target_game="fallout",
                experimental_fallout_write=True,
                fallout_allow_parallax_scale=True,
            ),
        )
        self.assertTrue(result.success, result.errors)
        self.assertTrue(any("per-operation safety gates enabled" in warning.lower() for warning in result.warnings))
        self.assertTrue(any("no existing type-3 parallax-capable shader blocks" in warning.lower() for warning in result.warnings))

    def test_target_game_fallout_rejects_force_type3(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130)
        _rewrite_user_version(nif, 11)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                force_shader_type_3=True,
                backup=False,
                target_game="fallout",
                experimental_fallout_write=True,
            ),
        )
        self.assertFalse(result.success)
        self.assertIn("force_shader_type_3", result.message)

    def test_target_game_fallout_rejects_advanced_shader_fields_without_safety_gates(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130, shader_layout="real")
        _rewrite_user_version(nif, 11)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                spec_strength=0.8,
                env_map_scale=1.1,
                backup=False,
                target_game="fallout",
                experimental_fallout_write=True,
            ),
        )
        self.assertFalse(result.success)
        self.assertIn("spec_strength", result.message)
        self.assertIn("env_map_scale", result.message)

    def test_target_game_fallout_allows_advanced_shader_fields_with_safety_gates(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130, shader_layout="real", shader_type=1, env_map_scale=0.5)
        _rewrite_user_version(nif, 11)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                spec_strength=0.65,
                env_map_scale=1.25,
                fix_mesh_lighting=True,
                backup=False,
                target_game="fallout",
                experimental_fallout_write=True,
                fallout_allow_spec_strength=True,
                fallout_allow_env_map_scale=True,
                fallout_allow_fix_mesh_lighting=True,
            ),
        )
        self.assertTrue(result.success, result.errors)
        self.assertTrue(any("per-operation safety gates enabled" in warning.lower() for warning in result.warnings))

    def test_invalid_target_game_option_fails_fast(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False, target_game="oblivion"))
        self.assertFalse(result.success)
        self.assertIn("unsupported target_game", result.message.lower())

    def test_disable_parallax_clears_parallax_and_pom_flags(self) -> None:
        nif = _write_nif(self.tmp, flags1=SLSF1_PARALLAX | SLSF1_PARALLAX_OCCLUSION)
        result = patch_nif(nif, NifPatchOptions(disable_parallax=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertFalse(infos[0].has_parallax_flag)
        self.assertFalse(infos[0].has_pom_flag)

    def test_disable_env_mapping_clears_flag(self) -> None:
        nif = _write_nif(self.tmp, flags1=SLSF1_ENVIRONMENT_MAPPING)
        result = patch_nif(nif, NifPatchOptions(disable_env_mapping=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertFalse(infos[0].has_env_mapping_flag)

    def test_strict_unknown_shader_types_fails_patch(self) -> None:
        nif = _write_nif(self.tmp, shader_type=0x12345678)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                strict_unknown_shader_types=True,
                backup=False,
            ),
        )
        self.assertFalse(result.success)
        self.assertIn("Strict unknown-shader check failed", result.message)
        self.assertTrue(any("0x12345678" in e for e in result.errors), result.errors)

    def test_strict_unknown_shader_resolved_by_semantic_flag_passes(self) -> None:
        # Unknown raw shader_type but SLSF1_PARALLAX set → resolves via
        # semantic inference → strict mode must NOT reject this block.
        nif = _write_nif(self.tmp, shader_type=0x12345678, flags1=SLSF1_PARALLAX)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                strict_unknown_shader_types=True,
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)

    def test_strict_unknown_shader_resolved_by_envmap_flag_passes(self) -> None:
        # Unknown raw shader_type but SLSF1_ENVIRONMENT_MAPPING set → resolves
        # via semantic inference → strict mode must NOT reject this.
        nif = _write_nif(self.tmp, shader_type=0x12345678, flags1=SLSF1_ENVIRONMENT_MAPPING)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                strict_unknown_shader_types=True,
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)

    def test_strict_unknown_shader_resolved_by_mapping_table_passes(self) -> None:
        # Unknown raw shader_type with no flags, but a user mapping table entry
        # → resolved via mapping_table → strict mode must NOT reject this.
        nif = _write_nif(self.tmp, shader_type=0x12345678)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                strict_unknown_shader_types=True,
                unknown_shader_type_map={0x12345678: SHADER_TYPE_DEFAULT},
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)

    def test_strict_unknown_shader_mapping_table_without_strict_also_works(self) -> None:
        # The mapping table should also work in non-strict mode to guide resolution.
        nif = _write_nif(self.tmp, shader_type=0x12345678)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                strict_unknown_shader_types=False,
                unknown_shader_type_map={0x12345678: SHADER_TYPE_DEFAULT},
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)

    def test_strict_unknown_shader_weak_texture_guess_warns_but_passes(self) -> None:
        # Slot-4-only cubemap inference is intentionally weak; strict mode should
        # still pass while surfacing WEAK_RESOLUTION in diagnostics.
        nif = _write_nif(
            self.tmp,
            shader_type=0x12345678,
            texture_paths=[
                "textures\\dungeons\\barrels\\barrel01.dds",
                "",
                "",
                "",
                "textures\\cubemaps\\custom_cube.dds",
            ] + [""] * 4,
        )
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                strict_unknown_shader_types=True,
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        self.assertTrue(
            any("WEAK_RESOLUTION" in warning and "texture_slot_cubemap" in warning for warning in result.warnings),
            result.warnings,
        )

    def test_strict_unknown_shader_texture_suffix_guess_is_weak(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_type=0x12345678,
            texture_paths=[
                "textures\\dungeons\\barrels\\barrel01.dds",
                "",
                "",
                "textures\\dungeons\\barrels\\barrel01_p.dds",
            ] + [""] * 5,
        )
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                strict_unknown_shader_types=True,
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        self.assertTrue(
            any("WEAK_RESOLUTION" in warning and "texture_suffix_parallax" in warning for warning in result.warnings),
            result.warnings,
        )

    def test_resolution_classifier_boundaries(self) -> None:
        self.assertEqual(
            _classify_shader_type_resolution("mapping_table").type,
            RESOLUTION_RESOLVED,
        )
        self.assertEqual(
            _classify_shader_type_resolution("texture_suffix_parallax").type,
            RESOLUTION_WEAK,
        )
        self.assertEqual(
            _classify_shader_type_resolution("default_fallback").type,
            RESOLUTION_UNRESOLVED,
        )
        self.assertEqual(
            _classify_shader_type_resolution("future_unknown_resolution").type,
            RESOLUTION_UNRESOLVED,
        )

    def test_retryable_force_type3_error_detection(self) -> None:
        self.assertTrue(
            _is_retryable_force_type3_error(
                ValueError("recorded block size 104 does not match expected type-0 size 100")
            )
        )
        self.assertTrue(
            _is_retryable_force_type3_error(
                ValueError("cannot force shader type 3 on a real-layout Skyrim shader block")
            )
        )

    def test_non_retryable_patch_error_detection(self) -> None:
        self.assertFalse(_is_retryable_force_type3_error(RuntimeError("boom")))
        self.assertFalse(_is_retryable_force_type3_error(ValueError("some other parse issue")))


# ---------------------------------------------------------------------------
# Tests: texture path patching
# ---------------------------------------------------------------------------

class TestPatchTexturePaths(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_writes_parallax_texture_path(self) -> None:
        nif = _write_nif(self.tmp)
        patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_texture_path="textures\\arch\\stone_p.dds",
                backup=False,
            ),
        )
        infos = scan_nif(nif)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_PARALLAX), "textures\\arch\\stone_p.dds"
        )

    def test_extends_low_slot_texture_set_to_full_skyrim_slot_count(self) -> None:
        nif = _write_nif(
            self.tmp,
            texture_paths=["textures\\arch\\stone.dds", "textures\\arch\\stone_n.dds"],
        )
        result = patch_nif(
            nif,
            NifPatchOptions(
                parallax_texture_path="textures\\arch\\stone_p.dds",
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_PARALLAX), "textures\\arch\\stone_p.dds"
        )
        self.assertEqual(_texture_set_slot_count(nif), 9)

    def test_extends_low_slot_u16_texture_set_to_full_skyrim_slot_count(self) -> None:
        nif = _write_nif(
            self.tmp,
            texture_paths=["textures\\arch\\stone.dds", ""],
            texture_set_count_u16=True,
        )
        result = patch_nif(
            nif,
            NifPatchOptions(
                env_mask_texture_path="textures\\arch\\stone_m.dds",
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].texture_paths.get(TEXTURE_SLOT_ENV_MASK), "textures\\arch\\stone_m.dds")
        self.assertEqual(_texture_set_slot_count(nif), 9)

    def test_normalises_forward_slashes_to_backslashes(self) -> None:
        nif = _write_nif(self.tmp)
        patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_texture_path="textures/arch/stone_p.dds",
                backup=False,
            ),
        )
        infos = scan_nif(nif)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_PARALLAX), "textures\\arch\\stone_p.dds"
        )

    def test_normalises_absolute_data_textures_path_to_skyrim_relative(self) -> None:
        nif = _write_nif(self.tmp)
        patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_texture_path=r"C:\Modlist\Data\Textures\architecture\stone\stone_p.dds",
                backup=False,
            ),
        )
        infos = scan_nif(nif)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_PARALLAX),
            "textures\\architecture\\stone\\stone_p.dds",
        )

    def test_normalises_duplicate_textures_root_segments(self) -> None:
        nif = _write_nif(self.tmp)
        patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_texture_path=r"textures\\textures\\architecture\\stone\\stone_p.dds",
                backup=False,
            ),
        )
        infos = scan_nif(nif)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_PARALLAX),
            "textures\\architecture\\stone\\stone_p.dds",
        )

    def test_normalises_dot_segments_and_duplicate_separators(self) -> None:
        nif = _write_nif(self.tmp)
        patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_texture_path=r".\\textures\\architecture\\.\\stone\\..\\stone\\\\stone_p.dds",
                backup=False,
            ),
        )
        infos = scan_nif(nif)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_PARALLAX),
            "textures\\architecture\\stone\\stone_p.dds",
        )

    def test_normalises_singular_texture_root(self) -> None:
        nif = _write_nif(self.tmp)
        patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_texture_path=r"Texture\architecture\stone\stone_p.dds",
                backup=False,
            ),
        )
        infos = scan_nif(nif)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_PARALLAX),
            "textures\\architecture\\stone\\stone_p.dds",
        )

    def test_normalises_absolute_data_singular_texture_root(self) -> None:
        nif = _write_nif(self.tmp)
        patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_texture_path=r"C:\Modlist\Data\Texture\architecture\stone\stone_p.dds",
                backup=False,
            ),
        )
        infos = scan_nif(nif)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_PARALLAX),
            "textures\\architecture\\stone\\stone_p.dds",
        )

    def test_rejects_texture_paths_outside_textures_root(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_texture_path=r"C:\Users\Desktop\stone_p.dds",
                backup=False,
            ),
        )
        self.assertFalse(result.success)
        self.assertIn("Patch error:", " ".join(result.errors))
        self.assertIn("Expected a Skyrim-relative path under 'textures\\'", result.message)

    def test_rejects_whitespace_only_texture_path_option(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_texture_path="   ",
                backup=False,
            ),
        )
        self.assertFalse(result.success)
        self.assertEqual(result.message, "Invalid parallax_texture_path.")
        self.assertIn("parallax_texture_path cannot be empty or whitespace-only.", result.errors)

    def test_writes_normal_texture_path(self) -> None:
        nif = _write_nif(self.tmp)
        patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                normal_texture_path="textures\\arch\\stone_n.dds",
                backup=False,
            ),
        )
        infos = scan_nif(nif)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_NORMAL), "textures\\arch\\stone_n.dds"
        )

    def test_replace_longer_path_with_shorter(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_PARALLAX] = "textures\\very\\long\\original_path_p.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_texture_path="textures\\short_p.dds",
                backup=False,
            ),
        )
        infos = scan_nif(nif)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_PARALLAX), "textures\\short_p.dds"
        )

    def test_clear_parallax_texture_path(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_PARALLAX] = "textures\\arch\\stone_p.dds"
        nif = _write_nif(self.tmp, texture_paths=paths, flags1=SLSF1_PARALLAX)
        patch_nif(
            nif,
            NifPatchOptions(
                clear_parallax_texture_path=True,
                backup=False,
            ),
        )
        infos = scan_nif(nif)
        self.assertEqual(infos[0].texture_paths.get(TEXTURE_SLOT_PARALLAX, ""), "")

    def test_clear_env_mask_texture_path(self) -> None:
        paths = [""] * 9
        paths[5] = "textures\\arch\\stone_m.dds"
        nif = _write_nif(self.tmp, texture_paths=paths, flags1=SLSF1_ENVIRONMENT_MAPPING)
        patch_nif(
            nif,
            NifPatchOptions(
                clear_env_mask_texture_path=True,
                backup=False,
            ),
        )
        infos = scan_nif(nif)
        self.assertEqual(infos[0].texture_paths.get(5, ""), "")

    def test_writes_parallax_texture_path_even_without_enabling_parallax_flag(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(
            nif,
            NifPatchOptions(
                parallax_texture_path="textures\\arch\\stone_p.dds",
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].texture_paths.get(TEXTURE_SLOT_PARALLAX), "textures\\arch\\stone_p.dds")
        self.assertFalse(infos[0].has_parallax_flag)

    def test_writes_env_mask_texture_path_even_without_enabling_env_mapping_flag(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(
            nif,
            NifPatchOptions(
                env_mask_texture_path="textures\\arch\\stone_m.dds",
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].texture_paths.get(5), "textures\\arch\\stone_m.dds")
        self.assertFalse(infos[0].has_env_mapping_flag)


# ---------------------------------------------------------------------------
# Tests: parallax scale (type-3 blocks)
# ---------------------------------------------------------------------------

class TestParallaxScale(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_write_parallax_scale_on_type3_block(self) -> None:
        nif = _write_nif(
            self.tmp, shader_type=SHADER_TYPE_HEIGHTMAP, parallax_scale=1.0
        )
        patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_scale=4.5,
                backup=False,
            ),
        )
        infos = scan_nif(nif)
        self.assertAlmostEqual(infos[0].parallax_scale or 0.0, 4.5, places=2)

    def test_extreme_parallax_scale_allowed(self) -> None:
        nif = _write_nif(
            self.tmp, shader_type=SHADER_TYPE_HEIGHTMAP, parallax_scale=1.0
        )
        patch_nif(
            nif,
            NifPatchOptions(enable_parallax=True, parallax_scale=10.0, backup=False),
        )
        infos = scan_nif(nif)
        self.assertAlmostEqual(infos[0].parallax_scale or 0.0, 10.0, places=2)

    def test_no_scale_on_type0_without_force(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_DEFAULT)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_scale=3.0,
                force_shader_type_3=False,
                backup=False,
            ),
        )
        self.assertTrue(result.success)
        infos = scan_nif(nif)
        # Without force_shader_type_3, legacy type-0 blocks keep their type
        # and only receive compatible flag updates.
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_DEFAULT)
        self.assertIsNone(infos[0].parallax_scale)


# ---------------------------------------------------------------------------
# Tests: force_shader_type_3 (block upgrade)
# ---------------------------------------------------------------------------

class TestForceShaderType3(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_upgrades_type0_to_type3(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_DEFAULT)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_scale=3.0,
                force_shader_type_3=True,
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        self.assertEqual(result.blocks_upgraded_to_type3, 1)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_HEIGHTMAP)
        self.assertAlmostEqual(infos[0].parallax_scale or 0.0, 3.0, places=2)

    def test_flags_set_after_upgrade(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_DEFAULT)
        patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_scale=2.0,
                force_shader_type_3=True,
                backup=False,
            ),
        )
        infos = scan_nif(nif)
        self.assertTrue(infos[0].has_parallax_flag)

    def test_already_type3_not_double_upgraded(self) -> None:
        nif = _write_nif(
            self.tmp, shader_type=SHADER_TYPE_HEIGHTMAP, parallax_scale=1.0
        )
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_scale=2.5,
                force_shader_type_3=True,
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        self.assertEqual(result.blocks_upgraded_to_type3, 0)
        infos = scan_nif(nif)
        self.assertAlmostEqual(infos[0].parallax_scale or 0.0, 2.5, places=2)

    def test_nif_file_remains_parseable_after_upgrade(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_DEFAULT)
        patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_scale=5.0,
                force_shader_type_3=True,
                backup=False,
            ),
        )
        # Reparsing should succeed and return valid infos
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_HEIGHTMAP)

    def test_force_upgrade_falls_back_when_layout_mismatch(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_DEFAULT, shader_layout="real")

        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_scale=3.0,
                force_shader_type_3=True,
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        self.assertEqual(result.blocks_upgraded_to_type3, 0)
        self.assertTrue(any("Skipped shader type-3 block expansion" in w for w in result.warnings))
        info = scan_nif(nif)[0]
        self.assertEqual(info.shader_type, SHADER_TYPE_DEFAULT)
        self.assertTrue(info.has_parallax_flag)

    def test_skip_if_havok_does_not_upgrade_default_shader(self) -> None:
        nif = self.tmp / "havok_default.nif"
        nif.write_bytes(_build_nif_with_shapes(shader_type=SHADER_TYPE_DEFAULT, add_havok=True))
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                enable_pom=True,
                parallax_scale=5.0,
                force_shader_type_3=True,
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        self.assertEqual(result.blocks_upgraded_to_type3, 0)
        info = scan_nif(nif)[0]
        self.assertEqual(info.shader_type, SHADER_TYPE_DEFAULT)
        self.assertFalse(info.has_parallax_flag)
        self.assertFalse(info.has_pom_flag)



# ---------------------------------------------------------------------------
# Tests: helpers
# ---------------------------------------------------------------------------

class TestHelpers(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_guess_parallax_path_from_diffuse(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_parallax_path_for_nif(nif)
        self.assertIsNotNone(guessed)
        self.assertIn("_p.dds", guessed or "")

    def test_guess_parallax_returns_none_for_no_diffuse(self) -> None:
        nif = _write_nif(self.tmp)
        self.assertIsNone(guess_parallax_path_for_nif(nif))

    def test_guess_normal_path_standard(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_normal_path_for_nif(nif)
        self.assertIsNotNone(guessed)
        self.assertTrue((guessed or "").endswith("_n.dds"))

    def test_guess_normal_path_msn(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_normal_path_for_nif(nif, msn=True)
        self.assertIsNotNone(guessed)
        self.assertTrue((guessed or "").endswith("_msn.dds"))

    def test_actions_for_parallax_slot_match_subcodes_have_specific_guidance(self) -> None:
        diffuse_actions = _actions_for_conflict_code("path_slot_parallax.matches_diffuse")
        normal_actions = _actions_for_conflict_code("path_slot_parallax.matches_normal")
        self.assertTrue(any("diffuse/albedo" in action.lower() for action in diffuse_actions))
        self.assertTrue(any("slot-1 normal" in action.lower() for action in normal_actions))

    def test_find_nif_files_recursive(self) -> None:
        sub = self.tmp / "sub"
        sub.mkdir()
        (self.tmp / "a.nif").write_bytes(b"")
        (sub / "b.nif").write_bytes(b"")
        (self.tmp / "skip.txt").write_bytes(b"")
        found = find_nif_files(self.tmp)
        names = [f.name for f in found]
        self.assertIn("a.nif", names)
        self.assertIn("b.nif", names)
        self.assertNotIn("skip.txt", names)

    def test_find_nif_files_recursive_case_insensitive_extension(self) -> None:
        sub = self.tmp / "sub"
        sub.mkdir()
        (self.tmp / "upper.NIF").write_bytes(b"")
        (sub / "mixed.NiF").write_bytes(b"")
        found = find_nif_files(self.tmp)
        names = [f.name for f in found]
        self.assertIn("upper.NIF", names)
        self.assertIn("mixed.NiF", names)


# ---------------------------------------------------------------------------
# Tests: glow / diffuse texture slot patching
# ---------------------------------------------------------------------------

class TestGlowAndDiffusePatching(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_writes_glow_texture_path(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(
            nif,
            NifPatchOptions(
                glow_texture_path="textures\\arch\\stone_g.dds",
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_GLOW),
            "textures\\arch\\stone_g.dds",
        )

    def test_enable_glow_map_flag(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(
            nif,
            NifPatchOptions(enable_glow_map=True, backup=False),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertTrue(infos[0].has_glow_map_flag)

    def test_disable_glow_map_flag(self) -> None:
        nif = _write_nif(self.tmp, flags2=SLSF2_GLOW_MAP)
        result = patch_nif(
            nif,
            NifPatchOptions(disable_glow_map=True, backup=False),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertFalse(infos[0].has_glow_map_flag)

    def test_writes_diffuse_texture_path(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(
            nif,
            NifPatchOptions(
                diffuse_texture_path="textures\\arch\\stone_new.dds",
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(
            infos[0].texture_paths.get(TEXTURE_SLOT_DIFFUSE),
            "textures\\arch\\stone_new.dds",
        )

    def test_clear_glow_texture_path(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_GLOW] = "textures\\arch\\stone_g.dds"
        nif = _write_nif(self.tmp, texture_paths=paths, flags2=SLSF2_GLOW_MAP)
        result = patch_nif(
            nif,
            NifPatchOptions(clear_glow_texture_path=True, backup=False),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].texture_paths.get(TEXTURE_SLOT_GLOW, ""), "")

    def test_clear_diffuse_texture_path(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths)
        result = patch_nif(
            nif,
            NifPatchOptions(clear_diffuse_texture_path=True, backup=False),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].texture_paths.get(TEXTURE_SLOT_DIFFUSE, ""), "")

    def test_glow_and_parallax_patched_together(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(
            nif,
            NifPatchOptions(
                enable_parallax=True,
                parallax_texture_path="textures\\arch\\stone_p.dds",
                enable_glow_map=True,
                glow_texture_path="textures\\arch\\stone_g.dds",
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertTrue(infos[0].has_parallax_flag)
        self.assertTrue(infos[0].has_glow_map_flag)
        self.assertEqual(infos[0].texture_paths.get(TEXTURE_SLOT_PARALLAX), "textures\\arch\\stone_p.dds")
        self.assertEqual(infos[0].texture_paths.get(TEXTURE_SLOT_GLOW), "textures\\arch\\stone_g.dds")


# ---------------------------------------------------------------------------
# Tests: NifShaderInfo.shader_type_name and has_glow_map_flag
# ---------------------------------------------------------------------------

class TestNifShaderInfoProperties(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_shader_type_name_default(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_DEFAULT)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].shader_type_name, SHADER_TYPE_NAMES[SHADER_TYPE_DEFAULT])

    def test_shader_type_name_heightmap(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_HEIGHTMAP, parallax_scale=1.0)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].shader_type_name, SHADER_TYPE_NAMES[SHADER_TYPE_HEIGHTMAP])
        self.assertIn("Parallax", infos[0].shader_type_name)

    def test_shader_type_name_unknown(self) -> None:
        from nif_patcher import NifShaderInfo
        info = NifShaderInfo(
            block_index=0, shader_type=99, flags1=0, flags2=0,
            parallax_scale=None, texture_paths={}
        )
        self.assertIn("99", info.shader_type_name)

    def test_has_glow_map_flag_false_by_default(self) -> None:
        nif = _write_nif(self.tmp)
        infos = scan_nif(nif)
        self.assertFalse(infos[0].has_glow_map_flag)

    def test_has_glow_map_flag_true_when_set(self) -> None:
        nif = _write_nif(self.tmp, flags2=SLSF2_GLOW_MAP)
        infos = scan_nif(nif)
        self.assertTrue(infos[0].has_glow_map_flag)

    def test_shader_type_names_covers_all_known_types(self) -> None:
        for st in (SHADER_TYPE_DEFAULT, SHADER_TYPE_ENVMAP, SHADER_TYPE_GLOW,
                   SHADER_TYPE_HEIGHTMAP, SHADER_TYPE_MULTILAYER):
            self.assertIn(st, SHADER_TYPE_NAMES)


# ---------------------------------------------------------------------------
# Tests: guess_env_mask_path_for_nif and guess_glow_path_for_nif
# ---------------------------------------------------------------------------

class TestGuessHelpers(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_guess_glow_path_from_diffuse(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_glow_path_for_nif(nif)
        self.assertIsNotNone(guessed)
        self.assertTrue((guessed or "").endswith("_g.dds"))
        self.assertIn("stone", guessed or "")

    def test_guess_glow_path_from_existing_glow_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_GLOW] = "textures\\arch\\stone_glow.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_glow_path_for_nif(nif)
        self.assertIsNotNone(guessed)
        self.assertTrue((guessed or "").endswith("_g.dds"))

    def test_guess_glow_path_from_existing_emis_suffix(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_GLOW] = "textures\\arch\\stone_emis.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_glow_path_for_nif(nif)
        self.assertIsNotNone(guessed)
        self.assertTrue((guessed or "").endswith("_g.dds"))

    def test_guess_glow_path_from_existing_skin_tint_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_GLOW] = "textures\\actors\\dragon\\dragon_sk.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_glow_path_for_nif(nif)
        self.assertEqual(guessed, "textures\\actors\\dragon\\dragon_g.dds")

    def test_guess_glow_returns_none_for_no_diffuse(self) -> None:
        nif = _write_nif(self.tmp)
        self.assertIsNone(guess_glow_path_for_nif(nif))

    def test_guess_cubemap_path_from_diffuse(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_cubemap_path_for_nif(nif)
        self.assertEqual(guessed, "textures\\arch\\stone_e.dds")

    def test_guess_cubemap_path_from_existing_env_suffix(self) -> None:
        paths = [""] * 9
        paths[4] = "textures\\arch\\stone_env.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_cubemap_path_for_nif(nif)
        self.assertEqual(guessed, "textures\\arch\\stone_e.dds")

    def test_guess_cubemap_returns_none_for_no_paths(self) -> None:
        nif = _write_nif(self.tmp)
        self.assertIsNone(guess_cubemap_path_for_nif(nif))

    def test_guess_env_mask_path_from_diffuse(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_env_mask_path_for_nif(nif)
        self.assertIsNotNone(guessed)
        self.assertTrue((guessed or "").endswith("_m.dds"))
        self.assertIn("stone", guessed or "")

    def test_guess_env_mask_path_from_existing_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\arch\\stone_mask.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_env_mask_path_for_nif(nif)
        self.assertIsNotNone(guessed)
        self.assertTrue((guessed or "").endswith("_m.dds"))

    def test_guess_env_mask_path_from_existing_rmaos_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\arch\\stone_rmaos.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_env_mask_path_for_nif(nif)
        self.assertEqual(guessed, "textures\\arch\\stone_m.dds")

    def test_guess_env_mask_path_from_existing_orm_slot(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\arch\\stone_orm.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_env_mask_path_for_nif(nif)
        self.assertEqual(guessed, "textures\\arch\\stone_m.dds")

    def test_guess_env_mask_path_prefers_rmaos_suffix_when_requested(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_env_mask_path_for_nif(nif, preferred_suffix="_rmaos.dds")
        self.assertEqual(guessed, "textures\\arch\\stone_rmaos.dds")

    def test_guess_env_mask_path_prefers_orm_suffix_when_requested(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_env_mask_path_for_nif(nif, preferred_suffix="_orm")
        self.assertEqual(guessed, "textures\\arch\\stone_rmaos.dds")

    def test_guess_env_mask_path_prefers_cm_suffix_when_requested(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\arch\\stone_mask.dds"
        nif = _write_nif(self.tmp, texture_paths=paths)
        guessed = guess_env_mask_path_for_nif(nif, preferred_suffix="_cm")
        self.assertEqual(guessed, "textures\\arch\\stone_cm.dds")

    def test_guess_env_mask_returns_none_for_no_paths(self) -> None:
        nif = _write_nif(self.tmp)
        self.assertIsNone(guess_env_mask_path_for_nif(nif))


# ---------------------------------------------------------------------------
# Tests: batch_patch_nif
# ---------------------------------------------------------------------------

class TestBatchPatchNif(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_batch_patches_multiple_files(self) -> None:
        nif_a = self.tmp / "a.nif"
        nif_b = self.tmp / "b.nif"
        nif_a.write_bytes(_build_minimal_nif())
        nif_b.write_bytes(_build_minimal_nif())
        results = batch_patch_nif(
            [nif_a, nif_b],
            NifPatchOptions(enable_parallax=True, backup=False),
        )
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertTrue(r.success, r.errors)
        for nif in (nif_a, nif_b):
            infos = scan_nif(nif)
            self.assertTrue(infos[0].has_parallax_flag)

    def test_batch_returns_empty_list_for_empty_input(self) -> None:
        results = batch_patch_nif([], NifPatchOptions(enable_parallax=True, backup=False))
        self.assertEqual(results, [])

    def test_batch_captures_errors_without_raising(self) -> None:
        missing = self.tmp / "nonexistent.nif"
        results = batch_patch_nif(
            [missing],
            NifPatchOptions(enable_parallax=True, backup=False),
        )
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].success)
        self.assertTrue(len(results[0].errors) > 0)

    def test_batch_results_preserve_order(self) -> None:
        nifs = []
        for i in range(3):
            p = self.tmp / f"nif_{i}.nif"
            p.write_bytes(_build_minimal_nif())
            nifs.append(p)
        results = batch_patch_nif(nifs, NifPatchOptions(enable_parallax=True, backup=False))
        for i, r in enumerate(results):
            self.assertEqual(r.nif_path, nifs[i])


class TestMixedModValidationBatches(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_large_mixed_mod_fixture_batch_surfaces_granular_codes(self) -> None:
        fixtures: list[tuple[dict, tuple[str, ...], bool]] = [
            ({}, ("missing_parallax_flag.flag1_not_set.",), False),
            ({"flags1": SLSF1_SINGLE_PASS}, ("skip_single_pass.",), False),
            ({"texture_paths": ["textures\\arch\\stone_n.dds"] + [""] * 8}, ("path_slot_diffuse.wrong_suffix.",), False),
            ({"texture_paths": [""] * 9, "shader_layout": "real"}, ("missing_parallax_flag.flag1_not_set.",), False),
            ({"texture_paths": [""] * 9, "flags1": SLSF1_PARALLAX_OCCLUSION, "shader_type": SHADER_TYPE_DEFAULT}, ("flag_pom.without_base_parallax.", "flag_pom.non_heightmap_shader."), False),
            ({"texture_paths": [""] * 9}, ("missing_parallax_slot3.empty.",), False),
            ({"texture_paths": ["textures\\arch\\stone.dds", "textures\\arch\\stone_p.dds"] + [""] * 7}, ("path_slot_normal.wrong_suffix.",), False),
            ({"texture_paths": ["textures\\arch\\stone.dds"] + [""] * 8, "user_ver2": 130}, ("missing_parallax_flag.flag1_not_set.",), True),
            ({"texture_paths": [""] * 9, "user_ver2": 130}, ("missing_parallax_flag.flag1_not_set.",), True),
            ({"texture_paths": [""] * 9}, ("missing_parallax_flag.flag1_not_set.",), False),
            ({"texture_paths": [""] * 4 + ["textures\\arch\\stone_n.dds"] + [""] * 4}, ("path_slot_cubemap.wrong_suffix.",), False),
            ({"texture_paths": [""] * 9}, ("missing_parallax_flag.flag1_not_set.",), False),
        ]
        reports: list[tuple[Path, list[str]]] = []
        for idx, (kwargs, expected_prefixes, make_fallout) in enumerate(fixtures):
            nif = self.tmp / f"batch_{idx}.nif"
            nif.write_bytes(_build_minimal_nif(**kwargs))
            if make_fallout:
                _rewrite_user_version(nif, 11)
            validation = validate_nif_for_parallax(nif)
            codes = [group.code for group in validation.conflict_report]
            reports.append((nif, codes))
            for prefix in expected_prefixes:
                self.assertTrue(
                    any(code.startswith(prefix) for code in codes),
                    f"{nif.name} missing {prefix}; got {codes}",
                )

        all_codes = [code for _, codes in reports for code in codes]
        self.assertGreaterEqual(len(reports), 12)
        self.assertTrue(any(".skyrim.legacy" in code for code in all_codes))
        self.assertTrue(any(".skyrim.real" in code for code in all_codes))
        self.assertTrue(any(".fallout." in code for code in all_codes))


class TestParitySampleMatrix(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_structured_parity_matrix_cases_cover_expected_conflict_prefixes(self) -> None:
        payload = _load_fixture_corpus_payload(_FIXTURE_PARITY_SAMPLE_MATRIX)
        cases = payload.get("cases", [])
        self.assertIsInstance(cases, list)
        corpus = _materialize_fixture_corpus(self.tmp, payload)
        self.assertEqual(len(corpus), len(cases))
        validations = [validate_nif_for_parallax(path) for path in corpus]

        all_codes: list[str] = []
        for case, validation in zip(cases, validations):
            self.assertIsInstance(case, dict)
            expected_prefixes = case.get("expected_prefixes", [])
            self.assertIsInstance(expected_prefixes, list)
            expected_absent_prefixes = case.get("expected_absent_prefixes", [])
            self.assertIsInstance(expected_absent_prefixes, list)
            expected_remediation_steps = case.get("expected_remediation_steps", [])
            self.assertIsInstance(expected_remediation_steps, list)
            expected_absent_remediation_steps = case.get("expected_absent_remediation_steps", [])
            self.assertIsInstance(expected_absent_remediation_steps, list)
            expected_no_auto_remediation = bool(case.get("expected_no_auto_remediation", False))
            codes = [group.code for group in validation.conflict_report]
            all_codes.extend(codes)
            for prefix in expected_prefixes:
                self.assertTrue(
                    any(code.startswith(str(prefix)) for code in codes),
                    f"{case.get('id', 'case')} missing {prefix}; got {codes}",
                )
            for prefix in expected_absent_prefixes:
                self.assertFalse(
                    any(code.startswith(str(prefix)) for code in codes),
                    f"{case.get('id', 'case')} unexpectedly matched {prefix}; got {codes}",
                )
            if expected_remediation_steps or expected_absent_remediation_steps or expected_no_auto_remediation:
                opts, rem_steps = build_auto_remediation_patch_options(
                    validation.nif_path,
                    codes,
                    backup=False,
                )
                if expected_no_auto_remediation:
                    self.assertIsNone(
                        opts,
                        f"{case.get('id', 'case')} expected no auto-remediation but got steps={rem_steps}",
                    )
                else:
                    self.assertIsNotNone(
                        opts,
                        f"{case.get('id', 'case')} expected auto-remediation options but got none",
                    )
                for step in expected_remediation_steps:
                    self.assertIn(
                        str(step),
                        rem_steps,
                        f"{case.get('id', 'case')} missing remediation step {step!r}; got {rem_steps}",
                    )
                for step in expected_absent_remediation_steps:
                    self.assertNotIn(
                        str(step),
                        rem_steps,
                        f"{case.get('id', 'case')} unexpectedly included remediation step {step!r}; got {rem_steps}",
                    )

        self.assertTrue(any(".skyrim." in code for code in all_codes))
        self.assertTrue(any(".fallout." in code for code in all_codes))
        annotated_strategy_cases = [
            case
            for case in cases
            if isinstance(case, dict)
            and str(case.get("pgpatcher_strategy", "")).strip()
            and str(case.get("local_strategy", "")).strip()
        ]
        self.assertGreaterEqual(
            len(annotated_strategy_cases),
            5,
            "Parity sample matrix should keep enough side-by-side strategy annotations for parity review.",
        )
        self.assertTrue(
            any(bool(case.get("intentional_strategy_difference", False)) for case in annotated_strategy_cases),
            "Parity sample matrix should include at least one intentional strategy divergence case.",
        )
        self.assertTrue(
            any(not bool(case.get("intentional_strategy_difference", False)) for case in annotated_strategy_cases),
            "Parity sample matrix should include at least one aligned strategy case.",
        )
        manual_review_cases = [
            case
            for case in cases
            if isinstance(case, dict) and bool(case.get("expected_no_auto_remediation", False))
        ]
        auto_remediable_cases = [
            case
            for case in cases
            if isinstance(case, dict)
            and isinstance(case.get("expected_remediation_steps", []), list)
            and len(case.get("expected_remediation_steps", [])) > 0
        ]
        self.assertGreaterEqual(
            len(manual_review_cases),
            2,
            "Parity sample matrix should include manual-review conflict families in addition to auto-remediable ones.",
        )
        self.assertGreaterEqual(
            len(auto_remediable_cases),
            5,
            "Parity sample matrix should include several auto-remediable families for parity tracking.",
        )
        report = build_parity_delta_report_text(summarize_validation_conflicts(validations), max_rows=12)
        self.assertIn("NIF parity delta report", report)
        self.assertIn("| Conflict code | Count | Files | Auto-remediation | Suggested action |", report)


class TestRealModSamplePacks(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_pack_baselines_cover_family_counts_and_expected_conflict_profiles(self) -> None:
        payload = _load_fixture_corpus_payload(_FIXTURE_REALMOD_SAMPLE_PACKS)
        packs = payload.get("packs", [])
        self.assertIsInstance(packs, list)
        self.assertGreater(len(packs), 0)

        for pack in packs:
            self.assertIsInstance(pack, dict)
            pack_id = str(pack.get("id", "pack")).strip() or "pack"
            cases = pack.get("cases", [])
            self.assertIsInstance(cases, list)
            pack_root = self.tmp / pack_id
            pack_root.mkdir(parents=True, exist_ok=True)
            corpus = _materialize_fixture_corpus(pack_root, {"cases": cases})
            self.assertEqual(len(corpus), len(cases), f"{pack_id}: case materialization mismatch")
            validations = [validate_nif_for_parallax(path) for path in corpus]

            case_map: dict[str, dict[str, object]] = {}
            for case in cases:
                if isinstance(case, dict):
                    case_id = str(case.get("id", "")).strip()
                    if case_id:
                        case_map[case_id] = case

            observed_family_case_counts: dict[str, int] = {}
            family_pass_fail: dict[str, dict[str, int]] = {}
            family_remediation_expectation_coverage: dict[str, dict[str, int]] = {}
            family_strategy_alignment: dict[str, dict[str, int]] = {}
            family_difference_buckets: dict[str, dict[str, int]] = {}
            fallback_conflict_groups = 0
            total_conflict_groups = 0
            for nif_path, validation in zip(corpus, validations):
                case = case_map.get(nif_path.stem, {})
                family = str(case.get("family", "unknown")).strip() or "unknown"
                observed_family_case_counts[family] = observed_family_case_counts.get(family, 0) + 1
                family_pass_fail.setdefault(family, {"pass": 0, "fail": 0})
                family_remediation_expectation_coverage.setdefault(
                    family,
                    {"with_expectation": 0, "total": 0},
                )
                family_strategy_alignment.setdefault(
                    family,
                    {"aligned": 0, "annotated": 0},
                )
                family_difference_buckets.setdefault(family, {})
                expected_prefixes = case.get("expected_prefixes", []) if isinstance(case, dict) else []
                expected_absent_prefixes = case.get("expected_absent_prefixes", []) if isinstance(case, dict) else []
                expected_remediation_steps = (
                    case.get("expected_remediation_steps", []) if isinstance(case, dict) else []
                )
                expected_absent_remediation_steps = (
                    case.get("expected_absent_remediation_steps", []) if isinstance(case, dict) else []
                )
                expected_no_auto_remediation = (
                    bool(case.get("expected_no_auto_remediation", False))
                    if isinstance(case, dict)
                    else False
                )
                pgpatcher_strategy = (
                    str(case.get("pgpatcher_strategy", "")).strip()
                    if isinstance(case, dict)
                    else ""
                )
                local_strategy = (
                    str(case.get("local_strategy", "")).strip()
                    if isinstance(case, dict)
                    else ""
                )
                intentional_strategy_difference = (
                    bool(case.get("intentional_strategy_difference", False))
                    if isinstance(case, dict)
                    else False
                )
                expected_difference_bucket = (
                    str(case.get("expected_difference_bucket", "")).strip()
                    if isinstance(case, dict)
                    else ""
                )
                safety_difference_note = (
                    str(case.get("safety_difference_note", "")).strip()
                    if isinstance(case, dict)
                    else ""
                )
                codes = [group.code for group in validation.conflict_report]
                total_conflict_groups += len(codes)
                fallback_conflict_groups += sum(
                    1 for code in codes if str(code).startswith("fallback_or_unknown.")
                )
                if intentional_strategy_difference:
                    self.assertTrue(
                        pgpatcher_strategy,
                        f"{pack_id}/{nif_path.stem}: intentional strategy differences must declare pgpatcher_strategy",
                    )
                    self.assertTrue(
                        local_strategy,
                        f"{pack_id}/{nif_path.stem}: intentional strategy differences must declare local_strategy",
                    )
                    self.assertTrue(
                        expected_difference_bucket,
                        f"{pack_id}/{nif_path.stem}: intentional strategy differences must declare expected_difference_bucket",
                    )
                    self.assertTrue(
                        safety_difference_note,
                        f"{pack_id}/{nif_path.stem}: intentional strategy differences must include safety_difference_note",
                    )
                if expected_difference_bucket:
                    buckets = family_difference_buckets[family]
                    buckets[expected_difference_bucket] = int(buckets.get(expected_difference_bucket, 0)) + 1
                case_ok = True
                if isinstance(expected_prefixes, list):
                    for prefix in expected_prefixes:
                        if not any(code.startswith(str(prefix)) for code in codes):
                            case_ok = False
                            break
                if case_ok and isinstance(expected_absent_prefixes, list):
                    for prefix in expected_absent_prefixes:
                        if any(code.startswith(str(prefix)) for code in codes):
                            case_ok = False
                            break
                has_remediation_expectation = bool(
                    (isinstance(expected_remediation_steps, list) and expected_remediation_steps)
                    or (isinstance(expected_absent_remediation_steps, list) and expected_absent_remediation_steps)
                    or expected_no_auto_remediation
                )
                family_remediation_expectation_coverage[family]["total"] += 1
                if pgpatcher_strategy and local_strategy:
                    family_strategy_alignment[family]["annotated"] += 1
                    if not intentional_strategy_difference:
                        family_strategy_alignment[family]["aligned"] += 1
                if has_remediation_expectation:
                    family_remediation_expectation_coverage[family]["with_expectation"] += 1
                    opts, rem_steps = build_auto_remediation_patch_options(
                        validation.nif_path,
                        codes,
                        backup=False,
                    )
                    if expected_no_auto_remediation:
                        self.assertIsNone(
                            opts,
                            f"{pack_id}/{nif_path.stem}: expected no auto-remediation options, got {rem_steps}",
                        )
                    else:
                        self.assertIsNotNone(
                            opts,
                            f"{pack_id}/{nif_path.stem}: expected auto-remediation options but got none",
                        )
                    if isinstance(expected_remediation_steps, list):
                        for step in expected_remediation_steps:
                            self.assertIn(
                                str(step),
                                rem_steps,
                                f"{pack_id}/{nif_path.stem}: missing remediation step {step!r}; got {rem_steps}",
                            )
                    if isinstance(expected_absent_remediation_steps, list):
                        for step in expected_absent_remediation_steps:
                            self.assertNotIn(
                                str(step),
                                rem_steps,
                                f"{pack_id}/{nif_path.stem}: unexpected remediation step {step!r}; got {rem_steps}",
                            )
                if case_ok:
                    family_pass_fail[family]["pass"] += 1
                else:
                    family_pass_fail[family]["fail"] += 1

            expected_family_case_counts = pack.get("expected_family_case_counts", {})
            self.assertIsInstance(expected_family_case_counts, dict)
            self.assertEqual(
                observed_family_case_counts,
                {str(k): int(v) for k, v in expected_family_case_counts.items()},
                f"{pack_id}: family case counts changed",
            )
            allowed_fallback_groups = max(2, int(total_conflict_groups * 0.08))
            self.assertLessEqual(
                fallback_conflict_groups,
                allowed_fallback_groups,
                (
                    f"{pack_id}: fallback_or_unknown groups {fallback_conflict_groups} exceed threshold "
                    f"{allowed_fallback_groups} out of {total_conflict_groups} grouped conflicts"
                ),
            )
            for family, stats in family_pass_fail.items():
                self.assertEqual(stats["fail"], 0, f"{pack_id}: family {family} has failing parity expectations")
                self.assertGreater(stats["pass"], 0, f"{pack_id}: family {family} has zero passing cases")

            expected_family_min_pass_ratio = pack.get("expected_family_min_pass_ratio", {})
            self.assertIsInstance(expected_family_min_pass_ratio, dict)
            for family, min_ratio_raw in expected_family_min_pass_ratio.items():
                family_name = str(family)
                min_ratio = float(min_ratio_raw)
                stats = family_pass_fail.get(family_name)
                self.assertIsNotNone(stats, f"{pack_id}: threshold references unknown family {family_name!r}")
                assert stats is not None
                total = int(stats["pass"]) + int(stats["fail"])
                observed_ratio = (float(stats["pass"]) / float(total)) if total > 0 else 0.0
                self.assertGreaterEqual(
                    observed_ratio,
                    min_ratio,
                    f"{pack_id}: family {family_name} pass ratio {observed_ratio:.3f} below threshold {min_ratio:.3f}",
                )

            expected_family_min_remediation_coverage = pack.get(
                "expected_family_min_remediation_expectation_coverage",
                {},
            )
            self.assertIsInstance(expected_family_min_remediation_coverage, dict)
            for family, min_ratio_raw in expected_family_min_remediation_coverage.items():
                family_name = str(family)
                min_ratio = float(min_ratio_raw)
                coverage = family_remediation_expectation_coverage.get(family_name)
                self.assertIsNotNone(coverage, f"{pack_id}: remediation-coverage threshold references unknown family {family_name!r}")
                assert coverage is not None
                total = int(coverage["total"])
                with_expectation = int(coverage["with_expectation"])
                observed_ratio = (float(with_expectation) / float(total)) if total > 0 else 0.0
                self.assertGreaterEqual(
                    observed_ratio,
                    min_ratio,
                    (
                        f"{pack_id}: family {family_name} remediation expectation coverage {observed_ratio:.3f} "
                        f"below threshold {min_ratio:.3f}"
                    ),
                )

            expected_family_min_strategy_alignment = pack.get(
                "expected_family_min_strategy_alignment_ratio",
                {},
            )
            self.assertIsInstance(expected_family_min_strategy_alignment, dict)
            for family, min_ratio_raw in expected_family_min_strategy_alignment.items():
                family_name = str(family)
                min_ratio = float(min_ratio_raw)
                alignment = family_strategy_alignment.get(family_name)
                self.assertIsNotNone(
                    alignment,
                    f"{pack_id}: strategy-alignment threshold references unknown family {family_name!r}",
                )
                assert alignment is not None
                annotated = int(alignment["annotated"])
                self.assertGreater(
                    annotated,
                    0,
                    f"{pack_id}: family {family_name} has no strategy annotations for alignment threshold checks",
                )
                aligned = int(alignment["aligned"])
                observed_ratio = float(aligned) / float(annotated)
                self.assertGreaterEqual(
                    observed_ratio,
                    min_ratio,
                    (
                        f"{pack_id}: family {family_name} strategy alignment ratio {observed_ratio:.3f} "
                        f"below threshold {min_ratio:.3f}"
                    ),
                )

            expected_family_allowed_difference_buckets = pack.get(
                "expected_family_allowed_difference_buckets",
                {},
            )
            self.assertIsInstance(expected_family_allowed_difference_buckets, dict)
            for family, allowed_raw in expected_family_allowed_difference_buckets.items():
                family_name = str(family)
                allowed = {str(value) for value in allowed_raw} if isinstance(allowed_raw, list) else set()
                self.assertTrue(allowed, f"{pack_id}: {family_name} must define at least one allowed difference bucket")
                observed_buckets = family_difference_buckets.get(family_name, {})
                self.assertIsNotNone(
                    observed_buckets,
                    f"{pack_id}: allowed-difference-bucket rule references unknown family {family_name!r}",
                )
                assert observed_buckets is not None
                for bucket_name, bucket_count in observed_buckets.items():
                    if int(bucket_count) <= 0:
                        continue
                    self.assertIn(
                        str(bucket_name),
                        allowed,
                        (
                            f"{pack_id}: family {family_name} used unexpected intentional-difference bucket "
                            f"{bucket_name!r}; allowed={sorted(allowed)}"
                        ),
                    )

            expected_family_min_difference_bucket_counts = pack.get(
                "expected_family_min_difference_bucket_counts",
                {},
            )
            self.assertIsInstance(expected_family_min_difference_bucket_counts, dict)
            for family, bucket_thresholds in expected_family_min_difference_bucket_counts.items():
                family_name = str(family)
                self.assertIsInstance(
                    bucket_thresholds,
                    dict,
                    f"{pack_id}: {family_name} min-difference-bucket thresholds must be an object",
                )
                observed_buckets = family_difference_buckets.get(family_name, {})
                self.assertIsNotNone(
                    observed_buckets,
                    f"{pack_id}: min-difference-bucket thresholds reference unknown family {family_name!r}",
                )
                assert observed_buckets is not None
                for bucket_name, min_count_raw in bucket_thresholds.items():
                    min_count = int(min_count_raw)
                    observed_count = int(observed_buckets.get(str(bucket_name), 0))
                    self.assertGreaterEqual(
                        observed_count,
                        min_count,
                        (
                            f"{pack_id}: family {family_name} intentional-difference bucket {bucket_name!r} "
                            f"count {observed_count} below threshold {min_count}"
                        ),
                    )

            summary = summarize_validation_conflicts(validations)
            summary_codes = [group.code for group in summary]
            required_prefixes = pack.get("required_summary_code_prefixes", [])
            self.assertIsInstance(required_prefixes, list)
            for prefix in required_prefixes:
                self.assertTrue(
                    any(code.startswith(str(prefix)) for code in summary_codes),
                    f"{pack_id}: missing required prefix {prefix!r}",
                )


class TestBatchConflictSummaries(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_summarize_validation_conflicts_groups_across_files(self) -> None:
        nif_a = _write_nif(
            self.tmp,
            texture_paths=["textures\\arch\\stone_n.dds"] + [""] * 8,
        )
        nif_b = self.tmp / "b.nif"
        nif_b.write_bytes(
            _build_minimal_nif(
                texture_paths=["textures\\arch\\stone_n.dds"] + [""] * 8
            )
        )
        v_a = validate_nif_for_parallax(nif_a)
        v_b = validate_nif_for_parallax(nif_b)
        summary = summarize_validation_conflicts([v_a, v_b])
        target = next(group for group in summary if group.code.startswith("path_slot_diffuse.wrong_suffix."))
        self.assertEqual(target.file_count, 2)
        self.assertGreaterEqual(target.count, 2)
        self.assertIn("test.nif", target.example_files)
        self.assertIn("b.nif", target.example_files)
        self.assertTrue(any("slot 0" in action.lower() for action in target.suggested_actions))

    def test_summarize_validation_conflicts_limits_example_files(self) -> None:
        validations = []
        for idx in range(5):
            p = self.tmp / f"many_{idx}.nif"
            p.write_bytes(
                _build_minimal_nif(
                    texture_paths=["textures\\arch\\stone_n.dds"] + [""] * 8
                )
            )
            validations.append(validate_nif_for_parallax(p))
        summary = summarize_validation_conflicts(validations, max_example_files=2)
        target = next(group for group in summary if group.code.startswith("path_slot_diffuse.wrong_suffix."))
        self.assertEqual(target.file_count, 5)
        self.assertEqual(len(target.example_files), 2)


class TestPluginAwareConflictSummaries(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_plugin_aware_summary_counts_plugins_per_conflict(self) -> None:
        a = self.tmp / "a.nif"
        b = self.tmp / "b.nif"
        a.write_bytes(_build_minimal_nif(texture_paths=["textures\\arch\\stone_n.dds"] + [""] * 8))
        b.write_bytes(_build_minimal_nif(texture_paths=["textures\\arch\\stone_n.dds"] + [""] * 8))
        v_a = validate_nif_for_parallax(a)
        v_b = validate_nif_for_parallax(b)
        summary = summarize_plugin_aware_validation_conflicts(
            [v_a, v_b],
            plugin_context={
                str(a).lower(): [
                    NifPluginConflictRef(plugin_name="MyMod.esp", record_id="0x0001", record_type="STAT"),
                    NifPluginConflictRef(plugin_name="Patch.esp", record_id="0x1001", record_type="STAT"),
                ],
                str(b).lower(): [
                    NifPluginConflictRef(plugin_name="Patch.esp", record_id="0x1002", record_type="STAT"),
                ],
            },
        )
        target = next(group for group in summary if group.code.startswith("path_slot_diffuse.wrong_suffix."))
        self.assertEqual(target.file_count, 2)
        self.assertEqual(target.plugin_count, 2)
        self.assertIn("MyMod.esp", target.example_plugins)
        self.assertIn("Patch.esp", target.example_plugins)


class TestAutoRemediationExecutor(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_build_auto_remediation_options_enables_matching_flags(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\arch\\stone_m.dds"
        paths[TEXTURE_SLOT_GLOW] = "textures\\arch\\stone_g.dds"
        nif = _write_nif(self.tmp, texture_paths=paths, flags1=0, flags2=0)
        v = validate_nif_for_parallax(nif)
        codes = [group.code for group in v.conflict_report]
        opts, steps = build_auto_remediation_patch_options(nif, codes)
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(opts.enable_parallax)
        self.assertTrue(opts.enable_env_mapping)
        self.assertTrue(opts.enable_glow_map)
        self.assertIn("enable_parallax", steps)
        self.assertIn("enable_env_mapping", steps)
        self.assertIn("enable_glow_map", steps)

    def test_auto_remediate_conflicts_applies_changes(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        paths[TEXTURE_SLOT_PARALLAX] = "textures\\arch\\stone_p.dds"
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\arch\\stone_m.dds"
        paths[TEXTURE_SLOT_GLOW] = "textures\\arch\\stone_g.dds"
        nif = _write_nif(
            self.tmp,
            texture_paths=paths,
            flags1=SLSF1_PARALLAX_OCCLUSION,
            flags2=0,
            shader_type=SHADER_TYPE_DEFAULT,
        )
        before = validate_nif_for_parallax(nif)
        before_codes = [group.code for group in before.conflict_report]
        result, steps = auto_remediate_nif_conflicts(nif, before_codes, backup=False)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.success, result.errors)
        after = scan_nif(nif)[0]
        self.assertTrue(after.has_parallax_flag)
        self.assertTrue(after.has_env_mapping_flag)
        self.assertTrue(after.has_glow_map_flag)
        self.assertIn("enable_parallax", steps)

    def test_auto_remediate_conflicts_end_to_end_validate_report_rerun(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        paths[TEXTURE_SLOT_PARALLAX] = "textures\\arch\\stone_p.dds"
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\arch\\stone_m.dds"
        paths[TEXTURE_SLOT_GLOW] = "textures\\arch\\stone_g.dds"
        nif = _write_nif(
            self.tmp,
            texture_paths=paths,
            flags1=0,
            flags2=0,
            shader_type=SHADER_TYPE_DEFAULT,
        )
        before = validate_nif_for_parallax(nif)
        before_codes = [group.code for group in before.conflict_report]
        self.assertTrue(any(code.startswith("missing_parallax_flag.") for code in before_codes))
        self.assertTrue(any(code.startswith("flag_env_mapping.") for code in before_codes))
        self.assertTrue(any(code.startswith("flag_glow_map.") for code in before_codes))

        result, steps = auto_remediate_nif_conflicts(nif, before_codes, backup=False)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.success, result.errors)
        self.assertIn("enable_parallax", steps)
        self.assertIn("enable_env_mapping", steps)
        self.assertIn("enable_glow_map", steps)

        after = validate_nif_for_parallax(nif)
        after_codes = [group.code for group in after.conflict_report]
        self.assertLessEqual(len(after_codes), len(before_codes))
        self.assertFalse(any(code.startswith("missing_parallax_flag.") for code in after_codes))
        self.assertFalse(any(code.startswith("flag_env_mapping.") for code in after_codes))
        self.assertFalse(any(code.startswith("flag_glow_map.") for code in after_codes))

    def test_auto_remediation_build_options_carries_fallout_gate_flags(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=130)
        _rewrite_user_version(nif, 11)
        opts, _steps = build_auto_remediation_patch_options(
            nif,
            ["missing_parallax_flag.fallout.legacy"],
            target_game="fallout",
            experimental_fallout_write=True,
            fallout_allow_parallax_scale=True,
            fallout_allow_fix_mesh_lighting=True,
            fallout_allow_spec_strength=True,
            fallout_allow_spec_color=True,
            fallout_allow_env_map_scale=True,
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(opts.fallout_allow_parallax_scale)
        self.assertTrue(opts.fallout_allow_fix_mesh_lighting)
        self.assertTrue(opts.fallout_allow_spec_strength)
        self.assertTrue(opts.fallout_allow_spec_color)
        self.assertTrue(opts.fallout_allow_env_map_scale)

    def test_auto_remediation_build_options_propagates_single_pass_policy(self) -> None:
        nif = _write_nif(self.tmp, flags1=SLSF1_SINGLE_PASS)
        opts, _steps = build_auto_remediation_patch_options(
            nif,
            ["missing_parallax_flag.skyrim.real"],
            skip_single_pass=False,
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertFalse(opts.skip_single_pass)

    def test_auto_remediation_build_options_disables_pom_for_non_heightmap_conflict(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_DEFAULT)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["flag_pom.non_heightmap_shader.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(opts.disable_pom)
        self.assertIn("disable_pom_for_non_heightmap_shader", steps)

    def test_auto_remediation_build_options_disables_glow_for_missing_slot2_conflict(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_DEFAULT)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["flag_glow_map.flag_set_without_slot2.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(opts.disable_glow_map)
        self.assertIn("disable_glow_map_for_missing_slot2", steps)

    def test_auto_remediation_skips_enable_parallax_when_disabling_non_heightmap_pom(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_DEFAULT)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            [
                "flag_pom.without_base_parallax.skyrim.legacy",
                "flag_pom.non_heightmap_shader.skyrim.legacy",
            ],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertFalse(opts.enable_parallax)
        self.assertTrue(opts.disable_pom)
        self.assertNotIn("enable_parallax_for_pom", steps)
        self.assertIn("disable_pom_for_non_heightmap_shader", steps)

    def test_auto_remediation_build_options_sets_cubemap_slot_when_guess_available(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["path_slot_cubemap.wrong_suffix.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(str(opts.cubemap_texture_path).lower().endswith("_e.dds"))
        self.assertIn("set_slot4_cubemap", steps)

    def test_auto_remediation_build_options_disables_parallax_for_missing_slot3_shader_state(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_HEIGHTMAP)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["shader_state.parallax_type_missing_slot3.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(opts.disable_parallax)
        self.assertIn("disable_parallax_for_missing_slot3", steps)

    def test_auto_remediation_build_options_sets_slots_for_missing_envmap_slots_when_guessable(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_ENVMAP, texture_paths=paths)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["shader_state.envmap_missing_slots4_5.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(str(opts.cubemap_texture_path).lower().endswith("_e.dds"))
        self.assertTrue(str(opts.env_mask_texture_path).lower().endswith("_m.dds"))
        self.assertIn("set_slot4_cubemap_for_missing_envmap_slots4_5", steps)
        self.assertIn("set_slot5_env_mask_for_missing_envmap_slots4_5", steps)
        self.assertIn("enable_env_mapping_for_missing_envmap_slots4_5", steps)
        self.assertNotIn("disable_env_mapping_for_missing_slots4_5", steps)
        self.assertTrue(opts.enable_env_mapping)
        self.assertFalse(opts.disable_env_mapping)

    def test_auto_remediation_build_options_disables_env_mapping_for_missing_envmap_slots_without_guesses(self) -> None:
        paths = [""] * 9
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_ENVMAP, texture_paths=paths)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["shader_state.envmap_missing_slots4_5.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(opts.disable_env_mapping)
        self.assertIn("disable_env_mapping_for_missing_slots4_5", steps)

    def test_auto_remediation_build_options_enables_env_mapping_for_slot4_flag_conflict(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_DEFAULT)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["flag_env_mapping.slot4_filled_without_flag.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(opts.enable_env_mapping)
        self.assertIn("enable_env_mapping", steps)

    def test_auto_remediation_build_options_sets_cubemap_for_missing_envmap_slot4_when_guessable(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_ENVMAP, texture_paths=paths)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["shader_state.envmap_missing_slot4.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(str(opts.cubemap_texture_path).lower().endswith("_e.dds"))
        self.assertIn("set_slot4_cubemap_for_missing_envmap_slot4", steps)
        self.assertIn("enable_env_mapping_for_missing_envmap_slot4", steps)
        self.assertTrue(opts.enable_env_mapping)
        self.assertNotIn("disable_env_mapping_for_missing_slot4", steps)

    def test_auto_remediation_build_options_disables_env_mapping_for_missing_envmap_slot4_without_guess(self) -> None:
        paths = [""] * 9
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_ENVMAP, texture_paths=paths)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["shader_state.envmap_missing_slot4.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(opts.disable_env_mapping)
        self.assertIn("disable_env_mapping_for_missing_slot4", steps)

    def test_auto_remediation_build_options_sets_env_mask_for_missing_envmap_slot5_when_guessable(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        paths[TEXTURE_SLOT_CUBEMAP] = "textures\\cubemaps\\stone_e.dds"
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_ENVMAP, texture_paths=paths)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["shader_state.envmap_missing_slot5.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(str(opts.env_mask_texture_path).lower().endswith("_m.dds"))
        self.assertIn("set_slot5_env_mask_for_missing_envmap_slot5", steps)
        self.assertIn("enable_env_mapping_for_missing_envmap_slot5", steps)
        self.assertTrue(opts.enable_env_mapping)
        self.assertNotIn("disable_env_mapping_for_missing_slot5", steps)

    def test_auto_remediation_build_options_disables_env_mapping_and_pom_for_mixed_envmap_pom_conflict(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_ENVMAP)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["shader_state.envmap_pom_missing_env_slots.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(opts.disable_env_mapping)
        self.assertTrue(opts.disable_pom)
        self.assertIn("disable_env_mapping_for_envmap_pom_mixed_unresolved", steps)
        self.assertIn("disable_pom_for_envmap_pom_mixed_unresolved", steps)

    def test_auto_remediation_build_options_restores_paths_for_mixed_envmap_glow_conflict_when_guessable(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_ENVMAP, texture_paths=paths)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["shader_state.envmap_glow_missing_slots2_4_5.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(str(opts.glow_texture_path).lower().endswith("_g.dds"))
        self.assertTrue(str(opts.cubemap_texture_path).lower().endswith("_e.dds"))
        self.assertTrue(str(opts.env_mask_texture_path).lower().endswith("_m.dds"))
        self.assertIn("set_slot2_glow_for_envmap_glow_mixed_unresolved", steps)
        self.assertIn("set_slot4_cubemap_for_envmap_glow_mixed_unresolved", steps)
        self.assertIn("set_slot5_env_mask_for_envmap_glow_mixed_unresolved", steps)
        self.assertIn("enable_env_mapping_for_envmap_glow_mixed_unresolved", steps)
        self.assertIn("enable_glow_map_for_envmap_glow_mixed_unresolved", steps)
        self.assertNotIn("disable_env_mapping_for_envmap_glow_mixed_unresolved", steps)
        self.assertNotIn("disable_glow_map_for_envmap_glow_mixed_unresolved", steps)

    def test_auto_remediation_build_options_disables_env_mapping_and_glow_for_mixed_envmap_glow_conflict_without_guesses(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            texture_paths=[""] * 9,
        )
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["shader_state.envmap_glow_missing_slots2_4_5.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(opts.disable_env_mapping)
        self.assertTrue(opts.disable_glow_map)
        self.assertIn("disable_env_mapping_for_envmap_glow_mixed_unresolved", steps)
        self.assertIn("disable_glow_map_for_envmap_glow_mixed_unresolved", steps)

    def test_auto_remediation_build_options_restores_glow_for_envmap_glow_slot2_only_conflict(self) -> None:
        paths = ["textures\\effects\\aura.dds"] + [""] * 8
        paths[TEXTURE_SLOT_CUBEMAP] = "textures\\cubemaps\\aura_e.dds"
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\effects\\aura_m.dds"
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_ENVMAP, texture_paths=paths)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["shader_state.envmap_glow_missing_slot2.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(str(opts.glow_texture_path).lower().endswith("_g.dds"))
        self.assertIn("set_slot2_glow_for_envmap_glow_slot2_only", steps)
        self.assertIn("enable_glow_map_for_envmap_glow_slot2_only", steps)
        self.assertNotIn("disable_glow_map_for_envmap_glow_slot2_only", steps)

    def test_auto_remediation_build_options_disables_glow_for_envmap_glow_slot2_only_without_guess(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_CUBEMAP] = "textures\\cubemaps\\aura_e.dds"
        paths[TEXTURE_SLOT_ENV_MASK] = "textures\\effects\\aura_m.dds"
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_ENVMAP, texture_paths=paths)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["shader_state.envmap_glow_missing_slot2.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(opts.disable_glow_map)
        self.assertIn("disable_glow_map_for_envmap_glow_slot2_only", steps)

    def test_auto_remediation_build_options_disables_env_mapping_parallax_and_pom_for_mixed_parallax_envmap_conflict(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_ENVMAP)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["shader_state.parallax_envmap_missing_slots3_4_5.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(opts.disable_env_mapping)
        self.assertTrue(opts.disable_parallax)
        self.assertTrue(opts.disable_pom)
        self.assertIn("disable_env_mapping_for_parallax_envmap_mixed_unresolved", steps)
        self.assertIn("disable_parallax_for_parallax_envmap_mixed_unresolved", steps)
        self.assertIn("disable_pom_for_parallax_envmap_mixed_unresolved", steps)

    def test_auto_remediation_build_options_restores_paths_for_mixed_parallax_envmap_glow_conflict(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_ENVMAP, texture_paths=paths)
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["shader_state.parallax_envmap_glow_missing_slots2_3_4_5.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(str(opts.parallax_texture_path).lower().endswith("_p.dds"))
        self.assertTrue(str(opts.glow_texture_path).lower().endswith("_g.dds"))
        self.assertTrue(str(opts.cubemap_texture_path).lower().endswith("_e.dds"))
        self.assertTrue(str(opts.env_mask_texture_path).lower().endswith("_m.dds"))
        self.assertTrue(opts.enable_env_mapping)
        self.assertIn("set_slot3_parallax_for_parallax_envmap_glow_mixed_unresolved", steps)
        self.assertIn("set_slot2_glow_for_parallax_envmap_glow_mixed_unresolved", steps)
        self.assertIn("set_slot4_cubemap_for_parallax_envmap_glow_mixed_unresolved", steps)
        self.assertIn("set_slot5_env_mask_for_parallax_envmap_glow_mixed_unresolved", steps)
        self.assertIn("enable_env_mapping_for_parallax_envmap_glow_mixed_unresolved", steps)

    def test_auto_remediation_build_options_disables_flags_for_mixed_parallax_envmap_glow_without_guesses(self) -> None:
        nif = _write_nif(
            self.tmp,
            shader_type=SHADER_TYPE_ENVMAP,
            texture_paths=[""] * 9,
        )
        opts, steps = build_auto_remediation_patch_options(
            nif,
            ["shader_state.parallax_envmap_glow_missing_slots2_3_4_5.skyrim.legacy"],
            backup=False,
        )
        self.assertIsNotNone(opts)
        assert opts is not None
        self.assertTrue(opts.disable_env_mapping)
        self.assertTrue(opts.disable_parallax)
        self.assertTrue(opts.disable_pom)
        self.assertTrue(opts.disable_glow_map)
        self.assertIn("disable_env_mapping_for_parallax_envmap_glow_mixed_unresolved", steps)
        self.assertIn("disable_parallax_for_parallax_envmap_glow_mixed_unresolved", steps)
        self.assertIn("disable_pom_for_parallax_envmap_glow_mixed_unresolved", steps)
        self.assertIn("disable_glow_map_for_parallax_envmap_glow_mixed_unresolved", steps)


class TestCompatibilityReport(unittest.TestCase):
    def test_game_profile_support_matrix_has_expected_profiles(self) -> None:
        matrix = build_game_profile_support_matrix()
        profiles = {row[0]: row for row in matrix}
        self.assertIn("skyrim", profiles)
        self.assertIn("fallout", profiles)
        self.assertIn("unknown", profiles)
        self.assertEqual(profiles["fallout"][1], "guarded")

    def test_compatibility_report_mentions_fallout_safety_gate_flags(self) -> None:
        report = build_compatibility_report_text()
        self.assertIn("NIF patch compatibility report", report)
        self.assertIn("Fallout signature range", report)
        self.assertIn("--fallout-allow-parallax-scale", report)
        self.assertIn("--fallout-allow-env-map-scale", report)

    def test_parity_delta_report_formats_markdown_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            validations = [
                validate_nif_for_parallax(_write_nif(tmp)),
                validate_nif_for_parallax(_write_nif(tmp, shader_type=SHADER_TYPE_ENVMAP)),
            ]
        summary = summarize_validation_conflicts(validations)
        report = build_parity_delta_report_text(summary, max_rows=5)
        self.assertIn("NIF parity delta report", report)
        self.assertIn("| Conflict code | Count | Files | Auto-remediation | Suggested action |", report)
        self.assertIn("`missing_parallax_flag.flag1_not_set.", report)

    def test_parity_delta_report_handles_empty_summary(self) -> None:
        report = build_parity_delta_report_text([])
        self.assertIn("No conflicts detected", report)


class TestFixtureCorpusCompatibilityMatrix(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_profile_layout_corpus_matrix_and_broken_headers(self) -> None:
        corpus: list[Path] = []
        for profile in ("skyrim", "fallout"):
            for layout in ("legacy", "real"):
                for idx in range(5):
                    p = self.tmp / f"{profile}_{layout}_{idx}.nif"
                    p.write_bytes(
                        _build_minimal_nif(
                            shader_layout=layout,
                            user_ver2=130 if layout == "real" else 83,
                            texture_paths=["textures\\arch\\stone.dds"] + [""] * 8,
                        )
                    )
                    if profile == "fallout":
                        _rewrite_user_version(p, 11)
                    corpus.append(p)
        broken = self.tmp / "broken_header.nif"
        broken.write_bytes((_build_minimal_nif())[:90])
        corpus.append(broken)

        validations = [validate_nif_for_parallax(p) for p in corpus]
        self.assertEqual(len(validations), 21)
        self.assertTrue(any(v.detected_game_profile == "skyrim" for v in validations))
        self.assertTrue(any(v.detected_game_profile == "fallout" for v in validations))
        self.assertTrue(any("unsupported nif header/profile values" in "\n".join(v.issues).lower() for v in validations))
        summary = summarize_validation_conflicts(validations)
        self.assertTrue(any(group.code.startswith("unsupported_header.") for group in summary))
        self.assertTrue(any(".skyrim.legacy" in group.code for group in summary))
        self.assertTrue(any(".fallout.real" in group.code for group in summary))


class TestFixtureCorpusBaselinePack(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_fixture_manifest_and_baseline_are_consistent(self) -> None:
        payload = _load_fixture_corpus_payload(_FIXTURE_CORPUS_MANIFEST)
        baseline = _load_fixture_corpus_payload(_FIXTURE_CORPUS_BASELINE)
        cases = payload.get("cases", [])
        self.assertIsInstance(cases, list)
        case_ids = [str(case.get("id", "")).strip() for case in cases if isinstance(case, dict)]
        self.assertEqual(len(case_ids), len(set(case_ids)), "Fixture case ids must be unique.")
        self.assertEqual(len(case_ids), int(baseline.get("expected_total_cases", 0)))

    def test_fixture_pack_matches_baseline_conflict_matrix(self) -> None:
        payload = _load_fixture_corpus_payload(_FIXTURE_CORPUS_MANIFEST)
        baseline = _load_fixture_corpus_payload(_FIXTURE_CORPUS_BASELINE)
        corpus = _materialize_fixture_corpus(self.tmp, payload)
        validations = [validate_nif_for_parallax(path) for path in corpus]
        self.assertEqual(len(validations), int(baseline.get("expected_total_cases", 0)))

        expected_profile_counts = baseline.get("expected_profile_counts", {})
        self.assertIsInstance(expected_profile_counts, dict)
        observed_profile_counts: dict[str, int] = {}
        for validation in validations:
            key = validation.detected_game_profile or "unknown"
            observed_profile_counts[key] = observed_profile_counts.get(key, 0) + 1
        self.assertEqual(observed_profile_counts, {str(k): int(v) for k, v in expected_profile_counts.items()})
        self.assertTrue(
            any("unexpected user version values" in "\n".join(v.issues).lower() for v in validations),
            "Fixture corpus should include at least one unknown-header signature case.",
        )

        summary = summarize_validation_conflicts(validations)
        summary_codes = tuple(group.code for group in summary)
        required_prefixes = baseline.get("required_summary_code_prefixes", [])
        self.assertIsInstance(required_prefixes, list)
        for prefix in required_prefixes:
            self.assertTrue(
                any(code.startswith(str(prefix)) for code in summary_codes),
                f"Missing required summary code prefix: {prefix!r}",
            )


class TestFixturePostMutations(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_materialize_supports_cross_block_texture_ref_mismatch(self) -> None:
        payload = {
            "cases": [
                {
                    "id": "cross_block_ref",
                    "profile": "fallout",
                    "shader_layout": "real",
                    "user_version": 11,
                    "user_ver2": 131,
                    "shader_type": SHADER_TYPE_ENVMAP,
                    "flags1": SLSF1_ENVIRONMENT_MAPPING,
                    "texture_paths": ["textures\\arch\\stone.dds"] + [""] * 8,
                    "extra_shader_blocks": [
                        {
                            "shader_type": SHADER_TYPE_DEFAULT,
                            "texture_paths": [
                                "textures\\arch\\stone.dds",
                                "textures\\arch\\stone_n.dds",
                                "",
                                "textures\\arch\\stone_n.dds",
                                "",
                                "",
                                "",
                                "",
                                "",
                            ],
                        }
                    ],
                    "extra_shader_texture_set_refs": [0],
                }
            ]
        }
        corpus = _materialize_fixture_corpus(self.tmp, payload)
        self.assertEqual(len(corpus), 1)
        validation = validate_nif_for_parallax(corpus[0])
        codes = [group.code for group in validation.conflict_report]
        self.assertTrue(any(code.startswith("path_slot_parallax.matches_normal.") for code in codes))

    def test_materialize_supports_header_num_block_corruption_delta(self) -> None:
        payload = {
            "cases": [
                {
                    "id": "header_blocks_delta",
                    "profile": "fallout",
                    "shader_layout": "real",
                    "user_version": 11,
                    "user_ver2": 131,
                    "num_blocks_delta": 2,
                }
            ]
        }
        corpus = _materialize_fixture_corpus(self.tmp, payload)
        self.assertEqual(len(corpus), 1)
        validation = validate_nif_for_parallax(corpus[0])
        self.assertFalse(validation.valid)
        self.assertTrue(any(group.code.startswith("unsupported_header.") for group in validation.conflict_report))


# ---------------------------------------------------------------------------
# Tests: multiple shader blocks (extra_shader_blocks builder feature)
# ---------------------------------------------------------------------------

class TestMultipleShaderBlocks(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_builder_creates_multiple_shader_blocks(self) -> None:
        nif_data = _build_minimal_nif(
            extra_shader_blocks=[
                {"shader_type": SHADER_TYPE_DEFAULT, "flags1": 0},
                {"shader_type": SHADER_TYPE_HEIGHTMAP, "parallax_scale": 2.0, "flags1": SLSF1_PARALLAX},
            ]
        )
        p = self.tmp / "multi.nif"
        p.write_bytes(nif_data)
        infos = scan_nif(p)
        self.assertEqual(len(infos), 3)

    def test_patch_affects_all_shader_blocks(self) -> None:
        nif_data = _build_minimal_nif(
            extra_shader_blocks=[{"shader_type": SHADER_TYPE_DEFAULT, "flags1": 0}]
        )
        p = self.tmp / "multi.nif"
        p.write_bytes(nif_data)
        result = patch_nif(p, NifPatchOptions(enable_parallax=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(p)
        self.assertEqual(len(infos), 2)
        for info in infos:
            self.assertTrue(info.has_parallax_flag)


# ---------------------------------------------------------------------------
# Tests: validate glow map consistency
# ---------------------------------------------------------------------------

class TestValidateGlowMap(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_reports_glow_slot_without_glow_flag(self) -> None:
        paths = [""] * 9
        paths[TEXTURE_SLOT_GLOW] = "textures\\arch\\stone_g.dds"
        nif = _write_nif(self.tmp, texture_paths=paths, flags2=0)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("slot 2", joined)
        self.assertIn("glow_map", joined)

    def test_reports_glow_flag_without_glow_slot(self) -> None:
        nif = _write_nif(self.tmp, flags2=SLSF2_GLOW_MAP)
        v = validate_nif_for_parallax(nif)
        joined = "\n".join(v.issues + v.suggestions).lower()
        self.assertIn("glow_map", joined)
        self.assertIn("slot 2", joined)


# ---------------------------------------------------------------------------
# Tests: multi-block type-0 upgrade correctness
# ---------------------------------------------------------------------------

class TestMultiBlockType0Upgrade(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_all_type0_blocks_upgraded_to_type3(self) -> None:
        """All type-0 BSLightingShaderProperty blocks must be upgraded when
        force_shader_type_3=True, not just the first one."""
        nif_data = _build_minimal_nif(
            shader_type=SHADER_TYPE_DEFAULT,
            extra_shader_blocks=[{"shader_type": SHADER_TYPE_DEFAULT, "flags1": 0}],
        )
        p = self.tmp / "multi_upgrade.nif"
        p.write_bytes(nif_data)
        result = patch_nif(
            p,
            NifPatchOptions(
                enable_parallax=True,
                parallax_scale=2.5,
                force_shader_type_3=True,
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        self.assertEqual(result.blocks_upgraded_to_type3, 2)
        infos = scan_nif(p)
        self.assertEqual(len(infos), 2)
        for info in infos:
            self.assertEqual(info.shader_type, SHADER_TYPE_HEIGHTMAP,
                             f"Block {info.block_index} still has shader_type={info.shader_type}")
            self.assertAlmostEqual(info.parallax_scale or 0.0, 2.5, places=2)

    def test_nif_parseable_after_multi_block_upgrade(self) -> None:
        """The NIF must remain structurally valid after upgrading multiple blocks."""
        nif_data = _build_minimal_nif(
            shader_type=SHADER_TYPE_DEFAULT,
            extra_shader_blocks=[
                {"shader_type": SHADER_TYPE_DEFAULT, "flags1": 0},
                {"shader_type": SHADER_TYPE_DEFAULT, "flags1": 0},
            ],
        )
        p = self.tmp / "triple_upgrade.nif"
        p.write_bytes(nif_data)
        result = patch_nif(
            p,
            NifPatchOptions(
                enable_parallax=True,
                parallax_scale=1.5,
                force_shader_type_3=True,
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        self.assertEqual(result.blocks_upgraded_to_type3, 3)
        infos = scan_nif(p)
        self.assertEqual(len(infos), 3)
        for info in infos:
            self.assertEqual(info.shader_type, SHADER_TYPE_HEIGHTMAP)

    def test_mixed_types_only_upgrades_type0(self) -> None:
        """Only type-0 blocks should be upgraded; existing type-3 blocks stay untouched."""
        nif_data = _build_minimal_nif(
            shader_type=SHADER_TYPE_DEFAULT,
            extra_shader_blocks=[
                {"shader_type": SHADER_TYPE_HEIGHTMAP, "parallax_scale": 1.0,
                 "flags1": SLSF1_PARALLAX},
            ],
        )
        p = self.tmp / "mixed_upgrade.nif"
        p.write_bytes(nif_data)
        result = patch_nif(
            p,
            NifPatchOptions(
                enable_parallax=True,
                parallax_scale=3.0,
                force_shader_type_3=True,
                backup=False,
            ),
        )
        self.assertTrue(result.success, result.errors)
        self.assertEqual(result.blocks_upgraded_to_type3, 1)
        infos = scan_nif(p)
        self.assertEqual(len(infos), 2)
        for info in infos:
            self.assertEqual(info.shader_type, SHADER_TYPE_HEIGHTMAP)


# ---------------------------------------------------------------------------
# Tests: parallax_scale-only patch (has_any_toggle fix)
# ---------------------------------------------------------------------------

class TestParallaxScaleOnly(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_scale_only_updates_existing_type3_block(self) -> None:
        """Setting parallax_scale alone (no enable_parallax) must update the
        scale on an already-type-3 block instead of returning 'Nothing to patch'."""
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_HEIGHTMAP, parallax_scale=1.0)
        result = patch_nif(nif, NifPatchOptions(parallax_scale=4.0, backup=False))
        self.assertTrue(result.success, result.errors)
        self.assertFalse(result.already_up_to_date)
        infos = scan_nif(nif)
        self.assertAlmostEqual(infos[0].parallax_scale or 0.0, 4.0, places=2)

    def test_scale_only_no_patch_when_already_matching(self) -> None:
        """No write should occur when the existing scale already matches the requested value."""
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_HEIGHTMAP, parallax_scale=2.5)
        result = patch_nif(nif, NifPatchOptions(parallax_scale=2.5, backup=False))
        self.assertTrue(result.success, result.errors)
        self.assertTrue(result.already_up_to_date)


# ---------------------------------------------------------------------------
# Tests: backup overwrite protection
# ---------------------------------------------------------------------------

class TestBackupOverwriteProtection(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_backup_created_when_none_exists(self) -> None:
        """A .nif.bak file is created on the first patch when no backup exists."""
        nif = _write_nif(self.tmp)
        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=True))
        self.assertTrue(result.success, result.errors)
        self.assertIsNotNone(result.backup_path)
        self.assertTrue(result.backup_path.exists())  # type: ignore[union-attr]
        self.assertEqual(result.warnings, [])

    def test_existing_backup_not_overwritten(self) -> None:
        """When a .nif.bak already exists the patch succeeds but skips the backup
        and adds a warning instead of silently overwriting the original backup."""
        nif = _write_nif(self.tmp)
        backup_path = nif.with_suffix(".nif.bak")
        sentinel = b"original backup sentinel"
        backup_path.write_bytes(sentinel)

        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=True))
        self.assertTrue(result.success, result.errors)
        self.assertIsNone(result.backup_path)
        self.assertEqual(backup_path.read_bytes(), sentinel,
                         "Existing backup must not be overwritten")
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("already exists", result.warnings[0])

    def test_no_warning_when_backup_disabled(self) -> None:
        """With backup=False no warning is emitted even if a .nif.bak exists."""
        nif = _write_nif(self.tmp)
        backup_path = nif.with_suffix(".nif.bak")
        backup_path.write_bytes(b"old backup")
        result = patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        self.assertTrue(result.success, result.errors)
        self.assertEqual(result.warnings, [])

    def test_patch_still_writes_nif_when_backup_skipped(self) -> None:
        """Skipping the backup must not prevent the NIF itself from being patched."""
        nif = _write_nif(self.tmp)
        nif.with_suffix(".nif.bak").write_bytes(b"old backup")
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=True))
        infos = scan_nif(nif)
        self.assertTrue(infos[0].has_parallax_flag)

    def test_result_has_warnings_field(self) -> None:
        """NifPatchResult must expose a 'warnings' list."""
        result = NifPatchResult(nif_path=Path("x.nif"), success=True)
        self.assertIsInstance(result.warnings, list)


# ---------------------------------------------------------------------------
# Tests for flag management and safety skip conditions
# ---------------------------------------------------------------------------

# Import additional constants needed for skip-condition tests
from nif_patcher import (
    SLSF1_DECAL,
    SLSF1_DYNAMIC_DECAL,
    SLSF2_SOFT_LIGHTING,
    SLSF2_RIM_LIGHTING,
    SLSF2_BACK_LIGHTING,
    SLSF2_ANISOTROPIC_LIGHTING,
    SLSF2_MULTI_LAYER_PARALLAX,
    SLSF2_UNUSED01,
    SHADER_TYPE_MULTILAYER,
    _ShapeBlock,
    _SHAPE_FIXED_PRE_REFS,
)


def _build_shape_block_body(
    *,
    shader_ref: int = 1,
    skin_ref: int = -1,
    alpha_ref: int = -1,
) -> bytes:
    """Build a minimal BSTriShape block body for skip-condition tests.

    Layout matches _parse_shape_block expectations:
      NiObjectNET : name_ref(4) + num_extra(4) + controller(4) = 12 bytes
      NiAVObject  : flags(2) + transform(52) + collision_ref(4) = 58 bytes
      BoundingSphere: 16 bytes
      → skin_instance_ref   (i32)
      → shader_property_ref (i32)
      → alpha_property_ref  (i32)
    Total header prefix = 86 bytes + refs (12 bytes) = 98 bytes.
    """
    niobjectnet = struct.pack("<IIi", 0, 0, -1)         # name, num_extra, controller
    niavobj = struct.pack("<H", 0)                       # flags u16
    niavobj += struct.pack("<" + "f" * 13, *([0.0] * 13))  # transform (52 bytes)
    niavobj += struct.pack("<i", -1)                     # collision_ref
    bsphere = struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0)  # BoundingSphere (16 bytes)
    refs = struct.pack("<iii", skin_ref, shader_ref, alpha_ref)
    return niobjectnet + niavobj + bsphere + refs


def _build_nif_with_shapes(
    *,
    shader_flags1: int = 0,
    shader_flags2: int = 0,
    skin_ref: int = -1,
    alpha_ref: int = -1,
    shader_type: int = SHADER_TYPE_DEFAULT,
    add_havok: bool = False,
) -> bytes:
    """Build a minimal NIF that includes a BSTriShape pointing at a shader.

    Block layout:
      0 – BSShaderTextureSet
      1 – BSLightingShaderProperty  (shader_property_ref from BSTriShape)
      2 – BSTriShape                (shader_ref=1, skin_ref, alpha_ref)
      [3 – BSBehaviorGraphExtraData (if add_havok=True)]
    """
    ts_body = _build_texture_set_block()
    sp_body = _build_shader_block(
        shader_type=shader_type,
        flags1=shader_flags1,
        flags2=shader_flags2,
        texture_set_ref=0,
    )
    num_blocks = 4 if add_havok else 3
    shape_body = _build_shape_block_body(shader_ref=1, skin_ref=skin_ref, alpha_ref=alpha_ref)

    block_type_names = [
        "BSShaderTextureSet",
        "BSLightingShaderProperty",
        "BSTriShape",
    ]
    all_blocks: list[tuple[int, bytes]] = [
        (0, ts_body),
        (1, sp_body),
        (2, shape_body),
    ]
    if add_havok:
        # Minimal BSBehaviorGraphExtraData body — content doesn't matter for detection
        havok_body = struct.pack("<IIiI", 0, 0, -1, 0)  # name, num_extra, ctrl, filename_ref
        block_type_names.append("BSBehaviorGraphExtraData")
        all_blocks.append((3, havok_body))

    type_indices_bytes = b"".join(struct.pack("<H", ti) for ti, _ in all_blocks)
    block_sizes_bytes = b"".join(struct.pack("<I", len(body)) for _, body in all_blocks)

    header_str = b"Gamebryo File Format, Version 20.2.0.7\n"
    version    = struct.pack("<I", 0x14020007)
    endian     = struct.pack("B", 1)
    user_ver   = struct.pack("<I", 12)
    num_blocks_bytes = struct.pack("<I", len(all_blocks))
    user_ver2  = struct.pack("<I", 83)
    export     = _sstring_u8("") + _sstring_u8("") + _sstring_u8("")
    num_btype  = struct.pack("<H", len(block_type_names))
    btypes     = b"".join(_sstring_u32(t) for t in block_type_names)
    string_table = struct.pack("<II", 0, 0)
    num_groups = struct.pack("<I", 0)

    header = (
        header_str + version + endian + user_ver + num_blocks_bytes
        + user_ver2 + export + num_btype + btypes
        + type_indices_bytes + block_sizes_bytes + string_table + num_groups
    )
    return header + b"".join(body for _, body in all_blocks)


class TestFlagManagement(unittest.TestCase):
    """Verify that enabling parallax or env mapping correctly manages flags."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_enabling_parallax_sets_vertex_colors(self) -> None:
        """SLSF2_VERTEX_COLORS must be set alongside SLSF1_PARALLAX."""
        nif = _write_nif(self.tmp)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertTrue(info.flags2 & SLSF2_VERTEX_COLORS, "SLSF2_VERTEX_COLORS not set")

    def test_enabling_parallax_preserves_existing_env_mapping_flag(self) -> None:
        """Parallax patching should not strip environment mapping from mixed workflows."""
        nif = _write_nif(self.tmp, flags1=SLSF1_ENVIRONMENT_MAPPING)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertTrue(info.flags1 & SLSF1_ENVIRONMENT_MAPPING,
                        "SLSF1_ENVIRONMENT_MAPPING should remain set alongside parallax")

    def test_enabling_parallax_clears_multi_layer_flag(self) -> None:
        """SLSF2_MULTI_LAYER_PARALLAX must be cleared when enabling parallax."""
        nif = _write_nif(self.tmp, flags2=SLSF2_MULTI_LAYER_PARALLAX)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertFalse(info.flags2 & SLSF2_MULTI_LAYER_PARALLAX,
                         "SLSF2_MULTI_LAYER_PARALLAX should be cleared by parallax")

    def test_enabling_parallax_preserves_existing_pbr_flag(self) -> None:
        """Parallax patching should not strip the TruePBR flag from mixed workflows."""
        nif = _write_nif(self.tmp, flags2=SLSF2_UNUSED01)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertTrue(info.flags2 & SLSF2_UNUSED01,
                        "SLSF2_UNUSED01 (PBR) should remain set alongside parallax")

    def test_enabling_env_mapping_preserves_parallax_flag(self) -> None:
        """Environment mapping patching should not strip parallax from mixed workflows."""
        nif = _write_nif(self.tmp, flags1=SLSF1_PARALLAX)
        patch_nif(nif, NifPatchOptions(enable_env_mapping=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertTrue(info.flags1 & SLSF1_PARALLAX,
                        "SLSF1_PARALLAX should remain set alongside environment mapping")

    def test_enabling_env_mapping_preserves_pom_flag(self) -> None:
        """Environment mapping patching should not strip ENB POM from mixed workflows."""
        nif = _write_nif(self.tmp, flags1=SLSF1_PARALLAX | SLSF1_PARALLAX_OCCLUSION)
        patch_nif(nif, NifPatchOptions(enable_env_mapping=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertTrue(info.flags1 & SLSF1_PARALLAX_OCCLUSION,
                        "SLSF1_PARALLAX_OCCLUSION should remain set alongside environment mapping")

    def test_enabling_env_mapping_clears_multi_layer_flag(self) -> None:
        """SLSF2_MULTI_LAYER_PARALLAX must be cleared when enabling env mapping."""
        nif = _write_nif(self.tmp, flags2=SLSF2_MULTI_LAYER_PARALLAX)
        patch_nif(nif, NifPatchOptions(enable_env_mapping=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertFalse(info.flags2 & SLSF2_MULTI_LAYER_PARALLAX,
                         "SLSF2_MULTI_LAYER_PARALLAX should be cleared by env mapping")

    def test_enabling_pbr_sets_truepbr_flag(self) -> None:
        nif = _write_nif(self.tmp)
        patch_nif(nif, NifPatchOptions(enable_pbr=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertTrue(info.flags2 & SLSF2_UNUSED01, "SLSF2_UNUSED01 (PBR) should be set")


class TestSkipConditions(unittest.TestCase):
    """Verify that unsafe shapes are skipped when enabling parallax."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, **kwargs: object) -> Path:
        p = self.tmp / "test.nif"
        p.write_bytes(_build_nif_with_shapes(**kwargs))  # type: ignore[arg-type]
        return p

    def test_skip_decal_flag(self) -> None:
        """Shaders with SLSF1_DECAL must not receive parallax."""
        nif = self._write(shader_flags1=SLSF1_DECAL)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertFalse(info.has_parallax_flag, "Decal shader must not get parallax")

    def test_skip_dynamic_decal_flag(self) -> None:
        """Shaders with SLSF1_DYNAMIC_DECAL must not receive parallax."""
        nif = self._write(shader_flags1=SLSF1_DYNAMIC_DECAL)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertFalse(info.has_parallax_flag, "Dynamic-decal shader must not get parallax")

    def test_skip_soft_lighting(self) -> None:
        """Shaders with SLSF2_SOFT_LIGHTING must not receive parallax."""
        nif = self._write(shader_flags2=SLSF2_SOFT_LIGHTING)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertFalse(info.has_parallax_flag, "Soft-lit shader must not get parallax")

    def test_skip_rim_lighting(self) -> None:
        """Shaders with SLSF2_RIM_LIGHTING must not receive parallax."""
        nif = self._write(shader_flags2=SLSF2_RIM_LIGHTING)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertFalse(info.has_parallax_flag, "Rim-lit shader must not get parallax")

    def test_skip_back_lighting(self) -> None:
        """Shaders with SLSF2_BACK_LIGHTING must not receive parallax."""
        nif = self._write(shader_flags2=SLSF2_BACK_LIGHTING)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertFalse(info.has_parallax_flag, "Back-lit shader must not get parallax")

    def test_skip_anisotropic_lighting(self) -> None:
        """Shaders with SLSF2_ANISOTROPIC_LIGHTING must not receive parallax."""
        nif = self._write(shader_flags2=SLSF2_ANISOTROPIC_LIGHTING)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertFalse(info.has_parallax_flag, "Anisotropic-lit shader must not get parallax")

    def test_skip_single_pass_flag(self) -> None:
        """Shaders with SLSF1_SINGLE_PASS must not receive parallax."""
        nif = self._write(shader_flags1=SLSF1_SINGLE_PASS)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertFalse(info.has_parallax_flag, "Single-pass shader must not get parallax")

    def test_skip_incompatible_shader_type(self) -> None:
        """Shaders with types other than Default/Parallax/EnvMap must be skipped."""
        nif = self._write(shader_type=SHADER_TYPE_MULTILAYER)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertFalse(info.has_parallax_flag, "Multi-layer shader must not get parallax")

    def test_skip_if_havok(self) -> None:
        """NIFs with BSBehaviorGraphExtraData must not have parallax enabled."""
        nif = self._write(add_havok=True)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertFalse(info.has_parallax_flag, "Havok NIF must not get parallax")

    def test_skip_if_havok_blocks_pom_flag(self) -> None:
        nif = self._write(add_havok=True)
        patch_nif(nif, NifPatchOptions(enable_pom=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertFalse(info.has_parallax_flag, "Havok NIF must not get parallax when POM is requested")
        self.assertFalse(info.has_pom_flag, "Havok NIF must not get POM when parallax is unsafe")

    def test_skip_if_skinned(self) -> None:
        """Shapes with a skin instance must not receive parallax."""
        # skin_ref=0 is the BSShaderTextureSet block — just needs to be a valid index
        nif = self._write(skin_ref=0)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertFalse(info.has_parallax_flag, "Skinned shape must not get parallax")

    def test_skip_if_alpha(self) -> None:
        """Shapes with NiAlphaProperty must not receive parallax."""
        # alpha_ref=0 is the BSShaderTextureSet block — just needs to be a valid index
        nif = self._write(alpha_ref=0)
        patch_nif(nif, NifPatchOptions(enable_parallax=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertFalse(info.has_parallax_flag, "Alpha-property shape must not get parallax")

    def test_no_skip_when_flags_disabled(self) -> None:
        """With all skip options disabled, decal shaders still get parallax."""
        nif = self._write(shader_flags1=SLSF1_DECAL)
        patch_nif(nif, NifPatchOptions(
            enable_parallax=True,
            backup=False,
            skip_decal=False,
        ))
        info = scan_nif(nif)[0]
        self.assertTrue(info.has_parallax_flag,
                        "Decal shader should get parallax when skip_decal=False")

    def test_no_skip_single_pass_when_disabled(self) -> None:
        nif = self._write(shader_flags1=SLSF1_SINGLE_PASS)
        patch_nif(nif, NifPatchOptions(
            enable_parallax=True,
            backup=False,
            skip_single_pass=False,
        ))
        info = scan_nif(nif)[0]
        self.assertTrue(info.has_parallax_flag,
                        "Single-pass shader should get parallax when skip_single_pass=False")

    def test_havok_skip_does_not_affect_env_mapping(self) -> None:
        """Havok skip only blocks parallax; env-mapping patching must still work."""
        nif = self._write(add_havok=True)
        patch_nif(nif, NifPatchOptions(enable_env_mapping=True, backup=False))
        info = scan_nif(nif)[0]
        self.assertTrue(info.has_env_mapping_flag,
                        "Env mapping must not be blocked by Havok skip")


class TestShaderFieldPatches(unittest.TestCase):
    """Verify spec_strength, spec_color, env_map_scale, and fix_mesh_lighting."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # Helpers: read raw float from the patched NIF's shader block
    # ------------------------------------------------------------------

    def _read_sp_float(self, nif: Path, attr: str) -> float:
        """Read a float from the _ShaderPropBlock field named *attr*
        (e.g. 'spec_strength_offset').  Uses the already-computed absolute
        file offset stored in the parsed _ShaderPropBlock."""
        from nif_patcher import _Buf, _read_header, _build_block_map
        data = nif.read_bytes()
        header = _read_header(_Buf(data))
        assert header is not None
        props, _, _ = _build_block_map(data, header)
        assert props
        sp = props[0]
        offset = getattr(sp, attr)
        assert offset is not None, f"_ShaderPropBlock.{attr} is None"
        return struct.unpack_from("<f", data, offset)[0]

    def test_spec_strength_patch(self) -> None:
        """spec_strength option must write the correct float to the shader block."""
        nif = _write_nif(self.tmp)
        patch_nif(nif, NifPatchOptions(spec_strength=0.75, backup=False))
        value = self._read_sp_float(nif, "spec_strength_offset")
        self.assertAlmostEqual(value, 0.75, places=4)

    def test_spec_color_patch(self) -> None:
        """spec_color option must write all three RGB floats to the shader block."""
        nif = _write_nif(self.tmp)
        patch_nif(nif, NifPatchOptions(spec_color=(0.5, 0.25, 0.125), backup=False))
        from nif_patcher import _Buf, _read_header, _build_block_map
        data = nif.read_bytes()
        header = _read_header(_Buf(data))
        assert header is not None
        props, _, _ = _build_block_map(data, header)
        sp = props[0]
        r = struct.unpack_from("<f", data, sp.spec_color_offset)[0]
        g = struct.unpack_from("<f", data, sp.spec_color_offset + 4)[0]
        b = struct.unpack_from("<f", data, sp.spec_color_offset + 8)[0]
        self.assertAlmostEqual(r, 0.5, places=4)
        self.assertAlmostEqual(g, 0.25, places=4)
        self.assertAlmostEqual(b, 0.125, places=4)

    def test_fix_mesh_lighting_clamps_high_value(self) -> None:
        """fix_mesh_lighting must clamp light_eff1 > 0.6 down to 0.6."""
        from nif_patcher import _Buf, _read_header, _build_block_map
        # Build NIF, then manually overwrite light_eff1 to 2.0 (too high)
        nif = _write_nif(self.tmp)
        data = bytearray(nif.read_bytes())
        header = _read_header(_Buf(bytes(data)))
        assert header is not None
        props, _, _ = _build_block_map(bytes(data), header)
        sp = props[0]
        struct.pack_into("<f", data, sp.light_eff1_offset, 2.0)
        nif.write_bytes(bytes(data))

        patch_nif(nif, NifPatchOptions(fix_mesh_lighting=True, backup=False))
        value = self._read_sp_float(nif, "light_eff1_offset")
        self.assertAlmostEqual(value, 0.6, places=4)

    def test_fix_mesh_lighting_leaves_low_value_unchanged(self) -> None:
        """fix_mesh_lighting must not modify light_eff1 when it is already ≤ 0.6."""
        # Default shader has light_eff1=0.3 which is below 0.6
        nif = _write_nif(self.tmp)
        patch_nif(nif, NifPatchOptions(fix_mesh_lighting=True, backup=False))
        value = self._read_sp_float(nif, "light_eff1_offset")
        self.assertAlmostEqual(value, 0.3, places=4)

    def test_env_map_scale_patched_on_envmap_shader(self) -> None:
        """env_map_scale must be written when shader type is ENVMAP (1)."""
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_ENVMAP)
        patch_nif(nif, NifPatchOptions(env_map_scale=1.0, backup=False))
        from nif_patcher import _Buf, _read_header, _build_block_map
        data = nif.read_bytes()
        header = _read_header(_Buf(data))
        assert header is not None
        props, _, _ = _build_block_map(data, header)
        sp = props[0]
        self.assertIsNotNone(sp.env_map_scale_offset)
        value = struct.unpack_from("<f", data, sp.env_map_scale_offset)[0]
        self.assertAlmostEqual(value, 1.0, places=4)


class TestCliArgumentValidation(unittest.TestCase):
    def test_missing_nif_paths_fails_fast(self) -> None:
        with mock.patch("sys.argv", ["nif_patcher.py"]):
            with self.assertRaises(SystemExit) as ctx:
                nif_patcher_main()
        self.assertEqual(ctx.exception.code, 2)

    def test_auto_remediate_requires_validate_mode(self) -> None:
        with mock.patch("sys.argv", ["nif_patcher.py", "dummy.nif", "--auto-remediate"]):
            with self.assertRaises(SystemExit) as ctx:
                nif_patcher_main()
        self.assertEqual(ctx.exception.code, 1)

    def test_auto_remediate_codes_requires_at_least_one_prefix(self) -> None:
        with mock.patch(
            "sys.argv",
            ["nif_patcher.py", "dummy.nif", "--validate", "--auto-remediate-codes"],
        ):
            with self.assertRaises(SystemExit) as ctx:
                nif_patcher_main()
        self.assertEqual(ctx.exception.code, 1)

    def test_auto_remediate_codes_requires_auto_remediate_flag(self) -> None:
        with mock.patch(
            "sys.argv",
            ["nif_patcher.py", "dummy.nif", "--validate", "--auto-remediate-codes", "missing_parallax_flag"],
        ):
            with self.assertRaises(SystemExit) as ctx:
                nif_patcher_main()
        self.assertEqual(ctx.exception.code, 1)

    def test_missing_nif_paths_errors_before_unknown_shader_map_validation(self) -> None:
        with mock.patch("sys.argv", ["nif_patcher.py", "--unknown-shader-type-map", "bad"]):
            with self.assertRaises(SystemExit) as ctx:
                nif_patcher_main()
        self.assertEqual(ctx.exception.code, 2)

    def test_compatibility_report_ignores_unrelated_unknown_shader_map_validation(self) -> None:
        out = io.StringIO()
        with mock.patch("sys.argv", ["nif_patcher.py", "--compatibility-report", "--unknown-shader-type-map", "bad"]):
            with redirect_stdout(out):
                nif_patcher_main()
        self.assertIn("NIF patch compatibility report", out.getvalue())


if __name__ == "__main__":
    unittest.main()
