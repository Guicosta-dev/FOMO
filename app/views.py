"""Views reais do FOMO, com acesso por perfil e propriedade dos registros."""

from functools import wraps
from urllib.parse import urlencode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Min, Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from .forms import BatchForm, EventForm, SignupForm, TicketOrderForm
from .models import Event, Favorite, Order, TicketBatch
from .services import OrderRuleError, cancel_order, create_order, expire_pending_orders


def producer_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            destination = (
                request.get_full_path()
                if request.method == "GET"
                else reverse("app:dashboard")
            )
            return redirect(
                f'{reverse("app:login")}?{urlencode({"next": destination})}'
            )
        if not request.user.is_producer:
            raise PermissionDenied("Esta área é exclusiva de produtores.")
        return view(request, *args, **kwargs)

    return wrapper


def owned_event(request, pk, *, locked=False):
    queryset = Event.objects.filter(producer=request.user)
    if locked:
        queryset = queryset.select_for_update()
    return get_object_or_404(queryset, pk=pk)


def present_events(queryset, user):
    events = list(queryset)
    favorites = set()
    if user.is_authenticated:
        favorites = set(
            Favorite.objects.filter(user=user, event__in=events).values_list(
                "event_id", flat=True
            )
        )
    for event in events:
        event.is_favorite = event.pk in favorites
    return events


class FomoLoginView(LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


class FomoLogoutView(LogoutView):
    next_page = "app:catalog"
    http_method_names = ["post"]


@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect("app:catalog")
    form = SignupForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
        except IntegrityError:
            form.add_error(
                None, "Este usuário ou e-mail já foi cadastrado. Confira os dados."
            )
        else:
            login(request, user)
            messages.success(request, "Conta criada com sucesso. Bem-vindo ao FOMO!")
            return redirect("app:dashboard" if user.is_producer else "app:catalog")
    return render(request, "accounts/signup.html", {"form": form})


@require_GET
def catalog(request):
    q = request.GET.get("q", "").strip()[:150]
    queryset = (
        Event.objects.filter(
            status=Event.Status.PUBLISHED, starts_at__gt=timezone.now()
        )
        .select_related("producer")
        .annotate(min_price=Min("batches__price", filter=Q(batches__is_active=True)))
    )
    if q:
        queryset = queryset.filter(
            Q(title__icontains=q)
            | Q(description__icontains=q)
            | Q(venue_name__icontains=q)
            | Q(city__icontains=q)
        )
    paginator = Paginator(
        queryset.order_by("starts_at", "pk"), settings.FOMO_CATALOG_PAGE_SIZE
    )
    page_obj = paginator.get_page(request.GET.get("page"))
    events = present_events(page_obj.object_list, request.user)
    return render(
        request,
        "events/catalog.html",
        {
            "events": events,
            "page_obj": page_obj,
            "paginator": paginator,
            "is_paginated": page_obj.has_other_pages(),
            "q": q,
            "show_hero": not q and page_obj.number == 1,
        },
    )


@require_GET
def event_detail(request, slug):
    allowed = Q(status=Event.Status.PUBLISHED)
    if request.user.is_authenticated and request.user.is_producer:
        allowed |= Q(producer=request.user)
    event = get_object_or_404(
        Event.objects.select_related("producer").filter(allowed), slug=slug
    )
    expire_pending_orders(event=event)
    batches = list(event.batches.filter(is_active=True).select_related("event"))
    order_form = TicketOrderForm(batches=batches)
    event.is_favorite = (
        request.user.is_authenticated
        and Favorite.objects.filter(user=request.user, event=event).exists()
    )
    return render(
        request,
        "events/event_detail.html",
        {
            "event": event,
            "batches": batches,
            "order_form": order_form,
            "can_manage": request.user.is_authenticated
            and event.producer_id == request.user.pk
            and request.user.is_producer,
            "has_on_sale": any(batch.on_sale for batch in batches),
        },
    )


@require_GET
@producer_required
def dashboard(request):
    events = (
        Event.objects.filter(producer=request.user)
        .prefetch_related("batches")
        .order_by("-created_at")
    )
    return render(request, "dashboard/event_list.html", {"events": events})


@require_http_methods(["GET", "POST"])
@producer_required
def event_create(request):
    form = EventForm(
        request.POST if request.method == "POST" else None,
        instance=Event(producer=request.user),
    )
    if request.method == "POST" and form.is_valid():
        event = form.save()
        messages.success(
            request, "Evento criado. Cadastre um lote ativo para poder publicá-lo."
        )
        return redirect("app:batch_list", event_pk=event.pk)
    return render(request, "dashboard/event_form.html", {"form": form, "object": None})


@require_http_methods(["GET", "POST"])
@producer_required
def event_update(request, pk):
    if request.method == "POST":
        with transaction.atomic():
            event = owned_event(request, pk, locked=True)
            form = EventForm(request.POST, instance=event)
            if form.is_valid():
                form.save()
                messages.success(request, "Evento atualizado com sucesso.")
                return redirect("app:dashboard")
    else:
        event = owned_event(request, pk)
        form = EventForm(instance=event)
    return render(request, "dashboard/event_form.html", {"form": form, "object": event})


@require_POST
@producer_required
def event_publish(request, pk):
    with transaction.atomic():
        event = owned_event(request, pk, locked=True)
        if not event.has_active_batch:
            messages.error(request, "Crie um lote ativo antes de publicar o evento.")
            return redirect("app:batch_list", event_pk=event.pk)
        if event.starts_at <= timezone.now():
            messages.error(request, "Não é possível publicar um evento que já começou.")
            return redirect("app:dashboard")
        event.status = Event.Status.PUBLISHED
        event.published_at = timezone.now()
        event.full_clean()
        event.save(update_fields=["status", "published_at"])
    messages.success(request, "Evento publicado e disponível no catálogo.")
    return redirect("app:dashboard")


@require_POST
@producer_required
def event_unpublish(request, pk):
    with transaction.atomic():
        event = owned_event(request, pk, locked=True)
        event.status = Event.Status.DRAFT
        event.save(update_fields=["status"])
    messages.success(
        request, "Evento despublicado. Os pedidos existentes continuam registrados."
    )
    return redirect("app:dashboard")


@require_http_methods(["GET", "POST"])
@producer_required
def event_delete(request, pk):
    event = owned_event(request, pk)
    if request.method == "POST":
        with transaction.atomic():
            event = owned_event(request, pk, locked=True)
            if event.is_published:
                messages.error(request, "Despublique o evento antes de excluí-lo.")
                return redirect("app:dashboard")
            try:
                event.delete()
            except ProtectedError:
                messages.error(
                    request,
                    "Este evento tem pedidos e deve ser mantido para preservar o histórico.",
                )
                return redirect("app:dashboard")
        messages.success(request, "Evento excluído com sucesso.")
        return redirect("app:dashboard")
    return render(request, "dashboard/event_confirm_delete.html", {"event": event})


@require_GET
@producer_required
def batch_list(request, event_pk):
    event = owned_event(request, event_pk)
    expire_pending_orders(event=event)
    return render(
        request,
        "dashboard/batch_list.html",
        {"event": event, "batches": event.batches.all()},
    )


@require_http_methods(["GET", "POST"])
@producer_required
def batch_create(request, event_pk):
    with transaction.atomic():
        event = owned_event(request, event_pk, locked=request.method == "POST")
        form = BatchForm(
            request.POST if request.method == "POST" else None,
            instance=TicketBatch(event=event),
        )
        if request.method == "POST" and form.is_valid():
            form.save()
            messages.success(request, "Lote criado com sucesso.")
            return redirect("app:batch_list", event_pk=event.pk)
    return render(
        request,
        "dashboard/batch_form.html",
        {"form": form, "event": event, "object": None},
    )


@require_http_methods(["GET", "POST"])
@producer_required
def batch_update(request, pk):
    original = get_object_or_404(
        TicketBatch.objects.filter(event__producer=request.user), pk=pk
    )
    expire_pending_orders(event=original.event)
    with transaction.atomic():
        event = owned_event(request, original.event_id, locked=request.method == "POST")
        batches = TicketBatch.objects.filter(event=event)
        if request.method == "POST":
            batches = batches.select_for_update()
        batch = get_object_or_404(batches, pk=pk)
        form = BatchForm(
            request.POST if request.method == "POST" else None, instance=batch
        )
        if request.method == "POST" and form.is_valid():
            form.save()
            messages.success(request, "Lote atualizado com sucesso.")
            return redirect("app:batch_list", event_pk=event.pk)
    return render(
        request,
        "dashboard/batch_form.html",
        {"form": form, "event": event, "object": batch},
    )


@require_POST
def order_create(request, slug):
    event = get_object_or_404(Event, slug=slug, status=Event.Status.PUBLISHED)
    if not request.user.is_authenticated:
        return redirect(
            f'{reverse("app:login")}?{urlencode({"next": event.get_absolute_url()})}'
        )
    try:
        expire_pending_orders(event=event)
        batches = list(event.batches.filter(is_active=True).select_related("event"))
        form = TicketOrderForm(request.POST, batches=batches)
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return redirect(event)
        order = create_order(
            buyer=request.user, event=event, quantities=form.quantities()
        )
    except OrderRuleError as error:
        messages.error(request, str(error))
        return redirect(event)
    except OperationalError as error:
        if "locked" not in str(error).lower() and "busy" not in str(error).lower():
            raise
        messages.error(
            request,
            "O estoque está sendo atualizado. Aguarde um instante e tente novamente.",
        )
        return redirect(event)
    messages.success(
        request, "Pedido criado. Os ingressos estão reservados até o prazo indicado."
    )
    return redirect(order)


@require_GET
@login_required
def order_list(request):
    expire_pending_orders(buyer=request.user)
    orders = Order.objects.filter(buyer=request.user).select_related("event")
    return render(request, "orders/order_list.html", {"orders": orders})


@require_GET
@login_required
def order_detail(request, public_id):
    order = get_object_or_404(
        Order.objects.filter(buyer=request.user).select_related("event"),
        public_id=public_id,
    )
    expire_pending_orders(buyer=request.user)
    order.refresh_from_db()
    return render(
        request,
        "orders/order_detail.html",
        {"order": order, "manual_confirmation": settings.FOMO_MANUAL_CONFIRMATION},
    )


@require_POST
@login_required
def order_cancel(request, public_id):
    order = get_object_or_404(Order, public_id=public_id, buyer=request.user)
    try:
        result = cancel_order(public_id=order.public_id, buyer=request.user)
    except OrderRuleError as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request,
            (
                "Pedido cancelado e ingressos liberados."
                if result.status == Order.Status.CANCELLED
                else "A reserva expirou e os ingressos foram liberados."
            ),
        )
    return redirect(order)


@require_GET
@login_required
def favorites(request):
    events = (
        Event.objects.filter(
            favorites__user=request.user, status=Event.Status.PUBLISHED
        )
        .select_related("producer")
        .annotate(min_price=Min("batches__price", filter=Q(batches__is_active=True)))
    )
    return render(
        request, "favorites.html", {"events": present_events(events, request.user)}
    )


@require_POST
@login_required
def favorite_toggle(request, slug):
    event = get_object_or_404(Event, slug=slug, status=Event.Status.PUBLISHED)
    favorite, created = Favorite.objects.get_or_create(user=request.user, event=event)
    if not created:
        favorite.delete()
    messages.success(
        request,
        (
            "Evento adicionado aos favoritos."
            if created
            else "Evento removido dos favoritos."
        ),
    )
    return redirect(event)


@require_GET
def about(request):
    return render(request, "about.html")
