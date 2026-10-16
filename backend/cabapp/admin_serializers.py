from datetime import datetime, time
from decimal import Decimal
from django.contrib.auth.models import User
from django.db import transaction
from rest_framework import serializers
from .models import Driver, Ride, CarCharge, AdvanceSalaryRequest


class AdminDriverSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(min_length=6, write_only=True, required=False)
    is_active = serializers.BooleanField(required=False)
    default_vehicle_number = serializers.CharField(max_length=50, required=False, allow_blank=True)
    default_seater = serializers.ChoiceField(choices=[4, 6], required=False)

    def validate(self, attrs):
        existing = self.instance
        users = User.objects.exclude(pk=existing.user_id) if existing else User.objects.all()
        if 'username' in attrs and users.filter(username=attrs['username']).exists():
            raise serializers.ValidationError({'username': 'Username is already taken.'})
        email = attrs.get('email', existing.user.email if existing else '').strip()
        phone = attrs.get('phone', existing.phone if existing else '').strip()
        if not email and not phone:
            raise serializers.ValidationError({'phone': 'Provide a phone number or email.'})
        if email and users.filter(email__iexact=email).exists():
            raise serializers.ValidationError({'email': 'Email is already registered.'})
        if not existing and not attrs.get('password'):
            raise serializers.ValidationError({'password': 'Password is required.'})
        return attrs

    @transaction.atomic
    def create(self, attrs):
        user = User.objects.create_user(username=attrs.pop('username'), email=attrs.pop('email', ''), password=attrs.pop('password'))
        driver = Driver.objects.create(user=user, **attrs)
        user.is_active = driver.is_active
        user.save(update_fields=['is_active'])
        return driver

    @transaction.atomic
    def update(self, instance, attrs):
        user = instance.user
        for field in ('username', 'email'):
            if field in attrs:
                setattr(user, field, attrs.pop(field))
        password = attrs.pop('password', None)
        if password:
            user.set_password(password)
        for key, value in attrs.items():
            setattr(instance, key, value)
        user.is_active = instance.is_active
        user.save()
        instance.save()
        return instance


class AdminRideSerializer(serializers.ModelSerializer):
    driver = serializers.PrimaryKeyRelatedField(queryset=Driver.objects.all())
    total_km = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=Decimal('0'), allow_null=True, required=False)
    requested_seater = serializers.ChoiceField(choices=[4, 6], required=False)

    class Meta:
        model = Ride
        fields = ['driver', 'company', 'date', 'ride_time', 'trip_type', 'route', 'pickup', 'drop', 'notes', 'total_km', 'vehicle_number', 'requested_seater']

    def validate_driver(self, driver):
        if not driver.is_active and (not self.instance or self.instance.driver_id != driver.id):
            raise serializers.ValidationError('Select an active driver.')
        return driver


class AdminChargeSerializer(serializers.ModelSerializer):
    charge_amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))

    class Meta:
        model = CarCharge
        fields = ['id', 'driver', 'date', 'app_used', 'time', 'place', 'vehicle_number', 'charge_amount', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate_driver(self, driver):
        if driver and not driver.is_active and (not self.instance or self.instance.driver_id != driver.id):
            raise serializers.ValidationError('Select an active driver.')
        return driver


class AdminAdvanceSerializer(serializers.ModelSerializer):
    paid_date = serializers.DateField(write_only=True, required=False)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))

    class Meta:
        model = AdvanceSalaryRequest
        fields = ['driver', 'amount', 'reason', 'status', 'paid_date']

    def validate(self, attrs):
        if not attrs['driver'].is_active:
            raise serializers.ValidationError({'driver': 'Select an active driver.'})
        if attrs.get('status') == 'Rejected':
            raise serializers.ValidationError({'status': 'Record a paid advance or a pending request.'})
        if attrs.get('paid_date') and attrs.get('status', 'Pending') != 'Paid':
            raise serializers.ValidationError({'paid_date': 'Payment date applies only to paid advances.'})
        if attrs.get('paid_date') and attrs['paid_date'] > datetime.now().date():
            raise serializers.ValidationError({'paid_date': 'Payment date cannot be in the future.'})
        return attrs

    def create(self, attrs):
        paid_date = attrs.pop('paid_date', None)
        if attrs.get('status') == 'Paid':
            attrs['resolved_date'] = datetime.combine(paid_date, time(12)) if paid_date else datetime.now()
        return super().create(attrs)
