"""Stage 3 of the bowtie: derivation chain -> human-readable text.

Pure string formatting -- no LLM, no free generation. The renderer can only
emit values that already appear in the derivation dict, so every claim in
the output text is grounded in a binding, primitive, or rule step that the
engine produced. By construction, the renderer cannot hallucinate.
"""
from __future__ import annotations

import json
from typing import Any


def render(result: dict) -> str:
    """Format an engine or parser result (success or failure) as text."""
    status = result.get("status")
    if status == "failure":
        return _render_failure(result)
    if status == "success":
        return _render_success(result)
    return f"(unrecognized result shape: {json.dumps(result, default=str)[:200]})"


def _render_success(result: dict) -> str:
    value = result["value"]
    unit = result["unit"]
    derivation = result["derivation"]

    base_steps = [s for s in derivation if s["type"] in ("binding", "primitive")]
    rule_steps = [s for s in derivation if s["type"] == "rule"]

    lines: list[str] = [f"Result: {_fmt_num(value)} {unit}", "", "Derivation:"]

    if base_steps:
        lines.append("  Given:")
        for s in base_steps:
            lines.append(f"    {s['predicate']} = {_fmt_num(s['value'])} {s['unit']}")

    for rstep in rule_steps:
        lines.append(f"  Applied: {rstep['id']} ({rstep['formula']})")
        lines.append("    Inputs:")
        for k, v in rstep["inputs"].items():
            if isinstance(v, dict):
                lines.append(f"      {k} = {_fmt_num(v['value'])} {v['unit']}")
            else:
                lines.append(f"      {k} = {_fmt_num(v)}")
        lines.append(f"    Output: {_fmt_num(rstep['output'])} {rstep['unit']}")
        lines.append(f"    Proof: {rstep.get('proof', '')}")

    return "\n".join(lines)


def render_trace(result: dict) -> str:
    """Raw step-by-step trace (used when the renderer is toggled OFF in the UI).

    Just dumps each derivation step as a single line; no template, no prose.
    """
    if result.get("status") == "failure":
        return _render_failure(result)
    if result.get("status") != "success":
        return f"(unrecognized result shape: {result!r})"
    lines: list[str] = []
    for s in result.get("derivation", []):
        n = s["step"]
        t = s["type"]
        if t == "binding":
            lines.append(f"[{n}] binding   {s['predicate']} = {_fmt_num(s['value'])} {s['unit']}")
        elif t == "primitive":
            lines.append(
                f"[{n}] primitive {s['id']}: {s['predicate']} = "
                f"{_fmt_num(s['value'])} {s['unit']}"
            )
        elif t == "rule":
            ins = ", ".join(
                f"{k}={_fmt_num(v['value']) if isinstance(v, dict) else _fmt_num(v)}"
                for k, v in s["inputs"].items()
            )
            lines.append(
                f"[{n}] rule      {s['id']}: {s['formula']}  "
                f"with [{ins}]  ->  {_fmt_num(s['output'])} {s['unit']}"
            )
    lines.append("")
    lines.append(f"= {_fmt_num(result['value'])} {result['unit']}")
    return "\n".join(lines)


def _render_failure(result: dict) -> str:
    ftype = result.get("type", "?")
    reason = result.get("reason", "(no reason given)")
    details = {k: v for k, v in result.items() if k not in ("status", "type", "reason")}
    lines = [
        f"Cannot answer: {reason}",
        f"Failure type: {ftype}",
    ]
    if details:
        lines.append(f"Details: {json.dumps(details, default=str)}")
    return "\n".join(lines)


def _fmt_num(v: Any) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v == 0.0:
            return "0"
        return f"{v:.4g}"
    return str(v)
