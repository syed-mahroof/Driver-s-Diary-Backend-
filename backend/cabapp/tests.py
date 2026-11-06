from datetime import date, timedelta
from io import BytesIO
from importlib import import_module
from unittest.mock import patch
from uuid import uuid4
import openpyxl
from django.apps import apps as global_apps
from django.contrib.admin.models import LogEntry, DELETION
from django.contrib.auth.models import Permission, User
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase
from .models import Driver, Company, Vehicle, Ride, Attendance, CarCharge, AdvanceSalaryRequest
from .views import get_current_and_previous_salary_cycles

WEDNESDAY = date(2026, 11, 4)


class AdminWorkspaceTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('admin', password='admin-test', is_staff=True)
        cls.user = User.objects.create_user('driver', password='driver-test')
        cls.driver = Driver.objects.create(user=cls.user, name='Driver One', phone='123')
        cls.other = Driver.objects.create(user=User.objects.create_user('other'), name='Driver Two', phone='456')
        cls.company = Company.objects.create(name='Cognizant')
        cls.special = Company.objects.create(name='Zellis')
        cls.vehicle = Vehicle.objects.create(number='0622')
        cls.today = date.today()

    def setUp(self):
        self.client.force_authenticate(self.admin)

    def ride(self, driver=None, **kwargs):
        values = {'local_id': str(uuid4()), 'driver': driver or self.driver, 'company': self.company,
                  'date': self.today, 'vehicle_number': self.vehicle.number, 'route': str(uuid4())}
        values.update(kwargs)
        return Ride.objects.create(**values)

    def test_admin_can_create_and_move_ride_and_recalculate_both_days(self):
        response = self.client.post('/api/admin/rides/create/', {'driver': self.driver.pk, 'company': self.company.pk,
            'date': str(self.today), 'vehicle_number': '0622', 'requested_seater': 6, 'total_km': '10.50'})
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['id']
        response = self.client.patch(f'/api/admin/rides/{pk}/', {'driver': self.other.pk, 'date': str(self.today - timedelta(days=1))})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(Attendance.objects.filter(driver=self.driver, date=self.today).exists())
        self.assertEqual(Attendance.objects.get(driver=self.other, date=self.today - timedelta(days=1)).total_rides, 1)
        self.assertEqual(Ride.objects.get(pk=pk).requested_seater, 6)
        self.assertEqual(LogEntry.objects.filter(user=self.admin).count(), 2)

    def test_new_admin_endpoints_forbid_drivers(self):
        self.client.force_authenticate(self.user)
        for method, path in [('post', '/api/admin/rides/create/'), ('patch', '/api/admin/rides/1/'), ('delete', '/api/admin/rides/1/'),
                             ('post', '/api/admin/charge/create/'), ('patch', '/api/admin/charge/1/'),
                             ('post', '/api/admin/advance-salary/create/'), ('post', '/api/admin/drivers/create/'),
                             ('patch', f'/api/admin/drivers/{self.other.pk}/'), ('delete', f'/api/admin/drivers/{self.other.pk}/'),
                             ('get', '/api/admin/performance/')]:
            response = getattr(self.client, method)(path, {})
            self.assertEqual(response.status_code, 403, path)

    def test_anonymous_cannot_access_admin(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/admin/performance/').status_code, 401)

    def test_create_driver_with_phone_only_and_edit_login_details(self):
        response = self.client.post('/api/admin/drivers/create/', {'name': 'New driver', 'username': 'new', 'phone': '12345', 'password': 'secret123'})
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['driver']['id']
        response = self.client.patch(f'/api/admin/drivers/{pk}/', {'name': 'Updated', 'username': 'updated', 'email': 'updated@example.test', 'password': 'changed123', 'default_seater': 6})
        self.assertEqual(response.status_code, 200, response.data)
        driver = Driver.objects.get(pk=pk)
        self.assertEqual(driver.user.username, 'updated')
        self.assertTrue(driver.user.check_password('changed123'))
        self.assertEqual(driver.default_seater, 6)

    def test_duplicate_username_and_email_and_missing_contact_rejected(self):
        payload = {'name': 'New', 'username': 'driver', 'phone': '123', 'password': 'secret123'}
        self.assertEqual(self.client.post('/api/admin/drivers/create/', payload).status_code, 400)
        self.assertEqual(self.client.patch(f'/api/admin/drivers/{self.driver.pk}/', {'phone': '', 'email': ''}).status_code, 400)
        self.client.patch(f'/api/admin/drivers/{self.driver.pk}/', {'email': 'a@example.test'})
        self.assertEqual(self.client.patch(f'/api/admin/drivers/{self.other.pk}/', {'email': 'A@example.test'}).status_code, 400)

    def test_delete_preserves_records_disables_login_and_can_be_restored(self):
        ride = self.ride()
        self.assertEqual(self.client.delete(f'/api/admin/drivers/{self.driver.pk}/').status_code, 204)
        self.assertTrue(Ride.objects.filter(pk=ride.pk).exists())
        self.assertFalse(User.objects.get(pk=self.user.pk).is_active)
        self.assertNotIn(self.driver.pk, [d['id'] for d in self.client.get('/api/admin/drivers/').data])
        self.assertIn(self.driver.pk, [d['id'] for d in self.client.get('/api/admin/drivers/?include_inactive=true').data])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post('/api/login/', {'username': 'driver', 'password': 'driver-test'}).status_code, 401)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.patch(f'/api/admin/drivers/{self.driver.pk}/', {'is_active': True}).status_code, 200)
        self.assertTrue(User.objects.get(pk=self.user.pk).is_active)

    def test_charge_create_edit_reassign_and_validation(self):
        payload = {'driver': self.driver.pk, 'date': str(self.today), 'app_used': 'Tata Power', 'time': '10:30', 'place': 'Kochi', 'vehicle_number': '0622', 'charge_amount': '225.50'}
        response = self.client.post('/api/admin/charge/create/', payload)
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['id']
        self.assertEqual(self.client.patch(f'/api/admin/charge/{pk}/', {'driver': self.other.pk, 'charge_amount': '250'}).status_code, 200)
        self.assertEqual(CarCharge.objects.get(pk=pk).driver_id, self.other.pk)
        self.assertEqual(self.client.patch(f'/api/admin/charge/{pk}/', {'charge_amount': '-1'}).status_code, 400)

    def test_admin_advance_uses_payment_date_and_pending_not_deducted(self):
        (start, end), _ = get_current_and_previous_salary_cycles()
        payload = {'driver': self.driver.pk, 'amount': '500.25', 'status': 'Paid', 'paid_date': str(start)}
        response = self.client.post('/api/admin/advance-salary/create/', payload)
        self.assertEqual(response.status_code, 201, response.data)
        advance = AdvanceSalaryRequest.objects.get(pk=response.data['id'])
        self.assertEqual(advance.resolved_date.date(), start)
        pending = self.client.post('/api/admin/advance-salary/create/', {'driver': self.driver.pk, 'amount': '200', 'status': 'Pending'})
        self.assertEqual(pending.status_code, 201)
        summary = self.client.get('/api/admin/salary-summary/').data['drivers'][0]['current_cycle']
        self.assertEqual(summary['advance_paid'], 500.25)
        payload['amount'] = 'NaN'
        self.assertEqual(self.client.post('/api/admin/advance-salary/create/', payload).status_code, 400)
        payload['amount'] = 500
        payload['paid_date'] = str(self.today + timedelta(days=1))
        self.assertEqual(self.client.post('/api/admin/advance-salary/create/', payload).status_code, 400)

    def test_multiselect_union_within_filters_intersection_across_filters(self):
        first = self.ride(total_km=10)
        second = self.ride(driver=self.other, company=self.special, total_km=20, vehicle_number='0656')
        self.ride(driver=self.other, vehicle_number='9999')
        params = {'start_date': str(self.today), 'end_date': str(self.today), 'driver_id': f'{self.driver.pk},{self.other.pk}', 'company_id': f'{self.company.pk},{self.special.pk}', 'vehicle_number': '0622,0656'}
        response = self.client.get('/api/admin/dashboard/', params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['total_rides'], 2)
        reports = self.client.get('/api/reports/', params).data
        self.assertEqual({ride['id'] for row in reports for ride in row['rides']}, {first.pk, second.pk})
        exported = self.client.get('/api/export-excel/', params)
        self.assertEqual(exported.status_code, 200)
        workbook = openpyxl.load_workbook(BytesIO(exported.content))
        text = str([[row for row in sheet.values] for sheet in workbook])
        self.assertIn('0622', text)
        self.assertIn('0656', text)
        self.assertNotIn('9999', text)

    def test_single_filter_backward_compatible_and_repeated_filters(self):
        self.ride()
        self.ride(driver=self.other)
        self.assertEqual(self.client.get('/api/admin/dashboard/', {'driver_id': str(self.driver.pk)}).data['total_rides'], 1)
        self.assertEqual(self.client.get(f'/api/admin/dashboard/?driver_id[]={self.driver.pk}&driver_id[]={self.other.pk}').data['total_rides'], 2)

    def test_invalid_filters_return_validation_errors(self):
        for endpoint in ('/api/admin/dashboard/', '/api/reports/', '/api/export-excel/', '/api/admin/performance/'):
            for params in ({'driver_id': 'bad'}, {'start_date': 'invalid'}, {'start_date': '2026-10-20', 'end_date': '2026-10-01'}):
                self.assertEqual(self.client.get(endpoint, params).status_code, 400, (endpoint, params))

    def test_performance_averages_include_zero_days_and_zero_activity_drivers(self):
        self.ride(date=self.today - timedelta(days=1), total_km=10)
        self.ride(date=self.today - timedelta(days=1), total_km=15)
        response = self.client.get('/api/admin/performance/', {'start_date': str(self.today - timedelta(days=1)), 'end_date': str(self.today)})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['summary']['trips'], 2)
        row = response.data['drivers'][0]
        self.assertEqual(row['avg_trips_per_day'], 1)
        self.assertEqual(row['avg_trips_per_active_day'], 2)
        self.assertEqual(row['total_km'], 25)
        self.assertEqual(response.data['daily_trend'][-1]['trips'], 0)
        self.assertEqual(response.data['drivers'][1]['trips'], 0)

    def test_performance_company_scope_does_not_change_salary_cycle(self):
        self.ride(total_km=10)
        self.ride(company=self.special, total_km=20)
        response = self.client.get('/api/admin/performance/', {'start_date': str(self.today), 'end_date': str(self.today), 'company_id': str(self.special.pk)})
        row = response.data['drivers'][0]
        self.assertEqual(row['trips'], 1)
        self.assertEqual(row['ride_earnings'], 70)
        self.assertEqual(row['current_cycle']['total_rides'], 2)

    def test_report_query_count_constant_as_days_increase(self):
        self.ride()
        with CaptureQueriesContext(connection) as queries:
            self.client.get('/api/reports/')
        initial = len(queries)
        for day in range(1, 6):
            self.ride(date=self.today - timedelta(days=day))
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/api/reports/')
        self.assertEqual(len(queries), initial)
        self.assertEqual(len(response.data), 6)

    def test_offline_sync_preserves_seater_and_acknowledges_retries(self):
        self.client.force_authenticate(self.user)
        payload = {'rides': [{'local_id': 'offline-1', 'date': str(self.today), 'company_id': self.company.pk, 'requested_seater': 6}]}
        first = self.client.post('/api/sync-rides/', payload, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(Ride.objects.get(local_id='offline-1').requested_seater, 6)
        second = self.client.post('/api/sync-rides/', payload, format='json')
        self.assertEqual(second.data['skipped'], ['offline-1'])
        self.assertEqual(Ride.objects.count(), 1)

    def test_driver_cannot_read_other_ride_by_reusing_local_id(self):
        ride = self.ride(driver=self.other)
        self.client.force_authenticate(self.user)
        response = self.client.post('/api/rides/', {'local_id': ride.local_id, 'date': str(self.today)})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('driver_name', response.data)

    def test_driver_advance_invalid_amount_never_crashes_or_pays_itself(self):
        self.client.force_authenticate(self.user)
        for amount in ('bad', 'NaN', '-1', '0'):
            self.assertEqual(self.client.post('/api/advance-salary/request/', {'amount': amount}).status_code, 400)
        response = self.client.post('/api/advance-salary/request/', {'amount': '100', 'status': 'Paid'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['status'], 'Pending')

    def test_exports_queries_do_not_grow_per_driver_day(self):
        self.ride()
        params = {'start_date': str(self.today - timedelta(days=7)), 'end_date': str(self.today)}
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get('/api/export-excel/', params).status_code, 200)
        initial = len(queries)
        for offset in range(1, 6):
            self.ride(driver=self.other, date=self.today - timedelta(days=offset))
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get('/api/export-excel/', params).status_code, 200)
        self.assertEqual(len(queries), initial)

    def test_performance_queries_do_not_grow_per_driver(self):
        self.ride()
        with CaptureQueriesContext(connection) as queries:
            self.client.get('/api/admin/performance/')
        initial = len(queries)
        extra = Driver.objects.create(user=User.objects.create_user('extra'), name='Extra')
        self.ride(driver=extra)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get('/api/admin/performance/').status_code, 200)
        self.assertEqual(len(queries), initial)

    def test_removed_driver_existing_jwt_no_longer_grants_access(self):
        self.client.force_authenticate(None)
        token = self.client.post('/api/login/', {'username': 'driver', 'password': 'driver-test'}).data['access']
        self.client.force_authenticate(self.admin)
        self.client.delete(f'/api/admin/drivers/{self.driver.pk}/')
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(self.client.get('/api/driver/dashboard/').status_code, 401)

    def test_missing_records_and_incorrect_methods_do_not_create_entries(self):
        for endpoint in ('rides', 'charge'):
            self.assertEqual(self.client.patch(f'/api/admin/{endpoint}/0/', {}).status_code, 404)
            self.assertEqual(self.client.post(f'/api/admin/{endpoint}/0/', {}).status_code, 405)
            self.assertEqual(self.client.patch(f'/api/admin/{endpoint}/create/', {}).status_code, 405)
        self.assertEqual(Ride.objects.count(), 0)
        self.assertEqual(CarCharge.objects.count(), 0)


class DerivedDataStaysInSyncTests(APITestCase):
    """Every write path (API, Django admin, bulk delete, cascades) must keep derived records correct."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('admin', password='admin-test', is_staff=True)
        cls.user = User.objects.create_user('driver', password='driver-test', email='driver@example.test')
        cls.driver = Driver.objects.create(user=cls.user, name='Driver One', phone='123')
        cls.other = Driver.objects.create(user=User.objects.create_user('other'), name='Driver Two', phone='123')
        cls.company = Company.objects.create(name='Cognizant')

    def setUp(self):
        cache.clear()
        self.client.force_authenticate(self.admin)

    def ride(self, driver=None, day=WEDNESDAY, **kwargs):
        return Ride.objects.create(local_id=str(uuid4()), driver=driver or self.driver, company=self.company,
                                   date=day, vehicle_number='0622', route=str(uuid4()), **kwargs)

    def manifest(self):
        return self.client.get('/api/reports/', {'start_date': str(WEDNESDAY), 'end_date': str(WEDNESDAY)}).data

    def test_deleting_a_ride_outside_the_api_removes_driver_from_manifest(self):
        ride = self.ride()
        self.assertEqual(len(self.manifest()), 1)
        ride.delete()
        self.assertEqual(self.manifest(), [])
        self.assertFalse(Attendance.objects.filter(driver=self.driver).exists())

    def test_bulk_delete_like_django_admin_action_removes_driver_from_manifest(self):
        self.ride(), self.ride()
        Ride.objects.filter(driver=self.driver).delete()
        self.assertEqual(self.manifest(), [])

    def test_deleting_one_ride_recalculates_status_and_salary(self):
        rides = [self.ride() for _ in range(4)]
        self.assertEqual(Attendance.objects.get(driver=self.driver, date=WEDNESDAY).status, 'Full')
        rides[0].delete()
        attendance = Attendance.objects.get(driver=self.driver, date=WEDNESDAY)
        self.assertEqual((attendance.total_rides, attendance.status, attendance.salary), (3, 'Half', 500))

    def test_ride_added_or_moved_outside_the_api_updates_both_days(self):
        ride = self.ride()
        self.assertEqual(Attendance.objects.get(driver=self.driver, date=WEDNESDAY).total_rides, 1)
        ride.date, ride.driver = WEDNESDAY - timedelta(days=1), self.other
        ride.save()
        self.assertFalse(Attendance.objects.filter(driver=self.driver).exists())
        self.assertEqual(Attendance.objects.get(driver=self.other, date=ride.date).total_rides, 1)

    def test_deleting_a_driver_with_rides_does_not_crash(self):
        driver = Driver.objects.create(user=User.objects.create_user('temp'), name='Temp')
        self.ride(driver=driver)
        driver.user.delete()
        self.assertFalse(Ride.objects.filter(driver_id=driver.pk).exists())

    def test_driver_edit_moving_ride_leaves_no_phantom_day(self):
        ride = self.ride()
        self.client.force_authenticate(self.user)
        response = self.client.patch(f'/api/rides/{ride.pk}/update/', {'date': str(WEDNESDAY - timedelta(days=1))})
        self.assertEqual(response.status_code, 200, response.data)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.manifest(), [])

    def test_explicit_leave_without_rides_stays_on_manifest(self):
        Attendance.objects.create(driver=self.driver, date=WEDNESDAY)
        self.assertEqual(self.manifest()[0]['status'], 'Leave')

    def test_migration_repairs_existing_stale_rows(self):
        migration = import_module('cabapp.migrations.0011_reconcile_derived_data')
        orphan_day, wrong_day, missing_day = WEDNESDAY - timedelta(days=1), WEDNESDAY, WEDNESDAY - timedelta(days=2)
        self.ride(day=wrong_day), self.ride(day=missing_day)
        Attendance.objects.filter(date=missing_day).delete()
        Attendance.objects.filter(date=wrong_day).update(total_rides=5, status='Full', salary=1000)
        Attendance.objects.bulk_create([
            Attendance(driver=self.driver, date=orphan_day, total_rides=1, status='Half', salary=500),
            Attendance(driver=self.other, date=WEDNESDAY, total_rides=0, status='Leave', salary=0),
        ])
        advance = AdvanceSalaryRequest.objects.create(driver=self.driver, amount=100)
        AdvanceSalaryRequest.objects.filter(pk=advance.pk).update(status='Paid', resolved_date=None)
        migration.reconcile_derived_data(global_apps, None)
        self.assertFalse(Attendance.objects.filter(driver=self.driver, date=orphan_day).exists())
        fixed = Attendance.objects.get(driver=self.driver, date=wrong_day)
        self.assertEqual((fixed.total_rides, fixed.status, fixed.salary), (1, 'Half', 500))
        self.assertEqual(Attendance.objects.get(driver=self.driver, date=missing_day).total_rides, 1)
        self.assertTrue(Attendance.objects.filter(driver=self.other, date=WEDNESDAY, status='Leave').exists())
        advance.refresh_from_db()
        self.assertEqual(advance.resolved_date, advance.request_date)

    def test_admin_can_delete_driver_ride_and_it_is_audited(self):
        keep, remove = self.ride(), self.ride()
        response = self.client.delete(f'/api/admin/rides/{remove.pk}/')
        self.assertEqual(response.status_code, 204)
        self.assertEqual(list(Ride.objects.values_list('pk', flat=True)), [keep.pk])
        self.assertEqual(Attendance.objects.get(driver=self.driver, date=WEDNESDAY).total_rides, 1)
        self.assertEqual(LogEntry.objects.get(user=self.admin).action_flag, DELETION)

    def test_deleting_last_ride_clears_day_and_duplicates_do_not_reappear(self):
        first = self.ride()
        Ride.objects.create(local_id=str(uuid4()), driver=self.driver, company=self.company, date=WEDNESDAY,
                            vehicle_number=first.vehicle_number, route=first.route)
        self.ride(driver=self.other)
        self.assertEqual(self.client.delete(f'/api/admin/rides/{first.pk}/').status_code, 204)
        self.assertEqual([row['driver_id'] for row in self.manifest()], [self.other.pk])

    def test_delete_ride_rejects_missing_and_collection_urls(self):
        self.assertEqual(self.client.delete('/api/admin/rides/0/').status_code, 404)
        self.assertEqual(self.client.delete('/api/admin/rides/create/').status_code, 405)

    def test_advance_paid_outside_the_api_counts_against_salary(self):
        advance = AdvanceSalaryRequest.objects.create(driver=self.driver, amount=500)
        self.assertIsNone(advance.resolved_date)
        advance.status = 'Paid'
        advance.save()
        self.assertIsNotNone(advance.resolved_date)
        summary = next(row for row in self.client.get('/api/admin/salary-summary/').data['drivers'] if row['driver_id'] == self.driver.pk)
        self.assertEqual(summary['current_cycle']['advance_paid'], 500)

    def test_driver_deactivated_outside_the_api_loses_login(self):
        self.driver.is_active = False
        self.driver.save()
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post('/api/login/', {'username': 'driver', 'password': 'driver-test'}).status_code, 401)
        User.objects.filter(pk=self.user.pk).first().save()
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_user_deactivated_outside_the_api_marks_driver_inactive(self):
        self.user.is_active = False
        self.user.save()
        self.driver.refresh_from_db()
        self.assertFalse(self.driver.is_active)


class SavedDriverPasswordTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser('root', password='root-test')
        cls.staff = User.objects.create_user('staff', password='staff-test', is_staff=True)
        cls.staff.user_permissions.add(Permission.objects.get(codename='change_driver'))

    def setUp(self):
        cache.clear()
        self.client.force_authenticate(self.superuser)

    def create_driver(self, password='secret123'):
        response = self.client.post('/api/admin/drivers/create/', {'name': 'New', 'username': 'new', 'phone': '12345', 'password': password})
        self.assertEqual(response.status_code, 201, response.data)
        return Driver.objects.get(pk=response.data['driver']['id']), response

    def admin_page(self, user, driver):
        client = Client()
        client.force_login(user)
        return client.get(f'/admin/cabapp/driver/{driver.pk}/change/').content.decode()

    def test_password_is_saved_without_any_key_and_never_returned_by_the_api(self):
        driver, response = self.create_driver()
        self.assertEqual(driver.saved_password(), 'secret123')
        self.assertNotIn('secret123', str(response.data))
        self.assertNotIn('secret123', str(self.client.get('/api/admin/drivers/').data))
        self.assertNotIn('saved_password', str(self.client.get('/api/admin/drivers/').data))

    def test_only_superusers_can_see_it_in_the_django_admin(self):
        driver, _ = self.create_driver()
        self.assertIn('secret123', self.admin_page(self.superuser, driver))
        self.assertNotIn('secret123', self.admin_page(self.staff, driver))
        client = Client()
        client.force_login(self.superuser)
        self.assertNotIn('secret123', client.get('/admin/cabapp/driver/').content.decode())

    def test_admin_changing_password_updates_saved_copy(self):
        driver, _ = self.create_driver()
        self.client.patch(f'/api/admin/drivers/{driver.pk}/', {'password': 'changed123'})
        driver.refresh_from_db()
        self.assertEqual(driver.saved_password(), 'changed123')
        self.assertTrue(driver.user.check_password('changed123'))

    def test_saved_copy_is_not_shown_after_password_changes_elsewhere(self):
        driver, _ = self.create_driver()
        driver.user.set_password('driver-chose-this')
        driver.user.save()
        driver.refresh_from_db()
        self.assertIsNone(driver.saved_password())
        self.assertNotIn('secret123', self.admin_page(self.superuser, driver))

    def test_saved_copy_does_not_depend_on_secret_key(self):
        driver, _ = self.create_driver()
        with override_settings(SECRET_KEY='a-completely-different-secret-key-for-this-test'):
            self.assertEqual(Driver.objects.get(pk=driver.pk).saved_password(), 'secret123')

    def test_missing_saved_copy_does_not_crash(self):
        driver = Driver.objects.create(user=User.objects.create_user('old', password='old-pass'), name='Old')
        self.assertIsNone(driver.saved_password())
        self.assertIn('Not available', self.admin_page(self.superuser, driver))


class RegressionTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('admin', password='admin-test', is_staff=True)
        cls.user = User.objects.create_user('driver', password='driver-test', email='driver@example.test')
        cls.driver = Driver.objects.create(user=cls.user, name='Ann / Bob: [Main]', phone='123')
        cls.company = Company.objects.create(name='Cognizant')
        cls.special = Company.objects.create(name='Zellis')

    def setUp(self):
        cache.clear()
        self.client.force_authenticate(self.user)

    def ride(self, **kwargs):
        values = {'local_id': str(uuid4()), 'driver': self.driver, 'company': self.company, 'date': date.today(), 'vehicle_number': '0622', 'route': str(uuid4())}
        values.update(kwargs)
        return Ride.objects.create(**values)

    def test_sync_saves_valid_rides_when_another_ride_fails(self):
        self.ride(local_id='taken', driver=Driver.objects.create(user=User.objects.create_user('x'), name='X'))
        rides = [{'local_id': 'bad-company', 'date': str(WEDNESDAY), 'company_id': 999999},
                 {'local_id': 'taken', 'date': str(WEDNESDAY)},
                 {'local_id': 'good', 'date': str(WEDNESDAY), 'company_id': self.company.pk}]
        response = self.client.post('/api/sync-rides/', {'rides': rides}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['synced'], ['good'])
        self.assertEqual({error['local_id'] for error in response.data['errors']}, {'bad-company', 'taken'})
        self.assertNotIn('999999', str(response.data['errors']))
        self.assertEqual(Attendance.objects.get(driver=self.driver, date=WEDNESDAY).total_rides, 1)

    def test_driver_dashboard_totals_with_special_and_companyless_rides(self):
        self.ride(), self.ride(company=None), self.ride(company=self.special, total_km=10)
        with CaptureQueriesContext(connection) as queries:
            data = self.client.get('/api/driver/dashboard/').data
        self.assertEqual((data['today_rides'], data['today_standard_rides'], data['today_special_kms']), (3, 2, 10))
        self.assertEqual((data['monthly_standard_rides'], data['monthly_special_kms']), (2, 10))
        self.assertLess(len(queries), 8)

    def test_driver_excel_accepts_names_invalid_for_sheet_titles_and_avoids_n_plus_one(self):
        self.ride()
        with CaptureQueriesContext(connection) as queries:
            first = self.client.get('/api/driver/export-excel/')
        self.assertEqual(first.status_code, 200)
        initial = len(queries)
        for offset in range(1, 5):
            self.ride(date=date.today() - timedelta(days=offset))
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get('/api/driver/export-excel/').status_code, 200)
        self.assertEqual(len(queries), initial)

    def test_admin_excel_includes_removed_drivers_when_scoped_to_their_company(self):
        self.ride(date=WEDNESDAY)
        Driver.objects.filter(pk=self.driver.pk).update(is_active=False)
        self.client.force_authenticate(self.admin)
        response = self.client.get('/api/export-excel/', {'start_date': str(WEDNESDAY), 'end_date': str(WEDNESDAY), 'company_id': str(self.company.pk)})
        self.assertEqual(response.status_code, 200)
        workbook = openpyxl.load_workbook(BytesIO(response.content))
        self.assertIn('All Rides', workbook.sheetnames)
        self.assertNotIn('Report', workbook.sheetnames)
        self.assertNotIn('Sheet', workbook.sheetnames)

    def test_monthly_report_titles_sheets_by_company_and_drops_duplicates(self):
        first = self.ride(date=WEDNESDAY)
        self.ride(date=WEDNESDAY, route=first.route)
        self.ride(date=WEDNESDAY, company=None)
        self.client.force_authenticate(self.admin)
        response = self.client.get('/api/export-monthly-report/', {'month': WEDNESDAY.month, 'year': WEDNESDAY.year})
        workbook = openpyxl.load_workbook(BytesIO(response.content))
        self.assertEqual(workbook.sheetnames, ['Cognizant', 'Other'])
        self.assertIn('Cognizant', workbook['Cognizant']['A1'].value)
        self.assertEqual(workbook['Cognizant'].max_row, 3)

    def test_default_vehicle_rejects_invalid_values(self):
        for payload in ({'default_seater': 'abc'}, {'default_seater': 5}, {'default_vehicle_number': 'x' * 51}):
            self.assertEqual(self.client.post('/api/driver/update-default-vehicle/', payload, format='json').status_code, 400, payload)
        self.assertEqual(self.client.post('/api/driver/update-default-vehicle/', {'default_vehicle_number': '0622', 'default_seater': 6}, format='json').status_code, 200)
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.default_seater, 6)

    def test_password_reset_with_duplicate_emails_does_not_crash(self):
        self.client.force_authenticate(None)
        User.objects.create_user('twin', email='DRIVER@example.test')
        response = self.client.post('/api/forgot-password/', {'contact': 'driver@example.test'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_otp_attempts_are_rate_limited(self):
        self.client.force_authenticate(None)
        statuses = [self.client.post('/api/verify-otp/', {'contact': 'driver@example.test', 'otp': '000000'}).status_code for _ in range(25)]
        self.assertEqual(statuses[0], 400)
        self.assertEqual(statuses[-1], 429)

    def test_email_failure_does_not_leak_server_details(self):
        self.client.force_authenticate(None)
        with patch('cabapp.views.send_mail', side_effect=OSError('smtp password rejected')):
            response = self.client.post('/api/forgot-password/', {'contact': 'driver@example.test'})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('password rejected', str(response.data))
