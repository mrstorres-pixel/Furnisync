import secrets
from django.db import migrations, models
import core.models


def populate_confirmation_tokens(apps, schema_editor):
    Payment = apps.get_model("core", "Payment")
    for payment in Payment.objects.filter(customer_confirmation_token__isnull=True):
        payment.customer_confirmation_token = secrets.token_urlsafe(24)
        payment.save(update_fields=["customer_confirmation_token"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_remove_payment_is_reversed_orderitem_price_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="customer_confirmation_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="payment",
            name="customer_confirmation_token",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="customer_confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="customer_receipt",
            field=models.ImageField(blank=True, upload_to=core.models.payment_receipt_temp_upload_to),
        ),
        migrations.AddField(
            model_name="payment",
            name="customer_reported_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="verification_status",
            field=models.CharField(
                choices=[
                    ("pending_customer", "Pending Customer Confirmation"),
                    ("matched", "Matched"),
                    ("review_required", "Review Required"),
                ],
                default="pending_customer",
                max_length=30,
            ),
        ),
        migrations.RunPython(populate_confirmation_tokens, migrations.RunPython.noop),
    ]
