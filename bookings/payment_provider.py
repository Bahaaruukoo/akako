from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _stripe():
    try:
        import stripe
    except ImportError as exc:
        raise ImproperlyConfigured("Install the stripe package before enabling Stripe checkout.") from exc
    if not settings.STRIPE_SECRET_KEY:
        raise ImproperlyConfigured("STRIPE_SECRET_KEY is not configured.")
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def create_checkout_session(*, ceremony, choice, amount, success_url, cancel_url):
    if settings.PAYMENT_PROVIDER != "stripe":
        raise ImproperlyConfigured("Online checkout is disabled. Set PAYMENT_PROVIDER=stripe.")
    stripe = _stripe()
    label = {
        "deposit": "Akako House ceremony deposit",
        "final": "Akako House final ceremony balance",
        "full": "Akako House ceremony — paid in full",
    }[choice]
    session = stripe.checkout.Session.create(
        mode="payment",
        customer_email=ceremony.quote.email,
        client_reference_id=str(ceremony.public_id),
        metadata={
            "ceremony_public_id": str(ceremony.public_id),
            "payment_choice": choice,
        },
        line_items=[
            {
                "price_data": {
                    "currency": settings.PAYMENT_CURRENCY,
                    "product_data": {"name": label},
                    "unit_amount": int(Decimal(amount) * 100),
                },
                "quantity": 1,
            }
        ],
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return {"id": session.id, "url": session.url, "currency": settings.PAYMENT_CURRENCY}


def construct_webhook_event(payload, signature):
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise ImproperlyConfigured("STRIPE_WEBHOOK_SECRET is not configured.")
    stripe = _stripe()
    return stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET)
