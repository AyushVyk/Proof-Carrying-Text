"""Additional scoring layer: UNDERSPECIFIED in-scope queries (2026-11-24).

This layer sits beside the locked rubric (docs/scoring_rubric.md, tools/analysis/score.py),
which it neither edits nor replaces: it imports score.py's own refusal detector, numeric
reading rule and aggregation, and reads the locked per-row outcomes from results/scores.json.

1. DEFINITION (mechanical). An in-scope query is underspecified if the rule chain its gold
   answer was generated from consumes a CONDITION, and the query text has no stating phrase
   for it in the KB2 phrase lists (kb2_<d>.json `condition_phrases`, case-insensitive regex;
   for collision_type any of its values' phrases).
     physics    the chain's conditions: tools/build/build_physics_gold_conditions.needs()
                (the generator rule / template -> condition map), plus every net_* input of
                the gold rule (the NET QUALIFIER wording rule).
     chemistry  the chain's conditions as the generator recorded them (query `conditions`
                keys). other_reactants is not required when the KB supplies it (a reaction
                with a single reactant: eval_chemistry.resolve).
     biology    KB2 declares no conditions: no query is underspecified.
   The list is compared with the oracle's unstated-condition F5 set (in-scope oracle F5 whose
   failure names a condition predicate); differences are reported, not resolved.

2. EXPECTED OUTPUT on an underspecified query: a refusal.
     PCT (deployed, oracle)  correct = refuses and a missing condition appears anywhere in
                             the failure: the stored failure, or the engine's failure
                             re-proved from the stored goal state, including its candidates
                             list at any depth (2026-11-24, item 2a; the rejected raw parse
                             is not searched). A refusal naming something else is counted
                             separately ("refusal, other cause"). An answer is wrong; it is
                             a hallucination only if its replay fails, otherwise an answered
                             faithfulness error (the PCT definition; item 2d).
     (2026-11-24) force_displacement_angle is a condition (item 2b); the work rule
     and the power template P = F d / t consume it. Its phrase list lives in this
     layer's own config, tools/analysis/underspecified_config.json (2026-11-25), not in
     KB2: it affects this analysis only, never the parser.
     A block row this layer leaves to manual review is resolved by the final manual
     verdict on that row: refusal -> correct refusal, any other verdict -> an answer.
     RAG / raw               a committed numeric answer (score.read_numeric finds a value in
                             the expected unit) is wrong and a hallucination; otherwise a
                             refusal (score.is_refusal_text) or an explicit statement that a
                             value is missing (MISSING below) is correct; anything else goes
                             to manual review.

3. TABLE. Per domain: the locked-rubric rows (unchanged, all in-scope queries), the same rows
   recomputed over the ANSWERABLE in-scope queries only (in-scope minus underspecified; the
   locked per-row outcomes), and the underspecified block; Wilson 95% intervals.

4. Every RAG / raw committed answer on an underspecified query, with the value it assumed
   where it can be read from the response: stated in words (ASSUMPTIONS) or implied by the
   formula the response applies (IMPLIED), reported separately; and whether it uses g = 9.8.

    python tools/analysis/underspecified.py
    -> results/underspecified.json, results/underspecified_table.md
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for p in (ROOT, _HERE, os.path.join(ROOT, "tools", "eval"), os.path.join(ROOT, "tools", "build")):
    if p not in sys.path:
        sys.path.insert(0, p)

from layout import DOMAINS, QUERY_SET, RESULTS_DIR, results_path  # noqa: E402
import score as S  # noqa: E402  (locked scorer: imported, not modified)
import split_metrics as SM  # noqa: E402

DOMS = ("physics", "chemistry", "biology")
SYSTEMS = (("pct_deployed", "PCT dep."), ("pct_oracle", "PCT oracle"), ("rag", "RAG"),
           ("baseline", "Raw"))

MISSING = re.compile(
    r"\bnot (?:been )?(?:given|provided|specified|stated|mentioned|known|indicated)"
    r"|\bis(?:n't| not) (?:given|provided|specified|stated|mentioned|known)"
    r"|\bmissing\b|\bunknown\b|\bunspecified\b|\bneed(?:s|ed)? to know\b|\bwould need\b"
    r"|\bwithout know(?:ing)?\b|\bdepends on\b|\binsufficient\b|\bnot enough\b"
    r"|\bcannot be (?:determined|calculated|computed)|\bcan't be (?:determined|calculated)"
    r"|\bcan(?:not|'t) (?:determine|calculate|compute)\b", re.I)

# condition -> [(label, regex)] for reading an assumed value from a response
ASSUMPTIONS = {
    "initial_velocity": [("initial velocity 0 / from rest",
                          r"from rest|at rest|initial (?:velocity|speed)[^.]{0,40}?\b0\b"
                          r"|\b[uv]_?0?\s*=\s*0\b|v_?i\s*=\s*0|starts? (?:from|at) (?:zero|0)")],
    "pendulum_amplitude": [("small-angle approximation", r"small[- ]angle|small amplitude"
                                                         r"|sin\s*\(?θ\)?\s*≈\s*θ")],
    "nonconservative_work": [("no friction / air resistance; energy conserved",
                              r"neglect\w*|ignor\w*|no (?:friction|air resistance|energy loss)"
                              r"|frictionless|energy is conserved|conservation of (?:mechanical )?energy")],
    "friction_force": [("frictionless", r"frictionless|no friction|neglect\w* friction"
                                        r"|ignor\w* friction")],
    "applied_force_angle": [("force horizontal", r"horizontal|parallel|angle (?:of )?0")],
    "incline_angle": [("flat surface", r"\bflat\b|\blevel\b|horizontal")],
    "torque_angle": [("perpendicular", r"perpendicular|90\s*(?:°|deg)|sin\s*\(?90")],
    "acceleration": [("no acceleration", r"at rest|equilibrium|constant velocity|a\s*=\s*0")],
    "collision_type": [("elastic", r"(?<!in)elastic"), ("perfectly inelastic",
                                                        r"perfectly inelastic|stick together")],
    "net_force": [("force taken as net", r"net force|only force|resultant")],
    "net_work": [("work taken as net", r"net work|total work|work-energy")],
    "net_impulse": [("impulse taken as net", r"net impulse|impulse[- ]momentum")],
    "other_reactants": [("other reactant in excess / complete reaction",
                         r"\bexcess\b|sufficient|enough|limiting|react\w* completely"
                         r"|complete(?:ly)? react|fully react|100\s*%")],
    "gas_conditions": [("STP", r"\bSTP\b|standard temperature|22\.4|0\s*°C|273(?:\.15)?\s*K"),
                       ("room conditions / other", r"24\.\d\s*L|25\s*°C|298(?:\.15)?\s*K"
                                                  r"|ideal gas law|PV\s*=\s*nRT")],
}
G_ASSUMED = r"9\.8\d?\s*(?:m/s|m s)"

# condition -> [(label, regex)]: the assumption read from the FORMULA the response uses
# (reported separately from an assumption the response states in words)
_X = r"\s*(?:\\?\*|×|·)?\s*"          # optional multiplication sign (*, \*, ×, ·)
_G = r"(?:g|a|acceleration)"
_T = r"(?:t|time)"
IMPLIED = {
    "initial_velocity": [("initial velocity 0 (formula v = g t / d = 1/2 g t^2)",
                          rf"\bv\s*=\s*{_G}{_X}{_T}\b"
                          rf"|\(?1/2\)?{_X}{_G}{_X}{_T}\s*(?:\^|\*\*)\s*2"
                          rf"|½{_X}{_G}{_X}{_T}|0\.5{_X}{_G}{_X}{_T}")],
    "pendulum_amplitude": [("small-angle period (T = 2 pi sqrt(L/g))",
                            rf"2{_X}(?:π|pi){_X}(?:√|sqrt)\s*\(?\s*L\s*/\s*g")],
    "nonconservative_work": [("no nonconservative work (v = sqrt(v0^2 + 2 g (h0 - h)))",
                              rf"sqrt\s*\(\s*v_?0?\s*(?:\^|\*\*)\s*2\s*\+\s*2{_X}g"
                              r"|1/2\s*m\s*v\^?2\s*\+\s*m\s*g\s*h|mgh\s*\+\s*(?:1/2|½)\s*mv")],
    "net_force": [("stated force used as F_net / in F = m a",
                   rf"F_?net\s*=\s*\d|\ba\s*=\s*F\s*/\s*m|\bF\s*=\s*m{_X}a\b")],
    "net_impulse": [("stated impulse used as net (J = m dv)",
                     rf"\bJ\s*=\s*m{_X}(?:Δ|delta|\()|impulse\s*=\s*(?:change in momentum|m\b)"
                     rf"|\(\s*(?:p|J|I)\s*\+\s*m{_X}v")],
    "net_work": [("stated work used as net (W = dKE)", r"W\s*=\s*(?:Δ|delta)\s*K|work[- ]energy")],
    "applied_force_angle": [("force taken along the motion (F - f)",
                             r"F_?net\s*=\s*F\s*-\s*f|F\s*-\s*(?:μ|mu|f_k|friction)"
                             r"|net force\s*=\s*applied force\s*-\s*friction")],
    "gas_conditions": [("molar volume 22.4 L/mol (STP)", r"22\.4")],
}


def load(p):
    return json.load(open(p, encoding="utf-8"))


CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "underspecified_config.json")


def kb2_phrases(domain: str) -> dict:
    """KB2 condition_phrases plus this layer's own lists (underspecified_config.json:
    analysis only, the parser never reads them; a KB2 list is never overridden)."""
    cp = dict(load(DOMAINS[domain]["kb2"]).get("condition_phrases") or {})
    for b, v in (load(CONFIG)["condition_phrases"].get(domain) or {}).items():
        if b in cp:
            raise SystemExit(f"{b}: in both KB2 and {CONFIG}")
        cp[b] = v
    return {b: [rx for e in v for rx in e["phrases"]] for b, v in cp.items()}


def stated(text: str, rxs: list) -> bool:
    return any(re.search(rx, text or "", re.I) for rx in rxs)


# ----------------------------------------------------------------------------- 1. lists
WORK_RULES = {"work_from_force_displacement"}
WORK_TEMPLATES = {("power", "energy", ("F", "d", "t"))}


def _template(q: dict):
    return (q.get("expected_predicate"), q.get("expected_context"),
            tuple(sorted(q.get("params") or {})))


def physics_list() -> dict:
    import build_physics_gold_conditions as G
    import eval_oracle_physics as EO
    phr = kb2_phrases("physics")
    rules = EO._kb_rules_by_id(EO.load_kb(DOMAINS["physics"]["kb1"]))
    qs = load(DOMAINS["physics"]["queries"]["structured"])
    out = {}
    for i, q in enumerate(qs, 1):
        if q["category"] == "oos":
            continue
        need = list(G.needs(q))
        raw, _ = EO._build_param_bindings(q, rules)
        need += [n for n in EO.NET_WORDING if raw and n in raw]
        # 2026-11-24 (item 2b): the work rule consumes force_displacement_angle; the
        # generator chains through it for its own rule id and for the power template
        # P = F d / t (power <- work <- force, displacement, angle)
        if q.get("rule_id") in WORK_RULES or _template(q) in WORK_TEMPLATES:
            need.append("force_displacement_angle")
        miss = [c for c in need if not stated(q["query"], phr[c])]
        if miss:
            out[i] = {"nl": q["query"], "conditions": miss}
    return out


def chemistry_list() -> dict:
    import eval_chemistry as RS
    phr = kb2_phrases("chemistry")
    out, kb_supplied = {}, []
    for q in load(DOMAINS["chemistry"]["queries"]["natural_language"])["queries"]:
        if q["part"] == "out_of_scope":
            continue
        g = RS.gold_goal(q)
        rx = RS.KB["reactions"].get(g.get("reaction")) if g and g.get("reaction") else None
        miss = []
        for c in (q.get("conditions") or {}):
            if c == "other_reactants" and rx is not None and len(rx["reactants"]) == 1:
                kb_supplied.append(q["id"])
                continue
            if not stated(q["nl"], phr[c]):
                miss.append(c)
        if miss:
            out[q["id"]] = {"nl": q["nl"], "conditions": miss}
    return out, kb_supplied


def oracle_condition_f5(domain: str) -> dict:
    conds = set(kb2_phrases(domain))
    out = {}
    for r in load(results_path(domain, "pipeline.json"))["rows"]:
        if r["scope"] != "in" or r.get("oracle_status") == "success":
            continue
        f = r.get("oracle_failure") or {}
        p = (f.get("goal") or {}).get("predicate")
        if str(f.get("type") or r.get("oracle_type") or "").startswith("F5") and p in conds:
            out[r["id"]] = p
    return out


# ----------------------------------------------------------------------------- 2. scoring
def _strings(x):
    """Every string (keys and values) in a nested structure."""
    if isinstance(x, dict):
        for k, v in x.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _strings(v)
    elif x is not None:
        yield str(x)


def mentions(x, conds: list) -> list:
    return sorted({c for s in _strings(x) for c in conds
                   if re.search(rf"(?<![a-z0-9_]){re.escape(c)}(?![a-z0-9_])", s)})


def engine_failure(ad, row: dict, cfg: str):
    """The engine's full failure for a refused row (candidates included), re-proved from
    the stored goal state; None when the refusal never reached the engine."""
    from pct.engine import prove
    if cfg == "deployed" and row.get("stage") in ("parser", "resolution"):
        return None
    try:
        st = ad.goal_state(row, cfg)
    except Exception:        # noqa: BLE001 -- no goal state can be rebuilt: never reached
        return None          # the engine (e.g. eval_chemistry.Unresolved)
    if st is None:
        return None
    goal, bnd, d_max, _prov = st
    res = prove(goal, bnd, ad.kb, d_max=d_max)
    return {k: v for k, v in res.items() if k not in ("trace", "bindings")}


def score_pct_under(row: dict, cfg: str, conds: list, ad, replay_bad: set):
    """(outcome, where the condition was found). 2026-11-24: a refusal is correct when a
    missing condition appears anywhere in the failure -- the stored failure (type,
    reason, goal) or the engine's re-proved failure including its candidates list at any
    depth. The rejected raw parse is the model's output, not the failure's
    explanation, and is not searched (counted separately as `only_in_raw_parse`).
    An answer is a hallucination only if its replay fails (the PCT definition);
    otherwise an answered faithfulness error."""
    if row[cfg] != "refuse":
        return ("answer_hallucination" if row["id"] in replay_bad
                else "answer_faithfulness_error"), None
    f = {k: v for k, v in (row.get(f"{cfg}_failure") or {}).items() if k != "raw"}
    hit = mentions(f, conds)
    where = "stored failure" if hit else None
    if not hit:
        ef = engine_failure(ad, row, cfg)
        hit = mentions(ef, conds) if ef else []
        where = "engine failure / candidates" if hit else None
    if hit:
        return "refusal_correct", where
    raw = (row.get(f"{cfg}_failure") or {}).get("raw")
    return ("refusal_other", "only_in_raw_parse" if mentions(raw, conds) else None)


def score_text_under(text: str, unit: str, nl: str):
    kind, v = S.read_numeric(text, unit, nl)
    if kind == "value":
        return "answer", v
    if S.is_refusal_text(text) or MISSING.search(text or ""):
        return "refusal_correct", None
    return "manual_review", v


def assumed(text: str, conds: list, table: dict | None = None) -> list:
    found = []
    for c in conds:
        for label, rx in (table or ASSUMPTIONS).get(c, []):
            m = re.search(rx, text or "", re.I)
            if m:
                # the sentence around the match (a full stop between digits is a decimal)
                ends = [x.start() for x in re.finditer(r"(?<!\d)\.(?!\d)|\.(?=\s)|\n", text)]
                s = max([x + 1 for x in ends if x < m.start()], default=0)
                e = min([x for x in ends if x >= m.end()], default=len(text))
                found.append({"condition": c, "assumed": label,
                              "evidence": " ".join(text[s:e].split())[:220]})
                break
    return found


# ----------------------------------------------------------------------------- 3. table
def rate(n, d):
    ci = SM.wilson(n, d)
    if not d:
        return {"n": n, "d": d, "pct": None, "ci": None}
    return {"n": n, "d": d, "pct": round(100 * n / d, 1),
            "ci": [round(100 * ci[0], 1), round(100 * ci[1], 1)]}


def fmt(r):
    if not r or r["d"] == 0:
        return "-"
    return f"{r['pct']:.1f}% ({r['n']}/{r['d']}) (CI {r['ci'][0]:.1f}-{r['ci'][1]:.1f})"


def answerable_metrics(cases: list, under: set, replay_bad: set, pct: bool) -> dict:
    """The table's rows over in-scope minus underspecified (+ all out-of-scope), from
    the per-row outcomes in results/scores.json (manual verdicts merged), aggregated as
    final_scoring.aggregate does."""
    import final_scoring as FS
    keep = [(c["scope"], c["outcome"], c["id"]) for c in cases
            if not (c["scope"] == "in" and c["id"] in under)]
    a = FS.aggregate([(s, o) for s, o, _ in keep])
    mr = a["manual_review"]
    n_in = a["in_scope"]
    out = {"in_scope": n_in, "answered": a["answered"], "manual_review": mr,
           "correct": a["correct"], "correct_refusals": a["correct_refusals"],
           "partial_ancestor": a["partial_ancestor"], "overspecific": a["overspecific"],
           "no_response": a["no_response"],
           "unconditional_accuracy": rate(a["correct"], n_in - mr),
           "conditional_accuracy": rate(a["correct_answers"], a["answered"] - mr),
           "oos_refusal_rate": rate(a["oos_refused"], a["out_of_scope"])}
    if pct:
        answered = [(s, o, i) for s, o, i in keep
                    if (s == "in" and o in (S.CORRECT, S.WRONG, FS.PARTIAL, FS.OVERSPEC))
                    or (s == "out" and o == S.WRONG)]
        den = len(answered)
        bad = sum(1 for _s, _o, i in answered if i in replay_bad)
        faith = sum(1 for _s, o, i in answered
                    if i not in replay_bad and o in (S.WRONG, FS.OVERSPEC))
        out["hallucination_rate"] = rate(bad, den)
        out["faithfulness_error_rate"] = rate(faith, den)
    else:
        den = a["answered"] - mr + a["oos_answered"]
        out["hallucination_rate"] = rate(a["wrong_answers"] + a["oos_answered"], den)
    return out


def main() -> int:
    if QUERY_SET != "test":
        raise SystemExit("underspecified.py scores the test-set results (PCT_QUERY_SET unset)")
    scores = load(os.path.join(RESULTS_DIR, "scores.json"))
    phys = physics_list()
    chem, chem_kb = chemistry_list()
    lists = {"physics": phys, "chemistry": chem, "biology": {}}
    rep = {"_note": __doc__.split("\n\n")[0], "domains": {}}
    for d in DOMS:
        U = lists[d]
        O = oracle_condition_f5(d)
        pipe = {r["id"]: r for r in load(results_path(d, "pipeline.json"))["rows"]}
        meta = S.query_meta(d)
        texts = S.baseline_texts(d) or {}
        # comparison with the oracle
        all_oracle_f5 = {i: ((r.get("oracle_failure") or {}).get("goal") or {}).get("predicate")
                         for i, r in pipe.items() if r["scope"] == "in"
                         and r.get("oracle_status") != "success"}
        cmp = {"underspecified": len(U), "oracle_condition_F5": len(O),
               "equal": set(U) == set(O),
               "in_list_not_oracle_condition_F5": [
                   {"id": i, "conditions": U[i]["conditions"],
                    "oracle": pipe[i].get("oracle") if pipe[i].get("oracle") != "refuse"
                    else f"refuse {pipe[i].get('oracle_type')} on "
                         f"{all_oracle_f5.get(i)}"} for i in sorted(set(U) - set(O))],
               "oracle_condition_F5_not_in_list": [
                   {"id": i, "oracle_names": O[i], "nl": pipe[i]["nl"][:140]}
                   for i in sorted(set(O) - set(U))],
               "other_oracle_in_scope_refusals_not_in_list": dict(Counter(
                   f"{pipe[i].get('oracle_type')} on {p}" for i, p in all_oracle_f5.items()
                   if i not in U and i not in O))}
        if d == "chemistry":
            cmp["other_reactants_supplied_by_KB_single_reactant"] = sorted(set(chem_kb))
        # scoring the underspecified block
        replays = SM.replay_domain(d)
        bad = {(r["config"], r["id"]) for r in replays if r["problems"]}
        ad = SM.ADAPTERS[d]()
        case = {(c["system"], c["id"]): c for c in scores[d]["cases"]}
        block, per_row, baseline_answers, found_where = {}, [], [], Counter()
        verdict_used = []
        for sysname, _lab in SYSTEMS:
            c = Counter()
            for i in sorted(U):
                conds = U[i]["conditions"]
                if sysname.startswith("pct"):
                    cfg = sysname.split("_")[1]
                    out, where = score_pct_under(pipe[i], cfg, conds, ad,
                                                 {q for (k, q) in bad if k == cfg})
                    if where:
                        found_where[(sysname, where)] += 1
                    if out == "refusal_other":
                        per_row.append({"system": sysname, "id": i, "conditions": conds,
                                        "failure": pipe[i].get(f"{cfg}_failure"),
                                        "condition_only_in_raw_parse": where
                                        == "only_in_raw_parse"})
                else:
                    txt = (texts.get(i) or {}).get(sysname, "")
                    out, v = score_text_under(txt, meta[i]["expected_unit"], meta[i]["nl"])
                    mv = case[(sysname, i)].get("manual_verdict")
                    if case[(sysname, i)]["outcome"] == "no_response":
                        # generation exceeded 15 min on re-run: not a refusal, not an
                        # answer, not manual review; counts in n (not correct)
                        out = "no_response"
                    elif out == "manual_review" and mv in ("refusal", "correct", "incorrect",
                                                         "partial_ancestor", "overspecific"):
                        # the reviewer's reading of the response: a refusal, or a
                        # committed answer (whatever its value)
                        out = "refusal_correct" if mv == "refusal" else "answer"
                        verdict_used.append({"system": sysname, "id": i, "verdict": mv,
                                             "block_outcome": out})
                    if out == "answer":
                        baseline_answers.append({
                            "system": sysname, "id": i, "query": meta[i]["nl"],
                            "conditions": conds, "value_given": v,
                            "unit": meta[i]["expected_unit"],
                            "gold_expected_value": meta[i]["truth"],
                            "also_says_value_missing": bool(MISSING.search(txt)
                                                            or S.is_refusal_text(txt)),
                            "assumed": assumed(txt, conds),
                            "implied_by_formula": assumed(txt, conds, IMPLIED),
                            "assumes_g": bool(re.search(G_ASSUMED, txt)),
                            "manual_verdict": mv,
                            "response": txt})
                c[out] += 1
            n = len(U)
            mr = c["manual_review"]
            answers = c["answer"] + c["answer_hallucination"] + c["answer_faithfulness_error"]
            # RAG / raw: every committed answer is a hallucination (item 2);
            # PCT: only an answer failing replay (item 2d)
            hall = c["answer"] + c["answer_hallucination"]
            block[sysname] = {
                "n": n, "correct_refusals": c["refusal_correct"], "answers": answers,
                "refusals_other_cause": c["refusal_other"], "manual_review": mr,
                "no_response": c["no_response"],
                "correct_rate": rate(c["refusal_correct"], n - mr),
                "correct_rate_bounds": [rate(c["refusal_correct"], n),
                                        rate(c["refusal_correct"] + mr, n)] if mr else None,
                "hallucinations": hall,
                "answered_faithfulness_errors": c["answer_faithfulness_error"],
                "hallucination_rate": rate(hall, n - mr)}
        # answerable-only recompute from the per-row outcomes (manual verdicts merged)
        cases = scores[d]["cases"]
        scope = {i: m["scope"] for i, m in meta.items()}
        ans = {}
        for sysname, _lab in SYSTEMS:
            cs = [{**c, "scope": scope[c["id"]]} for c in cases if c["system"] == sysname]
            cfg = sysname.split("_")[1] if sysname.startswith("pct") else None
            ans[sysname] = answerable_metrics(
                cs, set(U), {i for (k, i) in bad if k == cfg}, sysname.startswith("pct"))
        rep["domains"][d] = {
            "underspecified_queries": [{"id": i, **U[i]} for i in sorted(U)],
            "by_condition": dict(Counter(c for v in U.values() for c in v["conditions"])),
            "comparison_with_oracle": cmp, "underspecified_block": block,
            "pct_refusals_naming_another_cause": per_row,
            "pct_condition_found_in": {f"{s} {w}": n for (s, w), n in found_where.items()},
            "block_manual_review_resolved_by_verdict": verdict_used,
            "baseline_answers_on_underspecified": baseline_answers,
            "answerable_only": ans,
            "locked_rubric": {s: scores[d]["systems"][s] for s, _ in SYSTEMS}}
    path = os.path.join(RESULTS_DIR, "underspecified.json")
    json.dump(rep, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False, default=str)
    write_table(rep)
    print("wrote", path)
    return 0


def write_table(rep: dict) -> None:
    md = ["# Underspecified in-scope queries: additional scoring layer",
          "",
          "Beside, not replacing, the locked rubric (docs/scoring_rubric.md, score.py). "
          "Definition and rules: tools/analysis/underspecified.py. (CI a-b) = Wilson 95%.",
          ""]
    for d, v in rep["domains"].items():
        L = v["locked_rubric"]
        A = v["answerable_only"]
        B = v["underspecified_block"]
        md += [f"## {d}", "",
               f"Underspecified in-scope queries: {len(v['underspecified_queries'])} "
               f"{v['by_condition'] or ''}; answerable in-scope: "
               f"{A['pct_deployed']['in_scope']}.", "",
               "| Metric | " + " | ".join(lab for _, lab in SYSTEMS) + " |",
               "|---|" + "---|" * len(SYSTEMS)]

        def row(label, f):
            md.append(f"| {label} | " + " | ".join(f(s) for s, _ in SYSTEMS) + " |")

        def lk(key, s):
            x = L[s].get(key)
            return "-" if x is None else f"{100 * x:.1f}%"
        md.append("| **Locked rubric, all in-scope** (score.py + manual verdicts) | | | | |")
        row("answered (in scope)", lambda s: str(L[s]["answered"]))
        row("correct (in scope)", lambda s: str(L[s]["correct"]))
        row("unconditional accuracy", lambda s: lk("unconditional_accuracy", s))
        row("conditional accuracy", lambda s: lk("conditional_accuracy", s))
        md.append("| **Answerable in-scope only** (locked per-row outcomes) | | | | |")
        row("in-scope queries", lambda s: str(A[s]["in_scope"]))
        row("answered (in scope)", lambda s: str(A[s]["answered"]))
        row("less specific / over-specific", lambda s: f"{A[s]['partial_ancestor']} / "
            f"{A[s]['overspecific']}")
        row("manual review", lambda s: str(A[s]["manual_review"]))
        row("no response (generation exceeded 15 min; not correct, not answered)",
            lambda s: str(A[s]["no_response"]))
        row("correct (in scope)", lambda s: str(A[s]["correct"]))
        row("unconditional accuracy", lambda s: fmt(A[s]["unconditional_accuracy"]))
        row("conditional accuracy", lambda s: fmt(A[s]["conditional_accuracy"]))
        row("out-of-scope refusal rate", lambda s: fmt(A[s]["oos_refusal_rate"]))
        row("hallucination rate", lambda s: fmt(A[s]["hallucination_rate"]))
        row("faithfulness error rate", lambda s: fmt(A[s].get("faithfulness_error_rate")))
        md.append("| **Underspecified block** (expected: refusal) | | | | |")
        row("n", lambda s: str(B[s]["n"]))
        row("correct refusals", lambda s: str(B[s]["correct_refusals"]))
        row("refusals naming another cause", lambda s: str(B[s]["refusals_other_cause"])
            if s.startswith("pct") else "-")
        row("answers", lambda s: str(B[s]["answers"]))
        # own outcome: not a correct refusal, not an answer, not a hallucination
        row("no response (generation exceeded 15 min)", lambda s: str(B[s]["no_response"]))
        row("hallucinations (RAG/raw: every answer; PCT: replay fails)",
            lambda s: str(B[s]["hallucinations"]))
        row("manual review", lambda s: str(B[s]["manual_review"]))
        row("correct-refusal rate", lambda s: fmt(B[s]["correct_rate"]))
        row("hallucination rate", lambda s: fmt(B[s]["hallucination_rate"]))
        md.append("")
    open(os.path.join(RESULTS_DIR, "underspecified_table.md"), "w", encoding="utf-8").write(
        "\n".join(md) + "\n")
    # 4. every RAG / raw answer to an underspecified query, with the value it assumed
    lab = dict(SYSTEMS)
    md = ["# RAG / raw answers to underspecified queries, with the value assumed", "",
          "`stated` = the response states the assumption in words; `formula` = implied by "
          "the formula it applies; `-` = not readable from the response. Evidence is quoted "
          "from the response. g = the response uses g = 9.8(1) m/s^2.", ""]
    for d, v in rep["domains"].items():
        B = v["baseline_answers_on_underspecified"]
        if not B:
            continue
        md += [f"## {d} ({len(B)})", "",
               "| system | query | missing condition | answer given | gold value | "
               "assumed (how read) | evidence | g | also says a value is missing |",
               "|---|---|---|---|---|---|---|---|---|"]
        for b in sorted(B, key=lambda b: (b["id"], b["system"])):
            cells = []
            for c in b["conditions"]:
                st = [a for a in b["assumed"] if a["condition"] == c]
                im = [a for a in b["implied_by_formula"] if a["condition"] == c]
                a = (st or im or [None])[0]
                cells.append((c, f"{a['assumed']} ({'stated' if st else 'formula'})"
                              if a else "-", a["evidence"] if a else ""))
            md.append("| " + " | ".join([
                lab[b["system"]], f"q{b['id']}", "; ".join(c for c, _, _ in cells),
                (f"{b['value_given']:.4g} {b['unit']}" if isinstance(b["value_given"],
                 (int, float)) else f"not auto-read ({b['value_given']}); manual verdict: "
                 f"{b.get('manual_verdict')}".replace("|", "/")),
                f"{b['gold_expected_value']:.4g}",
                "; ".join(x for _, x, _ in cells),
                " / ".join(e for _, _, e in cells if e).replace("|", "/")[:200],
                "yes" if b["assumes_g"] else "", "yes" if b["also_says_value_missing"] else ""])
                + " |")
        md.append("")
    open(os.path.join(RESULTS_DIR, "underspecified_baseline_answers.md"), "w",
         encoding="utf-8").write("\n".join(md) + "\n")


if __name__ == "__main__":
    sys.exit(main())
