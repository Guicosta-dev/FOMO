from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import ValidationError

from .models import Event, Order, OrderItem, TicketBatch, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("FOMO", {"fields": ("role",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("FOMO", {"fields": ("email", "role")}),)
    list_display = UserAdmin.list_display + ("role",)
    list_filter = UserAdmin.list_filter + ("role",)


class TicketBatchInline(admin.TabularInline):
    model = TicketBatch
    extra = 0


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "producer", "city", "starts_at", "status")
    list_filter = ("status", "city")
    search_fields = ("title", "producer__username", "city")
    readonly_fields = ("slug", "published_at", "created_at", "updated_at")
    inlines = [TicketBatchInline]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ("batch", "quantity", "unit_price")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("short_code", "user", "event", "status", "total", "created_at")
    list_filter = ("status",)
    search_fields = ("user__username", "event__title", "public_id")
    readonly_fields = (
        "public_id", "user", "event", "status", "total",
        "created_at", "expires_at", "confirmed_at", "cancelled_at",
    )
    inlines = [OrderItemInline]
    actions = ["confirm_selected"]

    @admin.action(description="Confirmar pedidos selecionados (simula o pagamento)")
    def confirm_selected(self, request, queryset):
        confirmed = 0
        for order in queryset:
            try:
                order.confirm()
                confirmed += 1
            except ValidationError as error:
                self.message_user(
                    request,
                    f"Pedido {order.short_code}: {' '.join(error.messages)}",
                    level=messages.WARNING,
                )
        if confirmed:
            self.message_user(request, f"{confirmed} pedido(s) confirmado(s).")


