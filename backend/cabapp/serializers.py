from rest_framework import serializers
from django.contrib.auth.models import User
from django.db import transaction
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import Driver, Company, Ride, Attendance, CarCharge, Vehicle, AdvanceSalaryRequest


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'is_staff', 'is_superuser']


class CabTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['username'] = user.username
        token['is_staff'] = user.is_staff
        token['is_superuser'] = user.is_superuser
        return token


class DriverSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = Driver
        fields = ['id', 'user', 'name', 'phone', 'is_active', 'default_vehicle_number', 'default_seater']


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, min_length=6)
    name = serializers.CharField(max_length=200)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError('Username is already taken.')
        return value

    def validate_email(self, value):
        value = value.strip()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('An account with this email already exists.')
        return value

    def validate(self, data):
        return data

    @transaction.atomic
    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data.get('email', ''),
            password=validated_data['password'],
        )
        Driver.objects.create(
            user=user,
            name=validated_data['name'],
            phone=validated_data.get('phone', ''),
        )
        return user


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ['id', 'name', 'is_active']


class RideSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True)
    driver_name = serializers.CharField(source='driver.name', read_only=True)

    class Meta:
        model = Ride
        fields = ['id', 'local_id', 'driver', 'driver_name', 'company', 'company_name',
                  'date', 'ride_time', 'trip_type', 'route', 'pickup', 'drop',
                  'notes', 'total_km', 'vehicle_number', 'created_at']
        read_only_fields = ['id', 'created_at']


class RideCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ride
        fields = ['local_id', 'company', 'date', 'ride_time', 'trip_type', 'route',
                  'pickup', 'drop', 'notes', 'total_km', 'vehicle_number']
        extra_kwargs = {
            'pickup': {'required': False, 'allow_blank': True},
            'drop': {'required': False, 'allow_blank': True},
            'notes': {'required': False, 'allow_blank': True},
        }

    def create(self, validated_data):
        driver = self.context['request'].user.driver_profile
        validated_data['driver'] = driver
        return super().create(validated_data)


class SyncRideSerializer(serializers.Serializer):
    local_id = serializers.CharField(max_length=100)
    company_id = serializers.IntegerField(required=False, allow_null=True)
    date = serializers.DateField()
    ride_time = serializers.TimeField(required=False, allow_null=True)
    trip_type = serializers.ChoiceField(choices=['P', 'D'], required=False, default='P')
    route = serializers.CharField(max_length=500, required=False, allow_blank=True)
    pickup = serializers.CharField(max_length=500, required=False, allow_blank=True)
    drop = serializers.CharField(max_length=500, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    total_km = serializers.DecimalField(max_digits=7, decimal_places=2, required=False, allow_null=True)
    vehicle_number = serializers.CharField(max_length=50, required=False, allow_blank=True)


class SyncPayloadSerializer(serializers.Serializer):
    rides = SyncRideSerializer(many=True)


class AttendanceSerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source='driver.name', read_only=True)

    class Meta:
        model = Attendance
        fields = ['id', 'driver', 'driver_name', 'date', 'total_rides', 'status', 'salary']


class DriverDashboardSerializer(serializers.Serializer):
    driver_id = serializers.IntegerField()
    driver_name = serializers.CharField()
    today_rides = serializers.IntegerField()
    target_rides = serializers.IntegerField()
    status = serializers.CharField()
    salary_today = serializers.DecimalField(max_digits=10, decimal_places=2)
    progress_percentage = serializers.FloatField()
    recent_rides = RideSerializer(many=True)
    default_vehicle_number = serializers.CharField()
    default_seater = serializers.IntegerField()


class AdminDashboardSerializer(serializers.Serializer):
    total_rides = serializers.IntegerField()
    total_drivers = serializers.IntegerField()
    total_half_days = serializers.IntegerField()
    total_full_days = serializers.IntegerField()
    total_salary_paid = serializers.DecimalField(max_digits=12, decimal_places=2)


class ReportRowSerializer(serializers.Serializer):
    driver_name = serializers.CharField()
    date = serializers.DateField()
    total_rides = serializers.IntegerField()
    status = serializers.CharField()
    salary = serializers.DecimalField(max_digits=10, decimal_places=2)


class ForgotPasswordRequestSerializer(serializers.Serializer):
    contact = serializers.CharField(max_length=200)


class VerifyOTPSerializer(serializers.Serializer):
    contact = serializers.CharField(max_length=200)
    otp = serializers.CharField(max_length=6, min_length=6)


class ResetPasswordSerializer(serializers.Serializer):
    contact = serializers.CharField(max_length=200)
    otp = serializers.CharField(max_length=6, min_length=6)
    new_password = serializers.CharField(min_length=6)


class CarChargeSerializer(serializers.ModelSerializer):
    class Meta:
        model = CarCharge
        fields = ['id', 'driver', 'date', 'app_used', 'time', 'place', 'vehicle_number', 'charge_amount', 'created_at']
        read_only_fields = ['id', 'driver', 'created_at']

    def create(self, validated_data):
        driver = self.context['request'].user.driver_profile
        validated_data['driver'] = driver
        return super().create(validated_data)
class VehicleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = ['id', 'number', 'seater', 'is_active']


class AdvanceSalaryRequestSerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source='driver.name', read_only=True)

    class Meta:
        model = AdvanceSalaryRequest
        fields = ['id', 'driver', 'driver_name', 'amount', 'reason', 'status', 'request_date', 'resolved_date']
        read_only_fields = ['id', 'driver', 'driver_name', 'request_date', 'resolved_date']

