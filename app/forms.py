from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import Event, User

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
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_LOCAL),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_LOCAL),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("starts_at", "ends_at"):
            self.fields[name].input_formats = [DATETIME_LOCAL, "%Y-%m-%dT%H:%M:%S"]
