
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
        h = hashlib.md5(
            f"{base_hash}:{i}".encode('utf-8')
        ).hexdigest()
        val = (int(h[:8], 16) / 0xFFFFFFFF) * 2.0 - 1.0
        vals.append(val)

    norm = math.sqrt(sum(x * x for x in vals)) or 1.0
    return [x / norm for x in vals]


def _validate_embedding(vector: List[float]) -> List[float]:
    """Validate embedding dimensions and numeric values."""
    if not isinstance(vector, (list, tuple)):
        raise EmbeddingGenerationError(
            "Embedding provider returned an invalid vector."
        )

    if len(vector) != EMBEDDING_DIM:
        raise EmbeddingGenerationError(
            f"Expected {EMBEDDING_DIM} embedding dimensions, "
            f"received {len(vector)}."
        )

    if any(
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        for value in vector
    ):
        raise EmbeddingGenerationError(
            "Embedding contains invalid numeric values."
        )

    return [float(value) for value in vector]


def generate_embedding(text: str) -> List[float]:
    """
    Generate a 1536-dimensional embedding.

    Testing always uses deterministic mock embeddings.
    Development can explicitly enable mocks.
    Other environments require a real OpenAI API key.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Embedding input must be a non-empty string.")

    testing = (
        current_app.config.get('TESTING', False)
        if has_app_context()
        else False
    )

    allow_mock = (
        current_app.config.get('AI_ALLOW_MOCK_EMBEDDINGS', False)
        if has_app_context()
        else False
    )

    api_key = (
        current_app.config.get('OPENAI_API_KEY')
        if has_app_context()
        else None
    )

    if testing or allow_mock:
        return _validate_embedding(
            _deterministic_mock_embedding(text)
        )

    if not api_key or api_key.startswith('test-') or api_key == 'sk-test-dummy':
        raise EmbeddingGenerationError(
            "A valid OPENAI_API_KEY is required for real embeddings."
        )

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            timeout=current_app.config.get(
                'AI_REQUEST_TIMEOUT', 15.0
            )
        )

        model = current_app.config.get(
            'AI_EMBEDDING_MODEL',
            'text-embedding-3-small'
        )

        response = client.embeddings.create(
            model=model,
            input=text.strip()
        )

        if not response.data:
            raise EmbeddingGenerationError(
                "OpenAI returned an empty embedding response."
            )

        return _validate_embedding(response.data[0].embedding)

    except EmbeddingGenerationError:
        raise
    except Exception as exc:
        logger.exception("OpenAI embedding generation failed.")
        raise EmbeddingGenerationError(
            "Unable to generate an embedding using the configured provider."
        ) from exc
