from django.db import models
from django.conf import settings
from django.utils import timezone
from users.models import User, Department


# -----------------------
# LEAVE BALANCE
# -----------------------
class LeaveBalance(models.Model):
    employee     = models.OneToOneField(
                       settings.AUTH_USER_MODEL,
                       on_delete=models.CASCADE,
                       related_name="leave_balance"
                   )
    casual_leave = models.FloatField(default=12)
    sick_leave   = models.FloatField(default=10)
    updated_at   = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.employee} Leave Balance"


# -----------------------
# LEAVE REQUEST
# -----------------------
class LeaveRequest(models.Model):
    LEAVE_TYPE_CHOICES = (
        ("CASUAL",  "Casual Leave"),
        ("SICK",    "Sick Leave"),
        ("Casual",  "Casual Leave"),
        ("Sick",    "Sick Leave"),
        ("Urgent",  "Urgent Leave"),
        ("Married", "Married Leave"),
    )

    DURATION_CHOICES = (
        ("FULL",  "Full Day"),
        ("HALF",  "Half Day"),
        ("SHORT", "Short Leave"),
    )

    SESSION_CHOICES = (
        ("FIRST_HALF",  "First Half"),
        ("SECOND_HALF", "Second Half"),
        ("AM", "AM"),
        ("PM", "PM"),
    )

    STATUS_CHOICES = [
        ("PENDING",  "Pending"),      # ← replaces TL_PENDING / HR_PENDING / MANAGER_PENDING
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
]

    employee      = models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.CASCADE,
                        related_name="leaves"
                    )
    approvers = models.ManyToManyField(
    settings.AUTH_USER_MODEL,
    blank=True,
    related_name="leave_approvals"
)
    leave_type    = models.CharField(max_length=20, choices=LEAVE_TYPE_CHOICES)
    duration      = models.CharField(max_length=10, choices=DURATION_CHOICES, default="FULL")
    start_date    = models.DateField()
    end_date      = models.DateField(null=True, blank=True)
    short_session = models.CharField(max_length=20, choices=SESSION_CHOICES, null=True, blank=True)
    short_hours   = models.FloatField(null=True, blank=True)
    reason        = models.TextField()
    status        = models.CharField(max_length=30, choices=STATUS_CHOICES, default="TL_PENDING")
    attachment    = models.FileField(
                        upload_to='leave_attachments/',
                        null=True,
                        blank=True,
                        help_text="Medical report, marriage card, or other supporting document"
                    )
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # ── HALF: always same day ─────────────────────────────────
        if self.duration == "HALF":
            self.end_date      = self.start_date
            self.short_session = None
            self.short_hours   = None

        # ── SHORT: always same day ────────────────────────────────
        elif self.duration == "SHORT":
            self.end_date    = self.start_date
            self.short_hours = self.short_hours or 4

        # ── FULL: KEEP the end_date that was passed in ────────────
        #    Only default to start_date if end_date is truly missing
        else:
            self.short_session = None
            self.short_hours   = None
            if not self.end_date:
                self.end_date = self.start_date
            elif self.end_date < self.start_date:
                self.end_date = self.start_date

        super().save(*args, **kwargs)

    @property
    def leave_duration_days(self):
        if self.duration == "FULL":
            if self.end_date and self.end_date != self.start_date:
                return (self.end_date - self.start_date).days + 1
            return 1
        elif self.duration == "HALF":
            return 0.5
        elif self.duration == "SHORT":
            return (self.short_hours or 4) / 8
        return 0

    def __str__(self):
        return f"{self.employee.username} - {self.leave_type} ({self.duration})"


# -----------------------
# NOTIFICATIONS
# -----------------------
class Notification(models.Model):
    user        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    message     = models.TextField()
    read_status = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Notification for {self.user.username}"


# -----------------------
# HOLIDAY
# -----------------------
class Holiday(models.Model):
    HOLIDAY_TYPES = [
        ('NATIONAL',  '🇮🇳 National Holiday'),
        ('RELIGIOUS', '🕉️ Religious Holiday'),
        ('REGIONAL',  '📍 Regional Holiday'),
        ('COMPANY',   '🏢 Company Holiday'),
        ('BANK',      '🏦 Bank Holiday'),
        ('OTHER',     '🎉 Other'),
    ]

    MONTHS = [
        (1, 'January'),  (2, 'February'), (3, 'March'),    (4, 'April'),
        (5, 'May'),      (6, 'June'),     (7, 'July'),     (8, 'August'),
        (9, 'September'),(10, 'October'), (11, 'November'),(12, 'December'),
    ]

    name         = models.CharField(max_length=200)
    description  = models.TextField(blank=True, null=True)
    holiday_type = models.CharField(max_length=20, choices=HOLIDAY_TYPES, default='NATIONAL')

    # Date fields
    date     = models.DateField(help_text="Date of holiday")
    end_date = models.DateField(null=True, blank=True, help_text="For multi-day holidays")
    year     = models.IntegerField(editable=False)

    # Recurring
    is_recurring   = models.BooleanField(default=False)
    recurring_rule = models.CharField(max_length=50, blank=True, null=True)

    # Visibility
    is_active              = models.BooleanField(default=True)
    applicable_to_all      = models.BooleanField(default=True)
    applicable_departments = models.ManyToManyField(Department, blank=True)
    applicable_locations   = models.CharField(max_length=200, blank=True)

    # Half-day
    is_half_day   = models.BooleanField(default=False)
    half_day_type = models.CharField(
                        max_length=20,
                        choices=[('FIRST_HALF', 'First Half'), ('SECOND_HALF', 'Second Half')],
                        blank=True, null=True
                    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
                     settings.AUTH_USER_MODEL,
                     on_delete=models.SET_NULL,
                     null=True,
                     related_name='created_holidays'
                 )

    class Meta:
        ordering       = ['date']
        unique_together = ['name', 'date']
        indexes = [
            models.Index(fields=['date', 'is_active']),
            models.Index(fields=['year', 'holiday_type']),
        ]

    def save(self, *args, **kwargs):
        self.year = self.date.year
        if not self.end_date:
            self.end_date = self.date
        super().save(*args, **kwargs)

    @property
    def duration(self):
        if self.end_date and self.end_date != self.date:
            return (self.end_date - self.date).days + 1
        return 1

    @property
    def display_date(self):
        if self.end_date and self.end_date != self.date:
            return f"{self.date.strftime('%d %b')} - {self.end_date.strftime('%d %b, %Y')}"
        return self.date.strftime('%d %B, %Y')

    @property
    def icon(self):
        icons = {
            'NATIONAL':  'fa-flag',
            'RELIGIOUS': 'fa-star-and-crescent',
            'REGIONAL':  'fa-location-dot',
            'COMPANY':   'fa-building',
            'BANK':      'fa-building-columns',
            'OTHER':     'fa-calendar',
        }
        return icons.get(self.holiday_type, 'fa-calendar')

    @property
    def color_class(self):
        colors = {
            'NATIONAL':  'badge-info',
            'RELIGIOUS': 'badge-purple',
            'REGIONAL':  'badge-warning',
            'COMPANY':   'badge-success',
            'BANK':      'badge-secondary',
            'OTHER':     'badge-primary',
        }
        return colors.get(self.holiday_type, 'badge-info')

    def __str__(self):
        return f"{self.name} - {self.display_date}"