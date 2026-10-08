"""
In-memory TF-IDF retrieval - pgvector mock for POC.

Implements retrieval.py per P1 fix: replaces substring matching with
TF-IDF cosine similarity over FQN + description corpus. For prod, swap
with pgvector + text-embedding-3-small; interface stays identical.

No model download, deterministic, fast for 126 docs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from dsxlineage.estate.ir import EstateIR


@dataclass
class RetrievalResult:
    fqn: str
    score: float
    doc: str


class EstateRetriever:
    """TF-IDF retriever over estate nodes.

    Corpus: each FQN + its type + description + adjacent edge context.
    Query: natural language question -> top-k FQNs by cosine.
    """

    def __init__(self, ir: EstateIR):
        self.ir = ir
        self.fqns: list[str] = []
        self.docs: list[str] = []
        self._build_corpus()
        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=5000)
        # Handle empty corpus
        if self.docs:
            self.doc_vectors = self.vectorizer.fit_transform(self.docs)
        else:
            self.doc_vectors = None

    def _build_corpus(self):
        # Tables
        for t in self.ir.tables:
            cols = " ".join(c.name for c in t.columns[:5])
            self.fqns.append(t.fqn)
            self.docs.append(f"{t.fqn} table {t.system} columns {cols}")
        # Views - include source_fqns for retrieval
        for v in self.ir.views:
            srcs = " ".join(v.source_fqns[:3])
            self.fqns.append(v.fqn)
            self.docs.append(f"{v.fqn} view {v.complexity} sources {srcs} sql {v.sql[:200]}")
        # Procedures
        for p in self.ir.procedures:
            reads = " ".join(p.reads[:3])
            writes = " ".join(p.writes[:3])
            self.fqns.append(p.fqn)
            self.docs.append(f"{p.fqn} procedure {p.language} {p.complexity} reads {reads} writes {writes} dynamic={p.has_dynamic} cursor={p.has_cursor}")
        # Dashboards
        for d in self.ir.dashboards:
            self.fqns.append(d.fqn)
            self.docs.append(f"{d.fqn} dashboard {d.tool} source {d.source_fqn} {d.description}")
        # ETL / Schedules
        for j in self.ir.etl_jobs:
            self.fqns.append(j.fqn)
            self.docs.append(f"{j.fqn} etl {j.dialect} source {j.source_fqn} target {j.target_fqn}")
        for s in self.ir.schedules:
            deps = " ".join(s.depends_on[:3])
            self.fqns.append(s.fqn)
            self.docs.append(f"{s.fqn} schedule {s.scheduler} deps {deps}")

    def retrieve(self, query: str, k: int = 5) -> list[RetrievalResult]:
        if not self.docs or self.doc_vectors is None:
            return []
        q_vec = self.vectorizer.transform([query])
        scores = cosine_similarity(q_vec, self.doc_vectors).flatten()
        # Get top-k
        top_idx = scores.argsort()[::-1][:k]
        results = []
        for idx in top_idx:
            if scores[idx] > 0.05:  # threshold
                results.append(RetrievalResult(fqn=self.fqns[idx], score=float(scores[idx]), doc=self.docs[idx]))
        return results

    def retrieve_with_scores(self, query: str, k: int = 5) -> list[tuple[str, float]]:
        return [(r.fqn, r.score) for r in self.retrieve(query, k=k)]
