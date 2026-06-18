"""Shared contract for all receipt extractors."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional


# The instruction every vision provider receives. Kept here so Groq and Claude
# stay behaviourally identical and only the transport differs.
EXTRACTION_PROMPT = """You are a meticulous receipt-parsing assistant for a \
corporate petty-cash system. Examine the receipt image and return ONLY a JSON \
object (no markdown, no prose) with exactly these keys:

{
  "is_receipt": true/false,          // false if the image is not a purchase receipt
  "vendor": "string",                // merchant/store name, "" if unknown
  "total_amount": number,            // the grand total actually paid
  "currency": "string",              // ISO-ish code e.g. AED, USD; "" if unknown
  "date": "YYYY-MM-DD",              // purchase date; "" if unreadable
  "category_guess": "TAXI|FOOD|SUPPLIES|FUEL|OTHER",
  "confidence": number,              // 0-100, your confidence in this extraction
  "notes": "string"                  // anything unusual, blurriness, edits, etc.
}

Be conservative: if a value is unreadable leave it empty/zero rather than guessing."""


@dataclass
class ExtractionResult:
    """Normalised result returned by every extractor."""

    is_receipt: bool = True
    vendor: str = ""
    total_amount: Optional[Decimal] = None
    currency: str = ""
    date: Optional[dt.date] = None
    category_guess: str = "OTHER"
    confidence: int = 0
    notes: str = ""
    provider: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_payload(cls, data: dict, provider: str) -> "ExtractionResult":
        """Build a result from a provider's parsed JSON, defensively."""
        return cls(
            is_receipt=bool(data.get("is_receipt", True)),
            vendor=str(data.get("vendor", "") or "").strip()[:200],
            total_amount=_to_decimal(data.get("total_amount")),
            currency=str(data.get("currency", "") or "").strip()[:8],
            date=_to_date(data.get("date")),
            category_guess=_clean_category(data.get("category_guess")),
            confidence=_to_int(data.get("confidence"), 0, 100),
            notes=str(data.get("notes", "") or "").strip()[:500],
            provider=provider,
            raw=data,
        )

    def as_dict(self) -> dict:
        return {
            "is_receipt": self.is_receipt,
            "vendor": self.vendor,
            "total_amount": str(self.total_amount) if self.total_amount is not None else None,
            "currency": self.currency,
            "date": self.date.isoformat() if self.date else None,
            "category_guess": self.category_guess,
            "confidence": self.confidence,
            "notes": self.notes,
            "provider": self.provider,
        }


class ReceiptExtractor:
    """Interface every concrete extractor implements."""

    name = "base"

    def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> ExtractionResult:
        raise NotImplementedError


# --- small, forgiving coercion helpers --------------------------------------
def _to_decimal(value) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        # Strip currency symbols / thousands separators if a string sneaks in.
        if isinstance(value, str):
            value = value.replace(",", "").strip()
            value = "".join(c for c in value if c.isdigit() or c in ".-")
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_date(value) -> Optional[dt.date]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _to_int(value, lo, hi) -> int:
    try:
        return max(lo, min(hi, int(float(value))))
    except (TypeError, ValueError):
        return lo


def _clean_category(value) -> str:
    valid = {"TAXI", "FOOD", "SUPPLIES", "FUEL", "OTHER"}
    v = str(value or "OTHER").upper().strip()
    return v if v in valid else "OTHER"
