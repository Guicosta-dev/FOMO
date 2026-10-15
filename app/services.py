"""Regras de pedidos e estoque. Todos os ajustes são transacionais."""

from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from .models import Event, Order, OrderItem, TicketBatch


class OrderRuleError(ValueError):
    """Regra de compra que deve ser apresentada ao usuário."""


def _release_locked_order(order, status):
    """Chamador deve estar em atomic() e ter bloqueado o pedido."""
    items = list(order.items.order_by("batch_id"))
    for item in items:
        updated = TicketBatch.objects.filter(
            pk=item.batch_id, reserved__gte=item.quantity
        ).update(reserved=F("reserved") - item.quantity)
        if updated != 1:
            raise OrderRuleError("Não foi possível reconciliar o estoque deste pedido.")
    order.status = status
    order.save(update_fields=["status"])


def expire_pending_orders(*, event=None, buyer=None):
    now = timezone.now()
    due = Order.objects.filter(status=Order.Status.PENDING).filter(
        Q(expires_at__lte=now) | Q(event__starts_at__lte=now)
    )
    if event is not None:
        due = due.filter(event=event)
    if buyer is not None:
        due = due.filter(buyer=buyer)
    ids = list(due.order_by("pk").values_list("pk", flat=True))
    count = 0
    for pk in ids:
        with transaction.atomic():
            order = Order.objects.select_for_update().select_related("event").get(pk=pk)
            if order.status == Order.Status.PENDING and (
                order.expires_at <= now or order.event.starts_at <= now
            ):
                _release_locked_order(order, Order.Status.EXPIRED)
                count += 1
    return count


def create_order(*, buyer, event, quantities):
    if not buyer.is_authenticated or not buyer.is_active:
        raise PermissionDenied
    # Libera reservas vencidas antes de abrir a transação da compra.
    expire_pending_orders(event=event)
    selected = {int(pk): qty for pk, qty in quantities.items() if qty != 0}
    if not selected:
        raise OrderRuleError("Selecione pelo menos um ingresso.")
    if any(type(qty) is not int or qty < 1 for qty in selected.values()):
        raise OrderRuleError("Informe quantidades inteiras e positivas.")
    with transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        now = timezone.now()
        if not locked_event.is_published or locked_event.starts_at <= now:
            raise OrderRuleError("Este evento não está disponível para compra.")
        batches = list(
            TicketBatch.objects.select_for_update()
            .select_related("event")
            .filter(event=locked_event, pk__in=selected)
            .order_by("pk")
        )
        if len(batches) != len(selected):
            raise OrderRuleError("Um dos lotes não pertence a este evento.")
        expires_at = min(
            now + timedelta(minutes=settings.ORDER_RESERVATION_MINUTES),
            locked_event.starts_at,
        )
        total = Decimal("0")
        for batch in batches:
            qty = selected[batch.pk]
            if not batch.on_sale:
                raise OrderRuleError(
                    f'O lote "{batch.name}" está esgotado ou com vendas fechadas.'
                )
            if qty > batch.max_per_order:
                raise OrderRuleError(
                    f'O limite do lote "{batch.name}" é {batch.max_per_order} por pedido.'
                )
            if qty > batch.available:
                raise OrderRuleError(
                    f'O lote "{batch.name}" tem apenas {batch.available} ingressos disponíveis.'
                )
            total += batch.price * qty
            if batch.sales_end:
                expires_at = min(expires_at, batch.sales_end)
        if total > Decimal("999999999999.99"):
            raise OrderRuleError("O valor deste pedido ultrapassa o limite permitido.")
        order = Order.objects.create(
            buyer=buyer, event=locked_event, total=total, expires_at=expires_at
        )
        for batch in batches:
            qty = selected[batch.pk]
            # A condição é verificada pelo banco no mesmo UPDATE do contador.
            updated = TicketBatch.objects.filter(
                pk=batch.pk, quantity__gte=F("sold") + F("reserved") + qty
            ).update(reserved=F("reserved") + qty)
            if updated != 1:
                raise OrderRuleError(
                    "O estoque mudou. Confira as quantidades e tente novamente."
                )
            OrderItem.objects.create(
                order=order, batch=batch, quantity=qty, unit_price=batch.price
            )
        return order


def cancel_order(*, public_id, buyer=None):
    with transaction.atomic():
        order = (
            Order.objects.select_for_update()
            .select_related("event")
            .get(public_id=public_id)
        )
        if buyer is not None and order.buyer_id != buyer.pk:
            raise PermissionDenied
        if order.status != Order.Status.PENDING:
            raise OrderRuleError("Apenas pedidos pendentes podem ser cancelados.")
        if (
            order.expires_at <= timezone.now()
            or order.event.starts_at <= timezone.now()
        ):
            _release_locked_order(order, Order.Status.EXPIRED)
        else:
            _release_locked_order(order, Order.Status.CANCELLED)
        return order


def confirm_order(*, public_id):
    """Confirmação administrativa; não representa uma integração de pagamento."""
    expired = False
    with transaction.atomic():
        order = (
            Order.objects.select_for_update()
            .select_related("event")
            .get(public_id=public_id)
        )
        if order.status == Order.Status.CONFIRMED:
            return order  # Não contabiliza ingressos novamente.
        if order.status != Order.Status.PENDING:
            raise OrderRuleError("Somente pedidos pendentes podem ser confirmados.")
        now = timezone.now()
        if order.expires_at <= now or order.event.starts_at <= now:
            _release_locked_order(order, Order.Status.EXPIRED)
            expired = True
        else:
            for item in order.items.order_by("batch_id"):
                updated = TicketBatch.objects.filter(
                    pk=item.batch_id, reserved__gte=item.quantity
                ).update(
                    reserved=F("reserved") - item.quantity,
                    sold=F("sold") + item.quantity,
                )
                if updated != 1:
                    raise OrderRuleError(
                        "O estoque reservado não corresponde ao pedido."
                    )
            order.status = Order.Status.CONFIRMED
            order.confirmed_at = now
            order.save(update_fields=["status", "confirmed_at"])
    if expired:
        raise OrderRuleError("A reserva expirou e os ingressos foram liberados.")
    return order
