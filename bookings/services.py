from datetime import datetime

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import (
    AvailabilityOffer,
    CapacityHold,
    Ceremony,
    Partner,
    PartnerAvailability,
    PartnerDocument,
    PartnerPayout,
    PartnerTask,
    Payment,
    PaymentCheckout,
    QuoteRequest,
    Notification,
)
from .notifications import (
    notify_assignment,
    notify_ceremony_reminder_records,
    notify_document_expired,
    notify_document_expiring,
    notify_capacity_hold_created,
    notify_capacity_hold_released,
    notify_final_payment_reminder,
    notify_payment_received,
    notify_quote_expired,
)


ACTIVE_HOLD_STATUSES = [
    CapacityHold.Status.TEMPORARY,
    CapacityHold.Status.CONFIRMED,
    CapacityHold.Status.CONVERTED,
]


def partner_conflict_reason(partner, quote, *, exclude_hold=None):
    """Return a human-readable conflict, or an empty string when capacity is clear."""
    if not (
        partner.active
        and partner.application_status == Partner.ApplicationStatus.APPROVED
        and partner.food_permit_verified
        and partner.insurance_verified
        and partner.cultural_training_verified
    ):
        return "Partner is not active and fully verified."

    start, end = quote.occupied_window()
    unavailable = partner.availability.filter(date=quote.event_date, available=False)
    for entry in unavailable:
        if not entry.start_time or not entry.end_time:
            return "Partner marked the full day unavailable."
        block_start = timezone.make_aware(
            datetime.combine(entry.date, entry.start_time),
            timezone.get_current_timezone(),
        )
        block_end = timezone.make_aware(
            datetime.combine(entry.date, entry.end_time),
            timezone.get_current_timezone(),
        )
        if start < block_end and end > block_start:
            return "Partner marked this time unavailable."

    holds = partner.capacity_holds.filter(
        status__in=ACTIVE_HOLD_STATUSES,
        event_start__lt=end,
        event_end__gt=start,
    ).exclude(quote=quote)
    if exclude_hold:
        holds = holds.exclude(pk=exclude_hold.pk)
    if holds.exists():
        return "Partner already has an overlapping reservation."

    ceremonies = partner.ceremonies.exclude(
        status__in=[Ceremony.Status.CANCELLED, Ceremony.Status.FAILED, Ceremony.Status.NO_SHOW]
    ).exclude(quote=quote)
    for ceremony in ceremonies.select_related("quote"):
        other_start, other_end = ceremony.quote.occupied_window()
        if start < other_end and end > other_start:
            return "Partner already has an overlapping ceremony."
    return ""


def eligible_partners_for_quote(quote):
    candidates = Partner.objects.filter(
        active=True,
        application_status=Partner.ApplicationStatus.APPROVED,
        food_permit_verified=True,
        insurance_verified=True,
        cultural_training_verified=True,
    ).order_by("name")
    return [partner for partner in candidates if not partner_conflict_reason(partner, quote)]


@transaction.atomic
def accept_availability_offer(offer, *, notes="", confirmed_by_staff=False):
    offer = AvailabilityOffer.objects.select_for_update().select_related("quote", "partner").get(pk=offer.pk)
    if offer.status != AvailabilityOffer.Status.PENDING:
        raise ValueError("This availability request is no longer awaiting a response.")
    if offer.expires_at <= timezone.now():
        offer.status = AvailabilityOffer.Status.EXPIRED
        offer.responded_at = timezone.now()
        offer.save(update_fields=["status", "responded_at"])
        raise ValueError("This availability request has expired.")
    conflict = partner_conflict_reason(offer.partner, offer.quote)
    if conflict:
        raise ValueError(conflict)

    start, end = offer.quote.occupied_window()
    offer.status = AvailabilityOffer.Status.ACCEPTED
    offer.response_notes = notes or ("Confirmed by staff." if confirmed_by_staff else "")
    offer.responded_at = timezone.now()
    offer.save(update_fields=["status", "response_notes", "responded_at"])
    replaced_holds = list(
        CapacityHold.objects.filter(quote=offer.quote, status__in=ACTIVE_HOLD_STATUSES)
    )
    for replaced_hold in replaced_holds:
        replaced_hold.status = CapacityHold.Status.RELEASED
        replaced_hold.release_reason = "Replaced by a newly accepted partner hold."
        replaced_hold.save(update_fields=["status", "release_reason", "updated_at"])
        transaction.on_commit(
            lambda hold=replaced_hold: notify_capacity_hold_released(hold)
        )
    hold = CapacityHold.objects.create(
        quote=offer.quote,
        partner=offer.partner,
        offer=offer,
        event_start=start,
        event_end=end,
        status=CapacityHold.Status.TEMPORARY,
        expires_at=offer.quote.quote_expires_at or timezone.now() + timezone.timedelta(days=7),
    )
    offer.quote.availability_offers.filter(status=AvailabilityOffer.Status.PENDING).exclude(pk=offer.pk).update(
        status=AvailabilityOffer.Status.CANCELLED,
        responded_at=timezone.now(),
    )
    transaction.on_commit(
        lambda: notify_capacity_hold_created(hold, confirmed_by_staff=confirmed_by_staff)
    )
    return hold


def release_quote_holds(quote, reason, *, expired=False):
    holds = list(quote.capacity_holds.filter(status__in=ACTIVE_HOLD_STATUSES))
    new_status = CapacityHold.Status.EXPIRED if expired else CapacityHold.Status.RELEASED
    for hold in holds:
        hold.status = new_status
        hold.release_reason = reason
        hold.save(update_fields=["status", "release_reason", "updated_at"])
        transaction.on_commit(lambda hold=hold: notify_capacity_hold_released(hold))
    return len(holds)


@transaction.atomic
def convert_capacity_hold(ceremony, *, changed_by=None):
    """Turn reserved capacity into the operational partner task after payment."""
    hold = ceremony.quote.capacity_holds.select_for_update().filter(
        status__in=[CapacityHold.Status.CONFIRMED, CapacityHold.Status.CONVERTED]
    ).first()
    if not hold:
        raise ValueError("Partner coverage is no longer confirmed for this ceremony.")
    conflict = partner_conflict_reason(hold.partner, ceremony.quote, exclude_hold=hold)
    if conflict:
        ceremony.coverage_status = Ceremony.CoverageStatus.UNCOVERED
        ceremony.save(update_fields=["coverage_status", "updated_at"])
        ceremony.history.create(
            from_status=ceremony.status,
            to_status=ceremony.status,
            note=f"Reserved partner could not be converted: {conflict}",
            changed_by=changed_by,
        )
        return None

    previous = ceremony.status
    ceremony.assigned_partner = hold.partner
    ceremony.coverage_status = Ceremony.CoverageStatus.CONFIRMED
    final = ceremony.final_payment
    ceremony.status = (
        Ceremony.Status.READY
        if final and final.status in [Payment.Status.PAID, Payment.Status.WAIVED]
        else Ceremony.Status.ASSIGNED
    )
    ceremony.save(update_fields=["assigned_partner", "coverage_status", "status", "updated_at"])
    task, _ = PartnerTask.objects.get_or_create(
        ceremony=ceremony, defaults={"partner": hold.partner}
    )
    if task.partner_id != hold.partner_id:
        task.partner = hold.partner
        task.status = PartnerTask.Status.ASSIGNED
        task.save()
    PartnerPayout.objects.get_or_create(task=task, defaults={"partner": hold.partner})
    hold.status = CapacityHold.Status.CONVERTED
    hold.expires_at = None
    hold.save(update_fields=["status", "expires_at", "updated_at"])
    ceremony.history.create(
        from_status=previous,
        to_status=ceremony.status,
        note=f"Reserved capacity converted to an assignment for {hold.partner.name}.",
        changed_by=changed_by,
    )
    transaction.on_commit(lambda: notify_assignment(ceremony))
    transaction.on_commit(lambda: send_assignment_confirmation_email(ceremony))
    return task


def build_absolute_quote_url(request, quote_request):
    path = reverse("quote_review", kwargs={"public_id": quote_request.public_id})
    if request is None:
        return path
    return request.build_absolute_uri(path)


def claim_customer_records(customer):
    """Attach unowned guest requests after the account email is verified."""
    email = customer.user.email.strip().lower()
    if not email:
        return 0
    claimed_quotes = QuoteRequest.objects.filter(
        customer__isnull=True,
        email__iexact=email,
    ).update(customer=customer)
    Notification.objects.filter(
        recipient__isnull=True,
        recipient_email__iexact=email,
    ).update(recipient=customer.user)
    return claimed_quotes


def send_quote_email(quote_request, quote_url):
    subject = "Your Akako House coffee ceremony quote"
    message = (
        f"Hi {quote_request.customer_name or 'there'},\n\n"
        "Your Ethiopian coffee ceremony quote is ready.\n\n"
        f"Event date: {quote_request.event_date}\n"
        f"Location: {quote_request.location}\n"
        f"Guest count: {quote_request.guest_count}\n"
        f"Quote amount: ${quote_request.quoted_amount}\n"
    )
    if quote_request.deposit_amount:
        message += f"Deposit amount: ${quote_request.deposit_amount}\n"
    if quote_request.quote_notes:
        message += f"\nNotes:\n{quote_request.quote_notes}\n"
    message += f"\nReview your quote here:\n{quote_url}\n\nThank you,\nAkako House"

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [quote_request.email],
        fail_silently=False,
    )


def send_availability_offer_email(offer, workspace_url):
    send_mail(
        "Akako House availability request",
        (
            f"Hi {offer.partner.contact_name},\n\n"
            "Can you reserve the following ceremony time?\n"
            f"Date: {offer.quote.event_date}\n"
            f"Start: {offer.quote.event_time}\n"
            f"Location: {offer.quote.location}\n"
            f"Guests: {offer.quote.guest_count}\n"
            f"Respond by: {offer.expires_at}\n\n"
            f"Accept or decline in your partner workspace:\n{workspace_url}\n"
        ),
        settings.DEFAULT_FROM_EMAIL,
        [offer.partner.email],
        fail_silently=False,
    )


def build_payment_url(ceremony):
    return settings.PUBLIC_BASE_URL.rstrip("/") + reverse(
        "ceremony_payment", args=[ceremony.public_id]
    )


def send_payment_options_email(ceremony, payment_url=None):
    payment_url = payment_url or build_payment_url(ceremony)
    deposit = ceremony.deposit_payment
    final = ceremony.final_payment
    message = (
        f"Hi {ceremony.quote.customer_name or 'there'},\n\n"
        "Thank you for accepting your Akako House ceremony quote. "
        "You may pay the initial deposit and the balance later, or pay the full amount now.\n\n"
        f"Deposit: ${deposit.expected_amount}\n"
        f"Final balance: ${final.expected_amount}\n"
        f"Payment options: {payment_url}\n\n"
        "Thank you,\nAkako House"
    )
    send_mail(
        "Choose your Akako House ceremony payment option",
        message,
        settings.DEFAULT_FROM_EMAIL,
        [ceremony.quote.email],
        fail_silently=False,
    )


def send_payment_confirmation_email(ceremony, description, amount):
    send_mail(
        f"Akako House payment confirmation — {description}",
        (
            f"Hi {ceremony.quote.customer_name or 'there'},\n\n"
            f"We received your {description.lower()} payment of ${amount}.\n"
            f"Ceremony date: {ceremony.quote.event_date}\n"
            f"Current status: {ceremony.get_status_display()}\n\n"
            f"View payment status: {build_payment_url(ceremony)}\n\n"
            "Thank you,\nAkako House"
        ),
        settings.DEFAULT_FROM_EMAIL,
        [ceremony.quote.email],
        fail_silently=False,
    )


def send_payment_reminder_email(payment):
    ceremony = payment.ceremony
    send_mail(
        f"Reminder: {payment.get_payment_type_display()} is due",
        (
            f"Hi {ceremony.quote.customer_name or 'there'},\n\n"
            f"Your {payment.get_payment_type_display().lower()} of ${payment.expected_amount} "
            f"is due {payment.due_at}.\n"
            f"Pay securely here: {build_payment_url(ceremony)}\n\n"
            "Thank you,\nAkako House"
        ),
        settings.DEFAULT_FROM_EMAIL,
        [ceremony.quote.email],
        fail_silently=False,
    )


def send_payment_overdue_email(payment):
    ceremony = payment.ceremony
    consequence = (
        "The ceremony request has been cancelled because the deposit deadline passed."
        if payment.payment_type == Payment.PaymentType.DEPOSIT
        else "The ceremony is now marked payment overdue and needs immediate attention."
    )
    send_mail(
        f"Action required: {payment.get_payment_type_display()} overdue",
        (
            f"Hi {ceremony.quote.customer_name or 'there'},\n\n"
            f"The {payment.get_payment_type_display().lower()} of ${payment.expected_amount} was not received by its deadline.\n"
            f"{consequence}\n"
            f"Payment details: {build_payment_url(ceremony)}\n\n"
            "Akako House"
        ),
        settings.DEFAULT_FROM_EMAIL,
        [ceremony.quote.email],
        fail_silently=False,
    )


def send_ceremony_reminder_email(ceremony):
    quote = ceremony.quote
    customer_message = (
        f"Hi {quote.customer_name or 'there'},\n\n"
        f"Your Akako House ceremony is coming up on {quote.event_date}"
        f"{f' at {quote.event_time}' if quote.event_time else ''}.\n"
        f"Location: {quote.location}\n"
        f"Cultural Ambassador: {ceremony.assigned_partner or 'Being confirmed'}\n\n"
        "We look forward to serving you.\nAkako House"
    )
    send_mail(
        "Reminder: your Akako House ceremony is coming up",
        customer_message,
        settings.DEFAULT_FROM_EMAIL,
        [quote.email],
        fail_silently=False,
    )
    if ceremony.assigned_partner:
        send_mail(
            f"Upcoming Akako House assignment — {quote.event_date}",
            (
                f"Ceremony: {quote.get_event_type_display()}\n"
                f"Date/time: {quote.event_date} {quote.event_time or ''}\n"
                f"Location: {quote.location}\nGuests: {quote.guest_count}\n"
                f"Customer: {quote.customer_name}\nPhone: {quote.phone}\n"
            ),
            settings.DEFAULT_FROM_EMAIL,
            [ceremony.assigned_partner.email],
            fail_silently=False,
        )


def send_assignment_confirmation_email(ceremony):
    quote = ceremony.quote
    send_mail(
        "Your Akako House Cultural Ambassador is assigned",
        (
            f"Hi {quote.customer_name or 'there'},\n\n"
            f"{ceremony.assigned_partner.name} has been assigned to your ceremony on {quote.event_date}.\n"
            f"Location: {quote.location}\n\nThank you,\nAkako House"
        ),
        settings.DEFAULT_FROM_EMAIL,
        [quote.email],
        fail_silently=False,
    )
    send_mail(
        f"New Akako House ceremony assignment — {quote.event_date}",
        (
            f"Customer: {quote.customer_name}\nPhone: {quote.phone}\n"
            f"Date/time: {quote.event_date} {quote.event_time or ''}\n"
            f"Location: {quote.location}\nGuests: {quote.guest_count}\n"
        ),
        settings.DEFAULT_FROM_EMAIL,
        [ceremony.assigned_partner.email],
        fail_silently=False,
    )


@transaction.atomic
def process_partner_document_expirations():
    """Expire approved documents and remove affected partners from assignment."""
    today = timezone.localdate()
    now = timezone.now()
    expired_documents = list(
        PartnerDocument.objects.select_related("partner").filter(
            review_status=PartnerDocument.ReviewStatus.APPROVED,
            expiry_date__lte=today,
        )
    )
    verification_fields = {
        PartnerDocument.DocumentType.FOOD_PERMIT: "food_permit_verified",
        PartnerDocument.DocumentType.INSURANCE: "insurance_verified",
        PartnerDocument.DocumentType.TRAINING: "cultural_training_verified",
    }
    for document in expired_documents:
        document.review_status = PartnerDocument.ReviewStatus.EXPIRED
        document.expiry_processed_at = now
        document.save(update_fields=["review_status", "expiry_processed_at"])

        partner = document.partner
        has_current_replacement = PartnerDocument.objects.filter(
            partner=partner,
            document_type=document.document_type,
            review_status=PartnerDocument.ReviewStatus.APPROVED,
        ).filter(Q(expiry_date__isnull=True) | Q(expiry_date__gt=today)).exists()
        if has_current_replacement:
            continue
        partner.active = False
        partner.application_status = Partner.ApplicationStatus.NEEDS_INFO
        update_fields = ["active", "application_status"]
        verification_field = verification_fields.get(document.document_type)
        if verification_field:
            setattr(partner, verification_field, False)
            update_fields.append(verification_field)
        partner.save(update_fields=update_fields)
        notify_document_expired(document)
    return len(expired_documents)


def process_partner_document_warnings():
    today = timezone.localdate()
    warning_days = getattr(settings, "DOCUMENT_EXPIRY_WARNING_DAYS", 30)
    documents = PartnerDocument.objects.select_related("partner__user").filter(
        review_status=PartnerDocument.ReviewStatus.APPROVED,
        expiry_date__gt=today,
        expiry_date__lte=today + timezone.timedelta(days=warning_days),
    )
    warned = 0
    for document in documents:
        days_left = (document.expiry_date - today).days
        milestone = 1 if days_left <= 1 else 7 if days_left <= 7 else warning_days
        notify_document_expiring(document, milestone)
        warned += 1
    return warned


def process_workflow_deadlines(changed_by=None):
    """Expire unanswered quotes and enforce deposit/final-payment deadlines."""
    process_partner_document_warnings()
    process_partner_document_expirations()
    expiring_quotes = list(QuoteRequest.objects.filter(
        status=QuoteRequest.Status.QUOTED,
        quote_expires_at__lt=timezone.now(),
    ))
    for quote in expiring_quotes:
        quote.status = QuoteRequest.Status.EXPIRED
        quote.save(update_fields=["status", "updated_at"])
        release_quote_holds(quote, "Customer quote expired.", expired=True)
        notify_quote_expired(quote)
    expired_quotes = len(expiring_quotes)

    AvailabilityOffer.objects.filter(
        status=AvailabilityOffer.Status.PENDING,
        expires_at__lt=timezone.now(),
    ).update(status=AvailabilityOffer.Status.EXPIRED, responded_at=timezone.now())
    expired_holds = list(CapacityHold.objects.filter(
        status=CapacityHold.Status.TEMPORARY,
        expires_at__lt=timezone.now(),
    ))
    for hold in expired_holds:
        hold.status = CapacityHold.Status.EXPIRED
        hold.release_reason = "Temporary reservation expired."
        hold.save(update_fields=["status", "release_reason", "updated_at"])
        notify_capacity_hold_released(hold)

    ceremonies_checked = 0
    for ceremony in Ceremony.objects.exclude(
        status__in=[
            Ceremony.Status.COMPLETED,
            Ceremony.Status.FAILED,
            Ceremony.Status.CANCELLED,
            Ceremony.Status.NO_SHOW,
        ]
    ):
        now = timezone.now()
        final_payment = ceremony.final_payment
        hours_until_event = (ceremony.event_datetime() - now).total_seconds() / 3600
        if final_payment and final_payment.status == Payment.Status.PENDING:
            if 24 < hours_until_event <= 48:
                notify_final_payment_reminder(ceremony, 48)
            elif 0 < hours_until_event <= 24:
                notify_final_payment_reminder(ceremony, 24)

        ceremony.refresh_deadlines(changed_by=changed_by)
        if ceremony.status == Ceremony.Status.CANCELLED:
            release_quote_holds(ceremony.quote, "Ceremony cancelled after a missed deadline.")
        now = timezone.now()
        reminder_window = now + timezone.timedelta(hours=settings.PAYMENT_REMINDER_HOURS)
        for payment in ceremony.payments.all():
            try:
                if (
                    payment.status == Payment.Status.PENDING
                    and payment.payment_type != Payment.PaymentType.FINAL
                    and payment.due_at
                    and now < payment.due_at <= reminder_window
                    and not payment.reminder_sent_at
                ):
                    send_payment_reminder_email(payment)
                    payment.reminder_sent_at = now
                    payment.save(update_fields=["reminder_sent_at", "updated_at"])
                elif payment.status == Payment.Status.OVERDUE and not payment.overdue_notified_at:
                    if not (
                        payment.payment_type == Payment.PaymentType.FINAL
                        and 0 < hours_until_event <= 24
                    ):
                        send_payment_overdue_email(payment)
                    payment.overdue_notified_at = now
                    payment.save(update_fields=["overdue_notified_at", "updated_at"])
            except Exception:
                continue

        ceremony_window = now + timezone.timedelta(hours=settings.CEREMONY_REMINDER_HOURS)
        if (
            ceremony.status in [Ceremony.Status.ASSIGNED, Ceremony.Status.READY]
            and not ceremony.reminder_sent_at
            and now < ceremony.event_datetime() <= ceremony_window
        ):
            notify_ceremony_reminder_records(ceremony)
            try:
                send_ceremony_reminder_email(ceremony)
            except Exception:
                pass
            else:
                ceremony.reminder_sent_at = now
                ceremony.save(update_fields=["reminder_sent_at", "updated_at"])
        ceremonies_checked += 1
    return expired_quotes, ceremonies_checked


@transaction.atomic
def fulfill_payment_checkout(checkout, payment_reference=""):
    """Idempotently allocate a confirmed provider checkout to payment obligations."""
    checkout = PaymentCheckout.objects.select_for_update().select_related("ceremony").get(
        pk=checkout.pk
    )
    if checkout.status == PaymentCheckout.Status.COMPLETED:
        return False

    ceremony = checkout.ceremony
    if ceremony.status == Ceremony.Status.COMPLETED:
        raise ValueError("A completed ceremony cannot accept checkout fulfillment.")

    if checkout.payment_choice == PaymentCheckout.Choice.FULL:
        payments = ceremony.payments.filter(
            payment_type__in=[Payment.PaymentType.DEPOSIT, Payment.PaymentType.FINAL]
        ).exclude(status__in=[Payment.Status.PAID, Payment.Status.WAIVED])
    else:
        payments = ceremony.payments.filter(payment_type=checkout.payment_choice).exclude(
            status__in=[Payment.Status.PAID, Payment.Status.WAIVED]
        )

    now = timezone.now()
    settled = []
    for payment in payments:
        payment.status = Payment.Status.PAID
        payment.received_amount = payment.expected_amount
        payment.paid_at = now
        payment.provider_reference = payment_reference or checkout.provider_session_id
        payment.notes = "Confirmed by signed Stripe webhook."
        payment.save()
        settled.append(payment.get_payment_type_display())

    final = ceremony.final_payment
    previous_status = ceremony.status
    if not ceremony.terminal:
        if ceremony.assigned_partner and final and final.status in [Payment.Status.PAID, Payment.Status.WAIVED]:
            ceremony.status = Ceremony.Status.READY
        elif ceremony.status == Ceremony.Status.AWAITING_DEPOSIT:
            ceremony.status = Ceremony.Status.AWAITING_PARTNER
    ceremony.outcome_reason = ""
    ceremony.save(update_fields=["status", "outcome_reason", "updated_at"])
    ceremony.history.create(
        from_status=previous_status,
        to_status=ceremony.status,
        note=f"Online checkout confirmed; settled {', '.join(settled) or 'existing obligations'}.",
    )
    if settled and not ceremony.assigned_partner and ceremony.quote.capacity_holds.filter(
        status=CapacityHold.Status.CONFIRMED
    ).exists():
        convert_capacity_hold(ceremony)

    checkout.status = PaymentCheckout.Status.COMPLETED
    checkout.provider_payment_reference = payment_reference
    checkout.completed_at = now
    checkout.save(
        update_fields=["status", "provider_payment_reference", "completed_at", "updated_at"]
    )
    if settled:
        description = "paid in full" if checkout.payment_choice == PaymentCheckout.Choice.FULL else settled[0]
        transaction.on_commit(
            lambda: send_payment_confirmation_email(ceremony, description, checkout.amount)
        )
        transaction.on_commit(
            lambda: notify_payment_received(ceremony, description, checkout.amount)
        )
    return True
