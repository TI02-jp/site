from app import app, db, User  # Importe o app, db e o modelo User do seu arquivo principal
import getpass
import logging

logging.basicConfig(level=logging.INFO)

def main():
    """Função principal para criar o usuário administrador."""
    logging.info("Criar Usuário Admin")
    
    # Pede as informações do novo usuário
    name = input("Nome completo: ")
    username = input("Nome de usuário (para login): ")
    email = input("Email: ")
    password = getpass.getpass("Senha: ")

    # Cria uma nova instância do usuário
    new_user = User(
        name=name,
        username=username,
        email=email,
        role='admin' # Define a role como admin
    )

    # Usa o método do modelo para gerar e salvar o hash da senha
    new_user.set_password(password)

    # Adiciona o novo usuário à sessão do banco e salva
    db.session.add(new_user)
    db.session.commit()
    
    logging.info("Usuário '%s' criado com sucesso com a role de 'admin'!", username)

if __name__ == '__main__':
    # Executa a função 'main' dentro do contexto da aplicação Flask
    # Isso é essencial para que o script tenha acesso ao db
    with app.app_context():
        main()
