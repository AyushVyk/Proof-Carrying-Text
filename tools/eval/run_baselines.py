"""Run RAG and the raw baseline on a domain's natural-language query set and
store every raw response. Scoring is NOT done here: it is done afterwards, from
these stored responses, by the pre-registered scorer (tools/analysis/score.py).

Same query set PCT's deployed run used (out-of-scope queries included), same
model, same generation settings, same embedding model and top-k as the physics
baselines. The prompts come from the domain's KB2.

Refuses to run if the rubric lock is missing or its hashes no longer match the
files, so a baseline can never be run against an unlocked or edited rubric.

Usage (needs the virtualenv with faiss + sentence-transformers):
    .venv/Scripts/python tools/eval/run_baselines.py chemistry
    .venv/Scripts/python tools/eval/run_baselines.py biology
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for p in (ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from layout import DOMAINS, results_path  # noqa: E402

import baseline_rag as BR  # noqa: E402


def _sha_file(p: str) -> str:
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def check_rubric_lock() -> dict:
    lock_path = os.path.join(ROOT, "docs", "scoring_rubric.lock.json")
    if not os.path.exists(lock_path):
        raise SystemExit("no rubric lock: write and lock docs/scoring_rubric.md first")
    lock = json.load(open(lock_path, encoding="utf-8"))
    for rel, h in lock["files"].items():
        if _sha_file(os.path.join(ROOT, rel)) != h:
            raise SystemExit(f"rubric lock broken: {rel} changed since {lock['locked_at']}")
    return lock


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("chemistry", "biology"):
        print("usage: run_baselines.py {chemistry|biology}", file=sys.stderr)
        return 2
    domain = sys.argv[1]
    lock = check_rubric_lock()
    out_path = results_path(domain, "baselines.json")
    if os.path.exists(out_path):
        raise SystemExit(f"{out_path} exists; stored baseline runs are frozen, not overwritten")

    d = DOMAINS[domain]
    kb = json.load(open(d["kb1"], encoding="utf-8"))
    kb2 = json.load(open(d["kb2"], encoding="utf-8"))
    prompts = BR.baseline_prompts(kb2)
    from capture_parses import check_query_lock
    query_sha = check_query_lock(d["queries"]["natural_language"])
    queries = json.load(open(d["queries"]["natural_language"], encoding="utf-8"))["queries"]
    rag = BR.RAG(kb, prompts["rag_system"])

    out = {
        "domain": domain,
        "run_started": _dt.datetime.now().isoformat(timespec="seconds"),
        "rubric_locked_at": lock["locked_at"],
        "rubric_lock_hashes": lock["files"],
        "settings": {"model": BR.DEFAULT_GEN_MODEL, "options": BR.GEN_OPTIONS,
                     "embed_model": BR.DEFAULT_EMBED_MODEL, "top_k": rag.top_k,
                     "rag_system_prompt": prompts["rag_system"],
                     "raw_system_prompt": prompts["raw_system"],
                     "n_chunks": len(rag.chunks),
                     "chunks_sha256": hashlib.sha256(json.dumps(
                         rag.chunks, ensure_ascii=False).encode()).hexdigest()},
        "query_file_sha256": query_sha,
        "query_texts_sha256": hashlib.sha256(json.dumps(
            [[q["id"], q["nl"]] for q in queries], ensure_ascii=False).encode()).hexdigest(),
        "rows": [],
    }
    for q in queries:
        t0 = time.time()
        r = rag.answer(q["nl"])
        rag_row = {"content": r["content"], "retrieved": r["retrieved"],
                   "elapsed": round(time.time() - t0, 3)}
        raw_row = BR.raw_answer(q["nl"], prompts["raw_system"])
        raw_row["elapsed"] = round(raw_row["elapsed"], 3)
        out["rows"].append({"id": q["id"], "nl": q["nl"], "rag": rag_row,
                            "baseline": raw_row})
        print(f"  {q['id']:>3}  rag {len(r['content']):>4} chars  raw "
              f"{len(raw_row['content']):>4} chars  {q['nl'][:60]}", flush=True)
    out["run_finished"] = _dt.datetime.now().isoformat(timespec="seconds")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
