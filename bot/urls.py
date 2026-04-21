from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Your custom admin URLs with unique namespace
    path('admin/', include(('saloon_bot.urls', 'saloon_admin'), namespace='saloon_admin')),
    
    # Django's default admin
    path('django-admin/', admin.site.urls),
    
    # Webhook at root level (no namespace)
    path('', include('saloon_bot.urls')),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

if settings.DEBUG:
    from django.conf.urls.static import static
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)