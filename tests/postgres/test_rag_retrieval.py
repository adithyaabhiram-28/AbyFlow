
import uuid
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import Tenant, KnowledgeDocument, DocumentChunk
from app.services.rag.embeddings import generate_embedding
from app.services.rag.retrieval import (
    search_similar_chunks,
    RetrievalError,
    _search_postgres,
)


@pytest.fixture
def rag_tenants(postgres_app):
    """Create two isolated tenants for PostgreSQL RAG tests."""
    unique = uuid.uuid4().hex

    with postgres_app.app_context():
        tenant_a = Tenant(
            name=f"RAG Tenant A {unique}",
            slug=f"rag-tenant-a-{unique}",
        )
        tenant_b = Tenant(
            name=f"RAG Tenant B {unique}",
            slug=f"rag-tenant-b-{unique}",
        )

        db.session.add_all([tenant_a, tenant_b])
        db.session.commit()

        result = {
            "tenant_a": tenant_a.id,
            "tenant_b": tenant_b.id,
            "unique": unique,
        }

        yield result

        document_ids = [
            row[0]
            for row in db.session.query(KnowledgeDocument.id).filter(
                KnowledgeDocument.tenant_id.in_(
                    [tenant_a.id, tenant_b.id]
                )
                | KnowledgeDocument.slug.like(f"rag-test-%-{unique}")
            ).all()
        ]

        if document_ids:
            db.session.query(DocumentChunk).filter(
                DocumentChunk.document_id.in_(document_ids)
            ).delete(synchronize_session=False)

            db.session.query(KnowledgeDocument).filter(
                KnowledgeDocument.id.in_(document_ids)
            ).delete(synchronize_session=False)

        db.session.delete(tenant_a)
        db.session.delete(tenant_b)
        db.session.commit()


def _create_document(
    tenant_id,
    slug,
    content,
    chunk_tenant_id=None,
):
    """Create a document and one pgvector-backed chunk."""
    document = KnowledgeDocument(
        tenant_id=tenant_id,
        title=slug,
        slug=slug,
        category="test",
        version=1,
        is_active=True,
    )

    db.session.add(document)
    db.session.flush()

    chunk = DocumentChunk(
        document_id=document.id,
        tenant_id=chunk_tenant_id,
        chunk_index=0,
        content=content,
        embedding=generate_embedding(content),
        token_count=len(content.split()),
    )

    db.session.add(chunk)
    db.session.commit()

    return document


def test_postgres_native_cosine_retrieval(
    postgres_app,
    rag_tenants,
):
    """PostgreSQL executes native pgvector cosine-distance retrieval."""
    with postgres_app.app_context():
        content = "RAG PostgreSQL native vector retrieval test"
        slug = f"rag-test-native-{uuid.uuid4().hex}"

        _create_document(
            rag_tenants["tenant_a"],
            slug,
            content,
            rag_tenants["tenant_a"],
        )

        results = search_similar_chunks(
            content,
            tenant_id=rag_tenants["tenant_a"],
            top_k=5,
        )

        matching = [
            result for result in results
            if result["document_slug"] == slug
        ]

        assert matching
        assert matching[0]["content"] == content
        assert matching[0]["similarity_score"] == pytest.approx(
            1.0, abs=0.001
        )


def test_postgres_global_document_visibility(
    postgres_app,
    rag_tenants,
):
    """Both tenants can retrieve a global document."""
    with postgres_app.app_context():
        content = "Global RAG documentation visibility test"
        slug = f"rag-test-global-{rag_tenants['unique']}"

        _create_document(
            None,
            slug,
            content,
            None,
        )

        for tenant_id in (
            rag_tenants["tenant_a"],
            rag_tenants["tenant_b"],
        ):
            results = search_similar_chunks(
                content,
                tenant_id=tenant_id,
                top_k=10,
            )

            matching = [
                result for result in results
                if result["document_slug"] == slug
            ]

            assert matching, (
                f"Global document not found for tenant_id={tenant_id}. "
                f"Returned results: "
                f"{[(r['document_slug'], r['similarity_score']) for r in results]}"
            )


def test_postgres_private_document_isolation(
    postgres_app,
    rag_tenants,
):
    """A tenant cannot retrieve another tenant's private document."""
    with postgres_app.app_context():
        content = "Tenant B confidential PostgreSQL RAG document"
        slug = f"rag-test-private-{uuid.uuid4().hex}"

        _create_document(
            rag_tenants["tenant_b"],
            slug,
            content,
            rag_tenants["tenant_b"],
        )

        results = search_similar_chunks(
            content,
            tenant_id=rag_tenants["tenant_a"],
            top_k=10,
        )

        assert not any(
            result["document_slug"] == slug
            for result in results
        )


def test_postgres_rejects_mismatched_tenant_ownership(
    postgres_app,
    rag_tenants,
):
    """Mismatched document and chunk tenants cannot leak content."""
    with postgres_app.app_context():
        content = "Mismatched PostgreSQL tenant ownership secret"
        slug = f"rag-test-mismatch-{uuid.uuid4().hex}"

        _create_document(
            rag_tenants["tenant_b"],
            slug,
            content,
            rag_tenants["tenant_a"],
        )

        results = search_similar_chunks(
            content,
            tenant_id=rag_tenants["tenant_a"],
            top_k=10,
        )

        assert not any(
            result["document_slug"] == slug
            for result in results
        )


def test_postgres_retrieval_errors_are_not_silently_swallowed(
    postgres_app,
    rag_tenants,
):
    """A PostgreSQL query failure must propagate as RetrievalError."""
    with postgres_app.app_context():
        with patch(
            "app.services.rag.retrieval.db.session.query",
            side_effect=RuntimeError("Simulated database failure"),
        ):
            with pytest.raises(
                RetrievalError,
                match="PostgreSQL vector retrieval failed",
            ):
                _search_postgres(
                    query_vector=[0.0] * 1536,
                    tenant_id=rag_tenants["tenant_a"],
                    top_k=5,
                )