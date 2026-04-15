from __future__ import annotations

from datetime import date
from decimal import Decimal
import secrets
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
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="created_orders", null=True, blank=True
    )
    last_modified_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="modified_orders", null=True, blank=True
    )
    assigned_collector = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="assigned_orders", null=True, blank=True
    )
    approved_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="approved_orders", null=True, blank=True
    )
    approved_at = models.DateTimeField(null=True, blank=True)
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

    @property
    def is_collection_ready(self) -> bool:
        return self.status in {OrderStatus.PENDING, OrderStatus.RESERVED} and self.remaining_balance > Decimal("0.00")

    @property
    def next_action_label(self) -> str:
        if self.status == OrderStatus.CANCELLED:
            return "Order is cancelled"
        if not self.assigned_collector and self.remaining_balance > Decimal("0.00"):
            return "Assign a collector"
        unresolved_review_exists = self.payments.filter(
            verification_status=Payment.VerificationStatus.REVIEW_REQUIRED,
            manager_resolution_status=Payment.ManagerResolutionStatus.UNRESOLVED,
        ).exists()
        if unresolved_review_exists:
            return "Resolve flagged payment"
        pending_customer_exists = self.payments.filter(
            verification_status=Payment.VerificationStatus.PENDING_CUSTOMER
        ).exists()
        if pending_customer_exists:
            return "Wait for customer confirmation"
        if self.remaining_balance > Decimal("0.00"):
            return "Continue collection"
        if self.status != OrderStatus.COMPLETED:
            return "Close order"
        return "Order complete"

    def _apply_inventory_transition(self, old_status: Optional[str], new_status: str) -> None:
        """
        Apply inventory rules based on status transitions:
        - NEW/PENDING -> RESERVED: increase reserved.
        - NEW/PENDING -> COMPLETED: consume stock directly.
        - RESERVED -> COMPLETED: release reserved units and consume stock.
        - RESERVED -> PENDING/CANCELLED: release reserved units.
        """
        if old_status == new_status:
            return

        for item in self.items.select_related("product"):
            inventory, _ = Inventory.objects.select_for_update().get_or_create(
                product=item.product, branch=self.branch, defaults={"stock": 0, "reserved": 0, "available": 0}
            )
            qty = item.quantity

            if old_status is None and new_status == OrderStatus.RESERVED:
                inventory.reserved += qty
            elif old_status is None and new_status == OrderStatus.COMPLETED:
                inventory.stock = max(inventory.stock - qty, 0)
            elif old_status == OrderStatus.PENDING and new_status == OrderStatus.RESERVED:
                inventory.reserved += qty
            elif old_status == OrderStatus.PENDING and new_status == OrderStatus.COMPLETED:
                inventory.stock = max(inventory.stock - qty, 0)
            elif old_status == OrderStatus.RESERVED and new_status == OrderStatus.COMPLETED:
                inventory.reserved = max(inventory.reserved - qty, 0)
                inventory.stock = max(inventory.stock - qty, 0)
            elif old_status == OrderStatus.RESERVED and new_status in {OrderStatus.PENDING, OrderStatus.CANCELLED}:
                inventory.reserved = max(inventory.reserved - qty, 0)

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


class OrderChangeRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class OrderChangeRequest(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="change_requests")
    requested_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="order_change_requests"
    )
    reviewed_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="reviewed_order_change_requests", null=True, blank=True
    )
    requested_status = models.CharField(max_length=20, choices=OrderStatus.choices, blank=True)
    requested_assigned_collector = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="requested_assignments", null=True, blank=True
    )
    reason = models.TextField()
    status = models.CharField(
        max_length=20, choices=OrderChangeRequestStatus.choices, default=OrderChangeRequestStatus.PENDING
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Order change request #{self.pk} for Order #{self.order_id}"


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
    class VerificationStatus(models.TextChoices):
        PENDING_CUSTOMER = "pending_customer", "Pending Customer Confirmation"
        MATCHED = "matched", "Matched"
        REVIEW_REQUIRED = "review_required", "Review Required"

    class ManagerResolutionStatus(models.TextChoices):
        UNRESOLVED = "unresolved", "Awaiting Review Decision"
        ACCEPTED = "accepted", "Accepted By Management"
        DISPUTED = "disputed", "Disputed"
        FOLLOW_UP = "follow_up", "Needs Follow-Up"

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payments")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="payments")
    collector = models.ForeignKey(User, on_delete=models.PROTECT, related_name="collected_payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_at = models.DateTimeField(default=timezone.now)
    receipt = models.ImageField(upload_to=payment_receipt_temp_upload_to, blank=True)
    collector_submission_ip = models.GenericIPAddressField(null=True, blank=True)
    collector_submission_user_agent = models.TextField(blank=True)
    customer_receipt = models.ImageField(upload_to=payment_receipt_temp_upload_to, blank=True)
    customer_confirmation_name = models.CharField(max_length=255, blank=True)
    customer_reported_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    customer_confirmed_at = models.DateTimeField(null=True, blank=True)
    customer_confirmation_token = models.CharField(max_length=64, unique=True, blank=True, null=True)
    customer_confirmation_ip = models.GenericIPAddressField(null=True, blank=True)
    customer_confirmation_user_agent = models.TextField(blank=True)
    customer_signature_data = models.TextField(blank=True)
    suspicious_confirmation = models.BooleanField(default=False)
    suspicious_reason = models.TextField(blank=True)
    verification_status = models.CharField(
        max_length=30,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING_CUSTOMER,
    )
    manager_resolution_status = models.CharField(
        max_length=20,
        choices=ManagerResolutionStatus.choices,
        default=ManagerResolutionStatus.UNRESOLVED,
    )
    manager_resolution_note = models.TextField(blank=True)
    manager_resolved_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="resolved_payments",
        null=True,
        blank=True,
    )
    manager_resolved_at = models.DateTimeField(null=True, blank=True)

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

    @property
    def amount_matches_customer(self) -> bool:
        return self.customer_reported_amount is not None and self.customer_reported_amount == self.amount

    @property
    def requires_manager_resolution(self) -> bool:
        return (
            self.verification_status == self.VerificationStatus.REVIEW_REQUIRED
            and self.manager_resolution_status == self.ManagerResolutionStatus.UNRESOLVED
        )

    @property
    def next_action_label(self) -> str:
        if self.verification_status == self.VerificationStatus.PENDING_CUSTOMER:
            return "Wait for customer confirmation"
        if self.requires_manager_resolution:
            return "Manager review required"
        if self.manager_resolution_status == self.ManagerResolutionStatus.ACCEPTED:
            return "Review resolved and accepted"
        if self.manager_resolution_status == self.ManagerResolutionStatus.DISPUTED:
            return "Investigate disputed payment"
        if self.manager_resolution_status == self.ManagerResolutionStatus.FOLLOW_UP:
            return "Complete follow-up actions"
        return "Payment verified"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.customer_confirmation_token:
            self.customer_confirmation_token = secrets.token_urlsafe(24)

        # Enforce immutability - payments cannot be edited after creation
        if self.pk:
            orig = Payment.objects.get(pk=self.pk)
            immutable_fields = ["order_id", "branch_id", "collector_id", "amount", "paid_at"]
            for field in immutable_fields:
                if getattr(orig, field) != getattr(self, field):
                    raise ValueError("Payments are immutable and cannot be edited after creation.")
        super().save(*args, **kwargs)

    def apply_customer_confirmation(
        self,
        *,
        customer_name: str,
        reported_amount: Decimal,
        customer_receipt_file,
        signature_data: str,
        confirmation_ip: str | None,
        confirmation_user_agent: str,
    ) -> None:
        suspicious_reasons: list[str] = []
        if self.collector_submission_ip and confirmation_ip and self.collector_submission_ip == confirmation_ip:
            suspicious_reasons.append("Customer confirmation came from the same IP address as the collector submission.")
        if (
            self.collector_submission_user_agent
            and confirmation_user_agent
            and self.collector_submission_user_agent == confirmation_user_agent
        ):
            suspicious_reasons.append("Customer confirmation used the same browser/device signature as the collector submission.")
        if self.created_at and timezone.now() - self.created_at <= timezone.timedelta(minutes=2):
            suspicious_reasons.append("Customer confirmation happened unusually quickly after the payment was recorded.")

        self.customer_confirmation_name = customer_name
        self.customer_reported_amount = reported_amount
        self.customer_confirmed_at = timezone.now()
        self.customer_confirmation_ip = confirmation_ip
        self.customer_confirmation_user_agent = confirmation_user_agent
        self.customer_signature_data = signature_data
        self.suspicious_confirmation = bool(suspicious_reasons)
        self.suspicious_reason = " ".join(suspicious_reasons)
        self.verification_status = (
            self.VerificationStatus.MATCHED
            if reported_amount == self.amount and not self.suspicious_confirmation
            else self.VerificationStatus.REVIEW_REQUIRED
        )
        self.manager_resolution_status = self.ManagerResolutionStatus.UNRESOLVED
        self.manager_resolution_note = ""
        self.manager_resolved_by = None
        self.manager_resolved_at = None
        self.customer_receipt.save(
            f"uploads/customer_confirmations/payment_{self.id}.jpg",
            customer_receipt_file,
            save=False,
        )
        self.save(
            update_fields=[
                "customer_confirmation_name",
                "customer_reported_amount",
                "customer_confirmed_at",
                "customer_receipt",
                "customer_confirmation_ip",
                "customer_confirmation_user_agent",
                "customer_signature_data",
                "suspicious_confirmation",
                "suspicious_reason",
                "verification_status",
                "manager_resolution_status",
                "manager_resolution_note",
                "manager_resolved_by",
                "manager_resolved_at",
            ]
        )

    def resolve_review(self, *, resolution: str, note: str, resolved_by: User) -> None:
        self.manager_resolution_status = resolution
        self.manager_resolution_note = note
        self.manager_resolved_by = resolved_by
        self.manager_resolved_at = timezone.now()
        self.save(
            update_fields=[
                "manager_resolution_status",
                "manager_resolution_note",
                "manager_resolved_by",
                "manager_resolved_at",
            ]
        )


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

