from django.contrib import admin
from django.contrib.auth.models import Group

from .models import (
    AuditLog,
    Customer,
    CustomerPurchaseRequest,
    DailyReconciliation,
    Inventory,
    InventoryAdjustment,
    Order,
    OrderChangeRequest,
    OrderItem,
    Payment,
    Product,
    ProductCategory,
    Receipt,
    UserProfile,
    WishlistItem,
)

# Unregister Django's default Group model - we use role-based access only
admin.site.unregister(Group)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role")
    list_filter = ("role",)
    search_fields = ("user__username", "user__email")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "branch", "user", "installment_plan", "created_at")
    list_filter = ("branch",)
    search_fields = ("full_name", "phone", "email", "user__username")


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "description")
    search_fields = ("name", "description")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "sku", "price")
    list_filter = ("category",)
    search_fields = ("name", "sku", "category__name")


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ("customer", "product", "quantity", "created_at")
    list_filter = ("customer__branch",)
    search_fields = ("customer__full_name", "product__name", "product__sku")


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ("product", "branch", "stock", "reserved", "available")
    list_filter = ("branch",)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('subtotal',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "branch", "status", "assigned_collector", "created_by", "created_at", "get_total_amount")
    list_filter = ("branch", "status")
    inlines = [OrderItemInline]
    
    def get_total_amount(self, obj):
        return f"PHP {obj.total_amount}"
    get_total_amount.short_description = "Total Amount"


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "branch", "collector", "amount", "paid_at", "verification_status", "manager_resolution_status")
    list_filter = ("branch", "collector", "verification_status", "manager_resolution_status")
    readonly_fields = (
        "order", "branch", "collector", "amount", "paid_at", "receipt",
        "customer_receipt", "customer_confirmation_name", "customer_reported_amount",
        "customer_confirmed_at", "customer_confirmation_token", "verification_status",
        "collector_submission_ip", "collector_submission_user_agent",
        "customer_confirmation_ip", "customer_confirmation_user_agent",
        "customer_signature_data", "suspicious_confirmation", "suspicious_reason",
        "manager_resolution_status", "manager_resolution_note", "manager_resolved_by",
        "manager_resolved_at", "created_at"
    )

    def has_change_permission(self, request, obj=None):
        # Disallow edits to payments - they are immutable
        if obj:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        # Disallow deletion of payments
        return False


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("receipt_number", "payment", "order", "branch", "collector", "total_paid", "remaining_balance", "created_at")
    list_filter = ("branch", "collector", "created_at")
    search_fields = ("receipt_number", "payment__id", "order__id")
    readonly_fields = ("receipt_number", "payment", "order", "branch", "collector", "total_paid", "remaining_balance", "created_at")
    
    def has_add_permission(self, request):
        # Receipts are auto-generated, cannot be manually created
        return False
    
    def has_change_permission(self, request, obj=None):
        # Receipts are immutable
        return False
    
    def has_delete_permission(self, request, obj=None):
        # Receipts cannot be deleted
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "model_name", "object_id", "user", "created_at")
    list_filter = ("model_name", "action")
    search_fields = ("model_name", "object_id", "action")
    readonly_fields = ("user", "action", "model_name", "object_id", "old_values", "new_values", "created_at")


@admin.register(DailyReconciliation)
class DailyReconciliationAdmin(admin.ModelAdmin):
    list_display = ("branch", "collector", "date", "system_total", "cash_counted", "discrepancy", "status")
    list_filter = ("branch", "status")


@admin.register(InventoryAdjustment)
class InventoryAdjustmentAdmin(admin.ModelAdmin):
    list_display = ("product", "branch", "quantity", "approved", "created_by", "approved_by", "created_at")
    list_filter = ("branch", "approved")


@admin.register(OrderChangeRequest)
class OrderChangeRequestAdmin(admin.ModelAdmin):
    list_display = ("order", "requested_by", "requested_status", "requested_assigned_collector", "status", "created_at")
    list_filter = ("status", "requested_status")


@admin.register(CustomerPurchaseRequest)
class CustomerPurchaseRequestAdmin(admin.ModelAdmin):
    list_display = ("customer", "product", "branch", "quantity", "status", "reviewed_by", "created_at")
    list_filter = ("branch", "status", "created_at")
    search_fields = ("customer__full_name", "product__name", "product__sku")

