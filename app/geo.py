"""Cálculos de distância (sem internet, sem banco: funções puras e fáceis de testar)."""
import math

EARTH_RADIUS_KM = 6371.0088

# Raios oferecidos ao visitante (km) e o padrão quando o valor for inválido.
RADIUS_OPTIONS = (5, 10, 25, 50, 100)
DEFAULT_RADIUS = 25


def haversine_km(lat1, lon1, lat2, lon2):
    """Distância em km, em linha reta, entre dois pontos (latitude/longitude em graus)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bounding_box(lat, lon, radius_km):
    """Retângulo que contém o círculo de busca: (lat_min, lat_max, lon_min, lon_max).

    Serve para o banco descartar rapidamente o que está longe; a distância exata
    é conferida depois com haversine_km.
    """
    delta_lat = radius_km / 111.0
    # 1 grau de longitude encurta conforme se afasta da linha do Equador.
    delta_lon = radius_km / (111.320 * max(math.cos(math.radians(lat)), 0.01))
    return lat - delta_lat, lat + delta_lat, lon - delta_lon, lon + delta_lon
