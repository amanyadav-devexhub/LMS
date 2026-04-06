# leaves/context_processors.py
from datetime import datetime, timedelta
from .models import Holiday, LeaveRequest
from users.rbac import menu_permission_flags, user_has_permission

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
    }