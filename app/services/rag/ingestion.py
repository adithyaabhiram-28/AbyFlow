import logging
from typing import Optional
from app.extensions import db
from app.models import KnowledgeDocument, DocumentChunk
from app.services.rag.chunking import chunk_text
from app.services.rag.embeddings import generate_embedding

logger = logging.getLogger(__name__)



def ingest_document(
    title: str,
    slug: str,
    content: str,
    category: str = 'policy',
    tenant_id: Optional[int] = None,
    version: int = 1
) -> KnowledgeDocument:
    """
    Ingest or update a document and its embedding chunks.

    All database changes are committed together.
    If any operation fails, the transaction is rolled back.

    tenant_id=None represents global platform documentation.
    """
    if not title or not slug or not content:
        raise ValueError("Title, slug, and content are required")

    title = title.strip()
    slug = slug.strip().lower()

    chunks = chunk_text(content, chunk_size=500, chunk_overlap=50)

    try:
        existing = KnowledgeDocument.query.filter_by(
            tenant_id=tenant_id,
            slug=slug,
            version=version
        ).first()

        if existing:
            doc = existing

            # Delete old chunks within the same transaction.
            DocumentChunk.query.filter_by(
                document_id=existing.id
            ).delete(synchronize_session=False)

            doc.title = title
            doc.category = category
            doc.is_active = True

        else:
            doc = KnowledgeDocument(
                tenant_id=tenant_id,
                title=title,
                slug=slug,
                category=category,
                version=version,
                is_active=True
            )
            db.session.add(doc)
            db.session.flush()

        for index, chunk_text_content in enumerate(chunks):
            embedding_vec = generate_embedding(chunk_text_content)

            chunk = DocumentChunk(
                document_id=doc.id,
                tenant_id=tenant_id,
                chunk_index=index,
                content=chunk_text_content,
                embedding=embedding_vec,
                token_count=len(chunk_text_content.split()),
                metadata_json={
                    'title': title,
                    'slug': slug,
                    'category': category
                }
            )
            db.session.add(chunk)

        db.session.commit()

        logger.info(
            "Ingested document '%s' with %d chunks (tenant_id=%s)",
            slug,
            len(chunks),
            tenant_id
        )

        return doc

    except Exception:
        db.session.rollback()
        logger.exception(
            "Document ingestion failed for '%s' (tenant_id=%s)",
            slug,
            tenant_id
        )
        raise



def seed_platform_documentation():
    """Seed baseline global billing and platform documentation."""
    docs = [
        {
            'title': 'AbyFlow Billing & Subscription FAQ',
            'slug': 'billing-faq',
            'category': 'faq',
            'content': (
                "# AbyFlow Billing FAQ\n\n"
                "## What plans does AbyFlow offer?\n"
                "AbyFlow offers a Free plan and a Pro plan. The Pro plan is available as a one-time upgrade for $20.00 USD (2000 cents).\n\n"
                "## How are payments processed?\n"
                "Payments are processed securely through Stripe Checkout. Card payments in USD are supported.\n\n"
                "## How do upgrades take effect?\n"
                "Upon successful payment confirmation via Stripe webhook, your account is immediately upgraded to Pro tier through our transactional outbox worker.\n\n"
                "## What is our refund policy?\n"
                "Refunds can be requested within 14 days of upgrade by contacting support or an authorized platform operator."
            )
        },
        {
            'title': 'AbyFlow Invoicing Guidelines',
            'slug': 'invoicing-guide',
            'category': 'policy',
            'content': (
                "# AbyFlow Invoicing Guidelines\n\n"
                "## Creating Invoices\n"
                "Invoices are created in 'draft' status within a specific tenant workspace. Each invoice must contain at least one line item with description, positive quantity, and non-negative unit price.\n\n"
                "## Invoice Numbering\n"
                "Invoices receive sequential numbers in the format INV-000001 allocated atomically per workspace.\n\n"
                "## Issuing Invoices\n"
                "Once ready, a draft invoice can be transitioned to 'issued'. Issued invoices cannot be edited or deleted to preserve financial auditability."
            )
        },
        {
            'title': 'Workspace and Multi-Tenancy Terms',
            'slug': 'workspace-terms',
            'category': 'policy',
            'content': (
                "# AbyFlow Workspace and Tenant Isolation\n\n"
                "## Personal Workspace\n"
                "Every registered user automatically receives a personal workspace upon registration.\n\n"
                "## Workspace Roles\n"
                "- Owner: Full administrative rights over workspace settings, customers, invoices, and members.\n"
                "- Admin: Can manage customers, create and issue invoices, and invite members.\n"
                "- Member: Read and draft access.\n\n"
                "## Data Isolation Guarantee\n"
                "Customer and invoice data are strictly isolated by tenant boundary. Cross-tenant access is strictly prohibited."
            )
        }
    ]

    seeded = []
    for d in docs:
        doc = ingest_document(
            title=d['title'],
            slug=d['slug'],
            content=d['content'],
            category=d['category'],
            tenant_id=None,
            version=1
        )
        seeded.append(doc)
    return seeded

