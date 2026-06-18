# ClaimFlow — Complete Project Walkthrough

A feature-by-feature explanation of the petty-cash expense & claims tracker, with
the **logic behind each part** and **where to find it in code**.

File references look like `claims/models.py:122` — that's `file : line number`.

---

## 1. The one-paragraph pitch

> ClaimFlow is a Django web app that automates multi-branch petty-cash expense
> claims. An employee uploads **one or more receipt photos at once**; a vision AI
> model reads each one (vendor, amount, date, category) and a validation layer
> scores them and raises fraud/error flags. The employee reviews/edits the
> pre-filled data and submits. The claim then moves through a **multi-stage
> approval chain** (Branch Manager → Finance Manager → Accounts → Paid), routed
> by amount and AI risk. Every action is recorded in an **immutable audit log**,
> approvers can act **straight from an email**, individual receipts can be
> **rejected while approving the rest**, and any claim can be exported as a **PDF
> with the receipt images attached**.

The point that makes it more than a CRUD app: **the AI extraction + the
validation/scoring + the routing engine**. Everything else is plumbing around that.

---

## 2. Tech stack & why

| Choice | Why |
|---|---|
| **Django 5** | Batteries-included: ORM, auth, admin, forms, templating — broad coverage without wiring 10 libraries. |
| **Server-rendered templates + Tailwind (CDN)** | No SPA build step. The UI is fast to write and easy to read. Tailwind via CDN = zero front-end toolchain. |
| **SQLite** | Zero-setup demo DB. Swappable for PostgreSQL in one settings block (`config/settings.py:81`). |
| **Synchronous AI calls** | Simpler to reason about for a demo. The production path (Celery/Redis) is stubbed in `requirements.txt`. |
| **Pluggable AI adapter layer** | Start on Groq's free tier, switch to Claude with one env var — no code change (`claims/ai/__init__.py`). |
| **ReportLab** for PDF | Pure pip install, no system deps (unlike WeasyPrint which needs GTK on Windows), and embeds images cleanly. |

---

## 3. How the Django project & apps were created (the basics)

Django splits a codebase into a **project** (global config) and **apps**
(self-contained feature modules).

```bash
# 1. create venv and install Django
python -m venv venv
venv\Scripts\pip install django

# 2. create the PROJECT (global settings, urls, wsgi) — the "config" package
django-admin startproject config .

# 3. create the APPS (feature modules)
python manage.py startapp accounts   # users, branches, login
python manage.py startapp claims     # the whole claims domain
```

- **`config/`** — the project. `settings.py` (all configuration), `urls.py`
  (top-level routing), `wsgi.py`/`asgi.py` (server entry points).
- **`accounts/`** — custom user model + branches + auth URLs.
- **`claims/`** — the core domain: models, workflow, AI, views, forms, PDF, emails.

Apps are registered in `INSTALLED_APPS` (`config/settings.py:34`). Top-level URLs
`include()` each app's `urls.py` (`config/urls.py:6`).

**Why a custom user model?** We needed a `role` and a home `branch` on the user.
Django strongly recommends setting a custom user model at project start, done via
`AUTH_USER_MODEL = "accounts.User"` (`config/settings.py:89`) with
`class User(AbstractUser)` (`accounts/models.py:42`).

---

## 4. Data model — `claims/models.py` & `accounts/models.py`

The data model is a **batch**: one claim contains many line items, each line item
wraps one uploaded receipt.

```
Branch (1) ──< User (employee)
                  │
ExpenseClaim (the batch / report) 1 ──< ClaimItem (one per receipt) 1 ──1 Receipt (image + AI data)
       │
       └──< ApprovalLog (immutable audit trail)
```

### `Branch` — `accounts/models.py:5`
A physical location with a `monthly_budget`. Key method `spent_this_month()`
(`accounts/models.py:24`) sums **PAID** claims for the current month, used for
budget tracking on the dashboard and admin console.

### `User` — `accounts/models.py:42`
Extends `AbstractUser`, adds:
- `role` — EMPLOYEE / MANAGER (Branch Manager) / FINANCE / ACCOUNTS / ADMIN
- `branch` — home branch
- Convenience properties `is_employee`, `is_manager`, `is_finance`,
  `is_accounts`, `is_admin_role` (`accounts/models.py:65`) used everywhere for
  permission checks so we never compare role strings inline.

### `Receipt` — `claims/models.py:17`
The uploaded image + the raw AI output.
- `image` — `ImageField(upload_to="receipts/%Y/%m/")`
- `file_hash` — SHA-256 of the bytes, used for duplicate detection
  (`compute_hash()`, `claims/models.py:42`)
- `ai_*` fields — what the AI read (vendor, amount, date, currency, is_receipt)
- `ai_extracted` — the full JSON payload kept for the record/replay

### `ExpenseClaim` — `claims/models.py:49`
The batch that moves through approval as a unit.
- `Status` choices (`claims/models.py:55`): DRAFT, SUBMITTED, AI_FLAGGED,
  MANAGER_REVIEW, FINANCE_REVIEW, ACCOUNTS_REVIEW, APPROVED, PAID, REJECTED.
- `OPEN_STATUSES` set (`claims/models.py:66`) — "still in flight" statuses, used
  for the "pending" filters.
- `total_amount` — the sum of **non-rejected** line items.
- `ai_score` (0–100) + `ai_flags` (list) — aggregated validation results.
- `recalculate_total()` (`claims/models.py:126`) — sums only `is_rejected=False`
  items. **This is the method partial approval relies on.**
- `active_item_count` (`claims/models.py:123`) — count of non-rejected items.

### `ClaimItem` — `claims/models.py:133`
One receipt's editable, AI-prefilled line.
- `category`, `vendor`, `amount`, `expense_date`, `description` — editable fields.
- `ai_score`, `ai_flags`, `is_duplicate`, `edited`.
- **`is_rejected` + `reject_reason`** (`claims/models.py:160`) — added for the
  per-receipt approval feature. A rejected item is kept for the record but
  excluded from the total.

### `ApprovalLog` — `claims/models.py:179`
Immutable audit trail. Every transition writes one row: who (`actor`), what
(`action`), when, `from_status` → `to_status`, free-text `comment`. Ordered by
`created_at` so the detail page renders it as a timeline. Nothing ever updates or
deletes these rows — that's the "immutable" part.

> **Migrations:** every model change is captured by
> `python manage.py makemigrations` → versioned files in `claims/migrations/`.
> The rejection fields are migration `0003`. `migrate` applies them to the DB.

---

## 5. AI receipt extraction (the pluggable adapter layer) — `claims/ai/`

**Goal:** the rest of the app should never know *which* AI provider is running.

### The contract — `claims/ai/base.py`
- `EXTRACTION_PROMPT` (`base.py:12`) — the single prompt every provider sends, so
  Groq and Claude behave identically; only the transport differs.
- `ExtractionResult` dataclass (`base.py:30`) — the normalised result every
  provider returns. `from_payload()` (`base.py:45`) defensively coerces messy
  model JSON (strips currency symbols, parses multiple date formats, clamps
  confidence 0–100) so a sloppy model response can't crash the app.
- `ReceiptExtractor` (`base.py:75`) — the interface: one method `extract(image_bytes)`.

### The factory — `claims/ai/__init__.py:14`
```python
get_extractor()  # returns GroqExtractor / ClaudeExtractor / MockExtractor
```
It reads `settings.AI_PROVIDER`. If the chosen provider has no API key, it falls
back to **`MockExtractor`** — a deterministic stub so the whole app runs with
**zero API keys** (essential for a demo). This is the **Strategy / Adapter
pattern**: swap implementations behind a common interface.

### Concrete adapters
- `claims/ai/groq_extractor.py`, `claims/ai/claude_extractor.py` — real vision
  calls (image → base64 → model → JSON → `ExtractionResult.from_payload`).
- `claims/ai/mock_extractor.py` — deterministic fake for offline demos.

In short: the AI is behind an adapter so the provider is a config value, not a
code dependency. The app calls `get_extractor().extract(bytes)` and gets back a
normalised `ExtractionResult` no matter who answered.

---

## 6. The validation / scoring layer — `claims/ai/validators.py`

This is the "brain" that turns raw extraction into **risk signals**. It compares
*what the AI read* against *what the employee typed* and against *company policy*.

`validate_claim(item, extraction, duplicate_of)` (`validators.py:26`) returns
`(score, flags)`. It starts at 100 and subtracts a penalty per problem:

| Check | Severity | Penalty | Code line |
|---|---|---|---|
| Not a receipt | critical | 60 | `validators.py:35` |
| Amount mismatch >10% (fraud/inflation) | critical | 35 | `validators.py:47` |
| Amount mismatch 2–10% (likely typo) | warning | 15 | same |
| Amount unreadable | warning | 10 | `validators.py:56` |
| Stale receipt (older than policy window) | critical | 25 | `validators.py:65` |
| Future date | warning | 15 | `validators.py:71` |
| Date mismatch (receipt vs entered) | warning | 10 | `validators.py:78` |
| Over category limit | critical | 30 | `validators.py:86` |
| Duplicate | critical | 50 | `validators.py:96` |
| Low AI confidence (<40%) | warning | 10 | `validators.py:105` |

`score = max(0, 100 - sum(penalties))` (`validators.py:111`).
`has_critical(flags)` (`validators.py:119`) — any critical flag means the claim
gets routed to **AI_FLAGGED** for mandatory manager review.

Policy values (thresholds, category limits, max age) live in settings
(`config/settings.py:149`) so they're tunable without touching logic. The
`/about/` page renders these same rules so the docs never drift from the code.

---

## 7. The workflow engine — `claims/workflow.py`

This module owns everything that *changes* a claim: AI orchestration, submission,
routing, approval, rejection, and partial approval. Views call into it; it never
reaches back into views. All mutating functions are wrapped in
`@transaction.atomic` so a half-finished transition can't be saved.

### Upload → AI → line item
- `process_receipt(receipt)` (`workflow.py:28`) — hashes the file, opens the
  image bytes, calls `get_extractor().extract(...)`, saves the AI fields. Wrapped
  in try/except so **an AI hiccup never blocks the upload** — the receipt is saved
  with an error note instead.
- `add_receipt_to_claim(claim, file)` (`workflow.py:56`) — creates the `Receipt`,
  runs extraction, creates a `ClaimItem` pre-filled from the AI result.

### Duplicate detection — `find_duplicate_item()` (`workflow.py:75`)
Two strategies: (1) **same file hash** (exact same image re-uploaded), or
(2) **same employee + amount + date** (re-submitting the same expense).
Excludes already-rejected claims.

### Submission + routing — `submit_claim()` (`workflow.py:96`)
1. For each item: rebuild the extraction, detect duplicates, run
   `validate_claim`, store per-item score/flags.
2. Aggregate to the claim: `total = recalculate_total()`, `ai_score = min(item
   scores)` (worst item drives the claim), collect all flags.
3. **Route** via `_route()` (`workflow.py:143`): any critical flag → `AI_FLAGGED`,
   else → `MANAGER_REVIEW`.
4. Write two audit logs (SUBMITTED + AI_VALIDATED) and fire `notify_submitted`.

### The approval chain — `approve()` (`workflow.py:192`)
Each call advances the claim one stage based on its current status:

```
AI_FLAGGED / MANAGER_REVIEW ──(manager approves)──► _after_manager()
FINANCE_REVIEW              ──(finance approves)───► ACCOUNTS_REVIEW
ACCOUNTS_REVIEW            ──(accounts approves)──► APPROVED
APPROVED                   ──(accounts marks)─────► PAID
```

`_after_manager()` (`workflow.py:151`) is the **amount-based fork**: if total ≥
`FINANCE_REVIEW_THRESHOLD` → `FINANCE_REVIEW`, else skip Finance and go straight
to `ACCOUNTS_REVIEW`. Every approve writes an `ApprovalLog` and calls
`notify_advanced`.

### Rejection — `reject()` (`workflow.py:228`)
Sets status to REJECTED, logs it, emails the employee.

### Authority — `can_act_on(user, claim)` (`workflow.py:249`)
The single source of truth for "is this user allowed to action this claim right
now?" Admins can act on any open claim; managers only on their own branch at the
manager stage; finance at the finance stage; accounts at the accounts/payment
stage. Both the views and the email-action flow gate on this.

### Per-receipt (partial) approval — `reject_unselected_items()` (`workflow.py:164`)
Added so an approver can keep some receipts and drop others in one action:
1. Mark every active item the approver **didn't** select as `is_rejected=True`
   with the given reason.
2. `recalculate_total()` — total drops to the kept items only.
3. Log an `item-rejection` entry and email the employee which receipts were
   dropped (`notify_items_rejected`).
4. `approve()` accepts `approved_item_ids=` (`workflow.py:192`); it runs the step
   above **first**, then re-reads the claim so routing sees the reduced total
   (e.g. dropping receipts can push a claim under the Finance threshold).

In short: the workflow is a small state machine. Status + amount + AI risk decide
the next state. Views are thin; all state changes go through `workflow.py` inside
atomic transactions, and every change appends to an immutable log.

---

## 8. Views & URL routing — `claims/views.py`, `claims/urls.py`

URLs (`claims/urls.py`) map paths to view functions. The request flow:

| URL | View | What it does |
|---|---|---|
| `/` | `home` (`views.py:14`) | Public landing; logged-in users → dashboard. |
| `/about/` | `about` (`views.py:21`) | "How it works", data-driven from settings + AI rules. |
| `/dashboard/` | `dashboard` (`views.py:58`) | Stats, your action queue, branch budgets, recent claims. |
| `/claims/` | `claim_list` (`views.py:94`) | Filterable list (role-scoped). |
| `/claims/new/` | `claim_create` (`views.py:108`) | **Step 1**: upload receipts → AI prefill → redirect to review. |
| `/claims/<pk>/review/` | `claim_review` (`views.py:142`) | **Step 2**: edit AI data in a formset, then submit. |
| `/claims/<pk>/` | `claim_detail` (`views.py:181`) | Full claim view + decision panel. |
| `/claims/<pk>/decision/` | `claim_decision` (`views.py:263`) | Approve/reject (+ per-receipt selection). |
| `/claims/<pk>/pdf/` | `claim_pdf` (`views.py:204`) | Download the PDF with images. |
| `/approvals/` | `approvals` (`views.py:218`) | Bulk approve/reject queue. |
| `/email-action/` | `email_action` (`views.py:301`) | One-click approve/reject from email (no login). |
| `/manage/...` | `manage_views.py` | Admin console. |

**Role scoping:** `_visible_claims(user)` (`views.py:337`) and `_can_view`
(`views.py:345`) ensure employees see only their own claims, managers only their
branch, finance/accounts/admin everything.

**Permission gates:** `@login_required` on every authenticated view; mutating
actions additionally check `workflow.can_act_on`.

### The two-step create flow (why it's split)
`claim_create` saves the claim as **DRAFT** and runs AI, then redirects to
`claim_review`. The employee sees the AI's guesses, fixes anything, and only then
`submit_claim` runs validation + routing. Splitting upload from submit means the
AI does the typing and the human does the verifying.

---

## 9. Forms — `claims/forms.py`

- `MultipleFileField` (`forms.py:17`) — Django has no built-in multi-file upload;
  this custom field + widget (`forms.py:13`) accepts many files at once.
- `UploadForm` (`forms.py:29`) — the receipts + optional title, with a 1–20 file
  limit (`clean_receipts`, `forms.py:44`).
- `ClaimItemForm` + `ClaimItemFormSet` (`forms.py:53`, `forms.py:101`) — a
  **formset** renders one editable row per receipt on the review screen. `save()`
  (`forms.py:83`) flags an item as `edited` if the employee changed an
  AI-extracted value (that's where the ✎ marker comes from).
- `DecisionForm` (`forms.py:173`) — approve/reject + comment; `clean()` requires a
  comment when rejecting.
- `ManageUserForm` / `BranchForm` — admin console forms.

---

## 10. Templates & UI — `templates/`

Server-rendered Django templates extending `templates/base.html`. Tailwind via
CDN. Context processor `claims/context_processors.py` injects branding + policy
values into **every** template, so `{{ SITE_NAME }}` etc. work everywhere.

Key templates:
- `claims/claim_form.html` — upload + **scanning animation** (see §13).
- `claims/claim_review.html` — the editable formset rows.
- `claims/claim_detail.html` — receipts table, audit timeline, AI panel, and the
  **decision panel with per-receipt checklist + live total** (see §13).
- `claims/approvals.html` — bulk queue.
- `emails/claim_event.{html,txt}` — notification email bodies.
- `about.html` — public docs.

---

## 11. Notifications + one-click email approval — `claims/notifications.py`, `claims/tokens.py`

### Notifications (`claims/notifications.py`)
Every transition emails the relevant people. Public API called by `workflow.py`:
- `notify_submitted` (`notifications.py:92`) — employee + all approvers.
- `notify_advanced` (`notifications.py:116`) — employee ("moved forward"/"paid")
  + next approvers. **This is why the employee gets an email at every stage.**
- `notify_rejected` (`notifications.py:165`) — employee.
- `notify_items_rejected` (`notifications.py:143`) — employee, when individual
  receipts are dropped during partial approval.

Design points:
- Sends are deferred with `transaction.on_commit` (`_later`, `notifications.py:65`)
  so **no email goes out for a rolled-back change**.
- Default backend is the **console** (prints to terminal) — notifications work
  with zero SMTP setup. Set `EMAIL_HOST` in `.env` and it auto-switches to real
  SMTP (`config/settings.py:127`).
- Email templates are kept **emoji-free** — the Windows console encoding throws on
  emoji, and `fail_silently` would swallow it into a silent no-send.

### One-click approval tokens (`claims/tokens.py`)
`make_action_token(claim, user)` (`tokens.py:18`) creates a **signed, expiring**
token embedding `{claim id, user id, claim status}`. The `email_action` view
(`views.py:301`) re-checks all three:
- HMAC signature (can't be tampered with — signed by `SECRET_KEY`),
- 7-day expiry,
- the embedded status must still match (the link dies once the claim moves on),
- `can_act_on` still allows that user.

So an approver clicks **Approve** in their email and acts **without logging in**,
but the link is single-stage, time-limited, and user-bound.

---

## 12. PDF generation — `claims/pdf.py`

`build_claim_pdf(claim) -> bytes` (`pdf.py:188`) renders the full claim with
ReportLab's Platypus (flowables) layout engine:
1. Branded header + generated timestamp.
2. **Summary table** (`_summary_table`, `pdf.py:58`) — employee, branch, status,
   totals, AI score, description.
3. **Line-item table** (`_items_table`, `pdf.py:83`) — vendor/category/date/amount
   + total row. Rejected items render struck-through and excluded from the total.
4. **AI validation** flags (`_flags_block`, `pdf.py:135`).
5. **Audit trail** table (`_audit_table`, `pdf.py:150`).
6. **Each receipt image on its own page** (`_receipt_image`, `pdf.py:166`) — uses
   Pillow to fix EXIF rotation and scale-to-fit; wrapped in try/except so a
   corrupt image can't break the PDF.

The view `claim_pdf` (`views.py:204`) checks `_can_view`, then returns an
`HttpResponse(pdf_bytes, content_type="application/pdf")` with a
`Content-Disposition: attachment` header. Button is in `claim_detail.html`.

**Why ReportLab over WeasyPrint:** WeasyPrint needs GTK/Pango system libraries
(painful on Windows); ReportLab is a pure pip wheel and embeds images directly.

---

## 13. The two front-end animations

Both are **pure front-end** (no server round-trip), in `claim_detail.html` and
`claim_form.html`.

### a) Live approved-total on the decision checklist (`claim_detail.html`)
When a claim has more than one active receipt, the decision panel shows a checkbox
per receipt (ticked = keep). Each checkbox carries a `data-amount`. A small script:
- recomputes the sum of **ticked** amounts in the browser on every change,
- shows a spinner + dims the figure for ~350ms (the "recalculating" feel you asked
  for), then snaps to the new total and updates an `(N of M receipts)` counter.

It's computed client-side because all amounts are already in the DOM — instant and
no server load. The **authoritative** recalculation still happens server-side in
`recalculate_total()` on submit, so the displayed figure always matches what saves.

### b) "Claude is reading your receipts" scanning overlay (`claim_form.html`)
Because AI extraction is **synchronous** (the POST blocks until all receipts are
processed, then redirects to the review screen), submitting the upload form shows a
full-screen overlay with a receipt icon, an animated **scan line** (CSS keyframes),
bouncing dots, and a live "Reading N receipts…" message. It naturally stays up for
exactly as long as the server is working and disappears when the review page loads.
The submit button disables to prevent double submission.

---

## 14. Admin console — `claims/manage_views.py`, `templates/manage/`

A custom in-app admin (separate from Django's `/admin/`) for non-technical admins.
`admin_required` decorator (`manage_views.py:14`) gates everything on
`is_admin_role`. Provides: dashboard with role counts + paid totals
(`manage_home`), user CRUD (create/edit, set role/branch/active), and branch CRUD
(create/edit with budgets). Reuses `ManageUserForm`/`BranchForm`.

---

## 15. Auth & roles — `accounts/`

- Login/logout use Django's built-in auth views (`accounts/urls.py`) with a custom
  login template.
- `LOGIN_URL`, `LOGIN_REDIRECT_URL`, `LOGOUT_REDIRECT_URL` set in
  `config/settings.py:90`.
- Authorization is **role-based**, expressed through the `is_*` properties on
  `User` and centralised in `workflow.can_act_on` + the view-level `_visible_claims`
  / `_can_view` helpers.

---

## 16. Settings & configuration — `config/settings.py`

Everything tunable is read from a `.env` file via `python-dotenv`
(`settings.py:11`), with sensible defaults so the app runs out of the box:
- Branding: `SITE_NAME`, `SITE_TAGLINE`, `SITE_URL` (used to build email links).
- Email: console backend by default, SMTP when `EMAIL_HOST` is set (`settings.py:127`).
- AI: `AI_PROVIDER` + per-provider keys/models (`settings.py:142`).
- Policy: thresholds, category limits, max receipt age (`settings.py:149`).

`env_bool` / `env_list` helpers (`settings.py:20`) parse env values cleanly.

---

## 17. Seed / demo data — `claims/management/commands/seed_demo.py`

A custom management command (`python manage.py seed_demo`) that idempotently
creates 2 branches and one user per role (password `demo12345`):
`alice`/`bob` (employees), `manager`, `finance`, `accounts`, `admin`. This is how
you get a working multi-role demo in one command.

---

## 18. End-to-end example

1. **alice** logs in, clicks New Claim, drags in 3 receipt photos, hits *Read
   receipts with AI* → the scanning overlay appears.
2. `claim_create` saves a DRAFT claim and runs the AI on each image; she lands on
   the review screen with vendor/amount/date pre-filled.
3. She fixes one wrong amount and submits. `submit_claim` validates each item,
   scores the claim, detects no duplicates, and routes it to **MANAGER_REVIEW**
   (no critical flags). Alice and the branch manager get emails.
4. **manager** opens the claim. Because there are 3 receipts, the decision panel
   shows a checklist. He unticks one dubious receipt, types a reason, and approves.
   That receipt is rejected, the total drops, and since the new total is below the
   Finance threshold the claim skips Finance and goes to **ACCOUNTS_REVIEW**.
   Alice gets a "some receipts rejected" email + a "moved forward" email.
5. **accounts** approves (→ APPROVED) and marks paid (→ PAID). The branch's
   `spent_this_month` now includes it.
6. Anyone with access downloads the **PDF** — summary, line items (the rejected one
   struck through), audit trail, and each receipt image on its own page.
7. Every step above is a row in the **ApprovalLog**, visible as a timeline on the
   detail page.

---

## 19. Quick file map

```
config/settings.py        all configuration, env-driven
config/urls.py            top-level routing
accounts/models.py        User (roles) + Branch (budgets)
claims/models.py          Receipt, ExpenseClaim, ClaimItem, ApprovalLog
claims/ai/base.py         prompt, ExtractionResult, extractor interface
claims/ai/__init__.py     get_extractor() factory (provider switch + mock fallback)
claims/ai/validators.py   scoring + flag rules
claims/workflow.py        submit/route/approve/reject/partial-approve (state machine)
claims/views.py           request handlers (thin)
claims/urls.py            claims routing
claims/forms.py           upload, review formset, decision, admin forms
claims/notifications.py   lifecycle emails (employee + approvers)
claims/tokens.py          signed one-click email-approval tokens
claims/pdf.py             ReportLab PDF with embedded receipt images
claims/manage_views.py    in-app admin console
claims/context_processors.py  branding/policy into every template
claims/management/commands/seed_demo.py   demo users + branches
templates/                server-rendered UI (Tailwind)
```
