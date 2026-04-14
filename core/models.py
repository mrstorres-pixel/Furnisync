from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.utils import timezone

User = get_user_model()


class Branch(models.Model):
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=500, blank=True)

    def __str__(self) -> str:
        return self.name


class UserRole(models.TextChoices):
    COLLECTOR = "collector", "Collector"
    SECRETARY = "secretary", "Secretary"
    MANAGER = "manager", "Manager"
    OWNER = "owner", "Owner / Admin"


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=UserRole.choices, default=UserRole.COLLECTOR)
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.user.get_username()} ({self.get_role_display()})"


class Customer(models.Model):
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=500, blank=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="customers")
    installment_plan = models.CharField(max_length=255, help_text="Description of the installment plan")

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.full_name} ({self.branch})"


class Product(models.Model):
    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self) -> str:
        return f"{self.name} ({self.sku})"


class Inventory(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="inventories")
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="inventories")
    stock = models.PositiveIntegerField(default=0, help_text="Total physical stock on hand")
    reserved = models.PositiveIntegerField(default=0, help_text="Reserved for active orders")
    available = models.PositiveIntegerField(default=0, help_text="Available for new orders")

    class Meta:
        unique_together = ("product", "branch")

    def recalculate_available(self) -> None:
        self.available = max(self.stock - self.reserved, 0)

    def __str__(self) -> str:
        return f"{self.product} @ {self.branch}"


class OrderStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RESERVED = "reserved", "Reserved"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class Order(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Order #{self.pk} - {self.customer}"

    @property
    def total_amount(self) -> Decimal:
        """Sum of all order item subtotals."""
        return sum((item.subtotal for item in self.items.all()), Decimal("0.00"))

    @property
    def total_paid(self) -> Decimal:
        """Total amount paid so far."""
        return sum((p.amount for p in self.payments.all()), Decimal("0.00"))

    @property
    def remaining_balance(self) -> Decimal:
        """Remaining balance to be paid."""
        return self.total_amount - self.total_paid

    def _apply_inventory_transition(self, old_status: Optional[str], new_status: str) -> None:
        """
        Apply inventory rules based on status transitions:
        - On NEW -> RESERVED: increase reserved, decrease available.
        - RESERVED -> COMPLETED: decrease reserved and stock.
        - RESERVED -> CANCELLED: decrease reserved, increase available.
        """
        if old_status == new_status:
            return

        for item in self.items.select_related("product"):
            inventory, _ = Inventory.objects.select_for_update().get_or_create(
                product=item.product, branch=self.branch, defaults={"stock": 0, "reserved": 0, "available": 0}
            )
            qty = item.quantity

            if old_status is None and new_status in {OrderStatus.RESERVED, OrderStatus.COMPLETED}:
                # New order directly reserved/completed -> reserve immediately
                inventory.reserved += qty
                if inventory.available >= qty:
                    inventory.available -= qty
            elif old_status == OrderStatus.PENDING and new_status == OrderStatus.RESERVED:
                inventory.reserved += qty
                if inventory.available >= qty:
                    inventory.available -= qty
            elif old_status == OrderStatus.RESERVED and new_status == OrderStatus.COMPLETED:
                if inventory.reserved >= qty:
                    inventory.reserved -= qty
                if inventory.stock >= qty:
                    inventory.stock -= qty
            elif old_status == OrderStatus.RESERVED and new_status == OrderStatus.CANCELLED:
                if inventory.reserved >= qty:
                    inventory.reserved -= qty
                inventory.available += qty

            inventory.recalculate_available()
            inventory.save()

    def save(self, *args: Any, **kwargs: Any) -> None:
        with transaction.atomic():
            if self.pk:
                old = Order.objects.select_for_update().get(pk=self.pk)
                old_status: Optional[str] = old.status
            else:
                old_status = None

            super().save(*args, **kwargs)

            # Apply status-based inventory changes
            self._apply_inventory_transition(old_status, self.status)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Price per unit at time of order")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, help_text="Total for this line item")

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Auto-calculate subtotal if not provided
        if not self.subtotal or self.subtotal == Decimal("0.00"):
            self.subtotal = self.price * self.quantity
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.product} x {self.quantity} = {self.subtotal}"


def payment_receipt_temp_upload_to(instance: "Payment", filename: str) -> str:
    """
    Temporary upload folder; final path is enforced in the PaymentForm.
    """
    return f"uploads/temp/{filename}"


class Payment(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payments")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="payments")
    collector = models.ForeignKey(User, on_delete=models.PROTECT, related_name="collected_payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_at = models.DateTimeField(default=timezone.now)
    receipt = models.ImageField(upload_to=payment_receipt_temp_upload_to, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Payment #{self.pk} for Order #{self.order_id}"

    @property
    def balance_after_payment(self) -> Decimal:
        """Calculate remaining balance after this payment."""
        total_paid = sum(
            (p.amount for p in self.order.payments.filter(paid_at__lte=self.paid_at)),
            Decimal("0.00")
        )
        return self.order.total_amount - total_paid

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Enforce immutability - payments cannot be edited after creation
        if self.pk:
            orig = Payment.objects.get(pk=self.pk)
            immutable_fields = ["order_id", "branch_id", "collector_id", "amount", "paid_at", "receipt"]
            for field in immutable_fields:
                if getattr(orig, field) != getattr(self, field):
                    raise ValueError("Payments are immutable and cannot be edited after creation.")
        super().save(*args, **kwargs)


class Receipt(models.Model):
    """
    Auto-generated receipt for each payment transaction.
    """
    receipt_number = models.CharField(max_length=50, unique=True, db_index=True)
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="payment_receipt")
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="receipts")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="receipts")
    collector = models.ForeignKey(User, on_delete=models.PROTECT, related_name="issued_receipts")
    total_paid = models.DecimalField(max_digits=12, decimal_places=2, help_text="Amount paid in this transaction")
    remaining_balance = models.DecimalField(max_digits=12, decimal_places=2, help_text="Balance after this payment")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Receipt {self.receipt_number} - Payment #{self.payment_id}"

    @staticmethod
    def generate_receipt_number(payment: Payment) -> str:
        """Generate unique receipt number: RCP-BRANCH-YYYYMMDD-XXXXX"""
        today = timezone.now().strftime("%Y%m%d")
        branch_id = payment.branch_id or 0
        return f"RCP-{branch_id}-{today}-{payment.id:05d}"


class ReversalStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class PaymentReversal(models.Model):
    payment = models.OneToOneField(Payment, on_delete=models.CASCADE, related_name="reversal")
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=ReversalStatus.choices, default=ReversalStatus.PENDING)
    requested_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="requested_reversals"
    )
    reviewed_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="reviewed_reversals", null=True, blank=True
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Reversal for Payment #{self.payment_id} ({self.status})"


class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=255)
    model_name = models.CharField(max_length=255)
    object_id = models.CharField(max_length=255)
    old_values = models.JSONField(null=True, blank=True)
    new_values = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.action} on {self.model_name} ({self.object_id})"


class ReconciliationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class DailyReconciliation(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="reconciliations")
    collector = models.ForeignKey(User, on_delete=models.PROTECT, related_name="reconciliations")
    date = models.DateField(default=date.today)
    system_total = models.DecimalField(max_digits=12, decimal_places=2)
    cash_counted = models.DecimalField(max_digits=12, decimal_places=2)
    discrepancy = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=20, choices=ReconciliationStatus.choices, default=ReconciliationStatus.PENDING)
    approved_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="approved_reconciliations", null=True, blank=True
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Recalculate discrepancy whenever saving
        self.discrepancy = self.cash_counted - self.system_total
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Reconciliation {self.date} - {self.branch} - {self.collector}"


class InventoryAdjustment(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="inventory_adjustments")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="inventory_adjustments")
    quantity = models.IntegerField(help_text="Positive to increase stock, negative to decrease stock")
    reason = models.TextField()
    approved = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="created_inventory_adjustments"
    )
    approved_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="approved_inventory_adjustments", null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    audit_log = models.ForeignKey(
        AuditLog, on_delete=models.SET_NULL, null=True, blank=True, related_name="inventory_adjustments"
    )

    def apply_to_inventory(self) -> Inventory:
        """
        Apply this adjustment to the Inventory record for the product/branch.
        Should be called only once, when approved.
        """
        inventory, _ = Inventory.objects.get_or_create(
            product=self.product, branch=self.branch, defaults={"stock": 0, "reserved": 0, "available": 0}
        )
        inventory.stock = max(inventory.stock + self.quantity, 0)
        inventory.recalculate_available()
        inventory.save()
        return inventory

    def __str__(self) -> str:
        return f"Adjustment {self.quantity} {self.product} @ {self.branch}"


def create_audit_log(
    *, user: Optional[User], action: str, instance: models.Model, old_values: Any | None, new_values: Any | None
) -> AuditLog:
    """
    Helper for creating audit logs from views and business logic.
    """
    return AuditLog.objects.create(
        user=user,
        action=action,
        model_name=instance.__class__.__name__,
        object_id=str(getattr(instance, "pk", "")),
        old_values=old_values,
        new_values=new_values,
    )

