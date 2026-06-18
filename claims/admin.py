from django.contrib import admin

from .models import ApprovalLog, ClaimItem, ExpenseClaim, Receipt


class ClaimItemInline(admin.TabularInline):
    model = ClaimItem
    extra = 0
    fields = ("vendor", "category", "amount", "expense_date",
              "ai_score", "is_duplicate", "edited")
    readonly_fields = ("ai_score", "is_duplicate", "edited")


class ApprovalLogInline(admin.TabularInline):
    model = ApprovalLog
    extra = 0
    readonly_fields = ("actor", "action", "stage", "from_status",
                       "to_status", "comment", "created_at")
    can_delete = False


@admin.register(ExpenseClaim)
class ExpenseClaimAdmin(admin.ModelAdmin):
    list_display = ("id", "employee", "branch", "item_count", "total_amount",
                    "currency", "status", "ai_score", "is_duplicate", "created_at")
    list_filter = ("status", "branch", "is_duplicate")
    search_fields = ("employee__username", "title", "description")
    readonly_fields = ("ai_score", "ai_flags", "is_duplicate", "total_amount",
                       "created_at", "updated_at")
    inlines = [ClaimItemInline, ApprovalLogInline]


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("id", "ai_vendor", "ai_amount", "ai_currency",
                    "ai_date", "ai_provider", "uploaded_at")
    readonly_fields = ("file_hash", "ai_extracted", "uploaded_at")


@admin.register(ApprovalLog)
class ApprovalLogAdmin(admin.ModelAdmin):
    list_display = ("claim", "action", "actor", "from_status",
                    "to_status", "created_at")
    list_filter = ("action",)
