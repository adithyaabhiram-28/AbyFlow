import logging
import math
import hashlib
from typing import List
from numbers import Real

from flask import has_app_context, current_app

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1536


class EmbeddingGenerationError(RuntimeError):
    """Raised when a valid embedding cannot be generated."""


def _deterministic_mock_embedding(text: str) -> List[float]:
    """Generate a deterministic normalized vector for testing."""
    vals = []
    base_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
    for i in range(EMBEDDING_DIM):
        h = hashlib.md5(f"{base_hash}:{i}".encode('utf-8')).hexdigest()
        val = (int(h[:8], 16) / 0xFFFFFFFF) * 2.0 - 1.0
        vals.append(val)
    norm = math.sqrt(sum(x * x for x in vals)) or 1.0
    return [x / norm for x in vals]


def _validate_embedding(vector: List[float]) -> List[float]:
    """Validate dimensions and finite numeric values; normalize the vector."""
    if not isinstance(vector, (list, tuple)):
        raise EmbeddingGenerationError('Embedding provider returned an invalid vector.')
    if len(vector) != EMBEDDING_DIM:
        raise EmbeddingGenerationError(
            f'Expected {EMBEDDING_DIM} embedding dimensions, received {len(vector)}.'
        )
    if any(
        isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value)
        for value in vector
    ):
        raise EmbeddingGenerationError('Embedding contains invalid numeric values.')
    values = [float(value) for value in vector]
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise EmbeddingGenerationError('Embedding provider returned a zero vector.')
    return [value / norm for value in values]


def generate_embedding(text: str, task_type: str = 'RETRIEVAL_QUERY') -> List[float]:
    """Generate a normalized 1536-dimensional Gemini retrieval embedding.

    Use RETRIEVAL_DOCUMENT when indexing chunks and RETRIEVAL_QUERY when
    embedding a user's search query. Tests and explicitly enabled development
    mocks remain deterministic and never mask a live provider failure.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Embedding input must be a non-empty string.')

    testing = current_app.config.get('TESTING', False) if has_app_context() else False
    allow_mock = current_app.config.get('AI_ALLOW_MOCK_EMBEDDINGS', False) if has_app_context() else False
    api_key = current_app.config.get('GEMINI_API_KEY') if has_app_context() else None

    app_env = (
        str(current_app.config.get('APP_ENV', 'development')).lower()
        if has_app_context() else 'development'
    )

    if app_env == 'production' and allow_mock:
        raise EmbeddingGenerationError(
            'Mock embeddings are forbidden in production.'
        )

    if testing or (allow_mock and app_env != 'production'):
        return _validate_embedding(_deterministic_mock_embedding(text))

    if not api_key or api_key.startswith('test-') or api_key in {'mock-key-for-testing', 'test-gemini-key'}:
        raise EmbeddingGenerationError('A valid GEMINI_API_KEY is required for real embeddings.')

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=api_key,
            http_options={'timeout': int(current_app.config.get('AI_REQUEST_TIMEOUT', 15.0) * 1000)},
        )
        model = current_app.config.get('AI_EMBEDDING_MODEL', 'gemini-embedding-001')
        response = client.models.embed_content(
            model=model,
            contents=text.strip(),
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=EMBEDDING_DIM,
            ),
        )
        embeddings = getattr(response, 'embeddings', None) or []
        if not embeddings or not getattr(embeddings[0], 'values', None):
            raise EmbeddingGenerationError('Gemini returned an empty embedding response.')
        return _validate_embedding(embeddings[0].values)
    except EmbeddingGenerationError:
        raise
    except Exception as exc:
        logger.exception('Gemini embedding generation failed.')
        raise EmbeddingGenerationError(
            'Unable to generate an embedding using the configured provider.'
        ) from exc
