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
        self.fields["receipt"].label = "Collector Receipt Photo"
        
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


