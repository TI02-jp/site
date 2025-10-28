"""Teste das permissões de admin"""
from app import app, db
from app.models.tables import User
from app.utils.permissions import is_user_admin

class MockUser:
    """Mock de usuário para teste"""
    def __init__(self, role):
        self.role = role

with app.app_context():
    print("\n=== Teste de Permissões de Admin ===\n")

    # Testa diferentes roles
    roles_to_test = ["user", "admin", "admin_master", "guest", None]

    for role in roles_to_test:
        mock_user = MockUser(role) if role else MockUser("user")
        mock_user.role = role
        result = is_user_admin(mock_user)
        status = "ADMIN" if result else "NÃO ADMIN"
        print(f"Role: {role!r:15} -> {status}")

    # Verifica usuários reais do banco
    print("\n=== Usuários Reais no Banco de Dados ===\n")
    admin_users = User.query.filter(
        User.role.in_(["admin", "admin_master"])
    ).all()

    if admin_users:
        for user in admin_users:
            result = is_user_admin(user)
            status = "[OK] ADMIN" if result else "[ERRO] NAO ADMIN"
            print(f"ID: {user.id:3} | Username: {user.username:20} | Role: {user.role:15} -> {status}")
    else:
        print("Nenhum usuário admin encontrado no banco de dados")

    print("\n=== Teste Concluído ===\n")
