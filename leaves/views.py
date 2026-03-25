# ═══════════════════════════════════════════════════════════════════
#  leaves/views.py  —  COMPLETE UPDATED VERSION
#  Changes vs original:
#   ★ get_employee_leave_summary() — single helper, reads EmployeeLeaveAllocation
#   ★ employee_dashboard()  — uses new balance system
#   ★ apply_leave()         — validates against EmployeeLeaveAllocation
#   ★ approve_leave()       — deducts from EmployeeLeaveAllocation on approval
#   ★ tl_dashboard()        — team balance from admin-configured leave types
#   ★ hr_dashboard()        — own balance from admin-configured leave types
#   ★ manager_dashboard()   — team balance from admin-configured leave types
#   All other views unchanged
# ═══════════════════════════════════════════════════════════════════

# ── Standard library ────────────────────────────────────────────────
from datetime import date, datetime, timedelta
from calendar import month_name
import calendar
from users.views import _build_profile_context

# ── Django ───────────────────────────────────────────────────────────
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, Count, Sum, Case, When, Value, FloatField
from django.core.paginator import Paginator
from django.utils import timezone
from django.urls import reverse

# ── DRF ──────────────────────────────────────────────────────────────
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

# ── Leaves app models ────────────────────────────────────────────────
from .models import LeaveRequest, LeaveBalance, Notification, SalaryDeduction
from .pagination import EmployeePagination

# ── Users app models ─────────────────────────────────────────────────
from users.models import User, Department, Role

# ── Optional extras ──────────────────────────────────────────────────
try:
    from .pagination import EmployeePagination
    from users.serializers import HREmployeeSerializer
except ImportError:
    pass

# ── Optional Holiday model + decorator ───────────────────────────────
try:
    from .models import Holiday
    from .decorators import role_required
    HOLIDAYS_ENABLED = True
except ImportError:
    HOLIDAYS_ENABLED = False

# ── New policy models (safe import — won't crash if migration not run yet) ──
try:
    from .models import LeaveTypeConfig, LeavePolicy, EmployeeLeaveAllocation
    POLICY_ENABLED = True
except ImportError:
    POLICY_ENABLED = False


# ════════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════════

def get_user_role(user):
    """Returns role name exactly as stored in DB (e.g. 'HR', 'TL', 'Manager')."""
    return user.role.name if getattr(user, "role", None) else ""


def calculate_leave_days(leave):
    if leave.duration == "SHORT":
        return (leave.short_hours or 0) / 8
    return (leave.end_date - leave.start_date).days + 1


def send_notification(users, message, link=None):
    for u in users:
        Notification.objects.create(user=u, message=message, link=link)


# ★ NEW HELPER — reads EmployeeLeaveAllocation (admin-configured)
def get_employee_leave_summary(employee, year=None):
    """
    Returns a complete leave balance summary for an employee
    driven by admin-configured LeaveTypeConfig.
    Falls back to old LeaveBalance fields if migration not yet run.
    """
    if year is None:
        year = timezone.now().year

    if not POLICY_ENABLED:
        # Fallback to old system
        try:
            bal = LeaveBalance.objects.get(employee=employee)
            breakdown = [
                {'name': 'Casual Leave', 'code': 'CASUAL', 'color': '#00c6d4',
                 'is_paid': True, 'allocated': bal.casual_leave, 'used': 0,
                 'carried_forward': 0, 'remaining': bal.casual_leave, 'used_percent': 0},
                {'name': 'Sick Leave',   'code': 'SICK',   'color': '#f5a623',
                 'is_paid': True, 'allocated': bal.sick_leave, 'used': 0,
                 'carried_forward': 0, 'remaining': bal.sick_leave, 'used_percent': 0},
            ]
            return {'breakdown': breakdown,
                    'total_allocated': bal.casual_leave + bal.sick_leave,
                    'total_used': 0,
                    'total_remaining': bal.casual_leave + bal.sick_leave,
                    'year': year, 'has_allocations': True}
        except Exception:
            return {'breakdown': [], 'total_allocated': 0,
                    'total_used': 0, 'total_remaining': 0,
                    'year': year, 'has_allocations': False}

    allocations = EmployeeLeaveAllocation.objects.filter(
        employee=employee, year=year
    ).select_related('leave_type').order_by('leave_type__name')

    breakdown = []
    total_allocated = total_used = total_remaining = 0

    for alloc in allocations:
        remaining = alloc.remaining_days
        breakdown.append({
            'leave_type':      alloc.leave_type,
            'name':            alloc.leave_type.name,
            'code':            alloc.leave_type.code,
            'color':           alloc.leave_type.color,
            'is_paid':         alloc.leave_type.is_paid,
            'allocated':       alloc.allocated_days,
            'used':            alloc.used_days,
            'carried_forward': alloc.carried_forward,
            'remaining':       remaining,
            'used_percent':    alloc.used_percent,
        })
        total_allocated += alloc.allocated_days
        total_used      += alloc.used_days
        total_remaining += remaining

    # Fallback if admin hasn't synced yet
    if not breakdown:
        try:
            bal = LeaveBalance.objects.get(employee=employee)
            breakdown = [
                {'name': 'Casual Leave', 'code': 'CASUAL', 'color': '#00c6d4',
                 'is_paid': True, 'allocated': bal.casual_leave, 'used': 0,
                 'carried_forward': 0, 'remaining': bal.casual_leave, 'used_percent': 0},
                {'name': 'Sick Leave',   'code': 'SICK',   'color': '#f5a623',
                 'is_paid': True, 'allocated': bal.sick_leave, 'used': 0,
                 'carried_forward': 0, 'remaining': bal.sick_leave, 'used_percent': 0},
            ]
            total_remaining = bal.casual_leave + bal.sick_leave
            total_allocated = total_remaining
        except Exception:
            pass

    return {
        'breakdown':       breakdown,
        'total_allocated': round(total_allocated, 1),
        'total_used':      round(total_used, 1),
        'total_remaining': round(total_remaining, 1),
        'year':            year,
        'has_allocations': len(breakdown) > 0,
    }


def _get_available_balance_for_leave_type(employee, leave_type_code, year=None):
    """
    Returns remaining days for a specific leave type code for an employee.
    Used by apply_leave to check if balance exists.
    """
    if year is None:
        year = timezone.now().year

    if POLICY_ENABLED:
        try:
            alloc = EmployeeLeaveAllocation.objects.get(
                employee=employee,
                leave_type__code=leave_type_code,
                year=year
            )
            return alloc.remaining_days, alloc
        except EmployeeLeaveAllocation.DoesNotExist:
            pass

    # Fallback to old LeaveBalance
    try:
        bal = LeaveBalance.objects.get(employee=employee)
        return bal.available_balance, None
    except LeaveBalance.DoesNotExist:
        return 0, None


def _hr_base_context(request):
    """Shared context injected into every HR page."""
    today         = date.today()
    current_year  = timezone.now().year
    current_month = timezone.now().month

    pending_hr_count = LeaveRequest.objects.filter(
        status="PENDING"
    ).exclude(employee=request.user).count()

    unread_count = Notification.objects.filter(
        user=request.user, read_status=False
    ).count()

    on_leave_today_count = LeaveRequest.objects.filter(
        status="APPROVED",
        start_date__lte=today,
        end_date__gte=today
    ).count()

    new_joiners_count = User.objects.exclude(is_superuser=True).filter(
        date_joined__year=current_year,
        date_joined__month=current_month
    ).count()

    return {
        "pending_hr_count":     pending_hr_count,
        "unread_count":         unread_count,
        "on_leave_today_count": on_leave_today_count,
        "new_joiners_count":    new_joiners_count,
    }


# ════════════════════════════════════════════════════════════════════
#  ★ EMPLOYEE DASHBOARD — updated to use new balance system
# ════════════════════════════════════════════════════════════════════

@login_required
def employee_dashboard(request):
    from django.template.loader import render_to_string

    today         = date.today()
    current_year  = today.year
    current_month = today.month
    tab           = request.GET.get('tab', 'overview')
    current_month = today.month

    all_leaves = LeaveRequest.objects.filter(employee=request.user).order_by('-created_at')

    # ★ NEW: get balance from EmployeeLeaveAllocation (admin-configured)
    leave_summary = get_employee_leave_summary(request.user, current_year)

    # Keep old LeaveBalance for accrual fields (backward compat)
    balance, _ = LeaveBalance.objects.get_or_create(employee=request.user)

    # Use new system's total remaining as the primary balance
    available_balance = leave_summary['total_remaining']
    total_accrued     = leave_summary['total_allocated']
    total_taken       = leave_summary['total_used']

    # Monthly summary (approved leaves this month)
    monthly_leaves = LeaveRequest.objects.filter(
        employee=request.user,
        final_status='APPROVED',
        start_date__year=current_year,
        start_date__month=current_month
    )
    monthly_paid   = monthly_leaves.aggregate(total=Sum('paid_days'))['total']   or 0
    monthly_unpaid = monthly_leaves.aggregate(total=Sum('unpaid_days'))['total'] or 0

    # Salary deductions
    month_start = date(current_year, current_month, 1)
    monthly_deductions = SalaryDeduction.objects.filter(
        employee=request.user, deduction_month=month_start
    )
    total_deduction_this_month = monthly_deductions.aggregate(
        total=Sum('deduction_amount'))['total'] or 0
    total_deduction_all_time = SalaryDeduction.objects.filter(
        employee=request.user
    ).aggregate(total=Sum('deduction_amount'))['total'] or 0

    next_month_balance = available_balance + balance.monthly_accrual_rate

    unread = Notification.objects.filter(
        user=request.user, read_status=False).count()

    paginator   = Paginator(all_leaves, 5)
    page_obj    = paginator.get_page(request.GET.get('page', 1))
    pending_leaves = all_leaves.filter(final_status="PENDING").count()

    # ★ Active leave types for the apply form dropdown
    active_leave_types = []
    if POLICY_ENABLED:
        active_leave_types = LeaveTypeConfig.objects.filter(
            is_active=True).order_by('name')

    context = {
        "tab":             tab,
        "leaves":          page_obj,
        "all_leaves_count":all_leaves.count(),
        "page_obj":        page_obj,

        # ★ NEW balance variables
        "leave_summary":      leave_summary,
        "leave_breakdown":    leave_summary['breakdown'],
        "active_leave_types": active_leave_types,
        "total_remaining":    leave_summary['total_remaining'],
        "total_allocated":    leave_summary['total_allocated'],
        "total_used_new":     leave_summary['total_used'],

        # Keep old names so existing template tags still work
        "balance":            balance,
        "available_balance":  available_balance,
        "leave_balance":      available_balance,
        "total_accrued":      total_accrued,
        "total_taken":        total_taken,

        "pending_leaves":     pending_leaves,
        "total_leaves":       all_leaves.count(),
        "unread_count":       unread,
        "designation":        getattr(request.user, 'designation', None) or '',
        "role_name":          get_user_role(request.user),
        "profile":            _build_profile_context(request.user),

        "monthly_paid":       round(monthly_paid, 1),
        "monthly_unpaid":     round(monthly_unpaid, 1),
        "total_deduction_this_month": total_deduction_this_month,
        "total_deduction_all_time":   total_deduction_all_time,
        "next_month_balance": round(next_month_balance, 1),
    }

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        from django.template.loader import render_to_string
        html = render_to_string('leave_table.html', context, request=request)
        return JsonResponse({'html': html, 'success': True})

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        html = render_to_string('partials/employee_leave_history.html', context)
        return JsonResponse({'html': html, 'success': True})

    return render(request, "employee_dashboard.html", context)


# Add this after your employee_dashboard function

@login_required
def employee_leave_balance(request):
    """Employee leave balance page with stats and upcoming holidays"""
    today = date.today()
    current_year = today.year
    current_month = today.month

    # Get balance from EmployeeLeaveAllocation
    leave_summary = get_employee_leave_summary(request.user, current_year)

    # Keep old LeaveBalance for accrual fields (backward compat)
    balance, _ = LeaveBalance.objects.get_or_create(employee=request.user)

    # Use new system's total remaining as the primary balance
    available_balance = leave_summary['total_remaining']
    total_accrued = leave_summary['total_allocated']
    total_taken = leave_summary['total_used']

    # Monthly summary (approved leaves this month)
    monthly_leaves = LeaveRequest.objects.filter(
        employee=request.user,
        final_status='APPROVED',
        start_date__year=current_year,
        start_date__month=current_month
    )
    monthly_paid = monthly_leaves.aggregate(total=Sum('paid_days'))['total'] or 0
    monthly_unpaid = monthly_leaves.aggregate(total=Sum('unpaid_days'))['total'] or 0

    # Salary deductions
    month_start = date(current_year, current_month, 1)
    monthly_deductions = SalaryDeduction.objects.filter(
        employee=request.user, deduction_month=month_start
    )
    total_deduction_this_month = monthly_deductions.aggregate(
        total=Sum('deduction_amount'))['total'] or 0
    total_deduction_all_time = SalaryDeduction.objects.filter(
        employee=request.user
    ).aggregate(total=Sum('deduction_amount'))['total'] or 0

    next_month_balance = available_balance + balance.monthly_accrual_rate

    unread = Notification.objects.filter(
        user=request.user, read_status=False).count()

    pending_leaves = LeaveRequest.objects.filter(
        employee=request.user, final_status="PENDING").count()

    # Active leave types for the apply form dropdown
    active_leave_types = []
    if POLICY_ENABLED:
        active_leave_types = LeaveTypeConfig.objects.filter(
            is_active=True).order_by('name')

    # Get upcoming holidays (from admin configured holidays)
    upcoming_holidays = []
    if HOLIDAYS_ENABLED:
        from .models import Holiday
        upcoming_holidays = Holiday.objects.filter(
            date__gte=today,
            is_active=True
        ).order_by('date')[:10]

    context = {
        # Balance variables
        "leave_summary": leave_summary,
        "leave_breakdown": leave_summary['breakdown'],
        "active_leave_types": active_leave_types,
        "total_remaining": leave_summary['total_remaining'],
        "total_allocated": leave_summary['total_allocated'],
        "total_used_new": leave_summary['total_used'],

        # Keep old names so existing template tags still work
        "balance": balance,
        "available_balance": available_balance,
        "leave_balance": available_balance,
        "total_accrued": total_accrued,
        "total_taken": total_taken,

        "pending_leaves": pending_leaves,
        "unread_count": unread,
        "designation": getattr(request.user, 'designation', None) or '',
        "role_name": get_user_role(request.user),
        "profile": _build_profile_context(request.user),

        "monthly_paid": round(monthly_paid, 1),
        "monthly_unpaid": round(monthly_unpaid, 1),
        "total_deduction_this_month": total_deduction_this_month,
        "total_deduction_all_time": total_deduction_all_time,
        "next_month_balance": round(next_month_balance, 1),
        
        # Upcoming holidays
        "upcoming_holidays": upcoming_holidays,
    }
    return render(request, "employee_leave_balance.html", context)


# ════════════════════════════════════════════════════════════════════
#  ★ APPLY LEAVE — updated to check EmployeeLeaveAllocation balance
# ════════════════════════════════════════════════════════════════════

@login_required
def apply_leave(request):
    if request.method == "POST":
        leave_type     = request.POST.get("leave_type")
        duration       = request.POST.get("duration")
        start_date_str = request.POST.get("start_date", "").strip()
        end_date_str   = request.POST.get("end_date", "").strip()
        reason         = request.POST.get("reason", "").strip()
        short_session  = request.POST.get("short_session")
        short_hours    = request.POST.get("short_hours")
        attachment     = request.FILES.get("attachment")

        # ── Parse start_date ────────────────────────────────
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            error_msg = "Invalid start date. Please select a valid date."
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.headers.get('Accept') == 'application/json':
                return JsonResponse({"success": False, "error": error_msg})
            messages.error(request, error_msg)
            return redirect("apply_leave")

        today = date.today()
        if start_date < today:
            messages.error(request, "Start date cannot be in the past.")
            return redirect("apply_leave")

        if not leave_type:
            error_msg = "Please select a leave type."
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.headers.get('Accept') == 'application/json':
                return JsonResponse({"success": False, "error": error_msg})
            messages.error(request, error_msg)
            return redirect("apply_leave")

        # ── Set end_date based on duration ─────────────────
        if duration in ("HALF", "SHORT"):
            end_date = start_date
            if duration == "SHORT":
                short_session = short_session or "AM"
                short_hours   = int(short_hours or 4)
            else:
                short_session = None
                short_hours   = None
        else:
            short_session = None
            short_hours   = None
            if end_date_str:
                try:
                    end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    error_msg = "Invalid end date. Please select a valid date."
                    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.headers.get('Accept') == 'application/json':
                        return JsonResponse({"success": False, "error": error_msg})
                    messages.error(request, error_msg)
                    return redirect("apply_leave")
            else:
                end_date = start_date
            if end_date < start_date:
                end_date = start_date

        # ── Validate duration ────────────────────────────────
        total_days = (end_date - start_date).days + 1 if duration == "FULL" else 1

        # ★ Check against policy max_days_per_request if policy exists
        max_days = 5
        if POLICY_ENABLED:
            try:
                policy = LeavePolicy.objects.filter(
                    is_default=True, is_active=True
                ).first()
                if policy:
                    max_days = policy.max_days_per_request
            except Exception:
                pass

        if duration == "FULL" and total_days > max_days:
            messages.error(
                request,
                f"Maximum {max_days} days allowed per application. You selected {total_days} days."
            )
            return redirect("apply_leave")

        if attachment and attachment.size > 5 * 1024 * 1024:
            messages.error(request, "Attachment exceeds 5 MB. Please upload a smaller file.")
            return redirect("apply_leave")

        # ★ FIXED: Get available balance from EmployeeLeaveAllocation
        current_year = today.year
        allocation_obj = None
        available = 0

        if POLICY_ENABLED:
            # Try to find the specific leave type allocation
            try:
                # First try matching by code (new system)
                alloc = EmployeeLeaveAllocation.objects.get(
                    employee=request.user,
                    leave_type__code=leave_type.upper(),
                    year=current_year
                )
                available      = alloc.remaining_days
                allocation_obj = alloc
            except EmployeeLeaveAllocation.DoesNotExist:
                # Try name match
                try:
                    alloc = EmployeeLeaveAllocation.objects.get(
                        employee=request.user,
                        leave_type__name__iexact=leave_type,
                        year=current_year
                    )
                    available      = alloc.remaining_days
                    allocation_obj = alloc
                except EmployeeLeaveAllocation.DoesNotExist:
                    # Fallback: use total remaining across all types
                    summary   = get_employee_leave_summary(request.user, current_year)
                    available = summary['total_remaining']
        else:
            # Old system
            try:
                bal_obj   = LeaveBalance.objects.get(employee=request.user)
                available = bal_obj.available_balance
            except LeaveBalance.DoesNotExist:
                bal_obj   = LeaveBalance.objects.create(employee=request.user)
                available = 0

        # ── Create LeaveRequest (without saving) ─────────────
        leave_obj = LeaveRequest(
            employee      = request.user,
            leave_type    = leave_type,
            duration      = duration,
            start_date    = start_date,
            end_date      = end_date,
            reason        = reason,
            short_session = short_session if duration == "SHORT" else None,
            short_hours   = short_hours   if duration == "SHORT" else None,
            status        = "PENDING",
            attachment    = attachment
        )

        # ★ Calculate paid/unpaid against real balance
        leave_obj.calculate_paid_unpaid(available)

        # ── Voting setup ──────────────────────────────────────
        employee = request.user

        # TL = employee direct reporting manager
        tl = getattr(employee, 'reporting_manager', None)

        # ★ FIX: Find HR by Role name — Department has no .hr field
        hr = User.objects.filter(
            role__name='HR', is_active=True
        ).exclude(id=employee.id).first()

        # Manager = TL's reporting manager OR any active Manager
        manager = None
        if tl and getattr(tl, 'reporting_manager', None):
            manager = tl.reporting_manager
        if not manager:
            manager = User.objects.filter(
                role__name='Manager', is_active=True
            ).exclude(id=employee.id).first()

        leave_obj.tl_approved = leave_obj.hr_approved = leave_obj.manager_approved = False
        leave_obj.tl_rejected = leave_obj.hr_rejected = leave_obj.manager_rejected = False
        leave_obj.tl_voted    = leave_obj.hr_voted    = leave_obj.manager_voted    = False
        leave_obj.approval_count  = 0
        leave_obj.rejection_count = 0
        leave_obj.final_status    = 'PENDING'
        leave_obj.save()

        # Add approvers — only non-None, non-self users
        approvers_list = []
        for approver in [tl, hr, manager]:
            if approver and approver.id != employee.id:
                leave_obj.approvers.add(approver)
                approvers_list.append(approver)

        # ── Notifications ─────────────────────────────────────
        applicant_name = request.user.get_full_name() or request.user.username
        paid_unpaid_text = (
            f" ({leave_obj.paid_days} paid, {leave_obj.unpaid_days} unpaid)"
            if leave_obj.unpaid_days > 0 else ""
        )

        leave_url = reverse('leave_detail', args=[leave_obj.id])
        vote_msg = (
            f"🗳️ VOTE REQUIRED: {applicant_name} ({leave_type}, {duration}{paid_unpaid_text}, {start_date} to {end_date})"
        )
        send_notification(approvers_list, vote_msg, link=leave_url)

        Notification.objects.create(
            user=employee,
            message="Your leave request has been submitted and is pending votes.",
            link=leave_url
        )

        if leave_obj.unpaid_days > 0:
            messages.warning(
                request,
                f"⚠️ Leave submitted! {leave_obj.paid_days} days PAID, "
                f"{leave_obj.unpaid_days} days UNPAID (salary will be deducted)."
            )
        else:
            messages.success(
                request,
                f"✅ Leave submitted! {leave_obj.paid_days} days PAID. Awaiting 2 approvals."
            )

        redirect_map = {
            "Employee": "employee_dashboard",
            "TL":       "tl_dashboard",
            "HR":       "hr_dashboard",
            "Manager":  "manager_dashboard",
        }
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.headers.get('Accept') == 'application/json':
            role = get_user_role(request.user)
            redirect_url = redirect_map.get(role, "employee_dashboard")
            # Build URL with tab parameter so user sees their history
            tab_param = ""
            if role in ("TL", "Manager"):
                tab_param = "?tab=myleaves"
            return JsonResponse({
                "success": True,
                "message": "Leave application submitted successfully!",
                "redirect": reverse(redirect_url) + tab_param
            })

        return redirect(redirect_map.get(get_user_role(request.user), "employee_dashboard"))

    # ── GET: show form ────────────────────────────────────────
    current_year = date.today().year
    leave_summary = get_employee_leave_summary(request.user, current_year)

    # ★ Fetch active leave types for the dropdown
    active_leave_types = []
    if POLICY_ENABLED:
        active_leave_types = LeaveTypeConfig.objects.filter(
            is_active=True).order_by('name')

    # Keep old balance for backward compat
    try:
        balance = LeaveBalance.objects.get(employee=request.user)
    except LeaveBalance.DoesNotExist:
        balance = None

    # ★ Check active policy for rules
    active_policy = None
    if POLICY_ENABLED:
        try:
            active_policy = LeavePolicy.objects.filter(
                is_default=True, is_active=True).first()
        except Exception:
            pass

    return render(request, "apply_leave.html", {
        "balance":            balance,
        "leave_summary":      leave_summary,
        "leave_breakdown":    leave_summary['breakdown'],
        "active_leave_types": active_leave_types,
        "available_balance":  leave_summary['total_remaining'],
        "total_accrued":      leave_summary['total_allocated'],
        "total_taken":        leave_summary['total_used'],
        "active_policy":      active_policy,
        "max_days":           active_policy.max_days_per_request if active_policy else 5,
    })


# ════════════════════════════════════════════════════════════════════
#  ★ TL DASHBOARD — team balance from admin-configured leave types
# ════════════════════════════════════════════════════════════════════

@login_required
def tl_dashboard(request):
    if get_user_role(request.user) != "TL":
        return redirect("employee_dashboard")

    today        = date.today()
    current_year = timezone.now().year

    team_members = User.objects.filter(
        reporting_manager=request.user
    ).select_related("role", "department")

    all_pending = LeaveRequest.objects.filter(
        tl_voted=False,
        manager_voted=False,
        employee__reporting_manager=request.user
    ).select_related("employee").order_by("-created_at")

    leaves_page = Paginator(all_pending, 8).get_page(request.GET.get("page", 1))

    on_leave_today = LeaveRequest.objects.filter(
        status="APPROVED",
        employee__reporting_manager=request.user,
        start_date__lte=today,
        end_date__gte=today
    ).select_related("employee")

    all_team = LeaveRequest.objects.filter(
        employee__reporting_manager=request.user,
        start_date__year=current_year
    )

    history_qs   = all_team.select_related("employee").order_by("-created_at")
    history_page = Paginator(history_qs, 10).get_page(request.GET.get("hpage", 1))

    my_leaves_qs   = LeaveRequest.objects.filter(
        employee=request.user).order_by("-created_at")
    my_leaves_page = Paginator(my_leaves_qs, 10).get_page(request.GET.get("mypage", 1))

    # ★ FIXED: Per-member balance from EmployeeLeaveAllocation
    team_data = []
    for member in team_members:
        ml      = all_team.filter(employee=member)
        summary = get_employee_leave_summary(member, current_year)
        team_data.append({
            "member":          member,
            "total_leaves":    ml.count(),
            "approved":        ml.filter(status="APPROVED").count(),
            "pending":         ml.filter(status="PENDING").count(),
            "rejected":        ml.filter(status="REJECTED").count(),
            # ★ New balance fields
            "leave_summary":   summary,
            "breakdown":       summary['breakdown'],
            "total_remaining": summary['total_remaining'],
            "total_allocated": summary['total_allocated'],
            "total_used":      summary['total_used'],
            "has_allocations": summary['has_allocations'],
            "is_on_leave":     on_leave_today.filter(employee=member).exists(),
            # Backward compat — old templates using casual_balance / sick_balance still work
            "casual_balance":  next(
                (b['remaining'] for b in summary['breakdown']
                 if b['code'] in ('CASUAL', 'Casual')), 0),
            "sick_balance":    next(
                (b['remaining'] for b in summary['breakdown']
                 if b['code'] in ('SICK', 'Sick')), 0),
        })

    # ★ TL's own balance summary
    my_leave_summary = get_employee_leave_summary(request.user, current_year)

    # Active leave types for balance legend header
    active_leave_types = []
    if POLICY_ENABLED:
        active_leave_types = LeaveTypeConfig.objects.filter(
            is_active=True).order_by('name')

    unread = Notification.objects.filter(
        user=request.user, read_status=False).count()

    # AJAX handling for pagination/tab sections
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        from django.template.loader import render_to_string
        tab = request.GET.get('tab', 'pending')
        if tab == 'pending':
            html = render_to_string('partials/tl_pending_leaves.html', {"leaves": leaves_page}, request=request)
        elif tab == 'myleaves':
            html = render_to_string('partials/tl_my_leaves.html', {"my_leaves_page": my_leaves_page}, request=request)
        elif tab == 'teamhistory':
            html = render_to_string('partials/tl_team_history.html', {"history_page": history_page}, request=request)
        else:
            html = ""
        return JsonResponse({"html": html, "success": True})

    context = {
        "leaves":               leaves_page,
        "pending_count":        all_pending.count(),
        "on_leave_today":       on_leave_today,
        "on_leave_count":       on_leave_today.count(),
        "team_members":         team_members,
        "team_data":            team_data,
        "team_count":           team_members.count(),
        "approved_count":       all_team.filter(status="APPROVED").count(),
        "rejected_count":       all_team.filter(status="REJECTED").count(),
        "total_leaves_count":   all_team.count(),
        "history_page":         history_page,
        "my_leaves_page":       my_leaves_page,
        "my_leave_count":       my_leaves_qs.count(),
        # ★ TL's own new balance
        "my_leave_summary":     my_leave_summary,
        "my_breakdown":         my_leave_summary['breakdown'],
        "my_total_remaining":   my_leave_summary['total_remaining'],
        "my_total_allocated":   my_leave_summary['total_allocated'],
        # ★ Admin-configured leave types for column headers
        "active_leave_types":   active_leave_types,
        "notification_count":   unread,
        "current_year":         current_year,
        "profile":              _build_profile_context(request.user),
    }

    return render(request, "tl_dashboard.html", context)


# ════════════════════════════════════════════════════════════════════
#  ★ HR DASHBOARD — own balance from admin-configured leave types
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_dashboard(request):
    if get_user_role(request.user) != "HR":
        return redirect("employee_dashboard")

    today         = date.today()
    current_year  = timezone.now().year
    current_month = timezone.now().month

    all_emps        = User.objects.exclude(is_superuser=True)
    total_employees = all_emps.count()
    active_count    = all_emps.filter(is_active=True).count()

    pending_leaves = LeaveRequest.objects.filter(
        hr_voted=False,
        manager_voted=False
    ).exclude(employee=request.user
    ).select_related("employee", "employee__department").order_by("-created_at")

    pending_count = pending_leaves.count()

    new_joiners_count = all_emps.filter(
        date_joined__year=current_year,
        date_joined__month=current_month
    ).count()

    on_leave_today_count = LeaveRequest.objects.filter(
        status="APPROVED",
        start_date__lte=today,
        end_date__gte=today
    ).count()

    # ★ FIXED: HR's own balance from EmployeeLeaveAllocation
    my_leave_summary = get_employee_leave_summary(request.user, current_year)
    # Keep old my_balance for backward compat
    my_balance, _ = LeaveBalance.objects.get_or_create(employee=request.user)

    # ★ Active leave types
    active_leave_types = []
    if POLICY_ENABLED:
        active_leave_types = LeaveTypeConfig.objects.filter(
            is_active=True).order_by('name')

    on_leave_today_preview = LeaveRequest.objects.filter(
        status="APPROVED",
        start_date__lte=today,
        end_date__gte=today
    ).select_related("employee", "employee__department"
    ).order_by("employee__first_name")[:5]

    recent_activity = LeaveRequest.objects.select_related(
        "employee").order_by("-updated_at")[:6]

    recent_joiners = User.objects.exclude(
        is_superuser=True
    ).select_related("role").order_by("-date_joined")[:6]

    my_recent_leaves = LeaveRequest.objects.filter(
        employee=request.user).order_by("-created_at")[:4]

    context = {
        **_hr_base_context(request),
        "total_employees":        total_employees,
        "active_count":           active_count,
        "pending_count":          pending_count,
        "new_joiners_count":      new_joiners_count,
        "on_leave_today_count":   on_leave_today_count,
        "on_leave_today_preview": on_leave_today_preview,
        # ★ New balance
        "my_leave_summary":       my_leave_summary,
        "my_breakdown":           my_leave_summary['breakdown'],
        "my_total_remaining":     my_leave_summary['total_remaining'],
        "my_total_allocated":     my_leave_summary['total_allocated'],
        "active_leave_types":     active_leave_types,
        # Backward compat
        "my_balance":             my_balance,
        "recent_pending":         pending_leaves[:5],
        "recent_activity":        recent_activity,
        "recent_joiners":         recent_joiners,
        "my_recent_leaves":       my_recent_leaves,
        "profile":                _build_profile_context(request.user),
    }
    return render(request, "hr_dashboard.html", context)


# ════════════════════════════════════════════════════════════════════
#  HR — PENDING APPROVALS (unchanged)
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_pending_leaves(request):
    role = get_user_role(request.user)
    if role not in ("HR", "Admin") and not request.user.is_superuser:
        return redirect("employee_dashboard")

    leaves = LeaveRequest.objects.filter(
        hr_voted=False,
        manager_voted=False
    ).exclude(employee=request.user
    ).select_related("employee", "employee__department").order_by("-created_at")

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        from django.template.loader import render_to_string
        html = render_to_string('partials/hr_pending_leaves_table.html', {"leaves": leaves}, request=request)
        return JsonResponse({"html": html, "success": True})

    context = {
        **_hr_base_context(request),
        "leaves":        leaves,
        "pending_count": leaves.count(),
    }
    return render(request, "hr_pending_leaves.html", context)


# ════════════════════════════════════════════════════════════════════
#  HR — LEAVE ANALYTICS (unchanged)
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_leave_analytics(request):
    role = get_user_role(request.user)
    if role not in ("HR", "Admin", "Manager") and not request.user.is_superuser:
        return redirect("employee_dashboard")

    current_year = timezone.now().year

    monthly_data = []
    for month in range(1, 13):
        count = LeaveRequest.objects.filter(
            start_date__year=current_year,
            start_date__month=month
        ).count()
        monthly_data.append(count)

    casual_count = LeaveRequest.objects.filter(
        status="APPROVED", start_date__year=current_year, leave_type="CASUAL"
    ).count()
    sick_count = LeaveRequest.objects.filter(
        status="APPROVED", start_date__year=current_year, leave_type="SICK"
    ).count()

    approved_count = LeaveRequest.objects.filter(
        status="APPROVED", start_date__year=current_year).count()
    rejected_count = LeaveRequest.objects.filter(
        status="REJECTED", start_date__year=current_year).count()
    pending_total  = LeaveRequest.objects.filter(
        status="PENDING",  start_date__year=current_year).count()

    top_takers = (
        LeaveRequest.objects
        .filter(status="APPROVED", start_date__year=current_year)
        .values("employee", "employee__first_name", "employee__last_name",
                "employee__department__name")
        .annotate(total_days=Sum(
            Case(
                When(duration="FULL",  then=Value(1.0)),
                When(duration="HALF",  then=Value(0.5)),
                When(duration="SHORT", then=Value(0.25)),
                default=Value(0.0), output_field=FloatField()
            )
        ))
        .order_by("-total_days")[:8]
    )

    dept_leave_data = (
        LeaveRequest.objects
        .filter(status="APPROVED", start_date__year=current_year)
        .values("employee__department__name")
        .annotate(count=Count("id"))
        .order_by("-count")[:8]
    )

    context = {
        **_hr_base_context(request),
        "monthly_data":    monthly_data,
        "casual_count":    casual_count,
        "sick_count":      sick_count,
        "type_data":       [casual_count, sick_count],
        "approved_count":  approved_count,
        "rejected_count":  rejected_count,
        "pending_total":   pending_total,
        "top_takers":      top_takers,
        "dept_leave_data": list(dept_leave_data),
        "current_year":    current_year,
    }
    return render(request, "hr_leave_analytics.html", context)


# ════════════════════════════════════════════════════════════════════
#  HR — ON LEAVE TODAY (unchanged)
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_on_leave_today(request):
    role = get_user_role(request.user)
    if role not in ("HR", "Admin", "Manager") and not request.user.is_superuser:
        return redirect("employee_dashboard")

    today    = date.today()
    on_leave = LeaveRequest.objects.filter(
        status="APPROVED",
        start_date__lte=today,
        end_date__gte=today
    ).select_related("employee", "employee__department", "employee__role"
    ).order_by("employee__first_name")

    dept_breakdown = (
        on_leave.values("employee__department__name")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    context = {
        **_hr_base_context(request),
        "on_leave":       on_leave,
        "on_leave_count": on_leave.count(),
        "dept_breakdown": dept_breakdown,
        "today":          today,
    }
    return render(request, "hr_on_leave_today.html", context)


# ════════════════════════════════════════════════════════════════════
#  HR — NEW JOINERS (unchanged)
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_new_joiners(request):
    role = get_user_role(request.user)
    if role not in ("HR", "Admin") and not request.user.is_superuser:
        return redirect("employee_dashboard")

    today         = date.today()
    current_year  = timezone.now().year
    current_month = timezone.now().month
    filter_period = request.GET.get("period", "30")

    if filter_period == "month":
        joiners = User.objects.exclude(is_superuser=True).filter(
            date_joined__year=current_year, date_joined__month=current_month)
    elif filter_period == "year":
        joiners = User.objects.exclude(is_superuser=True).filter(
            date_joined__year=current_year)
    else:
        since   = today - timedelta(days=30)
        joiners = User.objects.exclude(is_superuser=True).filter(
            date_joined__date__gte=since)

    joiners = joiners.select_related("role", "department").order_by("-date_joined")

    context = {
        **_hr_base_context(request),
        "joiners":       joiners,
        "joiners_count": joiners.count(),
        "filter_period": filter_period,
    }
    return render(request, "hr_new_joiners.html", context)


# ════════════════════════════════════════════════════════════════════
#  HR — DEPARTMENTS OVERVIEW (unchanged)
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_departments(request):
    role = get_user_role(request.user)
    if role not in ("HR", "Admin") and not request.user.is_superuser:
        return redirect("employee_dashboard")

    today = date.today()

    departments = Department.objects.annotate(
        total_employees=Count("user"),
        active_employees=Count("user", filter=Q(user__is_active=True)),
    ).order_by("-total_employees")

    dept_data = []
    for dept in departments:
        on_leave_count = LeaveRequest.objects.filter(
            status="APPROVED",
            start_date__lte=today,
            end_date__gte=today,
            employee__department=dept
        ).count()
        dept_data.append({
            "dept":     dept,
            "total":    dept.total_employees,
            "active":   dept.active_employees,
            "on_leave": on_leave_count,
        })

    context = {
        **_hr_base_context(request),
        "dept_data":   dept_data,
        "dept_labels": [d["dept"].name for d in dept_data],
        "dept_counts": [d["total"]     for d in dept_data],
    }
    return render(request, "hr_departments.html", context)


# ════════════════════════════════════════════════════════════════════
#  HR — MY LEAVE BALANCE — ★ updated to show new balance
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_my_leave_balance(request):
    role = get_user_role(request.user)
    if role not in ("HR", "Admin") and not request.user.is_superuser:
        return redirect("employee_dashboard")

    today         = timezone.now().date()
    current_year  = today.year
    current_month = today.month
    
    balance, _   = LeaveBalance.objects.get_or_create(employee=request.user)
    my_leaves    = LeaveRequest.objects.filter(
        employee=request.user).order_by("-created_at")

    # ★ New balance from admin-configured types
    leave_summary = get_employee_leave_summary(request.user, current_year)
    
    # Monthly summary (approved leaves this month)
    monthly_leaves = LeaveRequest.objects.filter(
        employee=request.user,
        final_status='APPROVED',
        start_date__year=current_year,
        start_date__month=current_month
    )
    monthly_paid   = monthly_leaves.aggregate(total=Sum('paid_days'))['total']   or 0
    monthly_unpaid = monthly_leaves.aggregate(total=Sum('unpaid_days'))['total'] or 0

    # Salary deductions
    month_start = date(current_year, current_month, 1)
    monthly_deductions = SalaryDeduction.objects.filter(
        employee=request.user, deduction_month=month_start
    )
    total_deduction_this_month = monthly_deductions.aggregate(
        total=Sum('deduction_amount'))['total'] or 0
    total_deduction_all_time = SalaryDeduction.objects.filter(
        employee=request.user
    ).aggregate(total=Sum('deduction_amount'))['total'] or 0

    active_leave_types = []
    if POLICY_ENABLED:
        active_leave_types = LeaveTypeConfig.objects.filter(
            is_active=True).order_by('name')

    # Get upcoming holidays
    upcoming_holidays = []
    if HOLIDAYS_ENABLED:
        from .models import Holiday
        upcoming_holidays = Holiday.objects.filter(
            date__gte=today,
            is_active=True
        ).order_by('date')[:10]

    context = {
        **_hr_base_context(request),
        "balance":            balance,
        "my_leaves":          my_leaves[:10],
        "approved_count":     my_leaves.filter(status="APPROVED").count(),
        "rejected_count":     my_leaves.filter(status="REJECTED").count(),
        # Standardized names for UI reuse
        "pending_leaves":     my_leaves.filter(status="PENDING").count(),
        "leave_breakdown":    leave_summary['breakdown'],
        "total_remaining":    leave_summary['total_remaining'],
        "total_allocated":    leave_summary['total_allocated'],
        "total_used_new":     leave_summary['total_used'],
        "active_leave_types": active_leave_types,
        "monthly_paid":       round(monthly_paid, 1),
        "monthly_unpaid":     round(monthly_unpaid, 1),
        "total_deduction_this_month": total_deduction_this_month,
        "total_deduction_all_time":   total_deduction_all_time,
        "upcoming_holidays":  upcoming_holidays,
    }
    return render(request, "hr_my_leave_balance.html", context)


# ════════════════════════════════════════════════════════════════════
#  HR — EMPLOYEE LIST (unchanged)
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_employee_list(request):
    role_name = get_user_role(request.user)
    if role_name not in ("HR", "Manager", "Admin") and not request.user.is_superuser:
        messages.success(request, "Leave request submitted successfully!")
        return redirect("employee_dashboard")

    today         = date.today()
    search_query  = request.GET.get("search", "").strip()
    dept_filter   = request.GET.get("department", "").strip()
    role_filter   = request.GET.get("role", "").strip()
    status_filter = request.GET.get("status", "").strip()

    employees = (
        User.objects
        .exclude(role__name__iexact="Admin")
        .exclude(is_superuser=True)
        .select_related("role", "department")
        .order_by("-date_joined")
    )

    if search_query:
        employees = employees.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query)  |
            Q(email__icontains=search_query)       |
            Q(username__icontains=search_query)    |
            Q(department__name__icontains=search_query) |
            Q(role__name__icontains=search_query)
        )
    if dept_filter:
        employees = employees.filter(department__pk=dept_filter)
    if role_filter:
        employees = employees.filter(role__pk=role_filter)

    employee_data = []
    for emp in employees:
        on_leave = LeaveRequest.objects.filter(
            employee=emp, status="APPROVED",
            start_date__lte=today, end_date__gte=today
        ).exists()

        if status_filter == "on_leave"  and not on_leave:      continue
        if status_filter == "active"    and not emp.is_active: continue
        if status_filter == "inactive"  and emp.is_active:     continue

        employee_data.append({
            "emp":        emp,
            "on_leave":   on_leave,
            "full_name":  emp.get_full_name() or emp.username,
            "email":      emp.email,
            "role":       emp.role.name if emp.role else "—",
            "department": emp.department.name if emp.department else "—",
            "is_active":  emp.is_active,
            "date_joined":emp.date_joined,
        })

    all_emps       = User.objects.exclude(role__name__iexact="Admin").exclude(is_superuser=True)
    on_leave_count = LeaveRequest.objects.filter(
        status="APPROVED", start_date__lte=today, end_date__gte=today
    ).values("employee").distinct().count()

    # ★ Pagination
    paginator = Paginator(employee_data, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        from django.template.loader import render_to_string
        html = render_to_string('partials/hr_employee_table.html', {
            "page_obj": page_obj,
            "result_count": len(employee_data),
            "search_query": search_query
        }, request=request)
        return JsonResponse({"html": html, "success": True})

    context = {
        **_hr_base_context(request),
        "page_obj":       page_obj,
        "employee_data":  page_obj.object_list,
        "search_query":   search_query,
        "dept_filter":    dept_filter,
        "role_filter":    role_filter,
        "status_filter":  status_filter,
        "departments":    Department.objects.all().order_by("name"),
        "roles":          Role.objects.exclude(name="Admin").order_by("name"),
        "total_count":    all_emps.count(),
        "active_count":   all_emps.filter(is_active=True).count(),
        "inactive_count": all_emps.filter(is_active=False).count(),
        "on_leave_count": on_leave_count,
        "result_count":   len(employee_data),
        "viewer_role":    role_name,
    }
    return render(request, "hr_employee_list.html", context)


# ════════════════════════════════════════════════════════════════════
#  ★ MANAGER DASHBOARD — team balance from admin-configured leave types
# ════════════════════════════════════════════════════════════════════

@login_required
def manager_dashboard(request):
    if get_user_role(request.user) != "Manager":
        return redirect("employee_dashboard")

    today        = date.today()
    current_year = timezone.now().year

    team_members = User.objects.filter(
        reporting_manager=request.user
    ).select_related("role", "department")

    team_pending = LeaveRequest.objects.filter(
        manager_voted=False,
        employee__reporting_manager=request.user
    ).exclude(employee=request.user
    ).select_related("employee", "employee__department").order_by("-created_at")

    all_pending = LeaveRequest.objects.filter(
        manager_voted=False
    ).exclude(employee=request.user
    ).select_related("employee", "employee__department").order_by("-created_at")

    pending_page = Paginator(all_pending, 8).get_page(request.GET.get("page", 1))

    team_on_leave = LeaveRequest.objects.filter(
        status="APPROVED",
        employee__reporting_manager=request.user,
        start_date__lte=today,
        end_date__gte=today
    ).select_related("employee")

    team_history_qs = LeaveRequest.objects.filter(
        employee__reporting_manager=request.user,
        start_date__year=current_year
    ).select_related("employee").order_by("-created_at")

    team_history_page = Paginator(team_history_qs, 10).get_page(
        request.GET.get("hpage", 1))

    my_leaves_qs   = LeaveRequest.objects.filter(
        employee=request.user).order_by("-created_at")
    my_leaves_page = Paginator(my_leaves_qs, 10).get_page(
        request.GET.get("mypage", 1))

    # ★ FIXED: Per-member balance from EmployeeLeaveAllocation
    team_data = []
    for member in team_members:
        member_leaves = LeaveRequest.objects.filter(
            employee=member, start_date__year=current_year)
        summary = get_employee_leave_summary(member, current_year)
        team_data.append({
            "member":          member,
            "total_leaves":    member_leaves.count(),
            "approved":        member_leaves.filter(status="APPROVED").count(),
            "pending":         member_leaves.filter(status="PENDING").count(),
            "leave_summary":   summary,
            "breakdown":       summary['breakdown'],
            "total_remaining": summary['total_remaining'],
            "total_allocated": summary['total_allocated'],
            "total_used":      summary['total_used'],
            "has_allocations": summary['has_allocations'],
            "is_on_leave":     team_on_leave.filter(employee=member).exists(),
            # Backward compat
            "casual_balance":  next(
                (b['remaining'] for b in summary['breakdown']
                 if b['code'] in ('CASUAL', 'Casual')), 0),
            "sick_balance":    next(
                (b['remaining'] for b in summary['breakdown']
                 if b['code'] in ('SICK', 'Sick')), 0),
        })

    # ★ Manager's own balance
    my_leave_summary = get_employee_leave_summary(request.user, current_year)

    # ★ Active leave types
    active_leave_types = []
    if POLICY_ENABLED:
        active_leave_types = LeaveTypeConfig.objects.filter(
            is_active=True).order_by('name')

    unread = Notification.objects.filter(
        user=request.user, read_status=False).count()

    # AJAX handling for pagination/tab sections
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        from django.template.loader import render_to_string
        tab = request.GET.get('tab', 'pending')
        if tab == 'pending':
            html = render_to_string('partials/mgr_pending_leaves.html', {"pending_page": pending_page}, request=request)
        elif tab == 'history':
            # This handles both team history and my own leaves if they are in the same tab
            html = render_to_string('partials/mgr_team_history.html', {
                "team_history_page": team_history_page,
                "my_leaves_page": my_leaves_page,
                "current_year": current_year
            }, request=request)
        else:
            html = ""
        return JsonResponse({"html": html, "success": True})

    context = {
        "pending_page":        pending_page,
        "team_pending":        team_pending,
        "other_pending":       all_pending.exclude(
            employee__reporting_manager=request.user),
        "pending_count":       all_pending.count(),
        "team_members":        team_members,
        "team_data":           team_data,
        "team_count":          team_members.count(),
        "team_on_leave":       team_on_leave,
        "team_on_leave_count": team_on_leave.count(),
        "team_history_page":   team_history_page,
        "my_leaves_page":      my_leaves_page,
        # ★ New balance
        "my_leave_summary":    my_leave_summary,
        "my_breakdown":        my_leave_summary['breakdown'],
        "my_total_remaining":  my_leave_summary['total_remaining'],
        "my_total_allocated":  my_leave_summary['total_allocated'],
        "active_leave_types":  active_leave_types,
        "notification_count":  unread,
        "unread_count":        unread,
        "current_year":        current_year,
        "profile":             _build_profile_context(request.user),
    }
    return render(request, "manager_dashboard.html", context)


# ════════════════════════════════════════════════════════════════════
#  ADMIN DASHBOARD (unchanged except leave_types_count added)
# ════════════════════════════════════════════════════════════════════

@login_required
def admin_dashboard(request):
    if not request.user.is_superuser and not (
        request.user.role and request.user.role.name == "Admin"
    ):
        return redirect("employee_dashboard")

    tab          = request.GET.get("tab", "all")
    search_query = request.GET.get("search", "").strip()
    page_number  = request.GET.get("page", 1)

    qs = (
        User.objects
        .exclude(is_superuser=True)
        .select_related("role", "department")
        .order_by("-date_joined")
    )
    if tab == "active":
        qs = qs.filter(is_active=True)
    elif tab == "inactive":
        qs = qs.filter(is_active=False)
    if search_query:
        qs = qs.filter(
            Q(first_name__icontains=search_query)  |
            Q(last_name__icontains=search_query)   |
            Q(email__icontains=search_query)        |
            Q(username__icontains=search_query)     |
            Q(department__name__icontains=search_query) |
            Q(role__name__icontains=search_query)
        )

    paginator = Paginator(qs, 10)
    page_obj  = paginator.get_page(page_number)
    all_qs    = User.objects.exclude(is_superuser=True)

    current_year = timezone.now().year
    top_leave_takers = (
        LeaveRequest.objects
        .filter(status="APPROVED", start_date__year=current_year)
        .values("employee", "employee__first_name", "employee__last_name",
                "employee__department__name")
        .annotate(total_days=Sum(
            Case(
                When(duration="FULL",  then=Value(1.0)),
                When(duration="HALF",  then=Value(0.5)),
                When(duration="SHORT", then=Value(0.25)),
                default=Value(0.0), output_field=FloatField()
            )
        ))
        .order_by("-total_days")[:7]
    )

    # ★ Count active leave types for admin dashboard stat card
    leave_types_count = 0
    if POLICY_ENABLED:
        leave_types_count = LeaveTypeConfig.objects.filter(is_active=True).count()

    context = {
        "employees":         page_obj.object_list,
        "page_obj":          page_obj,
        "current_tab":       tab,
        "search_query":      search_query,
        "total_employees":   all_qs.count(),
        "active_count":      all_qs.filter(is_active=True).count(),
        "inactive_count":    all_qs.filter(is_active=False).count(),
        "pending_count":     LeaveRequest.objects.filter(status="PENDING").count(),
        "roles":             Role.objects.exclude(name="Admin"),
        "departments":       Department.objects.annotate(
            employee_count=Count("user")).order_by("name"),
        "recent_joined":     User.objects.exclude(
            is_superuser=True).select_related("role").order_by("-date_joined")[:5],
        "recent_approved":   LeaveRequest.objects.filter(
            status="APPROVED").select_related("employee").order_by("-updated_at")[:5],
        "recent_rejected":   LeaveRequest.objects.filter(
            status="REJECTED").select_related("employee").order_by("-updated_at")[:5],
        "top_leave_takers":  top_leave_takers,
        # ★ New
        "leave_types_count": leave_types_count,
        "profile":           _build_profile_context(request.user),
    }
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        employee_list = []
        for emp in page_obj.object_list:
            employee_list.append({
                "id": emp.id,
                "name": emp.get_full_name() or emp.username,
                "email": emp.email,
                "role": emp.role.name if emp.role else "—",
                "department": emp.department.name if emp.department else "—",
                "is_active": emp.is_active,
                "date_joined": emp.date_joined.strftime("%Y-%m-%d")
            })
        return JsonResponse({
            "success": True,
            "employees": employee_list,
            "has_next": page_obj.has_next(),
            "has_previous": page_obj.has_previous(),
            "number": page_obj.number,
            "num_pages": paginator.num_pages
        })

    return render(request, "admin_dashboard.html", context)


# ════════════════════════════════════════════════════════════════════
#  EMPLOYEE LIST (unchanged)
# ════════════════════════════════════════════════════════════════════

@login_required
def employee_list(request):
    if not request.user.is_superuser and not (
        request.user.role and request.user.role.name in ("Admin", "HR", "Manager")
    ):
        return redirect("employee_dashboard")

    tab          = request.GET.get("tab", "all")
    search_query = request.GET.get("search", "").strip()
    dept_filter  = request.GET.get("dept", "")
    page_number  = request.GET.get("page", 1)

    qs = (
        User.objects
        .exclude(is_superuser=True)
        .select_related("role", "department")
        .order_by("-date_joined")
    )
    if tab == "active":    qs = qs.filter(is_active=True)
    elif tab == "inactive": qs = qs.filter(is_active=False)
    if dept_filter:         qs = qs.filter(department__id=dept_filter)
    if search_query:
        qs = qs.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query)  |
            Q(email__icontains=search_query)       |
            Q(username__icontains=search_query)    |
            Q(department__name__icontains=search_query) |
            Q(role__name__icontains=search_query)
        )

    paginator = Paginator(qs, 15)
    page_obj  = paginator.get_page(page_number)
    all_qs    = User.objects.exclude(is_superuser=True)

    context = {
        "employees":       page_obj.object_list,
        "page_obj":        page_obj,
        "current_tab":     tab,
        "search_query":    search_query,
        "dept_filter":     dept_filter,
        "total_employees": all_qs.count(),
        "active_count":    all_qs.filter(is_active=True).count(),
        "inactive_count":  all_qs.filter(is_active=False).count(),
        "roles":           Role.objects.exclude(name="Admin"),
        "departments":     Department.objects.annotate(
            employee_count=Count("user")).order_by("name"),
    }
    return render(request, "employee_list.html", context)


# ════════════════════════════════════════════════════════════════════
#  LIVE SEARCH AJAX (unchanged)
# ════════════════════════════════════════════════════════════════════

@login_required
def employee_search_json(request):
    if not request.user.is_superuser and not (
        request.user.role and request.user.role.name in ("Admin", "HR", "Manager")
    ):
        return JsonResponse({"employees": []}, status=403)

    query = request.GET.get("q", "").strip()
    tab   = request.GET.get("tab", "all")

    qs = User.objects.exclude(is_superuser=True).select_related("role", "department")
    if tab == "active":    qs = qs.filter(is_active=True)
    elif tab == "inactive": qs = qs.filter(is_active=False)
    if query:
        qs = qs.filter(
            Q(first_name__icontains=query)   |
            Q(last_name__icontains=query)    |
            Q(email__icontains=query)         |
            Q(username__icontains=query)      |
            Q(department__name__icontains=query) |
            Q(role__name__icontains=query)
        )

    employees = [
        {
            "id":                 emp.pk,
            "name":               emp.get_full_name() or emp.username,
            "email":              emp.email,
            "role":               emp.role.name if emp.role else "—",
            "department":         emp.department.name if emp.department else None,
            "is_active":          emp.is_active,
            "first_name_initial": emp.first_name[:1].upper() if emp.first_name else "",
            "last_name_initial":  emp.last_name[:1].upper()  if emp.last_name  else "",
        }
        for emp in qs[:30]
    ]
    return JsonResponse({"employees": employees})


# ════════════════════════════════════════════════════════════════════
#  ★ APPROVE LEAVE — deducts from EmployeeLeaveAllocation on approval
# ════════════════════════════════════════════════════════════════════

@login_required
def approve_leave(request, leave_id):
    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER", "/"))

    leave     = get_object_or_404(LeaveRequest, id=leave_id)
    voter     = request.user
    role_name = get_user_role(voter)
    is_admin  = request.user.is_superuser or role_name == "Admin"
    is_ajax   = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.headers.get('Accept') == 'application/json'

    # ── Admin override ─────────────────────────────────
    if is_admin:
        leave.final_status       = "APPROVED"
        leave.status             = "APPROVED"
        leave.balance_deducted_at = timezone.now()
        leave.save()

        # ★ Deduct from EmployeeLeaveAllocation
        _deduct_leave_balance(leave)

        Notification.objects.create(
            user=leave.employee,
            message="Your leave request was force-approved by Admin."
        )
        if is_ajax:
            return JsonResponse({"success": True, "message": "Admin override: Leave approved.", "status": "APPROVED"})
        messages.success(request, "Admin override: Leave approved.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # ★ FIX: auto-add voter to approvers if they have the correct role
    # This handles cases where approvers weren't set correctly on apply
    if voter not in leave.approvers.all():
        if role_name in ('TL', 'HR', 'Manager') and leave.employee != voter:
            leave.approvers.add(voter)
        else:
            error_msg = "You are not an approver for this leave."
            if is_ajax:
                return JsonResponse({"success": False, "error": error_msg}, status=403)
            messages.error(request, error_msg)
            return redirect(request.META.get("HTTP_REFERER", "/"))

    if leave.employee == voter:
        error_msg = "You cannot approve your own leave request."
        if is_ajax:
            return JsonResponse({"success": False, "error": error_msg}, status=403)
        messages.error(request, error_msg)
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # ── Final status check (bypass for Manager/Admin) ──────────────
    if leave.final_status != 'PENDING' and role_name != 'Manager' and not is_admin:
        info_msg = f"This leave is already {leave.final_status}. Only Manager can override."
        if is_ajax:
            return JsonResponse({"success": False, "message": info_msg, "status": leave.final_status})
        messages.info(request, info_msg)
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # Check already voted
    already_voted = (
        (role_name == 'TL'      and leave.tl_voted)      or
        (role_name == 'HR'      and leave.hr_voted)      or
        (role_name == 'Manager' and leave.manager_voted)
    )
    # Manager can override even if they already voted or if status is not pending, 
    # but for simplicity, let's just allow them to vote if they haven't voted yet.
    # The rule says Manager is final.
    if already_voted:
        warning_msg = "You have already voted on this leave."
        if is_ajax:
            return JsonResponse({"success": False, "message": warning_msg})
        messages.warning(request, warning_msg)
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # If it's already FINAL (APPROVED/REJECTED), only Manager or Admin can vote/override
    if leave.final_status != 'PENDING' and role_name != 'Manager' and not is_admin:
        info_msg = f"This leave is already {leave.final_status}. Only Manager can override."
        if is_ajax:
            return JsonResponse({"success": False, "message": info_msg, "status": leave.final_status})
        messages.info(request, info_msg)
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # ── Record the APPROVE vote ───────────────────────────────────
    old_status = leave.final_status
    if role_name == 'TL':
        leave.tl_approved = True; leave.tl_rejected = False; leave.tl_voted = True
        leave.tl_acted_at = timezone.now()
    elif role_name == 'HR':
        leave.hr_approved = True; leave.hr_rejected = False; leave.hr_voted = True
        leave.hr_acted_at = timezone.now()
    elif role_name == 'Manager':
        leave.manager_approved = True; leave.manager_rejected = False; leave.manager_voted = True
        leave.manager_acted_at = timezone.now()
    else:
        error_msg = "You don't have voting rights."
        if is_ajax:
            return JsonResponse({"success": False, "error": error_msg}, status=403)
        messages.error(request, error_msg)
        return redirect(request.META.get("HTTP_REFERER", "/"))

    leave.approval_count += 1
    # If they previously rejected, decrement rejection count
    if (role_name == 'TL' and leave.tl_rejected) or (role_name == 'HR' and leave.hr_rejected) or (role_name == 'Manager' and leave.manager_rejected):
        leave.rejection_count = max(0, leave.rejection_count - 1)
    
    leave.save()

    # ── Evaluate decision with new priority logic ─────────────────
    decision, reason = _evaluate_leave_decision(leave)

    if decision == 'APPROVED':
        # If transitioning from NOT-APPROVED to APPROVED, deduct balance
        if old_status != 'APPROVED':
            leave.final_status        = 'APPROVED'
            leave.status              = 'APPROVED'
            leave.balance_deducted_at = timezone.now()
            leave.save()
            _deduct_leave_balance(leave)
            
            Notification.objects.create(
                user=leave.employee,
                message=f"✅ Your leave has been APPROVED. ({reason})"
            )
            messages.success(request, f"✅ Leave APPROVED! ({reason})")
        else:
            # Already approved, just notify that approval was recorded
            messages.success(request, f"✅ Approval recorded. Leave remains APPROVED.")

    elif decision == 'REJECTED':
        # If transitioning from APPROVED to REJECTED, restore balance
        if old_status == 'APPROVED':
            _restore_leave_balance(leave)
        
        leave.final_status = 'REJECTED'
        leave.status       = 'REJECTED'
        leave.save()
        
        Notification.objects.create(
            user=leave.employee,
            message=f"❌ Your leave has been REJECTED. ({reason})"
        )
        messages.warning(request, f"Leave REJECTED — {reason}")

    else:
        # Still PENDING
        leave.final_status = 'PENDING'
        leave.status       = 'PENDING'
        leave.save()
        
        waiting = []
        if not leave.hr_voted:   waiting.append("HR")
        if not leave.tl_voted:   waiting.append("TL")
        if not leave.manager_voted: waiting.append("Manager")
        wait_str = " and ".join(waiting) if waiting else "other approvers"
        messages.success(request, f"✅ Approval recorded. Still waiting for: {wait_str}")

    if is_ajax:
        return JsonResponse({"success": True, "message": f"Action recorded. Decision: {decision}", "status": leave.final_status})
    return redirect(request.META.get("HTTP_REFERER", "/"))


# ★ NEW HELPER — called when a leave is approved
def _deduct_leave_balance(leave):
    """
    Deducts paid_days from EmployeeLeaveAllocation (new system).
    Falls back to old LeaveBalance if new system not available.
    Also creates SalaryDeduction for unpaid days.
    """
    if POLICY_ENABLED and leave.paid_days > 0:
        try:
            # Try deducting from the matching leave type allocation
            alloc = EmployeeLeaveAllocation.objects.filter(
                employee=leave.employee,
                leave_type__code=leave.leave_type.upper(),
                year=leave.start_date.year
            ).first()

            if not alloc:
                # Try by name
                alloc = EmployeeLeaveAllocation.objects.filter(
                    employee=leave.employee,
                    leave_type__name__iexact=leave.leave_type,
                    year=leave.start_date.year
                ).first()

            if alloc:
                alloc.used_days = round(alloc.used_days + leave.paid_days, 2)
                alloc.save(update_fields=['used_days', 'updated_at'])
            else:
                # Fallback: deduct from old LeaveBalance
                _deduct_old_balance(leave)

        except Exception:
            _deduct_old_balance(leave)
    else:
        _deduct_old_balance(leave)

    # Create salary deduction for unpaid days
    if leave.unpaid_days > 0:
        try:
            from users.models import SalaryDetails
            salary = SalaryDetails.objects.get(user=leave.employee)
            daily_rate       = salary.salary_in_hand / 30
            deduction_amount = daily_rate * leave.unpaid_days

            SalaryDeduction.objects.create(
                employee         = leave.employee,
                leave_request    = leave,
                unpaid_days      = leave.unpaid_days,
                deduction_amount = round(deduction_amount, 2),
                deduction_month  = date.today().replace(day=1),
                notes            = f"Unpaid leave {leave.start_date} to {leave.end_date}"
            )
        except Exception:
            pass


def _deduct_old_balance(leave):
    """Fallback: deducts from old LeaveBalance model."""
    try:
        balance = LeaveBalance.objects.get(employee=leave.employee)
        if leave.paid_days > 0:
            balance.total_paid_taken = round(
                balance.total_paid_taken + leave.paid_days, 2)
            balance.save(update_fields=['total_paid_taken', 'updated_at'])
    except LeaveBalance.DoesNotExist:
        pass


def _restore_leave_balance(leave):
    """
    Restores paid_days to EmployeeLeaveAllocation if a leave status changes from APPROVED to something else.
    """
    if POLICY_ENABLED and leave.paid_days > 0 and leave.balance_deducted_at:
        try:
            alloc = EmployeeLeaveAllocation.objects.filter(
                employee=leave.employee,
                leave_type__code=leave.leave_type.upper(),
                year=leave.start_date.year
            ).first()

            if not alloc:
                alloc = EmployeeLeaveAllocation.objects.filter(
                    employee=leave.employee,
                    leave_type__name__iexact=leave.leave_type,
                    year=leave.start_date.year
                ).first()

            if alloc:
                alloc.used_days = max(0, round(alloc.used_days - leave.paid_days, 2))
                alloc.save(update_fields=['used_days', 'updated_at'])
            else:
                _restore_old_balance(leave)

        except Exception:
            _restore_old_balance(leave)

    # Handle salary deduction removal
    try:
        SalaryDeduction.objects.filter(leave_request=leave).delete()
    except Exception:
        pass
    
    leave.balance_deducted_at = None
    leave.save(update_fields=['balance_deducted_at'])


def _restore_old_balance(leave):
    """Fallback: restores to old LeaveBalance model."""
    try:
        balance = LeaveBalance.objects.get(employee=leave.employee)
        if leave.paid_days > 0:
            balance.total_paid_taken = max(0, round(balance.total_paid_taken - leave.paid_days, 2))
            balance.save(update_fields=['total_paid_taken', 'updated_at'])
    except LeaveBalance.DoesNotExist:
        pass


# ════════════════════════════════════════════════════════════════════
#  REJECT LEAVE (unchanged logic, just cleaned up)
# ════════════════════════════════════════════════════════════════════

@login_required
def reject_leave(request, leave_id):
    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER", "/"))

    leave     = get_object_or_404(LeaveRequest, id=leave_id)
    voter     = request.user
    role_name = get_user_role(voter)
    is_admin  = request.user.is_superuser or role_name == "Admin"

    is_ajax   = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.headers.get('Accept') == 'application/json'

    if is_admin:
        leave.status       = "REJECTED"
        leave.final_status = "REJECTED"
        leave.save()
        Notification.objects.create(
            user=leave.employee,
            message="Your leave request was force-rejected by Admin."
        )
        if is_ajax:
            return JsonResponse({"success": True, "message": "Admin override: Leave rejected.", "status": "REJECTED"})
        messages.warning(request, "Admin override: Leave rejected.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # ★ FIX: auto-add voter if they have correct role
    if voter not in leave.approvers.all():
        if role_name in ('TL', 'HR', 'Manager') and leave.employee != voter:
            leave.approvers.add(voter)
        else:
            error_msg = "You are not an approver for this leave."
            if is_ajax:
                return JsonResponse({"success": False, "error": error_msg}, status=403)
            messages.error(request, error_msg)
            return redirect(request.META.get("HTTP_REFERER", "/"))

    if leave.employee == voter:
        if is_ajax:
            return JsonResponse({"success": False, "error": "You cannot reject your own leave request."})
        messages.error(request, "You cannot reject your own leave request.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # ── Final status check (bypass for Manager/Admin) ──────────────
    if leave.final_status != 'PENDING' and role_name != 'Manager' and not is_admin:
        info_msg = f"This leave is already {leave.final_status}. Only Manager can override."
        if is_ajax:
            return JsonResponse({"success": False, "message": info_msg, "status": leave.final_status})
        messages.info(request, info_msg)
        return redirect(request.META.get("HTTP_REFERER", "/"))

    already_voted = (
        (role_name == 'TL'      and leave.tl_voted)      or
        (role_name == 'HR'      and leave.hr_voted)      or
        (role_name == 'Manager' and leave.manager_voted)
    )
    if already_voted:
        warning_msg = "You have already voted on this leave."
        if is_ajax:
            return JsonResponse({"success": False, "message": warning_msg})
        messages.warning(request, warning_msg)
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # Record the REJECT vote
    old_status = leave.final_status
    if role_name == 'TL':
        leave.tl_rejected = True; leave.tl_approved = False; leave.tl_voted = True
        leave.tl_acted_at = timezone.now()
    elif role_name == 'HR':
        leave.hr_rejected = True; leave.hr_approved = False; leave.hr_voted = True
        leave.hr_acted_at = timezone.now()
    elif role_name == 'Manager':
        leave.manager_rejected = True; leave.manager_approved = False; leave.manager_voted = True
        leave.manager_acted_at = timezone.now()
    else:
        # Admin or restricted
        error_msg = "You don't have voting rights."
        if is_ajax: return JsonResponse({"success": False, "error": error_msg}, status=403)
        messages.error(request, error_msg)
        return redirect(request.META.get("HTTP_REFERER", "/"))

    leave.rejection_count += 1
    # If they previously approved, decrement approval count
    if (role_name == 'TL' and leave.tl_approved) or (role_name == 'HR' and leave.hr_approved) or (role_name == 'Manager' and leave.manager_approved):
        leave.approval_count = max(0, leave.approval_count - 1)
        
    leave.save()

    # ── Evaluate decision ─────────────────────────────────────────
    decision, reason = _evaluate_leave_decision(leave)

    if decision == 'REJECTED':
        # If transitioning from APPROVED to REJECTED, restore balance
        if old_status == 'APPROVED':
            _restore_leave_balance(leave)
            
        leave.final_status = 'REJECTED'
        leave.status       = 'REJECTED'
        leave.save()

        Notification.objects.create(
            user=leave.employee,
            message=f"❌ Your leave has been REJECTED. ({reason})"
        )
        messages.warning(request, f"❌ Leave REJECTED — {reason}")

    elif decision == 'APPROVED':
        # If transitioning from NOT-APPROVED to APPROVED, deduct balance
        if old_status != 'APPROVED':
            leave.final_status = 'APPROVED'
            leave.status       = 'APPROVED'
            leave.balance_deducted_at = timezone.now()
            leave.save()
            _deduct_leave_balance(leave)
            messages.success(request, f"Leave APPROVED — {reason}")
        else:
            messages.success(request, "Rejection recorded, but leave remains APPROVED (Manager override)")

    else:
        # Still PENDING
        if old_status == 'APPROVED':
            _restore_leave_balance(leave)
            
        leave.final_status = 'PENDING'
        leave.status       = 'PENDING'
        leave.save()
        
        waiting = []
        if not leave.hr_voted:      waiting.append("HR")
        if not leave.tl_voted:      waiting.append("TL")
        if not leave.manager_voted: waiting.append("Manager")
        wait_str = " and ".join(waiting) if waiting else "other approvers"
        messages.warning(request, f"❌ Rejection recorded. Still waiting for: {wait_str}")

    if is_ajax:
        return JsonResponse({"success": True, "message": f"Action recorded. Decision: {decision}", "status": leave.final_status})
    return redirect(request.META.get("HTTP_REFERER", "/"))




# ════════════════════════════════════════════════════════════════════
#  VOTING DECISION ENGINE
#  Priority:
#    1. Manager voted → FINAL (overrides HR + TL completely)
#    2. Manager not voted → need BOTH HR AND TL to approve
#    3. Manager not voted + EITHER HR or TL rejects → REJECTED
# ════════════════════════════════════════════════════════════════════
def _evaluate_leave_decision(leave):
    """
    Returns ('APPROVED'|'REJECTED'|'PENDING', reason_string)
    
    VOTING DECISION ENGINE:
    1. Manager's vote is FINAL (overrides HR + TL completely)
       - Manager approves → APPROVED immediately
       - Manager rejects → REJECTED immediately
    
    2. If Manager hasn't acted:
       - EITHER HR or TL rejects → REJECTED immediately
       - BOTH HR AND TL must approve → APPROVED
       - Otherwise → PENDING (waiting for more votes)
    """
    
    # ── Rule 1: Manager's vote is FINAL ─────────────────────────────
    if leave.manager_voted:
        if leave.manager_approved:
            return 'APPROVED', 'Manager approved (Final decision)'
        else:
            return 'REJECTED', 'Manager rejected (Final decision)'
    
    # ── Rule 2: Manager hasn't voted ────────────────────────────────
    # Check for any rejection first
    if leave.hr_rejected or leave.tl_rejected:
        return 'REJECTED', f"{'HR' if leave.hr_rejected else 'TL'} rejected the request"
    
    # Check for both approvals
    if leave.hr_approved and leave.tl_approved:
        return 'APPROVED', 'Both HR and TL approved'
    
    # Otherwise, it's still pending
    waiting_for = []
    if not leave.hr_voted: waiting_for.append('HR')
    if not leave.tl_voted: waiting_for.append('TL')
    
    return 'PENDING', f"Waiting for {', '.join(waiting_for)} approval"

# ════════════════════════════════════════════════════════════════════
#  NOTIFICATIONS (unchanged)
# ════════════════════════════════════════════════════════════════════

@login_required
def notifications(request):
    notes = Notification.objects.filter(
        user=request.user).order_by("-created_at")
    notes.filter(read_status=False).update(read_status=True)
    return render(request, "notification.html", {"notifications": notes})


# ════════════════════════════════════════════════════════════════════
#  EMPLOYEE DETAIL / CREATE / TOGGLE STATUS (unchanged)
# ════════════════════════════════════════════════════════════════════

@login_required
def employee_detail(request, pk):
    employee = get_object_or_404(User, pk=pk)
    return render(request, "employee_detail.html", {"employee": employee})


@login_required
def create_employee(request):
    if request.method == "POST" and (
        request.user.is_superuser or get_user_role(request.user) in ("HR", "Admin")
    ):
        username = request.POST.get("username")
        email    = request.POST.get("email")
        password = request.POST.get("password")

        if User.objects.filter(username=username).exists():
            error_msg = "Username already exists."
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({"success": False, "error": error_msg}, status=400)
            messages.error(request, error_msg)
            return redirect("admin_dashboard" if request.user.is_superuser else "hr_dashboard")

        dept_id       = request.POST.get("department_id")
        manager_email = request.POST.get("reporting_manager_email")
        role_id       = request.POST.get("role_id")

        manager_user = None
        if manager_email:
            manager_user = User.objects.filter(email=manager_email).first()

        dept_obj = None
        if dept_id:
            try: dept_obj = Department.objects.get(id=dept_id)
            except Department.DoesNotExist: pass

        try:
            employee_role = (
                Role.objects.get(id=role_id) if role_id
                else Role.objects.get(name="Employee")
            )
        except Role.DoesNotExist:
            employee_role = None

        new_emp = User.objects.create_user(
            username          = username,
            email             = email,
            password          = password,
            first_name        = request.POST.get("first_name", ""),
            last_name         = request.POST.get("last_name", ""),
            role              = employee_role,
            reporting_manager = manager_user,
            department        = dept_obj,
        )

        # ★ Auto-create EmployeeLeaveAllocation for new employee
        if POLICY_ENABLED:
            from .models import LeaveTypeConfig, EmployeeLeaveAllocation
            current_year = timezone.now().year
            for lt in LeaveTypeConfig.objects.filter(is_active=True):
                EmployeeLeaveAllocation.objects.get_or_create(
                    employee=new_emp, leave_type=lt, year=current_year,
                    defaults={'allocated_days': lt.days_per_year}
                )

        messages.success(request, "Employee created successfully.")

        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if is_ajax:
            return JsonResponse({
                "success": True, 
                "message": "Employee created successfully.",
                "employee": {
                    "id": new_emp.id,
                    "name": new_emp.get_full_name() or new_emp.username,
                    "email": new_emp.email,
                    "role": new_emp.role.name if new_emp.role else "—",
                    "department": new_emp.department.name if new_emp.department else None,
                    "is_active": new_emp.is_active
                }
            })

    return redirect("admin_dashboard" if request.user.is_superuser else "hr_dashboard")


@login_required
def toggle_employee_status(request, user_id):
    employee = get_object_or_404(User, id=user_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    
    if request.method == "POST" and (
        request.user.is_superuser or get_user_role(request.user) == "Admin"
    ):
        employee.is_active = not employee.is_active
        employee.save()
        if is_ajax:
            return JsonResponse({
                "success": True, 
                "message": f"Status updated for {employee.get_full_name() or employee.username}",
                "is_active": employee.is_active
            })
            
    if is_ajax:
        return JsonResponse({"success": False, "error": "Invalid request"}, status=400)
    return redirect(request.META.get("HTTP_REFERER", "/"))


# ════════════════════════════════════════════════════════════════════
#  HOLIDAY VIEWS (unchanged)
# ════════════════════════════════════════════════════════════════════

@login_required
def holiday_list(request):
    if not HOLIDAYS_ENABLED:
        messages.error(request, "Holiday module not available.")
        return redirect("hr_dashboard")

    year         = int(request.GET.get("year", datetime.now().year))
    month        = request.GET.get("month", "")
    holiday_type = request.GET.get("type", "")
    search       = request.GET.get("search", "")

    holidays = Holiday.objects.filter(date__year=year)
    if month and month.isdigit():
        holidays = holidays.filter(date__month=int(month))
    if holiday_type:
        holidays = holidays.filter(holiday_type=holiday_type)
    if search:
        holidays = holidays.filter(
            Q(name__icontains=search) | Q(description__icontains=search))

    today    = datetime.now().date()
    upcoming = Holiday.objects.filter(
        date__gte=today, is_active=True).order_by("date")[:5]

    calendar_data = []
    for m in range(1, 13):
        mh = holidays.filter(date__month=m)
        if mh.exists() or str(m) == month:
            calendar_data.append({
                "month":      m,
                "month_name": month_name[m],
                "holidays":   mh,
                "count":      mh.count(),
            })

    context = {
        **_hr_base_context(request),
        "holidays":       holidays,
        "calendar_data":  calendar_data,
        "upcoming":       upcoming,
        "years":          Holiday.objects.dates("date", "year", order="DESC"),
        "current_year":   year,
        "current_month":  month,
        "current_type":   holiday_type,
        "search_query":   search,
        "holiday_types":  Holiday.HOLIDAY_TYPES,
        "total_holidays": holidays.count(),
        "total_days":     sum(h.duration for h in holidays),
        "months":         [(i, month_name[i]) for i in range(1, 13)],
    }
    return render(request, "holiday_list.html", context)


@login_required
def holiday_create(request):
    if not HOLIDAYS_ENABLED:
        return redirect("hr_dashboard")

    if request.method == "POST":
        name         = request.POST.get("name")
        date_str     = request.POST.get("date")
        end_date_str = request.POST.get("end_date") or date_str
        holiday_type = request.POST.get("holiday_type")
        is_half_day  = request.POST.get("is_half_day") == "on"

        # ★ FIX: parse strings → date objects before hitting model.save()
        try:
            parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            messages.error(request, "Invalid date format. Please select a valid date.")
            return redirect("holiday_create")

        try:
            parsed_end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            parsed_end_date = parsed_date

        if Holiday.objects.filter(name=name, date=parsed_date).exists():
            messages.error(request, f"Holiday '{name}' already exists on {date_str}.")
            return redirect("holiday_create")

        Holiday.objects.create(
            name             = name,
            description      = request.POST.get("description", ""),
            holiday_type     = holiday_type,
            date             = parsed_date,
            end_date         = parsed_end_date,
            is_recurring     = request.POST.get("is_recurring") == "on",
            is_half_day      = is_half_day,
            half_day_type    = request.POST.get("half_day_type") if is_half_day else None,
            applicable_to_all= request.POST.get("applicable_to_all") == "on",
            created_by       = request.user,
        )
        messages.success(request, f"Holiday '{name}' created successfully!")
        
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if is_ajax:
            return JsonResponse({"success": True, "message": f"Holiday '{name}' created successfully."})
            
        if "save_and_add" in request.POST:
            return redirect("holiday_create")
        return redirect("holiday_list")

    context = {
        **_hr_base_context(request),
        "holiday_types": Holiday.HOLIDAY_TYPES,
        "today":         datetime.now().date(),
        "current_year":  datetime.now().year,
    }
    return render(request, "holiday_form.html", context)


@login_required
def holiday_edit(request, holiday_id):
    if not HOLIDAYS_ENABLED:
        return redirect("hr_dashboard")

    holiday = get_object_or_404(Holiday, id=holiday_id)

    if request.method == "POST":
        # ★ FIX: parse date strings → date objects before model.save()
        date_str     = request.POST.get("date")
        end_date_str = request.POST.get("end_date") or date_str
        try:
            parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            messages.error(request, "Invalid date format.")
            return redirect("holiday_edit", holiday_id=holiday_id)
        try:
            parsed_end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            parsed_end_date = parsed_date

        holiday.name               = request.POST.get("name")
        holiday.description        = request.POST.get("description", "")
        holiday.holiday_type       = request.POST.get("holiday_type")
        holiday.date               = parsed_date
        holiday.end_date           = parsed_end_date
        holiday.is_recurring       = request.POST.get("is_recurring") == "on"
        holiday.is_half_day        = request.POST.get("is_half_day") == "on"
        holiday.half_day_type      = request.POST.get("half_day_type") if holiday.is_half_day else None
        holiday.applicable_to_all  = request.POST.get("applicable_to_all") == "on"
        holiday.is_active          = request.POST.get("is_active") == "on"
        holiday.save()
        messages.success(request, f"Holiday '{holiday.name}' updated successfully!")
        
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if is_ajax:
            return JsonResponse({"success": True, "message": f"Holiday '{holiday.name}' updated successfully."})
            
        return redirect("holiday_list")

    context = {
        **_hr_base_context(request),
        "holiday":       holiday,
        "holiday_types": Holiday.HOLIDAY_TYPES,
    }
    return render(request, "holiday_form.html", context)


@login_required
def holiday_delete(request, holiday_id):
    if not HOLIDAYS_ENABLED:
        return redirect("hr_dashboard")
    holiday = get_object_or_404(Holiday, id=holiday_id)
    if request.method == "POST":
        name = holiday.name
        holiday.delete()
        messages.success(request, f"Holiday '{name}' deleted successfully!")
        
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if is_ajax:
            return JsonResponse({"success": True, "message": f"Holiday '{name}' deleted."})
            
    return redirect("holiday_list")


@login_required
def holiday_bulk_create(request):
    if not HOLIDAYS_ENABLED:
        return redirect("hr_dashboard")

    current_year = datetime.now().year

    if request.method == "POST":
        year          = int(request.POST.get("year", current_year))
        holidays_text = request.POST.get("holidays_text", "")
        created = skipped = 0
        errors  = []

        for line in holidays_text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) >= 2:
                name         = parts[0].strip()
                date_str     = parts[1].strip()
                holiday_type = parts[2].strip() if len(parts) > 2 else "NATIONAL"
                try:
                    parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    if not Holiday.objects.filter(name=name, date=parsed_date).exists():
                        Holiday.objects.create(
                            name=name, holiday_type=holiday_type,
                            date=parsed_date, created_by=request.user,
                            is_recurring=True
                        )
                        created += 1
                    else:
                        skipped += 1
                except Exception as e:
                    errors.append(f"Error: {line} — {e}")

        for err in errors[:5]:
            messages.error(request, err)
        messages.success(
            request, f"Created {created} holidays. Skipped {skipped} duplicates.")
        return redirect("holiday_list")

    common_holidays = f"""# Indian Holidays {current_year}
Republic Day|{current_year}-01-26|NATIONAL
Holi|{current_year}-03-08|RELIGIOUS
Independence Day|{current_year}-08-15|NATIONAL
Gandhi Jayanti|{current_year}-10-02|NATIONAL
Diwali|{current_year}-11-01|RELIGIOUS
Christmas|{current_year}-12-25|RELIGIOUS"""

    context = {
        **_hr_base_context(request),
        "current_year":    current_year,
        "common_holidays": common_holidays,
        "years":           range(current_year - 1, current_year + 3),
    }
    return render(request, "holiday_bulk_form.html", context)


@login_required
def holiday_toggle_status(request, holiday_id):
    if not HOLIDAYS_ENABLED:
        return redirect("hr_dashboard")
    holiday = get_object_or_404(Holiday, id=holiday_id)
    holiday.is_active = not holiday.is_active
    holiday.save()
    status_word = "activated" if holiday.is_active else "deactivated"
    messages.success(request, f"Holiday '{holiday.name}' {status_word}.")
    
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if is_ajax:
        return JsonResponse({
            "success": True, 
            "message": f"Holiday '{holiday.name}' {status_word}.",
            "is_active": holiday.is_active
        })
        
    return redirect("holiday_list")


@login_required
def public_holidays(request):
    if not HOLIDAYS_ENABLED:
        return redirect("employee_dashboard")

    year     = int(request.GET.get("year", datetime.now().year))
    holidays = Holiday.objects.filter(
        is_active=True, date__year=year).order_by("date")
    today    = datetime.now().date()
    upcoming = Holiday.objects.filter(
        date__gte=today, is_active=True).order_by("date")[:10]

    calendar_data = []
    for m in range(1, 13):
        mh  = holidays.filter(date__month=m)
        cal = calendar.monthcalendar(year, m)
        weeks = []
        for week in cal:
            week_days = []
            for day in week:
                if day != 0:
                    d  = datetime(year, m, day).date()
                    dh = mh.filter(date=d)
                    week_days.append({
                        "date":       d,
                        "day":        day,
                        "holidays":   dh,
                        "is_holiday": dh.exists(),
                    })
                else:
                    week_days.append({"day": 0, "is_holiday": False})
            weeks.append(week_days)
        if mh.exists():
            calendar_data.append({
                "month":      m,
                "month_name": month_name[m],
                "weeks":      weeks,
                "holidays":   mh,
                "count":      mh.count(),
            })

    context = {
        "calendar_data":  calendar_data,
        "upcoming":       upcoming,
        "year":           year,
        "prev_year":      year - 1,
        "next_year":      year + 1,
        "total_holidays": holidays.count(),
        "total_days":     sum(h.duration for h in holidays),
        "type_stats":     holidays.values("holiday_type").annotate(
            count=Count("id")).order_by("-count"),
        "months":         [(i, month_name[i]) for i in range(1, 13)],
        "holiday_types":  dict(Holiday.HOLIDAY_TYPES) if HOLIDAYS_ENABLED else {},
    }
    return render(request, "public_holidays.html", context)


@login_required
def check_today_holiday(request):
    if not HOLIDAYS_ENABLED:
        return JsonResponse({"is_holiday": False})
    today   = date.today()
    holiday = Holiday.objects.filter(date=today, is_active=True).first()
    return JsonResponse({
        "is_holiday":    bool(holiday),
        "holiday_name":  holiday.name          if holiday else None,
        "is_half_day":   holiday.is_half_day   if holiday else False,
        "half_day_type": holiday.half_day_type if holiday else None,
    })


# ════════════════════════════════════════════════════════════════════
#  ADMIN — LEAVE POLICY VIEWS (from leaves_new_views.py)
# ════════════════════════════════════════════════════════════════════

def _allocate_all_types_to_employee(employee, year=None):
    if year is None:
        year = timezone.now().year
    if not POLICY_ENABLED:
        return 0
    created_count = 0
    for lt in LeaveTypeConfig.objects.filter(is_active=True):
        _, created = EmployeeLeaveAllocation.objects.get_or_create(
            employee=employee, leave_type=lt, year=year,
            defaults={'allocated_days': lt.days_per_year}
        )
        if created:
            created_count += 1
    return created_count


def _apply_leave_type_to_all_employees(leave_type_config, year=None, update_existing=False):
    if year is None:
        year = timezone.now().year
    employees = User.objects.filter(is_active=True).exclude(is_superuser=True)
    created = updated = 0
    for emp in employees:
        alloc, was_created = EmployeeLeaveAllocation.objects.get_or_create(
            employee=emp, leave_type=leave_type_config, year=year,
            defaults={'allocated_days': leave_type_config.days_per_year}
        )
        if was_created:
            created += 1
        elif update_existing:
            alloc.allocated_days = leave_type_config.days_per_year
            alloc.save(update_fields=['allocated_days', 'updated_at'])
            updated += 1
    return created, updated


@login_required
def admin_leave_policy(request):
    if not request.user.is_superuser and not (
        request.user.role and request.user.role.name == "Admin"
    ):
        return redirect("employee_dashboard")

    if not POLICY_ENABLED:
        messages.error(request, "Run migrations first: python manage.py migrate")
        return redirect("admin_dashboard")

    current_year   = timezone.now().year
    leave_types    = LeaveTypeConfig.objects.all().order_by('-is_active', 'name')
    policies       = LeavePolicy.objects.all().order_by('-is_default', 'name')
    roles          = Role.objects.exclude(name="Admin").order_by('name')
    departments    = Department.objects.order_by('name')

    total_employees   = User.objects.filter(
        is_active=True).exclude(is_superuser=True).count()
    total_leave_types = leave_types.filter(is_active=True).count()
    total_policies    = policies.filter(is_active=True).count()

    lt_stats = []
    for lt in leave_types:
        allocs = EmployeeLeaveAllocation.objects.filter(
            leave_type=lt, year=current_year)
        lt_stats.append({
            'lt':                lt,
            'employees_covered': allocs.count(),
            'total_used':        sum(a.used_days for a in allocs),
        })

    context = {
        'leave_types':       leave_types,
        'policies':          policies,
        'roles':             roles,
        'departments':       departments,
        'lt_stats':          lt_stats,
        'total_employees':   total_employees,
        'total_leave_types': total_leave_types,
        'total_policies':    total_policies,
        'current_year':      current_year,
        'profile':           _build_profile_context(request.user),
        'pending_count':     LeaveRequest.objects.filter(status="PENDING").count(),
    }
    return render(request, "admin_leave_policy.html", context)


@login_required
def admin_leave_type_save(request):
    if not request.user.is_superuser and not (
        request.user.role and request.user.role.name == "Admin"
    ):
        return redirect("employee_dashboard")
    if request.method != "POST":
        return redirect("admin_leave_policy")
    if not POLICY_ENABLED:
        messages.error(request, "Run migrations first.")
        return redirect("admin_dashboard")

    lt_id           = request.POST.get("lt_id")
    code            = request.POST.get("code", "").upper().strip()
    name            = request.POST.get("name", "").strip()
    apply_to_all    = request.POST.get("apply_to_all")    == "on"
    update_existing = request.POST.get("update_existing") == "on"

    if lt_id:
        lt = get_object_or_404(LeaveTypeConfig, id=lt_id)
        lt.name                    = name
        lt.description             = request.POST.get("description", "")
        lt.days_per_year           = float(request.POST.get("days_per_year", lt.days_per_year))
        lt.is_paid                 = request.POST.get("is_paid")             == "on"
        lt.is_accrual_based        = request.POST.get("is_accrual_based")    == "on"
        lt.monthly_accrual         = float(request.POST.get("monthly_accrual", lt.monthly_accrual))
        lt.max_consecutive_days    = int(request.POST.get("max_consecutive_days", 0))
        lt.advance_notice_days     = int(request.POST.get("advance_notice_days", 0))
        lt.document_required_after = int(request.POST.get("document_required_after", 0))
        lt.carry_forward           = request.POST.get("carry_forward") == "on"
        lt.carry_forward_limit     = float(request.POST.get("carry_forward_limit", 0))
        lt.color                   = request.POST.get("color", lt.color)
        lt.applicable_to           = request.POST.get("applicable_to", lt.applicable_to)
        lt.is_active               = request.POST.get("is_active") == "on"
        lt.save()

        if update_existing:
            n = EmployeeLeaveAllocation.objects.filter(
                leave_type=lt, year=timezone.now().year
            ).update(allocated_days=lt.days_per_year)
            messages.success(
                request,
                f"✅ '{lt.name}' updated. {n} employee allocation(s) refreshed."
            )
        else:
            messages.success(request, f"✅ '{lt.name}' updated.")
    else:
        if LeaveTypeConfig.objects.filter(code=code).exists():
            messages.error(request, f"Leave type with code '{code}' already exists.")
            return redirect("admin_leave_policy")

        lt = LeaveTypeConfig.objects.create(
            code                   = code,
            name                   = name,
            description            = request.POST.get("description", ""),
            days_per_year          = float(request.POST.get("days_per_year", 12)),
            is_paid                = request.POST.get("is_paid")             == "on",
            is_accrual_based       = request.POST.get("is_accrual_based")    == "on",
            monthly_accrual        = float(request.POST.get("monthly_accrual", 1.0)),
            max_consecutive_days   = int(request.POST.get("max_consecutive_days", 0)),
            advance_notice_days    = int(request.POST.get("advance_notice_days", 0)),
            document_required_after= int(request.POST.get("document_required_after", 0)),
            carry_forward          = request.POST.get("carry_forward") == "on",
            carry_forward_limit    = float(request.POST.get("carry_forward_limit", 0)),
            color                  = request.POST.get("color", "#00c6d4"),
            applicable_to          = request.POST.get("applicable_to", "ALL"),
            is_active              = True,
            created_by             = request.user,
        )
        messages.success(request, f"✅ Leave type '{name}' (code: {code}) created!")

    if apply_to_all:
        created, updated = _apply_leave_type_to_all_employees(
            lt, update_existing=update_existing)
        messages.info(
            request,
            f"📋 Allocation: {created} new rows created, {updated} existing rows updated."
        )

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if is_ajax:
        return JsonResponse({"success": True, "message": f"Leave type '{name}' saved successfully."})
    return redirect("admin_leave_policy")


@login_required
def admin_leave_type_toggle(request, lt_id):
    if not request.user.is_superuser and not (
        request.user.role and request.user.role.name == "Admin"
    ):
        return redirect("employee_dashboard")
    if not POLICY_ENABLED:
        return redirect("admin_dashboard")

    lt = get_object_or_404(LeaveTypeConfig, id=lt_id)
    lt.is_active = not lt.is_active
    lt.save()
    messages.success(
        request,
        f"Leave type '{lt.name}' {'activated ✅' if lt.is_active else 'deactivated ⛔'}."
    )
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if is_ajax:
        return JsonResponse({
            "success": True, 
            "message": f"Leave type '{lt.name}' {'activated' if lt.is_active else 'deactivated'}.",
            "is_active": lt.is_active
        })
    return redirect("admin_leave_policy")


@login_required
def admin_policy_save(request):
    if not request.user.is_superuser and not (
        request.user.role and request.user.role.name == "Admin"
    ):
        return redirect("employee_dashboard")
    if request.method != "POST":
        return redirect("admin_leave_policy")
    if not POLICY_ENABLED:
        messages.error(request, "Run migrations first.")
        return redirect("admin_dashboard")

    policy_id = request.POST.get("policy_id")
    if policy_id:
        policy = get_object_or_404(LeavePolicy, id=policy_id)
    else:
        policy = LeavePolicy(created_by=request.user)

    policy.name                    = request.POST.get("name", "").strip()
    policy.description             = request.POST.get("description", "")
    policy.max_days_per_request    = int(request.POST.get("max_days_per_request", 5))
    policy.min_advance_days        = int(request.POST.get("min_advance_days", 1))
    policy.weekend_counts_as_leave = request.POST.get("weekend_counts_as_leave") == "on"
    policy.holiday_counts_as_leave = request.POST.get("holiday_counts_as_leave") == "on"
    policy.allow_half_day          = request.POST.get("allow_half_day")    == "on"
    policy.allow_short_leave       = request.POST.get("allow_short_leave") == "on"
    policy.approval_threshold      = int(request.POST.get("approval_threshold", 2))
    policy.is_default              = request.POST.get("is_default") == "on"
    policy.is_active               = request.POST.get("is_active")  == "on"
    policy.save()

    verb = "updated" if policy_id else "created"
    messages.success(request, f"✅ Policy '{policy.name}' {verb} successfully!")
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if is_ajax:
        return JsonResponse({"success": True, "message": f"Policy '{policy.name}' {verb} successfully."})
    return redirect("admin_leave_policy")


@login_required
def admin_apply_to_all_employees(request):
    if not request.user.is_superuser and not (
        request.user.role and request.user.role.name == "Admin"
    ):
        return redirect("employee_dashboard")
    if request.method != "POST":
        return redirect("admin_leave_policy")
    if not POLICY_ENABLED:
        messages.error(request, "Run migrations first.")
        return redirect("admin_dashboard")

    force_update  = request.POST.get("force_update") == "on"
    year          = int(request.POST.get("year", timezone.now().year))
    total_created = total_updated = 0

    for lt in LeaveTypeConfig.objects.filter(is_active=True):
        c, u = _apply_leave_type_to_all_employees(
            lt, year=year, update_existing=force_update)
        total_created += c
        total_updated += u

    messages.success(
        request,
        f"✅ Sync complete for {year}! "
        f"{total_created} new allocations created, "
        f"{total_updated} existing allocations updated."
    )
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if is_ajax:
        return JsonResponse({
            "success": True, 
            "message": f"Sync complete! {total_created} new, {total_updated} updated."
        })
    return redirect("admin_leave_policy")


@login_required
def api_leave_types(request):
    """JSON endpoint — active leave types with employee's remaining balance."""
    if not POLICY_ENABLED:
        return JsonResponse({'leave_types': [], 'year': timezone.now().year})

    year        = timezone.now().year
    leave_types = LeaveTypeConfig.objects.filter(is_active=True).order_by('name')

    result = []
    for lt in leave_types:
        try:
            alloc = EmployeeLeaveAllocation.objects.get(
                employee=request.user, leave_type=lt, year=year)
            remaining = alloc.remaining_days
            used      = alloc.used_days
            allocated = alloc.allocated_days
        except EmployeeLeaveAllocation.DoesNotExist:
            remaining = lt.days_per_year
            used      = 0
            allocated = lt.days_per_year

        result.append({
            'id':                      lt.id,
            'code':                    lt.code,
            'name':                    lt.name,
            'is_paid':                 lt.is_paid,
            'color':                   lt.color,
            'days_per_year':           lt.days_per_year,
            'remaining':               remaining,
            'used':                    used,
            'allocated':               allocated,
            'max_consecutive_days':    lt.max_consecutive_days,
            'advance_notice_days':     lt.advance_notice_days,
            'document_required_after': lt.document_required_after,
        })

    return JsonResponse({'leave_types': result, 'year': year})


# ════════════════════════════════════════════════════════════════════
#  ★ ADMIN — DELETE LEAVE TYPE
# ════════════════════════════════════════════════════════════════════

@login_required
def admin_leave_type_delete(request, lt_id):
    """
    DELETE a LeaveTypeConfig.
    Blocked if any employee has already consumed days from this type.
    Admin should deactivate instead of delete in that case.
    """
    if not request.user.is_superuser and not (
        request.user.role and request.user.role.name == "Admin"
    ):
        return redirect("employee_dashboard")

    if request.method != "POST":
        return redirect("admin_leave_policy")

    if not POLICY_ENABLED:
        messages.error(request, "Run migrations first: python manage.py migrate")
        return redirect("admin_dashboard")

    lt = get_object_or_404(LeaveTypeConfig, id=lt_id)

    # Safety guard — block delete if leave days have been consumed
    used_days_total = EmployeeLeaveAllocation.objects.filter(
        leave_type=lt
    ).aggregate(total=Sum('used_days'))['total'] or 0

    if used_days_total > 0:
        messages.error(
            request,
            f"❌ Cannot delete '{lt.name}' — employees have already used "
            f"{used_days_total} day(s) of this leave type. "
            f"Deactivate it instead to hide it from employees without losing history."
        )
        return redirect("admin_leave_policy")

    # Safe — remove all zero-usage allocations then delete the type
    EmployeeLeaveAllocation.objects.filter(leave_type=lt).delete()
    name = lt.name
    lt.delete()
    messages.success(request, f"✅ Leave type '{name}' deleted successfully.")
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if is_ajax:
        return JsonResponse({"success": True, "message": f"Leave type '{name}' deleted."})
    return redirect("admin_leave_policy")


# ════════════════════════════════════════════════════════════════════
#  ★ ADMIN — TOGGLE LEAVE POLICY ACTIVE / INACTIVE
# ════════════════════════════════════════════════════════════════════

@login_required
def admin_policy_toggle(request, policy_id):
    """Toggle a LeavePolicy between active and inactive."""
    if not request.user.is_superuser and not (
        request.user.role and request.user.role.name == "Admin"
    ):
        return redirect("employee_dashboard")

    if not POLICY_ENABLED:
        return redirect("admin_dashboard")

    policy            = get_object_or_404(LeavePolicy, id=policy_id)
    policy.is_active  = not policy.is_active
    policy.save()
    status = "activated ✅" if policy.is_active else "deactivated ⛔"
    messages.success(request, f"Policy '{policy.name}' {status}.")
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if is_ajax:
        return JsonResponse({
            "success": True, 
            "message": f"Policy '{policy.name}' {status}.",
            "is_active": policy.is_active
        })
    return redirect("admin_leave_policy")


# ════════════════════════════════════════════════════════════════════
#  ★ ADMIN — DELETE LEAVE POLICY
# ════════════════════════════════════════════════════════════════════

@login_required
def admin_policy_delete(request, policy_id):
    """
    DELETE a LeavePolicy.
    Blocked if it is the only active default policy.
    """
    if not request.user.is_superuser and not (
        request.user.role and request.user.role.name == "Admin"
    ):
        return redirect("employee_dashboard")

    if request.method != "POST":
        return redirect("admin_leave_policy")

    if not POLICY_ENABLED:
        messages.error(request, "Run migrations first: python manage.py migrate")
        return redirect("admin_dashboard")

    policy = get_object_or_404(LeavePolicy, id=policy_id)

    # Safety guard — don't delete the last default policy
    if policy.is_default:
        other_active_defaults = LeavePolicy.objects.filter(
            is_default=True, is_active=True
        ).exclude(id=policy_id).count()
        if other_active_defaults == 0:
            messages.error(
                request,
                f"❌ Cannot delete '{policy.name}' — it is the only active default policy. "
                f"Set another policy as default first, then delete this one."
            )
            return redirect("admin_leave_policy")

    name = policy.name
    policy.delete()
    messages.success(request, f"✅ Policy '{name}' deleted successfully.")
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if is_ajax:
        return JsonResponse({"success": True, "message": f"Policy '{name}' deleted."})
    return redirect("admin_leave_policy")



# ════════════════════════════════════════════════════════════════════
#  EMPLOYEE LEAVE DETAIL  — JSON for modal
# ════════════════════════════════════════════════════════════════════


@login_required
def employee_leave_detail(request, leave_id):
    """Returns leave detail as JSON for the modal popup on employee dashboard."""
    leave = get_object_or_404(LeaveRequest, id=leave_id)

    # Only the employee themselves (or admin/hr/tl/manager) can view
    role = get_user_role(request.user)
    allowed = (
        leave.employee == request.user or
        request.user.is_superuser or
        role in ('HR', 'Admin', 'Manager', 'TL')
    )
    if not allowed:
        return JsonResponse({'error': 'Forbidden', 'success': False}, status=403)

    # Build approver status with new approval flow
    approvers_info = []
    
    # Sort order: Manager first, then HR, then TL
    approver_order = {'Manager': 0, 'HR': 1, 'TL': 2}
    
    for approver in leave.approvers.all():
        r = get_user_role(approver)
        
        # Determine vote status based on new approval flow
        if r == 'TL':
            if leave.tl_approved:
                vote = 'approved'
                vote_text = 'Approved'
                vote_icon = '✅'
                vote_color = '#28a745'
            elif leave.tl_rejected:
                vote = 'rejected'
                vote_text = 'Rejected'
                vote_icon = '❌'
                vote_color = '#dc3545'
            else:
                vote = 'pending'
                vote_text = 'Pending'
                vote_icon = '⏳'
                vote_color = '#ffc107'
            acted_at = leave.tl_acted_at.strftime('%d %b %Y, %I:%M %p') if leave.tl_acted_at else None
            
        elif r == 'HR':
            if leave.hr_approved:
                vote = 'approved'
                vote_text = 'Approved'
                vote_icon = '✅'
                vote_color = '#28a745'
            elif leave.hr_rejected:
                vote = 'rejected'
                vote_text = 'Rejected'
                vote_icon = '❌'
                vote_color = '#dc3545'
            else:
                vote = 'pending'
                vote_text = 'Pending'
                vote_icon = '⏳'
                vote_color = '#ffc107'
            acted_at = leave.hr_acted_at.strftime('%d %b %Y, %I:%M %p') if leave.hr_acted_at else None
            
        elif r == 'Manager':
            if leave.manager_approved:
                vote = 'approved'
                vote_text = 'Approved'
                vote_icon = '✅'
                vote_color = '#28a745'
            elif leave.manager_rejected:
                vote = 'rejected'
                vote_text = 'Rejected'
                vote_icon = '❌'
                vote_color = '#dc3545'
            else:
                vote = 'pending'
                vote_text = 'Pending'
                vote_icon = '⏳'
                vote_color = '#ffc107'
            acted_at = leave.manager_acted_at.strftime('%d %b %Y, %I:%M %p') if leave.manager_acted_at else None
        else:
            vote = 'pending'
            vote_text = 'Pending'
            vote_icon = '⏳'
            vote_color = '#ffc107'
            acted_at = None

        # Get initials for avatar
        if approver.first_name and approver.last_name:
            initials = (approver.first_name[0] + approver.last_name[0]).upper()
        else:
            initials = approver.username[:2].upper()

        approvers_info.append({
            'name': approver.get_full_name() or approver.username,
            'role': r,
            'vote': vote,
            'vote_text': vote_text,
            'vote_icon': vote_icon,
            'vote_color': vote_color,
            'acted_at': acted_at,
            'initials': initials,
            'email': approver.email,
        })

    # Sort approvers by order
    approvers_info.sort(key=lambda x: approver_order.get(x['role'], 9))

    # Determine final status display
    if leave.final_status == 'APPROVED':
        status_badge = 'success'
        status_icon = '✅'
        status_text = 'Approved'
        status_color = '#28a745'
    elif leave.final_status == 'REJECTED':
        status_badge = 'danger'
        status_icon = '❌'
        status_text = 'Rejected'
        status_color = '#dc3545'
    else:
        status_badge = 'warning'
        status_icon = '⏳'
        status_text = 'Pending'
        status_color = '#ffc107'

    # Calculate total days
    if leave.duration == 'FULL':
        total_days = (leave.end_date - leave.start_date).days + 1
    elif leave.duration == 'HALF':
        total_days = 0.5
    elif leave.duration == 'SHORT':
        total_days = leave.short_hours / 8 if leave.short_hours else 0.25
    else:
        total_days = 0

    # Prepare response data
    data = {
        'success': True,
        'id': leave.id,
        'leave_type': leave.leave_type,
        'leave_type_display': leave.get_leave_type_display() if hasattr(leave, 'get_leave_type_display') else leave.leave_type,
        'duration': leave.get_duration_display() if hasattr(leave, 'get_duration_display') else leave.duration,
        'duration_raw': leave.duration,
        'start_date': leave.start_date.strftime('%d %b %Y'),
        'start_date_full': leave.start_date.strftime('%A, %d %B %Y'),
        'end_date': leave.end_date.strftime('%d %b %Y') if leave.end_date else None,
        'end_date_full': leave.end_date.strftime('%A, %d %B %Y') if leave.end_date else None,
        'total_days': total_days,
        'reason': leave.reason,
        'final_status': leave.final_status,
        'status_badge': status_badge,
        'status_icon': status_icon,
        'status_text': status_text,
        'status_color': status_color,
        'paid_days': float(leave.paid_days) if leave.paid_days else 0,
        'unpaid_days': float(leave.unpaid_days) if leave.unpaid_days else 0,
        'is_fully_paid': leave.is_fully_paid,
        'approval_count': leave.approval_count,
        'rejection_count': leave.rejection_count,
        'created_at': leave.created_at.strftime('%d %b %Y, %I:%M %p'),
        'created_at_full': leave.created_at.strftime('%A, %d %B %Y at %I:%M %p'),
        'updated_at': leave.updated_at.strftime('%d %b %Y, %I:%M %p') if leave.updated_at else None,
        'approvers': approvers_info,
        'has_attachment': bool(leave.attachment),
        'attachment_url': leave.attachment.url if leave.attachment else None,
        'attachment_name': leave.attachment.name.split('/')[-1] if leave.attachment else None,
        'short_hours': leave.short_hours,
        'short_session': leave.short_session,
        'tl_voted': leave.tl_voted,
        'tl_approved': leave.tl_approved,
        'tl_rejected': leave.tl_rejected,
        'hr_voted': leave.hr_voted,
        'hr_approved': leave.hr_approved,
        'hr_rejected': leave.hr_rejected,
        'manager_voted': leave.manager_voted,
        'manager_approved': leave.manager_approved,
        'manager_rejected': leave.manager_rejected,
    }
    return JsonResponse(data)




# Add this after your employee_dashboard function

@login_required
def employee_leave_balance(request):
    """Employee leave balance page with stats and upcoming holidays"""
    today = date.today()
    current_year = today.year
    current_month = today.month

    # Get balance from EmployeeLeaveAllocation
    leave_summary = get_employee_leave_summary(request.user, current_year)

    # Keep old LeaveBalance for accrual fields (backward compat)
    balance, _ = LeaveBalance.objects.get_or_create(employee=request.user)

    # Use new system's total remaining as the primary balance
    available_balance = leave_summary['total_remaining']
    total_accrued = leave_summary['total_allocated']
    total_taken = leave_summary['total_used']

    # Monthly summary (approved leaves this month)
    monthly_leaves = LeaveRequest.objects.filter(
        employee=request.user,
        final_status='APPROVED',
        start_date__year=current_year,
        start_date__month=current_month
    )
    monthly_paid = monthly_leaves.aggregate(total=Sum('paid_days'))['total'] or 0
    monthly_unpaid = monthly_leaves.aggregate(total=Sum('unpaid_days'))['total'] or 0

    # Salary deductions
    month_start = date(current_year, current_month, 1)
    monthly_deductions = SalaryDeduction.objects.filter(
        employee=request.user, deduction_month=month_start
    )
    total_deduction_this_month = monthly_deductions.aggregate(
        total=Sum('deduction_amount'))['total'] or 0
    total_deduction_all_time = SalaryDeduction.objects.filter(
        employee=request.user
    ).aggregate(total=Sum('deduction_amount'))['total'] or 0

    next_month_balance = available_balance + balance.monthly_accrual_rate

    unread = Notification.objects.filter(
        user=request.user, read_status=False).count()

    pending_leaves = LeaveRequest.objects.filter(
        employee=request.user, final_status="PENDING").count()

    # Active leave types for the apply form dropdown
    active_leave_types = []
    if POLICY_ENABLED:
        active_leave_types = LeaveTypeConfig.objects.filter(
            is_active=True).order_by('name')

    # Get upcoming holidays (from admin configured holidays)
    upcoming_holidays = []
    if HOLIDAYS_ENABLED:
        from .models import Holiday
        upcoming_holidays = Holiday.objects.filter(
            date__gte=today,
            is_active=True
        ).order_by('date')[:10]

    context = {
        # Balance variables
        "leave_summary": leave_summary,
        "leave_breakdown": leave_summary['breakdown'],
        "active_leave_types": active_leave_types,
        "total_remaining": leave_summary['total_remaining'],
        "total_allocated": leave_summary['total_allocated'],
        "total_used_new": leave_summary['total_used'],

        # Keep old names so existing template tags still work
        "balance": balance,
        "available_balance": available_balance,
        "leave_balance": available_balance,
        "total_accrued": total_accrued,
        "total_taken": total_taken,

        "pending_leaves": pending_leaves,
        "unread_count": unread,
        "designation": getattr(request.user, 'designation', None) or '',
        "role_name": get_user_role(request.user),
        "profile": _build_profile_context(request.user),

        "monthly_paid": round(monthly_paid, 1),
        "monthly_unpaid": round(monthly_unpaid, 1),
        "total_deduction_this_month": total_deduction_this_month,
        "total_deduction_all_time": total_deduction_all_time,
        "next_month_balance": round(next_month_balance, 1),
        
        # Upcoming holidays
        "upcoming_holidays": upcoming_holidays,
    }
    return render(request, "employee_leave_balance.html", context)



@login_required
def leave_detail_page(request, leave_id):
    """Display leave details on a dedicated HTML page"""
    leave = get_object_or_404(LeaveRequest, id=leave_id)
    role_name = get_user_role(request.user)
    
    # Check if user can view this leave
    can_view = (
        leave.employee == request.user or
        request.user.is_superuser or
        role_name in ('HR', 'Admin', 'Manager', 'TL')
    )
    
    if not can_view:
        messages.error(request, "You don't have permission to view this leave.")
        if role_name == 'Employee':
            return redirect('employee_dashboard')
        elif role_name == 'TL':
            return redirect('tl_dashboard')
        elif role_name == 'HR':
            return redirect('hr_dashboard')
        elif role_name == 'Manager':
            return redirect('manager_dashboard')
        else:
            return redirect('employee_dashboard')
    
    # Build approvers list with their vote status
    approvers = []
    
    # TL
    tl = None
    for approver in leave.approvers.all():
        if get_user_role(approver) == 'TL':
            tl = approver
            break
    
    if tl:
        approvers.append({
            'name': tl.get_full_name() or tl.username,
            'email': tl.email,
            'role': 'Team Leader',
            'vote': 'approved' if leave.tl_approved else ('rejected' if leave.tl_rejected else 'pending'),
            'acted_at': leave.tl_acted_at.strftime('%d %b %Y, %I:%M %p') if leave.tl_acted_at else None,
        })
    
    # HR
    hr = None
    for approver in leave.approvers.all():
        if get_user_role(approver) == 'HR':
            hr = approver
            break
    
    if hr:
        approvers.append({
            'name': hr.get_full_name() or hr.username,
            'email': hr.email,
            'role': 'HR',
            'vote': 'approved' if leave.hr_approved else ('rejected' if leave.hr_rejected else 'pending'),
            'acted_at': leave.hr_acted_at.strftime('%d %b %Y, %I:%M %p') if leave.hr_acted_at else None,
        })
    
    # Manager
    manager = None
    for approver in leave.approvers.all():
        if get_user_role(approver) == 'Manager':
            manager = approver
            break
    
    if manager:
        approvers.append({
            'name': manager.get_full_name() or manager.username,
            'email': manager.email,
            'role': 'Manager',
            'vote': 'approved' if leave.manager_approved else ('rejected' if leave.manager_rejected else 'pending'),
            'acted_at': leave.manager_acted_at.strftime('%d %b %Y, %I:%M %p') if leave.manager_acted_at else None,
        })
    
    # Check if current user can approve/reject
    can_approve = False
    user_has_voted = False
    
    if leave.final_status == 'PENDING':
        if role_name == 'HR' and not leave.hr_voted and leave.employee != request.user:
            can_approve = True
            user_has_voted = leave.hr_voted
        elif role_name == 'TL' and not leave.tl_voted and leave.employee != request.user:
            can_approve = True
            user_has_voted = leave.tl_voted
        elif role_name == 'Manager' and not leave.manager_voted and leave.employee != request.user:
            can_approve = True
            user_has_voted = leave.manager_voted
        elif request.user.is_superuser:
            can_approve = True
    
    # Calculate total days for display
    total_days = leave.leave_duration_days
    
    context = {
        'leave': leave,
        'approvers': approvers,
        'can_approve': can_approve,
        'user_has_voted': user_has_voted,
        'role_name': role_name,
        'total_days': total_days,
    }
    
    return render(request, 'leave_detail.html', context)