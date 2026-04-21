import os
import traceback
from django.contrib import messages
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth import authenticate
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import requests
from urllib.parse import urljoin
from .models import AdminRegistration, Booking, Message
from django.db.models import Count, Q
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from .models import Service
import requests
import logging
from django.conf import settings
from saloon_bot.config import ZENO_API_KEY, ZENO_SECRET_KEY

logger = logging.getLogger(__name__)

def user_register(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        username = request.POST.get('username')
        phone_number = request.POST.get('phone_number')
        password1 = request.POST.get('password1')
        password2 = request.POST.get('password2')
        
        # Check if any fields are missing
        if not all([email, username, phone_number, password1, password2]):
            messages.error(request, 'All fields are required.')
            return redirect('register')
        
        # Check if passwords match
        if password1 != password2:
            messages.error(request, 'Passwords do not match.')
            return redirect('register')
        
        # Check if email already exists
        if AdminRegistration.objects.filter(email=email).exists():
            messages.error(request, 'Email is already taken.')
            return redirect('register')

        # Create the new user
        try:
            user = AdminRegistration(
                email=email,
                username=username,
                phone_number=phone_number
            )
            user.set_password(password1)  # Hash the password before saving
            user.save()
            
            messages.success(request, 'Account created successfully!')
            return redirect('login')
        except Exception as e:
            messages.error(request, f'Error creating account: {str(e)}')
            return redirect('register')
    return render(request, 'register.html')

def user_login(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        if not email or not password:
            messages.error(request, 'Both fields are required.')
            return redirect('login')
        
        try:
            user = AdminRegistration.objects.get(email=email)
            if user.check_password(password):
                # Successful login
                request.session['user_id'] = user.id  # Set user in session
                messages.success(request, 'Login successful!')
                return redirect('admin_dashboard')  # Redirect to the response page
            else:
                messages.error(request, 'Invalid password.')
        except AdminRegistration.DoesNotExist:
            messages.error(request, 'User with this email does not exist.')

    return render(request, 'login.html')

def admin_logout(request):
    request.session.flush()
    messages.success(request, 'You have been logged out successfully.')
    return redirect('login')

@login_required
def admin_dashboard(request):
    # Get filter parameters
    status_filter = request.GET.get('status', 'all')
    date_filter = request.GET.get('date')
    
    # Base query
    bookings = Booking.objects.all().order_by('-appointment_date')
    
    # Apply filters
    if status_filter != 'all':
        bookings = bookings.filter(status=status_filter)
    
    if date_filter:
        bookings = bookings.filter(appointment_date__date=date_filter)
    
    # Pagination
    paginator = Paginator(bookings, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'bookings': page_obj,
        'status_counts': {
            'all': Booking.objects.count(),
            'pending': Booking.objects.filter(status='pending').count(),
            'confirmed': Booking.objects.filter(status='confirmed').count(),
            'completed': Booking.objects.filter(status='completed').count(),
            'cancelled': Booking.objects.filter(status='cancelled').count(),
        },
        'current_status': status_filter,
        'selected_date': date_filter
    }
    return render(request, 'dashboard.html', context)

# View to handle adding, updating, and deleting messages
def manage_messages(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        key = request.POST.get('key')
        text = request.POST.get('text')
        language = request.POST.get('language')

        if action == 'add':
            # Add a new message
            Message.objects.update_or_create(key=key, defaults={'text': text, 'language': language})
            messages.success(request, f'Message with key {key} added/updated successfully.')
        elif action == 'update':
            # Update existing message
            try:
                message = Message.objects.get(key=key)
                message.text = text
                message.language = language
                message.save()
                messages.success(request, f'Message with key {key} updated successfully.')
            except Message.DoesNotExist:
                messages.error(request, f'Message with key {key} does not exist.')
        elif action == 'delete':
            # Delete a message
            try:
                message = Message.objects.get(key=key)
                message.delete()
                messages.success(request, f'Message with key {key} deleted successfully.')
            except Message.DoesNotExist:
                messages.error(request, f'Message with key {key} does not exist.')
        
        return redirect('response')  # Redirect back to the response page with updated data

    # Fetch all messages to display
    messages_list = Message.objects.all()
    return render(request, 'response.html', {'messages': messages_list})

def get_message(key, language="sw", **variables):
    """Retrieve localized message with optional variable substitution"""
    try:
        message = Message.objects.get(key=key, language=language)
        if variables:
            return message.text.format(**variables)
        return message.text
    except Message.DoesNotExist:
        return key  # Fallback to key name
    
    
def services(request):
    services = Service.objects.all().order_by('name')
    payment_response = None
    
    if request.method == 'POST':
        order_id = request.POST.get('order_id')
        if order_id:
            payment_response = check_customer_payment_status(order_id)
            
            if not payment_response:
                messages.error(request, "Failed to connect to payment gateway. Please try again.")
            elif payment_response.get('error'):
                messages.error(request, f"Payment check failed: {payment_response.get('message')}")
    
    return render(request, 'services.html', {
        'services': services,
        'payment_response': payment_response,
        'active_page': 'services'
    })
    
    
def check_customer_payment_status(order_id):
    """Check payment status with Zeno API"""
    try:
        status_data = {
            'check_status': 1,
            'order_id': order_id,
            'api_key': ZENO_API_KEY,
            'secret_key': ZENO_SECRET_KEY
        }
        
        logger.info(f"Checking payment status for order: {order_id}")
        
        response = requests.post(
            "https://api.zeno.africa/order-status",
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data=status_data,
            timeout=10
        )
        
        if response.status_code == 200:
            return response.json()  # Returns the full status response
        
        logger.error(f"Payment check failed with status {response.status_code}")
        return {
            'success': False,
            'error': True,
            'message': f"API request failed with status {response.status_code}",
            'order_id': order_id
        }
        
    except requests.exceptions.Timeout:
        logger.error("Payment check timed out")
        return {
            'success': False,
            'error': True,
            'message': "Request timed out. Please try again.",
            'order_id': order_id
        }
    except Exception as e:
        logger.error(f"Error checking payment status: {str(e)}")
        return {
            'success': False,
            'error': True,
            'message': str(e),
            'order_id': order_id
        }


def customers(request):
    # Add your customer data fetching logic here
    return render(request, 'customers.html', {'active_page': 'customers'})

# def services(request):
#     # Add your services data fetching logic here
#     return render(request, 'admin/services.html', {'active_page': 'services'})