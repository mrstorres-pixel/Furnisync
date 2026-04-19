from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.contrib.auth import logout as auth_logout
from django.shortcuts import redirect
from django.http import HttpRequest
from core import views as core_views

def logout_view(request: HttpRequest):
    """Custom logout view that handles both GET and POST"""
    auth_logout(request)
    return redirect('login')

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", core_views.login_view, name="login"),
    path("accounts/customer-signup/", core_views.customer_signup, name="customer_signup"),
    path("accounts/logout/", logout_view, name="logout"),
    path("", include("core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

