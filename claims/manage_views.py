"""Admin console — manage users and branches (admin role only)."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import Branch, User
from .forms import BranchForm, ManageUserForm
from .models import ExpenseClaim


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_admin_role:
            messages.error(request, "You need administrator access for that.")
            return redirect("dashboard")
        return view(request, *args, **kwargs)
    return wrapped


@admin_required
def manage_home(request):
    role_counts = {
        r.label: User.objects.filter(role=r.value).count()
        for r in User.Role
    }
    stats = {
        "users": User.objects.count(),
        "branches": Branch.objects.count(),
        "open_claims": ExpenseClaim.objects.filter(
            status__in=ExpenseClaim.OPEN_STATUSES).count(),
        "paid_total": ExpenseClaim.objects.filter(
            status=ExpenseClaim.Status.PAID
        ).aggregate(t=Sum("total_amount"))["t"] or 0,
    }
    return render(request, "manage/home.html", {
        "stats": stats,
        "role_counts": role_counts,
        "recent_users": User.objects.select_related("branch").order_by("-date_joined")[:6],
    })


# --- users ------------------------------------------------------------------
@admin_required
def manage_users(request):
    users = User.objects.select_related("branch").order_by("role", "username")
    return render(request, "manage/users.html", {"users": users})


@admin_required
def manage_user_create(request):
    if request.method == "POST":
        form = ManageUserForm(request.POST, creating=True)
        if form.is_valid():
            user = form.save()
            messages.success(request, f"User '{user.username}' created.")
            return redirect("manage_users")
    else:
        form = ManageUserForm(creating=True, initial={"is_active": True})
    return render(request, "manage/user_form.html", {"form": form, "creating": True})


@admin_required
def manage_user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = ManageUserForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, f"User '{user.username}' updated.")
            return redirect("manage_users")
    else:
        form = ManageUserForm(instance=user)
    return render(request, "manage/user_form.html", {
        "form": form, "creating": False, "obj": user,
    })


# --- branches ---------------------------------------------------------------
@admin_required
def manage_branches(request):
    rows = [
        {"b": b, "spent": b.spent_this_month(), "members": b.members.count()}
        for b in Branch.objects.order_by("name")
    ]
    return render(request, "manage/branches.html", {"rows": rows})


@admin_required
def manage_branch_create(request):
    if request.method == "POST":
        form = BranchForm(request.POST)
        if form.is_valid():
            b = form.save()
            messages.success(request, f"Branch '{b.name}' created.")
            return redirect("manage_branches")
    else:
        form = BranchForm(initial={"is_active": True})
    return render(request, "manage/branch_form.html", {"form": form, "creating": True})


@admin_required
def manage_branch_edit(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    if request.method == "POST":
        form = BranchForm(request.POST, instance=branch)
        if form.is_valid():
            form.save()
            messages.success(request, f"Branch '{branch.name}' updated.")
            return redirect("manage_branches")
    else:
        form = BranchForm(instance=branch)
    return render(request, "manage/branch_form.html", {
        "form": form, "creating": False, "obj": branch,
    })
