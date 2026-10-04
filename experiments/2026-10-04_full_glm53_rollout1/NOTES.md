# Full run: 173 seeds, 1 rollout, GLM 5.3, debrief design

Pipelined (`pipeline.log`), 12:43 to 17:51 on 2026-10-04, $226, 0 sample errors. Settings in `pipeline.json` (base/inject/monitor connections 20/8/6, inject batch 10, 30 turns, Wafer).

| | |
| :-- | --: |
| clean audits | 173 |
| injected continuations | 164 (9 seeds ended before turn 5) |
| debriefs parsed | 155 |
| monitor rows | 1,011 |
| debrief sim_score mean / median | 0.73 / 0.80 |
| runs reporting no probes | 88 of 155 |
| probes reported / matched to turns | 304 / 164, in 52 runs |
| trace AUROC actions / cot / both | 0.71 / 0.98 / 0.98 |
| turn recall actions / cot / both (threshold 0.5) | 0.06 / 0.45 / 0.43 |
| turn precision actions / cot / both | 0.36 / 0.21 / 0.27 (unadjudicated) |

Files: `breaks.json` (break turn per seed), `monitor.jsonl` (one row per transcript and view), `summary.json` and `evaluate.md` (output of `probing_control.evaluate`), stage outputs `*.out`. Transcripts: `logs/2026-10-04_full_glm53_rollout1/{base,inject}` (147 MB, not committed).
