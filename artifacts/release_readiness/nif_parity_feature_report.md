# NIF parity feature report

- Generated: 2026-10-08T01:58:02.435171+00:00
- Cases: 23
- Cases using disable/clear fallback: 3

| Case | Profile/layout | Detected family count | Remediation mode | Fallback used | Intentional strategy diff | Difference bucket |
| --- | --- | ---: | --- | --- | --- | --- |
| `parity_skyrim_missing_parallax_flag` | `skyrim/legacy` | 2 | `rebuild` | no | no | `none` |
| `parity_skyrim_envmap_missing_slots4_5` | `skyrim/legacy` | 3 | `rebuild` | no | yes | `safety_first` |
| `parity_skyrim_envmap_slot5_missing` | `skyrim/legacy` | 3 | `rebuild` | no | yes | `safety_first` |
| `parity_skyrim_parallax_envmap_glow_unresolved` | `skyrim/legacy` | 6 | `mixed_rebuild_and_disable` | yes | no | `safety_first` |
| `parity_skyrim_envmap_pom_crossblock_normal_reuse` | `skyrim/legacy` | 8 | `mixed_rebuild_and_disable` | yes | yes | `safety_first` |
| `parity_skyrim_parallax_diffuse_reuse` | `skyrim/legacy` | 3 | `rebuild` | no | no | `none` |
| `parity_skyrim_truepbr_canonical_rmaos` | `skyrim/legacy` | 2 | `rebuild` | no | no | `none` |
| `parity_skyrim_complex_material_cm_suffix` | `skyrim/legacy` | 3 | `rebuild` | no | no | `none` |
| `parity_skyrim_multishaderblock_mixed_conflicts` | `skyrim/legacy` | 5 | `rebuild` | no | no | `none` |
| `parity_skyrim_multiblock_conflicting_workflows` | `skyrim/legacy` | 5 | `rebuild` | no | no | `none` |
| `parity_skyrim_real_shifted_multiblock_env_flag_and_parallax_reuse` | `skyrim/real` | 5 | `rebuild` | no | no | `none` |
| `parity_skyrim_real_shifted_multiblock_envmap_slot4_and_parallax_diffuse` | `skyrim/real` | 6 | `rebuild` | no | no | `none` |
| `parity_skyrim_real_shifted_multiblock_envmap_slot5_and_glow_wrong_suffix` | `skyrim/real` | 6 | `rebuild` | no | no | `none` |
| `parity_fallout_missing_parallax_flag` | `fallout/legacy` | 3 | `rebuild` | no | no | `guarded_fallout` |
| `parity_fallout_single_pass_skip` | `fallout/legacy` | 2 | `manual_or_noop` | no | no | `guarded_fallout` |
| `parity_fallout_envmap_missing_slots4_5` | `fallout/legacy` | 4 | `rebuild` | no | no | `guarded_fallout` |
| `parity_fallout_env_slot5_without_flag` | `fallout/legacy` | 4 | `rebuild` | no | no | `guarded_fallout` |
| `parity_fallout_envmap_slot5_missing` | `fallout/legacy` | 4 | `rebuild` | no | yes | `guarded_fallout` |
| `parity_fallout_real_multiblock_envmap_slot4_and_parallax_diffuse` | `fallout/real` | 6 | `rebuild` | no | no | `guarded_fallout` |
| `parity_fallout_real_crlf_u16_shifted_parallax_envmap_mixed` | `fallout/real` | 4 | `mixed_rebuild_and_disable` | yes | no | `guarded_fallout` |
| `parity_skyrim_single_pass_manual_review` | `skyrim/legacy` | 1 | `manual_or_noop` | no | no | `none` |
| `parity_unknown_unsupported_header_manual_review` | `unknown/legacy` | 2 | `manual_or_noop` | no | no | `none` |
| `parity_fallout_real_crlf_u16_shifted_env_alias_without_flag` | `fallout/real` | 2 | `manual_or_noop` | no | yes | `guarded_fallout` |

Use the JSON artifact for full per-case details (detected families/codes, remediation steps, expected-step deltas, and safety-difference notes).
