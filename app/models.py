from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
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

    def publish(self):
        if self.has_ended:
            raise ValidationError("Não é possível publicar um evento que já terminou.")
        self.status = self.Status.PUBLISHED
        if self.published_at is None:
            self.published_at = timezone.now()
        self.save(update_fields=["status", "published_at", "updated_at"])

    def unpublish(self):
        self.status = self.Status.DRAFT
        self.save(update_fields=["status", "updated_at"])


