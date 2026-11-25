from django.contrib.auth import views as auth_views
from django.urls import path
from . import views

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="erp/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("purchases/", views.purchase_list_create, name="purchases"),
    path("production/", views.production_list, name="production_list"),
    path("production/new/", views.production_create, name="production_create"),
    path("production/<int:pk>/", views.production_detail, name="production_detail"),
]