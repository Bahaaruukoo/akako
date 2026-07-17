from django.conf import settings
from django.db.models import Q

from .models import (
    AvailabilityOffer,
    CustomerReview,
    Notification,
    PartnerDocument,
    PartnerGalleryPhoto,
    QuoteRequest,
)


def notification_alerts(request):
    if not request.user.is_authenticated:
        return {}
    return {
        "unread_notification_count": Notification.objects.filter(
            recipient=request.user,
            read_at__isnull=True,
        ).count()
    }


def staff_partner_alerts(request):
    user = request.user
    if not user.is_authenticated or not user.is_staff:
        return {}
    alerts = {}
    if user.has_perm("bookings.view_partner"):
        alerts["pending_partner_document_count"] = PartnerDocument.objects.filter(
            review_status=PartnerDocument.ReviewStatus.PENDING
        ).count()
        alerts["pending_content_count"] = (
            PartnerGalleryPhoto.objects.filter(status=PartnerGalleryPhoto.Status.PENDING).count()
            + CustomerReview.objects.filter(status=CustomerReview.Status.PENDING).count()
        )
    if user.has_perm("bookings.view_quoterequest"):
        actionable_quotes = QuoteRequest.objects.exclude(
            customer_name=""
        ).exclude(email="").exclude(phone="").filter(
            Q(status=QuoteRequest.Status.NEW)
            | Q(
                status__in=[QuoteRequest.Status.REVIEWING, QuoteRequest.Status.WAITLISTED],
                availability_offers__status__in=[
                    AvailabilityOffer.Status.ACCEPTED,
                    AvailabilityOffer.Status.DECLINED,
                ],
            )
        ).distinct()
        alerts["quote_attention_count"] = actionable_quotes.count()
    return alerts


def site_contact_details(request):
    return {
        "support_phone_display": settings.SUPPORT_PHONE_DISPLAY,
        "support_phone_tel": settings.SUPPORT_PHONE_TEL,
        "support_email": settings.SUPPORT_EMAIL,
        "support_hours": settings.SUPPORT_HOURS,
        "support_urgent_message": settings.SUPPORT_URGENT_MESSAGE,
        "service_area_display": settings.SERVICE_AREA_DISPLAY,
        "site_url": settings.PUBLIC_BASE_URL.rstrip("/"),
        "analytics_id": settings.GOOGLE_ANALYTICS_ID,
    }
