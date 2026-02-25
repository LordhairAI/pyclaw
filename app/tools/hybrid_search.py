#!/usr/bin/env python3
"""
Hybrid retrieval using:
1) Vector search: PGVector from langchain_postgres
2) Text search: PostgreSQL full-text search on table `text_search_collection`

Final score:
  final_score = 0.7 * vector_score + 0.3 * text_score
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Dict, List, Tuple

import psycopg
from psycopg import sql

from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector


DEFAULT_VECTOR_WEIGHT = 0.7
DEFAULT_TEXT_WEIGHT = 0.3
DEFAULT_DB_NAME = "openxbot"
DEFAULT_TEXT_TABLE = "text_search_collection"
DEFAULT_VECTOR_COLLECTION = "text_search_collection"


@dataclass
class SearchResult:
    doc_id: str
    score: float
    vector_score: float
    text_score: float
    text: str


def normalize_weights(vector_weight: float, text_weight: float) -> Tuple[float, float]:
    vw = max(0.0, min(1.0, vector_weight))
    tw = max(0.0, min(1.0, text_weight))
    total = vw + tw
    if total <= 0:
        return DEFAULT_VECTOR_WEIGHT, DEFAULT_TEXT_WEIGHT
    return vw / total, tw / total


def rank_to_score_by_order(items: List[Tuple[str, float, str]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for rank, (doc_id, _, _) in enumerate(items):
        out[doc_id] = 1.0 / (1.0 + rank)
    return out


def detect_text_column(conn: psycopg.Connection, table: str) -> str:
    candidates = ["text", "content", "document", "body", "chunk_text"]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        )
        found = {row[0] for row in cur.fetchall()}
    for name in candidates:
        if name in found:
            return name
    raise RuntimeError(
        f"Table '{table}' missing a text column. Tried: {', '.join(candidates)}."
    )


def text_search_postgres(
    conn: psycopg.Connection, query: str, top_k: int, table: str
) -> List[Tuple[str, float, str]]:
    text_col = detect_text_column(conn, table)
    stmt = sql.SQL(
        """
        SELECT
          id::text AS doc_id,
          {text_col}::text AS text,
          ts_rank_cd(
            to_tsvector('simple', {text_col}::text),
            plainto_tsquery('simple', %s)
          ) AS rank
        FROM {table}
        WHERE to_tsvector('simple', {text_col}::text) @@ plainto_tsquery('simple', %s)
        ORDER BY rank DESC, id::text ASC
        LIMIT %s
        """
    ).format(
        table=sql.Identifier(table),
        text_col=sql.Identifier(text_col),
    )

    with conn.cursor() as cur:
        cur.execute(stmt, (query, query, top_k))
        rows = cur.fetchall()

    return [(str(r[0]), float(r[2]), str(r[1])) for r in rows]


def vector_search_pgvector(
    query: str,
    top_k: int,
    collection_name: str,
    langchain_connection: str,
    embedding_model: str,
) -> List[Tuple[str, float, str]]:
    embeddings = OpenAIEmbeddings(model=embedding_model)
    store = PGVector(
        embeddings=embeddings,
        collection_name=collection_name,
        connection=langchain_connection,
    )
    docs_with_scores = store.similarity_search_with_score(query=query, k=top_k)

    out: List[Tuple[str, float, str]] = []
    for doc, distance in docs_with_scores:
        metadata = doc.metadata or {}
        doc_id = str(
            metadata.get("doc_id")
            or metadata.get("id")
            or metadata.get("source")
            or f"vector:{len(out)}"
        )
        # PGVector returns distance for similarity_search_with_score.
        similarity = 1.0 / (1.0 + max(float(distance), 0.0))
        out.append((doc_id, similarity, doc.page_content))
    return out


def hybrid_search(
    query: str,
    *,
    db_dsn: str,
    langchain_connection: str,
    vector_collection: str,
    text_table: str,
    embedding_model: str,
    vector_weight: float = DEFAULT_VECTOR_WEIGHT,
    text_weight: float = DEFAULT_TEXT_WEIGHT,
    top_k: int = 6,
    candidate_multiplier: int = 4,
) -> List[SearchResult]:
    vw, tw = normalize_weights(vector_weight, text_weight)
    candidates = max(1, top_k * max(1, candidate_multiplier))

    vector_rows = vector_search_pgvector(
        query=query,
        top_k=candidates,
        collection_name=vector_collection,
        langchain_connection=langchain_connection,
        embedding_model=embedding_model,
    )
    text_by_doc: Dict[str, str] = {}
    with psycopg.connect(db_dsn) as conn:
        text_rows = text_search_postgres(conn, query=query, top_k=candidates, table=text_table)

    vector_scores = rank_to_score_by_order(vector_rows)
    text_scores = rank_to_score_by_order(text_rows)

    for doc_id, _, text in vector_rows:
        text_by_doc.setdefault(doc_id, text)
    for doc_id, _, text in text_rows:
        text_by_doc.setdefault(doc_id, text)

    all_ids = set(vector_scores) | set(text_scores)
    merged: List[SearchResult] = []
    for doc_id in all_ids:
        vector_score = vector_scores.get(doc_id, 0.0)
        text_score = text_scores.get(doc_id, 0.0)
        score = vw * vector_score + tw * text_score
        merged.append(
            SearchResult(
                doc_id=doc_id,
                score=score,
                vector_score=vector_score,
                text_score=text_score,
                text=text_by_doc.get(doc_id, ""),
            )
        )

    merged.sort(key=lambda r: r.score, reverse=True)
    return merged[:top_k]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hybrid search with PGVector + PostgreSQL FTS.")
    parser.add_argument("--query", required=True, help="Search query.")
    parser.add_argument("--top-k", type=int, default=6, help="Final top-k results.")
    parser.add_argument("--vector-weight", type=float, default=0.7, help="Vector score weight.")
    parser.add_argument("--text-weight", type=float, default=0.3, help="Text score weight.")
    parser.add_argument(
        "--db-dsn",
        default=os.getenv("DB_DSN", f"dbname={DEFAULT_DB_NAME}"),
        help="psycopg DSN for text search (default: dbname=openxbot).",
    )
    parser.add_argument(
        "--langchain-connection",
        default=os.getenv(
            "LANGCHAIN_PG_CONNECTION",
            "postgresql+psycopg://postgres:postgres@localhost:5432/openxbot",
        ),
        help="SQLAlchemy-style DSN for langchain_postgres PGVector.",
    )
    parser.add_argument(
        "--vector-collection",
        default=os.getenv("VECTOR_COLLECTION", DEFAULT_VECTOR_COLLECTION),
        help="PGVector collection name.",
    )
    parser.add_argument(
        "--text-table",
        default=os.getenv("TEXT_TABLE", DEFAULT_TEXT_TABLE),
        help="PostgreSQL text search table.",
    )
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        help="Embedding model for PGVector query embedding.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = hybrid_search(
        query=args.query,
        db_dsn=args.db_dsn,
        langchain_connection=args.langchain_connection,
        vector_collection=args.vector_collection,
        text_table=args.text_table,
        embedding_model=args.embedding_model,
        vector_weight=args.vector_weight,
        text_weight=args.text_weight,
        top_k=max(1, args.top_k),
    )

    print(f"query: {args.query}\n")
    for idx, r in enumerate(results, start=1):
        print(
            f"{idx}. {r.doc_id}\n"
            f"   score={r.score:.4f} (vector={r.vector_score:.4f}, text={r.text_score:.4f})\n"
            f"   {r.text[:240]}\n"
        )


if __name__ == "__main__":
    main()
