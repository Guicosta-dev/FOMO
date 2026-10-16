"""Consulta de CEP (BrasilAPI v2) com cache.

A consulta acontece no SERVIDOR (e não no navegador): assim controlamos tempo
limite, cache e tratamento de erro, e o navegador nunca chama serviço externo.

A BrasilAPI informa cidade, UF, bairro e rua e, QUANDO DISPONÍVEL, as coordenadas.
Por isso o restante do sistema trata "sem coordenadas" como um caso normal.
"""
import json
import re
from dataclasses import asdict, dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.core.cache import cache

BASE_URL = "https://brasilapi.com.br/api/cep/v2/"
TIMEOUT_SECONDS = 5
CACHE_SECONDS = 60 * 60 * 24  # endereço de CEP quase nunca muda
NOT_FOUND_CACHE_SECONDS = 60 * 60
_NOT_FOUND = "not-found"


class CepServiceError(Exception):
    """O serviço de CEP está fora do ar, demorou demais ou respondeu algo inesperado."""


@dataclass(frozen=True)
class CepInfo:
    cep: str
    street: str
    neighborhood: str
    city: str
    state: str
    latitude: float | None = None
    longitude: float | None = None

    @property
    def has_coordinates(self):
        return self.latitude is not None and self.longitude is not None


class LocationStatus:
    """Resultado de apply_cep_location."""

    OK = "ok"
    NO_CEP = "no_cep"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    NO_COORDINATES = "no_coordinates"


_CEP_RE = re.compile(r"\s*(\d{5})[-.\s]?(\d{3})\s*")


def normalize_cep(value):
    """'20040-020' -> '20040020'. Retorna None se não for um CEP válido (8 dígitos)."""
    if not isinstance(value, str):
        return None
    match = _CEP_RE.fullmatch(value)
    return match.group(1) + match.group(2) if match else None


def format_cep(cep):
    return f"{cep[:5]}-{cep[5:]}"


def _fetch_cep(cep):
    """Chama a BrasilAPI. Retorna o JSON (dict), None se o CEP não existe,
    ou levanta CepServiceError se o serviço falhou."""
    request = Request(
        BASE_URL + cep,
        headers={"Accept": "application/json", "User-Agent": "FOMO/1.0"},
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:  # precisa vir antes de OSError (HTTPError herda dele)
        if error.code in (400, 404):
            return None
        raise CepServiceError(f"O serviço de CEP respondeu com erro {error.code}.") from error
    except (OSError, ValueError) as error:  # sem rede, tempo esgotado, JSON inválido...
        raise CepServiceError("Não foi possível consultar o serviço de CEP.") from error


def _to_coordinate(value, limit):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if -limit <= number <= limit else None


def _parse(cep, data):
    if not isinstance(data, dict):
        raise CepServiceError("Resposta inesperada do serviço de CEP.")
    city = str(data.get("city") or "").strip()
    if not city:
        raise CepServiceError("Resposta do serviço de CEP sem cidade.")

    location = data.get("location")
    coordinates = location.get("coordinates") if isinstance(location, dict) else None
    if not isinstance(coordinates, dict):
        coordinates = {}
    latitude = _to_coordinate(coordinates.get("latitude"), 90)
    longitude = _to_coordinate(coordinates.get("longitude"), 180)
    if latitude is None or longitude is None:
        latitude = longitude = None

    return CepInfo(
        cep=cep,
        street=str(data.get("street") or "").strip(),
        neighborhood=str(data.get("neighborhood") or "").strip(),
        city=city,
        state=str(data.get("state") or "").strip().upper()[:2],
        latitude=latitude,
        longitude=longitude,
    )


def lookup_cep(cep):
    """Busca um CEP já normalizado (8 dígitos).

    Retorna CepInfo, ou None se o CEP não existe. Levanta CepServiceError se o
    serviço falhar (falhas NÃO ficam no cache, para tentar de novo logo depois).
    """
    key = f"cep:{cep}"
    cached = cache.get(key)
    if cached == _NOT_FOUND:
        return None
    if cached is not None:
        return CepInfo(**cached)

    data = _fetch_cep(cep)
    if data is None:
        cache.set(key, _NOT_FOUND, NOT_FOUND_CACHE_SECONDS)
        return None
    info = _parse(cep, data)
    cache.set(key, asdict(info), CACHE_SECONDS)
    return info


def apply_cep_location(event):
    """Preenche cidade, UF, bairro e coordenadas do evento a partir do CEP dele.

    Não salva o evento (quem chama salva). Retorna um LocationStatus.
    Sempre limpa antes os campos calculados, para nunca sobrar coordenada de um CEP antigo.
    """
    event.state = ""
    event.neighborhood = ""
    event.latitude = None
    event.longitude = None

    if not event.cep:
        return LocationStatus.NO_CEP
    try:
        info = lookup_cep(event.cep)
    except CepServiceError:
        return LocationStatus.UNAVAILABLE
    if info is None:
        return LocationStatus.NOT_FOUND

    event.city = info.city[:80]  # nome oficial: evita "Rio" x "Rio de Janeiro" nos filtros
    event.state = info.state
    event.neighborhood = info.neighborhood[:80]
    if info.has_coordinates:
        event.latitude = info.latitude
        event.longitude = info.longitude
        return LocationStatus.OK
    return LocationStatus.NO_COORDINATES
