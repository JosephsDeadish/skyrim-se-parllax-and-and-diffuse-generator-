# Release Validation Checklist

Generated: 2026-10-08T01:58:02.480293+00:00

- [x] Full unittest suite x2
- [x] Targeted NIF fixture/parity stress checks x2
- [x] Compile check x2
- [x] Tracked-file secret scan
- [x] Localization sweep

## NIF real-sample family trend snapshot

- Snapshot JSON: `nif_family_trend_snapshot.json`

## NIF parity feature report snapshot

- Parity JSON: `nif_parity_feature_report.json`
- Parity markdown: `nif_parity_feature_report.md`

## NIF real-sample side-by-side parity delta snapshot

- Real-sample parity delta JSON: `nif_realmod_parity_delta_report.json`
- Real-sample parity delta markdown: `nif_realmod_parity_delta_report.md`

## Localization sweep snapshot

- Localization JSON: `localization_coverage_report.json`
- Localization markdown: `localization_coverage_report.md`
- Base (en) strings: 42
- Non-en catalogs with missing strings: 2
- Pack `skyrim_realmod_family_pack`
  - architecture: pass 7/7, fail 0, strategy alignment 1/2 (0.50)
  - armor: pass 1/1, fail 0, strategy alignment 0/0 (0.00)
  - clutter: pass 2/2, fail 0, strategy alignment 1/1 (1.00)
  - effects: pass 4/4, fail 0, strategy alignment 1/2 (0.50)
  - foliage: pass 1/1, fail 0, strategy alignment 0/0 (0.00)
  - landscape: pass 1/1, fail 0, strategy alignment 0/0 (0.00)
- Pack `fallout_guarded_family_pack`
  - architecture: pass 6/6, fail 0, strategy alignment 2/6 (0.33)
  - armor: pass 1/1, fail 0, strategy alignment 0/0 (0.00)
  - clutter: pass 2/2, fail 0, strategy alignment 0/0 (0.00)
  - effects: pass 1/1, fail 0, strategy alignment 0/0 (0.00)
  - env_scale_gate: pass 1/1, fail 0, strategy alignment 0/0 (0.00)
  - fix_lighting_gate: pass 1/1, fail 0, strategy alignment 0/0 (0.00)
  - landscape: pass 1/1, fail 0, strategy alignment 0/0 (0.00)
  - parallax_scale_gate: pass 1/1, fail 0, strategy alignment 0/0 (0.00)
  - spec_color_gate: pass 1/1, fail 0, strategy alignment 0/0 (0.00)
  - spec_gate: pass 1/1, fail 0, strategy alignment 0/0 (0.00)
