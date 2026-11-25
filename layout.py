"""Repository layout: where the domains, query sets and results live.

One place that knows the directory structure, so no script hardcodes a path to
another domain's files. Every domain has the SAME entry shape here -- that
symmetry is the point. Physics is not the default and has no privileged name;
adding a fourth domain means adding one entry below and nothing else.

A domain entry:
    kb1       the rule/data knowledge base the engine proves against
    kb2       the parser schema and prompt (Stage 1's only domain knowledge)
    source    human-reviewed source data the KB is compiled from, if any
    queries   structured (gold goal states) and natural-language query sets
    results   where that domain's runs are written
"""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.abspath(__file__))

DOMAINS_DIR = os.path.join(ROOT, "domains")

# Query set in use. "test" (default) is the locked evaluation set; "dev" is the
# development set (domains/<d>/queries/dev/, results under results/dev/). Every
# tool reads paths from here, so PCT_QUERY_SET=dev switches all of them at once
# and no development run can read or write a test-set file.
QUERY_SET = os.environ.get("PCT_QUERY_SET", "test")
if QUERY_SET not in ("test", "dev"):
    raise ValueError(f"PCT_QUERY_SET must be 'test' or 'dev', got {QUERY_SET!r}")
RESULTS_DIR = (os.path.join(ROOT, "results") if QUERY_SET == "test"
               else os.path.join(ROOT, "results", "dev"))


def _domain(name: str, kb1: str, kb2: str, sources: dict[str, str] | None = None,
            queries: dict[str, str] | None = None) -> dict:
    d = os.path.join(DOMAINS_DIR, name)
    q = os.path.join(d, "queries") if QUERY_SET == "test" else os.path.join(d, "queries", "dev")
    return {
        "name": name,
        "dir": d,
        "kb1": os.path.join(d, kb1),
        "kb2": os.path.join(d, kb2),
        "sources": {k: os.path.join(d, v) for k, v in (sources or {}).items()},
        "queries": {k: os.path.join(q, v) for k, v in (queries or {}).items()},
        "results": os.path.join(RESULTS_DIR, name),
    }


DOMAINS = {
    "physics": _domain(
        "physics", "kb1_physics.json", "kb2_physics.json",
        queries={"structured": "queries_300.json"},
    ),
    "chemistry": _domain(
        "chemistry", "kb1_chemistry.json", "kb2_chemistry.json",
        sources={"species_and_reactions": "source.json"},
        queries={"structured": "structured.json",
                 "natural_language": "natural_language.json"},
    ),
    "biology": _domain(
        "biology", "kb1_biology.json", "kb2_biology.json",
        sources={"vertebrates": "source_vertebrates.json",
                 "felidae": "source_felidae.json"},
        queries={"structured": "structured.json",
                 "natural_language": "natural_language.json"},
    ),
}

FIGURES_DIR = os.path.join(RESULTS_DIR, "figures")


def domain(name: str) -> dict:
    """Look up a domain, accepting the short aliases the CLIs take."""
    key = name.lower()
    alias = {"bio": "biology", "chem": "chemistry", "stoich": "chemistry",
             "phys": "physics"}
    key = alias.get(key, key)
    if key not in DOMAINS:
        raise KeyError(f"unknown domain {name!r}; have {sorted(DOMAINS)}")
    return DOMAINS[key]


def results_path(domain_name: str, filename: str) -> str:
    """Path inside a domain's results directory, creating it on first use."""
    d = domain(domain_name)["results"]
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, filename)
