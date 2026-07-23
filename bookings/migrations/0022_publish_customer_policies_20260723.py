from datetime import date

from django.db import migrations


VERSION = "2026-07-23"
EFFECTIVE_DATE = date(2026, 7, 23)

REPLACEMENTS = (
    (
        "It is delivered by Akako House personnel or a trained Cultural Ambassador "
        "from our approved local partner network under Akako House coordination.",
        "It may be fulfilled by qualified personnel or independent service providers "
        "operating under Akako House coordination and service standards.",
    ),
    ("guests, staff, partners, property, or cultural integrity", "guests, Cultural Ambassadors, staff, property, or cultural integrity"),
    ("A temporary partner reservation is not a confirmed assignment.", "A temporary service reservation is not a confirmed Cultural Ambassador assignment."),
    ("the event record, customer information, partner account, and other available evidence", "the event record, customer information, Cultural Ambassador assignment records, and other available evidence"),
    ("planning, partner coordination, and committed event costs", "planning, service coordination, and committed event costs"),
    ("an individual partner or employee", "a Cultural Ambassador or staff member"),
    ("Akako House personnel and partners may not pressure", "Akako House Cultural Ambassadors and personnel may not pressure"),
    ("Pricing, service area, partner availability, and policy versions", "Pricing, service area, service availability, and policy versions"),
    ("customer and partner accounts", "customer accounts, service-provider workspaces"),
    ("Separate workforce and partner notices", "Separate workforce and service-provider notices"),
    ("partner availability, assignments, attendance", "service availability, assignments, attendance"),
    ("scheduling, partner capacity, venue", "scheduling, service capacity, venue"),
    ("customer or partner accounts", "customer or service-provider accounts"),
    ("Assigned Cultural Ambassadors and operational partners,", "Authorized Cultural Ambassadors and service providers,"),
)


def publish_policies(apps, schema_editor):
    PolicyDocument = apps.get_model("bookings", "PolicyDocument")
    active_policies = list(PolicyDocument.objects.filter(is_active=True))
    for previous in active_policies:
        content = previous.content
        for old, new in REPLACEMENTS:
            content = content.replace(old, new)
        PolicyDocument.objects.update_or_create(
            policy_type=previous.policy_type,
            version=VERSION,
            defaults={
                "title": previous.title,
                "content": content,
                "effective_date": EFFECTIVE_DATE,
                "is_active": True,
            },
        )
        PolicyDocument.objects.filter(pk=previous.pk).update(is_active=False)


def unpublish_policies(apps, schema_editor):
    PolicyDocument = apps.get_model("bookings", "PolicyDocument")
    PolicyDocument.objects.filter(version=VERSION).update(is_active=False)
    for policy_type in ("privacy", "service", "payment", "cancellation"):
        previous = (
            PolicyDocument.objects.filter(policy_type=policy_type)
            .exclude(version=VERSION)
            .order_by("-effective_date", "-created_at")
            .first()
        )
        if previous:
            previous.is_active = True
            previous.save(update_fields=["is_active"])


class Migration(migrations.Migration):
    dependencies = [("bookings", "0021_renumber_published_policy_sections")]

    operations = [migrations.RunPython(publish_policies, unpublish_policies)]
