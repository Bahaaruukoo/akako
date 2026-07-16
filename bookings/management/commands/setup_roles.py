from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand


ROLE_PERMISSIONS = {
    "Operations Manager": [
        "view_quoterequest", "change_quoterequest",
        "view_ceremony", "change_ceremony",
        "view_payment", "change_payment",
        "view_paymentcheckout",
        "view_partner", "change_partner", "add_partner",
        "view_customerprofile", "change_customerprofile",
        "view_customercancellationrequest", "change_customercancellationrequest",
        "view_statushistory",
    ],
    "Quote Specialist": [
        "view_quoterequest", "change_quoterequest", "view_customerprofile"
    ],
    "Ceremony Coordinator": [
        "view_ceremony", "change_ceremony",
        "view_partner", "view_quoterequest",
        "view_customerprofile",
        "view_customercancellationrequest", "change_customercancellationrequest",
    ],
    "Finance Manager": [
        "view_ceremony", "view_quoterequest",
        "view_payment", "change_payment", "view_paymentcheckout",
    ],
}


class Command(BaseCommand):
    help = "Create or refresh the standard Akako House staff permission groups."

    def handle(self, *args, **options):
        available = {
            permission.codename: permission
            for permission in Permission.objects.filter(content_type__app_label="bookings")
        }
        for role_name, codenames in ROLE_PERMISSIONS.items():
            group, _created = Group.objects.get_or_create(name=role_name)
            group.permissions.set(
                [available[codename] for codename in codenames if codename in available]
            )
            self.stdout.write(self.style.SUCCESS(f"Configured role: {role_name}"))
