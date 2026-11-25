from decimal import Decimal
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from .models import Customer, Invoice, Item, ProductionRun, SalesOrder


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