# 🧾 Petty Cash Expense & Claims Tracker

A Django web app that replaces the manual *"walk to accounts with a paper
receipt"* process for petty-cash claims across multiple branches.

**Flow:** Employee uploads a receipt → **AI reads & validates it** → claim is
routed through **multi-stage approval** (Manager → Accounts) → marked **Paid**
and deducted from the branch's monthly budget. Every step is logged in an
immutable audit trail.

---

## Why it's more than an upload form

When a receipt is uploaded it is sent to a vision LLM that returns structured
JSON (vendor, total, date, currency, category). A **validation layer** then
scores the claim 0–100 and raises flags for:

| Check | Catches |
|-------|---------|
| Entered amount vs. receipt total | typos & inflated claims |
| "Is this actually a receipt?" | wrong/blank uploads |
| Receipt age vs. policy (30 days) | stale claims |
| Receipt date vs. entered date | sloppy / fraudulent entries |
| Per-category ceiling (e.g. taxi ≤ 100) | over-limit spend |
| Duplicate detection (file hash + vendor/amount/date) | double-claiming |
| Extractor confidence | unreadable receipts |

A **critical** flag routes the claim to `AI_FLAGGED` for human review; clean
small claims skip the manager and go straight to accounts.

---

## Tech stack

- **Django 5** + server-rendered templates + **Tailwind** (CDN)
- **SQLite** for the demo (swap `DATABASES` for PostgreSQL in prod)
- **Groq** vision model for receipt OCR (free tier), behind a swappable adapter
  — flip one env var to use **Claude** instead
- Synchronous AI processing (swap in **Celery + Redis** for prod — see below)

---

## Quick start

```bash
# 1. create + activate a virtualenv (already created as ./venv if you used the scaffold)
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # macOS/Linux

# 2. install deps
pip install -r requirements.txt

# 3. configure env (optional — runs with a mock extractor if you skip AI keys)
copy .env.example .env           # Windows  (cp on macOS/Linux)

# 4. migrate + seed demo users
python manage.py migrate
python manage.py seed_demo

# 5. run
python manage.py runserver
```

Open <http://127.0.0.1:8000/> and log in.

### Demo accounts (password `demo12345`)

| Username | Role | Branch |
|----------|------|--------|
| `alice` | Employee | Downtown |
| `bob` | Employee | Marina |
| `manager` | Branch Manager | Downtown |
| `accounts` | Accounts / Finance | — |
| `admin` | Administrator (superuser) | — |

Try it: log in as **alice**, submit a claim with any receipt image, then log in
as **manager** and **accounts** to approve it through to *Paid*.

---

## Enabling real AI

No key? The app auto-falls back to a deterministic **mock extractor**, so the
whole pipeline runs offline. To use a real provider, set in `.env`:

```ini
# Groq (free) — https://console.groq.com/keys
AI_PROVIDER=groq
GROQ_API_KEY=gsk_...

# …or Claude — https://console.anthropic.com/
AI_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...      # pip install anthropic
```

The app only ever calls `get_extractor().extract(image_bytes)`; the concrete
provider is chosen at runtime, so switching is a one-line env change.

---

## Project layout

```
config/                 Django project (settings, urls)
accounts/               Custom User (roles) + Branch model
claims/
  models.py             ExpenseClaim, Receipt, ApprovalLog
  workflow.py           AI orchestration + approval state machine
  ai/
    base.py             ExtractionResult contract + shared prompt
    groq_extractor.py   Groq vision provider  (default)
    claude_extractor.py Claude vision provider (swap-in)
    mock_extractor.py   offline deterministic stub
    validators.py       flagging + 0–100 scoring rules
  views.py / forms.py / urls.py
templates/              Tailwind UI (dashboard, claim form, detail, audit trail)
```

## Data model

```
User (role: EMPLOYEE/MANAGER/ACCOUNTS/ADMIN, branch FK)
Branch (monthly_budget)
ExpenseClaim (category, amount, status, ai_score, ai_flags) ──1:1── Receipt (image, ai_extracted JSON)
        │
        └──*── ApprovalLog (actor, action, from→to status, comment)  # audit trail
```

### Status lifecycle

```
DRAFT → SUBMITTED ─┬─ AI_FLAGGED ──► (human override)
                   ├─ MANAGER_REVIEW ─► ACCOUNTS_REVIEW ─► APPROVED ─► PAID
                   └─ (small claims skip manager)            │
                              any stage ─────────────────────┴─► REJECTED
```

Thresholds (in `.env`): `AUTO_APPROVE_THRESHOLD` (skip manager),
`HIGH_VALUE_THRESHOLD` (always needs manager), `MAX_RECEIPT_AGE_DAYS`,
and per-category limits in `settings.CATEGORY_LIMITS`.

---

## Scaling to production (what I'd change)

- **Async AI**: move `process_receipt` to a Celery task (Redis broker) so
  uploads return instantly and OCR runs in the background; show a "processing"
  state and update via polling/websocket.
- **PostgreSQL** instead of SQLite; **S3** (django-storages) instead of local
  media.
- **Notifications**: email/Slack on approval/rejection (Django signals).
- **Reporting**: CSV/Excel export, per-branch spend dashboards, monthly close.
- **Hardening**: rate-limit uploads, virus-scan files, store the AI prompt +
  raw response for auditability, and add row-level permission tests.
- **Observability**: log provider latency/cost per extraction.

---

## Design notes

- The AI provider is isolated behind a small interface (`ReceiptExtractor`) so
  it's testable and swappable — no provider SDK leaks into views or models.
- Approval authority is centralised in `workflow.can_act_on()` and reused by
  both the views and templates, so the UI can never offer an action the backend
  would reject.
- `ApprovalLog` is append-only and records `from → to` status plus the actor,
  giving accountants the audit trail they need.
```
