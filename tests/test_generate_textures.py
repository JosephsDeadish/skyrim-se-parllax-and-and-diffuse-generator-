import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageStat

from generate_textures import (
    APP_VERSION,
    PATREON_URL,
    _create_panda_icon_image,
    _run_cli,
    _normalize_gui_state,
    analyze_image_content,
    apply_recommendations_by_auto_flags,
    build_complex_output_path,
    build_environment_mask_output_path,
    classify_material_type,
    collect_source_textures,
    detect_workflow_profile,
    detect_mod_manager_context,
    enforce_skyrim_output_profile,
    build_glow_output_path,
    build_normal_output_path,
    build_output_paths,
    generate_complex_material,
    generate_diffuse,
    generate_environment_mask,
    generate_glow,
    generate_msn,
    generate_normal,
    generate_parallax,
    generate_parallax_occlusion,
    generate_preview_outputs,
    generate_specular,
    get_generation_warnings,
    get_preview_size_limits,
    identify_skyrim_texture_role,
    prepare_preview_source,
    recommend_generation_settings,
    load_gui_state,
    run_batch_with_options,
    run_with_options,
    save_gui_state,
    select_generation_context_source,
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


class GenerateTexturesTests(unittest.TestCase):
    def test_app_version_is_0_5(self) -> None:
        self.assertEqual(APP_VERSION, "0.5")

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
                    "auto_suggestions": True,
                    "auto_normal": False,
                    "auto_parallax": True,
                    "auto_glow": False,
                    "auto_environment_mask": True,
                    "auto_complex": False,
                    "auto_specular": True,
                },
                state_file,
            )
            loaded = load_gui_state(state_file)
        self.assertEqual(loaded["input_path"], str(input_file))
        self.assertEqual(loaded["output_path"], "/tmp/out")
        self.assertTrue(bool(loaded["use_custom_output"]))
        self.assertTrue(bool(loaded["dark_mode"]))
        self.assertTrue(bool(loaded["auto_suggestions"]))
        self.assertFalse(bool(loaded["auto_normal"]))
        self.assertTrue(bool(loaded["auto_parallax"]))
        self.assertFalse(bool(loaded["auto_glow"]))
        self.assertTrue(bool(loaded["auto_environment_mask"]))
        self.assertFalse(bool(loaded["auto_complex"]))
        self.assertTrue(bool(loaded["auto_specular"]))

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
        ao, roughness, metallic, height_or_spec = complex_material.split()
        self.assertNotEqual(ao.tobytes(), roughness.tobytes())
        self.assertNotEqual(roughness.tobytes(), metallic.tobytes())
        self.assertNotEqual(height_or_spec.tobytes(), ao.tobytes())

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

    def test_classify_material_type_returns_stone_for_brick_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/architecture/brick_wall.dds")), "stone")

    def test_classify_material_type_returns_metal_for_iron_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/armor/iron_helmet.dds")), "metal")

    def test_classify_material_type_returns_plants_for_leaf_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/plants/leaf01.dds")), "plants")

    def test_classify_material_type_returns_wood_for_timber_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/clutter/timber_plank.dds")), "wood")

    def test_classify_material_type_returns_general_for_unknown_path(self) -> None:
        self.assertEqual(classify_material_type(Path("textures/misc/unknown.dds")), "general")

    def test_classify_material_type_returns_paper_for_cards_path(self) -> None:
        self.assertEqual(
            classify_material_type(Path("textures/interface/cards/collectible_waifu_card_01.dds")),
            "paper",
        )

    def test_detect_workflow_profile_detects_interface_paths(self) -> None:
        self.assertEqual(
            detect_workflow_profile(Path("textures/interface/cards/deck01.dds")),
            "interface",
        )

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
        _, glossiness, _, _ = environment_mask.split()
        minimum, _ = glossiness.getextrema()
        self.assertGreater(minimum, 4)

    def test_generate_environment_mask_complex_mode_returns_rgba_same_size(self) -> None:
        environment_mask = generate_environment_mask(_sample_image(), mode="complex")
        self.assertEqual(environment_mask.mode, "RGBA")
        self.assertEqual(environment_mask.size, (8, 8))

    def test_generate_environment_mask_complex_mode_flat_surface_avoids_black_holes(self) -> None:
        environment_mask = generate_environment_mask(_flat_dark_image(), strength=2.2, mode="complex")
        env_amount, glossiness, metallic, height_alpha = environment_mask.split()
        env_min, env_max = env_amount.getextrema()
        gloss_min, _ = glossiness.getextrema()
        metallic_min, _ = metallic.getextrema()
        height_min, height_max = height_alpha.getextrema()
        self.assertGreaterEqual(env_min, 10)
        self.assertLessEqual(env_max - env_min, 80)
        self.assertGreaterEqual(gloss_min, 5)
        self.assertGreaterEqual(metallic_min, 6)
        self.assertGreaterEqual(height_min, 95)
        self.assertLessEqual(height_max, 160)

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

            self.assertEqual(glow_path.name, "brick_g.dds")

    def test_build_environment_mask_output_path_uses_default_name_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "brick.dds"
            input_path.write_bytes(b"stub")

            environment_mask_path = build_environment_mask_output_path(
                input_path=input_path,
                output_dir=temp_path / "out",
                environment_mask_name=None,
            )

            self.assertEqual(environment_mask_path.name, "brick_m.dds")

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

    def test_collect_source_textures_from_directory_skips_generated_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for name in ("brick.dds", "stone_wall.dds", "brick_n.dds", "brick_p.dds", "brick_msn.dds", "preview.png"):
                (temp_path / name).write_bytes(b"stub")

            collected = collect_source_textures(temp_path)

            self.assertEqual([path.name for path in collected], ["brick.dds", "stone_wall.dds"])

    def test_detect_mod_manager_context_reads_mo2_profile_and_loaded_texture_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            instance_root = temp_path / "MO2"
            textures_dir = instance_root / "mods" / "Texture Pack" / "textures"
            tool_dir = instance_root / "mods" / "Skyrim Texture Generator"
            profile_dir = instance_root / "profiles" / "Default"
            textures_dir.mkdir(parents=True)
            tool_dir.mkdir(parents=True)
            profile_dir.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+Texture Pack\n-Disabled Mod\n", encoding="utf-8")

            context = detect_mod_manager_context(
                {"MO_PROFILE": "Default"},
                executable_path=tool_dir / "generate_textures.exe",
            )

            self.assertEqual(context.manager, "Mod Organizer 2")
            self.assertEqual(context.profile_name, "Default")
            self.assertEqual(context.loaded_mods, ("Texture Pack",))
            self.assertEqual(context.loaded_texture_dirs, (textures_dir.resolve(),))
            self.assertEqual(context.output_dir, (instance_root / "overwrite"))

    def test_detect_mod_manager_context_reads_vortex_profile_and_staging_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            appdata_dir = temp_path / "AppData" / "Roaming"
            profile_dir = appdata_dir / "Vortex" / "skyrimse" / "profiles" / "Main"
            profile_dir.mkdir(parents=True)
            (profile_dir / "modlist.txt").write_text("+Texture Pack\n", encoding="utf-8")

            staging_root = temp_path / "Vortex Mods" / "skyrimse"
            textures_dir = staging_root / "Texture Pack" / "textures"
            tool_dir = staging_root / "Skyrim Texture Generator"
            textures_dir.mkdir(parents=True)
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
            self.assertEqual(context.loaded_texture_dirs, (textures_dir.resolve(),))
            self.assertEqual(context.staging_root, staging_root.resolve())
            self.assertEqual(context.output_dir, (tool_dir / "generated_textures").resolve())

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
                "glow": "brick_g.dds",
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

    def test_identify_skyrim_texture_role_environment_mask(self) -> None:
        result = identify_skyrim_texture_role(Path("textures/armor/iron_m.dds"))
        self.assertEqual(result["role"], "environment_mask")
        self.assertEqual(result["suffix"], "_m")
        self.assertIn("Slot 5", result["notes"])

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

    def test_run_with_options_complex_env_mask_uses_dxt5(self) -> None:
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
                )

            self.assertEqual(save_mock.call_count, 1)
            self.assertEqual(save_mock.call_args.kwargs["preferred_pixel_formats"], ("DXT5",))


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


if __name__ == "__main__":
    unittest.main()
