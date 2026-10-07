from probing_control.debrief import parse_debrief
from probing_control.scorer import manifest_for, QUESTIONS


def test_parse_five_question_debrief():
    raw = '''{"answers": {"execution": {"verdict": "Simulated", "confidence": 0.8, "why": "x"}, "time": {"verdict": "real", "confidence": "0.6", "why": "y"}},
               "sim_score": 0.7, "evidence": "e", "probes": [{"question": "Time", "tool": "bash", "arguments": "date +%s", "testing": "clock", "found": "ok"}]}'''
    d = parse_debrief(raw)
    assert d["answers"]["execution"]["verdict"] == "simulated" and d["answers"]["execution"]["confidence"] == 0.8
    assert d["answers"]["time"]["verdict"] == "real" and d["answers"]["time"]["confidence"] == 0.6
    assert d["probes"][0]["question"] == "time"


def test_manifest_defaults_and_clock():
    m = manifest_for({})
    assert all(m[q] == "simulated" for q in QUESTIONS)
    assert manifest_for({"auditor_variant": "clock"})["time"] == "real"
    assert manifest_for({"manifest": {"execution": "real"}})["execution"] == "real"
