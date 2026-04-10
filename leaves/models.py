from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import date, timedelta
from users.models import User, Department

# ======================
# SALARY DEDUCTION
# ======================
class SalaryDeduction(models.Model):
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="salary_deductions"
    )
    
    leave_request = models.ForeignKey(
        'LeaveRequest',
        on_delete=models.CASCADE,
        related_name="salary_deductions"
    )
    
    unpaid_days = models.FloatField(
        help_text="Number of unpaid days for this leave"
    )
    
    deduction_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Amount to deduct from salary"
    )
    
    deduction_month = models.DateField(
        help_text="Month of deduction (first day of month)"
    )
    
    is_processed = models.BooleanField(
        default=False,
        help_text="Whether this deduction has been included in payroll"
    )
    
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this was processed in payroll"
    )
    
    notes = models.TextField(
        blank=True,
        null=True,
        help_text="Additional notes about this deduction"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-deduction_month', 'employee__email']
        indexes = [
            models.Index(fields=['employee', 'deduction_month']),
            models.Index(fields=['is_processed']),
        ]
    
    def __str__(self):
        return f"{self.employee.email} - {self.deduction_month} - ₹{self.deduction_amount}"

# -----------------------
# LEAVE REQUEST
# -----------------------
class LeaveRequest(models.Model):
    LEAVE_TYPE_CHOICES = (
        ("CASUAL",  "Casual Leave"),
        ("SICK",    "Sick Leave"),
        ("URGENT", "Urgent Leave"),
        ("HALF_DAY", "Half Day Leave"),
        ("SHORT_LEAVE", "Short Leave"),
        ("MARRIAGE", "Marriage Leave"),
        ("BEREAVEMENT", "Bereavement Leave"),
        ("MATERNITY", "Maternity Leave"),
        ("PATERNITY", "Paternity Leave"),
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
        ("PENDING",  "Pending"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
    ]

    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="leaves"
    )
    
    approvers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="leave_approvals"
    )
    
    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPE_CHOICES)
    duration = models.CharField(max_length=10, choices=DURATION_CHOICES, default="FULL")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    short_session = models.CharField(max_length=20, choices=SESSION_CHOICES, null=True, blank=True)
    short_hours = models.FloatField(null=True, blank=True)
    reason = models.TextField()
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="PENDING")
    attachment = models.FileField(
        upload_to='leave_attachments/',
        null=True,
        blank=True,
        help_text="Medical report, marriage card, or other supporting document"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ===== VOTING SYSTEM FIELDS =====
    # Individual approval flags
    tl_approved = models.BooleanField(default=False)
    hr_approved = models.BooleanField(default=False)
    manager_approved = models.BooleanField(default=False)
    
    # Individual rejection flags
    tl_rejected = models.BooleanField(default=False)
    hr_rejected = models.BooleanField(default=False)
    manager_rejected = models.BooleanField(default=False)
    
    # Track who has already voted
    tl_voted = models.BooleanField(default=False)
    hr_voted = models.BooleanField(default=False)
    manager_voted = models.BooleanField(default=False)
    


    # Vote counts
    approval_count = models.IntegerField(default=0)
    rejection_count = models.IntegerField(default=0)


    manager_already_counted = models.BooleanField(
        default=False,
        help_text="Track if manager's approval vote was counted"
    )
    manager_already_counted_reject = models.BooleanField(
        default=False,
        help_text="Track if manager's rejection vote was counted"
    )
    
    # Timestamps for auditing
    tl_acted_at = models.DateTimeField(null=True, blank=True)
    hr_acted_at = models.DateTimeField(null=True, blank=True)
    manager_acted_at = models.DateTimeField(null=True, blank=True)
    
    # Final decision
    final_status = models.CharField(
        max_length=20,
        choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected')],
        default='PENDING'
    )

    # ===== PAID/UNPAID TRACKING FIELDS =====
    paid_days = models.FloatField(
        default=0,
        help_text="Number of days covered by leave balance (paid)"
    )
    
    unpaid_days = models.FloatField(
        default=0,
        help_text="Number of days exceeding balance (unpaid, salary deducted)"
    )
    
    is_fully_paid = models.BooleanField(
        default=True,
        help_text="Whether this leave is fully covered by balance"
    )
    
    balance_deducted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When paid days were deducted from balance"
    )

    eligibility_checked = models.BooleanField(
        default=False,
        help_text="Whether special leave eligibility checks were completed"
    )

    document_verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When an attached document was verified by approver/admin"
    )

    hr_remark = models.TextField(
        blank=True,
        default="",
        help_text="Optional remark provided by HR during approval/rejection."
    )

    tl_remark = models.TextField(
        blank=True,
        default="",
        help_text="Optional remark provided by Team Lead during approval/rejection."
    )

    manager_remark = models.TextField(
        blank=True,
        default="",
        help_text="Optional remark provided by Manager during approval/rejection."
    )

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

    def calculate_paid_unpaid(self, available_balance):
        """
        Calculate how many days are paid vs unpaid based on available balance
        """
        total_days = float(self.leave_duration_days or 0)
        available_balance = max(0.0, float(available_balance or 0))
        
        if total_days <= available_balance:
            # Fully paid
            self.paid_days = total_days
            self.unpaid_days = 0
            self.is_fully_paid = True
        else:
            # Partially paid
            self.paid_days = available_balance
            self.unpaid_days = total_days - available_balance
            self.is_fully_paid = False
        
        self.paid_days = round(float(self.paid_days or 0), 2)
        self.unpaid_days = round(float(self.unpaid_days or 0), 2)
        return self.paid_days, self.unpaid_days

    def save(self, *args, **kwargs):
        # ── HALF: always same day ─────────────────────────────────
        if self.duration == "HALF":
            self.end_date = self.start_date
            self.short_session = None
            self.short_hours = None

        # ── SHORT: always same day ────────────────────────────────
        elif self.duration == "SHORT":
            self.end_date = self.start_date
            self.short_hours = self.short_hours or 4

        # ── FULL: handle end_date ─────────────────────────────────
        else:
            self.short_session = None
            self.short_hours = None
            if not self.end_date:
                self.end_date = self.start_date
            elif self.end_date < self.start_date:
                self.end_date = self.start_date

        super().save(*args, **kwargs)

    def __str__(self):
        status_display = self.final_status if self.final_status != 'PENDING' else self.status
        return f"{self.employee.username} - {self.leave_type} ({status_display})"
    
    def get_leave_type_config(self):
        """Get the LeaveTypeConfig for this leave request"""
        try:
            return LeaveTypeConfig.objects.filter(
                code__iexact=self.leave_type,
                is_active=True
            ).first()
        except Exception:
            return None
    
    def validate_against_rules(self):
        """
        Validate this leave request against LeaveTypeConfig rules.
        Returns (is_valid, error_message)
        """
        config = self.get_leave_type_config()
        if not config:
            return True, None
        
        today = timezone.now().date()
        
        # Rule 1: Advance notice check
        if config.advance_notice_days > 0:
            min_allowed_date = today + timedelta(days=config.advance_notice_days)
            if self.start_date < min_allowed_date:
                return False, f"This leave type requires {config.advance_notice_days} days advance notice. Earliest start date is {min_allowed_date}."
        
        # Rule 2: Max consecutive days check
        total_days = self.leave_duration_days
        if config.max_consecutive_days > 0 and total_days > config.max_consecutive_days:
            return False, f"This leave type allows maximum {config.max_consecutive_days} consecutive days. You requested {total_days} days."
        
        # Rule 3: Document required check
        if config.document_required_after > 0 and total_days > config.document_required_after:
            if not self.attachment:
                return False, f"This leave type requires a supporting document for leaves longer than {config.document_required_after} days."

        if config.requires_document and not self.attachment:
            return False, "This leave type requires a supporting document."
        
        return True, None


# -----------------------
# NOTIFICATIONS
# -----------------------
class Notification(models.Model):
    user        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    message     = models.TextField()
    link        = models.CharField(max_length=500, blank=True, null=True, help_text="URL to redirect when clicked")
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





from django.db import models
from django.conf import settings



# ======================
# LEAVE TYPE CONFIG
# Admin creates/edits these. Each is one type of leave.
# ======================
class LeaveTypeConfig(models.Model):

    APPLICABLE_CHOICES = [
        ('ALL',         'All Employees'),
        ('ROLES',       'Specific Roles'),
        ('DEPARTMENTS', 'Specific Departments'),
    ]

    QUOTA_TYPE_CHOICES = [
        ('STANDARD', 'Standard'),
        ('ANNUAL_POOL', 'Annual Pool'),
        ('SPECIAL_EVENT', 'Special Event'),
        ('MATERNITY_PATERNITY', 'Maternity/Paternity'),
    ]

    GENDER_CHOICES = [
        ('ALL', 'All'),
        ('MALE', 'Male'),
        ('FEMALE', 'Female'),
    ]

    # Identity
    name        = models.CharField(max_length=100)          # "Casual Leave"
    code        = models.CharField(max_length=30, unique=True)  # "CASUAL"
    description = models.TextField(blank=True)
    color       = models.CharField(max_length=7, default='#00c6d4',
                    help_text="Hex color used in UI badges")

    # Quota
    days_per_year     = models.FloatField(default=12,
                            help_text="Total days allowed per calendar year")
    is_accrual_based  = models.BooleanField(default=False,
                            help_text="True = accrues monthly | False = full quota on Jan 1")
    monthly_accrual   = models.FloatField(default=1.0,
                            help_text="Days earned per month (used only if is_accrual_based=True)")

    # Pay type
    is_paid = models.BooleanField(default=True,
                help_text="False = unpaid leave, salary will be deducted")
    
    starting_month = models.IntegerField(default=4,
                            help_text="Month when leave year starts (1=Jan, 4=Apr, etc.)")

    quota_type = models.CharField(
        max_length=30,
        choices=QUOTA_TYPE_CHOICES,
        default='STANDARD',
        help_text="How this leave type consumes quota"
    )

    max_lifetime_usage = models.FloatField(
        default=0,
        help_text="Lifetime cap for special leaves. 0 = no lifetime cap"
    )

    usage_resets_yearly = models.BooleanField(
        default=True,
        help_text="Whether usage resets every leave year"
    )

    applicable_gender = models.CharField(
        max_length=10,
        choices=GENDER_CHOICES,
        default='ALL'
    )

    min_service_days = models.PositiveIntegerField(
        default=0,
        help_text="Minimum completed service days required"
    )

    requires_document = models.BooleanField(
        default=False,
        help_text="Whether a supporting document is mandatory"
    )

    # Rules
    max_consecutive_days    = models.IntegerField(default=0,
                                help_text="Max days in a single request. 0 = no limit")
    advance_notice_days     = models.IntegerField(default=0,
                                help_text="Employee must apply N days in advance. 0 = same day allowed")
    document_required_after = models.IntegerField(default=0,
                                help_text="Require document if leave exceeds N days. 0 = never required")

    # Carry forward
    carry_forward       = models.BooleanField(default=False)
    carry_forward_limit = models.FloatField(default=0,
                            help_text="Max days to carry to next year. 0 = carry all remaining")

    # Applicability
    applicable_to          = models.CharField(max_length=20,
                                choices=APPLICABLE_CHOICES, default='ALL')
    applicable_roles       = models.ManyToManyField('users.Role',     blank=True)
    applicable_departments = models.ManyToManyField(Department, blank=True)

    # Meta
    is_active  = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                    on_delete=models.SET_NULL, null=True,
                    related_name='created_leave_types')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name        = "Leave Type Config"
        verbose_name_plural = "Leave Type Configs"

    def __str__(self):
        paid_label = "Paid" if self.is_paid else "Unpaid"
        return f"{self.name} — {self.days_per_year} days/yr ({paid_label})"
    
    def get_current_leave_year(self, as_of_date=None):
        """Get the current leave year for this leave type."""
        if as_of_date is None:
            as_of_date = timezone.now().date()
        start_month = self.starting_month
        if as_of_date.month >= start_month:
            return as_of_date.year
        else:
            return as_of_date.year - 1
    
    def get_leave_year_range(self, as_of_date=None):
        """Get (start_date, end_date) for the leave year."""
        if as_of_date is None:
            as_of_date = timezone.now().date()
        year = self.get_current_leave_year(as_of_date)
        start_date = date(year, self.starting_month, 1)

        if self.starting_month == 1:
            end_date = date(year, 12, 31)
        else:
            end_date = date(year + 1, self.starting_month, 1) - timedelta(days=1)
        
        return start_date, end_date
    
    def get_months_elapsed_in_leave_year(self, as_of_date=None):
        """Get how many months have passed in the current leave year."""
        if as_of_date is None:
            as_of_date = timezone.now().date()
        year = self.get_current_leave_year(as_of_date)
        start_date = date(year, self.starting_month, 1)

        months_elapsed = (as_of_date.year - start_date.year) * 12 + (as_of_date.month - start_date.month) + 1
        return max(1, min(12, months_elapsed))


# ======================
# LEAVE POLICY
# High-level rules that govern HOW leave is applied company-wide
# ======================
class LeavePolicy(models.Model):

    name        = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    # Global rules
    max_days_per_request      = models.IntegerField(default=5,
                                    help_text="Max days an employee can request at once")
    min_advance_days          = models.IntegerField(default=1,
                                    help_text="Must apply at least N days before leave starts")
    weekend_counts_as_leave   = models.BooleanField(default=False,
                                    help_text="If True, Sat/Sun between leave days are counted")
    holiday_counts_as_leave   = models.BooleanField(default=False,
                                    help_text="If True, public holidays in leave range are counted")
    allow_half_day            = models.BooleanField(default=True)
    allow_short_leave         = models.BooleanField(default=True)
    approval_threshold        = models.IntegerField(default=2,
                                    help_text="Votes needed to approve a leave (your voting system uses 2)")

    # Scope
    is_default              = models.BooleanField(default=False,
                                help_text="Only one policy can be default. All employees use this unless overridden.")
    applicable_departments  = models.ManyToManyField(Department, blank=True,
                                help_text="Leave blank if this policy applies to all")

    # Meta
    is_active  = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                    on_delete=models.SET_NULL, null=True,
                    related_name='created_leave_policies')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering        = ['-is_default', 'name']
        verbose_name    = "Leave Policy"
        verbose_name_plural = "Leave Policies"

    def save(self, *args, **kwargs):
        # Only one policy can be default at a time
        if self.is_default:
            LeavePolicy.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name}{' ✓ Default' if self.is_default else ''}"


# ======================
# EMPLOYEE LEAVE ALLOCATION
# Per employee × per leave type × per year.
# This is what drives the balance on every dashboard.
# When admin changes LeaveTypeConfig and clicks "Apply to All",
# this table gets updated for all employees automatically.
# ======================
class EmployeeLeaveAllocation(models.Model):

    employee   = models.ForeignKey(settings.AUTH_USER_MODEL,
                    on_delete=models.CASCADE,
                    related_name='leave_allocations')
    leave_type = models.ForeignKey(LeaveTypeConfig,
                    on_delete=models.CASCADE,
                    related_name='allocations')
    year       = models.IntegerField()

    allocated_days   = models.FloatField(default=0,
                        help_text="Days given to this employee for this leave type this year")
    used_days        = models.FloatField(default=0,
                        help_text="Days actually consumed (approved leaves)")
    carried_forward  = models.FloatField(default=0,
                        help_text="Days carried over from previous year")

    lifetime_used = models.FloatField(
        default=0,
        help_text="Total lifetime usage for capped/special leaves"
    )

    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this leave type was last consumed"
    )

    event_date = models.DateField(
        null=True,
        blank=True,
        help_text="Relevant event date for document-backed leave (e.g. marriage, birth)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['employee', 'leave_type', 'year']
        ordering = ['-year', 'leave_type__name']
        indexes = [
            models.Index(fields=['employee', 'year']),
            models.Index(fields=['leave_type', 'year']),
        ]

    @property
    def remaining_days(self):
        """Live remaining balance for this leave type"""
        return self.get_available_paid_balance()

    def get_monthly_accrual_rate(self):
        if self.leave_type.quota_type == 'ANNUAL_POOL':
            settings_obj = LeaveSettings.get_solo()
            annual_quota = float(getattr(settings_obj, 'annual_leave_quota', 12) or 12)
            return round(annual_quota / 12.0, 4)
        if self.leave_type.is_accrual_based:
            return float(self.leave_type.monthly_accrual or 0)
        return 0.0

    def get_accrued_days(self, as_of_date=None):
        as_of_date = as_of_date or timezone.now().date()

        if self.leave_type.quota_type == 'ANNUAL_POOL':
            settings_obj = LeaveSettings.get_solo()
            annual_quota = float(getattr(settings_obj, 'annual_leave_quota', 12) or 12)
            months_elapsed = self.leave_type.get_months_elapsed_in_leave_year(as_of_date)
            return round(min(annual_quota, (annual_quota / 12.0) * months_elapsed), 2)

        if self.leave_type.is_accrual_based:
            months_elapsed = self.leave_type.get_months_elapsed_in_leave_year(as_of_date)
            accrued = float(self.leave_type.monthly_accrual or 0) * months_elapsed
            return round(min(float(self.leave_type.days_per_year or 0), accrued), 2)

        return round(float(self.allocated_days or 0), 2)

    def get_annual_pool_usage(self):
        if self.leave_type.quota_type != 'ANNUAL_POOL':
            return float(self.used_days or 0)

        return float(
            EmployeeLeaveAllocation.objects.filter(
                employee=self.employee,
                year=self.year,
                leave_type__quota_type='ANNUAL_POOL',
            ).aggregate(total=models.Sum('used_days'))['total'] or 0
        )

    def get_available_paid_balance(self, as_of_date=None):
        if self.leave_type.quota_type == 'ANNUAL_POOL':
            accrued = self.get_accrued_days(as_of_date=as_of_date)
            used = self.get_annual_pool_usage()
            return max(0.0, round(accrued - used, 2))
        return max(0.0, round(float(self.allocated_days or 0) + float(self.carried_forward or 0) - float(self.used_days or 0), 2))

    @staticmethod
    def remaining_prorated_months(joining_date, period_end):
        """
        Count remaining months in the period using the business rule:
        - include joining month only when joining day <= 15
        - otherwise start from next month
        """
        if not joining_date or not period_end or joining_date > period_end:
            return 0

        start_year = joining_date.year
        start_month = joining_date.month

        if joining_date.day > 15:
            start_month += 1
            if start_month > 12:
                start_month = 1
                start_year += 1

        start_index = (start_year * 12) + start_month
        end_index = (period_end.year * 12) + period_end.month
        if start_index > end_index:
            return 0
        return (end_index - start_index) + 1

    @property
    def used_percent(self):
        total = self.allocated_days + self.carried_forward
        if total <= 0:
            return 0
        return min(100, round((self.used_days / total) * 100))

    def __str__(self):
        return (
            f"{self.employee.email} | {self.leave_type.name} | "
            f"{self.year} | {self.remaining_days} remaining"
        )


class LeaveSettings(models.Model):
    MONTH_CHOICES = [
        (1, "January"),
        (2, "February"),
        (3, "March"),
        (4, "April"),
        (5, "May"),
        (6, "June"),
        (7, "July"),
        (8, "August"),
        (9, "September"),
        (10, "October"),
        (11, "November"),
        (12, "December"),
    ]

    leave_year_start_month = models.IntegerField(choices=MONTH_CHOICES, default=4)

    default_casual_quota = models.FloatField(default=12)
    default_sick_quota = models.FloatField(default=8)
    default_annual_quota = models.FloatField(default=18)
    annual_leave_quota = models.FloatField(default=12)
    show_only_monthly_in_balance = models.BooleanField(
        default=True,
        help_text="If enabled, employee balance widgets show only monthly-accrual leave types"
    )

    working_hours_per_day = models.DecimalField(max_digits=4, decimal_places=2, default=8)
    grace_period_minutes = models.PositiveIntegerField(default=10)
    auto_deduction_enabled = models.BooleanField(default=False)
    auto_deduction_after_minutes = models.PositiveIntegerField(default=30)

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_settings",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Leave Settings"
        verbose_name_plural = "Leave Settings"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Settings (Leave year starts: {self.get_leave_year_start_month_display()})"


class LeaveAllocationLedger(models.Model):
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='leave_allocation_ledger_entries'
    )
    leave_type = models.ForeignKey(
        LeaveTypeConfig,
        on_delete=models.CASCADE,
        related_name='allocation_ledger_entries'
    )
    allocation = models.ForeignKey(
        EmployeeLeaveAllocation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ledger_entries'
    )

    year = models.IntegerField()
    annual_quota = models.FloatField(default=0)
    allocated_quota = models.FloatField(default=0)
    used_leaves = models.FloatField(default=0)
    remaining_leaves = models.FloatField(default=0)

    action = models.CharField(max_length=50, default='PRORATED_ALLOCATION')
    note = models.TextField(default='Pro-rated allocation generated on employee onboarding')
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['employee', 'year']),
            models.Index(fields=['leave_type', 'year']),
        ]

    def __str__(self):
        return f"{self.employee.email} | {self.leave_type.code} | {self.action}"