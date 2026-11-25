"""Stage 1 of the bowtie: NL query -> typed goal state.

Domain-free: every domain word, selection hint and few-shot example comes
from the KB2 file this parser is constructed with (its `parser_prompt` block),
not from this module. Point it at a different KB2 and it parses that domain.

Calls a local Ollama daemon serving Mistral-7B-Instruct (4-bit GGUF).
KB2 is injected into the system prompt as compact one-line tables (full
schema dumps confuse small models). Few-shot examples are embedded inline
as plain text. For multi-turn follow-ups, the previously *validated* goal
is fed back as `PREVIOUS GOAL: {json}` so the model edits structured state
rather than continuing free-form chat. The output is coerced and validated
against KB2 before being returned.

No third-party Python deps -- speaks ollama's HTTP API via urllib.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.error
import urllib.request

DEFAULT_MODEL = "mistral:7b-instruct-q4_K_M"
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
# Context window sent with every request. Ollama's default (4096 here) silently
# drops the FRONT of a longer prompt; the physics prompt is ~5.3k tokens.
NUM_CTX = 8192
# Probe context used once per prompt to learn its untruncated token count.
_PROBE_CTX = 32768


def _build_system_prompt(kb2: dict) -> str:
    """Assemble the system prompt for whatever domain kb2 describes.

    All domain-specific text -- the role line, priority rules, selection hints
    and few-shot examples -- comes from kb2["parser_prompt"]["header"] and
    ["footer"]. This function only generates the two schema tables from the KB
    and splices them between. No domain knowledge lives in this file.
    """
    pred_lines = []
    for p in kb2["predicates"]:
        ctxs = ", ".join(p["contexts"])
        pred_lines.append(f"- {p['predicate']} | unit={p['unit']} | contexts: {ctxs}")
    bind_lines = []
    for b in kb2["bindings_schema"]:
        line = f"- {b['predicate']} | unit={b['unit']}"
        vals = b.get("permitted_values")
        if vals:
            line += f" | one of: {', '.join(map(str, vals))}"
        if b.get("description"):
            line += f" | {b['description']}"
        bind_lines.append(line)

    pp = kb2.get("parser_prompt") or {}
    header = pp.get("header", "")
    footer = pp.get("footer", "")
    if not header and not footer:
        raise ValueError(
            "kb2 has no `parser_prompt` block; the parser holds no domain text of "
            "its own, so the KB must supply header/footer."
        )
    return (
        header
        + "ALLOWED PREDICATES (predicate | unit | contexts):\n"
        + "\n".join(pred_lines)
        + "\n\nALLOWED BINDINGS (binding name | unit | meaning):\n"
        + "\n".join(bind_lines)
        + footer
    )


_NUM_UNIT_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(.*)$")


def _name_key(s: str) -> str:
    """Case- and format-insensitive key for a categorical token or a recorded name:
    Unicode-normalised (subscript digits -> digits), case-folded, apostrophes and
    full stops dropped, every other run of non-alphanumerics -> '_', a leading
    article dropped. 'Felidae', 'FELIDAE', 'the Puma lineage', 'puma-lineage' and
    'H\u2082O' all reduce to the key of their KB token."""
    k = unicodedata.normalize("NFKC", s).casefold()
    k = re.sub(r"['\u2019`.]", "", k)
    k = re.sub(r"[^0-9a-z]+", "_", k).strip("_")
    return re.sub(r"^(?:the|a|an)_", "", k)


def _token_index(bentry: dict) -> dict:
    """key -> KB token for one categorical binding: each permitted token and each
    name KB2 records for it (`value_names`). A key two tokens would share is
    dropped, so the mapping is deterministic and never guesses."""
    allowed = bentry.get("permitted_values") or []
    pairs = [(_name_key(str(t)), t) for t in allowed]
    for tok, names in (bentry.get("value_names") or {}).items():
        pairs += [(_name_key(n), tok) for n in names]
    idx: dict = {}
    clash: set = set()
    for k, t in pairs:
        if k in idx and idx[k] != t:
            clash.add(k)
        idx.setdefault(k, t)
    return {k: t for k, t in idx.items() if k not in clash}


def _normalize_unit(u: str | None) -> str | None:
    if not isinstance(u, str):
        return u
    out = u.replace("²", "2").replace("³", "3").replace("^", "")
    out = out.replace(" * ", "*").replace(" ", "*")
    return out


class Parser:
    def __init__(
        self,
        kb2_path: str,
        model: str = DEFAULT_MODEL,
        url: str = DEFAULT_OLLAMA_URL,
    ) -> None:
        """`kb2_path` is REQUIRED: the parser holds no domain knowledge of its
        own, so there is no domain it could default to. Point it at a different
        KB2 and it parses that domain."""
        with open(kb2_path, "r", encoding="utf-8") as f:
            self.kb2 = json.load(f)
        self.model = model
        self.url = url.rstrip("/")
        self.system_prompt = _build_system_prompt(self.kb2)
        self.followup_prompt = (self.kb2.get("parser_prompt") or {}).get(
            "followup", "")
        self._pred_index = {p["predicate"]: p for p in self.kb2["predicates"]}
        self._bind_index = {b["predicate"]: b for b in self.kb2["bindings_schema"]}
        self._token_idx = {b["predicate"]: _token_index(b) for b in self.kb2["bindings_schema"]
                           if b.get("permitted_values")}
        self._kb_supplied = {x["predicate"] for x in self.kb2.get("kb_supplied_inputs") or []}
        self.history: list[dict] = []
        self.previous_goal: dict | None = None
        self._prompt_tokens: dict[str, int] = {}
        self.last_prompt_eval_count: int | None = None

    def _full_prompt_tokens(self, system: str):
        """Untruncated token count of `system` + the shortest user turn, measured
        once per prompt with a one-token generation at a large context. Returns an
        int, or an F4 failure dict if Ollama cannot be reached."""
        if system in self._prompt_tokens:
            return self._prompt_tokens[system]
        payload = {"model": self.model,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": "Query: "}],
                   "stream": False,
                   # unload afterwards so parse calls run on a NUM_CTX runner,
                   # not on this large-context one (Ollama would reuse it)
                   "keep_alive": 0,
                   "options": {"temperature": 0.0, "num_ctx": _PROBE_CTX, "num_predict": 1}}
        req = urllib.request.Request(f"{self.url}/api/chat",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                n = json.loads(resp.read().decode("utf-8")).get("prompt_eval_count")
        except (urllib.error.URLError, TimeoutError) as e:
            return {"status": "failure", "type": "F4",
                    "reason": f"ollama transport error (prompt-length probe): {e}"}
        self._prompt_tokens[system] = int(n or 0)
        return self._prompt_tokens[system]

    def reset(self) -> None:
        self.history = []
        self.previous_goal = None

    @staticmethod
    def _is_followup(query: str) -> bool:
        q = query.strip().lower()
        prefixes = ("what if", "and if", "and what if", "what about", "now if",
                    "now what if", "now suppose", "now what about", "and ", "but if")
        if any(q.startswith(p) for p in prefixes):
            return True
        if "instead" in q:
            return True
        return False

    def parse(self, query: str) -> dict:
        if self.previous_goal is not None and self._is_followup(query):
            system = self.followup_prompt
            user_content = (
                f"PREVIOUS GOAL: {json.dumps(self.previous_goal)}\n"
                f"NEW QUERY: {query}"
            )
        else:
            system = self.system_prompt
            user_content = f"Query: {query}"

        # truncation guard: learn the prompt's full token count once (probe at a
        # large context), and refuse rather than parse from a truncated prompt
        full = self._full_prompt_tokens(system)
        if isinstance(full, dict):
            return full
        if full >= NUM_CTX - 256:
            return {"status": "failure", "type": "F4",
                    "reason": f"prompt ({full} tokens) does not fit num_ctx {NUM_CTX}"}

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0, "num_ctx": NUM_CTX},
        }
        req = urllib.request.Request(
            f"{self.url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as e:
            return {"status": "failure", "type": "F4", "reason": f"ollama transport error: {e}"}

        self.last_prompt_eval_count = body.get("prompt_eval_count")
        if self.last_prompt_eval_count is not None and self.last_prompt_eval_count < full:
            return {"status": "failure", "type": "F4",
                    "reason": (f"prompt truncated by Ollama: {self.last_prompt_eval_count} "
                               f"of >= {full} tokens evaluated")}

        content = body.get("message", {}).get("content", "")
        try:
            raw_goal = json.loads(content)
        except json.JSONDecodeError as e:
            return {"status": "failure", "type": "F4", "reason": f"non-JSON response: {e}", "raw": content}

        coerced, err = self._coerce(raw_goal)
        if err is not None:
            return {"status": "failure", "type": "F4", "reason": err, "raw": raw_goal}

        err = self._validate(coerced)
        if err is not None:
            return {"status": "failure", "type": "F4", "reason": err, "raw": coerced}

        # numeric provenance: every number bound must be one the query states
        # (a follow-up may also keep the previous goal's values)
        err = numeric_provenance_violation(
            query, coerced, self.kb2,
            self.previous_goal if system is self.followup_prompt else None)
        if err is not None:
            return {"status": "failure", "type": "F4", "reason": err, "raw": coerced}

        # condition provenance: a condition binding needs a stating phrase (KB2
        # condition_phrases) or, for a specific numeric value, the number itself
        err = condition_provenance_violation(
            query, coerced, self.kb2,
            self.previous_goal if system is self.followup_prompt else None)
        if err is not None:
            return {"status": "failure", "type": "F4", "reason": err, "raw": coerced}

        self.history.append({"query": query, "goal": coerced})
        self.previous_goal = coerced
        return coerced

    def _coerce(self, goal) -> tuple[dict | None, str | None]:
        if not isinstance(goal, dict):
            return None, f"top-level must be an object, got {type(goal).__name__}"

        pred = goal.get("predicate")
        entry = self._pred_index.get(pred) if pred else None
        if entry is None:
            return None, f"predicate {pred!r} not in KB2"

        out = {
            "predicate": pred,
            "unit": _normalize_unit(goal.get("unit", entry["unit"])),
            "context": goal.get("context"),
            "bindings": {},
        }

        bindings_raw = goal.get("bindings", {})
        if not isinstance(bindings_raw, dict):
            return None, f"bindings must be an object, got {type(bindings_raw).__name__}"

        for bname, bval in bindings_raw.items():
            bentry = self._bind_index.get(bname)
            if bentry is None:
                if bname in self._kb_supplied:
                    return None, (f"binding {bname!r} is supplied by the KB, "
                                  f"not stated in the query")
                return None, f"binding {bname!r} not in KB2 bindings_schema"
            coerced_b = self._coerce_one_binding(bval, bentry)
            if coerced_b is None:
                return None, f"could not coerce binding {bname!r}: {bval!r}"
            out["bindings"][bname] = coerced_b
        return out, None

    def _coerce_one_binding(self, bval, bentry: dict) -> dict | None:
        """Coerce one binding to {"value", "unit"} per the KB's declared type.

        `value_type` is declared per binding in KB2:
          "number"  (default) a JSON number, or a numeric string, optionally with
                    a trailing unit ("5 kg")
          "token"   a bare string from a closed vocabulary; when the KB lists
                    `permitted_values`, anything outside it is rejected here so
                    an invented token becomes a parser-stage F4 rather than
                    reaching the engine
          "boolean" a stated presence
        Nothing here is domain-specific: the behaviour follows the declaration.
        """
        target_unit = bentry["unit"]
        vtype = bentry.get("value_type", "number")
        allowed = bentry.get("permitted_values")

        if isinstance(bval, dict) and "value" in bval:
            v = bval["value"]
            u = _normalize_unit(bval.get("unit", target_unit))
        else:
            v, u = bval, target_unit

        if vtype == "boolean":
            if isinstance(v, bool):
                return {"value": v, "unit": target_unit}
            if isinstance(v, str) and v.strip().lower() in ("true", "yes", "present"):
                return {"value": True, "unit": target_unit}
            if isinstance(v, str) and v.strip().lower() in ("false", "no", "absent"):
                return {"value": False, "unit": target_unit}
            return None

        if vtype == "token":
            if isinstance(v, bool) or not isinstance(v, str):
                return None
            tok = v.strip()
            if not tok:
                return None
            if allowed is not None and tok not in allowed:
                # a case/format variant, or a name KB2 records for a token
                tok = self._token_idx.get(bentry["predicate"], {}).get(_name_key(tok))
                if tok is None:
                    return None
            return {"value": tok, "unit": target_unit}

        # numeric (the original behaviour)
        if isinstance(v, str):
            m = _NUM_UNIT_RE.match(v)
            if m:
                value = float(m.group(1))
                unit = _normalize_unit(m.group(2).strip()) or u or target_unit
                return {"value": value, "unit": unit}
            try:
                v = float(v)
            except ValueError:
                return None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return {"value": v, "unit": u if u else target_unit}

    def _validate(self, goal: dict) -> str | None:
        for k in ("predicate", "unit", "context", "bindings"):
            if k not in goal or goal[k] is None:
                return f"missing required field: {k}"
        entry = self._pred_index[goal["predicate"]]
        if goal["unit"] != entry["unit"]:
            return f"unit {goal['unit']!r} != {entry['unit']!r} for predicate {goal['predicate']!r}"
        if goal["context"] not in entry["contexts"]:
            return (
                f"context {goal['context']!r} not in {entry['contexts']!r} "
                f"for predicate {goal['predicate']!r}"
            )
        for bname, bval in goal["bindings"].items():
            bentry = self._bind_index[bname]
            if bval.get("unit") != bentry["unit"]:
                return f"binding {bname!r} unit {bval.get('unit')!r} != {bentry['unit']!r}"
        return nontrivial_violation(goal, self.kb2.get("predicate_aliases") or {})


def nontrivial_violation(goal: dict, aliases: dict) -> str | None:
    """Non-triviality: a goal state may not bind the quantity it asks for.

    The engine satisfies a goal from a binding before it tries any rule, so a goal
    state binding its own predicate -- or a name the KB declares as an alias the
    engine would accept for it -- is "answered" by echoing a number from the query.
    That is rejected here, at parse time, as an F4. `aliases` is the KB's
    `predicate_aliases` map (goal predicate -> names that satisfy it), read with
    the same direction the engine reads it.
    """
    pred = goal.get("predicate")
    bindings = goal.get("bindings") or {}
    if pred in bindings or any(a in bindings for a in aliases.get(pred, ())):
        return "goal state binds the quantity it asks for"
    return None


# --------------------------------------------------------------------------
# Numeric provenance (pre-freeze Part D). Every numeric binding must be a
# number the query states, up to a standard unit conversion. Condition values
# a wording rule binds from words (KB2 "stated_condition_values", e.g.
# initial_velocity 0 for "from rest") and non-numeric values are exempt.
# --------------------------------------------------------------------------
_NUM = re.compile(
    r"(?<![\w.])[-\u2212]?(?:\d+(?:,\d{3})*(?:\.\d*)?|\.\d+)"
    r"(?:\s*(?:[eE]|[x\u00d7*]\s*10\s*\^?\s*|\s*times\s+10\s+to\s+the\s+)"
    r"(?:\(?\s*[-+\u2212]?\s*\d+\s*\)?))?"
)
_POW10_WORD = re.compile(r"10\s*(?:\^|to the|\*\*)\s*\(?\s*([-+\u2212]?\s*\d+)\s*\)?", re.I)
_CONVERSIONS = [1.0, 60.0, 3600.0, 1 / 60.0, 1 / 3600.0, 1 / 3.6, 3.6,
                3.141592653589793 / 180.0, 180.0 / 3.141592653589793]


def _query_numbers(text: str) -> list[float]:
    """Every number written in the query, including scientific notation
    ('6.6e-34', '1.2 x 10^5', '10 to the -34')."""
    out: list[float] = []
    t = text.replace("\u2212", "-")
    for m in _NUM.finditer(t):
        s = m.group(0).replace(",", "").replace(" ", "")
        mant = re.match(r"-?(?:\d+(?:\.\d*)?|\.\d+)", s)
        if not mant:
            continue
        v = float(mant.group(0))
        exp = re.search(r"(?:[eE]|10\^?|10tothe|times10tothe)\(?([-+]?\d+)\)?$", s)
        if exp and s != mant.group(0):
            v *= 10.0 ** int(exp.group(1))
        out.append(v)
    for m in _POW10_WORD.finditer(t):          # bare '10 to the -34', '10^5'
        out.append(10.0 ** int(m.group(1).replace(" ", "")))
    return out


def _rounding_tolerance(value: float) -> float:
    """Half a unit in the last written digit of `value`, if it carries at least
    3 significant digits (a converted value the model rounded, e.g. 30 deg ->
    0.5236 rad); otherwise 0."""
    s = f"{value:.12g}"
    exp = 0
    if "e" in s:
        s, e = s.split("e")
        exp = int(e)
    dec = len(s.split(".")[1]) if "." in s else 0
    sig = len(s.replace("-", "").replace(".", "").lstrip("0"))
    return 0.5 * 10.0 ** (exp - dec) if sig >= 3 else 0.0


def _matches_stated(value: float, stated: list[float]) -> bool:
    tol_round = _rounding_tolerance(value)
    for x in stated:
        for c in _CONVERSIONS:
            base = x * c
            for k in range(-12, 13):            # powers of ten (prefixes, g/kg, L/m3, %)
                y = base * 10.0 ** k
                if abs(value - y) <= 1e-9 * max(1.0, abs(y)) or \
                        (y != 0 and abs(value / y - 1) < 1e-6):
                    return True
                if c != 1.0 and abs(value - y) <= tol_round:
                    return True
    return False


def numeric_provenance_violation(query: str, goal: dict, kb2: dict,
                                 previous_goal: dict | None = None) -> str | None:
    exempt = kb2.get("stated_condition_values") or {}
    stated = _query_numbers(query)
    if previous_goal:                            # a follow-up may keep earlier values
        for b in (previous_goal.get("bindings") or {}).values():
            if isinstance(b.get("value"), (int, float)) and not isinstance(b.get("value"), bool):
                stated.append(float(b["value"]))
    for name, b in (goal.get("bindings") or {}).items():
        v = b.get("value") if isinstance(b, dict) else b
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue                             # categorical / label / trait: wording rules
        if name in exempt and v == exempt[name]:
            continue                             # condition bound from words
        if not _matches_stated(float(v), stated):
            return "bound value not stated in the query"
    return None


# --------------------------------------------------------------------------
# Condition provenance (2026-11-23, change 2C). KB2 `condition_phrases` lists,
# per condition binding and value, the phrases that state it. A condition
# binding is kept only if the query states it: a listed phrase matches, or --
# for a specific numeric value -- the number itself is written in the query.
# Value "any" (net_force, net_work, net_impulse) needs the phrase whatever the
# number. A follow-up may keep a condition the previous goal already held.
# --------------------------------------------------------------------------
def _same_value(spec, v) -> bool:
    if isinstance(spec, (int, float)) and isinstance(v, (int, float)) \
            and not isinstance(v, bool):
        return abs(float(spec) - float(v)) <= 1e-9
    return str(spec).casefold() == str(v).casefold()


def condition_provenance_violation(query: str, goal: dict, kb2: dict,
                                   previous_goal: dict | None = None) -> str | None:
    phrases = kb2.get("condition_phrases") or {}
    prev = (previous_goal or {}).get("bindings") or {}
    for name, b in (goal.get("bindings") or {}).items():
        specs = phrases.get(name)
        if not specs:
            continue
        v = b.get("value") if isinstance(b, dict) else b
        if name in prev and _same_value(prev[name].get("value"), v):
            continue
        hit = [x for x in specs if x["value"] == "any" or _same_value(x["value"], v)]
        if not hit:
            continue                  # not a condition value (e.g. v0 = 5 m/s): numeric rule
        if any(re.search(rx, query, re.I) for x in hit for rx in x["phrases"]):
            continue
        if all(x["value"] != "any" for x in hit) and isinstance(v, (int, float)) \
                and not isinstance(v, bool):
            nums = _query_numbers(query)
            # zero is matched literally: scaled by 10^-k, any stated number comes
            # within _matches_stated's absolute tolerance of 0
            if (v == 0 and any(x == 0 for x in nums)) or \
                    (v != 0 and _matches_stated(float(v), nums)):
                continue
        return f"condition not stated in the query: {name} = {v!r}"
    return None
