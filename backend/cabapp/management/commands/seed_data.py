from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from cabapp.models import Driver, Company


class Command(BaseCommand):
    help = 'Seed initial data for development'

    def handle(self, *args, **options):
        # Create admin user
        if not User.objects.filter(username='admin').exists():
            admin = User.objects.create_superuser('admin', 'admin@cabservice.com', 'admin123')
            self.stdout.write(self.style.SUCCESS('Created admin user (admin/admin123)'))

        # Create driver users
        drivers_data = [
            ('driver1', 'Rajan Kumar', '9876543210'),
            ('driver2', 'Suresh Nair', '9876543211'),
            ('driver3', 'Anil Menon', '9876543212'),
        ]

        for username, name, phone in drivers_data:
            if not User.objects.filter(username=username).exists():
                user = User.objects.create_user(username, f'{username}@cabservice.com', 'driver123')
                Driver.objects.create(user=user, name=name, phone=phone)
                self.stdout.write(self.style.SUCCESS(f'Created driver: {name} ({username}/driver123)'))

        # Create companies
        companies = ['TCS', 'Infosys', 'Wipro', 'Cognizant', 'UST Global', 'HCL', 'Tech Mahindra']
        for company_name in companies:
            Company.objects.get_or_create(name=company_name)

        self.stdout.write(self.style.SUCCESS(f'Created {len(companies)} companies'))
        self.stdout.write(self.style.SUCCESS('Seeding complete!'))
