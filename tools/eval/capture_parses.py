"""Capture and freeze Stage-1 parses for a domain's natural-language query set.

This is the ONLY step of the deployed evaluation that calls the model. It stores
the parser's output for every query exactly as physics stores it (the dict
`Parser.parse()` returns, failures included), plus the settings and the hashes of
everything that determined it. Every deployed table is then computed by
REPLAYING these stored parses through the engine, so the paper's numbers are
reproducible without calling the model again.

It also records a small within-session repeat test (the same query parsed several
times in a row) as evidence for or against parser determinism.

Usage:
    python tools/eval/capture_parses.py biology
    python tools/eval/capture_parses.py chemistry
    python tools/eval/capture_parses.py physics      # the 300-query set
    python tools/eval/capture_parses.py repeats      # determinism probe only
    python tools/eval/capture_parses.py physics --run 2
        # one of several independent runs: results/<domain>/deployed_runs/run2.json
        # (never overwritten; install one as deployed_parses.json with --install)
    python tools/eval/capture_parses.py physics --install 1
        # copy run1.json to deployed_parses.json, moving the previous file to
        # previous/deployed_parses_<its captured_at date>.json

A row the parser refuses because Ollama evaluated fewer prompt tokens than the
prompt has (truncation guard) ABORTS the run: nothing is written.
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
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from layout import DOMAINS, results_path  # noqa: E402

from pct import parser as parser_module  # noqa: E402
from pct.parser import Parser  # noqa: E402


def _sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def settings_record(p: Parser, kb2_path: str) -> dict:
    """What determined the parses. Read from the parser, not restated."""
    return {
        "model": p.model,
        "endpoint": "/api/chat",
        "options": {"temperature": 0.0, "num_ctx": parser_module.NUM_CTX},
        "format": "json",
        "stream": False,
        "seed": None,
        "other_sampling_parameters": "none set; Ollama defaults apply",
        "truncation_guard": "parser probes the full prompt token count once and returns F4 on any call evaluating fewer tokens",
        "parser_py_sha256": _sha(parser_module.__file__),
        "kb2_sha256": _sha(kb2_path),
        "system_prompt_sha256": hashlib.sha256(p.system_prompt.encode()).hexdigest(),
    }


QUERY_LOCKS = os.path.join(ROOT, "domains", "query_set_locks.json")


def check_query_lock(path: str) -> str:
    """Refuse a query file whose sha256 differs from the one recorded when the set was
    validated and locked (tools/analysis/validate_query_sets.py --lock)."""
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    locks = json.load(open(QUERY_LOCKS, encoding="utf-8")) if os.path.exists(QUERY_LOCKS)         else {}
    if rel not in locks:
        raise SystemExit(f"query file {rel} is not locked (run validate_query_sets.py --lock)")
    h = _sha(path)
    if h != locks[rel]:
        raise SystemExit(f"query file {rel} changed since it was locked: {h} != {locks[rel]}")
    return h


def load_queries(domain: str) -> list[dict]:
    """[{id, nl}] for a domain. Physics's query set is the structured 300-query
    list (no separate NL file): its `query` text is what the parser is shown, and a
    query's id is its 1-based position, as in results/physics/pipeline.json."""
    d = DOMAINS[domain]
    check_query_lock(d["queries"].get("natural_language") or d["queries"]["structured"])
    if "natural_language" in d["queries"]:
        return json.load(open(d["queries"]["natural_language"], encoding="utf-8"))["queries"]
    rows = json.load(open(d["queries"]["structured"], encoding="utf-8"))
    return [{"id": i, "nl": r["query"]} for i, r in enumerate(rows, 1)]


class TruncatedRun(SystemExit):
    pass


def _truncated(parsed: dict) -> bool:
    r = str(parsed.get("reason") or "")
    return parsed.get("status") == "failure" and (
        r.startswith("prompt truncated by Ollama") or "does not fit num_ctx" in r)


def capture(domain: str, run: int | None = None) -> int:
    d = DOMAINS[domain]
    queries = load_queries(domain)
    p = Parser(d["kb2"])
    out = {"domain": domain,
           "captured_at": _dt.datetime.now().isoformat(timespec="seconds"),
           "query_set": os.path.relpath(d["queries"].get("natural_language")
                                        or d["queries"]["structured"], ROOT),
           "kb1_sha256": _sha(d["kb1"]),
           # hash of (id, text) only: expectation fields may be annotated later
           # without changing what the model was shown
           "query_file_sha256": _sha(d["queries"].get("natural_language")
                                     or d["queries"]["structured"]),
           "query_texts_sha256": hashlib.sha256(json.dumps(
               [[q["id"], q["nl"]] for q in queries], ensure_ascii=False
           ).encode()).hexdigest(),
           "settings": settings_record(p, d["kb2"]),
           "rows": []}
    for q in queries:
        p.reset()                       # no follow-up carry-over between queries
        t0 = time.time()
        parsed = p.parse(q["nl"])
        if _truncated(parsed):
            raise TruncatedRun(f"ABORTED run: query {q['id']} truncated: {parsed['reason']}")
        out["rows"].append({"id": q["id"], "nl": q["nl"], "parsed": parsed,
                            "elapsed": round(time.time() - t0, 3),
                            "prompt_eval_count": p.last_prompt_eval_count})
        st = parsed.get("status", "success")
        print(f"  {q['id']:>3} {st:<8} {q['nl'][:70]}", flush=True)
    out["settings"]["system_prompt_tokens_untruncated"] = p._prompt_tokens.get(p.system_prompt)
    out["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    if run is not None:
        out["run"] = run
        path = os.path.join(os.path.dirname(results_path(domain, "deployed_parses.json")),
                            "deployed_runs", f"run{run}.json")
        if os.path.exists(path):
            raise SystemExit(f"{path} exists; refusing to overwrite a stored run")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"wrote {path}")
        return 0
    path = results_path(domain, "deployed_parses.json")
    if os.path.exists(path):
        sup = os.path.join(os.path.dirname(path), "previous")
        os.makedirs(sup, exist_ok=True)
        dst = os.path.join(sup, "deployed_parses.json")
        if os.path.exists(dst):
            raise SystemExit(f"{dst} exists; refusing to overwrite a previous run")
        os.replace(path, dst)
        print(f"moved previous run to {dst}")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"wrote {path}")
    return 0


def repeats(n_repeat: int = 3) -> int:
    """Parse a fixed handful of queries several times in a row, same process."""
    picks = {"biology": [1, 5, 17, 24], "chemistry": [1, 11, 15, 21]}
    out = {"captured_at": _dt.datetime.now().isoformat(timespec="seconds"),
           "n_repeat": n_repeat, "rows": []}
    for domain, ids in picks.items():
        d = DOMAINS[domain]
        qs = {q["id"]: q for q in json.load(open(d["queries"]["natural_language"],
                                                 encoding="utf-8"))["queries"]}
        p = Parser(d["kb2"])
        out.setdefault("settings", {})[domain] = settings_record(p, d["kb2"])
        for qid in ids:
            got = []
            for _ in range(n_repeat):
                p.reset()
                got.append(p.parse(qs[qid]["nl"]))
            same = all(json.dumps(g, sort_keys=True) == json.dumps(got[0], sort_keys=True)
                       for g in got)
            out["rows"].append({"domain": domain, "id": qid, "nl": qs[qid]["nl"],
                                "identical_across_repeats": same, "parses": got})
            print(f"  {domain:<10} {qid:>3} identical={same}", flush=True)
    path = os.path.join(ROOT, "results", "determinism_repeats.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"wrote {path}")
    return 0


def install(domain: str, run: int) -> int:
    """Make stored run `run` the domain's deployed_parses.json (what replay reads)."""
    base = os.path.dirname(results_path(domain, "deployed_parses.json"))
    src = os.path.join(base, "deployed_runs", f"run{run}.json")
    data = json.load(open(src, encoding="utf-8"))
    path = results_path(domain, "deployed_parses.json")
    if os.path.exists(path):
        old = json.load(open(path, encoding="utf-8"))
        if old.get("run") is None:              # a pre-multi-run file: keep it
            sup = os.path.join(base, "previous")
            os.makedirs(sup, exist_ok=True)
            dst = os.path.join(sup, "deployed_parses_" + str(old.get("captured_at", "undated"))
                               .replace(":", "")[:15] + ".json")
            if os.path.exists(dst):
                raise SystemExit(f"{dst} exists; refusing to overwrite a previous run")
            os.replace(path, dst)
            print(f"moved previous run to {dst}")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"installed run{run} as {path}")
    return 0


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[2] in ("--run", "--install") and sys.argv[1] in DOMAINS:
        n = int(sys.argv[3])
        return capture(sys.argv[1], n) if sys.argv[2] == "--run" else install(sys.argv[1], n)
    if len(sys.argv) != 2:
        print("usage: python tools/eval/capture_parses.py {biology|chemistry|repeats}",
              file=sys.stderr)
        return 2
    arg = sys.argv[1].lower()
    if arg == "repeats":
        return repeats()
    if arg in DOMAINS:
        return capture(arg)
    print(f"unknown argument {sys.argv[1]!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
