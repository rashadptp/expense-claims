from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Branch, User


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "location", "monthly_budget", "is_active")
    search_fields = ("name", "code", "location")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "get_full_name", "role", "branch", "is_active")
    list_filter = ("role", "branch", "is_active", "is_staff")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Petty-cash role", {"fields": ("role", "branch")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Petty-cash role", {"fields": ("role", "branch")}),
    )
