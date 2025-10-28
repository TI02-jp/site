"""Script para verificar reuniões futuras com status incorreto"""
import sys
from datetime import datetime
from app import app, db
from app.models.tables import Reuniao, ReuniaoStatus

with app.app_context():
    # Buscar reuniões futuras
    now = datetime.now()
    future_meetings = Reuniao.query.filter(Reuniao.inicio > now).all()

    print(f"\n=== Verificação de Reuniões Futuras ===")
    print(f"Data/hora atual: {now}")
    print(f"Total de reuniões futuras: {len(future_meetings)}\n")

    # Verificar reuniões com status incorreto
    problematic = []
    for meeting in future_meetings:
        if meeting.status == ReuniaoStatus.REALIZADA:
            problematic.append(meeting)
            print(f"⚠️  ID: {meeting.id}")
            print(f"   Assunto: {meeting.assunto}")
            print(f"   Início: {meeting.inicio}")
            print(f"   Fim: {meeting.fim}")
            print(f"   Status: {meeting.status.value}")
            print(f"   Criador ID: {meeting.criador_id}")
            print()

    if problematic:
        print(f"\n[ERRO] Encontradas {len(problematic)} reuniões futuras marcadas como REALIZADA!")
        print(f"\nPara corrigir, execute o script fix_events.py")
    else:
        print(f"\n[OK] Todas as reuniões futuras estão com status correto!")

    # Mostrar estatísticas de status
    print(f"\n=== Estatísticas de Status (Reuniões Futuras) ===")
    status_counts = {}
    for meeting in future_meetings:
        status = meeting.status.value
        status_counts[status] = status_counts.get(status, 0) + 1

    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")
