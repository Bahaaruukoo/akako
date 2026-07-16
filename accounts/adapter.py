from allauth.account.adapter import DefaultAccountAdapter
from django.urls import reverse


class BunaGoAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        # Public self-service signup is currently limited to the complete
        # partner application. Customer signup can be enabled here later.
        return getattr(request.resolver_match, "url_name", "") in {
            "partner_registration",
            "customer_registration",
        }

    def get_login_redirect_url(self, request):
        user = request.user
        if user.is_staff:
            return reverse("operations_dashboard")
        if hasattr(user, "partner_profile"):
            return reverse("partner_dashboard")
        if hasattr(user, "customer_profile"):
            return reverse("customer_dashboard")
        return reverse("home")
