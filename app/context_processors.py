DISCOVERY_CATEGORIES = [
    {"nome": "Shows e Música", "query": "Música", "icone": "music"},
    {"nome": "Festivais", "query": "Festival", "icone": "ticket"},
    {"nome": "Teatro e Artes", "query": "Teatro", "icone": "masks"},
    {"nome": "Esportes", "query": "Esporte", "icone": "dumbbell"},
    {"nome": "Cursos e Palestras", "query": "Palestra", "icone": "graduation"},
    {"nome": "Networking", "query": "Networking", "icone": "users"},
    {"nome": "Experiências", "query": "Experiência", "icone": "star"},
    {"nome": "Outros", "query": "", "icone": "calendar"},
]


def branding(request):
    match = request.resolver_match
    return {
        "discovery_categories": DISCOVERY_CATEGORIES,
        "active_page": match.url_name if match else "",
        "preview_frontend": False,
    }
