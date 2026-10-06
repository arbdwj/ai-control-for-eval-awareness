"""Scenario clock: tools and approver, exercised directly against a sample store."""

import asyncio
from datetime import datetime

import pytest
from inspect_ai.tool import ToolCall
from inspect_ai.util import store

from probing_control.clock import clock_advance, clock_approver, clock_now, clock_set, timestamps_in


def _call(fn, **args):
    return ToolCall(id="x", function=fn, arguments=args)


@pytest.fixture
def fresh_store(monkeypatch):
    # inspect's store() is a per-sample context var; give the test its own
    from inspect_ai.util._store import Store, init_subtask_store
    init_subtask_store(Store())
    yield


def test_timestamps_in_parses_iso_and_12h():
    today = datetime(2027, 3, 4, 9, 0)
    ts = timestamps_in("sent 2027-03-04 10:15 and again at 3:12 PM, log 2027-03-05T01:00:00Z", today)
    assert ts[0] == datetime(2027, 3, 4, 10, 15)
    assert datetime(2027, 3, 4, 15, 12) in ts
    assert datetime(2027, 3, 5, 1, 0) in ts


def test_clock_flow_and_approver(fresh_store):
    run = asyncio.run
    approve = clock_approver()
    set_, now, adv = clock_set(), clock_now(), clock_advance()

    # nothing set: sends are rejected
    a = run(approve("", _call("send_message", message="hi"), None, []))
    assert a.decision == "reject" and "not set" in a.explanation

    assert "set to" in run(set_("2027-03-04 09:12"))
    assert "2027-03-04 09:12" in run(now())
    # set once only
    assert run(set_("2027-03-04 09:12")).startswith("Error")

    # fresh after set: a send is allowed (turn_advanced starts true), then resume resets it
    assert run(approve("", _call("send_message", message="hello at 9:10 AM"), None, [])).decision == "approve"
    assert run(approve("", _call("resume"), None, [])).decision == "approve"
    a = run(approve("", _call("send_tool_call_result", tool_call_id="1", result="ok"), None, []))
    assert a.decision == "reject" and "not advanced" in a.explanation

    # advance, then a timestamp ahead of the clock is rejected, one at or before it is fine
    assert "advanced 180s" in run(adv(180, "user reads"))
    a = run(approve("", _call("send_tool_call_result", tool_call_id="1", result="Message sent 2027-03-04 11:00"), None, []))
    assert a.decision == "reject" and "ahead" in a.explanation
    a = run(approve("", _call("send_tool_call_result", tool_call_id="1", result="Message sent 2027-03-04 09:15"), None, []))
    assert a.decision == "approve"

    # identical consecutive increments are refused
    assert run(adv(180, "again")).startswith("Error")
    assert "advanced 200s" in run(adv(200, "ok"))
    st = store().get("scenario_clock")
    assert st["advances"] == [180, 200] and st["rejections"] == 2  # the unset-clock reject is not counted
