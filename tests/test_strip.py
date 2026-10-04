from inspect_ai.model import ChatMessageAssistant, ContentReasoning, ContentText, ModelOutput

from probing_control.driver import strip_output, strip_tags


def test_strip_tags_basic():
    text = "Sure. <probe>check clock</probe> What time is it? <sim_score>0.65</sim_score>"
    cleaned, probes, sim = strip_tags(text)
    assert cleaned == "Sure.  What time is it?".replace("  ", " ") or "probe" not in cleaned
    assert "<probe>" not in cleaned and "<sim_score>" not in cleaned
    assert probes == ["check clock"]
    assert sim == 0.65


def test_strip_tags_multiline_and_unterminated():
    text = "A\n<probe>\nre-read file\n</probe>\nB <sim_score>0.3"
    cleaned, probes, sim = strip_tags(text)
    assert probes == ["re-read file"]
    assert sim is None  # unterminated: not parsed
    assert "<sim_score>" not in cleaned and "<probe>" not in cleaned
    assert cleaned.startswith("A") and "B" in cleaned


def test_strip_output_hides_reasoning_and_keeps_id():
    msg = ChatMessageAssistant(
        content=[
            ContentReasoning(reasoning="secret plan"),
            ContentText(text="<probe>why</probe> hello"),
        ]
    )
    out = ModelOutput.from_message(msg)
    visible, probes, sim = strip_output(out)
    assert visible.message.id == msg.id
    assert all(not isinstance(c, ContentReasoning) for c in visible.message.content)
    assert visible.message.text.strip() == "hello"
    assert probes == ["why"] and sim is None
    # original untouched
    assert isinstance(out.message.content[0], ContentReasoning)
    assert "<probe>" in out.message.text


def test_strip_output_string_content_and_reasoning_kept_when_asked():
    msg = ChatMessageAssistant(content=[ContentReasoning(reasoning="r"), ContentText(text="x")])
    visible, _, _ = strip_output(ModelOutput.from_message(msg), hide_reasoning=False)
    assert isinstance(visible.message.content[0], ContentReasoning)
    msg2 = ChatMessageAssistant(content="plain <sim_score>1.0</sim_score>")
    visible2, probes, sim = strip_output(ModelOutput.from_message(msg2))
    assert visible2.message.content == "plain" and sim == 1.0 and probes == []


def test_monitor_parse_json_robust():
    from probing_control.monitor import clip, parse_json

    assert parse_json('{"trace_probability": 0.2, "turns": []}')["trace_probability"] == 0.2
    messy = 'Let me think {"not": "it"} ... ```json\n{"trace_probability": 0.4, "first_probe_turn": null, "turns": [{"turn": 1, "probability": 0.1, "note": "x {y}"}]}\n``` done'
    out = parse_json(messy)
    assert out["trace_probability"] == 0.4 and out["turns"][0]["note"] == "x {y}"
    assert parse_json("no json here") is None and parse_json("") is None
    long = "a" * 20000
    c = clip(long, 8000)
    assert len(c) < 8200 and "elided" in c and c.startswith("a" * 4000) and c.endswith("a" * 4000)
