"""Testes de contrato, permissões, regras de estoque e reservas concorrentes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .forms import BatchForm, EventForm
from .models import Event, Favorite, Order, TicketBatch, User
from .services import (
    OrderRuleError,
    cancel_order,
    confirm_order,
    create_order,
    expire_pending_orders,
)

PASSWORD = "Fomo!TesteSeguro2026"
FAST_PASSWORDS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@override_settings(PASSWORD_HASHERS=FAST_PASSWORDS)
class FomoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.producer = User.objects.create_user(
            "produtor", "produtor@example.test", PASSWORD, role="producer"
        )
        cls.other_producer = User.objects.create_user(
            "outro", "outro@example.test", PASSWORD, role="producer"
        )
        cls.buyer = User.objects.create_user(
            "comprador", "comprador@example.test", PASSWORD
        )
        cls.other_buyer = User.objects.create_user(
            "outrocomprador", "outrocomprador@example.test", PASSWORD
        )
        cls.event = Event.objects.create(
            producer=cls.producer,
            title="Festival FOMO",
            description="Música e encontros",
            venue_name="Arena",
            address="Rua Um, 10",
            city="São Paulo",
            starts_at=timezone.now() + timedelta(days=5),
        )
        cls.batch = TicketBatch.objects.create(
            event=cls.event,
            name="Primeiro lote",
            price=Decimal("100.00"),
            quantity=10,
            max_per_order=4,
            sales_start=timezone.now() - timedelta(hours=1),
        )
        cls.event.status = Event.Status.PUBLISHED
        cls.event.published_at = timezone.now()
        cls.event.save(update_fields=["status", "published_at"])

    def reserve(self, quantity=2):
        return create_order(
            buyer=self.buyer, event=self.event, quantities={self.batch.pk: quantity}
        )

    def login_producer(self):
        self.client.force_login(self.producer)

    def event_data(self, **overrides):
        values = {
            "title": "Evento novo",
            "description": "Descrição segura",
            "venue_name": "Palco",
            "address": "Rua Dois",
            "city": "Recife",
            "starts_at": timezone.localtime(
                timezone.now() + timedelta(days=7)
            ).strftime("%Y-%m-%dT%H:%M"),
            "ends_at": "",
        }
        values.update(overrides)
        return values

    def batch_data(self, **overrides):
        values = {
            "name": "Novo lote",
            "price": "25.50",
            "quantity": "20",
            "max_per_order": "3",
            "sales_start": timezone.localtime(
                timezone.now() - timedelta(minutes=10)
            ).strftime("%Y-%m-%dT%H:%M"),
            "sales_end": "",
            "is_active": "on",
        }
        values.update(overrides)
        return values

    def test_contract_urls(self):
        expected = [
            ("catalog", [], "/"),
            ("event_detail", [self.event.slug], f"/eventos/{self.event.slug}/"),
            ("signup", [], "/contas/cadastro/"),
            ("login", [], "/contas/entrar/"),
            ("logout", [], "/contas/sair/"),
            ("dashboard", [], "/painel/"),
            ("event_create", [], "/painel/eventos/novo/"),
            (
                "event_update",
                [self.event.pk],
                f"/painel/eventos/{self.event.pk}/editar/",
            ),
            (
                "event_publish",
                [self.event.pk],
                f"/painel/eventos/{self.event.pk}/publicar/",
            ),
            (
                "event_unpublish",
                [self.event.pk],
                f"/painel/eventos/{self.event.pk}/despublicar/",
            ),
            ("batch_list", [self.event.pk], f"/painel/eventos/{self.event.pk}/lotes/"),
            (
                "batch_create",
                [self.event.pk],
                f"/painel/eventos/{self.event.pk}/lotes/novo/",
            ),
            ("batch_update", [self.batch.pk], f"/painel/lotes/{self.batch.pk}/editar/"),
            ("order_create", [self.event.slug], f"/eventos/{self.event.slug}/comprar/"),
            ("order_list", [], "/meus-pedidos/"),
        ]
        public_id = uuid4()
        expected += [
            ("order_detail", [public_id], f"/pedidos/{public_id}/"),
            ("order_cancel", [public_id], f"/pedidos/{public_id}/cancelar/"),
        ]
        for name, args, path in expected:
            with self.subTest(name=name):
                self.assertEqual(reverse("app:" + name, args=args), path)

    def test_catalog_contract_context_and_anonymous_flag(self):
        r = self.client.get(reverse("app:catalog"))
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "events/catalog.html")
        for key in ["events", "page_obj", "paginator", "is_paginated", "q"]:
            self.assertIn(key, r.context)
        self.assertFalse(r.context["user"].is_producer)
        self.assertContains(r, self.event.title)

    def test_signup_creates_producer_with_hashed_password(self):
        r = self.client.post(
            reverse("app:signup"),
            {
                "username": "novo",
                "email": "NOVO@EXAMPLE.TEST",
                "role": "producer",
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )
        self.assertRedirects(r, reverse("app:dashboard"))
        user = User.objects.get(username="novo")
        self.assertTrue(user.is_producer)
        self.assertEqual(user.email, "novo@example.test")
        self.assertNotEqual(user.password, PASSWORD)
        self.assertTrue(user.check_password(PASSWORD))

    def test_signup_duplicate_email_and_invalid_role(self):
        r = self.client.post(
            reverse("app:signup"),
            {
                "username": "tentativa",
                "email": self.buyer.email.upper(),
                "role": "superuser",
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("email", r.context["form"].errors)
        self.assertIn("role", r.context["form"].errors)
        self.assertFalse(User.objects.filter(username="tentativa").exists())

    def test_login_validates_credentials_and_rejects_external_next(self):
        bad = self.client.post(
            reverse("app:login"),
            {"username": self.buyer.username, "password": "senha errada"},
        )
        self.assertEqual(bad.status_code, 200)
        good = self.client.post(
            reverse("app:login"),
            {
                "username": self.buyer.username,
                "password": PASSWORD,
                "next": "https://outside.example/",
            },
        )
        self.assertRedirects(good, reverse("app:catalog"))

    def test_logout_and_mutations_only_post(self):
        self.login_producer()
        for url in [
            reverse("app:logout"),
            reverse("app:event_publish", args=[self.event.pk]),
            reverse("app:event_unpublish", args=[self.event.pk]),
            reverse("app:order_create", args=[self.event.slug]),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)
        self.assertRedirects(
            self.client.post(reverse("app:logout")), reverse("app:catalog")
        )

    def test_csrf_guards_authenticated_write(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.producer)
        url = reverse("app:event_unpublish", args=[self.event.pk])
        self.assertEqual(csrf.post(url).status_code, 403)
        csrf.get(reverse("app:catalog"))
        r = csrf.post(url, {"csrfmiddlewaretoken": csrf.cookies["csrftoken"].value})
        self.assertEqual(r.status_code, 302)

    def test_visitors_and_buyers_cannot_manage_events(self):
        self.assertEqual(self.client.get(reverse("app:dashboard")).status_code, 302)
        self.client.force_login(self.buyer)
        self.assertEqual(self.client.get(reverse("app:dashboard")).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("app:event_create"), self.event_data()
            ).status_code,
            403,
        )

    def test_other_producer_cannot_access_or_change_resources(self):
        self.client.force_login(self.other_producer)
        for name, args in [
            ("event_update", [self.event.pk]),
            ("batch_list", [self.event.pk]),
            ("batch_update", [self.batch.pk]),
        ]:
            self.assertEqual(
                self.client.get(reverse("app:" + name, args=args)).status_code, 404
            )
        self.assertEqual(
            self.client.post(
                reverse("app:event_unpublish", args=[self.event.pk])
            ).status_code,
            404,
        )

    def test_event_creation_ignores_forged_producer_and_status(self):
        self.login_producer()
        r = self.client.post(
            reverse("app:event_create"),
            self.event_data(producer=self.other_producer.pk, status="published"),
        )
        self.assertEqual(r.status_code, 302)
        event = Event.objects.get(title="Evento novo")
        self.assertEqual(event.producer, self.producer)
        self.assertFalse(event.is_published)
        self.assertTrue(event.slug)

    def test_event_dates_and_local_timezone(self):
        self.login_producer()
        r = self.client.post(reverse("app:event_create"), self.event_data())
        self.assertEqual(r.status_code, 302)
        event = Event.objects.get(title="Evento novo")
        self.assertEqual(
            timezone.localtime(event.starts_at).strftime("%Y-%m-%dT%H:%M"),
            self.event_data()["starts_at"],
        )
        form = EventForm(
            self.event_data(starts_at="2020-01-01T10:00"),
            instance=Event(producer=self.producer),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("starts_at", form.errors)

    def test_publish_requires_active_batch_and_draft_is_private(self):
        draft = Event.objects.create(
            producer=self.producer,
            **{
                "title": "Rascunho",
                "description": "Texto",
                "venue_name": "Lugar",
                "address": "Rua",
                "city": "Cidade",
                "starts_at": timezone.now() + timedelta(days=4),
            },
        )
        self.assertEqual(self.client.get(draft.get_absolute_url()).status_code, 404)
        self.login_producer()
        self.assertEqual(self.client.get(draft.get_absolute_url()).status_code, 200)
        r = self.client.post(reverse("app:event_publish", args=[draft.pk]), follow=True)
        self.assertContains(r, "Crie um lote ativo")
        draft.refresh_from_db()
        self.assertFalse(draft.is_published)
        TicketBatch.objects.create(
            event=draft, name="Inativo", price=0, quantity=10, is_active=False
        )
        self.client.post(reverse("app:event_publish", args=[draft.pk]))
        draft.refresh_from_db()
        self.assertFalse(draft.is_published)
        draft.batches.update(is_active=True)
        self.client.post(reverse("app:event_publish", args=[draft.pk]))
        draft.refresh_from_db()
        self.assertTrue(draft.is_published)

    def test_dashboard_uses_post_forms_for_publication(self):
        self.login_producer()
        r = self.client.get(reverse("app:dashboard"))
        self.assertContains(
            r, f'action="{reverse("app:event_unpublish", args=[self.event.pk])}"'
        )
        self.assertContains(r, 'method="post"')
        self.assertTemplateUsed(r, "dashboard/event_list.html")

    def test_catalog_search_and_pagination(self):
        with override_settings(FOMO_CATALOG_PAGE_SIZE=1):
            for title in ["Festival Dois", "Festival Três"]:
                event = Event.objects.create(
                    producer=self.producer,
                    title=title,
                    description="Texto",
                    venue_name="Arena",
                    address="Rua",
                    city="Recife",
                    starts_at=timezone.now() + timedelta(days=8),
                )
                TicketBatch.objects.create(
                    event=event, name="Lote", price=0, quantity=5
                )
                event.status = "published"
                event.save(update_fields=["status"])
            r = self.client.get(reverse("app:catalog"), {"q": "Recife", "page": "2"})
            self.assertEqual(r.context["paginator"].count, 2)
            self.assertTrue(r.context["is_paginated"])
            self.assertEqual(r.context["page_obj"].number, 2)
            self.assertEqual(
                self.client.get(
                    reverse("app:catalog"), {"page": "invalid"}
                ).status_code,
                200,
            )

    def test_template_escapes_event_content(self):
        self.event.title = "<script>alert(1)</script>"
        self.event.description = "<script>alert(2)</script>"
        self.event.save()
        r = self.client.get(self.event.get_absolute_url())
        self.assertNotContains(r, "<script>alert(1)</script>")
        self.assertContains(r, "&lt;script&gt;")

    def test_batch_create_and_update_contract(self):
        self.login_producer()
        self.assertTemplateUsed(
            self.client.get(reverse("app:batch_create", args=[self.event.pk])),
            "dashboard/batch_form.html",
        )
        r = self.client.post(
            reverse("app:batch_create", args=[self.event.pk]),
            self.batch_data(event=999999),
        )
        self.assertEqual(r.status_code, 302)
        batch = TicketBatch.objects.get(name="Novo lote")
        self.assertEqual(batch.event, self.event)
        r = self.client.post(
            reverse("app:batch_update", args=[batch.pk]),
            self.batch_data(name="Editado"),
        )
        self.assertEqual(r.status_code, 302)
        batch.refresh_from_db()
        self.assertEqual(batch.name, "Editado")

    def test_batch_quantity_cannot_drop_below_reserved(self):
        self.reserve(3)
        self.login_producer()
        r = self.client.post(
            reverse("app:batch_update", args=[self.batch.pk]),
            self.batch_data(name=self.batch.name, quantity="2"),
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("quantity", r.context["form"].errors)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity, 10)

    def test_reservation_totals_and_snapshot_price(self):
        order = self.reserve(2)
        self.batch.refresh_from_db()
        self.assertEqual(order.status, "pending")
        self.assertEqual(order.total, Decimal("200.00"))
        self.assertEqual(self.batch.available, 8)
        self.assertEqual(self.batch.taken, 2)
        self.assertEqual(self.batch.sold, 0)
        self.batch.price = 150
        self.batch.save()
        self.assertEqual(order.items.get().unit_price, Decimal("100.00"))
        self.assertEqual(order.items.get().subtotal, Decimal("200.00"))

    def test_multi_batch_failure_rolls_back_every_reservation(self):
        second = TicketBatch.objects.create(
            event=self.event,
            name="Último",
            price=20,
            quantity=1,
            max_per_order=4,
            sales_start=timezone.now() - timedelta(hours=1),
        )
        with self.assertRaises(OrderRuleError):
            create_order(
                buyer=self.buyer,
                event=self.event,
                quantities={self.batch.pk: 2, second.pk: 2},
            )
        self.batch.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.batch.reserved, 0)
        self.assertEqual(second.reserved, 0)
        self.assertEqual(Order.objects.count(), 0)

    def test_rejects_quantity_limits_empty_and_negative(self):
        for quantity in [0, -1, 5, True]:
            with self.subTest(quantity=quantity):
                with self.assertRaises(OrderRuleError):
                    self.reserve(quantity)
        self.assertEqual(Order.objects.count(), 0)

    def test_rejects_foreign_batch(self):
        other = Event.objects.create(
            producer=self.other_producer,
            title="Outro",
            description="Texto",
            venue_name="Arena",
            address="Rua",
            city="Cidade",
            starts_at=timezone.now() + timedelta(days=4),
        )
        batch = TicketBatch.objects.create(
            event=other, name="Outro lote", price=10, quantity=10
        )
        with self.assertRaises(OrderRuleError):
            create_order(buyer=self.buyer, event=self.event, quantities={batch.pk: 1})

    def test_rejects_closed_inactive_and_past_sales(self):
        for changes in [
            {"is_active": False},
            {"sales_start": timezone.now() + timedelta(hours=1)},
            {"sales_end": timezone.now() - timedelta(minutes=1)},
        ]:
            with self.subTest(changes=changes):
                self.batch.is_active = True
                self.batch.sales_start = timezone.now() - timedelta(hours=2)
                self.batch.sales_end = None
                for name, value in changes.items():
                    setattr(self.batch, name, value)
                self.batch.save()
                with self.assertRaises(OrderRuleError):
                    self.reserve(1)

    def test_order_endpoint_ignores_forged_price_and_status(self):
        self.client.force_login(self.buyer)
        r = self.client.post(
            reverse("app:order_create", args=[self.event.slug]),
            {
                f"qty_{self.batch.pk}": 2,
                "total": "0.01",
                "unit_price": "0",
                "status": "confirmed",
                "buyer": self.other_buyer.pk,
            },
        )
        self.assertEqual(r.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.buyer, self.buyer)
        self.assertEqual(order.status, "pending")
        self.assertEqual(order.total, Decimal("200.00"))

    def test_order_endpoint_displays_rule_errors(self):
        self.client.force_login(self.buyer)
        r = self.client.post(
            reverse("app:order_create", args=[self.event.slug]),
            {f"qty_{self.batch.pk}": 5},
            follow=True,
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Order.objects.count(), 0)
        self.assertContains(r, "alert-danger")

    def test_visitor_returns_to_event_after_login(self):
        r = self.client.post(
            reverse("app:order_create", args=[self.event.slug]),
            {f"qty_{self.batch.pk}": 1},
        )
        self.assertEqual(r.status_code, 302)
        self.assertIn("next=", r.url)
        self.assertNotIn("%2Fcomprar%2F", r.url)

    def test_cancel_releases_stock_once_and_blocks_repeated_cancel(self):
        order = self.reserve()
        cancel_order(public_id=order.public_id, buyer=self.buyer)
        self.batch.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(self.batch.available, 10)
        with self.assertRaises(OrderRuleError):
            cancel_order(public_id=order.public_id, buyer=self.buyer)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.reserved, 0)

    def test_expiration_releases_stock_and_allows_new_order(self):
        order = self.reserve(4)
        Order.objects.filter(pk=order.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(expire_pending_orders(), 1)
        self.assertEqual(expire_pending_orders(), 0)
        self.batch.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status, "expired")
        self.assertEqual(self.batch.available, 10)
        self.assertEqual(self.reserve(4).total, Decimal("400.00"))

    def test_confirmation_moves_reserved_to_sold_and_is_idempotent(self):
        order = self.reserve(2)
        confirm_order(public_id=order.public_id)
        confirm_order(public_id=order.public_id)
        self.batch.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status, "confirmed")
        self.assertEqual(self.batch.reserved, 0)
        self.assertEqual(self.batch.sold, 2)
        self.assertEqual(self.batch.available, 8)
        self.assertIsNotNone(order.confirmed_at)
        with self.assertRaises(OrderRuleError):
            cancel_order(public_id=order.public_id, buyer=self.buyer)

    def test_expired_order_cannot_be_confirmed_and_expiry_is_committed(self):
        order = self.reserve()
        Order.objects.filter(pk=order.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        with self.assertRaises(OrderRuleError):
            confirm_order(public_id=order.public_id)
        order.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual(order.status, "expired")
        self.assertEqual(self.batch.reserved, 0)

    def test_private_orders_are_only_accessible_to_buyer(self):
        order = self.reserve()
        self.client.force_login(self.other_buyer)
        self.assertEqual(self.client.get(order.get_absolute_url()).status_code, 404)
        self.assertEqual(
            self.client.post(
                reverse("app:order_cancel", args=[order.public_id])
            ).status_code,
            404,
        )
        self.assertNotContains(
            self.client.get(reverse("app:order_list")), order.short_code
        )
        self.client.force_login(self.buyer)
        self.assertTemplateUsed(
            self.client.get(order.get_absolute_url()), "orders/order_detail.html"
        )
        self.assertTemplateUsed(
            self.client.get(reverse("app:order_list")), "orders/order_list.html"
        )

    def test_order_cancel_endpoint_get_is_rejected(self):
        order = self.reserve()
        self.client.force_login(self.buyer)
        self.assertEqual(
            self.client.get(
                reverse("app:order_cancel", args=[order.public_id])
            ).status_code,
            405,
        )
        self.assertEqual(
            self.client.post(
                reverse("app:order_cancel", args=[order.public_id])
            ).status_code,
            302,
        )

    def test_pending_reservation_does_not_exceed_sales_deadline(self):
        deadline = timezone.now() + timedelta(minutes=2)
        self.batch.sales_end = deadline
        self.batch.save()
        self.assertLessEqual(self.reserve().expires_at, deadline)

    def test_free_batch_creates_zero_total_order(self):
        self.batch.price = 0
        self.batch.save()
        self.assertEqual(self.reserve(1).total, Decimal("0.00"))

    def test_favorites_are_saved_and_separated_per_user(self):
        self.client.force_login(self.buyer)
        url = reverse("app:favorite_toggle", args=[self.event.slug])
        self.client.post(url)
        self.assertTrue(
            Favorite.objects.filter(user=self.buyer, event=self.event).exists()
        )
        self.assertContains(self.client.get(reverse("app:favorites")), self.event.title)
        self.client.force_login(self.other_buyer)
        self.assertNotContains(
            self.client.get(reverse("app:favorites")), self.event.title
        )
        self.client.force_login(self.buyer)
        self.client.post(url)
        self.assertFalse(
            Favorite.objects.filter(user=self.buyer, event=self.event).exists()
        )

    def test_unpublish_hides_event_and_blocks_purchase(self):
        self.login_producer()
        self.client.post(reverse("app:event_unpublish", args=[self.event.pk]))
        self.client.force_login(self.buyer)
        self.assertEqual(
            self.client.get(self.event.get_absolute_url()).status_code, 404
        )
        self.assertEqual(
            self.client.post(
                reverse("app:order_create", args=[self.event.slug]),
                {f"qty_{self.batch.pk}": 1},
            ).status_code,
            404,
        )

    def test_delete_draft_without_orders_and_preserve_order_history(self):
        self.login_producer()
        self.client.post(reverse("app:event_unpublish", args=[self.event.pk]))
        self.assertEqual(
            self.client.post(
                reverse("app:event_delete", args=[self.event.pk])
            ).status_code,
            302,
        )
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())

    def test_event_with_orders_cannot_be_deleted(self):
        order = self.reserve()
        self.login_producer()
        self.client.post(reverse("app:event_unpublish", args=[self.event.pk]))
        r = self.client.post(
            reverse("app:event_delete", args=[self.event.pk]), follow=True
        )
        self.assertContains(r, "preservar o histórico")
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())

    def test_admin_orders_are_readonly_and_confirmable_by_admin_action(self):
        order = self.reserve()
        staff = User.objects.create_superuser("admin", "admin@example.test", PASSWORD)
        self.client.force_login(staff)
        r = self.client.get(reverse("admin:app_order_change", args=[order.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(admin.site._registry[Order].has_add_permission(r.wsgi_request))
        self.assertFalse(
            admin.site._registry[Order].has_delete_permission(r.wsgi_request)
        )
        r = self.client.post(
            reverse("admin:app_order_changelist"),
            {"action": "confirm_pending", "_selected_action": [order.pk]},
        )
        self.assertEqual(r.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, "confirmed")


@override_settings(PASSWORD_HASHERS=FAST_PASSWORDS)
class ConcurrentStockTests(TransactionTestCase):
    def test_two_buyers_cannot_reserve_the_last_ticket(self):
        producer = User.objects.create_user(
            "p", "p@example.test", PASSWORD, role="producer"
        )
        buyers = [
            User.objects.create_user(f"b{i}", f"b{i}@example.test", PASSWORD)
            for i in range(2)
        ]
        event = Event.objects.create(
            producer=producer,
            title="Último ingresso",
            description="Teste",
            venue_name="Arena",
            address="Rua",
            city="Cidade",
            starts_at=timezone.now() + timedelta(days=3),
        )
        batch = TicketBatch.objects.create(
            event=event,
            name="Único",
            price=10,
            quantity=1,
            max_per_order=1,
            sales_start=timezone.now() - timedelta(hours=1),
        )
        event.status = "published"
        event.save(update_fields=["status"])
        barrier = Barrier(2)

        def reserve_last(buyer):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    create_order(buyer=buyer, event=event, quantities={batch.pk: 1})
                    return "reserved"
                except OrderRuleError:
                    return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve_last, buyers))
        self.assertCountEqual(results, ["reserved", "rejected"])
        batch.refresh_from_db()
        self.assertEqual(batch.reserved, 1)
        self.assertEqual(batch.available, 0)
        self.assertEqual(Order.objects.count(), 1)
