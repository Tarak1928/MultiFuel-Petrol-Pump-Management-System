# MultiFuel

MultiFuel is a multi-station petrol pump management application built with
Django. It supports Owner, Manager, and DSMS operations across station
management, fuel inventory, duty reporting, reconciliation, credit, employee
workforce, expenses, analytics, and notifications.

## Requirements

- Python 3.11
- pip

## Local setup

Create and activate a Python 3.11 virtual environment, then install dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `DJANGO_SECRET_KEY` in `.env` to a private, randomly generated value before
using the application beyond local development. The default database is SQLite.
To use PostgreSQL, set `DATABASE_URL` to the connection URL; `dj-database-url`
will configure the Django database backend.

Run database migrations, create an administrator, and start the development
server:

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

The Django admin is available at `/admin/`. The custom user model is
`accounts.User`; it supports the Owner, Manager, and DSMS roles. The initial
superuser is created as an approved Owner.

## Accounts

- Staff can request Manager or DSMS access at `/register/` by selecting an
  approved Owner. Public registration cannot create Owner accounts.
- Only the selected Owner sees and can approve that registration request.
  Accounts cannot sign in until approved.
- Sign in at `/login/`; the app routes each approved role to its own dashboard.
  Dashboard and approval endpoints enforce role checks server-side.
- Password reset is available from the sign-in page. In local development,
  reset messages are printed to the runserver console. Configure the `EMAIL_*`
  environment variables in `.env` for an SMTP server before enabling resets in
  production.
- Sign-out uses a CSRF-protected POST request.

The Owner dashboard shows real station and employee counts and pending access
requests. Owner and Manager dashboards also show the number of submitted duty
reports waiting for review. Managers see only their active station assignments
and reports; DSMS staff see their assigned station and personal duty-report
actions.

Owners can add, edit, activate, and suspend stations from `/stations/`. Each
station has a globally unique code and belongs to the Owner who created it.
Suspension preserves the station record for future historical references.

Owners can create employee profiles at `/employees/` for staff accounts they
approved, set each employee's basic monthly salary, and manage station access.
A Manager must be assigned to one or more active stations; DSMS staff must be
assigned to exactly one. Employee records and station assignments are
restricted to the employee's Owner, and inactive employees cannot access
role-protected dashboards.

Fuel setup is available from each station's **Fuel setup** action. Shared MS
(Petrol) and HSD (Diesel) fuel types are preloaded; Owners can add private fuel
types, configure tanks and nozzles, and record effective-dated station prices.
Price records are append-only and retain their audit history. Initial tank
stock is recorded as an opening inventory movement. Owners can record supplier
deliveries against invoice lines; deliveries are capacity-checked and update
tank stock and the immutable inventory ledger atomically. Owners can apply
reasoned stock adjustments immediately, while Managers submit adjustments for
Owner approval. Approved duty reports post fuel sales, testing withdrawals,
and any testing fuel returned to the tank. The station inventory page shows
current stock, low-stock alerts, recent deliveries, adjustments, and ledger
history. Tank stock is no longer editable after initial setup.

Owners can maintain station-specific lubricant products, opening/current stock,
and selling prices. DSMS staff add lubricant quantities to a duty-report draft;
sales use a price snapshot and stock is deducted atomically when that report is
submitted. Duty sales totals combine fuel and lubricant sales.

Owners can configure station-specific shift schedules. DSMS staff can create
multiple daily duty reports as drafts, edit drafts, and submit them for
verification. Each report records active nozzle meter readings, testing litres,
and fuel sales using the price effective when the shift starts; the applied
price is retained with the entry. Managers see reports only for their assigned
active stations; DSMS staff see only their own report history. Owners and
assigned Managers can move submissions into verification, approve them, or
request corrections with a required reason. DSMS staff can edit and resubmit
correction-requested reports. Approval, correction, and submission events are
shown in an immutable report review history. Lubricant stock is adjusted by
the net change on resubmission, so corrected reports do not double-deduct stock.

DSMS staff can record cash by Indian note and coin denominations, UPI, debit
and credit cards, credit sales, advances applied, OTP settlements, and
expenses against an editable duty report. The Owner configures each station's
reconciliation policy, including whether advances and OTP count toward
settlement and the permitted variance tolerance. Expected collections equal
duty sales less credit sales; actual collections include counted cash and
eligible tenders. Expenses are shown separately and do not conceal a shortage.
Owners and assigned Managers can append signed, immutable, reasoned
adjustments to submitted reports; the authorization and adjustment history is
visible on the report. Credit repayments are not recorded as sales.

Owners can maintain station-specific credit customers, vehicle details, credit
limits, and payment terms. DSMS staff record credit fuel sales against the
report's effective fuel-price history; quantities and amounts cannot exceed
the report's meter sales or the customer's available limit. Owners and
assigned Managers can record partial or full repayments against individual
sales, view customer statements, and review overdue balances. Repayments only
reduce outstanding credit and never increase duty sales or collections.
Advance settlements and confirmed OTP payments are stored as referenced
transactions and feed reconciliation according to the station policy.
Transaction references are checked to prevent reusing the same payment on
multiple reports. OTP secrets are never stored.

Owners and assigned Managers can record and correct station-scoped daily
attendance, including status, clock times, and overtime hours. Employees can
view their own attendance. Managers see only records for currently assigned
stations. Managers and DSMS staff can submit dated leave requests; Owners and
assigned Managers record an approval or rejection with a review note.
Overlapping pending or approved leave is rejected, and approved leave must be
recorded as leave attendance.

Owners generate one payroll record per employee and month, allocated to a
station. Payslips snapshot basic salary, allowances, attendance overtime, the
configured overtime rate, other deductions, and advance deductions. Advance
repayments are allocated to the oldest outstanding employee advances and
recorded against both the advance and payslip. Net pay cannot be negative;
attendance for a generated payroll period is locked. Owners can mark payroll
as paid, which atomically posts its gross amount as an approved salary expense
for the selected station. Employees can view only their own printable
payslips.

Owners can record and approve station expenses; assigned Managers and DSMS
staff can submit expenses for Owner review. Approved expenses are immutable
and appear in the station accounting summary with approved duty-report fuel
and lubricant sales. Payroll is linked to its automatically posted salary
expense to avoid counting the same payroll twice. Duty-report expense totals
remain part of cash reconciliation only and are not imported into accounting.
The accounting summary is not a full profit statement; it does not deduct fuel
purchase costs, tax, or other liabilities.

## Checks and tests

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
pytest
```

## Free demo deployment on Render

The root `render.yaml` Blueprint defines a free Render web service and uses an
external PostgreSQL connection. Create a free PostgreSQL database with a
provider such as Neon, then configure `DATABASE_URL` in Render with that
provider's pooled PostgreSQL connection string. Do not use Render's free
PostgreSQL database for persistent project data: Render deletes it after its
30-day expiry window.

Connect this repository from the Render Dashboard's **Blueprints** page and
apply the Blueprint. Set `DATABASE_URL` and the one-time initial Owner values
(`INITIAL_OWNER_USERNAME`, `INITIAL_OWNER_EMAIL`, `INITIAL_OWNER_PASSWORD`)
directly in Render; never put the database URL or Owner password in source
control. The generated owner command creates the first approved Owner on the
first deployment and will not replace an existing superuser. Remove the
`INITIAL_OWNER_*` values from Render after the first successful deployment.

The Blueprint generates `DJANGO_SECRET_KEY`, sets `DJANGO_DEBUG=0`, runs
migrations and static collection during build, and checks `/health/`. Render
supplies the public hostname, which Django adds to `ALLOWED_HOSTS` and
`CSRF_TRUSTED_ORIGINS`. `SITE_URL` is derived from the Render hostname unless explicitly overridden.

**Free-tier limitations:** Render free web services sleep after 15 minutes
without traffic, can take about a minute to wake, and have an ephemeral
filesystem. Never use local SQLite or local uploads for persistent production
data on this service. An external free database has its own quotas, policies,
availability limits, and external-traffic restrictions; configure independent
backups before storing irreplaceable business records. Render free services
cannot send SMTP traffic on common email ports, so this Blueprint uses the
console email backend; password-reset and notification emails are not
delivered to users. The notification inbox labels these as console-only.
Free hosting is suitable for a demo, not dependable business operations. Check
current provider terms and quotas before deployment.

For a custom domain, configure it in Render and set `SITE_URL` to the HTTPS
domain. Move to paid hosting and configure database backups and an email
delivery provider before relying on the service for live business records.
See [MultiFuel_Project_Summary.pdf](./MultiFuel_Project_Summary.pdf) for a
quantified project and deployment overview.

## Initial scope

- Django project configuration with SQLite by default and PostgreSQL via
  `DATABASE_URL`.
- A custom `AbstractUser` model with MultiFuel role choices, registered in the
  Django admin.
- An initial migration and an approval-status migration for the custom user
  model.
- Approval-gated registration, sign-in, password reset, and role-based
  dashboards.
- Owner-controlled approval of Manager and DSMS registration requests.
- Owner station management, including station profiles, globally unique station
  codes, and activate/suspend controls. Suspended stations are retained rather
  than deleted.
- Owner-scoped employee profiles, Manager multi-station assignments, and
  single-station DSMS assignments.
- Fuel types, station tanks and nozzles, capacity validation, and append-only
  effective-dated fuel price history.
- Station-specific shift schedules and owner-, manager-, and DSMS-scoped duty
  report creation, editing, submission, and history.
- Duty report verification, correction requests, resubmission, approval, and
  immutable review history with station-scoped Manager/Owner authorization.
- Nozzle-level meter readings, testing deductions, fuel-sales calculations,
  effective-price lookup, and historical price snapshots.
- Station-specific lubricant catalogs, stock and price management, duty-report
  sales snapshots, and stock deduction on report submission.
- Duty-report payment collections with cash denominations, distinct credit
  sales, configurable reconciliation, variance status, and immutable authorized
  adjustments.
- Station-scoped credit customers, report-linked fuel credit sales with
  historical prices, credit-limit enforcement, per-sale repayments, customer
  statements, and overdue balance reporting.
- Referenced advance settlements and OTP transactions connected to payment
  reconciliation, with duplicate transaction protection.
- Fuel deliveries with supplier/invoice details, capacity validation, and
  atomic stock and ledger updates.
- Immutable inventory movements for opening balances, deliveries, approved
  duty-report fuel sales, testing fuel withdrawals and returns, and approved
  leakage, evaporation, or other adjustments.
- Station-scoped inventory views with low-stock alerts and Manager-requested
  adjustments requiring Owner approval.
- Daily attendance with station-scoped Owner/Manager access, employee-only
  personal history, and approved leave requests with review notes.
- Employee salary profiles, immutable monthly payroll snapshots, overtime and
  deduction calculations, advance repayment allocation, and printable
  employee-scoped payslips.
- Station expenses with Manager/DSMS submissions, Owner approvals, immutable
  decision history, and category/date reporting.
- Station-wise approved sales and expense summaries; paid payroll is
  automatically linked to a single salary expense entry.
- Role-scoped analytics for sales and expenses, fuel inventory movements,
  duty-report reconciliation, and workforce attendance/payroll at `/reports/`.
- CSV, Excel, and PDF downloads for each analytics report.
- An in-app notification inbox and email notifications for staff approval,
  duty-report review, expense decisions, inventory adjustments, leave review,
  and payroll payment. Email uses the configured Django email backend.
  Configure `SITE_URL` with the public application URL for email links.
- Tests for roles, approval, authentication, authorization, password reset,
  station management, duty-report workflows, station-level isolation, cash
  reconciliation, adjustment authorization, fuel deliveries, inventory
  adjustments, duty-report fuel stock movements, attendance, leave decisions,
  payroll calculations, advance repayments, expense/accounting workflows,
  analytics access/exports, and notification delivery.

The accounting summary is not a full profit statement, and analytics exclude
fuel purchase costs, taxes, and other liabilities. Existing staff accounts
from before owner-scoped registration may need their requesting/approving
Owner linked by a system administrator before an employee profile can be
created.
