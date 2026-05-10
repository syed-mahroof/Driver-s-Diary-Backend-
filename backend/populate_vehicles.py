import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cabservice.settings')
django.setup()

from cabapp.models import Vehicle

vehicles_data = [
    (4, '0680'),
    (4, '0622'),
    (4, '0656'),
    (4, '0664'),
    (4, '4507'),
    (4, '9699'),
    (6, '4647'),
    (6, '9798'),
]

for seater, number in vehicles_data:
    Vehicle.objects.get_or_create(number=number, seater=seater)

print("Vehicles populated successfully.")
