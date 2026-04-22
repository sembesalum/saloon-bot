
import json
import token
from decimal import Decimal
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from datetime import datetime, timedelta
import time
import requests
from saloon_bot.config import VERIFY_TOKEN, WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, ZENO_ACCOUNT_ID, ZENO_API_KEY, ZENO_API_URL, ZENO_SECRET_KEY
from django.views.decorators.csrf import csrf_exempt
import logging
import json
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from datetime import datetime, timedelta, time

import requests
from saloon_bot.config import VERIFY_TOKEN, WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID
from django.views.decorators.csrf import csrf_exempt
import logging
from .models import Service, TempBooking, WhatsappSession, Message
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import Booking, Customer
from .booking import BookingManager
from django.core.paginator import Paginator
from django.utils.crypto import get_random_string
from django.contrib import messages
from django.utils.dateparse import parse_date
from .forms import BookingForm

# Set up logging
logger = logging.getLogger(__name__)

# Session timeout in seconds (30 minutes)
SESSION_TIMEOUT = 1800
# Temporary switch for flow testing without charging users.
DUMMY_PAYMENT_MODE = True


LANGUAGES = {
    'SWAHILI': 'sw',
    'ENGLISH': 'en'
}


# Constants for menu states and service types
MENU_STATES = {
    'MAIN': 'main',
    'CONFIRMATION': 'confirmation',
    'HINNA_PIKO': 'hinna_piko',
    'KUSUKA': 'kusuka',
    'NATURAL_HAIR': 'natural_hair',
    'SHORT_HAIR': 'short_hair',
    'MAKEUP': 'makeup',
    'KUCHA': 'kucha',
    'KOPE': 'kope',
    'NYUSI': 'nyusi',
    'KUTOBOA': 'kutoboa',
    'KUOSHA': 'kuosha',
    'KUSUKA_CATEGORY': 'kusuka_category',
    'KUSUKA_STYLE': 'kusuka_style',
    'KUSUKA_TEXT': 'kusuka_text',
    'TIME_CONFIRMATION': 'time_confirmation'
}

SERVICE_TYPES = {
    'KUSUKA': 'Kusuka',
    'NATURAL_HAIR': 'Natural Hair',
    'SHORT_HAIR': 'Short Hair',
    'MAKEUP': 'Makeup',
    'KUCHA': 'Kucha',
    'KOPE': 'Kope',
    'NYUSI': 'Nyusi',
    'HINNA_PIKO': 'Hinna/Piko',
    'KUTOBOA': 'Kutoboa',
    'KUOSHA': 'Kuosha/Kufumua'
}

@csrf_exempt
def webhook(request):
    """Handle WhatsApp webhook verification and incoming messages"""
    if request.method == 'GET':
        return handle_verification(request)
    
    if request.method == 'POST':
        return handle_incoming_message(request)
    
    return HttpResponse('Method not allowed', status=405)

def handle_verification(request):
    """Handle WhatsApp webhook verification"""
    mode = request.GET.get('hub.mode')
    token = request.GET.get('hub.verify_token')
    challenge = request.GET.get('hub.challenge')
    
    if mode == 'subscribe' and token == VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return HttpResponse(challenge, status=200)
    
    logger.warning("Webhook verification failed")
    return HttpResponse('Verification failed', status=403)


def send_booking_link(phone_number):
    booking_url = f"https://oldonyotech.pythonanywhere.com/book/{phone_number}/"
    message = get_message('bonyeza_link')
    send_text_message(phone_number, message)

def handle_incoming_message(request):
    """Process incoming WhatsApp messages"""
    try:
        data = json.loads(request.body)
        entry_time = timezone.now()
        logger.info(f"Webhook received at {entry_time}")
        
        for entry in data.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value')
                if value and 'messages' in value:
                    process_message(value)
        
        return HttpResponse('OK', status=200)
    
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {str(e)}")
        return HttpResponse('Invalid JSON', status=400)
    
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}", exc_info=True)
        return HttpResponse('Error', status=500)

def process_message(value):
    """Process incoming WhatsApp messages and respond accordingly"""
    for contact in value.get('contacts', []):
        phone_number = contact['wa_id']
        logger.info(f"Processing message from {phone_number}")
        
        for message in value.get('messages', []):
            session = initialize_user_session(phone_number)
            
            if is_clear_session_command(message):
                clear_session(phone_number)
                send_text_message(phone_number, get_message('futa_session'))
                send_main_menu(phone_number)
                return
            
            if message.get('type') == 'text':
                handle_text_message_content(phone_number, message, session)
            
            elif message.get('type') == 'interactive':
                handle_interactive_message(phone_number, message, session)

def initialize_user_session(phone_number):
    """Initialize or update user session in database"""
    session, created = WhatsappSession.objects.get_or_create(
        phone_number=phone_number,
        defaults={
            'step': MENU_STATES['MAIN'],
            'data': {'booking_details': {}, 'current_options': []},
            'is_active': True
        }
    )
    
    if not created:
        # Ensure demo sessions always recover to an active, usable state.
        if not session.is_active:
            session.is_active = True
            if not session.data:
                session.data = {'booking_details': {}, 'current_options': []}
            if not session.step:
                session.step = MENU_STATES['MAIN']
        session.last_updated = timezone.now()
        session.save()
    
    return session

def is_clear_session_command(message):
    """Check if the message is a clear session command"""
    return (message.get('type') == 'text' and 
            message['text']['body'].strip() == '#')

def handle_text_message_content(phone_number, message, session):
    text = message['text']['body'].strip().lower()

    # Global navigation shortcuts for demo usability.
    if text in ['menu', 'home', 'start']:
        language = session.data.get('language', 'sw')
        send_main_menu(phone_number, language)
        return
    
    if session.step == "AWAITING_PAYMENT_PHONE":
        handle_payment_phone(phone_number, text)  # Process payment
        return 
    
    # Handle confirmation response
    if session.step == MENU_STATES['CONFIRMATION']:
        if text in ['1', 'ndiyo', 'yes']:
            confirm_booking(phone_number, session)
            return
        elif text in ['2', 'hapana', 'no']:
            cancel_booking(phone_number, session)
            return
        else:
            send_confirmation_prompt(phone_number, session)
            return
    
    # Handle clear session command
    if text == '#':
        clear_session(phone_number)
        send_text_message(phone_number, get_message('futa_session'))
        send_main_menu(phone_number)
        return
    
    # Handle main menu selections
    if session.step == MENU_STATES['MAIN']:
        handle_main_menu_selection(phone_number, text, session)
    
    # Handle all sub-menu selections
    elif session.step == MENU_STATES['NATURAL_HAIR']:
        handle_natural_hair_selection(phone_number, text, session)
    elif session.step == MENU_STATES['SHORT_HAIR']:
        handle_short_hair_selection(phone_number, text, session)
    elif session.step == MENU_STATES['MAKEUP']:
        handle_makeup_selection(phone_number, text, session)
    elif session.step == MENU_STATES['KUCHA']:
        handle_kucha_selection(phone_number, text, session)
    elif session.step == MENU_STATES['KOPE']:
        handle_kope_selection(phone_number, text, session)
    # elif session.step == MENU_STATES['NYUSI']:
    #     handle_nyusi_selection(phone_number, text, session)
    elif session.step == MENU_STATES['KUTOBOA']:
        handle_kutoboa_selection(phone_number, text, session)
    elif session.step == MENU_STATES['KUOSHA']:
        handle_kuosha_selection(phone_number, text, session)
    elif session.step == MENU_STATES['HINNA_PIKO']:
        handle_hina_selection(phone_number, text, session)
    elif session.step == MENU_STATES['KUSUKA']:
        handle_kusuka_text_selection(phone_number, text, session)
    else:
        send_main_menu(phone_number)
    
    # Handle final confirmation after web form
    if text.startswith('confirm_'):
        token = text.replace('confirm_', '', 1).strip()
        if not token:
            send_text_message(phone_number, get_message('hakuna_booking'))
            return
        try:
            temp_booking = TempBooking.objects.get(token=token, phone_number=phone_number)
            booking_data = json.loads(temp_booking.data)
            
            # Create the booking
            booking = Booking.objects.create(
                customer_phone=phone_number,
                customer_name=booking_data.get('customer_name'),
                service_type=booking_data['service_type'],
                service_name=booking_data['service_name'],
                appointment_date=f"{booking_data['appointment_date']} {booking_data['appointment_time']}",
                notes=booking_data.get('notes'),
                status='confirmed',
                price=booking_data.get('price')  # Save the price
            )
            
            # Prepare booking details for confirmation
            booking_details = {
                'service_type': booking.service_type,
                'service_name': booking.service_name,
                'appointment_date': booking.appointment_date.strftime('%Y-%m-%d'),
                'appointment_time': booking.appointment_date.strftime('%H:%M'),
                'price': booking.price
            }
            
            # Send confirmation with payment button
            # send_booking_confirmation(phone_number, booking_details)
            
            temp_booking.delete()
            
        except TempBooking.DoesNotExist:
            send_text_message(phone_number, get_message('hakuna_booking'))

def send_booking_confirmation(phone_number, booking_details, language='sw'):
    if language == 'en':
        message = f"""✅ BOOKING CONFIRMED
        
Service: {booking_details['service_name']}
Date: {booking_details['appointment_date']}
Time: {booking_details['appointment_time']}
Amount: Tsh {booking_details.get('price', 0):,}"""
        
        buttons = [{
            "type": "reply",
            "reply": {
                "id": "pay_now",
                "title": "💳 PAY NOW"
            }
        }]
        button_text = "Payment Options"
    else:
        message = f"""✅ BOOKING IMETHIBITISHWA
        
Huduma: {booking_details['service_name']}
Tarehe: {booking_details['appointment_date']}
Muda: {booking_details['appointment_time']}
Kiasi: Tsh {booking_details.get('price', 0):,}"""
        
        buttons = [{
            "type": "reply",
            "reply": {
                "id": "lipa_sasa",
                "title": "💳 LIPA SASA"
            }
        }]
        button_text = "Chagua"
    
    send_button_message(phone_number, message, button_text, buttons)

def handle_interactive_message(phone_number, message, session):
    """Handle interactive messages (list selections, buttons)"""
    interactive = message['interactive']
    
    if interactive['type'] == 'list_reply':
        handle_list_selection(phone_number, interactive['list_reply']['id'], session)
    elif interactive['type'] == 'button_reply':
        handle_button_reply(phone_number, interactive['button_reply']['id'], session)

def handle_confirmation_response(phone_number, text, session):
    """Handle user response to confirmation prompt"""
    if text == 'ndiyo':
        confirm_booking(phone_number, session)
    elif text == 'hapana':
        cancel_booking(phone_number, session)
    else:
        send_confirmation_prompt(phone_number, session)

def handle_list_selection(phone_number, selected_id, session):
    """Handle user selections from interactive lists"""
    menu_handlers = {
        MENU_STATES['MAIN']: handle_main_menu_selection,
        MENU_STATES['KUSUKA']: handle_kusuka_selection,
        MENU_STATES['NATURAL_HAIR']: handle_natural_hair_selection,
        MENU_STATES['SHORT_HAIR']: handle_short_hair_selection,
        MENU_STATES['MAKEUP']: handle_makeup_selection,
        MENU_STATES['KUCHA']: handle_kucha_selection,
        MENU_STATES['KOPE']: handle_kope_selection,
        # MENU_STATES['NYUSI']: handle_nyusi_selection,
        MENU_STATES['KUTOBOA']: handle_kutoboa_selection,
        MENU_STATES['KUOSHA']: handle_kuosha_selection,
        MENU_STATES['KUSUKA_STYLE']: handle_kusuka_style_selection,
        MENU_STATES['HINNA_PIKO']: handle_hina_selection
    }
    
    handler = menu_handlers.get(session.step)
    if handler:
        handler(phone_number, selected_id, session)
    else:
        send_main_menu(phone_number)
        
def get_message(key, language="sw"):
    """Helper function to get a message by key and language"""
    try:
        message = Message.objects.get(key=key, language=language)
        return message.text
    except Message.DoesNotExist:
        return ""

def send_natural_hair_menu(phone_number, language='sw'):
    """Send Natural Hair services in selected language with dynamic pricing"""
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['language'] = language  # Update session language
    session.save()
    
    if language == 'en':
        send_natural_hair_menu_en(phone_number)
    else:
        send_natural_hair_menu_sw(phone_number)

def send_natural_hair_menu_sw(phone_number):
    """Send Natural Hair services in Swahili"""
    services = [
        {"name": "Wash and Go", "price_key": "wash_and_go_price"},
        {"name": "Kuweka Mirija", "price_key": "kuweka_mirija_price"},
        {"name": "Finger Coil", "price_key": "finger_coil_price"},
        {"name": "Twist Out", "price_key": "twist_out_price"},
        {"name": "Braid Out", "price_key": "braid_out_price"},
        {"name": "Bantu Knots", "price_key": "bantu_knots_price"},
        {"name": "Faux Loc Installation", "price_key": "faux_loc_installation_price"},
        {"name": "Crochet Braids", "price_key": "crochet_braids_price"},
        {"name": "Natural Haircut", "price_key": "natural_haircut_price"},
        {"name": "Deep Conditioning", "price_key": "deep_conditioning_price"}
    ]

    # Get prices for all services with error handling
    for service in services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")

    # Filter only available services
    available_services = [s for s in services if s["price"] > 0]

    if not available_services:
        send_text_message(phone_number, get_message('hitilafu', 'sw'))
        return send_main_menu(phone_number, 'sw')

    # Format menu
    menu_text = get_message('naturalhair_huduma_kichwa', 'sw')
    for i, service in enumerate(available_services, start=1):
        formatted_price = "{:,}".format(service["price"])
        menu_text += f"\n{i}. {service['name']} - Tsh {formatted_price}"
    menu_text += "\n\nTuma namba ya huduma unayotaka:"

    send_chunked_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['NATURAL_HAIR'])

    # Store available services in session
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": s["name"], "price": s["price"]} for s in available_services
    ]
    session.save()   

def send_natural_hair_menu_en(phone_number):
    """Send Natural Hair services in English"""
    services = [
        {"name": "Wash and Go", "price_key": "wash_and_go_price"},
        {"name": "Twist Out", "price_key": "twist_out_price"},
        {"name": "Kuweka Mirija", "price_key": "kuweka_mirija_price"},
        {"name": "Finger Coil", "price_key": "finger_coil_price"},
        {"name": "Braid Out", "price_key": "braid_out_price"},
        {"name": "Bantu Knots", "price_key": "bantu_knots_price"},
        {"name": "Faux Loc Installation", "price_key": "faux_loc_installation_price"},
        {"name": "Crochet Braids", "price_key": "crochet_braids_price"},
        {"name": "Natural Haircut", "price_key": "natural_haircut_price"},
        {"name": "Deep Conditioning", "price_key": "deep_conditioning_price"}
    ]

    # Get prices for all services with error handling
    for service in services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")

    # Filter only available services
    available_services = [s for s in services if s["price"] > 0]

    if not available_services:
        send_text_message(phone_number, get_message('hitilafu_en', 'en'))
        return send_main_menu(phone_number, 'en')

    # Format menu
    menu_text = get_message('naturalhair_huduma_kichwa_eng', 'en')
    for i, service in enumerate(available_services, start=1):
        formatted_price = "{:,}".format(service["price"])
        menu_text += f"\n{i}. {service['name']} - Tsh {formatted_price}"
    menu_text += "\n\nSend the number of the service you want:"

    send_chunked_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['NATURAL_HAIR'])

    # Store available services in session
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": s["name"], "price": s["price"]} for s in available_services
    ]
    session.save()

def handle_natural_hair_selection(phone_number, text, session):
    """Handle Natural Hair service selection with prices in selected language"""
    if 'current_services' not in session.data:
        language = session.data.get('language', 'sw')
        send_natural_hair_menu(phone_number, language)
        return
    
    try:
        choice = int(text.strip())
        services = session.data['current_services']
        language = session.data.get('language', 'sw')
        
        if 1 <= choice <= len(services):
            selected_service = services[choice-1]
            session.data['booking_details'] = {
                'service_type': SERVICE_TYPES['NATURAL_HAIR'],
                'service_name': f"{selected_service['name']} - Tsh {selected_service['price']:,}",
                'price': selected_service['price']
            }
            session.step = MENU_STATES['CONFIRMATION']
            session.save()
            
            # Send confirmation in the correct language
            if language == 'en':
                message = f"""🧾 FINAL: CONFIRM BOOKING

You selected: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Do you want to confirm booking for this service?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Yes"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ No"}}
                ]
                send_button_message(phone_number, message, "Confirm", buttons)
            else:
                message = f"""🧾 MWISHO: THIBITISHA BOOKING

Umechagua: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Je, unataka kuthibitisha booking kwa huduma uliyochagua?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Ndiyo"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ Hapana"}}
                ]
                send_button_message(phone_number, message, "Thibitisha", buttons)
        else:
            if language == 'en':
                send_text_message(phone_number, f"Invalid number. Please send a number between 1 and {len(services)}.")
            else:
                send_text_message(phone_number, f"Namba si sahihi. Tafadhali tuma namba kati ya 1 na {len(services)}.")
            send_natural_hair_menu(phone_number, language)
    except ValueError:
        language = session.data.get('language', 'sw')
        if language == 'en':
            send_text_message(phone_number, "Please send numbers only (1-8).")
        else:
            send_text_message(phone_number, "Tafadhali tuma namba tu (1-8).")
        send_natural_hair_menu(phone_number, language)


def handle_button_reply(phone_number, button_id, session):
    """Handle all button replies with full bilingual support and error handling"""
    try:
        # Get current language from session (default to Swahili)
        language = session.data.get('language', 'sw')
        
        # Unified payment button handler
        if button_id in ['lipa_sasa', 'pay_now', 'lipa_hapa']:
            handle_lipa_sasa(phone_number, session)
            return
            
        if button_id == 'change_language':
            # Toggle language between English and Swahili
            new_lang = 'en' if language == 'sw' else 'sw'
            session.data['language'] = new_lang
            session.save()
            
            # Refresh current menu with new language
            if session.step == MENU_STATES['KUSUKA']:
                send_kusuka_menu(phone_number, new_lang)
            else:
                send_main_menu(phone_number, new_lang)
            return
            
        elif button_id == 'confirm_yes' and session.step == MENU_STATES['CONFIRMATION']:
            if 'booking_details' not in session.data:
                logger.error(f"No booking details in session for {phone_number}")
                send_text_message(phone_number, get_message('hakuna_taarifa_za_huduma', language))
                send_main_menu(phone_number, language)
                return
            confirm_booking(phone_number, session)
            
        elif button_id == 'confirm_no' and session.step == MENU_STATES['CONFIRMATION']:
            cancel_booking(phone_number, session)
            
        elif button_id == 'my_orders':
            handle_my_orders(phone_number)
            
        elif button_id == 'contact_admin':
            handle_contact_admin(phone_number)
            
        elif button_id == 'payment_confirmed':
            # Payment confirmation handling with bilingual support
            payment_info = session.data.get('payment_info', {})
            order_id = payment_info.get('transaction_id')
            
            if not order_id:
                send_text_message(phone_number, get_message('hakuna_taarifa_za_malipo', language))
                return send_main_menu(phone_number, language)
            
            status_response = check_payment_status(order_id)
            
            if status_response and status_response.get('payment_status') == 'COMPLETED':
                booking = Booking.objects.filter(
                    customer_phone=phone_number,
                    payment_reference=order_id
                ).order_by('-created_at').first()
                if booking:
                    booking.payment_status = 'completed'
                    booking.status = 'completed'
                    booking.save()

                    if language == 'en':
                        message = (
                            f"✅ Payment Completed\n\n"
                            f"Order ID: {order_id}\n"
                            f"Amount: Tsh {payment_info.get('amount', 0):,}\n"
                            f"Payment Phone: {payment_info.get('payment_phone', '--')}\n"
                            f"Appointment: {booking.appointment_date.strftime('%Y-%m-%d %H:%M')}\n"
                            f"Reference: {status_response.get('reference', '--')}\n\n"
                            f"Thank you for your payment!"
                        )
                    else:
                        message = (
                            f"✅ Malipo Yamekamilika\n\n"
                            f"Namba ya Oda: {order_id}\n"
                            f"Kiasi: Tsh {payment_info.get('amount', 0):,}\n"
                            f"Simu ya Malipo: {payment_info.get('payment_phone', '--')}\n"
                            f"Miadi: {booking.appointment_date.strftime('%Y-%m-%d %H:%M')}\n"
                            f"Kumbukumbu: {status_response.get('reference', '--')}\n\n"
                            f"Asante kwa malipo yako!"
                        )

                    # Send confirmation message to user
                    send_text_message(phone_number, message)
                    print(f"phone_number: {phone_number}")
                    print(f"message: {message}")

                    # Create and send message to admin
                    admin_message = (
                        f"Order success message 🟢🟢🟢🟢🟢🟢\n\n"
                        f"OrderNo: {order_id}\n"
                        f"Huduma: {booking.service_name}\n"
                        f"Kiasi: Tsh {payment_info.get('amount', 0):,}\n"
                        f"Simu ya Malipo: {payment_info.get('payment_phone', '--')}\n"
                        f"Miadi: {booking.appointment_date.strftime('%Y-%m-%d %H:%M')}\n"
                        f"Kumbukumbu: {status_response.get('reference', '--')}\n\n"
                        f"Payment status: Paid 🟢"
                    )

                    try:
                        send_text_message('+255616107670', admin_message)
                    except Exception as e:
                        print(f"Error sending admin message: {e}")

                else:
                    error_msg = (
                        "❌ Sorry, no booking found. Please contact support."
                        if language == 'en'
                        else "❌ Samahani, hakuna taarifa za booking. Tafadhali wasiliana na mhudumu."
                    )
                    send_text_message(phone_number, error_msg)

                
                # if booking:
                #     booking.payment_status = 'completed'
                #     booking.status = 'completed'
                #     booking.save()
                    
                #     if language == 'en':
                #         message = (
                #             f"✅ Payment Completed\n\n"
                #             f"Order ID: {order_id}\n"
                #             f"Amount: Tsh {payment_info.get('amount', 0):,}\n"
                #             f"Payment Phone: {payment_info.get('payment_phone', '--')}\n"
                #             f"Appointment: {booking.appointment_date.strftime('%Y-%m-%d %H:%M')}\n"
                #             f"Reference: {status_response.get('reference', '--')}\n\n"
                #             f"Thank you for your payment!"
                #         )
                #     else:
                #         message = (
                #             f"✅ Malipo Yamekamilika\n\n"
                #             f"Namba ya Oda: {order_id}\n"
                #             f"Kiasi: Tsh {payment_info.get('amount', 0):,}\n"
                #             f"Simu ya Malipo: {payment_info.get('payment_phone', '--')}\n"
                #             f"Miadi: {booking.appointment_date.strftime('%Y-%m-%d %H:%M')}\n"
                #             f"Kumbukumbu: {status_response.get('reference', '--')}\n\n"
                #             f"Asante kwa malipo yako!"
                #         )
                #     send_text_message(phone_number, message)
                #     print(f"phone_number: {phone_number}")
                #     print(f"message: {message}")
                #     send_text_message(phone_number, message)
                #     send_text_message('+255616107670', message)

                #     try:
                #         send_text_message('255616107670', message)
                #     except Exception as e:
                #         print(f"Error: {e}")

                # else:
                #     error_msg = "❌ Sorry, no booking found. Please contact support." if language == 'en' else "❌ Samahani, hakuna taarifa za booking. Tafadhali wasiliana na mhudumu."
                #     send_text_message(phone_number, error_msg)
            
            else:
                # Payment failed message
                if language == 'en':
                    failed_message = (
                        f"❌ Payment Failed\n\n"
                        f"Order ID: {order_id}\n"
                        f"Amount: Tsh {payment_info.get('amount', 0):,}\n"
                        f"Status: {status_response.get('payment_status', 'NOT VERIFIED')}\n\n"
                        f"Please try again or contact support."
                    )
                    buttons = [
                        {"type": "reply", "reply": {"id": "payment_help", "title": "🆘 Help"}},
                        {"type": "reply", "reply": {"id": "pay_now", "title": "🔄 Try Again"}}
                    ]
                    button_text = "Options"
                else:
                    failed_message = (
                        f"❌ Malipo Yameshindikana\n\n"
                        f"Namba ya Oda: {order_id}\n"
                        f"Kiasi: Tsh {payment_info.get('amount', 0):,}\n"
                        f"Hali: {status_response.get('payment_status', 'HAIJATHIBITISHWA')}\n\n"
                        f"Tafadhali jaribu tena au wasiliana na msaada."
                    )
                    buttons = [
                        {"type": "reply", "reply": {"id": "payment_help", "title": "🆘 Msaada"}},
                        {"type": "reply", "reply": {"id": "lipa_sasa", "title": "🔄 Jaribu Tena"}}
                    ]
                    button_text = "Chagua"
                
                send_button_message(phone_number, failed_message, button_text, buttons)
                send_text_message('+255616107670', failed_message)
                send_admin_message(failed_message)


                
        elif button_id == 'payment_help':
            # Bilingual payment help with fallback messages
            if language == 'en':
                help_msg = get_message('msaada_wa_malipo_en', 'en') or """🆘 Payment Help
        
We're here to assist with your payment issues:

1. Ensure you have sufficient balance
2. Complete the USSD prompt when it appears
3. If no prompt appears, wait 1 minute and try again

For immediate assistance:
📞 Call: +255123456789
📍 Visit: Our salon during working hours"""
            else:
                help_msg = get_message('msaada_wa_malipo', 'sw') or """🆘 Msaada wa Malipo

Tuko hapa kukusaidia kwa shida zako za malipo:

1. Hakikisha una salio la kutosha
2. Kamilisha USSD prompt inapoonekana
3. Kama hakuna prompt, subiri dakika 1 na jaribu tena

Kwa msaada wa haraka:
📞 Piga: +255123456789
📍 Tembelea: Salon yetu wakati wa masaa ya kazi"""

            send_text_message(phone_number, help_msg)
            
            # Additional help options
            buttons = [
                {
                    "type": "reply",
                    "reply": {
                        "id": "contact_admin",
                        "title": "📞 " + ("Call Support" if language == 'en' else "Piga Msaada")
                    }
                },
                {
                    "type": "reply",
                    "reply": {
                        "id": "pay_now" if language == 'en' else "lipa_sasa",
                        "title": "🔄 " + ("Try Again" if language == 'en' else "Jaribu Tena")
                    }
                }
            ]
            send_button_message(phone_number, 
                               "Need more help?" if language == 'en' else "Unahitaji msaada zaidi?",
                               buttons)
            
    except Exception as e:
        logger.error(f"Error in handle_button_reply: {str(e)}")
        # Fallback error message in both languages
        error_msg = (
            "❌ System error. Please try again later." 
            if session.data.get('language') == 'en' else
            "❌ Hitilafu ya mfumo. Tafadhali jaribu tena baadae."
        )
        send_text_message(phone_number, error_msg)


def send_admin_message(message):
    """Send a plain text message to the admin WhatsApp number."""
    admin_number = '255616107670'
    
    try:
        response = send_text_message(admin_number, message)
        print(f"[DEBUG] Admin message sent: {message}")
        print(f"[DEBUG] Admin message response: {response}")
        logger.info(f"Message sent to admin {admin_number}: {message}")
        logger.info(f"Response: {response}")
    except Exception as e:
        logger.error(f"Failed to send message to admin: {e}")
        print(f"[DEBUG] Failed to send admin message: {e}")



def handle_complete_payment(phone_number):
    """Handle payment completion response"""
    response_message = get_message('malipo_yaliokamilika')
    
    send_text_message(phone_number, response_message)
        
def handle_contact_admin(phone_number):
    """Handle 'Contact Admin' button click with bilingual support"""
    try:
        # Get language from session
        try:
            session = WhatsappSession.objects.get(phone_number=phone_number)
            language = session.data.get('language', 'sw')
        except WhatsappSession.DoesNotExist:
            language = 'sw'
        
        # Get appropriate message based on language
        if language == 'en':
            message = get_message('wasiliana_na_admin_en', 'en')
        else:
            message = get_message('wasiliana_na_admin', 'sw')
        
        send_text_message(phone_number, message)
        
    except Exception as e:
        logger.error(f"Error in handle_contact_admin: {str(e)}")
        # Fallback to English if there's an error
        message = "Please contact support at 0657 085 776 or visit our salon."
        send_text_message(phone_number, message)

def handle_my_orders(phone_number):
    """Show user's latest order with payment option if needed"""
    try:
        # Get the latest booking
        booking = Booking.objects.filter(
            customer_phone=phone_number
        ).order_by('-created_at').first()
        
        if not booking:
            # Get language from session
            try:
                session = WhatsappSession.objects.get(phone_number=phone_number)
                language = session.data.get('language', 'sw')
            except WhatsappSession.DoesNotExist:
                language = 'sw'
                
            send_text_message(phone_number, get_message('huna_order', language))
            return
            
        # Get language from session
        try:
            session = WhatsappSession.objects.get(phone_number=phone_number)
            language = session.data.get('language', 'sw')
        except WhatsappSession.DoesNotExist:
            language = 'sw'

        # Prepare order details message based on language
        if language == 'en':
            template = get_message('oda_yangu_en', 'en')
        else:
            template = get_message('oda_yangu', 'sw')

        message = template.format(
            service_name=booking.service_name,
            appointment_date=booking.appointment_date.strftime('%Y-%m-%d'),
            appointment_time=booking.appointment_date.strftime('%H:%M'),
            price=f"{booking.price:,}",
            status=booking.get_status_display().upper()
        )

        # Check if payment is needed
        if booking.status == 'confirmed' and booking.price and booking.price > 0:
            if language == 'en':
                message += "\nClick '💳 PAY NOW' to complete your payment."
                buttons = [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "pay_now",
                            "title": "💳 PAY NOW"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "contact_admin",
                            "title": "📞 Support"
                        }
                    }
                ]
                button_text = "Options"
            else:
                message += "\nBonyeza '💳 Lipa Sasa' kukamilisha malipo yako."
                buttons = [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "lipa_sasa",
                            "title": "💳 Lipa Sasa"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "contact_admin",
                            "title": "📞 Msaada"
                        }
                    }
                ]
                button_text = "Chagua"
            
            send_button_message(phone_number, message, button_text, buttons)
            
            # Store booking info in session for payment processing
            session = WhatsappSession.objects.get(phone_number=phone_number)
            session.data['current_booking'] = {
                'id': booking.id,
                'amount': float(booking.price),
                'service': booking.service_name
            }
            session.save()
        else:
            # For completed or other statuses
            if language == 'en':
                message += "\n\nThank you for using our services!"
            else:
                message += "\n\nAhsante kwa kutumia huduma zetu!"
            send_text_message(phone_number, message)
            
    except Exception as e:
        logger.error(f"Error in handle_my_orders: {str(e)}")
        # Get language from session
        try:
            session = WhatsappSession.objects.get(phone_number=phone_number)
            language = session.data.get('language', 'sw')
        except WhatsappSession.DoesNotExist:
            language = 'sw'
            
        if language == 'en':
            error_msg = (
                "❌ Sorry, there's an issue currently. "
                "Please try again later or contact support."
            )
        else:
            error_msg = (
                "❌ Samahani, kuna tatizo kwa sasa. "
                "Tafadhali jaribu tena baadae au wasiliana na mhudumu."
            )
        send_text_message(phone_number, error_msg)

def clear_session(phone_number):
    """Clear user session"""
    try:
        session = WhatsappSession.objects.get(phone_number=phone_number)
        session.step = MENU_STATES['MAIN']
        session.data = {'booking_details': {}, 'current_options': []}
        session.is_active = True
        session.last_updated = timezone.now()
        session.save()
        logger.info(f"Session cleared for {phone_number}")
    except WhatsappSession.DoesNotExist:
        pass

# --- Menu Navigation Functions ---
def send_main_menu(phone_number, language='sw'):
    """Send the main menu with language selection"""
    session = WhatsappSession.objects.get(phone_number=phone_number)
    welcome_message_en = get_message("welcome_message_en")
    welcome_message_sw = get_message("welcome_message_sw")
    
    # Store selected language in session
    session.data['language'] = language
    session.save()
    
    if language == 'en':
        # English version of the menu
        welcome_message = welcome_message_en
        
        text_message = f"""{welcome_message}

Choose service (1-9):
1. Braiding Service
2. Natural Hair
3. Short Hair
4. Makeup
5. Nails
6. Eyelashes
7. Henna/Pico
8. Piercing
9. Hair Wash/Dry

Type # anytime to reset session."""

        button_text = "Other Options:"
        
    else:
        # Swahili version (default)
        welcome_message = welcome_message_sw
        
        text_message = f"""{welcome_message}

Chagua huduma (1-9):
1. Huduma ya Kusuka
2. Natural Hair
3. Short Hair Na Mawigi
4. Makeup
5. Kucha
6. Kope
7. Hinna/Piko
8. Kutoboa
9. Kuosha/Kufumua

Tuma # muda wowote kuanza upya."""

        button_text = "Chagua Huduma nyingine:"

    # Send the text menu first
    send_text_message(phone_number, text_message)
    
    # Then send the interactive buttons with language option
    buttons = [
        {
            "type": "reply",
            "reply": {
                "id": "my_orders",
                "title": "📋 My Orders" if language == 'en' else "📋 Oda Yangu"
            }
        },
        {
            "type": "reply",
            "reply": {
                "id": "contact_admin",
                "title": "📞 Contact" if language == 'en' else "📞 Mawasiliano"
            }
        },
        {
            "type": "reply",
            "reply": {
                "id": "change_language",
                "title": "🇹🇿 Chagua Kiswahili" if language == 'en' else "🇬🇧 Change to English"
            }
        }
    ]
    
    send_button_message(phone_number, button_text, "Choose" if language == 'en' else "Chagua", buttons)
    update_session_menu(phone_number, MENU_STATES['MAIN'])

def handle_main_menu_selection(phone_number, text, session):
    """Handle selection from main menu using numbers"""
    try:
        choice = int(text.strip())
    except ValueError:
        current_lang = session.data.get('language', 'sw')
        msg = "Please choose a number between 1 and 9." if current_lang == 'en' else "Tafadhali chagua namba kati ya 1 hadi 9."
        send_text_message(phone_number, msg)
        send_main_menu(phone_number, current_lang)
        return
    
    menu_actions = {
        0: lambda: send_help_message(phone_number),
        1: lambda: send_kusuka_menu(phone_number, session.data.get('language', 'sw')),  # Updated to pass language
        2: lambda: send_natural_hair_menu(phone_number, session.data.get('language', 'sw')),
        3: lambda: send_short_hair_menu(phone_number, session.data.get('language', 'sw')),
        4: lambda: send_makeup_menu(phone_number, session.data.get('language', 'sw')),
        5: lambda: send_kucha_menu(phone_number, session.data.get('language', 'sw')),  # Updated
        6: lambda: send_kope_menu(phone_number, session.data.get('language', 'sw')),   # Updated
        # 7: lambda: send_nyusi_menu(phone_number, session.data.get('language', 'sw')),
        7: lambda: send_hina_menu(phone_number, session.data.get('language', 'sw')),
        8: lambda: send_kutoboa_menu(phone_number, session.data.get('language', 'sw')), # Updated
        9: lambda: send_kuosha_menu(phone_number, session.data.get('language', 'sw'))  # Updated
    }
    
    action = menu_actions.get(choice)
    if action:
        try:
            action()
        except Exception as e:
            logger.error(f"Main menu action failed for choice {choice}: {str(e)}", exc_info=True)
            current_lang = session.data.get('language', 'sw')
            fallback = "Sorry, something went wrong. Returning to main menu." if current_lang == 'en' else "Samahani, kuna hitilafu. Tunakurudisha menu kuu."
            send_text_message(phone_number, fallback)
            send_main_menu(phone_number, current_lang)
    else:
        send_text_message(phone_number, get_message("namba_si_sahihi"))
        send_main_menu(phone_number)
        
def send_hina_menu(phone_number, language='sw'):
    """Send Kusuka menu in selected language"""
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['language'] = language  # Update session language
    session.save()
    
    if language == 'en':
        send_hina_menu_en(phone_number)
    else:
        send_hina_menu_sw(phone_number)

def send_hina_menu_sw(phone_number):
    """Send Makeup services in Swahili"""
    services = [
        {"name": "Mkono Mmoja", "price_key": "one_hand_price"},
    ]
    
    # Get prices for all services with error handling
    for service in services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")
    
    # Filter out services with price 0 (not available)
    available_services = [s for s in services if s["price"] > 0]
    
    if not available_services:
        send_text_message(phone_number, get_message('hitilafu', 'sw'))
        return send_main_menu(phone_number, 'sw')
    
    menu_text = get_message('hina_piko', 'sw')
    for i, service in enumerate(available_services, start=1):
        menu_text += f"\n{i}. {service['name']} - Tsh {service['price']:,}"
    menu_text += "\n\nTuma namba ya huduma unayotaka:"
    
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['HINNA_PIKO'])
    
    # Store services in session for reference
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": service["name"], "price": service["price"]} 
        for service in available_services
    ]
    session.save()

def send_hina_menu_en(phone_number):
    """Send Makeup services in English"""
    services = [
        {"name": "One Hand", "price_key": "one_hand_price"},

    ]
    
    # Get prices for all services with error handling
    for service in services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")
    
    # Filter out services with price 0 (not available)
    available_services = [s for s in services if s["price"] > 0]
    
    if not available_services:
        send_text_message(phone_number, get_message('hitilafu_en', 'en'))
        return send_main_menu(phone_number, 'en')
    
    menu_text = get_message('hina_piko_en', 'en')
    for i, service in enumerate(available_services, start=1):
        menu_text += f"\n{i}. {service['name']} - Tsh {service['price']:,}"
    menu_text += "\n\nSend the number of the service you want:"
    
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['HINNA_PIKO'])
    
    # Store services in session for reference
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": service["name"], "price": service["price"]} 
        for service in available_services
    ]
    session.save()

def handle_hina_selection(phone_number, text, session):
    """Handle Makeup service selection with prices in selected language"""
    if 'current_services' not in session.data:
        language = session.data.get('language', 'sw')
        send_hina_menu(phone_number, language)
        return
    try:
        choice = int(text.strip())
        services = session.data['current_services']
        language = session.data.get('language', 'sw')
        
        if 1 <= choice <= len(services):
            selected_service = services[choice-1]
            session.data['booking_details'] = {
                'service_type': SERVICE_TYPES['HINNA_PIKO'],
                'service_name': f"{selected_service['name']} - Tsh {selected_service['price']:,}",
                'price': selected_service['price']
            }
            session.step = MENU_STATES['CONFIRMATION']
            session.save()
            
            # Send confirmation in the correct language
            if language == 'en':
                message = f"""🧾 FINAL: CONFIRM BOOKING

You selected: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Do you want to confirm booking for this service?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Yes"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ No"}}
                ]
                send_button_message(phone_number, message, "Confirm", buttons)
            else:
                message = f"""🧾 MWISHO: THIBITISHA BOOKING

Umechagua: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Je, unataka kuthibitisha booking kwa huduma uliyochagua?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Ndiyo"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ Hapana"}}
                ]
                send_button_message(phone_number, message, "Thibitisha", buttons)
        else:
            if language == 'en':
                send_text_message(phone_number, f"Invalid number. Please send a number between 1 and {len(services)}.")
            else:
                send_text_message(phone_number, f"Namba si sahihi. Tafadhali tuma namba kati ya 1 na {len(services)}.")
            send_makeup_menu(phone_number, language)
    except ValueError:
        language = session.data.get('language', 'sw')
        if language == 'en':
            send_text_message(phone_number, "Please send numbers only (1-5).")
        else:
            send_text_message(phone_number, "Tafadhali tuma namba tu (1-5).")
        send_makeup_menu(phone_number, language)
        



def send_kusuka_menu(phone_number, language='sw'):
    """Send Kusuka menu in selected language"""
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['language'] = language  # Update session language
    session.save()
    
    if language == 'en':
        send_kusuka_menu_en(phone_number)
    else:
        send_kusuka_menu_sw(phone_number)

def send_kusuka_menu_sw(phone_number):
    """Send Kusuka styles in Swahili"""
    services = [
        {"name": "Knotless Normal", "price_key": "knotless_normal_price"},
        {"name": "Knotless Medium", "price_key": "knotless_medium_price"},
        {"name": "Knotless Smart", "price_key": "knotless_smart_price"},
        {"name": "Knotless Donyo", "price_key": "knotless_donyo_price"},
        {"name": "Invisible Locs Normal", "price_key": "invisible_locs_normal_price"},
        {"name": "Invisible Locs Medium", "price_key": "invisible_locs_medium_price"},
        {"name": "Invisible Locs Donyo", "price_key": "invisible_locs_donyo_price"},

        {"name": "Stitches Normal", "price_key": "stitches_normal_price"},
        {"name": "Stitches Medium", "price_key": "stitches_medium_price"},
        {"name": "Stitches/Cornrows", "price_key": "stitches_knotless_price"},
        {"name": "Stitches Donyo", "price_key": "stitches_donyo_price"},
        
        {"name": "Yebo Achia Normal", "price_key": "yebo_achia_normal_price"},
        {"name": "Yebo Achia Smart", "price_key": "yebo_achia_smart_price"},
        {"name": "Yeboyebo Normal", "price_key": "yeboyebo_normal_price"},
        {"name": "Yeboyebo Medium", "price_key": "yeboyebo_medium_price"},
        {"name": "Yeboyebo Smart", "price_key": "yeboyebo_smart_price"},
        {"name": "Yeboyebo Donyo", "price_key": "yeboyebo_donyo_price"},
        {"name": "Yeboyebo Faster", "price_key": "yeboyebo_faster_price"},
        
        {"name": "Jellycurly Normal ", "price_key": "jellycurly_normal_price"},
        {"name": "Jellycurly Smart ", "price_key": "jellycurly_smart_price"},
        {"name": "Jelly Curl Donyo", "price_key": "jelly_curl_donyo_price"},
        
        {"name": "Sunshine Normal Bob", "price_key": "sunshine_normal_price"},
        {"name": "Sunshine Smart", "price_key": "sunshine_smart_price"},
        {"name": "Sunshine Super Long", "price_key": "sunshine_super_long_price"},
        
        {"name": "Butterfly Normal Bob", "price_key": "butterfly_normal_price"},
        {"name": "Butterfly Smart", "price_key": "butterfly_smart_price"},
        {"name": "Butterfly Super Long", "price_key": "butterfly_super_long_price"},
        
        {"name": "Afrokinky Normal", "price_key": "afrokinky_normal_price"},
        {"name": "Afrokiny Awote", "price_key": "afrokinky_awote_price"},
        {"name": "Afrokiny Long", "price_key": "afrokinky_long_price"},
        {"name": "Afrokiny Super Long", "price_key": "afrokinky_super_long_price"},
        
        {"name": "Twist njia mbili normal", "price_key": "twist_njia_mbili_normal_price"},
        {"name": "Twist njia mbili smart", "price_key": "twist_njia_mbili_smart_price"},
        {"name": "Twist njia mbili donyo", "price_key": "twist_njia_mbili_donyo_price"},
        
        {"name": "Human Hair Normal", "price_key": "human_hair_normal_price"},
        {"name": "Human Hair Medium", "price_key": "human_hair_medium_price"},
        {"name": "Human Hair Smart Collable", "price_key": "human_hair_smart_price"},
        {"name": "Human Hair Super Collable Donyo", "price_key": "human_hair_donyo_price"},
        
        {"name": "Human Hair Twist Normal", "price_key": "human_hair_twist_price"},
        {"name": "Human Hair Twist Smart Donyo Collable", "price_key": "human_hair_twist_smart_donyo_price"},
        {"name": "Human Hair Twist Super Donyo Collable ", "price_key": "human_hair_twist_super_donyo_price"},
        
        {"name": "French Curly Normal", "price_key": "french_curly_normal_price"},
        {"name": "French Curly Medium", "price_key": "french_curly_medium_price"},
        {"name": "French Curly Smart Collable", "price_key": "french_curly_smart_collable"},
        {"name": "French Curly Super Donyo Collable", "price_key": "french_curly_super_donyo_price"},
        
        {"name": "Pony Braids Normal", "price_key": "pony_braids_normal_price"},
        {"name": "Pony Braids Smart", "price_key": "pony_braids_smart_price"},
        {"name": "Pony Braids Donyo", "price_key": "pony_braids_donyo_price"},
        
        {"name": "Coco Twist Normal", "price_key": "coco_twist_normal_price"},
        {"name": "Coco Twist Smart", "price_key": "coco_twist_smart_price"},
        {"name": "Coco Twist Donyo", "price_key": "coco_twist_donyo_price"},
        
        {"name": "Vibutu vya Kubenjuka Normal", "price_key": "vibutu_kubenjuka_normal_price"},
        {"name": "Vibutu vya Kubenjuka Smart", "price_key": "vibutu_kubenjuka_smart_price"},
        {"name": "Vibutu vya Kubenjuka Super Long Donyo", "price_key": "vibutu_kubenjuka_super_price"},
        
        {"name": "Milano Normal Bob", "price_key": "milano_normal_price"},
        {"name": "Milano Smart Bob", "price_key": "milano_smart_price"},
        {"name": "Milano Super Long Donyo", "price_key": "milano_super_price"},
        
        {"name": "Zigzag Normal", "price_key": "zigzag_normal_price"},
        {"name": "Zigzag Stitch", "price_key": "zigzag_stitch_price"},
        {"name": "Zigzag Cornrows", "price_key": "zigzag_konless_price"},
        
        {"name": "Fluffy Kinky Normal", "price_key": "fluffy_kinky_normal_price"},
        {"name": "Fluffy Kinky Smart", "price_key": "fluffy_kinky_smart_price"},
        
        {"name": "Egyptian Locs", "price_key": "egyptian_locs_price"},

        {"name": "Passion Twist Normal Bob", "price_key": "passion_twist_small_normal_price"},
        {"name": "Passion Twist Smart", "price_key": "passion_twist_smart_price"},
        {"name": "Passion Twist Super Long", "price_key": "passion_twist_super_long_price"},
        
        {"name": "Selfie Normal", "price_key": "selfie_normal_price"},
        {"name": "Selfie Jelly", "price_key": "selfie_jelly_price"},
        
        {"name": "Love Curly Normal", "price_key": "lovely_curl_normal_price"},
        {"name": "Love Curly Smart", "price_key": "love_curly_donyo_smart_price"},
        {"name": "Love Curly Donyo", "price_key": "love_curly_donyo_price"},
        
        {"name": "Mimi Curls Normal", "price_key": "mimi_curls_normal_price"},
        {"name": "Mimi Curl na Jelly", "price_key": "mimi_curl_jelly_price"},
        
        {"name": "Havana Curly Normal", "price_key": "havana_normal_price"},
        {"name": "Havana Curly na Jelly", "price_key": "havana_jelly_price"},
        {"name": "Royal Curly Normal", "price_key": "royal_curl_normal"},
        {"name": "Royal Curly Jelly", "price_key": "royal_curl_jelly"},

        {"name": "Nywele ya Mkono Normal", "price_key": "nywele_mkono_normal_price"},
        {"name": "Nywele ya Mkono Medium", "price_key": "nywele_mkono_medium_price"},
        {"name": "Nywele ya Mkono Smart", "price_key": "nywele_mkono_smart_price"},
        {"name": "Nywele ya Mkono Donyo", "price_key": "nywele_mkono_donyo_price"},
        
    ]
    
    # Get prices for all services with error handling
    for service in services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")
    
    # Filter out services with price 0 (not available)
    available_services = [s for s in services if s["price"] > 0]
    
    if not available_services:
        send_text_message(phone_number, get_message('hitilafu', 'sw'))
        return send_main_menu(phone_number, 'sw')
    
    menu_text = get_message("kusuka_kichwa_cha_huduma")
    for i, service in enumerate(available_services, start=1):
        menu_text += f"{i}. {service['name']} - Tsh {service['price']:,}\n"
    menu_text += "\nKURUDI MENU KUU BONYEZA #"
    
    send_chunked_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['KUSUKA'])
    
    # Store services in session for reference
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": service["name"], "price": service["price"]} 
        for service in available_services
    ]
    session.data['language'] = 'sw'
    session.save()

def send_kusuka_menu_en(phone_number):
    """Send Kusuka styles in English"""
    services = [
        {"name": "Knotless Normal", "price_key": "knotless_normal_price"},
        {"name": "Knotless Medium", "price_key": "knotless_medium_price"},
        {"name": "Knotless Smart", "price_key": "knotless_smart_price"},
        {"name": "Knotless Donyo", "price_key": "knotless_donyo_price"},
        {"name": "Invisible Locs Normal", "price_key": "invisible_locs_normal_price"},
        {"name": "Invisible Locs Medium", "price_key": "invisible_locs_medium_price"},
        {"name": "Invisible Locs Donyo", "price_key": "invisible_locs_donyo_price"},

        {"name": "Stitches Normal", "price_key": "stitches_normal_price"},
        {"name": "Stitches Medium", "price_key": "stitches_medium_price"},
        {"name": "Stitches/Cornrows", "price_key": "stitches_knotless_price"},
        {"name": "Stitches Donyo", "price_key": "stitches_donyo_price"},
        
        {"name": "Yebo Achia Normal", "price_key": "yebo_achia_normal_price"},
        {"name": "Yebo Achia Smart", "price_key": "yebo_achia_smart_price"},
        {"name": "Yeboyebo Normal", "price_key": "yeboyebo_normal_price"},
        {"name": "Yeboyebo Medium", "price_key": "yeboyebo_medium_price"},
        {"name": "Yeboyebo Smart", "price_key": "yeboyebo_smart_price"},
        {"name": "Yeboyebo Donyo", "price_key": "yeboyebo_donyo_price"},
        {"name": "Yeboyebo Faster", "price_key": "yeboyebo_faster_price"},
        
        {"name": "Jellycurly Normal ", "price_key": "jellycurly_normal_price"},
        {"name": "Jellycurly Smart ", "price_key": "jellycurly_smart_price"},
        {"name": "Jelly Curl Donyo", "price_key": "jelly_curl_donyo_price"},
        
        {"name": "Sunshine Normal Bob", "price_key": "sunshine_normal_price"},
        {"name": "Sunshine Smart", "price_key": "sunshine_smart_price"},
        {"name": "Sunshine Super Long", "price_key": "sunshine_super_long_price"},
        
        {"name": "Butterfly Normal Bob", "price_key": "butterfly_normal_price"},
        {"name": "Butterfly Smart", "price_key": "butterfly_smart_price"},
        {"name": "Butterfly Super Long", "price_key": "butterfly_super_long_price"},
        
        {"name": "Afrokinky Normal", "price_key": "afrokinky_normal_price"},
        {"name": "Afrokiny Awote", "price_key": "afrokinky_awote_price"},
        {"name": "Afrokiny Long", "price_key": "afrokinky_long_price"},
        {"name": "Afrokiny Super Long", "price_key": "afrokinky_super_long_price"},
        
        {"name": "Twist njia mbili normal", "price_key": "twist_njia_mbili_normal_price"},
        {"name": "Twist njia mbili smart", "price_key": "twist_njia_mbili_smart_price"},
        {"name": "Twist njia mbili donyo", "price_key": "twist_njia_mbili_donyo_price"},
        
        {"name": "Human Hair Normal", "price_key": "human_hair_normal_price"},
        {"name": "Human Hair Medium", "price_key": "human_hair_medium_price"},
        {"name": "Human Hair Smart Collable", "price_key": "human_hair_smart_price"},
        {"name": "Human Hair Super Collable Donyo", "price_key": "human_hair_donyo_price"},
        
        {"name": "Human Hair Twist Normal", "price_key": "human_hair_twist_price"},
        {"name": "Human Hair Twist Smart Donyo Collable", "price_key": "human_hair_twist_smart_donyo_price"},
        {"name": "Human Hair Twist Super Donyo Collable ", "price_key": "human_hair_twist_super_donyo_price"},
        
        {"name": "French Curly Normal", "price_key": "french_curly_normal_price"},
        {"name": "French Curly Medium", "price_key": "french_curly_medium_price"},
        {"name": "French Curly Smart Collable", "price_key": "french_curly_smart_collable"},
        {"name": "French Curly Super Donyo Collable", "price_key": "french_curly_super_donyo_price"},
        
        {"name": "Pony Braids Normal", "price_key": "pony_braids_normal_price"},
        {"name": "Pony Braids Smart", "price_key": "pony_braids_smart_price"},
        {"name": "Pony Braids Donyo", "price_key": "pony_braids_donyo_price"},
        
        {"name": "Coco Twist Normal", "price_key": "coco_twist_normal_price"},
        {"name": "Coco Twist Smart", "price_key": "coco_twist_smart_price"},
        {"name": "Coco Twist Donyo", "price_key": "coco_twist_donyo_price"},
        
        {"name": "Vibutu vya Kubenjuka Normal", "price_key": "vibutu_kubenjuka_normal_price"},
        {"name": "Vibutu vya Kubenjuka Smart", "price_key": "vibutu_kubenjuka_smart_price"},
        {"name": "Vibutu vya Kubenjuka Super Long Donyo", "price_key": "vibutu_kubenjuka_super_price"},
        
        {"name": "Milano Normal Bob", "price_key": "milano_normal_price"},
        {"name": "Milano Smart Bob", "price_key": "milano_smart_price"},
        {"name": "Milano Super Long Donyo", "price_key": "milano_super_price"},
        
        {"name": "Zigzag Normal", "price_key": "zigzag_normal_price"},
        {"name": "Zigzag Stitch", "price_key": "zigzag_stitch_price"},
        {"name": "Zigzag Cornrows", "price_key": "zigzag_konless_price"},
        
        {"name": "Fluffy Kinky Normal", "price_key": "fluffy_kinky_normal_price"},
        {"name": "Fluffy Kinky Smart", "price_key": "fluffy_kinky_smart_price"},
        
        {"name": "Egyptian Locs", "price_key": "egyptian_locs_price"},

        {"name": "Passion Twist Normal Bob", "price_key": "passion_twist_small_normal_price"},
        {"name": "Passion Twist Smart", "price_key": "passion_twist_smart_price"},
        {"name": "Passion Twist Super Long", "price_key": "passion_twist_super_long_price"},
        
        {"name": "Selfie Normal", "price_key": "selfie_normal_price"},
        {"name": "Selfie Jelly", "price_key": "selfie_jelly_price"},
        
        {"name": "Love Curly Normal", "price_key": "lovely_curl_normal_price"},
        {"name": "Love Curly Smart", "price_key": "love_curly_donyo_smart_price"},
        {"name": "Love Curly Donyo", "price_key": "love_curly_donyo_price"},
        
        {"name": "Mimi Curls Normal", "price_key": "mimi_curls_normal_price"},
        {"name": "Mimi Curl na Jelly", "price_key": "mimi_curl_jelly_price"},
        
        {"name": "Havana Curly Normal", "price_key": "havana_normal_price"},
        {"name": "Havana Curly na Jelly", "price_key": "havana_jelly_price"},
        {"name": "Royal Curly Normal", "price_key": "royal_curl_normal"},
        {"name": "Royal Curly Jelly", "price_key": "royal_curl_jelly"},

        {"name": "Nywele ya Mkono Normal", "price_key": "nywele_mkono_normal_price"},
        {"name": "Nywele ya Mkono Medium", "price_key": "nywele_mkono_medium_price"},
        {"name": "Nywele ya Mkono Smart", "price_key": "nywele_mkono_smart_price"},
        {"name": "Nywele ya Mkono Donyo", "price_key": "nywele_mkono_donyo_price"},
        
    ]
    
    # Get prices for all services with error handling
    for service in services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")
    
    # Filter out services with price 0 (not available)
    available_services = [s for s in services if s["price"] > 0]
    
    if not available_services:
        send_text_message(phone_number, get_message('hitilafu_en', 'en'))
        return send_main_menu(phone_number, 'en')
    
    menu_text = get_message("kusuka_kichwa_cha_huduma_en")
    for i, service in enumerate(available_services, start=1):
        menu_text += f"{i}. {service['name']} - Tsh {service['price']:,}\n"
    menu_text += "\nTO RETURN TO MAIN MENU PRESS #"
    
    send_chunked_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['KUSUKA'])
    
    # Store services in session for reference
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": service["name"], "price": service["price"]} 
        for service in available_services
    ]
    session.data['language'] = 'en'
    session.save()

def handle_kusuka_text_selection(phone_number, text, session):
    """Handle Kusuka style selection with prices"""
    if 'current_services' not in session.data:
        language = session.data.get('language', 'sw')
        send_kusuka_menu(phone_number, language)
        return
    
    try:
        choice = int(text.strip())
        services = session.data['current_services']
        language = session.data.get('language', 'sw')
        
        if 1 <= choice <= len(services):
            selected_service = services[choice-1]
            session.data['booking_details'] = {
                'service_type': SERVICE_TYPES['KUSUKA'],
                'service_name': f"{selected_service['name']} - Tsh {selected_service['price']:,}",
                'price': selected_service['price']
            }
            session.step = MENU_STATES['CONFIRMATION']
            session.save()
            
            # Send confirmation in the correct language
            if language == 'en':
                message = f"""🧾 FINAL: CONFIRM BOOKING

You selected: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Do you want to confirm booking for this service?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Yes"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ No"}}
                ]
                send_button_message(phone_number, message, "Confirm", buttons)
            else:
                message = f"""🧾 MWISHO: THIBITISHA BOOKING

Umechagua: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Je, unataka kuthibitisha booking kwa huduma uliyochagua?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Ndiyo"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ Hapana"}}
                ]
                send_button_message(phone_number, message, "Thibitisha", buttons)
        else:
            if language == 'en':
                send_text_message(phone_number, f"Invalid number. Please send a number between 1 and {len(services)}.")
            else:
                send_text_message(phone_number, f"Namba si sahihi. Tafadhali tuma namba kati ya 1 na {len(services)}.")
            send_kusuka_menu(phone_number, language)
    except ValueError:
        language = session.data.get('language', 'sw')
        if language == 'en':
            send_text_message(phone_number, "Please send numbers only (1-25).")
        else:
            send_text_message(phone_number, "Tafadhali tuma namba tu (1-25).")
        send_kusuka_menu(phone_number, language)



def handle_kusuka_style_selection(phone_number, selected_id, session):
    """Handle the final style selection"""
    if 'kusuka_styles' not in session.data:
        send_main_menu(phone_number)
        return
    
    selected_style = next(
        (style['title'] for style in session.data['kusuka_styles'] 
        if style['id'] == selected_id), 
        None
    )
    
    if selected_style:
        session.data['booking_details'] = {
            'service_type': SERVICE_TYPES['KUSUKA'],
            'service_name': selected_style,
            'price': None
        }
        session.step = MENU_STATES['CONFIRMATION']
        session.save()
        send_confirmation_prompt(phone_number, session)
    else:
        send_main_menu(phone_number)

def handle_kusuka_selection(phone_number, selected_id, session):
    """Handle kusuka style selection"""
    style_map = {
        "knotless": "Knotless",
        "invisible_locs": "Invisible Locs",
        "stitches": "Stitches/Cornrows",
        "yeboyebo": "Yeboyebo",
        "jellycurly": "Jellycurly",
        "sunshine": "Sunshine",
        "butterfly_locs": "Butterfly Locs",
        "afrokinky": "Afrokinky",
        "twist_njia_mbili": "Twist njia mbili",
        "human_hair": "Human Hair - Bone Straight",
        "french_curly": "French Curly",
        "pony_braids": "Pony Braids",
        "coco_twist": "Coco Twist",
        "vibutu_kubenjuka": "Vibutu vya Kubenjuka",
        "milano": "Milano",
        "zigzag": "Zigzag",
        "fluffy_kinky": "Fluffy Kinky",
        "gypsy_locs": "Gypsy Locs",
        "passion_twist": "Passion Twist",
        "selfie": "Selfie",
        "lovely_curl": "Lovely Curl",
        "mimi_curls": "Mimi Curls",
        "havana": "Havana",
        "yebo_achia": "Yebo Achia",
        "nywele_mkono": "Nywele ya Mkono (Style yoyote)"
    }
    
    session.data['booking_details'] = {
        'service_type': SERVICE_TYPES['KUSUKA'],
        'service_name': style_map.get(selected_id, "Style maalum ya Kusuka"),
        'style_id': selected_id
    }
    session.step = MENU_STATES['CONFIRMATION']
    session.save()
    send_confirmation_prompt(phone_number, session)

def send_confirmation_prompt(phone_number, session):
    """Send confirmation prompt for the selected service"""
    booking = session.data['booking_details']
    message = f"""🧾 MWISHO: THIBITISHA BOOKING

Umechagua: {booking['service_type']} - {booking['service_name']}

Je, unataka kuthibitisha booking kwa huduma uliyochagua?"""
    
    buttons = [
        {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Ndiyo"}},
        {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ Hapana"}}
    ]
    
    send_button_message(phone_number, message, "Thibitisha", buttons)
    update_session_menu(phone_number, MENU_STATES['CONFIRMATION'])


def confirm_booking(phone_number, session):
    """Send booking link with language support (payment button comes after date/time selection)"""
    try:
        if 'booking_details' not in session.data:
            logger.error(f"No booking details found in session for {phone_number}")
            language = session.data.get('language', 'sw')
            if language == 'en':
                send_text_message(phone_number, get_message('booking_haipo', 'en'))
            else:
                send_text_message(phone_number, get_message('booking_haipo', 'sw'))
            send_main_menu(phone_number, language)
            return

        booking_details = session.data['booking_details']
        language = session.data.get('language', 'sw')
        token = get_random_string(32)
        
        # Create temp booking with price
        TempBooking.objects.create(
            phone_number=phone_number,
            data=json.dumps(booking_details),
            token=token,
            price=booking_details.get('price', None)
        )

        booking_url = f"https://oldonyotech.pythonanywhere.com/book/{phone_number}/{token}/"
        
        if language == 'en':
            # English version of the confirmation message
            template = get_message('thibitisha_booking_link_en', 'en')
            message = template.format(
                service_type=booking_details.get('service_type', 'Service'),
                service_name=booking_details.get('service_name', 'Selected Service'),
                price=f"{booking_details.get('price', 0):,}",
                booking_url=booking_url
            )
            
            # Additional English instructions
            message += "\n\nPlease click the link above to select your appointment date and time."
            
        else:
            # Swahili version of the confirmation message
            template = get_message('thibitisha_booking_link', 'sw')
            message = template.format(
                service_type=booking_details.get('service_type', 'Huduma'),
                service_name=booking_details.get('service_name', 'Huduma Uliochagua'),
                price=f"{booking_details.get('price', 0):,}",
                booking_url=booking_url
            )
            
            # Additional Swahili instructions
            message += "\n\nTafadhali bofya kiungo hapo juu kuchagua tarehe na muda wa miadi yako."

        send_text_message(phone_number, message)
        
        # Reset session to main menu with current language
        session.step = MENU_STATES['MAIN']
        session.save()
        
        # Send language-appropriate follow-up message
        # if language == 'en':
        #     follow_up = "After booking, you'll receive a confirmation with payment options."
        # else:
        #     follow_up = "Baada ya kufanya booking, utapokea uthibitisho na maelezo ya malipo."
            
        # send_text_message(phone_number, follow_up)
        
    except Exception as e:
        logger.error(f"Error in confirm_booking: {str(e)}")
        language = session.data.get('language', 'sw')
        if language == 'en':
            send_text_message(phone_number, "Sorry, we encountered an error. Please try again.")
        else:
            send_text_message(phone_number, "Samahani, kuna hitilafu. Tafadhali jaribu tena.")


def handle_lipa_sasa(phone_number, session=None):
    """Unified payment handler for both languages"""
    try:
        # Get language from session or default to Swahili
        language = session.data.get('language', 'sw') if session else 'sw'
        
        # Get the latest booking
        booking = Booking.objects.filter(customer_phone=phone_number).order_by('-created_at').first()
        
        if not booking or not booking.price:
            send_text_message(phone_number, get_message('hakuna_booking', language))
            return send_main_menu(phone_number, language)
            
        if language == 'en':
            message = "Please enter your payment phone number (e.g., 0757123456):"
        else:
            message = get_message('ingiza_namba_ya_simu')
        
        send_text_message(phone_number, message)
        
        # Update session to expect payment phone
        session = WhatsappSession.objects.get(phone_number=phone_number)
        session.step = "AWAITING_PAYMENT_PHONE"
        session.data['payment_amount'] = float(booking.price)  # Convert Decimal to float
        session.save()
        
    except Exception as e:
        logger.error(f"Error initiating payment: {str(e)}")
        error_msg = get_message('hitilafu', language)
        send_text_message(phone_number, error_msg)
        
def cancel_booking(phone_number, session):
    """Cancel the booking and return to main menu"""
    send_text_message(phone_number, get_message('sitisha_booking'))
    session.step = MENU_STATES['MAIN']
    session.data.pop('booking_details', None)
    session.save()

def send_help_message(phone_number):
    """Send help message with contact information"""
    message = get_message('mawasiliano_msaidizi')
    
    send_text_message(phone_number, message)

def update_session_menu(phone_number, menu_state):
    """Update the current menu state in the user session"""
    try:
        session = WhatsappSession.objects.get(phone_number=phone_number)
        session.step = menu_state
        session.last_updated = timezone.now()
        session.save()
    except WhatsappSession.DoesNotExist:
        pass

# --- Message Sending Functions ---
def send_text_message(phone_number, text):
    """Send simple text message"""
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone_number,
        "type": "text",
        "text": {"body": text}
    }
    return whatsapp_api_call(payload)

def send_chunked_text_message(phone_number, text, max_chars=3200):
    """Send long text safely in WhatsApp-sized chunks."""
    if not text:
        return

    lines = text.splitlines()
    chunk = ""
    for line in lines:
        candidate = f"{chunk}\n{line}".strip() if chunk else line
        if len(candidate) <= max_chars:
            chunk = candidate
            continue

        if chunk:
            send_text_message(phone_number, chunk)
            chunk = line
        else:
            # Single line too long; hard split.
            for i in range(0, len(line), max_chars):
                send_text_message(phone_number, line[i:i + max_chars])
            chunk = ""

    if chunk:
        send_text_message(phone_number, chunk)

def send_list_message(phone_number, text, button_text, sections):
    """Send interactive list message"""
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone_number,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": text},
            "action": {
                "button": button_text,
                "sections": sections
            }
        }
    }
    whatsapp_api_call(payload)

def send_button_message(phone_number, message, button_text, buttons):
    """Send button message with proper language support"""
    try:
        # Get language from session or default to Swahili
        try:
            session = WhatsappSession.objects.get(phone_number=phone_number)
            language = session.data.get('language', 'sw')
        except:
            language = 'sw'

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone_number,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {
                    "text": message
                },
                "action": {
                    "buttons": buttons
                }
            }
        }

        # Add language-specific validation
        if language == 'en':
            # Ensure English template is approved
            payload['interactive']['header'] = {"type": "text", "text": "Payment Confirmation"}
        else:
            # Ensure Swahili template is approved
            payload['interactive']['header'] = {"type": "text", "text": "Uthibitishaji wa Malipo"}

        response = whatsapp_api_call(payload)
        return response
        
    except Exception as e:
        logger.error(f"Failed to send button message: {str(e)}")
        # Fallback to simple text message
        fallback_msg = f"{message}\n\nReply PAY to complete payment" if language == 'en' else f"{message}\n\nJibu LIPA kukamilisha malipo"
        send_text_message(phone_number, fallback_msg)

def whatsapp_api_call(payload):
    headers = {
        'Authorization': f'Bearer {WHATSAPP_ACCESS_TOKEN}',
        'Content-Type': 'application/json'
    }

    try:
        print("[DEBUG] Sending payload to WhatsApp API:")
        print(json.dumps(payload, indent=2))  # nicely formatted

        response = requests.post(
            f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages",
            headers=headers,
            json=payload,
            timeout=10
        )

        print(f"[DEBUG] HTTP Status Code: {response.status_code}")
        print(f"[DEBUG] Response Text: {response.text}")

        if response.status_code == 400:
            error_data = response.json()
            print(f"[ERROR] WhatsApp API Error: {json.dumps(error_data, indent=2)}")

            if 'error' in error_data:
                if error_data['error'].get('code') == 131031:
                    print("[ERROR] Invalid phone number format - must include country code")
                elif error_data['error'].get('code') == 132000:
                    print("[ERROR] Message template missing required parameters")

        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as e:
        print(f"[ERROR] API Request failed: {str(e)}")
        if hasattr(e, 'response') and e.response:
            print(f"[ERROR] Response content: {e.response.text}")
        raise ValueError("Failed to send WhatsApp message")


def send_short_hair_menu(phone_number, language='sw'):
    """Send Short Hair menu in selected language"""
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['language'] = language  # Update session language
    session.save()
    
    if language == 'en':
        send_short_hair_menu_en(phone_number)
    else:
        send_short_hair_menu_sw(phone_number)

def send_short_hair_menu_sw(phone_number):
    """Send Short Hair services in Swahili with categorized menu"""
    services_by_category = {
        "KUBOND SHORT HAIR NA WIG": [
            {"name": "KuBond Wigs kila kitu kwangu", "price_key": "bond_wigs_price"},
            {"name": "Kubond Short Wig kila kitu kwangu", "price_key": "kubond_short_wig_price"},
            {"name": "Kushonea Short Wig", "price_key": "kushonea_short_wig_price"},
            {"name": "Kubond Lace Wig", "price_key": "kubond_lace_wig_price"},
            {"name": "Kushonea Weaving (nywele ya mteja)", "price_key": "kushonea_weaving_price"},
            {"name": "Extension (nywele ya mteja)", "price_key": "extension_price"},
            {"name": "Installation Wigs", "price_key": "installation_wigs_price"},
            {"name": "Kupaka Rangi Wig", "price_key": "kupaka_rangi_wig_price"},
            {"name": "Kuosha Wig", "price_key": "kuosha_wig_price"},
            {"name": "Kupass Mawigi", "price_key": "kupass_mawigi_price"},
        ],
        "MAWIMBI WIG": [
            {"name": "Kuwaeka Mawimbi Makubwa Wig", "price_key": "kuweka_mawimbi_makubwa_wig_price"},
            {"name": "Kuwaeka Mawimbi Medium", "price_key": "kuweka_mawimbi_medium_price"},
            {"name": "Kuwaeka Mawimbi Small", "price_key": "kuweka_mawimbi_small_price"},
            {"name": "Kuwaeka Mawimbi Donyo", "price_key": "kuweka_mawimbi_donyo_price"},
        ],
        "KUPAKA RANGI NYWELE": [
            {"name": "Kupaka Rangi Nywele", "price_key": "kupaka_rangi_nywele_price"},
            {"name": "Kupaka Rangi Nywele Premium", "price_key": "kupaka_rangi_nywele_premium_price"},
            {"name": "Kupaka Rangi Wig Premium", "price_key": "kupaka_rangi_wig_premium_price"},
            {"name": "Kupass Natural Hair", "price_key": "kupass_natural_hair_price"},
            {"name": "Kutone Wigs", "price_key": "kutone_wigs_price"},
        ]
    }

    all_services = []
    for category_services in services_by_category.values():
        all_services.extend(category_services)

    # Get prices for all services with error handling
    for service in all_services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")

    menu_text = get_message('shorthair_kichwa_cha_huduma', 'sw')
    counter = 1
    service_list = []
    
    for header, services in services_by_category.items():
        filtered_services = [s for s in services if s["price"] > 0]
        if not filtered_services:
            continue
        menu_text += f"\n\n*{header}*"
        for service in filtered_services:
            menu_text += f"\n{counter}. {service['name']} - Tsh {service['price']:,}"
            service_list.append({"name": service["name"], "price": service["price"]})
            counter += 1

    if not service_list:
        send_text_message(phone_number, get_message('hitilafu', 'sw'))
        return send_main_menu(phone_number, 'sw')

    menu_text += "\n\nKURUDI MENU KUU BOFYA #"
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['SHORT_HAIR'])

    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = service_list
    session.save()

def send_short_hair_menu_en(phone_number):
    """Send Short Hair services in English with categorized menu"""
    services_by_category = {
        "KUBOND SHORT HAIR AND WIG": [
            {"name": "Bond Wigs", "price_key": "bond_wigs_price"},
            {"name": "Bond Short Wig", "price_key": "kubond_short_wig_price"},
            {"name": "Sew-in Short Wig", "price_key": "kushonea_short_wig_price"},
            {"name": "Bond Lace Wig", "price_key": "kubond_lace_wig_price"},
            {"name": "Sew-in Weave (customer's hair)", "price_key": "kushonea_weaving_price"},
            {"name": "Hair Extensions (customer's hair)", "price_key": "extension_price"},
            {"name": "Wig Installation", "price_key": "installation_wigs_price"},
            {"name": "Wig Coloring", "price_key": "kupaka_rangi_wig_price"},
            {"name": "Wig Washing", "price_key": "kuosha_wig_price"},
            {"name": "Wig Styling", "price_key": "kupass_mawigi_price"},
        ],
        "WIG WAVES": [
            {"name": "Large Waves Wig Styling", "price_key": "kuweka_mawimbi_makubwa_wig_price"},
            {"name": "Medium Waves Styling", "price_key": "kuweka_mawimbi_medium_price"},
            {"name": "Small Waves Styling", "price_key": "kuweka_mawimbi_small_price"},
            {"name": "Tiny Waves Styling", "price_key": "kuweka_mawimbi_donyo_price"},
        ],
        "HAIR COLORING": [
            {"name": "Hair Coloring", "price_key": "kupaka_rangi_nywele_price"},
            {"name": "Premium Hair Coloring", "price_key": "kupaka_rangi_nywele_premium_price"},
            {"name": "Premium Wig Coloring", "price_key": "kupaka_rangi_wig_premium_price"},
            {"name": "Natural Hair Styling", "price_key": "kupass_natural_hair_price"},
            {"name": "Custom Wigs", "price_key": "kutone_wigs_price"},
        ]
    }

    all_services = []
    for category_services in services_by_category.values():
        all_services.extend(category_services)

    # Get prices for all services with error handling
    for service in all_services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")

    menu_text = get_message('shorthair_kichwa_cha_huduma_en', 'en')
    counter = 1
    service_list = []
    
    for header, services in services_by_category.items():
        filtered_services = [s for s in services if s["price"] > 0]
        if not filtered_services:
            continue
        menu_text += f"\n\n📌 {header}"
        for service in filtered_services:
            menu_text += f"\n{counter}. {service['name']} - Tsh {service['price']:,}"
            service_list.append({"name": service["name"], "price": service["price"]})
            counter += 1

    if not service_list:
        send_text_message(phone_number, get_message('hitilafu_en', 'en'))
        return send_main_menu(phone_number, 'en')

    menu_text += "\n\nTO RETURN MAIN MENU ENTER #"
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['SHORT_HAIR'])

    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = service_list
    session.save()

def handle_short_hair_selection(phone_number, text, session):
    """Handle Short Hair service selection with prices in selected language"""
    if 'current_services' not in session.data:
        language = session.data.get('language', 'sw')
        send_short_hair_menu(phone_number, language)
        return
    
    try:
        choice = int(text.strip())
        services = session.data['current_services']
        language = session.data.get('language', 'sw')
        
        if 1 <= choice <= len(services):
            selected_service = services[choice-1]
            session.data['booking_details'] = {
                'service_type': SERVICE_TYPES['SHORT_HAIR'],
                'service_name': f"{selected_service['name']} - Tsh {selected_service['price']:,}",
                'price': selected_service['price']
            }
            session.step = MENU_STATES['CONFIRMATION']
            session.save()
            
            # Send confirmation in the correct language
            if language == 'en':
                message = f"""🧾 FINAL: CONFIRM BOOKING

You selected: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Do you want to confirm booking for this service?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Yes"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ No"}}
                ]
                send_button_message(phone_number, message, "Confirm", buttons)
            else:
                message = f"""🧾 MWISHO: THIBITISHA BOOKING

Umechagua: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Je, unataka kuthibitisha booking kwa huduma uliyochagua?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Ndiyo"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ Hapana"}}
                ]
                send_button_message(phone_number, message, "Thibitisha", buttons)
        else:
            if language == 'en':
                send_text_message(phone_number, f"Invalid number. Please send a number between 1 and {len(services)}.")
            else:
                send_text_message(phone_number, f"Namba si sahihi. Tafadhali tuma namba kati ya 1 na {len(services)}.")
            send_short_hair_menu(phone_number, language)
    except ValueError:
        language = session.data.get('language', 'sw')
        if language == 'en':
            send_text_message(phone_number, "Please send numbers only (1-7).")
        else:
            send_text_message(phone_number, "Tafadhali tuma namba tu (1-7).")
        send_short_hair_menu(phone_number, language)

def send_makeup_menu(phone_number, language='sw'):
    """Send Makeup menu in selected language"""
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['language'] = language  # Update session language
    session.save()
    
    if language == 'en':
        send_makeup_menu_en(phone_number)
    else:
        send_makeup_menu_sw(phone_number)

def send_makeup_menu_sw(phone_number):
    """Send Makeup services in Swahili"""
    services = [
        {"name": "Full Makeup", "price_key": "full_makeup_price"},
        {"name": "Simple Makeup", "price_key": "simple_makeup_price"},
        {"name": "Bridal Makeup", "price_key": "bridal_makeup_price"},
        {"name": "Evening Makeup", "price_key": "evening_makeup_price"},
        {"name": "Photoshoot Makeup", "price_key": "photoshoot_makeup_price"}
    ]
    
    # Get prices for all services with error handling
    for service in services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")
    
    # Filter out services with price 0 (not available)
    available_services = [s for s in services if s["price"] > 0]
    
    if not available_services:
        send_text_message(phone_number, get_message('hitilafu', 'sw'))
        return send_main_menu(phone_number, 'sw')
    
    menu_text = get_message('makeup_kichwa_cha_huduma', 'sw')
    for i, service in enumerate(available_services, start=1):
        menu_text += f"\n{i}. {service['name']} - Tsh {service['price']:,}"
    menu_text += "\n\nTuma namba ya huduma unayotaka:"
    
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['MAKEUP'])
    
    # Store services in session for reference
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": service["name"], "price": service["price"]} 
        for service in available_services
    ]
    session.save()

def send_makeup_menu_en(phone_number):
    """Send Makeup services in English"""
    services = [
        {"name": "Full Makeup", "price_key": "full_makeup_price"},
        {"name": "Simple Makeup", "price_key": "simple_makeup_price"},
        {"name": "Bridal Makeup", "price_key": "bridal_makeup_price"},
        {"name": "Evening Makeup", "price_key": "evening_makeup_price"},
        {"name": "Photoshoot Makeup", "price_key": "photoshoot_makeup_price"}
    ]
    
    # Get prices for all services with error handling
    for service in services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")
    
    # Filter out services with price 0 (not available)
    available_services = [s for s in services if s["price"] > 0]
    
    if not available_services:
        send_text_message(phone_number, get_message('hitilafu_en', 'en'))
        return send_main_menu(phone_number, 'en')
    
    menu_text = get_message('makeup_kichwa_cha_huduma_en', 'en')
    for i, service in enumerate(available_services, start=1):
        menu_text += f"\n{i}. {service['name']} - Tsh {service['price']:,}"
    menu_text += "\n\nSend the number of the service you want:"
    
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['MAKEUP'])
    
    # Store services in session for reference
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": service["name"], "price": service["price"]} 
        for service in available_services
    ]
    session.save()

def handle_makeup_selection(phone_number, text, session):
    """Handle Makeup service selection with prices in selected language"""
    if 'current_services' not in session.data:
        language = session.data.get('language', 'sw')
        send_makeup_menu(phone_number, language)
        return
    
    try:
        choice = int(text.strip())
        services = session.data['current_services']
        language = session.data.get('language', 'sw')
        
        if 1 <= choice <= len(services):
            selected_service = services[choice-1]
            session.data['booking_details'] = {
                'service_type': SERVICE_TYPES['MAKEUP'],
                'service_name': f"{selected_service['name']} - Tsh {selected_service['price']:,}",
                'price': selected_service['price']
            }
            session.step = MENU_STATES['CONFIRMATION']
            session.save()
            
            # Send confirmation in the correct language
            if language == 'en':
                message = f"""🧾 FINAL: CONFIRM BOOKING

You selected: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Do you want to confirm booking for this service?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Yes"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ No"}}
                ]
                send_button_message(phone_number, message, "Confirm", buttons)
            else:
                message = f"""🧾 MWISHO: THIBITISHA BOOKING

Umechagua: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Je, unataka kuthibitisha booking kwa huduma uliyochagua?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Ndiyo"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ Hapana"}}
                ]
                send_button_message(phone_number, message, "Thibitisha", buttons)
        else:
            if language == 'en':
                send_text_message(phone_number, f"Invalid number. Please send a number between 1 and {len(services)}.")
            else:
                send_text_message(phone_number, f"Namba si sahihi. Tafadhali tuma namba kati ya 1 na {len(services)}.")
            send_makeup_menu(phone_number, language)
    except ValueError:
        language = session.data.get('language', 'sw')
        if language == 'en':
            send_text_message(phone_number, "Please send numbers only (1-5).")
        else:
            send_text_message(phone_number, "Tafadhali tuma namba tu (1-5).")
        send_makeup_menu(phone_number, language)

def send_kucha_menu(phone_number, language='sw'):
    """Send Kucha menu in selected language"""
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['language'] = language
    session.save()
    
    if language == 'en':
        send_kucha_menu_en(phone_number)
    else:
        send_kucha_menu_sw(phone_number)

def send_kucha_menu_sw(phone_number):
    services = [
        {"name": "Kubandika Kucha na kupaka gel", "price_key": "kubandika_kucha_price"},
        {"name": "Gel Polish", "price_key": "gel_polish_price"},
        {"name": "Poly Gel", "price_key": "poly_gel_price"},
        {"name": "Builder Gel", "price_key": "builder_gel_price"},
        {"name": "Kucha ya Unga", "price_key": "kucha_ya_unga_price"},
        {"name": "Pedicure", "price_key": "pedicure_price"},
        {"name": "Manicure", "price_key": "manicure_price"},
        {"name": "Pol gel", "price_key": "pol_gel_price"}
    ]
    
    # Get prices for all services
    for service in services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")
    
    available_services = [s for s in services if s["price"] > 0]
    
    if not available_services:
        send_text_message(phone_number, get_message('hitilafu', 'sw'))
        return send_main_menu(phone_number, 'sw')
    
    menu_text = get_message('kucha_huduma_kichwa', 'sw')
    for i, service in enumerate(available_services, start=1):
        menu_text += f"\n{i}. {service['name']} - Tsh {service['price']:,}"
    menu_text += "\n\nTuma namba ya huduma unayotaka:"
    
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['KUCHA'])
    
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": service["name"], "price": service["price"]} 
        for service in available_services
    ]
    session.save()

def send_kucha_menu_en(phone_number):
    services = [
        {"name": "Nail Extensions and polish with jell", "price_key": "kubandika_kucha_price"},
        {"name": "Gel Polish", "price_key": "gel_polish_price"},
        {"name": "Poly Gel", "price_key": "poly_gel_price"},
        {"name": "Builder Gel", "price_key": "builder_gel_price"},
        {"name": "Acrylic Nails", "price_key": "kucha_ya_unga_price"},
        {"name": "Pedicure", "price_key": "pedicure_price"},
        {"name": "Manicure", "price_key": "manicure_price"},
        {"name": "Pol gel", "price_key": "pol_gel_price"}
    ]
    
    for service in services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")
    
    available_services = [s for s in services if s["price"] > 0]
    
    if not available_services:
        send_text_message(phone_number, get_message('hitilafu', 'en'))
        return send_main_menu(phone_number, 'en')
    
    menu_text = get_message('kucha_huduma_kichwa_en', 'en')
    for i, service in enumerate(available_services, start=1):
        menu_text += f"\n{i}. {service['name']} - Tsh {service['price']:,}"
    menu_text += "\n\nSend the number of the service you want:"
    
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['KUCHA'])
    
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": service["name"], "price": service["price"]} 
        for service in available_services
    ]
    session.save()

def handle_kucha_selection(phone_number, text, session):
    if 'current_services' not in session.data:
        language = session.data.get('language', 'sw')
        send_kucha_menu(phone_number, language)
        return
    
    try:
        choice = int(text.strip())
        services = session.data['current_services']
        language = session.data.get('language', 'sw')
        
        if 1 <= choice <= len(services):
            selected_service = services[choice-1]
            session.data['booking_details'] = {
                'service_type': SERVICE_TYPES['KUCHA'],
                'service_name': f"{selected_service['name']} - Tsh {selected_service['price']:,}",
                'price': selected_service['price']
            }
            session.step = MENU_STATES['CONFIRMATION']
            session.save()
            
            if language == 'en':
                message = f"""🧾 FINAL: CONFIRM BOOKING

You selected: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Do you want to confirm booking for this service?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Yes"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ No"}}
                ]
                send_button_message(phone_number, message, "Confirm", buttons)
            else:
                message = f"""🧾 MWISHO: THIBITISHA BOOKING

Umechagua: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Je, unataka kuthibitisha booking kwa huduma uliyochagua?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Ndiyo"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ Hapana"}}
                ]
                send_button_message(phone_number, message, "Thibitisha", buttons)
        else:
            if language == 'en':
                send_text_message(phone_number, f"Invalid number. Please send a number between 1 and {len(services)}.")
            else:
                send_text_message(phone_number, f"Namba si sahihi. Tafadhali tuma namba kati ya 1 na {len(services)}.")
            send_kucha_menu(phone_number, language)
    except ValueError:
        language = session.data.get('language', 'sw')
        if language == 'en':
            send_text_message(phone_number, "Please send numbers only (1-7).")
        else:
            send_text_message(phone_number, "Tafadhali tuma namba tu (1-7).")
        send_kucha_menu(phone_number, language)

def send_kope_menu(phone_number, language='sw'):
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['language'] = language
    session.save()
    
    if language == 'en':
        send_kope_menu_en(phone_number)
    else:
        send_kope_menu_sw(phone_number)

def send_kope_menu_sw(phone_number):
    services = [
        {"name": "Lash Extension Original", "price_key": "lash_extension_original_price"},
        {"name": "Lash Extension Mirija(Human hair", "price_key": "lash_extension_mirija_price"},
        {"name": "Kope Kawaida", "price_key": "kope_kawaida_price"},
        # {"name": "Kope ya Mkanda", "price_key": "kope_ya_mkanda_price"},
        {"name": "Kuchonga kwa Uzi", "price_key": "kuchonga_uzi_price"},
        {"name": "Kulinda Nyusi", "price_key": "kulinda_nyusi_price"},
        {"name": "Kuchonga Kawaida", "price_key": "kuchonga_kawaida_price"},
        {"name": "Wanja wa Piko", "price_key": "wanja_piko_price"}
    ]
    
    for service in services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")
    
    available_services = [s for s in services if s["price"] > 0]
    
    if not available_services:
        send_text_message(phone_number, get_message('hitilafu', 'sw'))
        return send_main_menu(phone_number, 'sw')
    
    menu_text = get_message('kope_huduma_kichwa', 'sw')
    for i, service in enumerate(available_services, start=1):
        menu_text += f"\n{i}. {service['name']} - Tsh {service['price']:,}"
    menu_text += "\n\nTuma namba ya huduma unayotaka:"
    
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['KOPE'])
    
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": service["name"], "price": service["price"]} 
        for service in available_services
    ]
    session.save()

def send_kope_menu_en(phone_number):
    services = [
        {"name": "Original Lash Extensions", "price_key": "lash_extension_original_price"},
        {"name": "Original Lash Extensions(Human Hair)", "price_key": "lash_extension_mirija_price"},
        {"name": "Classic Lashes", "price_key": "kope_kawaida_price"},
        # {"name": "Strip Lashes", "price_key": "kope_ya_mkanda_price"},
        {"name": "Threading", "price_key": "kuchonga_uzi_price"},
        {"name": "Protect Lashes", "price_key": "kulinda_nyusi_price"},
        {"name": "Standard Shaping", "price_key": "kuchonga_kawaida_price"},
        {"name": "Henna Brows", "price_key": "wanja_piko_price"}
    ]
    
    for service in services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")
    
    available_services = [s for s in services if s["price"] > 0]
    
    if not available_services:
        send_text_message(phone_number, get_message('hitilafu_en', 'en'))
        return send_main_menu(phone_number, 'en')
    
    menu_text = get_message('kope_huduma_kichwa_en', 'en')
    for i, service in enumerate(available_services, start=1):
        menu_text += f"\n{i}. {service['name']} - Tsh {service['price']:,}"
    menu_text += "\n\nSend the number of the service you want:"
    
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['KOPE'])
    
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": service["name"], "price": service["price"]} 
        for service in available_services
    ]
    session.save()

def handle_kope_selection(phone_number, text, session):
    if 'current_services' not in session.data:
        language = session.data.get('language', 'sw')
        send_kope_menu(phone_number, language)
        return
    
    try:
        choice = int(text.strip())
        services = session.data['current_services']
        language = session.data.get('language', 'sw')
        
        if 1 <= choice <= len(services):
            selected_service = services[choice-1]
            session.data['booking_details'] = {
                'service_type': SERVICE_TYPES['KOPE'],
                'service_name': f"{selected_service['name']} - Tsh {selected_service['price']:,}",
                'price': selected_service['price']
            }
            session.step = MENU_STATES['CONFIRMATION']
            session.save()
            
            if language == 'en':
                message = f"""🧾 FINAL: CONFIRM BOOKING

You selected: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Do you want to confirm booking for this service?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Yes"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ No"}}
                ]
                send_button_message(phone_number, message, "Confirm", buttons)
            else:
                message = f"""🧾 MWISHO: THIBITISHA BOOKING

Umechagua: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Je, unataka kuthibitisha booking kwa huduma uliyochagua?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Ndiyo"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ Hapana"}}
                ]
                send_button_message(phone_number, message, "Thibitisha", buttons)
        else:
            if language == 'en':
                send_text_message(phone_number, f"Invalid number. Please send a number between 1 and {len(services)}.")
            else:
                send_text_message(phone_number, f"Namba si sahihi. Tafadhali tuma namba kati ya 1 na {len(services)}.")
            send_kope_menu(phone_number, language)
    except ValueError:
        language = session.data.get('language', 'sw')
        if language == 'en':
            send_text_message(phone_number, "Please send numbers only (1-3).")
        else:
            send_text_message(phone_number, "Tafadhali tuma namba tu (1-3).")
        send_kope_menu(phone_number, language)

        
def send_kutoboa_menu(phone_number, language='sw'):
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['language'] = language
    session.save()
    
    if language == 'en':
        send_kutoboa_menu_en(phone_number)
    else:
        send_kutoboa_menu_sw(phone_number)

def send_kutoboa_menu_sw(phone_number):
    services = [
        {"name": "Pua (Pamoja na Heleni)", "price_key": "pua_piercing_price"},
        {"name": "Sikio", "price_key": "sikio_piercing_price"},
        {"name": "Ulimi", "price_key": "ulimi_piercing_price"},
        {"name": "Kitovu", "price_key": "kitovu_piercing_price"}
    ]
    
    for service in services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")
    
    available_services = [s for s in services if s["price"] > 0]
    
    if not available_services:
        send_text_message(phone_number, get_message('hitilafu', 'sw'))
        return send_main_menu(phone_number, 'sw')
    
    menu_text = get_message('kutoboa_huduma_kichwa', 'sw')
    for i, service in enumerate(available_services, start=1):
        menu_text += f"\n{i}. {service['name']} - Tsh {service['price']:,}"
    menu_text += "\n\nTuma namba ya huduma unayotaka:"
    
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['KUTOBOA'])
    
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": service["name"], "price": service["price"]} 
        for service in available_services
    ]
    session.save()

def send_kutoboa_menu_en(phone_number):
    services = [
        {"name": "Nose (with jewelry)", "price_key": "pua_piercing_price"},
        {"name": "Ear", "price_key": "sikio_piercing_price"},
        {"name": "Tongue", "price_key": "ulimi_piercing_price"},
        {"name": "Navel", "price_key": "kitovu_piercing_price"}
    ]
    
    for service in services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")
    
    available_services = [s for s in services if s["price"] > 0]
    
    if not available_services:
        send_text_message(phone_number, get_message('hitilafu_en', 'en'))
        return send_main_menu(phone_number, 'en')
    
    menu_text = get_message('kutoboa_huduma_kichwa_en', 'en')
    for i, service in enumerate(available_services, start=1):
        menu_text += f"\n{i}. {service['name']} - Tsh {service['price']:,}"
    menu_text += "\n\nSend the number of the service you want:"
    
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['KUTOBOA'])
    
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": service["name"], "price": service["price"]} 
        for service in available_services
    ]
    session.save()

def handle_kutoboa_selection(phone_number, text, session):
    if 'current_services' not in session.data:
        language = session.data.get('language', 'sw')
        send_kutoboa_menu(phone_number, language)
        return
    
    try:
        choice = int(text.strip())
        services = session.data['current_services']
        language = session.data.get('language', 'sw')
        
        if 1 <= choice <= len(services):
            selected_service = services[choice-1]
            session.data['booking_details'] = {
                'service_type': SERVICE_TYPES['KUTOBOA'],
                'service_name': f"{selected_service['name']} - Tsh {selected_service['price']:,}",
                'price': selected_service['price']
            }
            session.step = MENU_STATES['CONFIRMATION']
            session.save()
            
            if language == 'en':
                message = f"""🧾 FINAL: CONFIRM BOOKING

You selected: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Do you want to confirm booking for this service?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Yes"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ No"}}
                ]
                send_button_message(phone_number, message, "Confirm", buttons)
            else:
                message = f"""🧾 MWISHO: THIBITISHA BOOKING

Umechagua: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Je, unataka kuthibitisha booking kwa huduma uliyochagua?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Ndiyo"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ Hapana"}}
                ]
                send_button_message(phone_number, message, "Thibitisha", buttons)
        else:
            if language == 'en':
                send_text_message(phone_number, f"Invalid number. Please send a number between 1 and {len(services)}.")
            else:
                send_text_message(phone_number, f"Namba si sahihi. Tafadhali tuma namba kati ya 1 na {len(services)}.")
            send_kutoboa_menu(phone_number, language)
    except ValueError:
        language = session.data.get('language', 'sw')
        if language == 'en':
            send_text_message(phone_number, "Please send numbers only (1-4).")
        else:
            send_text_message(phone_number, "Tafadhali tuma namba tu (1-4).")
        send_kutoboa_menu(phone_number, language)

def send_kuosha_menu(phone_number, language='sw'):
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['language'] = language
    session.save()
    
    if language == 'en':
        send_kuosha_menu_en(phone_number)
    else:
        send_kuosha_menu_sw(phone_number)

def send_kuosha_menu_sw(phone_number):
    services = [
        {"header": "KUOSHA NYWELE NA KUKAUSHA"},
        {"name": "Kuosha wigi", "price_key": "kuosha_wigi"},
        {"name": "Kuosha nywele na kukausha", "price_key": "kuosha_na_kukausha"},
        {"name": "Kukausha nywele natural tu", "price_key": "kukausha_natural"},
        {"name": "Kuosha na kurepair Rasta", "price_key": "kuosha_na_kurepair_rasta"},
        {"name": "Kuosha na kurepair nywele ya dread", "price_key": "kuosha_na_kurepair_dread"},
        {"name": "Kuosha na kuweka mirija", "price_key": "kuosha_na_kuweka_mirija"},
        {"name": "Kuosha na kuweka finger coil", "price_key": "kuosha_na_kuweka_finger_coil"},
        {"name": "Netwave", "price_key": "netwave"},
        {"name": "Kuosha na kubana style (Rasta Juu ya mteja)", "price_key": "kuosha_na_kubana_style"},
        
        {"header": "KUFUMUA NYWELE BILA KUOSHA"},
        {"name": "Kufumua nywele tuu hapo bila kuosha (Wawa)", "price_key": "kufumua_bila_kuosha_wawa"},
        {"name": "Kufumua nywele tuu hapo bila kuosha (Medium)", "price_key": "kufumua_bila_kuosha_medium"},
        {"name": "Kufumua nywele tuu hapo bila kuosha (Smart kidogo)", "price_key": "kufumua_bila_kuosha_smart_kidogo"},
        {"name": "Kufumua nywele tuu hapo bila kuosha (Smart sana)", "price_key": "kufumua_bila_kuosha_smart_sana"},
        {"name": "Kufumua nywele tuu hapo bila kuosha (Donyo)", "price_key": "kufumua_bila_kuosha_donyo"},
        {"name": "Kufumua nywele tuu hapo bila kuosha (Donyo sana)", "price_key": "kufumua_bila_kuosha_donyo_sana"},

        {"header": "KUWEKA DAWA NYWELE"},
        {"name": "Kuweka dawa (dawa juu ya mteja)", "price_key": "kuweka_dawa"},
        {"name": "Kufanya steaming", "price_key": "steaming_price"},
        {"name": "Kuweka rangi kichwani (20,000 - 100,000)", "price_key": "kuweka_rangi_kichwani"},
        {"name": "Kuweka rangi nyeusi kichwani", "price_key": "kuweka_rangi_nyeusi"},
    ]

    available_services = []
    for service in services:
        if "header" in service:
            available_services.append(service)
        else:
            try:
                price_str = get_message(service["price_key"], 'sw')
                service["price"] = int(price_str) if price_str else 0
            except (ValueError, TypeError):
                service["price"] = 0
                logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")
            if service["price"] > 0:
                available_services.append(service)

    if not any("name" in s for s in available_services):
        send_text_message(phone_number, get_message('hitilafu', 'sw'))
        return send_main_menu(phone_number, 'sw')

    menu_text = get_message('kuosha_huduma_kichwa', 'sw')
    index = 1
    for item in available_services:
        if "header" in item:
            menu_text += f"\n\n*{item['header']}*"
        else:
            menu_text += f"\n{index}. {item['name']} - Tsh {item['price']:,}"
            index += 1

    menu_text += "\n\nKURUDI MENU KUU BOFYA #"
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['KUOSHA'])

    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = [
        {"name": s["name"], "price": s["price"]} for s in available_services if "name" in s
    ]
    session.save()


def send_kuosha_menu_en(phone_number):
    services_by_category = {
        
        "Hair Washing and Drying": [
            {"name": "Wash wig", "price_key": "kuosha_wigi"},
            {"name": "Wash and dry hair", "price_key": "kuosha_na_kukausha"},
            {"name": "Dry natural hair only", "price_key": "kukausha_natural"},
            {"name": "Wash and repair Rasta", "price_key": "kuosha_na_kurepair_rasta"},
            {"name": "Wash and repair dread hair", "price_key": "kuosha_na_kurepair_dread"},
            {"name": "Wash and install tubes", "price_key": "kuosha_na_kuweka_mirija"},
            {"name": "Wash and install finger coil", "price_key": "kuosha_na_kuweka_finger_coil"},
            {"name": "Netwave", "price_key": "netwave"},
            {"name": "Wash and tighten style (Client’s Rasta)", "price_key": "kuosha_na_kubana_style"},
        ],
        
        "Undo Hair Without Washing": [
            {"name": "Undo hair only without washing (Wawa)", "price_key": "kufumua_bila_kuosha_wawa"},
            {"name": "Undo hair only without washing (Medium)", "price_key": "kufumua_bila_kuosha_medium"},
            {"name": "Undo hair only without washing (Slightly smart)", "price_key": "kufumua_bila_kuosha_smart_kidogo"},
            {"name": "Undo hair only without washing (Very smart)", "price_key": "kufumua_bila_kuosha_smart_sana"},
            {"name": "Undo hair only without washing (Donyo)", "price_key": "kufumua_bila_kuosha_donyo"},
            {"name": "Undo hair only without washing (Very Donyo)", "price_key": "kufumua_bila_kuosha_donyo_sana"},
        ],
        
        "Hair Chemical Treatments": [
            {"name": "Apply chemical (Client’s chemical)", "price_key": "kuweka_dawa"},
            {"name": "Do steaming", "price_key": "steaming"},
            {"name": "Apply color on scalp (20,000 - 100,000)", "price_key": "kuweka_rangi_kichwani"},
            {"name": "Apply black color in the head", "price_key": "kuweka_rangi_nyeusi"},
        ],
    }

    all_services = []
    for category_services in services_by_category.values():
        all_services.extend(category_services)

    for service in all_services:
        try:
            price_str = get_message(service["price_key"], 'sw')
            service["price"] = int(price_str) if price_str else 0
        except (ValueError, TypeError):
            service["price"] = 0
            logger.warning(f"Invalid price for {service['price_key']}, defaulting to 0")

    menu_text = get_message('kuosha_huduma_kichwa_en', 'en')
    counter = 1
    service_list = []
    for header, services in services_by_category.items():
        filtered_services = [s for s in services if s["price"] > 0]
        if not filtered_services:
            continue
        menu_text += f"\n\n📌 {header}"
        for service in filtered_services:
            menu_text += f"\n{counter}. {service['name']} - Tsh {service['price']:,}"
            service_list.append({"name": service["name"], "price": service["price"]})
            counter += 1

    if not service_list:
        send_text_message(phone_number, get_message('hitilafu_en', 'en'))
        return send_main_menu(phone_number, 'en')

    menu_text += "\n\nTO RETURN MAIN MENU ENTER #"
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, MENU_STATES['KUOSHA'])

    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_services'] = service_list
    session.save()


def handle_kuosha_selection(phone_number, text, session):
    if 'current_services' not in session.data:
        language = session.data.get('language', 'sw')
        send_kuosha_menu(phone_number, language)
        return
    
    try:
        choice = int(text.strip())
        services = session.data['current_services']
        language = session.data.get('language', 'sw')
        
        if 1 <= choice <= len(services):
            selected_service = services[choice-1]
            session.data['booking_details'] = {
                'service_type': SERVICE_TYPES['KUOSHA'],
                'service_name': f"{selected_service['name']} - Tsh {selected_service['price']:,}",
                'price': selected_service['price']
            }
            session.step = MENU_STATES['CONFIRMATION']
            session.save()
            
            if language == 'en':
                message = f"""🧾 FINAL: CONFIRM BOOKING

You selected: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Do you want to confirm booking for this service?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Yes"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ No"}}
                ]
                send_button_message(phone_number, message, "Confirm", buttons)
            else:
                message = f"""🧾 MWISHO: THIBITISHA BOOKING

Umechagua: {session.data['booking_details']['service_type']} - {session.data['booking_details']['service_name']}

Je, unataka kuthibitisha booking kwa huduma uliyochagua?"""
                buttons = [
                    {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Ndiyo"}},
                    {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ Hapana"}}
                ]
                send_button_message(phone_number, message, "Thibitisha", buttons)
        else:
            if language == 'en':
                send_text_message(phone_number, f"Invalid number. Please send a number between 1 and {len(services)}.")
            else:
                send_text_message(phone_number, f"Namba si sahihi. Tafadhali tuma namba kati ya 1 na {len(services)}.")
            send_kuosha_menu(phone_number, language)
    except ValueError:
        language = session.data.get('language', 'sw')
        if language == 'en':
            send_text_message(phone_number, "Please send numbers only (1-5).")
        else:
            send_text_message(phone_number, "Tafadhali tuma namba tu (1-5).")
        send_kuosha_menu(phone_number, language)

def send_text_menu(phone_number, menu_title, options, menu_state):
    """
    Generic function to send text-based menus
    """
    menu_text = f"{menu_title}\n\nChagua kwa kutumia namba:\n"
    
    for i, option in enumerate(options, start=1):
        menu_text += f"{i}. {option['title']}\n"
    
    menu_text += "\nTuma namba ya huduma unayotaka:"
    
    send_text_message(phone_number, menu_text)
    update_session_menu(phone_number, menu_state)
    
    # Store options in session
    session = WhatsappSession.objects.get(phone_number=phone_number)
    session.data['current_options'] = options
    session.save()
        
def handle_text_selection(phone_number, text, session, next_handler):
    """
    Generic handler for text-based selections
    """
    if 'current_options' not in session.data:
        send_main_menu(phone_number)
        return
    
    try:
        choice = int(text.strip())
        options = session.data['current_options']
        
        if 1 <= choice <= len(options):
            selected_option = options[choice-1]
            next_handler(phone_number, selected_option['id'], session)
        else:
            send_text_message(phone_number, 
                            f"Namba si sahihi. Tafadhali tuma namba kati ya 1 na {len(options)}.")
            handle_menu_redispatch(phone_number, session)
    except ValueError:
        send_text_message(phone_number, "Tafadhali tuma namba tu.")
        handle_menu_redispatch(phone_number, session)
            
def handle_menu_redispatch(phone_number, session):
    """Re-send the current menu based on session state"""
    menu_handlers = {
        MENU_STATES['KUSUKA']: lambda: send_kusuka_menu(phone_number),
        MENU_STATES['NATURAL_HAIR']: lambda: send_natural_hair_menu(phone_number),
        MENU_STATES['SHORT_HAIR']: lambda: send_short_hair_menu(phone_number),
        MENU_STATES['MAKEUP']: lambda: send_makeup_menu(phone_number),
        MENU_STATES['KUCHA']: lambda: send_kucha_menu(phone_number),
        MENU_STATES['KOPE']: lambda: send_kope_menu(phone_number),
        # MENU_STATES['NYUSI']: lambda: send_nyusi_menu(phone_number),
        MENU_STATES['KUTOBOA']: lambda: send_kutoboa_menu(phone_number),
        MENU_STATES['KUOSHA']: lambda: send_kuosha_menu(phone_number)
    }
    
    handler = menu_handlers.get(session.step)
    if handler:
        handler()
        
        
def admin_dashboard(request):
    bookings = Booking.objects.all().order_by('-appointment_date')[:200]
    recent_customers = Customer.objects.all().order_by('-join_date')[:5]
    status_counts = {
        'all': Booking.objects.count(),
        'pending': Booking.objects.filter(status='pending').count(),
        'confirmed': Booking.objects.filter(status='confirmed').count(),
        'completed': Booking.objects.filter(status='completed').count(),
        'cancelled': Booking.objects.filter(status='cancelled').count(),
    }
    context = {
        'bookings': bookings,
        'recent_customers': recent_customers,
        'total_bookings': Booking.objects.count(),
        'total_customers': Customer.objects.count(),
        'status_counts': status_counts,
        'active_page': 'admin_dashboard',
    }
    return render(request, 'admin/dashboard.html', context)

def booking_dashboard(request):
    status = request.GET.get('status', 'all')
    selected_date = request.GET.get('date')
    
    bookings = Booking.objects.all()

    if status != 'all':
        bookings = bookings.filter(status=status)

    if selected_date:
        try:
            date_obj = datetime.strptime(selected_date, "%Y-%m-%d").date()
            bookings = bookings.filter(appointment_date__date=date_obj)
        except ValueError:
            pass  # invalid date format

    # Count by status
    status_counts = {
        'all': Booking.objects.count(),
        'pending': Booking.objects.filter(status='pending').count(),
        'confirmed': Booking.objects.filter(status='confirmed').count(),
        'completed': Booking.objects.filter(status='completed').count(),
        'cancelled': Booking.objects.filter(status='cancelled').count(),
    }

    # Pagination
    paginator = Paginator(bookings.order_by('-appointment_date'), 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "bookings": page_obj,
        "current_status": status,
        "selected_date": selected_date,
        "status_counts": status_counts,
    }
    return render(request, "dashboard.html", context)


def api_bookings(request):
    if request.method == 'GET':
        bookings = Booking.objects.all().order_by('-appointment_date')
        data = [{
            'id': b.id,
            'customer': str(b.customer),
            'service': str(b.service),
            'date': b.appointment_date.strftime('%Y-%m-%d'),
            'time': b.appointment_date.strftime('%H:%M'),
            'status': b.status,
        } for b in bookings]
        return JsonResponse({'data': data})
    
def booking_detail(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)
    customer = None
    customer_booking_count = 0
    try:
        customer = Customer.objects.get(phone_number=booking.customer_phone)
        customer_booking_count = Booking.objects.filter(
            customer_phone=customer.phone_number
        ).count()
    except Customer.DoesNotExist:
        pass
    return render(request, 'booking_detail.html', {
        'booking': booking,
        'customer': customer,
        'customer_booking_count': customer_booking_count,
        'active_page': 'admin_dashboard',
    })

def booking_form(request, phone_number, token):
    try:
        temp_booking = TempBooking.objects.get(phone_number=phone_number, token=token)
        booking_data = json.loads(temp_booking.data)
        
        # Generate business hours (9am to 5pm with 30-minute intervals)
        business_hours = []
        for hour in range(9, 18):  # 9am to 5pm
            for minute in (0, 30):
                business_hours.append(time(hour, minute))
        
        if request.method == 'POST':
            appointment_date_str = request.POST.get('appointment_date')
            appointment_time_str = request.POST.get('appointment_time')
            customer_name = request.POST.get('customer_name', '')
            notes = request.POST.get('notes', '')
            
            try:
                appointment_datetime_naive = datetime.strptime(
                    f"{appointment_date_str} {appointment_time_str}",
                    "%Y-%m-%d %H:%M"
                )
                appointment_datetime = timezone.make_aware(appointment_datetime_naive)
                # Validate date is not in the past
                if appointment_datetime < timezone.now():
                    return render(request, 'booking/form.html', {
                        'phone_number': phone_number,
                        'service_type': booking_data.get('service_type'),
                        'service_name': booking_data.get('service_name'),
                        'price': temp_booking.price,
                        'token': token,
                        'business_hours': business_hours,
                        'error': 'Please select a future date and time'
                    })
                
                # Create booking
                booking = Booking.objects.create(
                    customer_phone=phone_number,
                    customer_name=customer_name,
                    service_type=booking_data['service_type'],
                    service_name=booking_data['service_name'],
                    price=temp_booking.price,
                    appointment_date=appointment_datetime,
                    notes=notes,
                    status='confirmed'
                )
                
                # Format date/time for WhatsApp message
                formatted_date = appointment_datetime.strftime('%Y-%m-%d')
                formatted_time = appointment_datetime.strftime('%H:%M')
                
                # Get language from session
                try:
                    session = WhatsappSession.objects.get(phone_number=phone_number)
                    language = session.data.get('language', 'sw')
                except WhatsappSession.DoesNotExist:
                    language = 'sw'
                
                # Prepare confirmation message
                if language == 'en':
                    confirmation_msg = (
                        f"✅ Booking Confirmed\n\n"
                        f"Service: {booking.service_name}\n"
                        f"Date: {formatted_date}\n"
                        f"Time: {formatted_time}\n"
                        f"Price: Tsh {temp_booking.price:,}\n\n"
                        f"Please reply with PAY to complete payment"
                    )
                else:
                    confirmation_msg = (
                        f"✅ Booking Imethibitishwa\n\n"
                        f"Huduma: {booking.service_name}\n"
                        f"Tarehe: {formatted_date}\n"
                        f"Muda: {formatted_time}\n"
                        f"Bei: Tsh {temp_booking.price:,}\n\n"
                        f"Bonyeza hapa chini kukamilisha malipo:"
                    )
                
                # Send confirmation message
                if temp_booking.price and temp_booking.price > 0:
                    if language == 'en':
                        buttons = [{
                            "type": "reply",
                            "reply": {
                                "id": "pay_now",
                                "title": "💳 PAY NOW"
                            }
                        }]
                        button_text = "Payment"
                    else:
                        buttons = [{
                            "type": "reply",
                            "reply": {
                                "id": "lipa_sasa",
                                "title": "💳 LIPA SASA"
                            }
                        }]
                        button_text = "Malipo"
                    
                    send_button_message(phone_number, confirmation_msg, button_text, buttons)
                else:
                    send_text_message(phone_number, confirmation_msg)
                
                temp_booking.delete()
                return render(request, 'booking/confirmation_sent.html')
                
            except ValueError as e:
                return render(request, 'booking/form.html', {
                    'phone_number': phone_number,
                    'service_type': booking_data.get('service_type'),
                    'service_name': booking_data.get('service_name'),
                    'price': temp_booking.price,
                    'token': token,
                    'business_hours': business_hours,
                    'error': 'Invalid date/time format'
                })
        
        return render(request, 'booking/form.html', {
            'phone_number': phone_number,
            'service_type': booking_data.get('service_type'),
            'service_name': booking_data.get('service_name'),
            'price': temp_booking.price,
            'token': token,
            'business_hours': business_hours
        })
        
    except TempBooking.DoesNotExist:
        return HttpResponse("Invalid booking token", status=400)
    except json.JSONDecodeError:
        return HttpResponse("Invalid booking data", status=400)
    
    
def delete_booking(request, booking_id):
    if request.method == 'POST':
        booking = get_object_or_404(Booking, id=booking_id)
        booking.delete()
        messages.success(request, 'Booking deleted successfully!')
        return redirect('saloon_admin:admin_dashboard')
    
    # GET request shows confirmation page
    booking = get_object_or_404(Booking, id=booking_id)
    return render(request, 'confirm_delete.html', {
        'booking': booking,
        'active_page': 'admin_dashboard',
    })

def process_payment(phone_number, payment_phone, amount):
    """Process payment through Zeno USSD endpoint"""
    try:
        # Convert Decimal to float if needed
        if isinstance(amount, Decimal):
            amount = float(amount)

        if DUMMY_PAYMENT_MODE:
            transaction_id = f"DEMO-{phone_number[-4:]}-{int(timezone.now().timestamp())}"
            logger.info(f"Dummy payment initiated for {phone_number}: {transaction_id}")
            return True, transaction_id, "Dummy payment created"
            
        payload = {
            'create_order': '1',
            'buyer_email': f'{payment_phone}@saloondemo.com',
            'buyer_name': 'Saloon Demo Customer',
            'buyer_phone': payment_phone,
            'amount': str(amount),
            'account_id': ZENO_ACCOUNT_ID,
            'api_key': ZENO_API_KEY,
            'secret_key': ZENO_SECRET_KEY,
            'currency': 'TZS',
            'payment_method': 'ussd'
        }
        
        logger.info(f"Initiating payment to Zeno API with payload: {payload}")
        
        response = requests.post(
            ZENO_API_URL,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data=payload,
            timeout=10
        )
        
        logger.info(f"Zeno API response: {response.status_code} - {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'success':
                return True, data.get('order_id'), data.get('message')
        
        return False, None, get_message('hitilafu')
        
    except Exception as e:
        logger.error(f"Payment processing error: {str(e)}")
        return False, None, f"Samahani, kuna tatizo kwa sasa: {str(e)}"
    
def handle_payment_phone(phone_number, text):
    logger.info(f"Processing payment for phone: {text}")
    """Handle payment phone number input and initiate USSD payment with full bilingual support"""
    try:
        # Clean the input - remove all non-digit characters
        payment_phone = ''.join(filter(str.isdigit, text.strip()))
        
        # Get language from session
        try:
            session = WhatsappSession.objects.get(phone_number=phone_number)
            language = session.data.get('language', 'sw')
        except WhatsappSession.DoesNotExist:
            language = 'sw'
        
        # Validate Tanzanian phone number format
        if not (payment_phone.startswith('0') and len(payment_phone) == 10):
            send_text_message(phone_number, get_message('namba_sio_sahihi', language))
            return
            
        # Get session and payment details
        amount = session.data.get('payment_amount')
        
        if not amount:
            send_text_message(phone_number, get_message('hitilafu', language))
            return send_main_menu(phone_number, language)
            
        # Process payment
        success, transaction_id, gateway_message = process_payment(phone_number, payment_phone, amount)
        
        if success:
            # Store payment info in session
            session.data['payment_info'] = {
                'payment_phone': payment_phone,
                'amount': amount,
                'transaction_id': transaction_id,
                'status': 'initiated'
            }
            session.save()
            
            # Language-specific success messages
            if language == 'en':
                payment_message = (
                    f"🔍 Tracking Your Payment\n\n"
                    f"Payment Phone: {payment_phone}\n"
                    f"Amount: Tsh {amount:,}\n"
                    f"Transaction ID: {transaction_id}\n\n"
                    f"We're processing your payment. Please complete the USSD prompt on your phone."
                )
                buttons = [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "payment_confirmed",
                            "title": "✅ I've Paid"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "payment_help",
                            "title": "🆘 Payment Issue"
                        }
                    }
                ]
                button_text = "Confirm Payment"
            else:
                payment_message = (
                    f"🔍 Tunafuatilia Malipo Yako\n\n"
                    f"Namba ya Malipo: {payment_phone}\n"
                    f"Kiasi: Tsh {amount:,}\n"
                    f"Kitambulisho: {transaction_id}\n\n"
                    f"Tunashughulikia malipo yako. Tafadhali kamilisha USSD kwenye simu yako."
                )
                buttons = [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "payment_confirmed",
                            "title": "✅ Nimeshalipa"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "payment_help",
                            "title": "🆘 Shida ya malipo"
                        }
                    }
                ]
                button_text = "Thibitisha Malipo"
            
            send_button_message(phone_number, payment_message, button_text, buttons)
            
            # Update booking record
            booking = Booking.objects.filter(customer_phone=phone_number).order_by('-created_at').first()
            if booking:
                booking.payment_reference = transaction_id
                booking.payment_phone = payment_phone
                booking.payment_status = 'initiated'
                booking.save()
        else:
            # Payment failed - use appropriate language message
            if language == 'en':
                error_message = get_message('malipo_yamesitishwa_en', 'en') or "Payment could not be initiated. Please try again."
            else:
                error_message = get_message('malipo_yamesitishwa', 'sw') or "Malipo hayawezi kuanzishwa. Tafadhali jaribu tena."
            
            send_text_message(phone_number, error_message)
            
        # Reset session to main menu
        session.step = MENU_STATES['MAIN']
        session.save()
        
    except Exception as e:
        logger.error(f"Error handling payment phone: {str(e)}")
        language = session.data.get('language', 'sw') if 'session' in locals() else 'sw'
        
        if language == 'en':
            error_msg = get_message('tatzo_la_server_en', 'en') or "A system error occurred. Please try again later."
        else:
            error_msg = get_message('tatzo_la_server', 'sw') or "Kuna tatizo la mfumo. Tafadhali jaribu tena baadae."
        
        send_text_message(phone_number, error_msg)

def process_payment(phone_number, payment_phone, amount):
    """Process payment through Zeno USSD endpoint"""
    try:
        # Convert Decimal to float if needed
        if isinstance(amount, Decimal):
            amount = float(amount)

        if DUMMY_PAYMENT_MODE:
            transaction_id = f"DEMO-{phone_number[-4:]}-{int(timezone.now().timestamp())}"
            logger.info(f"Dummy payment initiated for {phone_number}: {transaction_id}")
            return True, transaction_id, "Dummy payment created"
            
        payload = {
            'create_order': '1',
            'buyer_email': f'{payment_phone}@saloondemo.com',
            'buyer_name': 'Saloon Demo Customer',
            'buyer_phone': payment_phone,  # Send in 0757373765 format
            'amount': str(amount),
            'account_id': ZENO_ACCOUNT_ID,
            'api_key': ZENO_API_KEY,
            'secret_key': ZENO_SECRET_KEY,
            'currency': 'TZS',
            'payment_method': 'ussd'
        }
        
        logger.info(f"Initiating payment to Zeno API with payload: {payload}")
        
        response = requests.post(
            ZENO_API_URL,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data=payload,
            timeout=10
        )
        
        logger.info(f"Zeno API response: {response.status_code} - {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'success':
                return True, data.get('order_id'), data.get('message')
        
        return False, None, get_message('hitilafu')
        
    except Exception as e:
        logger.error(f"Payment processing error: {str(e)}")
        return False, None, f"Samahani, kuna tatizo kwa sasa: {str(e)}"
        
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)
    
def check_payment_status(order_id):
    """Check payment status with Zeno API"""
    try:
        if DUMMY_PAYMENT_MODE:
            logger.info(f"Dummy payment status completed for order: {order_id}")
            return {
                'status': 'success',
                'payment_status': 'COMPLETED',
                'order_id': order_id
            }

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
        return None
        
    except Exception as e:
        logger.error(f"Error checking payment status: {str(e)}")
        return None
    
    
def edit_booking(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)
    
    if request.method == 'POST':
        form = BookingForm(request.POST, instance=booking)
        if form.is_valid():
            form.save()
            messages.success(request, 'Booking updated successfully!')
            return redirect('saloon_admin:admin_dashboard')
    else:
        form = BookingForm(instance=booking)
    
    context = {
        'form': form,
        'booking': booking,
        'active_page': 'admin_dashboard',
    }
    return render(request, 'booking/edit_booking.html', context)
