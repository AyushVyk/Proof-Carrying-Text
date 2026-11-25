"""Final scoring pass (2026-11-24): merge the manual-review verdicts into scores.json.

The rubric and score.py stay locked. score.py produces results/scores.json from stored
outputs; that file is kept as results/final_scoring/scores_locked_auto.json and this
script writes results/scores.json = the locked outcomes with every manual_review row
replaced by its verdict (results/manual_review_final.jsonl, joined by review_id through
results/manual_review_key.json):

  correct          -> correct
  incorrect        -> wrong
  refusal          -> refusal
  partial_ancestor -> partial_ancestor: answered, not correct, NOT in the hallucination
                      numerator (a true claim, less specific)
  overspecific     -> overspecific: answered, not correct, IN the hallucination
                      numerator (a claim the stated characters do not support)
  no_answer        -> the row was a transport error; its query was re-run with identical
                      settings (tools/eval/rerun_transport_errors.py) and re-scored by
                      score.py. Its outcome is the locked scorer's; if that is manual
                      review again it stays manual review (listed, not scored here).
                      A query that timed out again was re-sent with only the client wait
                      limit raised to 15 min; one exceeding that is "no_response": in
                      scope, not correct, not answered, not manual review.
  needs_candidate  -> PCT `yes` on a what-is question: the claim "it is a c" for the
                      parsed candidate c (run-4 parse), scored by rubric 4.2 with
                      score.Taxa.judge: equal -> correct, c an ancestor of the truth ->
                      partial_ancestor, a descendant -> overspecific, otherwise wrong.

Aggregates are recomputed with `aggregate` below: score.aggregate's formulas, with
partial_ancestor and overspecific counted as answered and not correct, and overspecific
added to the wrong answers (hence to the rubric's Sec. 6 hallucination numerator).
For PCT the table's hallucination rate stays the replay definition (split_metrics).

    python tools/analysis/final_scoring.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for p in (ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from layout import DOMAINS, RESULTS_DIR, results_path  # noqa: E402
import score as S  # noqa: E402  (locked: imported, not modified)

PARTIAL, OVERSPEC = "partial_ancestor", "overspecific"
NO_RESPONSE = "no_response"     # generation exceeded the 15-min client limit on re-run
RERUN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "results",
                     "transport_rerun.json")
AUTO = os.path.join(RESULTS_DIR, "final_scoring", "scores_locked_auto.json")
VERDICTS = os.path.join(RESULTS_DIR, "manual_review_final.jsonl")
KEY = os.path.join(RESULTS_DIR, "manual_review_key.json")
# second blind round (2026-11-24): the rows still in manual review after the first merge
VERDICTS2 = os.path.join(RESULTS_DIR, "manual_review_remaining_final.jsonl")
KEY2 = os.path.join(RESULTS_DIR, "manual_review_remaining_key.json")
MAP = {"correct": S.CORRECT, "incorrect": S.WRONG, "refusal": S.REFUSAL,
       "partial_ancestor": PARTIAL, "overspecific": OVERSPEC}


def aggregate(outcomes: list) -> dict:
    """score.aggregate with the two manual-review labels (see module docstring)."""
    ins = [o for s, o in outcomes if s == "in"]
    oos = [o for s, o in outcomes if s == "out"]
    answered_in = [o for o in ins if o in (S.CORRECT, S.WRONG, S.MANUAL, PARTIAL, OVERSPEC)]
    mr = ins.count(S.MANUAL)
    corr_ans, corr_ref = ins.count(S.CORRECT), ins.count(S.REFUSAL_OK)
    wrong_in = ins.count(S.WRONG) + ins.count(OVERSPEC)
    oos_ans, oos_ref = oos.count(S.WRONG), oos.count(S.REFUSAL_OK)

    def rate(n, d):
        return (n / d) if d else None

    n_in = len(ins)
    scored_in, scored_ans = n_in - mr, len(answered_in) - mr
    hall_den = scored_ans + oos_ans
    return {
        "in_scope": n_in, "out_of_scope": len(oos),
        "answered": len(answered_in), "manual_review": mr,
        "correct": corr_ans + corr_ref, "correct_answers": corr_ans,
        "correct_refusals": corr_ref, "wrong_answers": wrong_in,
        "wrong_answers_incorrect": ins.count(S.WRONG), "overspecific": ins.count(OVERSPEC),
        "partial_ancestor": ins.count(PARTIAL),
        # no response: in scope, not correct, not answered, not manual review
        "no_response": ins.count(NO_RESPONSE),
        "oos_answered": oos_ans, "oos_refused": oos_ref,
        "unconditional_accuracy": rate(corr_ans + corr_ref, scored_in),
        "unconditional_accuracy_bounds": (rate(corr_ans + corr_ref, n_in),
                                          rate(corr_ans + corr_ref + mr, n_in)),
        "conditional_accuracy": rate(corr_ans, scored_ans),
        "conditional_accuracy_bounds": (rate(corr_ans, len(answered_in)),
                                        rate(corr_ans + mr, len(answered_in))),
        "oos_refusal_rate": rate(oos_ref, len(oos)),
        "hallucination_rate": rate(wrong_in + oos_ans, hall_den),
        "hallucination_rate_bounds": (rate(wrong_in + oos_ans, hall_den + mr),
                                      rate(wrong_in + oos_ans + mr, hall_den + mr)),
    }


def candidate_scores() -> dict:
    """query id -> (candidate c, truth, outcome) for biology run-4 parses."""
    parses = {r["id"]: r["parsed"] for r in json.load(open(
        results_path("biology", "deployed_parses.json"), encoding="utf-8"))["rows"]}
    qs = {q["id"]: q for q in json.load(open(DOMAINS["biology"]["queries"]["natural_language"],
                                              encoding="utf-8"))["queries"]}
    T = S.taxa()
    out = {}
    for qid, p in parses.items():
        c = ((p.get("bindings") or {}).get("candidate_taxon") or {}).get("value") \
            if isinstance(p, dict) else None
        truth = qs[qid].get("truth")
        if c is None or truth not in T.T or c not in T.T:
            out[qid] = (c, truth, S.WRONG)
            continue
        o, note = T.judge(c, truth)
        if o == S.MANUAL:
            o = PARTIAL if "ancestor" in note else OVERSPEC
        out[qid] = (c, truth, o)
    return out


def main() -> int:
    if not os.path.exists(AUTO):
        raise SystemExit(f"{AUTO} missing: copy score.py's results/scores.json there first")
    sc = json.load(open(AUTO, encoding="utf-8"))
    key = json.load(open(KEY, encoding="utf-8"))["key"]
    verdicts = [json.loads(l) for l in open(VERDICTS, encoding="utf-8") if l.strip()]
    used = [VERDICTS]
    if os.path.exists(VERDICTS2):
        key2 = json.load(open(KEY2, encoding="utf-8"))["key"]
        if set(key2) & set(key):
            raise SystemExit("review ids of the two rounds overlap")
        v2 = [json.loads(l) for l in open(VERDICTS2, encoding="utf-8") if l.strip()]
        if {v["review_id"] for v in v2} != set(key2):
            raise SystemExit(f"{VERDICTS2}: verdicts do not cover exactly the key's rows")
        key, verdicts, used = {**key, **key2}, verdicts + v2, used + [VERDICTS2]
    cand = candidate_scores()
    rr = json.load(open(RERUN, encoding="utf-8"))["rows"]
    no_resp = {(r["domain"], r["system"], r["id"]) for r in rr if r.get("no_response")}
    idx = {(d, c["system"], c["id"]): c for d in sc if isinstance(sc[d], dict)
           and "cases" in sc[d] for c in sc[d]["cases"]}
    report = {"applied": Counter(), "still_manual_after_rerun": [], "needs_candidate": [],
              "conflicts": []}
    for v in verdicts:
        k = key[v["review_id"]]
        c = idx[(k["domain"], k["system"], k["query_id"])]
        c["auto_outcome"] = c["outcome"]
        c["review_id"], c["manual_verdict"], c["manual_note"] = (
            v["review_id"], v["verdict"], v.get("note"))
        if v["verdict"] == "no_answer":
            if (k["domain"], k["system"], k["query_id"]) in no_resp:
                # user decision 2026-11-24: generation exceeded the 15-min client limit
                c["outcome"] = NO_RESPONSE
                report["applied"]["no_answer -> " + NO_RESPONSE] += 1
                continue
            if c["outcome"] == S.MANUAL:
                report["still_manual_after_rerun"].append(
                    {"domain": k["domain"], "system": k["system"], "id": k["query_id"],
                     "note": c["note"]})
            report["applied"]["no_answer -> re-scored " + c["outcome"]] += 1
            continue
        if c["outcome"] != S.MANUAL:
            report["conflicts"].append({**k, "locked_outcome": c["outcome"],
                                        "verdict": v["verdict"]})
            continue
        if v["verdict"] == "needs_candidate":
            cc, truth, o = cand[k["query_id"]]
            c["outcome"], c["candidate"] = o, cc
            report["needs_candidate"].append({"id": k["query_id"], "candidate": cc,
                                              "truth": truth, "outcome": o})
        else:
            c["outcome"] = MAP[v["verdict"]]
        report["applied"][f"{v['verdict']} -> {c['outcome']}"] += 1
    if report["conflicts"]:
        raise SystemExit(f"verdicts on rows the locked scorer no longer leaves to manual "
                         f"review: {report['conflicts']}")
    meta = {d: S.query_meta(d) for d in ("physics", "chemistry", "biology") if d in sc}
    for d in meta:
        per = {}
        for c in sc[d]["cases"]:
            per.setdefault(c["system"], []).append((meta[d][c["id"]]["scope"], c["outcome"]))
        sc[d]["systems"] = {s: aggregate(v) for s, v in per.items()}
    sc["_final_scoring"] = {
        "note": __doc__.split("\n\n")[0],
        "locked_auto_scores": os.path.relpath(AUTO, ROOT),
        "verdicts_files": [os.path.relpath(v, ROOT) for v in used],
        "applied": dict(report["applied"]),
        "still_manual_after_rerun": report["still_manual_after_rerun"],
        "needs_candidate": report["needs_candidate"]}
    json.dump(sc, open(os.path.join(RESULTS_DIR, "scores.json"), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    for k, n in sorted(report["applied"].items()):
        print(f"  {n:4d}  {k}")
    print("still manual after re-run:", report["still_manual_after_rerun"])
    for r in report["needs_candidate"]:
        print(f"  needs_candidate q{r['id']}: c = {r['candidate']}, truth {r['truth']} "
              f"-> {r['outcome']}")
    print("wrote results/scores.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
