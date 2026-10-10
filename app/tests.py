from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import EventForm
from .models import Event

# =============================================================================
# Funções auxiliares para criar dados nos testes
# =============================================================================

User = get_user_model()
PASSWORD = "senha-forte-123"


def make_user(username="produtor", role=None):
    role = role or User.Role.PRODUCER
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


def make_consumer(username="consumidor"):
    return make_user(username, role=User.Role.CONSUMER)


def make_event(producer, **kwargs):
    defaults = {
        "title": "Festa Junina",
        "description": "Uma festa para toda a família.",
        "venue_name": "Clube Central",
        "address": "Rua das Flores, 100",
        "city": "Rio de Janeiro",
        "starts_at": timezone.now() + timedelta(days=10),
    }
    defaults.update(kwargs)
    return Event.objects.create(producer=producer, **defaults)


def to_input(dt):
    """Converte datetime para o texto que o <input type="datetime-local"> envia."""
    return timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M")


def event_form_data(**overrides):
    data = {
        "title": "Show de Samba",
        "description": "Roda de samba com convidados.",
        "venue_name": "Circo Voador",
        "address": "Rua dos Arcos, s/n",
        "city": "Rio de Janeiro",
        "starts_at": to_input(timezone.now() + timedelta(days=10)),
        "ends_at": "",
    }
    data.update(overrides)
    return data


# =============================================================================
# Contas
# =============================================================================


def signup_data(**overrides):
    data = {
        "username": "ana",
        "email": "ana@example.com",
        "role": "producer",
        "password1": "Senha-Forte-12345",
        "password2": "Senha-Forte-12345",
    }
    data.update(overrides)
    return data


class SignUpTests(TestCase):
    def test_producer_signup_logs_in_and_goes_to_dashboard(self):
        response = self.client.post(reverse("app:signup"), signup_data())
        self.assertRedirects(response, reverse("app:dashboard"))
        user = User.objects.get(username="ana")
        self.assertTrue(user.is_producer)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_consumer_signup_goes_to_catalog(self):
        response = self.client.post(
            reverse("app:signup"), signup_data(username="bia", role="consumer")
        )
        self.assertRedirects(response, reverse("app:catalog"))
        self.assertFalse(User.objects.get(username="bia").is_producer)

    def test_duplicate_username_is_rejected(self):
        make_user("ana")
        response = self.client.post(reverse("app:signup"), signup_data())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username="ana").count(), 1)

    def test_password_is_not_stored_in_plain_text(self):
        self.client.post(reverse("app:signup"), signup_data())
        user = User.objects.get(username="ana")
        self.assertNotEqual(user.password, "Senha-Forte-12345")
        self.assertTrue(user.check_password("Senha-Forte-12345"))


class LoginLogoutTests(TestCase):
    def test_producer_login_goes_to_dashboard(self):
        make_user("prod")
        response = self.client.post(
            reverse("app:login"), {"username": "prod", "password": PASSWORD}
        )
        self.assertRedirects(response, reverse("app:dashboard"))

    def test_consumer_login_goes_to_catalog(self):
        make_consumer("cons")
        response = self.client.post(
            reverse("app:login"), {"username": "cons", "password": PASSWORD}
        )
        self.assertRedirects(response, reverse("app:catalog"))

    def test_wrong_password_does_not_log_in(self):
        make_user("prod")
        response = self.client.post(
            reverse("app:login"), {"username": "prod", "password": "errada"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_requires_post_and_ends_session(self):
        self.client.force_login(make_user("prod"))
        self.assertEqual(self.client.get(reverse("app:logout")).status_code, 405)
        response = self.client.post(reverse("app:logout"))
        self.assertRedirects(response, reverse("app:catalog"))
        self.assertNotIn("_auth_user_id", self.client.session)

# =============================================================================
# Eventos e catálogo
# =============================================================================


class EventModelTests(TestCase):
    def setUp(self):
        self.producer = make_user()

    def test_new_event_is_draft(self):
        event = make_event(self.producer)
        self.assertEqual(event.status, Event.Status.DRAFT)
        self.assertFalse(event.is_published)

    def test_slug_is_generated_and_unique(self):
        first = make_event(self.producer, title="Festa Junina")
        second = make_event(self.producer, title="Festa Junina")
        self.assertEqual(first.slug, "festa-junina")
        self.assertEqual(second.slug, "festa-junina-2")

    def test_slug_does_not_change_when_title_changes(self):
        event = make_event(self.producer, title="Festa Junina")
        event.title = "Outro Nome"
        event.save()
        event.refresh_from_db()
        self.assertEqual(event.slug, "festa-junina")

    def test_publish_sets_status_and_date(self):
        event = make_event(self.producer)
        event.publish()
        event.refresh_from_db()
        self.assertTrue(event.is_published)
        self.assertIsNotNone(event.published_at)

    def test_cannot_publish_event_that_already_ended(self):
        event = make_event(self.producer, starts_at=timezone.now() - timedelta(days=2))
        with self.assertRaises(ValidationError):
            event.publish()
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.DRAFT)

    def test_unpublish_returns_to_draft(self):
        event = make_event(self.producer)
        event.publish()
        event.unpublish()
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.DRAFT)


class EventQuerySetTests(TestCase):
    def setUp(self):
        self.producer = make_user()
        self.now = timezone.now()

    def test_published_excludes_drafts(self):
        make_event(self.producer, title="Rascunho")
        published = make_event(self.producer, title="Publicado", status=Event.Status.PUBLISHED)
        self.assertEqual(list(Event.objects.published()), [published])

    def test_upcoming_excludes_ended_events(self):
        make_event(self.producer, title="Passado", starts_at=self.now - timedelta(days=3))
        future = make_event(self.producer, title="Futuro")
        self.assertEqual(list(Event.objects.upcoming()), [future])

    def test_upcoming_includes_event_in_progress(self):
        in_progress = make_event(
            self.producer,
            starts_at=self.now - timedelta(hours=1),
            ends_at=self.now + timedelta(hours=3),
        )
        self.assertIn(in_progress, Event.objects.upcoming())


class EventFormTests(TestCase):
    def test_valid_form(self):
        self.assertTrue(EventForm(data=event_form_data()).is_valid())

    def test_end_before_start_is_invalid(self):
        start = timezone.now() + timedelta(days=10)
        data = event_form_data(
            starts_at=to_input(start), ends_at=to_input(start - timedelta(hours=1))
        )
        form = EventForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("ends_at", form.errors)

    def test_form_does_not_expose_producer_or_status(self):
        self.assertNotIn("producer", EventForm().fields)
        self.assertNotIn("status", EventForm().fields)
        self.assertNotIn("slug", EventForm().fields)


class PublicCatalogTests(TestCase):
    def setUp(self):
        self.producer = make_user()

    def test_catalog_shows_only_published_upcoming_events(self):
        make_event(self.producer, title="Evento Rascunho")
        make_event(
            self.producer,
            title="Evento Passado",
            status=Event.Status.PUBLISHED,
            starts_at=timezone.now() - timedelta(days=5),
        )
        make_event(self.producer, title="Evento Visivel", status=Event.Status.PUBLISHED)
        response = self.client.get(reverse("app:catalog"))
        self.assertContains(response, "Evento Visivel")
        self.assertNotContains(response, "Evento Rascunho")
        self.assertNotContains(response, "Evento Passado")

    def test_catalog_is_public(self):
        self.assertEqual(self.client.get(reverse("app:catalog")).status_code, 200)

    def test_catalog_search(self):
        make_event(self.producer, title="Show de Rock", city="Niterói", status=Event.Status.PUBLISHED)
        make_event(self.producer, title="Feira de Livros", city="Rio", status=Event.Status.PUBLISHED)
        response = self.client.get(reverse("app:catalog"), {"q": "rock"})
        self.assertContains(response, "Show de Rock")
        self.assertNotContains(response, "Feira de Livros")

    def test_detail_of_published_event(self):
        event = make_event(self.producer, title="Show de Rock", status=Event.Status.PUBLISHED)
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, "Show de Rock")

    def test_detail_of_draft_is_404_even_for_owner(self):
        event = make_event(self.producer, title="Segredo")
        self.assertEqual(self.client.get(event.get_absolute_url()).status_code, 404)
        self.client.force_login(self.producer)
        self.assertEqual(self.client.get(event.get_absolute_url()).status_code, 404)

# =============================================================================
# Painel do produtor
# =============================================================================


class DashboardAccessTests(TestCase):
    def test_anonymous_is_redirected_to_login(self):
        url = reverse("app:dashboard")
        response = self.client.get(url)
        self.assertRedirects(response, f"{reverse('app:login')}?next={url}")

    def test_consumer_gets_403(self):
        self.client.force_login(make_consumer())
        for name in ("app:dashboard", "app:event_create"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_consumer_cannot_create_event_via_post(self):
        self.client.force_login(make_consumer())
        response = self.client.post(reverse("app:event_create"), event_form_data())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Event.objects.count(), 0)

    def test_producer_can_open_dashboard(self):
        self.client.force_login(make_user())
        self.assertEqual(self.client.get(reverse("app:dashboard")).status_code, 200)


class ProducerEventTests(TestCase):
    def setUp(self):
        self.me = make_user("eu")
        self.other = make_user("outro")
        self.client.force_login(self.me)

    def test_list_shows_only_own_events(self):
        make_event(self.me, title="Meu Evento")
        make_event(self.other, title="Evento Alheio")
        response = self.client.get(reverse("app:dashboard"))
        self.assertContains(response, "Meu Evento")
        self.assertNotContains(response, "Evento Alheio")

    def test_create_saves_draft_owned_by_logged_user(self):
        response = self.client.post(reverse("app:event_create"), event_form_data())
        self.assertRedirects(response, reverse("app:dashboard"))
        event = Event.objects.get(title="Show de Samba")
        self.assertEqual(event.producer, self.me)
        self.assertEqual(event.status, Event.Status.DRAFT)

    def test_create_ignores_forged_producer_and_status(self):
        data = event_form_data(producer=self.other.pk, status="published", slug="forjado")
        self.client.post(reverse("app:event_create"), data)
        event = Event.objects.get(title="Show de Samba")
        self.assertEqual(event.producer, self.me)
        self.assertEqual(event.status, Event.Status.DRAFT)
        self.assertNotEqual(event.slug, "forjado")

    def test_invalid_form_does_not_create_event(self):
        response = self.client.post(reverse("app:event_create"), event_form_data(title=""))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Event.objects.count(), 0)

    def test_update_own_event(self):
        event = make_event(self.me, title="Antes")
        url = reverse("app:event_update", args=[event.pk])
        response = self.client.post(url, event_form_data(title="Depois"))
        self.assertRedirects(response, reverse("app:dashboard"))
        event.refresh_from_db()
        self.assertEqual(event.title, "Depois")

    def test_cannot_open_or_update_someone_elses_event(self):
        event = make_event(self.other, title="Do Outro")
        url = reverse("app:event_update", args=[event.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, event_form_data(title="Invadido")).status_code, 404)
        event.refresh_from_db()
        self.assertEqual(event.title, "Do Outro")

    def test_publish_own_event(self):
        event = make_event(self.me)
        self.client.post(reverse("app:event_publish", args=[event.pk]))
        event.refresh_from_db()
        self.assertTrue(event.is_published)

    def test_cannot_publish_someone_elses_event(self):
        event = make_event(self.other)
        response = self.client.post(reverse("app:event_publish", args=[event.pk]))
        self.assertEqual(response.status_code, 404)
        event.refresh_from_db()
        self.assertFalse(event.is_published)

    def test_cannot_unpublish_someone_elses_event(self):
        event = make_event(self.other, status=Event.Status.PUBLISHED)
        response = self.client.post(reverse("app:event_unpublish", args=[event.pk]))
        self.assertEqual(response.status_code, 404)
        event.refresh_from_db()
        self.assertTrue(event.is_published)

    def test_publish_requires_post(self):
        event = make_event(self.me)
        response = self.client.get(reverse("app:event_publish", args=[event.pk]))
        self.assertEqual(response.status_code, 405)

    def test_publishing_past_event_is_refused(self):
        event = make_event(self.me, starts_at=timezone.now() - timedelta(days=2))
        self.client.post(reverse("app:event_publish", args=[event.pk]))
        event.refresh_from_db()
        self.assertFalse(event.is_published)

    def test_unpublish_removes_event_from_catalog(self):
        event = make_event(self.me, title="Sai do Ar", status=Event.Status.PUBLISHED)
        self.assertContains(self.client.get(reverse("app:catalog")), "Sai do Ar")
        self.client.post(reverse("app:event_unpublish", args=[event.pk]))
        self.assertNotContains(self.client.get(reverse("app:catalog")), "Sai do Ar")


class FirstEndToEndFlowTests(TestCase):
    """A primeira meta do projeto: criar -> rascunho -> publicar -> aparecer no catálogo."""

    def test_producer_creates_publishes_and_visitor_sees_event(self):
        producer = make_user("produtora")
        self.client.force_login(producer)

        self.client.post(reverse("app:event_create"), event_form_data(title="Noite de Jazz"))
        event = Event.objects.get(title="Noite de Jazz")

        # Rascunho: não aparece para o público.
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("app:catalog")), "Noite de Jazz")
        self.assertEqual(self.client.get(event.get_absolute_url()).status_code, 404)

        # Publica.
        self.client.force_login(producer)
        self.client.post(reverse("app:event_publish", args=[event.pk]))

        # Visitante anônimo vê no catálogo e abre a página.
        self.client.logout()
        self.assertContains(self.client.get(reverse("app:catalog")), "Noite de Jazz")
        detail = self.client.get(event.get_absolute_url())
        self.assertContains(detail, "Noite de Jazz")
        self.assertContains(detail, "Circo Voador")

