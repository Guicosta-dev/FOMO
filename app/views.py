from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.functional import cached_property
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from .cep import (
    CepServiceError,
    LocationStatus,
    apply_cep_location,
    format_cep,
    lookup_cep,
    normalize_cep,
)
from .forms import BatchForm, EventForm, LoginForm, OrderForm, SignUpForm
from .geo import DEFAULT_RADIUS, RADIUS_OPTIONS
from .models import Event, Order, TicketBatch
from .services import OrderError, create_order, hold_minutes


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
    """Catálogo público: só eventos publicados que ainda não terminaram.

    Filtros por querystring:
      ?q=texto            busca em título, cidade e descrição
      ?cep=20040-020      eventos próximos ao CEP do visitante
      ?raio=25            raio em km (5, 10, 25, 50 ou 100; padrão 25)
    """

    template_name = "events/catalog.html"
    context_object_name = "events"
    paginate_by = 12
    
    def get_featured_events(self):
        """Eventos com imagem para o carrossel da home (só roda quando o hero aparece)."""
        return list(
            Event.objects.published()
            .upcoming()
            .filter(image__isnull=False)
            .exclude(image="")
            .order_by("starts_at")[:8]
        )

    @cached_property
    def radius(self):
        try:
            value = int(self.request.GET.get("raio", ""))
        except ValueError:
            return DEFAULT_RADIUS
        return value if value in RADIUS_OPTIONS else DEFAULT_RADIUS

    @cached_property
    def near(self):
        """Resolve o CEP do visitante. Retorna (cep, info, mensagem_de_erro)."""
        raw = self.request.GET.get("cep", "").strip()
        if not raw:
            return None, None, None
        cep = normalize_cep(raw)
        if cep is None:
            return None, None, "CEP inválido. Informe os 8 dígitos, ex.: 20040-020."
        try:
            info = lookup_cep(cep)
        except CepServiceError:
            return cep, None, "Não foi possível consultar o CEP agora. Tente novamente em instantes."
        if info is None:
            return cep, None, "CEP não encontrado."
        return cep, info, None

    def get_queryset(self):
        queryset = Event.objects.published().upcoming().select_related("producer")
        q = self.request.GET.get("q", "").strip()
        if q:
            queryset = queryset.filter(
                Q(title__icontains=q) | Q(city__icontains=q) | Q(description__icontains=q)
            )

        _, info, _ = self.near
        if info is None:
            return queryset  # sem CEP (ou CEP com problema): catálogo completo
        if info.has_coordinates:
            # LISTA ordenada do mais perto para o mais longe, com `distance_km` em cada evento.
            return queryset.nearby(info.latitude, info.longitude, self.radius)
        # O serviço de CEP não tem coordenadas para este CEP: filtra pela cidade.
        return queryset.filter(city__iexact=info.city).filter(
            Q(state="") | Q(state__iexact=info.state)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cep, info, error = self.near
        params = self.request.GET.copy()
        params.pop("page", None)
        q = self.request.GET.get("q", "").strip()
        page = context.get("page_obj")
        show_hero = not q and (page is None or page.number == 1)
        context.update(
            q=q,
            show_hero=show_hero,
            featured_events=self.get_featured_events() if show_hero else [],
            cep=format_cep(cep) if cep else self.request.GET.get("cep", "").strip(),
            cep_info=info,
            cep_error=error,
            location_mode=(None if info is None else "radius" if info.has_coordinates else "city"),
            radius=self.radius,
            radius_options=RADIUS_OPTIONS,
            base_query=params.urlencode(),
        )
        return context


class EventDetailView(DetailView):
    """Página pública do evento. Rascunhos dão 404 para qualquer pessoa."""

    template_name = "events/event_detail.html"
    context_object_name = "event"

    def get_queryset(self):
        return Event.objects.published().select_related("producer")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        batches = list(self.object.batches.filter(is_active=True))
        context["batches"] = batches
        context["order_form"] = OrderForm(batches=batches)
        return context


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


class EventLocationMixin:
    """Antes de salvar o evento, descobre cidade, UF e coordenadas pelo CEP do local.

    Best effort: se o serviço de CEP falhar, o evento É salvo (sem coordenadas) e o
    produtor é avisado. A consulta só acontece quando o CEP mudou ou ainda não há
    coordenadas, para não depender do serviço externo a cada edição.
    """

    LOCATION_WARNINGS = {
        LocationStatus.NOT_FOUND: (
            "CEP não encontrado. O evento foi salvo, mas não aparecerá nas buscas por proximidade."
        ),
        LocationStatus.UNAVAILABLE: (
            "Não foi possível consultar o CEP agora. O evento foi salvo sem localização; "
            "edite e salve de novo mais tarde para tentar outra vez."
        ),
        LocationStatus.NO_COORDINATES: (
            "Encontramos a cidade pelo CEP, mas não as coordenadas. O evento aparecerá nas "
            "buscas por cidade, não nas buscas por raio."
        ),
    }

    def form_valid(self, form):
        event = form.instance
        # Compara com o CEP já salvo (e não com o texto digitado): "20040-020" e
        # "20040020" são o mesmo CEP e não devem repetir a consulta.
        cep_changed = event.cep != form.initial.get("cep", "")
        if cep_changed or (event.cep and event.latitude is None):
            status = apply_cep_location(event)
            warning = self.LOCATION_WARNINGS.get(status)
            if warning:
                messages.warning(self.request, warning)
        return super().form_valid(form)


class DashboardView(OwnEventsMixin, ListView):
    template_name = "dashboard/event_list.html"
    context_object_name = "events"

    def get_queryset(self):
        return super().get_queryset().order_by("-created_at")


class EventCreateView(EventLocationMixin, ProducerRequiredMixin, CreateView):
    model = Event
    form_class = EventForm
    template_name = "dashboard/event_form.html"
    success_url = reverse_lazy("app:dashboard")

    def form_valid(self, form):
        form.instance.producer = self.request.user  # nunca vem do navegador
        messages.success(self.request, "Evento salvo como rascunho.")
        return super().form_valid(form)


class EventUpdateView(EventLocationMixin, OwnEventsMixin, UpdateView):
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


# =============================================================================
# Lotes de ingressos (painel do produtor)
# =============================================================================

class BatchListView(ProducerRequiredMixin, ListView):
    """Lotes de um evento do produtor, com vendidos e disponíveis."""

    template_name = "dashboard/batch_list.html"
    context_object_name = "batches"

    @cached_property
    def event(self):
        return get_object_or_404(
            Event.objects.filter(producer=self.request.user), pk=self.kwargs["event_pk"]
        )

    def get_queryset(self):
        return self.event.batches.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["event"] = self.event
        return context


class BatchCreateView(ProducerRequiredMixin, CreateView):
    model = TicketBatch
    form_class = BatchForm
    template_name = "dashboard/batch_form.html"

    @cached_property
    def event(self):
        return get_object_or_404(
            Event.objects.filter(producer=self.request.user), pk=self.kwargs["event_pk"]
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["event"] = self.event
        return context

    def get_success_url(self):
        return reverse("app:batch_list", args=[self.event.pk])

    def form_valid(self, form):
        form.instance.event = self.event  # nunca vem do navegador
        messages.success(self.request, "Lote criado.")
        return super().form_valid(form)


class BatchUpdateView(ProducerRequiredMixin, UpdateView):
    form_class = BatchForm
    template_name = "dashboard/batch_form.html"

    def get_queryset(self):
        return TicketBatch.objects.filter(
            event__producer=self.request.user
        ).select_related("event")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["event"] = self.object.event
        return context

    def get_success_url(self):
        return reverse("app:batch_list", args=[self.object.event_id])

    def form_valid(self, form):
        messages.success(self.request, "Lote atualizado.")
        return super().form_valid(form)


# =============================================================================
# Pedidos (comprador)
# =============================================================================

class OrderCreateView(LoginRequiredMixin, View):
    """Recebe as quantidades escolhidas na página do evento e cria o pedido."""

    http_method_names = ["post"]

    def post(self, request, slug):
        event = get_object_or_404(Event.objects.published(), slug=slug)
        batches = list(event.batches.filter(is_active=True))
        form = OrderForm(request.POST, batches=batches)
        if not form.is_valid():
            messages.error(
                request, "Quantidade inválida. Use números inteiros, de zero para cima."
            )
            return redirect(event)
        try:
            order = create_order(request.user, event, form.quantities())
        except OrderError as error:
            messages.error(request, str(error))
            return redirect(event)

        if order.status == Order.Status.CONFIRMED:
            messages.success(request, "Pedido confirmado!")
        else:
            messages.success(
                request,
                f"Pedido criado! Os ingressos ficam reservados por {hold_minutes()} "
                "minutos, aguardando o pagamento.",
            )
        return redirect("app:order_detail", public_id=order.public_id)


class OrderListView(LoginRequiredMixin, ListView):
    """Histórico de pedidos do usuário logado."""

    template_name = "orders/order_list.html"
    context_object_name = "orders"

    def get_queryset(self):
        Order.objects.expire_stale()
        return (
            Order.objects.filter(user=self.request.user)
            .select_related("event")
            .prefetch_related("items__batch")
        )


class OrderDetailView(LoginRequiredMixin, DetailView):
    template_name = "orders/order_detail.html"
    context_object_name = "order"
    slug_field = "public_id"
    slug_url_kwarg = "public_id"

    def get_queryset(self):
        Order.objects.expire_stale()
        return (
            Order.objects.filter(user=self.request.user)  # pedido alheio -> 404
            .select_related("event")
            .prefetch_related("items__batch")
        )


class OrderCancelView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, public_id):
        order = get_object_or_404(Order.objects.filter(user=request.user), public_id=public_id)
        try:
            order.cancel()
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, "Pedido cancelado. Os ingressos foram liberados.")
        return redirect("app:order_detail", public_id=order.public_id)
