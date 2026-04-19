from django.urls import path

from . import views

urlpatterns = [
    path("", views.landing_page, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("customer/", views.customer_dashboard, name="customer_dashboard"),
    path("customer/shop/", views.customer_product_list, name="customer_product_list"),
    path("customer/shop/<int:product_id>/", views.customer_product_detail, name="customer_product_detail"),
    path("customer/wishlist/", views.customer_wishlist, name="customer_wishlist"),
    path("customer/wishlist/<int:item_id>/remove/", views.customer_wishlist_remove, name="customer_wishlist_remove"),
    path("customer/account/", views.customer_account, name="customer_account"),
    path("customer/orders/", views.customer_orders, name="customer_orders"),
    # Customers & orders
    path("customers/new/", views.create_customer, name="create_customer"),
    path("customers/", views.customer_list, name="customer_list"),
    path("customers/<int:customer_id>/", views.customer_detail, name="customer_detail"),
    path("orders/new/", views.create_order, name="create_order"),
    path("orders/", views.order_list, name="order_list"),
    path("orders/<int:order_id>/", views.order_detail, name="order_detail"),
    path("orders/<int:order_id>/manage/", views.order_manage, name="order_manage"),
    path("orders/<int:order_id>/change-request/", views.order_change_request_create, name="order_change_request_create"),
    path("order-change-requests/<int:request_id>/<str:decision>/", views.order_change_request_process, name="order_change_request_process"),
    path("inventory/", views.inventory_list, name="inventory_list"),
    # Payments & Receipts
    path("payments/log/", views.log_payment, name="log_payment"),
    path("payments/confirm/<str:token>/", views.confirm_payment_by_customer, name="confirm_payment_by_customer"),
    path("payments/<int:payment_id>/customer-qr/", views.payment_customer_qr, name="payment_customer_qr"),
    path("payments/<int:payment_id>/resolve-review/", views.resolve_payment_review, name="resolve_payment_review"),
    path("receipts/<int:receipt_id>/", views.view_receipt, name="view_receipt"),
    # Reconciliation
    path("reconciliation/daily/", views.daily_reconciliation, name="daily_reconciliation"),
    path("reconciliation/", views.reconciliation_list, name="reconciliation_list"),
    path(
        "reconciliation/<int:reconciliation_id>/<str:decision>/",
        views.handle_reconciliation,
        name="handle_reconciliation",
    ),
    # Inventory adjustments
    path(
        "inventory/adjustment/new/",
        views.create_inventory_adjustment,
        name="inventory_adjustment",
    ),
    # Master data & user management
    path("products/", views.product_list, name="product_list"),
    path("products/new/", views.product_edit, name="product_create"),
    path("products/<int:product_id>/edit/", views.product_edit, name="product_edit"),
    path("products/<int:product_id>/", views.product_detail, name="product_detail"),
    path("employees/", views.employee_list, name="employee_list"),
    path("employees/<int:profile_id>/", views.employee_detail, name="employee_detail"),
    path("transactions/", views.transaction_list, name="transaction_list"),
    path("audit-trail/", views.audit_log_list, name="audit_log_list"),
    path("audit-trail/<int:log_id>/", views.audit_log_detail, name="audit_log_detail"),
    path("fraud-review/", views.fraud_review_list, name="fraud_review_list"),
    path("users/", views.user_list, name="user_list"),
    path("users/<int:profile_id>/edit/", views.user_edit, name="user_edit"),
]

