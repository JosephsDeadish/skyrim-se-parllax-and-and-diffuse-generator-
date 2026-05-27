"""Tests for nif_patcher.py."""
from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from nif_patcher import (
    SLSF1_ENVIRONMENT_MAPPING,
    SLSF1_PARALLAX,
    SLSF1_PARALLAX_OCCLUSION,
    SLSF2_GLOW_MAP,
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
    guess_env_mask_path_for_nif,
    guess_glow_path_for_nif,
    guess_normal_path_for_nif,
    guess_parallax_path_for_nif,
    batch_patch_nif,
    patch_nif,
    scan_nif,
    validate_nif_for_parallax,
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
    texture_set_ref: int = 0,
) -> bytes:
    """Build a single BSLightingShaderProperty block body."""
    # NiObjectNET: name_ref(0) + num_extra(0) + controller(-1)
    nio = struct.pack("<IIi", 0, 0, -1)
    flags = struct.pack("<II", flags1, flags2)
    stype = struct.pack("<I", shader_type)
    uv = struct.pack("<ffff", 0.0, 0.0, 1.0, 1.0)
    tsref = struct.pack("<i", texture_set_ref)
    emit = struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0)
    misc = struct.pack("<Ifff", 3, 1.0, 0.0, 80.0)
    spec = struct.pack("<ffff", 1.0, 1.0, 1.0, 1.0)
    light = struct.pack("<ff", 0.3, 2.0)

    body = nio + flags + stype + uv + tsref + emit + misc + spec + light
    if shader_type == SHADER_TYPE_HEIGHTMAP:
        scale = parallax_scale if parallax_scale is not None else 1.0
        body += struct.pack("<ff", 4.0, scale)
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
    layout_pad = b"\x00\x00\x00\x00" if texture_set_layout_shift == 4 else b""
    if texture_set_count_u16:
        count_bytes = struct.pack("<H", 9)
    else:
        count_bytes = struct.pack("<I", 9)
    body = layout_pad + count_bytes
    for path in texture_paths[:9]:
        body += _sstring_u32(path)
    return body


def _build_minimal_nif(
    *,
    shader_type: int = SHADER_TYPE_DEFAULT,
    flags1: int = 0,
    flags2: int = 0,
    parallax_scale: float | None = None,
    texture_paths: list[str] | None = None,
    shader_block_type: str = "BSLightingShaderProperty",
    user_ver2: int = 83,
    header_line_ending: bytes = b"\n",
    texture_set_layout_shift: int = 0,
    texture_set_count_u16: bool = False,
    extra_shader_blocks: list[dict] | None = None,
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
    sp0_body = _build_shader_block(
        shader_type=shader_type,
        flags1=flags1,
        flags2=flags2,
        parallax_scale=parallax_scale,
        texture_set_ref=0,
    )

    # --- Extra shader blocks --------------------------------------------
    extra_bodies: list[tuple[bytes, bytes]] = []
    for extra in (extra_shader_blocks or []):
        ts_idx = 2 + len(extra_bodies) * 2
        ts_body = _build_texture_set_block()
        sp_body = _build_shader_block(texture_set_ref=ts_idx, **extra)
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
    user_ver2 = struct.pack("<I", user_ver2)
    export = _sstring_u8("") + _sstring_u8("") + _sstring_u8("")
    num_block_types = struct.pack("<H", len(block_type_names))
    btypes = b"".join(_sstring_u32(t) for t in block_type_names)
    string_table = struct.pack("<II", 0, 0)

    header = (
        header_str + version + endian + user_ver + num_blocks_bytes
        + user_ver2 + export + num_block_types + btypes
        + type_indices_bytes + block_sizes_bytes + string_table
    )
    return header + b"".join(body for _, body in all_blocks)


def _write_nif(tmp_dir: Path, **kwargs: object) -> Path:
    p = tmp_dir / "test.nif"
    p.write_bytes(_build_minimal_nif(**kwargs))
    return p


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
        from nif_patcher import scan_nif_diagnostics
        paths = ["textures\\things\\coin01_d.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths, texture_set_count_u16=True)
        _infos, diagnostics = scan_nif_diagnostics(nif)
        ts_parse_errors = [d for d in diagnostics if "failed to parse BSShaderTextureSet" in d]
        self.assertEqual(ts_parse_errors, [], msg=f"Unexpected parse errors: {ts_parse_errors}")

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
        nif = _write_nif(self.tmp, flags1=SLSF1_PARALLAX)
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

    def test_guess_glow_returns_none_for_no_diffuse(self) -> None:
        nif = _write_nif(self.tmp)
        self.assertIsNone(guess_glow_path_for_nif(nif))

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


if __name__ == "__main__":
    unittest.main()
