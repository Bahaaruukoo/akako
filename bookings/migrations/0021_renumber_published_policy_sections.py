import re

from django.db import migrations


VERSION = "2026-07-17"


def renumber_sections(apps, schema_editor):
    PolicyDocument = apps.get_model("bookings", "PolicyDocument")
    policies = PolicyDocument.objects.filter(version=VERSION)
    for policy in policies:
        policy.content = re.sub(
            r"(?m)^[1-4]\.(\d+)\s+",
            lambda match: f"{match.group(1)}. ",
            policy.content,
        )
        policy.save(update_fields=["content", "updated_at"])


def restore_master_numbering(apps, schema_editor):
    PolicyDocument = apps.get_model("bookings", "PolicyDocument")
    prefixes = {
        "service": "1",
        "payment": "2",
        "cancellation": "3",
        "privacy": "4",
    }
    for policy in PolicyDocument.objects.filter(version=VERSION):
        prefix = prefixes.get(policy.policy_type)
        if not prefix:
            continue
        policy.content = re.sub(
            r"(?m)^(\d+)\.\s+",
            lambda match: f"{prefix}.{match.group(1)} ",
            policy.content,
        )
        policy.save(update_fields=["content", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [("bookings", "0020_publish_customer_policies_20260717")]

    operations = [migrations.RunPython(renumber_sections, restore_master_numbering)]
