"""
Pluggable AI receipt-extraction layer.

The rest of the app only ever calls ``get_extractor().extract(image_bytes)``.
Which concrete provider runs is decided by ``settings.AI_PROVIDER`` so you can
start on Groq's free tier and swap to Claude later by changing one env var.
"""
from django.conf import settings

from .base import ExtractionResult, ReceiptExtractor
from .mock_extractor import MockExtractor


def get_extractor() -> ReceiptExtractor:
    provider = (settings.AI_PROVIDER or "mock").lower()

    if provider == "groq" and settings.GROQ_API_KEY:
        from .groq_extractor import GroqExtractor

        return GroqExtractor()

    if provider == "claude" and settings.ANTHROPIC_API_KEY:
        from .claude_extractor import ClaudeExtractor

        return ClaudeExtractor()

    # Fallback: deterministic stub so the app runs with zero API keys.
    return MockExtractor()


__all__ = ["get_extractor", "ExtractionResult", "ReceiptExtractor"]
