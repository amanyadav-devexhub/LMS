from django.contrib import admin
from .models import LeaveRequest, LeaveBalance, Notification

from django.contrib import admin
from .models import (
    LeaveBalance,
    LeaveRequest,
    SalaryDeduction,
    Notification,
    Holiday,
)

# Safe import for new policy models
try:
    from .models import LeaveTypeConfig, LeavePolicy, EmployeeLeaveAllocation
    POLICY_MODELS = True
except ImportError:
    POLICY_MODELS = False


# ════════════════════════════════════════════════════════
#  EMPLOYEE LEAVE ALLOCATION  ← the one you edit directly
# ════════════════════════════════════════════════════════

if POLICY_MODELS:

    @admin.register(EmployeeLeaveAllocation)
    class EmployeeLeaveAllocationAdmin(admin.ModelAdmin):
        list_display  = (
            'employee', 'leave_type', 'year',
            'allocated_days', 'used_days', 'carried_forward', 'remaining_days'
        )
        list_filter   = ('year', 'leave_type', 'leave_type__is_paid')
        search_fields = (
            'employee__email', 'employee__first_name',
            'employee__last_name', 'leave_type__name'
        )
        list_editable = ('allocated_days',)   # ← edit quota inline in the list
        ordering      = ('-year', 'employee__email', 'leave_type__name')
        readonly_fields = ('remaining_days', 'used_percent', 'created_at', 'updated_at')

        fieldsets = (
            ('Employee & Type', {
                'fields': ('employee', 'leave_type', 'year')
            }),
            ('Balance', {
                'fields': ('allocated_days', 'used_days', 'carried_forward'),
                'description': (
                    'allocated_days = quota set by admin  |  '
                    'used_days = consumed by approved leaves  |  '
                    'carried_forward = rolled over from previous year'
                ),
            }),
            ('Read Only', {
                'fields': ('remaining_days', 'used_percent', 'created_at', 'updated_at'),
                'classes': ('collapse',),
            }),
        )

        def remaining_days(self, obj):
            return obj.remaining_days
        remaining_days.short_description = 'Remaining'

        def used_percent(self, obj):
            return f"{obj.used_percent}%"
        used_percent.short_description = 'Used %'


# ════════════════════════════════════════════════════════
#  LEAVE TYPE CONFIG
# ════════════════════════════════════════════════════════

    @admin.register(LeaveTypeConfig)
    class LeaveTypeConfigAdmin(admin.ModelAdmin):
        list_display  = (
            'name', 'code', 'days_per_year', 'is_paid',
            'is_accrual_based', 'carry_forward', 'is_active'
        )
        list_filter   = ('is_active', 'is_paid', 'is_accrual_based', 'carry_forward')
        search_fields = ('name', 'code')
        list_editable = ('days_per_year', 'is_active')   # ← edit quota inline
        ordering      = ('name',)
        readonly_fields = ('created_at', 'updated_at')

        fieldsets = (
            ('Identity', {
                'fields': ('name', 'code', 'description', 'color', 'is_active')
            }),
            ('Quota', {
                'fields': (
                    'days_per_year', 'is_paid',
                    'is_accrual_based', 'monthly_accrual'
                ),
                'description': (
                    'days_per_year is the DEFAULT quota used when syncing to employees. '
                    'Changing this and clicking Save does NOT auto-update existing allocations '
                    '— use "Sync All to Employees" on the Leave Policy page.'
                ),
            }),
            ('Rules', {
                'fields': (
                    'max_consecutive_days', 'advance_notice_days',
                    'document_required_after', 'carry_forward', 'carry_forward_limit'
                ),
                'classes': ('collapse',),
            }),
            ('Applicability', {
                'fields': ('applicable_to', 'applicable_roles', 'applicable_departments'),
                'classes': ('collapse',),
            }),
            ('Metadata', {
                'fields': ('created_by', 'created_at', 'updated_at'),
                'classes': ('collapse',),
            }),
        )


# ════════════════════════════════════════════════════════
#  LEAVE POLICY
# ════════════════════════════════════════════════════════

    @admin.register(LeavePolicy)
    class LeavePolicyAdmin(admin.ModelAdmin):
        list_display  = (
            'name', 'is_default', 'is_active',
            'max_days_per_request', 'min_advance_days', 'approval_threshold'
        )
        list_filter   = ('is_default', 'is_active')
        search_fields = ('name',)
        list_editable = ('is_default', 'is_active')
        readonly_fields = ('created_at', 'updated_at')


# ════════════════════════════════════════════════════════
#  LEAVE BALANCE  (old system — keep for reference)
# ════════════════════════════════════════════════════════

@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display  = (
        'employee', 'casual_leave', 'sick_leave',
        'total_accrued', 'total_paid_taken', 'available_balance',
        'monthly_accrual_rate'
    )
    search_fields = ('employee__email', 'employee__first_name', 'employee__last_name')
    list_editable = ('casual_leave', 'sick_leave', 'total_accrued', 'monthly_accrual_rate')
    readonly_fields = ('available_balance', 'last_accrual_date', 'updated_at')
    ordering = ('employee__email',)

    def available_balance(self, obj):
        return obj.available_balance
    available_balance.short_description = 'Available Balance'


# ════════════════════════════════════════════════════════
#  LEAVE REQUEST
# ════════════════════════════════════════════════════════

@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display  = (
        'employee', 'leave_type', 'duration',
        'start_date', 'end_date',
        'paid_days', 'unpaid_days',
        'approval_count', 'rejection_count', 'final_status'
    )
    list_filter   = ('final_status', 'leave_type', 'duration', 'start_date')
    search_fields = ('employee__email', 'employee__first_name', 'employee__last_name')
    readonly_fields = (
        'created_at', 'updated_at', 'balance_deducted_at',
        'tl_acted_at', 'hr_acted_at', 'manager_acted_at',
        'leave_duration_days',
    )
    ordering = ('-created_at',)

    fieldsets = (
        ('Employee & Type', {
            'fields': ('employee', 'leave_type', 'duration', 'reason', 'attachment')
        }),
        ('Dates', {
            'fields': ('start_date', 'end_date', 'short_session', 'short_hours')
        }),
        ('Status', {
            'fields': ('status', 'final_status')
        }),
        ('Paid / Unpaid', {
            'fields': ('paid_days', 'unpaid_days', 'is_fully_paid', 'balance_deducted_at'),
        }),
        ('Voting', {
            'fields': (
                'approvers',
                'tl_approved', 'tl_rejected', 'tl_voted', 'tl_acted_at',
                'hr_approved', 'hr_rejected', 'hr_voted', 'hr_acted_at',
                'manager_approved', 'manager_rejected', 'manager_voted', 'manager_acted_at',
                'approval_count', 'rejection_count',
            ),
            'classes': ('collapse',),
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


# ════════════════════════════════════════════════════════
#  SALARY DEDUCTION
# ════════════════════════════════════════════════════════

@admin.register(SalaryDeduction)
class SalaryDeductionAdmin(admin.ModelAdmin):
    list_display  = (
        'employee', 'leave_request', 'unpaid_days',
        'deduction_amount', 'deduction_month', 'is_processed'
    )
    list_filter   = ('is_processed', 'deduction_month')
    search_fields = ('employee__email', 'employee__first_name')
    list_editable = ('is_processed',)
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-deduction_month',)


# ════════════════════════════════════════════════════════
#  HOLIDAY
# ════════════════════════════════════════════════════════

@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display  = (
        'name', 'holiday_type', 'date', 'end_date',
        'is_recurring', 'is_half_day', 'is_active'
    )
    list_filter   = ('holiday_type', 'is_active', 'is_recurring', 'year')
    search_fields = ('name', 'description')
    list_editable = ('is_active',)
    ordering = ('date',)
    readonly_fields = ('year', 'display_date', 'duration', 'created_at', 'updated_at')


# ════════════════════════════════════════════════════════
#  NOTIFICATION
# ════════════════════════════════════════════════════════

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display  = ('user', 'message', 'read_status', 'created_at')
    list_filter   = ('read_status',)
    search_fields = ('user__email', 'message')
    list_editable = ('read_status',)
    ordering = ('-created_at',)


