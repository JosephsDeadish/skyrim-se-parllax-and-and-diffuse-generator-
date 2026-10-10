# NIF real-sample side-by-side parity delta

- Generated: 2026-10-08T01:58:02.478268+00:00
- Cases: 32

| Pack | Case | Family | Profile/layout | Conflict family count | Local remediation mode | Difference bucket |
| --- | --- | --- | --- | ---: | --- | --- |
| `skyrim_realmod_family_pack` | `pack_skyrim_architecture_parallax_diffuse_reuse` | `architecture` | `skyrim/legacy` | 3 | `rebuild` | `none` |
| `skyrim_realmod_family_pack` | `pack_skyrim_architecture_multishader_envmap` | `architecture` | `skyrim/legacy` | 5 | `rebuild` | `none` |
| `skyrim_realmod_family_pack` | `pack_skyrim_architecture_conflicting_per_block_workflows` | `architecture` | `skyrim/legacy` | 5 | `rebuild` | `none` |
| `skyrim_realmod_family_pack` | `pack_skyrim_clutter_envmap_slot4_missing` | `clutter` | `skyrim/legacy` | 3 | `rebuild` | `none` |
| `skyrim_realmod_family_pack` | `pack_skyrim_armor_envmap_slot5_missing` | `armor` | `skyrim/legacy` | 3 | `rebuild` | `none` |
| `skyrim_realmod_family_pack` | `pack_skyrim_landscape_envmask_alias` | `landscape` | `skyrim/legacy` | 4 | `rebuild` | `none` |
| `skyrim_realmod_family_pack` | `pack_skyrim_foliage_env_slot4_wrong_suffix` | `foliage` | `skyrim/legacy` | 4 | `rebuild` | `none` |
| `skyrim_realmod_family_pack` | `pack_skyrim_effects_glow_wrong_suffix` | `effects` | `skyrim/legacy` | 4 | `rebuild` | `none` |
| `skyrim_realmod_family_pack` | `pack_skyrim_effects_mixed_parallax_envmap_glow_unresolved` | `effects` | `skyrim/legacy` | 6 | `mixed_rebuild_and_disable` | `safety_first` |
| `skyrim_realmod_family_pack` | `pack_skyrim_effects_envmap_pom_and_crossblock_parallax_normal_reuse` | `effects` | `skyrim/legacy` | 8 | `mixed_rebuild_and_disable` | `safety_first` |
| `skyrim_realmod_family_pack` | `pack_skyrim_architecture_real_multiblock_env_slot5_and_glow_wrong` | `architecture` | `skyrim/real` | 6 | `rebuild` | `none` |
| `skyrim_realmod_family_pack` | `pack_skyrim_architecture_le_u16_envmask_alias` | `architecture` | `skyrim/legacy` | 4 | `rebuild` | `none` |
| `skyrim_realmod_family_pack` | `pack_skyrim_clutter_vr_crlf_parallax_normal_reuse` | `clutter` | `skyrim/legacy` | 3 | `rebuild` | `none` |
| `skyrim_realmod_family_pack` | `pack_skyrim_architecture_ae_real_crlf_shifted_envmap_glow_mixed` | `architecture` | `skyrim/real` | 6 | `mixed_rebuild_and_disable` | `safety_first` |
| `skyrim_realmod_family_pack` | `pack_skyrim_architecture_aevr_real_crlf_u16_shifted_parallax_envmap_mixed` | `architecture` | `skyrim/real` | 4 | `mixed_rebuild_and_disable` | `safety_first` |
| `skyrim_realmod_family_pack` | `pack_skyrim_effects_vr_parallax_envmap_glow_unresolved_no_diffuse` | `effects` | `skyrim/legacy` | 8 | `disable` | `safety_first` |
| `fallout_guarded_family_pack` | `pack_fallout_architecture_envmap_missing_slots` | `architecture` | `fallout/legacy` | 4 | `rebuild` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_architecture_real_shifted_envmap_glow_mixed` | `architecture` | `fallout/real` | 6 | `mixed_rebuild_and_disable` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_architecture_aevr_real_crlf_u16_shifted_parallax_envmap_mixed` | `architecture` | `fallout/real` | 4 | `mixed_rebuild_and_disable` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_effects_env_slot5_without_flag` | `effects` | `fallout/legacy` | 4 | `rebuild` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_clutter_single_pass_skip` | `clutter` | `fallout/legacy` | 2 | `manual_or_noop` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_armor_envmap_slot4_missing` | `armor` | `fallout/legacy` | 4 | `rebuild` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_landscape_envmap_slot5_missing` | `landscape` | `fallout/legacy` | 4 | `rebuild` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_spec_strength_gate_profile` | `spec_gate` | `fallout/real` | 3 | `rebuild` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_spec_color_gate_profile` | `spec_color_gate` | `fallout/real` | 4 | `rebuild` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_env_scale_gate_profile` | `env_scale_gate` | `fallout/real` | 4 | `rebuild` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_fix_lighting_gate_profile` | `fix_lighting_gate` | `fallout/legacy` | 2 | `manual_or_noop` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_parallax_scale_gate_profile` | `parallax_scale_gate` | `fallout/real` | 3 | `mixed_rebuild_and_disable` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_clutter_real_multiblock_env_slot4_and_parallax_diffuse` | `clutter` | `fallout/real` | 6 | `rebuild` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_architecture_real_crlf_u16_shifted_env_alias_without_flag` | `architecture` | `fallout/real` | 2 | `manual_or_noop` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_architecture_real_crlf_u16_shifted_envmap_glow_slot5_missing` | `architecture` | `fallout/real` | 2 | `manual_or_noop` | `guarded_fallout` |
| `fallout_guarded_family_pack` | `pack_fallout_architecture_real_crlf_u16_shifted_multiblock_guarded_unsupported` | `architecture` | `fallout/real` | 2 | `manual_or_noop` | `guarded_fallout` |

Bucket legend: `safety_first` = conservative disable/guard fallback, `guarded_fallout` = Fallout safety policy divergence, `destructive_disabled` = intentionally avoids destructive cleanup paths.
