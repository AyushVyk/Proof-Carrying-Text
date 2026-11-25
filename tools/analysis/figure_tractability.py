"""Tractability figure from STORED data only (rewritten 2026-11-25, paper inputs item 8).

No model call and no engine call.

  (a) PCT deployed refusals by failure type F1-F6, per domain, from results/<d>/pipeline.json
      (all 300 queries; F4 split by stage: parser / resolution).
  (b) PCT oracle refusals by failure type, in-scope queries, same source (out-of-scope
      queries have no gold goal state and never reach the engine; in-scope queries with no
      gold goal state are shown as their own bar).
  (c) Latency, mean +- SD seconds per query, from stored timings (stored_latency.py).

    python tools/analysis/figure_tractability.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for p in (ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from layout import FIGURES_DIR, results_path  # noqa: E402
import stored_latency as L  # noqa: E402

DOMS = ["physics", "chemistry", "biology"]
TYPES = ["F1", "F2", "F3", "F4", "F5", "F6"]
COL = {"physics": "#1f77b4", "chemistry": "#ff7f0e", "biology": "#2ca02c"}


def refusal_counts() -> dict:
    out = {}
    for d in DOMS:
        p = results_path(d, "pipeline.json")
        L.READ.add(os.path.relpath(p, ROOT))
        rows = json.load(open(p, encoding="utf-8"))["rows"]
        dep = Counter()
        for r in rows:
            if r["deployed"] != "refuse":
                continue
            t = r.get("deployed_type")
            dep[(t, r.get("stage")) if t == "F4" else (t, None)] += 1
        orc = Counter(r.get("oracle_type") for r in rows
                      if r["scope"] == "in" and r["oracle"] == "refuse")
        out[d] = {"deployed": dep, "oracle": orc, "n": len(rows),
                  "n_in": sum(r["scope"] == "in" for r in rows)}
    return out


def main() -> int:
    plt.style.use("seaborn-v0_8-whitegrid")
    R = refusal_counts()
    lat = L.summary()
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))
    w = 0.26
    x = np.arange(len(TYPES))

    # (a) deployed
    ax = axes[0]
    for j, d in enumerate(DOMS):
        c = R[d]["deployed"]
        parser = [c.get(("F4", "parser"), 0) if t == "F4" else 0 for t in TYPES]
        other = [(c.get(("F4", "resolution"), 0) + c.get(("F4", "engine"), 0)) if t == "F4"
                 else c.get((t, None), 0) for t in TYPES]
        xs = x + (j - 1) * w
        ax.bar(xs, parser, w, color=COL[d], alpha=0.45, edgecolor="#333", linewidth=0.5)
        ax.bar(xs, other, w, bottom=parser, color=COL[d], edgecolor="#333", linewidth=0.5,
               label=f"{d} (n = {sum(c.values())} of {R[d]['n']})")
        for xi, a, b in zip(xs, parser, other):
            if a + b:
                ax.text(xi, a + b + 3, str(a + b), ha="center", va="bottom", fontsize=7)
    ax.set_xticks(x, TYPES)
    top = max(sum(v for k, v in R[d]["deployed"].items() if k[0] == t)
              for d in DOMS for t in TYPES)
    ax.set_ylim(0, top * 1.35)
    ax.set_ylabel("Refusals (count)")
    ax.set_title("(a) PCT deployed: refusals by failure type", fontsize=10)
    ax.legend(fontsize=7.5, loc="upper left")
    ax.text(0.99, 0.70, "F4: light = parse stage,\ndark = resolution stage",
            transform=ax.transAxes, ha="right", va="top", fontsize=7, style="italic")

    # (b) oracle, in scope
    ax = axes[1]
    labels = TYPES + ["no gold\ngoal state"]
    x2 = np.arange(len(labels))
    for j, d in enumerate(DOMS):
        c = R[d]["oracle"]
        vals = [c.get(t, 0) for t in TYPES] + [c.get("-", 0)]
        xs = x2 + (j - 1) * w
        ax.bar(xs, vals, w, color=COL[d], edgecolor="#333", linewidth=0.5,
               label=f"{d} (n = {sum(vals)} of {R[d]['n_in']} in scope)")
        for xi, v in zip(xs, vals):
            if v:
                ax.text(xi, v + 1, str(v), ha="center", va="bottom", fontsize=7)
    ax.set_xticks(x2, labels, fontsize=8)
    ax.set_ylim(0, max(max(R[d]["oracle"].values()) for d in DOMS) * 1.35)
    ax.set_ylabel("Refusals (count)")
    ax.set_title("(b) PCT oracle (gold goal states): in-scope refusals", fontsize=10)
    ax.legend(fontsize=7.5, loc="upper left")

    # (c) latency
    ax = axes[2]
    sysl = [("pct_deployed", "PCT deployed\n(parse)"), ("rag", "RAG"), ("baseline", "Raw")]
    x3 = np.arange(len(sysl))
    for j, d in enumerate(DOMS):
        m = [lat[d][s]["mean_s"] for s, _ in sysl]
        sd = [lat[d][s]["sd_s"] for s, _ in sysl]
        n = [lat[d][s]["n"] for s, _ in sysl]
        xs = x3 + (j - 1) * w
        ax.bar(xs, m, w, yerr=sd, capsize=3, color=COL[d], edgecolor="#333", linewidth=0.5,
               label=d)
        for xi, v, k in zip(xs, m, n):
            ax.text(xi, 1, f"n={k}", ha="center", va="bottom", fontsize=6, rotation=90,
                    color="white")
    ax.set_xticks(x3, [l for _, l in sysl], fontsize=8)
    ax.set_ylabel("Seconds per query (mean +- SD)")
    ax.set_title("(c) Latency from stored timings", fontsize=10)
    ax.legend(fontsize=7.5, loc="upper right")
    fig.text(0.5, -0.02, "Stored test run, 2026-11-24.",
             ha="center", fontsize=8, style="italic")
    plt.tight_layout()
    os.makedirs(FIGURES_DIR, exist_ok=True)
    for ext in ("png", "pdf"):
        plt.savefig(os.path.join(FIGURES_DIR, f"tractability.{ext}"), dpi=300,
                    bbox_inches="tight")
    plt.close(fig)
    print(json.dumps({d: {"deployed": {f"{k[0]}{'/' + k[1] if k[1] else ''}": v
                                       for k, v in R[d]["deployed"].items()},
                          "oracle_in_scope": dict(R[d]["oracle"])} for d in DOMS}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
