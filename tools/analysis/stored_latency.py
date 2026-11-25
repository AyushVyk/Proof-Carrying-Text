"""Latency from STORED timings only (2026-11-25, paper inputs item 7). No model call.

  PCT deployed  `elapsed` of each parser call in results/<d>/deployed_parses.json (the run-4
                Stage-1 model call; the first call of a domain also contains the parser's
                one-off prompt-length probe). Engine time is not stored.
  PCT oracle    no model call; no stored timings.
  RAG, raw      `elapsed` of each stored response: physics results/physics/evaluation_300.json,
                chemistry / biology results/<d>/baselines.json.

Excluded: rows re-run after a transport error (flag `excluded_from_latency`, set by
tools/eval/rerun_transport_errors.py merge; their `elapsed` is the failed call), and PCT
parser calls that failed in transport (timeout / HTTP 500: no generation was returned).
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from layout import results_path  # noqa: E402

DOMS = ("physics", "chemistry", "biology")
SYSTEMS = ("pct_deployed", "pct_oracle", "rag", "baseline")
READ: set = set()


def _load(p):
    READ.add(os.path.relpath(p, ROOT))
    return json.load(open(p, encoding="utf-8"))


def timings(domain: str) -> dict:
    """system -> {"kept": [...], "excluded": [(id, s, why)], "all": [...]}"""
    out = {s: {"kept": [], "excluded": [], "all": []} for s in SYSTEMS}
    for r in _load(results_path(domain, "deployed_parses.json"))["rows"]:
        t = float(r["elapsed"])
        p = r["parsed"]
        out["pct_deployed"]["all"].append(t)
        if isinstance(p, dict) and "transport error" in str(p.get("reason", "")):
            out["pct_deployed"]["excluded"].append((r["id"], t, p["reason"]))
        else:
            out["pct_deployed"]["kept"].append(t)
    if domain == "physics":
        rows = [(i, r) for i, r in enumerate(
            _load(results_path("physics", "evaluation_300.json")), 1)]
    else:
        rows = [(r["id"], r) for r in _load(results_path(domain, "baselines.json"))["rows"]]
    for i, r in rows:
        for s in ("rag", "baseline"):
            x = r.get(s) or {}
            if x.get("elapsed") is None:
                continue
            t = float(x["elapsed"])
            out[s]["all"].append(t)
            if x.get("excluded_from_latency"):
                out[s]["excluded"].append((i, t, "re-run after a transport error"))
            else:
                out[s]["kept"].append(t)
    return out


def summary() -> dict:
    res = {}
    for d in DOMS:
        T = timings(d)
        res[d] = {}
        for s in SYSTEMS:
            k, a = T[s]["kept"], T[s]["all"]
            res[d][s] = None if not a else {
                "n": len(k), "mean_s": st.mean(k), "sd_s": st.stdev(k) if len(k) > 1 else 0.0,
                "n_excluded": len(T[s]["excluded"]),
                "excluded": T[s]["excluded"],
                "n_all": len(a), "mean_all_s": st.mean(a),
                "sd_all_s": st.stdev(a) if len(a) > 1 else 0.0}
    return res
