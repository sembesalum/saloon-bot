from django.db import models
from django.utils import timezone
from datetime import timedelta
from tinymce.models import HTMLField
from django.contrib.auth.hashers import make_password, check_password
# Create your models here.

SESSION_TIMEOUT = 600 

class WhatsappSession(models.Model):
    phone_number = models.CharField(max_length=20, unique=True)
    step = models.CharField(max_length=50, default='initial')
    data = models.JSONField(default=dict)
    last_updated = models.DateTimeField(auto_now=False)  # CHANGED FROM auto_now=True
    is_active = models.BooleanField(default=True)


    class Meta:
        indexes = [
            models.Index(fields=['phone_number', 'is_active']),
            models.Index(fields=['last_updated']),
        ]

    def is_expired(self):
        return (timezone.now() - self.last_updated).total_seconds() > SESSION_TIMEOUT
    
    def save(self, *args, **kwargs):
        if not self.pk:  # Only set timestamp on create
            self.last_updated = timezone.now()
        super().save(*args, **kwargs)
        
    
class Message(models.Model):
    key = models.CharField(max_length=100, unique=True)  # Unique identifier for the message
    text = HTMLField()
    language = models.CharField(max_length=10, default="sw")  # Language code (e.g., "sw" for Swahili)

    def __str__(self):
        return f"{self.key} ({self.language})"
    

class Customer(models.Model):
    phone_number = models.CharField(max_length=20, unique=True)
    language = models.CharField(max_length=2, default='sw')
    name = models.CharField(max_length=100, blank=True, null=True)
    join_date = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.name or self.phone_number}"

class Service(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    duration = models.PositiveIntegerField()  # Duration in minutes
    
    def __str__(self):
        return f"{self.name} - {self.price} TZS"

class Booking(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    
    customer_phone = models.CharField(max_length=20)
    customer_name = models.CharField(max_length=100, blank=True, null=True)
    service_type = models.CharField(max_length=50)
    service_name = models.CharField(max_length=100)
    appointment_date = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.customer_phone} - {self.service_name}"
    
    class Meta:
        ordering = ['-appointment_date']

class BookingManager:
    @staticmethod
    def create_booking(phone_number, service_type, service_name, price=None, notes=None):
        """
        Create a new booking with required fields only
        """
        # Get or create customer
        customer, created = Customer.objects.get_or_create(
            phone_number=phone_number,
            defaults={'name': None}  # Name can be added later
        )
        
        booking = Booking.objects.create(
            customer_phone=phone_number,
            service_type=service_type,
            service_name=service_name,
            price=price,
            notes=notes,
            status='confirmed'
        )
        return booking
    
class TempBooking(models.Model):
    phone_number = models.CharField(max_length=20)
    data = models.TextField()  # Stores JSON data
    token = models.CharField(max_length=32, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    
    def __str__(self):
        return f"Temp booking for {self.phone_number}"
    
class AdminRegistration(models.Model):
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=150, unique=True)
    password = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=15)  # Add phone_number field
    date_joined = models.DateTimeField(auto_now_add=True)

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)

    def __str__(self):
        return self.email
    
