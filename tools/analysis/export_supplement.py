"""Export the frozen test set and its stored outputs to supplement/ (2026-11-25).

Read-only on every stored run output; no model is called and no system is re-run. The one
computation is the PCT replay check (split_metrics.replay_domain): prove() re-run on each
stored answer's goal state, the same check that produced the hallucination numbers in
results/paper_numbers.json. Writes only supplement/.

  supplement/<domain>_queries.jsonl   one line per query (300 per domain)
  supplement/README.md                field definitions, counts, source hashes, check result
  supplement/rebuild_check.json       totals rebuilt from the JSONL vs paper_numbers.json

Then, separately:  python tools/analysis/export_supplement.py --check-only  re-reads the
JSONL files and repeats the comparison without touching the sources.

    python tools/analysis/export_supplement.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for p in (ROOT, _HERE, os.path.join(ROOT, "tools", "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)

from layout import DOMAINS, results_path  # noqa: E402
import split_metrics as SM  # noqa: E402

OUT = os.path.join(ROOT, "supplement")
DOMS = ("physics", "chemistry", "biology")
SYSTEMS = ("pct_deployed", "pct_oracle", "rag", "baseline")
READ: dict = {}

VERDICT = {"correct": "correct", "refusal_correct": "refusal", "refusal": "refusal",
           "wrong": "incorrect", "partial_ancestor": "less specific",
           "overspecific": "over-specific", "no_response": "no response"}
ANSWER_VERDICTS = ("correct", "incorrect", "less specific", "over-specific")


def rel(p):
    return os.path.relpath(p, ROOT).replace("\\", "/")


def load(p):
    r = rel(p)
    if r not in READ:
        READ[r] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return json.load(open(p, encoding="utf-8"))


def note_read(p):
    load(p) if p.endswith(".json") else READ.setdefault(
        rel(p), hashlib.sha256(open(p, "rb").read()).hexdigest())


# ----------------------------------------------------------------------------- sources
def query_rows(d):
    """id -> (category, category_source, query text, extra query metadata)."""
    out = {}
    if d == "physics":
        Q = load(DOMAINS["physics"]["queries"]["structured"])
        for i, q in enumerate(Q, 1):
            cat = {"shallow": "single", "medium": "multi", "oos": "oos"}[q["category"]]
            out[i] = {"category": cat, "category_source": q["category"], "query": q["query"],
                      "q": q}
        return out
    Q = load(DOMAINS[d]["queries"]["natural_language"])["queries"]
    for q in Q:
        out[q["id"]] = {"category": q["step_class"], "category_source": q["step_class"],
                        "query": q["nl"], "q": q}
    return out


def baseline_records(d):
    """id -> {"rag": rec, "baseline": rec} as stored (content, elapsed, flags)."""
    if d == "physics":
        rows = load(results_path("physics", "evaluation_300.json"))
        return {i: {s: r.get(s) or {} for s in ("rag", "baseline")} for i, r in enumerate(rows, 1)}
    rows = load(results_path(d, "baselines.json"))["rows"]
    return {r["id"]: {s: r.get(s) or {} for s in ("rag", "baseline")} for r in rows}


def extracted(note):
    if not note:
        return None
    m = re.match(r"read (.+)$", note)
    return m.group(1) if m else None


# ----------------------------------------------------------------------------- export
def build(d, scores, underspec, chem_unit):
    pipe = {r["id"]: r for r in load(results_path(d, "pipeline.json"))["rows"]}
    parses = {r["id"]: r["parsed"] for r in load(results_path(d, "deployed_parses.json"))["rows"]}
    qs = query_rows(d)
    texts = baseline_records(d)
    cases = {(c["system"], c["id"]): c for c in scores[d]["cases"]}
    gold_phys = {}
    if d == "physics":
        gold_phys = {r["query_id"]: r["gold_goal"]
                     for r in load(results_path("physics", "oracle_eval.json"))["results"]}
    us = {u["id"]: u["conditions"] for u in underspec["domains"][d]["underspecified_queries"]}

    replay = SM.replay_domain(d)
    rep = {(r["config"], r["id"]): r for r in replay}
    split = SM.split_domain(d, scores[d], replay)

    lines = []
    for qid in sorted(pipe):
        r, qm, q = pipe[qid], qs[qid], qs[qid]["q"]
        assert r["nl"] == qm["query"], (d, qid, "query text differs between files")
        scope = r["scope"]
        rec = {"id": qid, "domain": d, "category": qm["category"],
               "category_source": qm["category_source"], "scope": scope,
               "query": qm["query"],
               "underspecified": qid in us, "missing_conditions": us.get(qid, [])}
        # gold + expected
        if d == "physics":
            rec["gold_goal_state"] = gold_phys.get(qid)
            rec["expected"] = None if scope == "out" else {
                "value": q["expected_value"], "unit": q["expected_unit"]}
        elif d == "chemistry":
            g = q.get("gold") if scope == "in" else None
            rec["gold_goal_state"] = g or None
            rec["expected"] = None if scope == "out" else {
                "value": q["expected"], "unit": chem_unit[q["gold"]["goal"]]}
        else:
            has_gold = scope == "in" and q["part"] in ("vertebrate", "felid", "join")
            rec["gold_goal_state"] = {"form": q.get("gold_form"), "target": q.get("gold_target"),
                                      "traits": q.get("gold_traits"),
                                      "chars": q.get("gold_chars")} if has_gold else None
            rec["expected"] = None if scope == "out" else {
                "answer": q["expected"], "truth": q.get("truth"),
                "expected_classification": q.get("expected_classification"),
                "gold_organism": q.get("gold_organism")}
            form = q.get("gold_form")
            rec["query_type"] = {"what_is": "identification", "is_a": "membership"}.get(
                form, "other")
            rec["query_type_detail"] = {"part": q["part"], "gold_form": form}
            rec["pct_refusal_scored_correct"] = scope == "out" or (
                form == "is_a" and q.get("truth") == "no")
            rec["expected_refuse_in_query_set"] = q["expected"] == "refuse"
            if q.get("expect_refusal_by_design"):
                rec["refusal_by_design_note"] = q.get("why")

        # PCT deployed
        dep = {"parsed_goal_state": parses[qid], "answer": r["deployed"],
               "status": r["deployed_status"], "claim": r.get("deployed_claim")}
        if r["deployed"] == "refuse":
            f = r.get("deployed_failure") or {}
            t = r.get("deployed_type")
            sub = None
            if scope == "in" and r.get("stage") != "parser" and str(t or "").startswith("F5"):
                sub = "F5a" if r["oracle_status"] != "success" else "F5b"
            dep["failure"] = {"type": t, "stage": r.get("stage"), "F5_subtype": sub,
                              "reason": f.get("reason"),
                              "named_missing_inputs": [(f.get("goal") or {}).get("predicate")]
                              if (f.get("goal") or {}).get("predicate") else [],
                              "failure_goal": f.get("goal"), "failure_rule": f.get("rule")}
            dep["replay"] = None
        else:
            dep["failure"] = None
            x = rep[("deployed", qid)]
            dep["replay"] = {"passed": not x["problems"], "problems": x["problems"],
                             "replay_value": x.get("value"), "rules": x.get("rules")}
        rec["pct_deployed"] = dep

        # PCT oracle
        orc = {"answer": r["oracle"], "status": r["oracle_status"],
               "claim": r.get("oracle_claim")}
        if r["oracle"] == "refuse":
            f = r.get("oracle_failure") or {}
            orc["failure"] = {"type": r.get("oracle_type"), "reason": f.get("reason"),
                              "named_missing_inputs": [(f.get("goal") or {}).get("predicate")]
                              if (f.get("goal") or {}).get("predicate") else [],
                              "failure_goal": f.get("goal")}
            orc["replay"] = None
        else:
            orc["failure"] = None
            x = rep[("oracle", qid)]
            orc["replay"] = {"passed": not x["problems"], "problems": x["problems"],
                             "replay_value": x.get("value"), "rules": x.get("rules")}
        rec["pct_oracle"] = orc

        # RAG and raw
        for s in ("rag", "baseline"):
            st, c = texts[qid][s], cases[(s, qid)]
            assert (st.get("content") or "") == c["response"], (d, qid, s, "response differs")
            rec["raw" if s == "baseline" else "rag"] = {
                "response": st.get("content"),
                "extracted_answer": extracted(c.get("note")),
                "scorer_note": c.get("note"),
                "no_response_cause": st.get("no_response")
                if c["outcome"] == "no_response" else None,
                "transport_error_original": st.get("transport_error_original")}

        # verdicts
        v = {}
        for s in SYSTEMS:
            c = cases[(s, qid)]
            verdict = VERDICT[c["outcome"]]
            answered = verdict in ANSWER_VERDICTS
            ent = {"verdict": verdict,
                   "counts_as_correct": c["outcome"] in ("correct", "refusal_correct"),
                   "answered": answered,
                   "locked_scorer_outcome": c.get("auto_outcome") or c["outcome"],
                   "manual_verdict": c.get("manual_verdict"),
                   "manual_note": c.get("manual_note")}
            if s.startswith("pct"):
                sp = split[s]
                ent["hallucination"] = (qid in sp["hallucinations"]) if answered else None
                ent["faithfulness_error"] = (qid in sp["faithfulness_errors"]) if answered \
                    else None
                fk = sp["faithfulness_kinds"].get(qid)
                ent["faithfulness_error_kinds"] = fk["kinds"] if fk else None
            else:
                ent["hallucination"] = (verdict in ("incorrect", "over-specific")) \
                    if answered else None
                ent["faithfulness_error"] = None      # not applicable to RAG / raw
            v["raw" if s == "baseline" else s] = ent
        rec["verdicts"] = v
        lines.append(rec)
    return lines


# ----------------------------------------------------------------------------- rebuild
def rebuild(lines, d):
    """paper_numbers-shaped totals computed from the exported records only."""
    out = {}
    for s in ("pct_deployed", "pct_oracle", "rag", "raw"):
        src = "baseline" if s == "raw" else s
        ins = [x for x in lines if x["scope"] == "in"]
        oos = [x for x in lines if x["scope"] == "out"]
        V = lambda x: x["verdicts"][s]  # noqa: E731
        ans_in = [x for x in ins if V(x)["answered"]]
        ans_out = [x for x in oos if V(x)["answered"]]
        corr_a = sum(V(x)["verdict"] == "correct" for x in ins)
        corr_r = sum(V(x)["verdict"] == "refusal" and V(x)["counts_as_correct"] for x in ins)
        wrong = sum(V(x)["verdict"] in ("incorrect", "over-specific") for x in ins)
        hall = sum(bool(V(x)["hallucination"]) for x in ins + oos)
        den = len(ans_in) + len(ans_out)
        e = {"queries": {"in_scope": len(ins), "out_of_scope": len(oos)},
             "answered": {"in_scope": len(ans_in), "out_of_scope": len(ans_out), "total": den},
             "manual_review": 0,
             "correct": {"total": corr_a + corr_r, "answers": corr_a, "refusals": corr_r},
             "wrong_answers_in_scope": wrong,
             "partial_ancestor_in_scope": sum(V(x)["verdict"] == "less specific" for x in ins),
             "overspecific_in_scope": sum(V(x)["verdict"] == "over-specific" for x in ins),
             "unconditional_accuracy": SM._r(corr_a + corr_r, len(ins)),
             "conditional_accuracy": SM._r(corr_a, len(ans_in)),
             "out_of_scope_refusal_rate": SM._r(
                 sum(V(x)["counts_as_correct"] for x in oos), len(oos)),
             "hallucination_rate": SM._r(hall, den)}
        if s.startswith("pct"):
            cfg = s[4:]
            faith = [x for x in ins + oos if V(x)["faithfulness_error"]]
            e["faithfulness_error_rate"] = SM._r(len(faith), den)
            e["identity"] = {"hallucinations": hall, "faithfulness_errors": len(faith),
                             "correct_answers": corr_a,
                             "partial_ancestor": e["partial_ancestor_in_scope"],
                             "answered": den}
            ref = {}
            for x in lines:
                p = x[s]
                if p["answer"] != "refuse":
                    continue
                t = p["failure"]["type"] or "?"
                if cfg == "oracle" and t == "-":
                    stage = "no_gold_goal_state"
                elif cfg == "deployed" and p["failure"]["stage"] == "parser":
                    stage = "parse_stage"
                else:
                    stage = "engine_stage"
                ref.setdefault(x["scope"] + "_scope", {}).setdefault(stage, Counter())[t] += 1
            e["refused_by_type"] = {sc: {st: dict(sorted(c.items())) for st, c in v.items()}
                                    for sc, v in ref.items()}
            rp = [x[s]["replay"] for x in lines if x[s]["replay"] is not None]
            e["replay"] = {"answers_checked": len(rp),
                           "failures": sum(not r["passed"] for r in rp)}
            kinds = Counter()
            per = {}
            for x in faith:
                kinds.update(V(x)["faithfulness_error_kinds"])
                per[str(x["id"])] = V(x)["faithfulness_error_kinds"]
            e["faithfulness_error_kinds"] = {
                "counts_per_kind": dict(sorted(kinds.items())),
                "rows_with_more_than_one_kind": sorted(int(k) for k, v in per.items()
                                                       if len(v) > 1),
                "per_query": dict(sorted(per.items(), key=lambda kv: int(kv[0])))}
            if cfg == "deployed":
                f5 = [x for x in ins if x[s]["failure"] and x[s]["failure"]["F5_subtype"]]
                a = [x["id"] for x in f5 if x[s]["failure"]["F5_subtype"] == "F5a"]
                b = [x["id"] for x in f5 if x[s]["failure"]["F5_subtype"] == "F5b"]
                e["F5a_F5b"] = {"F5a": len(a), "F5b": len(b), "F5a_ids": a, "F5b_ids": b}
        else:
            e["faithfulness_error_rate"] = None
            e["refused_in_scope"] = len(ins) - len(ans_in)
        out[src] = e
    dep, orc = out["pct_deployed"], out["pct_oracle"]
    out["deployed_vs_oracle"] = {
        "answer_rate": {"deployed": SM._r(dep["answered"]["in_scope"], dep["queries"]["in_scope"]),
                        "oracle": SM._r(orc["answered"]["in_scope"], orc["queries"]["in_scope"])},
        "unconditional_accuracy": {"deployed": dep["unconditional_accuracy"],
                                   "oracle": orc["unconditional_accuracy"]}}
    return out


def compare(built, stored, path=""):
    """Every key in `built` must equal `stored`. -> (n leaves compared, mismatches)."""
    n, bad = 0, []
    if isinstance(built, dict):
        if not isinstance(stored, dict):
            return 1, [(path, built, stored)]
        for k, v in built.items():
            if k not in stored:
                bad.append((f"{path}.{k}", v, "<absent in paper_numbers.json>"))
                n += 1
                continue
            m, b = compare(v, stored[k], f"{path}.{k}")
            n, bad = n + m, bad + b
        return n, bad
    return 1, ([] if built == stored else [(path, built, stored)])


def not_rebuilt(built, stored, path=""):
    """Keys in paper_numbers.json that the rebuild does not reproduce."""
    out = []
    for k, v in stored.items():
        if k not in built:
            out.append(f"{path}.{k}")
        elif isinstance(v, dict) and isinstance(built[k], dict):
            out += not_rebuilt(built[k], v, f"{path}.{k}")
    return out


def check(lines_by_domain, paper):
    res = {}
    for d in DOMS:
        built = rebuild(lines_by_domain[d], d)
        stored = paper["domains"][d]
        n, bad = compare(built, stored, d)
        res[d] = {"leaves_compared": n, "mismatches": [list(map(str, b)) for b in bad],
                  "keys_not_rebuilt": not_rebuilt(built, stored, d)}
    return res


# ----------------------------------------------------------------------------- README
FIELDS = r"""
## Fields (one JSON object per line)

| field | meaning |
|---|---|
| `id` | query id within the domain (1-300) |
| `domain` | physics / chemistry / biology |
| `category` | `single` (one rule application), `multi` (a chain of rules), `oos` (out of scope). Chemistry and biology: the query set's `step_class`. Physics: the query set's `shallow` -> `single`, `medium` -> `multi` (the generator's design: single-rule vs 2-4 rule chains); the stored label is kept in `category_source`. |
| `scope` | `in` / `out` (out = `oos`) |
| `query` | the query text, verbatim as sent to every system |
| `underspecified` | true if the query is in the underspecified layer (results/underspecified.json, `underspecified_queries`): an in-scope query whose answer depends on a condition the query does not state |
| `missing_conditions` | the unstated condition predicate(s) for an underspecified query, else `[]` |
| `gold_goal_state` | the gold goal state the PCT oracle is run on. Physics: `gold_goal` of results/physics/oracle_eval.json (predicate, unit, context, bindings). Chemistry: the query set's `gold` (reaction, species roles, given values, goal predicate). Biology: gold form, target taxon, traits, characters. `null` for out-of-scope queries and for the 3 biology `name_only` queries (q33-q35: the query states no trait, so a goal state has no evidence) |
| `expected` | physics / chemistry: `{value, unit}`. Biology: `{answer, truth, expected_classification, gold_organism}`: `answer` is the query set's expected output (`yes`, a taxon, or `refuse`), `truth` the true answer (`yes`/`no` for is-a, the taxon for identification). `null` out of scope |
| `query_type` | biology only: `identification` ("What is it?", gold form `what_is`), `membership` ("Is it a t?", gold form `is_a`), `other` (out of scope). `query_type_detail` gives the stored `part` and `gold_form` |
| `pct_refusal_scored_correct` | biology only: true if the locked rubric scores a PCT typed failure on this query as a **correct** refusal: out-of-scope queries (rubric §4.4) and is-a queries whose truth is `no` (§4.3: the KB declares no sibling disjointness, so non-membership is not derivable and the typed failure is the right output) |
| `expected_refuse_in_query_set` | biology only: the query set's expected output is `refuse`. This includes q33-q35 (`refusal_by_design_note`), whose truth is `yes`/a taxon: the rubric scores PCT's refusal there as a refusal, **not** correct |
| `pct_deployed.parsed_goal_state` | the stored run-4 parser output (results/<d>/deployed_parses.json `parsed`): the goal state, or the parser's own typed failure (F4) with its reason and, where the model returned something, `raw` |
| `pct_deployed.answer` | the stored answer: a number, `yes`, a taxon, `derived: X`, or `refuse` |
| `pct_deployed.claim` | the stored claim (value/taxon, unit, rule ids, step count) |
| `pct_deployed.failure` | for a refusal: `type` (F1-F6), `stage` (`parser`, `resolution`, `engine`), `F5_subtype` (in-scope engine-stage F5 only: `F5a` = the gold goal state also fails, a KB gap; `F5b` = the gold goal state succeeds, parser-induced), `reason`, `named_missing_inputs` (the predicate the failure certificate names as underivable or cyclic), `failure_goal`, `failure_rule` |
| `pct_deployed.replay` | for an answer: the replay check (prove() re-run on the stored goal state; derivation checked step by step against the KB): `passed`, `problems`, `replay_value`, `rules`. `null` for a refusal |
| `pct_oracle` | the same for PCT run on the gold goal state: `answer`, `status`, `claim`, `failure` (`type` `-` = no gold goal state, the engine is not called), `replay` |
| `rag`, `raw` | the stored response text **verbatim** (`response`; raw = the unconstrained model), `extracted_answer` (the value / yes-no / taxon the locked scorer read, from its note `read X`; `null` when the scorer read nothing or the row went to manual review), `scorer_note` (the locked scorer's note), `no_response_cause` (only for "no response" rows), `transport_error_original` (rows re-sent after a transport error in the original run: that error; the response shown is the re-sent one, or the no-response record) |
| `verdicts.<system>` | per system (`pct_deployed`, `pct_oracle`, `rag`, `raw`): see below |

### `verdicts.<system>`

| field | meaning |
|---|---|
| `verdict` | `correct`, `incorrect`, `refusal`, `less specific` (biology: a true ancestor of the expected taxon), `over-specific` (biology: a descendant the query does not support), `no response` (generation exceeded 15 min or failed twice in transport: not correct, not answered) |
| `counts_as_correct` | true for `correct`, and for a `refusal` the rubric scores as correct (out of scope; biology negative is-a) |
| `answered` | true for `correct`, `incorrect`, `less specific`, `over-specific` (the conditional-accuracy and hallucination denominators) |
| `locked_scorer_outcome` | the locked scorer's (score.py) automatic outcome; `manual_review` where it deferred |
| `manual_verdict`, `manual_note` | the blind manual verdict that resolved a `manual_review` row (results/manual_review_*_final.jsonl via their key files), else `null` |
| `hallucination` | answered rows only (else `null`). PCT: the answer fails the replay check. RAG / raw (rubric §6): an in-scope answer verdict `incorrect` or `over-specific`, or any answered out-of-scope query |
| `faithfulness_error` | PCT only, answered rows: passes replay but is `incorrect`/`over-specific`/`less specific`, or answers an out-of-scope query. `null` for RAG / raw (not applicable) |
| `faithfulness_error_kinds` | PCT faithfulness errors: the parser-error kinds (dropped value, misassigned value, value bound that does not appear in the query, wrong predicate, ...); oracle: `gold goal state (not a parser error)` |
"""


def readme(lines_by_domain, chk, paper_hash):
    L = ["# PCT frozen test set: queries and stored outputs", "",
         "One JSON-lines file per domain, one line per query (300 each): the query, its gold "
         "goal state and expected answer, the stored output of all four systems (PCT deployed, "
         "PCT oracle, RAG, raw/unconstrained) and the final verdict of each. Exported read-only "
         "from the frozen test run of 2026-11-24 (run 4) after the final scoring pass, by "
         "tools/analysis/export_supplement.py. No model was called and no system re-run; the "
         "only computation is the PCT replay check (the engine re-proving each stored answer "
         "from its stored goal state), which is how the hallucination numbers were produced.",
         "", "Systems: **PCT deployed** = Mistral 7B parser -> backward-chaining engine; **PCT "
         "oracle** = the engine on the gold goal state; **RAG** = Mistral 7B with retrieved KB "
         "passages; **raw** = Mistral 7B unconstrained.", ""]
    L += [FIELDS.strip(), ""]
    L += ["## Counts per file", "",
          "| file | lines | in scope | out of scope | single | multi | underspecified |",
          "|---|---|---|---|---|---|---|"]
    for d in DOMS:
        X = lines_by_domain[d]
        c = Counter(x["category"] for x in X)
        L.append(f"| {d}_queries.jsonl | {len(X)} | {sum(x['scope'] == 'in' for x in X)} | "
                 f"{sum(x['scope'] == 'out' for x in X)} | {c['single']} | {c['multi']} | "
                 f"{sum(x['underspecified'] for x in X)} |")
    L += ["", "Verdicts per system (all 300 queries):", "",
          "| file | system | correct | incorrect | refusal (correct) | refusal (not correct) "
          "| less specific | over-specific | no response | hallucinations | faithfulness errors |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for d in DOMS:
        for s in ("pct_deployed", "pct_oracle", "rag", "raw"):
            V = [x["verdicts"][s] for x in lines_by_domain[d]]
            c = Counter(v["verdict"] for v in V)
            rc = sum(v["verdict"] == "refusal" and v["counts_as_correct"] for v in V)
            fe = sum(bool(v["faithfulness_error"]) for v in V)
            L.append(f"| {d} | {s} | {c['correct']} | {c['incorrect']} | {rc} | "
                     f"{c['refusal'] - rc} | {c['less specific']} | {c['over-specific']} | "
                     f"{c['no response']} | {sum(bool(v['hallucination']) for v in V)} | "
                     f"{fe if s.startswith('pct') else 'n/a'} |")
    L += ["", "## Check against results/paper_numbers.json", "",
          f"Every per-domain total in results/paper_numbers.json (sha256 `{paper_hash}`) that "
          "the per-query fields determine was rebuilt from these JSONL files alone and "
          "compared for exact equality: query and answered counts, correct answers and "
          "refusals, wrong / less specific / over-specific, unconditional and conditional "
          "accuracy, out-of-scope refusal rate, hallucination and faithfulness-error rates (n, "
          "d, percent and Wilson 95% interval), the PCT identity, refusals by failure type and "
          "stage, replay counts, faithfulness-error kinds per query, and the F5a/F5b split "
          "with ids.", "",
          "| domain | values compared | mismatches |", "|---|---|---|"]
    for d in DOMS:
        L.append(f"| {d} | {chk[d]['leaves_compared']} | {len(chk[d]['mismatches'])} |")
    mism = [m for d in DOMS for m in chk[d]["mismatches"]]
    if mism:
        L += ["", "Mismatches (path, rebuilt, paper_numbers.json):", ""]
        L += [f"- `{m[0]}`: {m[1]} vs {m[2]}" for m in mism]
    nr = sorted({re.sub(r"^\w+\.", "", k) for d in DOMS for k in chk[d]["keys_not_rebuilt"]})
    L += ["", "Not rebuilt (not determined by the exported fields): "
          + ", ".join(f"`{k}`" for k in nr) + ". Full result: rebuild_check.json.", ""]
    L += ["## SHA-256 of every source file read", "", "| file | sha256 |", "|---|---|"]
    L += [f"| {k} | `{v}` |" for k, v in sorted(READ.items())]
    return "\n".join(L) + "\n"


def main() -> int:
    check_only = "--check-only" in sys.argv
    paper_p = os.path.join(ROOT, "results", "paper_numbers.json")
    if check_only:
        lines = {d: [json.loads(s) for s in open(os.path.join(OUT, f"{d}_queries.jsonl"),
                                                  encoding="utf-8")] for d in DOMS}
        chk = check(lines, json.load(open(paper_p, encoding="utf-8")))
        print(json.dumps({d: {"compared": chk[d]["leaves_compared"],
                              "mismatches": chk[d]["mismatches"]} for d in DOMS}, indent=1))
        return 1 if any(chk[d]["mismatches"] for d in DOMS) else 0

    scores = load(os.path.join(ROOT, "results", "scores.json"))
    underspec = load(os.path.join(ROOT, "results", "underspecified.json"))
    paper = load(paper_p)
    import eval_chemistry as RS
    for d in DOMS:          # the engine and KBs the replay reads
        load(DOMAINS[d]["kb1"])
    for f in ("pct/engine.py", "tools/analysis/score.py", "tools/analysis/split_metrics.py",
              "docs/scoring_rubric.md", "tools/eval/eval_chemistry.py",
              "tools/eval/eval_biology.py", "tools/eval/eval_oracle_physics.py"):
        note_read(os.path.join(ROOT, f))
    lines = {d: build(d, scores, underspec, RS.UNIT) for d in DOMS}
    chk = check(lines, paper)
    os.makedirs(OUT, exist_ok=True)
    for d in DOMS:
        with open(os.path.join(OUT, f"{d}_queries.jsonl"), "w", encoding="utf-8",
                  newline="\n") as fh:
            for x in lines[d]:
                fh.write(json.dumps(x, ensure_ascii=False, default=str) + "\n")
    with open(os.path.join(OUT, "rebuild_check.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(chk, fh, indent=1)
    with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(readme(lines, chk, READ["results/paper_numbers.json"]))
    for d in DOMS:
        print(f"{d}: {len(lines[d])} lines; compared {chk[d]['leaves_compared']} values; "
              f"mismatches {len(chk[d]['mismatches'])}; not rebuilt "
              f"{len(chk[d]['keys_not_rebuilt'])}")
        for m in chk[d]["mismatches"][:20]:
            print("   ", m)
    return 1 if any(chk[d]["mismatches"] for d in DOMS) else 0


if __name__ == "__main__":
    sys.exit(main())
