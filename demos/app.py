"""Gradio UI for the PCT pipeline.

Two-column layout: query input on the left, three tabs on the right
(Result, Derivation Trace, Goal State). The parser keeps its own
conversation history so multi-turn follow-ups work; the Clear History
button resets it.

Run as a script (the module form does not work: this package uses bare
`from engine import ...` imports, which need pct/ itself on sys.path):
    python pct/app.py

Launches on http://127.0.0.1:7860 (share=False).
"""
from __future__ import annotations

import json

import gradio as gr

from pct.engine import load_kb, prove
from pct.parser import Parser
from pct.renderer import render, render_trace

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from layout import DOMAINS, RESULTS_DIR, domain, results_path  # noqa: E402

_KB = load_kb(DOMAINS["physics"]["kb1"])
_PARSER = Parser(DOMAINS["physics"]["kb2"])


_FAILURE_PREFIX = {
    "F1": "F1 -- Depth exceeded",
    # F2 is defined in the taxonomy but subsumed: no code path emits it.
    "F2": "F2 -- Missing primitive",
    "F3": "F3 -- Missing rule for context",
    # F4 covers a parser/format error at the parse stage, and a genuine KB defect
    # (a reached guard or formula raised) at the engine stage.
    "F4": "F4 -- Parser/format error, or KB defect",
    "F5": "F5 -- Out of scope: no derivation exists",
    "F6": "F6 -- No applicable rule: every candidate's condition failed",
}


def _format_value(v, unit: str) -> str:
    if isinstance(v, float):
        return ("0" if v == 0.0 else f"{v:.4g}") + f" {unit}"
    return f"{v} {unit}"


def _result_tab_text(parsed: dict, engine_result: dict) -> str:
    if parsed.get("status") == "failure":
        ftype = parsed.get("type", "?")
        prefix = _FAILURE_PREFIX.get(ftype, ftype)
        return f"{prefix}\n\n{parsed.get('reason', '')}"
    if engine_result.get("status") == "failure":
        ftype = engine_result.get("type", "?")
        prefix = _FAILURE_PREFIX.get(ftype, ftype)
        reason = engine_result.get("reason", "")
        if ftype == "F5":
            pred = parsed.get("predicate", "?")
            return f"{prefix} for predicate `{pred}`\n\n{reason}"
        return f"{prefix}\n\n{reason}"
    return _format_value(engine_result["value"], engine_result["unit"])


def _derivation_tab_text(parsed: dict, engine_result: dict, use_renderer: bool) -> str:
    if parsed.get("status") == "failure":
        return "(no derivation -- parser rejected the query)"
    if engine_result.get("status") == "failure":
        return "(no derivation -- engine could not prove this goal)"
    return render(engine_result) if use_renderer else render_trace(engine_result)


def _goal_tab_text(parsed: dict) -> str:
    if parsed.get("status") == "failure":
        raw = parsed.get("raw", parsed)
        return json.dumps(raw, indent=2, default=str)
    return json.dumps(
        {
            "predicate": parsed["predicate"],
            "unit": parsed["unit"],
            "context": parsed["context"],
            "bindings": parsed["bindings"],
        },
        indent=2,
        default=str,
    )


def submit_query(query: str, history: list, use_renderer: bool):
    if history is None:
        history = []
    if not query or not query.strip():
        return history, "(enter a query)", "", ""

    if not history:
        _PARSER.reset()

    parsed = _PARSER.parse(query)
    if parsed.get("status") == "failure":
        engine_result = parsed
    else:
        goal = {
            "predicate": parsed["predicate"],
            "unit": parsed["unit"],
            "context": parsed["context"],
        }
        engine_result = prove(goal, parsed["bindings"], _KB)

    result_text = _result_tab_text(parsed, engine_result)
    derivation_text = _derivation_tab_text(parsed, engine_result, use_renderer)
    goal_text = _goal_tab_text(parsed)

    history = history + [(query, result_text)]
    return history, result_text, derivation_text, goal_text


def clear_history():
    _PARSER.reset()
    return [], "", "", ""


with gr.Blocks(title="PCT -- Proof-Carrying Text") as demo:
    gr.Markdown(
        "# Proof-Carrying Text\n"
        "Closed-world physics queries answered through a parser -> proof engine -> "
        "fixed-template renderer pipeline. Every answer carries its derivation."
    )
    history_state = gr.State([])

    with gr.Row():
        with gr.Column(scale=1):
            query_box = gr.Textbox(
                label="Query",
                placeholder='e.g. "what is the velocity of an object after falling for 3 seconds"',
                lines=4,
            )
            submit_btn = gr.Button("Submit", variant="primary")
            clear_btn = gr.Button("Clear History")
            renderer_toggle = gr.Checkbox(
                label="Use renderer (off = raw trace)",
                value=True,
            )

        with gr.Column(scale=2):
            with gr.Tabs():
                with gr.Tab("Result"):
                    result_box = gr.Textbox(
                        label="Final answer",
                        lines=6,
                        interactive=False,
                    )
                with gr.Tab("Derivation Trace"):
                    derivation_box = gr.Textbox(
                        label="Step-by-step derivation",
                        lines=24,
                        interactive=False,
                    )
                with gr.Tab("Goal State"):
                    gr.Markdown(
                        "**Interpreted goal state -- verify this matches your query**"
                    )
                    goal_box = gr.Code(language="json", interactive=False)

    submit_btn.click(
        submit_query,
        inputs=[query_box, history_state, renderer_toggle],
        outputs=[history_state, result_box, derivation_box, goal_box],
        show_progress="full",
    )
    clear_btn.click(
        clear_history,
        outputs=[history_state, result_box, derivation_box, goal_box],
    )


if __name__ == "__main__":
    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        theme=gr.themes.Soft(),
    )
