from django.core.management.base import BaseCommand
from app.services import expire_pending_orders


class Command(BaseCommand):
    help = "Expira reservas vencidas e devolve os ingressos ao estoque."

    def handle(self, *args, **options):
        count = expire_pending_orders()
        self.stdout.write(self.style.SUCCESS(f"{count} reserva(s) expirada(s)."))
