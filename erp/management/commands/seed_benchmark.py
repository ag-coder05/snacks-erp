from decimal import Decimal
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from erp.models import (
    AuditLog,
    BOM,
    BOMLine,
    Customer,
    Invoice,
    Item,
    Payment,
    ProductionRun,
    ProductionRunMaterial,
    Purchase,
    SalesOrder,
    SalesOrderLine,
    StockLedger,
    Supplier,
    UserProfile,
)


class Command(BaseCommand):
    help = "Resets ERP transactional data and loads the Section 5 Murukku 200g benchmark master data."

    @transaction.atomic
    def handle(self, *args, **options):
        # Clear existing data cleanly so the benchmark can be rerun anytime
        AuditLog.objects.all().delete()
        Payment.objects.all().delete()
        Invoice.objects.all().delete()
        SalesOrderLine.objects.all().delete()
        SalesOrder.objects.all().delete()
        ProductionRunMaterial.objects.all().delete()
        ProductionRun.objects.all().delete()
        Purchase.objects.all().delete()
        StockLedger.objects.all().delete()
        BOMLine.objects.all().delete()
        BOM.objects.all().delete()
        Item.objects.all().delete()
        Customer.objects.all().delete()
        Supplier.objects.all().delete()

        # 1. Create Demo Users for all 5 roles (password: pass1234)
        demo_users = [
            ("salesman_a", "Salesman A", "SALESMAN", False),
            ("salesman_b", "Salesman B", "SALESMAN", False),
            ("admin_user", "Factory Admin", "ADMIN", True),
            ("prod_sup", "Production Supervisor", "PRODUCTION", False),
            ("auditor", "External Auditor", "AUDITOR", False),
            ("owner", "Factory Owner", "OWNER", True),
        ]
        admin_obj = None
        for username, first_name, role, is_staff in demo_users:
            user, _ = User.objects.get_or_create(username=username, defaults={"first_name": first_name})
            user.first_name = first_name
            user.is_staff = is_staff
            if username == "admin_user":
                user.is_superuser = True
                admin_obj = user
            user.set_password("pass1234")
            user.save()
            UserProfile.objects.update_or_create(user=user, defaults={"role": role})

        # 2. Supplier & Customers
        Supplier.objects.create(
            name="Sri Amman Agro Traders",
            gstin="33AABCS1234A1Z5",
            state="Tamil Nadu",
            phone="9840011223",
        )

        Customer.objects.create(
            name="Retailer A",
            customer_type="RETAILER",
            gstin="33AAACR5055K1Z2",
            state="Tamil Nadu",
            phone="9840123456",
            address="12 Gandhi Road, Tiruchirappalli, Tamil Nadu",
        )
        Customer.objects.create(
            name="Distributor B",
            customer_type="DISTRIBUTOR",
            gstin="29AABCD7890E1Z9",
            state="Karnataka",
            phone="9900112233",
            address="45 Market Street, Bengaluru, Karnataka",
        )

        # 3. Master Items (Section 5)
        flour = Item.objects.create(
            sku="RM-FLOUR", name="Flour", category="RAW", base_unit="kg", hsn_code="11010000", reorder_level=Decimal("50")
        )
        oil = Item.objects.create(
            sku="RM-OIL", name="Cooking Oil", category="RAW", base_unit="litre", hsn_code="15121910", reorder_level=Decimal("15")
        )
        spice = Item.objects.create(
            sku="RM-SPICE", name="Salt and Spice Mix", category="RAW", base_unit="kg", hsn_code="09109100", reorder_level=Decimal("2")
        )
        pouch = Item.objects.create(
            sku="PK-POUCH200", name="200 g Pouch", category="PACKAGING", base_unit="piece", hsn_code="39232100", reorder_level=Decimal("200")
        )
        carton = Item.objects.create(
            sku="PK-CARTON20", name="Carton (20 pkts)", category="PACKAGING", base_unit="piece", hsn_code="48191010", reorder_level=Decimal("10")
        )
        murukku = Item.objects.create(
            sku="FG-MURUKKU-200",
            name="Murukku 200 g",
            category="FINISHED",
            base_unit="packet",
            hsn_code="21069099",
            gst_rate=Decimal("5.00"),
            selling_price=Decimal("50.00"),
            pack_size_grams=200,
            packets_per_carton=20,
            reorder_level=Decimal("50"),
        )

        # 4. BOM for Murukku 200 g (1 batch = 200 packets)
        bom = BOM.objects.create(
            finished_item=murukku,
            expected_output=Decimal("200.000"),
            tolerance_percent=Decimal("3.00"),
            notes="Standard 1-batch recipe for 200g Murukku packets.",
        )
        BOMLine.objects.create(bom=bom, material=flour, quantity_per_batch=Decimal("40.000"), unit="kg")
        BOMLine.objects.create(bom=bom, material=oil, quantity_per_batch=Decimal("10.000"), unit="litre")
        BOMLine.objects.create(bom=bom, material=spice, quantity_per_batch=Decimal("1.200"), unit="kg")
        BOMLine.objects.create(bom=bom, material=pouch, quantity_per_batch=Decimal("200.000"), unit="piece")
        BOMLine.objects.create(bom=bom, material=carton, quantity_per_batch=Decimal("10.000"), unit="piece")

        # 5. Opening Stock Ledger Entries (Section 5)
        opening_entries = [
            (flour, Decimal("100.000"), Decimal("42.00")),
            (oil, Decimal("30.000"), Decimal("140.00")),
            (spice, Decimal("5.000"), Decimal("200.00")),
            (pouch, Decimal("500.000"), Decimal("2.50")),
            (carton, Decimal("30.000"), Decimal("15.00")),
            (murukku, Decimal("50.000"), Decimal("30.00")),
        ]
        for item, qty, cost in opening_entries:
            StockLedger.objects.create(
                item=item,
                movement_type="OPENING",
                quantity_delta=qty,
                unit_cost=cost,
                reference_type="OpeningBalance",
                notes="Benchmark opening stock",
                created_by=admin_obj,
            )

        self.stdout.write(self.style.SUCCESS("Benchmark master data, opening stock, and demo users seeded successfully!"))