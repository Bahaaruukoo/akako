from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import InvoiceVersion, PaymentReceipt


def money(value):
    return f"${value:,.2f}"


def payment_reference_notice(invoice):
    return (
        f"<b>Required payment reference: {escape(invoice.number)}</b><br/>"
        "For Zelle or bank transfers, include this invoice number in the memo or reference field "
        "so Akako House can identify and apply your payment correctly."
    )


def build_invoice_pdf(invoice):
    buffer = BytesIO()
    navy, gold, cream, gray = colors.HexColor("#18323D"), colors.HexColor("#C9954A"), colors.HexColor("#F7F3EC"), colors.HexColor("#606B70")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="BrandX", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=22, textColor=navy))
    styles.add(ParagraphStyle(name="BodyX", parent=styles["BodyText"], fontSize=9, leading=13, textColor=navy))
    styles.add(ParagraphStyle(name="RightX", parent=styles["BodyText"], fontSize=9, leading=13, alignment=TA_RIGHT, textColor=navy))
    styles.add(ParagraphStyle(name="LabelX", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=8, textColor=gold))
    p = lambda text, style="BodyX": Paragraph(str(text), styles[style])
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=.65*inch, rightMargin=.65*inch, topMargin=.6*inch, bottomMargin=.65*inch, title=f"Invoice {invoice.number}")
    quote = invoice.ceremony.quote
    story = [Table([[p("AKAKO HOUSE", "BrandX"), p("INVOICE", "RightX")], [p("Ethiopian coffee ceremony service"), p(f"Invoice: {invoice.number}<br/>Issue date: {invoice.issue_date:%B %d, %Y}<br/>Status: {invoice.get_status_display()}", "RightX")]], colWidths=[4.5*inch, 2.7*inch], style=[("VALIGN",(0,0),(-1,-1),"TOP"),("LINEBELOW",(0,-1),(-1,-1),1.5,gold),("BOTTOMPADDING",(0,-1),(-1,-1),12)]), Spacer(1,16)]
    bill_to = []
    if invoice.organization_name:
        bill_to.append(escape(invoice.organization_name))
    contact = invoice.billing_contact_name or invoice.customer_name
    if contact:
        bill_to.append(f"Attn: {escape(contact)}" if invoice.organization_name else escape(contact))
    bill_to.extend([escape(invoice.customer_email), escape(invoice.billing_address)])
    if invoice.purchase_order_number:
        bill_to.append(f"PO: {escape(invoice.purchase_order_number)}")
    story += [Table([[p("FROM", "LabelX"), p("BILL TO", "LabelX")], [p(f"{escape(settings.BUSINESS_LEGAL_NAME)}<br/>{escape(settings.BUSINESS_PRINCIPAL_ADDRESS)}<br/>support@akakohouse.com<br/>+1 (571) 715-8524"), p("<br/>".join(filter(None, bill_to)))]], colWidths=[3.6*inch,3.6*inch], style=[("VALIGN",(0,0),(-1,-1),"TOP")]), Spacer(1,18)]
    event = f"Event: {escape(quote.get_event_type_display())}<br/>Date: {quote.event_date:%B %d, %Y}<br/>Location: {escape(quote.location)}<br/>Guests: {quote.guest_count}"
    story += [Table([[p("EVENT DETAILS", "LabelX")],[p(event)]], colWidths=[7.2*inch], style=[("BACKGROUND",(0,0),(-1,-1),cream),("BOX",(0,0),(-1,-1),.75,gold),("LEFTPADDING",(0,0),(-1,-1),10),("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8)]), Spacer(1,18)]
    story += [Table([[p("DESCRIPTION", "LabelX"), p("AMOUNT", "LabelX")],[p(escape(invoice.description)), p(money(invoice.total_amount), "RightX")]], colWidths=[5.8*inch,1.4*inch], style=[("BACKGROUND",(0,0),(-1,0),navy),("BOX",(0,0),(-1,-1),.75,colors.HexColor("#D8DEE0")),("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),("TOPPADDING",(0,0),(-1,-1),9),("BOTTOMPADDING",(0,0),(-1,-1),9)]), Spacer(1,12)]
    rows = [[p("Total"), p(money(invoice.total_amount), "RightX")], [p("Payments received"), p(money(invoice.amount_paid), "RightX")], [p("Balance due"), p(money(invoice.balance_due), "RightX")]]
    if invoice.first_payment_amount and not invoice.amount_paid:
        rows.insert(1, [p("First payment due"), p(money(invoice.first_payment_amount), "RightX")])
    story += [Table(rows, colWidths=[3.1*inch,1.5*inch], hAlign="RIGHT", style=[("LINEABOVE",(0,0),(-1,0),.75,gold),("BACKGROUND",(0,-1),(-1,-1),cream),("BOX",(0,-1),(-1,-1),.75,gold),("LEFTPADDING",(0,0),(-1,-1),9),("RIGHTPADDING",(0,0),(-1,-1),9),("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7)]), Spacer(1,18)]
    due = f"First payment due: {invoice.first_payment_due_date or 'On receipt'}<br/>Remaining balance due: {invoice.balance_due_date or 'As agreed'}"
    reference_notice = payment_reference_notice(invoice)
    story += [p("PAYMENT DETAILS", "LabelX"), p(due), Spacer(1,6), Table([[p(reference_notice)]], colWidths=[7.2*inch], style=[("BACKGROUND",(0,0),(-1,-1),cream),("BOX",(0,0),(-1,-1),1,gold),("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8)]), Spacer(1,7), p(escape(invoice.payment_instructions or "Contact Akako House for payment instructions.").replace("\n", "<br/>")), Spacer(1,10)]
    if invoice.notes: story += [p("NOTES", "LabelX"), p(escape(invoice.notes).replace("\n", "<br/>"))]
    doc.build(story)
    return buffer.getvalue()


def invoice_revision_checksum(invoice):
    transaction_snapshot = tuple(
        invoice.transactions.order_by("pk").values_list(
            "amount", "received_on", "method", "reference", "notes"
        )
    )
    snapshot = (
        invoice.number,
        invoice.status,
        invoice.customer_name,
        invoice.customer_email,
        invoice.billing_type,
        invoice.organization_name,
        invoice.billing_contact_name,
        invoice.purchase_order_number,
        invoice.billing_address,
        invoice.description,
        invoice.total_amount,
        invoice.first_payment_amount,
        invoice.issue_date,
        invoice.first_payment_due_date,
        invoice.balance_due_date,
        invoice.notes,
        invoice.payment_instructions,
        transaction_snapshot,
    )
    return sha256(repr(snapshot).encode("utf-8")).hexdigest()

def generate_and_store_invoice(invoice, *, generated_by=None):
    """Preserve a material invoice revision and point the invoice at its latest PDF."""
    content = build_invoice_pdf(invoice)
    checksum = invoice_revision_checksum(invoice)
    latest = invoice.versions.order_by("-revision").first()
    if latest and latest.checksum == checksum and latest.pdf_file.storage.exists(latest.pdf_file.name):
        version = latest
    else:
        revision = (latest.revision if latest else 0) + 1
        version = InvoiceVersion(invoice=invoice, revision=revision, checksum=checksum, generated_by=generated_by)
        version.pdf_file.save(f"{invoice.number}-v{revision}.pdf", ContentFile(content), save=False)
        version.save()
    invoice.pdf_file.name = version.pdf_file.name
    invoice.pdf_generated_at = timezone.now()
    invoice.save(update_fields=["pdf_file", "pdf_generated_at", "updated_at"])
    return content


def send_invoice_email(invoice, *, recipient, subject, body):
    content = generate_and_store_invoice(invoice)
    message = EmailMessage(subject, body, None, [recipient])
    message.attach(f"{invoice.number}.pdf", content, "application/pdf")
    message.send(fail_silently=False)
    invoice.last_emailed_to = recipient
    invoice.last_emailed_at = timezone.now()
    invoice.email_subject = subject
    invoice.email_body = body
    invoice.save(update_fields=["last_emailed_to", "last_emailed_at", "email_subject", "email_body", "updated_at"])
    invoice.refresh_status()

def payment_receipt_number(transaction):
    return f"AKH-R-{transaction.pk:05d}"


def build_payment_receipt_pdf(invoice, transaction, *, booking_status):
    buffer = BytesIO()
    navy = colors.HexColor("#18323D")
    gold = colors.HexColor("#C9954A")
    cream = colors.HexColor("#F7F3EC")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReceiptBrand", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=22, textColor=navy))
    styles.add(ParagraphStyle(name="ReceiptBody", parent=styles["BodyText"], fontSize=10, leading=15, textColor=navy))
    styles.add(ParagraphStyle(name="ReceiptRight", parent=styles["BodyText"], fontSize=10, leading=15, alignment=TA_RIGHT, textColor=navy))
    styles.add(ParagraphStyle(name="ReceiptLabel", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=8, textColor=gold))
    p = lambda value, style="ReceiptBody": Paragraph(str(value), styles[style])
    receipt_number = payment_receipt_number(transaction)
    payment_state = "PAID IN FULL" if invoice.balance_due == 0 else "PARTIAL PAYMENT"
    final_due = invoice.balance_due_date.strftime("%B %d, %Y") if invoice.balance_due_date else "As agreed"
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=.7*inch, rightMargin=.7*inch, topMargin=.65*inch, bottomMargin=.7*inch, title=f"Payment Receipt {receipt_number}")
    story = [
        Table(
            [[p("AKAKO HOUSE", "ReceiptBrand"), p("PAYMENT RECEIPT", "ReceiptRight")],
             [p(f"{escape(settings.BUSINESS_LEGAL_NAME)}<br/>{escape(settings.BUSINESS_PRINCIPAL_ADDRESS)}<br/>support@akakohouse.com<br/>+1 (571) 715-8524"),
              p(f"Receipt: {receipt_number}<br/>Invoice: {invoice.number}<br/>Issued: {timezone.localdate():%B %d, %Y}<br/><b>{payment_state}</b>", "ReceiptRight")]],
            colWidths=[4.4*inch, 2.6*inch],
            style=[("VALIGN", (0,0), (-1,-1), "TOP"), ("LINEBELOW", (0,-1), (-1,-1), 1.5, gold), ("BOTTOMPADDING", (0,-1), (-1,-1), 14)],
        ),
        Spacer(1, 18),
        p("RECEIVED FROM", "ReceiptLabel"),
        p(f"{escape(invoice.customer_name)}<br/>{escape(invoice.customer_email)}"),
        Spacer(1, 16),
        Table(
            [[p("Amount received"), p(money(transaction.amount), "ReceiptRight")],
             [p("Payment method"), p(transaction.get_method_display(), "ReceiptRight")],
             [p("Payment date"), p(transaction.received_on.strftime("%B %d, %Y"), "ReceiptRight")],
             [p("Payment reference"), p(escape(transaction.reference or "Not provided"), "ReceiptRight")],
             [p("Total invoice"), p(money(invoice.total_amount), "ReceiptRight")],
             [p("Total payments received"), p(money(invoice.amount_paid), "ReceiptRight")],
             [p("Remaining balance"), p(money(invoice.balance_due), "ReceiptRight")],
             [p("Final due date"), p(final_due, "ReceiptRight")],
             [p("Booking status"), p(escape(booking_status), "ReceiptRight")]],
            colWidths=[3.8*inch, 3.2*inch],
            style=[("BOX", (0,0), (-1,-1), .75, gold), ("BACKGROUND", (0,-3), (-1,-1), cream), ("GRID", (0,0), (-1,-1), .25, colors.HexColor("#D8DEE0")), ("LEFTPADDING", (0,0), (-1,-1), 10), ("RIGHTPADDING", (0,0), (-1,-1), 10), ("TOPPADDING", (0,0), (-1,-1), 8), ("BOTTOMPADDING", (0,0), (-1,-1), 8)],
        ),
        Spacer(1, 18),
        p("This receipt confirms the payment shown above. Please retain it with the related invoice for your records."),
    ]
    doc.build(story)
    return buffer.getvalue()

@transaction.atomic
def ensure_payment_receipt(invoice, transaction, *, booking_status, generated_by=None):
    """Create one permanent receipt for a transaction; retries return the same record."""
    existing = PaymentReceipt.objects.select_for_update().filter(transaction=transaction).first()
    if existing:
        return existing, False
    generate_and_store_invoice(invoice, generated_by=generated_by)
    invoice.refresh_from_db()
    invoice.refresh_status()
    version = invoice.versions.order_by("-revision").first()
    receipt = PaymentReceipt.objects.create(
        transaction=transaction,
        invoice_version=version,
        amount=transaction.amount,
        balance_after=invoice.balance_due,
        payment_method=transaction.method,
        payment_date=transaction.received_on,
        payment_reference=transaction.reference,
        final_due_date=invoice.balance_due_date,
        booking_status=booking_status,
    )
    receipt.refresh_from_db()
    content = build_payment_receipt_pdf(invoice, transaction, booking_status=booking_status)
    receipt.pdf_file.save(f"{receipt.number}.pdf", ContentFile(content), save=True)
    return receipt, True


def send_payment_receipt_email(invoice, transaction, *, booking_status, generated_by=None):
    """Email the transaction's permanent receipt and its preserved invoice revision."""
    receipt, _ = ensure_payment_receipt(
        invoice, transaction, booking_status=booking_status, generated_by=generated_by
    )
    with receipt.pdf_file.open("rb") as receipt_file:
        receipt_content = receipt_file.read()
    with receipt.invoice_version.pdf_file.open("rb") as invoice_file:
        invoice_content = invoice_file.read()
    subject = f"Akako House payment receipt {receipt.number} — {invoice.number}"
    final_due = receipt.final_due_date.strftime("%B %d, %Y") if receipt.final_due_date else "As agreed"
    payment_state = "Paid in full" if receipt.balance_after == 0 else "Partial payment"
    body = (
        f"Hi {invoice.customer_name or 'there'},\n\n"
        f"Attached is your official Akako House payment receipt {receipt.number}.\n\n"
        f"Amount received: {money(receipt.amount)}\n"
        f"Payment method: {transaction.get_method_display()}\n"
        f"Payment date: {receipt.payment_date:%B %d, %Y}\n"
        f"Remaining balance: {money(receipt.balance_after)}\n"
        f"Final due date: {final_due}\n"
        f"Booking status: {receipt.booking_status}\n"
        f"Payment status: {payment_state}\n\n"
        f"Invoice revision {receipt.invoice_version.revision} is also attached for your records.\n\n"
        "Thank you,\nAkako House LLC"
    )
    message = EmailMessage(subject, body, None, [invoice.customer_email])
    message.attach(f"{receipt.number}.pdf", receipt_content, "application/pdf")
    message.attach(
        f"{invoice.number}-v{receipt.invoice_version.revision}.pdf", invoice_content, "application/pdf"
    )
    message.send(fail_silently=False)
    now = timezone.now()
    receipt.emailed_to = invoice.customer_email
    receipt.emailed_at = now
    receipt.save(update_fields=["emailed_to", "emailed_at"])
    invoice.last_emailed_to = invoice.customer_email
    invoice.last_emailed_at = now
    invoice.email_subject = subject
    invoice.email_body = body
    invoice.save(update_fields=["last_emailed_to", "last_emailed_at", "email_subject", "email_body", "updated_at"])
    invoice.refresh_status()
    return message
@transaction.atomic
def ensure_invoice_for_ceremony(ceremony, *, created_by=None):
    """Return the active ceremony invoice, creating it once under a row lock."""
    from .models import Ceremony, Invoice

    ceremony = Ceremony.objects.select_for_update().select_related("quote").get(pk=ceremony.pk)
    existing = ceremony.invoices.exclude(status=Invoice.Status.VOID).order_by("created_at").first()
    if existing:
        if not existing.pdf_file or not existing.pdf_file.storage.exists(existing.pdf_file.name):
            generate_and_store_invoice(existing, generated_by=created_by)
        return existing, False

    quote = ceremony.quote
    if not quote.billing_complete:
        raise ValueError("Billing details must be confirmed before creating an invoice.")
    deposit = ceremony.deposit_payment
    invoice = Invoice.objects.create(
        ceremony=ceremony,
        customer_name=quote.billing_contact_name,
        customer_email=quote.billing_email,
        billing_type=quote.billing_type,
        organization_name=quote.organization_name,
        billing_contact_name=quote.billing_contact_name,
        billing_address=quote.billing_address,
        purchase_order_number=quote.purchase_order_number,
        description=f"{quote.get_event_type_display()} Ethiopian coffee ceremony for {quote.guest_count} guests",
        total_amount=quote.quoted_amount,
        first_payment_amount=quote.deposit_amount or Decimal("0.00"),
        first_payment_due_date=deposit.due_at.date() if deposit and deposit.due_at else None,
        balance_due_date=ceremony.final_payment_due_at.date() if ceremony.final_payment_due_at else None,
        notes=quote.quote_notes,
        created_by=created_by,
    )
    generate_and_store_invoice(invoice, generated_by=created_by)
    return invoice, True




