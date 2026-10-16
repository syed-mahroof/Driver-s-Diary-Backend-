from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views
from . import admin_actions, performance

urlpatterns = [
    # Auth
    path('register/', views.register, name='register'),
    path('login/', views.CabTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('forgot-password/', views.forgot_password, name='forgot_password'),
    path('verify-otp/', views.verify_otp, name='verify_otp'),
    path('reset-password/', views.reset_password, name='reset_password'),

    # Driver
    path('driver/dashboard/', views.driver_dashboard, name='driver_dashboard'),
    path('driver/salary-summary/', views.driver_salary_summary, name='driver_salary_summary'),
    path('driver/export-excel/', views.export_driver_excel, name='export_driver_excel'),
    path('driver/report-data/', views.driver_report_data, name='driver_report_data'),
    path('driver/charge/', views.create_car_charge, name='create_car_charge'),
    path('driver/update-default-vehicle/', views.update_default_vehicle, name='update_default_vehicle'),
    path('rides/', views.create_ride, name='create_ride'),
    path('rides/<int:pk>/update/', views.update_ride, name='update_ride'),
    path('sync-rides/', views.sync_rides, name='sync_rides'),
    path('companies/', views.list_companies, name='list_companies'),

    # Advance Salary
    path('advance-salary/request/', views.request_advance_salary, name='request_advance_salary'),
    path('advance-salary/', views.list_advance_requests, name='list_advance_requests'),
    path('advance-salary/<int:pk>/update/', views.update_advance_request, name='update_advance_request'),

    # Admin
    path('admin/dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('admin/salary-summary/', views.admin_salary_summary, name='admin_salary_summary'),
    path('admin/drivers/', views.list_drivers, name='list_drivers'),
    path('admin/drivers/create/', admin_actions.create_driver, name='create_driver'),
    path('admin/drivers/<int:pk>/', admin_actions.driver_detail, name='admin_driver_detail'),
    path('admin/rides/create/', admin_actions.ride_detail, name='admin_create_ride'),
    path('admin/rides/<int:pk>/', admin_actions.ride_detail, name='admin_update_ride'),
    path('admin/charge/create/', admin_actions.charge_detail, name='admin_create_car_charge'),
    path('admin/charge/<int:pk>/', admin_actions.charge_detail, name='admin_update_car_charge'),
    path('admin/advance-salary/create/', admin_actions.create_advance, name='admin_create_advance'),
    path('admin/performance/', performance.driver_performance, name='admin_performance'),
    path('companies/create/', views.create_company, name='create_company'),
    path('reports/', views.reports, name='reports'),
    path('export-excel/', views.export_excel, name='export_excel'),
    path('export-monthly-report/', views.export_monthly_report, name='export_monthly_report'),
    path('vehicles/', views.list_vehicles, name='list_vehicles'),
    path('vehicles/create/', views.create_vehicle, name='create_vehicle'),
]
