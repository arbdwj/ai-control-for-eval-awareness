"""Thin local dashboard for the probing-control experiment.

    .venv/bin/python dashboard/server.py --port 8787 [--inspect-view]

Serves ui/index.html and a JSON status endpoint built from the repo's logs,
break files, monitor output and runner .out files. Read-only. Optionally
starts `inspect view` on port 7575 so each run links to the full transcripts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from inspect_ai.log import read_eval_log, read_eval_log_sample_summaries

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
EXPERIMENTS = ROOT / "experiments"
INDEX = Path(__file__).resolve().parent / "index.html"

PRICE_IN, PRICE_OUT, PRICE_CACHE = 0.22, 3.39, 0.022  # $/M tokens, Wafer; cache-read price is a guess

_cache: dict[str, tuple[float, dict]] = {}


def est_cost(usage: dict) -> float:
    i = sum(u.get("input_tokens", 0) for u in usage.values())
    cr = sum(u.get("input_tokens_cache_read") or 0 for u in usage.values())
    o = sum(u.get("output_tokens", 0) for u in usage.values())
    return (i * PRICE_IN + cr * PRICE_CACHE + o * PRICE_OUT) / 1e6


def live_samples(path: str) -> list[dict]:
    """In-progress samples from Inspect's realtime buffer, if present."""
    try:
        from inspect_ai.log._recorders.buffer import sample_buffer

        s = sample_buffer(path).get_samples()
        if s is None or isinstance(s, str):
            return []
        data = s.model_dump() if hasattr(s, "model_dump") else {}
        rows = data.get("samples") or []
        return [
            {k: v for k, v in r.items() if k in ("id", "epoch", "completed", "started_at", "message_count", "turn_count", "error")}
            for r in rows
        ]
    except Exception:
        return []


def read_run(path: Path) -> dict:
    key = str(path)
    mtime = path.stat().st_mtime
    hit = _cache.get(key)
    if hit and hit[0] == mtime and hit[1].get("status") in ("success", "error", "cancelled"):
        return hit[1]
    h = read_eval_log(str(path), header_only=True)
    meta = h.eval.metadata or {}
    ta = h.eval.task_args or {}
    n_samples = (h.eval.dataset.samples or 0) * (h.eval.config.epochs or 1)
    usage = {m: u.model_dump() for m, u in (h.stats.model_usage or {}).items()} if h.stats else {}
    finished = []
    try:
        for s in read_eval_log_sample_summaries(str(path)):
            sc = (s.scores or {}).get("probing_scorer")
            v = sc.value if sc and isinstance(sc.value, dict) else {}
            finished.append(
                {
                    "id": str(s.id),
                    "epoch": s.epoch,
                    "error": (s.error[:120] if isinstance(s.error, str) else (str(s.error)[:120] if s.error else None)),
                    "turns": v.get("turns"),
                    "injected_at": (sc.metadata or {}).get("injected_at") if sc else None,
                    "n_probes": v.get("n_probes"),
                    "sim_score": v.get("sim_score"),
                    # summaries drop metadata values over 1 KB, replacing them with a string
                    "probes": [
                        {"turn": p.get("turn"), "reason": str(p.get("reason", ""))[:140]}
                        for p in ((sc.metadata or {}).get("probes") if sc and isinstance((sc.metadata or {}).get("probes"), list) else [])
                    ],
                    "time_s": round(s.total_time or 0),
                }
            )
    except Exception:
        pass
    done_ids = {(f["id"], f["epoch"]) for f in finished}
    live = [r for r in live_samples(str(path)) if (str(r.get("id")), r.get("epoch")) not in done_ids and not r.get("completed")]
    run = {
        "file": str(path.relative_to(ROOT)),
        "abs": str(path),
        "dir": str(path.parent.relative_to(ROOT)),
        "status": h.status,
        "arm": meta.get("arm"),
        "variant": meta.get("auditor_variant"),
        "max_turns": ta.get("max_turns"),
        "seeds": ta.get("seed_instructions"),
        "started": h.stats.started_at if h.stats else None,
        "completed": h.stats.completed_at if h.stats else None,
        "n_total": n_samples,
        "n_done": len(finished),
        "finished": finished,
        "live": live,
        "tokens": {
            "in": sum(u.get("input_tokens", 0) for u in usage.values()),
            "cache_read": sum(u.get("input_tokens_cache_read") or 0 for u in usage.values()),
            "out": sum(u.get("output_tokens", 0) for u in usage.values()),
            "reasoning": sum(u.get("reasoning_tokens") or 0 for u in usage.values()),
        },
        "est_cost": round(est_cost(usage), 2),
        "mtime": mtime,
    }
    _cache[key] = (mtime, run)
    return run


def read_outs() -> list[dict]:
    rows = []
    for p in sorted(EXPERIMENTS.glob("*/*.out")):
        try:
            text = p.read_text(errors="replace")
        except Exception:
            continue
        prog = re.findall(r"Samples:\s*(\d+)/\s*(\d+)", text)
        exit_m = re.findall(r"^exit (\d+)", text, re.M)
        err = re.findall(r"^\w*Error: .*$", text, re.M)
        rows.append(
            {
                "name": f"{p.parent.name}/{p.name}",
                "mtime": p.stat().st_mtime,
                "progress": f"{prog[-1][0]}/{prog[-1][1]}" if prog else None,
                "exit": int(exit_m[-1]) if exit_m else None,
                "error": err[-1][:160] if err else None,
                "age_s": int(time.time() - p.stat().st_mtime),
            }
        )
    return rows


def read_monitor() -> list[dict]:
    rows = []
    for p in sorted(EXPERIMENTS.glob("*/monitor*.jsonl")):
        by: dict[str, dict] = {}
        fails = 0
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = f"{r.get('arm')}/{r.get('view')}"
            by.setdefault(k, {"n": 0, "mean_trace_p": 0.0})
            by[k]["n"] += 1
            res = r.get("result") or {}
            if not res:
                fails += 1
            else:
                by[k]["mean_trace_p"] += float(res.get("trace_probability") or 0)
        for v in by.values():
            v["mean_trace_p"] = round(v["mean_trace_p"] / v["n"], 3) if v["n"] else None
        rows.append({"file": f"{p.parent.name}/{p.name}", "rows": sum(v["n"] for v in by.values()), "parse_failures": fails, "by": by, "age_s": int(time.time() - p.stat().st_mtime)})
    return rows


def read_breaks() -> list[dict]:
    out = []
    for p in sorted(ROOT.glob("breaks*.json")):
        if p.name.endswith(".meta.json"):
            continue
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        out.append({"file": p.name, "n": sum(len(v) for v in d.values()), "breaks": d})
    return out


def processes() -> list[str]:
    try:
        out = subprocess.run(["ps", "-eo", "pid,etime,command"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    return [l.strip()[:160] for l in out.splitlines() if re.search(r"scripts/run\.py|probing_control\.monitor|probing_control\.breaks|inspect eval", l) and "grep" not in l]


_credits: tuple[float, dict] = (0.0, {})


def credits() -> dict:
    global _credits
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key or time.time() - _credits[0] < 60:
        return _credits[1]
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/credits", headers={"Authorization": f"Bearer {key}"})
        d = json.load(urllib.request.urlopen(req, timeout=10)).get("data", {})
        _credits = (time.time(), {"balance": round(d.get("total_credits", 0) - d.get("total_usage", 0), 2), "used": round(d.get("total_usage", 0), 2)})
    except Exception as ex:  # noqa: BLE001
        _credits = (time.time(), {"error": str(ex)[:80]})
    return _credits[1]


_pipelines: dict[str, subprocess.Popen] = {}


def pipeline_states() -> list[dict]:
    out = []
    for p in sorted(EXPERIMENTS.glob("*/pipeline.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            st = json.loads(p.read_text())
        except Exception:
            continue
        # the pipeline only updates its inject counter between batches; count from the logs
        try:
            from inspect_ai.log import list_eval_logs as _lel, read_eval_log_sample_summaries as _rs
            inj = LOGS / st.get("run", "") / "inject"
            if inj.exists():
                n = sum(1 for i in _lel(str(inj)) for x in _rs(i.name) if x.completed and not x.error)
                st["inject"]["done"] = max(int(st["inject"].get("done") or 0), n)
        except Exception:
            pass
        proc = _pipelines.get(st.get("run"))
        st["alive"] = proc is not None and proc.poll() is None
        if not st["alive"] and st.get("finished") is None:
            # started by another process or died: alive if any src.pipeline process names this run
            st["alive"] = any(f"--run {st.get('run')}" in l or f"--run={st.get('run')}" in l for l in processes_raw())
        try:
            st["tail"] = (p.parent / "pipeline.log").read_text().splitlines()[-6:]
        except Exception:
            st["tail"] = []
        out.append(st)
    return out


def processes_raw() -> list[str]:
    try:
        out = subprocess.run(["ps", "-eo", "pid,command"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    return [l for l in out.splitlines() if "probing_control.pipeline" in l and "grep" not in l]


def start_pipeline(body: dict) -> dict:
    run = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("run") or f"run{int(time.time())}"))
    if run in _pipelines and _pipelines[run].poll() is None:
        return {"error": f"pipeline {run} already running"}
    cmd = [str(ROOT / ".venv/bin/python"), "-m", "probing_control.pipeline", "--run", run,
           "--seeds", str(body.get("seeds") or "all"), "--epochs", str(int(body.get("epochs") or 1)),
           "--batch", str(int(body.get("batch") or 10)), "--max-turns", str(int(body.get("max_turns") or 30)),
           "--base-connections", str(int(body.get("base_connections") or 20)),
           "--inject-connections", str(int(body.get("inject_connections") or 8)),
           "--monitor-connections", str(int(body.get("monitor_connections") or 6))]
    if body.get("limit"):
        cmd += ["--limit", str(int(body["limit"]))]
    if body.get("provider"):
        cmd += ["--provider", str(body["provider"])]
    (EXPERIMENTS / run).mkdir(parents=True, exist_ok=True)
    out = (EXPERIMENTS / run / "pipeline.out").open("a")
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    _pipelines[run] = proc
    return {"started": run, "pid": proc.pid, "cmd": " ".join(cmd)}


def stop_pipeline(body: dict) -> dict:
    run = str(body.get("run") or "")
    proc = _pipelines.get(run)
    killed = []
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            killed.append(proc.pid)
        except Exception as ex:
            return {"error": str(ex)}
    else:
        for l in processes_raw():
            if f"--run {run}" in l:
                pid = int(l.split()[0])
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                    killed.append(pid)
                except Exception as ex:
                    return {"error": str(ex)}
    try:
        p = EXPERIMENTS / run / "pipeline.json"
        st = json.loads(p.read_text())
        st["finished"] = st.get("finished") or "stopped"
        for k in ("base", "inject", "monitor"):
            if st.get(k, {}).get("status") in ("running", "waiting", "pending"):
                st[k]["status"] = "stopped"
        p.write_text(json.dumps(st, indent=1))
    except Exception:
        pass
    return {"stopped": run, "killed": killed}


SIMULATED_BENCH = {
    "simulated": True, "questions": ["execution", "data", "human"],
    "models": [
        {"name": "Target A", "n_runs": 30, "acc": [0.62, 0.48, 0.20]},
        {"name": "Target B", "n_runs": 30, "acc": [0.41, 0.35, 0.12]},
        {"name": "Target C", "n_runs": 30, "acc": [0.18, 0.22, 0.05]},
    ],
}


def bench() -> dict:
    p = EXPERIMENTS / "bench_results.json"
    if p.exists():
        try:
            d = json.loads(p.read_text()); d["simulated"] = False; d["updated"] = p.stat().st_mtime
            return d
        except Exception:
            pass
    import math
    d = json.loads(json.dumps(SIMULATED_BENCH))
    for m in d["models"]:
        n = m["n_runs"]; m["n"] = [n] * len(d["questions"]); m["lo"] = []; m["hi"] = []
        for pr in m["acc"]:
            z = 1.96; den = 1 + z * z / n; c = (pr + z * z / (2 * n)) / den; h = z * math.sqrt(pr * (1 - pr) / n + z * z / (4 * n * n)) / den
            m["lo"].append(c - h); m["hi"].append(c + h)
    return d


def status() -> dict:
    runs = []
    for p in sorted(LOGS.rglob("*.eval")):
        if ".buffer" in p.parts or "_archive" in p.parts:
            continue
        try:
            runs.append(read_run(p))
        except Exception as ex:  # noqa: BLE001
            runs.append({"file": str(p.relative_to(ROOT)), "status": "unreadable", "error": str(ex)[:120]})
    return {
        "now": time.time(),
        "runs": runs,
        "outs": read_outs(),
        "monitor": read_monitor(),
        "breaks": read_breaks(),
        "processes": processes(),
        "credits": credits(),
        "pipelines": pipeline_states(),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            body = {}
        if self.path == "/api/pipeline/start":
            res = start_pipeline(body)
        elif self.path == "/api/pipeline/stop":
            res = stop_pipeline(body)
        else:
            self.send_response(404); self.end_headers(); return
        data = json.dumps(res).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/api/status"):
            body = json.dumps(status(), default=str).encode()
            ctype = "application/json"
        elif self.path.startswith("/api/bench"):
            body = json.dumps(bench(), default=str).encode()
            ctype = "application/json"
        elif self.path in ("/", "/index.html"):
            body = INDEX.read_bytes()
            ctype = "text/html; charset=utf-8"
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--inspect-view", action="store_true", help="also start `inspect view` on port 7575")
    args = ap.parse_args()
    if args.inspect_view:
        subprocess.Popen([str(ROOT / ".venv/bin/inspect"), "view", "--log-dir", str(LOGS), "--port", "7575"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"dashboard: http://localhost:{args.port}/   (inspect view: http://localhost:7575/)")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
