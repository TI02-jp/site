#!/usr/bin/env python3
"""
Script para gerar chaves VAPID para Web Push Notifications
"""

from py_vapid import Vapid
import base64

# Gerar chaves
vapid = Vapid()
vapid.generate_keys()

# Obter chave privada em formato PEM
private_key_pem = vapid.private_pem().decode('utf-8')

# Obter chave pública em formato URL-safe base64
# A chave pública é usada no frontend
from cryptography.hazmat.primitives import serialization

public_key_bytes = vapid.public_key.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint
)
public_key_b64 = base64.urlsafe_b64encode(public_key_bytes).decode('utf-8').rstrip('=')

print("=" * 80)
print("CHAVES VAPID GERADAS COM SUCESSO")
print("=" * 80)
print("\n1. Adicione estas variáveis ao seu arquivo .env ou configuração:")
print("\nVAPID_PRIVATE_KEY=")
print(private_key_pem)
print("\nVAPID_PUBLIC_KEY=" + public_key_b64)
print("\nVAPID_CLAIMS_EMAIL=mailto:suporte@jpcontabil.com.br")
print("\n" + "=" * 80)
print("IMPORTANTE:")
print("- Mantenha a PRIVATE KEY em SEGREDO!")
print("- A PUBLIC KEY será usada no JavaScript do frontend")
print("- Salve essas chaves em local seguro")
print("=" * 80)
