# FOMO — Django com backend e frontend integrados

Esta versão implementa o `CONTRATO_BACKEND_FRONTEND.md` enviado e mantém o design preto, laranja e violeta `#8B5CF6`. Os cadastros, as sessões, os eventos, os lotes, os favoritos e os pedidos agora usam o banco de dados. Não há eventos ou contas de demonstração pré-cadastrados.

## Instalar no Windows pelo CMD

Use Python 3.10 ou superior. Extraia o ZIP em uma pasta nova e entre em `FOMO_frontend`, onde está o `manage.py`.

```bat
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Abra `http://127.0.0.1:8000/`. O painel administrativo fica em `http://127.0.0.1:8000/admin/`.

Ao criar o superusuário, escolha seu nome, e-mail e senha. Nenhuma credencial vem pronta no pacote. Se já tiver um ambiente virtual ativo com as dependências corretas, pode começar na instalação dos requisitos.

Esta versão tem um modelo de usuário próprio. Use as migrations incluídas em um banco novo; não substitua o banco de outro projeto Django que já use outro modelo de usuário.

A versão anterior era uma prévia sem banco. Agora as páginas devem ser abertas pelo servidor Django, e não diretamente como arquivos HTML. A prévia estática antiga foi removida para não confundir seus botões com as ações reais.

## Usar como produtor

1. No site, escolha **Criar conta** e selecione o perfil **Produtor**.
2. Acesse **Gerenciar eventos** no menu da conta, ou `/painel/`.
3. Crie um evento. O cadastro começa como rascunho.
4. Crie pelo menos um lote ativo com preço, quantidade, limite por pedido e datas de venda.
5. Clique **Publicar evento**. Ele passa a aparecer no catálogo.
6. Edite evento e lotes pelo painel. **Despublicar** retira o evento do catálogo sem apagar seus pedidos.

Cada produtor vê e altera apenas seus próprios eventos e lotes. Para excluir um evento pelo site, ele precisa estar despublicado e não ter pedidos. Eventos com pedidos ficam preservados no histórico.

O valor 0 cria um lote gratuito. Os horários dos formulários e das páginas usam `America/Sao_Paulo`.

## Usar como comprador

1. Crie uma conta com perfil **Comprador**, ou faça login em uma conta existente.
2. Abra um evento publicado.
3. Escolha a quantidade de cada lote e clique **Reservar ingressos**.
4. Consulte o pedido em **Meus pedidos**.

Um pedido começa com status `pending` e reserva ingressos por até 20 minutos. O prazo é reduzido quando o evento ou a venda do lote termina antes. Pedidos pendentes podem ser cancelados pelo comprador; isso devolve os ingressos ao estoque. Pedidos confirmados não podem ser cancelados pelo comprador nesta implementação.

O total é recalculado no servidor com os preços atuais dos lotes. Valores enviados pelo navegador não determinam o preço. Os itens guardam o preço da compra, mesmo se o produtor alterar o lote depois.

## Confirmação dos pedidos e pagamento

O contrato não define um provedor de pagamento nem uma rota de confirmação pelo comprador. Esta versão **não cobra cartão, não gera Pix e não verifica pagamento externo**.

Para usar o fluxo administrativo de demonstração:

1. Entre em `/admin/` com o superusuário.
2. Abra **Pedidos**.
3. Selecione um pedido pendente, ainda dentro do prazo.
4. Escolha **Confirmar pedidos pendentes (modo administrativo)**.

A confirmação muda o pedido para `confirmed` e transfere os ingressos de reservados para vendidos. Repetir a confirmação não contabiliza ingressos duas vezes. Também existe uma ação administrativa para cancelar reservas pendentes.

Essa confirmação administrativa representa apenas o estado do sistema; não comprova recebimento de dinheiro. Para vendas com cobrança online, a próxima integração deve confirmar pedidos somente após a verificação do provedor de pagamento, usando as mesmas regras transacionais de `app/services.py`.

## Expiração de reservas

Reservas vencidas são liberadas ao acessar páginas relevantes e antes de novas compras. Para processar todas as reservas vencidas, inclusive sem tráfego no site:

```bat
python manage.py expire_orders
```

Na hospedagem, configure o agendador do servidor para executar esse comando periodicamente. O código não inicia um processo de agendamento próprio.

## Organização dos arquivos

| Caminho | Responsabilidade |
| --- | --- |
| `app/models.py` | User, Event, TicketBatch, Order, OrderItem e Favorite |
| `app/forms.py` | Cadastro, evento, lote e quantidades `qty_<id>` |
| `app/views.py` | Busca, contas, painel, lotes, pedidos e permissões |
| `app/services.py` | Reserva, confirmação, cancelamento e expiração de estoque |
| `app/urls.py` | URLs no namespace `app`, conforme o contrato |
| `app/admin.py` | Busca, filtros e ações administrativas |
| `app/middleware.py` | `user.is_producer` disponível também para visitantes |
| `app/migrations/` | Estrutura inicial do banco e suas restrições |
| `app/tests.py` | Testes funcionais e concorrência de reservas |
| `templates/` | Templates na raiz, exatamente como o contrato solicita |
| `templates/accounts/` | `login.html` e `signup.html` |
| `templates/events/` | `catalog.html` e `event_detail.html` |
| `templates/dashboard/` | Eventos, formulários e lotes do produtor |
| `templates/orders/` | Lista e detalhe dos pedidos |
| `templates/includes/` | Cards, ícones, banners e campos reutilizáveis |
| `app/static/style.css` | Tema e componentes do FOMO |
| `app/static/js/script.js` | Comportamentos de interface e total estimado dos lotes |
| `config/settings.py` | SQLite, autenticação, idioma e configurações por ambiente |
| `CONTRATO_BACKEND_FRONTEND.md` | Cópia do documento que orientou a implementação |

Os nomes obrigatórios de templates, contextos, URLs e campos do contrato foram preservados. O catálogo recebe `events`, `page_obj`, `paginator`, `is_paginated` e `q`; o detalhe recebe `event`, `batches` e `order_form`; os formulários recebem `form` e `object`, quando aplicável.

Os atalhos de categorias da home usam o parâmetro de busca `q`; eles pesquisam os textos reais dos eventos. Não foi criado um modelo de categorias, pois ele não faz parte do contrato. A busca considera título, descrição, local e cidade; a paginação mantém o termo buscado.

Além das rotas obrigatórias, foram preservados favoritos, página Sobre e exclusão segura de eventos como recursos adicionais do frontend.

## Estoque e permissões

- `sold` conta ingressos confirmados; `taken` inclui confirmados e reservados; `available` é o restante.
- A reserva verifica publicação, período de venda, quantidade e limite por pedido no servidor.
- Os ajustes de estoque são feitos em transações. O SQLite está configurado com `IMMEDIATE`, e a atualização do contador também verifica a disponibilidade no próprio banco.
- Publicar exige lote ativo. Logout, publicação, despublicação, compra e cancelamento exigem POST com CSRF.
- Somente o titular pode listar, consultar e cancelar seus pedidos. O UUID na URL não substitui a autorização.
- Campos como produtor, comprador, preços e status do pedido são definidos pelo backend, não por dados confiados ao cliente.
- Ações de confirmação e alteração manual de pedidos ficam no admin; os campos de estoque e estado dos pedidos são somente leitura ali.

## Executar os testes

```bat
python manage.py test --noinput
python manage.py check
```

A suíte usa um banco de teste separado. Ela cobre contrato de URLs, autenticação, papéis, propriedade, busca, CSRF, publicação com lote ativo, preços, limites, cancelamento, expiração, confirmação idempotente e disputa concorrente pelo último ingresso.

## Configuração na hospedagem

O desenvolvimento usa SQLite e `runserver`. Para hospedagem, use o WSGI em `config/wsgi.py` ou o ASGI em `config/asgi.py` e as configurações da plataforma escolhida.

Variáveis suportadas:

| Variável | Uso |
| --- | --- |
| `DJANGO_DEBUG` | Use `0` em produção |
| `DJANGO_SECRET_KEY` | Chave estável e privada, obrigatória com DEBUG desativado |
| `DJANGO_ALLOWED_HOSTS` | Domínios autorizados, separados por vírgula |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Origens HTTPS autorizadas, separadas por vírgula |
| `DJANGO_SSL_REDIRECT` | Use `1` quando o ambiente e o proxy HTTPS estiverem configurados |
| `FOMO_DB_PATH` | Caminho do banco SQLite; padrão: `db.sqlite3` na raiz |

Rode `python manage.py collectstatic --noinput` e configure a hospedagem para servir a pasta gerada. O pacote não contém contas, banco, chave local, ambiente virtual nem arquivos de teste gerados. A chave de desenvolvimento é criada localmente na primeira execução e persiste para manter as sessões entre reinícios.

## Paleta e imagens

A marca usa preto `#080808`, laranja `#FF641E` e violeta `#8B5CF6`, com violeta claro `#C4B5FD` para detalhes legíveis. A paleta está no início de `app/static/style.css`.

A imagem laranja da home e a fotografia violeta enviada permanecem incluídas. Bootstrap 5.3.8 e sua licença MIT estão em `app/static/vendor/`.
