"""
URL configuration for example_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView
from django.urls.base import reverse_lazy
from django.views.generic.base import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login", LoginView.as_view(), name="login"),
    path("logout", LogoutView.as_view(), name="logout"),
    path("change_password", PasswordChangeView.as_view(), name='change_password'),
    path("ticket/", include('django_fsm_ticket.urls')),
    path("home", RedirectView.as_view(url=reverse_lazy('all_tickets', args=['purchase'])), name="home"),
    path("", RedirectView.as_view(url=reverse_lazy('all_tickets', args=['purchase'])), name="root"),
]
