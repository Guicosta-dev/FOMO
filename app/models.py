"""Contas, eventos, lotes, pedidos e favoritos do FOMO."""

from decimal import Decimal
import uuid
from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify


class FomoUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", "producer")
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        BUYER = "buyer", "Comprador"
        PRODUCER = "producer", "Produtor"

    email = models.EmailField("e-mail", unique=True)
    role = models.CharField(
        "perfil", max_length=10, choices=Role.choices, default=Role.BUYER
    )
    objects = FomoUserManager()
    REQUIRED_FIELDS = ["email"]

    @property
    def is_producer(self):
        return self.role == self.Role.PRODUCER

    def clean(self):
        super().clean()
        self.email = self.email.strip().lower()

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        return super().save(*args, **kwargs)


class Event(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Rascunho"
        PUBLISHED = "published", "Publicado"

    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="events",
        verbose_name="produtor",
    )
    title = models.CharField("título", max_length=160)
    slug = models.SlugField(max_length=200, unique=True, editable=False)
    description = models.TextField("descrição")
    venue_name = models.CharField("local", max_length=180)
    address = models.CharField("endereço", max_length=250)
    city = models.CharField("cidade", max_length=120)
    starts_at = models.DateTimeField("início")
    ends_at = models.DateTimeField("término", null=True, blank=True)
    status = models.CharField(
        "status", max_length=12, choices=Status.choices, default=Status.DRAFT
    )
    published_at = models.DateTimeField("publicado em", null=True, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        ordering = ["starts_at", "pk"]
        verbose_name = "evento"
        verbose_name_plural = "eventos"
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(ends_at__gt=F("starts_at")),
                name="event_valid_dates",
            )
        ]

    def __str__(self):
        return self.title

    @property
    def is_published(self):
        return self.status == self.Status.PUBLISHED

    @property
    def has_active_batch(self):
        return self.pk is not None and self.batches.filter(is_active=True).exists()

    @property
    def starting_price(self):
        value = self.batches.filter(is_active=True).aggregate(
            value=models.Min("price")
        )["value"]
        return value

    def get_absolute_url(self):
        return reverse("app:event_detail", kwargs={"slug": self.slug})

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "O término deve ser depois do início."})
        if self.producer_id and not self.producer.is_producer:
            raise ValidationError(
                {"producer": "O organizador precisa ter perfil de produtor."}
            )
        if self.is_published and not self.has_active_batch:
            raise ValidationError("Crie um lote ativo antes de publicar o evento.")

    def save(self, *args, **kwargs):
        if self.is_published and not self.has_active_batch:
            raise ValidationError("Crie um lote ativo antes de publicar o evento.")
        if not self.slug:
            base = slugify(self.title)[:155] or "evento"
            self.slug = f"{base}-{uuid.uuid4().hex[:12]}"
        return super().save(*args, **kwargs)


class TicketBatch(models.Model):
    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name="batches", verbose_name="evento"
    )
    name = models.CharField("nome do lote", max_length=100)
    price = models.DecimalField(
        "preço",
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    quantity = models.PositiveIntegerField(
        "quantidade total", validators=[MinValueValidator(1)]
    )
    sold = models.PositiveIntegerField("confirmados", default=0, editable=False)
    reserved = models.PositiveIntegerField("reservados", default=0, editable=False)
    max_per_order = models.PositiveIntegerField(
        "limite por pedido", default=10, validators=[MinValueValidator(1)]
    )
    sales_start = models.DateTimeField("início das vendas", default=timezone.now)
    sales_end = models.DateTimeField("fim das vendas", null=True, blank=True)
    is_active = models.BooleanField("ativo", default=True)

    class Meta:
        ordering = ["price", "pk"]
        verbose_name = "lote"
        verbose_name_plural = "lotes"
        constraints = [
            models.CheckConstraint(
                condition=Q(price__gte=0), name="batch_nonnegative_price"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gte=1), name="batch_positive_quantity"
            ),
            models.CheckConstraint(
                condition=Q(max_per_order__gte=1), name="batch_positive_limit"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gte=F("sold") + F("reserved")),
                name="batch_stock_capacity",
            ),
            models.CheckConstraint(
                condition=Q(sales_end__isnull=True) | Q(sales_end__gt=F("sales_start")),
                name="batch_valid_dates",
            ),
        ]

    def __str__(self):
        return f"{self.event.title} - {self.name}"

    @property
    def taken(self):
        return self.sold + self.reserved

    @property
    def available(self):
        return self.quantity - self.taken

    @property
    def on_sale(self):
        now = timezone.now()
        return (
            self.is_active
            and self.event.is_published
            and self.available > 0
            and self.sales_start <= now < self.event.starts_at
            and (self.sales_end is None or now < self.sales_end)
        )

    def clean(self):
        if self.sales_end and self.sales_start and self.sales_end <= self.sales_start:
            raise ValidationError(
                {"sales_end": "O fim das vendas deve ser depois do início."}
            )
        if self.quantity is not None and self.quantity < self.taken:
            raise ValidationError(
                {
                    "quantity": "O total não pode ser menor que os ingressos confirmados e reservados."
                }
            )
        if (
            self.event_id
            and self.sales_start
            and self.sales_start >= self.event.starts_at
        ):
            raise ValidationError(
                {"sales_start": "As vendas devem começar antes do evento."}
            )
        if self.event_id and self.sales_end and self.sales_end > self.event.starts_at:
            raise ValidationError(
                {"sales_end": "As vendas devem terminar até o início do evento."}
            )


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        CONFIRMED = "confirmed", "Confirmado"
        CANCELLED = "cancelled", "Cancelado"
        EXPIRED = "expired", "Expirado"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="comprador",
    )
    event = models.ForeignKey(
        Event, on_delete=models.PROTECT, related_name="orders", verbose_name="evento"
    )
    status = models.CharField(
        "status", max_length=12, choices=Status.choices, default=Status.PENDING
    )
    total = models.DecimalField(
        "total", max_digits=14, decimal_places=2, default=Decimal("0")
    )
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    expires_at = models.DateTimeField("reserva até", null=True, blank=True)
    confirmed_at = models.DateTimeField("confirmado em", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "pedido"
        verbose_name_plural = "pedidos"
        constraints = [
            models.CheckConstraint(
                condition=Q(total__gte=0), name="order_nonnegative_total"
            ),
            models.CheckConstraint(
                condition=~Q(status="pending") | Q(expires_at__isnull=False),
                name="pending_order_has_deadline",
            ),
            models.CheckConstraint(
                condition=Q(
                    status__in=["pending", "confirmed", "cancelled", "expired"]
                ),
                name="order_valid_status",
            ),
        ]
        indexes = [models.Index(fields=["status", "expires_at"])]

    def __str__(self):
        return self.short_code

    @property
    def short_code(self):
        return str(self.public_id).split("-")[0].upper()

    def get_absolute_url(self):
        return reverse("app:order_detail", kwargs={"public_id": self.public_id})


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="items", verbose_name="pedido"
    )
    batch = models.ForeignKey(
        TicketBatch,
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name="lote",
    )
    quantity = models.PositiveIntegerField(
        "quantidade", validators=[MinValueValidator(1)]
    )
    unit_price = models.DecimalField("preço unitário", max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "item do pedido"
        verbose_name_plural = "itens do pedido"
        constraints = [
            models.UniqueConstraint(
                fields=["order", "batch"], name="unique_order_batch"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gte=1), name="item_positive_quantity"
            ),
            models.CheckConstraint(
                condition=Q(unit_price__gte=0), name="item_nonnegative_price"
            ),
        ]

    @property
    def subtotal(self):
        return self.quantity * self.unit_price


class Favorite(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorites"
    )
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="favorites")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "event"], name="unique_favorite")
        ]
        verbose_name = "favorito"
        verbose_name_plural = "favoritos"
