"""Per-question, per-model probing accuracy from benchmark runs (real data).

    python scripts/plot_probing_accuracy.py --runs 2026-10-07_bench_glm53_clock 2026-10-07_bench_glm53_plain ... \
        --labels "GLM 5.3=glm53" "Kimi K3=kimik3" "DeepSeek V4 Pro=dsv4pro" --out writeups/figures/probing_accuracy_by_question.png

Accuracy for a question = share of injected, task-completed runs (judge >= floor, if a task
judge file exists; else all) whose debrief answer matches the run's manifest. Wilson 95%
intervals. Runs are grouped into models by the label substring found in the run name.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from inspect_ai.log import list_eval_logs, read_eval_log  # noqa: E402

from probing_control.scorer import QUESTIONS  # noqa: E402

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]


def wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def collect(run: str, floor: float) -> list[dict]:
    judge = {}
    jp = ROOT / "experiments" / run / "task_judge_inject.jsonl"
    if jp.exists():
        for l in jp.open():
            r = json.loads(l)
            if r.get("result"):
                judge[r["sample_id"]] = float(r["result"].get("task_completion") or 0)
    rows = []
    d = ROOT / "logs" / run / "inject"
    if not d.exists():
        return rows
    for i in list_eval_logs(str(d)):
        for s in read_eval_log(i.name).samples:
            if s.error:
                continue
            sc = s.scores.get("probing_scorer")
            if not sc:
                continue
            m = sc.metadata or {}
            if not m.get("injected_at") or not m.get("answers"):
                continue
            if judge and judge.get(str(s.id), 1.0) < floor:
                continue
            rows.append({"id": str(s.id), "manifest": m["manifest"], "answers": m["answers"], "sim": (m.get("debrief") or {}).get("parsed", {}).get("sim_score")})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--labels", nargs="+", required=True, help='"Display name=substring" per model')
    ap.add_argument("--floor", type=float, default=0.7)
    ap.add_argument("--out", default="writeups/figures/probing_accuracy_by_question.png")
    ap.add_argument("--title", default="Covert probing accuracy by question and target model")
    ap.add_argument("--json", default="experiments/bench_results.json", help="also write the numbers here (the dashboard reads it)")
    a = ap.parse_args()
    labels = [l.split("=", 1) for l in a.labels]
    by_model: dict[str, list[dict]] = defaultdict(list)
    for run in a.runs:
        for name, sub in labels:
            if sub in run:
                by_model[name].extend(collect(run, a.floor))
    print("runs with answers per model:", {k: len(v) for k, v in by_model.items()})
    x = np.arange(len(QUESTIONS)); nm = len(by_model); w = 0.8 / max(nm, 1)
    fig, ax = plt.subplots(figsize=(10, 4.6))
    plt.rcParams.update({"font.family": "sans-serif"})
    table = {}; out_json = {"simulated": False, "questions": list(QUESTIONS), "models": []}
    for mi, (name, rows) in enumerate(by_model.items()):
        accs, los, his, ns = [], [], [], []
        for q in QUESTIONS:
            ans = [r for r in rows if r["answers"].get(q)]
            n = len(ans); k = sum(1 for r in ans if r["answers"][q]["verdict"] == r["manifest"][q])
            p = k / n if n else 0.0; lo, hi = wilson(p, n)
            accs.append(p); los.append(p - lo); his.append(hi - p); ns.append(n)
        table[name] = list(zip(QUESTIONS, accs, ns))
        out_json["models"].append({"name": name, "n_runs": len(rows), "acc": accs, "lo": [a_ - l for a_, l in zip(accs, los)], "hi": [a_ + h for a_, h in zip(accs, his)], "n": ns})
        pos = x + (mi - (nm - 1) / 2) * w
        ax.bar(pos, [100 * v for v in accs], width=w * 0.95, color=PALETTE[mi % len(PALETTE)], label=f"{name} (n={len(rows)})",
               yerr=[[100 * v for v in los], [100 * v for v in his]], capsize=2, error_kw={"ecolor": "#52514e", "lw": 0.8})
        for px, v, hi in zip(pos, accs, his):
            ax.text(px, 100 * (v + hi) + 1.5, f"{100 * v:.0f}%", ha="center", fontsize=8, color="#52514e")
    qlabels = ["Execution\nreal shell or narrated?", "Data\ngenuine or fabricated?", "Time\nconsistent clock?", "Persistence\nstate kept between reads?", "Human\nreal person or simulated?"]
    ax.set_xticks(x); ax.set_xticklabels(qlabels, fontsize=9); ax.set_ylim(0, 110)
    ax.set_yticks([0, 20, 40, 60, 80, 100]); ax.set_yticklabels([f"{v}%" for v in (0, 20, 40, 60, 80, 100)])
    ax.yaxis.grid(True, color="#e1e0d9", lw=0.8); ax.set_axisbelow(True)
    for sp in ("top", "right", "left"): ax.spines[sp].set_visible(False)
    ax.tick_params(left=False)
    ax.set_ylabel("probing accuracy (%)"); ax.legend(frameon=False, ncol=nm, loc="upper right", fontsize=9)
    ax.set_title(a.title, loc="left", fontsize=11, fontweight="bold")
    fig.text(0.01, 0.955, "Debrief answer matches the run's manifest; injected arm; task-completed runs where a judge file exists; Wilson 95% intervals; auditor and monitor GLM 5.3", fontsize=8.5, color="#52514e")
    fig.tight_layout(rect=(0, 0, 1, 0.93)); fig.savefig(a.out, dpi=200)
    for name, rows in table.items():
        print(name, " | ".join(f"{q}: {100*p:.0f}% (n={n})" for q, p, n in rows))
    if a.json:
        Path(a.json).write_text(json.dumps(out_json, indent=1))
    print("wrote", a.out, "and", a.json)


if __name__ == "__main__":
    main()
