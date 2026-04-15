from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_payment_suspicious_flags_and_signature"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="approved_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="approved_orders", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="order",
            name="assigned_collector",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="assigned_orders", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="order",
            name="created_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_orders", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="order",
            name="last_modified_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="modified_orders", to=settings.AUTH_USER_MODEL),
        ),
        migrations.CreateModel(
            name="OrderChangeRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("requested_status", models.CharField(blank=True, choices=[("pending", "Pending"), ("reserved", "Reserved"), ("completed", "Completed"), ("cancelled", "Cancelled")], max_length=20)),
                ("reason", models.TextField()),
                ("status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")], default="pending", max_length=20)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="change_requests", to="core.order")),
                ("requested_assigned_collector", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="requested_assignments", to=settings.AUTH_USER_MODEL)),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="order_change_requests", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="reviewed_order_change_requests", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
