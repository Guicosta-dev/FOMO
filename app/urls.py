from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "app"

urlpatterns = [
    # Área pública
    path("", views.CatalogView.as_view(), name="catalog"),
    path("eventos/<slug:slug>/", views.EventDetailView.as_view(), name="event_detail"),
    # Contas
    path("contas/cadastro/", views.SignUpView.as_view(), name="signup"),
    path("contas/entrar/", views.RoleAwareLoginView.as_view(), name="login"),
    path("contas/sair/", LogoutView.as_view(), name="logout"),  # exige POST (Django 5)
    # Painel do produtor
    path("painel/", views.DashboardView.as_view(), name="dashboard"),
    path("painel/eventos/novo/", views.EventCreateView.as_view(), name="event_create"),
    path("painel/eventos/<int:pk>/editar/", views.EventUpdateView.as_view(), name="event_update"),
    path("painel/eventos/<int:pk>/publicar/", views.EventPublishView.as_view(), name="event_publish"),
    path("painel/eventos/<int:pk>/despublicar/", views.EventUnpublishView.as_view(), name="event_unpublish"),
]
