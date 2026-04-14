from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import (
    Branch,
    Customer,
    Inventory,
    InventoryAdjustment,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    Product,
    UserProfile,
    UserRole,
)

User = get_user_model()


class Command(BaseCommand):
    help = "Seed demo data for defense presentation."

    def handle(self, *args, **options):
        with transaction.atomic():
            branch, _ = Branch.objects.get_or_create(
                name="Main Showroom",
                defaults={"address": "123 Demo Avenue"},
            )

            owner = self._ensure_user("owner", "owner@furniture.com", "password123", UserRole.OWNER, branch)
            manager = self._ensure_user("manager", "manager@furniture.com", "password123", UserRole.MANAGER, branch)
            secretary = self._ensure_user("secretary", "secretary@furniture.com", "password123", UserRole.SECRETARY, branch)
            collector = self._ensure_user("collector", "collector@furniture.com", "password123", UserRole.COLLECTOR, branch)

            products = [
                ("Sofa Set Deluxe", "SOFA-001", "Premium family sofa set", Decimal("25999.00"), 8),
                ("Dining Table Oak", "DINE-002", "Six-seater oak dining table", Decimal("18999.00"), 5),
                ("Queen Bed Frame", "BED-003", "Modern upholstered queen bed", Decimal("21999.00"), 7),
                ("Wardrobe Classic", "WARD-004", "Four-door wardrobe cabinet", Decimal("16999.00"), 4),
                ("Office Desk Pro", "DESK-005", "Executive office desk", Decimal("12499.00"), 9),
            ]

            product_map: dict[str, Product] = {}
            for name, sku, description, price, stock in products:
                product, _ = Product.objects.get_or_create(
                    sku=sku,
                    defaults={"name": name, "description": description, "price": price},
                )
                product.name = name
                product.description = description
                product.price = price
                product.save()
                inventory, _ = Inventory.objects.get_or_create(product=product, branch=branch)
                inventory.stock = stock
                inventory.reserved = 0
                inventory.recalculate_available()
                inventory.save()
                product_map[sku] = product

            customers = [
                ("Maria Santos", "09171234567", "maria@example.com", "North District", "12-month installment plan"),
                ("John Rivera", "09182345678", "john@example.com", "East District", "6-month installment plan"),
                ("Angela Cruz", "09193456789", "angela@example.com", "West District", "Cash / split payment"),
                ("David Reyes", "09204567890", "david@example.com", "South District", "9-month installment plan"),
            ]

            customer_map: dict[str, Customer] = {}
            for full_name, phone, email, address, installment_plan in customers:
                customer, _ = Customer.objects.get_or_create(
                    full_name=full_name,
                    defaults={
                        "phone": phone,
                        "email": email,
                        "address": address,
                        "branch": branch,
                        "installment_plan": installment_plan,
                    },
                )
                customer.phone = phone
                customer.email = email
                customer.address = address
                customer.branch = branch
                customer.installment_plan = installment_plan
                customer.save()
                customer_map[full_name] = customer

            self._create_demo_order(
                customer=customer_map["Maria Santos"],
                branch=branch,
                items=[("SOFA-001", 1), ("DESK-005", 1)],
                status=OrderStatus.COMPLETED,
                payments=[(collector, Decimal("15000.00")), (collector, Decimal("23498.00"))],
            )
            self._create_demo_order(
                customer=customer_map["John Rivera"],
                branch=branch,
                items=[("DINE-002", 1)],
                status=OrderStatus.RESERVED,
                payments=[(collector, Decimal("5000.00"))],
            )
            self._create_demo_order(
                customer=customer_map["Angela Cruz"],
                branch=branch,
                items=[("BED-003", 1), ("WARD-004", 1)],
                status=OrderStatus.COMPLETED,
                payments=[(collector, Decimal("38998.00"))],
            )
            self._create_demo_order(
                customer=customer_map["David Reyes"],
                branch=branch,
                items=[("SOFA-001", 1)],
                status=OrderStatus.PENDING,
                payments=[],
            )

            InventoryAdjustment.objects.get_or_create(
                product=product_map["WARD-004"],
                branch=branch,
                quantity=2,
                reason="Demo restock",
                defaults={
                    "approved": True,
                    "created_by": manager,
                    "approved_by": manager,
                    "approved_at": timezone.now(),
                },
            )

        self.stdout.write(self.style.SUCCESS("Demo data seeded successfully."))
        self.stdout.write("Demo accounts: owner / manager / secretary / collector")
        self.stdout.write("Password for all demo accounts: password123")

    def _ensure_user(self, username: str, email: str, password: str, role: str, branch: Branch):
        user, _ = User.objects.get_or_create(username=username, defaults={"email": email})
        user.email = email
        user.set_password(password)
        user.save()
        UserProfile.objects.update_or_create(user=user, defaults={"role": role, "branch": branch})
        return user

    def _create_demo_order(self, *, customer: Customer, branch: Branch, items: list[tuple[str, int]], status: str, payments: list[tuple[User, Decimal]]):
        order = Order.objects.filter(customer=customer, status=status).order_by("id").first()
        if order is None:
            order = Order.objects.create(customer=customer, branch=branch, status=OrderStatus.PENDING)
        if not order.items.exists():
            for sku, quantity in items:
                product = Product.objects.get(sku=sku)
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=quantity,
                    price=product.price,
                    subtotal=product.price * quantity,
                )
        if order.status != status:
            order.status = status
            order.save()
        for collector, amount in payments:
            Payment.objects.get_or_create(
                order=order,
                amount=amount,
                collector=collector,
                branch=branch,
                defaults={"paid_at": timezone.now()},
            )
