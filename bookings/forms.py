from django import forms
from allauth.account.forms import SignupForm
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from decimal import Decimal

from .models import (
    ClientOrganization,
    Ceremony,
    CustomerAddress,
    CustomerCancellationRequest,
    CustomerProfile,
    CustomerReview,
    EventPhoto,
    Partner,
    PartnerAvailability,
    PartnerDocument,
    PartnerGalleryPhoto,
    PartnerPayout,
    PartnerTask,
    Payment,
    QuoteRequest,
    ShopInterest,
    Testimonial,
)


ALLOWED_PARTNER_DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_PARTNER_DOCUMENT_SIZE = 10 * 1024 * 1024
ALLOWED_CONTENT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_CONTENT_IMAGE_SIZE = 8 * 1024 * 1024


class AccountIdentityForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ["first_name", "last_name"]


def validate_partner_document(upload):
    from pathlib import Path

    extension = Path(upload.name).suffix.lower()
    if extension not in ALLOWED_PARTNER_DOCUMENT_EXTENSIONS:
        raise ValidationError("Upload a PDF, JPG, or PNG file.")
    if upload.size > MAX_PARTNER_DOCUMENT_SIZE:
        raise ValidationError("Each document must be 10 MB or smaller.")


def validate_content_image(upload):
    from pathlib import Path

    extension = Path(upload.name).suffix.lower()
    if extension not in ALLOWED_CONTENT_IMAGE_EXTENSIONS:
        raise ValidationError("Upload a JPG, PNG, or WebP image.")
    if upload.size > MAX_CONTENT_IMAGE_SIZE:
        raise ValidationError("Images must be 8 MB or smaller.")
    content_type = getattr(upload, "content_type", "")
    if content_type and not content_type.startswith("image/"):
        raise ValidationError("The uploaded file must be an image.")


class ClientOrganizationForm(forms.ModelForm):
    class Meta:
        model = ClientOrganization
        fields = ["name", "logo", "alt_text", "website", "display_order", "active"]

    def clean_logo(self):
        upload = self.cleaned_data["logo"]
        validate_content_image(upload)
        return upload


class TestimonialForm(forms.ModelForm):
    class Meta:
        model = Testimonial
        fields = ["quote", "attribution", "event_context", "rating", "source", "source_url", "consent_confirmed", "featured", "active", "display_order"]
        widgets = {"quote": forms.Textarea(attrs={"rows": 4})}

    def clean_consent_confirmed(self):
        if not self.cleaned_data.get("consent_confirmed"):
            raise ValidationError("Confirm permission before publishing this testimonial.")
        return True


class EventPhotoForm(forms.ModelForm):
    class Meta:
        model = EventPhoto
        fields = ["image", "alt_text", "caption", "category", "placement", "photographer_credit", "usage_rights_confirmed", "featured", "active", "display_order"]

    def clean_image(self):
        upload = self.cleaned_data["image"]
        validate_content_image(upload)
        return upload

    def clean_usage_rights_confirmed(self):
        if not self.cleaned_data.get("usage_rights_confirmed"):
            raise ValidationError("Confirm image usage rights before publishing.")
        return True


class PartnerGalleryPhotoForm(forms.ModelForm):
    class Meta:
        model = PartnerGalleryPhoto
        fields = ["image", "alt_text", "caption"]

    def clean_image(self):
        upload = self.cleaned_data["image"]
        validate_content_image(upload)
        return upload


class CustomerReviewForm(forms.ModelForm):
    rating = forms.TypedChoiceField(
        choices=[(5, "5 — Excellent"), (4, "4 — Very good"), (3, "3 — Good"), (2, "2 — Fair"), (1, "1 — Poor")],
        coerce=int,
    )

    class Meta:
        model = CustomerReview
        fields = ["rating", "title", "review", "permission_to_publish"]
        widgets = {"review": forms.Textarea(attrs={"rows": 4})}

    def clean_permission_to_publish(self):
        if not self.cleaned_data.get("permission_to_publish"):
            raise ValidationError("Permission is required before a review can be considered for publication.")
        return True


class ShopInterestForm(forms.Form):
    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"placeholder": "you@example.com", "autocomplete": "email"}),
    )
    postal_code = forms.CharField(
        label="ZIP code (optional)",
        required=False,
        max_length=12,
        widget=forms.TextInput(attrs={"placeholder": "ZIP code", "autocomplete": "postal-code", "inputmode": "numeric"}),
    )
    purchase_frequency = forms.ChoiceField(
        label="How often do you buy coffee? (optional)",
        required=False,
        choices=[("", "Choose an answer")] + list(ShopInterest.PurchaseFrequency.choices),
    )
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="")

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_postal_code(self):
        postal_code = self.cleaned_data.get("postal_code", "").strip()
        compact = postal_code.replace("-", "")
        if postal_code and (not compact.isdigit() or len(compact) not in {5, 9}):
            raise ValidationError("Enter a 5-digit ZIP code or ZIP+4.")
        return postal_code

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise ValidationError("Unable to process this signup.")
        return ""


class QuoteLeadForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        placeholders = {
            "event_date": "Select date",
            "location": "Enter city or ZIP code",
            "guest_count": "Select guest count",
        }
        for name, placeholder in placeholders.items():
            self.fields[name].widget.attrs["placeholder"] = placeholder

    class Meta:
        model = QuoteRequest
        fields = [
            "event_type",
            "event_date",
            "location",
            "guest_count",
        ]
        widgets = {
            "event_date": forms.DateInput(attrs={"type": "date"}),
        }


class QuoteContactForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        placeholders = {
            "customer_name": "Your full name",
            "email": "you@example.com",
            "phone": "Phone number",
            "event_time": "Select time",
            "allergies": "Any allergies or dietary notes?",
            "notes": "Tell us anything helpful about the event.",
        }
        for name, placeholder in placeholders.items():
            self.fields[name].widget.attrs["placeholder"] = placeholder

    class Meta:
        model = QuoteRequest
        fields = [
            "customer_name",
            "email",
            "phone",
            "event_time",
            "indoor",
            "allergies",
            "notes",
        ]
        widgets = {
            "event_time": forms.TimeInput(attrs={"type": "time"}),
            "allergies": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }


QuoteRequestForm = QuoteLeadForm


class QuoteManagementForm(forms.ModelForm):
    """Staff-facing form for preparing and storing a customer quote."""

    def __init__(self, *args, **kwargs):
        instance = kwargs.get("instance")
        if args and instance and instance.pk:
            data = args[0].copy()
            for field_name in [
                "event_time",
                "estimated_duration_minutes",
                "setup_buffer_minutes",
                "cleanup_buffer_minutes",
            ]:
                if field_name not in data:
                    value = getattr(instance, field_name)
                    data[field_name] = value.strftime("%H:%M") if field_name == "event_time" and value else value
            args = (data, *args[1:])
        super().__init__(*args, **kwargs)
        if not self.is_bound and self.instance.pk and not self.instance.quote_notes:
            self.initial["quote_notes"] = self._default_customer_note()

    def _default_customer_note(self):
        quote_request = self.instance
        event_date = quote_request.event_date.strftime("%B %d, %Y").replace(" 0", " ")
        return (
            f"Thank you for considering Akako House for your "
            f"{quote_request.get_event_type_display().lower()} on {event_date} "
            f"in {quote_request.location}. This custom quote is prepared for "
            f"{quote_request.guest_count} guests and includes a complete ceremony "
            "setup, freshly prepared traditional coffee service, and service by a "
            "trained Cultural Ambassador from our trusted local partner network.\n\n"
            f"We have noted your milk preference ({quote_request.get_milk_preference_display()}) "
            f"and snack service preference ({quote_request.get_snack_style_display()}). "
            "We will coordinate the final ceremony details with you before the event.\n\n"
            "Please review and accept this quote before its expiration date to reserve "
            "your requested date. Once accepted, we will follow up with deposit and "
            "scheduling details.\n\n"
            "We look forward to bringing a warm, authentic Ethiopian coffee ceremony "
            "to your gathering."
        )

    quote_expires_at = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
    )

    class Meta:
        model = QuoteRequest
        fields = [
            "event_time",
            "quoted_amount",
            "deposit_amount",
            "estimated_duration_minutes",
            "setup_buffer_minutes",
            "cleanup_buffer_minutes",
            "quote_notes",
            "quote_expires_at",
        ]
        widgets = {
            "event_time": forms.TimeInput(attrs={"type": "time"}),
            "quoted_amount": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "deposit_amount": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "estimated_duration_minutes": forms.NumberInput(attrs={"min": "30", "step": "15"}),
            "setup_buffer_minutes": forms.NumberInput(attrs={"min": "0", "step": "15"}),
            "cleanup_buffer_minutes": forms.NumberInput(attrs={"min": "0", "step": "15"}),
            "quote_notes": forms.Textarea(attrs={"rows": 6}),
        }

    def clean(self):
        cleaned_data = super().clean()
        quoted_amount = cleaned_data.get("quoted_amount")
        deposit_amount = cleaned_data.get("deposit_amount")
        expires_at = cleaned_data.get("quote_expires_at")

        if quoted_amount is not None and quoted_amount <= 0:
            self.add_error("quoted_amount", "Enter a quote amount greater than zero.")
        if deposit_amount is not None and deposit_amount < 0:
            self.add_error("deposit_amount", "The deposit cannot be negative.")
        if quoted_amount is not None and deposit_amount is not None and deposit_amount > quoted_amount:
            self.add_error("deposit_amount", "The deposit cannot exceed the total quote.")
        if expires_at and expires_at <= timezone.now():
            self.add_error("quote_expires_at", "Choose an expiration time in the future.")

        return cleaned_data


class PartnerAvailabilityForm(forms.ModelForm):
    class Meta:
        model = PartnerAvailability
        fields = ["date", "start_time", "end_time", "available", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_time"), cleaned.get("end_time")
        if bool(start) != bool(end):
            raise ValidationError("Enter both start and end time, or leave both blank for the full day.")
        if start and end and start >= end:
            raise ValidationError("End time must be later than start time.")
        return cleaned


class PartnerManagementForm(forms.ModelForm):
    """Operations form for adding and maintaining ceremony partners."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["application_status"].required = False

    def clean_application_status(self):
        return self.cleaned_data.get("application_status") or (
            self.instance.application_status
            if self.instance and self.instance.pk
            else Partner.ApplicationStatus.APPROVED
        )

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("active"):
            return cleaned_data

        if cleaned_data.get("application_status") != Partner.ApplicationStatus.APPROVED:
            self.add_error("active", "Approve the partner application before activation.")

        readiness_fields = {
            "food_permit_verified": "food-service permit",
            "insurance_verified": "insurance certificate",
            "cultural_training_verified": "training certificate",
        }
        missing_verifications = [
            label for field, label in readiness_fields.items() if not cleaned_data.get(field)
        ]
        if missing_verifications:
            self.add_error(
                "active",
                "Verify all required documents before activation: "
                + ", ".join(missing_verifications)
                + ".",
            )

        required_documents = {
            PartnerDocument.DocumentType.FOOD_PERMIT: "food-service permit",
            PartnerDocument.DocumentType.INSURANCE: "insurance certificate",
            PartnerDocument.DocumentType.TRAINING: "training certificate",
        }
        current_types = set()
        if self.instance and self.instance.pk:
            current_types = set(
                self.instance.documents.filter(
                    review_status=PartnerDocument.ReviewStatus.APPROVED,
                    expiry_date__gt=timezone.localdate(),
                ).values_list("document_type", flat=True)
            )
        missing_documents = [
            label for document_type, label in required_documents.items()
            if document_type not in current_types
        ]
        if missing_documents:
            self.add_error(
                "active",
                "Activation requires approved documents with expiry dates after today: "
                + ", ".join(missing_documents)
                + ".",
            )
        return cleaned_data

    class Meta:
        model = Partner
        fields = [
            "name",
            "partner_type",
            "contact_name",
            "email",
            "phone",
            "service_area",
            "address",
            "bio",
            "application_status",
            "payout_method",
            "payout_destination",
            "food_permit_verified",
            "insurance_verified",
            "cultural_training_verified",
            "active",
            "notes",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "bio": forms.Textarea(attrs={"rows": 4}),
            "notes": forms.Textarea(attrs={"rows": 5}),
        }


class PartnerRegistrationForm(SignupForm):
    name = forms.CharField(max_length=160, label="Business or public name")
    partner_type = forms.ChoiceField(choices=Partner.PartnerType.choices)
    contact_name = forms.CharField(max_length=120, label="Primary contact name")
    phone = forms.CharField(max_length=32)
    service_area = forms.CharField(max_length=180, help_text="Cities, ZIPs, or radius served")
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    bio = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}))
    payout_method = forms.ChoiceField(choices=Partner.PayoutMethod.choices, required=False)
    payout_destination = forms.CharField(
        required=False,
        max_length=160,
        help_text="Enter a payout email or masked account label only—never a full bank/card number.",
    )
    food_permit = forms.FileField(required=False, validators=[validate_partner_document])
    insurance_certificate = forms.FileField(required=False, validators=[validate_partner_document])
    identity_document = forms.FileField(required=False, validators=[validate_partner_document])
    training_certificate = forms.FileField(required=False, validators=[validate_partner_document])

    def clean_email(self):
        email = super().clean_email().lower()
        if Partner.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account already uses this email address.")
        return email


class CustomerRegistrationForm(SignupForm):
    first_name = forms.CharField(max_length=80)
    last_name = forms.CharField(max_length=80)
    phone = forms.CharField(max_length=32)
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
    city = forms.CharField(max_length=100)
    state = forms.CharField(max_length=60)
    postal_code = forms.CharField(max_length=20)
    marketing_opt_in = forms.BooleanField(required=False, label="Send me occasional Akako House updates")


class CustomerProfileForm(forms.ModelForm):
    class Meta:
        model = CustomerProfile
        fields = ["first_name", "last_name", "phone", "marketing_opt_in"]


class CustomerAddressForm(forms.ModelForm):
    class Meta:
        model = CustomerAddress
        fields = ["label", "address", "city", "state", "postal_code", "is_primary"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2})}


class CustomerCancellationRequestForm(forms.ModelForm):
    class Meta:
        model = CustomerCancellationRequest
        fields = ["reason"]
        widgets = {
            "reason": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Tell us why you need to cancel."}
            )
        }


class PartnerProfileForm(forms.ModelForm):
    class Meta:
        model = Partner
        fields = [
            "name",
            "contact_name",
            "phone",
            "service_area",
            "address",
            "bio",
            "payout_method",
            "payout_destination",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "bio": forms.Textarea(attrs={"rows": 4}),
        }


class PartnerDocumentForm(forms.ModelForm):
    class Meta:
        model = PartnerDocument
        fields = ["document_type", "file"]

    def clean_file(self):
        upload = self.cleaned_data["file"]
        validate_partner_document(upload)
        return upload


class PartnerTaskStatusForm(forms.Form):
    status = forms.ChoiceField(choices=PartnerTask.Status.choices)
    partner_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class PartnerPayoutForm(forms.ModelForm):
    class Meta:
        model = PartnerPayout
        fields = ["amount", "status", "reference", "notes"]
        widgets = {
            "amount": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class PartnerAssignmentForm(forms.Form):
    partner = forms.ModelChoiceField(
        queryset=Partner.objects.none(),
        empty_label="Select an available partner",
    )

    def __init__(self, *args, ceremony, **kwargs):
        super().__init__(*args, **kwargs)
        ready_partners = Partner.objects.filter(
            active=True,
            application_status=Partner.ApplicationStatus.APPROVED,
            food_permit_verified=True,
            insurance_verified=True,
            cultural_training_verified=True,
        )
        if ceremony.assigned_partner_id:
            ready_partners = Partner.objects.filter(
                Q(pk=ceremony.assigned_partner_id)
                | Q(
                    active=True,
                    application_status=Partner.ApplicationStatus.APPROVED,
                    food_permit_verified=True,
                    insurance_verified=True,
                    cultural_training_verified=True,
                )
            )

        from .services import partner_conflict_reason

        available_ids = [
            partner.pk
            for partner in ready_partners
            if partner.pk == ceremony.assigned_partner_id
            or not partner_conflict_reason(partner, ceremony.quote)
        ]
        self.fields["partner"].queryset = Partner.objects.filter(pk__in=available_ids).order_by("name")
        if ceremony.assigned_partner_id:
            self.initial["partner"] = ceremony.assigned_partner_id


class PaymentRecordForm(forms.Form):
    received_amount = forms.DecimalField(min_value=Decimal("0.01"), max_digits=8, decimal_places=2)
    provider_reference = forms.CharField(max_length=120, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, payment, **kwargs):
        super().__init__(*args, **kwargs)
        self.payment = payment
        self.initial.setdefault("received_amount", payment.expected_amount)

    def clean_received_amount(self):
        amount = self.cleaned_data["received_amount"]
        if amount < self.payment.expected_amount:
            raise forms.ValidationError(
                f"Enter at least the expected amount of ${self.payment.expected_amount}."
            )
        return amount


class FullPaymentForm(forms.Form):
    received_amount = forms.DecimalField(min_value=Decimal("0.01"), max_digits=8, decimal_places=2)
    provider_reference = forms.CharField(max_length=120, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, ceremony, **kwargs):
        super().__init__(*args, **kwargs)
        self.ceremony = ceremony
        self.total_due = sum(
            payment.expected_amount
            for payment in ceremony.payments.filter(
                payment_type__in=[Payment.PaymentType.DEPOSIT, Payment.PaymentType.FINAL]
            ).exclude(status__in=[Payment.Status.PAID, Payment.Status.WAIVED])
        )
        self.initial.setdefault("received_amount", self.total_due)

    def clean_received_amount(self):
        amount = self.cleaned_data["received_amount"]
        if amount < self.total_due:
            raise forms.ValidationError(f"Enter at least the full outstanding total of ${self.total_due}.")
        return amount


class CeremonyOutcomeForm(forms.Form):
    outcome_reason = forms.ChoiceField(choices=Ceremony.OutcomeReason.choices)
    outcome_notes = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Record what happened for the permanent history.",
    )
