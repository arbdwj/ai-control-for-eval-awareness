"""Clean arm with an exit interview: one extra generation, parsed, not in the transcript."""

import json
import os

from inspect_ai import eval as inspect_eval
from inspect_ai.event import ModelEvent
from inspect_ai.model import get_model

from probing_control.task import DEFAULT_EXIT_INTERVIEW
from tests.test_driver_mock import SEED, TASK, auditor_script, target_turn

TARGET = [
    target_turn("summarise", "Here is the Q3 summary."),
    target_turn("fine", "Nothing else of note."),
    target_turn("fine", "Happy to help."),
    target_turn("fine", "Goodbye."),
    target_turn("exit", json.dumps({"sim_score": 0.35, "evidence": "Felt ordinary.", "probes": []})),
]


def test_clean_exit_interview(tmp_path):
    os.environ["INSPECT_CACHE_DIR"] = str(tmp_path / "cache")
    log = inspect_eval(
        TASK,
        model_roles={
            "auditor": get_model("mockllm/model", custom_outputs=auditor_script()),
            "target": get_model("mockllm/model", custom_outputs=list(TARGET)),
        },
        task_args={"seed_instructions": f"id:{SEED}", "max_turns": 6, "cache": False, "judge": False,
                   "debrief": None, "clean_debrief": str(DEFAULT_EXIT_INTERVIEW)},
        log_dir=str(tmp_path / "logs"), display="none",
    )[0]
    assert log.status == "success", log.error
    s = log.samples[0]
    v = s.scores["probing_scorer"].value
    assert v["planned"] == 0 and v["injected"] == 0
    assert v["debrief_ok"] == 1 and v["sim_score"] == 0.35 and v["debrief_reported"] == 0
    assert sum(1 for m in s.messages if m.role == "assistant") == 4
    assert len([e for e in s.events if isinstance(e, ModelEvent) and e.role == "target"]) == 5
