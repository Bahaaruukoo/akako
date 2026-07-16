from django.contrib import admin
from django.contrib import messages
from django.urls import reverse
from django.utils.html import format_html
from django.utils import timezone

from .models import (
    AvailabilityOffer,
    CapacityHold,
    Ceremony,
    ClientOrganization,
    CustomerAddress,
    CustomerCancellationRequest,
    CustomerProfile,
    CustomerReview,
    EventPhoto,
    Notification,
    Partner,
    PartnerAvailability,
    PartnerDocument,
    PartnerGalleryPhoto,
    PartnerPayout,
    PartnerTask,
    Payment,
    PaymentCheckout,
    QuoteRequest,
    ShopInterest,
    StatusHistory,
    Testimonial,
)
from .services import build_absolute_quote_url, send_quote_email

admin.site.register(PartnerAvailability)
admin.site.register(AvailabilityOffer)
admin.site.register(CapacityHold)
admin.site.register(ClientOrganization)
admin.site.register(Testimonial)
admin.site.register(EventPhoto)
admin.site.register(PartnerGalleryPhoto)
admin.site.register(CustomerReview)


@admin.register(ShopInterest)
class ShopInterestAdmin(admin.ModelAdmin):
    list_display = ("email", "postal_code", "purchase_frequency", "active", "created_at")
    list_filter = ("purchase_frequency", "active", "created_at")
    search_fields = ("email", "postal_code")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "recipient", "recipient_email", "email_status", "read_at", "created_at")
    list_filter = ("kind", "email_status", "read_at", "created_at")
    search_fields = ("title", "message", "recipient_email", "event_key")
    readonly_fields = ("event_key", "email_sent_at", "email_error", "created_at")


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "partner_type",
        "application_status",
        "service_area",
        "food_permit_verified",
        "insurance_verified",
        "cultural_training_verified",
        "active",
    )
    list_filter = (
        "partner_type",
        "application_status",
        "active",
        "food_permit_verified",
        "insurance_verified",
        "cultural_training_verified",
    )
    search_fields = ("name", "contact_name", "email", "phone", "service_area")


@admin.register(PartnerDocument)
class PartnerDocumentAdmin(admin.ModelAdmin):
    list_display = ("partner", "document_type", "review_status", "expiry_date", "uploaded_at")
    list_filter = ("document_type", "review_status")
    search_fields = ("partner__name", "partner__email", "review_notes")


@admin.register(PartnerTask)
class PartnerTaskAdmin(admin.ModelAdmin):
    list_display = ("partner", "ceremony", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("partner__name", "ceremony__quote__customer_name")


@admin.register(PartnerPayout)
class PartnerPayoutAdmin(admin.ModelAdmin):
    list_display = ("partner", "amount", "status", "paid_at", "reference")
    list_filter = ("status",)
    search_fields = ("partner__name", "reference")


class CustomerAddressInline(admin.TabularInline):
    model = CustomerAddress
    extra = 0


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ("full_name", "user", "phone", "created_at")
    search_fields = ("first_name", "last_name", "user__email", "phone")
    inlines = (CustomerAddressInline,)


@admin.register(CustomerCancellationRequest)
class CustomerCancellationRequestAdmin(admin.ModelAdmin):
    list_display = ("customer", "ceremony", "status", "created_at", "reviewed_at")
    list_filter = ("status",)
    search_fields = ("customer__first_name", "customer__last_name", "ceremony__quote__customer_name")


@admin.register(QuoteRequest)
class QuoteRequestAdmin(admin.ModelAdmin):
    list_display = (
        "customer_name",
        "event_date",
        "event_time",
        "location",
        "guest_count",
        "status",
        "quoted_amount",
        "deposit_amount",
        "quote_sent_at",
        "quote_review_link",
        "created_at",
    )
    list_filter = ("status", "event_type", "milk_preference", "snack_style", "indoor")
    search_fields = ("customer_name", "email", "phone", "location")
    readonly_fields = (
        "public_id",
        "quote_review_link",
        "quote_sent_at",
        "customer_decision_at",
        "created_at",
        "updated_at",
    )
    actions = ("send_quote_to_customer",)

    def has_change_permission(self, request, obj=None):
        if obj and obj.status == QuoteRequest.Status.ACCEPTED:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == QuoteRequest.Status.ACCEPTED:
            return False
        return super().has_delete_permission(request, obj)

    fieldsets = (
        ("Customer", {"fields": ("public_id", "quote_review_link", "customer_name", "email", "phone")}),
        (
            "Event",
            {
                "fields": (
                    "event_type",
                    "event_date",
                    "event_time",
                    "estimated_duration_minutes",
                    "setup_buffer_minutes",
                    "cleanup_buffer_minutes",
                    "location",
                    "guest_count",
                    "indoor",
                    "milk_preference",
                    "snack_style",
                    "allergies",
                    "notes",
                )
            },
        ),
        (
            "Quote",
            {
                "fields": (
                    "status",
                    "quoted_amount",
                    "deposit_amount",
                    "quote_notes",
                    "quote_expires_at",
                    "quote_sent_at",
                    "customer_decision_at",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.action(description="Send quote to selected customers")
    def send_quote_to_customer(self, request, queryset):
        sent = 0
        skipped = 0
        for quote_request in queryset:
            if quote_request.status in {
                QuoteRequest.Status.ACCEPTED,
                QuoteRequest.Status.DECLINED,
                QuoteRequest.Status.EXPIRED,
            } or not quote_request.quote_ready:
                skipped += 1
                continue
            if not quote_request.event_time or not quote_request.active_capacity_hold:
                skipped += 1
                continue
            if not quote_request.quote_expires_at:
                quote_request.quote_expires_at = timezone.now() + timezone.timedelta(days=7)
                quote_request.save(update_fields=["quote_expires_at", "updated_at"])
            quote_url = build_absolute_quote_url(request, quote_request)
            send_quote_email(quote_request, quote_url)
            quote_request.mark_quote_sent()
            sent += 1

        if sent:
            self.message_user(request, f"Sent {sent} quote email(s).", messages.SUCCESS)
        if skipped:
            self.message_user(
                request,
                f"Skipped {skipped} request(s). Complete the quote, event time, and accepted partner hold first.",
                messages.WARNING,
            )

    @admin.display(description="Quote response")
    def quote_review_link(self, obj):
        if not obj.pk:
            return "Save request first"
        url = reverse("quote_review", kwargs={"public_id": obj.public_id})
        return format_html('<a href="{}" target="_blank">View Quote</a>', url)


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = ("payment_type", "expected_amount", "created_at", "updated_at")


class StatusHistoryInline(admin.TabularInline):
    model = StatusHistory
    extra = 0
    can_delete = False
    readonly_fields = ("from_status", "to_status", "note", "changed_by", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Ceremony)
class CeremonyAdmin(admin.ModelAdmin):
    list_display = (
        "quote",
        "status",
        "assigned_partner",
        "final_payment_due_at",
        "completed_at",
    )
    list_filter = ("status", "outcome_reason")
    search_fields = ("quote__customer_name", "quote__email", "quote__location")
    autocomplete_fields = ("assigned_partner",)
    readonly_fields = ("public_id", "created_at", "updated_at")
    inlines = (PaymentInline, StatusHistoryInline)

    def has_change_permission(self, request, obj=None):
        if obj and obj.terminal:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "ceremony",
        "payment_type",
        "status",
        "expected_amount",
        "received_amount",
        "due_at",
        "paid_at",
    )
    list_filter = ("payment_type", "status")
    search_fields = ("ceremony__quote__customer_name", "provider_reference")

    def has_change_permission(self, request, obj=None):
        if obj and obj.ceremony.status == Ceremony.Status.COMPLETED:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentCheckout)
class PaymentCheckoutAdmin(admin.ModelAdmin):
    list_display = (
        "ceremony",
        "payment_choice",
        "amount",
        "currency",
        "status",
        "provider_session_id",
        "created_at",
        "completed_at",
    )
    list_filter = ("provider", "payment_choice", "status")
    search_fields = (
        "ceremony__quote__customer_name",
        "provider_session_id",
        "provider_payment_reference",
    )
    readonly_fields = (
        "ceremony",
        "provider",
        "provider_session_id",
        "provider_payment_reference",
        "payment_choice",
        "amount",
        "currency",
        "status",
        "checkout_url",
        "created_at",
        "updated_at",
        "completed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StatusHistory)
class StatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("ceremony", "from_status", "to_status", "changed_by", "created_at")
    list_filter = ("to_status",)
    readonly_fields = ("ceremony", "from_status", "to_status", "note", "changed_by", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
