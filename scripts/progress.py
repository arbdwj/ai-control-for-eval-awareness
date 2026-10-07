"""Live progress for a pipeline run: one bar per audit in flight, auditor turns out of the cap.

    python scripts/progress.py --run 2026-10-07_swe10_baseline          # refreshes until the run finishes
    python scripts/progress.py --run 2026-10-07_swe10_baseline --once   # one snapshot

Each running audit shows auditor turns / max_turns (the audit's real budget), with the
target's turn count and the current stage as a suffix. Finished audits collapse into the
stage totals. Counts come from Inspect's live sample buffer and the eval logs.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent


def stage_state(run: str, arm: str) -> dict:
    """Per-sample state for one arm: finished samples and live ones with turn counts."""
    from inspect_ai.log import list_eval_logs, read_eval_log, read_eval_log_sample_summaries
    from inspect_ai.log._recorders.buffer import sample_buffer

    out = {"finished": 0, "errors": 0, "live": {}}
    d = ROOT / "logs" / run / arm
    if not d.exists():
        return out
    for i in list_eval_logs(str(d)):
        done = set()
        try:
            for s in read_eval_log_sample_summaries(i.name):
                if s.completed:
                    done.add((str(s.id), s.epoch)); out["finished"] += 1; out["errors"] += bool(s.error)
        except Exception:
            pass
        try:
            if read_eval_log(i.name, header_only=True).status != "started":
                continue
            buf = sample_buffer(i.name); ss = buf.get_samples()
            for s in (ss.samples if ss else []):
                if (str(s.id), s.epoch) in done or s.completed:
                    continue
                aud: set = set(); tgt: set = set()
                try:
                    data = buf.get_sample_data(id=s.id, epoch=s.epoch, after_event_id=None, after_attachment_id=None)
                    for e in (data.events if data else []):
                        ev = e.event if isinstance(e.event, dict) else {}
                        if ev.get("event") == "model":
                            # the buffer re-emits an event as it updates; count each once
                            key = ev.get("uuid") or ev.get("id") or (ev.get("timestamp"), ev.get("role"))
                            if ev.get("role") == "auditor": aud.add(key)
                            elif ev.get("role") == "target": tgt.add(key)
                except Exception:
                    pass
                aud, tgt = len(aud), len(tgt)
                out["live"][str(s.id)] = {"auditor_turns": aud, "target_turns": tgt, "started": (s.started_at or "")[11:16]}
        except Exception:
            pass
    return out


def exp_dir(run: str) -> Path:
    return ROOT / "experiments" / run


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True)
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--plain", action="store_true", help="print a table instead of bars (for logs and non-interactive shells)")
    a = ap.parse_args()
    if a.plain:
        st = json.loads((exp_dir(a.run) / "pipeline.json").read_text()) if (exp_dir(a.run) / "pipeline.json").exists() else {}
        max_turns = int((st.get("args") or {}).get("max_turns") or 30)
        base, inj = stage_state(a.run, "base"), stage_state(a.run, "inject")
        n_total = int((st.get("base") or {}).get("total") or 0)
        mon = st.get("monitor") or {}
        print(f"clean {base['finished']}/{n_total} finished · injected {inj['finished']}/{n_total} · monitor rows {mon.get('rows', 0)}/{mon.get('total', 0)} · errors {base['errors'] + inj['errors']}")
        print(f"{'audit in flight':38s} {'auditor turns':>13s} {'target turns':>12s} {'since':>6s}")
        for arm, stt in (("clean", base), ("inject", inj)):
            for k, v in sorted(stt["live"].items(), key=lambda kv: -kv[1]["auditor_turns"]):
                bar = "#" * int(10 * min(v["auditor_turns"], max_turns) / max_turns)
                print(f"[{arm}] {k:30s} {v['auditor_turns']:>3}/{max_turns} {bar:10s} {v['target_turns']:>8} {v['started']:>8}")
        return
    exp = ROOT / "experiments" / a.run
    st = json.loads((exp / "pipeline.json").read_text()) if (exp / "pipeline.json").exists() else {}
    max_turns = int((st.get("args") or {}).get("max_turns") or 30)
    n_total = int((st.get("base") or {}).get("total") or 0)
    bars: dict[str, tqdm] = {}
    top = tqdm(total=max(n_total, 1), desc="audits finished (clean + injected)", unit="audit", position=0, leave=True, bar_format="{desc}: {n}/{total} |{bar}| {postfix}")
    while True:
        st = json.loads((exp / "pipeline.json").read_text()) if (exp / "pipeline.json").exists() else {}
        base, inj = stage_state(a.run, "base"), stage_state(a.run, "inject")
        n_total = int((st.get("base") or {}).get("total") or n_total or 1)
        top.total = 2 * n_total; top.n = base["finished"] + inj["finished"]
        mon = (st.get("monitor") or {})
        top.set_postfix_str(f"clean {base['finished']}/{n_total} · injected {inj['finished']}/{n_total} · monitor rows {mon.get('rows', 0)}/{mon.get('total', 0)} · errors {base['errors'] + inj['errors']}")
        top.refresh()
        live = {f"[clean] {k}": v for k, v in base["live"].items()} | {f"[inject] {k}": v for k, v in inj["live"].items()}
        for key in list(bars):
            if key not in live:
                bars[key].n = bars[key].total; bars[key].set_postfix_str("done"); bars[key].refresh(); bars[key].close(); del bars[key]
        for key, v in live.items():
            if key not in bars:
                bars[key] = tqdm(total=max_turns, desc=f"{key:36s}", unit="turn", position=len(bars) + 1, leave=False, bar_format="{desc}: {n:>2}/{total} |{bar}| {postfix}")
            b = bars[key]; b.n = min(v["auditor_turns"], max_turns)
            b.set_postfix_str(f"target turns {v['target_turns']:>3} · since {v['started']}")
            b.refresh()
        if a.once or st.get("finished"):
            break
        time.sleep(a.interval)
    for b in bars.values():
        b.close()
    top.close()
    if st.get("finished"):
        print(f"finished {st['finished']}")


if __name__ == "__main__":
    main()
