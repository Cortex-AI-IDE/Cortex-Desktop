"""adminpanel/urls.py — Admin Panel URL routing."""

from django.urls import path

from . import views

app_name = "adminpanel"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    # Subscriptions
    path("subscriptions/", views.subscription_list, name="subscriptions"),
    path("subscriptions/<int:sub_id>/", views.subscription_detail, name="subscription-detail"),
    # Users
    path("users/", views.user_list, name="users"),
    # Releases
    path("releases/", views.release_list, name="releases"),
    # Download tracking
    path("downloads/", views.download_list, name="downloads"),
    path("releases/create/", views.release_create, name="release-create"),
    path("releases/<int:release_id>/toggle/", views.release_toggle, name="release-toggle"),
]
