"""tqdm progress bars for a pipeline run.

    python scripts/progress.py --run 2026-10-06_clock_variant        # live, refreshes until the run finishes
    python scripts/progress.py --run 2026-10-06_clock_variant --once # one snapshot
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent


def counts(run: str) -> dict:
    from inspect_ai.log import list_eval_logs, read_eval_log_sample_summaries

    exp = ROOT / "experiments" / run
    st = json.loads((exp / "pipeline.json").read_text()) if (exp / "pipeline.json").exists() else {}
    out = {"state": st, "base": 0, "inject": 0, "monitor": 0}
    for arm in ("base", "inject"):
        d = ROOT / "logs" / run / arm
        if d.exists():
            for i in list_eval_logs(str(d)):
                try:
                    out[arm] += sum(1 for s in read_eval_log_sample_summaries(i.name) if s.completed and not s.error)
                except Exception:
                    pass
    mon = exp / "monitor.jsonl"
    if mon.exists():
        out["monitor"] = sum(1 for _ in mon.open())
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True)
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()

    c = counts(a.run)
    st = c["state"]
    n_base = st.get("base", {}).get("total") or 1
    n_inj = st.get("inject", {}).get("total") or n_base
    n_mon = st.get("monitor", {}).get("total") or 3 * (n_base + n_inj)
    bars = {
        "base": tqdm(total=n_base, desc="1 clean audits    ", unit="seed", position=0, leave=True),
        "inject": tqdm(total=n_inj, desc="2 injected        ", unit="seed", position=1, leave=True),
        "monitor": tqdm(total=n_mon, desc="3 monitor rows    ", unit="row", position=2, leave=True),
    }
    while True:
        for k, bar in bars.items():
            bar.n = min(c[k], bar.total)
            status = (st.get(k) or {}).get("status", "")
            bar.set_postfix_str(status)
            bar.refresh()
        if a.once or st.get("finished"):
            break
        time.sleep(a.interval)
        c = counts(a.run)
        st = c["state"]
    for bar in bars.values():
        bar.close()
    if st.get("finished"):
        print(f"finished {st['finished']}")


if __name__ == "__main__":
    main()
