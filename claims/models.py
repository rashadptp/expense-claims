import hashlib

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class Category(models.TextChoices):
    TAXI = "TAXI", "Taxi / Transport"
    FOOD = "FOOD", "Food / Meals"
    SUPPLIES = "SUPPLIES", "Office Supplies"
    FUEL = "FUEL", "Fuel"
    OTHER = "OTHER", "Other"


class Receipt(models.Model):
    """An uploaded receipt image plus the structured data the AI pulled out."""

    image = models.ImageField(upload_to="receipts/%Y/%m/")
    file_hash = models.CharField(
        max_length=64, blank=True, db_index=True,
        help_text="SHA-256 of the file bytes; used for duplicate detection.",
    )

    # Raw structured output from the AI extractor.
    ai_extracted = models.JSONField(default=dict, blank=True)
    ai_vendor = models.CharField(max_length=200, blank=True)
    ai_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    ai_date = models.DateField(null=True, blank=True)
    ai_currency = models.CharField(max_length=8, blank=True)
    ai_is_receipt = models.BooleanField(default=True)
    ai_provider = models.CharField(max_length=20, blank=True)

    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Receipt #{self.pk} — {self.ai_vendor or 'unparsed'}"

    def compute_hash(self):
        self.image.open()
        digest = hashlib.sha256(self.image.read()).hexdigest()
        self.image.close()
        return digest


class ExpenseClaim(models.Model):
    """
    A claim = a batch of receipts submitted together as one expense report.
    The total is the sum of its line items; it moves through approval as a unit.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        AI_FLAGGED = "AI_FLAGGED", "Flagged by AI"
        MANAGER_REVIEW = "MANAGER_REVIEW", "Awaiting Manager"
        FINANCE_REVIEW = "FINANCE_REVIEW", "Awaiting Finance Manager"
        ACCOUNTS_REVIEW = "ACCOUNTS_REVIEW", "Awaiting Accounts"
        APPROVED = "APPROVED", "Approved"
        PAID = "PAID", "Paid"
        REJECTED = "REJECTED", "Rejected"

    OPEN_STATUSES = {
        Status.SUBMITTED,
        Status.AI_FLAGGED,
        Status.MANAGER_REVIEW,
        Status.FINANCE_REVIEW,
        Status.ACCOUNTS_REVIEW,
        Status.APPROVED,
    }

    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="claims",
    )
    branch = models.ForeignKey(
        "accounts.Branch", on_delete=models.PROTECT, related_name="claims"
    )
    title = models.CharField(max_length=200, blank=True)
    currency = models.CharField(max_length=8, default="AED")
    description = models.CharField(max_length=300, blank=True)

    # Sum of line-item amounts; recomputed whenever items change.
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )

    # Aggregated AI validation results across all line items.
    ai_score = models.PositiveSmallIntegerField(null=True, blank=True)
    ai_flags = models.JSONField(default=list, blank=True)
    is_duplicate = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Claim #{self.pk} — {self.currency} {self.total_amount} ({self.get_status_display()})"

    def get_absolute_url(self):
        return reverse("claim_detail", args=[self.pk])

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def has_flags(self):
        return bool(self.ai_flags)

    @property
    def item_count(self):
        return self.items.count()

    @property
    def active_item_count(self):
        return self.items.filter(is_rejected=False).count()

    def recalculate_total(self):
        """Total of the non-rejected line items only."""
        agg = self.items.filter(is_rejected=False).aggregate(total=models.Sum("amount"))
        self.total_amount = agg["total"] or 0
        return self.total_amount


class ClaimItem(models.Model):
    """One receipt within a claim, with its AI-extracted (and editable) fields."""

    claim = models.ForeignKey(
        ExpenseClaim, on_delete=models.CASCADE, related_name="items"
    )
    receipt = models.OneToOneField(
        Receipt, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="item",
    )
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.OTHER
    )
    vendor = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    expense_date = models.DateField(null=True, blank=True)
    description = models.CharField(max_length=300, blank=True)

    # Per-item AI validation.
    ai_score = models.PositiveSmallIntegerField(null=True, blank=True)
    ai_flags = models.JSONField(default=list, blank=True)
    is_duplicate = models.BooleanField(default=False)
    # True if the employee changed a value the AI had extracted.
    edited = models.BooleanField(default=False)

    # An approver can reject individual receipts while approving the rest; a
    # rejected item is excluded from the claim total but kept for the record.
    is_rejected = models.BooleanField(default=False)
    reject_reason = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.get_category_display()} — {self.currency} {self.amount}"

    @property
    def currency(self):
        return self.claim.currency

    def get_category_display(self):  # noqa: keep signature for validators
        return Category(self.category).label


class ApprovalLog(models.Model):
    """Immutable audit trail: every action taken on a claim."""

    class Action(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted"
        AI_VALIDATED = "AI_VALIDATED", "AI validated"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        PAID = "PAID", "Marked paid"

    claim = models.ForeignKey(
        ExpenseClaim, on_delete=models.CASCADE, related_name="logs"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="approval_actions",
    )
    stage = models.CharField(max_length=30, blank=True)
    action = models.CharField(max_length=20, choices=Action.choices)
    comment = models.TextField(blank=True)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.get_action_display()} on claim #{self.claim_id}"
