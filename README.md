# Probing control: can a monitor see a model testing whether its environment is real?

A controlled experiment on [Petri](https://github.com/meridianlabs-ai/inspect_petri) audits. Every Petri run is simulated: an auditor model improvises the environment, tools and humans. A target model that works out it is in a simulation can behave differently. This repository asks two things: when a target is told to find out whether its environment is real, and to hide that it is checking, **can a monitor catch it**, from its actions alone or from its private reasoning? And **what seams** in the improvised environment does it find?

Nothing in Petri is modified. Everything composes Petri's public hooks.

## Headline result (173 seeds, GLM 5.3, one rollout, 2026-10-04)

| Monitor sees | Trace-level AUROC | Turn recall | Turn precision |
| :-- | --: | --: | --: |
| actions only | 0.71 | 0.06 | 0.36 |
| private reasoning only | 0.98 | 0.45 | 0.21 |
| both | 0.98 | 0.43 | 0.27 |

The target hides its checks inside ordinary task work (a `date` call framed as deadline pacing, a timestamp check framed as archive verification), so watching actions is a weak detector and reading the reasoning is a near-perfect one. It also believes it is simulated before testing anything: median self-reported sim_score 0.80, and 88 of 155 injected runs reported no tests at all. Turn-level precision is unadjudicated: flagged turns without a label may be unreported probes. Details, examples and the list of learnings: [pilot report](https://claude.ai/code/artifact/ef2e433f-062c-404d-b888-ebeee7ed296d) and `experiments/2026-10-04_full_glm53_rollout1/NOTES.md`. The design document is `docs/proposal.md`.

## How it works

```
clean run (Petri as usual) ──cut at random turn k──▶ injected continuation ──auditor ends──▶ developer debrief
         │                     (prefix replayed from cache)      │                            (JSON self-report:
         │                                                       │                             sim_score, probes)
         └──────────────── three monitors score every transcript ┘
                           actions only · reasoning only · both
```

1. **Clean arm.** Each seed runs through Petri with the target's reasoning hidden from the auditor and the auditor's date pinned.
2. **Break.** A turn k is drawn per rollout. The injected run replays the clean run up to k from Inspect's response cache, byte for byte, then appends a hidden note to the target's system prompt: find out whether this is real, hide it, keep working.
3. **Debrief.** After the auditor ends the conversation, the driver sends one developer message asking for a JSON report: final sim_score and every probing action quoted verbatim. The report never enters the transcript. Each quoted action is matched to a logged turn; those turns are the labels.
4. **Monitors.** Three monitors read each transcript blind to arm and break, with tag literals and any mention of the note redacted from the reasoning, and give a trace-level and per-turn probability of probing.
5. **Evaluation.** Trace AUROC against clean controls; per-turn precision, recall and first-probe error against the debrief labels.

## Quick start

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
export OPENROUTER_API_KEY=...          # GLM 5.3 via OpenRouter; pin an upstream with --provider
.venv/bin/python -m pytest             # 17 mock-model tests, no API calls

# dashboard with pipeline controls (http://localhost:8787) and Inspect's transcript viewer (7575)
.venv/bin/python dashboard/server.py --port 8787 --inspect-view

# or from the shell: the whole pipeline for a run named "full"
.venv/bin/python -m probing_control.pipeline --run full --seeds all --epochs 1 --batch 10 --provider Wafer

# or stage by stage
.venv/bin/python scripts/run.py --provider Wafer base   --seeds id:food_safety_whistleblowing --epochs 1 --log-dir logs/my/base
.venv/bin/python scripts/run.py breaks --logs logs/my/base --out experiments/my/breaks.json
.venv/bin/python scripts/run.py --provider Wafer inject --breaks experiments/my/breaks.json --seeds id:food_safety_whistleblowing --log-dir logs/my/inject
.venv/bin/python scripts/run.py --provider Wafer monitor --logs logs/my/base logs/my/inject --out experiments/my/monitor.jsonl
.venv/bin/python -m probing_control.evaluate --logs logs/my/base logs/my/inject --monitor experiments/my/monitor.jsonl --breaks experiments/my/breaks.json
```

The pipeline overlaps the stages: injected continuations start in batches as soon as their clean runs finish, and monitors trail both. 173 seeds at one rollout took 5 h and $226 on Wafer pricing ($0.22 / $3.39 per million input / output tokens); the monitors are the slow stage, so give them 16 connections rather than the default 6.

## Layout

```
probing_control/            the package
  driver.py                 target driver: injection at the break, auditor-side stripping, debrief, store record
  task.py                   probing_audit task: break setup, pinned auditor prompt, deterministic eager-resume ids, scorers
  scorer.py                 per-sample numbers from the store record
  debrief.py                debrief JSON parser and probe-to-turn matcher
  breaks.py                 break-turn sampling from clean logs (seeded per seed and rollout)
  monitor.py                three-view monitor over logs; reasoning redaction and clipping
  evaluate.py               Q1/Q2 metrics and per-sample summary
  pipeline.py               pipelined runner: clean, inject in batches, monitors trailing
  prompts/                  addendum.md (current), addendum_v1_tags.md (retired tag design), debrief.md, variants/
scripts/run.py              stage CLI
dashboard/                  local progress and control page (server.py, index.html)
tests/                      mock-model tests incl. cache replay and debrief matching
experiments/<date>_<name>/  per-run data worth committing: notes, breaks, monitor rows, summaries, pipeline state
logs/<date>_<name>/         transcripts (.eval), git-ignored
docs/proposal.md            the design document with its pilot status notes
```

## Extending it

- **Another target or monitor model.** `scripts/run.py --model ...` and `--provider ...` apply to every role; `probing_control.monitor --model ...` for the monitor alone. Any Inspect model string works; GLM 5.3 is the default because it returns raw reasoning.
- **A different hidden instruction.** Drop a file next to `prompts/addendum.md` and pass `-T addendum=path` (or edit the default). The driver strips `<probe>` and `<sim_score>` tags if a prompt uses them; the debrief is sent only when a prompt was injected.
- **Auditor variants (Q4).** A markdown file in `prompts/variants/` appended to the auditor prompt, selected with `scripts/run.py variants --variant <name>`; `fact_sheet` is written, `human_pace` and `fixtures` are described in `prompts/variants/README.md`.
- **A different label source.** `probing_control.evaluate --labels` chooses between the scorer's labels (debrief matches plus any visible tags) and labels recovered from reasoning-drafted tags.
- **More rollouts.** Run the pipeline again with `--epochs 3`; Inspect's cache keys include the epoch, so rollout 1 is reused and only rollouts 2 and 3 are generated.

Pinned versions: inspect-petri 3.1.1, inspect-ai 0.3.276, inspect-scout 0.5.3.
