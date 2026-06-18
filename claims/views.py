from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction

from accounts.models import Branch, User
from .forms import ClaimItemFormSet, DecisionForm, UploadForm
from .models import ExpenseClaim
from .tokens import load_action_token
from . import workflow


def home(request):
    """Public marketing landing page. Logged-in users go to their dashboard."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "home.html")


def about(request):
    """Public 'How it works' documentation page (meeting-ready)."""
    from django.conf import settings

    # The AI validation rules, kept in sync with claims/ai/validators.py so the
    # doc always shows the real checks, severities and score penalties.
    ai_rules = [
        {"name": "Not a receipt", "catches": "Random photos, screenshots or blank images uploaded by mistake.",
         "severity": "critical", "penalty": 60},
        {"name": "Amount mismatch (major)", "catches": "Entered amount differs from the receipt total by more than 10% — possible inflation or fraud.",
         "severity": "critical", "penalty": 35},
        {"name": "Amount mismatch (minor)", "catches": "Entered amount differs by 2–10% — likely a typo.",
         "severity": "warning", "penalty": 15},
        {"name": "Amount unreadable", "catches": "AI couldn't read a total from the image (blurry / cropped).",
         "severity": "warning", "penalty": 10},
        {"name": "Stale receipt", "catches": f"Receipt older than the {settings.MAX_RECEIPT_AGE_DAYS}-day policy window.",
         "severity": "critical", "penalty": 25},
        {"name": "Future date", "catches": "Receipt dated in the future.",
         "severity": "warning", "penalty": 15},
        {"name": "Date mismatch", "catches": "Date on the receipt differs from the date entered.",
         "severity": "warning", "penalty": 10},
        {"name": "Over category limit", "catches": "Amount exceeds the per-category single-claim ceiling.",
         "severity": "critical", "penalty": 30},
        {"name": "Duplicate", "catches": "Same receipt file, or same employee + amount + date as an existing claim.",
         "severity": "critical", "penalty": 50},
        {"name": "Low AI confidence", "catches": "The model's own confidence in the reading is under 40%.",
         "severity": "warning", "penalty": 10},
    ]
    return render(request, "about.html", {
        "finance_threshold": settings.FINANCE_REVIEW_THRESHOLD,
        "max_age": settings.MAX_RECEIPT_AGE_DAYS,
        "category_limits": settings.CATEGORY_LIMITS,
        "ai_rules": ai_rules,
    })


@login_required
def dashboard(request):
    user = request.user
    claims = _visible_claims(user)

    stats = {
        "total": claims.count(),
        "pending": claims.filter(status__in=ExpenseClaim.OPEN_STATUSES).count(),
        "paid": claims.filter(status=ExpenseClaim.Status.PAID).count(),
        "flagged": claims.filter(status=ExpenseClaim.Status.AI_FLAGGED).count(),
    }

    action_queue = [c for c in claims.select_related("employee", "branch")
                    if workflow.can_act_on(user, c)]

    branches = []
    if not user.is_employee:
        branch_qs = Branch.objects.filter(is_active=True)
        if user.is_manager and user.branch_id:
            branch_qs = branch_qs.filter(pk=user.branch_id)
        for b in branch_qs:
            spent = b.spent_this_month()
            branches.append({
                "branch": b, "spent": spent, "budget": b.monthly_budget,
                "remaining": b.monthly_budget - spent,
                "pct": _pct(spent, b.monthly_budget),
            })

    recent = claims.select_related("employee", "branch")[:8]

    return render(request, "claims/dashboard.html", {
        "stats": stats, "action_queue": action_queue,
        "branches": branches, "recent": recent,
    })


@login_required
def claim_list(request):
    user = request.user
    claims = _visible_claims(user).select_related("employee", "branch")
    status = request.GET.get("status")
    if status:
        claims = claims.filter(status=status)
    return render(request, "claims/claim_list.html", {
        "claims": claims,
        "statuses": ExpenseClaim.Status.choices,
        "selected_status": status or "",
    })


@login_required
def claim_create(request):
    """Step 1 — upload one or more receipts; AI pre-fills each line item."""
    user = request.user
    # Only employees (and admins) raise claims; approvers review them.
    if not (user.is_employee or user.is_admin_role):
        messages.error(request, "Approvers review claims; they don't raise them.")
        return redirect("dashboard")
    if not user.branch:
        messages.error(request, "You are not assigned to a branch. "
                                "Ask an admin to set your branch first.")
        return redirect("dashboard")

    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                claim = ExpenseClaim.objects.create(
                    employee=user, branch=user.branch, currency="AED",
                    title=form.cleaned_data.get("title", ""),
                    status=ExpenseClaim.Status.DRAFT,
                )
                for f in form.cleaned_data["receipts"]:
                    workflow.add_receipt_to_claim(claim, f)
                claim.recalculate_total()
                claim.save()
            messages.success(
                request,
                f"Read {claim.item_count} receipt(s) with AI. "
                "Review and edit anything below, then submit.",
            )
            return redirect("claim_review", pk=claim.pk)
    else:
        form = UploadForm()
    return render(request, "claims/claim_form.html", {"form": form})


@login_required
def claim_review(request, pk):
    """Step 2 — review AI-extracted line items, edit, then submit for approval."""
    claim = get_object_or_404(ExpenseClaim, pk=pk)
    if claim.employee_id != request.user.id and not request.user.is_admin_role:
        messages.error(request, "You can only review your own claims.")
        return redirect("dashboard")
    if claim.status != ExpenseClaim.Status.DRAFT:
        # Already submitted — nothing to edit.
        return redirect("claim_detail", pk=claim.pk)

    queryset = claim.items.select_related("receipt")

    if request.method == "POST":
        formset = ClaimItemFormSet(request.POST, queryset=queryset)
        if formset.is_valid():
            with transaction.atomic():
                formset.save()
                if not claim.items.exists():
                    messages.error(request, "A claim needs at least one receipt.")
                    return redirect("claim_review", pk=claim.pk)
                workflow.submit_claim(claim)
            messages.success(
                request,
                f"Claim #{claim.pk} submitted — total {claim.currency} "
                f"{claim.total_amount}. AI score "
                f"{claim.ai_score if claim.ai_score is not None else 'n/a'}/100.",
            )
            return redirect("claim_detail", pk=claim.pk)
    else:
        formset = ClaimItemFormSet(queryset=queryset)

    # Pair each form with its receipt for rendering.
    rows = [(form, form.instance.receipt) for form in formset.forms]
    return render(request, "claims/claim_review.html", {
        "claim": claim, "formset": formset, "rows": rows,
    })


@login_required
def claim_detail(request, pk):
    claim = get_object_or_404(
        ExpenseClaim.objects.select_related("employee", "branch"), pk=pk
    )
    if not _can_view(request.user, claim):
        messages.error(request, "You don't have access to that claim.")
        return redirect("dashboard")
    if claim.status == ExpenseClaim.Status.DRAFT and claim.employee_id == request.user.id:
        return redirect("claim_review", pk=claim.pk)

    items = list(claim.items.select_related("receipt"))
    return render(request, "claims/claim_detail.html", {
        "claim": claim,
        "items": items,
        "active_items": [i for i in items if not i.is_rejected],
        "logs": claim.logs.select_related("actor"),
        "can_act": workflow.can_act_on(request.user, claim),
        "next_label": workflow.next_action_label(claim),
        "decision_form": DecisionForm(),
    })


@login_required
def claim_pdf(request, pk):
    """Download the full claim (details + receipt images) as a PDF."""
    claim = get_object_or_404(
        ExpenseClaim.objects.select_related("employee", "branch"), pk=pk
    )
    if not _can_view(request.user, claim):
        messages.error(request, "You don't have access to that claim.")
        return redirect("dashboard")

    from .pdf import build_claim_pdf

    pdf_bytes = build_claim_pdf(claim)
    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="claim-{claim.pk}.pdf"'
    return resp


@login_required
def approvals(request):
    """Approver queue: tick claims to approve, reject the rest with a reason."""
    user = request.user

    if request.method == "POST":
        action = request.POST.get("action")
        ids = request.POST.getlist("claim_ids")
        reason = request.POST.get("reason", "").strip()

        if not ids:
            messages.error(request, "Select at least one claim first.")
            return redirect("approvals")
        if action == "reject" and not reason:
            messages.error(request, "A reason is required to reject claims.")
            return redirect("approvals")

        claims = ExpenseClaim.objects.filter(pk__in=ids)
        done = skipped = 0
        for claim in claims:
            if not workflow.can_act_on(user, claim):
                skipped += 1
                continue
            if action == "approve":
                workflow.approve(claim, user, "Approved from queue")
            elif action == "reject":
                workflow.reject(claim, user, reason)
            done += 1

        verb = "approved" if action == "approve" else "rejected"
        msg = f"{done} claim(s) {verb}."
        if skipped:
            msg += f" {skipped} skipped (not yours to action)."
        messages.success(request, msg)
        return redirect("approvals")

    queue = [c for c in _visible_claims(user).select_related("employee", "branch")
             if workflow.can_act_on(user, c)]
    return render(request, "claims/approvals.html", {"queue": queue})


@login_required
def claim_decision(request, pk):
    claim = get_object_or_404(ExpenseClaim, pk=pk)
    if request.method != "POST" or not workflow.can_act_on(request.user, claim):
        messages.error(request, "Action not allowed.")
        return redirect("claim_detail", pk=pk)

    form = DecisionForm(request.POST)
    if not form.is_valid():
        for err in form.non_field_errors():
            messages.error(request, err)
        return redirect("claim_detail", pk=pk)

    action = form.cleaned_data["action"]
    comment = form.cleaned_data["comment"]
    if action == "approve":
        # Per-receipt approval: the form lists a checkbox per active receipt.
        if request.POST.get("itemized") == "1":
            active_ids = [str(i.pk) for i in claim.items.filter(is_rejected=False)]
            selected = [i for i in request.POST.getlist("approved_items") if i in active_ids]
            if not selected:
                messages.error(request, "Select at least one receipt to approve, "
                                        "or use Reject to reject the whole claim.")
                return redirect("claim_detail", pk=pk)
            if len(selected) < len(active_ids) and not comment:
                messages.error(request, "Add a reason — it will be recorded "
                                        "against the receipt(s) you're dropping.")
                return redirect("claim_detail", pk=pk)
            workflow.approve(claim, request.user, comment, approved_item_ids=selected)
        else:
            workflow.approve(claim, request.user, comment)
        messages.success(request, f"Claim #{claim.pk} advanced to "
                                  f"{claim.get_status_display()}.")
    else:
        workflow.reject(claim, request.user, comment)
        messages.info(request, f"Claim #{claim.pk} rejected.")
    return redirect("claim_detail", pk=pk)


def email_action(request):
    """
    One-click approve/reject from a notification email. Auth is the signed
    token (no login). GET shows the claim + confirm buttons; POST acts.
    """
    token = request.POST.get("t") or request.GET.get("t")
    ctx = {}
    try:
        data = load_action_token(token or "")
    except signing.SignatureExpired:
        ctx["error"] = ("This approval link has expired. Please sign in to "
                        "act on the claim.")
        return render(request, "claims/email_action.html", ctx)
    except (signing.BadSignature, ValueError, KeyError, TypeError):
        ctx["error"] = "This approval link is invalid."
        return render(request, "claims/email_action.html", ctx)

    claim = get_object_or_404(
        ExpenseClaim.objects.select_related("employee", "branch"), pk=data["c"]
    )
    actor = get_object_or_404(User, pk=data["u"])
    stale = claim.status != data["s"]      # claim already moved on
    can = workflow.can_act_on(actor, claim)

    if request.method == "POST" and not stale and can:
        action = request.POST.get("action")
        if action == "approve":
            workflow.approve(claim, actor, "Approved via email link")
            ctx["done"] = "approved"
        elif action == "reject":
            reason = request.POST.get("reason", "").strip()
            if not reason:
                ctx.update({
                    "claim": claim, "actor": actor, "token": token, "stale": stale,
                    "can": can, "items": claim.items.select_related("receipt"),
                    "next_label": workflow.next_action_label(claim),
                    "form_error": "A reason is required to reject.",
                })
                return render(request, "claims/email_action.html", ctx)
            workflow.reject(claim, actor, reason)
            ctx["done"] = "rejected"
        ctx["claim"] = claim
        ctx["actor"] = actor
        return render(request, "claims/email_action.html", ctx)

    ctx.update({
        "claim": claim, "actor": actor, "token": token, "stale": stale, "can": can,
        "items": claim.items.select_related("receipt"),
        "next_label": workflow.next_action_label(claim),
    })
    return render(request, "claims/email_action.html", ctx)


# --- helpers ----------------------------------------------------------------
def _visible_claims(user):
    if user.is_employee:
        return ExpenseClaim.objects.filter(employee=user)
    if user.is_manager and user.branch_id:
        return ExpenseClaim.objects.filter(branch_id=user.branch_id)
    return ExpenseClaim.objects.all()


def _can_view(user, claim):
    if user.is_employee:
        return claim.employee_id == user.id
    if user.is_manager and user.branch_id:
        return claim.branch_id == user.branch_id
    return True


def _pct(part, whole):
    if not whole:
        return 0
    return min(100, round(float(part) / float(whole) * 100))
