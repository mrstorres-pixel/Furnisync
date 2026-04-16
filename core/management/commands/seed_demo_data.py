from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import (
    Branch,
    Customer,
    DailyReconciliation,
    Inventory,
    InventoryAdjustment,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    Product,
    ProductCategory,
    Receipt,
    ReconciliationStatus,
    UserProfile,
    UserRole,
)

User = get_user_model()

STORE_EMAIL_DOMAIN = "lacsonfurniture-demo.com"
DEFAULT_PASSWORD = "FurniSync2026!"


class Command(BaseCommand):
    help = "Seed realistic single-branch defense data for the furniture management system."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fresh",
            action="store_true",
            help="Reset existing business data first, while keeping user accounts, roles, and branches.",
        )

    def handle(self, *args, **options):
        if options["fresh"]:
            call_command("reset_business_data", "--confirm")

        if not options["fresh"] and (
            Product.objects.exists()
            or Customer.objects.exists()
            or Order.objects.exists()
            or Payment.objects.exists()
        ):
            raise CommandError("Existing business data detected. Re-run with --fresh.")

        with transaction.atomic():
            branch = self._ensure_branch()
            staff = self._ensure_staff(branch)
            products = self._seed_categories_and_products(branch)
            customers = self._seed_customers(branch)
            orders = self._seed_orders(branch, staff, customers, products)
            self._seed_inventory_adjustments(branch, staff, products)
            self._seed_reconciliations(branch, staff)

        self.stdout.write(self.style.SUCCESS("Defense data seeded successfully."))
        self.stdout.write(f"- Branch: {branch.name}")
        self.stdout.write(f"- Categories: {ProductCategory.objects.count()}")
        self.stdout.write(f"- Products: {Product.objects.count()}")
        self.stdout.write(f"- Customers: {Customer.objects.count()}")
        self.stdout.write(f"- Orders: {len(orders)}")
        self.stdout.write(f"- Payments: {Payment.objects.count()}")
        self.stdout.write(f"- Reconciliations: {DailyReconciliation.objects.count()}")
        self.stdout.write("")
        self.stdout.write("Seeded staff accounts:")
        self.stdout.write(f"- owner / {DEFAULT_PASSWORD}")
        self.stdout.write(f"- manager / {DEFAULT_PASSWORD}")
        self.stdout.write(f"- secretary / {DEFAULT_PASSWORD}")
        self.stdout.write(f"- collector / {DEFAULT_PASSWORD}")
        self.stdout.write(f"- collector2 / {DEFAULT_PASSWORD}")
        self.stdout.write(f"- collector3 / {DEFAULT_PASSWORD}")
        self.stdout.write(f"- collector4 / {DEFAULT_PASSWORD}")
        self.stdout.write(f"- collector5 / {DEFAULT_PASSWORD}")

    def _ensure_branch(self) -> Branch:
        branch, _ = Branch.objects.get_or_create(
            name="Bacolod Main Showroom",
            defaults={"address": "Lacson Street, Bacolod City, Negros Occidental"},
        )
        branch.address = "Lacson Street, Bacolod City, Negros Occidental"
        branch.save(update_fields=["address"])
        return branch

    def _ensure_staff(self, branch: Branch) -> dict[str, object]:
        owner = self._ensure_user("owner", "owner", UserRole.OWNER, branch, "Store Owner")
        manager = self._ensure_user("manager", "manager", UserRole.MANAGER, branch, "Branch Manager")
        secretary = self._ensure_user("secretary", "secretary", UserRole.SECRETARY, branch, "Sales Secretary")
        collectors = [
            self._ensure_user("collector", "collector", UserRole.COLLECTOR, branch, "Senior Collector"),
            self._ensure_user("collector2", "collector2", UserRole.COLLECTOR, branch, "Collector"),
            self._ensure_user("collector3", "collector3", UserRole.COLLECTOR, branch, "Collector"),
            self._ensure_user("collector4", "collector4", UserRole.COLLECTOR, branch, "Collector"),
            self._ensure_user("collector5", "collector5", UserRole.COLLECTOR, branch, "Collector"),
        ]
        return {
            "owner": owner,
            "manager": manager,
            "secretary": secretary,
            "collectors": collectors,
        }

    def _ensure_user(self, username: str, email_local: str, role: str, branch: Branch, first_name: str):
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={"email": f"{email_local}@{STORE_EMAIL_DOMAIN}"},
        )
        user.email = f"{email_local}@{STORE_EMAIL_DOMAIN}"
        user.first_name = first_name
        user.last_name = ""
        user.is_staff = True
        user.set_password(DEFAULT_PASSWORD)
        user.save()
        UserProfile.objects.update_or_create(user=user, defaults={"role": role, "branch": branch})
        return user

    def _seed_categories_and_products(self, branch: Branch) -> list[Product]:
        category_specs = [
            {
                "name": "Sala Sets",
                "description": "Living room furniture for sofa seating, lounge tables, and reception setups.",
                "prefix": "SAL",
                "series": ["Arielle", "Brixton", "Camella", "Dahlia", "Elise", "Fiora", "Granada", "Hampton", "Ivory", "Jasper"],
                "types": [
                    ("2-Seater Sofa", Decimal("15999.00"), "Compact sofa for smaller sala spaces."),
                    ("3-Seater Sofa", Decimal("21999.00"), "Main upholstered sofa for family seating."),
                    ("L-Shape Sofa", Decimal("32999.00"), "Corner sectional sofa with extended chaise."),
                    ("Sofa Bed", Decimal("18999.00"), "Convertible sofa bed for guest use."),
                    ("Accent Chair", Decimal("7999.00"), "Single occasional chair for living areas."),
                    ("Coffee Table", Decimal("5999.00"), "Center table with easy-clean top."),
                    ("Center Table", Decimal("6899.00"), "Rectangular sala center table."),
                    ("Side Table", Decimal("3499.00"), "Companion side table for sofa sets."),
                    ("Console Table", Decimal("7499.00"), "Slim living room or hallway console."),
                    ("Recliner Chair", Decimal("14499.00"), "Manual recliner for lounge comfort."),
                ],
            },
            {
                "name": "Dining Sets",
                "description": "Tables, dining chairs, and benches for family meal areas.",
                "prefix": "DIN",
                "series": ["Alonzo", "Bataan", "Celine", "Domingo", "Estrella", "Florence", "Giselle", "Hidalgo", "Isandro", "Julieta"],
                "types": [
                    ("4-Seater Dining Set", Decimal("16999.00"), "Dining table with four matching chairs."),
                    ("6-Seater Dining Set", Decimal("23999.00"), "Standard family dining set for six."),
                    ("8-Seater Dining Set", Decimal("34999.00"), "Large dining setup for bigger households."),
                    ("Round Dining Table", Decimal("14999.00"), "Round dining table for compact spaces."),
                    ("Dining Bench", Decimal("4999.00"), "Bench seating for dining tables."),
                    ("Dining Chair Pair", Decimal("5999.00"), "Two-piece dining chair bundle."),
                    ("Buffet Cabinet", Decimal("13499.00"), "Dining storage cabinet for plates and serving ware."),
                    ("Bar Table Set", Decimal("15999.00"), "High dining or breakfast bar set."),
                    ("Server Cart", Decimal("6799.00"), "Mobile serving cart for dining service."),
                    ("China Cabinet", Decimal("18999.00"), "Glass-front cabinet for tableware display."),
                ],
            },
            {
                "name": "Bedroom Furniture",
                "description": "Beds, dressers, and bedroom storage for primary and guest rooms.",
                "prefix": "BED",
                "series": ["Amora", "Beatrice", "Celestine", "Danika", "Eliana", "Felice", "Gianna", "Helena", "Ingrid", "Josefina"],
                "types": [
                    ("Single Bed Frame", Decimal("9999.00"), "Solid wood frame for single mattress size."),
                    ("Double Bed Frame", Decimal("14999.00"), "Bedroom frame for double mattress size."),
                    ("Queen Bed Frame", Decimal("20999.00"), "Main bedroom queen-size frame."),
                    ("King Bed Frame", Decimal("28999.00"), "King-size bedroom frame with sturdy headboard."),
                    ("Night Stand", Decimal("3999.00"), "Compact bedside table with drawer."),
                    ("6-Drawer Dresser", Decimal("12499.00"), "Bedroom dresser with ample storage."),
                    ("Wardrobe Cabinet", Decimal("17999.00"), "Tall wardrobe for hanging clothes and folded items."),
                    ("Vanity Table", Decimal("10999.00"), "Bedroom vanity with mirror and drawer storage."),
                    ("Chest of Drawers", Decimal("9499.00"), "Vertical drawer cabinet for bedroom use."),
                    ("Headboard Panel", Decimal("5499.00"), "Standalone upholstered headboard panel."),
                ],
            },
            {
                "name": "Office Furniture",
                "description": "Desks, shelving, and chairs suited for office and work-from-home use.",
                "prefix": "OFF",
                "series": ["Arcadia", "Brent", "Corbin", "Dover", "Ellis", "Ford", "Garrett", "Hayes", "Irwin", "Juno"],
                "types": [
                    ("Executive Desk", Decimal("17999.00"), "Wide office desk with storage pedestal."),
                    ("Work Table", Decimal("9499.00"), "Simple office table for daily admin work."),
                    ("Office Chair", Decimal("6999.00"), "Task chair with cushioned support."),
                    ("Manager Chair", Decimal("9999.00"), "High-back chair for office supervisors."),
                    ("Filing Cabinet", Decimal("7999.00"), "Lockable cabinet for office records."),
                    ("Book Shelf", Decimal("6499.00"), "Open shelf for office binders and display."),
                    ("Meeting Table", Decimal("15999.00"), "Conference table for small teams."),
                    ("Computer Desk", Decimal("8499.00"), "Compact desk for PC setups."),
                    ("Reception Desk", Decimal("22999.00"), "Front office counter desk."),
                    ("Mobile Pedestal", Decimal("4299.00"), "Rolling storage pedestal for under-desk use."),
                ],
            },
            {
                "name": "Storage Cabinets",
                "description": "General-purpose cabinets, shelves, and home organization furniture.",
                "prefix": "STO",
                "series": ["Almere", "Braga", "Catania", "Dijon", "Evora", "Faro", "Galway", "Huelva", "Imola", "Jaen"],
                "types": [
                    ("3-Door Cabinet", Decimal("12999.00"), "Tall cabinet for household storage."),
                    ("4-Door Cabinet", Decimal("16999.00"), "Larger storage cabinet for bedrooms or utility rooms."),
                    ("Open Shelf Rack", Decimal("5499.00"), "Open rack for display and daily storage."),
                    ("Shoe Cabinet", Decimal("6999.00"), "Cabinet with layered storage for footwear."),
                    ("Kitchen Rack", Decimal("7599.00"), "Freestanding kitchen organizer rack."),
                    ("Pantry Cabinet", Decimal("14999.00"), "Tall cabinet for dry-goods and utensils."),
                    ("Multi-Purpose Shelf", Decimal("5999.00"), "All-around shelf for home storage needs."),
                    ("Laundry Cabinet", Decimal("8899.00"), "Utility cabinet for laundry supplies."),
                    ("Corner Shelf", Decimal("4299.00"), "Compact corner storage solution."),
                    ("Display Cabinet", Decimal("11999.00"), "Glass-door cabinet for decor or collectibles."),
                ],
            },
            {
                "name": "Mattresses",
                "description": "Foam and spring mattresses for single, double, queen, and king bed sizes.",
                "prefix": "MAT",
                "series": ["Aster", "Bamboo", "Cloudrest", "Dreamline", "Everfirm", "Flexcare", "Goodrest", "Harmony", "Ideal", "Joysleep"],
                "types": [
                    ("Single Foam Mattress", Decimal("5499.00"), "Entry-level foam mattress for single bed."),
                    ("Double Foam Mattress", Decimal("7499.00"), "Foam mattress for double bed."),
                    ("Queen Foam Mattress", Decimal("9999.00"), "Thicker foam mattress for queen bed."),
                    ("King Foam Mattress", Decimal("13999.00"), "High-density foam mattress for king bed."),
                    ("Single Spring Mattress", Decimal("6999.00"), "Basic spring mattress for single bed."),
                    ("Double Spring Mattress", Decimal("9499.00"), "Spring mattress for double bed."),
                    ("Queen Spring Mattress", Decimal("12999.00"), "Queen-size spring mattress with pillow top."),
                    ("King Spring Mattress", Decimal("16999.00"), "King-size spring mattress for premium comfort."),
                    ("Mattress Topper", Decimal("2999.00"), "Add-on comfort layer for older mattresses."),
                    ("Foldable Foam Mattress", Decimal("3999.00"), "Portable folding mattress for guests."),
                ],
            },
            {
                "name": "Outdoor Furniture",
                "description": "Patio and garden furniture for balconies, terraces, and outdoor receiving areas.",
                "prefix": "OUT",
                "series": ["Alta", "Breeze", "Canyon", "Drift", "Escala", "Forest", "Grove", "Harbor", "Island", "Jardin"],
                "types": [
                    ("Patio Chair", Decimal("4499.00"), "Weather-ready outdoor accent chair."),
                    ("Outdoor Bench", Decimal("8999.00"), "Garden bench for porch or patio use."),
                    ("Patio Table", Decimal("6999.00"), "Outdoor table for coffee or meals."),
                    ("Balcony Set", Decimal("11999.00"), "Compact two-seat table set for balconies."),
                    ("Garden Swing", Decimal("18499.00"), "Outdoor swing bench for leisure areas."),
                    ("Rattan Sofa", Decimal("22499.00"), "Outdoor woven sofa for covered patios."),
                    ("Sun Lounger", Decimal("13999.00"), "Adjustable lounge bed for outdoor rest."),
                    ("Plant Stand", Decimal("2499.00"), "Display stand for potted plants."),
                    ("Outdoor Storage Box", Decimal("7999.00"), "Covered storage for outdoor accessories."),
                    ("Picnic Table", Decimal("15499.00"), "Outdoor bench-and-table dining setup."),
                ],
            },
            {
                "name": "Accent Furniture",
                "description": "Decorative and support furniture pieces used across living and bedroom spaces.",
                "prefix": "ACC",
                "series": ["Amber", "Bronze", "Cove", "Dune", "Elm", "Flair", "Garnet", "Haze", "Indigo", "Jade"],
                "types": [
                    ("Accent Chair", Decimal("7499.00"), "Statement chair for receiving areas."),
                    ("Ottoman", Decimal("2999.00"), "Compact upholstered ottoman."),
                    ("Bench Seat", Decimal("5999.00"), "Padded bench for foyer or bedroom."),
                    ("Entry Table", Decimal("6499.00"), "Slim console table for entrance areas."),
                    ("Mirror Console", Decimal("9899.00"), "Decorative console with matching mirror."),
                    ("Lamp Table", Decimal("3699.00"), "Small table for lamp or decor pieces."),
                    ("Nest Tables", Decimal("5299.00"), "Set of stackable side tables."),
                    ("Hallway Cabinet", Decimal("8999.00"), "Narrow cabinet for entryways."),
                    ("Pouf Stool", Decimal("1999.00"), "Soft portable stool."),
                    ("Display Pedestal", Decimal("3199.00"), "Raised stand for decor display."),
                ],
            },
            {
                "name": "Entertainment Units",
                "description": "TV racks, media storage, and living room entertainment furniture.",
                "prefix": "ENT",
                "series": ["Avenue", "Broadway", "Cinema", "Deluxe", "Encore", "Frame", "Galaxy", "Heritage", "Icon", "Jubilee"],
                "types": [
                    ("TV Rack 48in", Decimal("7999.00"), "Compact media rack for 48-inch televisions."),
                    ("TV Rack 55in", Decimal("9999.00"), "Media rack for mid-sized televisions."),
                    ("TV Rack 65in", Decimal("12999.00"), "Large media console for bigger screens."),
                    ("Wall Shelf", Decimal("2499.00"), "Floating shelf for decoders and decor."),
                    ("Media Cabinet", Decimal("8899.00"), "Closed storage cabinet for electronics."),
                    ("Display Shelf", Decimal("5599.00"), "Open display shelf for books and media."),
                    ("Soundbar Console", Decimal("6799.00"), "Low-profile media unit for TV and soundbar."),
                    ("Corner TV Stand", Decimal("7499.00"), "Space-saving entertainment corner stand."),
                    ("Home Theater Shelf", Decimal("10999.00"), "Multi-layer unit for media equipment."),
                    ("Game Console Rack", Decimal("4299.00"), "Compact organizer for gaming accessories."),
                ],
            },
            {
                "name": "Kids Furniture",
                "description": "Bedroom and study furniture sized for children and teens.",
                "prefix": "KID",
                "series": ["Aqua", "Bliss", "Comet", "Daisy", "Echo", "Fable", "Glimmer", "Halo", "Iris", "Joy"],
                "types": [
                    ("Kids Bed Frame", Decimal("8999.00"), "Child-sized bed frame with guard-friendly design."),
                    ("Study Table", Decimal("5499.00"), "Compact desk for homework and reading."),
                    ("Kids Wardrobe", Decimal("10999.00"), "Storage cabinet for children's clothes."),
                    ("Toy Cabinet", Decimal("4999.00"), "Organized storage for toys and supplies."),
                    ("Bunk Bed Frame", Decimal("18999.00"), "Double-level bed frame for shared rooms."),
                    ("Kids Bookshelf", Decimal("3999.00"), "Low shelf for books and learning materials."),
                    ("Drawer Cabinet", Decimal("4599.00"), "Small drawer storage for school items."),
                    ("Play Table Set", Decimal("5799.00"), "Child-height table and seat bundle."),
                    ("Mini Dresser", Decimal("6499.00"), "Compact dresser for kids' bedrooms."),
                    ("Bedside Table", Decimal("2499.00"), "Small side table for children's rooms."),
                ],
            },
        ]

        products: list[Product] = []
        for category_spec in category_specs:
            category, _ = ProductCategory.objects.get_or_create(
                name=category_spec["name"],
                defaults={"description": category_spec["description"]},
            )
            category.description = category_spec["description"]
            category.save(update_fields=["description"])

            prefix = category_spec["prefix"]
            for idx, (item_type, base_price, description) in enumerate(category_spec["types"], start=1):
                series_name = category_spec["series"][idx - 1]
                product_name = f"{series_name} {item_type}"
                price = self._price_variation(base_price, idx)
                sku = f"{prefix}-{idx:03d}"
                product, _ = Product.objects.get_or_create(
                    sku=sku,
                    defaults={
                        "category": category,
                        "name": product_name,
                        "description": description,
                        "price": price,
                    },
                )
                product.category = category
                product.name = product_name
                product.description = description
                product.price = price
                product.save()

                inventory, _ = Inventory.objects.get_or_create(product=product, branch=branch)
                inventory.stock = self._starting_stock(item_type, idx)
                inventory.reserved = 0
                inventory.recalculate_available()
                inventory.save()
                products.append(product)

        return products

    def _seed_customers(self, branch: Branch) -> list[Customer]:
        first_names = [
            "Maria", "John", "Angela", "David", "Carla", "Paulo", "Rhea", "Mark", "Janine", "Leo",
            "Mariel", "Joshua", "Kristine", "Kevin", "Patricia", "Jomar", "Catherine", "Noel", "Vanessa", "Miguel",
            "Aileen", "Bryan", "Sheila", "Carlo", "Dianne", "Ronald", "Elaine", "Patrick", "Monica", "Jerome",
            "Liza", "Kenneth", "Abigail", "Francis", "Therese", "Ralph", "Joy", "Nathan", "Sabrina", "Adrian",
            "Mika", "Vince", "Clarisse", "Harold", "Denise", "Anton", "Bianca", "Rey", "Karla", "Ian",
        ]
        last_names = [
            "Santos", "Rivera", "Cruz", "Reyes", "Garcia", "Mendoza", "Lopez", "Torres", "Ramos", "Fernandez",
            "Villanueva", "Aquino", "Domingo", "Navarro", "Castro", "Soriano", "Bautista", "Dela Cruz", "Rosales", "Mercado",
            "Salvador", "Benitez", "Velasco", "Manalo", "Tiongson", "Abella", "Montemayor", "Balicanta", "Javellana", "Yanson",
            "Magsino", "Beltran", "Nava", "Tiu", "Arceo", "Lim", "Sia", "Go", "Tan", "Valencia",
            "Labao", "Neri", "Ponce", "Ocampo", "Alba", "Sison", "De Guia", "Malabanan", "Marañon", "Ledesma",
        ]
        puroks = [
            "Purok Malipayon", "Purok San Isidro", "Purok Riverside", "Purok Mahogany", "Purok Rosal",
            "Purok Santan", "Purok Diamond", "Purok Sunshine", "Purok Mango", "Purok Ilang-Ilang",
        ]
        barangays = [
            "Mandalagan", "Villamonte", "Tangub", "Estefania", "Mansilingan",
            "Alijis", "Banago", "Taculing", "Sum-ag", "Cabug",
        ]
        streets = [
            "Lacson Street", "B.S. Aquino Drive", "Araneta Avenue", "Burgos Avenue", "Rizal Street",
            "Hernaez Street", "San Juan Street", "Lopez Jaena Street", "Galo Street", "Circumferential Road",
        ]
        installment_plans = [
            "Cash",
            "Cash / split payment",
            "30% down / 3 months",
            "20% down / 6 months",
            "25% down / 6 months",
            "12-month installment",
            "9-month installment",
            "6-month installment",
            "50% down / 3 months",
            "40% down / 4 months",
        ]
        phone_prefixes = [
            "0917", "0918", "0919", "0920", "0921", "0922", "0925", "0926", "0927", "0928",
            "0929", "0930", "0935", "0936", "0937", "0938", "0939", "0945", "0946", "0947",
            "0948", "0949", "0950", "0951", "0955", "0956", "0961", "0963", "0965", "0966",
            "0967", "0975", "0977", "0979", "0981", "0985", "0989", "0994", "0995",
        ]

        customers: list[Customer] = []
        for idx in range(50):
            full_name = f"{first_names[idx]} {last_names[idx]}"
            phone = f"{phone_prefixes[idx % len(phone_prefixes)]}{1000000 + (idx * 173):07d}"[:11]
            email = (
                f"{first_names[idx].lower().replace(' ', '')}.{last_names[idx].lower().replace(' ', '')}@gmail.com"
                if idx % 4 != 0
                else ""
            )
            address = (
                f"Blk {idx % 9 + 1} Lot {idx % 17 + 3}, "
                f"{puroks[idx % len(puroks)]}, {streets[idx % len(streets)]}, "
                f"Brgy. {barangays[idx % len(barangays)]}, Bacolod City"
            )
            installment_plan = installment_plans[idx % len(installment_plans)]

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
            customers.append(customer)

        return customers

    def _seed_orders(self, branch: Branch, staff: dict[str, object], customers: list[Customer], products: list[Product]) -> list[Order]:
        secretary = staff["secretary"]
        manager = staff["manager"]
        collectors = staff["collectors"]

        order_dates = [
            timezone.now() - timedelta(days=210 - index * 4)
            for index in range(len(customers))
        ]

        orders: list[Order] = []
        completed_cutoff = 20
        reserved_cutoff = 34

        for idx, customer in enumerate(customers):
            line_count = 1 if idx < 18 else (2 if idx < 40 else 3)
            chosen_products = products[idx * 2: idx * 2 + line_count]
            if len(chosen_products) < line_count:
                chosen_products = products[idx: idx + line_count]

            order = Order.objects.create(
                customer=customer,
                branch=branch,
                status=OrderStatus.PENDING,
                created_by=secretary,
                last_modified_by=secretary,
            )

            for line_index, product in enumerate(chosen_products, start=1):
                quantity = 1 + ((idx + line_index) % 2)
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=quantity,
                    price=product.price,
                    subtotal=product.price * quantity,
                )

            assigned_collector = collectors[idx % len(collectors)]
            target_status = (
                OrderStatus.COMPLETED
                if idx < completed_cutoff
                else OrderStatus.RESERVED
                if idx < reserved_cutoff
                else OrderStatus.PENDING
            )

            order.assigned_collector = assigned_collector
            order.approved_by = manager if target_status in {OrderStatus.RESERVED, OrderStatus.COMPLETED} else None
            order.approved_at = order_dates[idx] + timedelta(hours=6) if order.approved_by else None
            order.status = target_status
            order.last_modified_by = manager if order.approved_by else secretary
            order.save()
            self._set_order_timestamps(order, order_dates[idx])

            total_amount = order.total_amount
            if target_status == OrderStatus.COMPLETED:
                payment_count = 1 if customer.installment_plan in {"Cash", "Cash / split payment"} else (2 if idx % 2 == 0 else 3)
                payment_amounts = self._split_amount(total_amount, payment_count)
                self._create_payments(
                    order=order,
                    branch=branch,
                    collector=assigned_collector,
                    amounts=payment_amounts,
                    start_date=order_dates[idx] + timedelta(days=2),
                )
            elif target_status == OrderStatus.RESERVED:
                deposit_ratio = Decimal("0.30") if "Cash" in customer.installment_plan else Decimal("0.20")
                deposit_amount = self._money(total_amount * deposit_ratio)
                follow_up_amount = self._money(total_amount * Decimal("0.15")) if idx % 2 == 0 else Decimal("0.00")
                payment_amounts = [deposit_amount]
                if follow_up_amount > Decimal("0.00") and deposit_amount + follow_up_amount < total_amount:
                    payment_amounts.append(follow_up_amount)
                self._create_payments(
                    order=order,
                    branch=branch,
                    collector=assigned_collector,
                    amounts=payment_amounts,
                    start_date=order_dates[idx] + timedelta(days=1),
                )
            else:
                if idx % 3 != 0:
                    partial_amount = self._money(total_amount * Decimal("0.10"))
                    second_amount = self._money(total_amount * Decimal("0.08")) if idx % 4 == 0 else Decimal("0.00")
                    payment_amounts = [partial_amount]
                    if second_amount > Decimal("0.00") and partial_amount + second_amount < total_amount:
                        payment_amounts.append(second_amount)
                    self._create_payments(
                        order=order,
                        branch=branch,
                        collector=assigned_collector,
                        amounts=payment_amounts,
                        start_date=order_dates[idx] + timedelta(days=3),
                    )

            orders.append(order)

        return orders

    def _create_payments(
        self,
        *,
        order: Order,
        branch: Branch,
        collector,
        amounts: list[Decimal],
        start_date,
    ) -> None:
        for index, amount in enumerate(amounts):
            paid_at = start_date + timedelta(days=index * 14, hours=9 + index)
            payment = Payment.objects.create(
                order=order,
                branch=branch,
                collector=collector,
                amount=amount,
                paid_at=paid_at,
            )
            Payment.objects.filter(pk=payment.pk).update(
                verification_status=Payment.VerificationStatus.MATCHED,
                manager_resolution_status=Payment.ManagerResolutionStatus.ACCEPTED,
                customer_confirmation_token=None,
                collector_submission_ip=None,
                collector_submission_user_agent="",
                customer_confirmation_ip=None,
                customer_confirmation_user_agent="",
                suspicious_confirmation=False,
                suspicious_reason="",
                created_at=paid_at,
            )
            receipt_date = paid_at.strftime("%Y%m%d")
            Receipt.objects.filter(payment=payment).update(
                receipt_number=f"RCP-{branch.id}-{receipt_date}-{payment.id:05d}",
                created_at=paid_at,
            )

    def _seed_inventory_adjustments(self, branch: Branch, staff: dict[str, object], products: list[Product]) -> None:
        manager = staff["manager"]
        secretary = staff["secretary"]
        selected_products = products[::11][:9]
        reasons = [
            "Initial warehouse count alignment",
            "Supplier restock delivery",
            "Showroom pullout returned to available stock",
            "Backroom recount adjustment",
            "Additional display stock received",
            "Monthly inventory correction",
            "New supplier delivery posted",
            "Manual count correction",
            "Damage replacement stock received",
        ]
        for idx, product in enumerate(selected_products):
            created_at = timezone.now() - timedelta(days=150 - idx * 11)
            adjustment = InventoryAdjustment.objects.create(
                product=product,
                branch=branch,
                quantity=2 + (idx % 4),
                reason=reasons[idx],
                approved=True,
                created_by=secretary,
                approved_by=manager,
                approved_at=created_at + timedelta(hours=2),
            )
            adjustment.apply_to_inventory()
            InventoryAdjustment.objects.filter(pk=adjustment.pk).update(
                created_at=created_at,
                approved_at=created_at + timedelta(hours=2),
            )

    def _seed_reconciliations(self, branch: Branch, staff: dict[str, object]) -> None:
        manager = staff["manager"]
        collectors = staff["collectors"]

        for idx, collector in enumerate(collectors):
            recon_date = (timezone.now() - timedelta(days=idx + 2)).date()
            system_total = Decimal(str(18500 + idx * 2350))
            reconciliation = DailyReconciliation.objects.create(
                branch=branch,
                collector=collector,
                date=recon_date,
                system_total=system_total,
                cash_counted=system_total,
                status=ReconciliationStatus.APPROVED,
                approved_by=manager,
                approved_at=timezone.now() - timedelta(days=idx + 1, hours=6),
            )
            DailyReconciliation.objects.filter(pk=reconciliation.pk).update(
                created_at=timezone.now() - timedelta(days=idx + 1, hours=7)
            )

    def _set_order_timestamps(self, order: Order, created_at) -> None:
        updated_at = created_at + timedelta(hours=4)
        Order.objects.filter(pk=order.pk).update(created_at=created_at, updated_at=updated_at)

    def _split_amount(self, total: Decimal, parts: int) -> list[Decimal]:
        if parts <= 1:
            return [self._money(total)]

        weights = [Decimal(str(value)) for value in range(parts, 0, -1)]
        weight_total = sum(weights, Decimal("0.00"))
        amounts: list[Decimal] = []
        allocated = Decimal("0.00")
        for weight in weights[:-1]:
            amount = self._money(total * (weight / weight_total))
            amounts.append(amount)
            allocated += amount
        amounts.append(self._money(total - allocated))
        return amounts

    def _money(self, amount: Decimal) -> Decimal:
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _price_variation(self, base_price: Decimal, idx: int) -> Decimal:
        adjustment_steps = [Decimal("0.00"), Decimal("250.00"), Decimal("500.00"), Decimal("750.00"), Decimal("1000.00")]
        return base_price + adjustment_steps[idx % len(adjustment_steps)]

    def _starting_stock(self, item_type: str, idx: int) -> int:
        if "Mattress" in item_type:
            return 14 + (idx % 6)
        if "Chair" in item_type or "Table" in item_type or "Shelf" in item_type:
            return 9 + (idx % 5)
        if "Bed" in item_type or "Sofa" in item_type or "Wardrobe" in item_type:
            return 6 + (idx % 4)
        return 8 + (idx % 6)
