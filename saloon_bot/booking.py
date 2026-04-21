from django.utils import timezone
from .models import Booking
from datetime import datetime, timedelta

class BookingManager:
    @staticmethod
    def create_booking(phone_number, service_type, service_name, appointment_date=None, price=None, notes=None):
        """
        Create a new booking
        """
        booking = Booking.objects.create(
            customer_phone=phone_number,
            service_type=service_type,
            service_name=service_name,
            appointment_date=appointment_date,
            price=price,
            notes=notes,
            status='pending'
        )
        return booking

    @staticmethod
    def get_booking_by_id(booking_id):
        """
        Retrieve a single booking by ID
        """
        try:
            return Booking.objects.get(id=booking_id)
        except Booking.DoesNotExist:
            return None

    @staticmethod
    def get_bookings_by_phone(phone_number, status=None):
        """
        Retrieve all bookings for a phone number, optionally filtered by status
        """
        queryset = Booking.objects.filter(customer_phone=phone_number)
        if status:
            queryset = queryset.filter(status=status)
        return queryset.order_by('-appointment_date')

    @staticmethod
    def get_upcoming_bookings(days=7):
        """
        Get upcoming bookings within the next X days (default 7)
        """
        now = timezone.now()
        end_date = now + timedelta(days=days)
        return Booking.objects.filter(
            appointment_date__gte=now,
            appointment_date__lte=end_date,
            status__in=['pending', 'confirmed']
        ).order_by('appointment_date')

    @staticmethod
    def update_booking(booking_id, **kwargs):
        """
        Update booking fields
        """
        try:
            booking = Booking.objects.get(id=booking_id)
            for field, value in kwargs.items():
                setattr(booking, field, value)
            booking.save()
            return booking
        except Booking.DoesNotExist:
            return None

    @staticmethod
    def confirm_booking(booking_id):
        """
        Confirm a pending booking
        """
        return BookingManager.update_booking(booking_id, status='confirmed')

    @staticmethod
    def cancel_booking(booking_id):
        """
        Cancel a booking
        """
        return BookingManager.update_booking(booking_id, status='cancelled')

    @staticmethod
    def complete_booking(booking_id):
        """
        Mark a booking as completed
        """
        return BookingManager.update_booking(booking_id, status='completed')

    @staticmethod
    def delete_booking(booking_id):
        """
        Delete a booking permanently
        """
        try:
            booking = Booking.objects.get(id=booking_id)
            booking.delete()
            return True
        except Booking.DoesNotExist:
            return False

    @staticmethod
    def get_bookings_by_date_range(start_date, end_date, status=None):
        """
        Get bookings within a date range, optionally filtered by status
        """
        queryset = Booking.objects.filter(
            appointment_date__gte=start_date,
            appointment_date__lte=end_date
        )
        if status:
            queryset = queryset.filter(status=status)
        return queryset.order_by('appointment_date')