from decimal import Decimal
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from erp.models import BOM, Customer, Item, SalesOrder, SalesOrderLine, Supplier
from erp.services import (
    check_order_stock,
    create_gst_invoice,
    create_production_run,
    dispatch_invoice,
    record_payment,
    record_purchase,
    update_run_overheads,
)


class BenchmarkScenarioTest(TestCase):
    def setUp(self):
        call_command("seed_benchmark")
        self.admin = User.objects.get(username="admin_user")
        self.prod = User.objects.get(username="prod_sup")
        self.salesman_a = User.objects.get(username="salesman_a")
        self.salesman_b = User.objects.get(username="salesman_b")

        self.supplier = Supplier.objects.first()
        self.retailer_a = Customer.objects.get(name="Retailer A")
        self.distributor_b = Customer.objects.get(name="Distributor B")

        self.flour = Item.objects.get(sku="RM-FLOUR")
        self.oil = Item.objects.get(sku="RM-OIL")
        self.spice = Item.objects.get(sku="RM-SPICE")
        self.pouch = Item.objects.get(sku="PK-POUCH200")
        self.carton = Item.objects.get(sku="PK-CARTON20")
        self.murukku = Item.objects.get(sku="FG-MURUKKU-200")
        self.bom = BOM.objects.get(finished_item=self.murukku)

    def test_full_section_5_benchmark(self):
        # Step 1: Purchase 60 kg flour at ₹45/kg
        record_purchase(
            supplier=self.supplier,
            bill_number="SUP-001",
            item=self.flour,
            quantity=Decimal("60"),
            unit_price=Decimal("45.00"),
            user=self.prod,
        )

        # Steps 2 & 3: Production run (1 batch) with actuals & wastage
        run = create_production_run(
            bom=self.bom,
            batches=Decimal("1"),
            actual_output=Decimal("188"),
            material_inputs=[
                {"item": self.flour, "planned_qty": Decimal("40"), "actual_qty": Decimal("40"), "wastage_qty": Decimal("0")},
                {"item": self.oil, "planned_qty": Decimal("10"), "actual_qty": Decimal("10.5"), "wastage_qty": Decimal("0")},
                {"item": self.spice, "planned_qty": Decimal("1.2"), "actual_qty": Decimal("1.2"), "wastage_qty": Decimal("0")},
                {"item": self.pouch, "planned_qty": Decimal("200"), "actual_qty": Decimal("188"), "wastage_qty": Decimal("6")},
                {"item": self.carton, "planned_qty": Decimal("10"), "actual_qty": Decimal("9"), "wastage_qty": Decimal("0")},
            ],
            user=self.prod,
        )

        # Verify closing balances after production run
        self.assertEqual(self.flour.current_stock(), Decimal("120.000"))
        self.assertEqual(self.oil.current_stock(), Decimal("19.500"))
        self.assertEqual(self.pouch.current_stock(), Decimal("306.000"))
        self.assertEqual(self.murukku.current_stock(), Decimal("238.000"))

        # Verify variances & flags
        self.assertEqual(run.yield_variance_pct, Decimal("-6.00"))
        self.assertTrue(run.yield_variance_flagged)
        oil_line = run.materials.get(item=self.oil)
        self.assertEqual(oil_line.variance_pct, Decimal("5.00"))
        self.assertTrue(oil_line.variance_flagged)

        # Steps 4 & 5: Salesman A -> Retailer A (TN) 150 packets -> Invoice -> Dispatch
        order_a = SalesOrder.objects.create(
            order_number="SO-001", customer=self.retailer_a, salesman=self.salesman_a
        )
        SalesOrderLine.objects.create(
            order=order_a, item=self.murukku, quantity=Decimal("150"), unit_price=Decimal("50.00")
        )
        inv_a = create_gst_invoice(order_a, user=self.admin)
        self.assertFalse(inv_a.is_interstate)
        self.assertEqual(inv_a.taxable_value, Decimal("7500.00"))
        self.assertEqual(inv_a.cgst_amount, Decimal("187.50"))
        self.assertEqual(inv_a.sgst_amount, Decimal("187.50"))
        self.assertEqual(inv_a.igst_amount, Decimal("0.00"))
        self.assertEqual(inv_a.total_amount, Decimal("7875.00"))

        dispatch_invoice(inv_a, user=self.admin)
        self.assertEqual(self.murukku.current_stock(), Decimal("88.000"))

        # Step 6: Salesman B -> Distributor B (KA) 120 packets -> Shortage of 32 flagged
        order_b = SalesOrder.objects.create(
            order_number="SO-002", customer=self.distributor_b, salesman=self.salesman_b
        )
        SalesOrderLine.objects.create(
            order=order_b, item=self.murukku, quantity=Decimal("120"), unit_price=Decimal("50.00")
        )
        checks_b = check_order_stock(order_b)
        self.assertFalse(checks_b[0]["sufficient"])
        self.assertEqual(checks_b[0]["shortage"], Decimal("32.000"))

        # Step 7: Record ₹4,000 payment from Retailer A -> ₹3,875 outstanding
        record_payment(
            customer=self.retailer_a,
            invoice=inv_a,
            amount=Decimal("4000.00"),
            mode="UPI",
            reference="UPI-998877",
            user=self.admin,
        )
        self.assertEqual(self.retailer_a.outstanding_balance(), Decimal("3875.00"))

        # Step 8: Add labour ₹1,200 and gas/power ₹600 -> Cost per packet
        update_run_overheads(run, labour_cost=Decimal("1200.00"), overhead_cost=Decimal("600.00"), user=self.prod)
        # Flour weighted avg = (100*42 + 60*45)/160 = 43.125/kg -> 40*43.125 = 1725.00
        # Oil: 10.5*140 = 1470.00 | Spice: 1.2*200 = 240.00 | Pouches: 194*2.50 = 485.00 | Cartons: 9*15 = 135.00
        # Total material = 4055.00 + Labour 1200 + Overhead 600 = 5855.00 / 188 = ₹31.14 per packet
        self.assertEqual(run.total_material_cost, Decimal("4055.00"))
        self.assertEqual(run.cost_per_packet, Decimal("31.14"))