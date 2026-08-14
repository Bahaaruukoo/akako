from django.db import migrations


def replace_legacy_branding(apps, schema_editor):
    QuoteRequest = apps.get_model("bookings", "QuoteRequest")
    for quote in QuoteRequest.objects.filter(quote_notes__icontains="BunaGo").iterator():
        updated = quote.quote_notes.replace("BunaGo", "Akako House").replace("Bunago", "Akako House").replace("bunago", "Akako House")
        if updated != quote.quote_notes:
            QuoteRequest.objects.filter(pk=quote.pk).update(quote_notes=updated)


class Migration(migrations.Migration):
    dependencies = [("bookings", "0024_invoice_invoicetransaction")]

    operations = [migrations.RunPython(replace_legacy_branding, migrations.RunPython.noop)]