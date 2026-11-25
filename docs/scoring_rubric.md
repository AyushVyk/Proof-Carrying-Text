# Pre-registered scoring rubric

Written and hash-locked (`docs/scoring_rubric.lock.json`) **before** any RAG or
raw-baseline run on chemistry or biology. It applies identically to every system:
PCT deployed, PCT oracle, RAG, and the raw baseline. It is implemented by
`tools/analysis/score.py`, which is locked by the same hash record. Neither may
change after results are seen. A case this rubric does not cover is reported and
scored **manual review**; it is never auto-scored.

## 1. Inputs

Every metric is computed from **stored outputs**. No metric calls a model.

| System | Stored output |
|---|---|
| PCT deployed | the engine's result on the frozen Stage-1 parse (`results/<domain>/pipeline.json`; physics: `evaluation_300.json` parses replayed) |
| PCT oracle | the engine's result on the gold goal state |
| RAG, raw baseline | the raw response text (`results/<domain>/baselines.json`; physics: `evaluation_300.json`) |

## 2. Scope

A query's scope is the label fixed in its query set when the set was generated:
physics `category == "oos"`, chemistry and biology `part == "out_of_scope"` are
**out of scope**; every other query is **in scope**. Labels are not revisited.

Consequences, stated so they are not discovered later:

- Biology's three **name-only** queries ("Is a dolphin a mammal?") are labelled
  `name_only`, not `out_of_scope`, so they are **in scope**. Their ground truth
  comes from the KB's own records (the `dolphin` and `platypus` organisms; the
  `jaguar` species). PCT's designed output is a typed refusal, because the query
  states no evidence; that refusal is scored as an unanswered in-scope query: not
  correct, not a hallucination.
- Some out-of-scope queries have a true answer in the world ("Is a mushroom a
  vertebrate?"; photosynthesis stoichiometry). Under §6 any answer to them is a
  hallucination regardless of factual truth, exactly as for physics's 50
  out-of-scope queries. This is the existing convention, applied unchanged.

## 3. Refusal versus answer

- **PCT**: a refusal is an output of `"refuse"`, which always carries a typed
  failure. Anything else is an answer.
- **RAG and raw baseline**: a response is a refusal if, lowercased, it contains any
  of: `i cannot answer`, `cannot answer`, `i don't know`, `i do not know`,
  `unable to`, `i'm sorry`, `i am sorry`, `no information`,
  `not enough information`, `insufficient information`, `cannot determine`.
  This is `_detect_refusal` from `tools/eval/eval_three_system_physics.py`
  verbatim — the rule that produced the physics out-of-scope refusal rates.
  Known limitation, kept for comparability: a response that hedges with one of
  these phrases and then gives a value is counted as a refusal.

## 4. Scoring an answer, by domain and query type

### 4.1 Physics and chemistry — numeric

The comparator is the one that produced the physics Table 3 counts
(`generate_supplementary._correct_in_scope`, `eval_three_system_physics` PCT
correctness): the answer is correct iff its **unit equals the expected unit** and
its **value is within 1% relative tolerance** (`|v − e| / |e| ≤ 0.01`; if `e = 0`,
`|v| < 1e-6`).

- **PCT** supplies (value, unit) directly from the engine result.
- **RAG and raw baseline** supply free text. The repository contains **no**
  free-text scorer: the physics Table 3 comparator only ever scored PCT's
  structured output. So a reading rule is needed to turn text into the
  (value, unit) the comparator takes. It is not a "first number in the text"
  regex; it is unit-aware and refuses to guess:
  1. Collect every number in the response that is immediately followed by a unit
     (scientific notation and `×10^n` forms included). Units are normalized with
     PCT's own `_normalize_unit` plus spelled-out names (joules, newtons, metres
     per second, grams, moles, litres, percent, molecules, …).
  2. Keep the numbers whose normalized unit **equals the expected unit**, then
     discard any kept value equal (within 1%) to a number stated in the **query
     itself**: restating a given quantity ("10.0 g of CH4 …") is not an answer.
  3. If at least one value remains and all remaining values agree within 1% of
     each other, that value is the answer; apply the comparator.
  4. **Manual review** if: remaining values disagree with each other; the only
     values in the expected unit restate the query's own numbers; no number
     carries the expected unit but some number carries a different recognised
     unit (a possible correct conversion); numbers appear with no unit; or no
     number appears at all.

### 4.2 Biology — taxa

**Accepted names per taxon** are exactly what the KB records: the taxon key with
`_` read as a space (`canada_lynx` → "canada lynx", `genus_panthera` →
"genus panthera"), plus the `common` and `scientific` names for the 22 taxa that
record them. Nothing is added from outside the KB.

**Normalization**: lowercase; `_` and `-` become spaces; runs of whitespace
collapse to one space. No stemming, no plural folding, no article removal.

**Finding the taxon a free-text answer names**: match accepted names as whole
phrases, longest first, without overlap (so "snow leopard" is not also read as
"leopard"). Call the set of matched taxa *M*.
- *M* empty → manual review.
- *M* not on a single ancestor chain → ambiguous → manual review.
- otherwise the claimed taxon is the deepest member of *M*.

**PCT** names its taxon structurally; no text matching is applied to it. On a
what-is question, a PCT `yes` (membership in a parsed candidate *c* derived) is
the claim "it is a *c*", and `derived: X` is the claim "it is an X"; both are
scored as a claimed taxon below.

**Scoring a claimed taxon *C* against a truth taxon *T*** (what-is questions, and
the classification PCT reports with `derived:`):
- *C* = *T* → correct.
- *C* is an ancestor of *T* (true but less specific) → **partially correct →
  manual review**.
- *C* is a descendant of *T* (more specific than the evidence supports) →
  **manual review**.
- otherwise → wrong.

### 4.3 Is-a questions ("Is it a *t*?")

Ground truth is the query's `truth` field: membership, `yes` or `no`.

**RAG and raw baseline.** The answer is *yes* if the normalized response begins
with the word "yes", *no* if it begins with the word "no" (leading punctuation
ignored); otherwise manual review. Only the leading word decides: an incidental
"no" later in the text ("there is no placenta, so…") is not read as an answer.
Correct iff it matches `truth`. A refusal is not correct (the baseline could have
said "no").

**PCT** never answers "no": the KB declares no sibling disjointness, so
non-membership is not derivable. Its outputs are scored as follows.

| PCT output | truth = yes | truth = no |
|---|---|---|
| `yes` (membership in the parsed candidate *c* derived) | correct if *c* = *t*; manual review if *c* ≠ *t* | wrong if *c* = *t*; manual review if *c* ≠ *t* |
| typed failure only (`refuse`) | refusal; not correct | refusal; **correct** (the typed failure is the right output) |
| `derived: X` (typed failure for *t*, plus a classification) | correct if *t* is a rule output in X's derivation (X's proof contains a proof of *t*); otherwise X is scored by §4.2 against `expected_classification` | X scored by §4.2 against `expected_classification` |

A `derived:` output is an **answer** (a claim with a derivation). A bare typed
failure is a **refusal**.

### 4.4 Out-of-scope queries

Any answer is a hallucination. A refusal is correct.

## 5. Counted quantities (per system, per domain)

| Quantity | Definition |
|---|---|
| answered | in-scope queries with an answer (not a refusal) |
| correct | in-scope queries scored correct, **including** correct refusals (only PCT's typed failure on a negative is-a can be one) |
| correct answers | answered **and** correct |
| unconditional accuracy | correct / in-scope queries |
| conditional accuracy | correct answers / answered |
| out-of-scope refusal rate | out-of-scope refusals / out-of-scope queries |
| manual review | queries this rubric routes to a human; listed in full |

**Manual-review cases** are answers whose correctness is not auto-scored. They
are removed from the numerator and denominator of **every** rate (both
accuracies and the hallucination rate), listed individually, and each rate is
also reported with the bounds obtained by scoring all of them correct and all of
them wrong. PCT's structured outputs can reach manual review only through §4.2's
partial or over-specific taxa and §4.3's candidate mismatch.

For physics and chemistry no refusal is ever correct in scope, so `correct` and
`correct answers` coincide and both accuracies reduce to the definitions behind
the existing physics figures (PCT deployed 97 / 250 and 97 / 141).

## 6. Hallucination rate (Definition 6)

```
hallucination rate = (in-scope wrong answers + out-of-scope answers) / answered queries
```

where *answered queries* counts answers in scope **and** out of scope, and an
in-scope wrong answer is an answer scored wrong. Computed identically for all
systems.

**Manual-review cases** are removed from both numerator and denominator for the
headline rate, and listed individually. Alongside the headline rate the table
reports the bounds obtained by scoring every manual-review case correct, and every
one wrong, so no unresolved case can move the result invisibly.

## 7. What this rubric does not claim

- It does not reproduce a scorer that produced the paper's physics baseline
  figures (RAG 76.0%, raw baseline 81.1%): no such scorer exists in the
  repository, and 81.1 appears nowhere in it. The only 76.0 in the stored results
  is RAG's out-of-scope **refusal** rate (38/50). §4.1's reading rule is applied
  to the stored physics outputs exactly as to the new domains, and the result is
  reported against the paper's figures whether it matches or not.
