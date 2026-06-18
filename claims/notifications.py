"""
Email notifications for claim lifecycle events.

Every workflow transition (submitted, advanced, paid, rejected) emails the
employee and, where relevant, each approver. Approver emails carry a
per-recipient one-click action link (see tokens.py) so a Branch Manager,
Finance Manager or Accounts user can approve/reject straight from the email
without logging in.

Sends are deferred to `transaction.on_commit` so an email never goes out for a
rolled-back change. With no SMTP configured, Django's console backend prints the
emails to the terminal — so the whole flow is visible in the demo with zero setup.
"""
from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import TemplateDoesNotExist, render_to_string

from accounts.models import User
from .tokens import make_action_token


def _approver_users(claim):
    """Users who can act on the claim at its current stage."""
    S = claim.Status
    if claim.status in (S.MANAGER_REVIEW, S.AI_FLAGGED):
        return User.objects.filter(
            role=User.Role.MANAGER, branch=claim.branch, is_active=True
        )
    if claim.status == S.FINANCE_REVIEW:
        return User.objects.filter(role=User.Role.FINANCE, is_active=True)
    if claim.status in (S.ACCOUNTS_REVIEW, S.APPROVED):
        return User.objects.filter(role=User.Role.ACCOUNTS, is_active=True)
    return User.objects.none()


def _claim_url(claim) -> str:
    return f"{settings.SITE_URL.rstrip('/')}/claims/{claim.pk}/"


def _action_url(claim, user) -> str:
    return f"{settings.SITE_URL.rstrip('/')}/email-action/?t={make_action_token(claim, user)}"


def _send(subject: str, recipients: list[str], context: dict) -> None:
    recipients = [r for r in dict.fromkeys(recipients) if r]
    if not settings.NOTIFICATIONS_ENABLED or not recipients:
        return
    context.setdefault("site_name", settings.SITE_NAME)
    text_body = render_to_string("emails/claim_event.txt", context)
    msg = EmailMultiAlternatives(
        subject, text_body, settings.DEFAULT_FROM_EMAIL, recipients
    )
    try:
        msg.attach_alternative(
            render_to_string("emails/claim_event.html", context), "text/html"
        )
    except TemplateDoesNotExist:
        pass
    msg.send(fail_silently=True)


def _later(fn):
    transaction.on_commit(fn)


def _employee_name(claim) -> str:
    return claim.employee.get_full_name() or claim.employee.username


def _notify_approvers(claim, heading: str, message: str) -> None:
    """One individualized email per approver, each with its own action link."""
    site = settings.SITE_NAME
    url = _claim_url(claim)
    for user in _approver_users(claim):
        if not user.email:
            continue
        action_url = _action_url(claim, user)
        _later(lambda u=user, au=action_url: _send(
            f"[{site}] Claim #{claim.pk} needs your approval",
            [u.email],
            {
                "heading": heading, "message": message,
                "claim": claim, "url": url, "action_url": au,
            },
        ))


# --- public API used by workflow.py -----------------------------------------
def notify_submitted(claim) -> None:
    site = settings.SITE_NAME
    money = f"{claim.currency} {claim.total_amount}"

    _later(lambda: _send(
        f"[{site}] Claim #{claim.pk} submitted",
        [claim.employee.email],
        {
            "heading": "Your claim was submitted",
            "message": f"Your claim for {money} ({claim.item_count} receipt(s)) "
                       f"is now '{claim.get_status_display()}'.",
            "claim": claim, "url": _claim_url(claim),
        },
    ))

    _notify_approvers(
        claim,
        "A claim needs your approval",
        f"{_employee_name(claim)} submitted a claim for {money} "
        f"({claim.item_count} receipt(s)) at {claim.branch.name}. "
        f"AI score: {claim.ai_score if claim.ai_score is not None else 'n/a'}/100.",
    )


def notify_advanced(claim, actor) -> None:
    site = settings.SITE_NAME
    money = f"{claim.currency} {claim.total_amount}"
    paid = claim.status == claim.Status.PAID

    _later(lambda: _send(
        f"[{site}] Claim #{claim.pk} {'paid' if paid else 'approved'}",
        [claim.employee.email],
        {
            "heading": "Your claim was paid" if paid else "Your claim moved forward",
            "message": (f"Your claim for {money} has been paid."
                        if paid else
                        f"Your claim for {money} was approved and is now "
                        f"'{claim.get_status_display()}'."),
            "claim": claim, "url": _claim_url(claim),
        },
    ))

    if not paid:
        _notify_approvers(
            claim,
            "A claim needs your approval",
            f"Claim #{claim.pk} for {money} from {_employee_name(claim)} "
            f"is ready for your review.",
        )


def notify_items_rejected(claim, actor, rejected_items, reason: str) -> None:
    """Tell the employee which individual receipts were dropped, and why."""
    site = settings.SITE_NAME
    by = (actor.get_full_name() or actor.username) if actor else "a reviewer"
    lines = [f"- {it.vendor or 'Receipt'} ({claim.currency} {it.amount})"
             for it in rejected_items]
    listing = "\n".join(lines)

    _later(lambda: _send(
        f"[{site}] {len(rejected_items)} receipt(s) on claim #{claim.pk} were rejected",
        [claim.employee.email],
        {
            "heading": "Some receipts on your claim were rejected",
            "message": f"{by} rejected {len(rejected_items)} receipt(s) on your "
                       f"claim. The rest continue through approval. New total: "
                       f"{claim.currency} {claim.total_amount}.\n\n{listing}",
            "comment": reason,
            "claim": claim, "url": _claim_url(claim),
        },
    ))


def notify_rejected(claim, actor, comment: str) -> None:
    site = settings.SITE_NAME
    money = f"{claim.currency} {claim.total_amount}"
    by = (actor.get_full_name() or actor.username) if actor else "a reviewer"

    _later(lambda: _send(
        f"[{site}] Claim #{claim.pk} was rejected",
        [claim.employee.email],
        {
            "heading": "Your claim was rejected",
            "message": f"Your claim for {money} was rejected by {by}.",
            "comment": comment,
            "claim": claim, "url": _claim_url(claim),
        },
    ))
