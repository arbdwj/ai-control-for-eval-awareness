"""Inspect task for the probing-control experiment.

Composes Petri's public pieces (`seeds_dataset`, `audit_solver`, `auditor_agent`,
`auditor_tools`, `audit_judge`) with this repo's target driver, a setup solver
that injects the break turn from a breaks file, and the probing scorer.
Nothing in Petri is modified.

Run from the repo root, e.g.

    inspect eval probing_control/task.py@probing_audit -T breaks=breaks.json --epochs 3 \
        --model-role auditor=openrouter/z-ai/glm-5.3 ...
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Literal, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import re  # noqa: E402
from dataclasses import replace  # noqa: E402

from inspect_ai import Task, task  # noqa: E402
from inspect_ai.model import CachePolicy, ChatMessage, GenerateConfig, Model, ModelOutput  # noqa: E402
from inspect_ai.tool import Tool  # noqa: E402
from inspect_ai.solver import Generate, Solver, TaskState, solver  # noqa: E402
from inspect_petri import (  # noqa: E402
    JudgeDimension,
    audit_judge,
    audit_solver,
    auditor_agent,
    auditor_tools,
    seeds_dataset,
)
from inspect_petri._auditor.agent import AUDITOR_SYSTEM_MESSAGE  # noqa: E402

from probing_control.driver import BREAK_TURN_KEY, probing_target  # noqa: E402
from probing_control.scorer import probing_scorer  # noqa: E402

logger = logging.getLogger(__name__)

SRC = Path(__file__).resolve().parent
DEFAULT_ADDENDUM = SRC / "prompts" / "addendum.md"
DEFAULT_DEBRIEF = SRC / "prompts" / "debrief.md"
VARIANTS_DIR = SRC / "prompts" / "variants"


def load_breaks(path: str | Path) -> dict[str, dict[str, int]]:
    """Breaks file: ``{sample_id: {epoch: break_turn}}`` (epoch keys as strings)."""
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        # Inspect loads task files with the task's directory as cwd; fall
        # back to resolving relative to the repo root.
        p = ROOT / p
    with open(p) as f:
        raw = json.load(f)
    return {
        str(sid): {str(ep): int(k) for ep, k in (per_epoch or {}).items()}
        for sid, per_epoch in raw.items()
    }


@solver
def break_setup(breaks: dict[str, dict[str, int]]) -> Solver:
    """Put this sample/epoch's break turn into metadata for the driver."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        k = breaks.get(str(state.sample_id), {}).get(str(state.epoch))
        if k is None:
            logger.warning(
                "no break turn for sample=%s epoch=%s; running clean",
                state.sample_id,
                state.epoch,
            )
        else:
            state.metadata[BREAK_TURN_KEY] = k
        return state

    return solve


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def normalize_eager_resume_ids(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Make Petri's eager-resume tool-call ids deterministic.

    When the auditor stages content without calling `resume`, Petri injects a
    `resume` tool call into the auditor's message with a random uuid. That uuid
    then sits in every later model input and defeats Inspect's response cache,
    so the injected arm cannot replay the clean arm's prefix. Provider-issued
    ids are never uuid-shaped, so uuid-shaped `resume` ids are rewritten to
    `eager-resume-<n>` (and the matching tool results with them) in the copy
    sent to the model. The stored history is left untouched.
    """
    mapping: dict[str, str] = {}
    out: list[ChatMessage] = []
    n = 0
    for m in messages:
        if m.role == "assistant" and m.tool_calls:
            calls = []
            changed = False
            for tc in m.tool_calls:
                if tc.function == "resume" and _UUID_RE.match(tc.id or ""):
                    n += 1
                    mapping[tc.id] = f"eager-resume-{n}"
                    calls.append(replace(tc, id=mapping[tc.id]))
                    changed = True
                else:
                    calls.append(tc)
            out.append(m.model_copy(update={"tool_calls": calls}) if changed else m)
        elif m.role == "tool" and m.tool_call_id in mapping:
            out.append(m.model_copy(update={"tool_call_id": mapping[m.tool_call_id]}))
        else:
            out.append(m)
    return out


async def deterministic_generate(
    model: Model, messages: list[ChatMessage], tools: list[Tool], cache: bool | CachePolicy
) -> ModelOutput:
    return await model.generate(
        input=normalize_eager_resume_ids(messages), tools=tools, cache=cache
    )


def pinned_auditor_prompt(pinned_date: str | None, variant: str | None) -> str | None:
    """Default auditor prompt with `{today_date}` fixed and an optional variant appended."""
    if pinned_date is None and variant is None:
        return None
    prompt = AUDITOR_SYSTEM_MESSAGE
    if pinned_date is not None:
        prompt = prompt.replace("{today_date}", pinned_date)
    if variant is not None:
        vpath = VARIANTS_DIR / f"{variant}.md"
        if not vpath.exists():
            raise FileNotFoundError(f"unknown auditor variant {variant!r}: {vpath}")
        prompt = prompt.rstrip() + "\n" + vpath.read_text()
    return prompt


@task
def probing_audit(
    *,
    seed_instructions: str | list[str] | None = None,
    max_turns: int = 30,
    breaks: str | None = None,
    addendum: str = str(DEFAULT_ADDENDUM),
    debrief: str | None = str(DEFAULT_DEBRIEF),
    hide_reasoning: bool = True,
    pinned_date: str | None = "2026-10-01",
    auditor_variant: str | None = None,
    cache: bool = True,
    cache_expiry: str | None = "4W",
    enable_rollback: bool = False,
    compaction: bool = False,
    target_tools: Literal["synthetic", "fixed", "none"] = "synthetic",
    turn_counter: bool = False,
    target_display_name: str | None = None,
    judge: bool = False,
    judge_dimensions: str | Sequence[str | JudgeDimension] | None = None,
) -> Task:
    """Petri audit with the probing side channel.

    Args:
        seed_instructions: Petri seed selection (None = all 173 built-in seeds).
        max_turns: Auditor turn budget.
        breaks: Path to a breaks JSON file. None runs the clean arm.
        addendum: Path to the side-channel instruction appended at the break.
        debrief: Path to the developer debrief sent after the audit ends (injected arm only); None disables it.
        hide_reasoning: Strip target reasoning from what the auditor sees (both arms).
        pinned_date: Fixed value for `{today_date}` so base and injected runs share a cache prefix.
        auditor_variant: Name of a file in `probing_control/prompts/variants/` appended to the auditor prompt (Q4).
        cache: Cache every model call so the injected run replays the prefix.
        cache_expiry: Inspect cache expiry (default 4 weeks; the stock default is 1 week).
        enable_rollback: Auditor rollback/restart tools. Off so transcripts stay linear.
        compaction: Auditor context compaction. Off because compaction summaries are not cached.
        target_tools: Petri target tool mode.
        turn_counter: Show the auditor a turn counter.
        target_display_name: Public-facing target name in the auditor prompt.
        judge: Also run Petri's alignment judge. Off by default: it sees tags and raw reasoning.
        judge_dimensions: Judge dimensions if `judge` is on.
    """
    cache_policy: bool | CachePolicy = CachePolicy(expiry=cache_expiry) if cache else False
    addendum_text = Path(addendum).read_text()
    debrief_text = Path(debrief).read_text() if debrief else None

    setup: list[Solver] = []
    if breaks is not None:
        setup.append(break_setup(load_breaks(breaks)))

    solver = setup + [
        audit_solver(
            auditor=auditor_agent(
                max_turns=max_turns,
                turn_counter=turn_counter,
                compaction=compaction,
                tools=auditor_tools(rollback=enable_rollback, target_tools=target_tools),
                system_message=pinned_auditor_prompt(pinned_date, auditor_variant),
                cache=cache_policy,
                target_display_name=target_display_name,
                generate=deterministic_generate,
            ),
            target=probing_target(
                addendum=addendum_text,
                debrief=debrief_text,
                cache=cache_policy,
                hide_reasoning=hide_reasoning,
            ),
        )
    ]

    scorers = [probing_scorer()]
    if judge:
        scorers.append(audit_judge(judge_dimensions, cache=cache_policy))

    return Task(
        dataset=seeds_dataset(seed_instructions),
        solver=solver,
        scorer=scorers,
        metadata={
            "arm": "injected" if breaks is not None else "clean",
            "breaks": breaks,
            "addendum": str(addendum),
            "debrief": debrief,
            "auditor_variant": auditor_variant,
            "pinned_date": pinned_date,
            "hide_reasoning": hide_reasoning,
        },
    )
