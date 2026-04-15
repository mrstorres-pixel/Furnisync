from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Inventory, Order, OrderStatus


class Command(BaseCommand):
    help = (
        "One-time repair for demo data created before inventory transitions were fixed. "
        "It deducts completed-order quantities from current stock, rebuilds reserved quantities "
        "from reserved orders, and recalculates available stock."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required safety flag. This repair should only be run once on affected data.",
        )

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError(
                "This is a one-time repair command. Re-run with --confirm only if your old completed "
                "orders did not reduce stock."
            )

        completed_totals: dict[tuple[int, int], int] = defaultdict(int)
        reserved_totals: dict[tuple[int, int], int] = defaultdict(int)

        for order in Order.objects.prefetch_related("items").select_related("branch"):
            for item in order.items.all():
                key = (order.branch_id, item.product_id)
                if order.status == OrderStatus.COMPLETED:
                    completed_totals[key] += item.quantity
                elif order.status == OrderStatus.RESERVED:
                    reserved_totals[key] += item.quantity

        repaired_rows = 0
        with transaction.atomic():
            for inventory in Inventory.objects.select_for_update().all():
                key = (inventory.branch_id, inventory.product_id)
                completed_qty = completed_totals.get(key, 0)
                reserved_qty = reserved_totals.get(key, 0)

                inventory.stock = max(inventory.stock - completed_qty, 0)
                inventory.reserved = reserved_qty
                inventory.recalculate_available()
                inventory.save(update_fields=["stock", "reserved", "available"])
                repaired_rows += 1

        self.stdout.write(self.style.SUCCESS("Inventory repair complete."))
        self.stdout.write(
            "Completed-order quantities were deducted once from current stock, reserved quantities "
            "were rebuilt from reserved orders, and available stock was recalculated."
        )
        self.stdout.write(f"- Repaired {repaired_rows} inventory rows")
        self.stdout.write("Do not run this command again unless you intentionally want to deduct those completed orders a second time.")
