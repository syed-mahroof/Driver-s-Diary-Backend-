import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone


def _password_cipher():
    # The key lives outside the database, so a leaked backup alone can't reveal saved passwords.
    digest = hashlib.sha256(f'driver-saved-password:{settings.SECRET_KEY}'.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class Driver(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='driver_profile')
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    default_vehicle_number = models.CharField(max_length=50, blank=True, default='')
    default_seater = models.IntegerField(default=4)
    # Encrypted copy of a password set by an admin, so it can be shared if the driver forgets it.
    saved_password_encrypted = models.TextField(blank=True, default='', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def remember_password(self, raw_password):
        self.saved_password_encrypted = _password_cipher().encrypt(raw_password.encode()).decode()

    def saved_password(self):
        """The admin-set password, or None if there isn't one or it no longer logs in."""
        if not self.saved_password_encrypted:
            return None
        try:
            password = _password_cipher().decrypt(self.saved_password_encrypted.encode()).decode()
        except InvalidToken:  # e.g. SECRET_KEY was changed
            return None
        return password if self.user.check_password(password) else None

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

    def save(self, *args, **kwargs):
        # Salary reports select paid advances by resolved_date, so it must always follow status.
        if self.status == 'Pending':
            self.resolved_date = None
        elif not self.resolved_date:
            self.resolved_date = timezone.now()
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-request_date']


class PasswordResetOTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_reset_otps')
    otp = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    def is_valid(self):
        return not self.is_used and (timezone.now() - self.created_at).total_seconds() < 600  # 10 minutes

    def __str__(self):
        return f"OTP for {self.user.username} at {self.created_at}"

    class Meta:
        ordering = ['-created_at']


class CarCharge(models.Model):
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='charges', null=True, blank=True)
    date = models.DateField(db_index=True)
    app_used = models.CharField(max_length=100)
    time = models.TimeField()
    place = models.CharField(max_length=200)
    vehicle_number = models.CharField(max_length=50)
    charge_amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        driver_name = self.driver.name if self.driver else "No Driver"
        return f"{driver_name} - {self.date} - {self.charge_amount}"

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


def sync_attendance(driver_id, day):
    """Attendance is derived from rides: recount the day, or drop the row once no rides remain."""
    total = Ride.objects.filter(driver_id=driver_id, date=day).count()
    if not total:
        Attendance.objects.filter(driver_id=driver_id, date=day).delete()
        return
    attendance = Attendance.objects.filter(driver_id=driver_id, date=day).first() or Attendance(driver_id=driver_id, date=day)
    attendance.total_rides = total
    attendance.save()


# Signals rather than view code, so the Django admin, bulk deletes and every API path stay in sync.
@receiver(pre_save, sender=Ride)
def remember_ride_day(sender, instance, raw=False, **kwargs):
    instance._previous_day = None
    if instance.pk and not raw:
        instance._previous_day = Ride.objects.filter(pk=instance.pk).values_list('driver_id', 'date').first()


@receiver(post_save, sender=Ride)
def attendance_after_ride_save(sender, instance, raw=False, **kwargs):
    if raw:
        return
    current = (instance.driver_id, instance.date)
    if instance._previous_day and instance._previous_day != current:
        sync_attendance(*instance._previous_day)
    sync_attendance(*current)


@receiver(post_delete, sender=Ride)
def attendance_after_ride_delete(sender, instance, **kwargs):
    sync_attendance(instance.driver_id, instance.date)


@receiver(post_save, sender=Driver)
def login_follows_driver_status(sender, instance, raw=False, **kwargs):
    if not raw:
        User.objects.filter(pk=instance.user_id).exclude(is_active=instance.is_active).update(is_active=instance.is_active)


@receiver(post_save, sender=User)
def driver_status_follows_login(sender, instance, raw=False, **kwargs):
    if not raw:
        Driver.objects.filter(user_id=instance.pk).exclude(is_active=instance.is_active).update(is_active=instance.is_active)
