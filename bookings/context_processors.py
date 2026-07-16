from django.conf import settings

from .models import CustomerReview, Notification, PartnerDocument, PartnerGalleryPhoto


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
    if not user.has_perm("bookings.view_partner"):
        return {}
    pending_partner_documents = PartnerDocument.objects.filter(
        review_status=PartnerDocument.ReviewStatus.PENDING
    ).count()
    return {
        "pending_partner_document_count": pending_partner_documents,
        "pending_content_count": (
            PartnerGalleryPhoto.objects.filter(status=PartnerGalleryPhoto.Status.PENDING).count()
            + CustomerReview.objects.filter(status=CustomerReview.Status.PENDING).count()
        ),
    }


def site_contact_details(request):
    return {
        "support_phone_display": settings.SUPPORT_PHONE_DISPLAY,
        "support_phone_tel": settings.SUPPORT_PHONE_TEL,
        "support_email": settings.SUPPORT_EMAIL,
        "support_hours": settings.SUPPORT_HOURS,
        "support_urgent_message": settings.SUPPORT_URGENT_MESSAGE,
    }
