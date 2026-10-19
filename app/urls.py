from django.contrib.auth.views import LogoutView
from django.urls import path
from django.views.generic import TemplateView
from django.conf import settings
from django.conf.urls.static import static

from . import views

app_name = "app"

urlpatterns = [
    # Área pública
    path("", views.CatalogView.as_view(), name="catalog"),
    path("eventos/<slug:slug>/", views.EventDetailView.as_view(), name="event_detail"),
    # Pedidos (comprador)
    path("eventos/<slug:slug>/comprar/", views.OrderCreateView.as_view(), name="order_create"),
    path("meus-pedidos/", views.OrderListView.as_view(), name="order_list"),
    path("pedidos/<uuid:public_id>/", views.OrderDetailView.as_view(), name="order_detail"),
    path("pedidos/<uuid:public_id>/cancelar/", views.OrderCancelView.as_view(), name="order_cancel"),
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
    # Lotes (produtor)
    path("painel/eventos/<int:event_pk>/lotes/", views.BatchListView.as_view(), name="batch_list"),
    path("painel/eventos/<int:event_pk>/lotes/novo/", views.BatchCreateView.as_view(), name="batch_create"),
    path("painel/lotes/<int:pk>/editar/", views.BatchUpdateView.as_view(), name="batch_update"),
    path("sobre/", TemplateView.as_view(template_name="about.html", extra_context={"active_page": "about"}), name="about"),
]+ static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
