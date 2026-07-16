from allauth.account.signals import email_changed
from django.dispatch import receiver


@receiver(email_changed)
def sync_profile_email(sender, request, user, to_email_address, **kwargs):
    if hasattr(user, "partner_profile"):
        partner = user.partner_profile
        partner.email = to_email_address.email
        partner.save(update_fields=["email"])
    if hasattr(user, "customer_profile"):
        from bookings.services import claim_customer_records

        claim_customer_records(user.customer_profile)
