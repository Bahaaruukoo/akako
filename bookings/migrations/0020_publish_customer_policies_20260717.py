import re
from datetime import date
from pathlib import Path

from django.db import migrations


VERSION = "2026-07-17"
EFFECTIVE_DATE = date(2026, 7, 17)

POLICIES = (
    ("service", "Ceremony Service Terms", "# 1. Ceremony Service Terms", "# 2. Payment and Deposit Policy"),
    ("payment", "Payment and Deposit Policy", "# 2. Payment and Deposit Policy", "# 3. Cancellation, Rescheduling, and Refund Policy"),
    ("cancellation", "Cancellation, Rescheduling, and Refund Policy", "# 3. Cancellation, Rescheduling, and Refund Policy", "# 4. Privacy Notice"),
    ("privacy", "Privacy Notice", "# 4. Privacy Notice", "# Owner Approval Checklist"),
)


def _plain_text(markdown):
    lines = []
    for line in markdown.strip().splitlines():
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def publish_policies(apps, schema_editor):
    PolicyDocument = apps.get_model("bookings", "PolicyDocument")
    source_path = Path(__file__).resolve().parents[2] / "docs" / "akako_house_customer_policies_draft.md"
    source = source_path.read_text(encoding="utf-8")

    for policy_type, title, start_marker, end_marker in POLICIES:
        if start_marker not in source or end_marker not in source:
            raise RuntimeError(f"Unable to locate approved policy section: {title}")
        section = source.split(start_marker, 1)[1].split(end_marker, 1)[0]
        content = _plain_text(section)
        if not content or "PLACEHOLDER POLICY CONTENT" in content:
            raise RuntimeError(f"Approved policy content is invalid: {title}")

        PolicyDocument.objects.filter(policy_type=policy_type, is_active=True).update(
            is_active=False
        )
        policy, _created = PolicyDocument.objects.update_or_create(
            policy_type=policy_type,
            version=VERSION,
            defaults={
                "title": title,
                "content": content,
                "effective_date": EFFECTIVE_DATE,
                "is_active": True,
            },
        )
        PolicyDocument.objects.filter(policy_type=policy_type, is_active=True).exclude(
            pk=policy.pk
        ).update(is_active=False)


def unpublish_policies(apps, schema_editor):
    PolicyDocument = apps.get_model("bookings", "PolicyDocument")
    for policy_type, _title, _start_marker, _end_marker in POLICIES:
        PolicyDocument.objects.filter(
            policy_type=policy_type, version=VERSION
        ).update(is_active=False)
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
    dependencies = [("bookings", "0019_notification_lifecycle_kinds")]

    operations = [migrations.RunPython(publish_policies, unpublish_policies)]
