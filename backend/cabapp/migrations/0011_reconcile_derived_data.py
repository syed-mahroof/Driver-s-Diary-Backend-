"""Repair rows left stale by writes that bypassed the API (e.g. deleting rides in the Django admin).

Rows recording zero rides are kept: they may be deliberate Leave entries made in the admin.
"""
from django.conf import settings
from django.db import migrations
from django.db.models import Count, F


def day_values(day, total):
    if day.weekday() in (5, 6):
        return 'Holiday', 0
    if total >= settings.FULL_DAY_THRESHOLD:
        return 'Full', settings.FULL_DAY_RATE
    return 'Half', settings.HALF_DAY_RATE


def reconcile_derived_data(apps, schema_editor):
    Attendance = apps.get_model('cabapp', 'Attendance')
    Ride = apps.get_model('cabapp', 'Ride')
    AdvanceSalaryRequest = apps.get_model('cabapp', 'AdvanceSalaryRequest')

    counts = {
        (row['driver_id'], row['date']): row['total']
        for row in Ride.objects.order_by().values('driver_id', 'date').annotate(total=Count('id'))
    }
    for attendance in Attendance.objects.all():
        total = counts.pop((attendance.driver_id, attendance.date), 0)
        if total == attendance.total_rides:
            continue
        if total == 0:
            attendance.delete()
            continue
        status, salary = day_values(attendance.date, total)
        Attendance.objects.filter(pk=attendance.pk).update(total_rides=total, status=status, salary=salary)
    for (driver_id, day), total in counts.items():
        status, salary = day_values(day, total)
        Attendance.objects.create(driver_id=driver_id, date=day, total_rides=total, status=status, salary=salary)

    AdvanceSalaryRequest.objects.exclude(status='Pending').filter(resolved_date__isnull=True).update(resolved_date=F('request_date'))


class Migration(migrations.Migration):

    dependencies = [
        ('cabapp', '0010_alter_carcharge_driver'),
    ]

    operations = [
        migrations.RunPython(reconcile_derived_data, migrations.RunPython.noop),
    ]
