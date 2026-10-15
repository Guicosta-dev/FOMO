import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify


class User(AbstractUser):
    """Usuário da FOMO: o Django já cuida de senha, login e permissões.

    Acrescentamos apenas o tipo de conta (produtor ou consumidor).
    """

    class Role(models.TextChoices):
        CONSUMER = "consumer", "Consumidor"
        PRODUCER = "producer", "Produtor"

    role = models.CharField(
        "tipo de conta",
        max_length=10,
        choices=Role.choices,
        default=Role.CONSUMER,
    )

    @property
    def is_producer(self):
        return self.role == self.Role.PRODUCER


class EventQuerySet(models.QuerySet):
    def published(self):
        """Eventos que o produtor já publicou (rascunhos ficam de fora)."""
        return self.filter(status=Event.Status.PUBLISHED)

    def upcoming(self):
        """Eventos que ainda não terminaram (inclui os que estão acontecendo agora)."""
        now = timezone.now()
        return self.filter(
            Q(ends_at__gte=now) | Q(ends_at__isnull=True, starts_at__gte=now)
        )


class Event(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Rascunho"
        PUBLISHED = "published", "Publicado"

    # PROTECT: não apagar um produtor que ainda tem eventos (no futuro, terá vendas).
    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="events",
        verbose_name="produtor",
    )
    title = models.CharField("título", max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True, editable=False)
    description = models.TextField("descrição")
    venue_name = models.CharField("nome do local", max_length=120)
    address = models.CharField("endereço", max_length=255)
    city = models.CharField("cidade", max_length=80)
    starts_at = models.DateTimeField("início")
    ends_at = models.DateTimeField("término", null=True, blank=True)
    status = models.CharField(
        "status", max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    published_at = models.DateTimeField("publicado em", null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ["starts_at"]
        verbose_name = "evento"
        verbose_name_plural = "eventos"
        indexes = [models.Index(fields=["status", "starts_at"])]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("app:event_detail", kwargs={"slug": self.slug})

    # --- validação e salvamento -------------------------------------------------

    def clean(self):
        super().clean()
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "O término deve ser depois do início."})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._generate_unique_slug()
        super().save(*args, **kwargs)

    def _generate_unique_slug(self):
        """Gera 'festa-junina', 'festa-junina-2', ... O slug não muda ao editar o título."""
        base = slugify(self.title)[:120] or "evento"
        slug, n = base, 2
        while Event.objects.filter(slug=slug).exists():
            slug = f"{base}-{n}"
            n += 1
        return slug

    # --- regras de publicação ---------------------------------------------------

    @property
    def is_published(self):
        return self.status == self.Status.PUBLISHED

    @property
    def has_ended(self):
        return (self.ends_at or self.starts_at) < timezone.now()

    @property
    def has_active_batch(self):
        """O evento tem pelo menos um lote de ingressos ativo?"""
        return self.batches.filter(is_active=True).exists()

    def publish(self):
        if self.has_ended:
            raise ValidationError("Não é possível publicar um evento que já terminou.")
        if not self.has_active_batch:
            raise ValidationError(
                "Crie pelo menos um lote de ingressos ativo antes de publicar o evento."
            )
        self.status = self.Status.PUBLISHED
        if self.published_at is None:
            self.published_at = timezone.now()
        self.save(update_fields=["status", "published_at", "updated_at"])

    def unpublish(self):
        self.status = self.Status.DRAFT
        self.save(update_fields=["status", "updated_at"])


# =============================================================================
# Etapa 4 — Lotes e pedidos
# =============================================================================


class TicketBatch(models.Model):
    """Lote de ingressos de um evento (ex.: 'Pista – 1º lote')."""

    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name="batches", verbose_name="evento"
    )
    name = models.CharField("nome", max_length=80)
    price = models.DecimalField(
        "preço",
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Use 0 para ingresso gratuito.",
    )
    quantity = models.PositiveIntegerField(
        "quantidade total", validators=[MinValueValidator(1)]
    )
    max_per_order = models.PositiveSmallIntegerField(
        "máximo por pedido",
        default=4,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
    )
    sales_start = models.DateTimeField("início das vendas", null=True, blank=True)
    sales_end = models.DateTimeField("fim das vendas", null=True, blank=True)
    is_active = models.BooleanField("ativo", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "lote"
        verbose_name_plural = "lotes"

    def __str__(self):
        return f"{self.name} ({self.event})"

    # --- estoque ------------------------------------------------------------------
    # Ocupam estoque: pedidos CONFIRMADOS e pedidos PENDENTES ainda dentro do prazo
    # de reserva. Cancelados e expirados liberam os ingressos automaticamente.

    @property
    def taken(self):
        now = timezone.now()
        total = self.items.filter(
            Q(order__status=Order.Status.CONFIRMED)
            | Q(order__status=Order.Status.PENDING, order__expires_at__gt=now)
        ).aggregate(total=Sum("quantity"))["total"]
        return total or 0

    @property
    def sold(self):
        """Ingressos de pedidos já confirmados."""
        total = self.items.filter(order__status=Order.Status.CONFIRMED).aggregate(
            total=Sum("quantity")
        )["total"]
        return total or 0

    @property
    def available(self):
        return max(self.quantity - self.taken, 0)

    def is_open_now(self, now=None):
        """O lote está ativo e dentro da janela de vendas?"""
        now = now or timezone.now()
        if not self.is_active:
            return False
        if self.sales_start and now < self.sales_start:
            return False
        if self.sales_end and now > self.sales_end:
            return False
        return True

    @property
    def on_sale(self):
        return self.is_open_now() and self.available > 0

    def clean(self):
        super().clean()
        errors = {}
        if self.sales_start and self.sales_end and self.sales_end <= self.sales_start:
            errors["sales_end"] = "O fim das vendas deve ser depois do início."
        if self.pk and self.quantity is not None and self.quantity < self.taken:
            errors["quantity"] = (
                f"Já existem {self.taken} ingresso(s) vendidos ou reservados; "
                "a quantidade total não pode ser menor que isso."
            )
        if errors:
            raise ValidationError(errors)


class OrderQuerySet(models.QuerySet):
    def expire_stale(self):
        """Marca como EXPIRADO os pedidos pendentes cujo prazo de reserva acabou."""
        return self.filter(
            status=Order.Status.PENDING, expires_at__lte=timezone.now()
        ).update(status=Order.Status.EXPIRED)


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Aguardando pagamento"
        CONFIRMED = "confirmed", "Confirmado"
        CANCELLED = "cancelled", "Cancelado"
        EXPIRED = "expired", "Expirado"

    # Identificador público (usado nas URLs) para ninguém "adivinhar" pedidos por número.
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="comprador",
    )
    event = models.ForeignKey(
        Event, on_delete=models.PROTECT, related_name="orders", verbose_name="evento"
    )
    status = models.CharField(
        "status", max_length=10, choices=Status.choices, default=Status.PENDING
    )
    total = models.DecimalField("total", max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField("reserva válida até", null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    objects = OrderQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "pedido"
        verbose_name_plural = "pedidos"

    def __str__(self):
        return f"Pedido {self.short_code}"

    @property
    def short_code(self):
        return str(self.public_id)[:8].upper()

    @property
    def is_expired(self):
        if self.status == self.Status.EXPIRED:
            return True
        return (
            self.status == self.Status.PENDING
            and self.expires_at is not None
            and self.expires_at <= timezone.now()
        )

    def confirm(self):
        """Confirma um pedido pendente. Chamar de novo num pedido confirmado não faz nada."""
        if self.status == self.Status.CONFIRMED:
            return
        if self.status != self.Status.PENDING or self.is_expired:
            raise ValidationError(
                "Só é possível confirmar pedidos pendentes e dentro do prazo de reserva."
            )
        self.status = self.Status.CONFIRMED
        self.confirmed_at = timezone.now()
        self.expires_at = None
        self.save(update_fields=["status", "confirmed_at", "expires_at"])

    def cancel(self):
        """Cancela um pedido pendente (libera os ingressos)."""
        if self.status != self.Status.PENDING:
            raise ValidationError(
                "Só é possível cancelar pedidos pendentes. O cancelamento de pedidos "
                "confirmados depende de uma política que ainda não foi definida."
            )
        self.status = self.Status.CANCELLED
        self.cancelled_at = timezone.now()
        self.save(update_fields=["status", "cancelled_at"])


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    batch = models.ForeignKey(TicketBatch, on_delete=models.PROTECT, related_name="items")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    # Preço no momento da compra: mudar o preço do lote depois não altera pedidos antigos.
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "item do pedido"
        verbose_name_plural = "itens do pedido"

    def __str__(self):
        return f"{self.quantity} x {self.batch.name}"

    @property
    def subtotal(self):
        return self.unit_price * self.quantity
