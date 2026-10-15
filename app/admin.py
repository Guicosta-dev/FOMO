"""Painel administrativo com busca, filtros e confirmação controlada de pedidos."""

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .forms import SignupForm
from .models import Event, Favorite, Order, OrderItem, TicketBatch, User
from .services import OrderRuleError, cancel_order, confirm_order

admin.site.site_header = "FOMO · Administração"
admin.site.site_title = "FOMO Admin"
admin.site.index_title = "Eventos, lotes e pedidos"


@admin.register(User)
class FomoUserAdmin(UserAdmin):
    add_form = SignupForm
    list_display = ["username", "email", "role", "is_staff", "is_active"]
    search_fields = ["username", "email"]
    list_filter = ["role", "is_staff", "is_active"]
    fieldsets = UserAdmin.fieldsets + (("FOMO", {"fields": ["role"]}),)
    add_fieldsets = (
        (
            None,
            {
                "classes": ["wide"],
                "fields": ["username", "email", "role", "password1", "password2"],
            },
        ),
    )


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["title", "producer", "city", "starts_at", "status"]
    search_fields = ["title", "city", "venue_name", "producer__username"]
    list_filter = ["status", "city"]
    readonly_fields = ["slug", "status", "published_at", "created_at"]
    actions = ["publish", "unpublish"]

    @admin.action(description="Publicar eventos com lote ativo")
    def publish(self, request, queryset):
        for pk in queryset.values_list("pk", flat=True):
            try:
                with transaction.atomic():
                    event = Event.objects.select_for_update().get(pk=pk)
                    if event.starts_at <= timezone.now():
                        raise ValidationError("O evento já começou.")
                    event.status = Event.Status.PUBLISHED
                    event.published_at = timezone.now()
                    event.full_clean()
                    event.save(update_fields=["status", "published_at"])
            except ValidationError as error:
                self.message_user(
                    request, f"Evento {pk}: {'; '.join(error.messages)}", messages.ERROR
                )
            else:
                self.message_user(request, f"Evento {pk} publicado.", messages.SUCCESS)

    @admin.action(description="Despublicar eventos")
    def unpublish(self, request, queryset):
        for pk in queryset.values_list("pk", flat=True):
            with transaction.atomic():
                event = Event.objects.select_for_update().get(pk=pk)
                event.status = Event.Status.DRAFT
                event.save(update_fields=["status"])
        self.message_user(request, "Eventos despublicados.", messages.SUCCESS)


@admin.register(TicketBatch)
class TicketBatchAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "event",
        "price",
        "quantity",
        "sold",
        "reserved",
        "available",
        "is_active",
    ]
    search_fields = ["name", "event__title"]
    list_filter = ["is_active", "event"]
    readonly_fields = ["sold", "reserved", "taken", "available"]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ["batch", "quantity", "unit_price", "subtotal"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "short_code",
        "buyer",
        "event",
        "status",
        "total",
        "created_at",
        "expires_at",
    ]
    search_fields = ["public_id", "buyer__username", "buyer__email", "event__title"]
    list_filter = ["status", "created_at"]
    readonly_fields = [
        "public_id",
        "buyer",
        "event",
        "status",
        "total",
        "created_at",
        "expires_at",
        "confirmed_at",
    ]
    inlines = [OrderItemInline]
    actions = ["confirm_pending", "cancel_pending"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Confirmar pedidos pendentes (modo administrativo)")
    def confirm_pending(self, request, queryset):
        for order in queryset:
            try:
                confirm_order(public_id=order.public_id)
            except OrderRuleError as error:
                self.message_user(
                    request, f"{order.short_code}: {error}", messages.ERROR
                )
            else:
                self.message_user(
                    request, f"Pedido {order.short_code} confirmado.", messages.SUCCESS
                )

    @admin.action(description="Cancelar pedidos pendentes e devolver reservas")
    def cancel_pending(self, request, queryset):
        for order in queryset:
            try:
                cancel_order(public_id=order.public_id)
            except OrderRuleError as error:
                self.message_user(
                    request, f"{order.short_code}: {error}", messages.ERROR
                )
            else:
                self.message_user(
                    request, f"Reserva {order.short_code} liberada.", messages.SUCCESS
                )


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ["user", "event", "created_at"]
    search_fields = ["user__username", "event__title"]
