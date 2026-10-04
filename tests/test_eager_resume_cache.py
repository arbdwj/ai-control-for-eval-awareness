"""Eager-resume ids must not break cache replay between runs."""

import os

from inspect_ai import eval as inspect_eval
from inspect_ai.event import ModelEvent
from inspect_ai.model import get_model

from probing_control.task import normalize_eager_resume_ids
from tests.test_driver_mock import SEED, TASK, target_turn, tool_turn

# turn 1 stages system + user but forgets `resume`: Petri injects an eager resume
AUDITOR = [
    tool_turn(
        ("set_system_message", {"system_message": "You are an assistant."}),
        ("send_message", {"message": "Hello, summarise Q3."}),
    ),
    tool_turn(("send_message", {"message": "Thanks, bye."}), ("resume", {})),
    tool_turn(("end_conversation", {})),
]
TARGET = [target_turn("ok", "Q3 up 4%."), target_turn("ok", "Bye.")]


def _run(tmp_path, tag):
    return inspect_eval(
        TASK,
        model_roles={
            "auditor": get_model("mockllm/model", custom_outputs=list(AUDITOR)),
            "target": get_model("mockllm/model", custom_outputs=list(TARGET)),
        },
        task_args={"seed_instructions": f"id:{SEED}", "max_turns": 4, "cache": True, "judge": False, "debrief": None},
        log_dir=str(tmp_path / tag),
        display="none",
    )[0]


def test_second_run_replays_everything(tmp_path):
    os.environ["INSPECT_CACHE_DIR"] = str(tmp_path / "cache")
    first = _run(tmp_path, "a")
    assert first.status == "success", first.error
    aud = [e for e in first.samples[0].events if isinstance(e, ModelEvent) and e.role == "auditor"]
    # eager resume did fire: an injected resume call with a uuid id is in the history
    inputs = aud[-1].input
    assert any(tc.function == "resume" and tc.id.startswith("eager-resume-") for m in inputs if m.role == "assistant" for tc in (m.tool_calls or []))

    second = _run(tmp_path, "b")
    assert second.status == "success", second.error
    evs = [e for e in second.samples[0].events if isinstance(e, ModelEvent)]
    assert evs and all(e.cache == "read" for e in evs), [(e.role, e.cache) for e in evs]


def test_normalize_is_stable_and_leaves_provider_ids():
    from inspect_ai.model import ChatMessageAssistant, ChatMessageTool
    from inspect_ai.tool import ToolCall

    msgs = [
        ChatMessageAssistant(content="", tool_calls=[
            ToolCall(id="call_abc123", function="send_message", arguments={"message": "x"}),
            ToolCall(id="0f4e2c6a-1b2c-4d3e-8f9a-0b1c2d3e4f5a", function="resume", arguments={}),
        ]),
        ChatMessageTool(content="ok", tool_call_id="call_abc123", function="send_message"),
        ChatMessageTool(content="out", tool_call_id="0f4e2c6a-1b2c-4d3e-8f9a-0b1c2d3e4f5a", function="resume"),
    ]
    out = normalize_eager_resume_ids(msgs)
    assert out[0].tool_calls[0].id == "call_abc123"
    assert out[0].tool_calls[1].id == "eager-resume-1"
    assert out[1].tool_call_id == "call_abc123" and out[2].tool_call_id == "eager-resume-1"
    assert msgs[0].tool_calls[1].id.startswith("0f4e")  # original untouched
