"""URLs do contrato: nomes, argumentos e métodos preservados."""

from django.urls import path
from . import views

app_name = "app"
urlpatterns = [
    path("", views.catalog, name="catalog"),
    path("contas/cadastro/", views.signup, name="signup"),
    path("contas/entrar/", views.FomoLoginView.as_view(), name="login"),
    path("contas/sair/", views.FomoLogoutView.as_view(), name="logout"),
    path("eventos/<slug:slug>/", views.event_detail, name="event_detail"),
    path("eventos/<slug:slug>/comprar/", views.order_create, name="order_create"),
    path("painel/", views.dashboard, name="dashboard"),
    path("painel/eventos/novo/", views.event_create, name="event_create"),
    path("painel/eventos/<int:pk>/editar/", views.event_update, name="event_update"),
    path(
        "painel/eventos/<int:pk>/publicar/", views.event_publish, name="event_publish"
    ),
    path(
        "painel/eventos/<int:pk>/despublicar/",
        views.event_unpublish,
        name="event_unpublish",
    ),
    path("painel/eventos/<int:event_pk>/lotes/", views.batch_list, name="batch_list"),
    path(
        "painel/eventos/<int:event_pk>/lotes/novo/",
        views.batch_create,
        name="batch_create",
    ),
    path("painel/lotes/<int:pk>/editar/", views.batch_update, name="batch_update"),
    path("meus-pedidos/", views.order_list, name="order_list"),
    path("pedidos/<uuid:public_id>/", views.order_detail, name="order_detail"),
    path("pedidos/<uuid:public_id>/cancelar/", views.order_cancel, name="order_cancel"),
    # Recursos do frontend original, além das rotas obrigatórias do contrato.
    path("painel/eventos/<int:pk>/excluir/", views.event_delete, name="event_delete"),
    path("favoritos/", views.favorites, name="favorites"),
    path(
        "eventos/<slug:slug>/favoritar/", views.favorite_toggle, name="favorite_toggle"
    ),
    path("sobre/", views.about, name="about"),
]
