from datetime import date

from django.db import migrations


VERSION = "2026-07-24"
EFFECTIVE_DATE = date(2026, 7, 24)

OLD_SECTION = """5. Heat, venue, and safety rules

Akako House uses only heat sources and preparation methods allowed by the venue and applicable authorities. A traditional appearance does not justify prohibited flame, smoke, charcoal, fuel, or unsafe equipment.

Akako House may substitute an electric, reduced-smoke, or no-heat presentation method when required by venue rules, weather, safety, law, equipment condition, or the event risk assessment. A safety-based substitution that reasonably preserves the ceremony experience is not a failure to perform.

Akako House may pause, modify, relocate, or stop service if conditions present an unreasonable risk to guests, Cultural Ambassadors, staff, property, or cultural integrity. This includes unsafe crowding, interference with the hot zone, severe weather, harassment, illegal activity, or instructions that conflict with venue or safety requirements."""

NEW_SECTION = """5. Electric coffee roasting, frankincense, and venue safety

The ceremony ordinarily uses an electric stove for roasting coffee and preparing the beverage. When requested, included in the accepted quote, and permitted by the venue, a small charcoal disc may be used to burn frankincense as part of the traditional presentation.

Coffee roasting, steam, and frankincense may produce aromas or smoke that affect sensitive building systems. Before the ceremony, the customer must inform the venue about these activities, confirm that they are permitted in the designated ceremony area, and disclose applicable restrictions communicated by the venue. Customer approval does not replace authorization required from the venue or applicable authorities.

If the venue prohibits roasting or incense, authorization is unclear, or Akako House determines that conditions are unsuitable, Akako House may use a reduced-smoke method, pre-roasted coffee, an incense-free presentation, or another reasonable alternative. A safety-based adjustment that reasonably preserves the ceremony experience is not a failure to provide the ceremony and does not, by itself, entitle the customer to a refund.

No customer, guest, Cultural Ambassador, or other unauthorized person may cover, disconnect, disable, move, obstruct, reset, or tamper with a smoke detector, fire alarm, sprinkler, ventilation control, or fire-suppression system. Only personnel legally authorized and qualified by the venue or applicable authority may operate, isolate, reset, or modify those systems.

Akako House may pause, relocate, modify, or stop roasting, frankincense use, or other service when an alarm activates, smoke accumulates unsafely, the venue objects, authorization is unclear, or conditions create an unreasonable risk to people or property. Everyone must follow venue instructions and applicable emergency procedures if an alarm or fire-suppression system activates.

Responsibility for false-alarm charges, emergency-response fees, cleanup expenses, property damage, or related costs will be determined according to the cause, the information and restrictions disclosed before the ceremony, each party's acts or omissions, and applicable law. The customer is responsible to the extent costs result from inaccurate venue information, undisclosed restrictions known to the customer, missing permissions the customer agreed to obtain, or instructions contrary to the agreed safety plan. Akako House remains responsible to the extent costs or losses result from its negligence, willful misconduct, or unauthorized departure from its procedures. Nothing in this section limits liability that cannot lawfully be limited."""


def publish_service_terms(apps, schema_editor):
    PolicyDocument = apps.get_model("bookings", "PolicyDocument")
    previous = PolicyDocument.objects.filter(policy_type="service", is_active=True).first()
    if previous is None:
        raise RuntimeError("No active Ceremony Service Terms were found.")
    if OLD_SECTION in previous.content:
        amended_content = previous.content.replace(OLD_SECTION, NEW_SECTION, 1)
    elif NEW_SECTION in previous.content:
        amended_content = previous.content
    else:
        raise RuntimeError("Unable to locate the service-terms safety section.")
    policy, _created = PolicyDocument.objects.update_or_create(
        policy_type="service",
        version=VERSION,
        defaults={
            "title": previous.title,
            "content": amended_content,
            "effective_date": EFFECTIVE_DATE,
            "is_active": True,
        },
    )
    PolicyDocument.objects.filter(policy_type="service", is_active=True).exclude(
        pk=policy.pk
    ).update(is_active=False)


def unpublish_service_terms(apps, schema_editor):
    PolicyDocument = apps.get_model("bookings", "PolicyDocument")
    PolicyDocument.objects.filter(policy_type="service", version=VERSION).update(
        is_active=False
    )
    previous = (
        PolicyDocument.objects.filter(policy_type="service")
        .exclude(version=VERSION)
        .order_by("-effective_date", "-created_at")
        .first()
    )
    if previous:
        previous.is_active = True
        previous.save(update_fields=["is_active"])


class Migration(migrations.Migration):
    dependencies = [("bookings", "0022_publish_customer_policies_20260723")]

    operations = [migrations.RunPython(publish_service_terms, unpublish_service_terms)]