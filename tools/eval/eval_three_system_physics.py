"""Three-system evaluation harness on the 300-query set.

Runs each query through PCT, RAG, and Mistral baseline. Saves per-query
JSON + flat text dumps for manual hallucination review. Checkpoints to
disk after every query so partial runs are recoverable.

Run:
    python tools/eval/eval_three_system_physics.py

Outputs:
    results/evaluation_300.json       (full structured log)
    results/baseline_outputs.txt      (raw Mistral outputs, no engine)
    results/rag_outputs.txt           (RAG outputs)
    results/pct_outputs.txt           (PCT rendered outputs)
    results/summary_300.txt           (printed summary table)
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from pct.engine import load_kb, prove
from pct.parser import Parser
from baseline_rag import RAG, baseline_prompts, raw_payload
from pct.renderer import render


_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from layout import DOMAINS, RESULTS_DIR, domain, results_path  # noqa: E402

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
BASELINE_MODEL = "mistral:7b-instruct-q4_K_M"

QUERIES_PATH = DOMAINS["physics"]["queries"]["structured"]
RESULTS_PATH = results_path("physics", "evaluation_300.json")
SUMMARY_PATH = results_path("physics", "summary_300.txt")
PCT_OUT = results_path("physics", "pct_outputs.txt")
RAG_OUT = results_path("physics", "rag_outputs.txt")
BASELINE_OUT = results_path("physics", "baseline_outputs.txt")


def run_pct(parser: Parser, kb: dict, query: str) -> dict:
    t0 = time.time()
    parser.reset()
    parsed = parser.parse(query)
    if parsed.get("status") == "failure":
        return {
            "elapsed": time.time() - t0,
            "parsed": parsed,
            "engine": None,
            "rendered": render(parsed),
            "refused": True,
            "failure_type": parsed.get("type"),
        }
    goal = {
        "predicate": parsed["predicate"],
        "unit": parsed["unit"],
        "context": parsed["context"],
    }
    eng = prove(goal, parsed["bindings"], kb)
    refused = eng.get("status") == "failure"
    return {
        "elapsed": time.time() - t0,
        "parsed": parsed,
        "engine": eng,
        "rendered": render(eng),
        "refused": refused,
        "failure_type": eng.get("type") if refused else None,
    }


def run_baseline(query: str, system_prompt: str | None = None) -> dict:
    """The raw baseline. Its system prompt comes from the physics KB2's
    `baseline_prompts.raw_system`, which is null: no system message, exactly the
    payload this harness has always sent."""
    t0 = time.time()
    payload = raw_payload(query, system_prompt, BASELINE_MODEL)
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body.get("message", {}).get("content", "")
    except (urllib.error.URLError, TimeoutError) as e:
        content = f"(transport error: {e})"
    refused = _detect_refusal(content)
    return {"elapsed": time.time() - t0, "content": content, "refused": refused}


def run_rag(rag: RAG, query: str) -> dict:
    t0 = time.time()
    r = rag.answer(query)
    refused = _detect_refusal(r["content"])
    return {
        "elapsed": time.time() - t0,
        "content": r["content"],
        "retrieved": r["retrieved"],
        "refused": refused,
    }


_REFUSAL_TOKENS = (
    "i cannot answer", "cannot answer", "i don't know", "i do not know",
    "unable to", "i'm sorry", "i am sorry", "no information",
    "not enough information", "insufficient information", "cannot determine",
)


def _detect_refusal(text: str) -> bool:
    t = (text or "").lower()
    return any(tok in t for tok in _REFUSAL_TOKENS)


def _serializable(r: dict) -> dict:
    return {
        "category": r["category"],
        "query": r["query"],
        "expected_predicate": r.get("expected_predicate"),
        "expected_context": r.get("expected_context"),
        "expected_unit": r.get("expected_unit"),
        "expected_value": r.get("expected_value"),
        "expected_depth": r.get("expected_depth"),
        "rule_id": r.get("rule_id"),
        "params": r.get("params"),
        "pct": r.get("pct"),
        "rag": r.get("rag"),
        "baseline": r.get("baseline"),
    }


def _save_checkpoint(rows: list[dict]) -> None:
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump([_serializable(r) for r in rows], f, indent=2, default=str)


def _append_text(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def _truncate(s: str, n: int = 100) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> None:
    if not os.path.exists(QUERIES_PATH):
        print(f"missing {QUERIES_PATH}; run `python tools/build/build_queries_physics.py` first", file=sys.stderr)
        sys.exit(2)

    with open(QUERIES_PATH, "r", encoding="utf-8") as f:
        queries = json.load(f)
    print(f"Loaded {len(queries)} queries from {QUERIES_PATH}", flush=True)

    print("Loading PCT components...", flush=True)
    parser = Parser(DOMAINS["physics"]["kb2"])
    kb = load_kb(DOMAINS["physics"]["kb1"])

    print("Loading RAG components (sentence-transformers + FAISS)...", flush=True)
    prompts = baseline_prompts(json.load(open(DOMAINS["physics"]["kb2"], encoding="utf-8")))
    rag = RAG(kb, prompts["rag_system"])

    # Reset output text files (truncate).
    for p in (PCT_OUT, RAG_OUT, BASELINE_OUT):
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# {os.path.basename(p)}\n# Generated by eval_three_system_physics\n\n")

    rows: list[dict] = []
    t_start = time.time()
    for i, q in enumerate(queries, 1):
        row = dict(q)
        prefix = f"[{i:>3}/{len(queries)}] [{q['category']:>7}]"
        # PCT
        try:
            row["pct"] = run_pct(parser, kb, q["query"])
        except Exception as e:
            row["pct"] = {"error": str(e), "elapsed": 0, "refused": True, "failure_type": "exception"}
        # RAG
        try:
            row["rag"] = run_rag(rag, q["query"])
        except Exception as e:
            row["rag"] = {"error": str(e), "elapsed": 0, "refused": True}
        # Baseline
        try:
            row["baseline"] = run_baseline(q["query"], prompts["raw_system"])
        except Exception as e:
            row["baseline"] = {"error": str(e), "elapsed": 0, "refused": False}

        rows.append(row)

        # Console progress
        pct_t = row["pct"].get("elapsed", 0)
        rag_t = row["rag"].get("elapsed", 0)
        bl_t = row["baseline"].get("elapsed", 0)
        pct_short = _truncate(row["pct"].get("rendered", ""), 60)
        print(
            f"{prefix} pct={pct_t:.1f}s rag={rag_t:.1f}s bl={bl_t:.1f}s | {q['query'][:70]}",
            flush=True,
        )
        print(f"           PCT: {pct_short}", flush=True)

        # Per-query text dumps
        _append_text(
            PCT_OUT,
            f"\n===== Q{i} [{q['category']}] =====\n"
            f"Query: {q['query']}\n"
            f"PCT output:\n{row['pct'].get('rendered','')}\n"
            f"Refused: {row['pct'].get('refused')} ({row['pct'].get('failure_type')})\n"
            f"Elapsed: {pct_t:.2f}s\n",
        )
        _append_text(
            RAG_OUT,
            f"\n===== Q{i} [{q['category']}] =====\n"
            f"Query: {q['query']}\n"
            f"Retrieved chunks: {[r['id'] for r in row['rag'].get('retrieved',[])]}\n"
            f"RAG output:\n{row['rag'].get('content','')}\n"
            f"Refused: {row['rag'].get('refused')}\n"
            f"Elapsed: {rag_t:.2f}s\n",
        )
        _append_text(
            BASELINE_OUT,
            f"\n===== Q{i} [{q['category']}] =====\n"
            f"Query: {q['query']}\n"
            f"Baseline output:\n{row['baseline'].get('content','')}\n"
            f"Refused: {row['baseline'].get('refused')}\n"
            f"Elapsed: {bl_t:.2f}s\n",
        )

        # Periodic JSON checkpoint
        if i % 5 == 0 or i == len(queries):
            _save_checkpoint(rows)

    print(f"\nTotal wall time: {(time.time() - t_start) / 60:.1f} min", flush=True)
    _save_checkpoint(rows)
    _print_and_save_summary(rows)


def _print_and_save_summary(rows: list[dict]) -> None:
    in_scope = [r for r in rows if r["category"] != "oos"]
    oos = [r for r in rows if r["category"] == "oos"]

    pct_in_answered = sum(1 for r in in_scope if not r["pct"].get("refused"))
    rag_in_answered = sum(1 for r in in_scope if not r["rag"].get("refused"))
    bl_in_answered = sum(1 for r in in_scope if not r["baseline"].get("refused"))

    pct_oos_refused = sum(1 for r in oos if r["pct"].get("refused"))
    rag_oos_refused = sum(1 for r in oos if r["rag"].get("refused"))
    bl_oos_refused = sum(1 for r in oos if r["baseline"].get("refused"))

    pct_correct = 0
    pct_correct_in_scope_attempted = 0
    for r in in_scope:
        eng = r["pct"].get("engine")
        if eng and eng.get("status") == "success":
            pct_correct_in_scope_attempted += 1
            ev = r.get("expected_value")
            if ev is not None and r.get("expected_unit") == eng.get("unit"):
                actual = eng["value"]
                if isinstance(ev, (int, float)) and isinstance(actual, (int, float)):
                    if ev == 0:
                        if abs(actual) < 1e-6:
                            pct_correct += 1
                    elif abs(actual - ev) / abs(ev) <= 0.01:
                        pct_correct += 1

    pct_times = [r["pct"]["elapsed"] for r in rows if "elapsed" in r["pct"]]
    rag_times = [r["rag"]["elapsed"] for r in rows if "elapsed" in r["rag"]]
    bl_times = [r["baseline"]["elapsed"] for r in rows if "elapsed" in r["baseline"]]

    pct_failure_types: dict[str, int] = {}
    for r in rows:
        ft = r["pct"].get("failure_type")
        if ft:
            pct_failure_types[ft] = pct_failure_types.get(ft, 0) + 1

    pct_depths = []
    for r in in_scope:
        eng = r["pct"].get("engine")
        if eng and eng.get("status") == "success":
            d = sum(1 for s in eng.get("derivation", []) if s["type"] == "rule")
            pct_depths.append(d)

    def pct_str(num, den):
        return f"{num}/{den} ({100*num/den:.1f}%)" if den else f"{num}/0"

    n = len(rows)
    lines = [
        "=" * 76,
        "PCT 300-QUERY EVALUATION  -  Three-system comparison",
        "=" * 76,
        "",
        f"{'Metric':<42}{'Baseline':<15}{'RAG':<15}{'PCT':<15}",
        "-" * 76,
        f"{'Queries run':<42}{n:<15}{n:<15}{n:<15}",
        f"{'In-scope answered (non-refusal)':<42}"
        f"{pct_str(bl_in_answered, len(in_scope)):<15}"
        f"{pct_str(rag_in_answered, len(in_scope)):<15}"
        f"{pct_str(pct_in_answered, len(in_scope)):<15}",
        f"{'OOS refusal rate':<42}"
        f"{pct_str(bl_oos_refused, len(oos)):<15}"
        f"{pct_str(rag_oos_refused, len(oos)):<15}"
        f"{pct_str(pct_oos_refused, len(oos)):<15}",
        f"{'Typed failure on refusal':<42}{'no':<15}{'no':<15}{'yes (F1-F5)':<15}",
        f"{'Proof trace returned':<42}{'never':<15}{'never':<15}{'always':<15}",
        f"{'Mean response time (s)':<42}"
        f"{statistics.mean(bl_times):<15.2f}"
        f"{statistics.mean(rag_times):<15.2f}"
        f"{statistics.mean(pct_times):<15.2f}",
        f"{'Stdev response time (s)':<42}"
        f"{statistics.stdev(bl_times):<15.2f}"
        f"{statistics.stdev(rag_times):<15.2f}"
        f"{statistics.stdev(pct_times):<15.2f}",
        f"{'Hallucination (manual eval)':<42}{'TBD':<15}{'TBD':<15}{'0% by construction':<15}",
        "",
        f"PCT in-scope numerical correctness: {pct_str(pct_correct, len(in_scope))}",
        f"PCT in-scope attempts (engine produced answer): {pct_correct_in_scope_attempted}",
        f"PCT failure-type breakdown: {pct_failure_types}",
        f"PCT median derivation depth: {statistics.median(pct_depths) if pct_depths else 0}",
        f"PCT max derivation depth:    {max(pct_depths) if pct_depths else 0}",
        "",
        f"Manual hallucination review:  see {BASELINE_OUT} and {RAG_OUT}",
        f"Full per-query log:           see {RESULTS_PATH}",
    ]
    summary = "\n".join(lines)
    print("\n" + summary)
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        f.write(summary)


if __name__ == "__main__":
    main()
