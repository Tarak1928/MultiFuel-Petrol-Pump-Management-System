# MultiFuel

MultiFuel is a multi-station petrol pump management application built with Django.
The project foundation, custom account model, and initial authentication
workflows are in place. Station operations and business modules are being added
in subsequent stages.

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
requests. Manager and DSMS dashboards show the active station assignments
created by the Owner, plus the shift and duty-report actions available to them.

Owners can add, edit, activate, and suspend stations from `/stations/`. Each
station has a globally unique code and belongs to the Owner who created it.
Suspension preserves the station record for future historical references.

Owners can create employee profiles at `/employees/` for staff accounts they
approved. A Manager must be assigned to one or more active stations; DSMS staff
must be assigned to exactly one. Employee records and station assignments are
restricted to the employee's Owner, and inactive employees cannot access
role-protected dashboards.

Fuel setup is available from each station's **Fuel setup** action. Shared MS
(Petrol) and HSD (Diesel) fuel types are preloaded; Owners can add private fuel
types, configure tanks and nozzles, and record effective-dated station prices.
Price records are append-only and retain their audit history. Tank stock is an
initial setup value only; deliveries, sales, and inventory-ledger adjustments
are implemented in the later inventory stage.

Owners can maintain station-specific lubricant products, opening/current stock,
and selling prices. DSMS staff add lubricant quantities to a duty-report draft;
sales use a price snapshot and stock is deducted atomically when that report is
submitted. Duty sales totals combine fuel and lubricant sales.

Owners can configure station-specific shift schedules. DSMS staff can create
multiple daily duty reports as drafts, edit drafts, and submit them for
verification. Each report records active nozzle meter readings, testing litres,
and fuel sales using the price effective when the shift starts; the applied
price is retained with the entry. Managers see reports only for their assigned
active stations; DSMS staff see only their own report history. Approval and
correction workflows are implemented in a later stage.

## Checks and tests

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
pytest
```

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
- Nozzle-level meter readings, testing deductions, fuel-sales calculations,
  effective-price lookup, and historical price snapshots.
- Station-specific lubricant catalogs, stock and price management, duty-report
  sales snapshots, and stock deduction on report submission.
- Tests for roles, approval, authentication, authorization, password reset,
  station management, duty-report workflows, and station-level isolation.

Fuel inventory movements, report approvals, payment reconciliation, and payroll
have not yet been implemented. Existing staff
accounts from before owner-scoped registration may need their
requesting/approving Owner linked by a system administrator before an employee
profile can be created.
