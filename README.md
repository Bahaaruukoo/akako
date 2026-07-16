# Akako House Ceremony Operations

Akako House is a Django application for quoting, booking, assigning, and financially tracking Ethiopian coffee ceremonies.

## Lifecycle

The application deliberately keeps four concerns separate:

1. **Quote Request** — New, Reviewing, Sent, Accepted, Declined, or Expired.
2. **Ceremony** — Awaiting Deposit, Awaiting Partner, Assigned, Ready, Payment Overdue, Completed, Failed, Cancelled, or No-show.
3. **Payment** — Deposit/final-payment amounts and Pending, Paid, Overdue, Failed, Waived, Refunded, or Forfeited status.
4. **Status History** — Permanent timestamps, staff actor, transition, and notes.

The staff-only **Business Insights** page reports requests, conversion, outcomes, and payment totals for the current week, current month, last 30 days, or a custom event-date range. Its color-coded donut chart shows completed, in-progress, failed, cancelled, and no-show percentages.

Accepting a quote freezes it and creates a separate ceremony with deposit and final-payment records. Customers can pay the deposit first or pay the entire outstanding total in one checkout. Final payment is due 24 hours before the event by default. Completed ceremonies are frozen.

## Partner self-service

Partners apply at `/partners/register/` and sign in at `/partners/login/`. A new application is inactive and submitted for staff review. The private partner workspace includes profile and payout preferences, protected supporting-document uploads, assigned ceremony tasks and task status updates, plus payout amounts and statuses.

Staff review the application and documents from the dashboard Partner page. Approving permit, insurance, or training documents updates the matching verification flag. A partner becomes assignable only when the application is approved, all three readiness checks are verified, and the partner is marked active.

Uploaded partner documents accept PDF, JPG, and PNG files up to 10 MB. They are downloaded through an authenticated permission check; production deployments should store them in private object storage with encryption and retention controls.

Document expiry dates are staff-only. A partner can be activated only when the application is approved and approved food permit, insurance, and training documents each have an expiry date strictly later than today. Required documents cannot be approved without a future expiry date. The regular `process_deadlines` job marks a document expired on its expiry date and automatically makes the partner inactive with an application status of “More information needed.” The matching permit, insurance, or training verification is removed, and the partner receives an email requesting a replacement. Existing ceremony assignments are preserved. If a current approved replacement of the same type already exists, the old document expires without deactivating the partner.

## Accounts and authentication

Akako House uses a custom `accounts.User` model with a unique email address as the login identity; there is no username field. `django-allauth` provides email verification, password reset, and email management. OAuth providers are intentionally not configured. Customers, partners, and staff use the same account model with separate role profiles and permissions.

Customer and partner registration use separate public flows, and both require email verification. Customer registration creates a profile and primary saved address. After verification, unowned guest requests with the same email are safely attached to the customer account.

For local development, verification messages are written through the configured email backend. Configure a production transactional email provider before launch. `ACCOUNT_EMAIL_VERIFICATION` defaults to `mandatory`.

### Zoho Mail delivery

To send account verification, password reset, booking, and reminder emails through
Zoho, add these values to `.env` (never commit the password):

```text
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
DEFAULT_FROM_EMAIL=support@akakohouse.com
EMAIL_HOST=smtppro.zoho.com
EMAIL_PORT=587
EMAIL_HOST_USER=support@akakohouse.com
EMAIL_HOST_PASSWORD=your-zoho-app-password
EMAIL_USE_TLS=True
EMAIL_TIMEOUT=20
```

Use the exact mailbox address for both `DEFAULT_FROM_EMAIL` and
`EMAIL_HOST_USER`. If the Zoho account has two-factor authentication enabled,
generate an app-specific password in Zoho Accounts under **Security > App
passwords** and use it as `EMAIL_HOST_PASSWORD`. This example uses Zoho's paid
custom-domain organization server. Zoho displays the exact server for the account
in its mail configuration details; use that value if it differs by plan or region.

After restarting Django, test delivery without exposing the password:

```powershell
.\.venv\Scripts\python.exe manage.py shell -c "from django.core.mail import send_mail; print(send_mail('Akako House email test', 'Zoho SMTP is connected.', None, ['your-personal-email@example.com'], fail_silently=False))"
```

## Customer self-service

Customers register at `/customers/register/`, sign in through the shared email login, and use `/customer/` to view requests, quotes, ceremonies, payment status, printable receipts, partner assignment, and cancellation-request decisions. Customers can maintain multiple saved addresses. Cancellation requests require staff approval and do not change the ceremony until approved.

## Run locally

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

Open `http://127.0.0.1:8000/dashboard/` and sign in with a staff account.

## Production database

The application uses local SQLite when `DATABASE_HOST` is empty. To use a
hosted PostgreSQL database, set these private `.env` values using the connection
details supplied by the hosting platform:

```text
DATABASE_NAME=
DATABASE_USER=
DATABASE_PASSWORD=
DATABASE_HOST=
DATABASE_PORT=
DATABASE_SSLMODE=
DATABASE_CONN_MAX_AGE=
```

Install dependencies and apply the schema to the new database:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
```

The `.env` file is excluded from version control. Do not place the database
password in source code, `.env.example`, or documentation.

## Public contact details

The shared footer and `/contact/` page use `SUPPORT_PHONE_DISPLAY`, `SUPPORT_PHONE_TEL`, `SUPPORT_EMAIL`, `SUPPORT_HOURS`, and `SUPPORT_URGENT_MESSAGE` from `.env`. Set the real business number before publishing; the separate `SUPPORT_PHONE_TEL` value should contain the international dialable form used by mobile `tel:` links.

## Deadline processing

Dashboard access checks deadlines automatically. Production should also schedule this command regularly (for example, every 15 minutes):

```powershell
.\.venv\Scripts\python.exe manage.py process_deadlines
```

It expires unanswered quotes, cancels ceremonies whose deposit deadline passed, and marks final payments overdue at the configured cutoff.
It also creates durable in-app notifications and sends idempotent email alerts for quote expiration, final-payment milestones, ceremony reminders, and partner-document warnings. Run it every 15 minutes in production; repeated runs do not duplicate a milestone notification.

Immediate events—new quote requests, quote acceptance, payment receipt, partner assignment and acceptance, and new gallery/review submissions—create notifications as the workflow action happens. Signed-in users can review them at `/notifications/`. Delivery attempts and failures are retained for staff auditing in System Admin.

The timing policy is configurable in `.env`:

```text
DEPOSIT_DUE_HOURS=48
FINAL_PAYMENT_DUE_HOURS=24
PAYMENT_REMINDER_HOURS=48
CEREMONY_REMINDER_HOURS=24
DOCUMENT_EXPIRY_WARNING_DAYS=30
AUTOMATED_NOTIFICATION_EMAILS_ENABLED=True
```

## Stripe checkout

Online checkout is disabled safely by default. Create a Stripe test account and configure:

```text
PAYMENT_PROVIDER=stripe
PAYMENT_CURRENCY=usd
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
PUBLIC_BASE_URL=https://your-domain.example
```

Register this endpoint for Stripe Checkout events:

```text
https://your-domain.example/payments/stripe/webhook/
```

Listen for `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, and `checkout.session.expired`. Payment obligations are fulfilled only from a verified webhook and duplicate events are safe.

## Staff roles

Create or refresh the standard Operations Manager, Quote Specialist, Ceremony Coordinator, and Finance Manager groups:

```powershell
.\.venv\Scripts\python.exe manage.py setup_roles
```

Assign staff users to the appropriate group in System Admin.

## Backups

Create a consistent timestamped SQLite backup:

```powershell
.\.venv\Scripts\python.exe manage.py backup_database
```

For a hosted production database, use the provider's managed backup and point-in-time recovery tools.

## Production security

Set `DEBUG=False`, use a long unique `SECRET_KEY`, configure HTTPS, and enable the secure cookie/HSTS settings documented in `.env.example`. Test HSTS on a staging domain before enabling preload.

Run `python manage.py check_production_readiness --strict` in deployment checks. The project supports a configurable `PRIVATE_FILE_STORAGE_BACKEND`; production must install and configure a private hosted backend rather than exposing uploaded partner documents through a public media URL. OAuth remains disabled.

## Tests

```powershell
.\.venv\Scripts\python.exe manage.py test bookings
```
