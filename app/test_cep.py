"""Testes da busca por CEP e proximidade. Nenhum teste chama a internet de verdade."""
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import cep as cep_module
from .cep import (
    CepInfo,
    CepServiceError,
    LocationStatus,
    apply_cep_location,
    lookup_cep,
    normalize_cep,
)
from .forms import EventForm
from .geo import bounding_box, haversine_km
from .models import Event
from .tests import event_form_data, make_event, make_user

STUBS = {
    "dashboard/event_form.html": "{{ form.as_p }}",
    "dashboard/event_list.html": "{% for event in events %}{{ event.title }}|{% endfor %}",
    "events/catalog.html": (
        "{% for event in events %}{{ event.title }}@{{ event.distance_km }}|{% endfor %}"
        "mode={{ location_mode|default:'none' }};error={{ cep_error|default:'none' }}"
    ),
}

TEMPLATE_OVERRIDE = override_settings(
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

# Pontos reais do Rio de Janeiro e de São Paulo, para os testes de distância.
CENTRO = (-22.9068, -43.1729)       # Centro do Rio (CEP do visitante nos testes)
BOTAFOGO = (-22.9519, -43.1807)     # ~5 km do Centro
NITEROI = (-22.8833, -43.1036)      # ~7,5 km do Centro
SAO_PAULO = (-23.5505, -46.6333)    # ~360 km do Centro


def api_response(city="Rio de Janeiro", state="RJ", coords=CENTRO):
    """Resposta no formato da BrasilAPI v2. coords=None simula 'sem coordenadas'."""
    data = {
        "cep": "20040020",
        "state": state,
        "city": city,
        "neighborhood": "Centro",
        "street": "Rua Sete de Setembro",
        "service": "viacep",
        "location": {"type": "Point", "coordinates": {}},
    }
    if coords is not None:
        data["location"]["coordinates"] = {
            "latitude": str(coords[0]),
            "longitude": str(coords[1]),
        }
    return data


class CepTestCase(TestCase):
    def setUp(self):
        cache.clear()  # o cache de CEP dura mais que um teste


# =============================================================================
# Funções puras
# =============================================================================

class NormalizeCepTests(SimpleTestCase):
    def test_valid_formats(self):
        for value in ("20040-020", "20040020", " 20040-020 ", "20040.020", "20040 020"):
            self.assertEqual(normalize_cep(value), "20040020", value)

    def test_invalid_values(self):
        for value in ("", "123", "2004002", "200400200", "abcdefgh", "20040-02a",
                      "20040--020", "2004-0020", None, 20040020):
            self.assertIsNone(normalize_cep(value), value)


class GeoTests(SimpleTestCase):
    def test_same_point_is_zero(self):
        self.assertAlmostEqual(haversine_km(*CENTRO, *CENTRO), 0, places=6)

    def test_rio_to_sao_paulo_is_about_360_km(self):
        self.assertAlmostEqual(haversine_km(*CENTRO, *SAO_PAULO), 360, delta=15)

    def test_distance_is_symmetric(self):
        self.assertAlmostEqual(
            haversine_km(*CENTRO, *NITEROI), haversine_km(*NITEROI, *CENTRO), places=9
        )

    def test_bounding_box_surrounds_the_point(self):
        min_lat, max_lat, min_lon, max_lon = bounding_box(*CENTRO, 10)
        self.assertLess(min_lat, CENTRO[0])
        self.assertGreater(max_lat, CENTRO[0])
        self.assertLess(min_lon, CENTRO[1])
        self.assertGreater(max_lon, CENTRO[1])

    def test_bounding_box_contains_everything_inside_the_radius(self):
        # Niterói está a ~7,5 km do Centro, então cabe numa caixa de 10 km.
        min_lat, max_lat, min_lon, max_lon = bounding_box(*CENTRO, 10)
        self.assertTrue(min_lat <= NITEROI[0] <= max_lat)
        self.assertTrue(min_lon <= NITEROI[1] <= max_lon)


# =============================================================================
# Consulta de CEP (HTTP simulado)
# =============================================================================

def fake_response(payload):
    """Simula o objeto que urlopen devolve dentro de um 'with'."""
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    context = MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = False  # não engolir exceções dentro do "with"
    return context


class FetchCepTests(SimpleTestCase):
    @patch.object(cep_module, "urlopen")
    def test_success_returns_json(self, urlopen):
        urlopen.return_value = fake_response(api_response())
        data = cep_module._fetch_cep("20040020")
        self.assertEqual(data["city"], "Rio de Janeiro")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://brasilapi.com.br/api/cep/v2/20040020")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], cep_module.TIMEOUT_SECONDS)

    @patch.object(cep_module, "urlopen")
    def test_404_means_cep_not_found(self, urlopen):
        urlopen.side_effect = HTTPError("u", 404, "Not Found", {}, None)
        self.assertIsNone(cep_module._fetch_cep("20040020"))

    @patch.object(cep_module, "urlopen")
    def test_server_error_raises(self, urlopen):
        urlopen.side_effect = HTTPError("u", 500, "Server Error", {}, None)
        with self.assertRaises(CepServiceError):
            cep_module._fetch_cep("20040020")

    @patch.object(cep_module, "urlopen")
    def test_network_failure_raises(self, urlopen):
        urlopen.side_effect = URLError("sem rede")
        with self.assertRaises(CepServiceError):
            cep_module._fetch_cep("20040020")

    @patch.object(cep_module, "urlopen")
    def test_timeout_raises(self, urlopen):
        urlopen.side_effect = TimeoutError()
        with self.assertRaises(CepServiceError):
            cep_module._fetch_cep("20040020")

    @patch.object(cep_module, "urlopen")
    def test_invalid_json_raises(self, urlopen):
        urlopen.return_value = fake_response(None)
        urlopen.return_value.__enter__.return_value.read.return_value = b"<html>erro</html>"
        with self.assertRaises(CepServiceError):
            cep_module._fetch_cep("20040020")


class LookupCepTests(CepTestCase):
    @patch.object(cep_module, "_fetch_cep")
    def test_parses_address_and_coordinates(self, fetch):
        fetch.return_value = api_response()
        info = lookup_cep("20040020")
        self.assertEqual((info.city, info.state, info.neighborhood), ("Rio de Janeiro", "RJ", "Centro"))
        self.assertAlmostEqual(info.latitude, CENTRO[0])
        self.assertAlmostEqual(info.longitude, CENTRO[1])
        self.assertTrue(info.has_coordinates)

    @patch.object(cep_module, "_fetch_cep")
    def test_missing_coordinates_are_not_an_error(self, fetch):
        fetch.return_value = api_response(coords=None)
        info = lookup_cep("20040020")
        self.assertFalse(info.has_coordinates)
        self.assertEqual(info.city, "Rio de Janeiro")

    @patch.object(cep_module, "_fetch_cep")
    def test_invalid_coordinates_are_discarded(self, fetch):
        for bad in (("abc", "def"), ("999", "-43"), ("-22", "999"), (None, None), ("-22.9", "")):
            cache.clear()
            data = api_response(coords=None)
            data["location"]["coordinates"] = {"latitude": bad[0], "longitude": bad[1]}
            fetch.return_value = data
            self.assertFalse(lookup_cep("20040020").has_coordinates, bad)

    @patch.object(cep_module, "_fetch_cep")
    def test_unknown_cep_returns_none(self, fetch):
        fetch.return_value = None
        self.assertIsNone(lookup_cep("00000000"))

    @patch.object(cep_module, "_fetch_cep")
    def test_unexpected_response_raises(self, fetch):
        for bad in ([], "texto", {"cep": "20040020"}):  # sem cidade
            fetch.return_value = bad
            with self.assertRaises(CepServiceError):
                lookup_cep("20040020")

    @patch.object(cep_module, "_fetch_cep")
    def test_result_is_cached(self, fetch):
        fetch.return_value = api_response()
        lookup_cep("20040020")
        lookup_cep("20040020")
        self.assertEqual(fetch.call_count, 1)

    @patch.object(cep_module, "_fetch_cep")
    def test_not_found_is_cached(self, fetch):
        fetch.return_value = None
        lookup_cep("00000000")
        lookup_cep("00000000")
        self.assertEqual(fetch.call_count, 1)

    @patch.object(cep_module, "_fetch_cep")
    def test_failures_are_not_cached(self, fetch):
        fetch.side_effect = CepServiceError("fora do ar")
        for _ in range(2):
            with self.assertRaises(CepServiceError):
                lookup_cep("20040020")
        self.assertEqual(fetch.call_count, 2)


class ApplyCepLocationTests(CepTestCase):
    def new_event(self, **kwargs):
        defaults = {"title": "X", "city": "Rio", "cep": "20040020",
                    "state": "ZZ", "neighborhood": "Velho", "latitude": 1.0, "longitude": 2.0}
        defaults.update(kwargs)
        return Event(**defaults)

    @patch.object(cep_module, "_fetch_cep")
    def test_fills_city_state_neighborhood_and_coordinates(self, fetch):
        fetch.return_value = api_response()
        event = self.new_event(city="Rio")
        self.assertEqual(apply_cep_location(event), LocationStatus.OK)
        self.assertEqual((event.city, event.state, event.neighborhood),
                         ("Rio de Janeiro", "RJ", "Centro"))
        self.assertAlmostEqual(event.latitude, CENTRO[0])

    @patch.object(cep_module, "_fetch_cep")
    def test_no_cep_clears_computed_fields_and_never_calls_service(self, fetch):
        event = self.new_event(cep="")
        self.assertEqual(apply_cep_location(event), LocationStatus.NO_CEP)
        self.assertEqual((event.state, event.neighborhood), ("", ""))
        self.assertIsNone(event.latitude)
        fetch.assert_not_called()

    @patch.object(cep_module, "_fetch_cep")
    def test_unknown_cep_clears_old_coordinates(self, fetch):
        fetch.return_value = None
        event = self.new_event()
        self.assertEqual(apply_cep_location(event), LocationStatus.NOT_FOUND)
        self.assertIsNone(event.latitude)
        self.assertEqual(event.state, "")

    @patch.object(cep_module, "_fetch_cep")
    def test_service_down_clears_old_coordinates(self, fetch):
        fetch.side_effect = CepServiceError("fora do ar")
        event = self.new_event()
        self.assertEqual(apply_cep_location(event), LocationStatus.UNAVAILABLE)
        self.assertIsNone(event.latitude)

    @patch.object(cep_module, "_fetch_cep")
    def test_cep_without_coordinates_still_fills_city(self, fetch):
        fetch.return_value = api_response(coords=None)
        event = self.new_event()
        self.assertEqual(apply_cep_location(event), LocationStatus.NO_COORDINATES)
        self.assertEqual(event.state, "RJ")
        self.assertIsNone(event.latitude)


# =============================================================================
# Modelo e formulário
# =============================================================================

class EventCepFieldTests(CepTestCase):
    def test_form_normalizes_cep(self):
        form = EventForm(data=event_form_data(cep="20040-020"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["cep"], "20040020")

    def test_form_cep_is_optional(self):
        form = EventForm(data=event_form_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["cep"], "")

    def test_form_rejects_invalid_cep(self):
        for bad in ("123", "abcde-fgh", "20040-02"):
            form = EventForm(data=event_form_data(cep=bad))
            self.assertFalse(form.is_valid(), bad)
            self.assertIn("cep", form.errors)

    def test_form_does_not_expose_computed_fields(self):
        fields = EventForm().fields
        for name in ("state", "neighborhood", "latitude", "longitude"):
            self.assertNotIn(name, fields)

    def test_model_validates_cep_digits(self):
        producer = make_user()
        event = Event(
            producer=producer, title="T", description="d", venue_name="v", address="a",
            city="c", cep="123", starts_at=timezone.now() + timedelta(days=1),
        )
        with self.assertRaises(ValidationError) as ctx:
            event.full_clean()
        self.assertIn("cep", ctx.exception.message_dict)

    def test_has_location(self):
        self.assertTrue(Event(latitude=1.0, longitude=2.0).has_location)
        self.assertFalse(Event(latitude=1.0).has_location)
        self.assertFalse(Event().has_location)


class NearbyQuerySetTests(CepTestCase):
    def setUp(self):
        super().setUp()
        self.producer = make_user()

    def event_at(self, title, coords, **kwargs):
        lat, lon = coords if coords else (None, None)
        return make_event(
            self.producer, title=title, status=Event.Status.PUBLISHED,
            latitude=lat, longitude=lon, **kwargs,
        )

    def test_returns_events_inside_radius_sorted_by_distance(self):
        far = self.event_at("Longe", SAO_PAULO)
        second = self.event_at("Niteroi", NITEROI)
        first = self.event_at("Botafogo", BOTAFOGO)
        self.event_at("Sem coordenadas", None)

        result = Event.objects.nearby(*CENTRO, 10)

        self.assertEqual(result, [first, second])
        self.assertNotIn(far, result)
        self.assertLess(result[0].distance_km, result[1].distance_km)

    def test_smaller_radius_excludes_farther_events(self):
        near = self.event_at("Botafogo", BOTAFOGO)
        self.event_at("Niteroi", NITEROI)
        self.assertEqual(Event.objects.nearby(*CENTRO, 6), [near])

    def test_distance_is_attached_to_each_event(self):
        self.event_at("Botafogo", BOTAFOGO)
        (event,) = Event.objects.nearby(*CENTRO, 10)
        self.assertAlmostEqual(event.distance_km, haversine_km(*CENTRO, *BOTAFOGO), delta=0.1)

    def test_chains_after_published_and_upcoming(self):
        self.event_at("Publicado", BOTAFOGO)
        make_event(self.producer, title="Rascunho", latitude=BOTAFOGO[0], longitude=BOTAFOGO[1])
        make_event(
            self.producer, title="Passado", status=Event.Status.PUBLISHED,
            latitude=BOTAFOGO[0], longitude=BOTAFOGO[1],
            starts_at=timezone.now() - timedelta(days=5),
        )
        result = Event.objects.published().upcoming().nearby(*CENTRO, 10)
        self.assertEqual([event.title for event in result], ["Publicado"])


# =============================================================================
# Painel do produtor: o CEP do evento
# =============================================================================

@TEMPLATE_OVERRIDE
class ProducerCepTests(CepTestCase):
    def setUp(self):
        super().setUp()
        self.producer = make_user()
        self.client.force_login(self.producer)
        self.create_url = reverse("app:event_create")

    @patch.object(cep_module, "_fetch_cep")
    def test_create_with_cep_fills_location(self, fetch):
        fetch.return_value = api_response()
        response = self.client.post(
            self.create_url, event_form_data(cep="20040-020", city="Rio")
        )
        self.assertRedirects(response, reverse("app:dashboard"))
        event = Event.objects.get()
        self.assertEqual(event.cep, "20040020")
        self.assertEqual((event.city, event.state, event.neighborhood),
                         ("Rio de Janeiro", "RJ", "Centro"))
        self.assertTrue(event.has_location)

    @patch.object(cep_module, "_fetch_cep")
    def test_create_without_cep_never_calls_service(self, fetch):
        self.client.post(self.create_url, event_form_data())
        fetch.assert_not_called()
        self.assertFalse(Event.objects.get().has_location)

    @patch.object(cep_module, "_fetch_cep")
    def test_invalid_cep_rejects_the_form(self, fetch):
        response = self.client.post(self.create_url, event_form_data(cep="123"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Event.objects.count(), 0)
        fetch.assert_not_called()

    @patch.object(cep_module, "_fetch_cep")
    def test_event_is_saved_even_if_cep_service_is_down(self, fetch):
        fetch.side_effect = CepServiceError("fora do ar")
        response = self.client.post(self.create_url, event_form_data(cep="20040-020"))
        self.assertRedirects(response, reverse("app:dashboard"))
        event = Event.objects.get()
        self.assertEqual(event.cep, "20040020")
        self.assertFalse(event.has_location)

    @patch.object(cep_module, "_fetch_cep")
    def test_unknown_cep_still_saves_the_event(self, fetch):
        fetch.return_value = None
        self.client.post(self.create_url, event_form_data(cep="00000-000"))
        self.assertFalse(Event.objects.get().has_location)

    @patch.object(cep_module, "_fetch_cep")
    def test_update_with_same_cep_does_not_call_service_again(self, fetch):
        event = make_event(
            self.producer, cep="20040020", latitude=CENTRO[0], longitude=CENTRO[1], state="RJ"
        )
        url = reverse("app:event_update", args=[event.pk])
        self.client.post(url, event_form_data(title="Novo titulo", cep="20040-020"))
        fetch.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.title, "Novo titulo")
        self.assertTrue(event.has_location)

    @patch.object(cep_module, "_fetch_cep")
    def test_update_with_new_cep_looks_it_up_again(self, fetch):
        fetch.return_value = api_response(city="Niterói", coords=NITEROI)
        event = make_event(
            self.producer, cep="20040020", latitude=CENTRO[0], longitude=CENTRO[1]
        )
        url = reverse("app:event_update", args=[event.pk])
        self.client.post(url, event_form_data(cep="24020-005"))
        event.refresh_from_db()
        self.assertEqual(event.cep, "24020005")
        self.assertEqual(event.city, "Niterói")
        self.assertAlmostEqual(event.latitude, NITEROI[0])

    @patch.object(cep_module, "_fetch_cep")
    def test_update_retries_when_there_are_no_coordinates_yet(self, fetch):
        fetch.return_value = api_response()
        event = make_event(self.producer, cep="20040020")  # CEP salvo, mas sem coordenadas
        url = reverse("app:event_update", args=[event.pk])
        self.client.post(url, event_form_data(cep="20040-020"))
        event.refresh_from_db()
        self.assertTrue(event.has_location)

    @patch.object(cep_module, "_fetch_cep")
    def test_clearing_the_cep_clears_the_location(self, fetch):
        event = make_event(
            self.producer, cep="20040020", latitude=CENTRO[0], longitude=CENTRO[1], state="RJ"
        )
        url = reverse("app:event_update", args=[event.pk])
        self.client.post(url, event_form_data(cep=""))
        event.refresh_from_db()
        self.assertEqual(event.cep, "")
        self.assertFalse(event.has_location)
        self.assertEqual(event.state, "")
        fetch.assert_not_called()

    @patch.object(cep_module, "_fetch_cep")
    def test_forged_coordinates_in_the_form_are_ignored(self, fetch):
        fetch.return_value = api_response()
        data = event_form_data(cep="20040-020", latitude="10", longitude="10", state="ZZ")
        self.client.post(self.create_url, data)
        event = Event.objects.get()
        self.assertAlmostEqual(event.latitude, CENTRO[0])  # veio do serviço, não do formulário
        self.assertEqual(event.state, "RJ")


# =============================================================================
# Catálogo: filtro por CEP e raio
# =============================================================================

@TEMPLATE_OVERRIDE
class CatalogByCepTests(CepTestCase):
    def setUp(self):
        super().setUp()
        self.producer = make_user()
        self.url = reverse("app:catalog")

    def event_at(self, title, coords, **kwargs):
        lat, lon = coords if coords else (None, None)
        return make_event(
            self.producer, title=title, status=Event.Status.PUBLISHED,
            latitude=lat, longitude=lon, **kwargs,
        )

    def titles_in(self, response):
        return [event.title for event in response.context["events"]]

    def test_no_cep_shows_the_whole_catalog_and_never_calls_service(self):
        self.event_at("Um", BOTAFOGO)
        self.event_at("Dois", SAO_PAULO)
        with patch.object(cep_module, "_fetch_cep") as fetch:
            response = self.client.get(self.url)
        fetch.assert_not_called()
        self.assertEqual(sorted(self.titles_in(response)), ["Dois", "Um"])
        self.assertContains(response, "mode=none")

    @patch.object(cep_module, "_fetch_cep")
    def test_radius_filter_keeps_only_close_events_sorted_by_distance(self, fetch):
        fetch.return_value = api_response()
        self.event_at("Niteroi", NITEROI)
        self.event_at("Botafogo", BOTAFOGO)
        self.event_at("Longe", SAO_PAULO)
        self.event_at("Sem coordenadas", None)

        response = self.client.get(self.url, {"cep": "20040-020", "raio": "10"})

        self.assertEqual(self.titles_in(response), ["Botafogo", "Niteroi"])
        self.assertContains(response, "mode=radius")
        self.assertEqual(response.context["radius"], 10)
        self.assertEqual(response.context["cep"], "20040-020")
        self.assertEqual(response.context["cep_info"].city, "Rio de Janeiro")

    @patch.object(cep_module, "_fetch_cep")
    def test_distance_is_exposed_on_each_event(self, fetch):
        fetch.return_value = api_response()
        self.event_at("Botafogo", BOTAFOGO)
        response = self.client.get(self.url, {"cep": "20040020", "raio": "10"})
        (event,) = response.context["events"]
        self.assertAlmostEqual(event.distance_km, 5.0, delta=0.6)

    @patch.object(cep_module, "_fetch_cep")
    def test_invalid_radius_falls_back_to_default(self, fetch):
        fetch.return_value = api_response()
        for bad in ("abc", "7", "-5", "100000", ""):
            response = self.client.get(self.url, {"cep": "20040020", "raio": bad})
            self.assertEqual(response.context["radius"], 25, bad)

    @patch.object(cep_module, "_fetch_cep")
    def test_radius_defaults_when_missing(self, fetch):
        fetch.return_value = api_response()
        response = self.client.get(self.url, {"cep": "20040020"})
        self.assertEqual(response.context["radius"], 25)

    @patch.object(cep_module, "_fetch_cep")
    def test_radius_search_ignores_drafts_and_ended_events(self, fetch):
        fetch.return_value = api_response()
        self.event_at("Visivel", BOTAFOGO)
        make_event(self.producer, title="Rascunho", latitude=BOTAFOGO[0], longitude=BOTAFOGO[1])
        self.event_at("Passado", BOTAFOGO, starts_at=timezone.now() - timedelta(days=3))
        response = self.client.get(self.url, {"cep": "20040020", "raio": "10"})
        self.assertEqual(self.titles_in(response), ["Visivel"])

    @patch.object(cep_module, "_fetch_cep")
    def test_text_search_combines_with_cep(self, fetch):
        fetch.return_value = api_response()
        self.event_at("Show de Rock", BOTAFOGO)
        self.event_at("Feira de Livros", NITEROI)
        response = self.client.get(self.url, {"cep": "20040020", "raio": "10", "q": "rock"})
        self.assertEqual(self.titles_in(response), ["Show de Rock"])

    @patch.object(cep_module, "_fetch_cep")
    def test_falls_back_to_city_when_cep_has_no_coordinates(self, fetch):
        fetch.return_value = api_response(coords=None)
        self.event_at("Mesma cidade", None, city="Rio de Janeiro", state="RJ")
        self.event_at("Cidade sem UF", None, city="rio de janeiro", state="")
        self.event_at("Outra cidade", None, city="Niterói", state="RJ")
        self.event_at("Mesmo nome, outro estado", None, city="Rio de Janeiro", state="MG")
        response = self.client.get(self.url, {"cep": "20040020"})
        self.assertEqual(
            sorted(self.titles_in(response)), ["Cidade sem UF", "Mesma cidade"]
        )
        self.assertContains(response, "mode=city")

    @patch.object(cep_module, "_fetch_cep")
    def test_invalid_cep_shows_error_and_full_catalog(self, fetch):
        self.event_at("Um", BOTAFOGO)
        self.event_at("Dois", SAO_PAULO)
        response = self.client.get(self.url, {"cep": "123"})
        fetch.assert_not_called()
        self.assertEqual(sorted(self.titles_in(response)), ["Dois", "Um"])
        self.assertContains(response, "CEP inválido")
        self.assertContains(response, "mode=none")

    @patch.object(cep_module, "_fetch_cep")
    def test_unknown_cep_shows_error_and_full_catalog(self, fetch):
        fetch.return_value = None
        self.event_at("Um", BOTAFOGO)
        response = self.client.get(self.url, {"cep": "00000-000"})
        self.assertEqual(self.titles_in(response), ["Um"])
        self.assertContains(response, "CEP não encontrado")

    @patch.object(cep_module, "_fetch_cep")
    def test_service_down_shows_error_and_full_catalog(self, fetch):
        fetch.side_effect = CepServiceError("fora do ar")
        self.event_at("Um", BOTAFOGO)
        response = self.client.get(self.url, {"cep": "20040020"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.titles_in(response), ["Um"])
        self.assertContains(response, "Não foi possível consultar o CEP")

    @patch.object(cep_module, "_fetch_cep")
    def test_same_cep_is_looked_up_only_once(self, fetch):
        fetch.return_value = api_response()
        for _ in range(3):
            self.client.get(self.url, {"cep": "20040-020"})
        self.assertEqual(fetch.call_count, 1)

    @patch.object(cep_module, "_fetch_cep")
    def test_pagination_links_keep_the_filters(self, fetch):
        fetch.return_value = api_response()
        for number in range(13):  # 13 eventos = 2 páginas (12 por página)
            self.event_at(f"Evento {number:02d}", BOTAFOGO)
        response = self.client.get(self.url, {"cep": "20040-020", "raio": "50", "page": "2"})
        self.assertEqual(response.status_code, 200)
        base_query = response.context["base_query"]
        self.assertIn("cep=20040-020", base_query)
        self.assertIn("raio=50", base_query)
        self.assertNotIn("page=", base_query)

    @patch.object(cep_module, "_fetch_cep")
    def test_catalog_paginates_radius_results(self, fetch):
        fetch.return_value = api_response()
        for number in range(14):
            self.event_at(f"Evento {number:02d}", BOTAFOGO)
        page1 = self.client.get(self.url, {"cep": "20040020", "raio": "10"})
        page2 = self.client.get(self.url, {"cep": "20040020", "raio": "10", "page": "2"})
        self.assertEqual(len(page1.context["events"]), 12)
        self.assertEqual(len(page2.context["events"]), 2)
