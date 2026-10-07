from probing_control.debrief import parse_debrief


def test_truncated_debrief_recovers_score_and_marks_truncated():
    raw = '{\n  "sim_score": 0.7,\n  "evidence": "The filesystem layer behaved genuinely, real node_modules'
    d = parse_debrief(raw)
    assert d is not None and d["sim_score"] == 0.7 and d["truncated"] and d["probes"] == []
    assert "node_modules" in d["evidence"]


def test_well_formed_debrief_not_marked_truncated():
    d = parse_debrief('{"sim_score": 0.2, "evidence": "e", "probes": []}')
    assert d["truncated"] is False and d["sim_score"] == 0.2
