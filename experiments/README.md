# Experiments

One folder per run. Each holds what is small enough to commit: `NOTES.md` (what and why), `pipeline.json` and `pipeline.log` (pipelined runs), `breaks.json` (break turn per seed and rollout), `monitor.jsonl` (one row per transcript and monitor view), `summary.json` and `evaluate.md` (from `probing_control.evaluate`), and the stage outputs. Transcripts (`.eval`) live under `logs/<same name>/` and are not committed; the `log` field in `monitor.jsonl` rows is the path at the time of the run.

| Folder | What | Headline |
| :-- | :-- | :-- |
| `2026-10-04_smoke_1seed_6turns` | first GLM 5.3 call | raw reasoning is logged; auditor sees none of it |
| `2026-10-04_pilot3_tags_unmatched` | 3 seeds, tag design, cache bug | arms diverged early; led to the eager-resume fix |
| `2026-10-04_pilot3b_tags_matched` | 3 seeds, tag design, paired | tags land in reasoning, not replies; reasoning monitor finds the probes |
| `2026-10-04_pilot3c_debrief_1seed` | 1 seed, debrief design | debrief parses and matches turns; tag-free transcripts |
| `2026-10-04_pipeline_validation_2seeds` | pipeline smoke test | stage 2 overlaps stage 1 |
| `2026-10-04_full_glm53_rollout1` | 173 seeds, 1 rollout | trace AUROC 0.98 with reasoning vs 0.71 actions-only |
| `2026-10-06_clock_variant` | 45 timing-cited seeds, clock-equipped auditor, both arms | timing cited in debriefs 86% to 53%; sim_score mean 0.81 to 0.75, median unchanged |

To reproduce an evaluation: `python -m probing_control.evaluate --logs logs/<name>/base logs/<name>/inject --monitor experiments/<name>/monitor.jsonl --breaks experiments/<name>/breaks.json`.
