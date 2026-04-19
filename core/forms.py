from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import UploadedFile
from django.forms import BaseInlineFormSet
from django.utils import timezone

from .models import (
    Customer,
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
    UserProfile,
    UserRole,
    WishlistItem,
)

User = get_user_model()


class ProductPriceSelect(forms.Select):
    def __init__(
        self,
        *args,
        price_map: dict[str, str] | None = None,
        availability_map: dict[str, str] | None = None,
        **kwargs,
    ):
        self.price_map = price_map or {}
        self.availability_map = availability_map or {}
        super().__init__(*args, **kwargs)

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        option_value = "" if value is None else str(value)
        if option_value in self.price_map:
            option["attrs"]["data-price"] = self.price_map[option_value]
        if option_value in self.availability_map:
            option["attrs"]["data-available"] = self.availability_map[option_value]
        return option


def apply_tailwind_classes(form: forms.Form) -> None:
    """
    Apply a consistent UI treatment to default Django widgets so templates
    can render fields directly while still looking presentation-ready.
    """
    base_classes = (
        "w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-sm "
        "text-slate-700 outline-none transition focus:border-brand-500 focus:bg-white "
        "focus:ring-4 focus:ring-brand-200/40"
    )
    checkbox_classes = "h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-300"
    file_classes = (
        "block w-full rounded-xl border border-dashed border-slate-300 bg-white "
        "px-4 py-3 text-sm text-slate-600 file:mr-4 file:rounded-lg file:border-0 "
        "file:bg-slate-900 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-white"
    )

    for field in form.fields.values():
        widget = field.widget
        current = widget.attrs.get("class", "")

        if isinstance(widget, forms.CheckboxInput):
            widget.attrs["class"] = f"{current} {checkbox_classes}".strip()
            continue

        if isinstance(widget, forms.FileInput):
            widget.attrs["class"] = f"{current} {file_classes}".strip()
            continue

        widget.attrs["class"] = f"{current} {base_classes}".strip()


class CustomerForm(forms.ModelForm):
    def __init__(self, *args, current_branch=None, **kwargs):
        self.current_branch = current_branch
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)

    class Meta:
        model = Customer
        fields = ["full_name", "phone", "email", "address", "installment_plan"]

    def save(self, commit=True):
        customer = super().save(commit=False)
        if self.current_branch is not None:
            customer.branch = self.current_branch
        if commit:
            customer.save()
        return customer


class StaffLoginForm(AuthenticationForm):
    username = forms.CharField(label="Username")
    password = forms.CharField(label="Password", widget=forms.PasswordInput(render_value=False))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)


class CustomerSignupForm(UserCreationForm):
    full_name = forms.CharField(max_length=255)
    email = forms.EmailField()
    phone = forms.CharField(max_length=50, required=False)
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    installment_plan = forms.CharField(max_length=255, required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "password1", "password2")

    def __init__(self, *args, branch=None, **kwargs):
        self.branch = branch
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with that email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        full_name = self.cleaned_data["full_name"].strip()
        name_parts = full_name.split(None, 1)
        user.first_name = name_parts[0] if name_parts else ""
        user.last_name = name_parts[1] if len(name_parts) > 1 else ""
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            Customer.objects.create(
                user=user,
                full_name=full_name,
                phone=self.cleaned_data["phone"],
                email=self.cleaned_data["email"],
                address=self.cleaned_data["address"],
                installment_plan=self.cleaned_data["installment_plan"] or "Customer-selected items",
                branch=self.branch,
            )
        return user


class WishlistItemForm(forms.ModelForm):
    class Meta:
        model = WishlistItem
        fields = ["quantity"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)
        self.fields["quantity"].widget.attrs["min"] = "1"


class OrderItemForm(forms.ModelForm):
    def __init__(self, *args, user_role: str | None = None, current_branch=None, **kwargs):
        self.user_role = user_role
        self.current_branch = current_branch
        super().__init__(*args, **kwargs)
        if self.current_branch is not None:
            self.fields["product"].queryset = (
                Product.objects.filter(
                    inventories__branch=self.current_branch,
                    inventories__available__gt=0,
                )
                .distinct()
                .order_by("name")
            )
        product_queryset = self.fields["product"].queryset
        price_map = {str(product.pk): str(product.price) for product in product_queryset}
        availability_map = {}
        if self.current_branch is not None:
            availability_map = {
                str(row["product_id"]): str(row["available"])
                for row in Inventory.objects.filter(
                    branch=self.current_branch,
                    product__in=product_queryset,
                ).values("product_id", "available")
            }
        self.fields["product"].widget = ProductPriceSelect(
            choices=self.fields["product"].choices,
            price_map=price_map,
            availability_map=availability_map,
        )
        apply_tailwind_classes(self)
        self.fields["product"].empty_label = "Select a product"
        if not product_queryset.exists():
            self.fields["product"].help_text = "No products are currently available for sale in this branch. Add stock first."
        # Auto-populate price from product if not set
        if self.instance and self.instance.product_id and not self.instance.price:
            self.initial['price'] = self.instance.product.price

        # Make subtotal read-only in the form (calculated automatically)
        self.fields['subtotal'].required = False
        self.fields['subtotal'].widget.attrs['readonly'] = True
        self.fields['subtotal'].widget.attrs['tabindex'] = '-1'
        self.fields['subtotal'].widget.attrs['class'] = (
            f"{self.fields['subtotal'].widget.attrs.get('class', '')} bg-slate-100 font-semibold text-slate-900"
        ).strip()
        self.fields['quantity'].widget.attrs['min'] = '1'
        self.fields['quantity'].widget.attrs['placeholder'] = 'Qty'
        self.fields['price'].widget.attrs['placeholder'] = '0.00'

        if self.user_role == UserRole.SECRETARY:
            self.fields['price'].widget.attrs['readonly'] = True
            self.fields['price'].widget.attrs['class'] = (
                f"{self.fields['price'].widget.attrs.get('class', '')} bg-slate-100"
            ).strip()
            self.fields['price'].help_text = "Selling price is locked to the product catalog for secretary accounts."

    class Meta:
        model = OrderItem
        fields = ["product", "quantity", "price", "subtotal"]

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get('product')
        quantity = cleaned_data.get('quantity')
        price = cleaned_data.get('price')

        # Auto-fill price from product if not provided
        if product and not price:
            cleaned_data['price'] = product.price
            price = product.price

        # Secretaries cannot alter catalog pricing during order encoding.
        if product and self.user_role == UserRole.SECRETARY:
            cleaned_data['price'] = product.price
            price = product.price

        # Calculate subtotal
        if price and quantity:
            cleaned_data['subtotal'] = price * quantity

        return cleaned_data


class BaseOrderItemFormSet(BaseInlineFormSet):
    def __init__(self, *args, current_branch=None, order_status: str | None = None, **kwargs):
        self.current_branch = current_branch
        self.order_status = order_status
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["current_branch"] = self.current_branch
        return kwargs

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        requested_quantities: dict[int, int] = {}
        seen_products: dict[int, forms.Form] = {}
        duplicate_found = False
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            product = form.cleaned_data.get("product")
            quantity = form.cleaned_data.get("quantity")
            if not product or not quantity:
                continue
            if product.id in seen_products:
                form.add_error("product", "This product is already selected in another line item.")
                duplicate_found = True
            else:
                seen_products[product.id] = form
            requested_quantities[product.id] = requested_quantities.get(product.id, 0) + quantity

        if duplicate_found:
            raise forms.ValidationError("Each product can only be added once per order.")

        if not requested_quantities or self.current_branch is None:
            return

        inventory_map = {
            inventory.product_id: inventory.available
            for inventory in Inventory.objects.filter(
                branch=self.current_branch,
                product_id__in=requested_quantities.keys(),
            )
        }

        for product_id, requested_qty in requested_quantities.items():
            available_qty = inventory_map.get(product_id, 0)
            if requested_qty > available_qty:
                product = Product.objects.filter(pk=product_id).first()
                product_name = product.name if product else f"Product #{product_id}"
                if self.order_status == OrderStatus.RESERVED:
                    raise forms.ValidationError(
                        f"{product_name}: requested quantity {requested_qty} exceeds available stock of {available_qty}. "
                        "Reduce the quantity or save the order as Pending."
                    )


OrderItemFormSet = forms.inlineformset_factory(
    Order, 
    OrderItem, 
    formset=BaseOrderItemFormSet,
    form=OrderItemForm, 
    extra=0, 
    can_delete=True,
    min_num=1,
    validate_min=True
)


class OrderForm(forms.ModelForm):
    def __init__(self, *args, current_branch=None, current_role: str | None = None, **kwargs):
        self.current_branch = current_branch
        self.current_role = current_role
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)
        if self.current_branch is not None:
            self.fields["customer"].queryset = Customer.objects.filter(branch=self.current_branch).order_by("full_name")
            collector_profiles = UserProfile.objects.filter(branch=self.current_branch, role=UserRole.COLLECTOR).select_related("user")
            self.fields["assigned_collector"].queryset = User.objects.filter(
                id__in=[profile.user_id for profile in collector_profiles]
            ).order_by("username")
        if self.current_role == UserRole.SECRETARY:
            self.fields["status"].choices = [
                (OrderStatus.PENDING, OrderStatus.PENDING.label),
                (OrderStatus.RESERVED, OrderStatus.RESERVED.label),
            ]
            self.fields["assigned_collector"].widget = forms.HiddenInput()
            self.fields["assigned_collector"].required = False
        else:
            self.fields["assigned_collector"].required = False
            self.fields["assigned_collector"].help_text = "Optional: assign this order to a specific collector."

    class Meta:
        model = Order
        fields = ["customer", "status", "assigned_collector"]

    def clean_customer(self):
        customer = self.cleaned_data["customer"]
        if self.current_branch is not None and customer.branch_id != self.current_branch.id:
            raise forms.ValidationError("Customer must belong to the active branch.")
        return customer

    def clean_status(self):
        status = self.cleaned_data["status"]
        if self.current_role == UserRole.SECRETARY and status not in {OrderStatus.PENDING, OrderStatus.RESERVED}:
            raise forms.ValidationError("Secretary accounts can only create pending or reserved orders.")
        return status

    def clean_assigned_collector(self):
        collector = self.cleaned_data.get("assigned_collector")
        if collector and (not hasattr(collector, "profile") or collector.profile.role != UserRole.COLLECTOR):
            raise forms.ValidationError("Assigned user must be a collector account.")
        if collector and self.current_branch is not None and collector.profile.branch_id != self.current_branch.id:
            raise forms.ValidationError("Assigned collector must belong to the active branch.")
        return collector

    def save(self, commit=True):
        order = super().save(commit=False)
        if self.current_branch is not None:
            order.branch = self.current_branch
        if commit:
            order.save()
        return order


class PaymentForm(forms.ModelForm):
    """
    Collector-facing form for logging payments.
    Enforces mandatory receipt photo and builds the required upload path:
    /uploads/branch_<id>/YYYY-MM-DD/receipt_<id>.jpg
    """

    class Meta:
        model = Payment
        fields = ["order", "amount", "paid_at", "receipt"]

    def __init__(self, *args, user: User | None = None, **kwargs) -> None:
        self.user = user
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)
        self.fields["receipt"].required = True
        self.fields["receipt"].label = "Collector Receipt Photo"
        self.fields["paid_at"].widget = forms.DateTimeInput(
            attrs={"type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        )
        self.fields["paid_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        paid_at_value = self.initial.get("paid_at") or getattr(self.instance, "paid_at", None) or timezone.localtime()
        if timezone.is_aware(paid_at_value):
            paid_at_value = timezone.localtime(paid_at_value)
        self.initial["paid_at"] = paid_at_value.strftime("%Y-%m-%dT%H:%M")
        
        # Limit orders to those with remaining balance for collectors
        if user and hasattr(user, 'profile') and user.profile.branch:
            # Show active branch orders only; remaining balance is validated below.
            branch_orders = user.profile.branch.orders.filter(
                status__in=[OrderStatus.PENDING, OrderStatus.RESERVED],
                assigned_collector=user,
            )
            self.fields['order'].queryset = branch_orders
        else:
            self.fields['order'].queryset = Order.objects.all()

    def clean(self):
        cleaned_data = super().clean()
        order = cleaned_data.get('order')
        amount = cleaned_data.get('amount')

        if order and amount:
            remaining = order.remaining_balance
            if amount > remaining:
                raise forms.ValidationError(
                    f"Payment amount ({amount}) exceeds remaining balance ({remaining})."
                )
            if amount <= 0:
                raise forms.ValidationError("Payment amount must be greater than zero.")

        return cleaned_data

    def save(self, commit: bool = True) -> Payment:
        # Block editing existing payments
        if self.instance.pk:
            raise forms.ValidationError("Payments are immutable and cannot be edited.")

        receipt_file: UploadedFile | None = self.cleaned_data.get("receipt")
        if not receipt_file:
            raise forms.ValidationError("Receipt photo is required.")

        payment: Payment = super().save(commit=False)

        # Attach branch and collector automatically
        if self.user and hasattr(self.user, "profile") and self.user.profile.branch:
            payment.branch = self.user.profile.branch
        elif not payment.branch_id and payment.order_id:
            payment.branch = payment.order.branch

        if self.user:
            payment.collector = self.user

        # Temporarily clear receipt, save to get an ID
        payment.receipt = None
        if commit:
            payment.save()

        # Now build the final path and save the file to that path
        today = timezone.localdate().strftime("%Y-%m-%d")
        branch_id = payment.branch_id or 0
        filename = f"receipt_{payment.id}.jpg"
        relative_path = f"uploads/branch_{branch_id}/{today}/{filename}"

        payment.receipt.save(relative_path, receipt_file, save=False)
        payment.save(update_fields=['receipt'])

        return payment


class PaymentReviewResolutionForm(forms.Form):
    resolution = forms.ChoiceField(choices=Payment.ManagerResolutionStatus.choices)
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Record the basis for your decision so review outcomes stay auditable.",
    )

    def __init__(self, *args, payment: Payment, **kwargs):
        self.payment = payment
        super().__init__(*args, **kwargs)
        self.fields["resolution"].choices = [
            (Payment.ManagerResolutionStatus.ACCEPTED, Payment.ManagerResolutionStatus.ACCEPTED.label),
            (Payment.ManagerResolutionStatus.DISPUTED, Payment.ManagerResolutionStatus.DISPUTED.label),
            (Payment.ManagerResolutionStatus.FOLLOW_UP, Payment.ManagerResolutionStatus.FOLLOW_UP.label),
        ]
        self.fields["resolution"].initial = (
            payment.manager_resolution_status
            if payment.manager_resolution_status != Payment.ManagerResolutionStatus.UNRESOLVED
            else Payment.ManagerResolutionStatus.ACCEPTED
        )
        self.fields["note"].initial = payment.manager_resolution_note
        apply_tailwind_classes(self)


class DailyReconciliationForm(forms.ModelForm):
    def __init__(self, *args, user: User | None = None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)
        if user and hasattr(user, "profile") and user.profile.role == UserRole.COLLECTOR:
            self.fields["system_total"].widget = forms.HiddenInput()
            self.fields["system_total"].required = False
            self.fields["system_total"].initial = Decimal("0.00")
            self.fields["cash_counted"].label = "Cash You Are Turning Over"
            self.fields["date"].help_text = "Choose the date for the cash collection you are reporting."
            self.fields["cash_counted"].help_text = "Enter only the physical cash currently in your possession for the selected day."

    class Meta:
        model = DailyReconciliation
        fields = ["system_total", "cash_counted", "date"]


class CustomerPaymentConfirmationForm(forms.Form):
    customer_name = forms.CharField(max_length=255)
    reported_amount = forms.DecimalField(max_digits=12, decimal_places=2)
    customer_receipt = forms.ImageField()
    customer_signature = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, payment: Payment | None = None, **kwargs):
        self.payment = payment
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)
        self.fields["customer_name"].label = "Customer Name"
        self.fields["reported_amount"].label = "Amount Confirmed by Customer"
        self.fields["customer_receipt"].label = "Customer Receipt Photo"
        self.fields["customer_signature"].label = "Customer Signature"
        if payment is not None:
            self.fields["reported_amount"].initial = payment.amount

    def clean_reported_amount(self):
        amount = self.cleaned_data["reported_amount"]
        if amount <= 0:
            raise forms.ValidationError("Confirmed amount must be greater than zero.")
        return amount

    def clean_customer_signature(self):
        signature = self.cleaned_data["customer_signature"].strip()
        if not signature:
            raise forms.ValidationError("Customer signature is required.")
        return signature


class InventoryAdjustmentForm(forms.ModelForm):
    def __init__(self, *args, current_branch=None, fixed_product=None, **kwargs):
        self.current_branch = current_branch
        self.fixed_product = fixed_product
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)
        if self.fixed_product is not None:
            self.fields["product"].queryset = Product.objects.filter(pk=self.fixed_product.pk)
            self.fields["product"].initial = self.fixed_product
            self.fields["product"].widget = forms.HiddenInput()

    class Meta:
        model = InventoryAdjustment
        fields = ["product", "quantity", "reason"]

    def clean_product(self):
        if self.fixed_product is not None:
            return self.fixed_product
        return self.cleaned_data["product"]

    def save(self, commit=True):
        adjustment = super().save(commit=False)
        if self.current_branch is not None:
            adjustment.branch = self.current_branch
        if commit:
            adjustment.save()
        return adjustment


class ProductForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)
        self.fields["category"].queryset = ProductCategory.objects.order_by("name")

    class Meta:
        model = Product
        fields = ["category", "name", "sku", "description", "price"]


class OrderManagementForm(forms.ModelForm):
    def __init__(self, *args, current_branch=None, **kwargs):
        self.current_branch = current_branch
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)
        if current_branch is not None:
            collector_profiles = UserProfile.objects.filter(branch=current_branch, role=UserRole.COLLECTOR).select_related("user")
            self.fields["assigned_collector"].queryset = User.objects.filter(
                id__in=[profile.user_id for profile in collector_profiles]
            ).order_by("username")
        self.fields["assigned_collector"].required = False

    class Meta:
        model = Order
        fields = ["status", "assigned_collector"]

    def clean_assigned_collector(self):
        collector = self.cleaned_data.get("assigned_collector")
        if collector and (not hasattr(collector, "profile") or collector.profile.role != UserRole.COLLECTOR):
            raise forms.ValidationError("Assigned user must be a collector account.")
        if collector and self.current_branch is not None and collector.profile.branch_id != self.current_branch.id:
            raise forms.ValidationError("Assigned collector must belong to the active branch.")
        return collector


class OrderChangeRequestForm(forms.ModelForm):
    def __init__(self, *args, current_branch=None, **kwargs):
        self.current_branch = current_branch
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)
        if current_branch is not None:
            collector_profiles = UserProfile.objects.filter(branch=current_branch, role=UserRole.COLLECTOR).select_related("user")
            self.fields["requested_assigned_collector"].queryset = User.objects.filter(
                id__in=[profile.user_id for profile in collector_profiles]
            ).order_by("username")
        self.fields["requested_status"].required = False
        self.fields["requested_assigned_collector"].required = False

    class Meta:
        model = OrderChangeRequest
        fields = ["requested_status", "requested_assigned_collector", "reason"]

    def clean_requested_assigned_collector(self):
        collector = self.cleaned_data.get("requested_assigned_collector")
        if collector and (not hasattr(collector, "profile") or collector.profile.role != UserRole.COLLECTOR):
            raise forms.ValidationError("Assigned user must be a collector account.")
        if collector and self.current_branch is not None and collector.profile.branch_id != self.current_branch.id:
            raise forms.ValidationError("Assigned collector must belong to the active branch.")
        return collector

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("requested_status") and not cleaned_data.get("requested_assigned_collector"):
            raise forms.ValidationError("Request at least one change: status or assigned collector.")
        return cleaned_data


class UserProfileForm(forms.ModelForm):
    """
    Owner-facing form to manage user roles and branch assignments.
    """

    username = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    role = forms.ChoiceField(choices=UserRole.choices)
    new_password1 = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="New Password",
    )
    new_password2 = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="Confirm New Password",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)
        if self.instance.pk:
            self.fields["username"].initial = self.instance.user.username
            self.fields["email"].initial = self.instance.user.email
            self.fields["first_name"].initial = self.instance.user.first_name
            self.fields["last_name"].initial = self.instance.user.last_name

    class Meta:
        model = UserProfile
        fields = ["role"]

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        user_qs = User.objects.filter(username__iexact=username)
        if self.instance.pk:
            user_qs = user_qs.exclude(pk=self.instance.user_id)
        if user_qs.exists():
            raise forms.ValidationError("A user with that username already exists.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if not email:
            return email
        user_qs = User.objects.filter(email__iexact=email)
        if self.instance.pk:
            user_qs = user_qs.exclude(pk=self.instance.user_id)
        if user_qs.exists():
            raise forms.ValidationError("A user with that email already exists.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("new_password1", "")
        password2 = cleaned_data.get("new_password2", "")
        if password1 or password2:
            if password1 != password2:
                raise forms.ValidationError("The new passwords do not match.")
            if len(password1) < 8:
                raise forms.ValidationError("The new password must be at least 8 characters long.")
        return cleaned_data

    def save(self, commit=True):
        profile = super().save(commit=False)
        user = profile.user
        user.username = self.cleaned_data["username"]
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]

        password = self.cleaned_data.get("new_password1")
        if password:
            user.set_password(password)

        if commit:
            user.save()
            profile.save()
        return profile



