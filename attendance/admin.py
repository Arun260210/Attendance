
# Register your models here.


from django.contrib import admin
from .models import Attendance, Holiday, AttendanceSetting

@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("employee", "date", "check_in_time")

@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ("date", "name", "holiday_type")

@admin.register(AttendanceSetting)
class AttendanceSettingAdmin(admin.ModelAdmin):
    list_display = ("threshold_days",)

