from django.contrib.auth.models import AbstractUser
from django.db import models


class Branch(models.Model):
    """A physical branch/location with its own monthly petty-cash budget."""

    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, unique=True)
    location = models.CharField(max_length=200, blank=True)
    monthly_budget = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Monthly petty-cash budget for this branch.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "branches"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"

    def spent_this_month(self):
        """Total of PAID claims for the current calendar month."""
        from django.utils import timezone
        from claims.models import ExpenseClaim

        now = timezone.now()
        agg = ExpenseClaim.objects.filter(
            branch=self,
            status=ExpenseClaim.Status.PAID,
            created_at__year=now.year,
            created_at__month=now.month,
        ).aggregate(total=models.Sum("total_amount"))
        return agg["total"] or 0

    def budget_remaining(self):
        return self.monthly_budget - self.spent_this_month()


class User(AbstractUser):
    """Custom user with a role and an optional home branch."""

    class Role(models.TextChoices):
        EMPLOYEE = "EMPLOYEE", "Employee"
        MANAGER = "MANAGER", "Branch Manager"
        FINANCE = "FINANCE", "Finance Manager"
        ACCOUNTS = "ACCOUNTS", "Accounts / Finance"
        ADMIN = "ADMIN", "Administrator"

    role = models.CharField(
        max_length=20, choices=Role.choices, default=Role.EMPLOYEE
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="members",
    )

    def __str__(self):
        full = self.get_full_name()
        return f"{full or self.username} — {self.get_role_display()}"

    # Convenience role checks used throughout the views/templates.
    @property
    def is_employee(self):
        return self.role == self.Role.EMPLOYEE

    @property
    def is_manager(self):
        return self.role == self.Role.MANAGER

    @property
    def is_finance(self):
        return self.role == self.Role.FINANCE

    @property
    def is_accounts(self):
        return self.role == self.Role.ACCOUNTS

    @property
    def is_admin_role(self):
        return self.role == self.Role.ADMIN or self.is_superuser
