"""
Validation layer.

Takes the raw AI extraction plus the claim the employee typed and produces a
list of human-readable flags and a 0-100 health score. This is the logic that
catches typos, fraud, stale receipts, over-limit spend and duplicates — the
part that makes the system more than a glorified upload form.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.utils import timezone


@dataclass
class Flag:
    code: str
    severity: str  # "warning" | "critical"
    message: str
    penalty: int   # points subtracted from the 100-point health score


def validate_claim(claim, extraction, duplicate_of=None) -> tuple[int, list[dict]]:
    """
    Returns ``(score, flags)`` where score is 0-100 and flags is a list of
    serialisable dicts. ``extraction`` is an ExtractionResult; ``duplicate_of``
    is an existing ExpenseClaim (or None).
    """
    flags: list[Flag] = []

    # 1) Is it even a receipt?
    if not extraction.is_receipt:
        flags.append(Flag(
            "not_a_receipt", "critical",
            "The uploaded image does not look like a purchase receipt.", 60,
        ))

    # 2) Amount the employee typed vs. amount on the receipt.
    if extraction.total_amount is not None:
        typed = Decimal(claim.amount)
        ai_amt = Decimal(extraction.total_amount)
        if ai_amt > 0:
            diff_ratio = abs(typed - ai_amt) / ai_amt
            if diff_ratio > Decimal("0.02"):  # >2% mismatch
                sev = "critical" if diff_ratio > Decimal("0.10") else "warning"
                flags.append(Flag(
                    "amount_mismatch", sev,
                    f"Entered amount {claim.currency} {typed} differs from the "
                    f"receipt total {extraction.currency or claim.currency} {ai_amt}.",
                    35 if sev == "critical" else 15,
                ))
    else:
        flags.append(Flag(
            "amount_unreadable", "warning",
            "Could not read a total amount from the receipt.", 10,
        ))

    # 3) Receipt age vs. policy.
    ref_date = extraction.date or claim.expense_date
    if ref_date:
        age_days = (timezone.now().date() - ref_date).days
        if age_days > settings.MAX_RECEIPT_AGE_DAYS:
            flags.append(Flag(
                "stale_receipt", "critical",
                f"Receipt is {age_days} days old; policy limit is "
                f"{settings.MAX_RECEIPT_AGE_DAYS} days.", 14,
            ))
        elif age_days < 0:
            flags.append(Flag(
                "future_date", "warning",
                "Receipt date is in the future.", 15,
            ))

    # 4) Date on receipt vs. date the employee entered.
    if extraction.date and claim.expense_date and extraction.date != claim.expense_date:
        flags.append(Flag(
            "date_mismatch", "warning",
            f"Receipt date {extraction.date} differs from entered date "
            f"{claim.expense_date}.", 10,
        ))

    # 5) Category single-claim ceiling.
    limit = settings.CATEGORY_LIMITS.get(claim.category)
    if limit and Decimal(claim.amount) > Decimal(str(limit)):
        flags.append(Flag(
            "over_category_limit", "critical",
            f"{claim.get_category_display()} claims are capped at "
            f"{claim.currency} {limit}; this is {claim.currency} {claim.amount}.",
            30,
        ))

    # 6) Duplicate detection.
    if duplicate_of is not None:
        ref = getattr(duplicate_of, "claim_id", None) or getattr(duplicate_of, "pk", "?")
        flags.append(Flag(
            "duplicate", "critical",
            f"Looks like a duplicate of claim #{ref} "
            f"(same receipt or same vendor/amount/date).", 50,
        ))

    # 7) Low extractor confidence.
    if extraction.confidence and extraction.confidence < 40:
        flags.append(Flag(
            "low_confidence", "warning",
            f"AI extraction confidence is low ({extraction.confidence}%).", 10,
        ))

    score = max(0, 100 - sum(f.penalty for f in flags))
    serialised = [
        {"code": f.code, "severity": f.severity, "message": f.message}
        for f in flags
    ]
    return score, serialised


def has_critical(flags: list[dict]) -> bool:
    return any(f.get("severity") == "critical" for f in flags)
