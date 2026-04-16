from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    AuditLog,
    Customer,
    DailyReconciliation,
    Inventory,
    InventoryAdjustment,
    Order,
    OrderChangeRequest,
    OrderItem,
    Payment,
    PaymentReversal,
    Product,
    ProductCategory,
    Receipt,
)


class Command(BaseCommand):
    help = "Remove business/demo data while keeping user accounts, roles, and branches."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required safety flag so the reset only runs intentionally.",
        )

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("This command is destructive. Re-run with --confirm.")

        counts = {
            "payment_reversals": PaymentReversal.objects.count(),
            "receipts": Receipt.objects.count(),
            "payments": Payment.objects.count(),
            "order_change_requests": OrderChangeRequest.objects.count(),
            "order_items": OrderItem.objects.count(),
            "orders": Order.objects.count(),
            "inventory_adjustments": InventoryAdjustment.objects.count(),
            "reconciliations": DailyReconciliation.objects.count(),
            "inventory_rows": Inventory.objects.count(),
            "customers": Customer.objects.count(),
            "products": Product.objects.count(),
            "product_categories": ProductCategory.objects.count(),
            "audit_logs": AuditLog.objects.count(),
        }

        with transaction.atomic():
            PaymentReversal.objects.all().delete()
            Receipt.objects.all().delete()
            Payment.objects.all().delete()
            OrderChangeRequest.objects.all().delete()
            OrderItem.objects.all().delete()
            Order.objects.all().delete()
            InventoryAdjustment.objects.all().delete()
            DailyReconciliation.objects.all().delete()
            Inventory.objects.all().delete()
            Customer.objects.all().delete()
            Product.objects.all().delete()
            ProductCategory.objects.all().delete()
            AuditLog.objects.all().delete()

        media_root = Path(settings.MEDIA_ROOT)
        uploads_dir = media_root / "uploads"
        if uploads_dir.exists():
            shutil.rmtree(uploads_dir)

        self.stdout.write(self.style.SUCCESS("Business data reset complete."))
        for label, count in counts.items():
            self.stdout.write(f"- Removed {count} {label.replace('_', ' ')}")
        self.stdout.write("User accounts, roles, and branches were kept.")
