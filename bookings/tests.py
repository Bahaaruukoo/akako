from datetime import date, time
from decimal import Decimal
import tempfile
from unittest.mock import patch

from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
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
    PartnerDocument,
    PartnerGalleryPhoto,
    PartnerPayout,
    PartnerTask,
    Payment,
    PaymentCheckout,
    PolicyAcceptance,
    PolicyDocument,
    QuoteRequest,
    ShopInterest,
    Testimonial,
)
from .services import (
    accept_availability_offer,
    partner_conflict_reason,
    process_workflow_deadlines,
    send_quote_email,
)


class BookingFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff_user = get_user_model().objects.create_user(
            email="operations@example.com",
            password="test-password",
            is_staff=True,
            is_superuser=True,
        )
        EmailAddress.objects.create(
            user=cls.staff_user,
            email=cls.staff_user.email,
            verified=True,
            primary=True,
        )

    def setUp(self):
        self.upload_directory = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.upload_directory.name)
        self.media_override.enable()
        self.client.force_login(self.staff_user)

    def tearDown(self):
        self.media_override.disable()
        self.upload_directory.cleanup()

    def create_ceremony(self, *, event_date=date(2026, 8, 12), deposit=Decimal("0.00")):
        quote = QuoteRequest.objects.create(
            customer_name="Aster Bekele",
            email="aster@example.com",
            phone="555-0101",
            event_type=QuoteRequest.EventType.HOME,
            event_date=event_date,
            event_time=time(15, 30),
            location="Silver Spring, MD",
            guest_count=24,
            quoted_amount=Decimal("450.00"),
            deposit_amount=deposit,
            quote_expires_at=timezone.now() + timezone.timedelta(days=2),
            status=QuoteRequest.Status.QUOTED,
        )
        return quote.accept_quote()

    def create_capacity_hold(self, quote):
        suffix = Partner.objects.count() + 1
        partner = Partner.objects.create(
            name=f"Capacity Partner {suffix}",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Almaz",
            email=f"capacity{suffix}@example.com",
            phone="555-0199",
            service_area=quote.location,
            food_permit_verified=True,
            insurance_verified=True,
            cultural_training_verified=True,
        )
        offer = AvailabilityOffer.objects.create(
            quote=quote,
            partner=partner,
            expires_at=timezone.now() + timezone.timedelta(days=1),
            created_by=self.staff_user,
        )
        return accept_availability_offer(offer, confirmed_by_staff=True)

    def test_homepage_loads(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Where Coffee Becomes")
        self.assertContains(response, "Plan Your Ceremony")
        self.assertContains(response, reverse("operations_dashboard"))
        self.assertContains(response, reverse("account_home"))
        self.assertContains(response, "Contact &amp; Help Details")
        self.assertContains(response, self.staff_user.email)
        self.assertContains(response, ">Account</a>")
        self.assertContains(response, ">Profile</a>")
        self.assertContains(response, ">Logout</button>")
        self.assertContains(response, reverse("shop"))
        self.assertNotContains(response, '<a href="#faq">FAQ</a>', html=True)
        self.assertContains(response, f'{reverse("home")}#faq')

    def test_shop_signup_collects_and_updates_launch_interest(self):
        response = self.client.get(reverse("shop"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Freshly Roasted")
        self.assertContains(response, "Ethiopian Single-Origin Coffee")
        self.assertContains(response, "Notify Me")

        response = self.client.post(
            reverse("shop"),
            {
                "email": "coffee-lover@example.com",
                "postal_code": "20910",
                "purchase_frequency": ShopInterest.PurchaseFrequency.WEEKLY,
            },
        )
        self.assertRedirects(response, reverse("shop"))
        interest = ShopInterest.objects.get(email="coffee-lover@example.com")
        self.assertEqual(interest.postal_code, "20910")
        self.assertEqual(interest.purchase_frequency, ShopInterest.PurchaseFrequency.WEEKLY)
        self.assertTrue(interest.marketing_consent)

        self.client.post(
            reverse("shop"),
            {"email": "COFFEE-LOVER@example.com", "purchase_frequency": "monthly"},
        )
        self.assertEqual(ShopInterest.objects.count(), 1)
        interest.refresh_from_db()
        self.assertEqual(interest.purchase_frequency, ShopInterest.PurchaseFrequency.MONTHLY)

    def test_shop_signup_rejects_invalid_zip_and_bot_field(self):
        response = self.client.post(
            reverse("shop"),
            {"email": "shop@example.com", "postal_code": "invalid"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a 5-digit ZIP code")
        self.assertFalse(ShopInterest.objects.exists())

        response = self.client.post(
            reverse("shop"),
            {"email": "bot@example.com", "website": "https://spam.example"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ShopInterest.objects.exists())

    def test_my_account_shows_staff_account_options(self):
        response = self.client.get(reverse("account_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.staff_user.email)
        self.assertContains(response, reverse("operations_dashboard"))
        self.assertContains(response, reverse("account_email"))

    def test_profile_menu_route_opens_staff_profile(self):
        response = self.client.get(reverse("profile_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Personal details")
        self.assertContains(response, self.staff_user.email)

    def test_profile_menu_route_dispatches_customer_and_partner_profiles(self):
        customer_user = get_user_model().objects.create_user(
            email="profile-customer@example.com", password="test-password"
        )
        CustomerProfile.objects.create(
            user=customer_user, first_name="Customer", last_name="Profile", phone="555-0100"
        )
        self.client.force_login(customer_user)
        self.assertRedirects(self.client.get(reverse("profile_home")), reverse("customer_profile"))

        partner_user = get_user_model().objects.create_user(
            email="profile-partner@example.com", password="test-password"
        )
        Partner.objects.create(
            user=partner_user, name="Profile Partner", contact_name="Partner",
            email=partner_user.email, phone="555-0101", service_area="Maryland",
        )
        self.client.force_login(partner_user)
        self.assertRedirects(self.client.get(reverse("profile_home")), reverse("partner_profile"))

    def test_allauth_account_pages_use_akako_house_layout(self):
        for url_name, expected in [
            ("account_email", "Email addresses"),
            ("account_change_password", "Change password"),
            ("socialaccount_connections", "Account connections"),
            ("account_logout", "Sign out?"),
        ]:
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Akako House")
            self.assertContains(response, expected)
            self.assertNotContains(response, "Menu:")

        self.client.logout()
        response = self.client.get(reverse("account_reset_password"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reset your password")
        self.assertContains(response, "portal-auth-card")

    def test_contact_page_and_global_footer_load(self):
        response = self.client.get(reverse("contact"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Talk directly with our team")
        self.assertContains(response, "Ceremony within 24 hours")
        self.assertContains(response, reverse("contact"))
        self.assertContains(response, reverse("about"))
        self.assertContains(response, "+1 (571) 715-8524")
        self.assertContains(response, "tel:+15717158524")
        self.assertContains(response, "support@akakohouse.com")

    def test_about_page_preserves_ancestral_way_story(self):
        response = self.client.get(reverse("about"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Where Every Cup Tells the Story of a Beginning")
        self.assertContains(response, "A Land of Origins")
        self.assertContains(response, "More Than Coffee")
        self.assertContains(response, "Bringing Ethiopia to Your Gathering")
        self.assertContains(response, "Welcome to Akako House")
        self.assertContains(response, "The Meaning of Akako")
        self.assertContains(response, "Where coffee returns to its roots")
        self.assertContains(response, "<strong>Jebena</strong>", html=True)
        self.assertContains(response, "Coffee is never simply served")
        self.assertContains(response, "images/akako-coffee-cup.")
        self.assertContains(response, ".webp")
        self.assertContains(response, "images/akako-coffee-pouring.")

    def test_notification_center_is_private_and_marks_notifications_read(self):
        notice = Notification.objects.create(
            recipient=self.staff_user,
            recipient_email=self.staff_user.email,
            kind=Notification.Kind.QUOTE_NEW,
            title="A private operations alert",
            message="A new request needs review.",
            event_key="test:staff:private-notice",
            action_url=reverse("quote_requests"),
        )
        other_user = get_user_model().objects.create_user(
            email="other@example.com", password="test-password"
        )

        response = self.client.get(reverse("notification_center"))
        self.assertContains(response, "A private operations alert")
        self.assertContains(response, reverse("operations_dashboard"))
        self.assertContains(response, "Dashboard navigation")
        response = self.client.get(reverse("notification_open", args=[notice.pk]))
        self.assertRedirects(response, reverse("quote_requests"))
        notice.refresh_from_db()
        self.assertIsNotNone(notice.read_at)

        self.client.force_login(other_user)
        response = self.client.get(reverse("notification_open", args=[notice.pk]))
        self.assertEqual(response.status_code, 404)

    def test_about_story_photos_are_optional_and_placement_specific(self):
        origin_photo = EventPhoto.objects.create(
            image=SimpleUploadedFile("origin.jpg", b"origin", content_type="image/jpeg"),
            alt_text="Ethiopian highlands at sunrise",
            category=EventPhoto.Category.CULTURAL,
            placement=EventPhoto.Placement.ABOUT_ORIGIN,
            usage_rights_confirmed=True,
            active=True,
        )

        about_response = self.client.get(reverse("about"))
        home_response = self.client.get(reverse("home"))

        self.assertContains(about_response, origin_photo.image.url)
        self.assertContains(about_response, "Ethiopian highlands at sunrise")
        self.assertNotContains(home_response, origin_photo.image.url)

    def test_approved_ceremony_photo_replaces_default_about_image(self):
        ceremony_photo = EventPhoto.objects.create(
            image=SimpleUploadedFile("ceremony.jpg", b"ceremony", content_type="image/jpeg"),
            alt_text="An Akako House ambassador serving guests",
            placement=EventPhoto.Placement.ABOUT_CEREMONY,
            usage_rights_confirmed=True,
            active=True,
        )

        response = self.client.get(reverse("about"))

        self.assertContains(response, ceremony_photo.image.url)
        self.assertNotContains(
            response,
            "A cultural ambassador pouring Ethiopian coffee from a Jebena for a guest",
        )

    def test_signed_out_homepage_has_partner_login_button(self):
        self.client.logout()

        response = self.client.get(reverse("home"))

        self.assertContains(response, reverse("partner_login"))
        self.assertContains(
            response,
            f'<a class="nav-account-button" href="{reverse("partner_login")}">Login</a>',
            html=True,
        )

    def test_staff_signs_in_with_email_and_not_username(self):
        self.client.logout()

        response = self.client.post(
            reverse("partner_login"),
            {"login": "operations@example.com", "password": "test-password"},
        )

        self.assertRedirects(response, reverse("operations_dashboard"))

    def test_partner_can_self_register_with_supporting_document(self):
        self.client.logout()
        response = self.client.post(
            reverse("partner_registration"),
            {
                "email": "partner@example.com",
                "password1": "Strong-example-password-847!",
                "password2": "Strong-example-password-847!",
                "name": "Aster Coffee Service",
                "partner_type": Partner.PartnerType.INDIVIDUAL,
                "contact_name": "Aster Bekele",
                "phone": "555-0140",
                "service_area": "Silver Spring, MD",
                "address": "100 Main Street",
                "bio": "Experienced ceremony ambassador.",
                "payout_method": Partner.PayoutMethod.ACH,
                "payout_destination": "Account ending 1234",
                "food_permit": SimpleUploadedFile(
                    "permit.pdf", b"%PDF-1.4 test permit", content_type="application/pdf"
                ),
            },
        )

        self.assertRedirects(response, reverse("account_email_verification_sent"))
        partner = Partner.objects.get(email="partner@example.com")
        self.assertEqual(partner.application_status, Partner.ApplicationStatus.SUBMITTED)
        self.assertFalse(partner.active)
        self.assertIsNotNone(partner.user)
        email_address = EmailAddress.objects.get(user=partner.user)
        self.assertEqual(email_address.email, "partner@example.com")
        self.assertFalse(email_address.verified)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(partner.documents.count(), 1)
        self.assertEqual(
            partner.documents.get().document_type,
            PartnerDocument.DocumentType.FOOD_PERMIT,
        )

    def test_verified_customer_registration_claims_matching_guest_requests(self):
        guest_quote = QuoteRequest.objects.create(
            customer_name="Aster Customer",
            email="customer@example.com",
            phone="555-0150",
            event_type=QuoteRequest.EventType.HOME,
            event_date=date(2026, 9, 1),
            location="Rockville, MD",
            guest_count=20,
        )
        self.client.logout()
        response = self.client.post(
            reverse("customer_registration"),
            {
                "email": "customer@example.com",
                "password1": "Strong-customer-password-847!",
                "password2": "Strong-customer-password-847!",
                "first_name": "Aster",
                "last_name": "Customer",
                "phone": "555-0150",
                "address": "100 Main Street",
                "city": "Rockville",
                "state": "MD",
                "postal_code": "20850",
                "marketing_opt_in": "on",
            },
        )

        self.assertRedirects(response, reverse("account_email_verification_sent"))
        customer = CustomerProfile.objects.get(user__email="customer@example.com")
        self.assertEqual(customer.addresses.count(), 1)
        guest_quote.refresh_from_db()
        self.assertIsNone(guest_quote.customer)

        address = EmailAddress.objects.get(user=customer.user)
        address.verified = True
        address.save(update_fields=["verified"])
        email_confirmed.send(
            sender=EmailAddress,
            request=None,
            email_address=address,
        )
        guest_quote.refresh_from_db()
        self.assertEqual(guest_quote.customer, customer)

    def test_logged_in_customer_request_is_attached_to_their_profile(self):
        user = get_user_model().objects.create_user(
            email="requester@example.com", password="customer-password"
        )
        customer = CustomerProfile.objects.create(
            user=user,
            first_name="Rahel",
            last_name="Requester",
            phone="555-0151",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("home"),
            {
                "event_type": QuoteRequest.EventType.HOME,
                "event_date": date(2026, 9, 2).isoformat(),
                "location": "Bethesda, MD",
                "guest_count": 18,
                "milk_preference": QuoteRequest.MilkPreference.YES,
                "snack_style": QuoteRequest.SnackStyle.SHARED,
            },
        )

        quote = QuoteRequest.objects.get()
        self.assertRedirects(response, reverse("quote_contact", args=[quote.public_id]))
        self.assertEqual(quote.customer, customer)

    def test_customer_cancellation_requires_staff_approval(self):
        user = get_user_model().objects.create_user(
            email="cancel-customer@example.com", password="customer-password"
        )
        customer = CustomerProfile.objects.create(
            user=user,
            first_name="Marta",
            last_name="Customer",
            phone="555-0152",
        )
        ceremony = self.create_ceremony()
        ceremony.quote.customer = customer
        ceremony.quote.save(update_fields=["customer"])
        self.client.force_login(user)

        response = self.client.post(
            reverse("customer_request_cancellation", args=[ceremony.public_id]),
            {"reason": "The event was postponed."},
        )
        self.assertRedirects(
            response,
            reverse("customer_quote_detail", args=[ceremony.quote.public_id]),
        )
        cancellation = CustomerCancellationRequest.objects.get()
        ceremony.refresh_from_db()
        self.assertEqual(cancellation.status, CustomerCancellationRequest.Status.PENDING)
        self.assertFalse(ceremony.terminal)

        self.client.force_login(self.staff_user)
        response = self.client.post(
            reverse("review_customer_cancellation", args=[cancellation.pk, "approve"]),
            {"staff_notes": "Confirmed with customer."},
        )
        self.assertRedirects(response, reverse("ceremony_detail", args=[ceremony.public_id]))
        cancellation.refresh_from_db()
        ceremony.refresh_from_db()
        self.assertEqual(cancellation.status, CustomerCancellationRequest.Status.APPROVED)
        self.assertEqual(ceremony.status, Ceremony.Status.CANCELLED)
        self.assertEqual(ceremony.outcome_reason, Ceremony.OutcomeReason.CUSTOMER_CANCELLED)

    def test_assignment_creates_partner_task_and_payout_record(self):
        partner = Partner.objects.create(
            name="Ready Ambassador",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Rahel",
            email="rahel@example.com",
            phone="555-0191",
            service_area="Silver Spring",
            active=True,
            food_permit_verified=True,
            insurance_verified=True,
            cultural_training_verified=True,
        )
        ceremony = self.create_ceremony()

        response = self.client.post(
            reverse("assign_partner", args=[ceremony.public_id]),
            {"partner": partner.pk},
        )

        self.assertRedirects(response, reverse("ceremony_detail", args=[ceremony.public_id]))
        task = PartnerTask.objects.get(ceremony=ceremony)
        self.assertEqual(task.partner, partner)
        self.assertEqual(task.status, PartnerTask.Status.ASSIGNED)
        self.assertEqual(task.payout.status, PartnerPayout.Status.NOT_READY)
        self.assertEqual(
            Notification.objects.filter(kind=Notification.Kind.PARTNER_ASSIGNED).count(),
            2,
        )

    def test_partner_can_progress_only_their_assigned_task(self):
        user = get_user_model().objects.create_user(
            email="portal-partner@example.com", password="partner-password"
        )
        partner = Partner.objects.create(
            user=user,
            name="Portal Partner",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Mimi",
            email="mimi@example.com",
            phone="555-0122",
            service_area="Bethesda",
        )
        ceremony = self.create_ceremony()
        task = PartnerTask.objects.create(ceremony=ceremony, partner=partner)
        PartnerPayout.objects.create(task=task, partner=partner, amount=Decimal("175.00"))
        self.client.force_login(user)

        for status in [
            PartnerTask.Status.ACCEPTED,
            PartnerTask.Status.IN_PROGRESS,
            PartnerTask.Status.DELIVERED,
        ]:
            response = self.client.post(
                reverse("partner_task_update", args=[task.pk]),
                {"status": status, "partner_notes": "Updated from portal."},
            )
            self.assertRedirects(response, reverse("partner_task_detail", args=[task.pk]))
            task.refresh_from_db()
            self.assertEqual(task.status, status)

        self.assertTrue(
            Notification.objects.filter(
                recipient=self.staff_user,
                kind=Notification.Kind.PARTNER_ACCEPTED,
            ).exists()
        )
        self.assertEqual(
            len([message for message in mail.outbox if "ambassador confirmed" in message.subject.lower()]),
            1,
        )

        task.payout.refresh_from_db()
        self.assertEqual(task.payout.status, PartnerPayout.Status.PENDING)

    def test_partner_document_download_is_private_to_owner_and_authorized_staff(self):
        owner_user = get_user_model().objects.create_user(
            email="document-owner@example.com", password="partner-password"
        )
        owner = Partner.objects.create(
            user=owner_user,
            name="Document Owner",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Owner",
            email="owner@example.com",
            phone="555-0110",
            service_area="Rockville",
        )
        document = PartnerDocument.objects.create(
            partner=owner,
            document_type=PartnerDocument.DocumentType.INSURANCE,
            file=SimpleUploadedFile("insurance.pdf", b"%PDF private"),
        )
        other_user = get_user_model().objects.create_user(
            email="unrelated-partner@example.com", password="partner-password"
        )
        Partner.objects.create(
            user=other_user,
            name="Unrelated Partner",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Other",
            email="other@example.com",
            phone="555-0111",
            service_area="Bethesda",
        )

        self.client.force_login(other_user)
        denied = self.client.get(
            reverse("partner_document_download", args=[document.pk])
        )
        self.assertEqual(denied.status_code, 403)

        self.client.force_login(owner_user)
        allowed = self.client.get(
            reverse("partner_document_download", args=[document.pk])
        )
        self.assertEqual(allowed.status_code, 200)
        allowed.close()

    def test_partner_sidebar_badge_tracks_documents_pending_review(self):
        partner = Partner.objects.create(
            name="Badge Partner",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Badge Owner",
            email="badge@example.com",
            phone="555-0143",
            service_area="Rockville",
        )
        document = PartnerDocument.objects.create(
            partner=partner,
            document_type=PartnerDocument.DocumentType.OTHER,
            file=SimpleUploadedFile("support.pdf", b"%PDF pending"),
        )

        dashboard = self.client.get(reverse("operations_dashboard"))
        self.assertContains(dashboard, 'class="nav-alert-badge"')
        self.assertContains(dashboard, "1 new partner document pending review")

        self.client.post(
            reverse("review_partner_document", args=[document.pk, "approve"]),
            {"review_notes": "Reviewed."},
        )
        dashboard = self.client.get(reverse("operations_dashboard"))
        self.assertNotContains(dashboard, 'class="nav-alert-badge"')

    def test_document_expiry_date_is_visible_to_staff_but_not_partner(self):
        user = get_user_model().objects.create_user(
            email="expiry-owner@example.com", password="partner-password"
        )
        partner = Partner.objects.create(
            user=user,
            name="Expiry Owner",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Expiry Owner",
            email=user.email,
            phone="555-0144",
            service_area="Rockville",
        )
        expiry_date = timezone.localdate() + timezone.timedelta(days=30)
        PartnerDocument.objects.create(
            partner=partner,
            document_type=PartnerDocument.DocumentType.INSURANCE,
            file=SimpleUploadedFile("insurance.pdf", b"%PDF private"),
            review_status=PartnerDocument.ReviewStatus.APPROVED,
            expiry_date=expiry_date,
        )

        staff_page = self.client.get(reverse("manage_partner", args=[partner.pk]))
        self.assertContains(staff_page, expiry_date.isoformat())

        self.client.force_login(user)
        partner_page = self.client.get(reverse("partner_documents"))
        self.assertNotContains(partner_page, expiry_date.isoformat())
        self.assertNotContains(partner_page, "Expiry date (staff only)")

    def test_expired_document_deactivates_partner_only_once(self):
        partner = Partner.objects.create(
            name="Expiring Partner",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Hana",
            email="expiring@example.com",
            phone="555-0145",
            service_area="Bethesda",
            active=True,
            application_status=Partner.ApplicationStatus.APPROVED,
            food_permit_verified=True,
            insurance_verified=True,
            cultural_training_verified=True,
        )
        document = PartnerDocument.objects.create(
            partner=partner,
            document_type=PartnerDocument.DocumentType.INSURANCE,
            file=SimpleUploadedFile("insurance.pdf", b"%PDF expired"),
            review_status=PartnerDocument.ReviewStatus.APPROVED,
            expiry_date=timezone.localdate(),
        )

        process_workflow_deadlines()
        process_workflow_deadlines()

        partner.refresh_from_db()
        document.refresh_from_db()
        self.assertFalse(partner.active)
        self.assertFalse(partner.insurance_verified)
        self.assertEqual(
            partner.application_status,
            Partner.ApplicationStatus.NEEDS_INFO,
        )
        self.assertEqual(
            document.review_status,
            PartnerDocument.ReviewStatus.EXPIRED,
        )
        self.assertIsNotNone(document.expiry_processed_at)
        expiry_messages = [
            message for message in mail.outbox
            if "update an Akako House partner document" in message.subject
        ]
        self.assertEqual(len(expiry_messages), 1)

    def test_expired_document_does_not_deactivate_partner_with_current_replacement(self):
        partner = Partner.objects.create(
            name="Renewed Partner",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Marta",
            email="renewed@example.com",
            phone="555-0146",
            service_area="Bethesda",
            active=True,
            insurance_verified=True,
        )
        old_document = PartnerDocument.objects.create(
            partner=partner,
            document_type=PartnerDocument.DocumentType.INSURANCE,
            file=SimpleUploadedFile("old-insurance.pdf", b"%PDF expired"),
            review_status=PartnerDocument.ReviewStatus.APPROVED,
            expiry_date=timezone.localdate(),
        )
        PartnerDocument.objects.create(
            partner=partner,
            document_type=PartnerDocument.DocumentType.INSURANCE,
            file=SimpleUploadedFile("new-insurance.pdf", b"%PDF current"),
            review_status=PartnerDocument.ReviewStatus.APPROVED,
            expiry_date=timezone.localdate() + timezone.timedelta(days=365),
        )

        process_workflow_deadlines()

        partner.refresh_from_db()
        old_document.refresh_from_db()
        self.assertTrue(partner.active)
        self.assertTrue(partner.insurance_verified)
        self.assertEqual(
            old_document.review_status,
            PartnerDocument.ReviewStatus.EXPIRED,
        )

    def test_partner_document_expiry_warning_is_sent_once_per_milestone(self):
        partner_user = get_user_model().objects.create_user(
            email="expiring-partner@example.com", password="test-password"
        )
        partner = Partner.objects.create(
            user=partner_user,
            name="Expiring Partner",
            contact_name="Marta",
            email=partner_user.email,
            phone="555-0123",
            service_area="Maryland",
        )
        document = PartnerDocument.objects.create(
            partner=partner,
            document_type=PartnerDocument.DocumentType.INSURANCE,
            file=SimpleUploadedFile("insurance.pdf", b"%PDF current"),
            review_status=PartnerDocument.ReviewStatus.APPROVED,
            expiry_date=timezone.localdate() + timezone.timedelta(days=7),
        )

        process_workflow_deadlines()
        process_workflow_deadlines()

        warning = Notification.objects.get(
            recipient=partner_user,
            kind=Notification.Kind.DOCUMENT_EXPIRING,
        )
        self.assertIn("expires in 7 days", warning.title)
        self.assertIn(str(document.pk), warning.event_key)
        warning_messages = [
            message for message in mail.outbox if "expires in 7 days" in message.subject
        ]
        self.assertEqual(len(warning_messages), 1)

    def test_quote_request_submission_creates_record(self):
        response = self.client.post(
            reverse("home"),
            {
                "event_type": QuoteRequest.EventType.HOME,
                "event_date": date(2026, 8, 12).isoformat(),
                "location": "Silver Spring, MD",
                "guest_count": 24,
                "milk_preference": QuoteRequest.MilkPreference.YES,
                "snack_style": QuoteRequest.SnackStyle.INDIVIDUAL,
            },
        )

        quote_request = QuoteRequest.objects.first()
        self.assertRedirects(
            response,
            reverse("quote_contact", kwargs={"public_id": quote_request.public_id}),
        )
        self.assertEqual(QuoteRequest.objects.count(), 1)
        self.assertEqual(quote_request.status, QuoteRequest.Status.NEW)
        self.assertFalse(quote_request.contact_complete)

    def test_contact_step_completes_quote_request(self):
        quote_request = QuoteRequest.objects.create(
            event_type=QuoteRequest.EventType.HOME,
            event_date=date(2026, 8, 12),
            location="Silver Spring, MD",
            guest_count=24,
            milk_preference=QuoteRequest.MilkPreference.YES,
            snack_style=QuoteRequest.SnackStyle.INDIVIDUAL,
        )

        response = self.client.post(
            reverse("quote_contact", kwargs={"public_id": quote_request.public_id}),
            {
                "customer_name": "Aster Bekele",
                "email": "aster@example.com",
                "phone": "555-0101",
                "event_time": time(15, 30).strftime("%H:%M"),
                "indoor": "on",
                "allergies": "Dairy sensitivity for one guest",
                "notes": "Family graduation celebration",
            },
        )

        self.assertRedirects(
            response,
            reverse("quote_success", kwargs={"public_id": quote_request.public_id}),
        )
        quote_request.refresh_from_db()
        self.assertTrue(quote_request.contact_complete)
        self.assertEqual(quote_request.customer_name, "Aster Bekele")
        notification = Notification.objects.get(
            recipient=self.staff_user,
            kind=Notification.Kind.QUOTE_NEW,
        )
        self.assertIn("Aster Bekele", notification.message)
        self.assertIn(str(quote_request.public_id), notification.action_url)
        self.assertEqual(notification.email_status, Notification.EmailStatus.SENT)

    def test_dashboard_loads(self):
        response = self.client.get(reverse("operations_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Today's ceremony desk")
        self.assertContains(response, reverse("quote_requests"))
        self.assertContains(response, reverse("ceremonies"))
        self.assertContains(response, reverse("partners"))
        self.assertContains(response, "View Business Insights")
        self.assertContains(response, reverse("business_insights"))

    def test_dashboard_routes_require_staff_access(self):
        quote_request = QuoteRequest.objects.create(
            event_type=QuoteRequest.EventType.HOME,
            event_date=date(2026, 8, 12),
            location="Silver Spring, MD",
            guest_count=24,
        )
        self.client.logout()

        protected_urls = [
            reverse("operations_dashboard"),
            reverse("business_insights"),
            reverse("quote_requests"),
            reverse("manage_quote", kwargs={"public_id": quote_request.public_id}),
            reverse("ceremonies"),
            reverse("partners"),
            reverse("add_partner"),
        ]
        login_url = reverse("admin:login")
        for url in protected_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, f"{login_url}?next={url}")

    def test_branded_quote_request_list_loads(self):
        response = self.client.get(reverse("quote_requests"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quote requests")
        self.assertContains(response, "All statuses")

    def test_quote_queue_badge_includes_new_requests_and_partner_replies(self):
        new_quote = QuoteRequest.objects.create(
            customer_name="Aster Bekele",
            email="aster@example.com",
            phone="555-0101",
            event_type=QuoteRequest.EventType.HOME,
            event_date=date(2026, 8, 12),
            location="Silver Spring, MD",
            guest_count=12,
        )
        answered_quote = QuoteRequest.objects.create(
            customer_name="Hana Tesfaye",
            email="hana@example.com",
            phone="555-0102",
            event_type=QuoteRequest.EventType.CORPORATE,
            event_date=date(2026, 8, 14),
            event_time=time(14, 0),
            location="Washington, DC",
            guest_count=30,
            status=QuoteRequest.Status.REVIEWING,
        )
        partner = Partner.objects.create(
            name="Availability Partner",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Almaz",
            email="availability@example.com",
            phone="555-0188",
            service_area="Washington, DC",
        )
        AvailabilityOffer.objects.create(
            quote=answered_quote,
            partner=partner,
            status=AvailabilityOffer.Status.DECLINED,
            expires_at=timezone.now() + timezone.timedelta(days=1),
            responded_at=timezone.now(),
            created_by=self.staff_user,
        )

        response = self.client.get(reverse("quote_requests"))

        self.assertEqual(response.context["quote_attention_count"], 2)
        self.assertEqual(response.context["new_quote_count"], 1)
        self.assertEqual(response.context["answered_availability_count"], 1)
        self.assertContains(response, "2 requests need attention")
        self.assertContains(response, "Availability answered")
        self.assertContains(response, "Needs review")

        new_quote.status = QuoteRequest.Status.QUOTED
        new_quote.save(update_fields=["status", "updated_at"])
        answered_quote.status = QuoteRequest.Status.QUOTED
        answered_quote.save(update_fields=["status", "updated_at"])
        response = self.client.get(reverse("quote_requests"))
        self.assertEqual(response.context["quote_attention_count"], 0)

    def test_unfinished_quote_draft_is_hidden_from_staff_queue(self):
        draft = QuoteRequest.objects.create(
            event_type=QuoteRequest.EventType.HOME,
            event_date=date(2026, 8, 12),
            location="Silver Spring, MD",
            guest_count=12,
        )

        response = self.client.get(reverse("quote_requests"))

        self.assertNotContains(response, reverse("manage_quote", args=[draft.public_id]))
        self.assertEqual(response.context["quote_attention_count"], 0)
        dashboard = self.client.get(reverse("operations_dashboard"))
        self.assertNotContains(dashboard, reverse("manage_quote", args=[draft.public_id]))

    def test_business_insights_reports_custom_date_range(self):
        completed = self.create_ceremony(event_date=date(2026, 8, 10))
        completed.status = Ceremony.Status.COMPLETED
        completed.completed_at = timezone.now()
        completed.save()
        failed = self.create_ceremony(event_date=date(2026, 8, 11))
        failed.status = Ceremony.Status.FAILED
        failed.outcome_reason = Ceremony.OutcomeReason.PARTNER_NO_SHOW
        failed.save()
        cancelled = self.create_ceremony(event_date=date(2026, 8, 12))
        cancelled.status = Ceremony.Status.CANCELLED
        cancelled.outcome_reason = Ceremony.OutcomeReason.CUSTOMER_CANCELLED
        cancelled.save()
        QuoteRequest.objects.create(
            event_type=QuoteRequest.EventType.OTHER,
            event_date=date(2026, 8, 13),
            location="Takoma Park, MD",
            guest_count=12,
        )

        response = self.client.get(
            reverse("business_insights"),
            {"period": "custom", "start": "2026-08-01", "end": "2026-08-31"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["metrics"]["requested"], 4)
        self.assertEqual(response.context["metrics"]["accepted"], 3)
        self.assertEqual(response.context["metrics"]["completed"], 1)
        self.assertEqual(response.context["metrics"]["failed"], 1)
        self.assertEqual(response.context["metrics"]["cancelled"], 1)
        self.assertContains(response, "Ceremony outcomes")
        self.assertContains(response, "33.3%")

    def test_partner_can_be_added_inactive_from_operations_workspace(self):
        response = self.client.post(
            reverse("add_partner"),
            {
                "name": "Habesha Coffee Collective",
                "partner_type": Partner.PartnerType.RESTAURANT,
                "contact_name": "Mimi Tesfaye",
                "email": "mimi@example.com",
                "phone": "555-0199",
                "service_area": "Silver Spring, MD",
                "food_permit_verified": "on",
                "insurance_verified": "on",
                "cultural_training_verified": "on",
                "notes": "Available weekends.",
            },
        )

        self.assertRedirects(response, reverse("partners"))
        partner = Partner.objects.get(name="Habesha Coffee Collective")
        self.assertFalse(partner.active)

    def test_partner_activation_requires_all_current_compliance_documents(self):
        partner = Partner.objects.create(
            name="Pending Compliance",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Mimi",
            email="compliance@example.com",
            phone="555-0188",
            service_area="Silver Spring",
            application_status=Partner.ApplicationStatus.APPROVED,
            food_permit_verified=True,
            insurance_verified=True,
            cultural_training_verified=True,
            active=False,
        )
        for document_type, days in [
            (PartnerDocument.DocumentType.FOOD_PERMIT, 30),
            (PartnerDocument.DocumentType.INSURANCE, 30),
            (PartnerDocument.DocumentType.TRAINING, 0),
        ]:
            PartnerDocument.objects.create(
                partner=partner,
                document_type=document_type,
                file=SimpleUploadedFile(f"{document_type}.pdf", b"%PDF compliance"),
                review_status=PartnerDocument.ReviewStatus.APPROVED,
                expiry_date=timezone.localdate() + timezone.timedelta(days=days),
            )

        response = self.client.post(
            reverse("manage_partner", args=[partner.pk]),
            {
                "name": partner.name,
                "partner_type": partner.partner_type,
                "contact_name": partner.contact_name,
                "email": partner.email,
                "phone": partner.phone,
                "service_area": partner.service_area,
                "application_status": Partner.ApplicationStatus.APPROVED,
                "food_permit_verified": "on",
                "insurance_verified": "on",
                "cultural_training_verified": "on",
                "active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "expiry dates after today")
        partner.refresh_from_db()
        self.assertFalse(partner.active)

    def test_partner_can_be_activated_when_all_compliance_documents_are_current(self):
        partner = Partner.objects.create(
            name="Current Compliance",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Rahel",
            email="current-compliance@example.com",
            phone="555-0189",
            service_area="Silver Spring",
            application_status=Partner.ApplicationStatus.APPROVED,
            food_permit_verified=True,
            insurance_verified=True,
            cultural_training_verified=True,
            active=False,
        )
        for document_type in [
            PartnerDocument.DocumentType.FOOD_PERMIT,
            PartnerDocument.DocumentType.INSURANCE,
            PartnerDocument.DocumentType.TRAINING,
        ]:
            PartnerDocument.objects.create(
                partner=partner,
                document_type=document_type,
                file=SimpleUploadedFile(f"{document_type}.pdf", b"%PDF compliance"),
                review_status=PartnerDocument.ReviewStatus.APPROVED,
                expiry_date=timezone.localdate() + timezone.timedelta(days=30),
            )

        response = self.client.post(
            reverse("manage_partner", args=[partner.pk]),
            {
                "name": partner.name,
                "partner_type": partner.partner_type,
                "contact_name": partner.contact_name,
                "email": partner.email,
                "phone": partner.phone,
                "service_area": partner.service_area,
                "application_status": Partner.ApplicationStatus.APPROVED,
                "food_permit_verified": "on",
                "insurance_verified": "on",
                "cultural_training_verified": "on",
                "active": "on",
            },
        )

        self.assertRedirects(response, reverse("partners"))
        partner.refresh_from_db()
        self.assertTrue(partner.active)

    def test_partner_can_be_deactivated_from_edit_form(self):
        partner = Partner.objects.create(
            name="Habesha Coffee Collective",
            partner_type=Partner.PartnerType.RESTAURANT,
            contact_name="Mimi Tesfaye",
            email="mimi@example.com",
            phone="555-0199",
            service_area="Silver Spring, MD",
            food_permit_verified=True,
            insurance_verified=True,
            cultural_training_verified=True,
            active=True,
        )

        response = self.client.post(
            reverse("manage_partner", args=[partner.pk]),
            {
                "name": partner.name,
                "partner_type": partner.partner_type,
                "contact_name": partner.contact_name,
                "email": partner.email,
                "phone": partner.phone,
                "service_area": partner.service_area,
                "food_permit_verified": "on",
                "insurance_verified": "on",
                "cultural_training_verified": "on",
                "notes": "Temporarily unavailable.",
            },
        )
        self.assertRedirects(response, reverse("partners"))
        partner.refresh_from_db()
        self.assertFalse(partner.active)

    def test_quote_review_accepts_quoted_request(self):
        quote_request = QuoteRequest.objects.create(
            customer_name="Aster Bekele",
            email="aster@example.com",
            phone="555-0101",
            event_type=QuoteRequest.EventType.HOME,
            event_date=date(2026, 8, 12),
            event_time=time(15, 30),
            location="Silver Spring, MD",
            guest_count=24,
            milk_preference=QuoteRequest.MilkPreference.YES,
            snack_style=QuoteRequest.SnackStyle.INDIVIDUAL,
            quoted_amount=Decimal("450.00"),
            deposit_amount=Decimal("150.00"),
            quote_expires_at=timezone.now() + timezone.timedelta(days=7),
            status=QuoteRequest.Status.QUOTED,
        )
        self.create_capacity_hold(quote_request)

        response = self.client.get(
            reverse("quote_review", kwargs={"public_id": quote_request.public_id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "$450.00")
        self.assertContains(response, "Accept Quote")

        response = self.client.post(
            reverse(
                "quote_decision",
                kwargs={"public_id": quote_request.public_id, "decision": "accept"},
            ),
            {"policy_consent": "on"},
        )

        self.assertRedirects(
            response,
            reverse("quote_review", kwargs={"public_id": quote_request.public_id}),
        )
        quote_request.refresh_from_db()
        self.assertEqual(quote_request.status, QuoteRequest.Status.ACCEPTED)
        self.assertEqual(
            quote_request.policy_acceptances.count(),
            PolicyDocument.objects.filter(is_active=True).count(),
        )
        self.assertTrue(
            quote_request.policy_acceptances.filter(
                accepted_email=quote_request.email,
                policy_content__contains="PLACEHOLDER POLICY CONTENT",
            ).exists()
        )
        self.assertTrue(hasattr(quote_request, "ceremony"))
        self.assertEqual(quote_request.ceremony.status, Ceremony.Status.AWAITING_DEPOSIT)
        self.assertEqual(quote_request.ceremony.payments.count(), 2)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("payment option", mail.outbox[0].subject.lower())
        self.assertIn(str(quote_request.ceremony.public_id), mail.outbox[0].body)

    def test_quote_email_contains_review_link(self):
        quote_request = QuoteRequest.objects.create(
            customer_name="Aster Bekele",
            email="aster@example.com",
            phone="555-0101",
            event_type=QuoteRequest.EventType.HOME,
            event_date=date(2026, 8, 12),
            location="Silver Spring, MD",
            guest_count=24,
            milk_preference=QuoteRequest.MilkPreference.YES,
            snack_style=QuoteRequest.SnackStyle.INDIVIDUAL,
            quoted_amount=Decimal("450.00"),
        )

        send_quote_email(quote_request, "/quote/example/review/")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/quote/example/review/", mail.outbox[0].body)

    def test_quote_can_be_saved_as_draft_from_dashboard(self):
        quote_request = QuoteRequest.objects.create(
            customer_name="Aster Bekele",
            email="aster@example.com",
            phone="555-0101",
            event_type=QuoteRequest.EventType.HOME,
            event_date=date(2026, 8, 12),
            location="Silver Spring, MD",
            guest_count=24,
        )

        response = self.client.post(
            reverse("manage_quote", kwargs={"public_id": quote_request.public_id}),
            {
                "quoted_amount": "450.00",
                "deposit_amount": "150.00",
                "quote_notes": "Includes setup and two hours of service.",
                "action": "save",
            },
        )

        self.assertRedirects(
            response,
            reverse("manage_quote", kwargs={"public_id": quote_request.public_id}),
        )
        quote_request.refresh_from_db()
        self.assertEqual(quote_request.quoted_amount, Decimal("450.00"))
        self.assertEqual(quote_request.status, QuoteRequest.Status.REVIEWING)
        self.assertEqual(len(mail.outbox), 0)

    def test_quote_workspace_prefills_editable_customer_note(self):
        quote_request = QuoteRequest.objects.create(
            customer_name="Aster Bekele",
            email="aster@example.com",
            phone="555-0101",
            event_type=QuoteRequest.EventType.GRADUATION,
            event_date=date(2026, 8, 12),
            location="Silver Spring, MD",
            guest_count=24,
            milk_preference=QuoteRequest.MilkPreference.NON_DAIRY,
            snack_style=QuoteRequest.SnackStyle.INDIVIDUAL,
        )

        response = self.client.get(
            reverse("manage_quote", kwargs={"public_id": quote_request.public_id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thank you for considering Akako House")
        self.assertContains(response, "24 guests")
        self.assertContains(response, "snack service preference")
        self.assertNotContains(response, "milk preference")
        self.assertNotContains(response, "Non-dairy option")
        quote_request.refresh_from_db()
        self.assertEqual(quote_request.quote_notes, "")

        quote_request.quote_notes = "A staff-edited note."
        quote_request.save(update_fields=["quote_notes", "updated_at"])
        response = self.client.get(
            reverse("manage_quote", kwargs={"public_id": quote_request.public_id})
        )
        self.assertContains(response, "A staff-edited note.")
        self.assertNotContains(response, "Thank you for considering Akako House")

    def test_quote_can_be_saved_and_sent_from_dashboard(self):
        quote_request = QuoteRequest.objects.create(
            customer_name="Aster Bekele",
            email="aster@example.com",
            phone="555-0101",
            event_type=QuoteRequest.EventType.HOME,
            event_date=date(2026, 8, 12),
            event_time=time(15, 30),
            location="Silver Spring, MD",
            guest_count=24,
        )
        self.create_capacity_hold(quote_request)

        response = self.client.post(
            reverse("manage_quote", kwargs={"public_id": quote_request.public_id}),
            {
                "quoted_amount": "450.00",
                "deposit_amount": "150.00",
                "quote_notes": "Includes setup and two hours of service.",
                "action": "send",
            },
        )

        self.assertRedirects(
            response,
            reverse("operations_dashboard"),
        )
        quote_request.refresh_from_db()
        self.assertEqual(quote_request.status, QuoteRequest.Status.QUOTED)
        self.assertIsNotNone(quote_request.quote_sent_at)
        self.assertIsNotNone(quote_request.quote_expires_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(str(quote_request.public_id), mail.outbox[0].body)

        dashboard = self.client.get(reverse("operations_dashboard"))
        self.assertNotContains(dashboard, "Create Quote")

    def test_only_available_partner_can_be_assigned_on_event_date(self):
        available_partner = Partner.objects.create(
            name="Available Ambassador",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Almaz",
            email="almaz@example.com",
            phone="555-0102",
            service_area="Silver Spring, MD",
            food_permit_verified=True,
            insurance_verified=True,
            cultural_training_verified=True,
        )
        booked_partner = Partner.objects.create(
            name="Booked Ambassador",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Sara",
            email="sara@example.com",
            phone="555-0103",
            service_area="Silver Spring, MD",
            food_permit_verified=True,
            insurance_verified=True,
            cultural_training_verified=True,
        )
        booked_ceremony = self.create_ceremony()
        booked_ceremony.assigned_partner = booked_partner
        booked_ceremony.status = Ceremony.Status.ASSIGNED
        booked_ceremony.save()
        target = self.create_ceremony()

        workspace = self.client.get(
            reverse("ceremony_detail", kwargs={"public_id": target.public_id})
        )
        self.assertContains(workspace, available_partner.name)
        self.assertNotContains(workspace, booked_partner.name)

        response = self.client.post(
            reverse("assign_partner", kwargs={"public_id": target.public_id}),
            {"partner": available_partner.pk},
        )
        self.assertRedirects(
            response,
            reverse("ceremony_detail", kwargs={"public_id": target.public_id}),
        )
        target.refresh_from_db()
        self.assertEqual(target.assigned_partner, available_partner)
        self.assertEqual(target.status, Ceremony.Status.ASSIGNED)

    def test_booked_partner_cannot_be_assigned(self):
        booked_partner = Partner.objects.create(
            name="Booked Ambassador",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Sara",
            email="sara@example.com",
            phone="555-0103",
            service_area="Silver Spring, MD",
            food_permit_verified=True,
            insurance_verified=True,
            cultural_training_verified=True,
        )
        booked_ceremony = self.create_ceremony()
        booked_ceremony.assigned_partner = booked_partner
        booked_ceremony.status = Ceremony.Status.ASSIGNED
        booked_ceremony.save()
        target = self.create_ceremony()

        response = self.client.post(
            reverse("assign_partner", kwargs={"public_id": target.public_id}),
            {"partner": booked_partner.pk},
        )

        self.assertRedirects(
            response,
            reverse("ceremony_detail", kwargs={"public_id": target.public_id}),
        )
        target.refresh_from_db()
        self.assertIsNone(target.assigned_partner)

    def test_accepted_quote_cannot_be_edited_or_resent(self):
        quote_request = QuoteRequest.objects.create(
            customer_name="Aster Bekele",
            email="aster@example.com",
            phone="555-0101",
            event_type=QuoteRequest.EventType.HOME,
            event_date=date(2026, 8, 12),
            location="Silver Spring, MD",
            guest_count=24,
            quoted_amount=Decimal("450.00"),
            deposit_amount=Decimal("150.00"),
            quote_notes="Accepted terms.",
            status=QuoteRequest.Status.ACCEPTED,
        )

        workspace = self.client.get(
            reverse("manage_quote", kwargs={"public_id": quote_request.public_id})
        )
        self.assertContains(workspace, "This quote has been accepted")
        self.assertContains(workspace, "value=\"save\" disabled")
        self.assertContains(workspace, "value=\"send\" disabled")

        response = self.client.post(
            reverse("manage_quote", kwargs={"public_id": quote_request.public_id}),
            {
                "quoted_amount": "999.00",
                "deposit_amount": "500.00",
                "quote_notes": "Changed terms.",
                "action": "send",
            },
        )
        self.assertRedirects(
            response,
            reverse("manage_quote", kwargs={"public_id": quote_request.public_id}),
        )
        quote_request.refresh_from_db()
        self.assertEqual(quote_request.quoted_amount, Decimal("450.00"))
        self.assertEqual(quote_request.quote_notes, "Accepted terms.")
        self.assertEqual(len(mail.outbox), 0)

    def test_completed_job_is_frozen(self):
        partner = Partner.objects.create(
            name="Assigned Ambassador",
            partner_type=Partner.PartnerType.INDIVIDUAL,
            contact_name="Almaz",
            email="almaz@example.com",
            phone="555-0102",
            service_area="Silver Spring, MD",
            food_permit_verified=True,
            insurance_verified=True,
            cultural_training_verified=True,
        )
        ceremony = self.create_ceremony()
        self.client.post(
            reverse("record_payment", args=[ceremony.public_id, Payment.PaymentType.FINAL]),
            {
                "received_amount": "450.00",
                "provider_reference": "PAY-FINAL-001",
                "notes": "Paid in full.",
            },
        )
        self.client.post(
            reverse("assign_partner", args=[ceremony.public_id]),
            {"partner": partner.pk},
        )
        ceremony.refresh_from_db()
        self.assertEqual(ceremony.status, Ceremony.Status.READY)

        response = self.client.post(
            reverse("complete_job", kwargs={"public_id": ceremony.public_id})
        )
        self.assertRedirects(response, reverse("ceremonies"))
        ceremony.refresh_from_db()
        self.assertEqual(ceremony.status, Ceremony.Status.COMPLETED)

        workspace = self.client.get(
            reverse("ceremony_detail", kwargs={"public_id": ceremony.public_id})
        )
        self.assertContains(workspace, "Completed — record frozen")
        self.assertNotContains(workspace, "Assign Partner")
        self.assertNotContains(workspace, "Mark Completed Successfully")

        response = self.client.post(
            reverse("assign_partner", kwargs={"public_id": ceremony.public_id}),
            {"partner": partner.pk},
        )
        self.assertRedirects(
            response,
            reverse("ceremony_detail", kwargs={"public_id": ceremony.public_id}),
        )
        ceremony.refresh_from_db()
        self.assertEqual(ceremony.status, Ceremony.Status.COMPLETED)

    def test_deposit_payment_moves_ceremony_to_assignment_queue(self):
        ceremony = self.create_ceremony(deposit=Decimal("150.00"))
        self.assertEqual(ceremony.status, Ceremony.Status.AWAITING_DEPOSIT)

        response = self.client.post(
            reverse("record_payment", args=[ceremony.public_id, Payment.PaymentType.DEPOSIT]),
            {
                "received_amount": "150.00",
                "provider_reference": "DEP-001",
                "notes": "Card payment received.",
            },
        )

        self.assertRedirects(
            response,
            reverse("ceremony_detail", args=[ceremony.public_id]),
        )
        ceremony.refresh_from_db()
        deposit = ceremony.deposit_payment
        self.assertEqual(deposit.status, Payment.Status.PAID)
        self.assertEqual(deposit.received_amount, Decimal("150.00"))
        self.assertEqual(ceremony.status, Ceremony.Status.AWAITING_PARTNER)
        self.assertTrue(Notification.objects.filter(
            kind=Notification.Kind.PAYMENT_RECEIVED,
            event_key__contains=f"ceremony:{ceremony.pk}:payment",
        ).exists())

    def test_sensitive_ceremony_actions_require_browser_confirmation(self):
        ceremony = self.create_ceremony(deposit=Decimal("150.00"))

        response = self.client.get(
            reverse("ceremony_detail", args=[ceremony.public_id])
        )

        self.assertContains(
            response,
            "Confirm that this deposit was actually received?",
        )
        self.assertContains(
            response,
            "Confirm that the complete outstanding amount was received?",
        )
        self.assertContains(response, "Confirm this ceremony was cancelled?")

    def test_customer_full_payment_settles_deposit_and_final_balance(self):
        ceremony = self.create_ceremony(deposit=Decimal("150.00"))

        response = self.client.post(
            reverse("record_full_payment", args=[ceremony.public_id]),
            {
                "received_amount": "450.00",
                "provider_reference": "FULL-001",
                "notes": "Customer chose to pay everything at once.",
            },
        )

        self.assertRedirects(response, reverse("ceremony_detail", args=[ceremony.public_id]))
        ceremony.refresh_from_db()
        deposit = ceremony.deposit_payment
        final_payment = ceremony.final_payment
        self.assertEqual(deposit.status, Payment.Status.PAID)
        self.assertEqual(final_payment.status, Payment.Status.PAID)
        self.assertEqual(deposit.provider_reference, "FULL-001")
        self.assertEqual(final_payment.provider_reference, "FULL-001")
        self.assertEqual(ceremony.status, Ceremony.Status.AWAITING_PARTNER)

    def test_partial_amount_cannot_be_recorded_as_paid_in_full(self):
        ceremony = self.create_ceremony(deposit=Decimal("150.00"))

        response = self.client.post(
            reverse("record_full_payment", args=[ceremony.public_id]),
            {"received_amount": "200.00", "provider_reference": "SHORT-001"},
        )

        self.assertRedirects(response, reverse("ceremony_detail", args=[ceremony.public_id]))
        ceremony.refresh_from_db()
        self.assertEqual(ceremony.deposit_payment.status, Payment.Status.PENDING)
        self.assertEqual(ceremony.final_payment.status, Payment.Status.PENDING)
        self.assertEqual(ceremony.status, Ceremony.Status.AWAITING_DEPOSIT)

    def test_customer_payment_page_offers_deposit_or_full_payment(self):
        ceremony = self.create_ceremony(deposit=Decimal("150.00"))

        response = self.client.get(reverse("ceremony_payment", args=[ceremony.public_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pay deposit")
        self.assertContains(response, "Pay in full")
        self.assertContains(response, "$150.00")
        self.assertContains(response, "$450.00")
        self.assertContains(response, "Online checkout is not active yet")

    @patch("bookings.views.create_checkout_session")
    def test_signed_checkout_webhook_fulfills_full_payment_once(self, create_session):
        ceremony = self.create_ceremony(deposit=Decimal("150.00"))
        create_session.return_value = {
            "id": "cs_test_full_001",
            "url": "https://checkout.example/session/full-001",
            "currency": "usd",
        }
        response = self.client.post(
            reverse("start_payment_checkout", args=[ceremony.public_id, "full"])
        )
        self.assertRedirects(
            response,
            "https://checkout.example/session/full-001",
            fetch_redirect_response=False,
        )
        checkout = PaymentCheckout.objects.get(provider_session_id="cs_test_full_001")
        self.assertEqual(checkout.amount, Decimal("450.00"))

        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_full_001",
                    "payment_intent": "pi_test_full_001",
                }
            },
        }
        with patch("bookings.views.construct_webhook_event", return_value=event):
            first = self.client.post(
                reverse("stripe_webhook"),
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="test-signature",
            )
            second = self.client.post(
                reverse("stripe_webhook"),
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="test-signature",
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        checkout.refresh_from_db()
        ceremony.refresh_from_db()
        self.assertEqual(checkout.status, PaymentCheckout.Status.COMPLETED)
        self.assertEqual(ceremony.deposit_payment.status, Payment.Status.PAID)
        self.assertEqual(ceremony.final_payment.status, Payment.Status.PAID)
        self.assertEqual(
            ceremony.deposit_payment.provider_reference,
            "pi_test_full_001",
        )
        self.assertEqual(ceremony.status, Ceremony.Status.AWAITING_PARTNER)

    def test_missed_deposit_deadline_cancels_and_records_reason(self):
        ceremony = self.create_ceremony(deposit=Decimal("150.00"))
        deposit = ceremony.deposit_payment
        deposit.due_at = timezone.now() - timezone.timedelta(minutes=1)
        deposit.save(update_fields=["due_at", "updated_at"])

        self.client.get(reverse("operations_dashboard"))

        ceremony.refresh_from_db()
        deposit.refresh_from_db()
        self.assertEqual(ceremony.status, Ceremony.Status.CANCELLED)
        self.assertEqual(ceremony.outcome_reason, Ceremony.OutcomeReason.DEPOSIT_NOT_PAID)
        self.assertEqual(deposit.status, Payment.Status.OVERDUE)

    def test_payment_reminder_is_sent_only_once(self):
        ceremony = self.create_ceremony(deposit=Decimal("150.00"))
        deposit = ceremony.deposit_payment
        deposit.due_at = timezone.now() + timezone.timedelta(hours=1)
        deposit.save(update_fields=["due_at", "updated_at"])

        process_workflow_deadlines()
        process_workflow_deadlines()

        deposit.refresh_from_db()
        reminders = [message for message in mail.outbox if "Reminder:" in message.subject]
        self.assertEqual(len(reminders), 1)
        self.assertIsNotNone(deposit.reminder_sent_at)

    def test_final_payment_becomes_overdue_at_24_hour_cutoff(self):
        ceremony = self.create_ceremony()
        final_payment = ceremony.final_payment
        final_payment.due_at = timezone.now() - timezone.timedelta(minutes=1)
        final_payment.save(update_fields=["due_at", "updated_at"])

        self.client.get(reverse("ceremonies"))

        ceremony.refresh_from_db()
        final_payment.refresh_from_db()
        self.assertEqual(ceremony.status, Ceremony.Status.AT_RISK)
        self.assertEqual(final_payment.status, Payment.Status.OVERDUE)
        self.assertEqual(
            ceremony.outcome_reason,
            Ceremony.OutcomeReason.FINAL_PAYMENT_NOT_PAID,
        )

    def test_final_payment_48_and_24_hour_notifications_are_idempotent(self):
        ceremony = self.create_ceremony()
        quote = ceremony.quote
        target = timezone.localtime(timezone.now() + timezone.timedelta(hours=36))
        quote.event_date = target.date()
        quote.event_time = target.time().replace(second=0, microsecond=0)
        quote.save(update_fields=["event_date", "event_time", "updated_at"])
        final_payment = ceremony.final_payment
        final_payment.due_at = ceremony.event_datetime() - timezone.timedelta(hours=24)
        final_payment.save(update_fields=["due_at", "updated_at"])

        process_workflow_deadlines()
        process_workflow_deadlines()
        self.assertEqual(
            Notification.objects.filter(event_key__contains=f"ceremony:{ceremony.pk}:final-payment:48h").count(),
            1,
        )

        target = timezone.localtime(timezone.now() + timezone.timedelta(hours=12))
        quote.event_date = target.date()
        quote.event_time = target.time().replace(second=0, microsecond=0)
        quote.save(update_fields=["event_date", "event_time", "updated_at"])
        final_payment.due_at = ceremony.event_datetime() - timezone.timedelta(hours=24)
        final_payment.status = Payment.Status.PENDING
        final_payment.overdue_notified_at = None
        final_payment.save(update_fields=["due_at", "status", "overdue_notified_at", "updated_at"])

        process_workflow_deadlines()
        process_workflow_deadlines()
        self.assertEqual(
            Notification.objects.filter(
                event_key__contains=f"ceremony:{ceremony.pk}:final-payment:24h",
                recipient__isnull=True,
            ).count(),
            1,
        )
        customer_reminders = [
            message for message in mail.outbox
            if "Final payment required within" in message.subject
        ]
        self.assertEqual(len(customer_reminders), 2)

    def test_ceremony_reminder_reaches_customer_and_partner_once(self):
        ceremony = self.create_ceremony()
        target = timezone.localtime(timezone.now() + timezone.timedelta(hours=12))
        ceremony.quote.event_date = target.date()
        ceremony.quote.event_time = target.time().replace(second=0, microsecond=0)
        ceremony.quote.save(update_fields=["event_date", "event_time", "updated_at"])
        partner_user = get_user_model().objects.create_user(
            email="reminder-partner@example.com", password="test-password"
        )
        partner = Partner.objects.create(
            user=partner_user,
            name="Reminder Partner",
            contact_name="Mimi",
            email=partner_user.email,
            phone="555-0190",
            service_area="Maryland",
        )
        ceremony.assigned_partner = partner
        ceremony.status = Ceremony.Status.ASSIGNED
        ceremony.save(update_fields=["assigned_partner", "status", "updated_at"])
        final_payment = ceremony.final_payment
        final_payment.status = Payment.Status.PAID
        final_payment.paid_at = timezone.now()
        final_payment.save(update_fields=["status", "paid_at", "updated_at"])

        process_workflow_deadlines()
        process_workflow_deadlines()

        ceremony.refresh_from_db()
        self.assertIsNotNone(ceremony.reminder_sent_at)
        self.assertEqual(
            Notification.objects.filter(
                kind=Notification.Kind.CEREMONY_REMINDER,
                event_key__contains=f"ceremony:{ceremony.pk}:reminder",
            ).count(),
            2,
        )
        reminder_messages = [
            message for message in mail.outbox if "coming up" in message.subject.lower() or "upcoming" in message.subject.lower()
        ]
        self.assertEqual(len(reminder_messages), 2)

    def test_cancelled_ceremony_preserves_deposit_and_allows_refund_disposition(self):
        ceremony = self.create_ceremony(deposit=Decimal("150.00"))
        self.client.post(
            reverse("record_payment", args=[ceremony.public_id, Payment.PaymentType.DEPOSIT]),
            {"received_amount": "150.00", "provider_reference": "DEP-002"},
        )

        response = self.client.post(
            reverse("record_outcome", args=[ceremony.public_id, "cancel"]),
            {
                "outcome_reason": Ceremony.OutcomeReason.CUSTOMER_CANCELLED,
                "outcome_notes": "Customer cancelled due to an emergency.",
            },
        )
        self.assertRedirects(response, reverse("ceremonies"))
        ceremony.refresh_from_db()
        self.assertEqual(ceremony.status, Ceremony.Status.CANCELLED)
        self.assertEqual(ceremony.deposit_payment.status, Payment.Status.PAID)

        response = self.client.post(
            reverse(
                "update_payment_status",
                args=[ceremony.public_id, Payment.PaymentType.DEPOSIT, Payment.Status.REFUNDED],
            ),
            {"notes": "Refund REF-002 issued."},
        )
        self.assertRedirects(response, reverse("ceremony_detail", args=[ceremony.public_id]))
        ceremony.refresh_from_db()
        self.assertEqual(ceremony.status, Ceremony.Status.CANCELLED)
        self.assertEqual(ceremony.deposit_payment.status, Payment.Status.REFUNDED)

    def test_unaccepted_quote_expires_without_creating_ceremony(self):
        quote = QuoteRequest.objects.create(
            customer_name="Aster Bekele",
            email="aster@example.com",
            phone="555-0101",
            event_type=QuoteRequest.EventType.HOME,
            event_date=date(2026, 8, 12),
            location="Silver Spring, MD",
            guest_count=24,
            quoted_amount=Decimal("450.00"),
            status=QuoteRequest.Status.QUOTED,
            quote_expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )

        self.client.get(reverse("quote_review", args=[quote.public_id]))

        quote.refresh_from_db()
        self.assertEqual(quote.status, QuoteRequest.Status.EXPIRED)
        self.assertFalse(hasattr(quote, "ceremony"))

    def test_quote_cannot_be_sent_without_partner_capacity_hold(self):
        quote = QuoteRequest.objects.create(
            customer_name="Aster Bekele", email="aster@example.com", phone="555-0101",
            event_type=QuoteRequest.EventType.HOME, event_date=date(2026, 8, 12),
            event_time=time(15, 30), location="Silver Spring, MD", guest_count=24,
        )
        response = self.client.post(
            reverse("manage_quote", args=[quote.public_id]),
            {
                "event_time": "15:30", "estimated_duration_minutes": "120",
                "setup_buffer_minutes": "30", "cleanup_buffer_minutes": "30",
                "quoted_amount": "450.00", "deposit_amount": "150.00",
                "quote_notes": "Prepared quote.", "action": "send",
            },
        )
        quote.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(quote.status, QuoteRequest.Status.QUOTED)
        self.assertContains(response, "A partner must accept this time")
        self.assertEqual(len(mail.outbox), 0)

    def test_capacity_conflicts_use_time_windows_not_whole_dates(self):
        first = QuoteRequest.objects.create(
            customer_name="First", email="first@example.com", phone="1",
            event_type=QuoteRequest.EventType.HOME, event_date=date(2026, 8, 12),
            event_time=time(15, 30), location="Silver Spring, MD", guest_count=10,
        )
        hold = self.create_capacity_hold(first)
        overlapping = QuoteRequest.objects.create(
            customer_name="Second", email="second@example.com", phone="2",
            event_type=QuoteRequest.EventType.HOME, event_date=first.event_date,
            event_time=time(17, 30), location="Silver Spring, MD", guest_count=10,
        )
        separate = QuoteRequest.objects.create(
            customer_name="Third", email="third@example.com", phone="3",
            event_type=QuoteRequest.EventType.HOME, event_date=first.event_date,
            event_time=time(18, 30), location="Silver Spring, MD", guest_count=10,
        )
        self.assertIn("overlapping reservation", partner_conflict_reason(hold.partner, overlapping))
        self.assertEqual(partner_conflict_reason(hold.partner, separate), "")

    def test_partner_acceptance_converts_to_task_after_deposit(self):
        partner_user = get_user_model().objects.create_user(
            email="available@example.com", password="partner-password"
        )
        partner = Partner.objects.create(
            user=partner_user, name="Available Ambassador", contact_name="Almaz",
            email=partner_user.email, phone="555-0123", service_area="Silver Spring, MD",
            partner_type=Partner.PartnerType.INDIVIDUAL, food_permit_verified=True,
            insurance_verified=True, cultural_training_verified=True,
        )
        quote = QuoteRequest.objects.create(
            customer_name="Aster", email="aster@example.com", phone="555-0101",
            event_type=QuoteRequest.EventType.HOME, event_date=date(2026, 8, 12),
            event_time=time(15, 30), location="Silver Spring, MD", guest_count=24,
            quoted_amount=Decimal("450.00"), deposit_amount=Decimal("150.00"),
            quote_expires_at=timezone.now() + timezone.timedelta(days=2),
            status=QuoteRequest.Status.QUOTED,
        )
        offer = AvailabilityOffer.objects.create(
            quote=quote, partner=partner,
            expires_at=timezone.now() + timezone.timedelta(hours=24),
            created_by=self.staff_user,
        )
        self.client.force_login(partner_user)
        self.client.post(reverse("partner_offer_response", args=[offer.pk, "accept"]))
        hold = CapacityHold.objects.get(offer=offer)
        self.assertEqual(hold.status, CapacityHold.Status.TEMPORARY)

        self.client.post(
            reverse("quote_decision", args=[quote.public_id, "accept"]),
            {"policy_consent": "on"},
        )
        hold.refresh_from_db()
        self.assertEqual(hold.status, CapacityHold.Status.CONFIRMED)
        ceremony = quote.ceremony
        self.client.force_login(self.staff_user)
        self.client.post(
            reverse("record_payment", args=[ceremony.public_id, Payment.PaymentType.DEPOSIT]),
            {"received_amount": "150.00", "provider_reference": "DEP-HOLD"},
        )
        ceremony.refresh_from_db()
        hold.refresh_from_db()
        self.assertEqual(ceremony.assigned_partner, partner)
        self.assertEqual(ceremony.coverage_status, Ceremony.CoverageStatus.CONFIRMED)
        self.assertEqual(hold.status, CapacityHold.Status.CONVERTED)
        self.assertEqual(ceremony.partner_task.partner, partner)

    def test_expired_quote_releases_temporary_capacity(self):
        quote = QuoteRequest.objects.create(
            customer_name="Aster", email="aster@example.com", phone="555-0101",
            event_type=QuoteRequest.EventType.HOME, event_date=date(2026, 8, 12),
            event_time=time(15, 30), location="Silver Spring, MD", guest_count=24,
            quoted_amount=Decimal("450.00"), status=QuoteRequest.Status.QUOTED,
            quote_expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        hold = self.create_capacity_hold(quote)
        process_workflow_deadlines(self.staff_user)
        hold.refresh_from_db()
        self.assertEqual(hold.status, CapacityHold.Status.EXPIRED)
        self.assertTrue(Notification.objects.filter(
            kind=Notification.Kind.QUOTE_EXPIRED,
            event_key__contains=f"quote:{quote.pk}:expired",
        ).exists())

    def test_social_proof_sections_stay_hidden_until_content_exists(self):
        response = self.client.get(reverse("home"))

        self.assertNotContains(response, "Trusted by")
        self.assertNotContains(response, "Shared experiences")

    def test_staff_can_prepare_client_logo_in_content_workspace(self):
        response = self.client.get(reverse("content_library"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Content &amp; reviews")

        logo = SimpleUploadedFile("client-logo.png", b"image-bytes", content_type="image/png")
        response = self.client.post(
            reverse("content_library"),
            {
                "content_type": "organization",
                "organization-name": "Community University",
                "organization-logo": logo,
                "organization-alt_text": "Community University logo",
                "organization-website": "https://example.com",
                "organization-display_order": "1",
                "organization-active": "on",
            },
        )
        self.assertRedirects(response, reverse("content_library"))
        self.assertTrue(ClientOrganization.objects.filter(name="Community University").exists())
        self.assertContains(self.client.get(reverse("home")), "Trusted by")

    def test_partner_gallery_requires_staff_approval_before_homepage(self):
        partner_user = get_user_model().objects.create_user(
            email="gallery-partner@example.com", password="partner-password"
        )
        partner = Partner.objects.create(
            user=partner_user, name="Gallery Ambassador", contact_name="Aster",
            email=partner_user.email, phone="555-0150", service_area="Maryland",
        )
        self.client.force_login(partner_user)
        photo = SimpleUploadedFile("setup.jpg", b"photo-bytes", content_type="image/jpeg")
        response = self.client.post(
            reverse("partner_gallery"),
            {"image": photo, "alt_text": "Traditional ceremony setup", "caption": "A welcoming setup"},
        )
        self.assertRedirects(response, reverse("partner_gallery"))
        gallery_photo = PartnerGalleryPhoto.objects.get(partner=partner)
        self.assertEqual(gallery_photo.status, PartnerGalleryPhoto.Status.PENDING)
        self.assertTrue(Notification.objects.filter(
            recipient=self.staff_user,
            kind=Notification.Kind.GALLERY_UPLOADED,
        ).exists())
        self.assertNotContains(self.client.get(reverse("home")), "A welcoming setup")

        self.client.force_login(self.staff_user)
        self.client.post(
            reverse("moderate_community_content", args=["partner-photo", gallery_photo.pk, "approve"])
        )
        gallery_photo.refresh_from_db()
        self.assertEqual(gallery_photo.status, PartnerGalleryPhoto.Status.APPROVED)
        self.assertContains(self.client.get(reverse("home")), "A welcoming setup")

    def test_only_completed_customers_can_submit_verified_public_reviews(self):
        customer_user = get_user_model().objects.create_user(
            email="reviewer@example.com", password="customer-password"
        )
        customer = CustomerProfile.objects.create(
            user=customer_user, first_name="Mimi", last_name="Reviewer", phone="555-0160"
        )
        ceremony = self.create_ceremony()
        ceremony.quote.customer = customer
        ceremony.quote.save(update_fields=["customer", "updated_at"])
        ceremony.status = Ceremony.Status.COMPLETED
        ceremony.save(update_fields=["status", "updated_at"])
        self.client.force_login(customer_user)
        response = self.client.post(
            reverse("customer_submit_review", args=[ceremony.public_id]),
            {
                "rating": "5", "title": "A memorable gathering",
                "review": "Our guests stayed together and talked long after the coffee was served.",
                "permission_to_publish": "on",
            },
        )
        self.assertRedirects(response, reverse("customer_quote_detail", args=[ceremony.quote.public_id]))
        review = CustomerReview.objects.get(ceremony=ceremony)
        self.assertEqual(review.status, CustomerReview.Status.PENDING)
        self.assertTrue(Notification.objects.filter(
            recipient=self.staff_user,
            kind=Notification.Kind.REVIEW_SUBMITTED,
        ).exists())
        self.assertNotContains(self.client.get(reverse("home")), "A memorable gathering")

        self.client.force_login(self.staff_user)
        self.client.post(reverse("moderate_community_content", args=["review", review.pk, "approve"]))
        self.assertContains(self.client.get(reverse("home")), "A memorable gathering")

# Create your tests here.
