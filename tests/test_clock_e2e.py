"""Clock variant end to end on mock models: the approver must run through Inspect's real approval path."""

import os

from inspect_ai import eval as inspect_eval
from inspect_ai.event import ToolEvent
from inspect_ai.model import get_model

from tests.test_driver_mock import SEED, TASK, target_turn, tool_turn

AUDITOR = [
    tool_turn(
        ("clock_set", {"datetime_str": "2027-03-04 09:12"}),
        ("set_system_message", {"system_message": "You are an assistant."}),
        ("send_message", {"message": "Hi, what time is it?"}),
        ("resume", {}),
    ),
    # forgets to advance: the approver must reject this send with a tool error
    tool_turn(("send_message", {"message": "Thanks. Anything else?"}), ("resume", {})),
    # advances, then a timestamp ahead of the clock: rejected; then a good one
    tool_turn(("clock_advance", {"seconds": 240, "reason": "user reads"}), ("send_message", {"message": "Sent at 2027-03-04 10:30"}), ("resume", {})),
    # each resume closes a turn, so a fresh advance is needed before the next send
    tool_turn(("clock_advance", {"seconds": 300, "reason": "user replies"}), ("send_message", {"message": "Sent at 9:15 AM, bye"}), ("resume", {})),
    tool_turn(("end_conversation", {})),
]
TARGET = [target_turn("ok", "It is morning."), target_turn("ok", "Nothing."), target_turn("ok", "Bye.")]


def test_clock_variant_runs_and_approver_rejects(tmp_path):
    os.environ["INSPECT_CACHE_DIR"] = str(tmp_path / "cache")
    log = inspect_eval(
        TASK,
        model_roles={
            "auditor": get_model("mockllm/model", custom_outputs=list(AUDITOR)),
            "target": get_model("mockllm/model", custom_outputs=list(TARGET)),
        },
        task_args={"seed_instructions": f"id:{SEED}", "max_turns": 6, "cache": False, "judge": False,
                   "debrief": None, "auditor_variant": "clock"},
        log_dir=str(tmp_path / "logs"),
        display="none",
    )[0]
    assert log.status == "success", log.error
    s = log.samples[0]
    assert s.error is None
    sends = [ev for ev in s.events if isinstance(ev, ToolEvent) and ev.function == "send_message"]
    errs = [str(ev.error) if ev.error else None for ev in sends]
    assert errs[0] is None                      # first send after clock_set is allowed
    assert errs[1] and "not advanced" in errs[1]  # forgot to advance
    assert errs[2] and "ahead" in errs[2]         # timestamp ahead of the clock
    assert errs[3] is None                      # fine
    st = s.store.get("scenario_clock") if s.store else None
    assert st and st["advances"] == [240, 300] and st["rejections"] == 2
