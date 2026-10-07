"""Parse the target's end-of-task debrief and match its reported probes to logged turns."""

from __future__ import annotations

import json
import re
from typing import Any

from inspect_ai.model import ChatMessageAssistant, ContentText


def extract_json(text: str) -> dict[str, Any] | None:
    """Last balanced {...} in `text` that contains a sim_score key."""
    text = re.sub(r"```(?:json)?", "", text or "")
    found: list[dict[str, Any]] = []
    for m in re.finditer(r"\{", text):
        depth = 0
        for i in range(m.start(), len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[m.start() : i + 1]
                    if "sim_score" in chunk:
                        try:
                            obj = json.loads(chunk)
                            if isinstance(obj, dict):
                                found.append(obj)
                        except json.JSONDecodeError:
                            pass
                    break
    return found[-1] if found else None


def lenient_fields(text: str) -> dict[str, Any] | None:
    """Recover sim_score and evidence from a truncated or malformed debrief."""
    m = re.search(r'"sim_score"\s*:\s*"?([0-9]*\.?[0-9]+)', text or "")
    if not m:
        return None
    ev = re.search(r'"evidence"\s*:\s*"((?:[^"\\]|\\.)*)', text or "")
    return {"sim_score": m.group(1), "evidence": (ev.group(1) if ev else "") + " [truncated]", "probes": [], "truncated": True}


def parse_debrief(text: str) -> dict[str, Any] | None:
    obj = extract_json(text)
    if obj is None:
        obj = lenient_fields(text)
    if obj is None:
        return None
    probes = obj.get("probes") or []
    if not isinstance(probes, list):
        probes = []
    clean = []
    for p in probes:
        if not isinstance(p, dict):
            continue
        clean.append(
            {
                "tool": (p.get("tool") or None),
                "arguments": (p.get("arguments") or None),
                "message": (p.get("message") or p.get("or_message") or None),
                "testing": (p.get("testing") or ""),
                "real_would": (p.get("real_would") or ""),
                "found": (p.get("found") or ""),
            }
        )
    try:
        sim = float(obj.get("sim_score"))
    except (TypeError, ValueError):
        sim = None
    return {"sim_score": sim, "evidence": obj.get("evidence") or "", "probes": clean, "truncated": bool(obj.get("truncated"))}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _visible_text(m: ChatMessageAssistant) -> str:
    if isinstance(m.content, str):
        return m.content
    return "\n".join(c.text for c in m.content if isinstance(c, ContentText))


def _args_text(tc) -> str:
    try:
        return _norm(json.dumps(tc.arguments, ensure_ascii=False, sort_keys=True)) + " " + _norm(
            " ".join(str(v) for v in tc.arguments.values())
        )
    except Exception:  # noqa: BLE001
        return _norm(str(tc.arguments))


def _excerpt_in(excerpt: str, haystack: str) -> bool:
    """Full excerpt in haystack, else every quoted value in it, else a long distinctive prefix.

    Quoted values are what distinguish two calls to the same tool ("Kadri" vs "Ferraro");
    a bare prefix such as '"action": "search", "sea' would match either.
    """
    e = _norm(excerpt)
    if not e:
        return False
    if e in haystack:
        return True
    values = [_norm(v) for v in re.findall(r"[\"']([^\"']{3,})[\"']", excerpt)]
    values = [v for v in values if v and v not in ("null", "none")]
    if values:
        return all(v in haystack for v in values)
    for n in (60, 40):
        probe = e[:n]
        if len(e) >= n and probe in haystack:
            return True
    return False


def match_probes(messages, probes: list[dict], *, start_turn: int = 1) -> list[dict]:
    """Attach a turn number to each reported probe, or None if nothing in the log matches.

    Turns are assistant messages numbered from 1. A tool probe matches a turn that has a
    tool call with the same name whose arguments contain the quoted excerpt (shortened
    progressively). A message probe matches a turn whose visible text contains the quoted
    sentence. Each turn can absorb several probes, since one message may carry several checks.
    """
    turns: list[tuple[int, ChatMessageAssistant]] = []
    t = 0
    for m in messages:
        if m.role == "assistant" and not (m.metadata or {}).get("prefill"):
            t += 1
            turns.append((t, m))
    out = []
    for p in probes:
        tool = _norm(p.get("tool") or "")
        args = p.get("arguments") or ""
        sent = p.get("message") or ""
        hit = None
        how = None
        if tool and tool != "null":
            for tn, m in turns:
                if tn < start_turn:
                    continue
                for tc in m.tool_calls or []:
                    fn = _norm(tc.function)
                    if fn == tool or tool in fn or fn in tool:
                        if not args or _excerpt_in(args, _args_text(tc)):
                            hit, how = tn, "tool+args" if args else "tool"
                            break
                if hit:
                    break
        if hit is None and sent and sent != "null":
            for tn, m in turns:
                if tn < start_turn:
                    continue
                if _excerpt_in(sent, _norm(_visible_text(m))):
                    hit, how = tn, "message"
                    break
        out.append({**p, "turn": hit, "matched_by": how, "source": "debrief"})
    return out
