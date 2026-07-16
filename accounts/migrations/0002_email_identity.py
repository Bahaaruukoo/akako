import accounts.models
from django.db import migrations, models


def prepare_email_identities(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    used = set()
    for user in User.objects.order_by("id"):
        base = (user.email or user.username or f"user-{user.pk}@bunago.local").strip().lower()
        if "@" not in base:
            base = f"{base}@bunago.local"
        local, domain = base.rsplit("@", 1)
        candidate = base
        counter = 1
        while candidate in used or User.objects.exclude(pk=user.pk).filter(email__iexact=candidate).exists():
            counter += 1
            candidate = f"{local}+{user.pk}-{counter}@{domain}"
        user.email = candidate
        user.save(update_fields=["email"])
        used.add(candidate)

    ContentType = apps.get_model("contenttypes", "ContentType")
    old_type = ContentType.objects.filter(app_label="auth", model="user").first()
    new_type = ContentType.objects.filter(app_label="accounts", model="user").first()
    if old_type and new_type:
        Permission = apps.get_model("auth", "Permission")
        Permission.objects.filter(content_type=new_type).delete()
        new_type.delete()
    if old_type:
        old_type.app_label = "accounts"
        old_type.save(update_fields=["app_label"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.RunPython(prepare_email_identities, migrations.RunPython.noop),
        migrations.RemoveField(model_name="user", name="username"),
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(max_length=254, unique=True, verbose_name="email address"),
        ),
        migrations.AlterModelManagers(
            name="user",
            managers=[("objects", accounts.models.UserManager())],
        ),
    ]
