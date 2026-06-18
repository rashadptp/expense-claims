"""
The approval workflow + AI orchestration (multi-receipt batch claims).

A claim is a batch of receipts. Each receipt becomes a ClaimItem whose fields
are pre-filled by the AI and editable by the employee. On submit, every item is
validated, the results are aggregated to the claim, and the claim is routed:

  * any critical AI flag -> AI_FLAGGED (manager must review)
  * total <= AUTO_APPROVE_THRESHOLD -> skip manager, straight to ACCOUNTS_REVIEW
  * total >= HIGH_VALUE_THRESHOLD -> always requires manager
  * otherwise -> MANAGER_REVIEW -> ACCOUNTS_REVIEW -> APPROVED -> PAID
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from . import notifications
from .ai import get_extractor
from .ai.validators import has_critical, validate_claim
from .models import ApprovalLog, ClaimItem, ExpenseClaim, Receipt


# --- AI processing ----------------------------------------------------------
def process_receipt(receipt: Receipt) -> None:
    """Run extraction on a freshly uploaded receipt and persist the results."""
    receipt.file_hash = receipt.compute_hash()

    receipt.image.open()
    image_bytes = receipt.image.read()
    receipt.image.close()
    mime = _guess_mime(receipt.image.name)

    extractor = get_extractor()
    try:
        result = extractor.extract(image_bytes, mime_type=mime)
    except Exception as exc:  # never let an AI hiccup block the upload
        receipt.ai_extracted = {"error": str(exc), "provider": extractor.name}
        receipt.ai_provider = extractor.name
        receipt.save()
        return

    receipt.ai_extracted = result.as_dict()
    receipt.ai_vendor = result.vendor
    receipt.ai_amount = result.total_amount
    receipt.ai_date = result.date
    receipt.ai_currency = result.currency
    receipt.ai_is_receipt = result.is_receipt
    receipt.ai_provider = result.provider
    receipt.save()


def add_receipt_to_claim(claim: ExpenseClaim, uploaded_file) -> ClaimItem:
    """Create a Receipt + AI-prefilled ClaimItem from one uploaded file."""
    receipt = Receipt(image=uploaded_file)
    receipt.save()
    process_receipt(receipt)

    item = ClaimItem(
        claim=claim,
        receipt=receipt,
        category=receipt.ai_extracted.get("category_guess", "OTHER") or "OTHER",
        vendor=receipt.ai_vendor or "",
        amount=receipt.ai_amount or Decimal("0"),
        expense_date=receipt.ai_date or dt.date.today(),
    )
    item.save()
    return item


# --- Duplicate detection ----------------------------------------------------
def find_duplicate_item(item: ClaimItem):
    """Return an existing item that looks like a duplicate of this one, or None."""
    qs = ClaimItem.objects.exclude(pk=item.pk).exclude(
        claim__status=ExpenseClaim.Status.REJECTED
    )

    if item.receipt and item.receipt.file_hash:
        match = qs.filter(receipt__file_hash=item.receipt.file_hash).select_related("claim").first()
        if match:
            return match

    match = qs.filter(
        claim__employee=item.claim.employee,
        amount=item.amount,
        expense_date=item.expense_date,
    ).select_related("claim").first()
    return match


# --- Submission + routing ---------------------------------------------------
@transaction.atomic
def submit_claim(claim: ExpenseClaim) -> ExpenseClaim:
    """Validate every line item, aggregate, route, and log the claim."""
    all_flags: list[dict] = []
    scores: list[int] = []
    any_duplicate = False

    for item in claim.items.select_related("receipt"):
        extraction = _extraction_from_receipt(item.receipt)
        duplicate = find_duplicate_item(item)
        item.is_duplicate = duplicate is not None
        any_duplicate = any_duplicate or item.is_duplicate

        if extraction is not None:
            score, flags = validate_claim(item, extraction, duplicate_of=duplicate)
            item.ai_score = score
            item.ai_flags = flags
            scores.append(score)
            # Tag each flag with which receipt it came from.
            label = item.vendor or f"item #{item.pk}"
            all_flags.extend({**f, "item": label} for f in flags)
        else:
            item.ai_score = None
            item.ai_flags = []
        item.save()

    claim.recalculate_total()
    claim.ai_flags = all_flags
    claim.ai_score = min(scores) if scores else None
    claim.is_duplicate = any_duplicate

    prev = claim.status
    claim.status = _route(claim, all_flags)
    claim.save()

    _log(claim, None, ApprovalLog.Action.SUBMITTED, prev, claim.status,
         stage="submission")
    summary = (
        f"{claim.item_count} receipt(s), total {claim.currency} {claim.total_amount}; "
        f"AI score {claim.ai_score if claim.ai_score is not None else 'n/a'}/100"
        + (f"; {len(all_flags)} flag(s)" if all_flags else "; no issues")
    )
    _log(claim, None, ApprovalLog.Action.AI_VALIDATED,
         claim.status, claim.status, stage="ai", comment=summary)
    notifications.notify_submitted(claim)
    return claim


def _route(claim: ExpenseClaim, flags) -> str:
    """First stop after submission. The branch manager always reviews first."""
    S = ExpenseClaim.Status
    if has_critical(flags):
        return S.AI_FLAGGED
    return S.MANAGER_REVIEW


def _after_manager(claim: ExpenseClaim) -> str:
    """
    Where a claim goes once the branch manager approves:
      * total >= FINANCE_REVIEW_THRESHOLD -> Finance Manager, then Accounts
      * below it                          -> straight to Accounts (skip Finance)
    """
    S = ExpenseClaim.Status
    if Decimal(claim.total_amount) >= Decimal(str(settings.FINANCE_REVIEW_THRESHOLD)):
        return S.FINANCE_REVIEW
    return S.ACCOUNTS_REVIEW


# --- Approval actions -------------------------------------------------------
def reject_unselected_items(claim, actor, approved_item_ids, reason: str):
    """
    Reject the active line items the approver did NOT select, recalculate the
    claim total, and log it. Returns the list of rejected items (may be empty).
    """
    approved = {int(i) for i in approved_item_ids}
    to_reject = [it for it in claim.items.filter(is_rejected=False)
                 if it.pk not in approved]
    if not to_reject:
        return []

    for it in to_reject:
        it.is_rejected = True
        it.reject_reason = reason
        it.save(update_fields=["is_rejected", "reject_reason"])

    claim.recalculate_total()
    claim.save(update_fields=["total_amount", "updated_at"])

    labels = ", ".join(it.vendor or f"item #{it.pk}" for it in to_reject)
    _log(claim, actor, ApprovalLog.Action.REJECTED, claim.status, claim.status,
         stage="item-rejection",
         comment=f"Rejected {len(to_reject)} receipt(s): {labels}. Reason: {reason}")
    notifications.notify_items_rejected(claim, actor, to_reject, reason)
    return to_reject


@transaction.atomic
def approve(claim: ExpenseClaim, actor, comment: str = "",
            approved_item_ids=None) -> ExpenseClaim:
    S = ExpenseClaim.Status

    # Partial approval: drop the receipts the approver didn't select first, so
    # routing below sees the reduced total.
    if approved_item_ids is not None:
        reject_unselected_items(claim, actor, approved_item_ids, comment)
        claim.refresh_from_db(fields=["total_amount", "status"])

    prev = claim.status

    if claim.status in (S.AI_FLAGGED, S.MANAGER_REVIEW):
        claim.status = _after_manager(claim)
        stage = "manager"
    elif claim.status == S.FINANCE_REVIEW:
        claim.status = S.ACCOUNTS_REVIEW
        stage = "finance"
    elif claim.status == S.ACCOUNTS_REVIEW:
        claim.status = S.APPROVED
        stage = "accounts"
    elif claim.status == S.APPROVED:
        claim.status = S.PAID
        stage = "payment"
    else:
        return claim

    claim.save()
    action = (ApprovalLog.Action.PAID if claim.status == S.PAID
              else ApprovalLog.Action.APPROVED)
    _log(claim, actor, action, prev, claim.status, stage=stage, comment=comment)
    notifications.notify_advanced(claim, actor)
    return claim


@transaction.atomic
def reject(claim: ExpenseClaim, actor, comment: str = "") -> ExpenseClaim:
    prev = claim.status
    claim.status = ExpenseClaim.Status.REJECTED
    claim.save()
    _log(claim, actor, ApprovalLog.Action.REJECTED, prev, claim.status,
         stage="rejection", comment=comment)
    notifications.notify_rejected(claim, actor, comment)
    return claim


def next_action_label(claim: ExpenseClaim) -> str:
    S = ExpenseClaim.Status
    return {
        S.AI_FLAGGED: "Override flags & approve",
        S.MANAGER_REVIEW: "Approve (Branch Manager)",
        S.FINANCE_REVIEW: "Approve (Finance Manager)",
        S.ACCOUNTS_REVIEW: "Approve (Accounts)",
        S.APPROVED: "Mark as Paid",
    }.get(claim.status, "")


def can_act_on(user, claim: ExpenseClaim) -> bool:
    S = ExpenseClaim.Status
    if user.is_admin_role:
        return claim.is_open
    if claim.status in (S.MANAGER_REVIEW, S.AI_FLAGGED):
        return user.is_manager and user.branch_id == claim.branch_id
    if claim.status == S.FINANCE_REVIEW:
        return user.is_finance
    if claim.status in (S.ACCOUNTS_REVIEW, S.APPROVED):
        return user.is_accounts
    return False


# --- helpers ----------------------------------------------------------------
def _extraction_from_receipt(receipt):
    """Rebuild an ExtractionResult from a receipt's stored AI JSON, if any."""
    if not receipt or not receipt.ai_extracted:
        return None
    from .ai.base import ExtractionResult

    data = receipt.ai_extracted
    if data.get("error"):
        return None
    return ExtractionResult.from_payload(
        {
            "is_receipt": data.get("is_receipt", True),
            "vendor": data.get("vendor", ""),
            "total_amount": data.get("total_amount"),
            "currency": data.get("currency", ""),
            "date": data.get("date"),
            "category_guess": data.get("category_guess", "OTHER"),
            "confidence": data.get("confidence", 0),
            "notes": data.get("notes", ""),
        },
        provider=data.get("provider", ""),
    )


def _guess_mime(name: str) -> str:
    name = (name or "").lower()
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


def _log(claim, actor, action, from_status, to_status, stage="", comment=""):
    ApprovalLog.objects.create(
        claim=claim, actor=actor, action=action, stage=stage,
        comment=comment, from_status=from_status, to_status=to_status,
    )
