import re
from typing import List


def chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> List[str]:
    """
    Split document text into bounded, overlapping chunks.

    Chunk boundaries prefer paragraph and sentence endings when possible.
    Every returned chunk is at most ``chunk_size`` characters.
    """
    if (
        isinstance(chunk_size, bool)
        or not isinstance(chunk_size, int)
        or chunk_size <= 0
    ):
        raise ValueError("chunk_size must be a positive integer.")

    if (
        isinstance(chunk_overlap, bool)
        or not isinstance(chunk_overlap, int)
        or chunk_overlap < 0
        or chunk_overlap >= chunk_size
    ):
        raise ValueError(
            "chunk_overlap must be an integer greater than or equal to zero "
            "and smaller than chunk_size."
        )

    if not text or not isinstance(text, str):
        return []

    text = text.strip()
    if not text:
        return []

    if len(text) <= chunk_size:
        return [text]

    chunks: List[str] = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(start + chunk_size, text_length)

        if end < text_length:
            # Prefer a natural boundary in the latter half of the candidate.
            lower_bound = start + chunk_size - chunk_overlap
            candidates = [
                text.rfind("\n\n", lower_bound, end),
                text.rfind("\n", lower_bound, end),
                text.rfind(". ", lower_bound, end),
                text.rfind("! ", lower_bound, end),
                text.rfind("? ", lower_bound, end),
            ]
            boundary = max(candidates)

            if boundary >= lower_bound:
                # Include the newline or sentence punctuation, not the
                # whitespace that separates the next sentence/paragraph.
                end = boundary + (2 if text[boundary:boundary + 2] in ("\n\n", ". ", "! ", "? ") else 1)

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= text_length:
            break

        next_start = end - chunk_overlap
        # Ensure progress even if whitespace trimming changed chunk length.
        start = max(start + 1, next_start)

    return chunks
