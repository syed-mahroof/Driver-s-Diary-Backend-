from datetime import date, timedelta
from io import BytesIO
from uuid import uuid4
import openpyxl
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase
from .models import Driver, Company, Vehicle, Ride, Attendance, CarCharge, AdvanceSalaryRequest
from .views import update_attendance, get_current_and_previous_salary_cycles


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
        ride = Ride.objects.create(**values)
        update_attendance(ride.driver, ride.date)
        return ride

    def test_admin_can_create_and_move_ride_and_recalculate_both_days(self):
        response = self.client.post('/api/admin/rides/create/', {'driver': self.driver.pk, 'company': self.company.pk,
            'date': str(self.today), 'vehicle_number': '0622', 'requested_seater': 6, 'total_km': '10.50'})
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['id']
        response = self.client.patch(f'/api/admin/rides/{pk}/', {'driver': self.other.pk, 'date': str(self.today - timedelta(days=1))})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Attendance.objects.get(driver=self.driver, date=self.today).total_rides, 0)
        self.assertEqual(Attendance.objects.get(driver=self.other, date=self.today - timedelta(days=1)).total_rides, 1)
        self.assertEqual(Ride.objects.get(pk=pk).requested_seater, 6)
        self.assertEqual(LogEntry.objects.filter(user=self.admin).count(), 2)

    def test_new_admin_endpoints_forbid_drivers(self):
        self.client.force_authenticate(self.user)
        for method, path in [('post', '/api/admin/rides/create/'), ('patch', '/api/admin/rides/1/'),
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
