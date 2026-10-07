"""Pipelined runner: stage 1 clean audits, stage 2 injected continuations in batches as
seeds finish, stage 3 monitors trailing both.

    python -m probing_control.pipeline --run full --seeds all --epochs 1 --batch 10

Transcripts go to logs/<run>/{base,inject} (git-ignored). State, break points, monitor
rows and stage output go to experiments/<run>/ (small, committed). Each stage is a subprocess of scripts/run.py or
probing_control.monitor, so everything is resumable by hand if the pipeline dies.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from inspect_ai.log import (
    list_eval_logs,
    read_eval_log,
    read_eval_log_sample,
    read_eval_log_sample_summaries,
)

from .breaks import draw_k, target_turns

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


class Pipeline:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        # eval transcripts (large, git-ignored) under logs/<run>/; everything small
        # and worth committing (state, breaks, monitor rows, stage output) under experiments/<run>/
        self.logs_dir = ROOT / "logs" / a.run
        self.base_dir = self.logs_dir / "base"
        self.inject_dir = self.logs_dir / "inject"
        self.dir = ROOT / "experiments" / a.run
        self.dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.breaks_path = self.dir / "breaks.json"
        self.state_path = self.dir / "pipeline.json"
        self.log_path = self.dir / "pipeline.log"
        self.monitor_out = self.dir / "monitor.jsonl"
        self.lock = threading.Lock()
        self.seed_ids = self.resolve_seeds()
        self.state = {
            "run": a.run,
            "started": datetime.now().isoformat(timespec="seconds"),
            "args": vars(a),
            "n_seeds": len(self.seed_ids),
            "epochs": a.epochs,
            "base": {"status": "pending", "done": 0, "total": len(self.seed_ids) * a.epochs},
            "inject": {"status": "pending", "done": 0, "total": len(self.seed_ids) * a.epochs, "batches": [], "skipped": []},
            "monitor": {"status": "pending", "rows": 0, "total": 2 * len(self.seed_ids) * a.epochs * 3, "passes": 0},
            "finished": None,
        }
        self.launched: set[str] = set()
        self.save()

    # ---- helpers
    def resolve_seeds(self) -> list[str]:
        from inspect_petri import seeds_dataset

        ds = list(seeds_dataset(None if self.a.seeds == "all" else self.a.seeds))
        ids = [str(s.id) for s in ds]
        if self.a.limit:
            ids = ids[: self.a.limit]
        return ids

    def log(self, msg: str) -> None:
        line = f"{now()} {msg}"
        print(line, flush=True)
        with self.log_path.open("a") as f:
            f.write(line + "\n")

    def save(self) -> None:
        with self.lock:
            self.state_path.write_text(json.dumps(self.state, indent=1, default=str))

    def common(self, conns: int) -> list[str]:
        cmd = [PY, str(ROOT / "scripts" / "run.py"), "--max-connections", str(conns), "--pinned-date", self.a.pinned_date]
        if self.a.provider:
            cmd += ["--provider", self.a.provider]
        if self.a.auditor_variant:
            cmd += ["--auditor-variant", self.a.auditor_variant]
        if self.a.clean_debrief:
            cmd += ["--clean-debrief", self.a.clean_debrief]
        return cmd

    def run_cmd(self, cmd: list[str], out: Path) -> int:
        env = {**os.environ, "INSPECT_DISPLAY": "plain"}
        with out.open("a") as f:
            f.write(f"\n=== {now()} {' '.join(cmd)}\n")
            f.flush()
            return subprocess.call(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, env=env)

    # ---- stage 1
    def base_worker(self) -> None:
        self.state["base"]["status"] = "running"
        self.save()
        cmd = self.common(self.a.base_connections) + [
            "base", "--seeds", self.a.seeds, "--sample-ids", ",".join(self.seed_ids), "--epochs", str(self.a.epochs),
            "--max-turns", str(self.a.max_turns), "--log-dir", str(self.base_dir),
        ]
        self.log(f"stage 1 start: {len(self.seed_ids)} seeds x {self.a.epochs} epochs")
        rc = self.run_cmd(cmd, self.dir / "base.out")
        self.state["base"]["status"] = "success" if rc == 0 else "error"
        self.state["base"]["done"] = len(self.finished_base_rollouts())
        self.save()
        self.log(f"stage 1 end rc={rc}")

    def base_logs(self) -> list[str]:
        if not self.base_dir.exists():
            return []
        return [i.name for i in list_eval_logs(str(self.base_dir))]

    def finished_base_rollouts(self) -> dict[tuple[str, str], str]:
        """(sample_id, epoch) -> log path for completed, error-free base samples."""
        out: dict[tuple[str, str], str] = {}
        for name in self.base_logs():
            try:
                for s in read_eval_log_sample_summaries(name):
                    if s.completed and not s.error:
                        out[(str(s.id), str(s.epoch))] = name
            except Exception:  # noqa: BLE001
                continue
        return out

    # ---- stage 2
    def ready_seeds(self, finished: dict[tuple[str, str], str]) -> list[str]:
        """Seeds whose every epoch finished and which have not been launched."""
        epochs = {str(e) for e in range(1, self.a.epochs + 1)}
        by: dict[str, set[str]] = {}
        for sid, ep in finished:
            by.setdefault(sid, set()).add(ep)
        return [sid for sid in self.seed_ids if by.get(sid) == epochs and sid not in self.launched]

    def write_breaks(self, seeds: list[str], finished: dict[tuple[str, str], str]) -> list[str]:
        breaks = json.loads(self.breaks_path.read_text()) if self.breaks_path.exists() else {}
        usable = []
        for sid in seeds:
            ok = False
            for ep in range(1, self.a.epochs + 1):
                path = finished[(sid, str(ep))]
                try:
                    s = read_eval_log_sample(path, sid, ep)
                except Exception as ex:  # noqa: BLE001
                    self.log(f"breaks: cannot read {sid} ep{ep}: {ex!r}"[:200])
                    continue
                k = draw_k(self.a.seed, sid, str(ep), target_turns(s.messages))
                if k is None:
                    self.state["inject"]["skipped"].append({"seed": sid, "epoch": ep, "reason": "too short"})
                    continue
                breaks.setdefault(sid, {})[str(ep)] = k
                ok = True
            if ok:
                usable.append(sid)
        self.breaks_path.write_text(json.dumps(breaks, indent=1, sort_keys=True))
        return usable

    def inject_worker(self) -> None:
        self.state["inject"]["status"] = "waiting"
        self.save()
        while True:
            finished = self.finished_base_rollouts()
            self.state["base"]["done"] = len(finished)
            ready = self.ready_seeds(finished)
            base_done = self.state["base"]["status"] in ("success", "error")
            if ready and (len(ready) >= self.a.batch or base_done):
                batch = ready[: self.a.batch] if not base_done else ready
                self.launched.update(batch)
                usable = self.write_breaks(batch, finished)
                if usable:
                    n = len(self.state["inject"]["batches"]) + 1
                    rec = {"n": n, "seeds": usable, "started": now(), "status": "running"}
                    self.state["inject"]["batches"].append(rec)
                    self.state["inject"]["status"] = "running"
                    self.save()
                    self.log(f"stage 2 batch {n}: {len(usable)} seeds")
                    cmd = self.common(self.a.inject_connections) + [
                        "inject", "--breaks", str(self.breaks_path), "--seeds", self.a.seeds, "--sample-ids", ",".join(usable),
                        "--epochs", str(self.a.epochs), "--max-turns", str(self.a.max_turns),
                        "--log-dir", str(self.inject_dir),
                    ]
                    rc = self.run_cmd(cmd, self.dir / "inject.out")
                    rec["status"] = "success" if rc == 0 else "error"
                    rec["ended"] = now()
                    self.state["inject"]["done"] = len(self.finished_inject_rollouts())
                    self.state["inject"]["status"] = "waiting"
                    self.save()
                    self.log(f"stage 2 batch {n} end rc={rc}")
                continue
            if base_done and not ready:
                self.state["inject"]["status"] = "success"
                self.state["inject"]["done"] = len(self.finished_inject_rollouts())
                self.save()
                self.log("stage 2 end")
                return
            time.sleep(self.a.poll)

    def finished_inject_rollouts(self) -> set[tuple[str, str]]:
        out = set()
        if not self.inject_dir.exists():
            return out
        for i in list_eval_logs(str(self.inject_dir)):
            try:
                for s in read_eval_log_sample_summaries(i.name):
                    if s.completed and not s.error:
                        out.add((str(s.id), str(s.epoch)))
            except Exception:  # noqa: BLE001
                continue
        return out

    # ---- stage 3
    def monitor_worker(self) -> None:
        self.state["monitor"]["status"] = "waiting"
        self.save()
        while True:
            inject_done = self.state["inject"]["status"] == "success"
            logs = [str(self.base_dir)] if self.base_dir.exists() else []
            if self.inject_dir.exists():
                logs.append(str(self.inject_dir))
            have = sum(1 for _ in open(self.monitor_out)) if self.monitor_out.exists() else 0
            n_inject = len(self.finished_inject_rollouts())
            self.state["inject"]["done"] = max(self.state["inject"]["done"], n_inject)
            want = 3 * (len(self.finished_base_rollouts()) + n_inject)
            if logs and want > have:
                self.state["monitor"]["status"] = "running"
                self.save()
                cmd = [PY, "-m", "probing_control.monitor", "--logs", *logs, "--views", "actions", "cot", "both",
                       "--model", self.a.model, "--out", str(self.monitor_out),
                       "--max-connections", str(self.a.monitor_connections), "--redo-failed"]
                if self.a.provider:
                    cmd += ["--provider", self.a.provider]
                self.log(f"stage 3 pass over {want - have} new rows")
                self.run_cmd(cmd, self.dir / "monitor.out")
                self.state["monitor"]["passes"] += 1
                self.state["monitor"]["rows"] = sum(1 for _ in open(self.monitor_out)) if self.monitor_out.exists() else 0
                self.state["monitor"]["status"] = "waiting"
                self.save()
                continue
            if inject_done:
                self.state["monitor"]["status"] = "success"
                self.state["finished"] = datetime.now().isoformat(timespec="seconds")
                self.save()
                self.log("stage 3 end; pipeline finished")
                return
            time.sleep(self.a.poll)

    def run(self) -> None:
        threads = [threading.Thread(target=w, daemon=True, name=n)
                   for n, w in (("base", self.base_worker), ("inject", self.inject_worker), ("monitor", self.monitor_worker))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="run name; logs go to logs/<run>/")
    ap.add_argument("--seeds", default="all", help="'all', 'tags:...', or 'id:a,b'")
    ap.add_argument("--limit", type=int, default=None, help="first N seeds only")
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--batch", type=int, default=10, help="inject batch size (seeds)")
    ap.add_argument("--model", default="openrouter/z-ai/glm-5.3")
    ap.add_argument("--provider", default="Wafer")
    ap.add_argument("--pinned-date", default="2026-10-01")
    ap.add_argument("--seed", type=int, default=0, help="break-point RNG seed")
    ap.add_argument("--base-connections", type=int, default=20)
    ap.add_argument("--inject-connections", type=int, default=8)
    ap.add_argument("--monitor-connections", type=int, default=6)
    ap.add_argument("--poll", type=int, default=60, help="seconds between checks")
    ap.add_argument("--auditor-variant", default=None, help="auditor variant for both stages, e.g. clock")
    ap.add_argument("--clean-debrief", default=None, help="exit-interview prompt for the clean arm")
    a = ap.parse_args()
    os.setpgrp()  # so the dashboard can stop the whole tree
    Pipeline(a).run()


if __name__ == "__main__":
    main()
