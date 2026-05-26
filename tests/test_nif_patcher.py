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
    SHADER_TYPE_DEFAULT,
    SHADER_TYPE_HEIGHTMAP,
    TEXTURE_SLOT_DIFFUSE,
    TEXTURE_SLOT_NORMAL,
    TEXTURE_SLOT_PARALLAX,
    NifPatchOptions,
    find_nif_files,
    guess_normal_path_for_nif,
    guess_parallax_path_for_nif,
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


def _build_minimal_nif(
    *,
    shader_type: int = SHADER_TYPE_DEFAULT,
    flags1: int = 0,
    flags2: int = 0,
    parallax_scale: float | None = None,
    texture_paths: list[str] | None = None,
    shader_block_type: str = "BSLightingShaderProperty",
) -> bytes:
    """Build a minimal but structurally valid Skyrim SE NIF in memory.

    Contains exactly two blocks:
      0 – BSShaderTextureSet   (9 texture slots)
      1 – BSLightingShaderProperty  (references block 0)
    """
    if texture_paths is None:
        texture_paths = [""] * 9

    # --- BSShaderTextureSet block ---
    ts_body = struct.pack("<I", 9)
    for path in texture_paths[:9]:
        ts_body += _sstring_u32(path)

    # --- BSLightingShaderProperty block ---
    # NiObjectNET: name_ref(0) + num_extra(0) + controller(-1)
    nio = struct.pack("<IIi", 0, 0, -1)
    # BSShaderProperty flags
    flags = struct.pack("<II", flags1, flags2)
    # shader_type
    stype = struct.pack("<I", shader_type)
    # uv_offset(2f) + uv_scale(2f)
    uv = struct.pack("<ffff", 0.0, 0.0, 1.0, 1.0)
    # texture_set_ref → block 0
    tsref = struct.pack("<i", 0)
    # emissive_color(3f) + emissive_mul(f)
    emit = struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0)
    # clamp_mode(u32) + alpha(f) + refraction(f) + glossiness(f)
    misc = struct.pack("<Ifff", 3, 1.0, 0.0, 80.0)
    # spec_color(3f) + spec_strength(f)
    spec = struct.pack("<ffff", 1.0, 1.0, 1.0, 1.0)
    # light_eff1(f) + light_eff2(f)
    light = struct.pack("<ff", 0.3, 2.0)

    sp_body = nio + flags + stype + uv + tsref + emit + misc + spec + light
    # type-3 parallax-specific fields
    if shader_type == SHADER_TYPE_HEIGHTMAP:
        scale = parallax_scale if parallax_scale is not None else 1.0
        sp_body += struct.pack("<ff", 4.0, scale)

    # --- Header ---
    header_str = b"Gamebryo File Format, Version 20.2.0.7\n"
    version = struct.pack("<I", 0x14020007)
    endian = struct.pack("B", 1)
    user_ver = struct.pack("<I", 12)
    num_blocks = struct.pack("<I", 2)
    user_ver2 = struct.pack("<I", 83)
    # 3 export strings (all empty)
    export = _sstring_u8("") + _sstring_u8("") + _sstring_u8("")
    # block types
    block_types_list = ["BSShaderTextureSet", shader_block_type]
    num_block_types = struct.pack("<H", 2)
    btypes = b"".join(_sstring_u32(t) for t in block_types_list)
    # type indices
    type_indices = struct.pack("<HH", 0, 1)  # block 0 = type 0, block 1 = type 1
    # block sizes
    block_sizes = struct.pack("<II", len(ts_body), len(sp_body))
    # string table (empty)
    string_table = struct.pack("<II", 0, 0)

    header = (
        header_str + version + endian + user_ver + num_blocks
        + user_ver2 + export + num_block_types + btypes
        + type_indices + block_sizes + string_table
    )
    return header + ts_body + sp_body


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

    def test_scan_detects_shader_type(self) -> None:
        nif = _write_nif(self.tmp, shader_type=SHADER_TYPE_HEIGHTMAP, parallax_scale=1.5)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].shader_type, SHADER_TYPE_HEIGHTMAP)
        self.assertAlmostEqual(infos[0].parallax_scale or 0.0, 1.5, places=3)

    def test_scan_reads_texture_paths(self) -> None:
        paths = ["textures\\arch\\stone.dds"] + [""] * 8
        nif = _write_nif(self.tmp, texture_paths=paths)
        infos = scan_nif(nif)
        self.assertEqual(infos[0].texture_paths.get(TEXTURE_SLOT_DIFFUSE), "textures\\arch\\stone.dds")

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


if __name__ == "__main__":
    unittest.main()
