from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

from .models import Notification


def _delivery_key(event_key, recipient=None, email_address=""):
    if recipient:
        return f"{event_key}:user:{recipient.pk}"
    return f"{event_key}:email:{email_address.strip().lower()}"


def create_notification(
    *,
    kind,
    title,
    message,
    event_key,
    recipient=None,
    email_address="",
    action_url="",
    send_email=False,
    email_subject="",
    email_message="",
):
    """Create one durable, idempotent in-app/email notification delivery."""
    email_address = (email_address or getattr(recipient, "email", "") or "").strip().lower()
    notification, created = Notification.objects.get_or_create(
        event_key=_delivery_key(event_key, recipient, email_address),
        defaults={
            "recipient": recipient,
            "recipient_email": email_address,
            "kind": kind,
            "title": title,
            "message": message,
            "action_url": action_url,
            "email_status": (
                Notification.EmailStatus.PENDING if send_email and email_address
                else Notification.EmailStatus.SKIPPED
            ),
        },
    )
    if not created or not send_email or not email_address:
        return notification, created
    if not getattr(settings, "AUTOMATED_NOTIFICATION_EMAILS_ENABLED", True):
        notification.email_status = Notification.EmailStatus.SKIPPED
        notification.save(update_fields=["email_status"])
        return notification, created
    try:
        body = email_message or message
        if action_url:
            body += f"\n\nView details: {settings.PUBLIC_BASE_URL.rstrip('/')}{action_url}"
        body += "\n\nAkako House"
        send_mail(
            email_subject or title,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [email_address],
            fail_silently=False,
        )
    except Exception as exc:
        notification.email_status = Notification.EmailStatus.FAILED
        notification.email_error = str(exc)[:500]
        notification.save(update_fields=["email_status", "email_error"])
    else:
        notification.email_status = Notification.EmailStatus.SENT
        notification.email_sent_at = timezone.now()
        notification.email_error = ""
        notification.save(update_fields=["email_status", "email_sent_at", "email_error"])
    return notification, created


def notify_staff(*, kind, title, message, event_key, action_url="", send_email=False):
    notifications = []
    staff_users = get_user_model().objects.filter(is_active=True, is_staff=True).order_by("pk")
    for user in staff_users:
        notifications.append(
            create_notification(
                kind=kind,
                title=title,
                message=message,
                event_key=event_key,
                recipient=user,
                action_url=action_url,
                send_email=send_email,
            )[0]
        )
    return notifications


def quote_customer_user(quote):
    return quote.customer.user if quote.customer_id and quote.customer.user_id else None


def notify_quote_submitted(quote):
    event_key = f"quote:{quote.pk}:submitted"
    event_time = (
        quote.event_time.strftime("%I:%M %p").lstrip("0")
        if quote.event_time
        else "To be confirmed"
    )
    customer_message = (
        f"Hi {quote.customer_name},\n\n"
        "We received your Akako House ceremony request.\n\n"
        f"Event: {quote.get_event_type_display()}\n"
        f"Date: {quote.event_date}\n"
        f"Time: {event_time}\n"
        f"Location: {quote.location}\n"
        f"Guests: {quote.guest_count}\n\n"
        "Next steps: our team will review the request, confirm ceremony-partner availability, "
        "and email you a custom quote. No payment is due until you review and accept that quote.\n\n"
        "If you do not see future messages from us, please check your spam or junk folder."
    )
    create_notification(
        kind=Notification.Kind.QUOTE_NEW,
        title="We received your ceremony request",
        message=customer_message,
        event_key=f"{event_key}:customer",
        recipient=quote_customer_user(quote),
        email_address=quote.email,
        action_url=reverse("quote_success", args=[quote.public_id]),
        send_email=True,
        email_subject="We received your Akako House ceremony request",
        email_message=customer_message,
    )

    staff_message = (
        f"{quote.customer_name} requested a {quote.get_event_type_display()} "
        f"for {quote.event_date} in {quote.location} for {quote.guest_count} guests."
    )
    staff_action_url = reverse("manage_quote", args=[quote.public_id])
    notify_staff(
        kind=Notification.Kind.QUOTE_NEW,
        title="New quote request",
        message=staff_message,
        event_key=f"{event_key}:staff",
        action_url=staff_action_url,
        send_email=False,
    )
    create_notification(
        kind=Notification.Kind.QUOTE_NEW,
        title="New quote request",
        message=staff_message,
        event_key=f"{event_key}:support",
        email_address=settings.SUPPORT_EMAIL,
        action_url=staff_action_url,
        send_email=True,
        email_subject=f"New quote request from {quote.customer_name}",
    )


def notify_quote_accepted(quote):
    action_url = reverse("customer_quote_detail", args=[quote.public_id]) if quote.customer_id else reverse("quote_review", args=[quote.public_id])
    create_notification(
        kind=Notification.Kind.QUOTE_ACCEPTED,
        title="Your quote was accepted",
        message=f"Your Akako House ceremony for {quote.event_date} is moving to payment and scheduling.",
        event_key=f"quote:{quote.pk}:accepted",
        recipient=quote_customer_user(quote),
        email_address=quote.email,
        action_url=action_url,
        send_email=False,
    )
    notify_staff(
        kind=Notification.Kind.QUOTE_ACCEPTED,
        title="Quote accepted",
        message=f"{quote.customer_name} accepted the {quote.event_date} quote.",
        event_key=f"quote:{quote.pk}:accepted:staff",
        action_url=reverse("manage_quote", args=[quote.public_id]),
    )
    create_notification(
        kind=Notification.Kind.QUOTE_ACCEPTED,
        title="Quote accepted",
        message=f"{quote.customer_name} accepted the {quote.event_date} quote and is moving to payment.",
        event_key=f"quote:{quote.pk}:accepted:support",
        email_address=settings.SUPPORT_EMAIL,
        action_url=reverse("manage_quote", args=[quote.public_id]),
        send_email=True,
        email_subject=f"Quote accepted by {quote.customer_name}",
    )


def notify_quote_declined(quote):
    create_notification(
        kind=Notification.Kind.QUOTE_DECLINED,
        title="Your quote was declined",
        message=f"We recorded your decision to decline the Akako House quote for {quote.event_date}. No payment will be taken.",
        event_key=f"quote:{quote.pk}:declined:customer",
        recipient=quote_customer_user(quote),
        email_address=quote.email,
        action_url=reverse("quote_review", args=[quote.public_id]),
        send_email=True,
        email_subject="Your Akako House quote decision",
    )
    notify_staff(
        kind=Notification.Kind.QUOTE_DECLINED,
        title="Quote declined",
        message=f"{quote.customer_name} declined the {quote.event_date} quote.",
        event_key=f"quote:{quote.pk}:declined:staff",
        action_url=reverse("manage_quote", args=[quote.public_id]),
    )
    create_notification(
        kind=Notification.Kind.QUOTE_DECLINED,
        title="Quote declined",
        message=f"{quote.customer_name} declined the {quote.event_date} quote.",
        event_key=f"quote:{quote.pk}:declined:support",
        email_address=settings.SUPPORT_EMAIL,
        action_url=reverse("manage_quote", args=[quote.public_id]),
        send_email=True,
        email_subject=f"Quote declined by {quote.customer_name}",
    )


def notify_capacity_hold_created(hold, *, confirmed_by_staff=False):
    quote, partner = hold.quote, hold.partner
    confirmation_source = "Akako House staff confirmed this with you" if confirmed_by_staff else "You accepted the availability request"
    message = (
        f"{confirmation_source}, so the ceremony time below is now temporarily reserved for you.\n\n"
        f"Date/time: {quote.event_date} {quote.event_time or ''}\n"
        f"Location: {quote.location}\nGuests: {quote.guest_count}\n"
        f"Reservation expires: {hold.expires_at}\n\n"
        "This is not yet a confirmed assignment. The customer must accept the quote. "
        "Please avoid accepting conflicting work during this temporary hold."
    )
    create_notification(
        kind=Notification.Kind.CAPACITY_HOLD_CREATED,
        title="Ceremony time temporarily reserved",
        message=message,
        event_key=f"capacity-hold:{hold.pk}:created",
        recipient=partner.user,
        email_address=partner.email,
        action_url=reverse("partner_dashboard"),
        send_email=True,
        email_subject=f"Temporary Akako House reservation — {quote.event_date}",
    )


def notify_capacity_hold_released(hold):
    quote, partner = hold.quote, hold.partner
    state = "expired" if hold.status == hold.Status.EXPIRED else "released"
    message = (
        f"The temporary reservation for {quote.event_date} at {quote.location} has been {state}. "
        f"Reason: {hold.release_reason or 'The reservation is no longer required.'} "
        "You are no longer expected to keep this time reserved."
    )
    create_notification(
        kind=Notification.Kind.CAPACITY_HOLD_RELEASED,
        title=f"Temporary reservation {state}",
        message=message,
        event_key=f"capacity-hold:{hold.pk}:{state}",
        recipient=partner.user,
        email_address=partner.email,
        action_url=reverse("partner_dashboard"),
        send_email=True,
        email_subject=f"Akako House temporary reservation {state} — {quote.event_date}",
    )


def notify_ceremony_completed(ceremony):
    quote, partner = ceremony.quote, ceremony.assigned_partner
    create_notification(
        kind=Notification.Kind.CEREMONY_COMPLETED,
        title="Thank you for gathering with Akako House",
        message=f"Your ceremony on {quote.event_date} has been marked complete. Thank you for welcoming Akako House to your gathering.",
        event_key=f"ceremony:{ceremony.pk}:completed:customer",
        recipient=quote_customer_user(quote),
        email_address=quote.email,
        action_url=reverse("customer_quote_detail", args=[quote.public_id]) if quote.customer_id else reverse("quote_review", args=[quote.public_id]),
        send_email=True,
        email_subject="Thank you for gathering with Akako House",
    )
    if partner:
        create_notification(
            kind=Notification.Kind.CEREMONY_COMPLETED,
            title="Ceremony delivery recorded",
            message=f"The {quote.event_date} ceremony has been marked delivered. Your payout is now pending staff processing.",
            event_key=f"ceremony:{ceremony.pk}:completed:partner:{partner.pk}",
            recipient=partner.user,
            email_address=partner.email,
            action_url=reverse("partner_dashboard"),
            send_email=True,
            email_subject=f"Akako House ceremony completed — {quote.event_date}",
        )


def notify_payment_received(ceremony, description, amount):
    quote = ceremony.quote
    create_notification(
        kind=Notification.Kind.PAYMENT_RECEIVED,
        title=f"{description} received",
        message=f"We recorded ${amount} for your ceremony on {quote.event_date}.",
        event_key=f"ceremony:{ceremony.pk}:payment:{description.lower().replace(' ', '-')}:received:{amount}",
        recipient=quote_customer_user(quote),
        email_address=quote.email,
        action_url=reverse("ceremony_payment", args=[ceremony.public_id]),
        send_email=False,
    )
    notify_staff(
        kind=Notification.Kind.PAYMENT_RECEIVED,
        title=f"{description} received",
        message=f"${amount} was received for {quote.customer_name}'s {quote.event_date} ceremony.",
        event_key=f"ceremony:{ceremony.pk}:payment:{description.lower().replace(' ', '-')}:received:{amount}:staff",
        action_url=reverse("ceremony_detail", args=[ceremony.public_id]),
    )


def notify_assignment(ceremony):
    quote, partner = ceremony.quote, ceremony.assigned_partner
    if not partner:
        return
    create_notification(
        kind=Notification.Kind.PARTNER_ASSIGNED,
        title="New ceremony assignment",
        message=f"You were assigned to {quote.event_date} at {quote.location}.",
        event_key=f"ceremony:{ceremony.pk}:partner:{partner.pk}:assigned",
        recipient=partner.user,
        email_address=partner.email,
        action_url=reverse("partner_dashboard"),
        send_email=False,
    )
    create_notification(
        kind=Notification.Kind.PARTNER_ASSIGNED,
        title="Your Cultural Ambassador is assigned",
        message=f"{partner.name} is assigned to your {quote.event_date} ceremony.",
        event_key=f"ceremony:{ceremony.pk}:partner:{partner.pk}:assigned:customer",
        recipient=quote_customer_user(quote),
        email_address=quote.email,
        action_url=reverse("customer_quote_detail", args=[quote.public_id]) if quote.customer_id else reverse("quote_review", args=[quote.public_id]),
        send_email=False,
    )


def notify_partner_accepted(task):
    ceremony, quote = task.ceremony, task.ceremony.quote
    notify_staff(
        kind=Notification.Kind.PARTNER_ACCEPTED,
        title="Partner accepted assignment",
        message=f"{task.partner.name} accepted {quote.customer_name}'s {quote.event_date} ceremony.",
        event_key=f"task:{task.pk}:accepted:staff",
        action_url=reverse("ceremony_detail", args=[ceremony.public_id]),
    )
    create_notification(
        kind=Notification.Kind.PARTNER_ACCEPTED,
        title="Your ambassador confirmed",
        message=f"{task.partner.name} confirmed your ceremony assignment for {quote.event_date}.",
        event_key=f"task:{task.pk}:accepted:customer",
        recipient=quote_customer_user(quote),
        email_address=quote.email,
        action_url=reverse("customer_quote_detail", args=[quote.public_id]) if quote.customer_id else reverse("quote_review", args=[quote.public_id]),
        send_email=True,
        email_subject="Your Akako House ambassador confirmed",
    )


def notify_quote_expired(quote):
    customer_user = quote_customer_user(quote)
    create_notification(
        kind=Notification.Kind.QUOTE_EXPIRED,
        title="Your quote expired",
        message=f"The quote for your {quote.event_date} ceremony expired without acceptance. Contact us if you would like an updated quote.",
        event_key=f"quote:{quote.pk}:expired",
        recipient=customer_user,
        email_address=quote.email,
        action_url=reverse("quote_review", args=[quote.public_id]),
        send_email=True,
    )
    notify_staff(
        kind=Notification.Kind.QUOTE_EXPIRED,
        title="Quote expired",
        message=f"{quote.customer_name}'s quote for {quote.event_date} expired.",
        event_key=f"quote:{quote.pk}:expired:staff",
        action_url=reverse("manage_quote", args=[quote.public_id]),
    )


def notify_final_payment_reminder(ceremony, hours):
    quote, payment = ceremony.quote, ceremony.final_payment
    if not payment:
        return
    title = f"Final payment required within {hours} hours"
    message = (
        f"Your ${payment.expected_amount} final balance for the {quote.event_date} ceremony "
        f"must be completed before the ceremony can remain ready."
    )
    create_notification(
        kind=Notification.Kind.PAYMENT_DUE,
        title=title,
        message=message,
        event_key=f"ceremony:{ceremony.pk}:final-payment:{hours}h",
        recipient=quote_customer_user(quote),
        email_address=quote.email,
        action_url=reverse("ceremony_payment", args=[ceremony.public_id]),
        send_email=True,
    )
    if hours == 24:
        notify_staff(
            kind=Notification.Kind.PAYMENT_OVERDUE,
            title="Final payment at 24-hour cutoff",
            message=f"{quote.customer_name}'s ${payment.expected_amount} final balance is still unpaid for {quote.event_date}.",
            event_key=f"ceremony:{ceremony.pk}:final-payment:24h:staff",
            action_url=reverse("ceremony_detail", args=[ceremony.public_id]),
        )


def notify_ceremony_reminder_records(ceremony):
    quote = ceremony.quote
    create_notification(
        kind=Notification.Kind.CEREMONY_REMINDER,
        title="Your ceremony is coming up",
        message=f"Your Akako House ceremony is scheduled for {quote.event_date} at {quote.location}.",
        event_key=f"ceremony:{ceremony.pk}:reminder:customer",
        recipient=quote_customer_user(quote),
        email_address=quote.email,
        action_url=reverse("customer_quote_detail", args=[quote.public_id]) if quote.customer_id else reverse("quote_review", args=[quote.public_id]),
        send_email=False,
    )
    if ceremony.assigned_partner:
        partner = ceremony.assigned_partner
        create_notification(
            kind=Notification.Kind.CEREMONY_REMINDER,
            title="Upcoming ceremony assignment",
            message=f"Your assignment is scheduled for {quote.event_date} at {quote.location}.",
            event_key=f"ceremony:{ceremony.pk}:reminder:partner:{partner.pk}",
            recipient=partner.user,
            email_address=partner.email,
            action_url=reverse("partner_dashboard"),
            send_email=False,
        )


def notify_document_expiring(document, days):
    partner = document.partner
    create_notification(
        kind=Notification.Kind.DOCUMENT_EXPIRING,
        title=f"{document.get_document_type_display()} expires in {days} day{'s' if days != 1 else ''}",
        message="Upload a renewed document before expiration to remain eligible for assignments.",
        event_key=f"partner-document:{document.pk}:expiring:{days}d",
        recipient=partner.user,
        email_address=partner.email,
        action_url=reverse("partner_documents"),
        send_email=True,
    )


def notify_document_expired(document):
    partner = document.partner
    create_notification(
        kind=Notification.Kind.DOCUMENT_EXPIRED,
        title="Action required: update an Akako House partner document",
        message="Your profile is temporarily unavailable for new assignments until a current replacement is approved.",
        event_key=f"partner-document:{document.pk}:expired",
        recipient=partner.user,
        email_address=partner.email,
        action_url=reverse("partner_documents"),
        send_email=True,
    )
    notify_staff(
        kind=Notification.Kind.DOCUMENT_EXPIRED,
        title="Partner document expired",
        message=f"{partner.name}'s {document.get_document_type_display().lower()} expired.",
        event_key=f"partner-document:{document.pk}:expired:staff",
        action_url=reverse("manage_partner", args=[partner.pk]),
    )
