# leaves/context_processors.py
from datetime import datetime, timedelta
from .models import Holiday, LeaveRequest
from users.rbac import get_user_permission_codes, menu_permission_flags, user_has_permission


def _sidebar_role_from_permissions(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return None

    if getattr(user, 'is_superuser', False):
        return 'Admin'

    role_name = getattr(getattr(user, 'role', None), 'name', None)
    if role_name in {'Admin', 'HR', 'Manager', 'TL', 'Employee'}:
        return role_name

    perms = set(get_user_permission_codes(user))

    if perms & {
        'dashboard_admin', 'role_view', 'permission_view', 'role_create', 'role_update',
        'role_delete', 'role_assign_permissions', 'permission_assign', 'user_create',
        'user_update', 'user_delete', 'user_activate', 'user_deactivate', 'user_assign_role',
        'leave_policy_create', 'leave_policy_update', 'leave_policy_delete', 'leave_balance_update',
        'settings_update',
    }:
        return 'Admin'

    if perms & {
        'dashboard_hr', 'team_manage', 'user_view', 'report_view', 'settings_view',
        'leave_approve', 'leave_reject', 'leave_view_all', 'holiday_create', 'holiday_update',
        'holiday_delete', 'leave_balance_view',
    }:
        return 'HR'

    if perms & {
        'dashboard_manager', 'team_view', 'leave_view_all', 'leave_approve', 'leave_reject',
        'leave_balance_view',
    }:
        return 'Manager'

    if perms & {
        'dashboard_manager', 'leave_apply', 'leave_view_own', 'leave_approve', 'leave_reject',
        'leave_balance_view',
    }:
        return 'TL'

    if perms & {
        'dashboard_employee', 'leave_apply', 'leave_view_own', 'leave_balance_view',
        'notification_view',
    }:
        return 'Employee'

    return None

def holiday_context(request):
    """Add holiday counts and info to all templates"""
    if not request.user.is_authenticated:
        return {}
    
    today = datetime.now().date()
    
    # Count upcoming holidays (next 60 days)
    upcoming_count = Holiday.objects.filter(
        date__gte=today,
        is_active=True
    ).count()
    
    # Check if today is a holiday
    today_holiday = Holiday.objects.filter(
        date=today,
        is_active=True
    ).first()
    
    # Get next upcoming holiday
    next_holiday = Holiday.objects.filter(
        date__gte=today,
        is_active=True
    ).order_by('date').first()
    
    return {
        'upcoming_holidays_count': upcoming_count,
        'today_is_holiday': bool(today_holiday),
        'today_holiday_name': today_holiday.name if today_holiday else None,
        'next_holiday': next_holiday,
        'next_holiday_days': (next_holiday.date - today).days if next_holiday else None,
    }

def hr_counts_context(request):
    """Add HR-specific counts to templates"""
    if not request.user.is_authenticated or not request.user.role:
        return {}
    
    context = {}
    
    # Only for HR and Admin
    if user_has_permission(request.user, 'leave_manage') or user_has_permission(request.user, 'user_manage') or request.user.role.name in ['HR', 'Admin']:
        from django.db.models import Count, Q
        from datetime import date
        
        today = date.today()
        
        # Pending leaves count
        context['pending_hr_count'] = LeaveRequest.objects.filter(
            status='PENDING',
            employee__department__hr=request.user
        ).count() if request.user.role.name == 'HR' else 0
        
        # On leave today count
        context['on_leave_today_count'] = LeaveRequest.objects.filter(
            status='APPROVED',
            start_date__lte=today,
            end_date__gte=today
        ).count()
    
    return context


def rbac_context(request):
    """Expose computed RBAC menu flags to templates."""
    if not request.user.is_authenticated:
        return {}

    return {
        'rbac_flags': menu_permission_flags(request.user),
        'sidebar_role': _sidebar_role_from_permissions(request.user),
    }