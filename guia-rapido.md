# 🚀 Guia Rápido - Portal de Procedimentos

## 📋 Comandos Mais Usados

### Iniciar Aplicação
```bash
# Ativar ambiente virtual
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Executar aplicação
python run.py
```

### Migrações de Banco de Dados
```bash
# Ver status das migrações
flask db current

# Criar nova migração (após alterar models)
flask db migrate -m "Descrição da alteração"

# Aplicar migrações pendentes
flask db upgrade

# Reverter última migração
flask db downgrade

# Ver histórico
flask db history
```

### Gerenciamento de Dependências
```bash
# Instalar todas as dependências
pip install -r requirements.txt

# Adicionar nova dependência
pip install nome-do-pacote
pip freeze > requirements.txt

# Atualizar dependência
pip install --upgrade nome-do-pacote
```

### MySQL - Comandos Úteis
```sql
-- Conectar ao MySQL
mysql -u root -p

-- Usar banco de dados
USE cadastro_empresas;

-- Ver tabelas
SHOW TABLES;

-- Ver estrutura da tabela
DESCRIBE users;

-- Ver dados
SELECT * FROM users;

-- Backup
mysqldump -u root -p cadastro_empresas > backup.sql

-- Restaurar
mysql -u root -p cadastro_empresas < backup.sql
```

### Git - Fluxo de Trabalho
```bash
# Ver status
git status

# Criar nova branch
git checkout -b feature/nova-funcionalidade

# Adicionar arquivos
git add .

# Commit
git commit -m "Descrição clara da mudança"

# Push
git push origin feature/nova-funcionalidade

# Atualizar da main
git checkout main
git pull origin main
```

---

## 🔧 Troubleshooting Comum

### Erro: "ModuleNotFoundError"
```bash
# Solução: Instalar dependências faltantes
pip install -r requirements.txt
```

### Erro: "Access denied for user"
```bash
# Solução: Verificar credenciais no .env
# Testar conexão manualmente
mysql -u root -p
```

### Erro: "CSRF token missing"
```bash
# Solução: Verificar se o formulário tem {{ form.hidden_tag() }}
```

### Erro: "Table doesn't exist"
```bash
# Solução: Executar migrações
flask db upgrade
```

### Porta 5000 já em uso
```bash
# Solução: Mudar porta em run.py
app.run(debug=True, port=5001)
```

---

## 📝 Checklist de Deploy

### Desenvolvimento Local
- [ ] Ambiente virtual ativado
- [ ] Arquivo .env configurado
- [ ] Banco de dados criado
- [ ] Migrações aplicadas
- [ ] Dependências instaladas
- [ ] `debug=True` em run.py

### Produção
- [ ] `debug=False` em run.py
- [ ] SECRET_KEY forte e única
- [ ] Credenciais seguras no .env
- [ ] HTTPS configurado
- [ ] Backup automático configurado
- [ ] Logs configurados
- [ ] Servidor web (Nginx/Apache)
- [ ] WSGI server (Gunicorn/uWSGI)
- [ ] Firewall configurado

---

## 🎯 Estrutura de URLs

| Rota | Método | Descrição | Autenticação |
|------|--------|-----------|--------------|
| `/` | GET | Página inicial | Não |
| `/login` | GET, POST | Login de usuário | Não |
| `/register` | GET, POST | Cadastro de usuário | Não |
| `/users` | GET | Lista usuários | Sim (Admin) |
| `/test_connection` | GET | Testa conexão BD | Sim (Admin) |

---

## 🔐 Variáveis de Ambiente (.env)

```env
# Banco de Dados
DB_HOST=localhost          # Host do MySQL
DB_NAME=cadastro_empresas  # Nome do banco
DB_USER=root               # Usuário MySQL
DB_PASSWORD=sua_senha      # Senha MySQL

# Flask
SECRET_KEY=chave_aleatoria_muito_segura_aqui_min_32_chars

# Ambiente
FLASK_ENV=development      # development ou production
FLASK_DEBUG=1              # 1 = True, 0 = False
```

**Gerar SECRET_KEY segura:**
```python
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 📦 Requirements.txt Completo

```txt
Flask==2.3.0
Flask-SQLAlchemy==3.0.5
Flask-Migrate==4.0.4
Flask-WTF==1.1.1
WTForms==3.0.1
mysql-connector-python==8.0.33
python-dotenv==1.0.0
Werkzeug==2.3.0
email-validator==2.0.0
```

---

## 🎨 Padrão de Código

### Nomenclatura
```python
# Classes: PascalCase
class UserModel:
    pass

# Funções: snake_case
def get_user_by_id():
    pass

# Constantes: UPPER_SNAKE_CASE
MAX_LOGIN_ATTEMPTS = 5

# Variáveis: snake_case
user_name = "João"
```

### Templates Jinja2
```html
<!-- Herança de template -->
{% extends "base.html" %}

<!-- Blocos -->
{% block content %}
    <!-- Conteúdo aqui -->
{% endblock %}

<!-- Variáveis -->
{{ user.name }}

<!-- Condicionais -->
{% if user.is_active %}
    <p>Ativo</p>
{% else %}
    <p>Inativo</p>
{% endif %}

<!-- Loops -->
{% for item in items %}
    <li>{{ item }}</li>
{% endfor %}

<!-- URLs -->
<a href="{{ url_for('login') }}">Login</a>
```

---

## 🐛 Debug e Testes

### Flask Shell
```bash
# Abrir shell interativo
flask shell

# Dentro do shell:
>>> from app import db
>>> from app.models.tables import User
>>> users = User.query.all()
>>> print(users)
```

### Testar Rotas Manualmente
```python
# No Python shell ou arquivo de teste
import requests

# Testar GET
response = requests.get('http://localhost:5000/')
print(response.status_code)

# Testar POST
data = {'username': 'teste', 'password': '123456'}
response = requests.post('http://localhost:5000/login', data=data)
```

### Logs Personalizados
```python
# Adicionar em qualquer arquivo
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Usar nos controllers
logger.info("Usuário fez login")
logger.error("Erro ao salvar no banco")
logger.debug(f"Dados recebidos: {form.data}")
```

---

## 📊 Queries SQL Úteis

### Análise de Dados
```sql
-- Contar usuários
SELECT COUNT(*) as total_users FROM users;

-- Usuários cadastrados hoje
SELECT * FROM users 
WHERE DATE(created_at) = CURDATE();

-- Posts por usuário
SELECT u.username, COUNT(p.id) as total_posts
FROM users u
LEFT JOIN posts p ON u.id = p.id_user
GROUP BY u.id;

-- Últimos 10 usuários
SELECT * FROM users 
ORDER BY id DESC 
LIMIT 10;
```

### Manutenção
```sql
-- Limpar posts órfãos
DELETE FROM posts 
WHERE id_user NOT IN (SELECT id FROM users);

-- Resetar auto_increment
ALTER TABLE users AUTO_INCREMENT = 1;

-- Ver tamanho das tabelas
SELECT 
    table_name AS 'Tabela',
    ROUND(((data_length + index_length) / 1024 / 1024), 2) AS 'Tamanho (MB)'
FROM information_schema.TABLES
WHERE table_schema = 'cadastro_empresas';
```

---

## 🔄 Fluxo de Desenvolvimento

### 1. Nova Funcionalidade
```bash
# 1. Criar branch
git checkout -b feature/nome-funcionalidade

# 2. Desenvolver
# - Editar código
# - Testar localmente

# 3. Commit
git add .
git commit -m "feat: adiciona funcionalidade X"

# 4. Push
git push origin feature/nome-funcionalidade

# 5. Pull Request
# - Abrir no GitHub/GitLab
# - Solicitar revisão
```

### 2. Correção de Bug
```bash
# 1. Criar branch
git checkout -b fix/nome-do-bug

# 2. Corrigir
# - Identificar problema
# - Implementar solução
# - Testar

# 3. Commit
git commit -m "fix: corrige bug X"

# 4. Push e PR
git push origin fix/nome-do-bug
```

### 3. Atualização do Modelo
```bash
# 1. Editar app/models/tables.py
# 2. Criar migração
flask db migrate -m "adiciona campo X"

# 3. Revisar migração em migrations/versions/
# 4. Aplicar
flask db upgrade

# 5. Testar no Flask shell
flask shell
>>> from app.models.tables import User
>>> User.query.first()
```

---

## 📞 Contatos e Recursos

### Documentação Oficial
- **Flask**: https://flask.palletsprojects.com
- **SQLAlchemy**: https://docs.sqlalchemy.org
- **WTForms**: https://wtforms.readthedocs.io
- **MySQL**: https://dev.mysql.com/doc/

### Ferramentas Recomendadas
- **IDE**: VS Code, PyCharm
- **Cliente MySQL**: MySQL Workbench, DBeaver
- **API Testing**: Postman, Insomnia
- **Git GUI**: GitKraken, SourceTree

### Equipe de Desenvolvimento
- **Tech Lead**: [Nome]
- **Backend**: [Nome]
- **Frontend**: [Nome]
- **DBA**: [Nome]

---

## 🆘 FAQ - Perguntas Frequentes

**Q: Como redefinir a senha de um usuário?**
```python
# Via Flask shell
flask shell
>>> from app import db
>>> from app.models.tables import User
>>> user = User.query.filter_by(username='joao').first()
>>> user.set_password('nova_senha_123')
>>> db.session.commit()
```

**Q: Como adicionar um administrador?**
```python
# Futuro: adicionar campo 'is_admin' no modelo User
# Por enquanto, criar usuário normal via /register
```

**Q: Como fazer backup automático?**
```bash
# Criar script backup.sh
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
mysqldump -u root -p cadastro_empresas > backup_$DATE.sql

# Agendar no cron (Linux) ou Task Scheduler (Windows)
# Executar diariamente às 2h da manhã
0 2 * * * /caminho/backup.sh
```

**Q: Como limpar sessões antigas?**
```python
# Implementar limpeza de sessões expiradas
# Adicionar em utils.py ou task agendada
```

**Q: Como migrar para outro servidor?**
```bash
# 1. Backup do banco
mysqldump -u root -p cadastro_empresas > backup.sql

# 2. Copiar arquivos do projeto
scp -r projeto/ usuario@servidor:/caminho/

# 3. No novo servidor:
mysql -u root -p cadastro_empresas < backup.sql
cd projeto
pip install -r requirements.txt
# Atualizar .env com novas credenciais
python run.py
```

---

## 📈 Métricas e Monitoramento

### KPIs para Acompanhar
- Total de usuários cadastrados
- Logins por dia/semana/mês
- Posts criados
- Tempo médio de resposta
- Taxa de erro em formulários
- Uptime do sistema

### Ferramentas Sugeridas
- **Logs**: Sentry, LogRocket
- **Monitoramento**: Prometheus, Grafana
- **Analytics**: Google Analytics (se aplicável)
- **APM**: New Relic, DataDog

---

**Versão**: 1.0  
**Última Atualização**: Outubro 2025  
**Mantido por**: Equipe JP Contábil
