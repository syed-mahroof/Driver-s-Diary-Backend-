from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cabapp', '0002_ride_manifest_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ride',
            name='drop',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AlterField(
            model_name='ride',
            name='pickup',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='ride',
            name='notes',
            field=models.TextField(blank=True),
        ),
    ]
