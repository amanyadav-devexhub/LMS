from django.core.management.base import BaseCommand

from leaves.seeder import seed_leaves_data
from users.seeder import seed_users_data


class Command(BaseCommand):
    help = "Seed complete LMS project data (users, departments, leave setup, allocations, and sample requests)."

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("Starting LMS seeding..."))

        users_result = seed_users_data()
        leaves_result = seed_leaves_data()

        self.stdout.write(self.style.SUCCESS("Seeding complete."))
        self.stdout.write("Users domain:")
        for key, value in users_result.items():
            self.stdout.write(f"  - {key}: {value}")

        self.stdout.write("Leaves domain:")
        for key, value in leaves_result.items():
            self.stdout.write(f"  - {key}: {value}")

        self.stdout.write(self.style.SUCCESS("Run command: python manage.py seed_all"))
