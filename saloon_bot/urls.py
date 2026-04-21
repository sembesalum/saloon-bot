from django.urls import path
from . import views
from .views import delete_booking, booking_form, edit_booking
from saloon_bot import admin_views



urlpatterns = [
    # Webhook endpoint
    path('webhook/', views.webhook, name='webhook'),
    path('register/', admin_views.user_register, name='register'), 
    path('login/', admin_views.user_login, name='login'),
    path('admin/logout/', admin_views.admin_logout, name='admin_logout'),
    path('dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('bookings/delete/<int:booking_id>/', delete_booking, name='delete_booking'),
    path('book/<str:phone_number>/<str:token>/', booking_form, name='booking_form'),
    path('services/', admin_views.services, name='services'),
    path('verify-payment/', admin_views.services, name='verify_payment'),
    path('customers/', admin_views.customers, name='customers'),
    path('services/', admin_views.services, name='services'),
    path('booking/edit/<int:booking_id>/', edit_booking, name='edit_booking'),
    # Admin Dashboard User responses
    path('response/', admin_views.manage_messages, name='response'),
]