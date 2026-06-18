"""
Deterministic offline extractor.

Used automatically when no API key is configured so the whole pipeline —
upload, validation, approval — runs end-to-end with zero external calls.
It derives stable pseudo-values from the image bytes so the same file always
produces the same "extraction".
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from .base import ExtractionResult, ReceiptExtractor

_VENDORS = ["City Cabs", "Cafe Aroma", "OfficeMart", "FuelStop", "Quick Bites"]
_CATEGORIES = ["TAXI", "FOOD", "SUPPLIES", "FUEL", "OTHER"]


class MockExtractor(ReceiptExtractor):
    name = "mock"

    def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> ExtractionResult:
        seed = sum(image_bytes[:4096]) if image_bytes else 0
        amount = Decimal(f"{(seed % 400) + 15}.{(seed % 90):02d}")
        days_ago = seed % 20
        return ExtractionResult(
            is_receipt=True,
            vendor=_VENDORS[seed % len(_VENDORS)],
            total_amount=amount,
            currency="AED",
            date=dt.date.today() - dt.timedelta(days=days_ago),
            category_guess=_CATEGORIES[seed % len(_CATEGORIES)],
            confidence=85,
            notes="Mock extraction (no AI provider configured).",
            provider=self.name,
            raw={"mock": True, "seed": seed},
        )
