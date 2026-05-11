from django.db import models
from django.contrib.auth.models import User
from django.conf import settings


class Driver(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='driver_profile')
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    default_vehicle_number = models.CharField(max_length=50, blank=True, default='')
    default_seater = models.IntegerField(default=4)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class Company(models.Model):
    name = models.CharField(max_length=200, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Companies'


class Ride(models.Model):
    TRIP_TYPE_CHOICES = [
        ('P', 'Pickup'),
        ('D', 'Drop'),
    ]

    local_id = models.CharField(max_length=100, unique=True, db_index=True)
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='rides')
    company = models.ForeignKey(Company, on_delete=models.SET_NULL, null=True, related_name='rides')
    date = models.DateField(db_index=True)
    ride_time = models.TimeField(null=True, blank=True)
    trip_type = models.CharField(max_length=1, choices=TRIP_TYPE_CHOICES, default='P')
    route = models.CharField(max_length=500, blank=True)
    pickup = models.CharField(max_length=500, blank=True, default='')
    drop = models.CharField(max_length=500, blank=True, default='')
    notes = models.TextField(blank=True)
    total_km = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    vehicle_number = models.CharField(max_length=50, blank=True)
    requested_seater = models.IntegerField(default=4)
    created_at = models.DateTimeField(auto_now_add=True)
    synced_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.driver.name} - {self.date} - {self.pickup} to {self.drop}"

    class Meta:
        ordering = ['-date', '-created_at']
        indexes = [
            models.Index(fields=['driver', 'date']),
            models.Index(fields=['date', 'company']),
        ]


class Attendance(models.Model):
    STATUS_CHOICES = [
        ('Half', 'Half Day'),
        ('Full', 'Full Day'),
        ('Leave', 'Leave'),
        ('Holiday', 'Holiday'),
    ]

    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField(db_index=True)
    total_rides = models.IntegerField(default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Half')
    salary = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        unique_together = ('driver', 'date')
        ordering = ['-date']

    def __str__(self):
        return f"{self.driver.name} - {self.date} - {self.status}"

    def calculate_salary(self):
        threshold = getattr(settings, 'FULL_DAY_THRESHOLD', 4)
        full_rate = getattr(settings, 'FULL_DAY_RATE', 1000)
        half_rate = getattr(settings, 'HALF_DAY_RATE', 500)

        # Check if weekend (Saturday=5, Sunday=6)
        if self.date.weekday() in (5, 6):
            self.status = 'Holiday'
            self.salary = 0
        elif self.total_rides == 0:
            self.status = 'Leave'
            self.salary = 0
        elif self.total_rides >= threshold:
            self.status = 'Full'
            self.salary = full_rate
        else:
            self.status = 'Half'
            self.salary = half_rate
        return self

    def save(self, *args, **kwargs):
        self.calculate_salary()
        super().save(*args, **kwargs)


class AdvanceSalaryRequest(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Paid', 'Paid'),
        ('Rejected', 'Rejected'),
    ]

    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='advance_requests')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=500, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Pending')
    request_date = models.DateTimeField(auto_now_add=True)
    resolved_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.driver.name} - Rs {self.amount} - {self.status}"

    class Meta:
        ordering = ['-request_date']


class PasswordResetOTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_reset_otps')
    otp = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    def is_valid(self):
        from django.utils import timezone
        return not self.is_used and (timezone.now() - self.created_at).total_seconds() < 600  # 10 minutes

    def __str__(self):
        return f"OTP for {self.user.username} at {self.created_at}"

    class Meta:
        ordering = ['-created_at']


class CarCharge(models.Model):
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='charges')
    date = models.DateField(db_index=True)
    app_used = models.CharField(max_length=100)
    time = models.TimeField()
    place = models.CharField(max_length=200)
    vehicle_number = models.CharField(max_length=50)
    charge_amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.driver.name} - {self.date} - {self.charge_amount}"

    class Meta:
        ordering = ['-date', '-time']
        indexes = [
            models.Index(fields=['driver', 'date']),
        ]


class Vehicle(models.Model):
    SEATER_CHOICES = [
        (4, '4 Seater'),
        (6, '6 Seater'),
    ]
    number = models.CharField(max_length=50, unique=True)
    seater = models.IntegerField(choices=SEATER_CHOICES, default=4)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.number} ({self.seater} Seater)"

    class Meta:
        ordering = ['seater', 'number']
