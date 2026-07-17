from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, permission_required
from allauth.account import app_settings as allauth_settings
from allauth.account.models import EmailAddress
from allauth.account.utils import complete_signup
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count, Q, Sum
from django.core.exceptions import ImproperlyConfigured
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt

from .forms import (
    AccountIdentityForm,
    ClientOrganizationForm,
    CeremonyOutcomeForm,
    CustomerAddressForm,
    CustomerCancellationRequestForm,
    CustomerProfileForm,
    CustomerRegistrationForm,
    CustomerReviewForm,
    EventPhotoForm,
    FullPaymentForm,
    PartnerAssignmentForm,
    PartnerAvailabilityForm,
    PartnerDocumentForm,
    PartnerManagementForm,
    PartnerGalleryPhotoForm,
    PartnerPayoutForm,
    PartnerProfileForm,
    PartnerRegistrationForm,
    PartnerTaskStatusForm,
    PaymentRecordForm,
    QuoteContactForm,
    QuoteLeadForm,
    QuoteManagementForm,
    ShopInterestForm,
    TestimonialForm,
)
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
    Testimonial,
)
from .payment_provider import construct_webhook_event, create_checkout_session
from .notifications import (
    create_notification,
    notify_assignment,
    notify_partner_accepted,
    notify_payment_received,
    notify_quote_accepted,
    notify_quote_expired,
    notify_staff,
)
from .services import (
    accept_availability_offer,
    build_absolute_quote_url,
    claim_customer_records,
    convert_capacity_hold,
    eligible_partners_for_quote,
    fulfill_payment_checkout,
    process_workflow_deadlines,
    release_quote_holds,
    send_assignment_confirmation_email,
    send_availability_offer_email,
    send_payment_confirmation_email,
    send_payment_options_email,
    send_quote_email,
)


def home(request):
    if request.method == "POST":
        form = QuoteLeadForm(request.POST)
        if form.is_valid():
            quote_request = form.save(commit=False)
            if request.user.is_authenticated and hasattr(request.user, "customer_profile"):
                quote_request.customer = request.user.customer_profile
            quote_request.save()
            return redirect("quote_contact", public_id=quote_request.public_id)
    else:
        form = QuoteLeadForm()
    approved_reviews = CustomerReview.objects.filter(
        status=CustomerReview.Status.APPROVED,
        permission_to_publish=True,
    ).select_related("customer", "ceremony__quote")
    review_summary = approved_reviews.aggregate(average=Avg("rating"), total=Count("id"))
    return render(
        request,
        "bookings/home.html",
        {
            "form": form,
            "client_organizations": ClientOrganization.objects.filter(active=True)[:12],
            "testimonials": Testimonial.objects.filter(
                active=True, consent_confirmed=True
            ).order_by("-featured", "display_order")[:6],
            "event_photos": EventPhoto.objects.filter(
                active=True,
                usage_rights_confirmed=True,
                placement=EventPhoto.Placement.GALLERY,
            ).order_by("-featured", "display_order")[:8],
            "partner_gallery_photos": PartnerGalleryPhoto.objects.filter(
                status=PartnerGalleryPhoto.Status.APPROVED,
                active=True,
                partner__active=True,
            ).select_related("partner").order_by("-featured", "display_order")[:6],
            "customer_reviews": approved_reviews.order_by("-featured", "-created_at")[:6],
            "review_average": review_summary["average"],
            "review_total": review_summary["total"],
        },
    )


@login_required
def account_home(request):
    return render(request, "bookings/account_overview.html")


@login_required
def profile_home(request):
    if hasattr(request.user, "customer_profile"):
        return redirect("customer_profile")
    if hasattr(request.user, "partner_profile"):
        return redirect("partner_profile")
    if request.method == "POST":
        form = AccountIdentityForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Your profile was updated.")
            return redirect("profile_home")
    else:
        form = AccountIdentityForm(instance=request.user)
    return render(request, "bookings/user_profile.html", {"form": form})


@login_required
def notification_center(request):
    notifications = Notification.objects.filter(recipient=request.user)[:100]
    return render(
        request,
        "bookings/notification_center.html",
        {"notifications": notifications},
    )


@login_required
def notification_mark_all_read(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Notifications must be updated from the notification center.")
    Notification.objects.filter(recipient=request.user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    return redirect("notification_center")


@login_required
def notification_open(request, notification_id):
    notification = get_object_or_404(Notification, pk=notification_id, recipient=request.user)
    if not notification.read_at:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    if notification.action_url.startswith("/") and not notification.action_url.startswith("//"):
        return redirect(notification.action_url)
    return redirect("notification_center")


def contact(request):
    return render(request, "bookings/contact.html")


def about(request):
    approved_photos = EventPhoto.objects.filter(
        active=True,
        usage_rights_confirmed=True,
    ).order_by("-featured", "display_order", "-created_at")
    return render(
        request,
        "bookings/about.html",
        {
            "origin_photo": approved_photos.filter(
                placement=EventPhoto.Placement.ABOUT_ORIGIN
            ).first(),
            "ceremony_photo": approved_photos.filter(
                placement=EventPhoto.Placement.ABOUT_CEREMONY
            ).first(),
        },
    )


def shop(request):
    initial = {}
    if request.user.is_authenticated:
        initial["email"] = request.user.email
    if request.method == "POST":
        form = ShopInterestForm(request.POST)
        if form.is_valid():
            interest, created = ShopInterest.objects.update_or_create(
                email=form.cleaned_data["email"],
                defaults={
                    "postal_code": form.cleaned_data["postal_code"],
                    "purchase_frequency": form.cleaned_data["purchase_frequency"],
                    "marketing_consent": True,
                    "active": True,
                },
            )
            messages.success(
                request,
                "You're on the list. We'll let you know when Akako House coffee is ready to order."
                if created else
                "Your coffee launch preferences have been updated.",
            )
            return redirect("shop")
    else:
        form = ShopInterestForm(initial=initial)
    return render(request, "bookings/shop.html", {"form": form})


def partner_registration(request):
    if request.user.is_authenticated:
        if hasattr(request.user, "partner_profile"):
            return redirect("partner_dashboard")
        if request.user.is_staff:
            return redirect("operations_dashboard")
    if request.method == "POST":
        form = PartnerRegistrationForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                user = form.save(request)
                partner = Partner.objects.create(
                    user=user,
                    name=form.cleaned_data["name"],
                    partner_type=form.cleaned_data["partner_type"],
                    contact_name=form.cleaned_data["contact_name"],
                    email=form.cleaned_data["email"],
                    phone=form.cleaned_data["phone"],
                    service_area=form.cleaned_data["service_area"],
                    address=form.cleaned_data["address"],
                    bio=form.cleaned_data["bio"],
                    payout_method=form.cleaned_data["payout_method"],
                    payout_destination=form.cleaned_data["payout_destination"],
                    application_status=Partner.ApplicationStatus.SUBMITTED,
                    active=False,
                )
                document_fields = {
                    "food_permit": PartnerDocument.DocumentType.FOOD_PERMIT,
                    "insurance_certificate": PartnerDocument.DocumentType.INSURANCE,
                    "identity_document": PartnerDocument.DocumentType.IDENTITY,
                    "training_certificate": PartnerDocument.DocumentType.TRAINING,
                }
                for field_name, document_type in document_fields.items():
                    upload = form.cleaned_data.get(field_name)
                    if upload:
                        PartnerDocument.objects.create(
                            partner=partner,
                            document_type=document_type,
                            file=upload,
                        )
            messages.success(request, "Your partner application was submitted for review.")
            return complete_signup(
                request,
                user,
                allauth_settings.EMAIL_VERIFICATION,
                reverse("partner_dashboard"),
            )
    else:
        form = PartnerRegistrationForm()
    return render(request, "bookings/partner_registration.html", {"form": form})


def customer_registration(request):
    if request.user.is_authenticated:
        if hasattr(request.user, "customer_profile"):
            return redirect("customer_dashboard")
        if request.user.is_staff:
            return redirect("operations_dashboard")
        if hasattr(request.user, "partner_profile"):
            return redirect("partner_dashboard")
    if request.method == "POST":
        form = CustomerRegistrationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save(request)
                customer = CustomerProfile.objects.create(
                    user=user,
                    first_name=form.cleaned_data["first_name"],
                    last_name=form.cleaned_data["last_name"],
                    phone=form.cleaned_data["phone"],
                    marketing_opt_in=form.cleaned_data["marketing_opt_in"],
                )
                CustomerAddress.objects.create(
                    customer=customer,
                    label="Primary address",
                    address=form.cleaned_data["address"],
                    city=form.cleaned_data["city"],
                    state=form.cleaned_data["state"],
                    postal_code=form.cleaned_data["postal_code"],
                    is_primary=True,
                )
            messages.success(request, "Your customer account was created. Verify your email to continue.")
            return complete_signup(
                request,
                user,
                allauth_settings.EMAIL_VERIFICATION,
                reverse("customer_dashboard"),
            )
    else:
        form = CustomerRegistrationForm()
    return render(request, "bookings/customer_registration.html", {"form": form})


def _current_partner(request):
    try:
        return request.user.partner_profile
    except Partner.DoesNotExist as exc:
        raise PermissionDenied("This account does not have a partner profile.") from exc


def _current_customer(request):
    try:
        return request.user.customer_profile
    except CustomerProfile.DoesNotExist as exc:
        raise PermissionDenied("This account does not have a customer profile.") from exc


@login_required
def customer_dashboard(request):
    customer = _current_customer(request)
    if EmailAddress.objects.filter(user=request.user, email__iexact=request.user.email, verified=True).exists():
        claim_customer_records(customer)
    quotes = customer.quote_requests.select_related("ceremony", "ceremony__assigned_partner")
    ceremonies = Ceremony.objects.select_related("quote", "assigned_partner").filter(quote__customer=customer)
    return render(
        request,
        "bookings/customer_dashboard.html",
        {"customer": customer, "quotes": quotes, "ceremonies": ceremonies},
    )


@login_required
def customer_profile(request):
    customer = _current_customer(request)
    if request.method == "POST":
        action = request.POST.get("action", "profile")
        if action == "address":
            address_form = CustomerAddressForm(request.POST)
            profile_form = CustomerProfileForm(instance=customer)
            if address_form.is_valid():
                address = address_form.save(commit=False)
                address.customer = customer
                if address.is_primary:
                    customer.addresses.update(is_primary=False)
                address.save()
                messages.success(request, "Address saved.")
                return redirect("customer_profile")
        else:
            profile_form = CustomerProfileForm(request.POST, instance=customer)
            address_form = CustomerAddressForm()
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Your customer profile was updated.")
                return redirect("customer_profile")
    else:
        profile_form = CustomerProfileForm(instance=customer)
        address_form = CustomerAddressForm()
    return render(
        request,
        "bookings/customer_profile.html",
        {
            "customer": customer,
            "profile_form": profile_form,
            "address_form": address_form,
            "addresses": customer.addresses.all(),
        },
    )


@login_required
def customer_address_delete(request, address_id):
    customer = _current_customer(request)
    if request.method != "POST":
        return HttpResponseBadRequest("Addresses must be removed from the profile page.")
    address = get_object_or_404(CustomerAddress, pk=address_id, customer=customer)
    address.delete()
    if not customer.addresses.filter(is_primary=True).exists():
        first = customer.addresses.first()
        if first:
            first.is_primary = True
            first.save(update_fields=["is_primary"])
    messages.success(request, "Address removed.")
    return redirect("customer_profile")


@login_required
def customer_quote_detail(request, public_id):
    customer = _current_customer(request)
    quote = get_object_or_404(
        QuoteRequest.objects.select_related("ceremony", "ceremony__assigned_partner"),
        public_id=public_id,
        customer=customer,
    )
    ceremony = getattr(quote, "ceremony", None)
    cancellation_requests = ceremony.customer_cancellation_requests.all() if ceremony else []
    customer_review = CustomerReview.objects.filter(ceremony=ceremony).first() if ceremony else None
    return render(
        request,
        "bookings/customer_quote_detail.html",
        {
            "customer": customer,
            "quote_request": quote,
            "ceremony": ceremony,
            "payments": ceremony.payments.all() if ceremony else [],
            "cancellation_form": CustomerCancellationRequestForm(),
            "cancellation_request": (
                ceremony.customer_cancellation_requests.filter(
                    status=CustomerCancellationRequest.Status.PENDING
                ).first()
                if ceremony else None
            ),
            "latest_cancellation_request": cancellation_requests.first() if ceremony else None,
            "customer_review": customer_review,
            "review_form": (
                CustomerReviewForm()
                if ceremony and ceremony.status == Ceremony.Status.COMPLETED and not customer_review
                else None
            ),
        },
    )


@login_required
def customer_payment_receipt(request, payment_id):
    customer = _current_customer(request)
    payment = get_object_or_404(
        Payment.objects.select_related("ceremony__quote"),
        pk=payment_id,
        ceremony__quote__customer=customer,
        status__in=[Payment.Status.PAID, Payment.Status.REFUNDED, Payment.Status.FORFEITED],
    )
    return render(
        request,
        "bookings/customer_payment_receipt.html",
        {"customer": customer, "payment": payment, "quote_request": payment.ceremony.quote},
    )


@login_required
def customer_request_cancellation(request, public_id):
    customer = _current_customer(request)
    ceremony = get_object_or_404(Ceremony.objects.select_related("quote"), public_id=public_id, quote__customer=customer)
    if request.method != "POST":
        return HttpResponseBadRequest("Cancellation requests must be submitted from your booking page.")
    if ceremony.terminal:
        messages.error(request, "This ceremony is already closed.")
    elif ceremony.customer_cancellation_requests.filter(status=CustomerCancellationRequest.Status.PENDING).exists():
        messages.info(request, "A cancellation request is already awaiting staff review.")
    else:
        form = CustomerCancellationRequestForm(request.POST)
        if form.is_valid():
            cancellation = form.save(commit=False)
            cancellation.customer = customer
            cancellation.ceremony = ceremony
            cancellation.save()
            messages.success(request, "Your cancellation request was sent for staff review.")
        else:
            messages.error(request, "Please provide a reason for the cancellation request.")
    return redirect("customer_quote_detail", public_id=ceremony.quote.public_id)


@login_required
def customer_submit_review(request, public_id):
    customer = _current_customer(request)
    ceremony = get_object_or_404(
        Ceremony.objects.select_related("quote"),
        public_id=public_id,
        quote__customer=customer,
        status=Ceremony.Status.COMPLETED,
    )
    if request.method != "POST":
        return HttpResponseBadRequest("Reviews must be submitted from the completed ceremony page.")
    if CustomerReview.objects.filter(ceremony=ceremony).exists():
        messages.info(request, "A review has already been submitted for this ceremony.")
        return redirect("customer_quote_detail", public_id=ceremony.quote.public_id)
    form = CustomerReviewForm(request.POST)
    if form.is_valid():
        review = form.save(commit=False)
        review.customer = customer
        review.ceremony = ceremony
        review.save()
        notify_staff(
            kind=Notification.Kind.REVIEW_SUBMITTED,
            title="New customer review",
            message=f"{customer.full_name} submitted a {review.rating}-star review for moderation.",
            event_key=f"review:{review.pk}:submitted",
            action_url=reverse("content_library"),
            send_email=True,
        )
        messages.success(request, "Thank you. Your review was submitted for publication review.")
    else:
        messages.error(request, "Check the rating, review, and publication permission.")
    return redirect("customer_quote_detail", public_id=ceremony.quote.public_id)


@login_required
def partner_dashboard(request):
    partner = _current_partner(request)
    tasks = partner.tasks.select_related("ceremony__quote").all()
    payouts = partner.payouts.select_related("task__ceremony__quote")
    offers = partner.availability_offers.filter(
        status=AvailabilityOffer.Status.PENDING,
        expires_at__gt=timezone.now(),
    ).select_related("quote")
    return render(
        request,
        "bookings/partner_dashboard.html",
        {
            "partner": partner,
            "tasks": tasks,
            "availability_offers": offers,
            "recent_payouts": payouts[:5],
            "paid_total": payouts.filter(status=PartnerPayout.Status.PAID).aggregate(total=Sum("amount"))["total"] or 0,
            "pending_total": payouts.filter(status__in=[PartnerPayout.Status.PENDING, PartnerPayout.Status.APPROVED, PartnerPayout.Status.PROCESSING]).aggregate(total=Sum("amount"))["total"] or 0,
        },
    )


@login_required
def partner_availability(request):
    partner = _current_partner(request)
    if request.method == "POST":
        form = PartnerAvailabilityForm(request.POST)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.partner = partner
            entry.save()
            messages.success(request, "Your availability calendar was updated.")
            return redirect("partner_availability")
    else:
        form = PartnerAvailabilityForm()
    entries = partner.availability.filter(date__gte=timezone.localdate())
    return render(
        request,
        "bookings/partner_availability.html",
        {"partner": partner, "form": form, "entries": entries},
    )


@login_required
def partner_availability_delete(request, entry_id):
    partner = _current_partner(request)
    entry = get_object_or_404(PartnerAvailability, pk=entry_id, partner=partner)
    if request.method != "POST":
        return HttpResponseBadRequest("Calendar entries must be removed from the availability page.")
    entry.delete()
    messages.success(request, "Availability entry removed.")
    return redirect("partner_availability")


@login_required
def partner_offer_response(request, offer_id, decision):
    partner = _current_partner(request)
    offer = get_object_or_404(AvailabilityOffer, pk=offer_id, partner=partner)
    if request.method != "POST":
        return HttpResponseBadRequest("Availability responses must be submitted from your workspace.")
    notes = request.POST.get("response_notes", "").strip()
    try:
        if decision == "accept":
            accept_availability_offer(offer, notes=notes)
            messages.success(request, "Thank you. This time is now held while the customer reviews the quote.")
        elif decision == "decline":
            if offer.status != AvailabilityOffer.Status.PENDING:
                raise ValueError("This availability request is already closed.")
            offer.status = AvailabilityOffer.Status.DECLINED
            offer.response_notes = notes
            offer.responded_at = timezone.now()
            offer.save(update_fields=["status", "response_notes", "responded_at"])
            messages.info(request, "The availability request was declined.")
        else:
            return HttpResponseBadRequest("Unknown availability response.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("partner_dashboard")


@login_required
def partner_profile(request):
    partner = _current_partner(request)
    if request.method == "POST":
        form = PartnerProfileForm(request.POST, instance=partner)
        if form.is_valid():
            form.save()
            messages.success(request, "Your profile was updated.")
            return redirect("partner_profile")
    else:
        form = PartnerProfileForm(instance=partner)
    return render(request, "bookings/partner_profile.html", {"partner": partner, "form": form})


@login_required
def partner_documents(request):
    partner = _current_partner(request)
    if request.method == "POST":
        form = PartnerDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            document = form.save(commit=False)
            document.partner = partner
            document.save()
            if partner.application_status == Partner.ApplicationStatus.NEEDS_INFO:
                partner.application_status = Partner.ApplicationStatus.SUBMITTED
                partner.save(update_fields=["application_status"])
            messages.success(request, "Document uploaded for review.")
            return redirect("partner_documents")
    else:
        form = PartnerDocumentForm()
    return render(
        request,
        "bookings/partner_documents.html",
        {"partner": partner, "form": form, "documents": partner.documents.all()},
    )


@login_required
def partner_gallery(request):
    partner = _current_partner(request)
    if request.method == "POST":
        if partner.gallery_photos.count() >= 8:
            messages.error(request, "Your gallery can contain up to eight photos.")
            return redirect("partner_gallery")
        form = PartnerGalleryPhotoForm(request.POST, request.FILES)
        if form.is_valid():
            photo = form.save(commit=False)
            photo.partner = partner
            photo.save()
            notify_staff(
                kind=Notification.Kind.GALLERY_UPLOADED,
                title="New partner gallery upload",
                message=f"{partner.name} uploaded a gallery photograph for moderation.",
                event_key=f"gallery-photo:{photo.pk}:submitted",
                action_url=reverse("content_library"),
                send_email=True,
            )
            messages.success(request, "Photo uploaded and sent to staff for approval.")
            return redirect("partner_gallery")
    else:
        form = PartnerGalleryPhotoForm()
    return render(
        request,
        "bookings/partner_gallery.html",
        {"partner": partner, "form": form, "photos": partner.gallery_photos.all()},
    )


@login_required
def partner_gallery_delete(request, photo_id):
    partner = _current_partner(request)
    photo = get_object_or_404(PartnerGalleryPhoto, pk=photo_id, partner=partner)
    if request.method != "POST":
        return HttpResponseBadRequest("Gallery photos must be removed from the gallery page.")
    photo.delete()
    messages.success(request, "Gallery photo removed.")
    return redirect("partner_gallery")


@login_required
def partner_document_download(request, document_id):
    document = get_object_or_404(PartnerDocument.objects.select_related("partner"), pk=document_id)
    is_owner = getattr(request.user, "partner_profile", None) == document.partner
    if not (is_owner or (request.user.is_staff and request.user.has_perm("bookings.view_partner"))):
        raise PermissionDenied
    try:
        return FileResponse(document.file.open("rb"), as_attachment=True, filename=document.file.name.rsplit("/", 1)[-1])
    except FileNotFoundError as exc:
        raise Http404("Document file is unavailable.") from exc


@login_required
def partner_task_detail(request, task_id):
    partner = _current_partner(request)
    task = get_object_or_404(
        PartnerTask.objects.select_related("ceremony__quote", "payout"),
        pk=task_id,
        partner=partner,
    )
    return render(request, "bookings/partner_task_detail.html", {"partner": partner, "task": task})


@login_required
def partner_task_update(request, task_id):
    partner = _current_partner(request)
    task = get_object_or_404(PartnerTask, pk=task_id, partner=partner)
    if request.method != "POST":
        return HttpResponseBadRequest("Task updates must be submitted from the task workspace.")
    form = PartnerTaskStatusForm(request.POST)
    allowed_transitions = {
        PartnerTask.Status.ASSIGNED: {PartnerTask.Status.ACCEPTED, PartnerTask.Status.ISSUE, PartnerTask.Status.CANCELLED},
        PartnerTask.Status.ACCEPTED: {PartnerTask.Status.IN_PROGRESS, PartnerTask.Status.ISSUE, PartnerTask.Status.CANCELLED},
        PartnerTask.Status.IN_PROGRESS: {PartnerTask.Status.DELIVERED, PartnerTask.Status.ISSUE, PartnerTask.Status.CANCELLED},
        PartnerTask.Status.ISSUE: {PartnerTask.Status.ACCEPTED, PartnerTask.Status.IN_PROGRESS, PartnerTask.Status.CANCELLED},
    }
    if not form.is_valid() or form.cleaned_data["status"] not in allowed_transitions.get(task.status, set()):
        messages.error(request, "That task status change is not available.")
        return redirect("partner_task_detail", task_id=task.pk)
    now = timezone.now()
    task.status = form.cleaned_data["status"]
    task.partner_notes = form.cleaned_data["partner_notes"]
    if task.status == PartnerTask.Status.ACCEPTED and not task.accepted_at:
        task.accepted_at = now
    elif task.status == PartnerTask.Status.IN_PROGRESS and not task.started_at:
        task.started_at = now
    elif task.status == PartnerTask.Status.DELIVERED:
        task.delivered_at = now
        payout, _ = PartnerPayout.objects.get_or_create(task=task, defaults={"partner": partner})
        if payout.status == PartnerPayout.Status.NOT_READY:
            payout.status = PartnerPayout.Status.PENDING
            payout.save(update_fields=["status", "updated_at"])
    elif task.status == PartnerTask.Status.CANCELLED:
        ceremony = task.ceremony
        previous = ceremony.status
        ceremony.assigned_partner = None
        ceremony.coverage_status = Ceremony.CoverageStatus.UNCOVERED
        ceremony.status = Ceremony.Status.AWAITING_PARTNER
        ceremony.save(update_fields=["assigned_partner", "coverage_status", "status", "updated_at"])
        release_quote_holds(ceremony.quote, "Assigned partner withdrew from the ceremony.")
        payout, _ = PartnerPayout.objects.get_or_create(task=task, defaults={"partner": partner})
        if payout.status != PartnerPayout.Status.PAID:
            payout.status = PartnerPayout.Status.HELD
            payout.save(update_fields=["status", "updated_at"])
        ceremony.history.create(
            from_status=previous,
            to_status=ceremony.status,
            note=f"{partner.name} withdrew from the assignment: {task.partner_notes or 'No reason supplied.'}",
        )
    task.save()
    if task.status == PartnerTask.Status.ACCEPTED:
        notify_partner_accepted(task)
    messages.success(request, f"Task marked {task.get_status_display()}.")
    return redirect("partner_task_detail", task_id=task.pk)


def quote_contact(request, public_id):
    quote_request = get_object_or_404(QuoteRequest, public_id=public_id)
    customer = (
        request.user.customer_profile
        if request.user.is_authenticated and hasattr(request.user, "customer_profile")
        else None
    )
    if request.method == "POST":
        form = QuoteContactForm(request.POST, instance=quote_request)
        if form.is_valid():
            quote_request = form.save(commit=False)
            if customer:
                quote_request.customer = customer
            quote_request.save()
            notify_staff(
                kind=Notification.Kind.QUOTE_NEW,
                title="New quote request",
                message=(
                    f"{quote_request.customer_name} requested a {quote_request.get_event_type_display()} "
                    f"for {quote_request.event_date} in {quote_request.location}."
                ),
                event_key=f"quote:{quote_request.pk}:submitted",
                action_url=reverse("manage_quote", args=[quote_request.public_id]),
                send_email=True,
            )
            messages.success(
                request,
                "Your ceremony request was received. We will review the details and send a custom quote.",
            )
            return redirect("quote_success", public_id=quote_request.public_id)
    else:
        initial = {}
        if customer:
            initial = {
                "customer_name": customer.full_name,
                "email": customer.user.email,
                "phone": customer.phone,
            }
        form = QuoteContactForm(instance=quote_request, initial=initial)
    return render(
        request,
        "bookings/quote_contact.html",
        {"form": form, "quote_request": quote_request},
    )


def quote_success(request, public_id):
    quote_request = get_object_or_404(QuoteRequest, public_id=public_id)
    return render(request, "bookings/quote_success.html", {"quote_request": quote_request})


def quote_review(request, public_id):
    quote_request = get_object_or_404(QuoteRequest, public_id=public_id)
    if quote_request.quote_expired:
        quote_request.status = QuoteRequest.Status.EXPIRED
        quote_request.save(update_fields=["status", "updated_at"])
        release_quote_holds(quote_request, "Customer quote expired.", expired=True)
        notify_quote_expired(quote_request)
    return render(request, "bookings/quote_review.html", {"quote_request": quote_request})


def quote_decision(request, public_id, decision):
    quote_request = get_object_or_404(QuoteRequest, public_id=public_id)
    if request.method != "POST":
        return HttpResponseBadRequest("Quote decisions must be submitted from the quote page.")
    if quote_request.quote_expired:
        quote_request.status = QuoteRequest.Status.EXPIRED
        quote_request.save(update_fields=["status", "updated_at"])
        release_quote_holds(quote_request, "Customer quote expired.", expired=True)
        notify_quote_expired(quote_request)
        messages.error(request, "This quote has expired. Please request an updated quote.")
        return redirect("quote_review", public_id=quote_request.public_id)
    if quote_request.status != QuoteRequest.Status.QUOTED:
        messages.info(request, "This quote has already received a decision.")
        return redirect("quote_review", public_id=quote_request.public_id)
    if decision == "accept":
        hold = quote_request.active_capacity_hold
        if not hold:
            messages.error(request, "Partner availability changed before acceptance. Akako House will confirm a replacement and update your quote.")
            quote_request.status = QuoteRequest.Status.WAITLISTED
            quote_request.save(update_fields=["status", "updated_at"])
            return redirect("quote_review", public_id=quote_request.public_id)
        ceremony = quote_request.accept_quote()
        hold.status = CapacityHold.Status.CONFIRMED
        hold.expires_at = None
        hold.save(update_fields=["status", "expires_at", "updated_at"])
        ceremony.coverage_status = Ceremony.CoverageStatus.HELD
        ceremony.save(update_fields=["coverage_status", "updated_at"])
        if not ceremony.deposit_payment or ceremony.deposit_payment.status == Payment.Status.WAIVED:
            convert_capacity_hold(ceremony)
        try:
            send_payment_options_email(
                ceremony,
                request.build_absolute_uri(
                    reverse("ceremony_payment", args=[ceremony.public_id])
                ),
            )
        except Exception:
            pass
        notify_quote_accepted(quote_request)
        messages.success(
            request,
            "Quote accepted. We will follow up with deposit and scheduling details.",
        )
    elif decision == "decline":
        quote_request.decline_quote()
        release_quote_holds(quote_request, "Customer declined the quote.")
        messages.info(request, "Quote declined. Thank you for considering Akako House.")
    else:
        return HttpResponseBadRequest("Unknown quote decision.")
    return redirect("quote_review", public_id=quote_request.public_id)


def ceremony_payment(request, public_id):
    ceremony = get_object_or_404(
        Ceremony.objects.select_related("quote", "assigned_partner"),
        public_id=public_id,
    )
    deposit = ceremony.deposit_payment
    final_payment = ceremony.final_payment
    outstanding = sum(
        payment.expected_amount
        for payment in ceremony.payments.filter(
            payment_type__in=[Payment.PaymentType.DEPOSIT, Payment.PaymentType.FINAL]
        ).exclude(status__in=[Payment.Status.PAID, Payment.Status.WAIVED])
    )
    coverage_confirmed = ceremony.coverage_status == Ceremony.CoverageStatus.UNRESERVED or ceremony.quote.capacity_holds.filter(
        status__in=[CapacityHold.Status.CONFIRMED, CapacityHold.Status.CONVERTED]
    ).exists()
    return render(
        request,
        "bookings/ceremony_payment.html",
        {
            "ceremony": ceremony,
            "quote_request": ceremony.quote,
            "deposit": deposit,
            "final_payment": final_payment,
            "outstanding": outstanding,
            "coverage_confirmed": coverage_confirmed,
            "checkout_enabled": bool(
                coverage_confirmed
                and settings.PAYMENT_PROVIDER == "stripe" and settings.STRIPE_SECRET_KEY
            ),
            "checkout_success": request.GET.get("checkout") == "success",
            "checkout_cancelled": request.GET.get("checkout") == "cancelled",
        },
    )


def start_payment_checkout(request, public_id, choice):
    ceremony = get_object_or_404(Ceremony.objects.select_related("quote"), public_id=public_id)
    if request.method != "POST":
        return HttpResponseBadRequest("Checkout must be started from the payment page.")
    if ceremony.terminal:
        messages.error(request, "This ceremony is closed and cannot accept a payment.")
        return redirect("ceremony_payment", public_id=ceremony.public_id)
    if ceremony.coverage_status != Ceremony.CoverageStatus.UNRESERVED and not ceremony.quote.capacity_holds.filter(
        status__in=[CapacityHold.Status.CONFIRMED, CapacityHold.Status.CONVERTED]
    ).exists():
        messages.error(request, "Payment is paused until Akako House reconfirms partner coverage.")
        return redirect("ceremony_payment", public_id=ceremony.public_id)

    valid_choices = {value for value, _label in PaymentCheckout.Choice.choices}
    if choice not in valid_choices:
        return HttpResponseBadRequest("Unknown payment choice.")
    if choice == PaymentCheckout.Choice.FULL:
        payments = ceremony.payments.filter(
            payment_type__in=[Payment.PaymentType.DEPOSIT, Payment.PaymentType.FINAL]
        ).exclude(status__in=[Payment.Status.PAID, Payment.Status.WAIVED])
        amount = sum(payment.expected_amount for payment in payments)
    else:
        payment = get_object_or_404(Payment, ceremony=ceremony, payment_type=choice)
        if payment.status in [Payment.Status.PAID, Payment.Status.WAIVED]:
            messages.info(request, "That payment obligation is already settled.")
            return redirect("ceremony_payment", public_id=ceremony.public_id)
        amount = payment.expected_amount
    if amount <= 0:
        messages.info(request, "There is no outstanding amount for this payment choice.")
        return redirect("ceremony_payment", public_id=ceremony.public_id)

    payment_url = reverse("ceremony_payment", args=[ceremony.public_id])
    success_url = request.build_absolute_uri(payment_url) + "?checkout=success"
    cancel_url = request.build_absolute_uri(payment_url) + "?checkout=cancelled"
    try:
        provider_session = create_checkout_session(
            ceremony=ceremony,
            choice=choice,
            amount=amount,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except ImproperlyConfigured:
        messages.error(request, "Online payment is not configured yet. Please contact Akako House.")
        return redirect("ceremony_payment", public_id=ceremony.public_id)

    checkout = PaymentCheckout.objects.create(
        ceremony=ceremony,
        provider="stripe",
        provider_session_id=provider_session["id"],
        payment_choice=choice,
        amount=amount,
        currency=provider_session["currency"],
        checkout_url=provider_session["url"],
    )
    return redirect(checkout.checkout_url)


@csrf_exempt
def stripe_webhook(request):
    if request.method != "POST":
        return HttpResponse(status=405)
    try:
        event = construct_webhook_event(
            request.body,
            request.headers.get("Stripe-Signature", ""),
        )
    except Exception:
        return HttpResponse(status=400)

    event_type = event["type"]
    session = event["data"]["object"]
    checkout = PaymentCheckout.objects.filter(provider_session_id=session["id"]).first()
    if not checkout:
        return HttpResponse(status=200)
    if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        try:
            fulfill_payment_checkout(
                checkout,
                payment_reference=session.get("payment_intent") or session["id"],
            )
        except ValueError:
            return HttpResponse(status=409)
    elif event_type == "checkout.session.expired":
        PaymentCheckout.objects.filter(
            pk=checkout.pk,
            status=PaymentCheckout.Status.CREATED,
        ).update(status=PaymentCheckout.Status.EXPIRED, updated_at=timezone.now())
    elif event_type == "checkout.session.async_payment_failed":
        PaymentCheckout.objects.filter(pk=checkout.pk).update(
            status=PaymentCheckout.Status.FAILED,
            updated_at=timezone.now(),
        )
    return HttpResponse(status=200)


@staff_member_required
def operations_dashboard(request):
    process_workflow_deadlines(request.user)
    today = timezone.localdate()
    expiry_warning_date = today + timezone.timedelta(days=settings.DOCUMENT_EXPIRY_WARNING_DAYS)
    quote_counts = {
        item["status"]: item["total"]
        for item in QuoteRequest.objects.values("status").annotate(total=Count("id"))
    }
    ceremony_counts = {
        item["status"]: item["total"]
        for item in Ceremony.objects.values("status").annotate(total=Count("id"))
    }
    active_partners = Partner.objects.filter(active=True).count()
    compliance_attention = Partner.objects.filter(active=True).filter(
        Q(food_permit_verified=False)
        | Q(insurance_verified=False)
        | Q(cultural_training_verified=False)
    ).count()

    context = {
        "quote_counts": quote_counts,
        "ceremony_counts": ceremony_counts,
        "active_partners": active_partners,
        "compliance_attention": compliance_attention,
        "pending_partner_applications": Partner.objects.filter(
            application_status__in=[
                Partner.ApplicationStatus.SUBMITTED,
                Partner.ApplicationStatus.NEEDS_INFO,
            ]
        ).order_by("created_at")[:8],
        "expiring_documents": PartnerDocument.objects.select_related("partner").filter(
            review_status=PartnerDocument.ReviewStatus.APPROVED,
            expiry_date__gt=today,
            expiry_date__lte=expiry_warning_date,
        ).order_by("expiry_date")[:8],
        "cancellation_queue": CustomerCancellationRequest.objects.select_related(
            "customer", "ceremony__quote"
        ).filter(status=CustomerCancellationRequest.Status.PENDING)[:8],
        "waiting_quotes": QuoteRequest.objects.filter(
            status__in=[QuoteRequest.Status.NEW, QuoteRequest.Status.REVIEWING]
        ).order_by("created_at")[:8],
        "capacity_queue": QuoteRequest.objects.filter(
            Q(status=QuoteRequest.Status.WAITLISTED)
            | Q(
                availability_offers__status=AvailabilityOffer.Status.PENDING,
                availability_offers__expires_at__lte=timezone.now() + timezone.timedelta(hours=6),
            )
        ).distinct().order_by("event_date")[:8],
        "uncovered_queue": Ceremony.objects.select_related("quote").filter(
            coverage_status=Ceremony.CoverageStatus.UNCOVERED
        )[:8],
        "payment_queue": Ceremony.objects.select_related("quote").filter(
            status__in=[Ceremony.Status.AWAITING_DEPOSIT, Ceremony.Status.AT_RISK]
        )[:8],
        "assignment_queue": Ceremony.objects.select_related("quote").filter(
            status=Ceremony.Status.AWAITING_PARTNER
        )[:8],
        "upcoming_ceremonies": Ceremony.objects.select_related(
            "quote", "assigned_partner"
        ).filter(
            status__in=[Ceremony.Status.ASSIGNED, Ceremony.Status.READY]
        )[:8],
    }
    return render(request, "bookings/dashboard.html", context)


@staff_member_required
def content_library(request):
    forms_by_type = {
        "organization": ClientOrganizationForm,
        "testimonial": TestimonialForm,
        "event_photo": EventPhotoForm,
    }
    bound_type = request.POST.get("content_type") if request.method == "POST" else ""
    content_forms = {
        key: form_class(
            request.POST if bound_type == key else None,
            request.FILES if bound_type == key else None,
            prefix=key,
        )
        for key, form_class in forms_by_type.items()
    }
    if request.method == "POST" and bound_type in content_forms:
        form = content_forms[bound_type]
        if form.is_valid():
            form.save()
            messages.success(request, "Homepage content saved. It will appear only while active.")
            return redirect("content_library")
        messages.error(request, "Check the content fields and usage permission.")
    return render(
        request,
        "bookings/content_library.html",
        {
            "content_forms": content_forms,
            "organizations": ClientOrganization.objects.all(),
            "testimonials": Testimonial.objects.all(),
            "event_photos": EventPhoto.objects.all(),
            "shop_interests": ShopInterest.objects.filter(active=True)[:50],
            "pending_partner_photos": PartnerGalleryPhoto.objects.select_related("partner").filter(
                status=PartnerGalleryPhoto.Status.PENDING
            ),
            "partner_photos": PartnerGalleryPhoto.objects.select_related("partner").exclude(
                status=PartnerGalleryPhoto.Status.PENDING
            )[:20],
            "pending_reviews": CustomerReview.objects.select_related(
                "customer", "ceremony__quote"
            ).filter(status=CustomerReview.Status.PENDING),
            "reviews": CustomerReview.objects.select_related("customer", "ceremony__quote").exclude(
                status=CustomerReview.Status.PENDING
            )[:20],
        },
    )


@staff_member_required
def moderate_community_content(request, content_type, item_id, decision):
    if request.method != "POST":
        return HttpResponseBadRequest("Moderation decisions must be submitted from the content workspace.")
    model_map = {
        "partner-photo": PartnerGalleryPhoto,
        "review": CustomerReview,
    }
    model = model_map.get(content_type)
    if not model or decision not in {"approve", "reject", "feature"}:
        return HttpResponseBadRequest("Unknown moderation action.")
    item = get_object_or_404(model, pk=item_id)
    if decision == "feature":
        if item.status != model.Status.APPROVED:
            messages.error(request, "Approve this content before featuring it.")
        else:
            item.featured = not item.featured
            item.save(update_fields=["featured"])
            messages.success(request, "Featured placement updated.")
        return redirect("content_library")
    item.status = model.Status.APPROVED if decision == "approve" else model.Status.REJECTED
    item.reviewed_by = request.user
    item.reviewed_at = timezone.now()
    notes_field = "review_notes" if content_type == "partner-photo" else "staff_notes"
    setattr(item, notes_field, request.POST.get("staff_notes", "").strip())
    item.save()
    messages.success(request, f"Content marked {item.get_status_display().lower()}.")
    return redirect("content_library")


@staff_member_required
def toggle_published_content(request, content_type, item_id, field):
    if request.method != "POST":
        return HttpResponseBadRequest("Content changes must be submitted from the content workspace.")
    model_map = {
        "organization": (ClientOrganization, {"active"}),
        "testimonial": (Testimonial, {"active", "featured"}),
        "event-photo": (EventPhoto, {"active", "featured"}),
    }
    definition = model_map.get(content_type)
    if not definition or field not in definition[1]:
        return HttpResponseBadRequest("Unknown content change.")
    item = get_object_or_404(definition[0], pk=item_id)
    setattr(item, field, not getattr(item, field))
    item.save(update_fields=[field])
    messages.success(request, "Homepage content visibility updated.")
    return redirect("content_library")


@staff_member_required
def business_insights(request):
    today = timezone.localdate()
    period = request.GET.get("period", "month")

    if period == "week":
        start_date = today - timezone.timedelta(days=today.weekday())
        end_date = start_date + timezone.timedelta(days=6)
        period_label = "This week"
    elif period == "30days":
        start_date = today - timezone.timedelta(days=29)
        end_date = today
        period_label = "Last 30 days"
    elif period == "custom":
        try:
            start_date = timezone.datetime.fromisoformat(request.GET.get("start", "")).date()
            end_date = timezone.datetime.fromisoformat(request.GET.get("end", "")).date()
            if start_date > end_date:
                raise ValueError
        except (TypeError, ValueError):
            start_date = today.replace(day=1)
            end_date = today
            messages.error(request, "Choose a valid start and end date.")
        period_label = f"{start_date:%b %d, %Y} – {end_date:%b %d, %Y}"
    else:
        period = "month"
        start_date = today.replace(day=1)
        if today.month == 12:
            next_month = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_month = today.replace(month=today.month + 1, day=1)
        end_date = next_month - timezone.timedelta(days=1)
        period_label = today.strftime("%B %Y")

    process_workflow_deadlines(request.user)
    quotes = QuoteRequest.objects.filter(event_date__range=(start_date, end_date))
    ceremony_qs = Ceremony.objects.select_related("quote").filter(
        quote__event_date__range=(start_date, end_date)
    )
    payments = Payment.objects.filter(
        ceremony__quote__event_date__range=(start_date, end_date),
        payment_type__in=[Payment.PaymentType.DEPOSIT, Payment.PaymentType.FINAL],
    )

    requested = quotes.count()
    accepted = ceremony_qs.count()
    completed = ceremony_qs.filter(status=Ceremony.Status.COMPLETED).count()
    failed = ceremony_qs.filter(status=Ceremony.Status.FAILED).count()
    cancelled = ceremony_qs.filter(status=Ceremony.Status.CANCELLED).count()
    no_show = ceremony_qs.filter(status=Ceremony.Status.NO_SHOW).count()
    in_progress = max(accepted - completed - failed - cancelled - no_show, 0)

    quoted_total = quotes.aggregate(total=Sum("quoted_amount"))["total"] or 0
    collected_total = payments.filter(
        status__in=[Payment.Status.PAID, Payment.Status.FORFEITED]
    ).aggregate(total=Sum("received_amount"))["total"] or 0
    refunded_total = payments.filter(status=Payment.Status.REFUNDED).aggregate(
        total=Sum("received_amount")
    )["total"] or 0
    outstanding_total = payments.filter(
        status__in=[Payment.Status.PENDING, Payment.Status.OVERDUE, Payment.Status.FAILED]
    ).aggregate(total=Sum("expected_amount"))["total"] or 0

    request_data = [
        (
            "Open",
            quotes.filter(
                status__in=[QuoteRequest.Status.NEW, QuoteRequest.Status.REVIEWING]
            ).count(),
            "#d49a31",
        ),
        ("Sent", quotes.filter(status=QuoteRequest.Status.QUOTED).count(), "#477a9b"),
        ("Accepted", quotes.filter(status=QuoteRequest.Status.ACCEPTED).count(), "#26734d"),
        ("Declined", quotes.filter(status=QuoteRequest.Status.DECLINED).count(), "#b94b3f"),
        ("Expired", quotes.filter(status=QuoteRequest.Status.EXPIRED).count(), "#7b6d9c"),
    ]
    ceremony_data = [
        ("Completed", completed, "#26734d"),
        ("In progress", in_progress, "#d49a31"),
        ("Failed", failed, "#b94b3f"),
        ("Cancelled", cancelled, "#7b6d9c"),
        ("No-show", no_show, "#4d6475"),
    ]
    income_data = [
        ("Collected", collected_total, "#26734d"),
        ("Outstanding", outstanding_total, "#d49a31"),
        ("Refunded", refunded_total, "#b94b3f"),
    ]

    def build_donut(data, center_value, *, currency=False):
        total = sum(value for _label, value, _color in data)
        running = 0.0
        gradient_parts = []
        items = []
        for label, value, color in data:
            percentage = (float(value) / float(total) * 100) if total else 0
            start = running
            running += percentage
            if percentage:
                gradient_parts.append(f"{color} {start:.2f}% {running:.2f}%")
            items.append(
                {
                    "label": label,
                    "value": value,
                    "display_value": f"${value:,.2f}" if currency else str(value),
                    "percentage": percentage,
                    "color": color,
                }
            )
        gradient = (
            f"conic-gradient({', '.join(gradient_parts)})"
            if gradient_parts
            else "conic-gradient(#e6ded6 0% 100%)"
        )
        return {
            "items": items,
            "gradient": gradient,
            "center_value": center_value,
        }

    request_chart = build_donut(request_data, requested)
    ceremony_chart = build_donut(ceremony_data, accepted)
    income_total = collected_total + outstanding_total + refunded_total
    income_chart = build_donut(
        income_data,
        f"${income_total:,.0f}",
        currency=True,
    )

    metrics = {
        "requested": requested,
        "accepted": accepted,
        "completed": completed,
        "failed": failed,
        "cancelled": cancelled,
        "no_show": no_show,
        "acceptance_rate": (accepted / requested * 100) if requested else 0,
        "completion_rate": (completed / accepted * 100) if accepted else 0,
        "quoted_total": quoted_total,
        "collected_total": collected_total,
        "refunded_total": refunded_total,
        "outstanding_total": outstanding_total,
    }
    return render(
        request,
        "bookings/insights.html",
        {
            "metrics": metrics,
            "request_chart": request_chart,
            "ceremony_chart": ceremony_chart,
            "income_chart": income_chart,
            "period": period,
            "period_label": period_label,
            "start_date": start_date,
            "end_date": end_date,
        },
    )


@staff_member_required
@permission_required("bookings.view_quoterequest", raise_exception=True)
def quote_requests(request):
    process_workflow_deadlines(request.user)
    selected_status = request.GET.get("status", "")
    search = request.GET.get("q", "").strip()
    answered_offer_statuses = [
        AvailabilityOffer.Status.ACCEPTED,
        AvailabilityOffer.Status.DECLINED,
    ]
    queryset = QuoteRequest.objects.select_related("ceremony").prefetch_related(
        "availability_offers"
    )
    new_quote_count = QuoteRequest.objects.filter(status=QuoteRequest.Status.NEW).count()
    answered_availability_quote_ids = set(
        QuoteRequest.objects.filter(
            status__in=[QuoteRequest.Status.REVIEWING, QuoteRequest.Status.WAITLISTED],
            availability_offers__status__in=answered_offer_statuses,
        ).values_list("pk", flat=True)
    )
    valid_statuses = {value for value, _label in QuoteRequest.Status.choices}
    if selected_status in valid_statuses:
        queryset = queryset.filter(status=selected_status)
    else:
        selected_status = ""
    if search:
        queryset = queryset.filter(
            Q(customer_name__icontains=search)
            | Q(email__icontains=search)
            | Q(location__icontains=search)
        )
    return render(
        request,
        "bookings/quote_requests.html",
        {
            "quote_requests": queryset,
            "status_choices": QuoteRequest.Status.choices,
            "selected_status": selected_status,
            "search": search,
            "new_quote_count": new_quote_count,
            "answered_availability_count": len(answered_availability_quote_ids),
            "answered_availability_quote_ids": answered_availability_quote_ids,
        },
    )


@staff_member_required
@permission_required("bookings.change_quoterequest", raise_exception=True)
def manage_quote(request, public_id):
    quote_request = get_object_or_404(QuoteRequest, public_id=public_id)
    quote_locked = quote_request.status == QuoteRequest.Status.ACCEPTED
    if request.method == "POST":
        if quote_locked:
            messages.error(request, "This accepted quote is frozen and can no longer be edited or resent.")
            return redirect("manage_quote", public_id=quote_request.public_id)
        form = QuoteManagementForm(request.POST, instance=quote_request)
        if form.is_valid():
            quote_request = form.save()
            action = request.POST.get("action", "save")
            if action == "waitlist":
                quote_request.status = QuoteRequest.Status.WAITLISTED
                quote_request.save(update_fields=["status", "updated_at"])
                messages.success(request, "Request moved to the waiting-for-partner queue.")
                return redirect("manage_quote", public_id=quote_request.public_id)
            if action == "send":
                if not quote_request.contact_complete:
                    messages.error(request, "Add the customer's name, email, and phone number before sending.")
                elif quote_request.quoted_amount is None:
                    messages.error(request, "Enter a quote amount before sending.")
                elif not quote_request.event_time:
                    messages.error(request, "Set the event start time before reserving capacity and sending the quote.")
                elif not quote_request.active_capacity_hold:
                    messages.error(request, "A partner must accept this time before the quote can be sent.")
                else:
                    if not quote_request.quote_expires_at:
                        quote_request.quote_expires_at = timezone.now() + timezone.timedelta(days=7)
                        quote_request.save(update_fields=["quote_expires_at", "updated_at"])
                    hold = quote_request.active_capacity_hold
                    hold.expires_at = quote_request.quote_expires_at
                    hold.save(update_fields=["expires_at", "updated_at"])
                    try:
                        send_quote_email(
                            quote_request,
                            build_absolute_quote_url(request, quote_request),
                        )
                    except Exception:
                        messages.error(request, "The quote was saved, but the email could not be sent.")
                    else:
                        quote_request.mark_quote_sent()
                        messages.success(request, "Quote saved and sent to the customer.")
                        return redirect("operations_dashboard")
            else:
                if quote_request.status == QuoteRequest.Status.NEW:
                    quote_request.status = QuoteRequest.Status.REVIEWING
                    quote_request.save(update_fields=["status", "updated_at"])
                messages.success(request, "Quote draft saved in the database.")
                return redirect("manage_quote", public_id=quote_request.public_id)
    else:
        initial = {}
        if not quote_request.quote_expires_at:
            initial["quote_expires_at"] = timezone.now() + timezone.timedelta(days=7)
        form = QuoteManagementForm(instance=quote_request, initial=initial)
    if quote_locked:
        for field in form.fields.values():
            field.disabled = True
    ceremony = getattr(quote_request, "ceremony", None)
    return render(
        request,
        "bookings/manage_quote.html",
        {
            "form": form,
            "quote_request": quote_request,
            "quote_locked": quote_locked,
            "ceremony": ceremony,
            "eligible_partners": eligible_partners_for_quote(quote_request) if quote_request.event_time else [],
            "availability_offers": quote_request.availability_offers.select_related("partner"),
            "capacity_hold": quote_request.active_capacity_hold,
        },
    )


@staff_member_required
@permission_required("bookings.change_quoterequest", raise_exception=True)
def create_availability_offer(request, public_id):
    quote = get_object_or_404(QuoteRequest, public_id=public_id)
    if request.method != "POST":
        return HttpResponseBadRequest("Availability requests must be sent from the quote workspace.")
    if quote.status == QuoteRequest.Status.ACCEPTED:
        messages.error(request, "This accepted quote is frozen.")
        return redirect("manage_quote", public_id=quote.public_id)
    if not quote.event_time:
        messages.error(request, "Save the event start time before contacting a partner.")
        return redirect("manage_quote", public_id=quote.public_id)
    partner = get_object_or_404(Partner, pk=request.POST.get("partner_id"))
    if partner not in eligible_partners_for_quote(quote):
        messages.error(request, "That partner is no longer eligible or has a scheduling conflict.")
        return redirect("manage_quote", public_id=quote.public_id)
    if quote.active_capacity_hold:
        messages.error(request, "This request already has reserved partner capacity.")
        return redirect("manage_quote", public_id=quote.public_id)
    quote.availability_offers.filter(
        partner=partner, status=AvailabilityOffer.Status.PENDING
    ).update(status=AvailabilityOffer.Status.CANCELLED, responded_at=timezone.now())
    offer = AvailabilityOffer.objects.create(
        quote=quote,
        partner=partner,
        expires_at=timezone.now() + timezone.timedelta(
            hours=getattr(settings, "AVAILABILITY_OFFER_HOURS", 24)
        ),
        created_by=request.user,
    )
    quote.status = QuoteRequest.Status.REVIEWING
    quote.save(update_fields=["status", "updated_at"])
    if request.POST.get("confirm_on_behalf"):
        try:
            accept_availability_offer(offer, confirmed_by_staff=True)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{partner.name}'s availability was confirmed and the time is held.")
    else:
        if not partner.user_id:
            offer.status = AvailabilityOffer.Status.CANCELLED
            offer.responded_at = timezone.now()
            offer.save(update_fields=["status", "responded_at"])
            messages.error(request, "This legacy partner has no login. Confirm directly, then use the staff-confirmed option.")
        else:
            try:
                send_availability_offer_email(
                    offer, request.build_absolute_uri(reverse("partner_dashboard"))
                )
            except Exception:
                messages.warning(request, "The request is in the partner workspace, but its email notification could not be sent.")
            else:
                messages.success(request, f"Availability request sent to {partner.name}.")
    return redirect("manage_quote", public_id=quote.public_id)


@staff_member_required
@permission_required("bookings.view_ceremony", raise_exception=True)
def ceremonies(request):
    process_workflow_deadlines(request.user)
    selected_status = request.GET.get("status", "")
    search = request.GET.get("q", "").strip()
    queryset = Ceremony.objects.select_related("quote", "assigned_partner")
    valid_statuses = {value for value, _label in Ceremony.Status.choices}
    if selected_status in valid_statuses:
        queryset = queryset.filter(status=selected_status)
    else:
        selected_status = ""
    if search:
        queryset = queryset.filter(
            Q(quote__customer_name__icontains=search)
            | Q(quote__email__icontains=search)
            | Q(quote__location__icontains=search)
            | Q(assigned_partner__name__icontains=search)
        )
    return render(
        request,
        "bookings/ceremonies.html",
        {
            "ceremonies": queryset,
            "status_choices": Ceremony.Status.choices,
            "selected_status": selected_status,
            "search": search,
        },
    )


@staff_member_required
@permission_required("bookings.view_ceremony", raise_exception=True)
def ceremony_detail(request, public_id):
    ceremony = get_object_or_404(
        Ceremony.objects.select_related("quote", "assigned_partner"),
        public_id=public_id,
    )
    ceremony.refresh_deadlines(changed_by=request.user)
    deposit = ceremony.deposit_payment
    final_payment = ceremony.final_payment
    partner_task = PartnerTask.objects.filter(ceremony=ceremony).select_related("partner").first()
    partner_payout = PartnerPayout.objects.filter(task=partner_task).first() if partner_task else None
    context = {
        "ceremony": ceremony,
        "quote_request": ceremony.quote,
        "deposit": deposit,
        "final_payment": final_payment,
        "deposit_form": PaymentRecordForm(payment=deposit) if deposit else None,
        "final_payment_form": PaymentRecordForm(payment=final_payment) if final_payment else None,
        "full_payment_form": FullPaymentForm(ceremony=ceremony),
        "assignment_form": PartnerAssignmentForm(ceremony=ceremony),
        "outcome_form": CeremonyOutcomeForm(),
        "partner_task": partner_task,
        "partner_payout": partner_payout,
        "partner_payout_form": PartnerPayoutForm(instance=partner_payout) if partner_task else None,
        "customer_cancellation_requests": ceremony.customer_cancellation_requests.select_related("customer", "reviewed_by"),
    }
    return render(request, "bookings/ceremony_detail.html", context)


@staff_member_required
@permission_required("bookings.change_payment", raise_exception=True)
def record_payment(request, public_id, payment_type):
    ceremony = get_object_or_404(Ceremony, public_id=public_id)
    if request.method != "POST":
        return HttpResponseBadRequest("Payments must be recorded from the ceremony workspace.")
    if ceremony.terminal:
        messages.error(request, "This ceremony is frozen and cannot receive payment changes.")
        return redirect("ceremony_detail", public_id=ceremony.public_id)
    payment = get_object_or_404(
        Payment,
        ceremony=ceremony,
        payment_type=payment_type,
    )
    form = PaymentRecordForm(request.POST, payment=payment)
    if not form.is_valid():
        messages.error(request, "Check the payment amount and try again.")
        return redirect("ceremony_detail", public_id=ceremony.public_id)

    payment.received_amount = form.cleaned_data["received_amount"]
    payment.provider_reference = form.cleaned_data["provider_reference"]
    payment.notes = form.cleaned_data["notes"]
    payment.status = Payment.Status.PAID
    payment.paid_at = timezone.now()
    payment.save()

    if payment.payment_type == Payment.PaymentType.DEPOSIT:
        if not ceremony.assigned_partner and ceremony.quote.capacity_holds.filter(status=CapacityHold.Status.CONFIRMED).exists():
            convert_capacity_hold(ceremony, changed_by=request.user)
            ceremony.refresh_from_db()
        if ceremony.status in [Ceremony.Status.AWAITING_DEPOSIT, Ceremony.Status.AT_RISK]:
            ceremony.transition_to(
                Ceremony.Status.AWAITING_PARTNER,
                changed_by=request.user,
                note=f"Deposit of ${payment.received_amount} recorded.",
            )
    elif payment.payment_type == Payment.PaymentType.FINAL:
        if ceremony.assigned_partner and ceremony.status in [
            Ceremony.Status.ASSIGNED,
            Ceremony.Status.AT_RISK,
        ]:
            ceremony.outcome_reason = ""
            ceremony.save(update_fields=["outcome_reason", "updated_at"])
            ceremony.transition_to(
                Ceremony.Status.READY,
                changed_by=request.user,
                note=f"Final payment of ${payment.received_amount} recorded.",
            )
    messages.success(request, f"{payment.get_payment_type_display()} recorded as paid.")
    try:
        send_payment_confirmation_email(
            ceremony,
            payment.get_payment_type_display(),
            payment.received_amount,
        )
    except Exception:
        pass
    notify_payment_received(
        ceremony,
        payment.get_payment_type_display(),
        payment.received_amount,
    )
    return redirect("ceremony_detail", public_id=ceremony.public_id)


@staff_member_required
@permission_required("bookings.change_payment", raise_exception=True)
def record_full_payment(request, public_id):
    ceremony = get_object_or_404(Ceremony, public_id=public_id)
    if request.method != "POST":
        return HttpResponseBadRequest("Full payments must be recorded from the ceremony workspace.")
    if ceremony.terminal:
        messages.error(request, "This ceremony is frozen and cannot receive payment changes.")
        return redirect("ceremony_detail", public_id=ceremony.public_id)

    form = FullPaymentForm(request.POST, ceremony=ceremony)
    if not form.is_valid():
        messages.error(request, "The received amount does not cover the full outstanding balance.")
        return redirect("ceremony_detail", public_id=ceremony.public_id)

    now = timezone.now()
    reference = form.cleaned_data["provider_reference"]
    notes = form.cleaned_data["notes"]
    settled = []
    for payment in ceremony.payments.filter(
        payment_type__in=[Payment.PaymentType.DEPOSIT, Payment.PaymentType.FINAL]
    ).exclude(status__in=[Payment.Status.PAID, Payment.Status.WAIVED]):
        payment.received_amount = payment.expected_amount
        payment.provider_reference = reference
        payment.notes = notes
        payment.status = Payment.Status.PAID
        payment.paid_at = now
        payment.save()
        settled.append(payment.get_payment_type_display())

    if not ceremony.assigned_partner and ceremony.quote.capacity_holds.filter(status=CapacityHold.Status.CONFIRMED).exists():
        convert_capacity_hold(ceremony, changed_by=request.user)
        ceremony.refresh_from_db()

    previous_status = ceremony.status
    ceremony.outcome_reason = ""
    ceremony.status = (
        Ceremony.Status.READY
        if ceremony.assigned_partner
        else Ceremony.Status.AWAITING_PARTNER
    )
    ceremony.save(update_fields=["status", "outcome_reason", "updated_at"])
    ceremony.history.create(
        from_status=previous_status,
        to_status=ceremony.status,
        note=(
            f"Paid in full (${form.cleaned_data['received_amount']}); "
            f"settled {', '.join(settled) or 'all obligations'}. Reference: {reference or 'manual'}."
        ),
        changed_by=request.user,
    )
    try:
        send_payment_confirmation_email(
            ceremony,
            "Paid in full",
            form.cleaned_data["received_amount"],
        )
    except Exception:
        pass
    notify_payment_received(
        ceremony,
        "Paid in full",
        form.cleaned_data["received_amount"],
    )
    messages.success(request, "Full payment recorded. Deposit and final balance are settled.")
    return redirect("ceremony_detail", public_id=ceremony.public_id)


@staff_member_required
@permission_required("bookings.change_payment", raise_exception=True)
def update_payment_status(request, public_id, payment_type, new_status):
    ceremony = get_object_or_404(Ceremony, public_id=public_id)
    if request.method != "POST":
        return HttpResponseBadRequest("Payment outcomes must be recorded from the ceremony workspace.")
    if ceremony.status == Ceremony.Status.COMPLETED:
        messages.error(request, "A completed ceremony is fully frozen.")
        return redirect("ceremony_detail", public_id=ceremony.public_id)

    payment = get_object_or_404(Payment, ceremony=ceremony, payment_type=payment_type)
    allowed = {
        Payment.Status.FAILED,
        Payment.Status.WAIVED,
        Payment.Status.REFUNDED,
        Payment.Status.FORFEITED,
    }
    if new_status not in allowed:
        return HttpResponseBadRequest("Unknown payment outcome.")
    if new_status in {Payment.Status.REFUNDED, Payment.Status.FORFEITED}:
        if payment.status != Payment.Status.PAID:
            messages.error(request, "Only a paid amount can be refunded or forfeited.")
            return redirect("ceremony_detail", public_id=ceremony.public_id)
        if ceremony.status not in {
            Ceremony.Status.CANCELLED,
            Ceremony.Status.FAILED,
            Ceremony.Status.NO_SHOW,
        }:
            messages.error(request, "Refund or forfeit disposition is recorded after a cancelled or failed outcome.")
            return redirect("ceremony_detail", public_id=ceremony.public_id)
    elif payment.status not in {Payment.Status.PENDING, Payment.Status.OVERDUE, Payment.Status.FAILED}:
        messages.error(request, "This payment outcome is not available.")
        return redirect("ceremony_detail", public_id=ceremony.public_id)

    previous_payment_status = payment.status
    payment.status = new_status
    payment.notes = request.POST.get("notes", "").strip() or payment.notes
    payment.save(update_fields=["status", "notes", "updated_at"])

    if new_status == Payment.Status.WAIVED:
        if payment.payment_type == Payment.PaymentType.DEPOSIT and ceremony.status == Ceremony.Status.AWAITING_DEPOSIT:
            ceremony.transition_to(
                Ceremony.Status.AWAITING_PARTNER,
                changed_by=request.user,
                note="Deposit requirement waived.",
            )
        elif payment.payment_type == Payment.PaymentType.FINAL and ceremony.assigned_partner:
            if ceremony.status in [Ceremony.Status.ASSIGNED, Ceremony.Status.AT_RISK]:
                ceremony.outcome_reason = ""
                ceremony.save(update_fields=["outcome_reason", "updated_at"])
                ceremony.transition_to(
                    Ceremony.Status.READY,
                    changed_by=request.user,
                    note="Final payment requirement waived.",
                )
    elif new_status == Payment.Status.FAILED and payment.payment_type == Payment.PaymentType.FINAL:
        if not ceremony.terminal and ceremony.status != Ceremony.Status.AT_RISK:
            ceremony.transition_to(
                Ceremony.Status.AT_RISK,
                changed_by=request.user,
                note="Final payment attempt failed.",
                outcome_reason=Ceremony.OutcomeReason.FINAL_PAYMENT_NOT_PAID,
            )

    ceremony.history.create(
        from_status=ceremony.status,
        to_status=ceremony.status,
        note=(
            f"{payment.get_payment_type_display()} changed from "
            f"{previous_payment_status} to {payment.get_status_display()}."
        ),
        changed_by=request.user,
    )
    messages.success(request, f"{payment.get_payment_type_display()} marked {payment.get_status_display()}.")
    return redirect("ceremony_detail", public_id=ceremony.public_id)


@staff_member_required
@permission_required("bookings.change_ceremony", raise_exception=True)
def assign_partner(request, public_id):
    ceremony = get_object_or_404(Ceremony, public_id=public_id)
    if request.method != "POST":
        return HttpResponseBadRequest("Partner assignments must be submitted from the ceremony workspace.")
    if ceremony.terminal or ceremony.status == Ceremony.Status.AWAITING_DEPOSIT:
        messages.error(request, "The deposit must be paid before assigning a partner.")
        return redirect("ceremony_detail", public_id=ceremony.public_id)
    form = PartnerAssignmentForm(request.POST, ceremony=ceremony)
    if not form.is_valid():
        messages.error(request, "That partner is not available for this date.")
        return redirect("ceremony_detail", public_id=ceremony.public_id)

    previous_status = ceremony.status
    ceremony.assigned_partner = form.cleaned_data["partner"]
    final = ceremony.final_payment
    ceremony.status = (
        Ceremony.Status.READY
        if final and final.status in [Payment.Status.PAID, Payment.Status.WAIVED]
        else Ceremony.Status.ASSIGNED
    )
    ceremony.save(update_fields=["assigned_partner", "status", "updated_at"])
    task, created = PartnerTask.objects.get_or_create(
        ceremony=ceremony,
        defaults={"partner": ceremony.assigned_partner},
    )
    if not created and task.partner_id != ceremony.assigned_partner_id:
        task.partner = ceremony.assigned_partner
        task.status = PartnerTask.Status.ASSIGNED
        task.partner_notes = ""
        task.accepted_at = None
        task.started_at = None
        task.delivered_at = None
        task.save()
    payout, _ = PartnerPayout.objects.get_or_create(
        task=task,
        defaults={"partner": ceremony.assigned_partner},
    )
    if payout.partner_id != ceremony.assigned_partner_id:
        payout.partner = ceremony.assigned_partner
        payout.status = PartnerPayout.Status.NOT_READY
        payout.reference = ""
        payout.paid_at = None
        payout.save()
    ceremony.history.create(
        from_status=previous_status,
        to_status=ceremony.status,
        note=f"Assigned to {ceremony.assigned_partner.name}.",
        changed_by=request.user,
    )
    try:
        send_assignment_confirmation_email(ceremony)
    except Exception:
        pass
    notify_assignment(ceremony)
    messages.success(request, f"Task assigned to {ceremony.assigned_partner.name}.")
    return redirect("ceremony_detail", public_id=ceremony.public_id)


@staff_member_required
@permission_required("bookings.change_ceremony", raise_exception=True)
def complete_job(request, public_id):
    ceremony = get_object_or_404(Ceremony, public_id=public_id)
    if request.method != "POST":
        return HttpResponseBadRequest("Jobs must be completed from the ceremony workspace.")
    final = ceremony.final_payment
    if ceremony.status not in [Ceremony.Status.READY, Ceremony.Status.ASSIGNED]:
        messages.error(request, "Only a ready, assigned ceremony can be completed.")
    elif final and final.status not in [Payment.Status.PAID, Payment.Status.WAIVED]:
        messages.error(request, "Record or waive the final payment before completing this ceremony.")
    else:
        ceremony.transition_to(
            Ceremony.Status.COMPLETED,
            changed_by=request.user,
            note="Ceremony delivered successfully.",
        )
        task = PartnerTask.objects.filter(ceremony=ceremony).first()
        if task:
            task.status = PartnerTask.Status.DELIVERED
            task.delivered_at = task.delivered_at or timezone.now()
            task.save(update_fields=["status", "delivered_at", "updated_at"])
            payout, _ = PartnerPayout.objects.get_or_create(
                task=task, defaults={"partner": task.partner}
            )
            if payout.status == PartnerPayout.Status.NOT_READY:
                payout.status = PartnerPayout.Status.PENDING
                payout.save(update_fields=["status", "updated_at"])
        messages.success(request, "Ceremony marked completed. The record is now frozen.")
        return redirect("ceremonies")
    return redirect("ceremony_detail", public_id=ceremony.public_id)


@staff_member_required
@permission_required("bookings.change_ceremony", raise_exception=True)
def record_outcome(request, public_id, outcome):
    ceremony = get_object_or_404(Ceremony, public_id=public_id)
    if request.method != "POST":
        return HttpResponseBadRequest("Outcomes must be recorded from the ceremony workspace.")
    status_map = {
        "cancel": Ceremony.Status.CANCELLED,
        "fail": Ceremony.Status.FAILED,
        "no-show": Ceremony.Status.NO_SHOW,
    }
    if outcome not in status_map or ceremony.terminal:
        messages.error(request, "This ceremony outcome cannot be changed.")
        return redirect("ceremony_detail", public_id=ceremony.public_id)
    form = CeremonyOutcomeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a reason and add outcome notes.")
        return redirect("ceremony_detail", public_id=ceremony.public_id)
    ceremony.outcome_notes = form.cleaned_data["outcome_notes"]
    ceremony.save(update_fields=["outcome_notes", "updated_at"])
    ceremony.transition_to(
        status_map[outcome],
        changed_by=request.user,
        note=form.cleaned_data["outcome_notes"],
        outcome_reason=form.cleaned_data["outcome_reason"],
    )
    release_quote_holds(ceremony.quote, f"Ceremony recorded as {ceremony.get_status_display()}.")
    task = PartnerTask.objects.filter(ceremony=ceremony).first()
    if task:
        task.status = PartnerTask.Status.CANCELLED
        task.save(update_fields=["status", "updated_at"])
        payout, _ = PartnerPayout.objects.get_or_create(
            task=task, defaults={"partner": task.partner}
        )
        if payout.status not in {PartnerPayout.Status.PAID, PartnerPayout.Status.CANCELLED}:
            payout.status = PartnerPayout.Status.HELD
            payout.save(update_fields=["status", "updated_at"])
    messages.success(request, f"Ceremony recorded as {ceremony.get_status_display()} and frozen.")
    return redirect("ceremonies")


@staff_member_required
@permission_required("bookings.change_ceremony", raise_exception=True)
def review_customer_cancellation(request, request_id, decision):
    if request.method != "POST":
        return HttpResponseBadRequest("Cancellation decisions must be submitted from the ceremony workspace.")
    cancellation = get_object_or_404(
        CustomerCancellationRequest.objects.select_related("ceremony", "ceremony__quote"),
        pk=request_id,
        status=CustomerCancellationRequest.Status.PENDING,
    )
    ceremony = cancellation.ceremony
    cancellation.staff_notes = request.POST.get("staff_notes", "").strip()
    cancellation.reviewed_by = request.user
    cancellation.reviewed_at = timezone.now()
    if decision == "approve":
        if ceremony.terminal:
            messages.error(request, "The ceremony is already closed.")
            return redirect("ceremony_detail", public_id=ceremony.public_id)
        cancellation.status = CustomerCancellationRequest.Status.APPROVED
        cancellation.save()
        ceremony.outcome_notes = cancellation.reason
        ceremony.save(update_fields=["outcome_notes", "updated_at"])
        ceremony.transition_to(
            Ceremony.Status.CANCELLED,
            changed_by=request.user,
            note=f"Customer cancellation approved: {cancellation.reason}",
            outcome_reason=Ceremony.OutcomeReason.CUSTOMER_CANCELLED,
        )
        release_quote_holds(ceremony.quote, "Customer cancellation approved.")
        task = PartnerTask.objects.filter(ceremony=ceremony).first()
        if task:
            task.status = PartnerTask.Status.CANCELLED
            task.save(update_fields=["status", "updated_at"])
            payout, _ = PartnerPayout.objects.get_or_create(task=task, defaults={"partner": task.partner})
            if payout.status != PartnerPayout.Status.PAID:
                payout.status = PartnerPayout.Status.HELD
                payout.save(update_fields=["status", "updated_at"])
        messages.success(request, "Customer cancellation approved; the ceremony is frozen.")
    elif decision == "decline":
        cancellation.status = CustomerCancellationRequest.Status.DECLINED
        cancellation.save()
        messages.info(request, "Customer cancellation request declined.")
    else:
        return HttpResponseBadRequest("Unknown cancellation decision.")
    return redirect("ceremony_detail", public_id=ceremony.public_id)


@staff_member_required
@permission_required("bookings.view_partner", raise_exception=True)
def partners(request):
    search = request.GET.get("q", "").strip()
    queryset = Partner.objects.all()
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(contact_name__icontains=search)
            | Q(service_area__icontains=search)
        )
    return render(request, "bookings/partners.html", {"partners": queryset, "search": search})


@staff_member_required
@permission_required("bookings.change_partner", raise_exception=True)
def manage_partner(request, partner_id=None):
    partner = get_object_or_404(Partner, pk=partner_id) if partner_id else None
    if request.method == "POST":
        form = PartnerManagementForm(request.POST, instance=partner)
        if form.is_valid():
            partner = form.save()
            messages.success(request, f"{partner.name} was saved.")
            return redirect("partners")
    else:
        form = PartnerManagementForm(instance=partner)
    return render(
        request,
        "bookings/manage_partner.html",
        {"form": form, "partner": partner, "documents": partner.documents.all() if partner else []},
    )


@staff_member_required
@permission_required("bookings.change_partner", raise_exception=True)
def review_partner_document(request, document_id, decision):
    if request.method != "POST":
        return HttpResponseBadRequest("Document reviews must be submitted from the partner workspace.")
    document = get_object_or_404(PartnerDocument.objects.select_related("partner"), pk=document_id)
    status_map = {
        "approve": PartnerDocument.ReviewStatus.APPROVED,
        "reject": PartnerDocument.ReviewStatus.REJECTED,
    }
    if decision not in status_map:
        return HttpResponseBadRequest("Unknown document review decision.")
    expiry_value = request.POST.get("expiry_date", "").strip()
    expiry_date = parse_date(expiry_value) if expiry_value else None
    if expiry_value and expiry_date is None:
        messages.error(request, "Enter a valid document expiry date.")
        return redirect("manage_partner", partner_id=document.partner_id)
    required_expiry_types = {
        PartnerDocument.DocumentType.FOOD_PERMIT,
        PartnerDocument.DocumentType.INSURANCE,
        PartnerDocument.DocumentType.TRAINING,
    }
    if decision == "approve" and document.document_type in required_expiry_types:
        if not expiry_date:
            messages.error(request, "Enter a future expiry date before approving this required document.")
            return redirect("manage_partner", partner_id=document.partner_id)
        if expiry_date <= timezone.localdate():
            messages.error(request, "The expiry date must be after today before this document can be approved.")
            return redirect("manage_partner", partner_id=document.partner_id)
    document.review_status = status_map[decision]
    document.review_notes = request.POST.get("review_notes", "").strip()
    document.expiry_date = expiry_date
    document.expiry_processed_at = None
    document.save(
        update_fields=[
            "review_status",
            "review_notes",
            "expiry_date",
            "expiry_processed_at",
        ]
    )
    verified = decision == "approve"
    field_map = {
        PartnerDocument.DocumentType.FOOD_PERMIT: "food_permit_verified",
        PartnerDocument.DocumentType.INSURANCE: "insurance_verified",
        PartnerDocument.DocumentType.TRAINING: "cultural_training_verified",
    }
    verification_field = field_map.get(document.document_type)
    if verification_field:
        setattr(document.partner, verification_field, verified)
        document.partner.save(update_fields=[verification_field])
    messages.success(request, f"{document.get_document_type_display()} marked {document.get_review_status_display()}.")
    return redirect("manage_partner", partner_id=document.partner_id)


@staff_member_required
@permission_required("bookings.change_payment", raise_exception=True)
def manage_partner_payout(request, public_id):
    ceremony = get_object_or_404(Ceremony, public_id=public_id)
    if request.method != "POST":
        return HttpResponseBadRequest("Payout changes must be submitted from the ceremony workspace.")
    task = get_object_or_404(PartnerTask, ceremony=ceremony)
    payout, _ = PartnerPayout.objects.get_or_create(task=task, defaults={"partner": task.partner})
    form = PartnerPayoutForm(request.POST, instance=payout)
    if form.is_valid():
        payout = form.save(commit=False)
        payout.partner = task.partner
        if payout.status == PartnerPayout.Status.PAID and not payout.paid_at:
            payout.paid_at = timezone.now()
        elif payout.status != PartnerPayout.Status.PAID:
            payout.paid_at = None
        payout.save()
        messages.success(request, "Partner payout record updated.")
    else:
        messages.error(request, "Check the partner payout details and try again.")
    return redirect("ceremony_detail", public_id=ceremony.public_id)
