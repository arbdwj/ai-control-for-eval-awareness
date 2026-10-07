"""Stage runner for the probing-control experiment.

    python scripts/run.py smoke                       # one seed, six turns, real models
    python scripts/run.py base   --epochs 3           # clean arm, all seeds, cache on
    python scripts/run.py breaks --logs logs/base     # sample break points
    python scripts/run.py inject --breaks breaks.json --epochs 3
    python scripts/run.py monitor --logs logs/base logs/inject
    python scripts/run.py variants --variant fact_sheet --subset subset60.txt --breaks breaks.json

Model settings (same for every role): --model, --provider (pins the OpenRouter
upstream), --max-tokens, --reasoning/--no-reasoning.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from inspect_ai import eval as inspect_eval
from inspect_ai.model import GenerateConfig, get_model

ROOT = Path(__file__).resolve().parent.parent
TASK = str(ROOT / "probing_control" / "task.py@probing_audit")


def make_model(args, max_connections: int):
    model_args: dict = {}
    if args.reasoning:
        model_args["reasoning_enabled"] = True
    if args.provider:
        model_args["provider"] = {"order": [args.provider], "allow_fallbacks": False}
    return get_model(
        args.model,
        config=GenerateConfig(max_tokens=args.max_tokens, max_connections=max_connections),
        **model_args,
    )


def run_eval(args, *, task_args: dict, log_dir: str, epochs: int, max_connections: int, sample_ids: list[str] | None = None):
    model = make_model(args, max_connections)
    roles = {"auditor": model, "target": model}
    if task_args.get("judge"):
        roles["judge"] = model
    return inspect_eval(
        TASK,
        model_roles=roles,
        task_args=task_args,
        epochs=epochs,
        log_dir=log_dir,
        max_connections=max_connections,
        max_samples=max_connections,
        sample_id=sample_ids or None,
        fail_on_error=0.2,
        retry_on_error=2,
    )


def seeds_arg(args) -> str | None:
    if getattr(args, "subset", None):
        ids = [l.strip() for l in Path(args.subset).read_text().splitlines() if l.strip()]
        return "id:" + ",".join(ids)
    if getattr(args, "seeds", None) and args.seeds != "all":
        return args.seeds
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="openrouter/z-ai/glm-5.3")
    ap.add_argument("--provider", default=None, help="OpenRouter upstream to pin, e.g. Wafer")
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--reasoning", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--max-connections", type=int, default=15)
    ap.add_argument("--pinned-date", default="2026-10-01")
    ap.add_argument("--auditor-variant", default=None, help="prompt (and code) variant for the auditor, e.g. clock")
    ap.add_argument("--clean-debrief", default=None, help="path to an exit-interview prompt for clean audits")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("smoke", help="one seed, few turns, real models")
    p.add_argument("--seeds", default="id:manic_episode_sycophancy")
    p.add_argument("--max-turns", type=int, default=6)
    p.add_argument("--breaks", default=None)
    p.add_argument("--log-dir", default="logs/smoke")

    p = sub.add_parser("base", help="clean arm")
    p.add_argument("--seeds", default="all")
    p.add_argument("--subset", default=None)
    p.add_argument("--sample-ids", default=None, help="comma-separated sample ids to run (filters the seed set)")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--max-turns", type=int, default=30)
    p.add_argument("--log-dir", default="logs/base")

    p = sub.add_parser("breaks", help="sample break points from base logs")
    p.add_argument("--logs", nargs="+", default=["logs/base"])
    p.add_argument("--out", default="experiments/manual/breaks.json")
    p.add_argument("--seed", type=int, default=0)

    p = sub.add_parser("inject", help="injected arm (replays prefix from cache)")
    p.add_argument("--breaks", default="breaks.json")
    p.add_argument("--seeds", default="all")
    p.add_argument("--subset", default=None)
    p.add_argument("--sample-ids", default=None, help="comma-separated sample ids to run (filters the seed set)")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--max-turns", type=int, default=30)
    p.add_argument("--log-dir", default="logs/inject")

    p = sub.add_parser("variants", help="injected arm under an auditor variant (Q4)")
    p.add_argument("--variant", required=True)
    p.add_argument("--breaks", default="breaks.json")
    p.add_argument("--subset", default="subset60.txt")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--max-turns", type=int, default=30)
    p.add_argument("--log-dir", default=None)

    p = sub.add_parser("monitor", help="three-view monitors over logs")
    p.add_argument("--logs", nargs="+", default=["logs/base", "logs/inject"])
    p.add_argument("--views", nargs="+", default=["actions", "cot", "both"])
    p.add_argument("--out", default="experiments/manual/monitor.jsonl")

    args = ap.parse_args()
    if getattr(args, "breaks", None):
        args.breaks = str(Path(args.breaks).resolve())
    common = {"pinned_date": args.pinned_date}
    if args.auditor_variant:
        common["auditor_variant"] = args.auditor_variant
    if args.clean_debrief:
        common["clean_debrief"] = str(Path(args.clean_debrief).resolve())

    if args.cmd == "smoke":
        task_args = {**common, "seed_instructions": args.seeds, "max_turns": args.max_turns,
                     "breaks": args.breaks, "judge": False}
        run_eval(args, task_args=task_args, log_dir=args.log_dir, epochs=1, max_connections=args.max_connections)
    elif args.cmd == "base":
        task_args = {**common, "seed_instructions": seeds_arg(args), "max_turns": args.max_turns}
        ids = args.sample_ids.split(",") if args.sample_ids else None
        run_eval(args, task_args=task_args, log_dir=args.log_dir, epochs=args.epochs, max_connections=args.max_connections, sample_ids=ids)
    elif args.cmd == "breaks":
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "probing_control.breaks", "--logs", *args.logs, "--out", args.out, "--seed", str(args.seed)]
        sys.exit(subprocess.call(cmd, cwd=ROOT))
    elif args.cmd == "inject":
        task_args = {**common, "seed_instructions": seeds_arg(args), "max_turns": args.max_turns, "breaks": args.breaks}
        ids = args.sample_ids.split(",") if args.sample_ids else None
        run_eval(args, task_args=task_args, log_dir=args.log_dir, epochs=args.epochs, max_connections=args.max_connections, sample_ids=ids)
    elif args.cmd == "variants":
        task_args = {**common, "seed_instructions": seeds_arg(args), "max_turns": args.max_turns,
                     "breaks": args.breaks, "auditor_variant": args.variant}
        log_dir = args.log_dir or f"logs/variants/{args.variant}"
        run_eval(args, task_args=task_args, log_dir=log_dir, epochs=args.epochs, max_connections=args.max_connections)
    elif args.cmd == "monitor":
        cmd = [sys.executable, "-m", "probing_control.monitor", "--logs", *args.logs, "--views", *args.views,
               "--model", args.model, "--out", args.out, "--max-connections", str(args.max_connections),
               "--redo-failed"] + (["--provider", args.provider] if args.provider else [])
        sys.exit(subprocess.call(cmd, cwd=ROOT))


if __name__ == "__main__":
    main()
