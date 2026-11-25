from django.contrib import admin
from .models import (
    UserProfile, Item, Customer, Supplier,
    BOM, BOMLine, StockLedger, Purchase,
    ProductionRun, ProductionRunMaterial,
    SalesOrder, SalesOrderLine, Invoice, Payment, AuditLog
)


class BOMLineInline(admin.TabularInline):
    model = BOMLine
    extra = 1


@admin.register(BOM)
class BOMAdmin(admin.ModelAdmin):
    inlines = [BOMLineInline]


class SalesOrderLineInline(admin.TabularInline):
    model = SalesOrderLine
    extra = 1


@admin.register(SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "customer", "salesman", "status", "order_date")
    inlines = [SalesOrderLineInline]


admin.site.register(UserProfile)
admin.site.register(Item)
admin.site.register(Customer)
admin.site.register(Supplier)
admin.site.register(StockLedger)
admin.site.register(Purchase)
admin.site.register(ProductionRun)
admin.site.register(ProductionRunMaterial)
admin.site.register(Invoice)
admin.site.register(Payment)
admin.site.register(AuditLog)