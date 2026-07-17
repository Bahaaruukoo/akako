import django.db.models.deletion
from django.db import migrations, models
import django.utils.timezone


def create_placeholder_policies(apps, schema_editor):
    PolicyDocument = apps.get_model("bookings", "PolicyDocument")
    policies = [
        ("privacy", "Privacy Notice"),
        ("service", "Ceremony Service Terms"),
        ("payment", "Payment and Deposit Policy"),
        ("cancellation", "Cancellation, Rescheduling, and Refund Policy"),
    ]
    for policy_type, title in policies:
        PolicyDocument.objects.create(
            policy_type=policy_type,
            title=title,
            version="draft-1",
            content=(
                "PLACEHOLDER POLICY CONTENT\n\n"
                "This document is ready for Akako House's approved policy language. "
                "Replace this placeholder in System Admin before relying on it for customer agreements."
            ),
            is_active=True,
        )


class Migration(migrations.Migration):
    dependencies = [("bookings", "0017_alter_clientorganization_logo_alter_eventphoto_image_and_more")]

    operations = [
        migrations.CreateModel(
            name="PolicyDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("policy_type", models.CharField(choices=[("privacy", "Privacy notice"), ("service", "Service terms"), ("payment", "Payment and deposit policy"), ("cancellation", "Cancellation, rescheduling, and refund policy")], max_length=24)),
                ("title", models.CharField(max_length=160)),
                ("version", models.CharField(max_length=32)),
                ("content", models.TextField()),
                ("effective_date", models.DateField(default=django.utils.timezone.localdate)),
                ("is_active", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["policy_type", "-effective_date", "-created_at"]},
        ),
        migrations.CreateModel(
            name="PolicyAcceptance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("policy_title", models.CharField(max_length=160)),
                ("policy_version", models.CharField(max_length=32)),
                ("policy_content", models.TextField()),
                ("accepted_name", models.CharField(max_length=120)),
                ("accepted_email", models.EmailField(max_length=254)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("accepted_at", models.DateTimeField(auto_now_add=True)),
                ("policy", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="acceptances", to="bookings.policydocument")),
                ("quote", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="policy_acceptances", to="bookings.quoterequest")),
            ],
            options={"ordering": ["accepted_at"]},
        ),
        migrations.AddConstraint(
            model_name="policydocument",
            constraint=models.UniqueConstraint(fields=("policy_type", "version"), name="unique_policy_type_version"),
        ),
        migrations.AddConstraint(
            model_name="policyacceptance",
            constraint=models.UniqueConstraint(fields=("quote", "policy"), name="unique_quote_policy_acceptance"),
        ),
        migrations.RunPython(create_placeholder_policies, migrations.RunPython.noop),
    ]
