from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .cep import normalize_cep
from .models import Event, TicketBatch, User

DATETIME_LOCAL = "%Y-%m-%dT%H:%M"  # formato do <input type="datetime-local">


class BootstrapFormMixin:
    """Aplica as classes CSS do Bootstrap em todos os campos do formulário."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {css}".strip()


class SignUpForm(BootstrapFormMixin, UserCreationForm):
    email = forms.EmailField(label="E-mail", required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "role")


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    pass


class EventForm(BootstrapFormMixin, forms.ModelForm):
    """Formulário do produtor.

    Só expõe campos de conteúdo. 'producer', 'status' e 'slug' ficam de fora de
    propósito: quem define são as views, nunca o navegador.
    """

    # Declarado aqui (e não só no modelo) para aceitar "20040-020" e guardar "20040020".
    cep = forms.CharField(
        label="CEP do local",
        required=False,
        max_length=9,
        help_text="Com o CEP, o evento aparece nas buscas por proximidade.",
    )

    class Meta:
        model = Event
        fields = [
            "title",
            "description",
            "venue_name",
            "cep",
            "address",
            "city",
            "starts_at",
            "ends_at",
            "image"
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_LOCAL),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_LOCAL),
            "image": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("starts_at", "ends_at"):
            self.fields[name].input_formats = [DATETIME_LOCAL, "%Y-%m-%dT%H:%M:%S"]

    def clean_cep(self):
        value = self.cleaned_data.get("cep", "").strip()
        if not value:
            return ""
        cep = normalize_cep(value)
        if cep is None:
            raise forms.ValidationError("CEP inválido. Informe 8 dígitos, ex.: 20040-020.")
        return cep


class BatchForm(BootstrapFormMixin, forms.ModelForm):
    """Formulário de lote do produtor. O evento é definido pela view, nunca pelo navegador."""

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
            "sales_start": forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_LOCAL),
            "sales_end": forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_LOCAL),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("sales_start", "sales_end"):
            self.fields[name].input_formats = [DATETIME_LOCAL, "%Y-%m-%dT%H:%M:%S"]
        # Checkbox não deve receber a classe "form-control".
        self.fields["is_active"].widget.attrs["class"] = "form-check-input"


class OrderForm(forms.Form):
    """Quantidades escolhidas pelo comprador: um campo `qty_<id_do_lote>` por lote."""

    def __init__(self, *args, batches, **kwargs):
        super().__init__(*args, **kwargs)
        self.batches = list(batches)
        for batch in self.batches:
            self.fields[f"qty_{batch.pk}"] = forms.IntegerField(
                label=batch.name, min_value=0, required=False, initial=0
            )

    def quantities(self):
        """Retorna {id_do_lote: quantidade} com os valores já validados."""
        return {
            batch.pk: self.cleaned_data.get(f"qty_{batch.pk}") or 0
            for batch in self.batches
        }
