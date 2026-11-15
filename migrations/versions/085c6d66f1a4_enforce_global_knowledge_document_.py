
"""enforce global knowledge document uniqueness

Revision ID: 085c6d66f1a4
Revises: a1e4c83d7f90
"""

from alembic import op
import sqlalchemy as sa


revision = "085c6d66f1a4"
down_revision = "a1e4c83d7f90"
branch_labels = None
depends_on = None


def upgrade():
    # Check for existing duplicates before creating the unique index.
    # This prevents the migration from silently failing without context.
    bind = op.get_bind()

    duplicates = bind.execute(
        sa.text("""
            SELECT slug, version, COUNT(*) AS duplicate_count
            FROM knowledge_document
            WHERE tenant_id IS NULL
            GROUP BY slug, version
            HAVING COUNT(*) > 1
        """)
    ).fetchall()

    if duplicates:
        details = [
            f"slug={row.slug}, version={row.version}, count={row.duplicate_count}"
            for row in duplicates
        ]

        raise RuntimeError(
            "Cannot enforce global knowledge document uniqueness. "
            "Existing duplicate global documents were found: "
            + "; ".join(details)
        )

    # Global documents have tenant_id IS NULL.
    # PostgreSQL's normal unique constraint does not cover these rows.
    op.create_index(
        "uq_global_knowledge_document_slug_version",
        "knowledge_document",
        ["slug", "version"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NULL"),
    )


def downgrade():
    op.drop_index(
        "uq_global_knowledge_document_slug_version",
        table_name="knowledge_document",
    )
