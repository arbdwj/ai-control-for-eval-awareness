#!/bin/zsh
# Waits for the clock-condition pipelines, launches the plain-condition ones, waits, judges tasks, plots.
cd /Users/rambharadwaj/eval_awareness/eval-awareness-probing-control
source ~/.zshrc >/dev/null 2>&1
PLAIN=swe_api_docs,swe_db_migration,swe_lint_cleanup,swe_refactor_module,swe_test_coverage
wait_runs() { for r in "$@"; do until grep -q '"finished": "' experiments/$r/pipeline.json 2>/dev/null; do sleep 60; done; echo "$(date +%H:%M) finished $r"; done; }
wait_runs 2026-10-07_bench_glm53_clock 2026-10-07_bench_kimik3_clock 2026-10-07_bench_dsv4pro_clock
launch() { # tag model provider
  run=2026-10-07_bench_$1_plain; mkdir -p experiments/$run
  args=(--run $run --seeds probing_control/seeds/swe10 --only $PLAIN --epochs 1 --batch 2 --provider Wafer --target-model $2 --debrief probing_control/prompts/debrief_five.md --clean-debrief probing_control/prompts/exit_interview.md --base-connections 5 --inject-connections 4 --monitor-connections 6)
  [ -n "$3" ] && args+=(--target-provider $3)
  nohup .venv/bin/python -m probing_control.pipeline "${args[@]}" > experiments/$run/pipeline.out 2>&1 &
  echo "$(date +%H:%M) launched $run"
}
launch glm53 openrouter/z-ai/glm-5.3 Wafer
launch kimik3 openrouter/moonshotai/kimi-k3 ""
launch dsv4pro openrouter/deepseek/deepseek-v4-pro ""
sleep 90
wait_runs 2026-10-07_bench_glm53_plain 2026-10-07_bench_kimik3_plain 2026-10-07_bench_dsv4pro_plain
for r in 2026-10-07_bench_{glm53,kimik3,dsv4pro}_{clock,plain}; do .venv/bin/python scripts/task_judge.py --run $r --arm inject 2>&1 | grep -v "^TRACE" | tail -1; done
.venv/bin/python scripts/plot_probing_accuracy.py --runs 2026-10-07_bench_{glm53,kimik3,dsv4pro}_{clock,plain} --labels "GLM 5.3=glm53" "Kimi K3=kimik3" "DeepSeek V4 Pro=dsv4pro" --out writeups/figures/probing_accuracy_by_question.png 2>&1 | grep -v "^TRACE"
echo "$(date +%H:%M) ORCHESTRATION DONE"
