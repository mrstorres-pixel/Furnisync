from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_order_workflow_controls"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="manager_resolution_note",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="manager_resolution_status",
            field=models.CharField(
                choices=[
                    ("unresolved", "Awaiting Review Decision"),
                    ("accepted", "Accepted By Management"),
                    ("disputed", "Disputed"),
                    ("follow_up", "Needs Follow-Up"),
                ],
                default="unresolved",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="manager_resolved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="manager_resolved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="resolved_payments",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
