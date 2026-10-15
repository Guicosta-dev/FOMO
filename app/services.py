"""Regras de negócio de pedidos (fora das views para serem fáceis de testar)."""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Event, Order, OrderItem, TicketBatch


class OrderError(Exception):
    """Regra de negócio violada ao criar um pedido. A mensagem pode ser mostrada ao usuário."""


def hold_minutes():
    """Minutos que um pedido pendente reserva os ingressos (padrão: 15)."""
    return getattr(settings, "ORDER_HOLD_MINUTES", 15)


@transaction.atomic
def create_order(user, event, quantities):
    """Cria um pedido de forma segura contra venda acima da capacidade.

    `quantities` é um dicionário {id_do_lote: quantidade}.

    Tudo acontece dentro de uma transação. As linhas dos lotes são travadas
    (select_for_update) ANTES de conferir a disponibilidade: se duas pessoas
    comprarem o último ingresso ao mesmo tempo, a segunda espera a primeira
    terminar e só então confere o estoque.
    (No SQLite o travamento é ignorado, mas o banco já serializa as escritas;
    a proteção completa vale no PostgreSQL.)
    """
    quantities = {int(pk): int(qty) for pk, qty in quantities.items() if qty and int(qty) > 0}
    if not quantities:
        raise OrderError("Selecione ao menos um ingresso.")

    now = timezone.now()

    event = Event.objects.get(pk=event.pk)  # estado atual do evento
    if not event.is_published or event.has_ended:
        raise OrderError("Este evento não está disponível para compra.")

    batches = list(
        TicketBatch.objects.select_for_update()
        .filter(event=event, pk__in=list(quantities))
        .order_by("pk")
    )
    if len(batches) != len(quantities):
        raise OrderError("Um dos lotes escolhidos não existe para este evento.")

    # Só depois de travar os lotes: evita dois pedidos pendentes por clique duplo.
    Order.objects.expire_stale()
    if Order.objects.filter(
        user=user, event=event, status=Order.Status.PENDING
    ).exists():
        raise OrderError(
            "Você já tem um pedido pendente para este evento. "
            "Conclua ou cancele esse pedido antes de fazer outro."
        )

    total = Decimal("0.00")
    lines = []
    for batch in batches:
        qty = quantities[batch.pk]
        if not batch.is_open_now(now):
            raise OrderError(f"As vendas do lote “{batch.name}” não estão abertas.")
        if qty > batch.max_per_order:
            raise OrderError(
                f"O lote “{batch.name}” permite no máximo {batch.max_per_order} "
                "ingresso(s) por pedido."
            )
        available = batch.available
        if qty > available:
            raise OrderError(
                f"O lote “{batch.name}” tem apenas {available} ingresso(s) disponível(is)."
            )
        total += batch.price * qty
        lines.append((batch, qty))

    is_free = total == 0
    order = Order.objects.create(
        user=user,
        event=event,
        total=total,
        # Pedido gratuito já nasce confirmado; pago fica pendente até haver pagamento (Etapa 5).
        status=Order.Status.CONFIRMED if is_free else Order.Status.PENDING,
        expires_at=None if is_free else now + timedelta(minutes=hold_minutes()),
        confirmed_at=now if is_free else None,
    )
    OrderItem.objects.bulk_create(
        [
            OrderItem(order=order, batch=batch, quantity=qty, unit_price=batch.price)
            for batch, qty in lines
        ]
    )
    return order
