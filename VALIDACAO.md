# Validação da entrega

- 39 testes automatizados aprovados.
- Contrato de URLs, templates e contextos conferido.
- Permissões por perfil e por propriedade dos registros testadas.
- CSRF e métodos POST obrigatórios testados.
- Reservas, estoque, preços, limites, cancelamento, expiração e confirmação testados.
- Teste de dois compradores disputando o último ingresso aprovado em SQLite com arquivo de banco.
- Migrations aplicadas a um banco novo; nenhum usuário ou evento de demonstração incluído.
- Todos os templates compilam e o JavaScript passa na verificação de sintaxe.
- A confirmação de pedidos é administrativa; não há integração de cobrança online.

Comandos de validação: `python manage.py test --noinput`, `python manage.py check`, `python manage.py makemigrations --check --dry-run`.
