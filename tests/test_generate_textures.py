import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image, ImageStat

from generate_textures import (
    APP_VERSION,
    PATREON_URL,
    _create_panda_icon_image,
    _map_parallax_strength_to_nif_scale,
    _compute_tooltip_position,
    _format_nif_result_row_details,
    _normalize_nif_result_details,
    _preferred_dds_formats_for_output,
    _run_cli,
    _normalize_gui_state,
    _save_with_dds_fallback,
    _to_dds_compatible_image,
    analyze_image_content,
    auto_patch_related_nifs_for_texture,
    apply_recommendations_by_auto_flags,
    build_nif_patch_options_for_generated_outputs,
    build_render_profile_recommendation_message,
    build_complex_preview_image,
    build_complex_output_path,
    build_environment_mask_output_path,
    build_rmaos_output_path,
    classify_material_type,
    collect_source_textures,
    compute_wrapped_preview_index,
    ModManagerContext,
    describe_render_profile_default_outputs,
    describe_render_profile_files_to_create,
    detect_workflow_profile,
    detect_mod_manager_context,
    detect_render_profile_from_mod_manager_context,
    enforce_skyrim_output_profile,
    find_related_nif_files_for_texture,
    build_glow_output_path,
    build_normal_output_path,
    build_output_paths,
    generate_complex_material,
    generate_diffuse,
    generate_environment_mask,
    generate_environment_mask_for_workflow,
    generate_glow,
    generate_msn,
    generate_normal,
    generate_parallax,
    generate_parallax_occlusion,
    generate_preview_outputs,
    generate_specular,
    get_generation_warnings,
    get_nif_patch_option_warnings,
    get_preview_size_limits,
    identify_skyrim_texture_role,
    prepare_preview_source,
    parse_preview_jump_input,
    recommend_generation_settings,
    recommend_render_profile,
    resolve_nif_patch_defaults_for_render_profile,
    resolve_render_profile_guardrails,
    resolve_render_profile_mode_control_states,
    resolve_render_profile_mode_selection,
    resolve_render_profile_output_defaults,
    resolve_render_profile_options,
    build_render_profile_mode_controls_hint,
    render_profile_has_locked_controls,
    restore_nif_backups,
    resolve_env_mask_complex_workflow,
    load_gui_state,
    main,
    parse_args,
    run_batch_with_options,
    run_with_options,
    save_gui_state,
    should_apply_preview_recommendations,
    select_generation_context_source,
    get_output_folder_format_warnings,
    generate_ambient_occlusion,
    generate_roughness,
    analyze_material_type_from_image,
    _prefilter_for_mipmap_stability,
    generate_wetness_mask,
    generate_snow_mask,
    build_wetness_mask_output_path,
    build_snow_mask_output_path,
    build_ao_output_path,
    build_roughness_output_path,
    GENERATED_TEXTURE_SUFFIXES,
    recommend_output_resolution,
    write_rmaos_json_sidecar,
)


def _sample_image() -> Image.Image:
    return Image.new("RGB", (8, 8), color=(60, 100, 140))


def _flat_dark_image() -> Image.Image:
    return Image.new("RGB", (16, 16), color=(28, 28, 28))


def _detailed_bright_image() -> Image.Image:
    image = Image.new("RGB", (16, 16))
    pixels = image.load()
    for y in range(16):
        for x in range(16):
            v = 245 if (x + y) % 2 == 0 else 35
            pixels[x, y] = (v, v, v)
    return image


def _uniform_bright_image() -> Image.Image:
    return Image.new("RGB", (24, 24), color=(220, 220, 220))


def _vertical_gradient_image() -> Image.Image:
    image = Image.new("RGB", (24, 24))
    pixels = image.load()
    for y in range(24):
        value = int((y / 23) * 255)
        for x in range(24):
            pixels[x, y] = (value, value, value)
    return image


def _sparse_highlight_image() -> Image.Image:
    image = Image.new("RGB", (24, 24), color=(20, 20, 20))
    pixels = image.load()
    for y in range(0, 24, 6):
        for x in range(0, 24, 6):
            pixels[x, y] = (255, 255, 255)
    return image


def _high_saturation_image() -> Image.Image:
    return Image.new("RGB", (24, 24), color=(245, 40, 40))


def _detailed_low_saturation_image() -> Image.Image:
    image = Image.new("RGB", (24, 24))
    pixels = image.load()
    for y in range(24):
        for x in range(24):
            v = 230 if (x + y) % 2 == 0 else 18
            pixels[x, y] = (v, v, v)
    return image


def _large_high_detail_image() -> Image.Image:
    image = Image.new("RGB", (512, 512))
    pixels = image.load()
    for y in range(512):
        for x in range(512):
            value = ((x * 37) + (y * 91) + ((x * y) % 251)) % 256
            pixels[x, y] = (value, (value * 3) % 256, (255 - value))
    return image


def _normal_like_image() -> Image.Image:
    image = Image.new("RGB", (24, 24))
    pixels = image.load()
    for y in range(24):
        for x in range(24):
            r = 120 + ((x + y) % 12)
            g = 120 + ((x * 2 + y) % 12)
            b = 210 + ((x * y) % 25)
            pixels[x, y] = (r, g, b)
    return image


def _emboss_pattern_image() -> Image.Image:
    image = Image.new("RGB", (48, 48), color=(132, 132, 132))
    pixels = image.load()
    for y in range(8, 40):
        for x in range(8, 40):
            if x in (8, 16, 24, 32, 39) or y in (8, 16, 24, 32, 39):
                pixels[x, y] = (215, 215, 215)
            elif (x + y) % 7 == 0:
                pixels[x, y] = (95, 95, 95)
    return image


def _color_sign_image() -> Image.Image:
    image = Image.new("RGB", (48, 48), color=(255, 0, 0))
    pixels = image.load()
    for y in range(6, 42):
        for x in range(6, 42):
            if x in (6, 41) or y in (6, 41):
                pixels[x, y] = (0, 129, 0)
            elif 16 <= x <= 31 and 16 <= y <= 31:
                pixels[x, y] = (0, 129, 0)
    return image


def _shield_art_image() -> Image.Image:
    image = Image.new("RGB", (64, 64), color=(90, 40, 30))
    pixels = image.load()
    centre = 32
    for y in range(64):
        for x in range(64):
            dx = x - centre
            dy = y - centre
            distance_sq = (dx * dx) + (dy * dy)
            if distance_sq <= 26 * 26:
                pixels[x, y] = (140, 110, 58)
            if distance_sq <= 18 * 18 and abs(dx) <= 2:
                pixels[x, y] = (200, 28, 28)
            if distance_sq <= 18 * 18 and abs(dy) <= 2:
                pixels[x, y] = (230, 220, 175)
            if 9 * 9 <= distance_sq <= 11 * 11:
                pixels[x, y] = (30, 30, 30)
    return image


class GenerateTexturesTests(unittest.TestCase):
    def test_app_version_is_0_7(self) -> None:
        self.assertEqual(APP_VERSION, "0.7")

    def test_normalize_gui_state_turns_off_individual_auto_flags_when_master_off(self) -> None:
        normalized = _normalize_gui_state(
            {
                "auto_suggestions": False,
                "auto_normal": True,
                "auto_parallax": True,
                "auto_glow": True,
                "auto_environment_mask": True,
                "auto_complex": True,
                "auto_specular": True,
            }
        )
        self.assertFalse(bool(normalized["auto_suggestions"]))
        self.assertFalse(bool(normalized["auto_normal"]))
        self.assertFalse(bool(normalized["auto_parallax"]))
        self.assertFalse(bool(normalized["auto_glow"]))
        self.assertFalse(bool(normalized["auto_environment_mask"]))
        self.assertFalse(bool(normalized["auto_complex"]))
        self.assertFalse(bool(normalized["auto_specular"]))

    def test_normalize_gui_state_prevents_emboss_relief_conflict(self) -> None:
        normalized = _normalize_gui_state({"emboss_mode": True, "relief_mode": True})
        self.assertFalse(bool(normalized["emboss_mode"]))
        self.assertTrue(bool(normalized["relief_mode"]))

    def test_normalize_gui_state_clears_missing_input_path(self) -> None:
        normalized = _normalize_gui_state({"input_path": "/definitely/not/a/real/path.dds"})
        self.assertEqual(normalized["input_path"], "")

    def test_normalize_gui_state_disables_custom_output_without_output_path(self) -> None:
        normalized = _normalize_gui_state({"use_custom_output": True, "output_path": ""})
        self.assertFalse(bool(normalized["use_custom_output"]))

    def test_load_gui_state_defaults_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = load_gui_state(Path(temp_dir) / "missing-state.json")
        self.assertEqual(state["input_path"], "")
        self.assertEqual(state["output_path"], "")
        self.assertFalse(bool(state["dark_mode"]))
        self.assertTrue(bool(state["auto_suggestions"]))

    def test_save_gui_state_round_trips_expected_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = Path(temp_dir) / "in.dds"
            input_file.write_bytes(b"fake")
            state_file = Path(temp_dir) / "gui-state.json"
            save_gui_state(
                {
                    "input_path": str(input_file),
                    "output_path": "/tmp/out",
                    "use_custom_output": True,
                    "dark_mode": True,
                    "show_batch_preview": True,
                    "preview_size": "XL",
                    "complex_format": "cm",
                    "env_mask_mode": "complex",
                    "parallax_mode": "occlusion",
                    "render_profile": "community_shaders",
                    "emboss_mode": True,
                    "include_diffuse": False,
                    "include_normal": True,
                    "include_parallax": False,
                    "include_glow": True,
                    "include_environment_mask": True,
                    "include_complex": True,
                    "auto_suggestions": True,
                    "auto_normal": False,
                    "auto_parallax": True,
                    "auto_glow": False,
                    "auto_environment_mask": True,
                    "auto_complex": False,
                    "auto_specular": True,
                    "normal_strength": 3.5,
                    "parallax_strength": 2.25,
                    "glow_threshold": 140,
                    "environment_mask_strength": 2.4,
                    "complex_strength": 2.2,
                    "specular_strength": 2.3,
                },
                state_file,
            )
            loaded = load_gui_state(state_file)
        self.assertEqual(loaded["input_path"], str(input_file))
        self.assertEqual(loaded["output_path"], "/tmp/out")
        self.assertTrue(bool(loaded["use_custom_output"]))
        self.assertTrue(bool(loaded["dark_mode"]))
        self.assertTrue(bool(loaded["show_batch_preview"]))
        self.assertEqual(str(loaded["preview_size"]), "XL")
        self.assertEqual(str(loaded["complex_format"]), "cm")
        self.assertEqual(str(loaded["env_mask_mode"]), "complex")
        self.assertEqual(str(loaded["parallax_mode"]), "occlusion (ENB/POM)")
        self.assertEqual(str(loaded["render_profile"]), "community_shaders")
        self.assertTrue(bool(loaded["emboss_mode"]))
        self.assertFalse(bool(loaded["include_diffuse"]))
        self.assertTrue(bool(loaded["include_normal"]))
        self.assertFalse(bool(loaded["include_parallax"]))
        self.assertTrue(bool(loaded["include_glow"]))
        self.assertTrue(bool(loaded["include_environment_mask"]))
        self.assertTrue(bool(loaded["include_complex"]))
        self.assertTrue(bool(loaded["auto_suggestions"]))
        self.assertFalse(bool(loaded["auto_normal"]))
        self.assertTrue(bool(loaded["auto_parallax"]))
        self.assertFalse(bool(loaded["auto_glow"]))
        self.assertTrue(bool(loaded["auto_environment_mask"]))
        self.assertFalse(bool(loaded["auto_complex"]))
        self.assertTrue(bool(loaded["auto_specular"]))
        self.assertAlmostEqual(float(loaded["normal_strength"]), 3.5)
        self.assertAlmostEqual(float(loaded["parallax_strength"]), 2.25)
        self.assertEqual(int(loaded["glow_threshold"]), 140)
        self.assertAlmostEqual(float(loaded["environment_mask_strength"]), 2.4)
        self.assertAlmostEqual(float(loaded["complex_strength"]), 2.2)
        self.assertAlmostEqual(float(loaded["specular_strength"]), 2.3)

    def test_normalize_gui_state_clamps_slider_values_and_sanitizes_modes(self) -> None:
        normalized = _normalize_gui_state(
            {
                "preview_size": "NOPE",
                "complex_format": "bad",
                "env_mask_mode": "invalid",
                "parallax_mode": "occlusion",
                "render_profile": "mystery",
                "normal_strength": 500,
                "parallax_strength": -10,
                "glow_threshold": 999,
                "environment_mask_strength": 99,
                "complex_strength": -99,
                "specular_strength": 0,
            }
        )
        self.assertEqual(str(normalized["preview_size"]), "Medium")
        self.assertEqual(str(normalized["complex_format"]), "msn")
        self.assertEqual(str(normalized["env_mask_mode"]), "standard")
        self.assertEqual(str(normalized["parallax_mode"]), "occlusion (ENB/POM)")
        self.assertEqual(str(normalized["render_profile"]), "custom")
        self.assertAlmostEqual(float(normalized["normal_strength"]), 8.0)
        self.assertAlmostEqual(float(normalized["parallax_strength"]), 0.1)
        self.assertEqual(int(normalized["glow_threshold"]), 255)
        self.assertAlmostEqual(float(normalized["environment_mask_strength"]), 8.0)
        self.assertAlmostEqual(float(normalized["complex_strength"]), 0.1)
        self.assertAlmostEqual(float(normalized["specular_strength"]), 0.1)

    def test_normalize_gui_state_accepts_truepbr_alias(self) -> None:
        normalized = _normalize_gui_state({"render_profile": "true pbr"})
        self.assertEqual(str(normalized["render_profile"]), "truepbr")

    def test_normalize_gui_state_maps_experimental_alias_to_custom(self) -> None:
        normalized = _normalize_gui_state({"render_profile": "experimental"})
        self.assertEqual(str(normalized["render_profile"]), "custom")

    def test_normalize_gui_state_accepts_performance_and_vr_profiles(self) -> None:
        performance = _normalize_gui_state({"render_profile": "performance"})
        vr = _normalize_gui_state({"render_profile": "vr"})
        self.assertEqual(str(performance["render_profile"]), "performance")
        self.assertEqual(str(vr["render_profile"]), "vr")

    def test_normalize_gui_state_accepts_extended_render_profiles(self) -> None:
        terrain = _normalize_gui_state({"render_profile": "terrain"})
        architecture = _normalize_gui_state({"render_profile": "architecture"})
        characters = _normalize_gui_state({"render_profile": "characters"})
        self.assertEqual(str(terrain["render_profile"]), "terrain")
        self.assertEqual(str(architecture["render_profile"]), "architecture")
        self.assertEqual(str(characters["render_profile"]), "characters")

    def test_generate_diffuse_returns_rgb_same_size(self) -> None:
        diffuse = generate_diffuse(_sample_image())
        self.assertEqual(diffuse.mode, "RGB")
        self.assertEqual(diffuse.size, (8, 8))

    def test_generate_parallax_returns_l_same_size(self) -> None:
        parallax = generate_parallax(_sample_image())
        self.assertEqual(parallax.mode, "L")
        self.assertEqual(parallax.size, (8, 8))

    def test_generate_parallax_produces_non_flat_height_map(self) -> None:
        parallax = generate_parallax(_vertical_gradient_image(), strength=1.35)
        values = set(parallax.tobytes())
        self.assertGreater(len(values), 2)

    def test_generate_parallax_strength_slider_has_stronger_min_max_separation(self) -> None:
        source = _detailed_bright_image()
        low = generate_parallax(source, strength=0.2)
        high = generate_parallax(source, strength=6.0)
        low_range = low.getextrema()[1] - low.getextrema()[0]
        high_range = high.getextrema()[1] - high.getextrema()[0]
        self.assertGreater(high_range, low_range + 20)

    def test_generate_normal_returns_rgb_same_size(self) -> None:
        normal = generate_normal(_sample_image())
        self.assertEqual(normal.mode, "RGB")
        self.assertEqual(normal.size, (8, 8))

    def test_generate_normal_directx_green_channel_differs_from_opengl(self) -> None:
        gradient = _vertical_gradient_image()
        directx_normal = generate_normal(gradient, strength=1.0, directx=True)
        opengl_normal = generate_normal(gradient, strength=1.0, directx=False)
        _, directx_green, _ = directx_normal.split()
        _, opengl_green, _ = opengl_normal.split()
        sample_coord = (12, 12)
        self.assertNotEqual(directx_green.getpixel(sample_coord), opengl_green.getpixel(sample_coord))

    def test_generate_normal_defaults_to_directx_orientation(self) -> None:
        gradient = _vertical_gradient_image()
        default_normal = generate_normal(gradient, strength=1.0)
        directx_normal = generate_normal(gradient, strength=1.0, directx=True)
        self.assertEqual(default_normal.tobytes(), directx_normal.tobytes())

    def test_generate_normal_blue_channel_stays_in_skyrim_safe_range(self) -> None:
        gradient = _vertical_gradient_image()
        normal = generate_normal(gradient, strength=2.0)
        _, _, blue = normal.split()
        minimum_blue, _ = blue.getextrema()
        self.assertGreaterEqual(minimum_blue, 128)

    def test_enforce_skyrim_output_profile_normal_lifts_blue_floor(self) -> None:
        bad_normal = Image.new("RGB", (8, 8), color=(120, 120, 40))
        conformed = enforce_skyrim_output_profile("normal", bad_normal)
        _, _, blue = conformed.split()
        minimum_blue, _ = blue.getextrema()
        self.assertEqual(conformed.mode, "RGB")
        self.assertGreaterEqual(minimum_blue, 128)

    def test_enforce_skyrim_output_profile_standard_env_mask_is_l_mode(self) -> None:
        rgba_mask = Image.new("RGBA", (8, 8), color=(120, 80, 40, 255))
        conformed = enforce_skyrim_output_profile("environment_mask", rgba_mask, env_mask_mode="standard")
        self.assertEqual(conformed.mode, "L")

    def test_enforce_skyrim_output_profile_complex_material_is_rgba(self) -> None:
        rgb_complex = Image.new("RGB", (8, 8), color=(40, 60, 80))
        conformed = enforce_skyrim_output_profile("complex_material", rgb_complex, complex_format="cm")
        self.assertEqual(conformed.mode, "RGBA")

    def test_generate_normal_flat_surface_stays_near_neutral(self) -> None:
        normal = generate_normal(_flat_dark_image(), strength=2.0)
        red, green, blue = normal.split()
        red_min, red_max = red.getextrema()
        green_min, green_max = green.getextrema()
        blue_min, blue_max = blue.getextrema()
        self.assertLessEqual(red_max - red_min, 4)
        self.assertLessEqual(green_max - green_min, 4)
        self.assertLessEqual(blue_max - blue_min, 4)
        self.assertGreaterEqual(red.getpixel((8, 8)), 120)
        self.assertLessEqual(red.getpixel((8, 8)), 136)
        self.assertGreaterEqual(green.getpixel((8, 8)), 120)
        self.assertLessEqual(green.getpixel((8, 8)), 136)
        self.assertGreaterEqual(blue.getpixel((8, 8)), 245)

    def test_generate_glow_returns_l_same_size(self) -> None:
        glow = generate_glow(_sample_image())
        self.assertEqual(glow.mode, "L")
        self.assertEqual(glow.size, (8, 8))

    def test_generate_environment_mask_returns_l_same_size(self) -> None:
        environment_mask = generate_environment_mask(_sample_image())
        self.assertEqual(environment_mask.mode, "L")
        self.assertEqual(environment_mask.size, (8, 8))

    def test_generate_environment_mask_flat_surface_avoids_black_holes(self) -> None:
        environment_mask = generate_environment_mask(_flat_dark_image(), strength=2.2)
        env_min, env_max = environment_mask.getextrema()
        self.assertGreaterEqual(env_min, 10)
        self.assertLessEqual(env_max - env_min, 80)

    def test_generate_complex_material_returns_rgba_same_size(self) -> None:
        complex_material = generate_complex_material(_sample_image())
        self.assertEqual(complex_material.mode, "RGBA")
        self.assertEqual(complex_material.size, (8, 8))

    def test_generate_complex_material_flat_surface_avoids_black_holes(self) -> None:
        complex_material = generate_complex_material(_flat_dark_image(), strength=2.2)
        extrema = complex_material.getextrema()
        for minimum, maximum in extrema:
            self.assertGreaterEqual(minimum, 3)
            self.assertLessEqual(maximum - minimum, 90)

    def test_generate_complex_material_is_not_normal_map_like(self) -> None:
        source = _vertical_gradient_image()
        complex_material = generate_complex_material(source, strength=1.5)
        normal = generate_normal(source, strength=1.5)
        self.assertNotEqual(complex_material.split()[:3], normal.split())

    def test_generate_complex_material_channel_order_matches_packed_contract(self) -> None:
        source = _vertical_gradient_image()
        complex_material = generate_complex_material(source, strength=1.4)
        env_reflection, glossiness, metallic, height = complex_material.split()
        self.assertNotEqual(env_reflection.tobytes(), glossiness.tobytes())
        self.assertNotEqual(glossiness.tobytes(), metallic.tobytes())
        self.assertNotEqual(height.tobytes(), env_reflection.tobytes())

    def test_generate_msn_returns_rgba_same_size(self) -> None:
        msn = generate_msn(_sample_image())
        self.assertEqual(msn.mode, "RGBA")
        self.assertEqual(msn.size, (8, 8))

    def test_generate_msn_rgb_matches_normal_and_alpha_matches_specular(self) -> None:
        source = _vertical_gradient_image()
        normal_strength = 1.8
        specular_strength = 1.2
        msn = generate_msn(source, normal_strength=normal_strength, specular_strength=specular_strength)
        expected_normal = generate_normal(source, strength=normal_strength)
        expected_specular = generate_specular(source, strength=specular_strength)
        self.assertEqual(msn.split()[:3], expected_normal.split())
        self.assertEqual(msn.split()[3].tobytes(), expected_specular.tobytes())

    def test_generate_ambient_occlusion_returns_l_mode_same_size(self) -> None:
        source = _sample_image()
        ao = generate_ambient_occlusion(source)
        self.assertEqual(ao.mode, "L")
        self.assertEqual(ao.size, source.size)

    def test_generate_ambient_occlusion_has_no_pure_black_holes(self) -> None:
        source = _sample_image()
        ao = generate_ambient_occlusion(source, strength=1.0)
        min_val = min(ao.getdata())
        self.assertGreater(min_val, 0, "AO should have no pure-black pixels")

    def test_generate_ambient_occlusion_higher_strength_increases_contrast(self) -> None:
        source = _vertical_gradient_image()
        ao_low = generate_ambient_occlusion(source, strength=0.5)
        ao_high = generate_ambient_occlusion(source, strength=3.0)
        import numpy as np
        std_low = float(np.std(list(ao_low.getdata())))
        std_high = float(np.std(list(ao_high.getdata())))
        self.assertGreater(std_high, std_low)

    def test_generate_roughness_returns_l_mode_same_size(self) -> None:
        source = _sample_image()
        roughness = generate_roughness(source)
        self.assertEqual(roughness.mode, "L")
        self.assertEqual(roughness.size, source.size)

    def test_generate_roughness_has_no_pure_black_holes(self) -> None:
        source = _sample_image()
        roughness = generate_roughness(source, strength=1.0)
        min_val = min(roughness.getdata())
        self.assertGreater(min_val, 0, "Roughness should have no pure-black pixels")

    def test_generate_roughness_metal_is_smoother_than_stone(self) -> None:
        source = _vertical_gradient_image()
        roughness_metal = generate_roughness(source, material_type="metal")
        roughness_stone = generate_roughness(source, material_type="stone")
        mean_metal = sum(roughness_metal.getdata()) / (source.width * source.height)
        mean_stone = sum(roughness_stone.getdata()) / (source.width * source.height)
        self.assertLess(mean_metal, mean_stone, "Metal should have lower roughness than stone")

    def test_prefilter_mipmap_stability_normal_preserves_size_and_mode(self) -> None:
        source = _sample_image().convert("RGB")
        result = _prefilter_for_mipmap_stability(source, "normal")
        self.assertEqual(result.size, source.size)
        self.assertEqual(result.mode, "RGB")

    def test_prefilter_mipmap_stability_normal_blue_channel_raised(self) -> None:
        # Pre-filter blends blue toward 255 (flat normal) — mean should rise slightly.
        source = _sample_image().convert("RGB")
        original_b = list(source.getchannel("B").getdata())
        result = _prefilter_for_mipmap_stability(source, "normal")
        filtered_b = list(result.getchannel("B").getdata())
        mean_orig = sum(original_b) / len(original_b)
        mean_filtered = sum(filtered_b) / len(filtered_b)
        self.assertGreaterEqual(mean_filtered, mean_orig - 1)  # should not darken blue

    def test_prefilter_mipmap_stability_parallax_preserves_size_and_mode(self) -> None:
        source = _sample_image().convert("L")
        result = _prefilter_for_mipmap_stability(source, "parallax")
        self.assertEqual(result.size, source.size)
        self.assertEqual(result.mode, "L")

    def test_prefilter_mipmap_stability_passthrough_for_diffuse(self) -> None:
        source = _sample_image().convert("RGB")
        result = _prefilter_for_mipmap_stability(source, "diffuse")
        self.assertEqual(result.tobytes(), source.tobytes())

    def test_prefilter_mipmap_stability_passthrough_for_unknown_kind(self) -> None:
        source = _sample_image().convert("RGB")
        result = _prefilter_for_mipmap_stability(source, "")
        self.assertEqual(result.tobytes(), source.tobytes())

    def test_analyze_material_type_from_image_returns_none_or_valid_type(self) -> None:
        valid_types = {
            "metal", "stone", "wood", "leather", "fur", "cloth", "glass", "skin",
            "snow", "plants", "terrain", "dirt", "sand", "architecture", "paper", "general", None,
        }
        result = analyze_material_type_from_image(_sample_image())
        self.assertIn(result, valid_types)

    def test_analyze_material_type_from_image_accepts_rgba_input(self) -> None:
        source = _sample_image().convert("RGBA")
        result = analyze_material_type_from_image(source)
        # Should not raise; result is None or a valid type string
        valid_types = {
            "metal", "stone", "wood", "leather", "fur", "cloth", "glass", "skin",
            "snow", "plants", "terrain", "dirt", "sand", "architecture", "paper", "general", None,
        }
        self.assertIn(result, valid_types)

    def test_recommend_generation_settings_returns_valid_ranges(self) -> None:
        settings = recommend_generation_settings(_sample_image())
        self.assertIn("specular_strength", settings)
        self.assertIn("complex_strength", settings)
        self.assertIn("environment_mask_strength", settings)
        self.assertGreaterEqual(float(settings["normal_strength"]), 1.1)
        self.assertLessEqual(float(settings["normal_strength"]), 3.8)
        self.assertGreaterEqual(float(settings["parallax_strength"]), 0.8)
        self.assertLessEqual(float(settings["parallax_strength"]), 2.4)
        self.assertGreaterEqual(float(settings["specular_strength"]), 0.9)
        self.assertLessEqual(float(settings["specular_strength"]), 2.2)
        self.assertGreaterEqual(int(settings["glow_threshold"]), 140)
        self.assertLessEqual(int(settings["glow_threshold"]), 235)

    def test_classify_material_type_returns_architecture_for_brick_path(self) -> None:
        # textures/architecture/ folder is now its own category; stone is the fallback
        # when only the filename has stone tokens but no architecture folder token.
        self.assertEqual(classify_material_type(Path("textures/architecture/brick_wall.dds")), "architecture")

    def test_classify_material_type_returns_stone_for_non_architecture_brick_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/dungeons/brick_floor.dds")), "stone")

    def test_classify_material_type_returns_metal_for_iron_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/armor/iron_helmet.dds")), "metal")

    def test_classify_material_type_returns_plants_for_leaf_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/plants/leaf01.dds")), "plants")

    def test_classify_material_type_returns_wood_for_timber_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/clutter/timber_plank.dds")), "wood")

    def test_classify_material_type_returns_general_for_unknown_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/misc/unknown.dds")), "general")

    def test_classify_material_type_returns_leather_for_hide_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/armor/leather/hide_strap.dds")), "leather")

    def test_classify_material_type_returns_fur_for_pelt_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/armor/fur/wolf_pelt.dds")), "fur")

    def test_classify_material_type_returns_dirt_for_mud_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/clutter/mud_pile.dds")), "dirt")

    def test_classify_material_type_returns_sand_for_dune_token(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/environment/dune_surface.dds")), "sand")

    def test_classify_material_type_returns_sand_for_sand_token(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/misc/sand_pile.dds")), "sand")

    def test_classify_material_type_dirt_path_classifies_as_dirt_not_sand(self) -> None:
        # "dirt" is a dirt token; sand no longer includes it so a dirt-named path stays dirt
        self.assertEqual(classify_material_type(Path("textures/clutter/dirt_road.dds")), "dirt")

    def test_classify_material_type_returns_paper_for_cards_path(self) -> None:
        self.assertEqual(
            classify_material_type(Path("textures/interface/cards/collectible_waifu_card_01.dds")),
            "paper",
        )

    def test_classify_material_type_returns_paper_for_sign_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/signs/inn_sign_painted.dds")), "paper")

    def test_classify_material_type_returns_terrain_for_landscape_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/landscape/dirt01.dds")), "terrain")

    def test_classify_material_type_returns_terrain_for_ground_token_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/ground/rocky_terrain.dds")), "terrain")

    def test_classify_material_type_returns_architecture_for_whiterun_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/whiterun/wrbuildings01.dds")), "architecture")

    def test_classify_material_type_returns_architecture_for_dwemer_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/dwemer/dwmplatform01.dds")), "architecture")

    def test_classify_material_type_returns_architecture_for_nordic_ruin(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/nordic/norwall01.dds")), "architecture")

    def test_detect_workflow_profile_detects_interface_paths(self) -> None:
        self.assertEqual(
            detect_workflow_profile(Path("textures/interface/cards/deck01.dds")),
            "interface",
        )

    def test_detect_workflow_profile_detects_renderer_and_content_profiles(self) -> None:
        self.assertEqual(detect_workflow_profile(Path("textures/vr/armor/helmet.dds")), "vr")
        self.assertEqual(detect_workflow_profile(Path("textures/performance/stone.dds")), "performance")
        self.assertEqual(detect_workflow_profile(Path("textures/architecture/ruins/wall.dds")), "architecture")

    def test_recommend_render_profile_detects_enb_from_path_hint(self) -> None:
        self.assertEqual(
            recommend_render_profile(Path("mods/ENBSeries/textures/architecture/stone.dds")),
            "enb",
        )

    def test_recommend_render_profile_detects_community_shaders_from_cm_role(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("textures/architecture/stone_cm.dds"),
                detected_role="complex_material_cm",
            ),
            "community_shaders",
        )

    def test_recommend_render_profile_prefers_vanilla_for_interface_or_paper(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("textures/interface/cards/collectible.dds"),
                material_type="paper",
                workflow_profile="interface",
            ),
            "vanilla",
        )

    def test_recommend_render_profile_defaults_to_vanilla_without_renderer_hints(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("textures/clutter/misc/metalplate.dds"),
                source=_detailed_low_saturation_image(),
            ),
            "vanilla",
        )

    def test_recommend_render_profile_infers_content_profile_from_path_when_unspecified(self) -> None:
        self.assertEqual(
            recommend_render_profile(Path("textures/architecture/stone/wall.dds")),
            "architecture",
        )
        self.assertEqual(
            recommend_render_profile(Path("textures/landscape/mountain.dds")),
            "terrain",
        )
        self.assertEqual(
            recommend_render_profile(Path("textures/actors/character/face.dds")),
            "characters",
        )

    def test_recommend_render_profile_uses_extended_profiles_for_neutral_material_hints(self) -> None:
        self.assertEqual(
            recommend_render_profile(Path("textures/landscape/mountain.dds"), material_type="terrain"),
            "terrain",
        )
        self.assertEqual(
            recommend_render_profile(Path("textures/architecture/stone/wall.dds"), material_type="architecture"),
            "architecture",
        )
        self.assertEqual(
            recommend_render_profile(Path("textures/actors/character/face.dds"), material_type="skin"),
            "characters",
        )

    def test_recommend_render_profile_uses_workflow_profile_hints(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("textures/architecture/stone.dds"),
                workflow_profile="vr",
            ),
            "vr",
        )
        self.assertEqual(
            recommend_render_profile(
                Path("textures/architecture/stone.dds"),
                workflow_profile="performance",
            ),
            "performance",
        )

    def test_recommend_render_profile_uses_mod_manager_context_hint_when_path_is_neutral(self) -> None:
        context = ModManagerContext(
            manager="MO2",
            loaded_mods=("Community Shaders", "Some Texture Pack"),
        )
        self.assertEqual(
            recommend_render_profile(
                Path("textures/architecture/metalplate.dds"),
                source=_detailed_low_saturation_image(),
                manager_context=context,
            ),
            "community_shaders",
        )

    def test_recommend_render_profile_detects_truepbr_from_rmaos_suffix(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("textures/architecture/stone_rmaos.dds"),
                detected_role="environment_mask",
                detected_suffix="_rmaos",
            ),
            "truepbr",
        )

    def test_recommend_render_profile_detects_truepbr_from_ramos_suffix(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("textures/architecture/stone_ramos.dds"),
                detected_role="environment_mask",
                detected_suffix="_ramos",
            ),
            "truepbr",
        )

    def test_recommend_render_profile_detects_truepbr_from_orm_suffix(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("textures/architecture/stone_orm.dds"),
                detected_role="environment_mask",
                detected_suffix="_orm",
            ),
            "truepbr",
        )

    def test_recommend_generation_settings_leather_is_less_reflective_than_metal(self) -> None:
        source = _detailed_low_saturation_image()
        leather = recommend_generation_settings(source, Path("textures/armor/leather/hide_strap.dds"))
        metal = recommend_generation_settings(source, Path("textures/armor/steel/plate.dds"))
        self.assertLess(float(leather["environment_mask_strength"]), float(metal["environment_mask_strength"]))
        self.assertLess(float(leather["specular_strength"]), float(metal["specular_strength"]))

    def test_recommend_render_profile_detects_truepbr_from_pbrnifpatcher_path_hint(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("mods/PBRNifPatcher/textures/architecture/stone.dds"),
            ),
            "truepbr",
        )

    def test_recommend_render_profile_detects_truepbr_from_textures_pbr_path_hint(self) -> None:
        self.assertEqual(
            recommend_render_profile(
                Path("mods/MyPack/textures/pbr/architecture/stone.dds"),
            ),
            "truepbr",
        )

    def test_detect_render_profile_from_mod_manager_context_prioritizes_truepbr(self) -> None:
        context = ModManagerContext(
            manager="MO2",
            loaded_mods=("Community Shaders", "PBRNifPatcher", "ENB Light"),
        )
        self.assertEqual(detect_render_profile_from_mod_manager_context(context), "truepbr")

    def test_detect_render_profile_from_mod_manager_context_prefers_cs_when_enb_and_cs_both_detected(self) -> None:
        context = ModManagerContext(
            manager="Vortex",
            loaded_mods=("Community Shaders", "ENB Light"),
        )
        self.assertEqual(detect_render_profile_from_mod_manager_context(context), "community_shaders")

    def test_detect_render_profile_from_mod_manager_context_uses_plugin_and_load_order_hints(self) -> None:
        context = ModManagerContext(
            manager="MO2",
            enabled_plugins=("CommunityShaders.esp",),
            load_order=("PBRNifPatcher.esp",),
        )
        self.assertEqual(detect_render_profile_from_mod_manager_context(context), "truepbr")

    def test_detect_render_profile_from_mod_manager_context_detects_enb_from_runtime_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "d3d11.dll").write_bytes(b"")
            context = ModManagerContext(
                manager="MO2",
                instance_root=root,
            )
            self.assertEqual(detect_render_profile_from_mod_manager_context(context), "enb")

    def test_detect_render_profile_from_mod_manager_context_detects_cs_from_skse_plugin_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin_dir = root / "SKSE" / "Plugins"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "CommunityShaders.dll").write_bytes(b"")
            context = ModManagerContext(
                manager="Vortex",
                staging_root=root,
            )
            self.assertEqual(detect_render_profile_from_mod_manager_context(context), "community_shaders")

    def test_detect_render_profile_from_mod_manager_context_detects_truepbr_from_textures_pbr_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pbr_dir = root / "textures" / "pbr"
            pbr_dir.mkdir(parents=True)
            context = ModManagerContext(
                manager="MO2",
                instance_root=root,
            )
            self.assertEqual(detect_render_profile_from_mod_manager_context(context), "truepbr")

    def test_detect_render_profile_from_mod_manager_context_detects_enb_from_game_root_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            game_root = Path(temp_dir) / "SkyrimSE"
            game_root.mkdir(parents=True)
            (game_root / "d3d11.dll").write_bytes(b"")
            context = ModManagerContext(
                manager="MO2",
                game_root=game_root,
            )
            self.assertEqual(detect_render_profile_from_mod_manager_context(context), "enb")

    def test_detect_render_profile_from_mod_manager_context_detects_cs_from_game_data_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            game_root = Path(temp_dir) / "SkyrimSE"
            plugin_dir = game_root / "Data" / "SKSE" / "Plugins"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "CommunityShaders.dll").write_bytes(b"")
            context = ModManagerContext(
                manager="Vortex",
                game_root=game_root,
            )
            self.assertEqual(detect_render_profile_from_mod_manager_context(context), "community_shaders")

    def test_resolve_render_profile_options_returns_expected_modes(self) -> None:
        enb = resolve_render_profile_options("enb")
        self.assertEqual(enb["complex_format"], "msn")
        self.assertEqual(enb["env_mask_mode"], "complex")
        self.assertEqual(enb["parallax_mode"], "occlusion")

        performance = resolve_render_profile_options("performance")
        self.assertEqual(performance["complex_format"], "msn")
        self.assertEqual(performance["env_mask_mode"], "standard")
        self.assertEqual(performance["parallax_mode"], "standard")

        vr = resolve_render_profile_options("vr")
        self.assertEqual(vr["complex_format"], "msn")
        self.assertEqual(vr["env_mask_mode"], "standard")
        self.assertEqual(vr["parallax_mode"], "standard")

        terrain = resolve_render_profile_options("terrain")
        self.assertEqual(terrain["complex_format"], "msn")
        self.assertEqual(terrain["env_mask_mode"], "standard")
        self.assertEqual(terrain["parallax_mode"], "standard")

        architecture = resolve_render_profile_options("architecture")
        self.assertEqual(architecture["complex_format"], "msn")
        self.assertEqual(architecture["env_mask_mode"], "standard")
        self.assertEqual(architecture["parallax_mode"], "standard")

        characters = resolve_render_profile_options("characters")
        self.assertEqual(characters["complex_format"], "msn")
        self.assertEqual(characters["env_mask_mode"], "standard")
        self.assertEqual(characters["parallax_mode"], "standard")

        cs = resolve_render_profile_options("community_shaders")
        self.assertEqual(cs["complex_format"], "cm")
        self.assertEqual(cs["env_mask_mode"], "standard")
        self.assertEqual(cs["parallax_mode"], "standard")

        truepbr = resolve_render_profile_options("truepbr")
        self.assertEqual(truepbr["complex_format"], "cm")
        self.assertEqual(truepbr["env_mask_mode"], "complex")
        self.assertEqual(truepbr["parallax_mode"], "standard")

        auto = resolve_render_profile_options("auto", recommended_profile="enb")
        self.assertEqual(auto["effective_profile"], "enb")
        # Auto-detected ENB keeps parallax in standard mode so sign/artwork textures
        # keep their pop-out effect. Occlusion is only used when ENB is explicitly chosen.
        self.assertEqual(auto["parallax_mode"], "standard")
        self.assertEqual(auto["complex_format"], "msn")
        self.assertEqual(auto["env_mask_mode"], "complex")

    def test_resolve_render_profile_guardrails_enforces_locked_profile_modes(self) -> None:
        guarded = resolve_render_profile_guardrails(
            selected_profile="enb",
            complex_format="cm",
            env_mask_mode="standard",
            parallax_mode="standard",
        )
        self.assertEqual(str(guarded["effective_profile"]), "enb")
        self.assertEqual(str(guarded["complex_format"]), "msn")
        self.assertEqual(str(guarded["env_mask_mode"]), "complex")
        self.assertEqual(str(guarded["parallax_mode"]), "occlusion")
        self.assertEqual(len(list(guarded["changes"])), 3)

    def test_resolve_render_profile_guardrails_keeps_auto_modes_without_recommendation(self) -> None:
        guarded = resolve_render_profile_guardrails(
            selected_profile="auto",
            recommended_profile=None,
            complex_format="cm",
            env_mask_mode="complex",
            parallax_mode="occlusion",
        )
        self.assertEqual(str(guarded["effective_profile"]), "auto")
        self.assertEqual(str(guarded["complex_format"]), "cm")
        self.assertEqual(str(guarded["env_mask_mode"]), "complex")
        self.assertEqual(str(guarded["parallax_mode"]), "occlusion")
        self.assertEqual(list(guarded["changes"]), [])

    def test_resolve_render_profile_output_defaults_returns_expected_checkboxes(self) -> None:
        vanilla = resolve_render_profile_output_defaults("vanilla")
        self.assertTrue(bool(vanilla["include_diffuse"]))
        self.assertTrue(bool(vanilla["include_normal"]))
        self.assertFalse(bool(vanilla["include_parallax"]))
        self.assertFalse(bool(vanilla["include_complex"]))
        self.assertFalse(bool(vanilla["include_wetness_mask"]))
        self.assertFalse(bool(vanilla["include_snow_mask"]))

        performance = resolve_render_profile_output_defaults("performance")
        self.assertTrue(bool(performance["include_diffuse"]))
        self.assertTrue(bool(performance["include_normal"]))
        self.assertFalse(bool(performance["include_parallax"]))
        self.assertFalse(bool(performance["include_complex"]))
        self.assertFalse(bool(performance["include_wetness_mask"]))
        self.assertFalse(bool(performance["include_snow_mask"]))

        terrain = resolve_render_profile_output_defaults("terrain")
        self.assertTrue(bool(terrain["include_parallax"]))
        self.assertFalse(bool(terrain["include_environment_mask"]))
        self.assertFalse(bool(terrain["include_complex"]))
        self.assertFalse(bool(terrain["include_wetness_mask"]))
        self.assertFalse(bool(terrain["include_snow_mask"]))

        architecture = resolve_render_profile_output_defaults("architecture")
        self.assertTrue(bool(architecture["include_parallax"]))
        self.assertTrue(bool(architecture["include_environment_mask"]))
        self.assertFalse(bool(architecture["include_complex"]))
        self.assertFalse(bool(architecture["include_wetness_mask"]))
        self.assertFalse(bool(architecture["include_snow_mask"]))

        characters = resolve_render_profile_output_defaults("characters")
        self.assertFalse(bool(characters["include_parallax"]))
        self.assertFalse(bool(characters["include_environment_mask"]))
        self.assertFalse(bool(characters["include_complex"]))
        self.assertFalse(bool(characters["include_wetness_mask"]))
        self.assertFalse(bool(characters["include_snow_mask"]))

        enb = resolve_render_profile_output_defaults("enb")
        self.assertTrue(bool(enb["include_parallax"]))
        self.assertTrue(bool(enb["include_environment_mask"]))
        self.assertTrue(bool(enb["include_complex"]))
        self.assertFalse(bool(enb["include_normal"]))
        self.assertFalse(bool(enb["include_wetness_mask"]))
        self.assertFalse(bool(enb["include_snow_mask"]))

        truepbr = resolve_render_profile_output_defaults("truepbr")
        self.assertTrue(bool(truepbr["include_parallax"]))
        self.assertFalse(bool(truepbr["include_environment_mask"]))
        self.assertTrue(bool(truepbr["include_rmaos"]))
        self.assertFalse(bool(truepbr["include_complex"]))
        self.assertTrue(bool(truepbr["include_normal"]))
        self.assertFalse(bool(truepbr["include_wetness_mask"]))
        self.assertFalse(bool(truepbr["include_snow_mask"]))

    def test_render_profile_has_locked_controls_only_in_custom_mode(self) -> None:
        self.assertFalse(render_profile_has_locked_controls("custom"))
        self.assertTrue(render_profile_has_locked_controls("auto"))
        self.assertTrue(render_profile_has_locked_controls("enb"))

    def test_resolve_render_profile_mode_control_states_locks_all_modes_for_non_custom_profile(self) -> None:
        states = resolve_render_profile_mode_control_states(
            selected_profile="enb",
            include_complex=True,
            include_environment_mask=True,
            include_parallax=True,
        )
        self.assertTrue(bool(states["locked"]))
        self.assertEqual(str(states["complex_format"]), "disabled")
        self.assertEqual(str(states["env_mask_mode"]), "disabled")
        self.assertEqual(str(states["parallax_mode"]), "disabled")

    def test_resolve_render_profile_mode_control_states_only_enables_modes_for_checked_outputs(self) -> None:
        states = resolve_render_profile_mode_control_states(
            selected_profile="custom",
            include_complex=False,
            include_environment_mask=True,
            include_parallax=False,
        )
        self.assertFalse(bool(states["locked"]))
        self.assertEqual(str(states["complex_format"]), "disabled")
        self.assertEqual(str(states["env_mask_mode"]), "readonly")
        self.assertEqual(str(states["parallax_mode"]), "disabled")

    def test_build_render_profile_mode_controls_hint_reports_disabled_outputs_in_custom_mode(self) -> None:
        hint = build_render_profile_mode_controls_hint(
            {
                "locked": False,
                "complex_format": "disabled",
                "env_mask_mode": "readonly",
                "parallax_mode": "disabled",
            }
        )
        self.assertIn("Custom mode: enable", hint)
        self.assertIn("Complex/PBR material", hint)
        self.assertIn("Parallax", hint)

    def test_build_render_profile_mode_controls_hint_reports_locked_state(self) -> None:
        hint = build_render_profile_mode_controls_hint(
            {
                "locked": True,
                "complex_format": "disabled",
                "env_mask_mode": "disabled",
                "parallax_mode": "disabled",
            }
        )
        self.assertIn("locks mode selectors", hint)

    def test_resolve_nif_patch_defaults_for_render_profile_returns_expected_toggles(self) -> None:
        vanilla = resolve_nif_patch_defaults_for_render_profile("vanilla")
        self.assertTrue(bool(vanilla["enable_parallax"]))
        self.assertFalse(bool(vanilla["enable_pom"]))
        self.assertFalse(bool(vanilla["enable_env_mapping"]))
        self.assertFalse(bool(vanilla["force_shader_type_3"]))
        self.assertFalse(bool(vanilla["prefer_msn_normal"]))

        performance = resolve_nif_patch_defaults_for_render_profile("performance")
        self.assertFalse(bool(performance["enable_parallax"]))
        self.assertFalse(bool(performance["enable_pom"]))
        self.assertFalse(bool(performance["enable_env_mapping"]))
        self.assertFalse(bool(performance["force_shader_type_3"]))
        self.assertFalse(bool(performance["prefer_msn_normal"]))

        vr = resolve_nif_patch_defaults_for_render_profile("vr")
        self.assertFalse(bool(vr["enable_parallax"]))
        self.assertFalse(bool(vr["enable_pom"]))
        self.assertFalse(bool(vr["enable_env_mapping"]))
        self.assertFalse(bool(vr["force_shader_type_3"]))
        self.assertFalse(bool(vr["prefer_msn_normal"]))

        terrain = resolve_nif_patch_defaults_for_render_profile("terrain")
        self.assertTrue(bool(terrain["enable_parallax"]))
        self.assertFalse(bool(terrain["enable_pom"]))
        self.assertFalse(bool(terrain["enable_env_mapping"]))

        architecture = resolve_nif_patch_defaults_for_render_profile("architecture")
        self.assertTrue(bool(architecture["enable_parallax"]))
        self.assertFalse(bool(architecture["enable_pom"]))
        self.assertTrue(bool(architecture["enable_env_mapping"]))

        characters = resolve_nif_patch_defaults_for_render_profile("characters")
        self.assertFalse(bool(characters["enable_parallax"]))
        self.assertFalse(bool(characters["enable_pom"]))
        self.assertFalse(bool(characters["enable_env_mapping"]))

        enb = resolve_nif_patch_defaults_for_render_profile("enb")
        self.assertTrue(bool(enb["enable_parallax"]))
        self.assertTrue(bool(enb["enable_pom"]))
        self.assertTrue(bool(enb["enable_env_mapping"]))
        self.assertTrue(bool(enb["force_shader_type_3"]))
        self.assertTrue(bool(enb["prefer_msn_normal"]))

        truepbr = resolve_nif_patch_defaults_for_render_profile("truepbr")
        self.assertTrue(bool(truepbr["enable_parallax"]))
        self.assertFalse(bool(truepbr["enable_pom"]))
        self.assertTrue(bool(truepbr["enable_env_mapping"]))
        self.assertTrue(bool(truepbr["force_shader_type_3"]))
        self.assertFalse(bool(truepbr["prefer_msn_normal"]))

    def test_get_nif_patch_option_warnings_reports_disabled_feature_path_combos(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="vanilla",
            enable_parallax=False,
            enable_pom=False,
            enable_env_mapping=False,
            force_shader_type_3=False,
            parallax_texture_path="textures\\arch\\stone_p.dds",
            env_mask_texture_path="textures\\arch\\stone_m.dds",
        )
        self.assertTrue(any("Parallax slot path set" in text for text in warnings))
        self.assertTrue(any("Environment mask path set" in text for text in warnings))

    def test_get_nif_patch_option_warnings_skips_empty_env_mask_warning_for_enb(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="enb",
            enable_parallax=True,
            enable_pom=True,
            enable_env_mapping=True,
            force_shader_type_3=True,
            env_mask_texture_path="",
        )
        self.assertFalse(any("empty slot 5 path" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_uses_recommended_profile_when_selected_is_auto(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="auto",
            recommended_profile="enb",
            enable_parallax=True,
            enable_pom=True,
            enable_env_mapping=True,
            force_shader_type_3=True,
            env_mask_texture_path="",
        )
        self.assertFalse(any("vanilla meshes" in text.lower() for text in warnings))
        self.assertFalse(any("empty slot 5 path" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_reports_absolute_and_non_textures_paths(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="enb",
            enable_parallax=True,
            enable_pom=True,
            enable_env_mapping=True,
            force_shader_type_3=True,
            parallax_texture_path=r"C:\Mods\Data\Textures\architecture\stone\stone.dds",
            normal_texture_path="stone_n.dds",
            env_mask_texture_path=r"D:\mod\masks\stone_mask.dds",
        )
        self.assertTrue(any("absolute" in text.lower() for text in warnings))
        self.assertTrue(any("start with textures\\" in text.lower() for text in warnings))
        self.assertTrue(any("_p.dds" in text for text in warnings))
        self.assertTrue(any("env mask slot" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_accepts_c_env_mask_suffix(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="community_shaders",
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=True,
            force_shader_type_3=True,
            env_mask_texture_path="textures\\architecture\\stone\\stone_c.dds",
        )
        self.assertFalse(any("env mask slot path should usually end" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_reports_diffuse_slot_misuse(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="vanilla",
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=False,
            force_shader_type_3=False,
            diffuse_texture_path="textures\\architecture\\stone\\stone_n.dds",
            cubemap_texture_path="textures\\architecture\\stone\\stone_n.dds",
        )
        self.assertTrue(any("slot 0" in text.lower() or "diffuse slot" in text.lower() for text in warnings))
        self.assertTrue(any("cubemap slot" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_reports_diffuse_slot_misuse_for_emissive_suffix(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="vanilla",
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=False,
            force_shader_type_3=False,
            diffuse_texture_path="textures\\architecture\\stone\\stone_em.dds",
        )
        self.assertTrue(any("slot 0" in text.lower() or "diffuse slot" in text.lower() for text in warnings))
        self.assertTrue(any("_em" in text.lower() for text in warnings))

    def test_get_nif_patch_option_warnings_reports_renderer_specific_suffix_mismatch(self) -> None:
        enb_warnings = get_nif_patch_option_warnings(
            selected_profile="enb",
            enable_parallax=True,
            enable_pom=True,
            enable_env_mapping=True,
            force_shader_type_3=True,
            normal_texture_path="textures\\architecture\\stone\\stone_n.dds",
            env_mask_texture_path="textures\\architecture\\stone\\stone_cm.dds",
        )
        self.assertTrue(any("_msn" in text.lower() for text in enb_warnings))
        self.assertTrue(any("slot 5" in text.lower() and "_m" in text.lower() for text in enb_warnings))

        truepbr_warnings = get_nif_patch_option_warnings(
            selected_profile="truepbr",
            enable_parallax=True,
            enable_pom=False,
            enable_env_mapping=True,
            force_shader_type_3=True,
            normal_texture_path="textures\\pbr\\stone_msn.dds",
            env_mask_texture_path="textures\\pbr\\stone_cm.dds",
        )
        self.assertTrue(any("_n" in text.lower() and "_msn" in text.lower() for text in truepbr_warnings))
        self.assertTrue(any("_rmaos" in text.lower() for text in truepbr_warnings))

    def test_build_render_profile_recommendation_message_lists_renderer_guidance(self) -> None:
        message = build_render_profile_recommendation_message("community_shaders")
        self.assertIn("Suggested target: Community Shaders", message)
        self.assertIn("Renderer quick guide:", message)
        self.assertIn("Vanilla:", message)
        self.assertIn("Terrain:", message)
        self.assertIn("Architecture:", message)
        self.assertIn("Characters / Skin:", message)
        self.assertIn("Community Shaders TruePBR:", message)
        self.assertIn("ENB:", message)
        self.assertIn("Auto-check:", message)
        self.assertIn("_C.dds", message)
        self.assertIn("How files should look:", message)

    def test_build_render_profile_recommendation_message_avoids_experimental_inaccuracy_copy(self) -> None:
        message = build_render_profile_recommendation_message("community_shaders")
        lowered = message.lower()
        self.assertNotIn("experimental", lowered)
        self.assertNotIn("may be inaccurate", lowered)

    def test_describe_render_profile_default_outputs_mentions_auto_checked_outputs(self) -> None:
        summary = describe_render_profile_default_outputs("enb")
        self.assertIn("Auto-check:", summary)
        self.assertIn("parallax/_p", summary)

    def test_describe_render_profile_default_outputs_includes_wetness_snow_in_disabled_list(self) -> None:
        for profile in ("vanilla", "terrain", "architecture", "community_shaders", "enb", "truepbr"):
            with self.subTest(profile=profile):
                summary = describe_render_profile_default_outputs(profile)
                self.assertIn("wetness mask/_wt", summary)
                self.assertIn("snow mask/_sm", summary)

    def test_describe_render_profile_default_outputs_glow_uses_em_suffix(self) -> None:
        """Glow output label in profile summaries must say _em, not the legacy _g."""
        for profile in ("vanilla", "enb", "community_shaders", "truepbr"):
            with self.subTest(profile=profile):
                summary = describe_render_profile_default_outputs(profile)
                # The summary string should contain "glow/_em" wherever glow appears
                if "glow/" in summary:
                    self.assertIn("glow/_em", summary)
                    self.assertNotIn("glow/_g", summary)

    def test_describe_render_profile_files_to_create_mentions_enb_msn_outputs(self) -> None:
        summary = describe_render_profile_files_to_create("enb")
        self.assertIn("<stem>_msn.dds", summary)
        self.assertIn("<stem>_m.dds", summary)
        self.assertNotIn("<stem>_n.dds", summary)

    def test_describe_render_profile_files_to_create_mentions_truepbr_outputs(self) -> None:
        summary = describe_render_profile_files_to_create("truepbr")
        self.assertIn("<stem>_rmaos.dds", summary)
        self.assertIn("<stem>_n.dds", summary)
        self.assertIn("<stem>_ao.dds", summary)
        self.assertIn("<stem>_rough.dds", summary)

    def test_get_nif_patch_option_warnings_reports_glow_and_cubemap_clear_write_conflicts(self) -> None:
        warnings = get_nif_patch_option_warnings(
            selected_profile="custom",
            enable_parallax=False,
            enable_pom=False,
            enable_env_mapping=False,
            force_shader_type_3=False,
            glow_texture_path="textures\\arch\\stone_g.dds",
            diffuse_texture_path="textures\\arch\\stone.dds",
            cubemap_texture_path="textures\\arch\\stone_env.dds",
            disable_glow_map=True,
            clear_glow_texture_path=True,
            clear_diffuse_texture_path=True,
            clear_cubemap_texture_path=True,
        )
        self.assertTrue(any("disable and write a slot 2 path" in text.lower() for text in warnings))
        self.assertTrue(any("glow slot is set to both clear and write a path" in text.lower() for text in warnings))
        self.assertTrue(any("diffuse slot is set to both clear and write a path" in text.lower() for text in warnings))
        self.assertTrue(any("cubemap slot is set to both clear and write a path" in text.lower() for text in warnings))

    def test_resolve_render_profile_mode_selection_preserves_current_modes_without_preset_apply(self) -> None:
        resolved = resolve_render_profile_mode_selection(
            {
                "complex_format": "cm",
                "env_mask_mode": "standard",
                "parallax_mode": "occlusion (ENB/POM)",
            },
            selected_profile="auto",
            recommended_profile="vanilla",
            apply_preset=False,
        )
        self.assertEqual(resolved["effective_profile"], "vanilla")
        self.assertEqual(resolved["complex_format"], "cm")
        self.assertEqual(resolved["env_mask_mode"], "standard")
        self.assertEqual(resolved["parallax_mode"], "occlusion")

    def test_parse_preview_jump_input_validates_bounds(self) -> None:
        self.assertEqual(parse_preview_jump_input("1", 5), 0)
        self.assertEqual(parse_preview_jump_input("5", 5), 4)
        self.assertIsNone(parse_preview_jump_input("0", 5))
        self.assertIsNone(parse_preview_jump_input("6", 5))
        self.assertIsNone(parse_preview_jump_input("abc", 5))

    def test_normalize_nif_result_details_falls_back_for_blank_input(self) -> None:
        self.assertEqual(_normalize_nif_result_details(" \n "), "(no details)")

    def test_format_nif_result_row_details_keeps_full_text_without_truncation(self) -> None:
        original = "Line one\n" + ("Very long detail chunk " * 20) + "\nLine three"
        formatted = _format_nif_result_row_details(original)
        self.assertIn("Line one ↩ ", formatted)
        self.assertIn("Line three", formatted)
        self.assertNotIn("...", formatted)
        self.assertGreater(len(formatted), 190)

    def test_compute_wrapped_preview_index_wraps_at_boundaries(self) -> None:
        self.assertEqual(compute_wrapped_preview_index(-1, 3), 2)
        self.assertEqual(compute_wrapped_preview_index(3, 3), 0)
        self.assertEqual(compute_wrapped_preview_index(1, 3), 1)

    def test_restore_nif_backups_restores_from_nif_bak(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mesh_path = Path(tmpdir) / "test_mesh.nif"
            backup_path = mesh_path.with_suffix(".nif.bak")
            mesh_path.write_bytes(b"new-data")
            backup_path.write_bytes(b"old-data")

            rows = restore_nif_backups([mesh_path])

            self.assertEqual(rows[0][0], "OK")
            self.assertEqual(mesh_path.read_bytes(), b"old-data")

    def test_restore_nif_backups_skips_when_backup_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mesh_path = Path(tmpdir) / "test_mesh.nif"
            mesh_path.write_bytes(b"current-data")

            rows = restore_nif_backups([mesh_path])

            self.assertEqual(rows[0], ("SKIP", "test_mesh.nif", "No .nif.bak backup found."))
            self.assertEqual(mesh_path.read_bytes(), b"current-data")

    def test_should_apply_preview_recommendations_disabled_when_processing(self) -> None:
        self.assertFalse(should_apply_preview_recommendations(
            auto_suggestions_enabled=True,
            is_processing=True,
        ))

    def test_should_apply_preview_recommendations_enabled_when_idle_and_auto_on(self) -> None:
        self.assertTrue(should_apply_preview_recommendations(
            auto_suggestions_enabled=True,
            is_processing=False,
        ))

    def test_should_apply_preview_recommendations_disabled_when_auto_off(self) -> None:
        self.assertFalse(should_apply_preview_recommendations(
            auto_suggestions_enabled=False,
            is_processing=False,
        ))

    def test_get_generation_warnings_glow_on_stone_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_glow=True,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("glow_non_magical", ids)

    def test_get_generation_warnings_glow_on_plants_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "plants",
            include_glow=True,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("glow_non_magical", ids)

    def test_get_generation_warnings_high_env_mask_on_plants(self) -> None:
        warnings = get_generation_warnings(
            "plants",
            include_glow=False,
            include_environment_mask=True,
            env_mask_mode="standard",
            env_mask_strength=1.8,
            include_parallax=False,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("high_env_mask_organic", ids)

    def test_get_generation_warnings_no_false_positives_for_metal(self) -> None:
        warnings = get_generation_warnings(
            "metal",
            include_glow=False,
            include_environment_mask=True,
            env_mask_mode="standard",
            env_mask_strength=1.8,
            include_parallax=False,
            include_complex=False,
        )
        self.assertEqual(warnings, [])

    def test_get_generation_warnings_parallax_on_plants(self) -> None:
        warnings = get_generation_warnings(
            "plants",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("parallax_flat_plants", ids)

    def test_get_generation_warnings_parallax_on_terrain_always_warns(self) -> None:
        # Terrain shimmer warning must fire regardless of source_hint content
        warnings = get_generation_warnings(
            "terrain",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("parallax_landscape_shimmer", ids)

    def test_get_generation_warnings_parallax_on_terrain_fires_without_landscape_hint(self) -> None:
        # Previously required "landscape" in source_hint — now fires on material_type alone
        warnings_no_hint = get_generation_warnings(
            "terrain",
            source_hint=None,
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            include_complex=False,
        )
        warnings_unrelated_hint = get_generation_warnings(
            "terrain",
            source_hint="forest path texture",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            include_complex=False,
        )
        self.assertIn("parallax_landscape_shimmer", [w[0] for w in warnings_no_hint])
        self.assertIn("parallax_landscape_shimmer", [w[0] for w in warnings_unrelated_hint])

    def test_get_generation_warnings_parallax_high_strength_snow_warns(self) -> None:
        warnings = get_generation_warnings(
            "snow",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            parallax_strength=2.0,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("parallax_high_strength_snow", ids)

    def test_get_generation_warnings_parallax_low_strength_snow_no_warning(self) -> None:
        warnings = get_generation_warnings(
            "snow",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            parallax_strength=1.2,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_high_strength_snow", ids)

    def test_get_generation_warnings_parallax_high_strength_dirt_warns(self) -> None:
        warnings = get_generation_warnings(
            "dirt",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            parallax_strength=2.5,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("parallax_high_strength_ground", ids)

    def test_get_generation_warnings_parallax_high_strength_sand_warns(self) -> None:
        warnings = get_generation_warnings(
            "sand",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            parallax_strength=3.0,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("parallax_high_strength_ground", ids)

    def test_get_generation_warnings_parallax_within_threshold_ground_no_warning(self) -> None:
        for mat in ("dirt", "sand"):
            warnings = get_generation_warnings(
                mat,
                include_glow=False,
                include_environment_mask=False,
                env_mask_mode="standard",
                env_mask_strength=1.0,
                include_parallax=True,
                parallax_strength=1.8,
                include_complex=False,
            )
            ids = [w[0] for w in warnings]
            self.assertNotIn("parallax_high_strength_ground", ids, msg=f"Unexpected warning for {mat}")

    def test_get_generation_warnings_parallax_strength_none_no_false_positives(self) -> None:
        # When parallax_strength is not provided the strength-based warnings must not fire
        for mat in ("snow", "dirt", "sand"):
            warnings = get_generation_warnings(
                mat,
                include_glow=False,
                include_environment_mask=False,
                env_mask_mode="standard",
                env_mask_strength=1.0,
                include_parallax=True,
                parallax_strength=None,
                include_complex=False,
            )
            ids = [w[0] for w in warnings]
            self.assertNotIn("parallax_high_strength_snow", ids, msg=f"Unexpected snow warning for {mat}")
            self.assertNotIn("parallax_high_strength_ground", ids, msg=f"Unexpected ground warning for {mat}")

    def test_get_generation_warnings_diffuse_from_normal_source(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            source_role="normal",
            include_diffuse=True,
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("diffuse_from_derived_source", ids)
        warning_lookup = dict(warnings)
        self.assertIn("_em", warning_lookup["diffuse_from_derived_source"])
        self.assertIn("_ao", warning_lookup["diffuse_from_derived_source"])
        self.assertNotIn("_g/_m", warning_lookup["diffuse_from_derived_source"])

    def test_get_generation_warnings_normal_from_normal_source(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            source_role="normal",
            include_normal=True,
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("normal_from_normal_source", ids)

    def test_get_generation_warnings_ui_hint_flags_advanced_maps(self) -> None:
        warnings = get_generation_warnings(
            "general",
            source_hint="UI/interface texture (not for in-world use)",
            include_glow=False,
            include_environment_mask=True,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("ui_texture_advanced_maps", ids)

    def test_get_generation_warnings_orm_source_triggers_truepbr_suffix_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            source_suffix="_orm",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("rmaos_source_requires_renderer_check", ids)

    def test_recommend_generation_settings_clamps_interface_workflow_strengths(self) -> None:
        settings = recommend_generation_settings(
            _detailed_bright_image(),
            input_path=Path("textures/interface/cards/collectible_waifu_card_01.dds"),
        )
        self.assertLessEqual(float(settings["parallax_strength"]), 1.0)
        self.assertLessEqual(float(settings["environment_mask_strength"]), 1.15)
        self.assertLessEqual(float(settings["specular_strength"]), 1.15)

    def test_generate_complex_material_slider_produces_visible_change(self) -> None:
        source = _large_high_detail_image()
        low_strength = generate_complex_material(source, strength=0.5)
        high_strength = generate_complex_material(source, strength=3.0)
        low_r, _, _, _ = low_strength.split()
        high_r, _, _, _ = high_strength.split()
        import numpy as np

        low_arr = np.asarray(low_r, dtype=np.float32)
        high_arr = np.asarray(high_r, dtype=np.float32)
        mean_diff = float(np.abs(low_arr - high_arr).mean())
        self.assertGreater(mean_diff, 2.0)

    def test_analyze_image_content_exposes_expected_metrics(self) -> None:
        metrics = analyze_image_content(_sample_image())
        for key in (
            "megapixels",
            "brightness",
            "contrast",
            "edge_strength",
            "edge_variance",
            "detail_energy",
            "dynamic_range",
            "shadow_ratio",
            "highlight_ratio",
            "bright_cluster_ratio",
            "midtone_ratio",
            "p90_luma",
            "saturation_mean",
            "saturation_variance",
            "low_saturation_ratio",
            "color_variance",
        ):
            self.assertIn(key, metrics)

    def test_recommend_generation_settings_changes_by_image_content(self) -> None:
        flat_dark = recommend_generation_settings(_flat_dark_image())
        detailed_bright = recommend_generation_settings(_detailed_bright_image())
        self.assertNotEqual(int(flat_dark["glow_threshold"]), int(detailed_bright["glow_threshold"]))
        self.assertNotEqual(float(flat_dark["normal_strength"]), float(detailed_bright["normal_strength"]))
        self.assertNotEqual(float(flat_dark["specular_strength"]), float(detailed_bright["specular_strength"]))

    def test_recommend_generation_settings_glow_handles_sparse_highlights(self) -> None:
        uniform = recommend_generation_settings(_uniform_bright_image())
        sparse = recommend_generation_settings(_sparse_highlight_image())
        self.assertGreater(int(uniform["glow_threshold"]), int(sparse["glow_threshold"]))

    def test_recommend_generation_settings_uses_saturation_for_glow_threshold(self) -> None:
        low_saturation = recommend_generation_settings(_uniform_bright_image())
        high_saturation = recommend_generation_settings(_high_saturation_image())
        self.assertLess(int(high_saturation["glow_threshold"]), int(low_saturation["glow_threshold"]))

    def test_recommend_generation_settings_dampens_highly_detailed_large_inputs(self) -> None:
        settings = recommend_generation_settings(_large_high_detail_image())
        self.assertLess(float(settings["parallax_strength"]), 2.2)
        self.assertLess(float(settings["complex_strength"]), 2.5)
        self.assertLess(float(settings["specular_strength"]), 2.1)

    def test_recommend_generation_settings_adjusts_for_normal_input_filename(self) -> None:
        source = _sample_image()
        baseline = recommend_generation_settings(source)
        normal_named = recommend_generation_settings(source, input_path=Path("textures/architecture/stone_n.dds"))
        self.assertLessEqual(float(normal_named["normal_strength"]), float(baseline["normal_strength"]))
        self.assertLessEqual(float(normal_named["parallax_strength"]), float(baseline["parallax_strength"]))

    def test_recommend_generation_settings_can_infer_normal_like_image(self) -> None:
        baseline = recommend_generation_settings(_sample_image())
        inferred = recommend_generation_settings(_normal_like_image())
        self.assertLessEqual(float(inferred["normal_strength"]), float(baseline["normal_strength"]))
        self.assertGreaterEqual(int(inferred["glow_threshold"]), int(baseline["glow_threshold"]))

    def test_generate_environment_mask_glossiness_stays_above_complex_parallax_floor(self) -> None:
        environment_mask = generate_environment_mask(_large_high_detail_image(), strength=2.2, mode="complex")
        _, metallic, _, _ = environment_mask.split()
        minimum, _ = metallic.getextrema()
        self.assertGreater(minimum, 4)

    def test_generate_environment_mask_complex_mode_returns_rgba_same_size(self) -> None:
        environment_mask = generate_environment_mask(_sample_image(), mode="complex")
        self.assertEqual(environment_mask.mode, "RGBA")
        self.assertEqual(environment_mask.size, (8, 8))

    def test_generate_environment_mask_complex_mode_flat_surface_avoids_black_holes(self) -> None:
        environment_mask = generate_environment_mask(_flat_dark_image(), strength=2.2, mode="complex")
        roughness, metallic, ao, specular_height = environment_mask.split()
        rough_min, rough_max = roughness.getextrema()
        metallic_min, _ = metallic.getextrema()
        ao_min, _ = ao.getextrema()
        alpha_min, alpha_max = specular_height.getextrema()
        self.assertGreaterEqual(rough_min, 24)
        self.assertLessEqual(rough_max - rough_min, 80)
        self.assertGreaterEqual(metallic_min, 6)
        self.assertGreaterEqual(ao_min, 24)
        self.assertGreaterEqual(alpha_min, 24)
        self.assertLessEqual(alpha_max, 224)

    def test_generate_environment_mask_complex_mode_channel_order_matches_rmaos_contract(self) -> None:
        environment_mask = generate_environment_mask(_flat_dark_image(), strength=2.2, mode="complex")
        roughness, metallic, ao, specular_height = environment_mask.split()
        self.assertGreater(ImageStat.Stat(roughness).mean[0], ImageStat.Stat(metallic).mean[0])
        self.assertGreater(ImageStat.Stat(ao).mean[0], ImageStat.Stat(roughness).mean[0])
        self.assertGreater(ImageStat.Stat(specular_height).mean[0], ImageStat.Stat(metallic).mean[0])

    def test_generate_environment_mask_for_workflow_enb_uses_reflection_gloss_metal_height_layout(self) -> None:
        environment_mask = generate_environment_mask_for_workflow(
            _flat_dark_image(),
            strength=2.2,
            mode="complex",
            complex_workflow="enb",
        )
        reflection, glossiness, metalness, height = environment_mask.split()
        self.assertGreater(ImageStat.Stat(glossiness).mean[0], ImageStat.Stat(reflection).mean[0])
        self.assertGreater(ImageStat.Stat(reflection).mean[0], ImageStat.Stat(metalness).mean[0])
        self.assertGreater(ImageStat.Stat(height).mean[0], ImageStat.Stat(metalness).mean[0])

    def test_resolve_env_mask_complex_workflow_prefers_render_profile(self) -> None:
        self.assertEqual(
            resolve_env_mask_complex_workflow(env_mask_mode="complex", complex_format="cm", render_profile="enb"),
            "enb",
        )
        self.assertEqual(
            resolve_env_mask_complex_workflow(env_mask_mode="complex", complex_format="msn", render_profile="truepbr"),
            "truepbr",
        )

    def test_resolve_env_mask_complex_workflow_falls_back_to_complex_format(self) -> None:
        self.assertEqual(
            resolve_env_mask_complex_workflow(env_mask_mode="complex", complex_format="msn", render_profile="auto"),
            "enb",
        )
        self.assertEqual(
            resolve_env_mask_complex_workflow(env_mask_mode="complex", complex_format="cm", render_profile="auto"),
            "truepbr",
        )
        self.assertEqual(
            resolve_env_mask_complex_workflow(
                env_mask_mode="complex",
                complex_format="msn",
                render_profile="auto",
                include_complex=False,
            ),
            "enb",
        )

    def test_prepare_preview_source_downscales_large_images(self) -> None:
        source = Image.new("RGB", (4096, 2048), color=(64, 96, 128))
        preview = prepare_preview_source(source, max_dimension=1024)
        self.assertEqual(preview.mode, "RGB")
        self.assertLessEqual(max(preview.size), 1024)

    def test_get_preview_size_limits_returns_expected_preset_sizes(self) -> None:
        self.assertEqual(get_preview_size_limits("XS"), (160, 120))
        self.assertEqual(get_preview_size_limits("Small"), (240, 180))
        self.assertEqual(get_preview_size_limits("Medium"), (340, 250))
        self.assertEqual(get_preview_size_limits("Large"), (460, 340))
        self.assertEqual(get_preview_size_limits("XL"), (620, 460))

    def test_get_preview_size_limits_falls_back_to_medium_for_unknown_values(self) -> None:
        self.assertEqual(get_preview_size_limits("Unknown"), (340, 250))

    def test_generate_preview_outputs_only_includes_requested_outputs(self) -> None:
        outputs = generate_preview_outputs(
            _sample_image(),
            normal_strength=2.0,
            parallax_strength=1.35,
            glow_threshold=190,
            environment_mask_strength=1.2,
            complex_strength=1.15,
            specular_strength=1.15,
            complex_format="msn",
            include_diffuse=True,
            include_normal=False,
            include_parallax=True,
            include_glow=False,
            include_environment_mask=False,
            include_complex=False,
        )
        self.assertEqual(set(outputs.keys()), {"diffuse", "parallax"})

    def test_generate_preview_outputs_supports_complex_formats(self) -> None:
        msn_outputs = generate_preview_outputs(
            _sample_image(),
            normal_strength=2.0,
            parallax_strength=1.35,
            glow_threshold=190,
            environment_mask_strength=1.2,
            complex_strength=1.15,
            specular_strength=1.15,
            complex_format="msn",
            include_diffuse=False,
            include_normal=False,
            include_parallax=False,
            include_glow=False,
            include_environment_mask=False,
            include_complex=True,
        )
        cm_outputs = generate_preview_outputs(
            _sample_image(),
            normal_strength=2.0,
            parallax_strength=1.35,
            glow_threshold=190,
            environment_mask_strength=1.2,
            complex_strength=1.15,
            specular_strength=1.15,
            complex_format="cm",
            include_diffuse=False,
            include_normal=False,
            include_parallax=False,
            include_glow=False,
            include_environment_mask=False,
            include_complex=True,
        )
        self.assertEqual(msn_outputs["complex_material"].mode, "RGBA")
        self.assertEqual(cm_outputs["complex_material"].mode, "RGBA")

    def test_build_complex_preview_image_msn_visualizes_rgb_and_alpha_side_by_side(self) -> None:
        source = _sample_image()
        msn = generate_msn(source)
        preview = build_complex_preview_image(msn, complex_format="msn")
        self.assertEqual(preview.mode, "RGB")
        self.assertEqual(preview.size, (source.width * 2 + 2, source.height))
        left_sample = preview.getpixel((0, 0))
        right_sample = preview.getpixel((source.width + 2, 0))
        self.assertNotEqual(left_sample, right_sample)

    def test_build_complex_preview_image_cm_returns_original_image(self) -> None:
        cm = generate_complex_material(_sample_image())
        preview = build_complex_preview_image(cm, complex_format="cm")
        self.assertIs(preview, cm)

    def test_generate_preview_outputs_defaults_environment_mask_to_standard_skyrim_se(self) -> None:
        outputs = generate_preview_outputs(
            _sample_image(),
            normal_strength=2.0,
            parallax_strength=1.35,
            glow_threshold=190,
            environment_mask_strength=1.2,
            complex_strength=1.15,
            specular_strength=1.15,
            complex_format="msn",
            include_diffuse=False,
            include_normal=False,
            include_parallax=False,
            include_glow=False,
            include_environment_mask=True,
            include_complex=False,
        )
        self.assertEqual(outputs["environment_mask"].mode, "L")

    def test_generate_glow_produces_graded_values_above_threshold(self) -> None:
        glow = generate_glow(_vertical_gradient_image(), threshold=180)
        values = set(glow.tobytes())
        self.assertGreater(len(values), 2)

    def test_apply_recommendations_by_auto_flags_applies_only_checked_values(self) -> None:
        current = {
            "normal_strength": 1.5,
            "parallax_strength": 1.3,
            "glow_threshold": 180,
            "environment_mask_strength": 1.2,
        }
        recommended = {
            "normal_strength": 2.4,
            "parallax_strength": 2.0,
            "glow_threshold": 210,
            "environment_mask_strength": 1.8,
        }
        auto_flags = {
            "normal_strength": True,
            "parallax_strength": False,
            "glow_threshold": True,
            "environment_mask_strength": False,
        }
        merged = apply_recommendations_by_auto_flags(current=current, recommended=recommended, auto_flags=auto_flags)
        self.assertEqual(float(merged["normal_strength"]), 2.4)
        self.assertEqual(float(merged["parallax_strength"]), 1.3)
        self.assertEqual(int(merged["glow_threshold"]), 210)
        self.assertEqual(float(merged["environment_mask_strength"]), 1.2)

    def test_build_output_paths_uses_skyrim_default_names_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            diffuse_path, parallax_path = build_output_paths(
                input_path=input_path,
                output_dir=temp_path / "out",
                diffuse_name=None,
                parallax_name=None,
            )

            self.assertEqual(diffuse_path.name, "brick.dds")
            self.assertEqual(parallax_path.name, "brick_p.dds")

    def test_build_output_paths_use_dds_extension_even_for_png_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            input_path.write_bytes(b"stub")

            diffuse_path, parallax_path = build_output_paths(
                input_path=input_path,
                output_dir=temp_path / "out",
                diffuse_name=None,
                parallax_name=None,
            )

            self.assertEqual(diffuse_path.name, "brick.dds")
            self.assertEqual(parallax_path.name, "brick_p.dds")

    def test_custom_output_paths_preserve_textures_subfolders_for_skyrim_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "mod" / "textures" / "architecture" / "stone" / "brick.dds"
            input_path.parent.mkdir(parents=True)
            input_path.write_bytes(b"stub")
            output_root = temp_path / "out"

            diffuse_path, parallax_path = build_output_paths(input_path=input_path, output_dir=output_root)
            normal_path = build_normal_output_path(input_path=input_path, output_dir=output_root)
            environment_mask_path = build_environment_mask_output_path(input_path=input_path, output_dir=output_root)
            complex_path = build_complex_output_path(input_path=input_path, output_dir=output_root, complex_format="msn")

            self.assertEqual(diffuse_path.relative_to(output_root).as_posix(), "textures/architecture/stone/brick.dds")
            self.assertEqual(parallax_path.relative_to(output_root).as_posix(), "textures/architecture/stone/brick_p.dds")
            self.assertEqual(normal_path.relative_to(output_root).as_posix(), "textures/architecture/stone/brick_n.dds")
            self.assertEqual(environment_mask_path.relative_to(output_root).as_posix(), "textures/architecture/stone/brick_m.dds")
            self.assertEqual(complex_path.relative_to(output_root).as_posix(), "textures/architecture/stone/brick_msn.dds")

    def test_build_output_paths_accepts_omitted_name_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            diffuse_path, parallax_path = build_output_paths(
                input_path=input_path,
                output_dir=temp_path / "out",
            )

            self.assertEqual(diffuse_path.name, "brick.dds")
            self.assertEqual(parallax_path.name, "brick_p.dds")

    def test_build_normal_output_path_uses_default_name_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            normal_path = build_normal_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                normal_name=None,
            )

            self.assertEqual(normal_path.name, "brick_n.dds")

    def test_build_glow_output_path_uses_default_name_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            glow_path = build_glow_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                glow_name=None,
            )

            self.assertEqual(glow_path.name, "brick_em.dds")

    def test_build_environment_mask_output_path_uses_standard_default_name_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            environment_mask_path = build_environment_mask_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                environment_mask_name=None,
                env_mask_mode="standard",
            )

            self.assertEqual(environment_mask_path.name, "brick_m.dds")

    def test_build_environment_mask_output_path_uses_m_name_for_enb_complex_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            environment_mask_path = build_environment_mask_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                environment_mask_name=None,
                env_mask_mode="complex",
                complex_format="msn",
                render_profile="enb",
            )

            self.assertEqual(environment_mask_path.name, "brick_m.dds")

    def test_build_environment_mask_output_path_keeps_m_name_for_truepbr_complex_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            environment_mask_path = build_environment_mask_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                environment_mask_name=None,
                env_mask_mode="complex",
                complex_format="cm",
                render_profile="truepbr",
            )

            self.assertEqual(environment_mask_path.name, "brick_m.dds")

    def test_build_rmaos_output_path_uses_rmaos_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            rmaos_path = build_rmaos_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                rmaos_name=None,
            )

            self.assertEqual(rmaos_path.name, "brick_rmaos.dds")

    def test_build_complex_output_path_uses_msn_default_name_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            complex_path = build_complex_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                complex_name=None,
                complex_format="msn",
            )

            self.assertEqual(complex_path.name, "brick_msn.dds")

    def test_build_complex_output_path_supports_cm_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            complex_path = build_complex_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                complex_name=None,
                complex_format="cm",
            )

            self.assertEqual(complex_path.name, "brick_cm.dds")

    def test_build_single_output_paths_accept_omitted_name_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")
            output_dir = temp_path / "out"

            normal_path = build_normal_output_path(input_path=input_path, output_dir=output_dir)
            glow_path = build_glow_output_path(input_path=input_path, output_dir=output_dir)
            environment_mask_path = build_environment_mask_output_path(input_path=input_path, output_dir=output_dir)
            complex_path = build_complex_output_path(input_path=input_path, output_dir=output_dir, complex_format="msn")

            self.assertEqual(normal_path.name, "brick_n.dds")
            self.assertEqual(glow_path.name, "brick_em.dds")
            self.assertEqual(environment_mask_path.name, "brick_m.dds")
            self.assertEqual(complex_path.name, "brick_msn.dds")

    def test_collect_source_textures_from_directory_skips_generated_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for name in (
                "brick.dds",
                "stone_wall.dds",
                "brick_n.dds",
                "brick_p.dds",
                "brick_msn.dds",
                "brick_c.dds",
                "brick_rmaos.dds",
                "brick_em.dds",
                "brick_emissive.dds",
                "brick_s.dds",
                "brick_sk.dds",
                "preview.png",
            ):
                (temp_path / name).write_bytes(b"stub")

            collected = collect_source_textures(temp_path)

            self.assertEqual([path.name for path in collected], ["brick.dds", "stone_wall.dds"])

    def test_detect_mod_manager_context_reads_mo2_profile_and_loaded_texture_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            instance_root = temp_path / "MO2"
            textures_dir = instance_root / "mods" / "Texture Pack" / "textures"
            meshes_dir = instance_root / "mods" / "Texture Pack" / "meshes"
            tool_dir = instance_root / "mods" / "Skyrim Texture Generator"
            profile_dir = instance_root / "profiles" / "Default"
            textures_dir.mkdir(parents=True)
            meshes_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)
            profile_dir.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+Texture Pack\n-Disabled Mod\n", encoding="utf-8")
            (profile_dir / "plugins.txt").write_text("*Skyrim.esm\n*CommunityShaders.esp\n", encoding="utf-8")
            (profile_dir / "loadorder.txt").write_text("Skyrim.esm\nCommunityShaders.esp\n", encoding="utf-8")

            context = detect_mod_manager_context(
                {"MO_PROFILE": "Default"},
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.manager, "Mod Organizer 2")
            self.assertEqual(context.profile_name, "Default")
            self.assertEqual(context.loaded_mods, ("Texture Pack",))
            self.assertEqual(context.enabled_plugins, ("Skyrim.esm", "CommunityShaders.esp"))
            self.assertEqual(context.load_order, ("Skyrim.esm", "CommunityShaders.esp"))
            self.assertEqual(context.loaded_texture_dirs, (textures_dir.resolve(),))
            self.assertEqual(context.loaded_mesh_dirs, (meshes_dir.resolve(),))
            self.assertEqual(context.output_dir, (instance_root / "overwrite"))
            self.assertIn("plugin(s)", context.summary)
            self.assertIn("load-order", context.summary)

    def test_detect_mod_manager_context_detects_body_and_skeleton_hints(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            instance_root = temp_path / "MO2"
            textures_dir = instance_root / "mods" / "CBBE 3BA" / "textures"
            tool_dir = instance_root / "mods" / "Skyrim Texture Generator"
            profile_dir = instance_root / "profiles" / "Default"
            textures_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)
            profile_dir.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+CBBE 3BA\n+XPMSSE\n", encoding="utf-8")
            (profile_dir / "plugins.txt").write_text("*Skyrim.esm\n", encoding="utf-8")
            (profile_dir / "loadorder.txt").write_text("Skyrim.esm\n", encoding="utf-8")

            context = detect_mod_manager_context(
                {"MO_PROFILE": "Default"},
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.detected_body_profiles, ("CBBE", "3BA"))
            self.assertEqual(context.detected_skeleton_profiles, ("XPMSSE",))
            self.assertIn("body hints CBBE, 3BA", context.summary)
            self.assertIn("skeleton hints XPMSSE", context.summary)

    def test_detect_mod_manager_context_reads_vortex_profile_and_staging_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            appdata_dir = temp_path / "AppData" / "Roaming"
            profile_dir = appdata_dir / "Vortex" / "skyrimse" / "profiles" / "Main"
            profile_dir.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+Texture Pack\n", encoding="utf-8")
            (profile_dir / "plugins.txt").write_text("*Skyrim.esm\n*PBRNifPatcher.esp\n", encoding="utf-8")
            (profile_dir / "loadorder.txt").write_text("Skyrim.esm\nPBRNifPatcher.esp\n", encoding="utf-8")

            staging_root = temp_path / "Vortex Mods" / "skyrimse"
            textures_dir = staging_root / "Texture Pack" / "textures"
            meshes_dir = staging_root / "Texture Pack" / "meshes"
            tool_dir = staging_root / "Skyrim Texture Generator"
            textures_dir.mkdir(parents=True)
            meshes_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)

            context = detect_mod_manager_context(
                {
                    "APPDATA": str(appdata_dir),
                    "VORTEX_PROFILE": "Main",
                },
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.manager, "Vortex")
            self.assertEqual(context.profile_name, "Main")
            self.assertEqual(context.loaded_mods, ("Texture Pack",))
            self.assertEqual(context.enabled_plugins, ("Skyrim.esm", "PBRNifPatcher.esp"))
            self.assertEqual(context.load_order, ("Skyrim.esm", "PBRNifPatcher.esp"))
            self.assertEqual(context.loaded_texture_dirs, (textures_dir.resolve(),))
            self.assertEqual(context.loaded_mesh_dirs, (meshes_dir.resolve(),))
            self.assertEqual(context.staging_root, staging_root.resolve())
            self.assertEqual(context.output_dir, (tool_dir / "generated_textures").resolve())

    def test_detect_mod_manager_context_vortex_falls_back_to_staging_scan_when_mod_names_do_not_match_folder_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            appdata_dir = temp_path / "AppData" / "Roaming"
            profile_dir = appdata_dir / "Vortex" / "skyrimse" / "profiles" / "Main"
            profile_dir.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+Texture Pack Pretty Name\n", encoding="utf-8")

            staging_root = temp_path / "Vortex Mods" / "skyrimse"
            textures_dir = staging_root / "Texture Pack-1234" / "textures"
            meshes_dir = staging_root / "Texture Pack-1234" / "meshes"
            tool_dir = staging_root / "Skyrim Texture Generator"
            textures_dir.mkdir(parents=True)
            meshes_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)

            context = detect_mod_manager_context(
                {
                    "APPDATA": str(appdata_dir),
                    "VORTEX_PROFILE": "Main",
                },
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.manager, "Vortex")
            self.assertEqual(context.loaded_mods, ("Texture Pack Pretty Name",))
            self.assertEqual(context.loaded_texture_dirs, (textures_dir.resolve(),))
            self.assertEqual(context.loaded_mesh_dirs, (meshes_dir.resolve(),))

    def test_detect_mod_manager_context_includes_mo2_overwrite_dirs_and_game_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            instance_root = temp_path / "MO2"
            textures_dir = instance_root / "mods" / "Texture Pack" / "textures"
            meshes_dir = instance_root / "mods" / "Texture Pack" / "meshes"
            overwrite_textures_dir = instance_root / "overwrite" / "textures"
            overwrite_meshes_dir = instance_root / "overwrite" / "meshes"
            tool_dir = instance_root / "mods" / "Skyrim Texture Generator"
            profile_dir = instance_root / "profiles" / "Default"
            game_root = temp_path / "SkyrimSE"
            textures_dir.mkdir(parents=True)
            meshes_dir.mkdir(parents=True)
            overwrite_textures_dir.mkdir(parents=True)
            overwrite_meshes_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)
            profile_dir.mkdir(parents=True)
            game_root.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+Texture Pack\n", encoding="utf-8")

            context = detect_mod_manager_context(
                {"MO_PROFILE": "Default", "MO2_GAME_PATH": str(game_root)},
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.game_root, game_root)
            self.assertEqual(
                context.loaded_texture_dirs,
                (overwrite_textures_dir.resolve(), textures_dir.resolve()),
            )
            self.assertEqual(
                context.loaded_mesh_dirs,
                (overwrite_meshes_dir.resolve(), meshes_dir.resolve()),
            )

    def test_find_related_nif_files_for_texture_matches_family_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            nif_path = temp_path / "meshes" / "sign01.nif"
            nif_path.parent.mkdir(parents=True)
            nif_path.write_bytes(b"")
            source = temp_path / "textures" / "sign01_d.dds"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"")

            class _FakeInfo:
                def __init__(self, texture_paths: dict[int, str]) -> None:
                    self.texture_paths = texture_paths

            related = find_related_nif_files_for_texture(
                source,
                candidate_roots=(nif_path.parent,),
                nif_info_provider=lambda _: [_FakeInfo({0: "textures\\sign01.dds"})],
            )

            self.assertEqual(related, (nif_path.resolve(),))

    def test_find_related_nif_files_for_texture_matches_alias_family_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            nif_path = temp_path / "meshes" / "sign01.nif"
            nif_path.parent.mkdir(parents=True)
            nif_path.write_bytes(b"")
            source = temp_path / "textures" / "sign01_envmask.dds"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"")

            class _FakeInfo:
                def __init__(self, texture_paths: dict[int, str]) -> None:
                    self.texture_paths = texture_paths

            related = find_related_nif_files_for_texture(
                source,
                candidate_roots=(nif_path.parent,),
                nif_info_provider=lambda _: [_FakeInfo({0: "textures\\sign01.dds"})],
            )

            self.assertEqual(related, (nif_path.resolve(),))

    def test_find_related_nif_files_for_texture_falls_back_to_nif_name_when_scan_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            nif_path = temp_path / "meshes" / "sign01.nif"
            nif_path.parent.mkdir(parents=True)
            nif_path.write_bytes(b"")
            source = temp_path / "textures" / "sign01_d.dds"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"")

            related = find_related_nif_files_for_texture(
                source,
                candidate_roots=(nif_path.parent,),
                nif_info_provider=lambda _: (_ for _ in ()).throw(ValueError("parse failed")),
            )

            self.assertEqual(related, (nif_path.resolve(),))

    def test_find_related_nif_files_for_texture_filename_fallback_avoids_unrelated_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            nif_path = temp_path / "meshes" / "different_mesh.nif"
            nif_path.parent.mkdir(parents=True)
            nif_path.write_bytes(b"")
            source = temp_path / "textures" / "sign01_d.dds"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"")

            related = find_related_nif_files_for_texture(
                source,
                candidate_roots=(nif_path.parent,),
                nif_info_provider=lambda _: (_ for _ in ()).throw(ValueError("parse failed")),
            )

            self.assertEqual(related, ())

    def test_build_nif_patch_options_for_generated_outputs_prefers_msn_for_complex_parallax(self) -> None:
        outputs = {
            "parallax": Path("/tmp/textures/sign01_p.dds"),
            "complex_material": Path("/tmp/textures/sign01_msn.dds"),
            "environment_mask": Path("/tmp/textures/sign01_m.dds"),
        }

        options = build_nif_patch_options_for_generated_outputs(
            None,
            outputs,
            complex_format="msn",
            env_mask_mode="complex",
            parallax_mode="occlusion",
            parallax_scale=4.0,
            render_profile="community_shaders",
        )

        self.assertTrue(options.enable_parallax)
        self.assertTrue(options.enable_pom)
        self.assertTrue(options.enable_env_mapping)
        self.assertTrue(options.force_shader_type_3)
        self.assertEqual(options.normal_texture_path, "textures\\sign01_msn.dds")
        self.assertEqual(options.parallax_texture_path, "textures\\sign01_p.dds")
        self.assertEqual(options.env_mask_texture_path, "textures\\sign01_m.dds")
        self.assertEqual(options.parallax_scale, 4.0)

    def test_build_nif_patch_options_render_profile_vanilla_no_type3_upgrade(self) -> None:
        outputs = {"parallax": Path("/tmp/textures/brick_p.dds")}
        for profile in ("vanilla", "auto"):
            with self.subTest(profile=profile):
                options = build_nif_patch_options_for_generated_outputs(
                    None,
                    outputs,
                    complex_format="msn",
                    env_mask_mode="standard",
                    parallax_mode="standard",
                    parallax_scale=2.0,
                    render_profile=profile,
                )
                self.assertFalse(
                    options.force_shader_type_3,
                    msg=f"render_profile={profile!r} should not force shader type-3 upgrade",
                )
                self.assertTrue(options.enable_parallax)

    def test_build_nif_patch_options_render_profile_community_shaders_enables_type3(self) -> None:
        outputs = {"parallax": Path("/tmp/textures/brick_p.dds")}
        options = build_nif_patch_options_for_generated_outputs(
            None,
            outputs,
            complex_format="msn",
            env_mask_mode="standard",
            parallax_mode="standard",
            parallax_scale=2.0,
            render_profile="community_shaders",
        )
        self.assertTrue(options.force_shader_type_3)
        self.assertTrue(options.enable_parallax)

    def test_build_nif_patch_options_render_profile_enb_enables_type3(self) -> None:
        outputs = {"parallax": Path("/tmp/textures/brick_p.dds")}
        options = build_nif_patch_options_for_generated_outputs(
            None,
            outputs,
            complex_format="msn",
            env_mask_mode="standard",
            parallax_mode="standard",
            parallax_scale=2.0,
            render_profile="enb",
        )
        self.assertTrue(options.force_shader_type_3)
        self.assertTrue(options.enable_parallax)

    def test_build_nif_patch_options_render_profile_truepbr_enables_type3(self) -> None:
        outputs = {"parallax": Path("/tmp/textures/brick_p.dds")}
        options = build_nif_patch_options_for_generated_outputs(
            None,
            outputs,
            complex_format="cm",
            env_mask_mode="complex",
            parallax_mode="standard",
            parallax_scale=2.0,
            render_profile="truepbr",
        )
        self.assertTrue(options.force_shader_type_3)
        self.assertTrue(options.enable_parallax)

    def test_build_nif_patch_options_no_parallax_never_forces_type3(self) -> None:
        for profile in ("vanilla", "community_shaders", "truepbr", "enb", "auto"):
            with self.subTest(profile=profile):
                options = build_nif_patch_options_for_generated_outputs(
                    None,
                    {},
                    complex_format="msn",
                    env_mask_mode="standard",
                    parallax_mode="standard",
                    parallax_scale=None,
                    render_profile=profile,
                )
                self.assertFalse(
                    options.force_shader_type_3,
                    msg=f"No parallax output — force_shader_type_3 must be False for {profile!r}",
                )

    def test_build_nif_patch_options_for_generated_outputs_falls_back_to_source_texture_resource_dir(self) -> None:
        source_texture = Path("/tmp/mod/textures/architecture/stone/brick.dds")
        outputs = {
            "parallax": Path("/tmp/generated/brick_p.dds"),
            "normal": Path("/tmp/generated/brick_n.dds"),
        }

        options = build_nif_patch_options_for_generated_outputs(
            source_texture,
            outputs,
            complex_format="msn",
            env_mask_mode="standard",
            parallax_mode="standard",
            parallax_scale=2.0,
        )

        self.assertEqual(options.parallax_texture_path, "textures\\architecture\\stone\\brick_p.dds")
        self.assertEqual(options.normal_texture_path, "textures\\architecture\\stone\\brick_n.dds")

    def test_build_nif_patch_options_for_generated_outputs_coerces_non_dds_slots_to_dds(self) -> None:
        source_texture = Path("/tmp/mod/textures/architecture/stone/brick.dds")
        outputs = {
            "parallax": Path("/tmp/generated/brick_p.png"),
            "normal": Path("/tmp/generated/brick_n.png"),
            "environment_mask": Path("/tmp/generated/brick_m.png"),
        }

        options = build_nif_patch_options_for_generated_outputs(
            source_texture,
            outputs,
            complex_format="msn",
            env_mask_mode="standard",
            parallax_mode="standard",
            parallax_scale=2.0,
        )

        self.assertEqual(options.parallax_texture_path, "textures\\architecture\\stone\\brick_p.dds")
        self.assertEqual(options.normal_texture_path, "textures\\architecture\\stone\\brick_n.dds")
        self.assertEqual(options.env_mask_texture_path, "textures\\architecture\\stone\\brick_m.dds")

    def test_build_nif_patch_options_respects_disabled_parallax_output_setting(self) -> None:
        options = build_nif_patch_options_for_generated_outputs(
            None,
            {},
            complex_format="msn",
            env_mask_mode="standard",
            parallax_mode="standard",
            parallax_scale=2.0,
            include_parallax=False,
            include_environment_mask=True,
        )
        self.assertTrue(options.disable_parallax)
        self.assertTrue(options.clear_parallax_texture_path)
        self.assertFalse(options.enable_parallax)
        self.assertFalse(options.enable_pom)

    def test_build_nif_patch_options_respects_disabled_env_mask_output_setting(self) -> None:
        options = build_nif_patch_options_for_generated_outputs(
            None,
            {},
            complex_format="msn",
            env_mask_mode="standard",
            parallax_mode="standard",
            parallax_scale=2.0,
            include_parallax=True,
            include_environment_mask=False,
        )
        self.assertTrue(options.disable_env_mapping)
        self.assertTrue(options.clear_env_mask_texture_path)
        self.assertFalse(options.enable_env_mapping)

    def test_auto_patch_related_nifs_for_texture_patches_all_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "textures" / "sign01.dds"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"")
            nif_path = temp_path / "meshes" / "sign01.nif"
            nif_path.parent.mkdir(parents=True)
            nif_path.write_bytes(b"")
            outputs = {"parallax": temp_path / "textures" / "sign01_p.dds"}
            outputs["parallax"].write_bytes(b"")

            with mock.patch("generate_textures.find_related_nif_files_for_texture", return_value=(nif_path,)), mock.patch(
                "generate_textures.patch_nif", return_value=mock.Mock(success=True)
            ) as patch_nif_mock:
                results = auto_patch_related_nifs_for_texture(
                    source,
                    outputs,
                    output_dir=None,
                    manager_context=None,
                    complex_format="msn",
                    env_mask_mode="standard",
                    parallax_mode="standard",
                    parallax_scale=2.0,
                    include_parallax=True,
                    include_environment_mask=False,
                )

            self.assertEqual(len(results), 1)
            patch_nif_mock.assert_called_once()
            passed_options = patch_nif_mock.call_args.args[1]
            self.assertFalse(bool(passed_options.disable_parallax))
            self.assertTrue(bool(passed_options.disable_env_mapping))

    def test_run_with_options_requires_at_least_one_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            _sample_image().save(input_path)

            with self.assertRaises(ValueError):
                run_with_options(
                    input_file=input_path,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                )

    def test_run_with_options_writes_openable_dds_files_for_png_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            outputs = run_with_options(
                input_file=input_path,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=True,
                include_parallax=True,
                include_glow=True,
                include_environment_mask=True,
                include_complex=True,
            )

            expected_names = {
                "diffuse": "brick.dds",
                "normal": "brick_n.dds",
                "parallax": "brick_p.dds",
                "glow": "brick_em.dds",
                "environment_mask": "brick_m.dds",
                "complex_material": "brick_msn.dds",
            }
            self.assertEqual({key: value.name for key, value in outputs.items()}, expected_names)

            for path in outputs.values():
                self.assertEqual(path.suffix.lower(), ".dds")
                with Image.open(path) as generated:
                    generated.load()
                    self.assertEqual(generated.size, (8, 8))

    def test_run_with_options_writes_msn_with_alpha_when_complex_format_is_msn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            outputs = run_with_options(
                input_file=input_path,
                output_dir=output_dir,
                include_diffuse=False,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=True,
                complex_format="msn",
            )

            with Image.open(outputs["complex_material"]) as generated:
                generated.load()
                self.assertIn(generated.mode, ("RGBA", "RGB"))

    def test_run_batch_with_options_processes_only_original_dds_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            input_dir.mkdir()

            for name in ("brick.dds", "stone_wall.dds", "brick_n.dds", "brick_p.dds", "stone_wall_msn.dds"):
                _sample_image().save(input_dir / name, format="DDS", pixel_format="DXT5")

            outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
            )

            self.assertEqual(sorted(path.name for path in outputs.keys()), ["brick.dds", "stone_wall.dds"])
            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), ["brick.dds", "stone_wall.dds"])

    def test_collect_source_textures_scans_subfolders_and_skips_generated_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "input"
            nested = root / "nested"
            deeper = nested / "deeper"
            deeper.mkdir(parents=True)

            _sample_image().save(root / "top.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(nested / "brick.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_n.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_c.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_rmaos.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_em.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_emissive.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_sk.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_specular.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_height.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_envmask.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_roughness.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_metalness.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_ao.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(deeper / "stone_orm.dds", format="DDS", pixel_format="DXT5")

            discovered = collect_source_textures(root)
            self.assertEqual(
                sorted(path.relative_to(root).as_posix() for path in discovered),
                ["nested/brick.dds", "nested/deeper/stone.dds", "top.dds"],
            )

    def test_select_generation_context_source_prefers_first_selected_input(self) -> None:
        folder_path = Path("textures/architecture")
        selected = [
            Path("textures/architecture/stone.dds"),
            Path("textures/architecture/brick.dds"),
        ]
        self.assertEqual(select_generation_context_source(folder_path, selected), selected[0])

    def test_select_generation_context_source_falls_back_to_input_path(self) -> None:
        input_path = Path("textures/architecture/stone.dds")
        self.assertEqual(select_generation_context_source(input_path, []), input_path)

    def test_run_batch_with_options_processes_subfolder_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            nested = input_dir / "nested"
            output_dir = temp_path / "out"
            nested.mkdir(parents=True)

            _sample_image().save(input_dir / "top.dds", format="DDS", pixel_format="DXT5")
            _sample_image().save(nested / "inner.dds", format="DDS", pixel_format="DXT5")

            outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
                batch_workers=1,
            )

            self.assertEqual(sorted(path.name for path in outputs.keys()), ["inner.dds", "top.dds"])
            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), ["inner.dds", "top.dds"])

    def test_run_batch_with_options_can_continue_on_file_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            input_dir.mkdir()

            _sample_image().save(input_dir / "good.dds", format="DDS", pixel_format="DXT5")
            (input_dir / "bad.dds").write_bytes(b"this is not a valid dds")
            errors: list[tuple[str, str]] = []

            outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
                continue_on_error=True,
                error_callback=lambda _index, _total, current, exc: errors.append((current.name, str(exc))),
            )

            self.assertEqual(sorted(path.name for path in outputs.keys()), ["good.dds"])
            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), ["good.dds"])
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0][0], "bad.dds")

    def test_run_batch_with_options_supports_parallel_workers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "out"
            input_dir.mkdir()

            for name in ("a.dds", "b.dds", "c.dds", "d.dds"):
                _sample_image().save(input_dir / name, format="DDS", pixel_format="DXT5")

            outputs = run_batch_with_options(
                input_path=input_dir,
                output_dir=output_dir,
                include_diffuse=True,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
                batch_workers=2,
            )

            self.assertEqual(sorted(path.name for path in outputs.keys()), ["a.dds", "b.dds", "c.dds", "d.dds"])
            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), ["a.dds", "b.dds", "c.dds", "d.dds"])

    def test_create_panda_icon_image_returns_rgba_square(self) -> None:
        for size in (16, 32, 64, 128, 256):
            icon = _create_panda_icon_image(size=size)
            self.assertEqual(icon.mode, "RGBA")
            self.assertEqual(icon.size, (size, size))

    def test_create_panda_icon_image_is_not_fully_transparent(self) -> None:
        icon = _create_panda_icon_image(size=128)
        opaque_count = 0
        for x in range(icon.width):
            for y in range(icon.height):
                if icon.getpixel((x, y))[3] == 255:
                    opaque_count += 1
        self.assertGreater(opaque_count, 0)

    def test_patreon_url_is_correct(self) -> None:
        self.assertEqual(PATREON_URL, "https://www.patreon.com/cw/DeadOnTheInside")

    def test_parse_args_accepts_pbr_material_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = Path(temp_dir) / "brick.dds"
            input_file.write_bytes(b"dds")
            with mock.patch("sys.argv", ["generate_textures.py", str(input_file), "--pbr-material"]):
                args = parse_args()
        self.assertTrue(args.pbr_material)

    def test_parse_args_accepts_render_profile_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = Path(temp_dir) / "brick.dds"
            input_file.write_bytes(b"dds")
            with mock.patch("sys.argv", ["generate_textures.py", str(input_file), "--render-profile", "enb"]):
                args = parse_args()
        self.assertEqual(str(args.render_profile), "enb")

    def test_parse_args_accepts_extended_render_profile_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = Path(temp_dir) / "brick.dds"
            input_file.write_bytes(b"dds")
            with mock.patch("sys.argv", ["generate_textures.py", str(input_file), "--render-profile", "architecture"]):
                args = parse_args()
        self.assertEqual(str(args.render_profile), "architecture")

    def test_main_pbr_material_forces_complex_material_cm_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = Path(temp_dir) / "brick.dds"
            input_file.write_bytes(b"dds")
            args = mock.Mock(
                gui=False,
                input_file=input_file,
                output_dir=None,
                diffuse_name=None,
                normal_name=None,
                parallax_name=None,
                glow_name=None,
                environment_mask_name=None,
                complex_name=None,
                normal_strength=None,
                parallax_strength=None,
                glow_threshold=None,
                environment_mask_strength=None,
                complex_strength=None,
                specular_strength=None,
                complex_format="msn",
                environment_mask_mode="complex",
                emboss_mode=False,
                relief_mode=False,
                parallax_mode="occlusion",
                no_diffuse=False,
                no_normal=False,
                no_parallax=False,
                glow_map=False,
                environment_mask=False,
                complex_material=False,
                pbr_material=True,
                render_profile="auto",
            )
            with mock.patch("generate_textures.parse_args", return_value=args):
                with mock.patch("generate_textures.run_with_options", return_value={}) as run_with_options_mock:
                    exit_code = main()
        self.assertEqual(exit_code, 0)
        self.assertTrue(args.complex_material)
        self.assertEqual(args.complex_format, "cm")
        self.assertEqual(args.environment_mask_mode, "standard")
        self.assertEqual(args.parallax_mode, "standard")
        run_with_options_mock.assert_called_once()
        self.assertTrue(bool(run_with_options_mock.call_args.kwargs["include_complex"]))
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["complex_format"]), "cm")
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["render_profile"]), "auto")

    def test_main_passes_selected_render_profile_to_run_with_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = Path(temp_dir) / "brick.dds"
            input_file.write_bytes(b"dds")
            args = mock.Mock(
                gui=False,
                input_file=input_file,
                output_dir=None,
                diffuse_name=None,
                normal_name=None,
                parallax_name=None,
                glow_name=None,
                environment_mask_name=None,
                rmaos_name=None,
                complex_name=None,
                normal_strength=None,
                parallax_strength=None,
                glow_threshold=None,
                environment_mask_strength=None,
                rmaos_strength=None,
                complex_strength=None,
                specular_strength=None,
                complex_format="msn",
                environment_mask_mode="standard",
                emboss_mode=False,
                relief_mode=False,
                parallax_mode="standard",
                no_diffuse=False,
                no_normal=False,
                no_parallax=False,
                glow_map=False,
                environment_mask=False,
                rmaos=False,
                complex_material=False,
                pbr_material=False,
                render_profile="enb",
            )
            with mock.patch("generate_textures.parse_args", return_value=args):
                with mock.patch("generate_textures.run_with_options", return_value={}) as run_with_options_mock:
                    exit_code = main()
        self.assertEqual(exit_code, 0)
        run_with_options_mock.assert_called_once()
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["render_profile"]), "enb")
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["complex_format"]), "msn")
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["env_mask_mode"]), "complex")
        self.assertEqual(str(run_with_options_mock.call_args.kwargs["parallax_mode"]), "occlusion")

    def test_run_cli_handles_missing_gui_dependencies_without_traceback(self) -> None:
        with mock.patch("generate_textures.main", side_effect=RuntimeError("GUI dependencies are unavailable in this environment.")):
            with mock.patch("sys.stderr") as mock_stderr:
                exit_code = _run_cli()
        self.assertEqual(exit_code, 1)
        self.assertIn("GUI dependencies are unavailable", "".join(call.args[0] for call in mock_stderr.write.call_args_list))

    def test_run_cli_handles_generic_runtime_error(self) -> None:
        with mock.patch("generate_textures.main", side_effect=RuntimeError("boom")):
            with mock.patch("sys.stderr") as mock_stderr:
                exit_code = _run_cli()
        self.assertEqual(exit_code, 1)
        self.assertIn("Error: boom", "".join(call.args[0] for call in mock_stderr.write.call_args_list))

    def test_run_cli_reports_headless_gui_startup_failure(self) -> None:
        class FakeTclError(Exception):
            pass

        args = mock.Mock(gui=True, input_file=None)
        with mock.patch("generate_textures.parse_args", return_value=args):
            with mock.patch("generate_textures.GUI_AVAILABLE", True):
                with mock.patch("generate_textures.tk", mock.Mock(TclError=FakeTclError)):
                    with mock.patch("generate_textures.TextureGeneratorGUI", side_effect=FakeTclError("no display")):
                        with mock.patch("sys.stderr") as mock_stderr:
                            exit_code = _run_cli()
        self.assertEqual(exit_code, 1)
        self.assertIn("no desktop display", "".join(call.args[0] for call in mock_stderr.write.call_args_list))

    def test_generate_normal_raises_clear_error_for_buffer_size_mismatch(self) -> None:
        with mock.patch("generate_textures.np.frombuffer", return_value=np.zeros(1, dtype=np.uint8)):
            with self.assertRaisesRegex(RuntimeError, "Normal map red buffer size mismatch"):
                generate_normal(_detailed_bright_image())

    def test_generate_specular_raises_clear_error_for_buffer_size_mismatch(self) -> None:
        with mock.patch("generate_textures.np.frombuffer", return_value=np.zeros(1, dtype=np.uint8)):
            with self.assertRaisesRegex(RuntimeError, "Specular buffer size mismatch"):
                generate_specular(_detailed_bright_image())

    def test_to_dds_compatible_image_uses_rgb_for_dxt1(self) -> None:
        converted = _to_dds_compatible_image(Image.new("L", (4, 4), color=128), pixel_format="DXT1")
        self.assertEqual(converted.mode, "RGB")

    def test_to_dds_compatible_image_uses_rgba_for_dxt5(self) -> None:
        converted = _to_dds_compatible_image(Image.new("L", (4, 4), color=128), pixel_format="DXT5")
        self.assertEqual(converted.mode, "RGBA")

    def test_save_with_dds_fallback_returns_dds_path_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "stone.dds"

            def fake_save(_self, fp, format=None, **_kwargs):
                Path(fp).write_bytes(f"{format}".encode("utf-8"))

            with mock.patch.object(Image.Image, "save", autospec=True, side_effect=fake_save):
                saved_path = _save_with_dds_fallback(_sample_image(), target)

        self.assertEqual(saved_path, target)

    def test_save_with_dds_fallback_uses_rgb_for_dxt1_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "stone.dds"

            def fake_save(image_self, fp, format=None, **kwargs):
                self.assertEqual(format, "DDS")
                self.assertEqual(kwargs.get("pixel_format"), "DXT1")
                self.assertEqual(image_self.mode, "RGB")
                Path(fp).write_bytes(b"DDS")

            with mock.patch.object(Image.Image, "save", autospec=True, side_effect=fake_save):
                saved_path = _save_with_dds_fallback(
                    Image.new("L", (8, 8), color=120),
                    target,
                    preferred_pixel_formats=("DXT1",),
                )

        self.assertEqual(saved_path, target)

    def test_save_with_dds_fallback_returns_png_path_when_dds_save_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "stone.dds"

            def fake_save(_self, fp, format=None, **_kwargs):
                if format == "DDS":
                    raise OSError("dds unavailable")
                Path(fp).write_bytes(f"{format}".encode("utf-8"))

            with mock.patch.object(Image.Image, "save", autospec=True, side_effect=fake_save):
                saved_path = _save_with_dds_fallback(_sample_image(), target)

        self.assertEqual(saved_path, target.with_suffix(".png"))

    def test_save_with_dds_fallback_normalizes_and_deduplicates_preferred_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "stone.dds"
            attempted_formats: list[str] = []

            def fake_save(_self, fp, format=None, **kwargs):
                if format == "DDS":
                    pixel_format = str(kwargs.get("pixel_format"))
                    attempted_formats.append(pixel_format)
                    if pixel_format == "DXT1":
                        raise OSError("dxt1 unavailable")
                Path(fp).write_bytes(f"{format}".encode("utf-8"))

            with mock.patch.object(Image.Image, "save", autospec=True, side_effect=fake_save):
                saved_path = _save_with_dds_fallback(
                    _sample_image(),
                    target,
                    preferred_pixel_formats=(" dxt1 ", "DXT1", "dXt5"),
                )

        self.assertEqual(saved_path, target)
        self.assertEqual(attempted_formats, ["DXT1", "DXT5"])


    def test_generate_specular_no_black_holes_on_detailed_image(self) -> None:
        specular = generate_specular(_large_high_detail_image(), strength=2.0)
        self.assertEqual(specular.mode, "L")
        minimum, _ = specular.getextrema()
        self.assertGreaterEqual(minimum, 8)

    def test_generate_environment_mask_standard_mode_returns_l_same_size(self) -> None:
        env_mask = generate_environment_mask(_sample_image(), mode="standard")
        self.assertEqual(env_mask.mode, "L")
        self.assertEqual(env_mask.size, (8, 8))

    def test_generate_environment_mask_standard_mode_stays_above_floor(self) -> None:
        env_mask = generate_environment_mask(_flat_dark_image(), strength=2.2, mode="standard")
        minimum, _ = env_mask.getextrema()
        self.assertGreaterEqual(minimum, 8)

    def test_identify_skyrim_texture_role_normal_map(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/architecture/stonewall_n.dds"))
        self.assertEqual(result["role"], "normal")
        self.assertEqual(result["suffix"], "_n")
        self.assertIn("DirectX", result["notes"])

    def test_identify_skyrim_texture_role_parallax(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/landscape/dirt_p.dds"))
        self.assertEqual(result["role"], "parallax")
        self.assertEqual(result["suffix"], "_p")

    def test_identify_skyrim_texture_role_em_is_glow(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/effects/fire_em.dds"))
        self.assertEqual(result["role"], "glow")
        self.assertEqual(result["suffix"], "_em")
        self.assertIn("Slot 2", result["notes"])

    def test_identify_skyrim_texture_role_emis_is_glow_alias(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/effects/fire_emis.dds"))
        self.assertEqual(result["role"], "glow")
        self.assertEqual(result["suffix"], "_emis")
        self.assertIn("Slot 2", result["notes"])

    def test_identify_skyrim_texture_role_environment_mask(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_m.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_m")
        self.assertIn("Slot 5", result["notes"])

    def test_identify_skyrim_texture_role_rmaos_environment_mask(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_rmaos.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_rmaos")

    def test_identify_skyrim_texture_role_specular_alias_maps_to_environment_mask(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_specular.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_specular")
        self.assertIn("alias", result["description"].lower())

    def test_identify_skyrim_texture_role_roughness_alias_maps_to_environment_mask(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_roughness.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_roughness")
        self.assertIn("alias", result["description"].lower())

    def test_identify_skyrim_texture_role_ao_alias_maps_to_environment_mask(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_ao.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_ao")
        self.assertIn("alias", result["description"].lower())

    def test_identify_skyrim_texture_role_orm_alias_maps_to_rmaos_environment_mask(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_orm.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_orm")
        self.assertIn("alias", result["description"].lower())

    def test_identify_skyrim_texture_role_c_as_complex_material(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/architecture/brick_c.dds"))
        self.assertEqual(result["role"], "complex_material_cm")
        self.assertEqual(result["suffix"], "_c")

    def test_identify_skyrim_texture_role_diffuse_fallback(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/clutter/candle.dds"))
        self.assertEqual(result["role"], "diffuse")
        self.assertEqual(result["suffix"], "")

    def test_identify_skyrim_texture_role_path_hint(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/landscape/grass01.dds"))
        self.assertIn("landscape", result["hint"].lower())

    def test_identify_skyrim_texture_role_msn_is_enb_only(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/architecture/brick_msn.dds"))
        self.assertEqual(result["role"], "complex_material")
        self.assertIn("ENBSeries", result["notes"])

    def test_identify_skyrim_texture_role_rmaos_has_channel_notes(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_rmaos.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_rmaos")
        # Notes must describe the TruePBR RMAOS channel layout.
        self.assertIn("Roughness", result["notes"])
        self.assertIn("Metallic", result["notes"])
        self.assertIn("Ambient Occlusion", result["notes"])
        self.assertIn("TruePBR", result["notes"])

    def test_identify_skyrim_texture_role_cm_has_channel_notes(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/architecture/brick_cm.dds"))
        self.assertEqual(result["role"], "complex_material_cm")
        self.assertEqual(result["suffix"], "_cm")
        self.assertIn("Environment reflection amount", result["notes"])
        self.assertIn("Glossiness", result["notes"])
        self.assertIn("Metallic", result["notes"])
        self.assertIn("Extended Materials", result["notes"])

    def test_identify_skyrim_texture_role_c_has_channel_notes(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/architecture/brick_c.dds"))
        self.assertEqual(result["role"], "complex_material_cm")
        self.assertEqual(result["suffix"], "_c")
        self.assertIn("Environment reflection amount", result["notes"])
        self.assertIn("Glossiness", result["notes"])
        self.assertIn("Metallic", result["notes"])

    def test_identify_skyrim_texture_role_uppercase_C_is_complex_material(self) -> None:
        # _C.dds (uppercase) must be treated identically to _c.dds — stem is lowercased before lookup
        result = identify_skyrim_texture_role(Path("textures/architecture/brick_C.dds"))
        self.assertEqual(result["role"], "complex_material_cm")
        self.assertEqual(result["suffix"], "_c")

    def test_identify_skyrim_texture_role_infers_from_filename_tokens(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/architecture/wall_normalmap.dds"))
        self.assertEqual(result["role"], "normal")
        self.assertEqual(result["suffix"], "")
        self.assertIn("inferred", result["description"].lower())

    def test_run_with_options_env_mask_standard_mode_produces_l_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            outputs = run_with_options(
                input_file=input_path,
                output_dir=output_dir,
                include_diffuse=False,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=True,
                include_complex=False,
                env_mask_mode="standard",
            )

            self.assertIn("environment_mask", outputs)
            with Image.open(outputs["environment_mask"]) as generated:
                generated.load()
                # Standard mode must produce a greyscale-compatible output (L or RGBA
                # that decodes to a single channel — pillow DDS may store as RGBA).
                self.assertIn(generated.mode, ("L", "RGBA", "RGB"))

    def test_run_with_options_standard_env_mask_prefers_dxt1_then_dxt5(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=True,
                    include_complex=False,
                    env_mask_mode="standard",
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.kwargs["preferred_pixel_formats"], ("DXT1", "DXT5"))

    def test_run_with_options_normal_prefers_bc5_then_legacy_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=True,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.kwargs["preferred_pixel_formats"], ("BC5", "DXT5", "DXT3"))

    def test_run_with_options_parallax_prefers_dxt1_then_dxt5(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=True,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.kwargs["preferred_pixel_formats"], ("DXT1", "DXT5"))

    def test_preferred_dds_formats_for_opaque_diffuse_uses_bc7_then_dxt1(self) -> None:
        formats = _preferred_dds_formats_for_output("diffuse", Image.new("RGB", (4, 4), color=(10, 20, 30)))
        self.assertEqual(formats, ("BC7", "DXT1", "DXT5"))

    def test_preferred_dds_formats_for_alpha_diffuse_avoids_dxt1(self) -> None:
        image = Image.new("RGBA", (4, 4), color=(10, 20, 30, 255))
        image.putpixel((0, 0), (10, 20, 30, 80))
        formats = _preferred_dds_formats_for_output("diffuse", image)
        self.assertEqual(formats, ("BC7", "DXT5", "DXT3"))

    def test_run_with_options_complex_env_mask_uses_dxt5_and_defaults_to_m_for_enb_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=True,
                    include_complex=True,
                    env_mask_mode="complex",
                    complex_format="msn",
                    render_profile="enb",
                )

            self.assertEqual(save_mock.call_count, 2)
            env_calls = [call for call in save_mock.call_args_list if call.args[1].name.endswith("_m.dds")]
            self.assertEqual(len(env_calls), 1)
            self.assertEqual(env_calls[0].kwargs["preferred_pixel_formats"], ("BC7", "DXT5", "DXT3"))

    def test_run_with_options_truepbr_complex_env_mask_defaults_to_m_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=True,
                    include_complex=False,
                    env_mask_mode="complex",
                    complex_format="cm",
                    render_profile="truepbr",
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.kwargs["preferred_pixel_formats"], ("BC7", "DXT5", "DXT3"))
            self.assertEqual(save_mock.call_args.args[1].name, "brick_m.dds")

    def test_run_with_options_rmaos_writes_rmaos_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_rmaos=True,
                    include_complex=False,
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.kwargs["preferred_pixel_formats"], ("BC7", "DXT5", "DXT3"))
            self.assertEqual(save_mock.call_args.args[1].name, "brick_rmaos.dds")

    def test_run_with_options_rmaos_writes_json_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            outputs = run_with_options(
                input_file=input_path,
                output_dir=output_dir,
                include_diffuse=False,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_rmaos=True,
                include_complex=False,
            )

            self.assertIn("rmaos", outputs)
            self.assertIn("rmaos_json", outputs)
            self.assertTrue(outputs["rmaos_json"].exists())
            self.assertEqual(outputs["rmaos_json"].parent.name, "PBRNifPatcher")
            payload = json.loads(outputs["rmaos_json"].read_text(encoding="utf-8"))
            self.assertIsInstance(payload, list)
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["texture"], "brick")
            self.assertFalse(bool(payload[0]["parallax"]))
            self.assertFalse(bool(payload[0]["emissive"]))
            self.assertFalse(bool(payload[0]["subsurface"]))
            self.assertFalse(bool(payload[0]["subsurface_foliage"]))
            self.assertEqual(payload[0]["subsurface_color"], [1.0, 1.0, 1.0])
            self.assertAlmostEqual(float(payload[0]["subsurface_opacity"]), 1.0)
            self.assertAlmostEqual(float(payload[0]["specular_level"]), 0.04)

    def test_run_with_options_rmaos_writes_sidecar_next_to_textures_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "mod" / "textures" / "trees" / "treepineforestbarkcomp.dds"
            output_dir = temp_path / "generated"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            _sample_image().save(input_path)

            outputs = run_with_options(
                input_file=input_path,
                output_dir=output_dir,
                include_diffuse=False,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_rmaos=True,
                include_complex=False,
            )

            expected_sidecar = (
                output_dir
                / "PBRNifPatcher"
                / "trees"
                / "treepineforestbarkcomp_rmaos.json"
            )
            self.assertEqual(outputs["rmaos_json"], expected_sidecar)
            self.assertTrue(expected_sidecar.exists())
            payload = json.loads(expected_sidecar.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["texture"], "trees/treepineforestbarkcomp")
            self.assertFalse(bool(payload[0]["parallax"]))

    def test_run_with_options_ramos_alias_writes_json_with_family_texture_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            outputs = run_with_options(
                input_file=input_path,
                output_dir=output_dir,
                include_diffuse=False,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=False,
                include_rmaos=True,
                include_complex=False,
                rmaos_name="brick_ramos",
            )

            self.assertEqual(outputs["rmaos"].name, "brick_ramos.dds")
            payload = json.loads(outputs["rmaos_json"].read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["texture"], "brick")


class EmbossNormalTests(unittest.TestCase):
    def test_emboss_normal_returns_rgb_same_size(self) -> None:
        result = generate_normal(_sample_image(), emboss_mode=True)
        self.assertEqual(result.mode, "RGB")
        self.assertEqual(result.size, (8, 8))

    def test_emboss_normal_flat_image_returns_flat_normal(self) -> None:
        flat = Image.new("RGB", (16, 16), color=(128, 128, 128))
        result = generate_normal(flat, strength=2.0, emboss_mode=True)
        self.assertEqual(result.mode, "RGB")
        self.assertEqual(result.size, (16, 16))
        # Flat uniform input → Sobel gradients are zero → flat normal (128, 128, 255).
        r, g, b = result.split()
        self.assertLessEqual(r.getextrema()[1] - r.getextrema()[0], 4)
        self.assertLessEqual(g.getextrema()[1] - g.getextrema()[0], 4)
        self.assertGreaterEqual(b.getextrema()[0], 245)

    def test_emboss_normal_differs_from_standard_on_detailed_image(self) -> None:
        detail = _detailed_bright_image()
        standard = generate_normal(detail, strength=2.0, emboss_mode=False)
        emboss = generate_normal(detail, strength=2.0, emboss_mode=True)
        self.assertNotEqual(standard.tobytes(), emboss.tobytes())

    def test_emboss_normal_blue_channel_stays_in_skyrim_safe_range(self) -> None:
        detail = _detailed_bright_image()
        result = generate_normal(detail, strength=2.0, emboss_mode=True)
        _, _, blue = result.split()
        self.assertGreaterEqual(blue.getextrema()[0], 128)

    def test_emboss_mode_threads_through_generate_msn(self) -> None:
        detail = _detailed_bright_image()
        standard_msn = generate_msn(detail, normal_strength=2.0, emboss_mode=False)
        emboss_msn = generate_msn(detail, normal_strength=2.0, emboss_mode=True)
        # RGB channels encode the normal; they should differ between modes.
        std_r, std_g, _, _ = standard_msn.split()
        emb_r, emb_g, _, _ = emboss_msn.split()
        self.assertNotEqual(std_r.tobytes(), emb_r.tobytes())

    def test_emboss_mode_threads_through_run_with_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "book_cover.png"
            _detailed_bright_image().save(input_path)
            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ):
                outputs = run_with_options(
                    input_file=input_path,
                    output_dir=temp_path / "out",
                    include_diffuse=False,
                    include_normal=True,
                    include_parallax=False,
                    emboss_mode=True,
                )
            self.assertIn("normal", outputs)

    def test_emboss_mode_produces_stronger_edge_response_for_printed_pattern(self) -> None:
        pattern = _emboss_pattern_image()
        standard = generate_normal(pattern, strength=2.0, emboss_mode=False)
        emboss = generate_normal(pattern, strength=2.0, emboss_mode=True)
        std_r, std_g, _ = standard.split()
        emb_r, emb_g, _ = emboss.split()
        standard_variation = sum(ImageStat.Stat(channel).stddev[0] for channel in (std_r, std_g))
        emboss_variation = sum(ImageStat.Stat(channel).stddev[0] for channel in (emb_r, emb_g))
        self.assertGreater(emboss_variation, standard_variation)

    def test_emboss_mode_low_detail_input_stays_close_to_flat_normal(self) -> None:
        low_detail = Image.new("RGB", (24, 24), color=(132, 132, 132))
        result = generate_normal(low_detail, strength=2.0, emboss_mode=True)
        r, g, b = result.split()
        self.assertLessEqual(r.getextrema()[1] - r.getextrema()[0], 4)
        self.assertLessEqual(g.getextrema()[1] - g.getextrema()[0], 4)
        self.assertGreaterEqual(b.getextrema()[0], 245)


class ReliefModeTests(unittest.TestCase):
    def test_relief_normal_returns_rgb_same_size(self) -> None:
        result = generate_normal(_sample_image(), relief_mode=True)
        self.assertEqual(result.mode, "RGB")
        self.assertEqual(result.size, (8, 8))

    def test_relief_normal_flat_image_returns_flat_normal(self) -> None:
        flat = Image.new("RGB", (16, 16), color=(128, 128, 128))
        result = generate_normal(flat, strength=2.0, relief_mode=True)
        self.assertEqual(result.mode, "RGB")
        self.assertEqual(result.size, (16, 16))
        r, g, b = result.split()
        self.assertLessEqual(r.getextrema()[1] - r.getextrema()[0], 4)
        self.assertLessEqual(g.getextrema()[1] - g.getextrema()[0], 4)
        self.assertGreaterEqual(b.getextrema()[0], 245)

    def test_relief_normal_blue_channel_stays_in_skyrim_safe_range(self) -> None:
        detail = _detailed_bright_image()
        result = generate_normal(detail, strength=2.0, relief_mode=True)
        _, _, blue = result.split()
        self.assertGreaterEqual(blue.getextrema()[0], 128)

    def test_relief_normal_differs_from_standard_on_detailed_image(self) -> None:
        detail = _detailed_bright_image()
        standard = generate_normal(detail, strength=2.0, relief_mode=False)
        relief = generate_normal(detail, strength=2.0, relief_mode=True)
        self.assertNotEqual(standard.tobytes(), relief.tobytes())

    def test_relief_mode_takes_priority_over_emboss_mode(self) -> None:
        detail = _detailed_bright_image()
        emboss_only = generate_normal(detail, strength=2.0, emboss_mode=True, relief_mode=False)
        relief_only = generate_normal(detail, strength=2.0, emboss_mode=False, relief_mode=True)
        both = generate_normal(detail, strength=2.0, emboss_mode=True, relief_mode=True)
        # relief_mode=True should take priority: result with both matches relief_only.
        self.assertEqual(both.tobytes(), relief_only.tobytes())
        self.assertNotEqual(both.tobytes(), emboss_only.tobytes())

    def test_relief_parallax_returns_l_same_size(self) -> None:
        from generate_textures import generate_parallax

        result = generate_parallax(_sample_image(), relief_mode=True)
        self.assertEqual(result.mode, "L")
        self.assertEqual(result.size, (8, 8))

    def test_relief_parallax_differs_from_standard(self) -> None:
        from generate_textures import generate_parallax

        detail = _detailed_bright_image()
        standard = generate_parallax(detail, strength=1.35, relief_mode=False)
        relief = generate_parallax(detail, strength=1.35, relief_mode=True)
        self.assertNotEqual(standard.tobytes(), relief.tobytes())

    def test_relief_mode_threads_through_generate_msn(self) -> None:
        detail = _detailed_bright_image()
        standard_msn = generate_msn(detail, normal_strength=2.0, relief_mode=False)
        relief_msn = generate_msn(detail, normal_strength=2.0, relief_mode=True)
        std_r, std_g, _, _ = standard_msn.split()
        rel_r, rel_g, _, _ = relief_msn.split()
        self.assertNotEqual(std_r.tobytes(), rel_r.tobytes())

    def test_relief_mode_threads_through_generate_preview_outputs(self) -> None:
        detail = _detailed_bright_image()
        standard_outputs = generate_preview_outputs(
            detail,
            normal_strength=2.0,
            parallax_strength=1.35,
            glow_threshold=190,
            environment_mask_strength=1.2,
            complex_strength=1.15,
            specular_strength=1.15,
            complex_format="msn",
            relief_mode=False,
            include_diffuse=False,
            include_normal=True,
            include_parallax=False,
            include_glow=False,
            include_environment_mask=False,
            include_complex=False,
        )
        relief_outputs = generate_preview_outputs(
            detail,
            normal_strength=2.0,
            parallax_strength=1.35,
            glow_threshold=190,
            environment_mask_strength=1.2,
            complex_strength=1.15,
            specular_strength=1.15,
            complex_format="msn",
            relief_mode=True,
            include_diffuse=False,
            include_normal=True,
            include_parallax=False,
            include_glow=False,
            include_environment_mask=False,
            include_complex=False,
        )
        self.assertNotEqual(
            standard_outputs["normal"].tobytes(),
            relief_outputs["normal"].tobytes(),
        )

    def test_relief_mode_threads_through_run_with_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "painting.png"
            _detailed_bright_image().save(input_path)
            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ):
                outputs = run_with_options(
                    input_file=input_path,
                    output_dir=temp_path / "out",
                    include_diffuse=False,
                    include_normal=True,
                    include_parallax=False,
                    relief_mode=True,
                )
            self.assertIn("normal", outputs)

    def test_analyze_image_content_includes_bg_uniformity(self) -> None:
        uniform = Image.new("RGB", (64, 64), color=(200, 200, 200))
        result = analyze_image_content(uniform)
        self.assertIn("bg_uniformity", result)
        self.assertGreaterEqual(float(result["bg_uniformity"]), 0.0)
        self.assertLessEqual(float(result["bg_uniformity"]), 1.0)

    def test_scharr_normal_differs_from_old_sobel_for_diagonal_gradient(self) -> None:
        # Create a diagonal gradient image — Scharr has better diagonal accuracy.
        diag = Image.new("RGB", (24, 24))
        px = diag.load()
        for y in range(24):
            for x in range(24):
                v = int((x + y) / 46.0 * 255)
                px[x, y] = (v, v, v)
        result = generate_normal(diag, strength=2.0)
        self.assertEqual(result.mode, "RGB")
        # Verify blue channel is in Skyrim-safe range.
        _, _, blue = result.split()
        self.assertGreaterEqual(blue.getextrema()[0], 128)

    def test_generate_normal_color_sign_image_is_not_near_flat(self) -> None:
        normal = generate_normal(_color_sign_image(), strength=3.2, relief_mode=True)
        red, green, blue = normal.split()
        self.assertGreater(red.getextrema()[1] - red.getextrema()[0], 8)
        self.assertGreater(green.getextrema()[1] - green.getextrema()[0], 8)
        self.assertGreaterEqual(blue.getextrema()[0], 128)

    def test_generate_specular_color_sign_image_is_not_flat(self) -> None:
        specular = generate_specular(_color_sign_image(), strength=2.0)
        minimum, maximum = specular.getextrema()
        self.assertGreater(maximum - minimum, 10)

    def test_generate_normal_shield_art_image_is_not_near_flat(self) -> None:
        normal = generate_normal(_shield_art_image(), strength=3.0)
        red, green, blue = normal.split()
        self.assertGreater(red.getextrema()[1] - red.getextrema()[0], 12)
        self.assertGreater(green.getextrema()[1] - green.getextrema()[0], 12)
        self.assertGreaterEqual(blue.getextrema()[0], 128)


class TooltipPositionTests(unittest.TestCase):
    def test_compute_tooltip_position_uses_cursor_offset_when_room_exists(self) -> None:
        x, y = _compute_tooltip_position(
            pointer_x=120,
            pointer_y=150,
            tip_width=200,
            tip_height=60,
            screen_width=1920,
            screen_height=1080,
        )
        self.assertEqual((x, y), (136, 170))

    def test_compute_tooltip_position_clamps_inside_screen_bounds(self) -> None:
        x, y = _compute_tooltip_position(
            pointer_x=1910,
            pointer_y=1070,
            tip_width=260,
            tip_height=120,
            screen_width=1920,
            screen_height=1080,
        )
        self.assertGreaterEqual(x, 10)
        self.assertGreaterEqual(y, 10)
        self.assertLessEqual(x + 260, 1910)
        self.assertLessEqual(y + 120, 1070)


class ParallaxOcclusionTests(unittest.TestCase):
    def test_generate_parallax_occlusion_returns_l_same_size(self) -> None:
        result = generate_parallax_occlusion(_sample_image())
        self.assertEqual(result.mode, "L")
        self.assertEqual(result.size, (8, 8))

    def test_generate_parallax_occlusion_produces_non_flat_output(self) -> None:
        result = generate_parallax_occlusion(_vertical_gradient_image(), strength=1.35)
        values = set(result.tobytes())
        self.assertGreater(len(values), 2)

    def test_parallax_occlusion_differs_from_standard_on_detailed_image(self) -> None:
        detail = _detailed_bright_image()
        standard = generate_parallax(detail, strength=1.35)
        occlusion = generate_parallax_occlusion(detail, strength=1.35)
        self.assertNotEqual(standard.tobytes(), occlusion.tobytes())

    def test_parallax_occlusion_smoother_than_standard(self) -> None:
        """POM heightmap should have smaller pixel-to-pixel variance (smoother gradients)."""
        import statistics
        detail = _detailed_bright_image()
        std_bytes = list(generate_parallax(detail, strength=1.35).tobytes())
        pom_bytes = list(generate_parallax_occlusion(detail, strength=1.35).tobytes())
        std_diffs = [abs(std_bytes[i + 1] - std_bytes[i]) for i in range(len(std_bytes) - 1)]
        pom_diffs = [abs(pom_bytes[i + 1] - pom_bytes[i]) for i in range(len(pom_bytes) - 1)]
        self.assertLessEqual(statistics.mean(pom_diffs), statistics.mean(std_diffs))

    def test_parallax_occlusion_keeps_mean_depth_near_standard(self) -> None:
        detail = _detailed_bright_image()
        standard_mean = ImageStat.Stat(generate_parallax(detail, strength=1.35)).mean[0]
        pom_mean = ImageStat.Stat(generate_parallax_occlusion(detail, strength=1.35)).mean[0]
        self.assertLess(abs(pom_mean - standard_mean), 40.0)

    def test_generate_parallax_occlusion_strength_slider_has_stronger_min_max_separation(self) -> None:
        source = _detailed_bright_image()
        low = generate_parallax_occlusion(source, strength=0.2)
        high = generate_parallax_occlusion(source, strength=6.0)
        low_range = low.getextrema()[1] - low.getextrema()[0]
        high_range = high.getextrema()[1] - high.getextrema()[0]
        self.assertGreater(high_range, low_range + 18)

    def test_map_parallax_strength_to_nif_scale_boosts_in_game_depth(self) -> None:
        self.assertAlmostEqual(float(_map_parallax_strength_to_nif_scale(0.1) or 0.0), 0.34, places=2)
        self.assertGreater(float(_map_parallax_strength_to_nif_scale(1.35) or 0.0), 2.0)
        self.assertAlmostEqual(float(_map_parallax_strength_to_nif_scale(6.0) or 0.0), 10.0, places=3)

    def test_parallax_occlusion_flat_input_returns_mid_gray(self) -> None:
        flat = Image.new("RGB", (16, 16), color=(128, 128, 128))
        result = generate_parallax_occlusion(flat, strength=1.35)
        self.assertEqual(result.getextrema(), (127, 127))

    def test_parallax_mode_occlusion_threads_through_preview_outputs(self) -> None:
        detail = _detailed_bright_image()
        standard_out = generate_preview_outputs(
            detail,
            normal_strength=2.0, parallax_strength=1.35, glow_threshold=200,
            environment_mask_strength=1.0, complex_strength=1.0, specular_strength=1.15,
            complex_format="msn", parallax_mode="standard",
            include_diffuse=False, include_normal=False, include_parallax=True,
            include_glow=False, include_environment_mask=False, include_complex=False,
        )
        occlusion_out = generate_preview_outputs(
            detail,
            normal_strength=2.0, parallax_strength=1.35, glow_threshold=200,
            environment_mask_strength=1.0, complex_strength=1.0, specular_strength=1.15,
            complex_format="msn", parallax_mode="occlusion",
            include_diffuse=False, include_normal=False, include_parallax=True,
            include_glow=False, include_environment_mask=False, include_complex=False,
        )
        self.assertNotEqual(
            standard_out["parallax"].tobytes(),
            occlusion_out["parallax"].tobytes(),
        )

    def test_parallax_mode_occlusion_threads_through_run_with_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "stone.png"
            _detailed_bright_image().save(input_path)
            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ):
                outputs = run_with_options(
                    input_file=input_path,
                    output_dir=temp_path / "out",
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=True,
                    parallax_mode="occlusion",
                )
            self.assertIn("parallax", outputs)

    def test_generate_preview_outputs_rejects_invalid_parallax_mode(self) -> None:
        with self.assertRaises(ValueError):
            generate_preview_outputs(
                _sample_image(),
                normal_strength=2.0,
                parallax_strength=1.35,
                glow_threshold=200,
                environment_mask_strength=1.0,
                complex_strength=1.0,
                specular_strength=1.15,
                complex_format="msn",
                parallax_mode="invalid-mode",
                include_diffuse=False,
                include_normal=False,
                include_parallax=True,
                include_glow=False,
                include_environment_mask=False,
                include_complex=False,
            )

    def test_run_with_options_rejects_invalid_parallax_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "stone.png"
            _sample_image().save(input_path)
            with self.assertRaises(ValueError):
                run_with_options(
                    input_file=input_path,
                    output_dir=temp_path / "out",
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=True,
                    parallax_mode="broken",
                )

    def test_run_with_options_uses_resolved_truepbr_env_mask_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "stone.png"
            _sample_image().save(input_path)
            captured_workflows: list[str] = []
            original_generate = generate_environment_mask_for_workflow

            def _spy(source, *, strength=1.2, mode="standard", complex_workflow="truepbr"):
                captured_workflows.append(str(complex_workflow))
                return original_generate(
                    source,
                    strength=strength,
                    mode=mode,
                    complex_workflow=complex_workflow,
                )

            with mock.patch("generate_textures.generate_environment_mask_for_workflow", side_effect=_spy), \
                 mock.patch(
                     "generate_textures._save_with_dds_fallback",
                     side_effect=lambda _image, path, **_kwargs: path,
                 ):
                run_with_options(
                    input_file=input_path,
                    output_dir=temp_path / "out",
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=True,
                    include_complex=True,
                    complex_format="cm",
                    env_mask_mode="complex",
                    render_profile="auto",
                )
            self.assertEqual(captured_workflows, ["truepbr"])

    def test_generate_preview_outputs_uses_resolved_truepbr_env_mask_workflow(self) -> None:
        source = _sample_image()
        captured_workflows: list[str] = []
        original_generate = generate_environment_mask_for_workflow

        def _spy(source_image, *, strength=1.2, mode="standard", complex_workflow="truepbr"):
            captured_workflows.append(str(complex_workflow))
            return original_generate(
                source_image,
                strength=strength,
                mode=mode,
                complex_workflow=complex_workflow,
            )

        with mock.patch("generate_textures.generate_environment_mask_for_workflow", side_effect=_spy):
            generate_preview_outputs(
                source,
                normal_strength=2.0,
                parallax_strength=1.35,
                glow_threshold=190,
                environment_mask_strength=1.2,
                complex_strength=1.2,
                specular_strength=1.1,
                complex_format="cm",
                env_mask_mode="complex",
                include_diffuse=False,
                include_normal=False,
                include_parallax=False,
                include_glow=False,
                include_environment_mask=True,
                include_complex=True,
                render_profile="auto",
            )
        self.assertEqual(captured_workflows, ["truepbr"])

    def test_run_with_options_rejects_conflicting_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "stone.png"
            _sample_image().save(input_path)
            with self.assertRaisesRegex(ValueError, "Conflicting output filenames detected"):
                run_with_options(
                    input_file=input_path,
                    output_dir=temp_path / "out",
                    diffuse_name="stone_shared",
                    normal_name="stone_shared",
                    include_diffuse=True,
                    include_normal=True,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_rmaos=False,
                    include_complex=False,
                )

    def test_generate_parallax_occlusion_relief_mode_differs_from_standard(self) -> None:
        """Relief mode should change the occlusion heightmap (luminosity-as-height)."""
        source = _detailed_bright_image()
        standard = generate_parallax_occlusion(source, strength=1.35, relief_mode=False)
        relief = generate_parallax_occlusion(source, strength=1.35, relief_mode=True)
        self.assertEqual(standard.mode, "L")
        self.assertEqual(relief.mode, "L")
        self.assertNotEqual(standard.tobytes(), relief.tobytes())

    def test_generate_parallax_occlusion_relief_flat_returns_l(self) -> None:
        """relief_mode on a flat input should still return an L-mode image of the same size."""
        flat = Image.new("RGB", (16, 16), color=(128, 128, 128))
        result = generate_parallax_occlusion(flat, strength=1.35, relief_mode=True)
        self.assertEqual(result.mode, "L")
        self.assertEqual(result.size, (16, 16))

    def test_run_with_options_parallax_occlusion_relief_mode_threads_through(self) -> None:
        """run_with_options should pass relief_mode to generate_parallax_occlusion."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "sign.png"
            _detailed_bright_image().save(input_path)
            captured: list[dict] = []

            original_gen = generate_parallax_occlusion

            def _spy(source, strength=1.35, *, relief_mode=False):
                captured.append({"relief_mode": relief_mode})
                return original_gen(source, strength=strength, relief_mode=relief_mode)

            with mock.patch("generate_textures.generate_parallax_occlusion", side_effect=_spy), \
                 mock.patch(
                     "generate_textures._save_with_dds_fallback",
                     side_effect=lambda _img, path, **_kw: path,
                 ):
                run_with_options(
                    input_file=input_path,
                    output_dir=temp_path / "out",
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=True,
                    parallax_mode="occlusion",
                    relief_mode=True,
                )
            self.assertEqual(len(captured), 1)
            self.assertTrue(captured[0]["relief_mode"])

    def test_generate_preview_outputs_parallax_occlusion_relief_mode_threads_through(self) -> None:
        """generate_preview_outputs should pass relief_mode to generate_parallax_occlusion."""
        source = _detailed_bright_image()
        standard = generate_preview_outputs(
            source,
            normal_strength=2.0, parallax_strength=1.35, glow_threshold=200,
            environment_mask_strength=1.0, complex_strength=1.0, specular_strength=1.15,
            complex_format="msn", parallax_mode="occlusion", relief_mode=False,
            include_diffuse=False, include_normal=False, include_parallax=True,
            include_glow=False, include_environment_mask=False, include_complex=False,
        )
        relief = generate_preview_outputs(
            source,
            normal_strength=2.0, parallax_strength=1.35, glow_threshold=200,
            environment_mask_strength=1.0, complex_strength=1.0, specular_strength=1.15,
            complex_format="msn", parallax_mode="occlusion", relief_mode=True,
            include_diffuse=False, include_normal=False, include_parallax=True,
            include_glow=False, include_environment_mask=False, include_complex=False,
        )
        self.assertNotEqual(
            standard["parallax"].tobytes(),
            relief["parallax"].tobytes(),
        )



    """Tests for conflicting-option and output-folder-format warnings."""

    def _base_kwargs(self) -> dict:
        return dict(
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            include_complex=False,
        )

    def test_emboss_and_relief_both_enabled_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            emboss_mode=True,
            relief_mode=True,
            **self._base_kwargs(),
        )
        ids = [w[0] for w in warnings]
        self.assertIn("emboss_and_relief_both_enabled", ids)

    def test_emboss_and_relief_not_both_enabled_no_conflict_warning(self) -> None:
        for emboss, relief in [(True, False), (False, True), (False, False)]:
            warnings = get_generation_warnings(
                "stone",
                include_normal=True,
                emboss_mode=emboss,
                relief_mode=relief,
                **self._base_kwargs(),
            )
            ids = [w[0] for w in warnings]
            self.assertNotIn("emboss_and_relief_both_enabled", ids)

    def test_emboss_mode_without_normal_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=False,
            emboss_mode=True,
            relief_mode=False,
            **self._base_kwargs(),
        )
        ids = [w[0] for w in warnings]
        self.assertIn("depth_mode_without_normal", ids)

    def test_relief_mode_without_normal_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=False,
            emboss_mode=False,
            relief_mode=True,
            **self._base_kwargs(),
        )
        ids = [w[0] for w in warnings]
        self.assertIn("depth_mode_without_normal", ids)

    def test_depth_mode_with_normal_enabled_no_warning(self) -> None:
        for emboss, relief in [(True, False), (False, True)]:
            warnings = get_generation_warnings(
                "stone",
                include_normal=True,
                emboss_mode=emboss,
                relief_mode=relief,
                **self._base_kwargs(),
            )
            ids = [w[0] for w in warnings]
            self.assertNotIn("depth_mode_without_normal", ids)

    def test_emboss_non_paper_material_triggers_warning(self) -> None:
        """Emboss mode on a solid material (stone, terrain, metal…) should warn."""
        for material in ("stone", "terrain", "metal", "wood", "skin"):
            with self.subTest(material=material):
                warnings = get_generation_warnings(
                    material,
                    include_normal=True,
                    emboss_mode=True,
                    relief_mode=False,
                    **self._base_kwargs(),
                )
                ids = [w[0] for w in warnings]
                self.assertIn("emboss_non_paper_material", ids)

    def test_emboss_paper_no_false_positive_for_non_paper_warning(self) -> None:
        """Emboss mode on paper is correct usage — should NOT trigger the non-paper warning."""
        warnings = get_generation_warnings(
            "paper",
            include_normal=True,
            emboss_mode=True,
            relief_mode=False,
            **self._base_kwargs(),
        )
        ids = [w[0] for w in warnings]
        self.assertNotIn("emboss_non_paper_material", ids)

    def test_emboss_non_paper_warning_not_raised_when_emboss_off(self) -> None:
        """No false positive when emboss_mode is False on a non-paper material."""
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            emboss_mode=False,
            relief_mode=False,
            **self._base_kwargs(),
        )
        ids = [w[0] for w in warnings]
        self.assertNotIn("emboss_non_paper_material", ids)

    def test_env_mask_with_complex_material_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_environment_mask=True,
            include_complex=True,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="cm",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("env_mask_with_complex_material", ids)

    def test_env_mask_with_enb_complex_combo_does_not_trigger_conflict_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_environment_mask=True,
            include_complex=True,
            include_glow=False,
            env_mask_mode="complex",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="msn",
        )
        ids = [w[0] for w in warnings]
        self.assertNotIn("env_mask_with_complex_material", ids)

    def test_env_mask_alone_no_complex_conflict_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_environment_mask=True,
            include_complex=False,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
        )
        ids = [w[0] for w in warnings]
        self.assertNotIn("env_mask_with_complex_material", ids)

    def test_msn_with_standard_env_mode_triggers_renderer_mismatch_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=False,
            include_complex=True,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="msn",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("msn_with_standard_env_mode", ids)

    def test_cm_with_normal_enabled_does_not_trigger_msn_explanatory_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=False,
            include_complex=True,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="cm",
        )
        ids = [w[0] for w in warnings]
        self.assertNotIn("msn_with_standard_env_mode", ids)

    def test_cm_with_complex_env_mode_triggers_renderer_mismatch_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=True,
            include_complex=True,
            include_glow=False,
            env_mask_mode="complex",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="cm",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("cm_with_complex_env_mode", ids)

    def test_cm_without_normal_map_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=False,
            include_environment_mask=False,
            include_complex=True,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="cm",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("cm_without_normal_map", ids)

    def test_rmaos_without_normal_map_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=False,
            include_environment_mask=False,
            include_rmaos=True,
            include_complex=False,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="cm",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("rmaos_without_normal_map", ids)

    def test_rmaos_with_msn_complex_triggers_workflow_mix_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=True,
            include_rmaos=True,
            include_complex=True,
            include_glow=False,
            env_mask_mode="complex",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="msn",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("rmaos_with_msn_mix", ids)

    def test_get_generation_warnings_rmaos_with_env_mask_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=True,
            include_rmaos=True,
            include_complex=False,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("rmaos_with_env_mask", ids)

    def test_get_generation_warnings_rmaos_without_env_mask_no_false_env_mask_warnings(self) -> None:
        """RMAOS alone must not trigger env-mask-specific warnings (false-positive guard)."""
        warnings = get_generation_warnings(
            "organic",
            include_normal=True,
            include_environment_mask=False,
            include_rmaos=True,
            include_complex=False,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.8,
            include_parallax=False,
        )
        ids = [w[0] for w in warnings]
        env_mask_specific = {
            "high_env_mask_organic",
            "high_env_mask_glass",
            "low_env_mask_metal",
            "high_env_mask_strength",
        }
        for warning_id in env_mask_specific:
            self.assertNotIn(warning_id, ids, f"False-positive: {warning_id} fired with RMAOS only")

    def test_msn_with_normal_output_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=True,
            include_complex=True,
            include_glow=False,
            env_mask_mode="complex",
            env_mask_strength=1.2,
            include_parallax=False,
            complex_format="msn",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("msn_with_normal_output", ids)

    def test_complex_env_without_complex_material_triggers_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=True,
            include_complex=False,
            include_glow=False,
            env_mask_mode="complex",
            env_mask_strength=1.2,
            include_parallax=False,
        )
        ids = [w[0] for w in warnings]
        self.assertIn("complex_env_without_msn", ids)

    def test_cm_with_occlusion_parallax_triggers_workflow_mix_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_normal=True,
            include_environment_mask=False,
            include_complex=True,
            include_glow=False,
            env_mask_mode="standard",
            env_mask_strength=1.2,
            include_parallax=True,
            complex_format="cm",
            parallax_mode="occlusion",
        )
        ids = [w[0] for w in warnings]
        self.assertIn("cm_with_enb_pom", ids)

    def test_get_output_folder_format_warnings_msn_vs_cm_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "stone_msn.dds").write_bytes(b"fake")
            warnings = get_output_folder_format_warnings(
                temp_path, include_complex=True, complex_format="cm"
            )
            ids = [w[0] for w in warnings]
            self.assertIn("output_folder_has_msn_files", ids)

    def test_get_output_folder_format_warnings_cm_vs_msn_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "stone_cm.dds").write_bytes(b"fake")
            warnings = get_output_folder_format_warnings(
                temp_path, include_complex=True, complex_format="msn"
            )
            ids = [w[0] for w in warnings]
            self.assertIn("output_folder_has_cm_files", ids)

    def test_get_output_folder_format_warnings_c_vs_msn_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "stone_c.dds").write_bytes(b"fake")
            warnings = get_output_folder_format_warnings(
                temp_path, include_complex=True, complex_format="msn"
            )
            ids = [w[0] for w in warnings]
            self.assertIn("output_folder_has_cm_files", ids)

    def test_get_output_folder_format_warnings_uppercase_C_vs_msn_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "stone_C.dds").write_bytes(b"fake")
            warnings = get_output_folder_format_warnings(
                temp_path, include_complex=True, complex_format="msn"
            )
            ids = [w[0] for w in warnings]
            self.assertIn("output_folder_has_cm_files", ids)

    def test_get_output_folder_format_warnings_matching_format_no_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "stone_msn.dds").write_bytes(b"fake")
            warnings = get_output_folder_format_warnings(
                temp_path, include_complex=True, complex_format="msn"
            )
            self.assertEqual(warnings, [])

    def test_get_output_folder_format_warnings_include_complex_false_no_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "stone_msn.dds").write_bytes(b"fake")
            warnings = get_output_folder_format_warnings(
                temp_path, include_complex=False, complex_format="cm"
            )
            self.assertEqual(warnings, [])

    def test_get_output_folder_format_warnings_empty_folder_no_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            warnings = get_output_folder_format_warnings(
                temp_path, include_complex=True, complex_format="cm"
            )
            self.assertEqual(warnings, [])

    def test_normalize_gui_state_persists_dismissed_warnings(self) -> None:
        from generate_textures import _normalize_gui_state
        state = _normalize_gui_state({"dismissed_warnings": ["glow_non_magical", "parallax_flat_plants"]})
        self.assertIn("glow_non_magical", state["dismissed_warnings"])
        self.assertIn("parallax_flat_plants", state["dismissed_warnings"])

    def test_normalize_gui_state_dismissed_warnings_defaults_to_empty(self) -> None:
        from generate_textures import _normalize_gui_state
        state = _normalize_gui_state({})
        self.assertEqual(state["dismissed_warnings"], [])

    def test_auto_patch_nifs_default_is_false(self) -> None:
        from generate_textures import _GUI_STATE_DEFAULTS
        self.assertFalse(
            _GUI_STATE_DEFAULTS["auto_patch_nifs"],
            "auto_patch_nifs must default to False so NIF auto-patching is opt-in",
        )

    def test_normalize_gui_state_auto_patch_nifs_defaults_false_when_missing(self) -> None:
        from generate_textures import _normalize_gui_state
        state = _normalize_gui_state({})
        self.assertFalse(state["auto_patch_nifs"])

    # --- Wetness mask tests ---

    def test_generate_wetness_mask_returns_l_mode_same_size(self) -> None:
        source = _sample_image()
        result = generate_wetness_mask(source, strength=1.0)
        self.assertEqual(result.mode, "L")
        self.assertEqual(result.size, source.size)

    def test_generate_wetness_mask_no_pure_black(self) -> None:
        source = _sample_image()
        result = generate_wetness_mask(source, strength=1.0)
        pixels = list(result.get_flattened_data())
        self.assertTrue(all(v > 0 for v in pixels), "Wetness mask should have no pure-black pixels (floor-lifted)")

    def test_generate_wetness_mask_higher_strength_increases_contrast(self) -> None:
        source = Image.new("RGB", (32, 32))
        pixels = source.load()
        for y in range(32):
            for x in range(32):
                pixels[x, y] = (x * 8, y * 8, 0)
        low = generate_wetness_mask(source, strength=0.5)
        high = generate_wetness_mask(source, strength=2.5)
        low_vals = list(low.get_flattened_data())
        high_vals = list(high.get_flattened_data())
        low_range = max(low_vals) - min(low_vals)
        high_range = max(high_vals) - min(high_vals)
        self.assertGreaterEqual(high_range, low_range, "Higher strength should produce >= contrast range")

    # --- Snow mask tests ---

    def test_generate_snow_mask_returns_l_mode_same_size(self) -> None:
        source = _sample_image()
        result = generate_snow_mask(source, strength=1.0)
        self.assertEqual(result.mode, "L")
        self.assertEqual(result.size, source.size)

    def test_generate_snow_mask_no_pure_black(self) -> None:
        source = _sample_image()
        result = generate_snow_mask(source, strength=1.0)
        pixels = list(result.get_flattened_data())
        self.assertTrue(all(v > 0 for v in pixels), "Snow mask should have no pure-black pixels (floor-lifted)")

    def test_generate_snow_mask_higher_strength_increases_brightness(self) -> None:
        source = _sample_image()
        low = generate_snow_mask(source, strength=0.5)
        high = generate_snow_mask(source, strength=2.5)
        low_mean = sum(low.get_flattened_data()) / (source.width * source.height)
        high_mean = sum(high.get_flattened_data()) / (source.width * source.height)
        self.assertGreaterEqual(high_mean, low_mean, "Higher snow mask strength should produce >= mean brightness")

    # --- Output path builder tests ---

    def test_build_wetness_mask_output_path_uses_wt_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "stone.dds"
            input_path.write_bytes(b"stub")
            result = build_wetness_mask_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                wetness_name=None,
            )
            self.assertEqual(result.name, "stone_wt.dds")

    def test_build_snow_mask_output_path_uses_sm_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "stone.dds"
            input_path.write_bytes(b"stub")
            result = build_snow_mask_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                snow_name=None,
            )
            self.assertEqual(result.name, "stone_sm.dds")

    # --- GENERATED_TEXTURE_SUFFIXES tests ---

    def test_wt_in_generated_texture_suffixes(self) -> None:
        self.assertIn("_wt", GENERATED_TEXTURE_SUFFIXES)

    def test_sm_in_generated_texture_suffixes(self) -> None:
        self.assertIn("_sm", GENERATED_TEXTURE_SUFFIXES)

    # --- run_with_options wetness/snow integration tests ---

    def test_run_with_options_generates_wetness_mask(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                    include_wetness_mask=True,
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.args[1].name, "brick_wt.dds")

    def test_run_with_options_generates_snow_mask(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                    include_snow_mask=True,
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.args[1].name, "brick_sm.dds")

    # --- Generation warnings for snow/wetness tests ---

    def test_snow_mask_on_glass_raises_warning(self) -> None:
        warnings = get_generation_warnings(
            "glass",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
            include_snow_mask=True,
        )
        warning_ids = [w[0] for w in warnings]
        self.assertIn("snow_mask_on_non_accumulating_material", warning_ids)

    def test_snow_mask_on_stone_raises_no_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
            include_snow_mask=True,
        )
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("snow_mask_on_non_accumulating_material", warning_ids)

    def test_wetness_mask_on_glass_raises_warning(self) -> None:
        warnings = get_generation_warnings(
            "glass",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
            include_wetness_mask=True,
        )
        warning_ids = [w[0] for w in warnings]
        self.assertIn("wetness_mask_on_reflective_material", warning_ids)

    def test_wetness_mask_on_stone_raises_no_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
            include_wetness_mask=True,
        )
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("wetness_mask_on_reflective_material", warning_ids)


# ---------------------------------------------------------------------------
# Parallax warnings — glass and skin
# ---------------------------------------------------------------------------


class ParallaxGlassSkinWarningTests(unittest.TestCase):
    """Tests for new parallax_flat_glass and parallax_character_skin warnings."""

    def _base_kwargs(self) -> dict:
        return dict(
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=True,
            include_complex=False,
        )

    def test_parallax_glass_raises_warning(self) -> None:
        warnings = get_generation_warnings("glass", **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertIn("parallax_flat_glass", warning_ids)

    def test_parallax_glass_no_warning_when_parallax_off(self) -> None:
        kwargs = self._base_kwargs()
        kwargs["include_parallax"] = False
        warnings = get_generation_warnings("glass", **kwargs)
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_flat_glass", warning_ids)

    def test_parallax_skin_raises_warning(self) -> None:
        warnings = get_generation_warnings("skin", **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertIn("parallax_character_skin", warning_ids)

    def test_parallax_skin_no_warning_when_parallax_off(self) -> None:
        kwargs = self._base_kwargs()
        kwargs["include_parallax"] = False
        warnings = get_generation_warnings("skin", **kwargs)
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_character_skin", warning_ids)

    def test_parallax_stone_raises_no_glass_or_skin_warning(self) -> None:
        warnings = get_generation_warnings("stone", **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_flat_glass", warning_ids)
        self.assertNotIn("parallax_character_skin", warning_ids)


# ---------------------------------------------------------------------------
# recommend_output_resolution
# ---------------------------------------------------------------------------


class RecommendOutputResolutionTests(unittest.TestCase):
    """Tests for recommend_output_resolution(width, height, material_type)."""

    def test_terrain_max_is_2048(self) -> None:
        max_dim, _ = recommend_output_resolution(4096, 4096, "terrain")
        self.assertEqual(max_dim, 2048)

    def test_skin_max_is_2048(self) -> None:
        max_dim, _ = recommend_output_resolution(4096, 4096, "skin")
        self.assertEqual(max_dim, 2048)

    def test_plants_max_is_1024(self) -> None:
        max_dim, _ = recommend_output_resolution(2048, 2048, "plants")
        self.assertEqual(max_dim, 1024)

    def test_paper_max_is_1024(self) -> None:
        max_dim, _ = recommend_output_resolution(2048, 2048, "paper")
        self.assertEqual(max_dim, 1024)

    def test_architecture_max_is_4096(self) -> None:
        max_dim, _ = recommend_output_resolution(8192, 8192, "architecture")
        self.assertEqual(max_dim, 4096)

    def test_general_max_is_4096(self) -> None:
        max_dim, _ = recommend_output_resolution(8192, 8192, "general")
        self.assertEqual(max_dim, 4096)

    def test_unknown_material_max_is_4096(self) -> None:
        max_dim, _ = recommend_output_resolution(8192, 8192, "unknownmaterial")
        self.assertEqual(max_dim, 4096)

    def test_returns_reason_string(self) -> None:
        _, reason = recommend_output_resolution(2048, 2048, "general")
        self.assertIsInstance(reason, str)
        self.assertTrue(len(reason) > 0)

    def test_small_source_returns_max_dim_unchanged(self) -> None:
        max_dim, _ = recommend_output_resolution(512, 512, "terrain")
        self.assertEqual(max_dim, 2048)

    def test_8k_source_reason_mentions_resolution(self) -> None:
        _, reason = recommend_output_resolution(16384, 16384, "general")
        self.assertIn("16384", reason)


# ---------------------------------------------------------------------------
# Source resolution warning in get_generation_warnings
# ---------------------------------------------------------------------------


class SourceResolutionWarningTests(unittest.TestCase):
    """Tests that get_generation_warnings emits source_resolution_excessive when appropriate."""

    def _base_kwargs(self) -> dict:
        return dict(
            include_glow=False,
            include_environment_mask=False,
            env_mask_mode="standard",
            env_mask_strength=1.0,
            include_parallax=False,
            include_complex=False,
        )

    def test_terrain_8k_source_raises_resolution_warning(self) -> None:
        warnings = get_generation_warnings("terrain", source_size=(8192, 8192), **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertIn("source_resolution_excessive", warning_ids)

    def test_terrain_2k_source_no_resolution_warning(self) -> None:
        warnings = get_generation_warnings("terrain", source_size=(2048, 2048), **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("source_resolution_excessive", warning_ids)

    def test_general_4k_source_no_resolution_warning(self) -> None:
        warnings = get_generation_warnings("general", source_size=(4096, 4096), **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("source_resolution_excessive", warning_ids)

    def test_general_8k_source_raises_resolution_warning(self) -> None:
        warnings = get_generation_warnings("general", source_size=(8192, 4096), **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertIn("source_resolution_excessive", warning_ids)

    def test_no_source_size_no_resolution_warning(self) -> None:
        warnings = get_generation_warnings("terrain", source_size=None, **self._base_kwargs())
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("source_resolution_excessive", warning_ids)


# ---------------------------------------------------------------------------
# Material-aware write_rmaos_json_sidecar
# ---------------------------------------------------------------------------


class RmaosJsonMaterialTypeTests(unittest.TestCase):
    """Tests that write_rmaos_json_sidecar applies per-material PBR overrides."""

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_and_read(self, material_type: str) -> dict:
        import json
        from pathlib import Path

        rmaos_path = Path(self._tmpdir) / f"test_{material_type}_rmaos.dds"
        write_rmaos_json_sidecar(rmaos_path, parallax_enabled=False, material_type=material_type)
        # The function writes into a PBRNifPatcher sub-directory.
        json_path = Path(self._tmpdir) / "PBRNifPatcher" / f"{rmaos_path.stem}.json"
        with open(json_path) as f:
            raw = json.load(f)
        # Payload is a list; return the first (and only) entry for convenience.
        return raw[0] if isinstance(raw, list) else raw

    def test_general_uses_defaults(self) -> None:
        data = self._write_and_read("general")
        # General should not enable subsurface
        self.assertFalse(data.get("subsurface", False))
        self.assertFalse(data.get("subsurface_foliage", False))

    def test_skin_enables_subsurface(self) -> None:
        data = self._write_and_read("skin")
        self.assertTrue(data.get("subsurface", False))
        self.assertAlmostEqual(data["subsurface_color"][0], 1.0, places=3)
        self.assertAlmostEqual(data["subsurface_color"][1], 0.85, places=3)
        self.assertAlmostEqual(data["subsurface_color"][2], 0.7, places=3)
        self.assertAlmostEqual(data["subsurface_opacity"], 0.65, places=3)
        self.assertAlmostEqual(data["displacement_scale"], 0.0, places=3)

    def test_plants_enables_foliage_subsurface(self) -> None:
        data = self._write_and_read("plants")
        self.assertTrue(data.get("subsurface_foliage", False))
        self.assertAlmostEqual(data["displacement_scale"], 0.0, places=3)

    def test_metal_has_high_specular(self) -> None:
        data = self._write_and_read("metal")
        self.assertAlmostEqual(data["specular_level"], 0.5, places=3)

    def test_glass_has_low_roughness(self) -> None:
        data = self._write_and_read("glass")
        self.assertAlmostEqual(data["roughness_scale"], 0.35, places=3)

    def test_terrain_has_shallow_displacement(self) -> None:
        data = self._write_and_read("terrain")
        self.assertAlmostEqual(data["displacement_scale"], 0.08, places=3)

    def test_snow_roughness_set(self) -> None:
        data = self._write_and_read("snow")
        self.assertAlmostEqual(data["roughness_scale"], 0.75, places=3)

    def test_cloth_low_specular(self) -> None:
        data = self._write_and_read("cloth")
        self.assertAlmostEqual(data["specular_level"], 0.016, places=4)

    def test_fur_has_very_high_roughness_and_very_low_specular(self) -> None:
        data = self._write_and_read("fur")
        self.assertGreater(data["roughness_scale"], 1.2)
        self.assertLess(data["specular_level"], 0.02)

    def test_paper_has_low_specular_and_shallow_displacement(self) -> None:
        data = self._write_and_read("paper")
        self.assertLess(data["specular_level"], 0.02)
        self.assertLess(data["displacement_scale"], 0.1)

    def test_dirt_has_high_roughness(self) -> None:
        data = self._write_and_read("dirt")
        self.assertGreater(data["roughness_scale"], 1.1)

    def test_sand_has_low_specular(self) -> None:
        data = self._write_and_read("sand")
        self.assertLess(data["specular_level"], 0.03)

    def test_architecture_has_moderate_roughness(self) -> None:
        data = self._write_and_read("architecture")
        self.assertGreater(data["roughness_scale"], 1.0)
        self.assertLess(data["roughness_scale"], 1.2)

    def test_fur_displacement_is_shallow(self) -> None:
        data = self._write_and_read("fur")
        self.assertLess(data["displacement_scale"], 0.15)

    def test_dirt_no_subsurface(self) -> None:
        data = self._write_and_read("dirt")
        self.assertFalse(data.get("subsurface", False))
        self.assertFalse(data.get("subsurface_foliage", False))

    def test_sand_no_subsurface(self) -> None:
        data = self._write_and_read("sand")
        self.assertFalse(data.get("subsurface", False))
        self.assertFalse(data.get("subsurface_foliage", False))


# ---------------------------------------------------------------------------
# Terrain parallax high-strength warning
# ---------------------------------------------------------------------------


class TerrainParallaxStrengthWarningTests(unittest.TestCase):
    """Tests for the terrain-parallax high-strength warning added to get_generation_warnings."""

    def _base_kwargs(self) -> dict:
        return {
            "include_glow": False,
            "include_environment_mask": False,
            "env_mask_mode": "standard",
            "env_mask_strength": 1.2,
            "include_parallax": True,
            "include_complex": False,
        }

    def test_terrain_parallax_below_threshold_no_high_strength_warning(self) -> None:
        warnings = get_generation_warnings(
            "terrain",
            parallax_strength=0.9,
            **self._base_kwargs(),
        )
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_high_strength_terrain", warning_ids)

    def test_terrain_parallax_at_threshold_no_high_strength_warning(self) -> None:
        warnings = get_generation_warnings(
            "terrain",
            parallax_strength=1.0,
            **self._base_kwargs(),
        )
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_high_strength_terrain", warning_ids)

    def test_terrain_parallax_above_threshold_raises_warning(self) -> None:
        warnings = get_generation_warnings(
            "terrain",
            parallax_strength=1.5,
            **self._base_kwargs(),
        )
        warning_ids = [w[0] for w in warnings]
        self.assertIn("parallax_high_strength_terrain", warning_ids)

    def test_terrain_parallax_high_strength_warning_includes_value(self) -> None:
        warnings = get_generation_warnings(
            "terrain",
            parallax_strength=2.0,
            **self._base_kwargs(),
        )
        messages = {w[0]: w[1] for w in warnings}
        self.assertIn("parallax_high_strength_terrain", messages)
        self.assertIn("2.00", messages["parallax_high_strength_terrain"])

    def test_terrain_parallax_still_emits_shimmer_warning_alongside_high_strength(self) -> None:
        warnings = get_generation_warnings(
            "terrain",
            parallax_strength=1.5,
            **self._base_kwargs(),
        )
        warning_ids = [w[0] for w in warnings]
        self.assertIn("parallax_landscape_shimmer", warning_ids)
        self.assertIn("parallax_high_strength_terrain", warning_ids)

    def test_terrain_parallax_disabled_no_high_strength_warning(self) -> None:
        kwargs = dict(self._base_kwargs())
        kwargs["include_parallax"] = False
        warnings = get_generation_warnings(
            "terrain",
            parallax_strength=2.5,
            **kwargs,
        )
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_high_strength_terrain", warning_ids)

    def test_non_terrain_material_no_terrain_high_strength_warning(self) -> None:
        warnings = get_generation_warnings(
            "stone",
            parallax_strength=3.0,
            **self._base_kwargs(),
        )
        warning_ids = [w[0] for w in warnings]
        self.assertNotIn("parallax_high_strength_terrain", warning_ids)


# ---------------------------------------------------------------------------
# Glow map mipmap pre-filter
# ---------------------------------------------------------------------------


class GlowMipmapPrefilterTests(unittest.TestCase):
    """Tests for the glow-map branch added to _prefilter_for_mipmap_stability."""

    def _solid_l(self, value: int = 200, size: tuple[int, int] = (64, 64)) -> Image.Image:
        return Image.new("L", size, value)

    def _solid_rgb(self, value: tuple[int, int, int] = (200, 150, 50), size: tuple[int, int] = (64, 64)) -> Image.Image:
        return Image.new("RGB", size, value)

    def test_glow_l_mode_preserved_size_and_mode(self) -> None:
        src = self._solid_l()
        result = _prefilter_for_mipmap_stability(src, "glow")
        self.assertEqual(result.size, src.size)
        self.assertEqual(result.mode, "L")

    def test_glow_rgb_mode_preserved(self) -> None:
        src = self._solid_rgb()
        result = _prefilter_for_mipmap_stability(src, "glow")
        self.assertEqual(result.size, src.size)
        self.assertEqual(result.mode, "RGB")

    def test_glow_rgba_mode_preserved(self) -> None:
        src = Image.new("RGBA", (64, 64), (200, 150, 50, 255))
        result = _prefilter_for_mipmap_stability(src, "glow")
        self.assertEqual(result.size, src.size)
        self.assertEqual(result.mode, "RGBA")

    def test_glow_flat_image_value_unchanged(self) -> None:
        src = self._solid_l(200)
        result = _prefilter_for_mipmap_stability(src, "glow")
        pixels = list(result.getdata())
        self.assertTrue(all(abs(p - 200) <= 2 for p in pixels))

    def test_glow_noisy_image_is_smoothed(self) -> None:
        import random
        rng = random.Random(42)
        noisy = Image.new("L", (64, 64))
        pixels = [rng.randint(0, 255) for _ in range(64 * 64)]
        noisy.putdata(pixels)
        result = _prefilter_for_mipmap_stability(noisy, "glow")
        orig_std = float(np.std(np.array(noisy, dtype=np.float32)))
        result_std = float(np.std(np.array(result, dtype=np.float32)))
        self.assertLess(result_std, orig_std)

    def test_unknown_mode_glow_passthrough(self) -> None:
        src = Image.new("P", (64, 64))
        result = _prefilter_for_mipmap_stability(src, "glow")
        self.assertIs(result, src)

    def test_non_glow_kind_passthrough(self) -> None:
        src = self._solid_l()
        result = _prefilter_for_mipmap_stability(src, "diffuse")
        self.assertIs(result, src)

    def test_glow_kind_is_applied_not_passthrough(self) -> None:
        import random
        rng = random.Random(7)
        noisy = Image.new("L", (64, 64))
        noisy.putdata([rng.randint(0, 255) for _ in range(64 * 64)])
        result = _prefilter_for_mipmap_stability(noisy, "glow")
        self.assertIsNot(result, noisy)


class AoRoughnessPathTests(unittest.TestCase):
    def test_build_ao_output_path_uses_ao_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            ao_path = build_ao_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
            )

            self.assertEqual(ao_path.name, "brick_ao.dds")

    def test_build_ao_output_path_places_file_in_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "subdir" / "stone.dds"
            output_dir = temp_path / "out"

            ao_path = build_ao_output_path(input_path=input_path, output_dir=output_dir)

            self.assertEqual(ao_path.parent, output_dir)

    def test_build_roughness_output_path_uses_rough_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            rough_path = build_roughness_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
            )

            self.assertEqual(rough_path.name, "brick_rough.dds")

    def test_build_roughness_output_path_places_file_in_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "subdir" / "stone.dds"
            output_dir = temp_path / "out"

            rough_path = build_roughness_output_path(input_path=input_path, output_dir=output_dir)

            self.assertEqual(rough_path.parent, output_dir)


class AoRoughnessRunWithOptionsTests(unittest.TestCase):
    def test_run_with_options_generates_ao_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                    include_ao=True,
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.args[1].name, "brick_ao.dds")

    def test_run_with_options_generates_roughness_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.png"
            output_dir = temp_path / "out"
            _sample_image().save(input_path)

            with mock.patch(
                "generate_textures._save_with_dds_fallback",
                side_effect=lambda _image, path, **_kwargs: path,
            ) as save_mock:
                run_with_options(
                    input_file=input_path,
                    output_dir=output_dir,
                    include_diffuse=False,
                    include_normal=False,
                    include_parallax=False,
                    include_glow=False,
                    include_environment_mask=False,
                    include_complex=False,
                    include_roughness=True,
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.args[1].name, "brick_rough.dds")


class NormalizeGuiStateAoRoughnessTests(unittest.TestCase):
    def test_normalize_gui_state_includes_ao_roughness_defaults(self) -> None:
        normalized = _normalize_gui_state({})
        self.assertIn("include_ao", normalized)
        self.assertIn("include_roughness", normalized)
        self.assertIn("auto_ao", normalized)
        self.assertIn("auto_roughness", normalized)
        self.assertIn("ao_strength", normalized)
        self.assertIn("roughness_strength", normalized)

    def test_normalize_gui_state_coerces_ao_roughness_booleans(self) -> None:
        normalized = _normalize_gui_state({"include_ao": 1, "include_roughness": 0, "auto_ao": 1, "auto_roughness": 0})
        self.assertIs(normalized["include_ao"], True)
        self.assertIs(normalized["include_roughness"], False)
        self.assertIs(normalized["auto_ao"], True)
        self.assertIs(normalized["auto_roughness"], False)

    def test_normalize_gui_state_coerces_ao_roughness_strengths(self) -> None:
        normalized = _normalize_gui_state({"ao_strength": "2.5", "roughness_strength": "0.8"})
        self.assertAlmostEqual(float(normalized["ao_strength"]), 2.5)
        self.assertAlmostEqual(float(normalized["roughness_strength"]), 0.8)


class RecommendGenerationSettingsAoRoughnessTests(unittest.TestCase):
    def test_recommend_generation_settings_includes_ao_roughness_keys(self) -> None:
        image = _sample_image()
        result = recommend_generation_settings(image)
        self.assertIn("ao_strength", result)
        self.assertIn("roughness_strength", result)

    def test_recommend_generation_settings_ao_roughness_in_valid_range(self) -> None:
        image = _sample_image()
        result = recommend_generation_settings(image)
        ao_strength = float(result["ao_strength"])
        roughness_strength = float(result["roughness_strength"])
        self.assertGreater(ao_strength, 0.0)
        self.assertLessEqual(ao_strength, 8.0)
        self.assertGreater(roughness_strength, 0.0)
        self.assertLessEqual(roughness_strength, 8.0)


if __name__ == "__main__":
    unittest.main()
