from django.contrib.auth.models import AnonymousUser


class FomoAnonymousUser(AnonymousUser):
    is_producer = False


class ProducerFlagMiddleware:
    """Expõe user.is_producer também para visitantes, conforme o contrato."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            request.user = FomoAnonymousUser()
        return self.get_response(request)
