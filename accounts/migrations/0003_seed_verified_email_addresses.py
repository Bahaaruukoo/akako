from django.db import migrations


def seed_verified_email_addresses(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    EmailAddress = apps.get_model("account", "EmailAddress")
    for user in User.objects.exclude(email=""):
        EmailAddress.objects.update_or_create(
            user_id=user.pk,
            email=user.email.lower(),
            defaults={"verified": True, "primary": True},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_email_identity"),
        ("account", "0009_emailaddress_unique_primary_email"),
    ]

    operations = [
        migrations.RunPython(seed_verified_email_addresses, migrations.RunPython.noop),
    ]
