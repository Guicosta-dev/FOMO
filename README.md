# FOMO — plataforma de venda de ingressos para eventos

> Descubra shows, festivais e experiências perto de você. Produtores criam eventos e lotes de ingressos; compradores reservam e acompanham seus pedidos.

**Demo online:** https://guidev017.pythonanywhere.com/

![Home do FOMO com carrossel de eventos em destaque](docs/screenshots/home.png)
![Catálogo com busca por CEP e distância](docs/screenshots/catalogo.png)

> ⚠️ Projeto acadêmico/de portfólio, desenvolvido em equipe. Ainda **não há pagamento real**: pedidos pagos ficam pendentes com reserva temporária, e a confirmação é simulada pelo painel admin.

---

## Funcionalidades

**Para quem compra**
- Catálogo público com eventos publicados que ainda não terminaram, com paginação e carrossel de destaques.
- Busca por texto (título, cidade e descrição) e **busca por proximidade**: informe seu CEP e um raio (5, 10, 25, 50 ou 100 km) e veja os eventos mais perto primeiro.
- Página do evento com lotes de ingressos e compra por quantidade.
- Pedidos com **reserva de 15 minutos** para pedidos pagos; pedidos gratuitos já nascem confirmados.
- Histórico de pedidos, detalhe do pedido e cancelamento de pedidos pendentes.

**Para quem produz (painel do produtor)**
- Criar, editar, publicar, despublicar e excluir eventos, com imagem de capa.
- Localização preenchida automaticamente pelo CEP (cidade, UF, bairro e coordenadas).
- Lotes de ingressos com preço, quantidade, limite por pedido e janela de vendas.
- Regras de publicação: o evento precisa ter pelo menos um lote ativo e não pode já ter terminado.

**Administração**
- Django Admin com ações para buscar localização pelo CEP e confirmar pedidos (simula o pagamento).

## Decisões técnicas

- **Venda segura contra excesso de capacidade:** a criação do pedido roda em transação atômica e trava as linhas dos lotes (`select_for_update`) antes de conferir o estoque. O estoque considera pedidos confirmados e pedidos pendentes ainda dentro do prazo de reserva. *(No SQLite o travamento é ignorado; a proteção completa vale em PostgreSQL.)*
- **Preço congelado no pedido:** `OrderItem.unit_price` guarda o valor no momento da compra, então mudar o lote depois não altera pedidos antigos.
- **Controle de acesso no servidor:** evento, lote ou pedido de outra pessoa responde 404; ações destrutivas aceitam somente POST; o produtor do evento nunca vem do formulário.
- **Pedidos com UUID público** nas URLs, para ninguém "adivinhar" números de pedido.
- **Consulta de CEP resiliente:** feita no servidor (BrasilAPI v2) com timeout e cache; se o serviço falhar, o evento é salvo mesmo assim e o produtor é avisado.
- **Regras de negócio fora das views:** a lógica de pedidos fica em `app/services.py`, mais fácil de testar.
- **Usuário customizado** (`produtor` ou `consumidor`) definido antes da primeira migração.
- **Segredos fora do código:** configuração por variáveis de ambiente (`.env`).

## Stack

Python · Django · SQLite (desenvolvimento) · Pillow · python-dotenv · Bootstrap 5 e Swiper (servidos localmente) · BrasilAPI (CEP) · deploy no PythonAnywhere.

## Como rodar localmente

```bash
# 1. Clone e entre na pasta
git clone https://github.com/Guicosta-dev/FOMO.git
cd FOMO

# 2. Ambiente virtual
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Dependências
pip install -r requirements.txt

# 4. Variáveis de ambiente
cp .env.example .env            # Windows: copy .env.example .env
# edite o .env (para desenvolvimento, deixe DJANGO_DEBUG=True)

# 5. Banco de dados e usuário admin
python manage.py migrate
python manage.py createsuperuser

# 6. Servidor
python manage.py runserver
```

Acesse http://127.0.0.1:8000/. Para ver o painel do produtor, cadastre-se escolhendo o tipo de conta **Produtor**, crie um evento, adicione um lote ativo e publique.

### Testes

```bash
python manage.py test
```

Os testes cobrem contas e permissões, regras de eventos e lotes, criação e cancelamento de pedidos e a integração de CEP.

## Estrutura

```
FOMO/
├── config/        # configurações do projeto (settings, urls, wsgi)
├── app/
│   ├── models.py  # User, Event, TicketBatch, Order, OrderItem
│   ├── services.py# regras de negócio de pedidos
│   ├── cep.py     # consulta de CEP (BrasilAPI) com cache
│   ├── geo.py     # distância (haversine) e bounding box
│   ├── views.py, forms.py, admin.py, urls.py
│   ├── static/    # CSS, JS e libs locais
│   └── test_*.py, tests.py
├── templates/     # páginas (catálogo, painel, pedidos, contas)
└── manage.py
```

## Próximos passos

- Integrar pagamento real (hoje a confirmação é simulada).
- Migrar para PostgreSQL.
- Definir a política de cancelamento de pedidos já confirmados.
- Separar o app único em módulos (contas, eventos, pedidos).

