from __future__ import annotations

import csv
from datetime import date
from functools import wraps
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q, Sum, Prefetch
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import (
    CustomerPaymentConfirmationForm,
    CustomerForm,
    CustomerPurchaseRequestForm,
    CustomerPurchaseRequestReviewForm,
    CustomerSignupForm,
    DailyReconciliationForm,
    InventoryAdjustmentForm,
    OrderChangeRequestForm,
    OrderManagementForm,
    OrderForm,
    OrderItemFormSet,
    PaymentForm,
    PaymentReviewResolutionForm,
    ProductForm,
    StaffLoginForm,
    UserProfileForm,
    WishlistItemForm,
)
from .models import (
    AuditLog,
    Branch,
    Customer,
    CustomerPurchaseRequest,
    CustomerPurchaseRequestStatus,
    DailyReconciliation,
    Inventory,
    InventoryAdjustment,
    Order,
    OrderChangeRequest,
    OrderChangeRequestStatus,
    OrderItem,
    OrderStatus,
    Payment,
    Product,
    ProductCategory,
    Receipt,
    ReconciliationStatus,
    UserProfile,
    UserRole,
    WishlistItem,
    create_audit_log,
)

User = get_user_model()


def _get_client_ip(request: HttpRequest) -> str | None:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _user_has_role(user: User, *roles: str) -> bool: # type: ignore
    return hasattr(user, "profile") and user.profile.role in roles


def _get_customer_account(user: User):
    return getattr(user, "customer_profile", None)


def _is_customer_user(user: User) -> bool:
    return _get_customer_account(user) is not None


def _get_active_branch(user: User):
    customer = _get_customer_account(user)
    if customer and customer.branch_id:
        return customer.branch
    profile = getattr(user, "profile", None)
    if getattr(profile, "branch", None):
        return profile.branch
    return Branch.objects.order_by("id").first()


def _get_public_branch():
    branch = (
        Branch.objects.annotate(
            inventory_lines=Count("inventories", distinct=True),
            customer_count=Count("customers", distinct=True),
        )
        .order_by("-inventory_lines", "-customer_count", "id")
        .first()
    )
    return branch


def _get_collectible_orders_for_user(user: User):
    active_branch = _get_active_branch(user)
    qs = (
        Order.objects.select_related("customer", "branch")
        .prefetch_related("payments", "items__product")
        .order_by("-created_at")
    )
    if active_branch is not None:
        qs = qs.filter(branch=active_branch)

    active_statuses = {OrderStatus.PENDING, OrderStatus.RESERVED}
    qs = qs.filter(assigned_collector=user)
    return [order for order in qs if order.status in active_statuses and order.remaining_balance > Decimal("0.00")]


def _serialize_order_items(order: Order) -> list[dict[str, object]]:
    return [
        {
            "product": item.product.name,
            "sku": item.product.sku,
            "quantity": item.quantity,
            "price": str(item.price),
            "subtotal": str(item.subtotal),
        }
        for item in order.items.select_related("product").all()
    ]


def _apply_inventory_adjustment(*, request: HttpRequest, form: InventoryAdjustmentForm) -> InventoryAdjustment:
    with transaction.atomic():
        adjustment: InventoryAdjustment = form.save(commit=False)
        adjustment.created_by = request.user
        adjustment.approved = True
        adjustment.approved_by = request.user
        adjustment.approved_at = timezone.now()
        adjustment.save()

        existing_inventory = Inventory.objects.filter(
            product=adjustment.product,
            branch=adjustment.branch,
        ).first()
        old_inventory = None
        if existing_inventory is not None:
            old_inventory = {
                "stock": existing_inventory.stock,
                "reserved": existing_inventory.reserved,
                "available": existing_inventory.available,
            }

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
        return adjustment


def _get_order_lifecycle(order: Order) -> list[dict[str, object]]:
    has_payments = order.payments.exists()
    fully_paid = order.remaining_balance <= Decimal("0.00")
    order_closed = order.status == OrderStatus.COMPLETED
    approved_done = bool(
        order.approved_by_id or order.assigned_collector_id or has_payments or fully_paid or order_closed
    )
    assigned_done = bool(order.assigned_collector_id or has_payments or fully_paid or order_closed)
    collecting_done = bool(has_payments or fully_paid or order_closed)
    partial_payment_active = has_payments and not fully_paid

    current_stage = "approved"
    if order.status == OrderStatus.CANCELLED:
        current_stage = "cancelled"
    elif order_closed:
        current_stage = "completed"
    elif fully_paid:
        current_stage = "fully_paid"
    elif partial_payment_active:
        current_stage = "partially_paid"
    elif not assigned_done:
        current_stage = "assigned"
    elif not collecting_done:
        current_stage = "collecting"

    return [
        {
            "label": "Encoded",
            "done": True,
            "current": False,
            "note": order.created_at,
        },
        {
            "label": "Approved",
            "done": approved_done,
            "current": current_stage == "approved",
            "note": order.approved_at or "Awaiting management approval",
        },
        {
            "label": "Assigned",
            "done": assigned_done,
            "current": current_stage == "assigned",
            "note": order.assigned_collector.username if order.assigned_collector else "Awaiting collector assignment",
        },
        {
            "label": "Collecting",
            "done": collecting_done,
            "current": current_stage == "collecting",
            "note": f"{order.payments.count()} payment(s) logged" if has_payments else "No payments logged yet",
        },
        {
            "label": "Partially Paid",
            "done": partial_payment_active,
            "current": current_stage == "partially_paid",
            "note": f"Balance: \u20b1{order.remaining_balance}" if partial_payment_active else "No partial payments recorded",
        },
        {
            "label": "Fully Paid",
            "done": fully_paid,
            "current": current_stage == "fully_paid",
            "note": "Account fully paid" if fully_paid else f"Balance: \u20b1{order.remaining_balance}",
        },
        {
            "label": "Completed",
            "done": order_closed,
            "current": current_stage == "completed",
            "note": "Order closed" if order_closed else "Pending closure",
        },
    ]


def _build_stock_ledger(product: Product, branch: Branch) -> list[dict[str, object]]:
    ledger: list[dict[str, object]] = []
    for adjustment in product.inventory_adjustments.filter(branch=branch).select_related("created_by").order_by("-created_at"):
        ledger.append(
            {
                "timestamp": adjustment.created_at,
                "movement_type": "Manual Adjustment",
                "quantity": adjustment.quantity,
                "direction": "in" if adjustment.quantity >= 0 else "out",
                "reference": adjustment.reason,
                "actor": adjustment.created_by.username,
            }
        )

    related_items = (
        OrderItem.objects.filter(order__branch=branch, product=product)
        .select_related("order__customer", "order__assigned_collector", "order__created_by")
        .order_by("-order__updated_at")
    )
    for item in related_items:
        if item.order.status == OrderStatus.RESERVED:
            ledger.append(
                {
                    "timestamp": item.order.updated_at,
                    "movement_type": "Reservation",
                    "quantity": item.quantity,
                    "direction": "reserve",
                    "reference": f"Order #{item.order_id} for {item.order.customer.full_name}",
                    "actor": item.order.last_modified_by.username if item.order.last_modified_by else "System",
                }
            )
        elif item.order.status == OrderStatus.COMPLETED:
            ledger.append(
                {
                    "timestamp": item.order.updated_at,
                    "movement_type": "Completed Sale",
                    "quantity": item.quantity,
                    "direction": "out",
                    "reference": f"Order #{item.order_id} for {item.order.customer.full_name}",
                    "actor": item.order.assigned_collector.username if item.order.assigned_collector else (item.order.last_modified_by.username if item.order.last_modified_by else "System"),
                }
            )

    direction_priority = {"Manual Adjustment": 0, "Completed Sale": 1, "Reservation": 2}
    return sorted(
        ledger,
        key=lambda entry: (entry["timestamp"], direction_priority.get(entry["movement_type"], 9)),
        reverse=True,
    )[:20]


def _csv_response(filename: str, header: list[str], rows: list[list[object]]) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


def _build_customer_summary(customer: Customer) -> dict[str, object]:
    orders = list(customer.orders.all())
    total_purchased = sum((order.total_amount for order in orders), Decimal("0.00"))
    outstanding_balance = sum((order.remaining_balance for order in orders), Decimal("0.00"))
    payments = [payment for order in orders for payment in order.payments.all()]
    last_transaction_at = max((payment.paid_at for payment in payments), default=None)
    return {
        "customer": customer,
        "order_count": len(orders),
        "total_purchased": total_purchased,
        "outstanding_balance": outstanding_balance,
        "last_transaction_at": last_transaction_at,
    }


def _build_product_summary(product: Product) -> dict[str, object]:
    inventories = list(getattr(product, "inventories_cache", product.inventories.all()))
    completed_orderitems = list(getattr(product, "completed_orderitems", []))
    sold_quantity = sum((item.quantity for item in completed_orderitems), 0)
    revenue_total = sum((item.subtotal for item in completed_orderitems), Decimal("0.00"))
    last_sold_at = max((item.order.updated_at for item in completed_orderitems), default=None)
    inventory_stock = sum((inventory.stock for inventory in inventories), 0)
    inventory_available = sum((inventory.available for inventory in inventories), 0)
    return {
        "product": product,
        "inventory_stock": inventory_stock,
        "inventory_available": inventory_available,
        "sold_quantity": sold_quantity,
        "revenue_total": revenue_total,
        "last_sold_at": last_sold_at,
    }


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


def customer_required(view_func):
    @wraps(view_func)
    @login_required
    def _wrapped(request: HttpRequest, *args, **kwargs):
        if not _is_customer_user(request.user):
            messages.error(request, "Customer access is required for that page.")
            return redirect("dashboard")
        return view_func(request, *args, **kwargs)

    return _wrapped


def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        if _is_customer_user(request.user):
            return redirect("customer_dashboard")
        return redirect("dashboard")

    next_url = request.POST.get("next") or request.GET.get("next") or ""
    form = StaffLoginForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        auth_login(request, form.get_user())
        if _is_customer_user(form.get_user()):
            return redirect(next_url or "customer_dashboard")
        return redirect("dashboard")
    return render(request, "registration/login.html", {"form": form, "next": next_url})


def customer_signup(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        if _is_customer_user(request.user):
            return redirect("customer_dashboard")
        return redirect("dashboard")

    next_url = request.POST.get("next") or request.GET.get("next") or ""
    branch = _get_public_branch()
    if branch is None:
        messages.error(request, "Create a branch first before opening customer registration.")
        return redirect("login")

    form = CustomerSignupForm(request.POST or None, branch=branch)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        auth_login(request, user)
        messages.success(request, "Customer account created. You can now browse and save furniture items.")
        return redirect(next_url or "customer_dashboard")
    return render(request, "registration/customer_signup.html", {"form": form, "branch": branch, "next": next_url})


def landing_page(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        if _is_customer_user(request.user):
            return redirect("customer_dashboard")
        return redirect("dashboard")

    branch = _get_public_branch()
    featured_products = list(
        Product.objects.select_related("category").prefetch_related(
            Prefetch(
                "inventories",
                queryset=Inventory.objects.filter(branch=branch).order_by("id") if branch is not None else Inventory.objects.none(),
                to_attr="inventories_cache",
            ),
        ).order_by("name")[:6]
    )
    featured_cards: list[dict[str, object]] = []
    for product in featured_products:
        inventory = product.inventories_cache[0] if getattr(product, "inventories_cache", None) else None
        featured_cards.append(
            {
                "product": product,
                "available": inventory.available if inventory else 0,
            }
        )

    return render(
        request,
        "core/landing_page.html",
        {
            "branch": branch,
            "featured_cards": featured_cards,
            "category_count": ProductCategory.objects.count(),
            "product_count": Product.objects.count(),
        },
    )


@customer_required
def customer_dashboard(request: HttpRequest) -> HttpResponse:
    customer = _get_customer_account(request.user)
    assert customer is not None
    orders = list(
        customer.orders.prefetch_related("items__product", "payments").order_by("-created_at")
    )
    wishlist_items = list(
        customer.wishlist_items.select_related("product", "product__category").order_by("-created_at")[:6]
    )
    purchase_requests = list(
        customer.purchase_requests.select_related("product", "branch").order_by("-created_at")[:5]
    )
    payments = list(
        Payment.objects.filter(order__customer=customer)
        .select_related("payment_receipt", "collector", "order")
        .order_by("-paid_at")[:5]
    )
    outstanding_total = sum((order.remaining_balance for order in orders), Decimal("0.00"))
    return render(
        request,
        "core/customer_dashboard.html",
        {
            "customer": customer,
            "orders": orders[:5],
            "wishlist_items": wishlist_items,
            "purchase_requests": purchase_requests,
            "recent_payments": payments,
            "dashboard_summary": {
                "order_count": len(orders),
                "wishlist_count": customer.wishlist_items.count(),
                "request_count": customer.purchase_requests.count(),
                "outstanding_total": outstanding_total,
                "paid_orders": sum((1 for order in orders if order.remaining_balance <= Decimal("0.00")), 0),
            },
        },
    )


def customer_product_list(request: HttpRequest) -> HttpResponse:
    customer = _get_customer_account(request.user) if request.user.is_authenticated else None
    q = request.GET.get("q", "").strip()
    category_filter = request.GET.get("category", "").strip()
    stock_filter = request.GET.get("stock", "").strip()
    branch = customer.branch if customer is not None else _get_public_branch()
    if branch is None:
        messages.error(request, "No branch is configured yet for product browsing.")
        return redirect("login")

    products = Product.objects.select_related("category").order_by("name")
    if q:
        products = products.filter(
            Q(name__icontains=q)
            | Q(sku__icontains=q)
            | Q(description__icontains=q)
            | Q(category__name__icontains=q)
        )
    if category_filter:
        products = products.filter(category_id=category_filter)

    products = products.prefetch_related(
        Prefetch(
            "inventories",
            queryset=Inventory.objects.filter(branch=branch).order_by("id"),
            to_attr="inventories_cache",
        ),
    )
    wishlist_product_ids = set(customer.wishlist_items.values_list("product_id", flat=True)) if customer is not None else set()

    product_cards: list[dict[str, object]] = []
    for product in products:
        inventory = product.inventories_cache[0] if getattr(product, "inventories_cache", None) else None
        available = inventory.available if inventory else 0
        if stock_filter == "in" and available <= 0:
            continue
        if stock_filter == "out" and available > 0:
            continue
        product_cards.append(
            {
                "product": product,
                "inventory": inventory,
                "available": available,
                "is_saved": product.id in wishlist_product_ids,
            }
        )

    return render(
        request,
        "core/customer_product_list.html",
        {
            "customer": customer,
            "is_public_browse": customer is None,
            "product_cards": product_cards,
            "categories": ProductCategory.objects.order_by("name"),
            "filters": {"q": q, "category": category_filter, "stock": stock_filter},
        },
    )


def customer_product_detail(request: HttpRequest, product_id: int) -> HttpResponse:
    customer = _get_customer_account(request.user) if request.user.is_authenticated else None
    branch = customer.branch if customer is not None else _get_public_branch()
    if branch is None:
        messages.error(request, "No branch is configured yet for product browsing.")
        return redirect("login")
    product = get_object_or_404(
        Product.objects.select_related("category").prefetch_related(
            Prefetch(
                "inventories",
                queryset=Inventory.objects.filter(branch=branch).order_by("id"),
                to_attr="inventories_cache",
            ),
        ),
        pk=product_id,
    )
    inventory = product.inventories_cache[0] if getattr(product, "inventories_cache", None) else None
    wishlist_item = customer.wishlist_items.filter(product=product).first() if customer is not None else None
    form = WishlistItemForm(request.POST or None, instance=wishlist_item)
    request_form = CustomerPurchaseRequestForm(request.POST or None, prefix="request")
    if request.method == "POST" and customer is None:
        messages.info(request, "Create a customer account first so you can save items and continue with your order request.")
        return redirect(f"{reverse('customer_signup')}?next={reverse('customer_product_detail', args=[product.id])}")
    action = request.POST.get("action")
    if request.method == "POST" and action == "wishlist" and form.is_valid():
        item = form.save(commit=False)
        item.customer = customer
        item.product = product
        item.save()
        messages.success(request, f"{product.name} was added to your saved items.")
        return redirect("customer_product_detail", product_id=product.id)
    if request.method == "POST" and action == "purchase_request" and request_form.is_valid():
        CustomerPurchaseRequest.objects.create(
            customer=customer,
            branch=branch,
            product=product,
            quantity=request_form.cleaned_data["quantity"],
            note=request_form.cleaned_data["note"],
        )
        messages.success(request, f"Your request for {product.name} was sent to the store for review.")
        return redirect("customer_requests")

    return render(
        request,
        "core/customer_product_detail.html",
        {
            "customer": customer,
            "branch": branch,
            "product": product,
            "inventory": inventory,
            "wishlist_item": wishlist_item,
            "wishlist_form": form,
            "request_form": request_form,
            "is_public_browse": customer is None,
        },
    )


@customer_required
def customer_wishlist(request: HttpRequest) -> HttpResponse:
    customer = _get_customer_account(request.user)
    assert customer is not None
    items = list(
        customer.wishlist_items.select_related("product", "product__category").order_by("-created_at")
    )
    return render(request, "core/customer_wishlist.html", {"customer": customer, "items": items})


@customer_required
def customer_wishlist_remove(request: HttpRequest, item_id: int) -> HttpResponse:
    customer = _get_customer_account(request.user)
    assert customer is not None
    item = get_object_or_404(WishlistItem, pk=item_id, customer=customer)
    if request.method == "POST":
        product_name = item.product.name
        item.delete()
        messages.success(request, f"{product_name} was removed from your wishlist.")
    return redirect("customer_wishlist")


@customer_required
def customer_account(request: HttpRequest) -> HttpResponse:
    customer = _get_customer_account(request.user)
    assert customer is not None
    return render(request, "core/customer_account.html", {"customer": customer})


@customer_required
def customer_orders(request: HttpRequest) -> HttpResponse:
    customer = _get_customer_account(request.user)
    assert customer is not None
    orders = list(
        customer.orders.prefetch_related("items__product", "payments").order_by("-created_at")
    )
    return render(request, "core/customer_orders.html", {"customer": customer, "orders": orders})


@customer_required
def customer_requests(request: HttpRequest) -> HttpResponse:
    customer = _get_customer_account(request.user)
    assert customer is not None
    requests = list(
        customer.purchase_requests.select_related("product", "branch", "reviewed_by").order_by("-created_at")
    )
    return render(request, "core/customer_requests.html", {"customer": customer, "requests": requests})


@role_required(UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def customer_purchase_request_list(request: HttpRequest) -> HttpResponse:
    active_branch = _get_active_branch(request.user)
    status_filter = request.GET.get("status", "").strip()
    requests = CustomerPurchaseRequest.objects.select_related("customer", "product", "branch", "reviewed_by").order_by("-created_at")
    if active_branch is not None:
        requests = requests.filter(branch=active_branch)
    if status_filter:
        requests = requests.filter(status=status_filter)
    requests = list(requests)
    return render(
        request,
        "core/customer_purchase_request_list.html",
        {
            "requests": requests,
            "filters": {"status": status_filter},
            "status_choices": CustomerPurchaseRequestStatus.choices,
            "request_summary": {
                "count": len(requests),
                "pending": sum((1 for item in requests if item.status == CustomerPurchaseRequestStatus.PENDING), 0),
                "reviewed": sum((1 for item in requests if item.status == CustomerPurchaseRequestStatus.REVIEWED), 0),
                "converted": sum((1 for item in requests if item.status == CustomerPurchaseRequestStatus.CONVERTED), 0),
            },
        },
    )


@role_required(UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def customer_purchase_request_detail(request: HttpRequest, request_id: int) -> HttpResponse:
    active_branch = _get_active_branch(request.user)
    request_qs = CustomerPurchaseRequest.objects.select_related("customer", "product", "branch", "reviewed_by")
    if active_branch is not None:
        request_qs = request_qs.filter(branch=active_branch)
    purchase_request = get_object_or_404(request_qs, pk=request_id)

    if request.method == "POST":
        form = CustomerPurchaseRequestReviewForm(request.POST, instance=purchase_request)
        if form.is_valid():
            item = form.save(commit=False)
            item.reviewed_by = request.user
            item.reviewed_at = timezone.now()
            item.save()
            messages.success(request, "Purchase request updated.")
            return redirect("customer_purchase_request_detail", request_id=item.id)
    else:
        form = CustomerPurchaseRequestReviewForm(instance=purchase_request)

    return render(
        request,
        "core/customer_purchase_request_detail.html",
        {"purchase_request": purchase_request, "form": form},
    )


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """
    Simple role-based dashboard.
    """
    if _is_customer_user(request.user):
        return redirect("customer_dashboard")

    role = getattr(getattr(request.user, "profile", None), "role", None)
    active_branch = _get_active_branch(request.user)
    today = timezone.now().date()

    orders_qs = Order.objects.prefetch_related("payments", "items", "customer")
    payments_qs = Payment.objects.select_related("order__customer", "collector", "payment_receipt")
    inventory_qs = Inventory.objects.select_related("product", "branch")
    customers_qs = Customer.objects.all()

    # High-level metrics used for all dashboards
    top_products = (
        OrderItem.objects.filter(order__status=OrderStatus.COMPLETED)
        .values("product__name")
        .annotate(units_sold=Sum("quantity"), revenue=Sum("subtotal"))
        .order_by("-units_sold", "-revenue")[:5]
    )
    recent_transactions = (
        payments_qs
        .order_by("-paid_at")[:5]
    )
    total_employees = UserProfile.objects.count()
    total_inventory_units = inventory_qs.aggregate(total=Sum("stock"))["total"] or 0
    outstanding_balance_total = sum(
        (order.remaining_balance for order in orders_qs),
        Decimal("0.00"),
    )

    customer_candidates = (
        customers_qs.prefetch_related(
            Prefetch(
                "orders",
                queryset=Order.objects.prefetch_related("payments", "items").order_by("-created_at"),
            )
        )
        .order_by("full_name")
    )
    customer_summaries = [_build_customer_summary(customer) for customer in customer_candidates]
    customers_with_balance = sorted(
        [summary for summary in customer_summaries if summary["outstanding_balance"] > Decimal("0.00")],
        key=lambda summary: summary["outstanding_balance"],
        reverse=True,
    )[:5]

    order_status_counts = {
        "pending": orders_qs.filter(status=OrderStatus.PENDING).count(),
        "reserved": orders_qs.filter(status=OrderStatus.RESERVED).count(),
        "completed": orders_qs.filter(status=OrderStatus.COMPLETED).count(),
        "cancelled": orders_qs.filter(status=OrderStatus.CANCELLED).count(),
    }
    payments_today = payments_qs.filter(paid_at__date=today)
    payments_today_total = payments_today.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    low_stock_count = inventory_qs.filter(available__lte=5).count()
    pending_change_requests = OrderChangeRequest.objects.filter(status=OrderChangeRequestStatus.PENDING).count()
    flagged_payments_count = payments_qs.filter(
        Q(verification_status=Payment.VerificationStatus.REVIEW_REQUIRED) | Q(suspicious_confirmation=True)
    ).count()
    unassigned_collectible_orders = sum(
        (
            1
            for order in orders_qs
            if order.remaining_balance > Decimal("0.00") and not order.assigned_collector_id and order.status != OrderStatus.CANCELLED
        ),
        0,
    )
    approval_backlog = orders_qs.filter(created_by__profile__role=UserRole.SECRETARY, approved_by__isnull=True).count()

    role_focus = {
        UserRole.COLLECTOR: {
            "title": "Collections and cash handling",
            "summary": "Prioritize accurate payment logging, receipt capture, and end-of-day reconciliation.",
        },
        UserRole.SECRETARY: {
            "title": "Customer intake and order encoding",
            "summary": "Keep customer records complete and make sure every order is captured clearly and correctly.",
        },
        UserRole.MANAGER: {
            "title": "Operational control and stock visibility",
            "summary": "Watch branch activity, resolve stock issues, and keep transaction flow and reporting consistent.",
        },
        UserRole.OWNER: {
            "title": "Business oversight and reporting",
            "summary": "Monitor branch health, collection performance, inventory pressure, and staff activity in one place.",
        },
    }.get(
        role,
        {
            "title": "Branch overview",
            "summary": "Use this dashboard to monitor the current state of branch operations and recent activity.",
        },
    )

    context = {
        "role": role,
        "role_focus": role_focus,
        "active_branch": active_branch,
        "total_customers": Customer.objects.count(),
        "total_orders": orders_qs.count(),
        "total_payments": payments_qs.count(),
        "total_employees": total_employees,
        "total_inventory_units": total_inventory_units,
        "outstanding_balance_total": outstanding_balance_total,
        "payments_today_total": payments_today_total,
        "payments_today_count": payments_today.count(),
        "order_status_counts": order_status_counts,
        "low_stock_count": low_stock_count,
        "customers_with_balance_count": len(customers_with_balance),
        "pending_reconciliations": DailyReconciliation.objects.filter(
            status=ReconciliationStatus.PENDING
        ).count(),
        "pending_change_requests": pending_change_requests,
        "flagged_payments_count": flagged_payments_count,
        "unassigned_collectible_orders": unassigned_collectible_orders,
        "approval_backlog": approval_backlog,
        "inventory_low": inventory_qs.filter(available__lte=5)[:10],
        "top_products": top_products,
        "recent_transactions": recent_transactions,
        "customers_with_balance": customers_with_balance,
    }

    if role == UserRole.COLLECTOR:
        collectible_orders = _get_collectible_orders_for_user(request.user)
        collector_payments_today = list(
            payments_qs.filter(collector=request.user, paid_at__date=today).order_by("-paid_at")
        )
        recent_collector_payments = list(
            payments_qs.filter(collector=request.user).order_by("-paid_at")[:5]
        )
        last_reconciliation = (
            DailyReconciliation.objects.filter(collector=request.user).order_by("-date", "-created_at").first()
        )
        context.update(
            {
                "collectible_orders": collectible_orders[:8],
                "collectible_order_count": len(collectible_orders),
                "collector_payments_today": collector_payments_today,
                "collector_payments_today_count": len(collector_payments_today),
                "collector_payments_today_total": sum(
                    (payment.amount for payment in collector_payments_today),
                    Decimal("0.00"),
                ),
                "recent_collector_payments": recent_collector_payments,
                "last_reconciliation": last_reconciliation,
            }
        )

    # Extra aggregates for owner/manager dashboards
    if role in {UserRole.MANAGER, UserRole.OWNER}:
        recent_payments = (
            Payment.objects.select_related("order__customer", "branch", "collector")
            .order_by("-paid_at")[:10]
        )
        context["recent_payments"] = recent_payments

    if role == UserRole.OWNER:
        context["today_payment_total"] = float(payments_today_total)

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
                    payment.collector_submission_ip = _get_client_ip(request)
                    payment.collector_submission_user_agent = request.META.get("HTTP_USER_AGENT", "")[:1000]
                    payment.save(update_fields=["collector_submission_ip", "collector_submission_user_agent"])
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
                return redirect("payment_customer_qr", payment_id=payment.id)
        # Form errors (incl validation) shown below
    else:
        form = PaymentForm(user=request.user)
    return render(request, "core/log_payment.html", {"form": form})


@role_required(UserRole.COLLECTOR, UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def payment_customer_qr(request: HttpRequest, payment_id: int) -> HttpResponse:
    payment = get_object_or_404(
        Payment.objects.select_related("order__customer", "payment_receipt", "collector", "branch"),
        pk=payment_id,
    )

    user_profile = getattr(request.user, "profile", None)
    role = getattr(user_profile, "role", None)
    if role == UserRole.COLLECTOR and payment.collector != request.user:
        messages.error(request, "You can only open QR handoff screens for your own payments.")
        return redirect("dashboard")

    return render(request, "core/payment_customer_qr.html", {"payment": payment})


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

    resolution_form = None
    if role in {UserRole.MANAGER, UserRole.OWNER}:
        resolution_form = PaymentReviewResolutionForm(payment=receipt.payment)

    return render(
        request,
        "core/receipt_detail.html",
        {"receipt": receipt, "resolution_form": resolution_form},
    )


def confirm_payment_by_customer(request: HttpRequest, token: str) -> HttpResponse:
    payment = get_object_or_404(
        Payment.objects.select_related("order__customer", "payment_receipt", "collector", "branch"),
        customer_confirmation_token=token,
    )

    if payment.customer_confirmed_at:
        return render(
            request,
            "core/customer_payment_confirmed.html",
            {"payment": payment, "already_confirmed": True},
        )

    if request.method == "POST":
        form = CustomerPaymentConfirmationForm(request.POST, request.FILES, payment=payment)
        if form.is_valid():
            payment.apply_customer_confirmation(
                customer_name=form.cleaned_data["customer_name"],
                reported_amount=form.cleaned_data["reported_amount"],
                customer_receipt_file=form.cleaned_data["customer_receipt"],
                signature_data=form.cleaned_data["customer_signature"],
                confirmation_ip=_get_client_ip(request),
                confirmation_user_agent=request.META.get("HTTP_USER_AGENT", "")[:1000],
            )
            create_audit_log(
                user=None,
                action="Customer Payment Confirmation",
                instance=payment,
                old_values=None,
                new_values={
                    "customer_confirmation_name": payment.customer_confirmation_name,
                    "customer_reported_amount": str(payment.customer_reported_amount),
                    "verification_status": payment.verification_status,
                    "suspicious_confirmation": payment.suspicious_confirmation,
                    "suspicious_reason": payment.suspicious_reason,
                },
            )
            return render(
                request,
                "core/customer_payment_confirmed.html",
                {"payment": payment, "already_confirmed": False},
            )
    else:
        form = CustomerPaymentConfirmationForm(payment=payment)

    return render(
        request,
        "core/customer_payment_confirmation.html",
        {"payment": payment, "form": form},
    )


@role_required(UserRole.COLLECTOR)
def daily_reconciliation(request: HttpRequest) -> HttpResponse:
    """
    Collector or Secretary records a daily cash reconciliation for their branch.
    """
    active_branch = _get_active_branch(request.user)
    if active_branch is None:
        messages.error(request, "Set up the operating branch before recording reconciliation.")
        return redirect("dashboard")

    if request.method == "POST":
        form = DailyReconciliationForm(request.POST, user=request.user)
        if form.is_valid():
            reconciliation: DailyReconciliation = form.save(commit=False)
            reconciliation.collector = request.user
            reconciliation.branch = active_branch
            if _user_has_role(request.user, UserRole.COLLECTOR):
                reconciliation.system_total = (
                    Payment.objects.filter(collector=request.user, paid_at__date=reconciliation.date)
                    .aggregate(total=Sum("amount"))["total"]
                    or Decimal("0.00")
                )
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
        if _user_has_role(request.user, UserRole.COLLECTOR):
            initial["system_total"] = (
                Payment.objects.filter(collector=request.user, paid_at__date=initial["date"])
                .aggregate(total=Sum("amount"))["total"]
                or Decimal("0.00")
            )
        form = DailyReconciliationForm(initial=initial, user=request.user)
    return render(request, "core/daily_reconciliation.html", {"form": form})


@role_required(UserRole.MANAGER, UserRole.OWNER)
def reconciliation_list(request: HttpRequest) -> HttpResponse:
    reconciliations = list(DailyReconciliation.objects.select_related("branch", "collector").order_by("-date"))
    context = {
        "reconciliations": reconciliations,
        "reconciliation_summary": {
            "count": len(reconciliations),
            "pending": sum((1 for reconciliation in reconciliations if reconciliation.status == ReconciliationStatus.PENDING), 0),
            "approved": sum((1 for reconciliation in reconciliations if reconciliation.status == ReconciliationStatus.APPROVED), 0),
            "total_discrepancy": sum((reconciliation.discrepancy for reconciliation in reconciliations), Decimal("0.00")),
        },
    }
    return render(request, "core/reconciliation_list.html", context)


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
            _apply_inventory_adjustment(request=request, form=form)
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
    current_role = getattr(getattr(request.user, "profile", None), "role", None)
    if active_branch is None:
        messages.error(request, "Set up the operating branch before creating orders.")
        return redirect("dashboard")

    if request.method == "POST":
        form = OrderForm(request.POST, current_branch=active_branch, current_role=current_role)
        formset = OrderItemFormSet(
            request.POST,
            form_kwargs={"user_role": current_role},
            current_branch=active_branch,
            order_status=request.POST.get("status"),
        )
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                order = form.save()
                order.created_by = request.user
                order.last_modified_by = request.user
                if current_role in {UserRole.MANAGER, UserRole.OWNER}:
                    order.approved_by = request.user
                    order.approved_at = timezone.now()
                order.save(update_fields=["created_by", "last_modified_by", "approved_by", "approved_at"])
                formset.instance = order
                formset.save()
                if order.status in {OrderStatus.RESERVED, OrderStatus.COMPLETED}:
                    order._apply_inventory_transition(None, order.status)
                create_audit_log(
                    user=request.user,
                    action="Create Order",
                    instance=order,
                    old_values=None,
                    new_values={
                        "customer_id": order.customer_id,
                        "customer_name": order.customer.full_name,
                        "branch_id": order.branch_id,
                        "status": order.status,
                        "assigned_collector": order.assigned_collector.username if order.assigned_collector else "",
                        "total_amount": str(order.total_amount),
                        "line_items": _serialize_order_items(order),
                    },
                )
            messages.success(request, "Order created.")
            return redirect("order_detail", order_id=order.id)
    else:
        form = OrderForm(current_branch=active_branch, current_role=current_role)
        formset = OrderItemFormSet(
            form_kwargs={"user_role": current_role},
            current_branch=active_branch,
        )
    return render(request, "core/order_form.html", {"form": form, "formset": formset})


@role_required(UserRole.COLLECTOR, UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def order_detail(request: HttpRequest, order_id: int) -> HttpResponse:
    active_branch = _get_active_branch(request.user)
    order_qs = Order.objects.select_related(
        "customer", "branch", "created_by", "last_modified_by", "assigned_collector", "approved_by"
    ).prefetch_related(
        "items__product", "payments__collector", "payments__payment_receipt", "change_requests__requested_by", "change_requests__reviewed_by"
    )
    if active_branch is not None:
        order_qs = order_qs.filter(branch=active_branch)
    order = get_object_or_404(order_qs, pk=order_id)
    role = getattr(getattr(request.user, "profile", None), "role", None)
    if role == UserRole.COLLECTOR and order.assigned_collector_id not in {None, request.user.id}:
        messages.error(request, "You can only access orders assigned to you.")
        return redirect("order_list")
    show_audit_details = role in {UserRole.MANAGER, UserRole.OWNER}
    order_audit_logs = []
    if show_audit_details:
        order_audit_logs = list(
            AuditLog.objects.filter(model_name="Order", object_id=str(order.id)).select_related("user").order_by("-created_at")
        )
    payments = list(order.payments.all().order_by("-paid_at"))
    change_requests = list(order.change_requests.all().order_by("-created_at"))
    context = {
        "order": order,
        "payments": payments,
        "change_requests": change_requests,
        "lifecycle_steps": _get_order_lifecycle(order),
        "show_audit_details": show_audit_details,
        "order_audit_logs": order_audit_logs,
        "change_request_form": OrderChangeRequestForm(current_branch=order.branch),
    }
    return render(request, "core/order_detail.html", context)


@role_required(UserRole.MANAGER, UserRole.OWNER)
def order_manage(request: HttpRequest, order_id: int) -> HttpResponse:
    active_branch = _get_active_branch(request.user)
    order_qs = Order.objects.select_related("customer", "assigned_collector", "branch")
    if active_branch is not None:
        order_qs = order_qs.filter(branch=active_branch)
    order = get_object_or_404(order_qs, pk=order_id)

    if request.method == "POST":
        form = OrderManagementForm(request.POST, instance=order, current_branch=order.branch)
        if form.is_valid():
            old_values = {
                "status": order.status,
                "assigned_collector": order.assigned_collector.username if order.assigned_collector else "",
            }
            updated_order = form.save(commit=False)
            updated_order.last_modified_by = request.user
            updated_order.approved_by = request.user
            updated_order.approved_at = timezone.now()
            updated_order.save()
            create_audit_log(
                user=request.user,
                action="Manage Order",
                instance=updated_order,
                old_values=old_values,
                new_values={
                    "status": updated_order.status,
                    "assigned_collector": updated_order.assigned_collector.username if updated_order.assigned_collector else "",
                    "approved_by": request.user.username,
                },
            )
            messages.success(request, "Order updated.")
            return redirect("order_detail", order_id=updated_order.id)
    else:
        form = OrderManagementForm(instance=order, current_branch=order.branch)
    return render(request, "core/order_manage.html", {"form": form, "order": order})


@role_required(UserRole.SECRETARY)
def order_change_request_create(request: HttpRequest, order_id: int) -> HttpResponse:
    active_branch = _get_active_branch(request.user)
    order_qs = Order.objects.select_related("customer", "branch")
    if active_branch is not None:
        order_qs = order_qs.filter(branch=active_branch)
    order = get_object_or_404(order_qs, pk=order_id)

    if request.method != "POST":
        return redirect("order_detail", order_id=order.id)

    form = OrderChangeRequestForm(request.POST, current_branch=order.branch)
    if form.is_valid():
        change_request = form.save(commit=False)
        change_request.order = order
        change_request.requested_by = request.user
        change_request.save()
        create_audit_log(
            user=request.user,
            action="Create Order Change Request",
            instance=change_request,
            old_values=None,
            new_values={
                "order_id": order.id,
                "requested_status": change_request.requested_status,
                "requested_assigned_collector": change_request.requested_assigned_collector.username if change_request.requested_assigned_collector else "",
                "reason": change_request.reason,
            },
        )
        messages.success(request, "Order change request submitted for manager review.")
    else:
        messages.error(request, form.non_field_errors() or "Unable to submit order change request.")
    return redirect("order_detail", order_id=order.id)


@role_required(UserRole.MANAGER, UserRole.OWNER)
def order_change_request_process(request: HttpRequest, request_id: int, decision: str) -> HttpResponse:
    change_request = get_object_or_404(
        OrderChangeRequest.objects.select_related("order", "requested_assigned_collector", "requested_by"),
        pk=request_id,
    )
    if change_request.status != OrderChangeRequestStatus.PENDING:
        messages.error(request, "This order change request has already been reviewed.")
        return redirect("order_detail", order_id=change_request.order_id)
    if decision not in {"approve", "reject"}:
        return HttpResponseForbidden("Invalid decision")

    change_request.reviewed_by = request.user
    change_request.reviewed_at = timezone.now()
    old_values = {
        "status": change_request.order.status,
        "assigned_collector": change_request.order.assigned_collector.username if change_request.order.assigned_collector else "",
    }
    if decision == "approve":
        if change_request.requested_status:
            change_request.order.status = change_request.requested_status
        change_request.order.assigned_collector = change_request.requested_assigned_collector
        change_request.order.last_modified_by = request.user
        change_request.order.approved_by = request.user
        change_request.order.approved_at = timezone.now()
        change_request.order.save()
        change_request.status = OrderChangeRequestStatus.APPROVED
        create_audit_log(
            user=request.user,
            action="Approve Order Change Request",
            instance=change_request.order,
            old_values=old_values,
            new_values={
                "status": change_request.order.status,
                "assigned_collector": change_request.order.assigned_collector.username if change_request.order.assigned_collector else "",
                "source_request_id": change_request.id,
            },
        )
        messages.success(request, "Order change request approved.")
    else:
        change_request.status = OrderChangeRequestStatus.REJECTED
        messages.info(request, "Order change request rejected.")
    change_request.save()
    return redirect("order_detail", order_id=change_request.order_id)


@role_required(UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def inventory_list(request: HttpRequest) -> HttpResponse:
    return redirect("product_list")


@role_required(UserRole.COLLECTOR, UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def order_list(request: HttpRequest) -> HttpResponse:
    role = getattr(getattr(request.user, "profile", None), "role", None)
    qs = Order.objects.select_related("customer", "branch").prefetch_related("payments", "items__product")
    active_branch = _get_active_branch(request.user)
    if active_branch is not None:
        qs = qs.filter(branch=active_branch)

    status = request.GET.get("status")
    allowed_statuses = None
    if role == UserRole.COLLECTOR:
        allowed_statuses = {OrderStatus.PENDING, OrderStatus.RESERVED}
        qs = qs.filter(status__in=allowed_statuses, assigned_collector=request.user)
    if status and (allowed_statuses is None or status in allowed_statuses):
        qs = qs.filter(status=status)

    qs = qs.order_by("-created_at")
    orders = list(qs)
    if role == UserRole.COLLECTOR:
        orders = [order for order in orders if order.remaining_balance > Decimal("0.00")]
    order_rows = [{"order": order, "lifecycle_steps": _get_order_lifecycle(order)} for order in orders]
    context = {
        "order_rows": order_rows,
        "selected_status": status or "",
        "role": role,
        "order_summary": {
            "count": len(orders),
            "pending": sum((1 for order in orders if order.status == OrderStatus.PENDING), 0),
            "reserved": sum((1 for order in orders if order.status == OrderStatus.RESERVED), 0),
            "completed": sum((1 for order in orders if order.status == OrderStatus.COMPLETED), 0),
            "outstanding_total": sum((order.remaining_balance for order in orders), Decimal("0.00")),
        },
    }
    return render(request, "core/order_list.html", context)


@role_required(UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def product_list(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    category_filter = request.GET.get("category", "").strip()
    stock_filter = request.GET.get("stock", "").strip()
    sales_filter = request.GET.get("sales", "").strip()
    products = Product.objects.select_related("category").all().order_by("name")
    inventory_rows = Inventory.objects.select_related("product", "branch")
    active_branch = _get_active_branch(request.user)
    if active_branch is not None:
        inventory_rows = inventory_rows.filter(branch=active_branch)
    inventory_rows = list(inventory_rows)
    if q:
        products = products.filter(
            Q(name__icontains=q)
            | Q(sku__icontains=q)
            | Q(description__icontains=q)
            | Q(category__name__icontains=q)
        )
    if category_filter:
        products = products.filter(category_id=category_filter)
    products = products.prefetch_related(
        Prefetch("inventories", queryset=Inventory.objects.order_by("id"), to_attr="inventories_cache"),
        Prefetch(
            "orderitem_set",
            queryset=OrderItem.objects.filter(order__status=OrderStatus.COMPLETED).select_related("order", "order__customer"),
            to_attr="completed_orderitems",
        ),
    )
    product_summaries = [_build_product_summary(product) for product in products]
    if stock_filter == "low":
        product_summaries = [summary for summary in product_summaries if summary["inventory_available"] <= 5]
    elif stock_filter == "out":
        product_summaries = [summary for summary in product_summaries if summary["inventory_available"] == 0]
    elif stock_filter == "in":
        product_summaries = [summary for summary in product_summaries if summary["inventory_available"] > 0]

    if sales_filter == "sold":
        product_summaries = [summary for summary in product_summaries if summary["sold_quantity"] > 0]
    elif sales_filter == "unsold":
        product_summaries = [summary for summary in product_summaries if summary["sold_quantity"] == 0]
    if request.GET.get("export") == "csv":
        return _csv_response(
            "catalog_stock_report.csv",
            ["Product", "Category", "SKU", "Price", "Stock", "Available", "Units Sold", "Revenue"],
            [
                [
                    summary["product"].name,
                    summary["product"].category.name if summary["product"].category else "Uncategorized",
                    summary["product"].sku,
                    summary["product"].price,
                    summary["inventory_stock"],
                    summary["inventory_available"],
                    summary["sold_quantity"],
                    summary["revenue_total"],
                ]
                for summary in product_summaries
            ],
        )

    return render(
        request,
        "core/product_list.html",
        {
            "product_summaries": product_summaries,
            "filters": {"q": q, "category": category_filter, "stock": stock_filter, "sales": sales_filter},
            "categories": ProductCategory.objects.order_by("name"),
            "inventory_summary": {
                "product_lines": len(inventory_rows),
                "total_stock": sum((inventory.stock for inventory in inventory_rows), 0),
                "total_reserved": sum((inventory.reserved for inventory in inventory_rows), 0),
                "low_stock_count": sum((1 for inventory in inventory_rows if inventory.available <= 5), 0),
            },
        },
    )


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
                new_values={
                    "category": obj.category.name if obj.category else "",
                    "name": obj.name,
                    "sku": obj.sku,
                    "price": str(obj.price),
                },
            )
            messages.success(request, "Product saved.")
            return redirect("product_list")
    else:
        form = ProductForm(instance=product)
    return render(request, "core/product_form.html", {"form": form, "product": product})


@role_required(UserRole.MANAGER, UserRole.OWNER)
def product_detail(request: HttpRequest, product_id: int) -> HttpResponse:
    active_branch = _get_active_branch(request.user)
    product = get_object_or_404(
        Product.objects.select_related("category").prefetch_related(
            Prefetch("inventories", queryset=Inventory.objects.order_by("id"), to_attr="inventories_cache"),
            Prefetch(
                "orderitem_set",
                queryset=OrderItem.objects.filter(order__status=OrderStatus.COMPLETED).select_related("order", "order__customer"),
                to_attr="completed_orderitems",
            ),
        ),
        pk=product_id,
    )
    if active_branch is None:
        messages.error(request, "Set up the operating branch before managing stock.")
        return redirect("product_list")

    if request.method == "POST":
        adjustment_form = InventoryAdjustmentForm(
            request.POST,
            current_branch=active_branch,
            fixed_product=product,
        )
        if adjustment_form.is_valid():
            _apply_inventory_adjustment(request=request, form=adjustment_form)
            messages.success(request, f"Stock updated for {product.name}.")
            return redirect("product_detail", product_id=product.id)
    else:
        adjustment_form = InventoryAdjustmentForm(
            current_branch=active_branch,
            fixed_product=product,
        )

    summary = _build_product_summary(product)
    branch_inventory = next(
        (inventory for inventory in product.inventories_cache if inventory.branch_id == active_branch.id),
        None,
    )
    recent_adjustments = list(
        product.inventory_adjustments.select_related("created_by", "approved_by", "branch", "audit_log")
        .filter(branch=active_branch)
        .order_by("-created_at")[:10]
    )
    stock_ledger = _build_stock_ledger(product, active_branch)
    recent_sales = sorted(product.completed_orderitems, key=lambda item: item.order.updated_at, reverse=True)[:10]
    return render(
        request,
        "core/product_detail.html",
        {
            "product": product,
            "summary": summary,
            "recent_sales": recent_sales,
            "branch_inventory": branch_inventory,
            "recent_adjustments": recent_adjustments,
            "stock_ledger": stock_ledger,
            "adjustment_form": adjustment_form,
            "active_branch": active_branch,
        },
    )


@role_required(UserRole.OWNER)
def user_list(request: HttpRequest) -> HttpResponse:
    profiles = list(UserProfile.objects.select_related("user", "branch").order_by("user__username"))
    return render(
        request,
        "core/user_list.html",
        {
            "profiles": profiles,
            "user_summary": {
                "count": len(profiles),
                "owners": sum((1 for profile in profiles if profile.role == UserRole.OWNER), 0),
                "managers": sum((1 for profile in profiles if profile.role == UserRole.MANAGER), 0),
                "collectors": sum((1 for profile in profiles if profile.role == UserRole.COLLECTOR), 0),
            },
        },
    )


@role_required(UserRole.MANAGER, UserRole.OWNER)
def employee_list(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    role_filter = request.GET.get("role", "").strip()
    profiles = (
        UserProfile.objects.select_related("user", "branch")
        .annotate(
            payment_count=Count("user__collected_payments", distinct=True),
            reconciliation_count=Count("user__reconciliations", distinct=True),
            adjustment_count=Count("user__created_inventory_adjustments", distinct=True),
        )
        .order_by("role", "user__username")
    )
    if q:
        profiles = profiles.filter(Q(user__username__icontains=q) | Q(user__email__icontains=q))
    if role_filter:
        profiles = profiles.filter(role=role_filter)
    profiles = list(profiles)
    return render(
        request,
        "core/employee_list.html",
        {
            "profiles": profiles,
            "filters": {"q": q, "role": role_filter},
            "roles": UserRole.choices,
            "employee_summary": {
                "count": len(profiles),
                "collector_count": sum((1 for profile in profiles if profile.role == UserRole.COLLECTOR), 0),
                "manager_count": sum((1 for profile in profiles if profile.role == UserRole.MANAGER), 0),
                "payment_count": sum((profile.payment_count for profile in profiles), 0),
            },
        },
    )


@role_required(UserRole.MANAGER, UserRole.OWNER)
def employee_detail(request: HttpRequest, profile_id: int) -> HttpResponse:
    profile = get_object_or_404(UserProfile.objects.select_related("user"), pk=profile_id)
    payments = list(
        profile.user.collected_payments.select_related("order__customer", "payment_receipt").order_by("-paid_at")[:10]
    )
    reconciliations = list(profile.user.reconciliations.order_by("-date")[:10])
    adjustments = list(profile.user.created_inventory_adjustments.select_related("product").order_by("-created_at")[:10])
    audit_logs = AuditLog.objects.filter(user=profile.user).order_by("-created_at")[:15]
    return render(
        request,
        "core/employee_detail.html",
        {
            "profile": profile,
            "payments": payments,
            "reconciliations": reconciliations,
            "adjustments": adjustments,
            "audit_logs": audit_logs,
            "payment_total": sum((payment.amount for payment in payments), Decimal("0.00")),
        },
    )


@role_required(UserRole.MANAGER, UserRole.OWNER)
def audit_log_list(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    model_filter = request.GET.get("model", "").strip()
    action_filter = request.GET.get("action", "").strip()
    actor_filter = request.GET.get("actor", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    logs = AuditLog.objects.select_related("user").order_by("-created_at")
    if q:
        logs = logs.filter(
            Q(action__icontains=q)
            | Q(model_name__icontains=q)
            | Q(object_id__icontains=q)
            | Q(user__username__icontains=q)
        )
    if model_filter:
        logs = logs.filter(model_name=model_filter)
    if action_filter:
        logs = logs.filter(action=action_filter)
    if actor_filter == "system":
        logs = logs.filter(user__isnull=True)
    elif actor_filter:
        logs = logs.filter(user_id=actor_filter)
    if date_from:
        logs = logs.filter(created_at__date__gte=date_from)
    if date_to:
        logs = logs.filter(created_at__date__lte=date_to)

    logs = list(logs[:250])
    context = {
        "logs": logs,
        "filters": {
            "q": q,
            "model": model_filter,
            "action": action_filter,
            "actor": actor_filter,
            "date_from": date_from,
            "date_to": date_to,
        },
        "audit_summary": {
            "count": len(logs),
            "system_events": sum((1 for log in logs if log.user_id is None), 0),
            "payment_events": sum((1 for log in logs if log.model_name == "Payment"), 0),
            "order_events": sum((1 for log in logs if log.model_name == "Order"), 0),
            "today_events": sum((1 for log in logs if log.created_at.date() == timezone.now().date()), 0),
        },
        "model_options": AuditLog.objects.order_by("model_name").values_list("model_name", flat=True).distinct(),
        "action_options": AuditLog.objects.order_by("action").values_list("action", flat=True).distinct(),
        "actors": User.objects.filter(auditlog__isnull=False).distinct().order_by("username"),
    }
    return render(request, "core/audit_log_list.html", context)


@role_required(UserRole.MANAGER, UserRole.OWNER)
def audit_log_detail(request: HttpRequest, log_id: int) -> HttpResponse:
    log = get_object_or_404(AuditLog.objects.select_related("user"), pk=log_id)
    related_logs = list(
        AuditLog.objects.select_related("user")
        .filter(model_name=log.model_name, object_id=log.object_id)
        .exclude(pk=log.pk)
        .order_by("-created_at")[:10]
    )
    return render(
        request,
        "core/audit_log_detail.html",
        {"log": log, "related_logs": related_logs},
    )


@role_required(UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def customer_list(request: HttpRequest) -> HttpResponse:
    active_branch = _get_active_branch(request.user)
    q = request.GET.get("q", "").strip()
    balance_filter = request.GET.get("balance", "").strip()
    customers = Customer.objects.all().order_by("full_name").prefetch_related("orders__payments", "orders__items")
    if active_branch is not None:
        customers = customers.filter(branch=active_branch)
    if q:
        customers = customers.filter(
            Q(full_name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q) | Q(address__icontains=q)
        )
    customer_summaries = []
    for customer in customers:
        customer_summaries.append(_build_customer_summary(customer))
    if balance_filter == "with_balance":
        customer_summaries = [summary for summary in customer_summaries if summary["outstanding_balance"] > Decimal("0.00")]
    elif balance_filter == "paid":
        customer_summaries = [summary for summary in customer_summaries if summary["outstanding_balance"] <= Decimal("0.00")]
    if request.GET.get("export") == "csv":
        return _csv_response(
            "customer_balance_report.csv",
            ["Customer", "Phone", "Email", "Installment Plan", "Orders", "Total Purchased", "Outstanding Balance"],
            [
                [
                    summary["customer"].full_name,
                    summary["customer"].phone,
                    summary["customer"].email,
                    summary["customer"].installment_plan,
                    summary["order_count"],
                    summary["total_purchased"],
                    summary["outstanding_balance"],
                ]
                for summary in customer_summaries
            ],
        )
    return render(
        request,
        "core/customer_list.html",
        {
            "customer_summaries": customer_summaries,
            "filters": {"q": q, "balance": balance_filter},
            "customer_summary": {
                "count": len(customer_summaries),
                "with_balance": sum((1 for summary in customer_summaries if summary["outstanding_balance"] > Decimal("0.00")), 0),
                "total_purchased": sum((summary["total_purchased"] for summary in customer_summaries), Decimal("0.00")),
                "outstanding_total": sum((summary["outstanding_balance"] for summary in customer_summaries), Decimal("0.00")),
            },
        },
    )


@role_required(UserRole.SECRETARY, UserRole.MANAGER, UserRole.OWNER)
def customer_detail(request: HttpRequest, customer_id: int) -> HttpResponse:
    active_branch = _get_active_branch(request.user)
    customer_qs = Customer.objects.prefetch_related("orders__items__product", "orders__payments")
    if active_branch is not None:
        customer_qs = customer_qs.filter(branch=active_branch)
    customer = get_object_or_404(customer_qs, pk=customer_id)
    summary = _build_customer_summary(customer)
    orders = customer.orders.all().order_by("-created_at")
    payments = Payment.objects.filter(order__customer=customer).select_related("collector", "payment_receipt", "order").order_by("-paid_at")
    return render(
        request,
        "core/customer_detail.html",
        {
            "customer": customer,
            "summary": summary,
            "orders": orders,
            "payments": payments,
            "latest_order": orders.first(),
            "latest_payment": payments.first(),
        },
    )


@role_required(UserRole.MANAGER, UserRole.OWNER)
def transaction_list(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    employee = request.GET.get("employee", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    transactions = (
        Payment.objects.select_related("order__customer", "collector", "payment_receipt")
        .order_by("-paid_at")
    )
    if q:
        transactions = transactions.filter(
            Q(order__customer__full_name__icontains=q)
            | Q(collector__username__icontains=q)
            | Q(payment_receipt__receipt_number__icontains=q)
        )
    if employee:
        transactions = transactions.filter(collector_id=employee)
    if date_from:
        transactions = transactions.filter(paid_at__date__gte=date_from)
    if date_to:
        transactions = transactions.filter(paid_at__date__lte=date_to)
    employees = User.objects.filter(collected_payments__isnull=False).distinct().order_by("username")
    transactions = list(transactions)
    if request.GET.get("export") == "csv":
        return _csv_response(
            "transaction_report.csv",
            ["Date", "Receipt", "Customer", "Collector", "Amount", "Remaining Balance", "Verification"],
            [
                [
                    transaction.paid_at,
                    transaction.payment_receipt.receipt_number if getattr(transaction, "payment_receipt", None) else "",
                    transaction.order.customer.full_name,
                    transaction.collector.username,
                    transaction.amount,
                    transaction.balance_after_payment,
                    transaction.get_verification_status_display(),
                ]
                for transaction in transactions
            ],
        )
    return render(
        request,
        "core/transaction_list.html",
        {
            "transactions": transactions,
            "employees": employees,
            "filters": {"q": q, "employee": employee, "date_from": date_from, "date_to": date_to},
            "transaction_summary": {
                "count": len(transactions),
                "total_amount": sum((transaction.amount for transaction in transactions), Decimal("0.00")),
                "outstanding_total": sum((transaction.balance_after_payment for transaction in transactions), Decimal("0.00")),
                "receipt_count": sum((1 for transaction in transactions if getattr(transaction, "payment_receipt", None)), 0),
                "matched_count": sum(
                    (1 for transaction in transactions if transaction.verification_status == Payment.VerificationStatus.MATCHED),
                    0,
                ),
            },
        },
    )


@role_required(UserRole.MANAGER, UserRole.OWNER)
def fraud_review_list(request: HttpRequest) -> HttpResponse:
    status_filter = request.GET.get("status", "").strip()
    risk_filter = request.GET.get("risk", "").strip()

    payments = Payment.objects.select_related(
        "order__customer", "collector", "payment_receipt"
    ).order_by("-created_at")

    if status_filter:
        payments = payments.filter(verification_status=status_filter)
    if risk_filter == "suspicious":
        payments = payments.filter(suspicious_confirmation=True)
    elif risk_filter == "clean":
        payments = payments.filter(suspicious_confirmation=False)

    payments = list(payments)
    if request.GET.get("export") == "csv":
        return _csv_response(
            "fraud_review_report.csv",
            ["Payment", "Customer", "Collector", "Collector Amount", "Customer Amount", "Verification", "Suspicious", "Risk Notes"],
            [
                [
                    payment.payment_receipt.receipt_number if getattr(payment, "payment_receipt", None) else f"Payment #{payment.id}",
                    payment.order.customer.full_name,
                    payment.collector.username,
                    payment.amount,
                    payment.customer_reported_amount or "",
                    payment.get_verification_status_display(),
                    "Yes" if payment.suspicious_confirmation else "No",
                    payment.suspicious_reason,
                ]
                for payment in payments
            ],
        )
    context = {
        "payments": payments,
        "filters": {"status": status_filter, "risk": risk_filter},
        "review_summary": {
            "count": len(payments),
            "pending_customer": sum(
                (1 for payment in payments if payment.verification_status == Payment.VerificationStatus.PENDING_CUSTOMER),
                0,
            ),
            "review_required": sum(
                (1 for payment in payments if payment.verification_status == Payment.VerificationStatus.REVIEW_REQUIRED),
                0,
            ),
            "unresolved_reviews": sum((1 for payment in payments if payment.requires_manager_resolution), 0),
            "suspicious": sum((1 for payment in payments if payment.suspicious_confirmation), 0),
            "matched": sum(
                (1 for payment in payments if payment.verification_status == Payment.VerificationStatus.MATCHED),
                0,
            ),
        },
        "verification_statuses": Payment.VerificationStatus.choices,
    }
    return render(request, "core/fraud_review_list.html", context)


@role_required(UserRole.MANAGER, UserRole.OWNER)
def resolve_payment_review(request: HttpRequest, payment_id: int) -> HttpResponse:
    payment = get_object_or_404(
        Payment.objects.select_related("order__customer", "payment_receipt"),
        pk=payment_id,
    )
    if request.method != "POST":
        return redirect("view_receipt", receipt_id=payment.payment_receipt.id)

    form = PaymentReviewResolutionForm(request.POST, payment=payment)
    if not form.is_valid():
        messages.error(request, "Review decision could not be saved. Please check the form and try again.")
        return render(
            request,
            "core/receipt_detail.html",
            {"receipt": payment.payment_receipt, "resolution_form": form},
            status=400,
        )

    old_values = {
        "manager_resolution_status": payment.manager_resolution_status,
        "manager_resolution_note": payment.manager_resolution_note,
        "manager_resolved_by": payment.manager_resolved_by_id,
        "manager_resolved_at": payment.manager_resolved_at.isoformat() if payment.manager_resolved_at else None,
    }
    payment.resolve_review(
        resolution=form.cleaned_data["resolution"],
        note=form.cleaned_data["note"],
        resolved_by=request.user,
    )
    create_audit_log(
        user=request.user,
        action="Resolve Payment Review",
        instance=payment,
        old_values=old_values,
        new_values={
            "manager_resolution_status": payment.manager_resolution_status,
            "manager_resolution_note": payment.manager_resolution_note,
            "manager_resolved_by": payment.manager_resolved_by_id,
            "manager_resolved_at": payment.manager_resolved_at.isoformat() if payment.manager_resolved_at else None,
        },
    )
    messages.success(request, "Payment review decision saved.")
    return redirect("view_receipt", receipt_id=payment.payment_receipt.id)


@role_required(UserRole.OWNER)
def user_edit(request: HttpRequest, profile_id: int) -> HttpResponse:
    profile = get_object_or_404(UserProfile, pk=profile_id)
    active_branch = _get_active_branch(request.user)
    if request.method == "POST":
        form = UserProfileForm(request.POST, instance=profile)
        if form.is_valid():
            old_values = {
                "username": profile.user.username,
                "email": profile.user.email,
                "first_name": profile.user.first_name,
                "last_name": profile.user.last_name,
                "role": profile.role,
                "branch_id": profile.branch_id,
            }
            obj = form.save()
            if active_branch is not None and obj.branch_id != active_branch.id:
                obj.branch = active_branch
                obj.save(update_fields=["branch"])
            changed_password = bool(form.cleaned_data.get("new_password1"))
            create_audit_log(
                user=request.user,
                action="Update User Profile",
                instance=obj,
                old_values=old_values,
                new_values={
                    "username": obj.user.username,
                    "email": obj.user.email,
                    "first_name": obj.user.first_name,
                    "last_name": obj.user.last_name,
                    "role": obj.role,
                    "branch_id": obj.branch_id,
                    "password_reset": changed_password,
                },
            )
            messages.success(request, "User account updated.")
            return redirect("user_list")
    else:
        form = UserProfileForm(instance=profile)
    return render(request, "core/user_form.html", {"form": form, "profile": profile})

