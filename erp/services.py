from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.utils import timezone
from .models import (
    FACTORY_STATE,
    AuditLog,
    BOM,
    Invoice,
    Item,
    Payment,
    ProductionRun,
    ProductionRunMaterial,
    Purchase,
    SalesOrder,
    StockLedger,
)


def q2(val: Decimal) -> Decimal:
    return Decimal(val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def q3(val: Decimal) -> Decimal:
    return Decimal(val).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def convert_unit(qty: Decimal, from_unit: str, to_unit: str, packets_per_carton: int = 20) -> Decimal:
    qty = Decimal(qty)
    if from_unit == to_unit:
        return qty
    conversions = {
        ("kg", "g"): Decimal("1000"),
        ("g", "kg"): Decimal("0.001"),
        ("litre", "ml"): Decimal("1000"),
        ("ml", "litre"): Decimal("0.001"),
        ("carton", "packet"): Decimal(packets_per_carton),
        ("packet", "carton"): Decimal("1") / Decimal(packets_per_carton),
    }
    factor = conversions.get((from_unit, to_unit))
    if factor is None:
        raise ValueError(f"Unsupported unit conversion: {from_unit} -> {to_unit}")
    return q3(qty * factor)


def get_financial_year_str(dt=None) -> str:
    dt = dt or timezone.localdate()
    start_year = dt.year if dt.month >= 4 else dt.year - 1
    end_year_short = str(start_year + 1)[-2:]
    return f"{start_year}-{end_year_short}"


def log_audit(user, action: str, model_name: str, object_id: int, details: str = ""):
    AuditLog.objects.create(
        user=user,
        action=action,
        model_name=model_name,
        object_id=object_id,
        details=details,
    )


@transaction.atomic
def record_purchase(supplier, bill_number: str, item: Item, quantity: Decimal, unit_price: Decimal, user, purchase_date=None) -> Purchase:
    quantity = q3(quantity)
    unit_price = q2(unit_price)
    total_amount = q2(quantity * unit_price)

    purchase = Purchase.objects.create(
        supplier=supplier,
        bill_number=bill_number,
        purchase_date=purchase_date or timezone.localdate(),
        item=item,
        quantity=quantity,
        unit_price=unit_price,
        total_amount=total_amount,
        created_by=user,
    )

    StockLedger.objects.create(
        item=item,
        movement_type="PURCHASE",
        quantity_delta=quantity,
        unit_cost=unit_price,
        reference_type="Purchase",
        reference_id=purchase.id,
        notes=f"Bill {bill_number} from {supplier.name}",
        created_by=user,
    )

    log_audit(user, "CREATE", "Purchase", purchase.id, f"{quantity} {item.base_unit} {item.name} @ ₹{unit_price}")
    return purchase


@transaction.atomic
def create_production_run(
    bom: BOM,
    batches: Decimal,
    actual_output: Decimal,
    material_inputs: list,
    user,
    labour_cost: Decimal = Decimal("0.00"),
    overhead_cost: Decimal = Decimal("0.00"),
    notes: str = "",
    run_date=None,
) -> ProductionRun:
    """
    material_inputs: list of dicts:
      {"item": Item, "planned_qty": Decimal, "actual_qty": Decimal, "wastage_qty": Decimal}
    """
    batches = Decimal(batches)
    actual_output = q3(actual_output)
    expected_output = q3(bom.expected_output * batches)
    labour_cost = q2(labour_cost)
    overhead_cost = q2(overhead_cost)

    yield_var_pct = Decimal("0.00")
    if expected_output > 0:
        yield_var_pct = q2(((actual_output - expected_output) / expected_output) * Decimal("100"))
    yield_flagged = abs(yield_var_pct) > bom.tolerance_percent

    next_num = ProductionRun.objects.count() + 1
    run_code = f"RUN-{timezone.localdate().strftime('%Y%m')}-{next_num:03d}"

    run = ProductionRun.objects.create(
        run_code=run_code,
        bom=bom,
        batches=batches,
        run_date=run_date or timezone.localdate(),
        expected_output=expected_output,
        actual_output=actual_output,
        yield_variance_pct=yield_var_pct,
        yield_variance_flagged=yield_flagged,
        labour_cost=labour_cost,
        overhead_cost=overhead_cost,
        notes=notes,
        created_by=user,
    )

    total_material_cost = Decimal("0.00")

    for entry in material_inputs:
        item = entry["item"]
        planned = q3(entry["planned_qty"])
        actual = q3(entry["actual_qty"])
        wastage = q3(entry.get("wastage_qty", Decimal("0")))
        total_used = q3(actual + wastage)

        unit_cost = item.weighted_avg_cost()
        var_pct = Decimal("0.00")
        if planned > 0:
            var_pct = q2(((total_used - planned) / planned) * Decimal("100"))
        flagged = abs(var_pct) > bom.tolerance_percent

        ProductionRunMaterial.objects.create(
            run=run,
            item=item,
            planned_qty=planned,
            actual_qty=actual,
            wastage_qty=wastage,
            unit_cost=unit_cost,
            variance_pct=var_pct,
            variance_flagged=flagged,
        )

        if actual > 0:
            StockLedger.objects.create(
                item=item,
                movement_type="PROD_ISSUE",
                quantity_delta=-actual,
                unit_cost=unit_cost,
                reference_type="ProductionRun",
                reference_id=run.id,
                notes=f"Issued to {run.run_code}",
                created_by=user,
            )

        if wastage > 0:
            StockLedger.objects.create(
                item=item,
                movement_type="WASTAGE",
                quantity_delta=-wastage,
                unit_cost=unit_cost,
                reference_type="ProductionRun",
                reference_id=run.id,
                notes=f"Damaged/wastage in {run.run_code}",
                created_by=user,
            )

        total_material_cost += total_used * unit_cost

    run.total_material_cost = q2(total_material_cost)
    total_run_cost = run.total_material_cost + labour_cost + overhead_cost
    run.cost_per_packet = q2(total_run_cost / actual_output) if actual_output > 0 else Decimal("0.00")
    run.save()

    if actual_output > 0:
        StockLedger.objects.create(
            item=bom.finished_item,
            movement_type="PROD_OUTPUT",
            quantity_delta=actual_output,
            unit_cost=run.cost_per_packet,
            reference_type="ProductionRun",
            reference_id=run.id,
            notes=f"Output from {run.run_code}",
            created_by=user,
        )

    log_audit(
        user,
        "CREATE",
        "ProductionRun",
        run.id,
        f"{run.run_code}: output={actual_output}, yield_var={yield_var_pct}%, cost/pkt=₹{run.cost_per_packet}",
    )
    return run


@transaction.atomic
def update_run_overheads(run: ProductionRun, labour_cost: Decimal, overhead_cost: Decimal, user) -> ProductionRun:
    run.labour_cost = q2(labour_cost)
    run.overhead_cost = q2(overhead_cost)
    total_cost = run.total_material_cost + run.labour_cost + run.overhead_cost
    run.cost_per_packet = q2(total_cost / run.actual_output) if run.actual_output > 0 else Decimal("0.00")
    run.save()

    StockLedger.objects.filter(
        reference_type="ProductionRun",
        reference_id=run.id,
        movement_type="PROD_OUTPUT",
    ).update(unit_cost=run.cost_per_packet)

    log_audit(
        user,
        "UPDATE_OVERHEADS",
        "ProductionRun",
        run.id,
        f"Labour=₹{run.labour_cost}, Overhead=₹{run.overhead_cost}, New cost/pkt=₹{run.cost_per_packet}",
    )
    return run


def check_order_stock(order: SalesOrder) -> list:
    """Returns a list of dicts with stock status for each line in the order."""
    results = []
    for line in order.lines.select_related("item"):
        available = line.item.current_stock()
        shortage = max(Decimal("0.000"), line.quantity - available)
        results.append({
            "line": line,
            "item": line.item,
            "ordered": line.quantity,
            "available": available,
            "shortage": shortage,
            "sufficient": shortage == 0,
        })
    return results


@transaction.atomic
def create_gst_invoice(order: SalesOrder, user) -> Invoice:
    if hasattr(order, "invoice"):
        raise ValueError("Invoice already exists for this order.")

    is_interstate = order.customer.state.strip().lower() != FACTORY_STATE.lower()
    taxable_value = Decimal("0.00")
    total_tax = Decimal("0.00")

    for line in order.lines.select_related("item"):
        line_val = q2(line.quantity * line.unit_price)
        taxable_value += line_val
        total_tax += q2(line_val * line.item.gst_rate / Decimal("100"))

    if is_interstate:
        igst = q2(total_tax)
        cgst = Decimal("0.00")
        sgst = Decimal("0.00")
    else:
        cgst = q2(total_tax / Decimal("2"))
        sgst = q2(total_tax - cgst)
        igst = Decimal("0.00")

    total_amount = q2(taxable_value + cgst + sgst + igst)
    fy = get_financial_year_str()
    prefix = f"INV/{fy}/"
    count = Invoice.objects.filter(invoice_number__startswith=prefix).count() + 1
    inv_number = f"{prefix}{count:04d}"

    invoice = Invoice.objects.create(
        invoice_number=inv_number,
        order=order,
        customer=order.customer,
        is_interstate=is_interstate,
        taxable_value=taxable_value,
        cgst_amount=cgst,
        sgst_amount=sgst,
        igst_amount=igst,
        total_amount=total_amount,
        created_by=user,
    )

    order.status = "INVOICED"
    order.save()

    log_audit(user, "CREATE", "Invoice", invoice.id, f"{inv_number} total=₹{total_amount} interstate={is_interstate}")
    return invoice


@transaction.atomic
def dispatch_invoice(invoice: Invoice, user) -> Invoice:
    if invoice.is_dispatched:
        raise ValueError("Invoice is already dispatched.")
    if invoice.is_cancelled:
        raise ValueError("Cannot dispatch a cancelled invoice.")

    stock_checks = check_order_stock(invoice.order)
    shortages = [c for c in stock_checks if not c["sufficient"]]
    if shortages:
        msg = ", ".join(f"{s['item'].name} short by {s['shortage']}" for s in shortages)
        raise ValueError(f"Insufficient finished-goods stock: {msg}")

    for line in invoice.order.lines.select_related("item"):
        StockLedger.objects.create(
            item=line.item,
            movement_type="DISPATCH",
            quantity_delta=-line.quantity,
            unit_cost=line.unit_price,
            reference_type="Invoice",
            reference_id=invoice.id,
            notes=f"Dispatched {invoice.invoice_number} to {invoice.customer.name}",
            created_by=user,
        )

    invoice.is_dispatched = True
    invoice.dispatched_at = timezone.now()
    invoice.save()

    invoice.order.status = "DISPATCHED"
    invoice.order.save()

    log_audit(user, "DISPATCH", "Invoice", invoice.id, f"Dispatched {invoice.invoice_number}")
    return invoice


@transaction.atomic
def cancel_invoice_with_credit_note(invoice: Invoice, user) -> Invoice:
    if invoice.is_cancelled:
        raise ValueError("Invoice is already cancelled.")

    if invoice.is_dispatched:
        for line in invoice.order.lines.select_related("item"):
            StockLedger.objects.create(
                item=line.item,
                movement_type="CREDIT_NOTE",
                quantity_delta=line.quantity,
                unit_cost=line.unit_price,
                reference_type="Invoice",
                reference_id=invoice.id,
                notes=f"Credit note reversal for {invoice.invoice_number}",
                created_by=user,
            )

    invoice.is_cancelled = True
    invoice.save()
    log_audit(user, "CREDIT_NOTE_CANCEL", "Invoice", invoice.id, f"Cancelled {invoice.invoice_number} via Credit Note")
    return invoice


@transaction.atomic
def record_payment(customer, invoice, amount: Decimal, mode: str, reference: str, user, payment_date=None) -> Payment:
    amount = q2(amount)
    payment = Payment.objects.create(
        customer=customer,
        invoice=invoice,
        amount=amount,
        mode=mode,
        reference=reference,
        payment_date=payment_date or timezone.localdate(),
        recorded_by=user,
    )
    log_audit(user, "CREATE", "Payment", payment.id, f"₹{amount} from {customer.name} via {mode}")
    return payment