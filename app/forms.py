"""Formulários compatíveis com CONTRATO_BACKEND_FRONTEND.md."""

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone
from .models import Event, TicketBatch, User

DATE_FORMAT = "%Y-%m-%dT%H:%M"


class SignupForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ["username", "email", "role", "password1", "password2"]
        labels = {"role": "Como você quer usar o FOMO?"}

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Este e-mail já está cadastrado.")
        return email

    def clean_username(self):
        name = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=name).exists():
            raise forms.ValidationError("Este nome de usuário já está em uso.")
        return name


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            "title",
            "description",
            "venue_name",
            "address",
            "city",
            "starts_at",
            "ends_at",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "starts_at": forms.DateTimeInput(
                format=DATE_FORMAT, attrs={"type": "datetime-local"}
            ),
            "ends_at": forms.DateTimeInput(
                format=DATE_FORMAT, attrs={"type": "datetime-local"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ["starts_at", "ends_at"]:
            self.fields[name].input_formats = [DATE_FORMAT, "%Y-%m-%dT%H:%M:%S"]

    def clean_starts_at(self):
        value = self.cleaned_data["starts_at"]
        if value <= timezone.now():
            raise forms.ValidationError("O evento deve começar em uma data futura.")
        return value


class BatchForm(forms.ModelForm):
    class Meta:
        model = TicketBatch
        fields = [
            "name",
            "price",
            "quantity",
            "max_per_order",
            "sales_start",
            "sales_end",
            "is_active",
        ]
        widgets = {
            "price": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "quantity": forms.NumberInput(attrs={"min": "1"}),
            "max_per_order": forms.NumberInput(attrs={"min": "1"}),
            "sales_start": forms.DateTimeInput(
                format=DATE_FORMAT, attrs={"type": "datetime-local"}
            ),
            "sales_end": forms.DateTimeInput(
                format=DATE_FORMAT, attrs={"type": "datetime-local"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ["sales_start", "sales_end"]:
            self.fields[name].input_formats = [DATE_FORMAT, "%Y-%m-%dT%H:%M:%S"]


class TicketOrderForm(forms.Form):
    """Um campo qty_<pk> para cada lote ativo do evento."""

    def __init__(self, *args, batches, **kwargs):
        super().__init__(*args, **kwargs)
        self.batches = list(batches)
        for batch in self.batches:
            name = f"qty_{batch.pk}"
            self.fields[name] = forms.IntegerField(
                label=batch.name,
                required=False,
                min_value=0,
                max_value=batch.max_per_order,
                initial=0,
                help_text=f"{batch.available} disponíveis · limite de {batch.max_per_order} por pedido",
                widget=forms.NumberInput(
                    attrs={
                        "min": "0",
                        "max": min(batch.max_per_order, batch.available),
                        "step": "1",
                        "data-batch-price": str(batch.price),
                    }
                ),
            )
            if not batch.on_sale:
                self.fields[name].disabled = True
                self.fields[name].help_text = "Vendas fechadas ou lote esgotado."

    def clean(self):
        cleaned = super().clean()
        expected = {f"qty_{batch.pk}" for batch in self.batches}
        if any(key.startswith("qty_") and key not in expected for key in self.data):
            raise forms.ValidationError(
                "Um dos lotes enviados não pertence a este evento."
            )
        for batch in self.batches:
            name = f"qty_{batch.pk}"
            if self.fields[name].disabled and self.data.get(name, "0") not in [
                "",
                "0",
                0,
                None,
            ]:
                raise forms.ValidationError(
                    f'O lote "{batch.name}" está com vendas fechadas ou esgotado.'
                )
        if not self.errors and not any((value or 0) > 0 for value in cleaned.values()):
            raise forms.ValidationError("Selecione pelo menos um ingresso.")
        return cleaned

    def quantities(self):
        return {
            batch.pk: self.cleaned_data.get(f"qty_{batch.pk}") or 0
            for batch in self.batches
        }
