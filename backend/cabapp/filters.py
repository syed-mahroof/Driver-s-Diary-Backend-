"""Validated, shared dashboard and export filters (CSV or repeated values)."""
from datetime import date
from rest_framework.exceptions import ValidationError


def filter_values(params, key):
    values = []
    for name in (key, key + '[]'):
        for item in params.getlist(name):
            values.extend(value.strip() for value in item.split(',') if value.strip())
    values = list(dict.fromkeys(values))
    if key.endswith('_id') and any(not value.isdigit() for value in values):
        raise ValidationError({key: 'Select valid IDs.'})
    return values


def admin_filters(params):
    start, end = params.get('start_date'), params.get('end_date')
    try:
        parsed_start = date.fromisoformat(start) if start else None
        parsed_end = date.fromisoformat(end) if end else None
    except (ValueError, TypeError):
        raise ValidationError({'date': 'Use a valid YYYY-MM-DD date.'})
    if parsed_start and parsed_end and parsed_start > parsed_end:
        raise ValidationError({'end_date': 'End date must be on or after start date.'})
    return start, end, filter_values(params, 'driver_id'), filter_values(params, 'company_id'), filter_values(params, 'vehicle_number')


def scope_rides(queryset, start, end, drivers, companies, vehicles):
    if start:
        queryset = queryset.filter(date__gte=start)
    if end:
        queryset = queryset.filter(date__lte=end)
    if drivers:
        queryset = queryset.filter(driver_id__in=drivers)
    if companies:
        queryset = queryset.filter(company_id__in=companies)
    if vehicles:
        queryset = queryset.filter(vehicle_number__in=vehicles)
    return queryset
