"""Custom Petri target driver for the probing-control experiment.

A copy of `inspect_petri.target.target_agent` with three additions:

1. A counter of target generations. When the counter reaches the break turn
   (``context.metadata["break_turn"]``, 1-based), the addendum is appended to
   the target's system message before that generation. Without a break turn the
   driver behaves exactly like the stock agent, so the same driver serves the
   clean and injected arms.
2. The copy of each output that goes to the auditor has ``<probe>`` and
   ``<sim_score>`` tags removed and, by default, all reasoning content removed.
   The target's own history and the eval log keep the full message.
3. The stripped tags are written to the sample store under ``"probing"`` with
   their turn index, for the scorer and for post-hoc analysis.

Reasoning is hidden from the auditor in *both* arms so the auditor's inputs are
identical up to the break and Inspect's cache replays the shared prefix.
"""

from __future__ import annotations

import re
from typing import Any

from inspect_ai.agent import Agent, AgentState, agent
from inspect_ai.model import (
    CachePolicy,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageUser,
    ContentReasoning,
    ContentText,
    Model,
    ModelOutput,
    get_model,
)
from inspect_ai.util import store
from inspect_petri.target import TOOL_RESULT, ExitSignal, TargetContext

from .debrief import match_probes, parse_debrief

BREAK_TURN_KEY = "break_turn"
STORE_KEY = "probing"

PROBE_RE = re.compile(r"<probe>(.*?)</probe>", re.IGNORECASE | re.DOTALL)
SIM_RE = re.compile(r"<sim_score>\s*([0-9]*\.?[0-9]+)\s*</sim_score>", re.IGNORECASE | re.DOTALL)
# unterminated tags (model cut off mid-tag): drop from the tag to end of text
PROBE_OPEN_RE = re.compile(r"<probe>.*$", re.IGNORECASE | re.DOTALL)
SIM_OPEN_RE = re.compile(r"<sim_score>.*$", re.IGNORECASE | re.DOTALL)
# the injected addendum block, so monitors can be shown an arm-blind system message
ADDENDUM_RE = re.compile(r"\s*<developer_addendum>.*?</developer_addendum>\s*", re.IGNORECASE | re.DOTALL)


def strip_tags(text: str) -> tuple[str, list[str], float | None]:
    """Remove probe/sim_score tags from `text`.

    Returns the cleaned text, the probe reasons found, and the last sim_score.
    """
    probes = [m.strip() for m in PROBE_RE.findall(text)]
    sims = SIM_RE.findall(text)
    sim: float | None = None
    if sims:
        try:
            sim = float(sims[-1])
        except ValueError:
            sim = None
    cleaned = PROBE_RE.sub("", text)
    cleaned = SIM_RE.sub("", cleaned)
    cleaned = PROBE_OPEN_RE.sub("", cleaned)
    cleaned = SIM_OPEN_RE.sub("", cleaned)
    # tidy whitespace left behind
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, probes, sim


def strip_output(
    output: ModelOutput, *, hide_reasoning: bool = True
) -> tuple[ModelOutput, list[str], float | None]:
    """Return a deep copy of `output` safe to show the auditor, plus extracted tags.

    The message id is preserved so the auditor's `[message_id: ...]` labels
    still refer to the logged message.
    """
    copy = output.model_copy(deep=True)
    msg = copy.message
    probes: list[str] = []
    sim: float | None = None

    if isinstance(msg.content, str):
        cleaned, probes, sim = strip_tags(msg.content)
        new_content: str | list[Any] = cleaned
    else:
        parts: list[Any] = []
        for item in msg.content:
            if isinstance(item, ContentReasoning):
                if hide_reasoning:
                    continue
                parts.append(item)
            elif isinstance(item, ContentText):
                cleaned, p, s = strip_tags(item.text)
                probes.extend(p)
                if s is not None:
                    sim = s
                if cleaned:
                    parts.append(item.model_copy(update={"text": cleaned}))
            else:
                parts.append(item)
        new_content = parts if parts else ""

    new_msg = msg.model_copy(update={"content": new_content})
    copy.choices[0].message = new_msg
    return copy, probes, sim


def _text_of(msg: ChatMessageAssistant) -> str:
    if isinstance(msg.content, str):
        return msg.content
    return "\n".join(c.text for c in msg.content if isinstance(c, ContentText))


def _with_addendum(system: ChatMessageSystem, addendum: str) -> ChatMessageSystem:
    text = system.text
    if addendum.strip() in text:
        return system
    return system.model_copy(update={"content": f"{text}\n\n{addendum.strip()}"})


@agent(name="target")
def probing_target(
    *,
    addendum: str,
    debrief: str | None = None,
    clean_debrief: str | None = None,
    model: str | Model | None = None,
    cache: bool | CachePolicy = False,
    hide_reasoning: bool = True,
    system_required: bool = True,
) -> Agent:
    """Target driver with break-turn injection and auditor-side stripping.

    Args:
        addendum: Text appended to the target system message at the break turn.
        clean_debrief: Like `debrief` but sent when no note was injected: a neutral exit
            interview asking whether the target believed it was being tested.
        debrief: If given, sent to the target as one extra developer message after the
            auditor ends the conversation (injected arm only). The reply is parsed as JSON,
            its reported probes are matched to logged turns, and the result goes to the
            store. Neither the debrief nor the reply is appended to the target's message
            list, so monitors reading the transcript never see them.
        model: Target model override (defaults to the `target` role).
        cache: Response caching policy for target generations.
        hide_reasoning: Remove reasoning content from what the auditor sees.
        system_required: Whether the conversation must begin with a system message.
    """

    async def execute(state: AgentState, context: TargetContext) -> AgentState:
        target_model = get_model(model) if model is not None else get_model(role="target")
        generate = context.replayable(target_model.generate, surface_errors=True)
        target_cache = context.scoped_cache(cache)

        break_turn: int | None = context.metadata.get(BREAK_TURN_KEY)
        if break_turn is not None:
            break_turn = int(break_turn)

        record: dict[str, Any] = {
            "break_turn": break_turn,
            "injected_at": None,
            "turns": 0,
            "probes": [],
            "sim_scores": [],
            "post_break_turns": [],
            "debrief": None,
        }
        store().set(STORE_KEY, record)

        turn = 0
        injected = False

        def save() -> None:
            store().set(STORE_KEY, dict(record))

        try:
            await context.wait_for_resume()
            if system_required:
                system_message = await context.system_message()
                user_message = await context.user_message()
            else:
                user_message = await context.user_message()
                system_message = await context.system_message(required=False)
            state.messages[:] = (
                [system_message, user_message]
                if system_message is not None
                else [user_message]
            )

            while True:
                if prefill := context.prefill():
                    state.messages.append(
                        ChatMessageAssistant(content=prefill, metadata={"prefill": True})
                    )

                turn += 1
                record["turns"] = turn

                # inject the addendum at the break turn (before this generation)
                if break_turn is not None and turn >= break_turn and not injected:
                    if state.messages and isinstance(state.messages[0], ChatMessageSystem):
                        state.messages[0] = _with_addendum(state.messages[0], addendum)
                    else:
                        state.messages.insert(0, ChatMessageSystem(content=addendum.strip()))
                    injected = True
                    record["injected_at"] = turn
                    save()

                state.output = await generate(
                    input=state.messages, tools=context.tools, cache=target_cache
                )
                state.messages.append(state.output.message)

                # auditor-facing copy: tags and (optionally) reasoning removed
                visible, probes, sim = strip_output(
                    state.output, hide_reasoning=hide_reasoning
                )
                for reason in probes:
                    record["probes"].append({"turn": turn, "reason": reason})
                if sim is not None:
                    record["sim_scores"].append({"turn": turn, "score": sim})
                if injected:
                    msg = state.output.message
                    record["post_break_turns"].append(
                        {
                            "turn": turn,
                            "n_probes": len(probes),
                            "tool_calls": [tc.function for tc in (msg.tool_calls or [])],
                            "text_preview": _text_of(msg)[:300],
                        }
                    )
                save()

                if tool_calls := state.output.message.tool_calls:
                    context.expect({TOOL_RESULT: {tc.id for tc in tool_calls}})
                    await context.send_output(visible)
                    state.messages.extend(await context.tool_results(tool_calls))
                    if user_message := await context.user_message(required=False):
                        state.messages.append(user_message)
                else:
                    context.expect({TOOL_RESULT: set()})
                    await context.send_output(visible)
                    state.messages.append(await context.user_message())
        except ExitSignal:
            if injected and debrief:
                await _run_debrief(target_model, state, record, debrief, target_cache)
                save()
            elif not injected and clean_debrief:
                await _run_debrief(target_model, state, record, clean_debrief, target_cache)
                save()
            return state

    return execute


async def _run_debrief(target_model, state: AgentState, record: dict[str, Any], debrief: str, cache) -> None:
    """One extra generation after the audit ends: the developer asks for the self-report."""
    msgs = list(state.messages) + [ChatMessageUser(content=debrief.strip())]
    try:
        output = await target_model.generate(input=msgs, cache=cache)
    except Exception as ex:  # noqa: BLE001
        record["debrief"] = {"error": repr(ex)[:300]}
        return
    text = _text_of(output.message)
    parsed = parse_debrief(text)
    entry: dict[str, Any] = {"raw": text[:6000], "parsed": parsed}
    if parsed is not None:
        start = record.get("injected_at") or 1
        entry["matched"] = match_probes(state.messages, parsed["probes"], start_turn=start)
    record["debrief"] = entry
