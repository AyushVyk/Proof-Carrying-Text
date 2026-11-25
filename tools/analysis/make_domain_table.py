"""The three-domain results table: PCT deployed, PCT oracle, RAG, raw baseline.

Every figure comes from results/scores.json, which the hash-locked scorer
(tools/analysis/score.py, implementing docs/scoring_rubric.md) computes from
STORED outputs. The two structural rows -- typed failure on every refusal, proof
trace on every claim -- are checked here against the stored PCT rows. No model is
called and no number is typed in by hand.

PCT oracle sits in its own column. It is the engine on gold goal states, an
upper bound on the parser, and is never compared against deployed numbers.

Writes results/three_domain_table.txt, results/three_domain_table.md, and
results/manual_review.md (every case the rubric would not auto-score).

Run:
    python tools/analysis/score.py
    python tools/analysis/make_domain_table.py
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from layout import RESULTS_DIR, results_path  # noqa: E402

if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import split_metrics as SM  # noqa: E402

DOMAINS = ["physics", "chemistry", "biology"]
SYSTEMS = [("pct_deployed", "PCT dep."), ("pct_oracle", "PCT oracle"),
           ("rag", "RAG"), ("baseline", "Raw")]


def pct(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def ci(n, d):
    """Wilson 95% interval (split_metrics.wilson), e.g. ' (95% CI 61.2-72.4)'."""
    w = SM.wilson(n, d)
    return "" if w is None else f" (CI {100 * w[0]:.1f}-{100 * w[1]:.1f})"


def bounds(b):
    lo, hi = b
    if lo is None or hi is None or abs(lo - hi) < 1e-12:
        return ""
    return f" [{100 * lo:.1f}-{100 * hi:.1f}]"


def structural(domain: str, which: str) -> tuple[bool, bool, int, int]:
    """(every refusal typed, every claim carries a proof trace, refusals, claims)."""
    rows = json.load(open(results_path(domain, "pipeline.json"), encoding="utf-8"))["rows"]
    # a row with no gold goal state never reached the engine in the oracle run,
    # so it is not an engine refusal and carries no failure type to check
    refusals = [r for r in rows if r[which] == "refuse"
                and not (which == "oracle" and r.get("oracle_type") == "-")]
    claims = [r for r in rows if r[which] != "refuse"]
    typed = all((r.get(f"{which}_failure") or {}).get("type") for r in refusals)
    traced = all((r.get(f"{which}_claim") or {}).get("rules")
                 or (r.get(f"{which}_claim") or {}).get("derivation_steps")
                 for r in claims)
    return typed, traced, len(refusals), len(claims)


def frac(n, d):
    return "-" if not d else f"{100 * n / d:.1f}% ({n}/{d}){ci(n, d)}"


def main() -> int:
    scores = json.load(open(os.path.join(RESULTS_DIR, "scores.json"), encoding="utf-8"))
    cols = [(d, s, lab) for d in DOMAINS if d in scores
            for s, lab in SYSTEMS if s in scores[d]["systems"]]

    # hallucination vs faithfulness split (split_metrics: PCT by replay of every
    # stored answer; RAG/raw keep the locked rubric's Def. 6 rate)
    splits, replays = {}, {}
    for d in DOMAINS:
        if d in scores:
            replays[d] = SM.replay_domain(d)
            splits[d] = SM.split_domain(d, scores[d], replays[d])

    def hall(a, d, s):
        sp = splits[d][s]
        if s.startswith("pct"):
            return frac(len(sp["hallucinations"]), sp["answered_den"])
        hn = a["wrong_answers"] + a["oos_answered"]
        hd = a["answered"] - a["manual_review"] + a["oos_answered"]
        return pct(a["hallucination_rate"]) + ci(hn, hd) + bounds(a["hallucination_rate_bounds"])

    def faith(a, d, s):
        sp = splits[d][s]
        if not s.startswith("pct"):
            return "-"
        return frac(len(sp["faithfulness_errors"]), sp["answered_den"])

    def cell(d, s, key, fmt):
        return fmt(scores[d]["systems"][s], d, s)

    rows = [
        ("in-scope / out-of-scope queries",
         lambda a, d, s: f"{a['in_scope']} / {a['out_of_scope']}"),
        ("answered (in scope)", lambda a, d, s: f"{a['answered']}"),
        ("correct (in scope)", lambda a, d, s: f"{a['correct']}"
         + (f" ({a['correct_refusals']} ref.)" if a["correct_refusals"] else "")),
        ("less specific / over-specific", lambda a, d, s:
         f"{a.get('partial_ancestor', 0)} / {a.get('overspecific', 0)}"),
        ("manual review", lambda a, d, s: f"{a['manual_review']}"),
        ("no response (generation > 15 min)", lambda a, d, s: f"{a.get('no_response', 0)}"),
        ("unconditional accuracy", lambda a, d, s: pct(a["unconditional_accuracy"])
         + ci(a["correct"], a["in_scope"] - a["manual_review"])
         + bounds(a["unconditional_accuracy_bounds"])),
        ("conditional accuracy", lambda a, d, s: pct(a["conditional_accuracy"])
         + ci(a["correct_answers"], a["answered"] - a["manual_review"])
         + bounds(a["conditional_accuracy_bounds"])),
        ("out-of-scope refusal rate", lambda a, d, s: pct(a["oos_refusal_rate"])
         + ci(a["oos_refused"], a["out_of_scope"])),
        ("hallucination rate", hall),
        ("faithfulness error rate", faith),
        ("typed failure on refusal", lambda a, d, s: (
            ("yes" if structural(d, s[4:])[0] else "NO") if s.startswith("pct") else "no")),
        ("proof trace on every claim", lambda a, d, s: (
            ("yes" if structural(d, s[4:])[1] else "NO") if s.startswith("pct") else "no")),
    ]

    W, L = 40, 32
    head1 = " " * L + "".join(f"{d.upper():<{W * sum(1 for c in cols if c[0] == d)}}"
                              for d in DOMAINS if d in scores)
    head2 = f"{'':<{L}}" + "".join(f"{lab:<{W}}" for _, _, lab in cols)
    lines = ["=" * (L + W * len(cols)),
             "PCT ACROSS THREE SCIENTIFIC DOMAINS -- PCT deployed, PCT oracle, RAG, raw baseline",
             "=" * (L + W * len(cols)),
             "Scored by the pre-registered rubric (docs/scoring_rubric.md, hash-locked) from",
             "stored outputs only. PCT oracle = the engine on gold goal states; it is an upper",
             "bound on the parser and is never compared against the deployed column.",
             "[a-b] = bounds with every manual-review case scored correct / wrong.",
             "(CI a-b) = Wilson 95% confidence interval on the rate's count / denominator.",
             "hallucination rate: PCT = answers whose stored derivation fails replay (prove()",
             "  re-run on the stored goal state; every step checked against the KB) / answered;",
             "  RAG, raw = (in-scope wrong + out-of-scope answers) / answered (rubric Sec. 6).",
             "faithfulness error rate (PCT only): answers that pass replay but are scored wrong,",
             "  over-specific or less specific, plus answered out-of-scope queries / answered.",
             "  Answered = in + out of scope,",
             "  manual-review cases excluded.",
             "", head1, head2, "-" * (L + W * len(cols))]
    for label, f in rows:
        lines.append(f"{label:<{L}}" + "".join(f"{f(scores[d]['systems'][s], d, s):<{W}}"
                                                for d, s, _ in cols))
    lines.append("-" * (L + W * len(cols)))
    txt = "\n".join(lines) + "\n"
    print(txt)
    with open(os.path.join(RESULTS_DIR, "three_domain_table.txt"), "w", encoding="utf-8") as fh:
        fh.write(txt)

    # markdown version
    md = ["| Metric | " + " | ".join(f"{d} {lab}" for d, _, lab in cols) + " |",
          "|---|" + "---|" * len(cols)]
    for label, f in rows:
        md.append(f"| {label} | " + " | ".join(f(scores[d]["systems"][s], d, s)
                                              for d, s, _ in cols) + " |")
    with open(os.path.join(RESULTS_DIR, "three_domain_table.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")

    # every manual-review case
    mr = ["# Manual-review cases", "",
          "Every answer the pre-registered rubric would not auto-score. Excluded from all",
          "headline rates; the table's bracketed bounds show their maximum effect.", ""]
    n = 0
    for d in DOMAINS:
        if d not in scores:
            continue
        cases = [c for c in scores[d]["cases"] if c["outcome"] == "manual_review"]
        mr.append(f"## {d} ({len(cases)})")
        mr.append("")
        for c in cases:
            n += 1
            resp = str(c["response"]).replace("\n", " ")
            mr.append(f"- **{c['system']}**, query {c['id']}: {c['nl']}")
            mr.append(f"  - expected: `{c['expected']}`")
            mr.append(f"  - why manual: {c['note']}")
            mr.append(f"  - response: {resp[:700]}{'…' if len(resp) > 700 else ''}")
        mr.append("")
    with open(os.path.join(RESULTS_DIR, "manual_review.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(mr) + "\n")
    print(f"wrote results/three_domain_table.txt, .md, results/manual_review.md ({n} cases)")

    numbers = SM.paper_numbers(scores, splits, replays)
    with open(os.path.join(RESULTS_DIR, "paper_numbers.json"), "w", encoding="utf-8") as fh:
        json.dump(numbers, fh, indent=2, ensure_ascii=False)
    print("wrote results/paper_numbers.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
