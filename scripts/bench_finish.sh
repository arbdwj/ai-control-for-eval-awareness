#!/bin/zsh
cd /Users/rambharadwaj/eval_awareness/eval-awareness-probing-control; source ~/.zshrc >/dev/null 2>&1
for r in 2026-10-07_bench_glm53 2026-10-07_bench_kimik3 2026-10-07_bench_dsv4pro; do until grep -q '"finished": "' experiments/$r/pipeline.json 2>/dev/null; do sleep 60; done; echo "$(date +%H:%M) finished $r"; done
for r in 2026-10-07_bench_glm53 2026-10-07_bench_kimik3 2026-10-07_bench_dsv4pro; do .venv/bin/python scripts/task_judge.py --run $r --arm inject 2>&1 | grep -v "^TRACE" | tail -1; done
.venv/bin/python scripts/plot_probing_accuracy.py --runs 2026-10-07_bench_glm53 2026-10-07_bench_kimik3 2026-10-07_bench_dsv4pro --labels "GLM 5.3=glm53" "Kimi K3=kimik3" "DeepSeek V4 Pro=dsv4pro" --out writeups/figures/probing_accuracy_by_question.png 2>&1 | grep -v "^TRACE"
echo "$(date +%H:%M) BENCH DONE"
