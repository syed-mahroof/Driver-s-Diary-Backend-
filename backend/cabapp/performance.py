"""Bounded, aggregate analytics without one query per driver."""
from datetime import date, timedelta
from django.db.models import Count, Sum, Max, Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from .filters import admin_filters, scope_rides
from .models import Driver, Ride, Attendance, CarCharge, AdvanceSalaryRequest


@api_view(['GET'])
@permission_classes([IsAdminUser])
def driver_performance(request):
    from .views import (build_salary_cycle_summaries, get_current_and_previous_salary_cycles,
                        SALARY_BASE_TRIP_VALUE, SALARY_SPECIAL_KM_RATE, SALARY_TARGET_AMOUNT)
    start, end, driver_ids, companies, vehicles = admin_filters(request.query_params)
    start = date.fromisoformat(start) if start else date.today().replace(day=1)
    end = date.fromisoformat(end) if end else date.today()
    if start > end or (end - start).days > 365:
        raise ValidationError({'date': 'Choose a performance period of up to 366 days.'})
    drivers = Driver.objects.all() if driver_ids else Driver.objects.filter(is_active=True)
    if driver_ids:
        drivers = drivers.filter(pk__in=driver_ids)
    drivers = list(drivers.order_by('name'))
    ids = [driver.pk for driver in drivers]
    rides = scope_rides(Ride.objects.filter(driver_id__in=ids), start, end, [], companies, vehicles)
    special = Q(company__name__icontains='zellis') | Q(company__name__icontains='dodge')
    aggregates = {
        row['driver_id']: row for row in rides.order_by().values('driver_id').annotate(
            trips=Count('id'), active_days=Count('date', distinct=True), distance_km=Sum('total_km'),
            standard_trips=Count('id', filter=~special), special_km=Sum('total_km', filter=special),
            pickups=Count('id', filter=Q(trip_type='P')), last_ride=Max('date'))
    }
    daily = list(rides.order_by('date').values('date').annotate(trips=Count('id'), km=Sum('total_km')))
    daily_map = {row['date']: row for row in daily}
    breakdown = list(rides.order_by().values('company__name').annotate(trips=Count('id')).order_by('-trips', 'company__name'))
    charges = CarCharge.objects.filter(driver_id__in=ids, date__range=(start, end))
    if vehicles:
        charges = charges.filter(vehicle_number__in=vehicles)
    charge_map = {row['driver_id']: float(row['total'] or 0) for row in charges.order_by().values('driver_id').annotate(total=Sum('charge_amount'))}
    advance_map = {row['driver_id']: float(row['total'] or 0) for row in AdvanceSalaryRequest.objects.filter(
        driver_id__in=ids, status='Paid', resolved_date__date__range=(start, end)
    ).order_by().values('driver_id').annotate(total=Sum('amount'))}
    attendance = {}
    for row in Attendance.objects.filter(driver_id__in=ids, date__range=(start, end)).order_by().values('driver_id', 'status').annotate(count=Count('id')):
        attendance.setdefault(row['driver_id'], {})[row['status']] = row['count']
    (cycle_start, cycle_end), _ = get_current_and_previous_salary_cycles()
    cycles = build_salary_cycle_summaries(drivers, cycle_start, cycle_end)
    rows = []
    for driver in drivers:
        data = aggregates.get(driver.pk, {})
        trips, active_days = data.get('trips', 0), data.get('active_days', 0)
        elapsed = max(0, (min(end, date.today()) - start).days + 1)
        earnings = round(data.get('standard_trips', 0) * SALARY_BASE_TRIP_VALUE + float(data.get('special_km') or 0) * SALARY_SPECIAL_KM_RATE, 2)
        rows.append({
            'driver_id': driver.pk, 'driver_name': driver.name, 'is_active': driver.is_active,
            'trips': trips, 'active_days': active_days, 'avg_trips_per_day': round(trips / elapsed, 2) if elapsed else 0,
            'avg_trips_per_active_day': round(trips / active_days, 2) if active_days else 0,
            'total_km': float(data.get('distance_km') or 0), 'pickups': data.get('pickups', 0),
            'drops': trips - data.get('pickups', 0), 'last_ride': data.get('last_ride'),
            'ride_earnings': earnings, 'charging_cost': charge_map.get(driver.pk, 0),
            'advance_paid': advance_map.get(driver.pk, 0), 'attendance': attendance.get(driver.pk, {}),
            'current_cycle': cycles[driver.pk],
            'target_progress': round(cycles[driver.pk]['gross_earnings'] / SALARY_TARGET_AMOUNT * 100, 1),
        })
    trend = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        values = daily_map.get(day, {})
        trend.append({'date': day, 'trips': values.get('trips', 0), 'km': float(values.get('km') or 0)})
    elapsed_days = max(0, (min(end, date.today()) - start).days + 1)
    total_trips = sum(row['trips'] for row in rows)
    return Response({
        'start_date': start, 'end_date': end, 'elapsed_days': elapsed_days,
        'cycle_start': cycle_start, 'cycle_end': cycle_end, 'salary_target': SALARY_TARGET_AMOUNT,
        'summary': {'trips': total_trips, 'total_km': sum(row['total_km'] for row in rows),
                    'avg_trips_per_day': round(total_trips / elapsed_days, 2) if elapsed_days else 0,
                    'active_drivers': sum(row['trips'] > 0 for row in rows),
                    'charging_cost': sum(charge_map.values())},
        'drivers': rows, 'daily_trend': trend,
        'companies': [{'name': row['company__name'] or 'Other', 'trips': row['trips']} for row in breakdown],
    })
