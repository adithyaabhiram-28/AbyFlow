
import math
import logging
from typing import List, Dict, Any

from sqlalchemy import and_, or_

from app.extensions import db
from app.models import KnowledgeDocument, DocumentChunk
from app.services.rag.embeddings import generate_embedding

logger = logging.getLogger(__name__)


class RetrievalError(RuntimeError):
    """Raised when the configured retrieval backend cannot operate."""


def _tenant_visibility_filter(model, tenant_id: int):
    """Allow only global records or records belonging to this tenant."""
    return or_(
        model.tenant_id.is_(None),
        model.tenant_id == tenant_id,
    )


def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Calculate cosine similarity between two float vectors."""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0

    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot / (norm1 * norm2)


def _visibility_filter(tenant_id: int):
    """Enforce document and chunk tenant consistency."""
    return or_(
        and_(
            KnowledgeDocument.tenant_id.is_(None),
            DocumentChunk.tenant_id.is_(None),
        ),
        and_(
            KnowledgeDocument.tenant_id == tenant_id,
            DocumentChunk.tenant_id == tenant_id,
        ),
        and_(
            KnowledgeDocument.tenant_id.is_(None),
            DocumentChunk.tenant_id == tenant_id,
        ),
    )


def _base_query(tenant_id: int):
    """Build the shared tenant-isolated document query."""
    return (
        db.session.query(DocumentChunk, KnowledgeDocument)
        .join(
            KnowledgeDocument,
            DocumentChunk.document_id == KnowledgeDocument.id,
        )
        .filter(
            KnowledgeDocument.is_active.is_(True),
            _tenant_visibility_filter(KnowledgeDocument, tenant_id),
            _tenant_visibility_filter(DocumentChunk, tenant_id),
            _visibility_filter(tenant_id),
        )
    )


def _format_match(chunk, doc, score):
    return {
        'document_title': doc.title,
        'document_slug': doc.slug,
        'category': doc.category,
        'chunk_index': chunk.chunk_index,
        'content': chunk.content,
        'similarity_score': round(score, 4),
        'is_tenant_private': chunk.tenant_id is not None,
    }


def _search_postgres(query_vector, tenant_id, top_k):
    """Perform native pgvector cosine-distance retrieval."""
    try:
        from pgvector.sqlalchemy import Vector  # noqa: F401
    except ImportError as exc:
        raise RetrievalError(
            "PostgreSQL retrieval requires the pgvector Python package."
        ) from exc

    try:
        results = (
            db.session.query(
                DocumentChunk,
                KnowledgeDocument,
                DocumentChunk.embedding.cosine_distance(
                    query_vector
                ).label('distance'),
            )
            .join(
                KnowledgeDocument,
                DocumentChunk.document_id == KnowledgeDocument.id,
            )
            .filter(
                KnowledgeDocument.is_active.is_(True),
                _tenant_visibility_filter(KnowledgeDocument, tenant_id),
                _tenant_visibility_filter(DocumentChunk, tenant_id),
                _visibility_filter(tenant_id),
            )
            .order_by('distance')
            .limit(top_k)
            .all()
        )
    except Exception as exc:
        logger.exception("Native PostgreSQL vector retrieval failed.")
        raise RetrievalError(
            "PostgreSQL vector retrieval failed."
        ) from exc

    matches = []

    for chunk, doc, distance in results:
        score = 1.0 - float(distance) if distance is not None else 0.0
        matches.append(_format_match(chunk, doc, score))

    return matches


def _search_sqlite(query_vector, tenant_id, top_k):
    """Perform in-memory cosine similarity retrieval for SQLite."""
    candidates = _base_query(tenant_id).all()

    scored_candidates = []

    for chunk, doc in candidates:
        chunk_embedding = chunk.embedding

        if isinstance(chunk_embedding, list):
            score = _cosine_similarity(query_vector, chunk_embedding)
        else:
            score = 0.0

        scored_candidates.append(
            _format_match(chunk, doc, score)
        )

    scored_candidates.sort(
        key=lambda item: item['similarity_score'],
        reverse=True,
    )

    return scored_candidates[:top_k]


def search_similar_chunks(
    query: str,
    tenant_id: int,
    top_k: int = 3,
) -> List[Dict[str, Any]]:
    """
    Search document chunks matching the query.

    PostgreSQL uses native pgvector retrieval.
    SQLite uses in-memory cosine similarity.

    No cross-backend fallback is performed.
    """
    if not query or not isinstance(query, str) or not query.strip():
        return []

    if tenant_id is None:
        raise ValueError("tenant_id is required for tenant-isolated retrieval.")

    top_k = max(1, min(top_k or 3, 10))
    query_vector = generate_embedding(query.strip(), task_type='RETRIEVAL_QUERY')

    dialect = db.session.get_bind().dialect.name

    if dialect == 'postgresql':
        return _search_postgres(query_vector, tenant_id, top_k)

    if dialect == 'sqlite':
        return _search_sqlite(query_vector, tenant_id, top_k)

    raise RetrievalError(
        f"Unsupported database dialect for RAG retrieval: {dialect}"
    )
