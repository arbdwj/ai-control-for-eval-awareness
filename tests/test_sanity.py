"""The tool-schema approver rejects parameterless tools that take input, through Inspect's approval path."""

import os

from inspect_ai import eval as inspect_eval
from inspect_ai.event import ToolEvent
from inspect_ai.model import get_model

from tests.test_driver_mock import SEED, TASK, target_turn, tool_turn

AUDITOR = [
    tool_turn(
        ("create_tool", {"environment_description": "a dev box", "name": "bash", "description": "Run a shell command"}),
        ("create_tool", {"environment_description": "a dev box", "name": "get_time", "description": "Returns the current time. Takes no input."}),
        ("create_tool", {"environment_description": "a dev box", "name": "bash", "description": "Run a shell command",
                         "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}),
        ("set_system_message", {"system_message": "You are an assistant."}),
        ("send_message", {"message": "hi"}),
        ("resume", {}),
    ),
    tool_turn(("end_conversation", {})),
]


def test_parameterless_bash_rejected_but_schema_and_inputless_allowed(tmp_path):
    os.environ["INSPECT_CACHE_DIR"] = str(tmp_path / "cache")
    log = inspect_eval(
        TASK,
        model_roles={
            "auditor": get_model("mockllm/model", custom_outputs=list(AUDITOR)),
            "target": get_model("mockllm/model", custom_outputs=[target_turn("ok", "hello")]),
        },
        task_args={"seed_instructions": f"id:{SEED}", "max_turns": 4, "cache": False, "judge": False, "debrief": None},
        log_dir=str(tmp_path / "logs"), display="none",
    )[0]
    assert log.status == "success", log.error
    creates = [ev for ev in log.samples[0].events if isinstance(ev, ToolEvent) and ev.function == "create_tool"]
    assert len(creates) == 3
    assert creates[0].error and "parameter schema" in str(creates[0].error)   # bash without parameters: rejected
    assert creates[1].error is None                                           # input-less tool: allowed
    assert creates[2].error is None                                           # bash with a schema: allowed
