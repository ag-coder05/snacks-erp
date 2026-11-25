from decimal import Decimal
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from .models import BOM, Customer, Invoice, Item, ProductionRun, Purchase, SalesOrder, Supplier
from .services import create_production_run, record_purchase, update_run_overheads


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