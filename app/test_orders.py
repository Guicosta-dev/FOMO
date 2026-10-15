"""Testes da Etapa 4: lotes, pedidos e controle de estoque."""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Event, Order, TicketBatch
from .services import OrderError, create_order
from .tests import make_consumer, make_event, make_user, to_input

# Templates de mentira (o HTML real é trabalho do frontend).
STUBS = {
    "accounts/login.html": "{{ form.as_p }}",
    "events/event_detail.html": (
        "{{ event.title }} {% for batch in batches %}{{ batch.name }}|{% endfor %}"
    ),
    "orders/order_list.html": "{% for order in orders %}{{ order.public_id }}|{% endfor %}",
    "orders/order_detail.html": "{{ order.public_id }} {{ order.status }}",
    "dashboard/batch_list.html": (
        "{% for batch in batches %}{{ batch.name }}:{{ batch.available }}|{% endfor %}"
    ),
    "dashboard/batch_form.html": "{{ form.as_p }}",
}


@override_settings(
    TEMPLATES=[
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {
                "loaders": [("django.template.loaders.locmem.Loader", STUBS)],
                "context_processors": [
                    "django.template.context_processors.request",
                    "django.contrib.auth.context_processors.auth",
                    "django.contrib.messages.context_processors.messages",
                ],
            },
        }
    ]
)
class OrdersTestCase(TestCase):
    """Base: usa os templates de mentira e cria produtor, evento publicado e compradores."""

    def setUp(self):
        self.producer = make_user("produtor")
        self.buyer = make_consumer("comprador")
        self.buyer2 = make_consumer("comprador2")
        self.event = make_event(self.producer, status=Event.Status.PUBLISHED)


def make_batch(event, **kwargs):
    defaults = {
        "name": "Lote 1",
        "price": Decimal("50.00"),
        "quantity": 10,
        "max_per_order": 4,
    }
    defaults.update(kwargs)
    return TicketBatch.objects.create(event=event, **defaults)


def expire(order):
    """Faz o prazo de reserva do pedido terminar agora há pouco."""
    Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))


# =============================================================================
# Estoque do lote
# =============================================================================

class BatchStockTests(OrdersTestCase):
    def test_new_batch_is_fully_available(self):
        batch = make_batch(self.event, quantity=10)
        self.assertEqual(batch.available, 10)
        self.assertEqual(batch.sold, 0)

    def test_pending_and_confirmed_orders_take_stock(self):
        batch = make_batch(self.event, quantity=10)
        create_order(self.buyer, self.event, {batch.pk: 2}).confirm()
        create_order(self.buyer2, self.event, {batch.pk: 3})  # fica pendente
        self.assertEqual(batch.sold, 2)
        self.assertEqual(batch.taken, 5)
        self.assertEqual(batch.available, 5)

    def test_expired_pending_order_releases_stock(self):
        batch = make_batch(self.event, quantity=3)
        order = create_order(self.buyer, self.event, {batch.pk: 3})
        self.assertEqual(batch.available, 0)
        expire(order)
        self.assertEqual(batch.available, 3)

    def test_cancelled_order_releases_stock(self):
        batch = make_batch(self.event, quantity=3)
        order = create_order(self.buyer, self.event, {batch.pk: 3})
        order.cancel()
        self.assertEqual(batch.available, 3)

    def test_cannot_lower_quantity_below_taken(self):
        batch = make_batch(self.event, quantity=10)
        create_order(self.buyer, self.event, {batch.pk: 4})
        batch.quantity = 3
        with self.assertRaises(ValidationError) as ctx:
            batch.full_clean()
        self.assertIn("quantity", ctx.exception.message_dict)

    def test_sales_end_must_be_after_start(self):
        now = timezone.now()
        batch = TicketBatch(
            event=self.event, name="X", price=1, quantity=5,
            sales_start=now, sales_end=now - timedelta(hours=1),
        )
        with self.assertRaises(ValidationError) as ctx:
            batch.full_clean()
        self.assertIn("sales_end", ctx.exception.message_dict)

    def test_sales_window(self):
        now = timezone.now()
        future = make_batch(self.event, name="Futuro", sales_start=now + timedelta(days=1))
        past = make_batch(self.event, name="Encerrado", sales_end=now - timedelta(days=1))
        paused = make_batch(self.event, name="Pausado", is_active=False)
        open_ = make_batch(self.event, name="Aberto")
        self.assertFalse(future.on_sale)
        self.assertFalse(past.on_sale)
        self.assertFalse(paused.on_sale)
        self.assertTrue(open_.on_sale)


# =============================================================================
# Criação de pedidos (regras de negócio)
# =============================================================================

class CreateOrderTests(OrdersTestCase):
    def test_paid_order_is_pending_with_reservation(self):
        batch = make_batch(self.event, price=Decimal("50.00"))
        order = create_order(self.buyer, self.event, {batch.pk: 2})
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.total, Decimal("100.00"))
        self.assertGreater(order.expires_at, timezone.now())
        item = order.items.get()
        self.assertEqual((item.batch, item.quantity, item.unit_price), (batch, 2, Decimal("50.00")))

    def test_free_order_is_confirmed_immediately(self):
        batch = make_batch(self.event, price=Decimal("0.00"))
        order = create_order(self.buyer, self.event, {batch.pk: 1})
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertIsNone(order.expires_at)
        self.assertIsNotNone(order.confirmed_at)

    def test_order_with_two_batches_sums_total(self):
        a = make_batch(self.event, name="A", price=Decimal("10.00"))
        b = make_batch(self.event, name="B", price=Decimal("25.50"))
        order = create_order(self.buyer, self.event, {a.pk: 2, b.pk: 1})
        self.assertEqual(order.total, Decimal("45.50"))
        self.assertEqual(order.items.count(), 2)

    def test_price_change_does_not_alter_existing_order(self):
        batch = make_batch(self.event, price=Decimal("50.00"))
        order = create_order(self.buyer, self.event, {batch.pk: 1})
        batch.price = Decimal("99.00")
        batch.save()
        order.refresh_from_db()
        self.assertEqual(order.total, Decimal("50.00"))
        self.assertEqual(order.items.get().unit_price, Decimal("50.00"))

    def test_cannot_exceed_capacity(self):
        batch = make_batch(self.event, quantity=3)
        create_order(self.buyer, self.event, {batch.pk: 2})
        with self.assertRaises(OrderError):
            create_order(self.buyer2, self.event, {batch.pk: 2})
        self.assertEqual(Order.objects.count(), 1)
        create_order(self.buyer2, self.event, {batch.pk: 1})  # o último cabe
        self.assertEqual(batch.available, 0)

    def test_sold_out_batch_rejects_any_order(self):
        batch = make_batch(self.event, quantity=1)
        create_order(self.buyer, self.event, {batch.pk: 1})
        with self.assertRaises(OrderError):
            create_order(self.buyer2, self.event, {batch.pk: 1})

    def test_failure_in_second_batch_creates_nothing(self):
        a = make_batch(self.event, name="A", quantity=10)
        b = make_batch(self.event, name="B", quantity=1)
        with self.assertRaises(OrderError):
            create_order(self.buyer, self.event, {a.pk: 2, b.pk: 2})
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(a.available, 10)

    def test_respects_max_per_order(self):
        batch = make_batch(self.event, quantity=50, max_per_order=2)
        with self.assertRaises(OrderError):
            create_order(self.buyer, self.event, {batch.pk: 3})

    def test_rejects_closed_batches(self):
        now = timezone.now()
        batches = [
            make_batch(self.event, name="Pausado", is_active=False),
            make_batch(self.event, name="Futuro", sales_start=now + timedelta(days=1)),
            make_batch(self.event, name="Encerrado", sales_end=now - timedelta(days=1)),
        ]
        for batch in batches:
            with self.assertRaises(OrderError):
                create_order(self.buyer, self.event, {batch.pk: 1})

    def test_rejects_nothing_selected(self):
        batch = make_batch(self.event)
        with self.assertRaises(OrderError):
            create_order(self.buyer, self.event, {batch.pk: 0})
        with self.assertRaises(OrderError):
            create_order(self.buyer, self.event, {})

    def test_rejects_batch_from_another_event(self):
        other_event = make_event(self.producer, title="Outro", status=Event.Status.PUBLISHED)
        foreign = make_batch(other_event)
        with self.assertRaises(OrderError):
            create_order(self.buyer, self.event, {foreign.pk: 1})

    def test_rejects_draft_event(self):
        draft = make_event(self.producer, title="Rascunho")
        batch = make_batch(draft)
        with self.assertRaises(OrderError):
            create_order(self.buyer, draft, {batch.pk: 1})

    def test_rejects_ended_event(self):
        ended = make_event(
            self.producer, title="Passado", status=Event.Status.PUBLISHED,
            starts_at=timezone.now() - timedelta(days=3),
        )
        batch = make_batch(ended)
        with self.assertRaises(OrderError):
            create_order(self.buyer, ended, {batch.pk: 1})

    def test_second_pending_order_for_same_event_is_blocked(self):
        batch = make_batch(self.event)
        create_order(self.buyer, self.event, {batch.pk: 1})
        with self.assertRaises(OrderError):
            create_order(self.buyer, self.event, {batch.pk: 1})
        self.assertEqual(Order.objects.filter(user=self.buyer).count(), 1)

    def test_can_order_again_after_previous_one_expired(self):
        batch = make_batch(self.event)
        first = create_order(self.buyer, self.event, {batch.pk: 1})
        expire(first)
        second = create_order(self.buyer, self.event, {batch.pk: 1})
        first.refresh_from_db()
        self.assertEqual(first.status, Order.Status.EXPIRED)
        self.assertEqual(second.status, Order.Status.PENDING)

    def test_expired_hold_lets_someone_else_buy(self):
        batch = make_batch(self.event, quantity=2)
        first = create_order(self.buyer, self.event, {batch.pk: 2})
        expire(first)
        create_order(self.buyer2, self.event, {batch.pk: 2})
        self.assertEqual(batch.available, 0)

    def test_can_order_again_after_cancelling(self):
        batch = make_batch(self.event)
        create_order(self.buyer, self.event, {batch.pk: 1}).cancel()
        create_order(self.buyer, self.event, {batch.pk: 1})


class OrderStateTests(OrdersTestCase):
    def setUp(self):
        super().setUp()
        self.batch = make_batch(self.event)

    def test_confirm_pending_order(self):
        order = create_order(self.buyer, self.event, {self.batch.pk: 1})
        order.confirm()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertIsNone(order.expires_at)

    def test_confirm_is_idempotent(self):
        order = create_order(self.buyer, self.event, {self.batch.pk: 1})
        order.confirm()
        order.confirm()  # não deve dar erro nem mudar nada
        self.assertEqual(order.status, Order.Status.CONFIRMED)

    def test_cannot_confirm_expired_order(self):
        order = create_order(self.buyer, self.event, {self.batch.pk: 1})
        expire(order)
        order.refresh_from_db()
        with self.assertRaises(ValidationError):
            order.confirm()

    def test_cancel_pending_order(self):
        order = create_order(self.buyer, self.event, {self.batch.pk: 1})
        order.cancel()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertIsNotNone(order.cancelled_at)

    def test_cannot_cancel_confirmed_order(self):
        order = create_order(self.buyer, self.event, {self.batch.pk: 1})
        order.confirm()
        with self.assertRaises(ValidationError):
            order.cancel()


# =============================================================================
# Páginas e permissões do comprador
# =============================================================================

class BuyerViewsTests(OrdersTestCase):
    def setUp(self):
        super().setUp()
        self.batch = make_batch(self.event, quantity=5)
        self.buy_url = reverse("app:order_create", args=[self.event.slug])

    def post_qty(self, qty):
        return self.client.post(self.buy_url, {f"qty_{self.batch.pk}": qty})

    def test_anonymous_is_sent_to_login(self):
        response = self.post_qty("1")
        self.assertRedirects(response, f"{reverse('app:login')}?next={self.buy_url}")
        self.assertEqual(Order.objects.count(), 0)

    def test_buy_creates_order_and_redirects_to_it(self):
        self.client.force_login(self.buyer)
        response = self.post_qty("2")
        order = Order.objects.get()
        self.assertRedirects(response, reverse("app:order_detail", args=[order.public_id]))
        self.assertEqual(order.user, self.buyer)
        self.assertEqual(order.items.get().quantity, 2)

    def test_invalid_quantity_creates_nothing(self):
        self.client.force_login(self.buyer)
        for value in ("abc", "-1", "1.5"):
            response = self.post_qty(value)
            self.assertRedirects(response, self.event.get_absolute_url())
        self.assertEqual(Order.objects.count(), 0)

    def test_over_capacity_creates_nothing(self):
        self.client.force_login(self.buyer)
        response = self.post_qty("9")
        self.assertRedirects(response, self.event.get_absolute_url())
        self.assertEqual(Order.objects.count(), 0)

    def test_forged_batch_of_another_event_is_ignored(self):
        other = make_event(self.producer, title="Outro", status=Event.Status.PUBLISHED)
        foreign = make_batch(other)
        self.client.force_login(self.buyer)
        self.client.post(self.buy_url, {f"qty_{foreign.pk}": "1"})
        self.assertEqual(Order.objects.count(), 0)

    def test_cannot_buy_from_draft_event(self):
        draft = make_event(self.producer, title="Rascunho")
        batch = make_batch(draft)
        self.client.force_login(self.buyer)
        response = self.client.post(
            reverse("app:order_create", args=[draft.slug]), {f"qty_{batch.pk}": "1"}
        )
        self.assertEqual(response.status_code, 404)

    def test_buy_requires_post(self):
        self.client.force_login(self.buyer)
        self.assertEqual(self.client.get(self.buy_url).status_code, 405)

    def test_event_page_lists_only_active_batches(self):
        make_batch(self.event, name="Escondido", is_active=False)
        response = self.client.get(self.event.get_absolute_url())
        self.assertContains(response, self.batch.name)
        self.assertNotContains(response, "Escondido")

    def test_order_list_shows_only_own_orders(self):
        mine = create_order(self.buyer, self.event, {self.batch.pk: 1})
        theirs = create_order(self.buyer2, self.event, {self.batch.pk: 1})
        self.client.force_login(self.buyer)
        response = self.client.get(reverse("app:order_list"))
        self.assertContains(response, str(mine.public_id))
        self.assertNotContains(response, str(theirs.public_id))

    def test_order_list_requires_login(self):
        url = reverse("app:order_list")
        self.assertRedirects(self.client.get(url), f"{reverse('app:login')}?next={url}")

    def test_cannot_open_someone_elses_order(self):
        theirs = create_order(self.buyer2, self.event, {self.batch.pk: 1})
        self.client.force_login(self.buyer)
        response = self.client.get(reverse("app:order_detail", args=[theirs.public_id]))
        self.assertEqual(response.status_code, 404)

    def test_cancel_own_pending_order(self):
        order = create_order(self.buyer, self.event, {self.batch.pk: 1})
        self.client.force_login(self.buyer)
        self.client.post(reverse("app:order_cancel", args=[order.public_id]))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_cannot_cancel_someone_elses_order(self):
        theirs = create_order(self.buyer2, self.event, {self.batch.pk: 1})
        self.client.force_login(self.buyer)
        response = self.client.post(reverse("app:order_cancel", args=[theirs.public_id]))
        self.assertEqual(response.status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Order.Status.PENDING)

    def test_cancel_requires_post(self):
        order = create_order(self.buyer, self.event, {self.batch.pk: 1})
        self.client.force_login(self.buyer)
        response = self.client.get(reverse("app:order_cancel", args=[order.public_id]))
        self.assertEqual(response.status_code, 405)


# =============================================================================
# Painel do produtor: lotes
# =============================================================================

def batch_form_data(**overrides):
    data = {
        "name": "Lote Promocional",
        "price": "40.00",
        "quantity": "100",
        "max_per_order": "4",
        "sales_start": "",
        "sales_end": "",
        "is_active": "on",
    }
    data.update(overrides)
    return data


class ProducerBatchTests(OrdersTestCase):
    def setUp(self):
        super().setUp()
        self.other_producer = make_user("outro-produtor")
        self.client.force_login(self.producer)
        self.list_url = reverse("app:batch_list", args=[self.event.pk])
        self.create_url = reverse("app:batch_create", args=[self.event.pk])

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        self.assertRedirects(
            self.client.get(self.list_url), f"{reverse('app:login')}?next={self.list_url}"
        )

    def test_consumer_gets_403(self):
        self.client.force_login(self.buyer)
        self.assertEqual(self.client.get(self.list_url).status_code, 403)
        self.assertEqual(self.client.post(self.create_url, batch_form_data()).status_code, 403)
        self.assertEqual(TicketBatch.objects.count(), 0)

    def test_producer_creates_batch_for_own_event(self):
        response = self.client.post(self.create_url, batch_form_data())
        self.assertRedirects(response, self.list_url)
        batch = TicketBatch.objects.get()
        self.assertEqual(batch.event, self.event)
        self.assertEqual(batch.price, Decimal("40.00"))
        self.assertEqual(batch.quantity, 100)

    def test_cannot_manage_batches_of_someone_elses_event(self):
        foreign_event = make_event(self.other_producer, title="Alheio")
        list_url = reverse("app:batch_list", args=[foreign_event.pk])
        create_url = reverse("app:batch_create", args=[foreign_event.pk])
        self.assertEqual(self.client.get(list_url).status_code, 404)
        self.assertEqual(self.client.post(create_url, batch_form_data()).status_code, 404)
        self.assertEqual(TicketBatch.objects.count(), 0)

    def test_invalid_batch_is_rejected(self):
        for bad in (
            {"quantity": "0"},
            {"price": "-5"},
            {"max_per_order": "0"},
            {
                "sales_start": to_input(timezone.now() + timedelta(days=2)),
                "sales_end": to_input(timezone.now() + timedelta(days=1)),
            },
        ):
            response = self.client.post(self.create_url, batch_form_data(**bad))
            self.assertEqual(response.status_code, 200, bad)
        self.assertEqual(TicketBatch.objects.count(), 0)

    def test_update_own_batch(self):
        batch = make_batch(self.event, name="Antes")
        url = reverse("app:batch_update", args=[batch.pk])
        response = self.client.post(url, batch_form_data(name="Depois"))
        self.assertRedirects(response, self.list_url)
        batch.refresh_from_db()
        self.assertEqual(batch.name, "Depois")

    def test_cannot_update_someone_elses_batch(self):
        foreign_event = make_event(self.other_producer, title="Alheio")
        batch = make_batch(foreign_event, name="Do Outro")
        url = reverse("app:batch_update", args=[batch.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, batch_form_data(name="Invadido")).status_code, 404)
        batch.refresh_from_db()
        self.assertEqual(batch.name, "Do Outro")

    def test_cannot_lower_quantity_below_sold(self):
        batch = make_batch(self.event, quantity=10)
        create_order(self.buyer, self.event, {batch.pk: 4})
        url = reverse("app:batch_update", args=[batch.pk])
        response = self.client.post(url, batch_form_data(quantity="3"))
        self.assertEqual(response.status_code, 200)  # formulário recusado
        batch.refresh_from_db()
        self.assertEqual(batch.quantity, 10)

    def test_forged_event_field_is_ignored(self):
        other_event = make_event(self.other_producer, title="Alheio")
        self.client.post(self.create_url, batch_form_data(event=other_event.pk))
        self.assertEqual(TicketBatch.objects.get().event, self.event)

    def test_list_shows_available_count(self):
        batch = make_batch(self.event, name="Pista", quantity=10)
        create_order(self.buyer, self.event, {batch.pk: 3})
        self.assertContains(self.client.get(self.list_url), "Pista:7")


class FullPurchaseFlowTests(OrdersTestCase):
    """Produtor cria lote -> comprador compra -> estoque cai -> pedido aparece no histórico."""

    def test_full_flow(self):
        self.client.force_login(self.producer)
        self.client.post(
            reverse("app:batch_create", args=[self.event.pk]),
            batch_form_data(name="Pista", quantity="2", price="30.00"),
        )
        batch = TicketBatch.objects.get()

        self.client.force_login(self.buyer)
        buy_url = reverse("app:order_create", args=[self.event.slug])
        self.client.post(buy_url, {f"qty_{batch.pk}": "2"})
        order = Order.objects.get()
        self.assertEqual(order.total, Decimal("60.00"))
        self.assertEqual(batch.available, 0)

        # Esgotado para o segundo comprador.
        self.client.force_login(self.buyer2)
        self.client.post(buy_url, {f"qty_{batch.pk}": "1"})
        self.assertEqual(Order.objects.count(), 1)

        # Pedido no histórico do primeiro comprador.
        self.client.force_login(self.buyer)
        self.assertContains(self.client.get(reverse("app:order_list")), str(order.public_id))

        # Cancelando, os ingressos voltam a ficar disponíveis.
        self.client.post(reverse("app:order_cancel", args=[order.public_id]))
        self.assertEqual(batch.available, 2)
