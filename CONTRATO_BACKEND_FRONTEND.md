# Contrato entre backend e frontend (FOMO)

O backend já entrega as páginas com dados, formulários, permissões e URLs.
O frontend precisa criar os arquivos HTML abaixo na pasta **`templates/`**
(na raiz do projeto, ao lado do `manage.py`). Os nomes e as variáveis abaixo
são o que o backend usa e **não devem mudar sem combinar com o backend**.

Existe um exemplo funcional (feio, só Bootstrap) em `opcional/templates_provisorios/`
que pode ser usado como ponto de partida.

## Regras gerais

- Todo `<form method="post">` precisa de `{% csrf_token %}`.
- **Sair (logout), publicar e despublicar são POST**: precisam ser um `<form>` com
  botão, não um link.
- Mensagens de sucesso/erro chegam em `messages` (`{% for message in messages %}`).
  `message.tags` vale `success`, `danger`, etc.
- Em todas as páginas existem `user` (usuário logado ou anônimo) e `user.is_producer`.
- Datas: `{{ valor|date:"d/m/Y H:i" }}` (já vêm no fuso America/Sao_Paulo).

## Páginas

| Arquivo | Variáveis disponíveis |
|---|---|
| `base.html` | `user`, `messages` |
| `accounts/login.html` | `form` (campos `username`, `password`) |
| `accounts/signup.html` | `form` (campos `username`, `email`, `role`, `password1`, `password2`) |
| `events/catalog.html` | `events` (lista da página), `page_obj`, `paginator`, `is_paginated`, `q` (texto buscado) |
| `events/event_detail.html` | `event`, `batches` (lotes ativos), `order_form` (veja "Compra de ingressos") |
| `dashboard/event_list.html` | `events` (só os do produtor logado) |
| `dashboard/event_form.html` | `form`; `object` (vazio ao criar, o evento ao editar) |
| `dashboard/batch_list.html` | `event`, `batches` (lotes do evento, com vendidos/disponíveis) |
| `dashboard/batch_form.html` | `form`, `event`; `object` (vazio ao criar, o lote ao editar) |
| `orders/order_list.html` | `orders` (pedidos do usuário logado, do mais novo ao mais antigo) |
| `orders/order_detail.html` | `order` (com `order.items.all`) |

Atenção: **só dá para publicar um evento que tenha pelo menos um lote ativo**. No painel,
`event.has_active_batch` diz se pode; sem ele, o botão Publicar deve avisar "crie um lote"
e levar a `{% url 'app:batch_list' event.pk %}`. Se o produtor clicar mesmo assim, o
backend recusa e manda a explicação em `messages`.

Campos de um evento: `title`, `slug`, `description`, `venue_name`, `address`,
`city`, `starts_at`, `ends_at` (pode ser vazio), `status`, `get_status_display`,
`is_published`, `has_active_batch`, `published_at`, `producer` (`producer.username`,
`producer.get_full_name`), `pk`, `get_absolute_url`.

Formulário de evento (`dashboard/event_form.html`): renderize com `{{ form.as_div }}`
ou campo a campo (`form.title`, `form.description`, `form.venue_name`, `form.address`,
`form.city`, `form.starts_at`, `form.ends_at`). Mostre os erros de cada campo
(`form.title.errors`, etc.). Os campos de data já vêm como `datetime-local`.

Campos de um lote (`batch`): `name`, `price`, `quantity` (total), `available`
(disponíveis agora), `sold` (confirmados), `taken` (confirmados + reservados),
`max_per_order`, `sales_start`, `sales_end`, `is_active`, `on_sale` (aberto e com estoque), `pk`.

Campos de um pedido (`order`): `public_id`, `short_code` (código curto para exibir),
`status` (`pending`, `confirmed`, `cancelled`, `expired`), `get_status_display`, `total`,
`created_at`, `expires_at` (fim da reserva, só nos pendentes), `confirmed_at`, `event`,
`items.all` (cada item: `batch.name`, `quantity`, `unit_price`, `subtotal`).

## Compra de ingressos

Na página do evento, o comprador escolhe quantidades e envia um **POST** para
`{% url 'app:order_create' event.slug %}` com um campo por lote chamado
**`qty_<id do lote>`** (ex.: `qty_7`). O jeito mais simples é percorrer `order_form`:

```html
<form method="post" action="{% url 'app:order_create' event.slug %}">
  {% csrf_token %}
  {% for batch in batches %}
    <label>{{ batch.name }} — R$ {{ batch.price }} ({{ batch.available }} disponíveis)</label>
    <input type="number" name="qty_{{ batch.pk }}" min="0" max="{{ batch.max_per_order }}" value="0">
  {% endfor %}
  <button type="submit">Comprar</button>
</form>
```

Visitante não logado é mandado para o login e volta depois. Erros de regra
(esgotado, limite por pedido, vendas fechadas...) chegam em `messages` e o usuário
volta para a página do evento.

## URLs (use sempre `{% url %}`, nunca o caminho escrito à mão)

| Nome | Caminho | Método |
|---|---|---|
| `app:catalog` | `/` (aceita `?q=` e `?page=`) | GET |
| `app:event_detail` slug | `/eventos/<slug>/` | GET |
| `app:signup` | `/contas/cadastro/` | GET/POST |
| `app:login` | `/contas/entrar/` | GET/POST |
| `app:logout` | `/contas/sair/` | **POST** |
| `app:dashboard` | `/painel/` | GET |
| `app:event_create` | `/painel/eventos/novo/` | GET/POST |
| `app:event_update` pk | `/painel/eventos/<pk>/editar/` | GET/POST |
| `app:event_publish` pk | `/painel/eventos/<pk>/publicar/` | **POST** |
| `app:event_unpublish` pk | `/painel/eventos/<pk>/despublicar/` | **POST** |
| `app:batch_list` event_pk | `/painel/eventos/<event_pk>/lotes/` | GET |
| `app:batch_create` event_pk | `/painel/eventos/<event_pk>/lotes/novo/` | GET/POST |
| `app:batch_update` pk | `/painel/lotes/<pk>/editar/` | GET/POST |
| `app:order_create` slug | `/eventos/<slug>/comprar/` | **POST** |
| `app:order_list` | `/meus-pedidos/` | GET |
| `app:order_detail` public_id | `/pedidos/<uuid>/` | GET |
| `app:order_cancel` public_id | `/pedidos/<uuid>/cancelar/` | **POST** |

Exemplo de botão de publicar:

```html
<form method="post" action="{% url 'app:event_publish' event.pk %}">
  {% csrf_token %}
  <button type="submit">Publicar</button>
</form>
```
