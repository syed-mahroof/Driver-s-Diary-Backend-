from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views

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
    path('admin/drivers/', views.list_drivers, name='list_drivers'),
    path('admin/drivers/create/', views.create_driver, name='create_driver'),
    path('companies/create/', views.create_company, name='create_company'),
    path('reports/', views.reports, name='reports'),
    path('export-excel/', views.export_excel, name='export_excel'),
    path('vehicles/', views.list_vehicles, name='list_vehicles'),
    path('vehicles/create/', views.create_vehicle, name='create_vehicle'),
]
