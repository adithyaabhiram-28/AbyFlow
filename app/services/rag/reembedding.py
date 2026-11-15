"""Controlled full re-embedding for a provider/model-space change.

Run only during a maintenance window with application workers paused. The
operation uses one database transaction: if any provider request fails, all
embedding updates are rolled back so the table cannot be left half in the old
and half in the new vector space.
"""

import logging
from typing import Dict, Any

from flask import current_app, has_app_context

from app.extensions import db
from app.models import DocumentChunk
from app.services.rag.embeddings import generate_embedding

logger = logging.getLogger(__name__)


def reembed_all_chunks(batch_size: int = 25) -> Dict[str, Any]:
    """Replace every stored chunk vector with the configured Gemini document vector."""
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError('batch_size must be a positive integer')

    if not has_app_context():
        raise RuntimeError('Re-embedding requires an active Flask application context.')

    if current_app.config.get('TESTING', False):
        raise RuntimeError('Re-embedding is forbidden in test mode.')

    if current_app.config.get('APP_ENV', '').lower() != 'production':
        raise RuntimeError('Re-embedding is allowed only in production.')

    if current_app.config.get('AI_ALLOW_MOCK_EMBEDDINGS', False):
        raise RuntimeError('Re-embedding is forbidden while mock embeddings are enabled.')

    api_key = (current_app.config.get('GEMINI_API_KEY') or '').strip()
    if (
        not api_key
        or api_key.startswith('test-')
        or api_key in {'mock-key-for-testing', 'test-gemini-key'}
    ):
        raise RuntimeError('Re-embedding requires a real GEMINI_API_KEY.')

    total = DocumentChunk.query.count()
    processed = 0

    try:
        query = DocumentChunk.query.order_by(DocumentChunk.id.asc()).yield_per(batch_size)
        for chunk in query:
            chunk.embedding = generate_embedding(
                chunk.content,
                task_type='RETRIEVAL_DOCUMENT',
            )
            processed += 1
            if processed % batch_size == 0:
                logger.info('Re-embedded %d of %d document chunks', processed, total)

        db.session.commit()
        logger.info('Gemini re-embedding completed for %d document chunks', processed)
        return {'total': total, 'processed': processed}
    except Exception:
        db.session.rollback()
        logger.exception('Gemini re-embedding failed; all vector updates were rolled back.')
        raise
