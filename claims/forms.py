from django import forms
from django.forms import modelformset_factory

from accounts.models import Branch, User
from .models import ClaimItem, ExpenseClaim


INPUT_CSS = ("w-full rounded-lg border border-slate-300 px-3 py-2 "
             "focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500")


# --- multiple-file upload (Django has no built-in multi-file field) ---------
class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(d, initial) for d in data]
        return [single(data, initial)]


class UploadForm(forms.Form):
    """Step 1: drop in one or more receipts. No manual data entry."""

    receipts = MultipleFileField(
        label="Receipts",
        help_text="Select one or more receipt images — Claude reads each one.",
    )
    title = forms.CharField(
        required=False, max_length=200,
        widget=forms.TextInput(attrs={
            "class": INPUT_CSS,
            "placeholder": "e.g. June client visit expenses (optional)",
        }),
    )

    def clean_receipts(self):
        files = self.cleaned_data["receipts"]
        if not files:
            raise forms.ValidationError("Please upload at least one receipt.")
        if len(files) > 20:
            raise forms.ValidationError("Please upload at most 20 receipts at a time.")
        return files


class ClaimItemForm(forms.ModelForm):
    """Step 2: one editable row per receipt, pre-filled by the AI."""

    class Meta:
        model = ClaimItem
        fields = ["vendor", "category", "amount", "expense_date", "description"]
        widgets = {
            "vendor": forms.TextInput(attrs={"class": INPUT_CSS}),
            "category": forms.Select(attrs={"class": INPUT_CSS}),
            "amount": forms.NumberInput(attrs={
                "class": INPUT_CSS + " js-amount", "step": "0.01", "min": "0",
            }),
            "expense_date": forms.DateInput(
                attrs={"type": "date", "class": INPUT_CSS}, format="%Y-%m-%d"
            ),
            "description": forms.TextInput(attrs={
                "class": INPUT_CSS, "placeholder": "Optional note",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["expense_date"].input_formats = ["%Y-%m-%d"]

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount is None or amount <= 0:
            raise forms.ValidationError("Amount must be greater than zero.")
        return amount

    def save(self, commit=True):
        # Mark the item as edited if the employee changed an AI-extracted value.
        item = super().save(commit=False)
        if item.receipt:
            ai = item.receipt.ai_extracted or {}
            ai_amount = ai.get("total_amount")
            changed = (
                "amount" in self.changed_data
                or "vendor" in self.changed_data
                or "category" in self.changed_data
                or "expense_date" in self.changed_data
            )
            item.edited = bool(changed)
        if commit:
            item.save()
        return item


ClaimItemFormSet = modelformset_factory(
    ClaimItem, form=ClaimItemForm, extra=0, can_delete=True
)


class ManageUserForm(forms.ModelForm):
    """Admin form to create / edit a user, set role and branch."""

    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"class": INPUT_CSS, "autocomplete": "new-password"}),
        help_text="Required for a new user. Leave blank to keep the current password.",
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email",
                  "role", "branch", "is_active"]
        widgets = {
            "username": forms.TextInput(attrs={"class": INPUT_CSS}),
            "first_name": forms.TextInput(attrs={"class": INPUT_CSS}),
            "last_name": forms.TextInput(attrs={"class": INPUT_CSS}),
            "email": forms.EmailInput(attrs={"class": INPUT_CSS}),
            "role": forms.Select(attrs={"class": INPUT_CSS}),
            "branch": forms.Select(attrs={"class": INPUT_CSS}),
        }

    def __init__(self, *args, creating=False, **kwargs):
        self.creating = creating
        super().__init__(*args, **kwargs)
        self.fields["branch"].required = False
        self.fields["branch"].empty_label = "— No branch —"

    def clean_password(self):
        pw = self.cleaned_data.get("password")
        if self.creating and not pw:
            raise forms.ValidationError("Set a password for the new user.")
        return pw

    def clean(self):
        cleaned = super().clean()
        # Employees and branch managers must belong to a branch.
        role = cleaned.get("role")
        branch = cleaned.get("branch")
        if role in (User.Role.EMPLOYEE, User.Role.MANAGER) and not branch:
            self.add_error("branch", "Employees and branch managers need a branch.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        pw = self.cleaned_data.get("password")
        if pw:
            user.set_password(pw)
        if commit:
            user.save()
        return user


class BranchForm(forms.ModelForm):
    """Admin form to create / edit a branch and its monthly budget."""

    class Meta:
        model = Branch
        fields = ["name", "code", "location", "monthly_budget", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CSS}),
            "code": forms.TextInput(attrs={"class": INPUT_CSS}),
            "location": forms.TextInput(attrs={"class": INPUT_CSS}),
            "monthly_budget": forms.NumberInput(attrs={"class": INPUT_CSS, "step": "0.01", "min": "0"}),
        }


class DecisionForm(forms.Form):
    """Approve / reject action with an optional comment."""

    ACTIONS = (("approve", "approve"), ("reject", "reject"))
    action = forms.ChoiceField(choices=ACTIONS, widget=forms.HiddenInput)
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 2,
            "class": "w-full rounded-lg border border-slate-300 px-3 py-2",
            "placeholder": "Optional comment (required when rejecting)…",
        }),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("action") == "reject" and not cleaned.get("comment"):
            raise forms.ValidationError("Please give a reason for rejection.")
        return cleaned
