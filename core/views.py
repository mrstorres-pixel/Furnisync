from __future__ import annotations

from datetime import date
from functools import wraps
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    CustomerForm,
    DailyReconciliationForm,
    InventoryAdjustmentForm,
    OrderForm,
    OrderItemFormSet,
    PaymentForm,
    ProductForm,
    UserProfileForm,
)
from .models import (
    AuditLog,
    Branch,
    Customer,
    DailyReconciliation,
    Inventory,
    InventoryAdjustment,
    Order,
    OrderStatus,
    Payment,
    Product,
    Receipt,
    ReconciliationStatus,
    UserProfile,
    UserRole,
    create_audit_log,
)

User = get_user_model()


def _user_has_role(user: User, *roles: str) -> bool: # type: ignore
    return hasattr(user, "profile") and user.profile.role in roles


def _get_active_branch(user: User):
    profile = getattr(user, "profile", None)
    if getattr(profile, "branch", None):
        return profile.branch
    return Branch.objects.order_by("id").first()


def role_required(*roles: str):
    """
    Decorator that requires the user to be logged in and have one of the
    given roles. If the user lacks permission, they are redirected back
    to the dashboard with an error message instead of seeing the login
    page again.
    """

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request: HttpRequest, *args, **kwargs):
            if not _user_has_role(request.user, *roles):
                messages.error(request, "You do not have permission to access that page.")
                return redirect("dashboard")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """
    Simple role-based dashboard.
    """
    role = getattr(getattr(request.user, "profile", None), "role", None)
    # High-level metrics used for all dashboards
    context = {
        "role": role,
        "total_customers": Customer.objects.count(),
        "total_orders": Order.objects.count(),
        "total_payments": Payment.objects.count(),
        "pending_reconciliations": DailyReconciliation.objects.filter(
            status=ReconciliationStatus.PENDING
        ).count(),
        "inventory_low": Inventory.objects.filter(available__lte=5).select_related("product", "branch")[:10],
    }

    # Extra aggregates for owner/manager dashboards
    if role in {UserRole.MANAGER, UserRole.OWNER}:
        recent_payments = (
            Payment.objects.select_related("order__customer", "branch", "collector")
            .order_by("-paid_at")[:10]
        )
        context["recent_payments"] = recent_payments

    if role == UserRole.OWNER:
        today = timezone.now().date()
        context["today_payment_total"] = sum(
            (float(p.amount) for p in Payment.objects.filter(paid_at__date=today)),
            0.0,
        )

    template_map = {
        UserRole.COLLECTOR: "core/dashboard_collector.html",
        UserRole.SECRETARY: "core/dashboard_secretary.html",
        UserRole.MANAGER: "core/dashboard_manager.html",
        UserRole.OWNER: "core/dashboard_owner.html",
    }
    template_name = template_map.get(role, "core/dashboard_generic.html")
    return render(request, template_name, context)


@role_required(UserRole.COLLECTOR)
def log_payment(request: HttpRequest) -> HttpResponse:
    """
    Collector logs a cash payment with mandatory receipt photo.
    Receipt is auto-generated via signal.
    """
    if request.method == "POST":
        form = PaymentForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            if form.instance.pk:
                messages.error(request, "Payments are immutable and cannot be edited.")
            else:
                with transaction.atomic():
                    payment = form.save()
                    create_audit_log(
                        user=request.user,
                        action="Create Payment",
                        instance=payment,
                        old_values=None,
                        new_values={
                            "order_id": payment.order_id,
                            "amount": str(payment.amount),
                            "collector_id": payment.collector_id,
                        },
                    )
                messages.success(request, f"Payment logged successfully. Receipt: {payment.payment_receipt.receipt_number}")
                return redirect("dashboard")
        # Form errors (incl validation) shown below
    else:
        form = PaymentForm(user=request.user)
    return render(request, "core/log_payment.html", {"form": form})


@role_required(UserRole.COLLECTOR, UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def view_receipt(request: HttpRequest, receipt_id: int) -> HttpResponse:
    """
    View and print receipt details.
    """
    receipt = get_object_or_404(Receipt.objects.select_related(
        'payment', 'order__customer', 'branch', 'collector'
    ), pk=receipt_id)
    
    # Check permissions - collectors can only view their own receipts
    user_profile = getattr(request.user, "profile", None)
    role = getattr(user_profile, "role", None)
    
    if role == UserRole.COLLECTOR:
        if receipt.collector != request.user:
            messages.error(request, "You can only view your own receipts.")
            return redirect("dashboard")
    
    return render(request, "core/receipt_detail.html", {"receipt": receipt})


@role_required(UserRole.COLLECTOR, UserRole.SECRETARY)
def daily_reconciliation(request: HttpRequest) -> HttpResponse:
    """
    Collector or Secretary records a daily cash reconciliation for their branch.
    """
    active_branch = _get_active_branch(request.user)
    if active_branch is None:
        messages.error(request, "Set up the operating branch before recording reconciliation.")
        return redirect("dashboard")

    if request.method == "POST":
        form = DailyReconciliationForm(request.POST)
        if form.is_valid():
            reconciliation: DailyReconciliation = form.save(commit=False)
            reconciliation.collector = request.user
            reconciliation.branch = active_branch
            reconciliation.save()
            create_audit_log(
                user=request.user,
                action="Create Daily Reconciliation",
                instance=reconciliation,
                old_values=None,
                new_values={
                    "branch_id": reconciliation.branch_id,
                    "collector_id": reconciliation.collector_id,
                    "system_total": str(reconciliation.system_total),
                    "cash_counted": str(reconciliation.cash_counted),
                    "discrepancy": str(reconciliation.discrepancy),
                },
            )
            messages.success(request, "Daily reconciliation submitted for approval.")
            return redirect("dashboard")
    else:
        initial = {"date": date.today()}
        form = DailyReconciliationForm(initial=initial)
    return render(request, "core/daily_reconciliation.html", {"form": form})


@role_required(UserRole.MANAGER, UserRole.OWNER)
def reconciliation_list(request: HttpRequest) -> HttpResponse:
    reconciliations = DailyReconciliation.objects.select_related("branch", "collector").order_by("-date")
    return render(request, "core/reconciliation_list.html", {"reconciliations": reconciliations})


@role_required(UserRole.MANAGER, UserRole.OWNER)
def handle_reconciliation(request: HttpRequest, reconciliation_id: int, decision: str) -> HttpResponse:
    reconciliation = get_object_or_404(DailyReconciliation, pk=reconciliation_id)
    if reconciliation.status != ReconciliationStatus.PENDING:
        messages.error(request, "This reconciliation has already been processed.")
        return redirect("reconciliation_list")

    if decision not in {"approve", "reject"}:
        return HttpResponseForbidden("Invalid decision")

    old_status = reconciliation.status
    if decision == "approve":
        reconciliation.status = ReconciliationStatus.APPROVED
        messages.success(request, "Reconciliation approved.")
    else:
        reconciliation.status = ReconciliationStatus.REJECTED
        messages.info(request, "Reconciliation rejected.")

    reconciliation.approved_by = request.user
    reconciliation.approved_at = timezone.now()
    reconciliation.save()

    create_audit_log(
        user=request.user,
        action="Process Daily Reconciliation",
        instance=reconciliation,
        old_values={"status": old_status},
        new_values={"status": reconciliation.status},
    )

    return redirect("reconciliation_list")


@role_required(UserRole.MANAGER, UserRole.OWNER)
def create_inventory_adjustment(request: HttpRequest) -> HttpResponse:
    """
    Manager creates an inventory adjustment and approves it immediately.
    (For phase 1 MVP we treat creation by a manager as the approval step.)
    """
    active_branch = _get_active_branch(request.user)
    if active_branch is None:
        messages.error(request, "Set up the operating branch before creating inventory adjustments.")
        return redirect("dashboard")

    if request.method == "POST":
        form = InventoryAdjustmentForm(request.POST, current_branch=active_branch)
        if form.is_valid():
            with transaction.atomic():
                adjustment: InventoryAdjustment = form.save(commit=False)
                adjustment.created_by = request.user
                adjustment.approved = True
                adjustment.approved_by = request.user
                adjustment.approved_at = timezone.now()
                adjustment.save()

                old_inventory = None
                inventory = adjustment.apply_to_inventory()
                new_inv_values = {
                    "stock": inventory.stock,
                    "reserved": inventory.reserved,
                    "available": inventory.available,
                }

                audit = create_audit_log(
                    user=request.user,
                    action="Inventory Adjustment",
                    instance=inventory,
                    old_values=old_inventory,
                    new_values=new_inv_values,
                )
                adjustment.audit_log = audit
                adjustment.save(update_fields=["audit_log"])

            messages.success(request, "Inventory adjusted successfully.")
            return redirect("dashboard")
    else:
        form = InventoryAdjustmentForm(current_branch=active_branch)
    return render(request, "core/inventory_adjustment.html", {"form": form})


@role_required(UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def create_customer(request: HttpRequest) -> HttpResponse:
    active_branch = _get_active_branch(request.user)
    if active_branch is None:
        messages.error(request, "Set up the operating branch before creating customers.")
        return redirect("dashboard")

    if request.method == "POST":
        form = CustomerForm(request.POST, current_branch=active_branch)
        if form.is_valid():
            customer = form.save()
            create_audit_log(
                user=request.user,
                action="Create Customer",
                instance=customer,
                old_values=None,
                new_values={"full_name": customer.full_name, "branch_id": customer.branch_id},
            )
            messages.success(request, "Customer created.")
            return redirect("dashboard")
    else:
        form = CustomerForm(current_branch=active_branch)
    return render(request, "core/customer_form.html", {"form": form})


@role_required(UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def create_order(request: HttpRequest) -> HttpResponse:
    active_branch = _get_active_branch(request.user)
    if active_branch is None:
        messages.error(request, "Set up the operating branch before creating orders.")
        return redirect("dashboard")

    if request.method == "POST":
        form = OrderForm(request.POST, current_branch=active_branch)
        formset = OrderItemFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                order = form.save()
                formset.instance = order
                formset.save()
                create_audit_log(
                    user=request.user,
                    action="Create Order",
                    instance=order,
                    old_values=None,
                    new_values={"customer_id": order.customer_id, "branch_id": order.branch_id},
                )
            messages.success(request, "Order created.")
            return redirect("dashboard")
    else:
        form = OrderForm(current_branch=active_branch)
        formset = OrderItemFormSet()
    return render(request, "core/order_form.html", {"form": form, "formset": formset})


@role_required(UserRole.COLLECTOR, UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def inventory_list(request: HttpRequest) -> HttpResponse:
    qs = Inventory.objects.select_related("product", "branch")
    active_branch = _get_active_branch(request.user)
    if active_branch is not None:
        qs = qs.filter(branch=active_branch)
    qs = qs.order_by("branch__name", "product__name")
    return render(request, "core/inventory_list.html", {"inventories": qs})


@role_required(UserRole.COLLECTOR, UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def order_list(request: HttpRequest) -> HttpResponse:
    qs = Order.objects.select_related("customer", "branch").prefetch_related("items__product")
    active_branch = _get_active_branch(request.user)
    if active_branch is not None:
        qs = qs.filter(branch=active_branch)

    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)

    qs = qs.order_by("-created_at")
    return render(request, "core/order_list.html", {"orders": qs})


@role_required(UserRole.MANAGER, UserRole.OWNER)
def product_list(request: HttpRequest) -> HttpResponse:
    sold_quantity = Sum(
        "orderitem__quantity",
        filter=Q(orderitem__order__status=OrderStatus.COMPLETED),
    )
    revenue_total = Sum(
        ExpressionWrapper(
            F("orderitem__quantity") * F("orderitem__price"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        ),
        filter=Q(orderitem__order__status=OrderStatus.COMPLETED),
    )
    products = (
        Product.objects.annotate(
            sold_quantity=sold_quantity,
            revenue_total=revenue_total,
            inventory_stock=Sum("inventories__stock"),
            inventory_available=Sum("inventories__available"),
        )
        .order_by("name")
    )
    return render(request, "core/product_list.html", {"products": products})


@role_required(UserRole.MANAGER, UserRole.OWNER)
def product_edit(request: HttpRequest, product_id: int | None = None) -> HttpResponse:
    product = get_object_or_404(Product, pk=product_id) if product_id else None
    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            obj = form.save()
            create_audit_log(
                user=request.user,
                action="Save Product",
                instance=obj,
                old_values=None,
                new_values={"name": obj.name, "sku": obj.sku},
            )
            messages.success(request, "Product saved.")
            return redirect("product_list")
    else:
        form = ProductForm(instance=product)
    return render(request, "core/product_form.html", {"form": form, "product": product})


@role_required(UserRole.OWNER)
def user_list(request: HttpRequest) -> HttpResponse:
    profiles = UserProfile.objects.select_related("user", "branch").order_by("user__username")
    return render(request, "core/user_list.html", {"profiles": profiles})


@role_required(UserRole.MANAGER, UserRole.OWNER)
def employee_list(request: HttpRequest) -> HttpResponse:
    profiles = (
        UserProfile.objects.select_related("user", "branch")
        .annotate(
            payment_count=Count("user__collected_payments", distinct=True),
            reconciliation_count=Count("user__reconciliations", distinct=True),
            adjustment_count=Count("user__created_inventory_adjustments", distinct=True),
        )
        .order_by("role", "user__username")
    )
    return render(request, "core/employee_list.html", {"profiles": profiles})


@role_required(UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def customer_list(request: HttpRequest) -> HttpResponse:
    active_branch = _get_active_branch(request.user)
    customers = Customer.objects.all().order_by("full_name").prefetch_related("orders__payments")
    if active_branch is not None:
        customers = customers.filter(branch=active_branch)
    customer_summaries = []
    for customer in customers:
        orders = list(customer.orders.all())
        total_purchased = sum((order.total_amount for order in orders), Decimal("0.00"))
        outstanding_balance = sum((order.remaining_balance for order in orders), Decimal("0.00"))
        customer_summaries.append(
            {
                "customer": customer,
                "order_count": len(orders),
                "total_purchased": total_purchased,
                "outstanding_balance": outstanding_balance,
            }
        )
    return render(request, "core/customer_list.html", {"customer_summaries": customer_summaries})


@role_required(UserRole.MANAGER, UserRole.OWNER)
def transaction_list(request: HttpRequest) -> HttpResponse:
    transactions = (
        Payment.objects.select_related("order__customer", "collector", "payment_receipt")
        .order_by("-paid_at")
    )
    return render(request, "core/transaction_list.html", {"transactions": transactions})


@role_required(UserRole.OWNER)
def user_edit(request: HttpRequest, profile_id: int) -> HttpResponse:
    profile = get_object_or_404(UserProfile, pk=profile_id)
    active_branch = _get_active_branch(request.user)
    if request.method == "POST":
        form = UserProfileForm(request.POST, instance=profile)
        if form.is_valid():
            obj = form.save()
            if active_branch is not None and obj.branch_id != active_branch.id:
                obj.branch = active_branch
                obj.save(update_fields=["branch"])
            create_audit_log(
                user=request.user,
                action="Update User Profile",
                instance=obj,
                old_values=None,
                new_values={"role": obj.role, "branch_id": obj.branch_id},
            )
            messages.success(request, "User profile updated.")
            return redirect("user_list")
    else:
        form = UserProfileForm(instance=profile)
    return render(request, "core/user_form.html", {"form": form, "profile": profile})

