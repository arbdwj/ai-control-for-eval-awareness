"""Sample break turns from base-run logs.

For each (sample, epoch) in the base logs, T is the number of target assistant
turns. k is drawn uniformly from [min_turn, T - tail]. Rollouts too short for
that range are skipped and listed in the sidecar file. The draw is seeded per
(seed, sample_id, epoch) so adding logs later does not change earlier draws.

    python -m probing_control.breaks --logs logs/base --out breaks.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from inspect_ai.log import list_eval_logs, read_eval_log


def target_turns(messages) -> int:
    return sum(
        1
        for m in messages
        if m.role == "assistant" and not (m.metadata or {}).get("prefill")
    )


def draw_k(seed: int, sid: str, ep: str, T: int, *, min_turn: int = 2, tail: int = 3) -> int | None:
    """Deterministic break turn for one rollout, or None if it is too short."""
    hi = T - tail
    if hi < min_turn:
        return None
    return random.Random(f"{seed}:{sid}:{ep}").randint(min_turn, hi)


def sample_breaks(
    log_dirs: list[str], *, seed: int, min_turn: int, tail: int
) -> tuple[dict[str, dict[str, int]], dict]:
    breaks: dict[str, dict[str, int]] = {}
    meta: dict = {"skipped": [], "lengths": {}, "logs": []}
    for d in log_dirs:
        for info in list_eval_logs(d):
            log = read_eval_log(info.name, resolve_attachments=True)
            meta["logs"].append(info.name)
            for s in log.samples or []:
                sid, ep = str(s.id), str(s.epoch)
                if s.error is not None:
                    meta["skipped"].append({"sample": sid, "epoch": ep, "reason": "error"})
                    continue
                T = target_turns(s.messages)
                meta["lengths"].setdefault(sid, {})[ep] = T
                k = draw_k(seed, sid, ep, T, min_turn=min_turn, tail=tail)
                if k is None:
                    meta["skipped"].append(
                        {"sample": sid, "epoch": ep, "reason": f"too short (T={T})"}
                    )
                    continue
                breaks.setdefault(sid, {})[ep] = k
    return breaks, meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs", nargs="+", required=True, help="base-run log dirs")
    ap.add_argument("--out", default="breaks.json")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--min-turn", type=int, default=2)
    ap.add_argument("--tail", type=int, default=3, help="k <= T - tail")
    args = ap.parse_args()

    breaks, meta = sample_breaks(
        args.logs, seed=args.seed, min_turn=args.min_turn, tail=args.tail
    )
    Path(args.out).write_text(json.dumps(breaks, indent=2, sort_keys=True))
    side = Path(args.out).with_suffix(".meta.json")
    side.write_text(json.dumps(meta, indent=2, sort_keys=True))
    n = sum(len(v) for v in breaks.values())
    print(f"wrote {n} break points for {len(breaks)} samples to {args.out}")
    print(f"skipped {len(meta['skipped'])} rollouts; details in {side}")


if __name__ == "__main__":
    main()
