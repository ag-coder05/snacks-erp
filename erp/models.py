from decimal import Decimal
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Sum, F, DecimalField, ExpressionWrapper
from django.utils import timezone


FACTORY_STATE = "Tamil Nadu"


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ("SALESMAN", "Salesman"),
        ("ADMIN", "Factory Admin"),
        ("PRODUCTION", "Production Supervisor"),
        ("AUDITOR", "Accountant / Auditor"),
        ("OWNER", "Owner"),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    phone = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"


class Item(models.Model):
    CATEGORY_CHOICES = [
        ("RAW", "Raw Material"),
        ("PACKAGING", "Packaging Material"),
        ("FINISHED", "Finished Goods"),
    ]
    UNIT_CHOICES = [
        ("kg", "Kilogram (kg)"),
        ("g", "Gram (g)"),
        ("litre", "Litre (L)"),
        ("ml", "Millilitre (ml)"),
        ("piece", "Piece"),
        ("packet", "Packet"),
        ("carton", "Carton"),
    ]

    sku = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    base_unit = models.CharField(max_length=15, choices=UNIT_CHOICES)
    hsn_code = models.CharField(max_length=15, default="21069099")
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("5.00"))
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    pack_size_grams = models.IntegerField(null=True, blank=True)
    packets_per_carton = models.IntegerField(null=True, blank=True)
    reorder_level = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0.000"))

    def current_stock(self) -> Decimal:
        total = self.ledger_entries.aggregate(total=Sum("quantity_delta"))["total"]
        return total if total is not None else Decimal("0.000")

    def weighted_avg_cost(self) -> Decimal:
        inbound = self.ledger_entries.filter(
            movement_type__in=["OPENING", "PURCHASE"],
            quantity_delta__gt=0,
        ).aggregate(
            total_qty=Sum("quantity_delta"),
            total_val=Sum(
                ExpressionWrapper(
                    F("quantity_delta") * F("unit_cost"),
                    output_field=DecimalField(max_digits=16, decimal_places=4),
                )
            ),
        )
        qty = inbound["total_qty"] or Decimal("0")
        val = inbound["total_val"] or Decimal("0")
        if qty <= 0:
            return Decimal("0.00")
        return (val / qty).quantize(Decimal("0.0001"))

    def __str__(self):
        return f"{self.name} ({self.base_unit})"


class Customer(models.Model):
    TYPE_CHOICES = [
        ("RETAILER", "Retailer"),
        ("DISTRIBUTOR", "Distributor"),
    ]
    name = models.CharField(max_length=120)
    customer_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="RETAILER")
    gstin = models.CharField(max_length=15, blank=True)
    state = models.CharField(max_length=50, default=FACTORY_STATE)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)

    def total_invoiced(self) -> Decimal:
        val = self.invoices.filter(is_cancelled=False).aggregate(t=Sum("total_amount"))["t"]
        return val if val is not None else Decimal("0.00")

    def total_paid(self) -> Decimal:
        val = self.payments.aggregate(t=Sum("amount"))["t"]
        return val if val is not None else Decimal("0.00")

    def outstanding_balance(self) -> Decimal:
        return self.total_invoiced() - self.total_paid()

    def __str__(self):
        return f"{self.name} ({self.state})"


class Supplier(models.Model):
    name = models.CharField(max_length=120)
    gstin = models.CharField(max_length=15, blank=True)
    state = models.CharField(max_length=50, default=FACTORY_STATE)
    phone = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return self.name


class BOM(models.Model):
    finished_item = models.OneToOneField(
        Item,
        on_delete=models.CASCADE,
        limit_choices_to={"category": "FINISHED"},
        related_name="bom",
    )
    expected_output = models.DecimalField(
        max_digits=12, decimal_places=3, help_text="Expected packets per 1 standard batch"
    )
    tolerance_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("3.00")
    )
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"BOM: {self.finished_item.name} ({self.expected_output} {self.finished_item.base_unit}/batch)"


class BOMLine(models.Model):
    bom = models.ForeignKey(BOM, on_delete=models.CASCADE, related_name="lines")
    material = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        limit_choices_to={"category__in": ["RAW", "PACKAGING"]},
    )
    quantity_per_batch = models.DecimalField(max_digits=12, decimal_places=3)
    unit = models.CharField(max_length=15, choices=Item.UNIT_CHOICES)

    def __str__(self):
        return f"{self.material.name}: {self.quantity_per_batch} {self.unit}"


class StockLedger(models.Model):
    MOVEMENT_CHOICES = [
        ("OPENING", "Opening Stock"),
        ("PURCHASE", "Raw Material Purchase"),
        ("PROD_ISSUE", "Production Issue"),
        ("PROD_OUTPUT", "Production Output"),
        ("WASTAGE", "Production Wastage / Damage"),
        ("DISPATCH", "Sales Dispatch"),
        ("CREDIT_NOTE", "Invoice Cancellation / Return"),
        ("ADJUSTMENT", "Manual Adjustment"),
    ]
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="ledger_entries")
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_CHOICES)
    quantity_delta = models.DecimalField(max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))
    reference_type = models.CharField(max_length=40, blank=True)
    reference_id = models.IntegerField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        sign = "+" if self.quantity_delta >= 0 else ""
        return f"{self.item.name}: {sign}{self.quantity_delta} ({self.movement_type})"


class Purchase(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchases")
    bill_number = models.CharField(max_length=40)
    purchase_date = models.DateField(default=timezone.localdate)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"PUR-{self.id}: {self.quantity} {self.item.base_unit} {self.item.name}"


class ProductionRun(models.Model):
    run_code = models.CharField(max_length=30, unique=True)
    bom = models.ForeignKey(BOM, on_delete=models.PROTECT, related_name="runs")
    batches = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("1.00"))
    run_date = models.DateField(default=timezone.localdate)
    expected_output = models.DecimalField(max_digits=12, decimal_places=3)
    actual_output = models.DecimalField(max_digits=12, decimal_places=3)
    yield_variance_pct = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"))
    yield_variance_flagged = models.BooleanField(default=False)
    labour_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    overhead_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_material_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    cost_per_packet = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.run_code} - {self.bom.finished_item.name}"


class ProductionRunMaterial(models.Model):
    run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE, related_name="materials")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    planned_qty = models.DecimalField(max_digits=12, decimal_places=3)
    actual_qty = models.DecimalField(max_digits=12, decimal_places=3)
    wastage_qty = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0.000"))
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))
    variance_pct = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"))
    variance_flagged = models.BooleanField(default=False)

    def total_consumed(self) -> Decimal:
        return self.actual_qty + self.wastage_qty

    def __str__(self):
        return f"{self.run.run_code} | {self.item.name}: {self.actual_qty}"


class SalesOrder(models.Model):
    STATUS_CHOICES = [
        ("SUBMITTED", "Submitted"),
        ("APPROVED", "Approved"),
        ("INVOICED", "Invoiced"),
        ("DISPATCHED", "Dispatched"),
        ("REJECTED", "Rejected"),
    ]
    order_number = models.CharField(max_length=30, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    salesman = models.ForeignKey(User, on_delete=models.PROTECT, related_name="sales_orders")
    order_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="SUBMITTED")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def total_before_tax(self) -> Decimal:
        return sum((line.line_total() for line in self.lines.all()), Decimal("0.00"))

    def __str__(self):
        return f"{self.order_number} - {self.customer.name} ({self.status})"


class SalesOrderLine(models.Model):
    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(
        Item, on_delete=models.PROTECT, limit_choices_to={"category": "FINISHED"}
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    def line_total(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.item.name} x {self.quantity}"


class Invoice(models.Model):
    invoice_number = models.CharField(max_length=35, unique=True)
    order = models.OneToOneField(SalesOrder, on_delete=models.PROTECT, related_name="invoice")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices")
    invoice_date = models.DateField(default=timezone.localdate)
    is_interstate = models.BooleanField(default=False)
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2)
    cgst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    sgst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    igst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    is_dispatched = models.BooleanField(default=False)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    is_cancelled = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def amount_paid(self) -> Decimal:
        val = self.payments.aggregate(t=Sum("amount"))["t"]
        return val if val is not None else Decimal("0.00")

    def balance_due(self) -> Decimal:
        if self.is_cancelled:
            return Decimal("0.00")
        return self.total_amount - self.amount_paid()

    def __str__(self):
        return f"{self.invoice_number} - {self.customer.name} (₹{self.total_amount})"


class Payment(models.Model):
    MODE_CHOICES = [
        ("UPI", "UPI"),
        ("NEFT", "NEFT / RTGS"),
        ("CASH", "Cash"),
        ("CHEQUE", "Cheque"),
    ]
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="payments")
    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, related_name="payments", null=True, blank=True
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_date = models.DateField(default=timezone.localdate)
    mode = models.CharField(max_length=20, choices=MODE_CHOICES, default="UPI")
    reference = models.CharField(max_length=80, blank=True)
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"₹{self.amount} from {self.customer.name}"


class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=60)
    model_name = models.CharField(max_length=60)
    object_id = models.IntegerField(null=True, blank=True)
    details = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {self.action} on {self.model_name}#{self.object_id}"
