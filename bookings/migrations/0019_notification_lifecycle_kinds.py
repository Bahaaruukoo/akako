from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bookings", "0018_policydocument_policyacceptance")]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="kind",
            field=models.CharField(
                choices=[
                    ("quote_new", "New quote request"),
                    ("quote_accepted", "Quote accepted"),
                    ("quote_declined", "Quote declined"),
                    ("quote_expired", "Quote expired"),
                    ("payment_received", "Payment received"),
                    ("payment_due", "Payment approaching"),
                    ("payment_overdue", "Payment overdue"),
                    ("partner_assigned", "Partner assignment"),
                    ("partner_accepted", "Partner acceptance"),
                    ("capacity_hold_created", "Temporary reservation created"),
                    ("capacity_hold_released", "Temporary reservation released"),
                    ("ceremony_reminder", "Ceremony reminder"),
                    ("ceremony_completed", "Ceremony completed"),
                    ("document_expiring", "Document expiring"),
                    ("document_expired", "Document expired"),
                    ("gallery_uploaded", "Gallery upload"),
                    ("review_submitted", "Review submitted"),
                ],
                max_length=32,
            ),
        ),
    ]
