from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from .forms import EventForm, LoginForm, SignUpForm
from .models import Event


# =============================================================================
# Contas
# =============================================================================

def home_url_for(user):
    """Produtor vai para o painel; os demais, para o catálogo."""
    if user.is_producer:
        return reverse("app:dashboard")
    return reverse("app:catalog")


class RoleAwareLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        return home_url_for(self.request.user)


class SignUpView(CreateView):
    form_class = SignUpForm
    template_name = "accounts/signup.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(home_url_for(request.user))
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        return redirect(home_url_for(user))


# =============================================================================
# Área pública (catálogo)
# =============================================================================

class CatalogView(ListView):
    """Catálogo público: só eventos publicados que ainda não terminaram."""

    template_name = "events/catalog.html"
    context_object_name = "events"
    paginate_by = 12

    def get_queryset(self):
        queryset = Event.objects.published().upcoming().select_related("producer")
        q = self.request.GET.get("q", "").strip()
        if q:
            queryset = queryset.filter(
                Q(title__icontains=q) | Q(city__icontains=q) | Q(description__icontains=q)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["q"] = self.request.GET.get("q", "").strip()
        return context


class EventDetailView(DetailView):
    """Página pública do evento. Rascunhos dão 404 para qualquer pessoa."""

    template_name = "events/event_detail.html"
    context_object_name = "event"

    def get_queryset(self):
        return Event.objects.published().select_related("producer")


# =============================================================================
# Painel do produtor
# =============================================================================

class ProducerRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Só deixa passar usuários logados que sejam produtores.

    - Visitante anônimo    -> redirecionado para o login.
    - Logado, não produtor -> erro 403 (acesso negado).
    A checagem acontece no servidor, em toda requisição.
    """

    def test_func(self):
        return self.request.user.is_producer


class OwnEventsMixin(ProducerRequiredMixin):
    """Restringe tudo aos eventos do produtor logado.

    Evento de outra pessoa simplesmente "não existe" para quem pede: dá 404.
    """

    def get_queryset(self):
        return Event.objects.filter(producer=self.request.user)


class DashboardView(OwnEventsMixin, ListView):
    template_name = "dashboard/event_list.html"
    context_object_name = "events"

    def get_queryset(self):
        return super().get_queryset().order_by("-created_at")


class EventCreateView(ProducerRequiredMixin, CreateView):
    model = Event
    form_class = EventForm
    template_name = "dashboard/event_form.html"
    success_url = reverse_lazy("app:dashboard")

    def form_valid(self, form):
        form.instance.producer = self.request.user  # nunca vem do navegador
        messages.success(self.request, "Evento salvo como rascunho.")
        return super().form_valid(form)


class EventUpdateView(OwnEventsMixin, UpdateView):
    form_class = EventForm
    template_name = "dashboard/event_form.html"
    success_url = reverse_lazy("app:dashboard")

    def form_valid(self, form):
        messages.success(self.request, "Evento atualizado.")
        return super().form_valid(form)


class EventPublishView(ProducerRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        event = get_object_or_404(Event.objects.filter(producer=request.user), pk=pk)
        try:
            event.publish()
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, f"“{event.title}” foi publicado.")
        return redirect("app:dashboard")


class EventUnpublishView(ProducerRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        event = get_object_or_404(Event.objects.filter(producer=request.user), pk=pk)
        event.unpublish()
        messages.success(request, f"“{event.title}” voltou para rascunho.")
        return redirect("app:dashboard")

