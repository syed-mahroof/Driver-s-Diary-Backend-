# Generated for Ride Monitor manifest fields.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cabapp', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='ride',
            name='ride_time',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ride',
            name='trip_type',
            field=models.CharField(choices=[('P', 'Pickup'), ('D', 'Drop')], default='P', max_length=1),
        ),
        migrations.AddField(
            model_name='ride',
            name='route',
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name='ride',
            name='total_km',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True),
        ),
        migrations.AddField(
            model_name='ride',
            name='vehicle_number',
            field=models.CharField(blank=True, max_length=50),
        ),
    ]
