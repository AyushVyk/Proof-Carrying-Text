"""The two baselines PCT is compared against: RAG and the raw model.

RAG builds a FAISS index over EVERY content type in a domain's KB1 --
primitives, rules (with their guards), and every data entry (biology taxa,
chemistry species and reactions) -- each serialized as one readable chunk,
embedded with sentence-transformers/all-MiniLM-L6-v2. At query time it retrieves
the top-5 chunks and gives them to the model as context.

The raw baseline sends the query to the same model with no context.

Neither baseline holds domain text of its own. Both system prompts come from the
domain's KB2 (`baseline_prompts`): `rag_system` is RAG's system prompt;
`raw_system` is the raw baseline's, and null means no system message at all --
which is what the physics raw baseline has always sent.

Physics equivalence: physics has no guards and no data blocks, so its 55 chunks
are produced by exactly the same text templates as before, and its KB2 prompt is
the former hardcoded RAG prompt verbatim. tools/eval/verify_baseline_equivalence.py
proves both, plus identical payloads and identical top-5 retrieval for all 300
stored physics queries, without calling the model.

Direct dependencies: sentence-transformers, faiss-cpu, urllib (stdlib).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

import faiss  # type: ignore[import-untyped]
import numpy as np
from sentence_transformers import SentenceTransformer

DEFAULT_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_GEN_MODEL = "mistral:7b-instruct-q4_K_M"
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_TOP_K = 5
GEN_OPTIONS = {"temperature": 0.0}

# KB1 keys that are engine configuration, not knowledge to retrieve
_NON_DATA_KEYS = {"rules", "primitives", "context_transitions", "predicate_aliases"}


def baseline_prompts(kb2: dict) -> dict:
    """{"rag_system": str, "raw_system": str | None} from a domain's KB2."""
    bp = kb2.get("baseline_prompts")
    if not bp or "rag_system" not in bp or "raw_system" not in bp:
        raise ValueError("KB2 has no `baseline_prompts` block with rag_system and "
                         "raw_system; the baselines hold no domain text of their own")
    return {"rag_system": bp["rag_system"], "raw_system": bp["raw_system"]}


# ---------------------------------------------------------------------------
# chunking: one readable chunk per KB entry
# ---------------------------------------------------------------------------
def _primitive_chunk(p: dict) -> dict:
    text = (
        f"{p['id']}: {p['predicate']} in context {p.get('context', '-')} "
        f"has value {p['value']} {p['unit']}. Source: {p.get('source', '')}"
    )
    return {"id": p["id"], "kind": "primitive", "text": text}


def _rule_chunk(r: dict) -> dict:
    inp_list = ", ".join(
        f"{i['name']} ({i['predicate']}, {i['unit']})" for i in r["inputs"]
    )
    guard = f" The rule applies only when: {r['guard']}." if "guard" in r else ""
    text = (
        f"{r['id']}: to compute {r['output']['predicate']} in context "
        f"{r.get('context', '-')}, apply formula {r['formula']} using inputs "
        f"{inp_list}.{guard} Proof: {r.get('proof', '')}"
    )
    return {"id": r["id"], "kind": "rule", "text": text}


def _taxon_chunk(key: str, t: dict) -> dict:
    head = f"{key}: a {t['rank']} (taxonomic level {t['level']})"
    if t.get("parent"):
        head += f", member of {t['parent']}"
    if t.get("common") or t.get("scientific"):
        head += f"; common name {t.get('common', '-')}, scientific name {t.get('scientific', '-')}"
    head += "."
    if t["discriminator"] == "traits":
        body = f" Members of {key} have: {', '.join(t['required_traits'])}."
    elif t["discriminator"] == "envelope":
        body = (f" Head-body length {t['body_min']}-{t['body_max']} cm;"
                f" geographic range: {', '.join(t['regions'])};"
                f" habitats: {', '.join(t['habitats'])}.")
    else:
        body = ""
    return {"id": key, "kind": "taxon", "text": f"{head}{body} Source: {t.get('source', '')}"}


def _species_chunk(formula: str, s: dict) -> dict:
    comp = ", ".join(f"{el} {n}" for el, n in s.get("composition", {}).items())
    text = (f"{formula}: chemical species with molar mass {s['molar_mass']} "
            f"{s.get('unit', 'g/mol')}; composition {comp}.")
    if s.get("source"):
        text += f" Source: {s['source']}"
    return {"id": formula, "kind": "species", "text": text}


def _reaction_chunk(rid: str, r: dict) -> dict:
    lhs = ", ".join(f"{sp} {c}" for sp, c in r.get("reactants", {}).items())
    rhs = ", ".join(f"{sp} {c}" for sp, c in r.get("products", {}).items())
    text = (f"{rid}: balanced equation {r['equation']}. Reactant coefficients: {lhs}. "
            f"Product coefficients: {rhs}. Source: {r.get('source', '')}")
    return {"id": rid, "kind": "reaction", "text": text}


_DATA_SERIALIZERS = {"taxa": _taxon_chunk, "species": _species_chunk,
                     "reactions": _reaction_chunk}


def _kb_to_chunks(kb: dict) -> list[dict]:
    chunks = [_primitive_chunk(p) for p in kb.get("primitives", [])]
    chunks += [_rule_chunk(r) for r in kb.get("rules", [])]
    for block, records in kb.items():
        if block in _NON_DATA_KEYS or block.startswith("_") or not isinstance(records, dict):
            continue
        ser = _DATA_SERIALIZERS.get(block)
        for key, rec in records.items():
            if ser is not None:
                chunks.append(ser(key, rec))
            else:   # a data block this module has no template for: still indexed
                chunks.append({"id": f"{block}:{key}", "kind": "data",
                               "text": f"{block} {key}: {json.dumps(rec, ensure_ascii=False)}"})
    return chunks


# ---------------------------------------------------------------------------
# model calls
# ---------------------------------------------------------------------------
def _chat(payload: dict, url: str) -> str:
    req = urllib.request.Request(
        f"{url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body.get("message", {}).get("content", "")
    except (urllib.error.URLError, TimeoutError) as e:
        return f"(transport error: {e})"


def raw_payload(query: str, system_prompt: str | None,
                model: str = DEFAULT_GEN_MODEL) -> dict:
    """The raw baseline's request. With no system prompt this is exactly the
    physics raw baseline's payload."""
    messages = [{"role": "user", "content": query}]
    if system_prompt is not None:
        messages = [{"role": "system", "content": system_prompt}] + messages
    return {"model": model, "messages": messages, "stream": False,
            "options": dict(GEN_OPTIONS)}


def raw_answer(query: str, system_prompt: str | None,
               model: str = DEFAULT_GEN_MODEL, url: str = DEFAULT_OLLAMA_URL) -> dict:
    t0 = time.time()
    content = _chat(raw_payload(query, system_prompt, model), url.rstrip("/"))
    return {"content": content, "elapsed": time.time() - t0}


class RAG:
    def __init__(
        self,
        kb: dict,
        system_prompt: str,
        embed_model_name: str = DEFAULT_EMBED_MODEL,
        gen_model: str = DEFAULT_GEN_MODEL,
        ollama_url: str = DEFAULT_OLLAMA_URL,
        top_k: int = DEFAULT_TOP_K,
    ) -> None:
        self.system_prompt = system_prompt
        self.gen_model = gen_model
        self.ollama_url = ollama_url.rstrip("/")
        self.top_k = top_k

        self.chunks = _kb_to_chunks(kb)
        self.embed_model = SentenceTransformer(embed_model_name)
        embeds = self.embed_model.encode(
            [c["text"] for c in self.chunks],
            convert_to_numpy=True,
            normalize_embeddings=True,
        ).astype(np.float32)
        self.index = faiss.IndexFlatIP(embeds.shape[1])
        self.index.add(embeds)

    def retrieve(self, query: str) -> list[dict]:
        q_emb = self.embed_model.encode(
            [query], convert_to_numpy=True, normalize_embeddings=True
        ).astype(np.float32)
        D, I = self.index.search(q_emb, self.top_k)
        out = []
        for rank, (idx, score) in enumerate(zip(I[0], D[0]), 1):
            c = self.chunks[idx]
            out.append({"rank": rank, "score": float(score), "id": c["id"], "text": c["text"]})
        return out

    def payload(self, query: str, retrieved: list[dict]) -> dict:
        ctx = "\n".join(f"- {r['text']}" for r in retrieved)
        user_msg = f"Context:\n{ctx}\n\nQuestion: {query}"
        return {
            "model": self.gen_model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "stream": False,
            "options": dict(GEN_OPTIONS),
        }

    def answer(self, query: str) -> dict:
        retrieved = self.retrieve(query)
        content = _chat(self.payload(query, retrieved), self.ollama_url)
        return {"content": content, "retrieved": retrieved}
