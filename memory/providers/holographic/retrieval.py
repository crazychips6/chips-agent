"""Hybrid keyword/BM25 retrieval for the memory store.

Ported from KIK memory_agent.py — combines FTS5 full-text search with
Jaccard similarity reranking and trust-weighted scoring.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import MemoryStore

try:
    from . import holographic as hrr
except ImportError:
    import holographic as hrr  # type: ignore[no-redef]


class FactRetriever:
    """Multi-strategy fact retrieval with trust-weighted scoring."""

    def __init__(
        self,
        store: MemoryStore,
        temporal_decay_half_life: int = 0,
        fts_weight: float = 0.4,
        jaccard_weight: float = 0.3,
        hrr_weight: float = 0.3,
        hrr_dim: int = 1024,
    ):
        self.store = store
        self.half_life = temporal_decay_half_life
        self.hrr_dim = hrr_dim
        if hrr_weight > 0 and not hrr._HAS_NUMPY:
            fts_weight = 0.6
            jaccard_weight = 0.4
            hrr_weight = 0.0
        self.fts_weight = fts_weight
        self.jaccard_weight = jaccard_weight
        self.hrr_weight = hrr_weight

    def search(self, query: str, category: str | None = None,
               min_trust: float = 0.3, limit: int = 10) -> list[dict]:
        candidates = self._fts_candidates(query, category, min_trust, limit * 3)
        if not candidates:
            return []
        query_tokens = self._tokenize(query)
        scored = []
        for fact in candidates:
            content_tokens = self._tokenize(fact["content"])
            tag_tokens = self._tokenize(fact.get("tags", ""))
            all_tokens = content_tokens | tag_tokens
            jaccard = self._jaccard_similarity(query_tokens, all_tokens)
            fts_score = fact.get("fts_rank", 0.0)
            if self.hrr_weight > 0 and fact.get("hrr_vector"):
                fact_vec = hrr.bytes_to_phases(fact["hrr_vector"])
                query_vec = hrr.encode_text(query, self.hrr_dim)
                hrr_sim = (hrr.similarity(query_vec, fact_vec) + 1.0) / 2.0
            else:
                hrr_sim = 0.5
            relevance = (self.fts_weight * fts_score
                         + self.jaccard_weight * jaccard
                         + self.hrr_weight * hrr_sim)
            score = relevance * fact["trust_score"]
            if self.half_life > 0:
                score *= self._temporal_decay(fact.get("updated_at") or fact.get("created_at"))
            fact["score"] = score
            scored.append(fact)
        scored.sort(key=lambda x: x["score"], reverse=True)
        results = scored[:limit]
        for fact in results:
            fact.pop("hrr_vector", None)
        return results

    def probe(self, entity: str, category: str | None = None,
              limit: int = 10) -> list[dict]:
        if not hrr._HAS_NUMPY:
            return self.search(entity, category=category, limit=limit)
        conn = self.store._conn
        role_entity = hrr.encode_atom("__hrr_role_entity__", self.hrr_dim)
        entity_vec = hrr.encode_atom(entity.lower(), self.hrr_dim)
        probe_key = hrr.bind(entity_vec, role_entity)
        if category:
            bank_name = f"cat:{category}"
            bank_row = conn.execute(
                "SELECT vector FROM memory_banks WHERE bank_name = ?", (bank_name,)
            ).fetchone()
            if bank_row:
                bank_vec = hrr.bytes_to_phases(bank_row["vector"])
                extracted = hrr.unbind(bank_vec, probe_key)
                return self._score_facts_by_vector(extracted, category=category, limit=limit)
        where = "WHERE hrr_vector IS NOT NULL"
        params: list = []
        if category:
            where += " AND category = ?"
            params.append(category)
        rows = conn.execute(
            f"SELECT fact_id, content, category, tags, trust_score, "
            f"retrieval_count, helpful_count, created_at, updated_at, hrr_vector "
            f"FROM facts {where}", params,
        ).fetchall()
        if not rows:
            return self.search(entity, category=category, limit=limit)
        scored = []
        for row in rows:
            fact = dict(row)
            fact_vec = hrr.bytes_to_phases(fact.pop("hrr_vector"))
            residual = hrr.unbind(fact_vec, probe_key)
            role_content = hrr.encode_atom("__hrr_role_content__", self.hrr_dim)
            content_vec = hrr.bind(hrr.encode_text(fact["content"], self.hrr_dim), role_content)
            sim = hrr.similarity(residual, content_vec)
            fact["score"] = (sim + 1.0) / 2.0 * fact["trust_score"]
            scored.append(fact)
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def related(self, entity: str, category: str | None = None,
                limit: int = 10) -> list[dict]:
        if not hrr._HAS_NUMPY:
            return self.search(entity, category=category, limit=limit)
        conn = self.store._conn
        entity_vec = hrr.encode_atom(entity.lower(), self.hrr_dim)
        where = "WHERE hrr_vector IS NOT NULL"
        params: list = []
        if category:
            where += " AND category = ?"
            params.append(category)
        rows = conn.execute(
            f"SELECT fact_id, content, category, tags, trust_score, "
            f"retrieval_count, helpful_count, created_at, updated_at, hrr_vector "
            f"FROM facts {where}", params,
        ).fetchall()
        if not rows:
            return self.search(entity, category=category, limit=limit)
        scored = []
        for row in rows:
            fact = dict(row)
            fact_vec = hrr.bytes_to_phases(fact.pop("hrr_vector"))
            residual = hrr.unbind(fact_vec, entity_vec)
            role_entity = hrr.encode_atom("__hrr_role_entity__", self.hrr_dim)
            role_content = hrr.encode_atom("__hrr_role_content__", self.hrr_dim)
            entity_role_sim = hrr.similarity(residual, role_entity)
            content_role_sim = hrr.similarity(residual, role_content)
            best_sim = max(entity_role_sim, content_role_sim)
            fact["score"] = (best_sim + 1.0) / 2.0 * fact["trust_score"]
            scored.append(fact)
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def reason(self, entities: list[str], category: str | None = None,
               limit: int = 10) -> list[dict]:
        if not hrr._HAS_NUMPY or not entities:
            query = " ".join(entities)
            return self.search(query, category=category, limit=limit)
        conn = self.store._conn
        role_entity = hrr.encode_atom("__hrr_role_entity__", self.hrr_dim)
        entity_residuals = []
        for entity in entities:
            entity_vec = hrr.encode_atom(entity.lower(), self.hrr_dim)
            probe_key = hrr.bind(entity_vec, role_entity)
            entity_residuals.append(probe_key)
        where = "WHERE hrr_vector IS NOT NULL"
        params: list = []
        if category:
            where += " AND category = ?"
            params.append(category)
        rows = conn.execute(
            f"SELECT fact_id, content, category, tags, trust_score, "
            f"retrieval_count, helpful_count, created_at, updated_at, hrr_vector "
            f"FROM facts {where}", params,
        ).fetchall()
        if not rows:
            query = " ".join(entities)
            return self.search(query, category=category, limit=limit)
        role_content = hrr.encode_atom("__hrr_role_content__", self.hrr_dim)
        scored = []
        for row in rows:
            fact = dict(row)
            fact_vec = hrr.bytes_to_phases(fact.pop("hrr_vector"))
            entity_scores = []
            for probe_key in entity_residuals:
                residual = hrr.unbind(fact_vec, probe_key)
                sim = hrr.similarity(residual, role_content)
                entity_scores.append(sim)
            min_sim = min(entity_scores)
            fact["score"] = (min_sim + 1.0) / 2.0 * fact["trust_score"]
            scored.append(fact)
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def contradict(self, category: str | None = None,
                   threshold: float = 0.3, limit: int = 10) -> list[dict]:
        if not hrr._HAS_NUMPY:
            return []
        conn = self.store._conn
        where = "WHERE f.hrr_vector IS NOT NULL"
        params: list = []
        if category:
            where += " AND f.category = ?"
            params.append(category)
        rows = conn.execute(
            f"SELECT f.fact_id, f.content, f.category, f.tags, f.trust_score, "
            f"f.created_at, f.updated_at, f.hrr_vector FROM facts f {where}", params,
        ).fetchall()
        if len(rows) < 2:
            return []
        _MAX_CONTRADICT_FACTS = 500
        if len(rows) > _MAX_CONTRADICT_FACTS:
            rows = sorted(rows, key=lambda r: r["updated_at"] or r["created_at"], reverse=True)
            rows = rows[:_MAX_CONTRADICT_FACTS]
        fact_entities: dict[int, set[str]] = {}
        for row in rows:
            fid = row["fact_id"]
            entity_rows = conn.execute(
                "SELECT e.name FROM entities e JOIN fact_entities fe ON fe.entity_id = e.entity_id WHERE fe.fact_id = ?",
                (fid,),
            ).fetchall()
            fact_entities[fid] = {r["name"].lower() for r in entity_rows}
        facts = [dict(r) for r in rows]
        contradictions = []
        for i in range(len(facts)):
            for j in range(i + 1, len(facts)):
                f1, f2 = facts[i], facts[j]
                ents1 = fact_entities.get(f1["fact_id"], set())
                ents2 = fact_entities.get(f2["fact_id"], set())
                if not ents1 or not ents2:
                    continue
                entity_overlap = len(ents1 & ents2) / len(ents1 | ents2) if (ents1 | ents2) else 0.0
                if entity_overlap < 0.3:
                    continue
                v1 = hrr.bytes_to_phases(f1["hrr_vector"])
                v2 = hrr.bytes_to_phases(f2["hrr_vector"])
                content_sim = hrr.similarity(v1, v2)
                contradiction_score = entity_overlap * (1.0 - (content_sim + 1.0) / 2.0)
                if contradiction_score >= threshold:
                    f1_clean = {k: v for k, v in f1.items() if k != "hrr_vector"}
                    f2_clean = {k: v for k, v in f2.items() if k != "hrr_vector"}
                    contradictions.append({
                        "fact_a": f1_clean,
                        "fact_b": f2_clean,
                        "entity_overlap": round(entity_overlap, 3),
                        "content_similarity": round(content_sim, 3),
                        "contradiction_score": round(contradiction_score, 3),
                        "shared_entities": sorted(ents1 & ents2),
                    })
        contradictions.sort(key=lambda x: x["contradiction_score"], reverse=True)
        return contradictions[:limit]

    def _score_facts_by_vector(self, target_vec, category: str | None = None,
                                limit: int = 10) -> list[dict]:
        conn = self.store._conn
        where = "WHERE hrr_vector IS NOT NULL"
        params: list = []
        if category:
            where += " AND category = ?"
            params.append(category)
        rows = conn.execute(
            f"SELECT fact_id, content, category, tags, trust_score, "
            f"retrieval_count, helpful_count, created_at, updated_at, hrr_vector "
            f"FROM facts {where}", params,
        ).fetchall()
        scored = []
        for row in rows:
            fact = dict(row)
            fact_vec = hrr.bytes_to_phases(fact.pop("hrr_vector"))
            sim = hrr.similarity(target_vec, fact_vec)
            fact["score"] = (sim + 1.0) / 2.0 * fact["trust_score"]
            scored.append(fact)
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def _fts_candidates(self, query: str, category: str | None,
                        min_trust: float, limit: int) -> list[dict]:
        conn = self.store._conn
        params: list = []
        where_clauses = ["facts_fts MATCH ?"]
        params.append(query)
        if category:
            where_clauses.append("f.category = ?")
            params.append(category)
        where_clauses.append("f.trust_score >= ?")
        params.append(min_trust)
        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT f.*, facts_fts.rank as fts_rank_raw
            FROM facts_fts JOIN facts f ON f.fact_id = facts_fts.rowid
            WHERE {where_sql} ORDER BY facts_fts.rank LIMIT ?
        """
        params.append(limit)
        try:
            rows = conn.execute(sql, params).fetchall()
        except Exception:
            return []
        if not rows:
            return []
        raw_ranks = [abs(row["fts_rank_raw"]) for row in rows]
        max_rank = max(raw_ranks) if raw_ranks else 1.0
        max_rank = max(max_rank, 1e-6)
        results = []
        for row, raw_rank in zip(rows, raw_ranks):
            fact = dict(row)
            fact.pop("fts_rank_raw", None)
            fact["fts_rank"] = raw_rank / max_rank
            results.append(fact)
        return results

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        if not text:
            return set()
        tokens = set()
        for word in text.lower().split():
            cleaned = word.strip(".,;:!?\"'()[]{}#@<>")
            if cleaned:
                tokens.add(cleaned)
        return tokens

    @staticmethod
    def _jaccard_similarity(set_a: set, set_b: set) -> float:
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    def _temporal_decay(self, timestamp_str: str | None) -> float:
        if not self.half_life or not timestamp_str:
            return 1.0
        try:
            if isinstance(timestamp_str, str):
                ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            else:
                ts = timestamp_str
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
            if age_days < 0:
                return 1.0
            return math.pow(0.5, age_days / self.half_life)
        except (ValueError, TypeError):
            return 1.0
