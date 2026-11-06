"""Saved driver passwords no longer use encryption or a key.

Copies saved by the earlier encrypted version cannot be shown as plain text, so they are
cleared; the admin sets a new password for those drivers in Manage drivers.
"""
from django.db import migrations


def clear_encrypted_copies(apps, schema_editor):
    apps.get_model('cabapp', 'Driver').objects.exclude(admin_set_password='').update(admin_set_password='')


class Migration(migrations.Migration):

    dependencies = [
        ('cabapp', '0012_driver_saved_password'),
    ]

    operations = [
        migrations.RenameField(
            model_name='driver',
            old_name='saved_password_encrypted',
            new_name='admin_set_password',
        ),
        migrations.RunPython(clear_encrypted_copies, migrations.RunPython.noop),
    ]
