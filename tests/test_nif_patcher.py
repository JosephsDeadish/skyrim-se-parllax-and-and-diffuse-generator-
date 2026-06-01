"""Tests for nif_patcher.py."""
from __future__ import annotations

import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from nif_patcher import (
    SLSF1_ENVIRONMENT_MAPPING,
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
    scan_nif_diagnostics,
    validate_nif_for_parallax,
    _Buf,
    _build_block_map,
    _renderer_compatibility,
    _read_header,
)


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
        ts_idx = 2 + len(extra_bodies) * 2
        ts_body = _build_texture_set_block()
        sp_body = shader_builder(texture_set_ref=ts_idx, **extra)
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

    def test_scan_accepts_user_version_2_155(self) -> None:
        nif = _write_nif(self.tmp, user_ver2=155)
        infos = scan_nif(nif)
        self.assertEqual(len(infos), 1)

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

    def test_enable_pom_sets_both_parallax_and_occlusion_flags(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(nif, NifPatchOptions(enable_pom=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertTrue(infos[0].has_parallax_flag)
        self.assertTrue(infos[0].has_pom_flag)

    def test_enable_env_mapping_sets_flag(self) -> None:
        nif = _write_nif(self.tmp)
        result = patch_nif(nif, NifPatchOptions(enable_env_mapping=True, backup=False))
        self.assertTrue(result.success, result.errors)
        infos = scan_nif(nif)
        self.assertTrue(infos[0].has_env_mapping_flag)

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
        # Type is still 0 — no scale field exists in the block
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


if __name__ == "__main__":
    unittest.main()
