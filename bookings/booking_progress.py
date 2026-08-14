from decimal import Decimal

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import BookingMilestone, Ceremony, Invoice, InvoiceTransaction, Notification, Payment, PaymentAllocation
from .notifications import create_notification, notify_staff, quote_customer_user


STAGES = [
    BookingMilestone.Stage.QUOTE_ACCEPTED,
    BookingMilestone.Stage.INVOICE_SENT,
    BookingMilestone.Stage.DEPOSIT_PENDING,
    BookingMilestone.Stage.DEPOSIT_RECEIVED,
    BookingMilestone.Stage.BOOKING_CONFIRMED,
    BookingMilestone.Stage.BALANCE_PENDING,
    BookingMilestone.Stage.PAID_IN_FULL,
    BookingMilestone.Stage.EVENT_READY,
    BookingMilestone.Stage.COMPLETED,
]


def _settled(payment):
    return payment is None or payment.status in (Payment.Status.PAID, Payment.Status.WAIVED)


def reached_booking_stages(ceremony):
    deposit = ceremony.deposit_payment
    final = ceremony.final_payment
    invoices = ceremony.invoices.exclude(status=Invoice.Status.VOID)
    stages = {BookingMilestone.Stage.QUOTE_ACCEPTED}
    if invoices.filter(last_emailed_at__isnull=False).exists():
        stages.add(BookingMilestone.Stage.INVOICE_SENT)
    if deposit and not _settled(deposit):
        stages.add(BookingMilestone.Stage.DEPOSIT_PENDING)
    if _settled(deposit):
        stages.add(BookingMilestone.Stage.DEPOSIT_RECEIVED)
        if final and not _settled(final):
            stages.add(BookingMilestone.Stage.BALANCE_PENDING)
    coverage_confirmed = (
        ceremony.coverage_status == Ceremony.CoverageStatus.CONFIRMED
        and ceremony.assigned_partner_id is not None
    )
    if _settled(deposit) and coverage_confirmed:
        stages.add(BookingMilestone.Stage.BOOKING_CONFIRMED)
    if _settled(deposit) and _settled(final):
        stages.add(BookingMilestone.Stage.PAID_IN_FULL)
    if ceremony.status in (Ceremony.Status.READY, Ceremony.Status.COMPLETED):
        stages.add(BookingMilestone.Stage.EVENT_READY)
    if ceremony.status == Ceremony.Status.COMPLETED:
        stages.add(BookingMilestone.Stage.COMPLETED)
    return stages


def _notify_milestone(ceremony, stage):
    quote = ceremony.quote
    details = {
        BookingMilestone.Stage.BOOKING_CONFIRMED: (
            Notification.Kind.BOOKING_CONFIRMED,
            "Your booking is confirmed",
            f"Your Akako House ceremony for {quote.event_date} is confirmed. Your initial payment and ceremony-team coverage are secured.",
        ),
        BookingMilestone.Stage.PAID_IN_FULL: (
            Notification.Kind.BOOKING_PAID,
            "Your booking is paid in full",
            f"Your Akako House ceremony for {quote.event_date} has no remaining balance.",
        ),
    }
    if stage not in details:
        return
    kind, title, message = details[stage]
    customer_url = reverse("customer_quote_detail", args=[quote.public_id]) if quote.customer_id else reverse("quote_review", args=[quote.public_id])
    event_key = f"ceremony:{ceremony.pk}:milestone:{stage}"
    create_notification(
        kind=kind, title=title, message=message, event_key=event_key,
        recipient=quote_customer_user(quote), email_address=quote.email,
        action_url=customer_url, send_email=False,
    )
    notify_staff(
        kind=kind, title=title,
        message=f"{quote.customer_name}'s {quote.event_date} booking reached: {title}.",
        event_key=f"{event_key}:staff",
        action_url=reverse("ceremony_detail", args=[ceremony.public_id]),
    )


@transaction.atomic
def sync_booking_milestones(ceremony, *, actor=None, source="derived"):
    ceremony = Ceremony.objects.select_for_update().select_related("quote", "assigned_partner").get(pk=ceremony.pk)
    reached = reached_booking_stages(ceremony)
    existing = set(ceremony.milestones.values_list("stage", flat=True))
    for stage in STAGES:
        if stage in reached and stage not in existing:
            BookingMilestone.objects.create(ceremony=ceremony, stage=stage, source=source, recorded_by=actor)
            transaction.on_commit(lambda c=ceremony, s=stage: _notify_milestone(c, s))
    return booking_progress(ceremony)


def booking_progress(ceremony):
    reached = reached_booking_stages(ceremony)
    history = {item.stage: item.reached_at for item in ceremony.milestones.all()}
    active = None
    for stage in STAGES:
        if stage in reached:
            active = stage
    if BookingMilestone.Stage.DEPOSIT_PENDING in reached:
        active = BookingMilestone.Stage.DEPOSIT_PENDING
    elif BookingMilestone.Stage.BALANCE_PENDING in reached and BookingMilestone.Stage.EVENT_READY not in reached:
        active = BookingMilestone.Stage.BALANCE_PENDING
    return [
        {
            "key": stage,
            "label": BookingMilestone.Stage(stage).label,
            "reached": stage in reached or stage in history,
            "reached_at": history.get(stage),
            "current": stage == active,
        }
        for stage in STAGES
    ]


@transaction.atomic
def allocate_invoice_transaction(item, *, actor=None):
    item = InvoiceTransaction.objects.select_for_update().select_related("invoice__ceremony").get(pk=item.pk)
    ceremony = item.invoice.ceremony
    remaining = item.amount
    paid_labels = []
    for payment in Payment.objects.select_for_update().filter(
        ceremony=ceremony,
        payment_type__in=(Payment.PaymentType.DEPOSIT, Payment.PaymentType.FINAL),
    ).order_by("payment_type"):
        outstanding = max(payment.expected_amount - payment.received_amount, Decimal("0.00"))
        applied = min(remaining, outstanding)
        if applied <= 0:
            continue
        PaymentAllocation.objects.create(transaction=item, payment=payment, amount=applied)
        payment.received_amount += applied
        payment.provider_reference = item.reference
        payment.notes = item.notes
        if payment.received_amount >= payment.expected_amount:
            payment.status = Payment.Status.PAID
            payment.paid_at = timezone.now()
            paid_labels.append(payment.get_payment_type_display())
        elif payment.status not in (Payment.Status.OVERDUE, Payment.Status.FAILED):
            payment.status = Payment.Status.PENDING
        payment.save()
        remaining -= applied
        if remaining <= 0:
            break

    previous = ceremony.status
    deposit, final = ceremony.deposit_payment, ceremony.final_payment
    if not ceremony.terminal:
        if _settled(final) and ceremony.assigned_partner_id:
            ceremony.status = Ceremony.Status.READY
        elif _settled(deposit) and ceremony.status in (Ceremony.Status.AWAITING_DEPOSIT, Ceremony.Status.AT_RISK):
            ceremony.status = Ceremony.Status.AWAITING_PARTNER
        if ceremony.status != previous:
            ceremony.outcome_reason = ""
            ceremony.save(update_fields=["status", "outcome_reason", "updated_at"])
            ceremony.history.create(from_status=previous, to_status=ceremony.status, note=f"Invoice payment {item.invoice.number} recorded.", changed_by=actor)
    item.invoice.refresh_status()
    sync_booking_milestones(ceremony, actor=actor, source=f"invoice:{item.invoice.number}")
    return paid_labels


@transaction.atomic
def record_linked_invoice_transaction(ceremony, *, amount, method, reference="", notes="", actor=None, received_on=None):
    invoice = ceremony.invoices.exclude(status=Invoice.Status.VOID).order_by("-created_at").first()
    if not invoice:
        return None
    item = InvoiceTransaction.objects.create(
        invoice=invoice,
        amount=amount,
        received_on=received_on or timezone.localdate(),
        method=method,
        reference=reference,
        notes=notes,
        recorded_by=actor,
    )
    allocate_invoice_transaction(item, actor=actor)
    return item

