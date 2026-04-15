from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_payment_customer_verification"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="collector_submission_ip",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="collector_submission_user_agent",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="customer_confirmation_ip",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="customer_confirmation_user_agent",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="customer_signature_data",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="suspicious_confirmation",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="payment",
            name="suspicious_reason",
            field=models.TextField(blank=True),
        ),
    ]
