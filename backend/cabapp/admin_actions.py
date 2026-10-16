"""Admin-only mutations. Preserve financial history when removing a driver."""
from uuid import uuid4
from django.contrib.admin.models import LogEntry, ADDITION, CHANGE, DELETION
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from .models import Driver, Ride, CarCharge
from .serializers import DriverSerializer, RideSerializer, AdvanceSalaryRequestSerializer
from .admin_serializers import AdminDriverSerializer, AdminRideSerializer, AdminChargeSerializer, AdminAdvanceSerializer


def log_action(user, obj, action, message):
    LogEntry.objects.log_action(user_id=user.pk, content_type_id=ContentType.objects.get_for_model(obj).pk,
                               object_id=obj.pk, object_repr=str(obj)[:200], action_flag=action, change_message=message)


@api_view(['POST'])
@permission_classes([IsAdminUser])
@transaction.atomic
def create_driver(request):
    serializer = AdminDriverSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    driver = serializer.save()
    log_action(request.user, driver, ADDITION, 'Created driver account.')
    return Response({'driver': DriverSerializer(driver).data}, status=201)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAdminUser])
@transaction.atomic
def driver_detail(request, pk):
    driver = get_object_or_404(Driver.objects.select_related('user').select_for_update(), pk=pk)
    if driver.user.is_staff or driver.user.is_superuser:
        return Response({'error': 'Admin accounts cannot be changed through driver management.'}, status=400)
    if request.method == 'DELETE':
        driver.is_active = False
        driver.user.is_active = False
        driver.user.save(update_fields=['is_active'])
        driver.save(update_fields=['is_active'])
        log_action(request.user, driver, DELETION, 'Driver removed; login disabled and financial history retained.')
        return Response(status=204)
    serializer = AdminDriverSerializer(driver, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    log_action(request.user, driver, CHANGE, 'Updated driver: ' + ', '.join(serializer.validated_data.keys()))
    return Response(DriverSerializer(driver).data)


@api_view(['POST', 'PATCH'])
@permission_classes([IsAdminUser])
@transaction.atomic
def ride_detail(request, pk=None):
    from .views import update_attendance
    if request.method != ('POST' if pk is None else 'PATCH'):
        return Response(status=405)
    ride = get_object_or_404(Ride.objects.select_for_update(), pk=pk) if pk is not None else None
    old = (ride.driver, ride.date) if ride else None
    serializer = AdminRideSerializer(ride, data=request.data, partial=bool(ride))
    serializer.is_valid(raise_exception=True)
    ride = serializer.save(**({} if ride else {'local_id': 'admin-' + str(uuid4())}))
    if old and old != (ride.driver, ride.date):
        update_attendance(*old)
    update_attendance(ride.driver, ride.date)
    log_action(request.user, ride, CHANGE if old else ADDITION, 'Recorded ride on behalf of driver.')
    return Response(RideSerializer(ride).data, status=200 if old else 201)


@api_view(['POST', 'PATCH'])
@permission_classes([IsAdminUser])
@transaction.atomic
def charge_detail(request, pk=None):
    if request.method != ('POST' if pk is None else 'PATCH'):
        return Response(status=405)
    charge = get_object_or_404(CarCharge.objects.select_for_update(), pk=pk) if pk is not None else None
    serializer = AdminChargeSerializer(charge, data=request.data, partial=bool(charge))
    serializer.is_valid(raise_exception=True)
    saved = serializer.save()
    log_action(request.user, saved, CHANGE if charge else ADDITION, 'Recorded charging expense on behalf of driver.')
    return Response(serializer.data, status=200 if charge else 201)


@api_view(['POST'])
@permission_classes([IsAdminUser])
@transaction.atomic
def create_advance(request):
    serializer = AdminAdvanceSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    advance = serializer.save()
    log_action(request.user, advance, ADDITION, 'Recorded salary advance on behalf of driver.')
    return Response(AdvanceSalaryRequestSerializer(advance).data, status=201)
