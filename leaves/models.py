from django.db import models
from django.conf import settings

User = settings.AUTH_USER_MODEL


# -----------------------
# LEAVE BALANCE
# -----------------------
class LeaveBalance(models.Model):
    employee = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="leave_balance"
    )
    casual_leave = models.FloatField(default=12)
    sick_leave = models.FloatField(default=10)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.employee} Leave Balance"


# -----------------------
# LEAVE REQUEST
# -----------------------
class LeaveRequest(models.Model):
    LEAVE_TYPE_CHOICES = (
        ("CASUAL", "Casual Leave"),
        ("SICK", "Sick Leave"),
    )

    DURATION_CHOICES = (
        ("FULL", "Full Day"),
        ("HALF", "Half Day"),
        ("SHORT", "Short Leave"),
    )

    SESSION_CHOICES = (
        ("FIRST_HALF", "First Half"),
        ("SECOND_HALF", "Second Half"),
    )

    STATUS_CHOICES = (
        ("TL_PENDING", "TL Pending"),
        ("HR_PENDING", "HR Pending"),
        ("MANAGER_PENDING", "Manager Pending"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
    )

    employee = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="leaves"
    )
    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPE_CHOICES)
    duration = models.CharField(max_length=10, choices=DURATION_CHOICES, default="FULL")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    short_session = models.CharField(max_length=20, choices=SESSION_CHOICES, null=True, blank=True)
    short_hours = models.FloatField(null=True, blank=True)
    reason = models.TextField()
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="TL_PENDING")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # For FULL or HALF day, end_date = start_date
        if self.duration in ["FULL", "HALF"]:
            self.end_date = self.start_date
            self.short_session = None
            self.short_hours = None
        # For SHORT leave, default hours = 4 if not provided
        elif self.duration == "SHORT" and not self.short_hours:
            self.short_hours = 4
        super().save(*args, **kwargs)

    @property
    def leave_duration_days(self):
        if self.duration == "FULL":
            return 1
        elif self.duration == "HALF":
            return 0.5
        elif self.duration == "SHORT":
            return self.short_hours / 8
        return 0

    def __str__(self):
        return f"{self.employee.username} - {self.leave_type} ({self.duration})"


# -----------------------
# NOTIFICATIONS
# -----------------------
class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.TextField()
    read_status = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Notification for {self.user.username}"