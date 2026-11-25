"""The pre-registered scorer. Implements docs/scoring_rubric.md and nothing else.

Hash-locked with the rubric in docs/scoring_rubric.lock.json before any baseline
run on chemistry or biology. Every metric is computed from STORED outputs:
    PCT       results/<domain>/pipeline.json
    RAG, raw  results/physics/evaluation_300.json   (physics)
              results/<domain>/baselines.json        (chemistry, biology)

Run:
    python tools/analysis/score.py                 # all domains with stored outputs
    python tools/analysis/score.py biology         # one domain, with case listing
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from layout import DOMAINS, results_path  # noqa: E402

TOL = 0.01
SYSTEMS = ("pct_deployed", "pct_oracle", "rag", "baseline")

# outcome labels
CORRECT, WRONG, MANUAL, REFUSAL_OK, REFUSAL = (
    "correct", "wrong", "manual_review", "refusal_correct", "refusal")


# ===========================================================================
# §3  refusal detection -- verbatim from eval_three_system_physics._detect_refusal
# ===========================================================================
_REFUSAL_TOKENS = (
    "i cannot answer", "cannot answer", "i don't know", "i do not know",
    "unable to", "i'm sorry", "i am sorry", "no information",
    "not enough information", "insufficient information", "cannot determine",
)


def is_refusal_text(text: str) -> bool:
    t = (text or "").lower()
    return any(tok in t for tok in _REFUSAL_TOKENS)


# ===========================================================================
# §4.1  numeric: the Table 3 comparator, and the unit-aware reading rule
# ===========================================================================
def table3_correct(value, unit, expected, expected_unit) -> bool:
    """generate_supplementary._correct_in_scope, applied to a (value, unit)."""
    if value is None or expected is None or unit != expected_unit:
        return False
    try:
        if expected == 0:
            return abs(float(value)) < 1e-6
        return abs(float(value) - float(expected)) / abs(float(expected)) <= TOL
    except (TypeError, ValueError):
        return False


# canonical unit -> spellings. Symbols are case-sensitive; names are not.
_UNIT_SYMBOLS = {
    "m/s2": ["m/s2", "m/s^2", "m/s²", "m s^-2", "m·s⁻²", "m s-2", "m/s/s"],
    "m/s": ["m/s", "m s^-1", "m·s⁻¹", "m s-1", "ms^-1", "ms-1"],
    "kg*m2/s": ["kg*m2/s", "kg·m²/s", "kg m^2/s", "kg m²/s", "kg*m^2/s", "kg·m^2/s",
                "kg m2/s", "kg⋅m²/s"],
    "kg*m2": ["kg*m2", "kg·m²", "kg m^2", "kg m²", "kg*m^2", "kg·m^2", "kg m2", "kg⋅m²"],
    "kg*m/s": ["kg*m/s", "kg·m/s", "kg m/s", "kg⋅m/s"],
    "kg/m3": ["kg/m3", "kg/m^3", "kg/m³", "kg m^-3", "kg·m⁻³"],
    "rad/s": ["rad/s", "rad s^-1", "rad·s⁻¹"],
    "N*s": ["N*s", "N·s", "N s", "N⋅s", "Ns"],
    "N*m": ["N*m", "N·m", "N m", "N⋅m", "Nm"],
    "Pa": ["Pa", "N/m2", "N/m^2", "N/m²"],
    "J": ["J"], "N": ["N"], "W": ["W"], "m": ["m"], "s": ["s"],
    "g": ["g"], "mol": ["mol"], "L": ["L"], "percent": ["%"],
}
_UNIT_NAMES = {
    "m/s2": ["meters per second squared", "metres per second squared",
             "meter per second squared", "metre per second squared"],
    "m/s": ["meters per second", "metres per second", "meter per second",
            "metre per second"],
    "kg*m2/s": ["kilogram square meters per second", "kilogram meters squared per second",
                "kg meters squared per second"],
    "kg*m2": ["kilogram square meters", "kilogram meters squared",
              "kilogram square metres", "kilogram metres squared"],
    "kg*m/s": ["kilogram meters per second", "kilogram metres per second",
               "kilogram-meters per second", "kilogram-metres per second"],
    "kg/m3": ["kilograms per cubic meter", "kilograms per cubic metre",
              "kilogram per cubic meter", "kilogram per cubic metre"],
    "rad/s": ["radians per second", "radian per second"],
    "N*s": ["newton seconds", "newton-seconds", "newton second", "newton-second"],
    "N*m": ["newton meters", "newton-meters", "newton metres", "newton-metres",
            "newton meter", "newton-meter", "newton metre", "newton-metre"],
    "Pa": ["pascals", "pascal"],
    "J": ["joules", "joule"], "N": ["newtons", "newton"], "W": ["watts", "watt"],
    "m": ["meters", "metres", "meter", "metre"],
    "s": ["seconds", "second", "secs", "sec"],
    "g": ["grams", "gram"], "mol": ["moles", "mole"],
    "L": ["liters", "litres", "liter", "litre"],
    "percent": ["percent", "per cent"],
    "particles": ["particles", "molecules", "formula units"],
}
# recognised units that are NOT any expected unit (a possible conversion)
_OTHER_UNITS = ["kJ", "MJ", "km/h", "km", "cm", "mm", "kg", "mg", "mL", "kN", "kW",
                "kPa", "ms", "min", "hours", "kilojoules", "kilometers", "kilometres",
                "centimeters", "centimetres", "kilograms", "milligrams", "milliliters",
                "millilitres", "kilonewtons", "kilowatts", "minutes", "atm", "mmol",
                "kilopascals"]

_UNIT_TABLE = []    # (spelling, canonical, case_sensitive)
for canon, sp in _UNIT_SYMBOLS.items():
    _UNIT_TABLE += [(x, canon, True) for x in sp]
for canon, sp in _UNIT_NAMES.items():
    _UNIT_TABLE += [(x, canon, False) for x in sp]
_UNIT_TABLE += [(x, "__other__", x[0].isupper() or len(x) <= 3) for x in _OTHER_UNITS]
_UNIT_TABLE.sort(key=lambda t: -len(t[0]))          # longest spelling first

_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
_NUM = re.compile(
    r"(?<![\w.])([-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-−]?\d+(?:\.\d+)?)"
    r"(?:\s*(?:[eE]([-+]?\d+)|\s*[x×*·]\s*10\s*(?:\^|\*\*)?\s*([-−⁻]?[\d⁰¹²³⁴⁵⁶⁷⁸⁹]+)))?")


def _unit_at(text: str, pos: int):
    """The longest unit spelling starting at `pos` (after optional spaces)."""
    j = pos
    while j < len(text) and text[j] in " \t":
        j += 1
    for spelling, canon, cs in _UNIT_TABLE:
        seg = text[j:j + len(spelling)]
        if (seg == spelling) if cs else (seg.lower() == spelling.lower()):
            end = j + len(spelling)
            nxt = text[end:end + 1]
            if nxt and (nxt.isalnum() or nxt in "²³/^·*"):
                continue            # part of a longer token
            return canon
    return None


def _numbers(t: str):
    """(value, end_position) for every number in t, scientific forms included."""
    for m in _NUM.finditer(t or ""):
        base = float(m.group(1).replace(",", "").replace("−", "-"))
        if m.group(2):
            base *= 10 ** int(m.group(2))
        elif m.group(3):
            base *= 10 ** int(m.group(3).translate(_SUP).replace("−", "-"))
        yield base, m.end()


def _same(a: float, b: float) -> bool:
    return (abs(a - b) <= TOL * abs(b)) if b else abs(a) < 1e-6


def read_numeric(text: str, expected_unit: str, query: str = ""):
    """§4.1 reading rule -> ("value", v) or ("manual", reason)."""
    t = text or ""
    given = [v for v, _ in _numbers(query)]
    with_expected, with_other, unitless, restated = [], [], 0, 0
    for base, end in _numbers(t):
        canon = _unit_at(t, end)
        if canon == expected_unit and any(_same(base, g) for g in given):
            restated += 1
            continue
        if canon == expected_unit:
            with_expected.append(base)
        elif canon is not None:
            with_other.append((base, canon))
        else:
            unitless += 1
    if with_expected:
        ref = with_expected[0]
        if all(_same(v, ref) for v in with_expected):
            return "value", ref
        return "manual", f"conflicting values with the expected unit: {with_expected[:4]}"
    if restated:
        return "manual", "the only values in the expected unit restate the query"
    if with_other:
        return "manual", f"no value in {expected_unit}; other units {with_other[:3]}"
    if unitless:
        return "manual", "numbers stated without a unit"
    return "manual", "no number stated"


# ===========================================================================
# §4.2  taxa
# ===========================================================================
def _norm(s: str) -> str:
    s = (s or "").lower().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", s).strip()


class Taxa:
    def __init__(self):
        kb = json.load(open(DOMAINS["biology"]["kb1"], encoding="utf-8"))
        self.T = kb["taxa"]
        names: dict[str, set] = {}
        for key, v in self.T.items():
            for n in (key, v.get("common"), v.get("scientific")):
                if n:
                    names.setdefault(_norm(n), set()).add(key)
        self.collisions = {n: sorted(k) for n, k in names.items() if len(k) > 1}
        self.names = sorted(((n, next(iter(k))) for n, k in names.items() if len(k) == 1),
                            key=lambda x: -len(x[0]))

    def ancestry(self, t):
        c = [t]
        while self.T[c[-1]]["parent"] is not None:
            c.append(self.T[c[-1]]["parent"])
        return list(reversed(c))

    def mentioned(self, text: str) -> list[str]:
        s = " " + _norm(text) + " "
        found = []
        for name, key in self.names:
            pat = re.compile(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])")
            m = pat.search(s)
            while m:
                found.append(key)
                s = s[:m.start()] + " " * (m.end() - m.start()) + s[m.end():]
                m = pat.search(s)
        return sorted(set(found))

    def claimed(self, text: str):
        """('taxon', t) or ('manual', reason)."""
        m = self.mentioned(text)
        if not m:
            return "manual", "names no KB taxon"
        deepest = max(m, key=lambda t: self.T[t]["level"])
        chain = set(self.ancestry(deepest))
        if not all(t in chain for t in m):
            return "manual", f"names taxa on different branches: {m}"
        return "taxon", deepest

    def judge(self, claim: str, truth: str):
        """§4.2 scoring of a claimed taxon against a truth taxon."""
        if claim == truth:
            return CORRECT, None
        if claim in self.ancestry(truth):
            return MANUAL, f"partially correct: {claim} is an ancestor of {truth}"
        if truth in self.ancestry(claim):
            return MANUAL, f"over-specific: {claim} is a descendant of {truth}"
        return WRONG, None


_TAXA = None


def taxa() -> Taxa:
    global _TAXA
    if _TAXA is None:
        _TAXA = Taxa()
    return _TAXA


def leading_yes_no(text: str):
    m = re.match(r"^[^a-z0-9]*(yes|no)(?![a-z0-9])", _norm(text))
    return m.group(1) if m else None


# ===========================================================================
# per-query scoring
# ===========================================================================
def score_pct(q: dict, row: dict, which: str):
    """-> (outcome, note). `which` is 'deployed' or 'oracle'."""
    answer = row[which]
    claim = row.get(f"{which}_claim")
    if answer == "refuse":
        if q["scope"] == "out":
            return REFUSAL_OK, None
        if q["qtype"] == "is_a" and q["truth"] == "no":
            return REFUSAL_OK, "typed failure on a negative is-a"
        return REFUSAL, None
    if q["scope"] == "out":
        return WRONG, "answered an out-of-scope query"
    if q["qtype"] == "numeric":
        ok = table3_correct(claim.get("value"), claim.get("unit"),
                            q["truth"], q["expected_unit"])
        return (CORRECT if ok else WRONG), None
    T = taxa()
    kind, tx = claim["kind"], claim["taxon"]
    if q["qtype"] == "what_is":
        return T.judge(tx, q["truth"])
    # is_a
    target = q["target"]
    if kind == "member":
        if tx != target:
            return MANUAL, f"membership derived for parsed candidate {tx!r}, asked {target!r}"
        return (CORRECT if q["truth"] == "yes" else WRONG), None
    # classification ("derived: X", or a bare classification)
    if q["truth"] == "yes" and target in (claim.get("derivation_outputs") or []):
        return CORRECT, f"{target} is proved inside the derivation of {tx}"
    if not q.get("expected_classification"):
        return WRONG, f"classified as {tx}; no classification is derivable from the query"
    return T.judge(tx, q["expected_classification"])


def score_text(q: dict, text: str):
    if is_refusal_text(text):
        return (REFUSAL_OK, None) if q["scope"] == "out" else (REFUSAL, None)
    if q["scope"] == "out":
        return WRONG, "answered an out-of-scope query"
    if q["qtype"] == "numeric":
        kind, v = read_numeric(text, q["expected_unit"], q["nl"])
        if kind == "manual":
            return MANUAL, v
        return (CORRECT if table3_correct(v, q["expected_unit"], q["truth"],
                                          q["expected_unit"]) else WRONG), f"read {v}"
    if q["qtype"] == "is_a":
        yn = leading_yes_no(text)
        if yn is None:
            return MANUAL, "does not begin with yes or no"
        return (CORRECT if yn == q["truth"] else WRONG), f"read {yn}"
    kind, tx = taxa().claimed(text)
    if kind == "manual":
        return MANUAL, tx
    out, note = taxa().judge(tx, q["truth"])
    return out, note or f"read {tx}"


# ===========================================================================
# loading stored outputs
# ===========================================================================
def query_meta(domain: str) -> dict:
    meta = {}
    if domain == "physics":
        for r in json.load(open(results_path("physics", "pipeline.json"),
                                encoding="utf-8"))["rows"]:
            meta[r["id"]] = {"scope": r["scope"], "qtype": "numeric", "truth": r["truth"],
                             "expected_unit": r["expected_unit"], "nl": r["nl"]}
        return meta
    qs = json.load(open(DOMAINS[domain]["queries"]["natural_language"],
                        encoding="utf-8"))["queries"]
    if domain == "chemistry":
        sys.path.insert(0, os.path.join(ROOT, "tools", "eval"))
        import eval_chemistry as RS
        for q in qs:
            out = q["part"] == "out_of_scope"
            meta[q["id"]] = {"scope": "out" if out else "in", "qtype": "numeric",
                             "truth": None if out else q["expected"],
                             "expected_unit": None if out else RS.UNIT[q["gold"]["goal"]],
                             "nl": q["nl"]}
        return meta
    for q in qs:
        out = q.get("scope") == "out"
        form = q.get("gold_form")
        meta[q["id"]] = {"scope": "out" if out else "in",
                         "qtype": None if out else form,
                         "truth": q.get("truth"),
                         "target": q.get("gold_target"),
                         "expected_classification": q.get("expected_classification"),
                         "nl": q["nl"]}
    return meta


def baseline_texts(domain: str):
    """{id: {"rag": text, "baseline": text}} or None if not yet run."""
    if domain == "physics":
        rows = json.load(open(results_path("physics", "evaluation_300.json"), encoding="utf-8"))
        return {i: {"rag": (r.get("rag") or {}).get("content", ""),
                    "baseline": (r.get("baseline") or {}).get("content", "")}
                for i, r in enumerate(rows, 1)}
    p = results_path(domain, "baselines.json")
    if not os.path.exists(p):
        return None
    rows = json.load(open(p, encoding="utf-8"))["rows"]
    return {r["id"]: {"rag": r["rag"]["content"], "baseline": r["baseline"]["content"]}
            for r in rows}


# ===========================================================================
# aggregation (§5, §6)
# ===========================================================================
def aggregate(outcomes: list) -> dict:
    """outcomes: list of (scope, outcome)."""
    ins = [o for s, o in outcomes if s == "in"]
    oos = [o for s, o in outcomes if s == "out"]
    answered_in = [o for o in ins if o in (CORRECT, WRONG, MANUAL)]
    mr = sum(1 for o in ins if o == MANUAL)
    corr_ans = sum(1 for o in ins if o == CORRECT)
    corr_ref = sum(1 for o in ins if o == REFUSAL_OK)
    wrong_in = sum(1 for o in ins if o == WRONG)
    oos_ans = sum(1 for o in oos if o == WRONG)
    oos_ref = sum(1 for o in oos if o == REFUSAL_OK)

    def rate(n, d):
        return (n / d) if d else None

    n_in = len(ins)
    scored_in = n_in - mr
    scored_ans = len(answered_in) - mr
    hall_den = scored_ans + oos_ans
    return {
        "in_scope": n_in, "out_of_scope": len(oos),
        "answered": len(answered_in), "manual_review": mr,
        "correct": corr_ans + corr_ref, "correct_answers": corr_ans,
        "correct_refusals": corr_ref, "wrong_answers": wrong_in,
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


def score_domain(domain: str) -> dict:
    meta = query_meta(domain)
    pipe = {r["id"]: r for r in json.load(open(results_path(domain, "pipeline.json"),
                                               encoding="utf-8"))["rows"]}
    texts = baseline_texts(domain)
    per_system = {s: [] for s in SYSTEMS}
    cases = []
    for qid, q in meta.items():
        row = pipe[qid]
        for which, sysname in (("deployed", "pct_deployed"), ("oracle", "pct_oracle")):
            out, note = score_pct(q, row, which)
            per_system[sysname].append((q["scope"], out))
            cases.append({"system": sysname, "id": qid, "outcome": out, "note": note,
                          "response": row[which], "nl": q["nl"],
                          "expected": q.get("truth")})
        if texts is not None:
            for sysname in ("rag", "baseline"):
                txt = texts[qid][sysname]
                out, note = score_text(q, txt)
                per_system[sysname].append((q["scope"], out))
                cases.append({"system": sysname, "id": qid, "outcome": out, "note": note,
                              "response": txt, "nl": q["nl"],
                              "expected": q.get("truth")})
    agg = {s: aggregate(v) for s, v in per_system.items() if v}
    return {"domain": domain, "systems": agg, "cases": cases,
            "taxon_name_collisions": taxa().collisions if domain == "biology" else {}}


def main() -> int:
    domains = [a.lower() for a in sys.argv[1:]] or ["physics", "chemistry", "biology"]
    report = {}
    for d in domains:
        if not os.path.exists(results_path(d, "pipeline.json")):
            print(f"  {d}: no pipeline.json yet")
            continue
        r = score_domain(d)
        report[d] = r
        print(f"== {d.upper()} ==")
        for s, a in r["systems"].items():
            h = a["hallucination_rate"]
            print(f"  {s:<13} answered {a['answered']:>3}/{a['in_scope']:<3} correct "
                  f"{a['correct']:>3}  MR {a['manual_review']:>2}  oos refused "
                  f"{a['oos_refused']}/{a['out_of_scope']}  halluc "
                  f"{'-' if h is None else f'{100*h:.1f}%'}")
    path = os.path.join(ROOT, "results", "scores.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
