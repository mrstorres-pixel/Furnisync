from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django import forms
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import UploadedFile

from .models import Customer, DailyReconciliation, InventoryAdjustment, Order, OrderItem, Payment, Product, UserProfile, UserRole

User = get_user_model()


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


class OrderItemForm(forms.ModelForm):
    class Meta:
        model = OrderItem
        fields = ["product", "quantity", "price", "subtotal"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)
        # Auto-populate price from product if not set
        if self.instance and self.instance.product_id and not self.instance.price:
            self.initial['price'] = self.instance.product.price
        
        # Make subtotal read-only in the form (calculated automatically)
        self.fields['subtotal'].required = False
        self.fields['subtotal'].widget.attrs['readonly'] = True

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get('product')
        quantity = cleaned_data.get('quantity')
        price = cleaned_data.get('price')

        # Auto-fill price from product if not provided
        if product and not price:
            cleaned_data['price'] = product.price
            price = product.price

        # Calculate subtotal
        if price and quantity:
            cleaned_data['subtotal'] = price * quantity

        return cleaned_data


OrderItemFormSet = forms.inlineformset_factory(
    Order, 
    OrderItem, 
    form=OrderItemForm, 
    extra=3, 
    can_delete=True,
    min_num=1,
    validate_min=True
)


class OrderForm(forms.ModelForm):
    def __init__(self, *args, current_branch=None, **kwargs):
        self.current_branch = current_branch
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)
        if self.current_branch is not None:
            self.fields["customer"].queryset = Customer.objects.filter(branch=self.current_branch).order_by("full_name")

    class Meta:
        model = Order
        fields = ["customer", "status"]

    def clean_customer(self):
        customer = self.cleaned_data["customer"]
        if self.current_branch is not None and customer.branch_id != self.current_branch.id:
            raise forms.ValidationError("Customer must belong to the active branch.")
        return customer

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
        self.fields["receipt"].label = "Receipt Photo"
        
        # Limit orders to those with remaining balance for collectors
        if user and hasattr(user, 'profile') and user.profile.branch:
            # Show orders from collector's branch that have remaining balance
            branch_orders = user.profile.branch.orders.all()
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
        today = date.today().strftime("%Y-%m-%d")
        branch_id = payment.branch_id or 0
        filename = f"receipt_{payment.id}.jpg"
        relative_path = f"uploads/branch_{branch_id}/{today}/{filename}"

        payment.receipt.save(relative_path, receipt_file, save=False)
        payment.save(update_fields=['receipt'])

        return payment


class DailyReconciliationForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)

    class Meta:
        model = DailyReconciliation
        fields = ["system_total", "cash_counted", "date"]


class InventoryAdjustmentForm(forms.ModelForm):
    def __init__(self, *args, current_branch=None, **kwargs):
        self.current_branch = current_branch
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)

    class Meta:
        model = InventoryAdjustment
        fields = ["product", "quantity", "reason"]

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

    class Meta:
        model = Product
        fields = ["name", "sku", "description", "price"]


class UserProfileForm(forms.ModelForm):
    """
    Owner-facing form to manage user roles and branch assignments.
    """

    role = forms.ChoiceField(choices=UserRole.choices)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind_classes(self)

    class Meta:
        model = UserProfile
        fields = ["role"]


