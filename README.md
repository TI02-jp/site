# Sistema de Cadastro de Empresas

Este projeto é uma aplicação Web desenvolvida em **Flask** para gerenciar usuários e um cadastro de empresas. Ele utiliza **MySQL** para persistência dos dados e `Flask-Login` para autenticação.

## Funcionalidades

- Registro e login de usuários
- Perfis de usuário (administrador ou usuário comum)
- Cadastro, edição e exclusão de empresas
- Listagem de empresas cadastradas
- Área administrativa para gerenciamento de usuários (apenas para administradores)

## Pré‑requisitos

- Python 3.10+
- MySQL 5.7 ou superior

## Instalação

1. Clone este repositório e acesse a pasta do projeto.
2. Crie um ambiente virtual e instale as dependências:

```bash
python3 -m venv venv
source venv/bin/activate
pip install Flask Flask-Login SQLAlchemy mysql-connector-python python-dotenv
```

3. Crie o banco de dados MySQL chamado `cadastro_empresas`:

```sql
CREATE DATABASE cadastro_empresas;
```

4. Copie o arquivo `.env.example` para `.env` e preencha com as suas credenciais. Exemplo:

```env
DB_HOST=localhost
DB_USER=seu_usuario
DB_PASSWORD=sua_senha
DB_NAME=cadastro_empresas
SQLALCHEMY_DATABASE_URI=mysql+mysqlconnector://seu_usuario:sua_senha@localhost:3306/cadastro_empresas
SECRET_KEY=sua_chave_secreta
```

A primeira execução da aplicação irá criar automaticamente as tabelas necessárias.

## Executando

Ative o ambiente virtual e execute o arquivo `run.py`:

```bash
python run.py
```

A aplicação estará disponível em `http://localhost:5000`.

## Contribuição

Pull requests são bem-vindos. Para grandes mudanças, por favor abra uma issue primeiro para discutir o que você gostaria de alterar.

