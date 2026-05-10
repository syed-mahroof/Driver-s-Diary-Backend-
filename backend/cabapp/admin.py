from django.contrib import admin
from .models import Driver, Company, Ride, Attendance, PasswordResetOTP, CarCharge, Vehicle, AdvanceSalaryRequest


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'phone']


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name']


@admin.register(Ride)
class RideAdmin(admin.ModelAdmin):
    list_display = ['driver', 'company', 'date', 'trip_type', 'pickup', 'drop', 'created_at']
    list_filter = ['date', 'company', 'driver', 'trip_type']
    search_fields = ['driver__name', 'pickup', 'drop', 'local_id']
    date_hierarchy = 'date'


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ['driver', 'date', 'total_rides', 'status', 'salary']
    list_filter = ['status', 'date']
    search_fields = ['driver__name']
    date_hierarchy = 'date'


@admin.register(PasswordResetOTP)
class PasswordResetOTPAdmin(admin.ModelAdmin):
    list_display = ['user', 'otp', 'created_at', 'is_used']
    list_filter = ['is_used', 'created_at']
    search_fields = ['user__username']
    date_hierarchy = 'created_at'


@admin.register(CarCharge)
class CarChargeAdmin(admin.ModelAdmin):
    list_display = ['driver', 'date', 'time', 'app_used', 'place', 'charge_amount']
    list_filter = ['date', 'app_used', 'driver']
    search_fields = ['driver__name', 'app_used', 'place']
    date_hierarchy = 'date'


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ['number', 'seater', 'is_active', 'created_at']
    list_filter = ['seater', 'is_active']
    search_fields = ['number']


@admin.register(AdvanceSalaryRequest)
class AdvanceSalaryRequestAdmin(admin.ModelAdmin):
    list_display = ['driver', 'amount', 'status', 'request_date', 'resolved_date']
    list_filter = ['status', 'request_date']
    search_fields = ['driver__name']
    date_hierarchy = 'request_date'
