from decimal import Decimal
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from .exports import export_csv_response, export_excel_response
from .models import (
    BOM,
    FACTORY_STATE,
    Customer,
    Invoice,
    Item,
    Payment,
    ProductionRun,
    Purchase,
    SalesOrder,
    SalesOrderLine,
    StockLedger,
    Supplier,
)
from .services import (
    cancel_invoice_with_credit_note,
    check_order_stock,
    create_gst_invoice,
    create_production_run,
    dispatch_invoice,
    record_payment,
    record_purchase,
    update_run_overheads,
)


def get_user_role(user):
    if hasattr(user, "profile"):
        return user.profile.role
    return "ADMIN" if user.is_superuser else "SALESMAN"


def role_required(*allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            role = get_user_role(request.user)
            if role not in allowed_roles and not request.user.is_superuser:
                messages.error(request, f"Access denied for role: {role}.")
                return redirect("dashboard")
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


@login_required
def dashboard(request):
    role = get_user_role(request.user)
    items = Item.objects.all().order_by("category", "name")
    stock_summary = [
        {
            "item": item,
            "stock": item.current_stock(),
            "avg_cost": item.weighted_avg_cost(),
            "low_stock": item.current_stock() <= item.reorder_level,
        }
        for item in items
    ]

    recent_runs = ProductionRun.objects.select_related("bom__finished_item").order_by("-created_at")[:5]
    variance_alerts = [r for r in recent_runs if r.yield_variance_flagged or r.materials.filter(variance_flagged=True).exists()]

    if role == "SALESMAN":
        orders = SalesOrder.objects.filter(salesman=request.user).select_related("customer").order_by("-created_at")[:10]
    else:
        orders = SalesOrder.objects.select_related("customer", "salesman").order_by("-created_at")[:10]

    customers = Customer.objects.all()
    total_outstanding = sum((c.outstanding_balance() for c in customers), Decimal("0.00"))
    total_sales = sum((inv.total_amount for inv in Invoice.objects.filter(is_cancelled=False)), Decimal("0.00"))

    return render(
        request,
        "erp/dashboard.html",
        {
            "role": role,
            "stock_summary": stock_summary,
            "recent_runs": recent_runs,
            "variance_alerts": variance_alerts,
            "orders": orders,
            "total_outstanding": total_outstanding,
            "total_sales": total_sales,
        },
    )
@role_required("PRODUCTION", "ADMIN", "OWNER")
def purchase_list_create(request):
    if request.method == "POST":
        try:
            supplier = get_object_or_404(Supplier, pk=request.POST["supplier"])
            item = get_object_or_404(Item, pk=request.POST["item"])
            bill_number = request.POST["bill_number"].strip()
            quantity = Decimal(request.POST["quantity"])
            unit_price = Decimal(request.POST["unit_price"])

            record_purchase(
                supplier=supplier,
                bill_number=bill_number,
                item=item,
                quantity=quantity,
                unit_price=unit_price,
                user=request.user,
            )
            messages.success(request, f"Recorded purchase of {quantity} {item.base_unit} {item.name}.")
            return redirect("purchases")
        except Exception as e:
            messages.error(request, f"Error recording purchase: {e}")

    purchases = Purchase.objects.select_related("supplier", "item").order_by("-created_at")
    suppliers = Supplier.objects.all()
    raw_items = Item.objects.filter(category__in=["RAW", "PACKAGING"]).order_by("name")
    return render(
        request,
        "erp/purchases.html",
        {"purchases": purchases, "suppliers": suppliers, "raw_items": raw_items},
    )


@role_required("PRODUCTION", "ADMIN", "OWNER", "AUDITOR")
def production_list(request):
    runs = ProductionRun.objects.select_related("bom__finished_item").prefetch_related("materials").order_by("-created_at")
    return render(request, "erp/production_list.html", {"runs": runs, "role": get_user_role(request.user)})


@role_required("PRODUCTION", "ADMIN")
def production_create(request):
    boms = BOM.objects.select_related("finished_item").prefetch_related("lines__material")
    selected_bom_id = request.GET.get("bom") or request.POST.get("bom")
    selected_bom = boms.filter(pk=selected_bom_id).first() if selected_bom_id else boms.first()

    if request.method == "POST" and selected_bom:
        try:
            batches = Decimal(request.POST.get("batches") or "1")
            actual_output = Decimal(request.POST.get("actual_output") or "0")
            labour_cost = Decimal(request.POST.get("labour_cost") or "0")
            overhead_cost = Decimal(request.POST.get("overhead_cost") or "0")
            notes = request.POST.get("notes", "").strip()

            material_inputs = []
            for line in selected_bom.lines.select_related("material"):
                planned_qty = line.quantity_per_batch * batches
                actual_qty = Decimal(request.POST.get(f"actual_{line.id}") or "0")
                wastage_qty = Decimal(request.POST.get(f"wastage_{line.id}") or "0")
                material_inputs.append({
                    "item": line.material,
                    "planned_qty": planned_qty,
                    "actual_qty": actual_qty,
                    "wastage_qty": wastage_qty,
                })

            run = create_production_run(
                bom=selected_bom,
                batches=batches,
                actual_output=actual_output,
                material_inputs=material_inputs,
                user=request.user,
                labour_cost=labour_cost,
                overhead_cost=overhead_cost,
                notes=notes,
            )
            messages.success(request, f"Production Run {run.run_code} saved! Stock ledger updated.")
            return redirect("production_detail", pk=run.id)
        except Exception as e:
            messages.error(request, f"Error saving production run: {e}")

    return render(request, "erp/production_create.html", {"boms": boms, "selected_bom": selected_bom})


@role_required("PRODUCTION", "ADMIN", "OWNER", "AUDITOR")
def production_detail(request, pk):
    run = get_object_or_404(ProductionRun.objects.select_related("bom__finished_item"), pk=pk)
    role = get_user_role(request.user)

    if request.method == "POST" and role in ("PRODUCTION", "ADMIN"):
        try:
            labour_cost = Decimal(request.POST.get("labour_cost") or "0")
            overhead_cost = Decimal(request.POST.get("overhead_cost") or "0")
            update_run_overheads(run, labour_cost=labour_cost, overhead_cost=overhead_cost, user=request.user)
            messages.success(request, f"Overheads updated. New cost per packet: ₹{run.cost_per_packet}")
            return redirect("production_detail", pk=run.id)
        except Exception as e:
            messages.error(request, f"Error updating overheads: {e}")

    materials = run.materials.select_related("item").all()
    selling_price = run.bom.finished_item.selling_price
    margin = selling_price - run.cost_per_packet
    return render(
        request,
        "erp/production_detail.html",
        {
            "run": run,
            "materials": materials,
            "selling_price": selling_price,
            "margin": margin,
            "role": role,
        },
    )
@role_required("SALESMAN", "ADMIN")
def salesman_order(request):
    customers = Customer.objects.all().order_by("name")
    products = Item.objects.filter(category="FINISHED").order_by("name")

    if request.method == "POST":
        try:
            customer = get_object_or_404(Customer, pk=request.POST["customer"])
            product = get_object_or_404(Item, pk=request.POST["item"])
            qty = Decimal(request.POST["quantity"])
            notes = request.POST.get("notes", "").strip()
            if qty <= 0:
                raise ValueError("Quantity must be greater than zero.")

            next_num = SalesOrder.objects.count() + 1
            order_number = f"SO-{timezone.localdate().strftime('%Y%m')}-{next_num:03d}"

            order = SalesOrder.objects.create(
                order_number=order_number,
                customer=customer,
                salesman=request.user,
                notes=notes,
            )
            SalesOrderLine.objects.create(
                order=order,
                item=product,
                quantity=qty,
                unit_price=product.selling_price,
            )
            messages.success(request, f"Order {order.order_number} submitted for {customer.name} ({qty} {product.base_unit}).")
            return redirect("salesman_order")
        except Exception as e:
            messages.error(request, f"Error placing order: {e}")

    my_orders = SalesOrder.objects.filter(salesman=request.user).select_related("customer").prefetch_related("lines__item").order_by("-created_at")
    return render(
        request,
        "erp/salesman_order.html",
        {"customers": customers, "products": products, "my_orders": my_orders},
    )


@login_required
def orders_list(request):
    role = get_user_role(request.user)
    qs = SalesOrder.objects.select_related("customer", "salesman").prefetch_related("lines__item").order_by("-created_at")
    if role == "SALESMAN":
        qs = qs.filter(salesman=request.user)
    return render(request, "erp/orders_list.html", {"orders": qs, "role": role})


@login_required
def order_detail(request, pk):
    order = get_object_or_404(SalesOrder.objects.select_related("customer", "salesman"), pk=pk)
    role = get_user_role(request.user)
    if role == "SALESMAN" and order.salesman != request.user:
        messages.error(request, "You can only view your own orders.")
        return redirect("orders_list")

    stock_checks = check_order_stock(order)
    all_sufficient = all(c["sufficient"] for c in stock_checks)
    is_interstate = order.customer.state.strip().lower() != FACTORY_STATE.lower()
    invoice = getattr(order, "invoice", None)

    if request.method == "POST" and role == "ADMIN":
        action = request.POST.get("action")
        try:
            if action == "approve":
                order.status = "APPROVED"
                order.save()
                messages.success(request, f"Order {order.order_number} approved.")
            elif action == "reject":
                order.status = "REJECTED"
                order.save()
                messages.success(request, f"Order {order.order_number} rejected.")
            elif action == "invoice":
                inv = create_gst_invoice(order, user=request.user)
                messages.success(request, f"GST Invoice {inv.invoice_number} generated (₹{inv.total_amount}).")
            elif action == "dispatch" and invoice:
                dispatch_invoice(invoice, user=request.user)
                messages.success(request, f"Invoice {invoice.invoice_number} dispatched. Finished-goods stock updated.")
            elif action == "cancel_invoice" and invoice:
                cancel_invoice_with_credit_note(invoice, user=request.user)
                messages.success(request, f"Invoice {invoice.invoice_number} cancelled via Credit Note.")
        except Exception as e:
            messages.error(request, str(e))
        return redirect("order_detail", pk=order.id)

    return render(
        request,
        "erp/order_detail.html",
        {
            "order": order,
            "stock_checks": stock_checks,
            "all_sufficient": all_sufficient,
            "is_interstate": is_interstate,
            "invoice": invoice,
            "role": role,
        },
    )


@login_required
def invoice_pdf(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("customer", "order"), pk=pk)
    lines = invoice.order.lines.select_related("item").all()
    html_string = render_to_string(
        "erp/invoice_pdf.html",
        {"invoice": invoice, "lines": lines, "factory_state": FACTORY_STATE},
    )
    try:
        import weasyprint
        pdf_bytes = weasyprint.HTML(string=html_string).write_pdf()
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{invoice.invoice_number.replace("/", "-")}.pdf"'
        return response
    except Exception:
        # Fallback to print-ready HTML if system GTK libraries are absent on Windows
        return HttpResponse(html_string)


@role_required("ADMIN", "OWNER", "AUDITOR")
def payments_view(request):
    role = get_user_role(request.user)
    if request.method == "POST" and role == "ADMIN":
        try:
            invoice = get_object_or_404(Invoice, pk=request.POST["invoice"])
            amount = Decimal(request.POST["amount"])
            mode = request.POST["mode"]
            reference = request.POST.get("reference", "").strip()
            record_payment(
                customer=invoice.customer,
                invoice=invoice,
                amount=amount,
                mode=mode,
                reference=reference,
                user=request.user,
            )
            messages.success(request, f"Payment of ₹{amount} recorded for {invoice.customer.name}.")
            return redirect("payments")
        except Exception as e:
            messages.error(request, f"Error recording payment: {e}")

    open_invoices = Invoice.objects.filter(is_cancelled=False).select_related("customer").order_by("-created_at")
    payments = Payment.objects.select_related("customer", "invoice").order_by("-created_at")
    customer_balances = [
        {
            "customer": c,
            "invoiced": c.total_invoiced(),
            "paid": c.total_paid(),
            "outstanding": c.outstanding_balance(),
        }
        for c in Customer.objects.all()
    ]
    return render(
        request,
        "erp/payments.html",
        {
            "open_invoices": open_invoices,
            "payments": payments,
            "customer_balances": customer_balances,
            "role": role,
        },
    )


@role_required("ADMIN", "OWNER", "AUDITOR")
def reports_hub(request):
    date_from = request.GET.get("from") or ""
    date_to = request.GET.get("to") or ""

    invoices = Invoice.objects.filter(is_cancelled=False).select_related("customer").order_by("-invoice_date")
    purchases = Purchase.objects.select_related("supplier", "item").order_by("-purchase_date")
    ledger = StockLedger.objects.select_related("item", "created_by").order_by("-created_at")[:100]
    runs = ProductionRun.objects.select_related("bom__finished_item").order_by("-run_date")

    if date_from:
        invoices = invoices.filter(invoice_date__gte=date_from)
        purchases = purchases.filter(purchase_date__gte=date_from)
        runs = runs.filter(run_date__gte=date_from)
    if date_to:
        invoices = invoices.filter(invoice_date__lte=date_to)
        purchases = purchases.filter(purchase_date__lte=date_to)
        runs = runs.filter(run_date__lte=date_to)

    gst_summary = {
        "taxable": sum((i.taxable_value for i in invoices), Decimal("0.00")),
        "cgst": sum((i.cgst_amount for i in invoices), Decimal("0.00")),
        "sgst": sum((i.sgst_amount for i in invoices), Decimal("0.00")),
        "igst": sum((i.igst_amount for i in invoices), Decimal("0.00")),
        "total": sum((i.total_amount for i in invoices), Decimal("0.00")),
    }

    return render(
        request,
        "erp/reports.html",
        {
            "date_from": date_from,
            "date_to": date_to,
            "invoices": invoices,
            "purchases": purchases,
            "ledger": ledger,
            "runs": runs,
            "gst_summary": gst_summary,
        },
    )


@role_required("ADMIN", "OWNER", "AUDITOR")
def export_report(request, report_type, fmt):
    if report_type == "sales":
        headers = ["Invoice #", "Date", "Customer", "State", "GSTIN", "Taxable", "CGST", "SGST", "IGST", "Total", "Balance Due"]
        rows = [
            [
                i.invoice_number, str(i.invoice_date), i.customer.name, i.customer.state, i.customer.gstin,
                i.taxable_value, i.cgst_amount, i.sgst_amount, i.igst_amount, i.total_amount, i.balance_due(),
            ]
            for i in Invoice.objects.filter(is_cancelled=False).select_related("customer")
        ]
    elif report_type == "stock_ledger":
        headers = ["Timestamp", "SKU", "Item", "Movement", "Delta", "Unit", "Unit Cost", "Source", "Notes"]
        rows = [
            [
                e.created_at.strftime("%Y-%m-%d %H:%M"), e.item.sku, e.item.name, e.get_movement_type_display(),
                e.quantity_delta, e.item.base_unit, e.unit_cost, f"{e.reference_type}#{e.reference_id or ''}", e.notes,
            ]
            for e in StockLedger.objects.select_related("item").all()
        ]
    elif report_type == "production":
        headers = ["Run Code", "Date", "Product", "Expected", "Actual", "Yield Var %", "Flagged", "Material Cost", "Labour", "Overhead", "Cost/Pkt"]
        rows = [
            [
                r.run_code, str(r.run_date), r.bom.finished_item.name, r.expected_output, r.actual_output,
                r.yield_variance_pct, "YES" if r.yield_variance_flagged else "NO",
                r.total_material_cost, r.labour_cost, r.overhead_cost, r.cost_per_packet,
            ]
            for r in ProductionRun.objects.select_related("bom__finished_item").all()
        ]
    else:
        headers = ["Customer", "State", "GSTIN", "Total Invoiced", "Total Paid", "Outstanding"]
        rows = [
            [c.name, c.state, c.gstin, c.total_invoiced(), c.total_paid(), c.outstanding_balance()]
            for c in Customer.objects.all()
        ]

    if fmt == "csv":
        return export_csv_response(f"snacks_erp_{report_type}", headers, rows)
    return export_excel_response(f"snacks_erp_{report_type}", report_type.title(), headers, rows)