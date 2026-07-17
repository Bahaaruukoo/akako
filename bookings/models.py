import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .storage import private_document_storage, public_media_storage


class Partner(models.Model):
    class PartnerType(models.TextChoices):
        RESTAURANT = "restaurant", "Restaurant partner"
        INDIVIDUAL = "individual", "Individual ambassador"

    class ApplicationStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted for review"
        NEEDS_INFO = "needs_info", "More information needed"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Not approved"

    class PayoutMethod(models.TextChoices):
        ACH = "ach", "Bank transfer / ACH"
        CHECK = "check", "Check"
        PAYPAL = "paypal", "PayPal"
        OTHER = "other", "Other"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_profile",
    )

    name = models.CharField(max_length=160)
    partner_type = models.CharField(
        max_length=20,
        choices=PartnerType.choices,
        default=PartnerType.RESTAURANT,
    )
    contact_name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=32)
    service_area = models.CharField(max_length=180, help_text="Cities, ZIPs, or radius served")
    address = models.TextField(blank=True)
    bio = models.TextField(blank=True)
    application_status = models.CharField(
        max_length=20,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.APPROVED,
    )
    payout_method = models.CharField(
        max_length=20,
        choices=PayoutMethod.choices,
        blank=True,
    )
    payout_destination = models.CharField(
        max_length=160,
        blank=True,
        help_text="Payout email or masked account label. Do not enter a full bank/card number.",
    )
    food_permit_verified = models.BooleanField(default=False)
    insurance_verified = models.BooleanField(default=False)
    cultural_training_verified = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PartnerDocument(models.Model):
    class DocumentType(models.TextChoices):
        FOOD_PERMIT = "food_permit", "Food-service permit"
        INSURANCE = "insurance", "Insurance certificate"
        IDENTITY = "identity", "Identity document"
        TRAINING = "training", "Training certificate"
        TAX = "tax", "Tax form"
        OTHER = "other", "Other supporting document"

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=24, choices=DocumentType.choices)
    file = models.FileField(
        upload_to="partner_documents/%Y/%m/",
        storage=private_document_storage,
    )
    review_status = models.CharField(
        max_length=16,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
    )
    review_notes = models.TextField(blank=True)
    expiry_date = models.DateField(
        null=True,
        blank=True,
        help_text="Staff-only validity end date. Leave blank when the document does not expire.",
    )
    expiry_processed_at = models.DateTimeField(null=True, blank=True, editable=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.partner} - {self.get_document_type_display()}"


class ClientOrganization(models.Model):
    name = models.CharField(max_length=160)
    logo = models.FileField(
        upload_to="content/client_logos/%Y/%m/",
        storage=public_media_storage,
    )
    alt_text = models.CharField(max_length=180, blank=True)
    website = models.URLField(blank=True)
    display_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name


class Testimonial(models.Model):
    quote = models.TextField()
    attribution = models.CharField(max_length=160)
    event_context = models.CharField(max_length=160, blank=True)
    rating = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    source = models.CharField(max_length=80, blank=True)
    source_url = models.URLField(blank=True)
    consent_confirmed = models.BooleanField(default=False)
    featured = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "-created_at"]

    def __str__(self):
        return f"{self.attribution}: {self.quote[:50]}"


class EventPhoto(models.Model):
    class Category(models.TextChoices):
        CEREMONY = "ceremony", "Ceremony"
        HOSPITALITY = "hospitality", "Hospitality"
        WEDDING = "wedding", "Wedding"
        CORPORATE = "corporate", "Corporate"
        CULTURAL = "cultural", "Cultural gathering"
        DETAIL = "detail", "Coffee and equipment detail"

    class Placement(models.TextChoices):
        GALLERY = "gallery", "Homepage event gallery"
        ABOUT_ORIGIN = "about_origin", "About page — land of origins"
        ABOUT_CEREMONY = "about_ceremony", "About page — ceremony and hospitality"

    image = models.FileField(
        upload_to="content/event_photos/%Y/%m/",
        storage=public_media_storage,
    )
    alt_text = models.CharField(max_length=220)
    caption = models.CharField(max_length=240, blank=True)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.CEREMONY)
    placement = models.CharField(
        max_length=24,
        choices=Placement.choices,
        default=Placement.GALLERY,
        help_text="Choose where this approved photograph should appear.",
    )
    photographer_credit = models.CharField(max_length=160, blank=True)
    usage_rights_confirmed = models.BooleanField(default=False)
    featured = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "-created_at"]

    def __str__(self):
        return self.caption or self.alt_text


class PartnerGalleryPhoto(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="gallery_photos")
    image = models.FileField(
        upload_to="partner_gallery/%Y/%m/",
        storage=public_media_storage,
    )
    alt_text = models.CharField(max_length=220)
    caption = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    featured = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    review_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="reviewed_partner_gallery_photos",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "-created_at"]

    def __str__(self):
        return f"{self.partner} - {self.caption or self.alt_text}"


class PartnerAvailability(models.Model):
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="availability")
    date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    available = models.BooleanField(default=True)
    notes = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "start_time"]

    def __str__(self):
        state = "Available" if self.available else "Unavailable"
        return f"{self.partner} - {self.date} ({state})"


class CustomerProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="customer_profile",
    )
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    phone = models.CharField(max_length=32)
    marketing_opt_in = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["first_name", "last_name"]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self):
        return self.full_name or self.user.email


class CustomerAddress(models.Model):
    customer = models.ForeignKey(
        CustomerProfile,
        on_delete=models.CASCADE,
        related_name="addresses",
    )
    label = models.CharField(max_length=60, default="Primary address")
    address = models.TextField()
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=60)
    postal_code = models.CharField(max_length=20)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_primary", "label"]

    def __str__(self):
        return f"{self.label}: {self.city}, {self.state}"


class QuoteRequest(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        REVIEWING = "reviewing", "Reviewing"
        WAITLISTED = "waitlisted", "Waiting for partner"
        QUOTED = "quoted", "Sent"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"

    class EventType(models.TextChoices):
        HOME = "home", "Home gathering"
        WEDDING = "wedding", "Wedding"
        GRADUATION = "graduation", "Graduation"
        CORPORATE = "corporate", "Corporate event"
        CULTURAL = "cultural", "Cultural program"
        OTHER = "other", "Other"

    class MilkPreference(models.TextChoices):
        YES = "yes", "Yes, include milk"
        NO = "no", "No milk"
        NON_DAIRY = "non_dairy", "Non-dairy option"
        UNSURE = "unsure", "Not sure yet"

    class SnackStyle(models.TextChoices):
        SHARED = "shared", "Traditional shared plate with utensil"
        INDIVIDUAL = "individual", "Individual portions"
        RECOMMEND = "recommend", "Ambassador recommendation"

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    customer = models.ForeignKey(
        CustomerProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="quote_requests",
    )
    customer_name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    event_date = models.DateField()
    event_time = models.TimeField(null=True, blank=True)
    estimated_duration_minutes = models.PositiveIntegerField(default=120)
    setup_buffer_minutes = models.PositiveIntegerField(default=30)
    cleanup_buffer_minutes = models.PositiveIntegerField(default=30)
    location = models.CharField(max_length=220, help_text="Address, city, or ZIP")
    guest_count = models.PositiveIntegerField()
    indoor = models.BooleanField(default=True)
    milk_preference = models.CharField(
        max_length=20,
        choices=MilkPreference.choices,
        default=MilkPreference.UNSURE,
    )
    snack_style = models.CharField(
        max_length=20,
        choices=SnackStyle.choices,
        default=SnackStyle.RECOMMEND,
    )
    allergies = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    quoted_amount = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    quote_notes = models.TextField(blank=True)
    deposit_amount = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    quote_sent_at = models.DateTimeField(null=True, blank=True)
    quote_expires_at = models.DateTimeField(null=True, blank=True)
    customer_decision_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        customer = self.customer_name or "Quote request"
        return f"{customer} - {self.event_date} ({self.get_status_display()})"

    @property
    def contact_complete(self):
        return bool(self.customer_name and self.email and self.phone)

    @property
    def quote_ready(self):
        return self.quoted_amount is not None and self.contact_complete

    def event_datetime(self):
        value = datetime.combine(self.event_date, self.event_time or time(0, 0))
        return timezone.make_aware(value, timezone.get_current_timezone())

    def occupied_window(self):
        event_start = self.event_datetime()
        return (
            event_start - timedelta(minutes=self.setup_buffer_minutes),
            event_start + timedelta(
                minutes=self.estimated_duration_minutes + self.cleanup_buffer_minutes
            ),
        )

    @property
    def active_capacity_hold(self):
        now = timezone.now()
        return self.capacity_holds.filter(
            status__in=[
                CapacityHold.Status.TEMPORARY,
                CapacityHold.Status.CONFIRMED,
                CapacityHold.Status.CONVERTED,
            ]
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).first()

    @property
    def quote_expired(self):
        return bool(
            self.status == self.Status.QUOTED
            and self.quote_expires_at
            and timezone.now() > self.quote_expires_at
        )

    def mark_quote_sent(self):
        self.status = self.Status.QUOTED
        self.quote_sent_at = timezone.now()
        self.save(update_fields=["status", "quote_sent_at", "updated_at"])

    def accept_quote(self):
        self.status = self.Status.ACCEPTED
        self.customer_decision_at = timezone.now()
        self.save(update_fields=["status", "customer_decision_at", "updated_at"])
        ceremony, created = Ceremony.objects.get_or_create(quote=self)
        if created:
            ceremony.initialize_payments()
        return ceremony

    def decline_quote(self):
        self.status = self.Status.DECLINED
        self.customer_decision_at = timezone.now()
        self.save(update_fields=["status", "customer_decision_at", "updated_at"])


class AvailabilityOffer(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting response"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    quote = models.ForeignKey(QuoteRequest, on_delete=models.CASCADE, related_name="availability_offers")
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="availability_offers")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateTimeField()
    response_notes = models.TextField(blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_availability_offers",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.partner} - {self.quote.event_date} ({self.get_status_display()})"


class CapacityHold(models.Model):
    class Status(models.TextChoices):
        TEMPORARY = "temporary", "Temporary hold"
        CONFIRMED = "confirmed", "Customer confirmed"
        CONVERTED = "converted", "Converted to assignment"
        RELEASED = "released", "Released"
        EXPIRED = "expired", "Expired"

    quote = models.ForeignKey(QuoteRequest, on_delete=models.CASCADE, related_name="capacity_holds")
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="capacity_holds")
    offer = models.OneToOneField(
        AvailabilityOffer,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="capacity_hold",
    )
    event_start = models.DateTimeField()
    event_end = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.TEMPORARY)
    expires_at = models.DateTimeField(null=True, blank=True)
    release_reason = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["event_start"]

    def __str__(self):
        return f"{self.partner} hold for {self.quote.event_date} ({self.get_status_display()})"


class Ceremony(models.Model):
    class Status(models.TextChoices):
        AWAITING_DEPOSIT = "awaiting_deposit", "Awaiting deposit"
        AWAITING_PARTNER = "awaiting_partner", "Awaiting partner"
        ASSIGNED = "assigned", "Assigned"
        READY = "ready", "Ready"
        AT_RISK = "at_risk", "Payment overdue"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        NO_SHOW = "no_show", "No-show"

    class OutcomeReason(models.TextChoices):
        CUSTOMER_CANCELLED = "customer_cancelled", "Customer cancelled"
        BUNAGO_CANCELLED = "bunago_cancelled", "Akako House cancelled"
        PARTNER_UNAVAILABLE = "partner_unavailable", "Partner unavailable"
        CUSTOMER_NO_SHOW = "customer_no_show", "Customer no-show"
        PARTNER_NO_SHOW = "partner_no_show", "Partner no-show"
        VENUE_ACCESS = "venue_access", "Venue or access problem"
        WEATHER_EMERGENCY = "weather_emergency", "Weather or emergency"
        DEPOSIT_NOT_PAID = "deposit_not_paid", "Deposit not paid"
        FINAL_PAYMENT_NOT_PAID = "final_payment_not_paid", "Final payment not paid"
        OTHER = "other", "Other"

    class CoverageStatus(models.TextChoices):
        UNRESERVED = "unreserved", "No partner reserved"
        HELD = "held", "Partner held"
        CONFIRMED = "confirmed", "Partner confirmed"
        UNCOVERED = "uncovered", "Partner coverage needed"

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    quote = models.OneToOneField(
        QuoteRequest,
        on_delete=models.PROTECT,
        related_name="ceremony",
    )
    assigned_partner = models.ForeignKey(
        Partner,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ceremonies",
    )
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.AWAITING_DEPOSIT,
    )
    coverage_status = models.CharField(
        max_length=16,
        choices=CoverageStatus.choices,
        default=CoverageStatus.UNRESERVED,
    )
    final_payment_due_at = models.DateTimeField(null=True, blank=True)
    outcome_reason = models.CharField(
        max_length=32,
        choices=OutcomeReason.choices,
        blank=True,
    )
    outcome_notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["quote__event_date", "quote__event_time"]

    def __str__(self):
        return f"Ceremony for {self.quote.customer_name or self.quote.email} on {self.quote.event_date}"

    @property
    def terminal(self):
        return self.status in {
            self.Status.COMPLETED,
            self.Status.FAILED,
            self.Status.CANCELLED,
            self.Status.NO_SHOW,
        }

    @property
    def deposit_payment(self):
        return self.payments.filter(payment_type=Payment.PaymentType.DEPOSIT).first()

    @property
    def final_payment(self):
        return self.payments.filter(payment_type=Payment.PaymentType.FINAL).first()

    def event_datetime(self):
        event_time = self.quote.event_time or time(0, 0)
        value = datetime.combine(self.quote.event_date, event_time)
        return timezone.make_aware(value, timezone.get_current_timezone())

    def initialize_payments(self):
        total = self.quote.quoted_amount or Decimal("0.00")
        deposit = self.quote.deposit_amount or Decimal("0.00")
        deposit_due = timezone.now() + timedelta(
            hours=getattr(settings, "DEPOSIT_DUE_HOURS", 48)
        )
        self.final_payment_due_at = self.event_datetime() - timedelta(
            hours=getattr(settings, "FINAL_PAYMENT_DUE_HOURS", 24)
        )
        self.status = (
            self.Status.AWAITING_DEPOSIT if deposit > 0 else self.Status.AWAITING_PARTNER
        )
        self.save(update_fields=["status", "final_payment_due_at", "updated_at"])

        Payment.objects.get_or_create(
            ceremony=self,
            payment_type=Payment.PaymentType.DEPOSIT,
            defaults={
                "expected_amount": deposit,
                "due_at": deposit_due,
                "status": Payment.Status.PENDING if deposit > 0 else Payment.Status.WAIVED,
            },
        )
        final_amount = max(total - deposit, Decimal("0.00"))
        Payment.objects.get_or_create(
            ceremony=self,
            payment_type=Payment.PaymentType.FINAL,
            defaults={
                "expected_amount": final_amount,
                "due_at": self.final_payment_due_at,
                "status": Payment.Status.PENDING if final_amount > 0 else Payment.Status.WAIVED,
            },
        )
        StatusHistory.objects.get_or_create(
            ceremony=self,
            from_status="",
            to_status=self.status,
            defaults={"note": "Ceremony created from accepted quote."},
        )

    def transition_to(self, new_status, *, changed_by=None, note="", outcome_reason=""):
        if self.terminal:
            raise ValueError("Terminal ceremony records are frozen.")
        previous = self.status
        self.status = new_status
        if outcome_reason:
            self.outcome_reason = outcome_reason
        if new_status == self.Status.COMPLETED:
            self.completed_at = timezone.now()
        if new_status in {self.Status.CANCELLED, self.Status.FAILED, self.Status.NO_SHOW}:
            self.cancelled_at = timezone.now()
        self.save()
        StatusHistory.objects.create(
            ceremony=self,
            from_status=previous,
            to_status=new_status,
            note=note,
            changed_by=changed_by,
        )

    def refresh_deadlines(self, *, changed_by=None):
        if self.terminal:
            return
        now = timezone.now()
        deposit = self.deposit_payment
        final = self.final_payment

        if (
            self.status == self.Status.AWAITING_DEPOSIT
            and deposit
            and deposit.status == Payment.Status.PENDING
            and deposit.due_at
            and deposit.due_at < now
        ):
            deposit.status = Payment.Status.OVERDUE
            deposit.save(update_fields=["status", "updated_at"])
            self.transition_to(
                self.Status.CANCELLED,
                changed_by=changed_by,
                note="Deposit deadline passed without payment.",
                outcome_reason=self.OutcomeReason.DEPOSIT_NOT_PAID,
            )
            return

        if (
            final
            and final.status == Payment.Status.PENDING
            and final.due_at
            and final.due_at < now
        ):
            final.status = Payment.Status.OVERDUE
            final.save(update_fields=["status", "updated_at"])
            if self.status != self.Status.AT_RISK:
                self.transition_to(
                    self.Status.AT_RISK,
                    changed_by=changed_by,
                    note="Final payment was not received 24 hours before the ceremony.",
                    outcome_reason=self.OutcomeReason.FINAL_PAYMENT_NOT_PAID,
                )


class Payment(models.Model):
    class PaymentType(models.TextChoices):
        DEPOSIT = "deposit", "Initial deposit"
        FINAL = "final", "Final payment"
        REFUND = "refund", "Refund"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"
        FORFEITED = "forfeited", "Forfeited"
        WAIVED = "waived", "Waived"

    ceremony = models.ForeignKey(Ceremony, on_delete=models.PROTECT, related_name="payments")
    payment_type = models.CharField(max_length=16, choices=PaymentType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    expected_amount = models.DecimalField(max_digits=8, decimal_places=2)
    received_amount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    due_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    overdue_notified_at = models.DateTimeField(null=True, blank=True)
    provider_reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["ceremony", "payment_type"],
                name="unique_payment_type_per_ceremony",
            )
        ]

    def __str__(self):
        return f"{self.get_payment_type_display()} - {self.get_status_display()}"


class PaymentCheckout(models.Model):
    class Choice(models.TextChoices):
        DEPOSIT = "deposit", "Deposit only"
        FINAL = "final", "Final balance"
        FULL = "full", "Pay in full"

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        COMPLETED = "completed", "Completed"
        EXPIRED = "expired", "Expired"
        FAILED = "failed", "Failed"

    ceremony = models.ForeignKey(
        Ceremony,
        on_delete=models.PROTECT,
        related_name="checkouts",
    )
    provider = models.CharField(max_length=32, default="stripe")
    provider_session_id = models.CharField(max_length=160, unique=True)
    provider_payment_reference = models.CharField(max_length=160, blank=True)
    payment_choice = models.CharField(max_length=16, choices=Choice.choices)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    currency = models.CharField(max_length=3, default="usd")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CREATED)
    checkout_url = models.URLField(max_length=500)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_payment_choice_display()} - {self.get_status_display()}"


class CustomerCancellationRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending staff review"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined"
        WITHDRAWN = "withdrawn", "Withdrawn"

    ceremony = models.ForeignKey(
        Ceremony,
        on_delete=models.PROTECT,
        related_name="customer_cancellation_requests",
    )
    customer = models.ForeignKey(
        CustomerProfile,
        on_delete=models.PROTECT,
        related_name="cancellation_requests",
    )
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    staff_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_customer_cancellations",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Cancellation request for {self.ceremony} ({self.get_status_display()})"


class CustomerReview(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    ceremony = models.OneToOneField(
        Ceremony, on_delete=models.PROTECT, related_name="customer_review"
    )
    customer = models.ForeignKey(
        CustomerProfile, on_delete=models.PROTECT, related_name="reviews"
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    title = models.CharField(max_length=140, blank=True)
    review = models.TextField()
    permission_to_publish = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    featured = models.BooleanField(default=False)
    staff_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="reviewed_customer_reviews",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.customer} — {self.rating}/5"


class ShopInterest(models.Model):
    class PurchaseFrequency(models.TextChoices):
        WEEKLY = "weekly", "Every week"
        BIWEEKLY = "biweekly", "Every two weeks"
        MONTHLY = "monthly", "About once a month"
        OCCASIONALLY = "occasionally", "Occasionally"
        EXPLORING = "exploring", "I am just exploring"

    email = models.EmailField(unique=True)
    postal_code = models.CharField(max_length=12, blank=True)
    purchase_frequency = models.CharField(
        max_length=20,
        choices=PurchaseFrequency.choices,
        blank=True,
    )
    marketing_consent = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.email


class PolicyDocument(models.Model):
    class PolicyType(models.TextChoices):
        PRIVACY = "privacy", "Privacy notice"
        SERVICE = "service", "Service terms"
        PAYMENT = "payment", "Payment and deposit policy"
        CANCELLATION = "cancellation", "Cancellation, rescheduling, and refund policy"

    policy_type = models.CharField(max_length=24, choices=PolicyType.choices)
    title = models.CharField(max_length=160)
    version = models.CharField(max_length=32)
    content = models.TextField()
    effective_date = models.DateField(default=timezone.localdate)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["policy_type", "-effective_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["policy_type", "version"], name="unique_policy_type_version"
            )
        ]

    def __str__(self):
        return f"{self.title} ({self.version})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:
            type(self).objects.filter(policy_type=self.policy_type, is_active=True).exclude(
                pk=self.pk
            ).update(is_active=False)


class PolicyAcceptance(models.Model):
    quote = models.ForeignKey(
        QuoteRequest, on_delete=models.PROTECT, related_name="policy_acceptances"
    )
    policy = models.ForeignKey(
        PolicyDocument, on_delete=models.PROTECT, related_name="acceptances"
    )
    policy_title = models.CharField(max_length=160)
    policy_version = models.CharField(max_length=32)
    policy_content = models.TextField()
    accepted_name = models.CharField(max_length=120)
    accepted_email = models.EmailField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["accepted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["quote", "policy"], name="unique_quote_policy_acceptance"
            )
        ]

    def __str__(self):
        return f"{self.accepted_email} accepted {self.policy_title} {self.policy_version}"


class Notification(models.Model):
    class Kind(models.TextChoices):
        QUOTE_NEW = "quote_new", "New quote request"
        QUOTE_ACCEPTED = "quote_accepted", "Quote accepted"
        QUOTE_DECLINED = "quote_declined", "Quote declined"
        QUOTE_EXPIRED = "quote_expired", "Quote expired"
        PAYMENT_RECEIVED = "payment_received", "Payment received"
        PAYMENT_DUE = "payment_due", "Payment approaching"
        PAYMENT_OVERDUE = "payment_overdue", "Payment overdue"
        PARTNER_ASSIGNED = "partner_assigned", "Partner assignment"
        PARTNER_ACCEPTED = "partner_accepted", "Partner acceptance"
        CAPACITY_HOLD_CREATED = "capacity_hold_created", "Temporary reservation created"
        CAPACITY_HOLD_RELEASED = "capacity_hold_released", "Temporary reservation released"
        CEREMONY_REMINDER = "ceremony_reminder", "Ceremony reminder"
        CEREMONY_COMPLETED = "ceremony_completed", "Ceremony completed"
        DOCUMENT_EXPIRING = "document_expiring", "Document expiring"
        DOCUMENT_EXPIRED = "document_expired", "Document expired"
        GALLERY_UPLOADED = "gallery_uploaded", "Gallery upload"
        REVIEW_SUBMITTED = "review_submitted", "Review submitted"

    class EmailStatus(models.TextChoices):
        SKIPPED = "skipped", "Not requested"
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    recipient_email = models.EmailField(blank=True)
    kind = models.CharField(max_length=32, choices=Kind.choices)
    title = models.CharField(max_length=180)
    message = models.TextField()
    action_url = models.CharField(max_length=300, blank=True)
    event_key = models.CharField(max_length=255, unique=True)
    email_status = models.CharField(
        max_length=16,
        choices=EmailStatus.choices,
        default=EmailStatus.SKIPPED,
    )
    email_sent_at = models.DateTimeField(null=True, blank=True)
    email_error = models.CharField(max_length=500, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "read_at", "-created_at"]),
            models.Index(fields=["kind", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.title}"


class PartnerTask(models.Model):
    class Status(models.TextChoices):
        ASSIGNED = "assigned", "Assigned"
        ACCEPTED = "accepted", "Accepted"
        IN_PROGRESS = "in_progress", "In progress"
        DELIVERED = "delivered", "Marked delivered"
        ISSUE = "issue", "Issue reported"
        CANCELLED = "cancelled", "Cancelled"

    ceremony = models.OneToOneField(Ceremony, on_delete=models.PROTECT, related_name="partner_task")
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="tasks")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ASSIGNED)
    partner_notes = models.TextField(blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ceremony__quote__event_date"]

    def __str__(self):
        return f"{self.partner} - {self.ceremony.quote.event_date}"


class PartnerPayout(models.Model):
    class Status(models.TextChoices):
        NOT_READY = "not_ready", "Not ready"
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved"
        PROCESSING = "processing", "Processing"
        PAID = "paid", "Paid"
        HELD = "held", "On hold"
        CANCELLED = "cancelled", "Cancelled"

    task = models.OneToOneField(PartnerTask, on_delete=models.PROTECT, related_name="payout")
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="payouts")
    amount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_READY)
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.partner} - ${self.amount} ({self.get_status_display()})"


class StatusHistory(models.Model):
    ceremony = models.ForeignKey(Ceremony, on_delete=models.PROTECT, related_name="history")
    from_status = models.CharField(max_length=24, choices=Ceremony.Status.choices, blank=True)
    to_status = models.CharField(max_length=24, choices=Ceremony.Status.choices)
    note = models.TextField(blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "status histories"

    def __str__(self):
        return f"{self.from_status or 'Created'} → {self.to_status}"
