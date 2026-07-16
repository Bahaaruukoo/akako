from allauth.account.signals import email_confirmed
from django.dispatch import receiver

from .services import claim_customer_records


@receiver(email_confirmed)
def claim_requests_after_email_confirmation(sender, request, email_address, **kwargs):
    user = email_address.user
    if hasattr(user, "customer_profile"):
        claim_customer_records(user.customer_profile)
