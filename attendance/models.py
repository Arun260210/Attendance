from django.db import models
from django.contrib.auth.models import User


class Attendance(models.Model):
    STATUS_CHOICES = (
        ('P', 'Present'),
        ('A', 'Absent'),
    )

    # User can be null so uploads are saved even if they haven't signed up yet.
    employee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,      # keep attendance if a user is deleted
        null=True,
        blank=True,
        related_name="attendance_records",
    )

    # Canonical identity for a row (always try to fill this).
    # Kept nullable to make migration easy; we'll backfill and can tighten later.
    employee_email = models.EmailField(db_index=True, null=True, blank=True)

    date = models.DateField()
    check_in_time = models.TimeField(null=True, blank=True)

    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default='A')

    class Meta:
        # One row per (email, date). Null emails won't collide.
        constraints = [
            models.UniqueConstraint(
                fields=["employee_email", "date"],
                name="uniq_attendance_email_date",
            )
        ]

    def save(self, *args, **kwargs):
        # With no check-out, Present if we have a check-in, else Absent
        self.status = 'P' if self.check_in_time else 'A'
        # If user is set but email is empty, fill from user.email
        if not self.employee_email and self.employee and self.employee.email:
            self.employee_email = (self.employee.email or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        who = self.employee.username if self.employee else (self.employee_email or "unknown")
        return f"{who} - {self.date}"


class Holiday(models.Model):
    HOLIDAY_TYPES = (
        ('PUBLIC', 'Public Holiday'),
        ('RESTRICTED', 'Restricted Holiday'),
    )
    date = models.DateField(unique=True)
    name = models.CharField(max_length=100)
    holiday_type = models.CharField(max_length=20, choices=HOLIDAY_TYPES)

    def __str__(self):
        return f"{self.name} ({self.date})"


class AttendanceSetting(models.Model):
    threshold_days = models.PositiveIntegerField(default=12)  # Admin-defined present-day threshold

    def __str__(self):
        return f"Threshold: {self.threshold_days} days"
