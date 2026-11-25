"""PCT deployed and oracle runs for biology and chemistry, by REPLAY.

This script never calls the model. The deployed run replays the frozen Stage-1
parses in results/<domain>/deployed_parses.json (written once by
capture_parses.py) through the engine; the oracle run feeds the same engine the
gold goal state. So every number it writes is reproducible from stored files.

The deployed path sees ONLY the parse. It never reads the query's gold form,
gold target, or expected answer: an earlier version did, choosing its output
format from the gold question form and answering a dropped-candidate is_a query
by comparing against the gold target. Both leaks are removed.

Deployed biology behaviour, driven by the parse alone:
  candidate named   a membership question: "yes" if membership is derived;
                    otherwise the typed failure, plus "derived: <taxon>" if the
                    specimen's classification is derivable. Never "no".
  no candidate      a classification question: the derived taxon, or the typed
                    failure.

Resolution and identification come from eval_biology / eval_chemistry, so the
oracle column reproduces the engine-only runs by construction.

Usage:
    python tools/eval/eval_pipeline.py biology
    python tools/eval/eval_pipeline.py chemistry
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from layout import DOMAINS, results_path  # noqa: E402

from pct.engine import prove  # noqa: E402

TOL = 0.01


def close(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return a == b
    return abs(a) < 1e-9 if b == 0 else abs(a - b) / abs(b) <= TOL


def rule_outputs(res: dict) -> list:
    """Every value a rule step of the derivation produced, in order."""
    return [s.get("output") for s in (res or {}).get("derivation", [])
            if s.get("type") == "rule"]


def rule_ids(res: dict) -> list:
    return [s.get("id") for s in (res or {}).get("derivation", [])
            if s.get("type") == "rule"]


# ===========================================================================
# BIOLOGY
# ===========================================================================
def bio_runner():
    import eval_biology as RB

    CHAR_KEYS = ("body_length", "geographic_range", "habitat")

    def _outcome(answer, res, failure=None, candidate=None, kind=None, taxon=None):
        return {"answer": answer, "result": res, "failure": failure,
                "candidate": candidate,
                "claim": ({"kind": kind, "taxon": taxon,
                           "derivation_outputs": rule_outputs(res),
                           "rules": rule_ids(res)} if kind else None)}

    def _answer_membership(target, traits, chars, candidate):
        out = RB.answer_is_a(target, traits=traits or None, chars=chars or None)
        a = out["answer"]
        if a == "yes":
            return _outcome("yes", out["result"], None, candidate, "member", target)
        if a.startswith("derived: "):
            tx = a[len("derived: "):]
            return _outcome(a, out["result"], out["failure"], candidate,
                            "classification", tx)
        return _outcome("refuse", out["result"], out["failure"], candidate)

    def _answer_classification(traits, chars):
        tx, res = RB.classify(traits=traits or None, chars=chars or None)
        if tx is not None:
            return _outcome(tx, res, None, None, "classification", tx)
        return _outcome("refuse", res, RB.typed_failure(res))

    def deployed(parsed):
        """From the parse alone. No query metadata is visible here."""
        b = parsed.get("bindings", {})
        cand = b.get("candidate_taxon", {}).get("value")
        traits = {k: True for k, v in b.items()
                  if k not in CHAR_KEYS + ("candidate_taxon", "is_felid")
                  and v.get("value") is True}
        chars = {k: b[k]["value"] for k in CHAR_KEYS if k in b}

        if cand is not None:
            if cand not in RB.TAXA:
                f = {"status": "failure", "type": "F4", "stage": "parser",
                     "reason": f"taxon {cand!r} is not in the KB"}
                return _outcome("refuse", f, {"type": "F4", "reason": f["reason"],
                                              "goal": None, "rule": None}, cand)
            return _answer_membership(cand, traits, chars, cand)
        if not chars and not traits:
            f = {"status": "failure", "type": "F5", "family": "F5",
                 "reason": "no evidence stated and no candidate named"}
            return _outcome("refuse", f, {"type": "F5", "reason": f["reason"],
                                          "goal": None, "rule": None})
        return _answer_classification(traits, chars)

    def oracle(q):
        """The gold goal state: gold evidence AND the gold question form."""
        if q["part"] in ("vertebrate", "felid", "join"):
            # join (2026-11-22): trait evidence AND felid characters in one query
            traits = {t: True for t in q.get("gold_traits") or []}
            ch = q.get("gold_chars") or {}
            if q["gold_form"] == "is_a":
                return _answer_membership(q["gold_target"], traits, ch, q["gold_target"])
            return _answer_classification(traits, ch)
        f = {"status": "failure", "type": "-",
             "reason": "no gold goal state exists for this query"}
        return _outcome("refuse", f)

    def goldstr(q):
        return json.dumps(q.get("gold_traits") or q.get("gold_chars") or None)

    return deployed, oracle, goldstr


# ===========================================================================
# CHEMISTRY  (behaviour unchanged; wrapped in the same outcome shape)
# ===========================================================================
def chem_runner():
    import eval_chemistry as RS

    ROLE_KEYS = ("reactant", "product", "reactant_a", "reactant_b")

    def _prove(reaction, roles, given, goal_pred):
        """Uses eval_chemistry.resolve, so molar masses and coefficients can only
        come from the KB -- never from the parser."""
        try:
            bnd = RS.resolve({"reaction": reaction, "roles": roles, "given": given})
        except RS.Unresolved as e:
            return None, {"status": "failure", "type": "F4", "stage": "resolution",
                          "reason": str(e)}
        if goal_pred not in RS.UNIT:
            return None, {"status": "failure", "type": "F4", "stage": "parser",
                          "reason": f"predicate {goal_pred!r} is not a stoichiometry goal"}
        goal = {"predicate": goal_pred, "unit": RS.UNIT[goal_pred],
                "context": RS.CONTEXT}
        r = prove(goal, bnd, RS.KB, d_max=8)
        return (r.get("value") if r.get("status") == "success" else None), r

    def _outcome(v, r):
        ok = r.get("status") == "success"
        return {"answer": v if ok else "refuse", "result": r,
                "failure": None if ok else {"type": r.get("type"),
                                            "reason": r.get("reason"),
                                            "goal": r.get("goal"),
                                            "rule": r.get("rule")},
                "candidate": None,
                "claim": ({"kind": "value", "value": v, "unit": r.get("unit"),
                           "derivation_outputs": rule_outputs(r),
                           "rules": rule_ids(r)} if ok else None)}

    def deployed(parsed):
        b = parsed.get("bindings", {})
        reaction = b.get("reaction", {}).get("value")
        # no reaction bound: a one-substance conversion (resolve supplies molar masses only)
        if reaction is not None and reaction not in RS.KB["reactions"]:
            return _outcome(None, {"status": "failure", "type": "F4", "stage": "parser",
                                   "reason": f"reaction {reaction!r} is not in the KB"})
        roles = {k: b[k]["value"] for k in ROLE_KEYS if k in b}
        given = {k: v["value"] for k, v in b.items()
                 if k not in ROLE_KEYS + ("reaction",)}
        v, r = _prove(reaction, roles, given, parsed["predicate"])
        return _outcome(v, r)

    def oracle(q):
        g = RS.gold_goal(q)
        if not g:
            return _outcome(None, {"status": "failure", "type": "-",
                                   "reason": "no gold goal state exists for this query"})
        v, r = _prove(g["reaction"], g["roles"], g["given"], g["goal"])
        return _outcome(v, r)

    def goldstr(q):
        g = q.get("gold")
        if not g:
            return "none (out of scope)"
        return f"{g['goal']} | {json.dumps(g['roles'])} | {json.dumps(g['given'])}"

    return deployed, oracle, goldstr


RUNNERS = {"biology": bio_runner, "chemistry": chem_runner}


def replay(domain: str, parses: dict) -> dict:
    """Replay stored parses (by query id) through the engine. Pure function of
    the stored parses, the query file and the KB."""
    deployed, oracle, goldstr = RUNNERS[domain]()
    queries = json.load(open(DOMAINS[domain]["queries"]["natural_language"],
                             encoding="utf-8"))["queries"]
    rows = []
    for q in queries:
        parsed = parses[q["id"]]
        if parsed.get("status") == "failure":
            dep = {"answer": "refuse", "result": dict(parsed, stage="parser"),
                   "failure": {"type": parsed.get("type"), "reason": parsed.get("reason"),
                               "goal": None, "rule": None},
                   "candidate": None, "claim": None}
        else:
            dep = deployed(parsed)
        orc = oracle(q)
        dres, ores = dep["result"], orc["result"]
        rows.append({
            "id": q["id"], "part": q["part"], "nl": q["nl"],
            "scope": q.get("scope", "out" if q["part"] in ("out_of_scope", "oos") else "in"),
            "truth": q.get("truth", q.get("expected")),
            "expected": q["expected"],
            "expected_classification": q.get("expected_classification"),
            "deployed": dep["answer"],
            "ok": bool(close(dep["answer"], q["expected"])),
            "deployed_status": dres.get("status"),
            "deployed_type": dres.get("type") if dres.get("status") != "success"
            else None,
            "stage": dres.get("stage", "engine"),
            "deployed_failure": dep["failure"],
            "deployed_candidate": dep["candidate"],
            "deployed_claim": dep["claim"],
            "parsed": ({k: v["value"] for k, v in parsed.get("bindings", {}).items()}
                       if parsed.get("status") != "failure" else None),
            "parsed_predicate": parsed.get("predicate"),
            "oracle": orc["answer"],
            "oracle_status": ores.get("status"),
            "oracle_type": ores.get("type") if ores.get("status") != "success" else None,
            "oracle_failure": orc["failure"],
            "oracle_claim": orc["claim"],
            "gold": goldstr(q),
        })
    return {"domain": domain, "n": len(rows), "rows": rows}


def load_frozen_parses(domain: str) -> tuple[dict, dict]:
    path = results_path(domain, "deployed_parses.json")
    if not os.path.exists(path):
        raise SystemExit(f"no frozen parses at {path}\n"
                         f"capture once with: python tools/eval/capture_parses.py {domain}")
    data = json.load(open(path, encoding="utf-8"))
    return {r["id"]: r["parsed"] for r in data["rows"]}, data


def summarize(out: dict) -> None:
    rows = out["rows"]
    ins = [r for r in rows if r["scope"] == "in"]
    claims = [r for r in ins if r["deployed"] != "refuse"]
    proved = [r for r in claims if r["deployed_status"] == "success"
              and r["deployed_claim"] and r["deployed_claim"]["rules"]]
    p_types = Counter(r["deployed_type"] for r in rows
                      if r["deployed_status"] != "success" and r["stage"] == "parser")
    e_types = Counter(r["deployed_type"] for r in rows
                      if r["deployed_status"] != "success" and r["stage"] != "parser")
    with_failure = sum(1 for r in claims if r["deployed_failure"])
    print(f"  {out['domain'].upper()}  n={len(rows)}  in-scope {len(ins)}")
    print(f"  DEPLOYED  claims {len(claims)}/{len(ins)}   carrying a derivation "
          f"{len(proved)}/{len(claims)}")
    print(f"            claims that also carry a typed failure for the asked taxon: "
          f"{with_failure}")
    print(f"            parser-stage refusals {dict(p_types)}   "
          f"engine-stage refusals {dict(e_types)}")
    oc = [r for r in ins if r["oracle"] != "refuse"]
    print(f"  ORACLE    claims {len(oc)}/{len(ins)}")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
        print("usage: python tools/eval/eval_pipeline.py {biology|chemistry}",
              file=sys.stderr)
        return 2
    arg = sys.argv[1].lower()
    alias = {"bio": "biology", "chem": "chemistry", "stoich": "chemistry"}
    domain = alias.get(arg, arg)
    if domain not in RUNNERS:
        print(f"unknown domain {sys.argv[1]!r}; expected biology or chemistry",
              file=sys.stderr)
        return 2
    parses, meta = load_frozen_parses(domain)
    out = replay(domain, parses)
    out["frozen_parses"] = os.path.relpath(results_path(domain, "deployed_parses.json"), ROOT)
    out["captured_at"] = meta.get("captured_at")
    out["settings"] = meta.get("settings")
    summarize(out)
    path = results_path(domain, "pipeline.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
