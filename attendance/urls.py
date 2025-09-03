# attendance/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("dashboard/", views.dashboard, name="dashboard"),

    # Admin only
    path("upload_attendance/", views.upload_attendance, name="upload_attendance"),
    path("holidays/", views.manage_holidays, name="manage_holidays"),

    # Admin/HR
    path("defaulters/", views.defaulter_list, name="defaulter_list"),
    path("reports/", views.reports, name="reports"),
    path("reports/export/", views.reports_export_page, name="reports_export_page"),
    path("export.csv", views.export_csv, name="export_csv"),
    path("settings/", views.attendance_settings, name="attendance_settings"),
    path("attendance/clear/", views.clear_attendance, name="clear_attendance"),
    path("signup/", views.signup_view, name="signup"),

]

