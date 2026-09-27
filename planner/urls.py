from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("routes/", views.explore, name="explore"),
    path("routes/<slug:slug>/", views.route_detail, name="route_detail"),
    path("routes/<slug:slug>/use/", views.route_use, name="route_use"),
    path("routes/<slug:slug>/bookmark/", views.bookmark, name="bookmark"),
    path("plan/new/", views.planner_new, name="planner_new"),
    path("my-trips/", views.trips, name="trips"),
    path("community/", views.community, name="community"),
    path("import/", views.import_trip, name="import_trip"),
    path("trips/<uuid:trip_id>/", views.trip_detail, name="trip_detail"),
    path("trips/<uuid:trip_id>/edit/", views.trip_edit, name="trip_edit"),
    path("trips/<uuid:trip_id>/delete/", views.trip_delete, name="trip_delete"),
    path("trips/<uuid:trip_id>/publish/", views.trip_publish, name="trip_publish"),
    path("trips/<uuid:trip_id>/clone/", views.trip_clone, name="trip_clone"),
    path("trips/<uuid:trip_id>/export/", views.trip_export, name="trip_export"),
    path("api/trips/", views.trip_create, name="trip_create"),
    path("api/trips/<uuid:trip_id>/save/", views.trip_save, name="trip_save"),
    path("api/places/", views.api_places, name="api_places"),
    path("api/directions/", views.api_directions, name="api_directions"),
    path("api/config/", views.api_config, name="api_config"),
    path("accounts/register/", views.register, name="register"),
    path("accounts/login/", views.login_view, name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(next_page="home"), name="logout"),
    path("accounts/password/change/", auth_views.PasswordChangeView.as_view(template_name="registration/password_change_form.html", success_url="/accounts/password/changed/"), name="password_change"),
    path("accounts/password/changed/", auth_views.PasswordChangeDoneView.as_view(template_name="registration/password_change_done.html"), name="password_change_done"),
]
