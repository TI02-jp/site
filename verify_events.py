from app import app, db
from app.models.tables import GeneralCalendarEvent

app.app_context().push()

total = GeneralCalendarEvent.query.count()
normal = GeneralCalendarEvent.query.filter_by(is_birthday=False).count()
birthday = GeneralCalendarEvent.query.filter_by(is_birthday=True).count()

print("=== RESUMO DOS EVENTOS ===")
print(f"Total de eventos: {total}")
print(f"Eventos normais: {normal}")
print(f"Eventos de aniversario: {birthday}")

print("\n=== PRIMEIROS 5 EVENTOS NORMAIS ===")
for e in GeneralCalendarEvent.query.filter_by(is_birthday=False).order_by(GeneralCalendarEvent.start_date).limit(5).all():
    print(f"{e.id}: {e.title} ({e.start_date})")

print("\n=== PRIMEIROS 5 ANIVERSARIOS ===")
for e in GeneralCalendarEvent.query.filter_by(is_birthday=True).order_by(GeneralCalendarEvent.start_date).limit(5).all():
    print(f"{e.id}: {e.title} - {e.birthday_user_name} ({e.start_date})")
