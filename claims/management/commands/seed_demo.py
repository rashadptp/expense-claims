"""Create demo branches and one user per role. Idempotent."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounts.models import Branch

User = get_user_model()

PASSWORD = "demo12345"

BRANCHES = [
    {"name": "Downtown Branch", "code": "DXB-01", "location": "Dubai", "monthly_budget": 5000},
    {"name": "Marina Branch", "code": "DXB-02", "location": "Dubai Marina", "monthly_budget": 3000},
]

USERS = [
    # username, first, last, role, branch_code
    ("alice",   "Alice",  "Employee", User.Role.EMPLOYEE, "DXB-01"),
    ("bob",     "Bob",    "Employee", User.Role.EMPLOYEE, "DXB-02"),
    ("manager", "Maya",   "Manager",  User.Role.MANAGER,  "DXB-01"),
    ("finance", "Farah",  "Khan",     User.Role.FINANCE,  None),
    ("accounts","Aaron",  "Finance",  User.Role.ACCOUNTS, None),
    ("admin",   "Admin",  "User",     User.Role.ADMIN,    None),
]


class Command(BaseCommand):
    help = "Seed demo branches and users for the petty-cash tracker."

    def handle(self, *args, **options):
        branches = {}
        for data in BRANCHES:
            b, created = Branch.objects.get_or_create(
                code=data["code"], defaults=data
            )
            branches[b.code] = b
            self.stdout.write(("Created " if created else "Exists  ") + f"branch {b}")

        for username, first, last, role, branch_code in USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "role": role,
                    "branch": branches.get(branch_code),
                    "email": f"{username}@example.com",
                },
            )
            if created:
                user.set_password(PASSWORD)
            user.role = role
            user.email = f"{username}@example.com"
            user.branch = branches.get(branch_code)
            if role == User.Role.ADMIN:
                user.is_staff = True
                user.is_superuser = True
            user.save()
            self.stdout.write(
                ("Created " if created else "Updated ")
                + f"user {username} ({role}) / password: {PASSWORD}"
            )

        self.stdout.write(self.style.SUCCESS(
            "\nDemo data ready. Log in at /login/ with any username above "
            f"and password '{PASSWORD}'."
        ))
