from datetime import date, datetime, timedelta
import random
import re
from django.db import transaction
from django.db.models import Count, Sum, Q, Case, When, Value, IntegerField
from django.http import HttpResponse
from django.conf import settings
from rest_framework import status

import calendar

def get_trailing_month_range(year, month):
    """Salary cycle ending on the 15th of (year, month): runs 16th of the
    previous month through the 15th of this one."""
    if month == 1:
        start_date = date(year - 1, 12, 16)
    else:
        start_date = date(year, month - 1, 16)
    end_date = date(year, month, 15)
    return start_date, end_date


def get_cycle_end_year_month(ref_date):
    """Which (year, month) cycle — per get_trailing_month_range — contains ref_date.
    From the 16th onward, ref_date belongs to the cycle ending next month."""
    if ref_date.day >= 16:
        if ref_date.month == 12:
            return ref_date.year + 1, 1
        return ref_date.year, ref_date.month + 1
    return ref_date.year, ref_date.month


# Salary is earned against the same ride-based rules shown in the driver's live
# earnings tracker. Keeping the calculation here means the driver statement,
# the admin overview, and their PDFs all use one authoritative result.
SALARY_TARGET_TRIPS = 120
SALARY_TARGET_AMOUNT = 20000
SALARY_BASE_TRIP_VALUE = SALARY_TARGET_AMOUNT / SALARY_TARGET_TRIPS
SALARY_INCENTIVE_TRIP_VALUE = 200
SALARY_SPECIAL_KM_RATE = 3.5


def get_current_and_previous_salary_cycles(ref_date=None):
    """Return the active 16th-to-15th cycle and the immediately prior one."""
    ref_date = ref_date or date.today()
    cycle_end_year, cycle_end_month = get_cycle_end_year_month(ref_date)
    current_start, current_end = get_trailing_month_range(cycle_end_year, cycle_end_month)

    previous_reference = current_start - timedelta(days=1)
    previous_end_year, previous_end_month = get_cycle_end_year_month(previous_reference)
    previous_start, previous_end = get_trailing_month_range(previous_end_year, previous_end_month)

    return (current_start, current_end), (previous_start, previous_end)


def build_salary_cycle_summaries(drivers, cycle_start, cycle_end):
    """Build efficient, per-driver salary summaries for one salary cycle."""
    driver_ids = [driver.id for driver in drivers]
    if not driver_ids:
        return {}

    special_company_q = (
        Q(company__name__icontains='zellis') |
        Q(company__name__icontains='dodge')
    )

    ride_data = {
        row['driver_id']: row
        for row in Ride.objects.filter(
            driver_id__in=driver_ids,
            date__gte=cycle_start,
            date__lte=cycle_end,
        ).values('driver_id').annotate(
            total_rides=Count('id'),
            standard_rides=Count('id', filter=~special_company_q),
            special_kms=Sum('total_km', filter=special_company_q),
        )
    }

    attendance_data = {driver_id: {'Full': 0, 'Half': 0, 'Leave': 0, 'Holiday': 0} for driver_id in driver_ids}
    for row in Attendance.objects.filter(
        driver_id__in=driver_ids,
        date__gte=cycle_start,
        date__lte=cycle_end,
    ).values('driver_id', 'status').annotate(count=Count('id')):
        attendance_data[row['driver_id']][row['status']] = row['count']

    advance_data = {
        row['driver_id']: float(row['total'] or 0)
        for row in AdvanceSalaryRequest.objects.filter(
            driver_id__in=driver_ids,
            status='Paid',
            resolved_date__date__gte=cycle_start,
            resolved_date__date__lte=cycle_end,
        ).values('driver_id').annotate(total=Sum('amount'))
    }

    summaries = {}
    for driver in drivers:
        rides = ride_data.get(driver.id, {})
        total_rides = int(rides.get('total_rides') or 0)
        standard_rides = int(rides.get('standard_rides') or 0)
        special_kms = float(rides.get('special_kms') or 0)
        incentive_trips = max(0, standard_rides - SALARY_TARGET_TRIPS)

        standard_ride_earnings = round(standard_rides * SALARY_BASE_TRIP_VALUE, 2)
        distance_earnings = round(special_kms * SALARY_SPECIAL_KM_RATE, 2)
        incentive_earnings = round(incentive_trips * SALARY_INCENTIVE_TRIP_VALUE, 2)
        gross_earnings = round(standard_ride_earnings + distance_earnings + incentive_earnings, 2)
        advance_paid = round(advance_data.get(driver.id, 0), 2)

        attendance = attendance_data[driver.id]
        summaries[driver.id] = {
            'cycle_start': cycle_start.isoformat(),
            'cycle_end': cycle_end.isoformat(),
            'total_rides': total_rides,
            'standard_rides': standard_rides,
            'special_rides': total_rides - standard_rides,
            'special_kms': round(special_kms, 2),
            'standard_ride_earnings': standard_ride_earnings,
            'distance_earnings': distance_earnings,
            'incentive_trips': incentive_trips,
            'incentive_earnings': incentive_earnings,
            'gross_earnings': gross_earnings,
            'advance_paid': advance_paid,
            'net_payable': round(gross_earnings - advance_paid, 2),
            'attendance': {
                'full_days': attendance['Full'],
                'half_days': attendance['Half'],
                'leaves': attendance['Leave'],
                'holidays': attendance['Holiday'],
            },
        }

    return summaries

def is_full_month(d_start, d_end):
    if not d_start or not d_end:
        return False
    if d_start.day != 1:
        return False
    _, last_day = calendar.monthrange(d_end.year, d_end.month)
    return d_end.day == last_day and d_start.month == d_end.month and d_start.year == d_end.year


def make_unique_sheet_title(name, used_titles, fallback='Sheet'):
    """Sanitize and deduplicate Excel worksheet titles (max 31 chars)."""
    cleaned = re.sub(r'[\\/*?:\[\]]', '', str(name)).strip()[:31]
    if not cleaned:
        cleaned = fallback
    temp = cleaned
    counter = 1
    while temp.lower() in used_titles:
        suffix = f"_{counter}"
        temp = cleaned[:31 - len(suffix)] + suffix
        counter += 1
    used_titles.add(temp.lower())
    return temp


def ride_dedupe_key(ride):
    """Signature for duplicate ride rows (e.g. repeated offline sync entries)."""
    route = (ride.route or ride.pickup or '').strip().lower()
    return (
        ride.driver_id,
        ride.date,
        ride.company_id,
        str(ride.ride_time or ''),
        (ride.trip_type or '').strip(),
        route,
        (ride.vehicle_number or '').strip(),
        str(ride.total_km or ''),
    )


def dedupe_ride_list(rides):
    seen = set()
    unique = []
    for ride in rides:
        key = ride_dedupe_key(ride)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ride)
    return unique


from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from django.core.mail import send_mail

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .models import Driver, Company, Ride, Attendance, PasswordResetOTP, Vehicle, CarCharge, AdvanceSalaryRequest
from .filters import admin_filters
from .serializers import (
    CompanySerializer, RideSerializer, RideCreateSerializer,
    SyncPayloadSerializer, DriverSerializer,
    RegisterSerializer, UserSerializer, CabTokenObtainPairSerializer,
    ForgotPasswordRequestSerializer, VerifyOTPSerializer, ResetPasswordSerializer,
    CarChargeSerializer, VehicleSerializer, AdvanceSalaryRequestSerializer,
)


def update_attendance(driver, ride_date):
    """Recalculate attendance for a driver on a given date."""
    total = Ride.objects.filter(driver=driver, date=ride_date).count()
    attendance, _ = Attendance.objects.get_or_create(driver=driver, date=ride_date)
    attendance.total_rides = total
    attendance.save()
    return attendance


class CabTokenObtainPairView(TokenObtainPairView):
    serializer_class = CabTokenObtainPairSerializer


@api_view(['POST'])
@permission_classes([])
def register(request):
    serializer = RegisterSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        return Response({
            'user': UserSerializer(user).data,
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        }, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


def _find_user_by_contact(contact):
    """Find a user by email or phone number."""
    contact = contact.strip()
    # Try email first
    try:
        from django.contrib.auth.models import User
        user = User.objects.get(email__iexact=contact)
        return user
    except User.DoesNotExist:
        pass
    # Try phone number via Driver model
    try:
        driver = Driver.objects.get(phone=contact)
        return driver.user
    except Driver.DoesNotExist:
        pass
    return None


@api_view(['POST'])
@permission_classes([])
def forgot_password(request):
    serializer = ForgotPasswordRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    contact = serializer.validated_data['contact']
    user = _find_user_by_contact(contact)

    if not user:
        return Response(
            {'error': 'No account found with this email address.'},
            status=status.HTTP_404_NOT_FOUND
        )

    # Invalidate any existing unused OTPs for this user
    PasswordResetOTP.objects.filter(user=user, is_used=False).update(is_used=True)

    # Generate 6-digit OTP
    otp_code = f"{random.randint(100000, 999999)}"
    PasswordResetOTP.objects.create(user=user, otp=otp_code)

    # Determine which method was used
    is_email = '@' in contact
    masked = _mask_contact(contact, is_email)

    if is_email:
        subject = "Driver's Diary - Password Reset OTP"
        message = f"Hello {user.username},\n\nYour OTP for password reset is: {otp_code}\n\nPlease do not share this OTP with anyone.\n\nThank you,\nDriver's Diary Team"
        html_message = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
                    <h2 style="color: #2E7D32; text-align: center;">Driver's Diary</h2>
                    <p>Hello <strong>{user.username}</strong>,</p>
                    <p>We received a request to reset your password. Your One-Time Password (OTP) is:</p>
                    <div style="background-color: #f4f4f4; padding: 15px; text-align: center; font-size: 24px; letter-spacing: 5px; font-weight: bold; border-radius: 5px; margin: 20px 0;">
                        {otp_code}
                    </div>
                    <p style="color: #d32f2f; font-size: 0.9em;">Please do not share this OTP with anyone. It is valid for a limited time.</p>
                    <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
                    <p style="font-size: 0.8em; color: #777;">Thank you,<br>Driver's Diary Team</p>
                </div>
            </body>
        </html>
        """
        try:
            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL if hasattr(settings, 'DEFAULT_FROM_EMAIL') else 'noreply@driversdiary.com',
                [contact],
                fail_silently=False,
                html_message=html_message
            )
        except Exception as e:
            print(f"Error sending email: {e}")
            import traceback
            traceback.print_exc()
            return Response(
                {'error': f'Failed to send email. Error: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    else:
        # If it's a phone number, logic for sending SMS would go here.
        # Since the user requested to send to email, and the form now only accepts email, 
        # this path is unlikely, but we keep it for fallback.
        print(f"  PASSWORD RESET OTP for {user.username}: {otp_code}")

    return Response({
        'message': f'OTP sent to {masked}',
        'contact_type': 'email' if is_email else 'phone',
    })


def _mask_contact(contact, is_email):
    """Mask contact info for privacy."""
    if is_email:
        parts = contact.split('@')
        name = parts[0]
        masked_name = name[0] + '***' + (name[-1] if len(name) > 1 else '')
        return f"{masked_name}@{parts[1]}"
    else:
        return contact[:2] + '****' + contact[-2:] if len(contact) > 4 else '****'


@api_view(['POST'])
@permission_classes([])
def verify_otp(request):
    serializer = VerifyOTPSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    contact = serializer.validated_data['contact']
    otp = serializer.validated_data['otp']
    user = _find_user_by_contact(contact)

    if not user:
        return Response({'error': 'No account found.'}, status=status.HTTP_404_NOT_FOUND)

    otp_obj = PasswordResetOTP.objects.filter(user=user, otp=otp, is_used=False).first()
    if not otp_obj or not otp_obj.is_valid():
        return Response({'error': 'Invalid or expired OTP.'}, status=status.HTTP_400_BAD_REQUEST)

    return Response({'message': 'OTP verified successfully.'})


@api_view(['POST'])
@permission_classes([])
def reset_password(request):
    serializer = ResetPasswordSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    contact = serializer.validated_data['contact']
    otp = serializer.validated_data['otp']
    new_password = serializer.validated_data['new_password']
    user = _find_user_by_contact(contact)

    if not user:
        return Response({'error': 'No account found.'}, status=status.HTTP_404_NOT_FOUND)

    otp_obj = PasswordResetOTP.objects.filter(user=user, otp=otp, is_used=False).first()
    if not otp_obj or not otp_obj.is_valid():
        return Response({'error': 'Invalid or expired OTP.'}, status=status.HTTP_400_BAD_REQUEST)

    # Mark OTP as used and set new password
    otp_obj.is_used = True
    otp_obj.save()
    user.set_password(new_password)
    user.save()

    return Response({'message': 'Password reset successfully. You can now log in.'})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def driver_dashboard(request):
    try:
        driver = request.user.driver_profile
    except Driver.DoesNotExist:
        return Response({'error': 'Driver profile not found'}, status=404)

    today = date.today()
    threshold = getattr(settings, 'FULL_DAY_THRESHOLD', 4)
    full_rate = getattr(settings, 'FULL_DAY_RATE', 1000)
    half_rate = getattr(settings, 'HALF_DAY_RATE', 500)

    # Q filter: rides linked to Zellis or Dodge are billed by distance @ Rs 3.5/km
    special_company_q = (
        Q(company__name__icontains='zellis') |
        Q(company__name__icontains='dodge')
    )

    today_rides_qs = Ride.objects.filter(driver=driver, date=today)
    today_count = today_rides_qs.count()

    is_full = today_count >= threshold
    current_salary = 0 if today.weekday() >= 5 or today_count == 0 else full_rate if is_full else half_rate

    recent_rides = RideSerializer(
        today_rides_qs.select_related('company', 'driver').order_by('-created_at')[:10],
        many=True
    ).data

    # ── Daily earnings aggregates ────────────────────────────────────────────
    today_standard_rides = today_rides_qs.exclude(special_company_q).count()
    today_special_kms_agg = (
        today_rides_qs
        .filter(special_company_q)
        .aggregate(total=Sum('total_km'))
    )
    today_special_kms = float(today_special_kms_agg['total'] or 0)

    # ── Monthly earnings aggregates ──────────────────────────────────────────
    cycle_end_year, cycle_end_month = get_cycle_end_year_month(today)
    salary_start, salary_end = get_trailing_month_range(cycle_end_year, cycle_end_month)
    month_rides_qs = Ride.objects.filter(
        driver=driver,
        date__gte=salary_start,
        date__lte=salary_end,
    )
    monthly_standard_rides = month_rides_qs.exclude(special_company_q).count()
    monthly_special_kms_agg = (
        month_rides_qs
        .filter(special_company_q)
        .aggregate(total=Sum('total_km'))
    )
    monthly_special_kms = float(monthly_special_kms_agg['total'] or 0)

    attendance_qs = Attendance.objects.filter(
        driver=driver,
        date__gte=salary_start,
        date__lte=salary_end,
    )
    monthly_attendance_stats = attendance_qs.values('status').annotate(count=Count('id'))
    attendance_dict = {item['status']: item['count'] for item in monthly_attendance_stats}

    cycle_days_elapsed = (today - salary_start).days + 1

    cycle_advance_paid = AdvanceSalaryRequest.objects.filter(
        driver=driver,
        status='Paid',
        resolved_date__date__gte=salary_start,
        resolved_date__date__lte=salary_end,
    ).aggregate(total=Sum('amount'))['total'] or 0

    return Response({
        'driver_id': driver.id,
        'driver_name': driver.name,
        'today_rides': today_count,
        'target_rides': threshold,
        'status': 'Holiday' if today.weekday() >= 5 else 'Leave' if today_count == 0 else 'Full Day' if is_full else 'Half Day',
        'salary_today': current_salary,
        'progress_percentage': min(100, (today_count / threshold) * 100),
        'recent_rides': recent_rides,
        'default_vehicle_number': driver.default_vehicle_number,
        'default_seater': driver.default_seater,
        # ── NEW: earnings engine aggregates ──────────────────────────────────
        'today_standard_rides': today_standard_rides,
        'today_special_kms': today_special_kms,
        'monthly_standard_rides': monthly_standard_rides,
        'monthly_special_kms': monthly_special_kms,
        'monthly_target': 120,
        'cycle_days_elapsed': cycle_days_elapsed,
        'cycle_start': salary_start.isoformat(),
        'cycle_end': salary_end.isoformat(),
        'monthly_full_days': attendance_dict.get('Full', 0),
        'monthly_half_days': attendance_dict.get('Half', 0),
        'monthly_leaves': attendance_dict.get('Leave', 0),
        'monthly_holidays': attendance_dict.get('Holiday', 0),
        'cycle_advance_paid': float(cycle_advance_paid),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def driver_salary_summary(request):
    """Previous and in-progress salary-cycle figures for the signed-in driver."""
    try:
        driver = request.user.driver_profile
    except Driver.DoesNotExist:
        return Response({'error': 'Driver profile not found'}, status=404)

    (current_start, current_end), (previous_start, previous_end) = get_current_and_previous_salary_cycles()
    current_summary = build_salary_cycle_summaries([driver], current_start, current_end)[driver.id]
    previous_summary = build_salary_cycle_summaries([driver], previous_start, previous_end)[driver.id]

    return Response({
        'driver_id': driver.id,
        'driver_name': driver.name,
        'salary_rules': {
            'standard_ride_rate': round(SALARY_BASE_TRIP_VALUE, 2),
            'special_km_rate': SALARY_SPECIAL_KM_RATE,
            'incentive_trip_rate': SALARY_INCENTIVE_TRIP_VALUE,
            'incentive_threshold': SALARY_TARGET_TRIPS,
        },
        'previous_cycle': previous_summary,
        'current_cycle': current_summary,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_ride(request):
    try:
        driver = request.user.driver_profile
    except Driver.DoesNotExist:
        return Response({'error': 'Driver profile not found'}, status=404)

    existing = Ride.objects.filter(local_id=request.data.get('local_id'), driver=driver).first()
    if existing:
        return Response(RideSerializer(existing).data)
    serializer = RideCreateSerializer(data=request.data, context={'request': request})
    if serializer.is_valid():
        ride = serializer.save()
        update_attendance(driver, ride.date)
        return Response(RideSerializer(ride).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PATCH', 'PUT'])
@permission_classes([IsAuthenticated])
def update_ride(request, pk):
    try:
        driver = request.user.driver_profile
        ride = Ride.objects.get(pk=pk, driver=driver)
    except Driver.DoesNotExist:
        return Response({'error': 'Driver profile not found'}, status=404)
    except Ride.DoesNotExist:
        return Response({'error': 'Ride not found'}, status=404)

    old_date = ride.date
    serializer = RideCreateSerializer(ride, data=request.data, partial=True, context={'request': request})
    if serializer.is_valid():
        updated_ride = serializer.save()

        # Update attendance for the old date if it changed
        if old_date != updated_ride.date:
            update_attendance(driver, old_date)

        # Always update attendance for the (new) date
        update_attendance(driver, updated_ride.date)

        return Response(RideSerializer(updated_ride).data)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_car_charge(request):
    try:
        driver = request.user.driver_profile
    except Driver.DoesNotExist:
        return Response({'error': 'Driver profile not found'}, status=404)

    serializer = CarChargeSerializer(data=request.data, context={'request': request})
    if serializer.is_valid():
        charge = serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_rides(request):
    try:
        driver = request.user.driver_profile
    except Driver.DoesNotExist:
        return Response({'error': 'Driver profile not found'}, status=404)

    payload = SyncPayloadSerializer(data=request.data)
    if not payload.is_valid():
        return Response(payload.errors, status=status.HTTP_400_BAD_REQUEST)

    rides_data = payload.validated_data['rides']
    results = {'synced': [], 'skipped': [], 'errors': []}

    with transaction.atomic():
        for ride_data in rides_data:
            local_id = ride_data['local_id']

            existing = Ride.objects.filter(local_id=local_id).first()
            if existing:
                if existing.driver_id == driver.pk:
                    results['skipped'].append(local_id)
                else:
                    results['errors'].append({'local_id': local_id, 'error': 'Ride ID is unavailable.'})
                continue

            try:
                company = None
                if ride_data.get('company_id'):
                    company = Company.objects.get(id=ride_data['company_id'])

                ride = Ride.objects.create(
                    local_id=local_id,
                    driver=driver,
                    company=company,
                    date=ride_data['date'],
                    ride_time=ride_data.get('ride_time'),
                    trip_type=ride_data.get('trip_type', 'P'),
                    route=ride_data.get('route', ''),
                    pickup=ride_data.get('pickup', ''),
                    drop=ride_data.get('drop', ''),
                    notes=ride_data.get('notes', ''),
                    total_km=ride_data.get('total_km'),
                    vehicle_number=ride_data.get('vehicle_number', ''),
                    requested_seater=ride_data.get('requested_seater', 4),
                )
                results['synced'].append(local_id)
                update_attendance(driver, ride.date)
            except Exception as e:
                results['errors'].append({'local_id': local_id, 'error': str(e)})

    return Response({
        'message': f"Sync complete. {len(results['synced'])} synced, {len(results['skipped'])} skipped.",
        **results
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_companies(request):
    companies = Company.objects.filter(is_active=True)
    return Response(CompanySerializer(companies, many=True).data)


@api_view(['POST'])
@permission_classes([IsAdminUser])
def create_company(request):
    serializer = CompanySerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_dashboard(request):
    if not (request.user.is_staff or request.user.is_superuser):
        return Response({'error': 'Admin access required'}, status=403)

    # Parse filters
    start_date, end_date, driver_id, company_id, vehicle_number = admin_filters(request.query_params)

    attendance_qs = Attendance.objects.all()
    ride_qs = Ride.objects.all()

    if start_date:
        attendance_qs = attendance_qs.filter(date__gte=start_date)
        ride_qs = ride_qs.filter(date__gte=start_date)
    if end_date:
        attendance_qs = attendance_qs.filter(date__lte=end_date)
        ride_qs = ride_qs.filter(date__lte=end_date)
    if driver_id:
        attendance_qs = attendance_qs.filter(driver_id__in=driver_id)
        ride_qs = ride_qs.filter(driver_id__in=driver_id)
    if company_id:
        ride_qs = ride_qs.filter(company_id__in=company_id)
    if vehicle_number:
        ride_qs = ride_qs.filter(vehicle_number__in=vehicle_number)
        # Align attendance to drivers who actually drove this vehicle in range
        vehicle_driver_ids = ride_qs.values_list('driver_id', flat=True).distinct()
        attendance_qs = attendance_qs.filter(driver_id__in=vehicle_driver_ids)
    elif company_id:
        company_driver_ids = ride_qs.values_list('driver_id', flat=True).distinct()
        attendance_qs = attendance_qs.filter(driver_id__in=company_driver_ids)

    stats_agg = attendance_qs.aggregate(
        total_half_days=Count('id', filter=Q(status='Half')),
        total_full_days=Count('id', filter=Q(status='Full')),
    )

    d_start_parsed = date.fromisoformat(start_date) if start_date else None
    d_end_parsed = date.fromisoformat(end_date) if end_date else None
    if is_full_month(d_start_parsed, d_end_parsed):
        salary_start, salary_end = get_trailing_month_range(d_end_parsed.year, d_end_parsed.month)
        sal_qs = Attendance.objects.filter(date__gte=salary_start, date__lte=salary_end)
        if driver_id:
            sal_qs = sal_qs.filter(driver_id__in=driver_id)
        stats_agg['total_salary'] = sal_qs.aggregate(total_salary=Sum('salary'))['total_salary']
    else:
        stats_agg['total_salary'] = attendance_qs.aggregate(total_salary=Sum('salary'))['total_salary']


    # Advance salary stats
    advance_qs = AdvanceSalaryRequest.objects.select_related('driver').all()
    if driver_id:
        advance_qs = advance_qs.filter(driver_id__in=driver_id)
    
    # Notification count should show all pending requests for visibility
    pending_advance_count = advance_qs.filter(status='Pending').count()

    # Stats for the quick overview should respect the date filter
    paid_advances = advance_qs.filter(status='Paid')
    if start_date:
        paid_advances = paid_advances.filter(resolved_date__date__gte=start_date)
    if end_date:
        paid_advances = paid_advances.filter(resolved_date__date__lte=end_date)
    
    total_advance_paid = sum(float(a.amount) for a in paid_advances)
    advance_paid_drivers = list(set(a.driver.name for a in paid_advances))
    advance_details = [{
        'id': a.id,
        'driver_name': a.driver.name,
        'date': a.resolved_date.strftime('%Y-%m-%d') if a.resolved_date else '',
        'amount': float(a.amount),
        'reason': a.reason,
    } for a in paid_advances.order_by('-resolved_date')]

    # Company breakdown (deduped to avoid inflated counts from duplicate sync rows)
    company_breakdown_map = {}
    unique_rides = dedupe_ride_list(list(ride_qs.select_related('company')))
    for ride in unique_rides:
        name = ride.company.name if ride.company else 'Other'
        if name not in company_breakdown_map:
            company_breakdown_map[name] = {'count': 0, 'total_km': 0.0}
        company_breakdown_map[name]['count'] += 1
        company_breakdown_map[name]['total_km'] += float(ride.total_km or 0)

    company_breakdown = [
        {
            'name': name,
            'count': data['count'],
            'total_km': data['total_km'],
        }
        for name, data in sorted(company_breakdown_map.items(), key=lambda x: -x[1]['count'])
    ]

    # Charging stats
    charge_qs = CarCharge.objects.all()
    if start_date: charge_qs = charge_qs.filter(date__gte=start_date)
    if end_date: charge_qs = charge_qs.filter(date__lte=end_date)
    if driver_id: charge_qs = charge_qs.filter(driver_id__in=driver_id)
    if vehicle_number: charge_qs = charge_qs.filter(vehicle_number__in=vehicle_number)
    total_charging_cost = float(charge_qs.aggregate(total=Sum('charge_amount'))['total'] or 0)

    charge_list = [{
        'id': c.id,
        'driver_name': c.driver.name if c.driver else 'No Driver',
        'date': c.date.strftime('%Y-%m-%d'),
        'app_used': c.app_used,
        'time': c.time.isoformat() if c.time else '',
        'driver': c.driver_id,
        'place': c.place,
        'vehicle_number': c.vehicle_number,
        'charge_amount': float(c.charge_amount),
    } for c in charge_qs.select_related('driver').order_by('-date', '-time')]

    # Drivers target achieved (count drivers who have at least one 'Full' day in the period)
    drivers_target_achieved = attendance_qs.filter(status='Full').values('driver').distinct().count()
    # When filters are applied, show drivers present in the filtered ride set
    if company_id or vehicle_number or driver_id:
        total_active_drivers = ride_qs.values('driver_id').distinct().count()
    else:
        total_active_drivers = Driver.objects.filter(is_active=True).count()

    return Response({
        'total_rides': len(unique_rides),
        'total_drivers': total_active_drivers,
        'drivers_target_achieved': drivers_target_achieved,
        'total_half_days': stats_agg['total_half_days'] or 0,
        'total_full_days': stats_agg['total_full_days'] or 0,
        'total_salary_paid': stats_agg['total_salary'] or 0,
        'pending_advance_count': pending_advance_count,
        'total_advance_paid': total_advance_paid,
        'advance_paid_drivers': advance_paid_drivers,
        'advance_details': advance_details,
        'company_breakdown': company_breakdown,
        'total_charging_cost': total_charging_cost,
        'charging_details': charge_list,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_salary_summary(request):
    """All active drivers' current and previous salary-cycle earnings."""
    if not (request.user.is_staff or request.user.is_superuser):
        return Response({'error': 'Admin access required'}, status=403)

    drivers = list(Driver.objects.filter(is_active=True).order_by('name'))
    (current_start, current_end), (previous_start, previous_end) = get_current_and_previous_salary_cycles()
    current_summaries = build_salary_cycle_summaries(drivers, current_start, current_end)
    previous_summaries = build_salary_cycle_summaries(drivers, previous_start, previous_end)

    rows = [
        {
            'driver_id': driver.id,
            'driver_name': driver.name,
            'previous_cycle': previous_summaries[driver.id],
            'current_cycle': current_summaries[driver.id],
        }
        for driver in drivers
    ]

    def cycle_totals(summaries):
        return {
            'gross_earnings': round(sum(summary['gross_earnings'] for summary in summaries.values()), 2),
            'advance_paid': round(sum(summary['advance_paid'] for summary in summaries.values()), 2),
            'net_payable': round(sum(summary['net_payable'] for summary in summaries.values()), 2),
            'total_rides': sum(summary['total_rides'] for summary in summaries.values()),
        }

    return Response({
        'salary_rules': {
            'standard_ride_rate': round(SALARY_BASE_TRIP_VALUE, 2),
            'special_km_rate': SALARY_SPECIAL_KM_RATE,
            'incentive_trip_rate': SALARY_INCENTIVE_TRIP_VALUE,
            'incentive_threshold': SALARY_TARGET_TRIPS,
        },
        'previous_cycle': {
            'cycle_start': previous_start.isoformat(),
            'cycle_end': previous_end.isoformat(),
            'totals': cycle_totals(previous_summaries),
        },
        'current_cycle': {
            'cycle_start': current_start.isoformat(),
            'cycle_end': current_end.isoformat(),
            'totals': cycle_totals(current_summaries),
        },
        'drivers': rows,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reports(request):
    if not (request.user.is_staff or request.user.is_superuser):
        return Response({'error': 'Admin access required'}, status=403)

    start_date, end_date, driver_id, company_id, vehicle_number = admin_filters(request.query_params)

    from .filters import scope_rides
    rides = scope_rides(Ride.objects.select_related('company', 'driver'), start_date, end_date, driver_id, company_id, vehicle_number)
    grouped = {}
    for ride in dedupe_ride_list(list(rides.order_by('-date', 'driver__name', 'ride_time', 'created_at'))):
        grouped.setdefault((ride.driver_id, ride.date), []).append(ride)
    attendance = Attendance.objects.select_related('driver')
    if start_date:
        attendance = attendance.filter(date__gte=start_date)
    if end_date:
        attendance = attendance.filter(date__lte=end_date)
    if driver_id:
        attendance = attendance.filter(driver_id__in=driver_id)
    attendance_map = {(row.driver_id, row.date): row for row in attendance}
    keys = set(grouped) if company_id or vehicle_number else set(grouped) | set(attendance_map)
    results = []
    for key in sorted(keys, key=lambda value: (value[1], -value[0]), reverse=True):
        day_rides = grouped.get(key, [])
        att = attendance_map.get(key)
        driver = att.driver if att else day_rides[0].driver
        results.append({
            'driver_name': driver.name, 'driver_id': driver.pk, 'date': key[1],
            'total_rides': len(day_rides), 'status': att.status if att else '',
            'salary': att.salary if att else 0, 'rides': RideSerializer(day_rides, many=True).data,
        })
    return Response(results)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_excel(request):
    if not (request.user.is_staff or request.user.is_superuser):
        return Response({'error': 'Admin access required'}, status=403)

    start_date_str, end_date_str, driver_id, company_id, vehicle_number = admin_filters(request.query_params)

    # Parse date range
    try:
        d_start = date.fromisoformat(start_date_str) if start_date_str else date.today().replace(day=1)
        d_end = date.fromisoformat(end_date_str) if end_date_str else date.today()
    except ValueError:
        d_start = date.today().replace(day=1)
        d_end = date.today()

    # Generate all dates in range
    all_dates = []
    current = d_start
    while current <= d_end:
        all_dates.append(current)
        current += timedelta(days=1)

    scoped_filters = bool(company_id or vehicle_number)

    # Get drivers to include
    rides_scope = Ride.objects.filter(date__gte=d_start, date__lte=d_end)
    if driver_id:
        rides_scope = rides_scope.filter(driver_id__in=driver_id)
    if company_id:
        rides_scope = rides_scope.filter(company_id__in=company_id)
    if vehicle_number:
        rides_scope = rides_scope.filter(vehicle_number__in=vehicle_number)

    if driver_id:
        target_drivers = list(Driver.objects.filter(id__in=driver_id))
    elif scoped_filters:
        driver_ids = rides_scope.values_list('driver_id', flat=True).distinct()
        target_drivers = list(Driver.objects.filter(id__in=driver_ids, is_active=True).order_by('name'))
    else:
        target_drivers = list(Driver.objects.filter(is_active=True).order_by('name'))

    # Fetch attendance data
    att_qs = Attendance.objects.select_related('driver').filter(
        date__gte=d_start, date__lte=d_end
    )
    if driver_id:
        att_qs = att_qs.filter(driver_id__in=driver_id)
    elif scoped_filters:
        att_qs = att_qs.filter(driver_id__in=[d.id for d in target_drivers])

    # Build attendance lookup: {(driver_id, date): attendance}
    att_lookup = {}
    for att in att_qs:
        att_lookup[(att.driver_id, att.date)] = att

    # Fetch charges for the period
    charge_qs_all = CarCharge.objects.filter(date__gte=d_start, date__lte=d_end)
    if driver_id:
        charge_qs_all = charge_qs_all.filter(driver_id__in=driver_id)
    if vehicle_number:
        charge_qs_all = charge_qs_all.filter(vehicle_number__in=vehicle_number)

    # Fetch advance salary requests (Paid) for the period
    advance_qs_all = AdvanceSalaryRequest.objects.filter(
        status='Paid',
        resolved_date__date__gte=d_start,
        resolved_date__date__lte=d_end,
    )
    if driver_id:
        advance_qs_all = advance_qs_all.filter(driver_id__in=driver_id)

    full_rate = getattr(settings, 'FULL_DAY_RATE', 1000)
    half_rate = getattr(settings, 'HALF_DAY_RATE', 500)
    threshold = getattr(settings, 'FULL_DAY_THRESHOLD', 4)

    wb = openpyxl.Workbook()

    header_fill = PatternFill(start_color='1F3A5F', end_color='1F3A5F', fill_type='solid')
    charge_header_fill = PatternFill(start_color='D32F2F', end_color='D32F2F', fill_type='solid')
    advance_header_fill = PatternFill(start_color='1565C0', end_color='1565C0', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF', size=11)
    center_align = Alignment(horizontal='center')
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin'),
    )

    full_fill = PatternFill(start_color='E8F5E9', end_color='E8F5E9', fill_type='solid')
    half_fill = PatternFill(start_color='FFF9C4', end_color='FFF9C4', fill_type='solid')
    leave_fill = PatternFill(start_color='FFCDD2', end_color='FFCDD2', fill_type='solid')
    holiday_fill = PatternFill(start_color='E0E0E0', end_color='E0E0E0', fill_type='solid')

    rides_by_day = {}
    for ride in dedupe_ride_list(list(rides_scope.select_related('company', 'driver').order_by('ride_time', 'created_at'))):
        rides_by_day.setdefault((ride.driver_id, ride.date), []).append(ride)
    charges_by_driver = {}
    for charge in charge_qs_all:
        charges_by_driver.setdefault(charge.driver_id, []).append(charge)
    advances_by_driver = {}
    for advance in advance_qs_all:
        advances_by_driver.setdefault(advance.driver_id, []).append(advance)
    cycle_salary = {}
    if is_full_month(d_start, d_end):
        salary_start, salary_end = get_trailing_month_range(d_end.year, d_end.month)
        cycle_salary = {row['driver_id']: row['total'] for row in Attendance.objects.filter(
            driver_id__in=[driver.id for driver in target_drivers], date__range=(salary_start, salary_end)
        ).order_by().values('driver_id').annotate(total=Sum('salary'))}

    used_sheet_titles = set()
    first = True
    for drv in target_drivers:
        # 1. Rides Sheet
        sheet_name = make_unique_sheet_title(drv.name, used_sheet_titles, fallback=f'Driver {drv.id}')
        if first:
            ws = wb.active
            ws.title = sheet_name
            first = False
        else:
            ws = wb.create_sheet(title=sheet_name)

        # Title
        ws.merge_cells('A1:H1')
        title_cell = ws['A1']
        title_cell.value = f"Ride Manifest - {drv.name}"
        title_cell.font = Font(bold=True, size=13)
        title_cell.alignment = center_align

        # Headers
        headers = ['Date', 'No', 'Client', 'P/D', 'Time', 'Route', 'Notes', 'Total km', 'Status']
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=2, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_align
            cell.border = border

        total_salary = 0
        total_rides_count = 0
        row_idx = 3

        for d in all_dates:
            att = att_lookup.get((drv.id, d))
            is_weekend = d.weekday() in (5, 6)

            rides = rides_by_day.get((drv.id, d), [])

            if rides:
                day_status = att.status if att else ''
                day_salary = float(att.salary) if att and not scoped_filters else 0
                total_rides_count += len(rides)

                for ride_number, ride in enumerate(rides, 1):
                    route = ride.route or f"{ride.pickup} to {ride.drop}"
                    data = [
                        d.strftime('%d-%m-%Y'),
                        ride_number,
                        ride.company.name if ride.company else '',
                        ride.trip_type,
                        ride.ride_time.strftime('%I:%M %p').lstrip('0') if ride.ride_time else '',
                        route,
                        ride.notes or '',
                        float(ride.total_km) if ride.total_km is not None else '',
                        day_status if ride_number == 1 else '',
                    ]

                    row_fill = full_fill if day_status == 'Full' else half_fill
                    for col, val in enumerate(data, 1):
                        cell = ws.cell(row=row_idx, column=col, value=val)
                        cell.alignment = center_align if col not in (6, 7) else Alignment(horizontal='left')
                        cell.border = border
                        cell.fill = row_fill
                    row_idx += 1
                if not scoped_filters:
                    total_salary += day_salary
            elif not scoped_filters:
                # No rides: Leave or Holiday (only when not company/vehicle scoped)
                if is_weekend:
                    day_label = 'Holiday'
                    row_fill = holiday_fill
                else:
                    day_label = 'Leave'
                    row_fill = leave_fill

                data = [d.strftime('%d-%m-%Y'), '', '', '', '', '', '', '', day_label]
                for col, val in enumerate(data, 1):
                    cell = ws.cell(row=row_idx, column=col, value=val)
                    cell.alignment = center_align
                    cell.border = border
                    cell.fill = row_fill
                row_idx += 1

        # Rides Total Summary
        total_row = row_idx + 1
        ws.cell(row=total_row, column=1, value='TOTAL RIDES').font = Font(bold=True)
        ws.cell(row=total_row, column=2, value=total_rides_count).font = Font(bold=True)
        if is_full_month(d_start, d_end):
            salary_start, salary_end = get_trailing_month_range(d_end.year, d_end.month)
            total_salary = cycle_salary.get(drv.id, 0)

        ws.cell(row=total_row, column=7, value='TOTAL SALARY').font = Font(bold=True)
        ws.cell(row=total_row, column=9, value=total_salary).font = Font(bold=True)

        # 2. Charges Sheet
        charge_sheet_name = make_unique_sheet_title(
            f"{drv.name} - Charges", used_sheet_titles, fallback=f'Charges {drv.id}'
        )
        wsc = wb.create_sheet(title=charge_sheet_name)
        
        # Title
        wsc.merge_cells('A1:H1')
        title_cell_c = wsc['A1']
        title_cell_c.value = f"Car Charge Details - {drv.name}"
        title_cell_c.font = Font(bold=True, size=13, color='FFFFFF')
        title_cell_c.fill = charge_header_fill
        title_cell_c.alignment = center_align
        
        # Headers
        charge_headers = ['Date', 'App Used', 'Time', 'Place', 'Vehicle', '', '', 'Amount']
        for col, header in enumerate(charge_headers, 1):
            if header:
                cell = wsc.cell(row=2, column=col, value=header)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = center_align
                cell.border = border
        
        driver_charges = sorted(charges_by_driver.get(drv.id, []), key=lambda charge: (charge.date, charge.time))
        
        total_charges = 0
        crow_idx = 3
        for charge in driver_charges:
            data = [
                charge.date.strftime('%d-%m-%Y'),
                charge.app_used,
                charge.time.strftime('%I:%M %p').lstrip('0'),
                charge.place,
                charge.vehicle_number,
                None,
                None,
                float(charge.charge_amount)
            ]
            for col, val in enumerate(data, 1):
                if val is not None:
                    cell = wsc.cell(row=crow_idx, column=col, value=val)
                    cell.alignment = center_align
                    cell.border = border
            total_charges += float(charge.charge_amount)
            crow_idx += 1
            
        # Charge Total
        total_charges_row = crow_idx + 1
        wsc.cell(row=total_charges_row, column=1, value='TOTAL CHARGES').font = Font(bold=True)
        wsc.cell(row=total_charges_row, column=8, value=total_charges).font = Font(bold=True)

        # 3. Advance Salary on Rides Sheet
        driver_advances = advances_by_driver.get(drv.id, [])
        total_advance = sum(float(a.amount) for a in driver_advances)
        
        # 4. Final Summary on Rides Sheet
        summary_row = total_row + 2
        ws.merge_cells(f'A{summary_row}:I{summary_row}')
        summary_header = ws.cell(row=summary_row, column=1, value="PAYMENT SUMMARY")
        summary_header.font = Font(bold=True, size=11, color='FFFFFF')
        summary_header.fill = header_fill
        summary_header.alignment = center_align
        
        row_idx = summary_row + 1
        ws.cell(row=row_idx, column=1, value='Total Salary (A)').font = Font(bold=True)
        ws.cell(row=row_idx, column=9, value=total_salary)

        row_idx += 1
        ws.cell(row=row_idx, column=1, value='Advance Salary Paid (B)').font = Font(bold=True)
        adv_cell = ws.cell(row=row_idx, column=9, value=total_advance)
        adv_cell.font = Font(bold=True, color='1565C0')
        
        row_idx += 1
        ws.cell(row=row_idx, column=1, value='SALARY TO BE PAID (A - B)').font = Font(bold=True)
        net_cell = ws.cell(row=row_idx, column=9, value=total_salary - total_advance)
        net_cell.font = Font(bold=True, size=12, color='2E7D32')
        net_cell.border = Border(bottom=Side(style='double'))

        # Column widths for both sheets
        col_widths = [15, 15, 20, 8, 14, 34, 25, 12, 14]
        for i, width in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = width
            wsc.column_dimensions[get_column_letter(i)].width = width

    # Consolidated Charges Sheet (Only if charges exist)
    if charge_qs_all.exists():
        wsall = wb.create_sheet(title="All Car Charges", index=0)
        
        # Title
        wsall.merge_cells('A1:G1')
        title_cell_a = wsall['A1']
        title_cell_a.value = "Consolidated Car Charges - All Drivers"
        title_cell_a.font = Font(bold=True, size=14, color='FFFFFF')
        title_cell_a.fill = charge_header_fill
        title_cell_a.alignment = center_align
        
        # Headers
        all_headers = ['Date', 'Driver Name', 'App Used', 'Time', 'Place', 'Vehicle', 'Amount']
        for col, header in enumerate(all_headers, 1):
            cell = wsall.cell(row=2, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_align
            cell.border = border
            
        row_idx_a = 3
        total_all_charges = 0
        for charge in charge_qs_all.select_related('driver').order_by('date', 'time'):
            data = [
                charge.date.strftime('%d-%m-%Y'),
                charge.driver.name if charge.driver else 'No Driver',
                charge.app_used,
                charge.time.strftime('%I:%M %p').lstrip('0'),
                charge.place,
                charge.vehicle_number,
                float(charge.charge_amount)
            ]
            for col, val in enumerate(data, 1):
                cell = wsall.cell(row=row_idx_a, column=col, value=val)
                cell.alignment = center_align
                cell.border = border
            total_all_charges += float(charge.charge_amount)
            row_idx_a += 1
            
        # Total row
        row_idx_a += 1
        wsall.cell(row=row_idx_a, column=1, value='GRAND TOTAL').font = Font(bold=True)
        wsall.cell(row=row_idx_a, column=7, value=total_all_charges).font = Font(bold=True)
        
        # Widths
        col_widths_a = [15, 20, 20, 14, 30, 15, 15]
        for i, width in enumerate(col_widths_a, 1):
            wsall.column_dimensions[get_column_letter(i)].width = width

    # Consolidated All Rides Sheet (all drivers, all rides in one sheet)
    all_rides_qs = Ride.objects.select_related('company', 'driver').filter(
        date__gte=d_start, date__lte=d_end
    )
    if driver_id:
        all_rides_qs = all_rides_qs.filter(driver_id__in=driver_id)
    if company_id:
        all_rides_qs = all_rides_qs.filter(company_id__in=company_id)
    if vehicle_number:
        all_rides_qs = all_rides_qs.filter(vehicle_number__in=vehicle_number)

    all_rides_list = dedupe_ride_list(list(all_rides_qs.annotate(
        trip_type_order=Case(
            When(trip_type='P', then=Value(0)),
            When(trip_type='D', then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
    ).order_by('date', 'trip_type_order', 'ride_time', 'driver__name', 'created_at')))

    if all_rides_list:
        rides_fill = PatternFill(start_color='1B5E20', end_color='1B5E20', fill_type='solid')
        wsr = wb.create_sheet(title="All Rides", index=0)

        # Title
        wsr.merge_cells('A1:J1')
        title_cell_r = wsr['A1']
        title_cell_r.value = "Consolidated Rides - All Drivers"
        title_cell_r.font = Font(bold=True, size=14, color='FFFFFF')
        title_cell_r.fill = rides_fill
        title_cell_r.alignment = center_align

        # Headers
        rides_headers = ['Date', 'Driver Name', 'Client', 'P/D', 'Time', 'Route', 'Notes', 'Total km', 'Vehicle', 'Amount']
        for col, header in enumerate(rides_headers, 1):
            cell = wsr.cell(row=2, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_align
            cell.border = border

        row_idx_r = 3
        total_rides_all = 0
        for ride in all_rides_list:
            route = ride.route or f"{ride.pickup} to {ride.drop}"
            data = [
                ride.date.strftime('%d-%m-%Y'),
                ride.driver.name,
                ride.company.name if ride.company else '',
                ride.trip_type,
                ride.ride_time.strftime('%I:%M %p').lstrip('0') if ride.ride_time else '',
                route,
                ride.notes or '',
                float(ride.total_km) if ride.total_km is not None else '',
                ride.vehicle_number or '',
                '',
            ]
            for col, val in enumerate(data, 1):
                cell = wsr.cell(row=row_idx_r, column=col, value=val)
                cell.alignment = center_align if col not in (6, 7) else Alignment(horizontal='left')
                cell.border = border
            total_rides_all += 1
            row_idx_r += 1

        # Total row
        row_idx_r += 1
        wsr.cell(row=row_idx_r, column=1, value='TOTAL RIDES').font = Font(bold=True)
        wsr.cell(row=row_idx_r, column=3, value=total_rides_all).font = Font(bold=True)

        # Column widths
        col_widths_r = [15, 22, 20, 8, 14, 34, 25, 12, 14, 14]
        for i, width in enumerate(col_widths_r, 1):
            wsr.column_dimensions[get_column_letter(i)].width = width

    if not target_drivers:
        ws = wb.active
        ws.title = 'Report'
        ws['A1'] = 'No data found for the selected filters.'

    filename = f"ride_manifest"
    if start_date_str:
        filename += f"_{start_date_str}"
    if end_date_str:
        filename += f"_to_{end_date_str}"
    filename += ".xlsx"

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_monthly_report(request):
    if not (request.user.is_staff or request.user.is_superuser):
        return Response({'error': 'Admin access required'}, status=403)

    month_str = request.query_params.get('month')
    year_str = request.query_params.get('year')

    try:
        month = int(month_str)
        year = int(year_str)
        if not (1 <= month <= 12):
            raise ValueError()
    except (TypeError, ValueError):
        return Response({'error': 'Invalid month or year'}, status=400)

    import calendar

    _, last_day = calendar.monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    # Find companies that have rides in this period, and all active companies
    company_ids = Ride.objects.filter(
        date__gte=start_date, date__lte=end_date
    ).values_list('company_id', flat=True).distinct()
    
    companies_with_rides = Company.objects.filter(id__in=company_ids)
    active_companies = Company.objects.filter(is_active=True)
    companies = (companies_with_rides | active_companies).distinct().order_by('name')

    # Also check if there are any rides with no company (company=None)
    has_unassigned = Ride.objects.filter(
        date__gte=start_date, date__lte=end_date, company__isnull=True
    ).exists()

    wb = openpyxl.Workbook()
    # Remove default sheet to start clean
    default_sheet = wb.active
    wb.remove(default_sheet)

    # Styles
    rides_fill = PatternFill(start_color='1B5E20', end_color='1B5E20', fill_type='solid')
    header_fill = PatternFill(start_color='1F3A5F', end_color='1F3A5F', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF', size=11)
    center_align = Alignment(horizontal='center')
    left_align = Alignment(horizontal='left')
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin'),
    )

    headers = ['Date', 'Driver Name', 'Client', 'P/D', 'Time', 'Route', 'Total km', 'Vehicle']
    col_widths = [15, 22, 18, 8, 12, 35, 12, 14]

    def populate_sheet(ws, title, rides_list):
        # Merge A1:H1 for title banner
        ws.merge_cells('A1:H1')
        title_cell = ws['A1']
        title_cell.value = "Consolidated Rides - All Drivers"
        title_cell.font = Font(bold=True, size=14, color='FFFFFF')
        title_cell.fill = rides_fill
        title_cell.alignment = center_align
        ws.row_dimensions[1].height = 30

        # Headers
        ws.row_dimensions[2].height = 24
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=2, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_align
            cell.border = border

        # Data Rows
        row_idx = 3
        for ride in rides_list:
            ws.row_dimensions[row_idx].height = 20
            route = ride.route or f"{ride.pickup} to {ride.drop}"
            ride_time_str = ride.ride_time.strftime('%I:%M %p').lstrip('0') if ride.ride_time else ''
            
            data = [
                ride.date.strftime('%d-%m-%Y'),
                ride.driver.name,
                ride.company.name if ride.company else '',
                ride.trip_type,
                ride_time_str,
                route,
                float(ride.total_km) if ride.total_km is not None else '',
                ride.vehicle_number or '',
            ]

            for col, val in enumerate(data, 1):
                cell = ws.cell(row=row_idx, column=col, value=val)
                cell.alignment = left_align if col == 6 else center_align
                cell.border = border
            row_idx += 1

        # Column widths
        for i, width in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = width

    used_titles = set()

    # Create sheets for each company
    for comp in companies:
        rides_qs = Ride.objects.select_related('company', 'driver').filter(
            company=comp,
            date__gte=start_date,
            date__lte=end_date
        )
        rides_list = list(rides_qs.annotate(
            trip_type_order=Case(
                When(trip_type='P', then=Value(0)),
                When(trip_type='D', then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        ).order_by('date', 'trip_type_order', 'ride_time', 'driver__name', 'created_at'))

        # Only create sheet if there are rides
        if not rides_list:
            continue

        sheet_title = make_unique_sheet_title(comp.name, used_titles, fallback='Company')
        ws = wb.create_sheet(title=sheet_title)
        populate_sheet(ws, comp.name, rides_list)

    # Handle unassigned company rides if any
    if has_unassigned:
        rides_qs = Ride.objects.select_related('company', 'driver').filter(
            company__isnull=True,
            date__gte=start_date,
            date__lte=end_date
        )
        rides_list = list(rides_qs.annotate(
            trip_type_order=Case(
                When(trip_type='P', then=Value(0)),
                When(trip_type='D', then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        ).order_by('date', 'trip_type_order', 'ride_time', 'driver__name', 'created_at'))

        if rides_list:
            sheet_title = make_unique_sheet_title("Other", used_titles, fallback='Other')
            ws = wb.create_sheet(title=sheet_title)
            populate_sheet(ws, "Other", rides_list)

    # Fallback if no sheets were created
    if len(wb.sheetnames) == 0:
        ws = wb.create_sheet(title="Report")
        ws.merge_cells('A1:H1')
        title_cell = ws['A1']
        title_cell.value = "No rides recorded for this period."
        title_cell.font = Font(bold=True, size=12)
        title_cell.alignment = center_align

    # Filename
    month_name = calendar.month_name[month]
    filename = f"monthly_report_{month_name}_{year}.xlsx"

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_driver_excel(request):
    try:
        driver = request.user.driver_profile
    except Driver.DoesNotExist:
        return Response({'error': 'Driver profile not found'}, status=403)

    period = request.query_params.get('period')
    today = date.today()
    
    qs = Attendance.objects.filter(driver=driver)
    
    is_monthly = False
    if period == 'month':
        is_monthly = True
        qs = qs.filter(date__year=today.year, date__month=today.month)
        period_str = today.strftime('%b_%Y')
    elif period == 'year':
        qs = qs.filter(date__year=today.year)
        period_str = str(today.year)
    else:
        period_str = 'all_time'

    qs = qs.order_by('date')

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{driver.name[:20]}_{period_str}"

    header_fill = PatternFill(start_color='1F3A5F', end_color='1F3A5F', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF', size=11)
    center_align = Alignment(horizontal='center')
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin'),
    )

    ws.merge_cells('A1:I1')
    title_cell = ws['A1']
    title_cell.value = f"Ride Manifest - {driver.name} ({period_str.replace('_', ' ')})"
    title_cell.font = Font(bold=True, size=13)
    title_cell.alignment = center_align

    headers = ['Date', 'No', 'Client', 'P/D', 'Time', 'Route', 'Notes', 'Total km', 'Vehicle']
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center_align
        cell.border = border

    total_salary = 0
    row_idx = 3
    attendances = list(qs)
    
    for att in attendances:
        rides = list(Ride.objects.select_related('company').filter(driver=driver, date=att.date).order_by('ride_time', 'created_at')) or [None]

        for ride_number, ride in enumerate(rides, 1):
            if ride:
                route = ride.route or f"{ride.pickup} to {ride.drop}"
                data = [
                    att.date.strftime('%d-%m-%Y'),
                    ride_number,
                    ride.company.name if ride.company else '',
                    ride.trip_type,
                    ride.ride_time.strftime('%I:%M %p').lstrip('0') if ride.ride_time else '',
                    route,
                    ride.notes or '',
                    float(ride.total_km) if ride.total_km is not None else '',
                    ride.vehicle_number,
                ]
            else:
                data = [att.date.strftime('%d-%m-%Y'), '', '', '', '', '', '', '', '']

            for col, val in enumerate(data, 1):
                cell = ws.cell(row=row_idx, column=col, value=val)
                cell.alignment = center_align if col not in (6, 7) else Alignment(horizontal='left')
                cell.border = border
                if att.status == 'Full':
                    cell.fill = PatternFill(start_color='E8F5E9', end_color='E8F5E9', fill_type='solid')
                else:
                    cell.fill = PatternFill(start_color='FFF9C4', end_color='FFF9C4', fill_type='solid')
            row_idx += 1
        total_salary += float(att.salary)

    if is_monthly:
        cycle_end_year, cycle_end_month = get_cycle_end_year_month(today)
        salary_start, salary_end = get_trailing_month_range(cycle_end_year, cycle_end_month)
        sal_qs = Attendance.objects.filter(driver=driver, date__gte=salary_start, date__lte=salary_end)
        total_salary = sal_qs.aggregate(t=Sum('salary'))['t'] or 0

    total_row = row_idx + 1
    ws.cell(row=total_row, column=1, value='TOTAL RIDES').font = Font(bold=True)
    ws.cell(row=total_row, column=2, value=sum(a.total_rides for a in attendances)).font = Font(bold=True)
    salary_cell = ws.cell(row=total_row, column=6, value=f"Salary: {total_salary}")
    salary_cell.font = Font(bold=True)

    col_widths = [15, 8, 20, 8, 14, 34, 25, 12, 14]
    for i, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width

    if not attendances:
        ws = wb.active
        ws['A1'] = f"No rides found for {period_str}."

    filename = f"{driver.name.replace(' ', '_')}_Report_{period_str}.xlsx"

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_drivers(request):
    if not (request.user.is_staff or request.user.is_superuser):
        return Response({'error': 'Admin access required'}, status=403)
    drivers = Driver.objects.select_related('user')
    if request.query_params.get('include_inactive') != 'true':
        drivers = drivers.filter(is_active=True)
    return Response(DriverSerializer(drivers, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def driver_report_data(request):
    try:
        driver = request.user.driver_profile
    except Driver.DoesNotExist:
        return Response({'error': 'Driver profile not found'}, status=403)

    period = request.query_params.get('period')
    today = date.today()
    
    qs = Attendance.objects.filter(driver=driver)
    is_monthly = False
    if period == 'month':
        is_monthly = True
        qs = qs.filter(date__year=today.year, date__month=today.month)
    elif period == 'year':
        qs = qs.filter(date__year=today.year)
    
    qs = qs.order_by('date')
    
    rides_by_date = {}
    for ride in Ride.objects.select_related('company', 'driver').filter(driver=driver, date__in=qs.values('date')).order_by('ride_time', 'created_at'):
        rides_by_date.setdefault(ride.date, []).append(ride)
    results = []
    for att in qs:
        rides = RideSerializer(rides_by_date.get(att.date, []), many=True).data
        results.append({
            'date': att.date,
            'status': att.status,
            'total_rides': att.total_rides,
            'salary': att.salary,
            'rides': rides
        })
        
    return Response({
        'driver_name': driver.name,
        'period': period,
        'data': results
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_vehicles(request):
    vehicles = Vehicle.objects.filter(is_active=True)
    return Response(VehicleSerializer(vehicles, many=True).data)


@api_view(['POST'])
@permission_classes([IsAdminUser])
def create_vehicle(request):
    serializer = VehicleSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_default_vehicle(request):
    try:
        driver = request.user.driver_profile
    except Driver.DoesNotExist:
        return Response({'error': 'Driver profile not found'}, status=404)

    number = request.data.get('default_vehicle_number', '')
    seater = request.data.get('default_seater', 4)

    driver.default_vehicle_number = number
    driver.default_seater = seater
    driver.save()

    return Response({'message': 'Default vehicle updated successfully'})


# ──────────── Advance Salary Endpoints ────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def request_advance_salary(request):
    """Driver requests an advance salary."""
    try:
        driver = request.user.driver_profile
    except Driver.DoesNotExist:
        return Response({'error': 'Driver profile not found'}, status=404)

    serializer = AdvanceSalaryRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    advance = serializer.save(driver=driver, status='Pending')
    return Response(
        AdvanceSalaryRequestSerializer(advance).data,
        status=status.HTTP_201_CREATED
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_advance_requests(request):
    """Admin lists all advance salary requests. Drivers see only their own."""
    if request.user.is_staff or request.user.is_superuser:
        qs = AdvanceSalaryRequest.objects.select_related('driver').all()
        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
    else:
        try:
            driver = request.user.driver_profile
        except Driver.DoesNotExist:
            return Response({'error': 'Driver profile not found'}, status=404)
        qs = AdvanceSalaryRequest.objects.filter(driver=driver)

    return Response(AdvanceSalaryRequestSerializer(qs, many=True).data)


@api_view(['PATCH'])
@permission_classes([IsAdminUser])
def update_advance_request(request, pk):
    """Admin updates an advance salary request status."""
    try:
        advance = AdvanceSalaryRequest.objects.get(pk=pk)
    except AdvanceSalaryRequest.DoesNotExist:
        return Response({'error': 'Request not found'}, status=404)

    new_status = request.data.get('status')
    if new_status not in ('Paid', 'Rejected'):
        return Response({'error': 'Status must be Paid or Rejected.'}, status=400)

    advance.status = new_status
    advance.resolved_date = datetime.now()
    advance.save()

    return Response(AdvanceSalaryRequestSerializer(advance).data)

