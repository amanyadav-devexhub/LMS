# ═══════════════════════════════════════════════════════════════════
#  leaves/api_views.py  — PURE JSON API VERSION
#  All views return JsonResponse only. No HTML rendering.
#  Auth: session-based @login_required + custom role decorators.
#  ─────────────────────────────────────────────────────────────────
#  Endpoint map (wire these in urls.py):
#
#  GET  /api/dashboard/                     → unified_dashboard_api
#  GET  /api/dashboard/employee/            → employee_dashboard_api
#  GET  /api/dashboard/tl/                  → tl_dashboard_api
#  GET  /api/dashboard/hr/                  → hr_dashboard_api
#  GET  /api/dashboard/manager/             → manager_dashboard_api
#
#  GET  /api/leave/balance/                 → employee_leave_balance_api
#  POST /api/leave/apply/                   → apply_leave_api
#  POST /api/leave/<id>/approve/            → approve_leave_api
#  POST /api/leave/<id>/reject/             → reject_leave_api
#  GET  /api/leave/<id>/                    → leave_detail_api
#  GET  /api/leave/types/                   → api_leave_types        (unchanged, already JSON)
#
#  GET  /api/hr/pending/                    → hr_pending_leaves_api
#  GET  /api/hr/analytics/                  → hr_leave_analytics_api
#  GET  /api/hr/on-leave-today/             → hr_on_leave_today_api
#  GET  /api/hr/new-joiners/                → hr_new_joiners_api
#  GET  /api/hr/departments/               → hr_departments_api
#  GET  /api/hr/my-balance/                 → hr_my_leave_balance_api
#  GET  /api/hr/employees/                  → hr_employee_list_api
#
#  GET  /api/admin/dashboard/               → admin_dashboard_api     (unchanged, already JSON)
#  POST /api/admin/employees/create/        → create_employee_api
#  POST /api/admin/employees/<id>/toggle/   → toggle_employee_status_api
#  GET  /api/admin/employees/search/        → employee_search_json    (unchanged, already JSON)
#  GET  /api/leave-policy/                  → admin_leave_policy_api
#  POST /api/admin/leave-type/save/         → admin_leave_type_save_api
#  POST /api/admin/leave-type/<id>/toggle/  → admin_leave_type_toggle_api
#  POST /api/admin/leave-type/<id>/delete/  → admin_leave_type_delete_api
#  POST /api/admin/policy/save/             → admin_policy_save_api
#  POST /api/admin/policy/<id>/toggle/      → admin_policy_toggle_api
#  POST /api/admin/policy/<id>/delete/      → admin_policy_delete_api
#  POST /api/admin/allocations/sync/        → admin_apply_to_all_employees_api
#
#  GET  /api/holidays/                      → holiday_list_api
#  POST /api/holidays/create/               → holiday_create_api
#  GET  /api/holidays/<id>/                 → holiday_detail_api
#  POST /api/holidays/<id>/edit/            → holiday_edit_api
#  POST /api/holidays/<id>/delete/          → holiday_delete_api
#  POST /api/holidays/<id>/toggle/          → holiday_toggle_status_api
#  POST /api/holidays/bulk-create/          → holiday_bulk_create_api
#  GET  /api/holidays/public/               → public_holidays_api
#  GET  /api/holidays/today/                → check_today_holiday     (unchanged, already JSON)
#
#  GET  /api/notifications/                 → notifications_api
#  GET  /api/employees/<pk>/                → employee_detail_api
# ═══════════════════════════════════════════════════════════════════

from __future__ import annotations

# ── Standard library ─────────────────────────────────────────────────
import json
from datetime import date, datetime, timedelta
from calendar import month_name
import calendar
from decimal import Decimal, ROUND_HALF_UP

# ── Django ───────────────────────────────────────────────────────────
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q, Count, Sum, Case, When, Value, FloatField
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.template.loader import render_to_string
from django.contrib import messages
from django.urls import reverse
from django.utils import timezone
from django.utils.timesince import timesince
from django.views.decorators.csrf import csrf_exempt

# ── App models ───────────────────────────────────────────────────────
from .models import LeaveRequest, Notification, SalaryDeduction
from users.models import User, Department, Role, RolePermissionAssignment, SalaryDetails
from users.rbac import user_has_permission

# ── Auth decorators (your custom ones) ───────────────────────────────
from .decorators import role_required, hr_required, admin_required

# ── Optional models (same safe-import pattern as views.py) ───────────
try:
    from .models import Holiday
    HOLIDAYS_ENABLED = True
except ImportError:
    HOLIDAYS_ENABLED = False

try:
    from .models import LeaveTypeConfig, LeavePolicy, EmployeeLeaveAllocation
    POLICY_ENABLED = True
except ImportError:
    POLICY_ENABLED = False

# ── Re-use all pure-logic helpers from the original views ────────────
# (These helpers don't render HTML, so they're fine to import directly.)
from users.views import _build_profile_context


# ⚠️  DEPRECATED (v2.0+): Not exposed via any URL endpoint
# This is a helper function used internally only.
# Can be removed in a future cleanup if no internal dependencies exist.
def get_user_role(user):
    if getattr(user, "is_superuser", False):
        return "Admin"
    role = getattr(user, "role", None)
    return getattr(role, "name", "") or "Employee"


# ⚠️  DEPRECATED (v2.0+): Not exposed via any URL endpoint
# This is a helper function used internally only.
# Can be removed in a future cleanup if no internal dependencies exist.
def calculate_leave_days(leave):
    if getattr(leave, "duration", "FULL") == "HALF":
        return 0.5
    if getattr(leave, "duration", "FULL") == "SHORT":
        return round(float(getattr(leave, "short_hours", 4) or 4) / 8, 2)
    start_date = getattr(leave, "start_date", None)
    end_date = getattr(leave, "end_date", None) or start_date
    if not start_date:
        return 0
    return (end_date - start_date).days + 1


# ⚠️  DEPRECATED (v2.0+): Not exposed via any URL endpoint
# This is a helper function used internally only.
# Can be removed in a future cleanup if no internal dependencies exist.
def send_notification(users, message, link=None):
    if not users:
        return
    notifications = []
    for user in users:
        if user:
            notifications.append(Notification(user=user, message=message, link=link))
    if notifications:
        Notification.objects.bulk_create(notifications)


def _get_active_policy_for_employee(employee):
    if not POLICY_ENABLED:
        return None
    department = getattr(employee, "department", None)
    active_policies = LeavePolicy.objects.filter(is_active=True)
    if department:
        department_policy = active_policies.filter(applicable_departments=department).order_by("-is_default", "name").first()
        if department_policy:
            return department_policy
    return active_policies.filter(is_default=True).first() or active_policies.order_by("-is_default", "name").first()


def _get_applicable_leave_types_for_employee(employee):
    if not POLICY_ENABLED:
        return LeaveTypeConfig.objects.none()
    role = getattr(employee, "role", None)
    department = getattr(employee, "department", None)
    return (
        LeaveTypeConfig.objects.filter(is_active=True)
        .filter(
            Q(applicable_to="ALL")
            | Q(applicable_to="ROLES", applicable_roles=role)
            | Q(applicable_to="DEPARTMENTS", applicable_departments=department)
        )
        .distinct()
        .order_by("name")
    )

def get_leave_year_for_date(date_obj, starting_month=4):
    """
    Returns the leave year for a given date based on the starting month.
    Example: If starting_month=4 (April), leave year 2025 runs from April 2025 to March 2026
    """
    year = date_obj.year
    if date_obj.month >= starting_month:
        return year
    else:
        return year - 1


def get_leave_year_range(leave_type_config, date_obj=None):
    """
    Returns (start_date, end_date) tuple for the leave year.
    """
    if date_obj is None:
        date_obj = timezone.now().date()
    
    start_month = getattr(leave_type_config, 'starting_month', 4)
    current_year = get_leave_year_for_date(date_obj, start_month)
    
    start_date = date(current_year, start_month, 1)
    
    # End date is the day before the next leave-year start month
    if start_month == 1:
        end_date = date(current_year, 12, 31)
    else:
        end_date = date(current_year + 1, start_month, 1) - timedelta(days=1)
    
    return start_date, end_date

def _target_allocation_days_for_leave_type(leave_type, sync_mode="monthly", as_of_date=None):
    as_of_date = as_of_date or timezone.now().date()
    target_days = float(leave_type.days_per_year or 0)
    
    if sync_mode == "monthly" and leave_type.is_accrual_based:
        # Calculate months elapsed since leave year started
        start_month = getattr(leave_type, 'starting_month', 4)
        leave_year_start = get_leave_year_for_date(as_of_date, start_month)
        leave_start_date = date(leave_year_start, start_month, 1)
        
        # Calculate months elapsed (1-based, capped at 12)
        months_elapsed = (as_of_date.year - leave_start_date.year) * 12 + (as_of_date.month - start_month) + 1
        months_elapsed = max(1, min(12, months_elapsed))
        
        target_days = min(target_days, float(leave_type.monthly_accrual or 0) * months_elapsed)
    
    return target_days

def _ensure_leave_allocations_for_employee(employee, year=None, leave_type_config=None):
    if not POLICY_ENABLED:
        return []
    
    # Determine year based on leave type's starting month
    if year is None:
        current_date = timezone.now().date()
        start_month = getattr(leave_type_config, 'starting_month', 4) if leave_type_config else 4
        year = get_leave_year_for_date(current_date, start_month)
    
    allocations = []
    for leave_type in _get_applicable_leave_types_for_employee(employee):
        # Use the leave type's own starting month to calculate target days
        target_days = _target_allocation_days_for_leave_type(leave_type, sync_mode="monthly")
        
        allocation, _ = EmployeeLeaveAllocation.objects.get_or_create(
            employee=employee,
            leave_type=leave_type,
            year=year,
            defaults={"allocated_days": target_days},
        )
        
        if leave_type.is_accrual_based and float(allocation.allocated_days or 0) < target_days:
            allocation.allocated_days = target_days
            allocation.save(update_fields=["allocated_days", "updated_at"])
        
        allocations.append(allocation)
    return allocations

def _get_available_balance_for_leave_type(employee, leave_type, year=None):
    year = year or timezone.now().year
    if POLICY_ENABLED:
        _ensure_leave_allocations_for_employee(employee, year)
        code = getattr(leave_type, "code", None) or str(leave_type)
        allocation = (
            EmployeeLeaveAllocation.objects.filter(
                employee=employee,
                year=year,
                leave_type__code__iexact=str(code),
            )
            .select_related("leave_type")
            .first()
        )
        if allocation:
            return allocation.remaining_days
    return get_employee_leave_summary(employee, year)["total_remaining"]


def _get_projected_next_month_accrual(employee):
    if not POLICY_ENABLED:
        return 0.0

    return round(
        sum(
            float(getattr(leave_type, "monthly_accrual", 0) or 0)
            for leave_type in _get_applicable_leave_types_for_employee(employee)
            if getattr(leave_type, "is_accrual_based", False) and getattr(leave_type, "is_active", False)
        ),
        1,
    )


def _resolve_leave_type_config_for_code(employee, leave_type_value):
    if not POLICY_ENABLED:
        return None

    normalized = str(leave_type_value or "").strip()
    if not normalized:
        return None

    return (
        _get_applicable_leave_types_for_employee(employee)
        .filter(Q(code__iexact=normalized) | Q(name__iexact=normalized))
        .order_by("name")
        .first()
    )


def _resolve_allocation_for_leave(employee, leave_type_value, leave_date=None):
    if not POLICY_ENABLED:
        return None, None

    leave_type_config = _resolve_leave_type_config_for_code(employee, leave_type_value)
    if leave_date is None:
        leave_date = timezone.now().date()

    if leave_type_config:
        leave_year = get_leave_year_for_date(
            leave_date,
            getattr(leave_type_config, "starting_month", 4),
        )
        _ensure_leave_allocations_for_employee(
            employee,
            year=leave_year,
            leave_type_config=leave_type_config,
        )
        allocation = (
            EmployeeLeaveAllocation.objects.filter(
                employee=employee,
                leave_type=leave_type_config,
                year=leave_year,
            )
            .select_related("leave_type")
            .first()
        )
        return allocation, leave_type_config

    fallback_allocation = (
        EmployeeLeaveAllocation.objects.filter(
            employee=employee,
            leave_type__code__iexact=str(leave_type_value or "").upper(),
        )
        .select_related("leave_type")
        .order_by("-year")
        .first()
    )
    return fallback_allocation, getattr(fallback_allocation, "leave_type", None)


# ⚠️  DEPRECATED (v2.0+): Not exposed via any URL endpoint
# This is a helper function used internally only.
# Can be removed in a future cleanup if no internal dependencies exist.
def get_employee_leave_summary(employee, year=None,leave_type_config=None):
    current_date = timezone.now().date()
    if year is None:
        start_month = getattr(leave_type_config, 'starting_month', 4) if leave_type_config else 4
        year = get_leave_year_for_date(current_date, start_month)

    year = year or timezone.now().year
    if not POLICY_ENABLED:
        return {
            "year": year,
            "has_allocations": False,
            "total_allocated": 0.0,
            "total_used": 0.0,
            "total_remaining": 0.0,
            "breakdown": [],
        }

    if leave_type_config:
        applicable_leave_types = [leave_type_config]
    else:
        applicable_leave_types = list(_get_applicable_leave_types_for_employee(employee))

    allocations = []
    for leave_type in applicable_leave_types:
        allocation_year = year if leave_type_config else get_leave_year_for_date(
            current_date,
            getattr(leave_type, "starting_month", 4),
        )
        _ensure_leave_allocations_for_employee(
            employee,
            year=allocation_year,
            leave_type_config=leave_type,
        )
        allocation = (
            EmployeeLeaveAllocation.objects.filter(
                employee=employee,
                year=allocation_year,
                leave_type=leave_type,
            )
            .select_related("leave_type")
            .first()
        )
        if allocation:
            allocations.append(allocation)

    allocations.sort(key=lambda allocation: allocation.leave_type.name)
    breakdown = [
        {
            "id": allocation.leave_type_id,
            "name": allocation.leave_type.name,
            "code": allocation.leave_type.code,
            "color": allocation.leave_type.color,
            "allocated": round(float(allocation.allocated_days + allocation.carried_forward), 1),
            "used": round(float(allocation.used_days), 1),
            "remaining": round(float(allocation.remaining_days), 1),
            "is_paid": allocation.leave_type.is_paid,
        }
        for allocation in allocations
    ]
    return {
        "year": year,
        "has_allocations": bool(breakdown),
        "total_allocated": round(sum(item["allocated"] for item in breakdown), 1),
        "total_used": round(sum(item["used"] for item in breakdown), 1),
        "total_remaining": round(sum(item["remaining"] for item in breakdown), 1),
        "breakdown": breakdown,
    }


def _calculate_unpaid_leave_deduction_amount(employee, unpaid_days):
    unpaid_days = float(unpaid_days or 0)
    if unpaid_days <= 0:
        return Decimal("0.00")

    salary = SalaryDetails.objects.filter(user=employee).first()
    if not salary:
        return Decimal("0.00")

    monthly_salary = Decimal(str(salary.salary_in_hand or 0))
    if monthly_salary <= 0:
        monthly_salary = (
            Decimal(str(salary.basic_salary or 0))
            + Decimal(str(salary.hra or 0))
            + Decimal(str(salary.bonus or 0))
        )

    if monthly_salary <= 0:
        return Decimal("0.00")

    daily_rate = monthly_salary / Decimal("30")
    return (daily_rate * Decimal(str(unpaid_days))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _upsert_salary_deduction_for_leave(leave):
    unpaid_days = float(getattr(leave, "unpaid_days", 0) or 0)
    if unpaid_days <= 0:
        return

    leave_date = leave.start_date or timezone.now().date()
    deduction_month = date(leave_date.year, leave_date.month, 1)
    deduction_amount = _calculate_unpaid_leave_deduction_amount(leave.employee, unpaid_days)

    SalaryDeduction.objects.update_or_create(
        employee=leave.employee,
        leave_request=leave,
        defaults={
            "unpaid_days": unpaid_days,
            "deduction_amount": deduction_amount,
            "deduction_month": deduction_month,
            "notes": "Auto-created from approved unpaid leave days.",
        },
    )


def _clear_salary_deduction_for_leave(leave):
    deductions = SalaryDeduction.objects.filter(leave_request=leave)
    for deduction in deductions:
        if deduction.is_processed:
            deduction.unpaid_days = 0
            deduction.deduction_amount = Decimal("0.00")
            current_notes = (deduction.notes or "").strip()
            rollback_note = "Auto-adjusted to zero due to leave status reversal."
            deduction.notes = f"{current_notes} | {rollback_note}" if current_notes else rollback_note
            deduction.save(update_fields=["unpaid_days", "deduction_amount", "notes", "updated_at"])
        else:
            deduction.delete()


def _deduct_leave_balance(leave):
    paid_days = float(getattr(leave, "paid_days", 0) or 0)
    if paid_days > 0:
        allocation, _ = _resolve_allocation_for_leave(
            leave.employee,
            leave.leave_type,
            leave.start_date or timezone.now().date(),
        )
        if allocation:
            allocation.used_days = float(allocation.used_days or 0) + paid_days
            allocation.save(update_fields=["used_days", "updated_at"])

    _upsert_salary_deduction_for_leave(leave)


def _restore_leave_balance(leave):
    _clear_salary_deduction_for_leave(leave)

    if float(getattr(leave, "paid_days", 0) or 0) <= 0:
        return

    allocation, _ = _resolve_allocation_for_leave(
        leave.employee,
        leave.leave_type,
        leave.start_date or timezone.now().date(),
    )
    if allocation:
        allocation.used_days = max(0.0, float(allocation.used_days or 0) - float(leave.paid_days or 0))
        allocation.save(update_fields=["used_days", "updated_at"])


def _evaluate_leave_decision(leave):
    threshold = 2
    policy = _get_active_policy_for_employee(leave.employee)
    if policy and getattr(policy, "approval_threshold", None):
        threshold = max(1, int(policy.approval_threshold))
    if getattr(leave, "manager_rejected", False):
        return "REJECTED", "Rejected by Manager"
    if getattr(leave, "manager_approved", False):
        return "APPROVED", "Approved by Manager"
    if int(getattr(leave, "rejection_count", 0) or 0) >= threshold:
        return "REJECTED", f"Received {threshold} rejection votes"
    if int(getattr(leave, "approval_count", 0) or 0) >= threshold:
        return "APPROVED", f"Received {threshold} approval votes"
    return "PENDING", "Awaiting more votes"


# ════════════════════════════════════════════════════════════════════
#  SMALL HELPERS
# ════════════════════════════════════════════════════════════════════

def _ok(data: dict, status: int = 200) -> JsonResponse:
    """Shortcut: success JSON response."""
    return JsonResponse({"success": True, **data}, status=status)


def _err(message: str, status: int = 400, **extra) -> JsonResponse:
    """Shortcut: error JSON response."""
    return JsonResponse({"success": False, "error": message, **extra}, status=status)


def _forbidden(message: str = "You don't have permission to access this resource.") -> JsonResponse:
    return _err(message, status=403)


def _serialize_leave(leave: LeaveRequest) -> dict:
    """Serialize a LeaveRequest to a dict suitable for JSON."""
    total_days = calculate_leave_days(leave)
    return {
        "id": leave.id,
        "leave_type": leave.leave_type,
        "duration": leave.duration,
        "start_date": str(leave.start_date),
        "end_date": str(leave.end_date) if leave.end_date else None,
        "total_days": total_days,
        "reason": leave.reason,
        "status": leave.status,
        "final_status": leave.final_status,
        "paid_days": float(leave.paid_days or 0),
        "unpaid_days": float(leave.unpaid_days or 0),
        "is_fully_paid": getattr(leave, "is_fully_paid", True),
        "approval_count": leave.approval_count,
        "rejection_count": leave.rejection_count,
        "tl_voted": leave.tl_voted,
        "tl_approved": leave.tl_approved,
        "tl_rejected": leave.tl_rejected,
        "hr_voted": leave.hr_voted,
        "hr_approved": leave.hr_approved,
        "hr_rejected": leave.hr_rejected,
        "manager_voted": leave.manager_voted,
        "manager_approved": leave.manager_approved,
        "manager_rejected": leave.manager_rejected,
        "has_attachment": bool(leave.attachment),
        "attachment_url": leave.attachment.url if leave.attachment else None,
        "short_hours": leave.short_hours,
        "short_session": leave.short_session,
        "created_at": leave.created_at.isoformat(),
        "updated_at": leave.updated_at.isoformat() if leave.updated_at else None,
    }


def _serialize_user(emp: User) -> dict:
    """Serialize a User to a minimal dict."""
    return {
        "id": emp.id,
        "name": emp.get_full_name() or emp.username,
        "username": emp.username,
        "email": emp.email,
        "first_name": emp.first_name,
        "last_name": emp.last_name,
        "role": emp.role.name if emp.role else None,
        "department": emp.department.name if emp.department else None,
        "department_id": emp.department.id if emp.department else None,
        "is_active": emp.is_active,
        "date_joined": emp.date_joined.isoformat(),
        "initials": (
            (emp.first_name[:1] + emp.last_name[:1]).upper()
            if emp.first_name and emp.last_name
            else emp.username[:2].upper()
        ),
    }


def _paginate(queryset_or_list, request, page_param: str = "page", per_page: int = 10) -> dict:
    """Returns pagination meta + object list for JSON."""
    paginator = Paginator(queryset_or_list, per_page)
    page_obj = paginator.get_page(request.GET.get(page_param, 1))
    return {
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
        "total_count": paginator.count,
        "has_next": page_obj.has_next(),
        "has_previous": page_obj.has_previous(),
        "start_index": page_obj.start_index() if paginator.count else 0,
        "end_index": page_obj.end_index() if paginator.count else 0,
        "results": list(page_obj.object_list),   # caller converts to list of dicts
        "_page_obj": page_obj,                   # removed before serialising
    }


# ════════════════════════════════════════════════════════════════════
#  UNIFIED DASHBOARD
# ════════════════════════════════════════════════════════════════════

@login_required
def unified_dashboard_api(request):
    """
    Returns the role of the logged-in user so the frontend can decide
    which dashboard view to load.
    """
    role = get_user_role(request.user)
    dashboard_map = {
        "HR":      "/api/dashboard/hr/",
        "TL":      "/api/dashboard/tl/",
        "Manager": "/api/dashboard/manager/",
        "Admin":   "/api/admin/dashboard/",
    }
    if request.user.is_superuser:
        role = "Admin"

    return _ok({
        "role": role,
        "dashboard_url": dashboard_map.get(role, "/api/dashboard/employee/"),
        "user": _serialize_user(request.user),
    })


# ════════════════════════════════════════════════════════════════════
#  EMPLOYEE DASHBOARD
# ════════════════════════════════════════════════════════════════════

@login_required
def employee_dashboard_api(request):
    today = date.today()
    current_year = today.year
    current_month = today.month

    all_leaves = LeaveRequest.objects.filter(employee=request.user).order_by("-created_at")
    leave_summary = get_employee_leave_summary(request.user, current_year)

    available_balance = leave_summary["total_remaining"]
    total_accrued = leave_summary["total_allocated"]
    total_taken = leave_summary["total_used"]

    monthly_leaves = LeaveRequest.objects.filter(
        employee=request.user,
        final_status="APPROVED",
        start_date__year=current_year,
        start_date__month=current_month,
    )
    monthly_paid = monthly_leaves.aggregate(total=Sum("paid_days"))["total"] or 0
    monthly_unpaid = monthly_leaves.aggregate(total=Sum("unpaid_days"))["total"] or 0

    month_start = date(current_year, current_month, 1)
    total_deduction_this_month = (
        SalaryDeduction.objects.filter(
            employee=request.user, deduction_month=month_start
        ).aggregate(total=Sum("deduction_amount"))["total"] or 0
    )
    total_deduction_all_time = (
        SalaryDeduction.objects.filter(employee=request.user)
        .aggregate(total=Sum("deduction_amount"))["total"] or 0
    )

    next_month_balance = round(available_balance + _get_projected_next_month_accrual(request.user), 1)
    unread = Notification.objects.filter(user=request.user, read_status=False).count()
    pending_leaves = all_leaves.filter(final_status="PENDING").count()

    # Paginate leave history
    page_data = _paginate(
        [_serialize_leave(l) for l in all_leaves],
        request,
        per_page=10,
    )
    page_data.pop("_page_obj")

    active_leave_types = []
    if POLICY_ENABLED:
        active_leave_types = [
            {
                "id": lt.id,
                "code": lt.code,
                "name": lt.name,
                "color": lt.color,
                "is_paid": lt.is_paid,
                "days_per_year": lt.days_per_year,
            }
            for lt in _get_applicable_leave_types_for_employee(request.user)
        ]

    return _ok({
        "user": _serialize_user(request.user),
        "profile": _build_profile_context(request.user),
        "designation": getattr(request.user, "designation", None) or "",
        "role_name": get_user_role(request.user),

        # Balance
        "leave_summary": {
            "total_remaining": leave_summary["total_remaining"],
            "total_allocated": leave_summary["total_allocated"],
            "total_used": leave_summary["total_used"],
            "year": leave_summary["year"],
            "has_allocations": leave_summary["has_allocations"],
            "breakdown": leave_summary["breakdown"],
        },
        "available_balance": available_balance,
        "total_accrued": total_accrued,
        "total_taken": total_taken,
        "next_month_balance": next_month_balance,

        # Leave counts
        "all_leaves_count": all_leaves.count(),
        "pending_leaves": pending_leaves,
        "leaves": page_data,

        # Monthly
        "monthly_paid": round(float(monthly_paid), 1),
        "monthly_unpaid": round(float(monthly_unpaid), 1),
        "total_deduction_this_month": float(total_deduction_this_month),
        "total_deduction_all_time": float(total_deduction_all_time),

        # Misc
        "unread_count": unread,
        "active_leave_types": active_leave_types,
    })


# ════════════════════════════════════════════════════════════════════
#  EMPLOYEE LEAVE BALANCE
# ════════════════════════════════════════════════════════════════════

@login_required
def employee_leave_balance_api(request):
    today = date.today()
    current_year = today.year
    current_month = today.month

    leave_summary = get_employee_leave_summary(request.user, current_year)

    available_balance = leave_summary["total_remaining"]
    monthly_leaves = LeaveRequest.objects.filter(
        employee=request.user,
        final_status="APPROVED",
        start_date__year=current_year,
        start_date__month=current_month,
    )
    monthly_paid = monthly_leaves.aggregate(total=Sum("paid_days"))["total"] or 0
    monthly_unpaid = monthly_leaves.aggregate(total=Sum("unpaid_days"))["total"] or 0

    month_start = date(current_year, current_month, 1)
    total_deduction_this_month = (
        SalaryDeduction.objects.filter(
            employee=request.user, deduction_month=month_start
        ).aggregate(total=Sum("deduction_amount"))["total"] or 0
    )
    total_deduction_all_time = (
        SalaryDeduction.objects.filter(employee=request.user)
        .aggregate(total=Sum("deduction_amount"))["total"] or 0
    )

    pending_leaves = LeaveRequest.objects.filter(
        employee=request.user, final_status="PENDING"
    ).count()
    unread = Notification.objects.filter(user=request.user, read_status=False).count()

    active_leave_types = []
    if POLICY_ENABLED:
        active_leave_types = [
            {"id": lt.id, "code": lt.code, "name": lt.name, "color": lt.color}
            for lt in LeaveTypeConfig.objects.filter(is_active=True).order_by("name")
        ]

    upcoming_holidays = []
    if HOLIDAYS_ENABLED:
        upcoming_holidays = [
            {
                "id": h.id,
                "name": h.name,
                "date": str(h.date),
                "holiday_type": h.holiday_type,
                "is_half_day": h.is_half_day,
            }
            for h in Holiday.objects.filter(date__gte=today, is_active=True).order_by("date")[:10]
        ]

    data = {
        "user": _serialize_user(request.user),
        "leave_summary": leave_summary,
        "available_balance": available_balance,
        "total_accrued": leave_summary["total_allocated"],
        "total_taken": leave_summary["total_used"],
        "next_month_balance": round(available_balance + _get_projected_next_month_accrual(request.user), 1),
        "pending_leaves": pending_leaves,
        "unread_count": unread,
        "monthly_paid": round(float(monthly_paid), 1),
        "monthly_unpaid": round(float(monthly_unpaid), 1),
        "total_deduction_this_month": float(total_deduction_this_month),
        "total_deduction_all_time": float(total_deduction_all_time),
        "active_leave_types": active_leave_types,
        "upcoming_holidays": upcoming_holidays,
    }

    return JsonResponse(data)
from django.http import JsonResponse

def ajax_login_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Authentication required"}, status=401)
        return view_func(request, *args, **kwargs)
    return wrapper
# ════════════════════════════════════════════════════════════════════
#  APPLY LEAVE
# ════════════════════════════════════════════════════════════════════

# @csrf_exempt
# @login_required
# def apply_leave_api(request):
#     """
#     GET  → returns form metadata (leave types, policy rules, current balance).
#     POST → submits a leave application.
#     """
#     if request.method == "GET":
#         current_year = date.today().year
#         leave_summary = get_employee_leave_summary(request.user, current_year)
#         active_leave_types = []
#         active_policy = None
#         max_days = 5

#         if POLICY_ENABLED:
#             active_leave_types = [
#                 {
#                     "id": lt.id,
#                     "code": lt.code,
#                     "name": lt.name,
#                     "color": lt.color,
#                     "is_paid": lt.is_paid,
#                     "days_per_year": lt.days_per_year,
#                     "max_consecutive_days": lt.max_consecutive_days,
#                     "advance_notice_days": lt.advance_notice_days,
#                     "document_required_after": lt.document_required_after,
#                 }
#                 for lt in LeaveTypeConfig.objects.filter(is_active=True).order_by("name")
#             ]
#             active_policy = LeavePolicy.objects.filter(is_default=True, is_active=True).first()
#             if active_policy:
#                 max_days = active_policy.max_days_per_request

#         return _ok({
#             "leave_summary": leave_summary,
#             "active_leave_types": active_leave_types,
#             "available_balance": leave_summary["total_remaining"],
#             "max_days": max_days,
#             "policy": {
#                 "id": active_policy.id,
#                 "name": active_policy.name,
#                 "max_days_per_request": active_policy.max_days_per_request,
#                 "min_advance_days": active_policy.min_advance_days,
#                 "allow_half_day": active_policy.allow_half_day,
#                 "allow_short_leave": active_policy.allow_short_leave,
#             } if active_policy else None,
#         })

#     if request.method != "POST":
#         return _err("Method not allowed.", status=405)

#     # ── Parse POST body ───────────────────────────────────────────────
#     leave_type = request.POST.get("leave_type")
#     duration = request.POST.get("duration")
#     start_date_str = request.POST.get("start_date", "").strip()
#     end_date_str = request.POST.get("end_date", "").strip()
#     reason = request.POST.get("reason", "").strip()
#     short_session = request.POST.get("short_session")
#     short_hours = request.POST.get("short_hours")
#     attachment = request.FILES.get("attachment")

#     # Validate start_date
#     try:
#         start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
#     except (ValueError, TypeError):
#         return _err("Invalid start date. Please select a valid date.")

#     today = date.today()
#     if start_date < today:
#         return _err("Start date cannot be in the past.")

#     if not leave_type:
#         return _err("Please select a leave type.")

#     # Resolve end_date + short leave fields
#     if duration in ("HALF", "SHORT"):
#         end_date = start_date
#         if duration == "SHORT":
#             short_session = short_session or "AM"
#             try:
#                 short_hours = int(short_hours or 4)
#             except ValueError:
#                 short_hours = 4
#         else:
#             short_session = None
#             short_hours = None
#     else:
#         short_session = None
#         short_hours = None
#         if end_date_str:
#             try:
#                 end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
#             except (ValueError, TypeError):
#                 return _err("Invalid end date. Please select a valid date.")
#         else:
#             end_date = start_date
#         if end_date < start_date:
#             end_date = start_date

#     total_days = (end_date - start_date).days + 1 if duration == "FULL" else 1

#     # Policy max days check
#     max_days = 5
#     if POLICY_ENABLED:
#         try:
#             policy = LeavePolicy.objects.filter(is_default=True, is_active=True).first()
#             if policy:
#                 max_days = policy.max_days_per_request
#         except Exception:
#             pass

#     if duration == "FULL" and total_days > max_days:
#         return _err(
#             f"Maximum {max_days} days allowed per application. You selected {total_days} days."
#         )

#     if attachment and attachment.size > 5 * 1024 * 1024:
#         return _err("Attachment exceeds 5 MB. Please upload a smaller file.")

#     # Resolve available balance
#     current_year = today.year
#     allocation_obj = None
#     available = 0

#     if POLICY_ENABLED:
#         try:
#             alloc = EmployeeLeaveAllocation.objects.get(
#                 employee=request.user,
#                 leave_type__code=leave_type.upper(),
#                 year=current_year,
#             )
#             available = alloc.remaining_days
#             allocation_obj = alloc
#         except EmployeeLeaveAllocation.DoesNotExist:
#             try:
#                 alloc = EmployeeLeaveAllocation.objects.get(
#                     employee=request.user,
#                     leave_type__name__iexact=leave_type,
#                     year=current_year,
#                 )
#                 available = alloc.remaining_days
#                 allocation_obj = alloc
#             except EmployeeLeaveAllocation.DoesNotExist:
#                 summary = get_employee_leave_summary(request.user, current_year)
#                 available = summary["total_remaining"]
#     else:
#         try:
#             bal_obj = LeaveBalance.objects.get(employee=request.user)
#             available = bal_obj.available_balance
#         except LeaveBalance.DoesNotExist:
#             available = 0

#     # Build and save LeaveRequest
#     leave_obj = LeaveRequest(
#         employee=request.user,
#         leave_type=leave_type,
#         duration=duration,
#         start_date=start_date,
#         end_date=end_date,
#         reason=reason,
#         short_session=short_session if duration == "SHORT" else None,
#         short_hours=short_hours if duration == "SHORT" else None,
#         status="PENDING",
#         attachment=attachment,
#     )
#     leave_obj.calculate_paid_unpaid(available)

#     # Resolve approvers
#     employee = request.user
#     tl = getattr(employee, "reporting_manager", None)
#     hr = (
#         User.objects.filter(role__name="HR", is_active=True)
#         .exclude(id=employee.id)
#         .first()
#     )
#     manager = None
#     if tl and getattr(tl, "reporting_manager", None):
#         manager = tl.reporting_manager
#     if not manager:
#         manager = (
#             User.objects.filter(role__name="Manager", is_active=True)
#             .exclude(id=employee.id)
#             .first()
#         )

#     leave_obj.tl_approved = leave_obj.hr_approved = leave_obj.manager_approved = False
#     leave_obj.tl_rejected = leave_obj.hr_rejected = leave_obj.manager_rejected = False
#     leave_obj.tl_voted = leave_obj.hr_voted = leave_obj.manager_voted = False
#     leave_obj.approval_count = 0
#     leave_obj.rejection_count = 0
#     leave_obj.final_status = "PENDING"
#     leave_obj.save()

#     approvers_list = []
#     for approver in [tl, hr, manager]:
#         if approver and approver.id != employee.id:
#             leave_obj.approvers.add(approver)
#             approvers_list.append(approver)

#     # Notifications
#     applicant_name = request.user.get_full_name() or request.user.username
#     paid_unpaid_text = (
#         f" ({leave_obj.paid_days} paid, {leave_obj.unpaid_days} unpaid)"
#         if leave_obj.unpaid_days > 0
#         else ""
#     )
#     leave_url = reverse("leave_detail", args=[leave_obj.id])
#     send_notification(
#         approvers_list,
#         f"Leave approval required for {applicant_name}. {leave_type} request from {start_date} to {end_date}.",
#         link=leave_url,
#     )
#     Notification.objects.create(
#         user=employee,
#         message="Your leave request has been submitted and is awaiting approval.",
#         link=leave_url,
#     )

#     msg = (
#         f"Leave submitted! {leave_obj.paid_days} days PAID, "
#         f"{leave_obj.unpaid_days} days UNPAID (salary will be deducted)."
#         if leave_obj.unpaid_days > 0
#         else f"Leave submitted! {leave_obj.paid_days} days PAID. Awaiting 2 approvals."
#     )

#     role_name = get_user_role(request.user)
#     if role_name == "HR":
#         redirect_url = reverse("hr_my_leave_balance")
#     elif role_name == "TL":
#         redirect_url = reverse("tl_dashboard")
#     elif role_name == "Manager":
#         redirect_url = reverse("manager_dashboard")
#     elif role_name == "Admin" or request.user.is_superuser:
#         redirect_url = reverse("admin_dashboard")
#     else:
#         redirect_url = reverse("employee_dashboard")

#     return _ok({
#         "message": msg,
#         "leave": _serialize_leave(leave_obj),
#         "has_unpaid": leave_obj.unpaid_days > 0,
#         "redirect_url": redirect_url,
#     }, status=201)

@csrf_exempt
@login_required
def apply_leave_api(request):
    """
    GET  → returns form metadata (leave types, policy rules, current balance).
    POST → submits a leave application.
    """
    if request.method == "GET":
        # ... KEEP YOUR EXISTING GET CODE COMPLETELY UNCHANGED ...
        pass

    if request.method != "POST":
        return _err("Method not allowed.", status=405)

    # ── Parse POST body (YOUR EXISTING CODE) ───────────────────────────────
    leave_type = request.POST.get("leave_type")
    duration = request.POST.get("duration")
    start_date_str = request.POST.get("start_date", "").strip()
    end_date_str = request.POST.get("end_date", "").strip()
    reason = request.POST.get("reason", "").strip()
    short_session = request.POST.get("short_session")
    short_hours = request.POST.get("short_hours")
    attachment = request.FILES.get("attachment")

    # Validate start_date (YOUR EXISTING CODE)
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return _err("Invalid start date. Please select a valid date.")

    today = date.today()
    if start_date < today:
        return _err("Start date cannot be in the past.")

    if not leave_type:
        return _err("Please select a leave type.")

    # ========== NEW VALIDATIONS START HERE ==========
    
    # 1. Get the leave type configuration from database
    if POLICY_ENABLED:
        try:
            from .models import LeaveTypeConfig
            leave_type_config = LeaveTypeConfig.objects.get(
                code__iexact=leave_type,
                is_active=True
            )
        except LeaveTypeConfig.DoesNotExist:
            return _err(f"Invalid or inactive leave type: {leave_type}", status=400)
        
        # 2. RULE: Advance notice check
        advance_notice_days = leave_type_config.advance_notice_days
        if advance_notice_days > 0:
            min_allowed_date = today + timedelta(days=advance_notice_days)
            if start_date < min_allowed_date:
                return _err(
                    f"This leave type requires {advance_notice_days} days advance notice. "
                    f"Earliest start date is {min_allowed_date.strftime('%Y-%m-%d')}."
                )
        
        # 3. Calculate total days for validation
        if duration in ("HALF", "SHORT"):
            temp_end_date = start_date
            if duration == "SHORT":
                temp_total_days = (int(short_hours or 4)) / 8
            else:
                temp_total_days = 0.5
        else:
            if end_date_str:
                try:
                    temp_end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    temp_end_date = start_date
            else:
                temp_end_date = start_date
            if temp_end_date < start_date:
                temp_end_date = start_date
            temp_total_days = (temp_end_date - start_date).days + 1
        
        # 4. RULE: Max consecutive days check
        max_consecutive = leave_type_config.max_consecutive_days
        if max_consecutive > 0 and temp_total_days > max_consecutive:
            return _err(
                f"This leave type allows maximum {max_consecutive} consecutive days. "
                f"You requested {temp_total_days} days."
            )
        
        # 5. RULE: Document requirement check
        doc_required_after = leave_type_config.document_required_after
        if doc_required_after > 0 and temp_total_days > doc_required_after:
            if not attachment:
                return _err(
                    f"This leave type requires a supporting document for leaves longer than "
                    f"{doc_required_after} days. Please upload medical report, marriage card, "
                    f"or other relevant document."
                )
    
    # ========== NEW VALIDATIONS END HERE ==========

    # Resolve end_date + short leave fields (YOUR EXISTING CODE - UNCHANGED)
    if duration in ("HALF", "SHORT"):
        end_date = start_date
        if duration == "SHORT":
            short_session = short_session or "AM"
            try:
                short_hours = int(short_hours or 4)
            except ValueError:
                short_hours = 4
        else:
            short_session = None
            short_hours = None
    else:
        short_session = None
        short_hours = None
        if end_date_str:
            try:
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                return _err("Invalid end date. Please select a valid date.")
        else:
            end_date = start_date
        if end_date < start_date:
            end_date = start_date

    total_days = (end_date - start_date).days + 1 if duration == "FULL" else 1

    # Policy max days check (YOUR EXISTING CODE - UNCHANGED)
    max_days = 5
    if POLICY_ENABLED:
        try:
            policy = LeavePolicy.objects.filter(is_default=True, is_active=True).first()
            if policy:
                max_days = policy.max_days_per_request
        except Exception:
            pass

    if duration == "FULL" and total_days > max_days:
        return _err(
            f"Maximum {max_days} days allowed per application. You selected {total_days} days."
        )

    if attachment and attachment.size > 5 * 1024 * 1024:
        return _err("Attachment exceeds 5 MB. Please upload a smaller file.")

    # Resolve available balance (YOUR EXISTING CODE - UNCHANGED)
    leave_type_config = _resolve_leave_type_config_for_code(request.user, leave_type)
    current_year = get_leave_year_for_date(
        start_date,
        getattr(leave_type_config, "starting_month", 1),
    ) if leave_type_config else start_date.year
    allocation_obj = None
    available = 0

    if POLICY_ENABLED:
        alloc, _ = _resolve_allocation_for_leave(request.user, leave_type, start_date)
        if alloc:
            available = alloc.remaining_days
            allocation_obj = alloc
        else:
            summary = get_employee_leave_summary(request.user, current_year, leave_type_config=leave_type_config)
            available = summary["total_remaining"]
    else:
        summary = get_employee_leave_summary(request.user, current_year)
        available = summary["total_remaining"]

    # Build and save LeaveRequest (YOUR EXISTING CODE - UNCHANGED)
    leave_obj = LeaveRequest(
        employee=request.user,
        leave_type=leave_type,
        duration=duration,
        start_date=start_date,
        end_date=end_date,
        reason=reason,
        short_session=short_session if duration == "SHORT" else None,
        short_hours=short_hours if duration == "SHORT" else None,
        status="PENDING",
        attachment=attachment,
    )
    leave_obj.calculate_paid_unpaid(available)

    # Resolve approvers (YOUR EXISTING CODE - UNCHANGED)
    employee = request.user
    tl = getattr(employee, "reporting_manager", None)
    hr = (
        User.objects.filter(role__name="HR", is_active=True)
        .exclude(id=employee.id)
        .first()
    )
    manager = None
    if tl and getattr(tl, "reporting_manager", None):
        manager = tl.reporting_manager
    if not manager:
        manager = (
            User.objects.filter(role__name="Manager", is_active=True)
            .exclude(id=employee.id)
            .first()
        )

    leave_obj.tl_approved = leave_obj.hr_approved = leave_obj.manager_approved = False
    leave_obj.tl_rejected = leave_obj.hr_rejected = leave_obj.manager_rejected = False
    leave_obj.tl_voted = leave_obj.hr_voted = leave_obj.manager_voted = False
    leave_obj.approval_count = 0
    leave_obj.rejection_count = 0
    leave_obj.final_status = "PENDING"
    leave_obj.save()

    approvers_list = []
    for approver in [tl, hr, manager]:
        if approver and approver.id != employee.id:
            leave_obj.approvers.add(approver)
            approvers_list.append(approver)

    # Notifications (YOUR EXISTING CODE - UNCHANGED)
    applicant_name = request.user.get_full_name() or request.user.username
    paid_unpaid_text = (
        f" ({leave_obj.paid_days} paid, {leave_obj.unpaid_days} unpaid)"
        if leave_obj.unpaid_days > 0
        else ""
    )
    leave_url = reverse("leave_detail", args=[leave_obj.id])
    send_notification(
        approvers_list,
        f"Leave approval required for {applicant_name}. {leave_type} request from {start_date} to {end_date}.{paid_unpaid_text}",
        link=leave_url,
    )
    Notification.objects.create(
        user=employee,
        message="Your leave request has been submitted and is awaiting approval.",
        link=leave_url,
    )

    msg = (
        f"Leave submitted! {leave_obj.paid_days} days PAID, "
        f"{leave_obj.unpaid_days} days UNPAID (salary will be deducted)."
        if leave_obj.unpaid_days > 0
        else f"Leave submitted! {leave_obj.paid_days} days PAID. Awaiting 2 approvals."
    )

    role_name = get_user_role(request.user)
    if role_name == "HR":
        redirect_url = reverse("hr_my_leave_balance")
    elif role_name == "TL":
        redirect_url = reverse("tl_dashboard")
    elif role_name == "Manager":
        redirect_url = reverse("manager_dashboard")
    elif role_name == "Admin" or request.user.is_superuser:
        redirect_url = reverse("admin_dashboard")
    else:
        redirect_url = reverse("employee_dashboard")

    return _ok({
        "message": msg,
        "leave": _serialize_leave(leave_obj),
        "has_unpaid": leave_obj.unpaid_days > 0,
        "redirect_url": redirect_url,
    }, status=201)


    
# ════════════════════════════════════════════════════════════════════
#  APPROVE / REJECT LEAVE
# ════════════════════════════════════════════════════════════════════

@login_required
def approve_leave_api(request, leave_id):
    if request.method != "POST":
        return _err("Method not allowed.", status=405)

    leave = get_object_or_404(LeaveRequest, id=leave_id)
    voter = request.user
    role_name = get_user_role(voter)
    is_admin = request.user.is_superuser or role_name == "Admin"
    old_status = leave.final_status

    # Admin override
    if is_admin:
        leave.final_status = "APPROVED"
        leave.status = "APPROVED"
        if old_status != "APPROVED":
            leave.balance_deducted_at = timezone.now()
        leave.save()
        if old_status != "APPROVED":
            _deduct_leave_balance(leave)
        Notification.objects.create(
            user=leave.employee,
            message="Your leave request has been approved by Admin.",
        )
        return _ok({"message": "Admin override: Leave approved.", "status": "APPROVED"})

    # Auto-add voter if eligible
    if voter not in leave.approvers.all():
        if role_name in ("TL", "HR", "Manager") and leave.employee != voter:
            leave.approvers.add(voter)
        else:
            return _forbidden("You are not an approver for this leave.")

    if leave.employee == voter:
        return _forbidden("You cannot approve your own leave request.")

    if leave.final_status != "PENDING" and role_name != "Manager" and not is_admin:
        return _ok(
            {
                "message": f"This leave is already {leave.final_status}. Only Manager can override.",
                "status": leave.final_status,
            }
        )

    already_voted = (
        (role_name == "TL" and leave.tl_voted)
        or (role_name == "HR" and leave.hr_voted)
        or (role_name == "Manager" and leave.manager_voted)
    )
    if already_voted:
        return _err("You have already voted on this leave.", status=409)

    if role_name == "TL":
        leave.tl_approved = True
        leave.tl_rejected = False
        leave.tl_voted = True
        leave.tl_acted_at = timezone.now()
    elif role_name == "HR":
        leave.hr_approved = True
        leave.hr_rejected = False
        leave.hr_voted = True
        leave.hr_acted_at = timezone.now()
    elif role_name == "Manager":
        leave.manager_approved = True
        leave.manager_rejected = False
        leave.manager_voted = True
        leave.manager_acted_at = timezone.now()
    else:
        return _forbidden("You don't have voting rights.")

    leave.approval_count += 1
    leave.save()

    decision, reason = _evaluate_leave_decision(leave)

    if decision == "APPROVED" and old_status != "APPROVED":
        leave.final_status = "APPROVED"
        leave.status = "APPROVED"
        leave.balance_deducted_at = timezone.now()
        leave.save()
        _deduct_leave_balance(leave)
        Notification.objects.create(
            user=leave.employee,
            message=f"Your leave request has been approved. {reason}.",
        )
    elif decision == "REJECTED":
        if old_status == "APPROVED":
            _restore_leave_balance(leave)
        leave.final_status = "REJECTED"
        leave.status = "REJECTED"
        leave.save()
        Notification.objects.create(
            user=leave.employee,
            message=f"Your leave request has been rejected. {reason}.",
        )
    else:
        leave.final_status = "PENDING"
        leave.status = "PENDING"
        leave.save()

    waiting = []
    if not leave.hr_voted:
        waiting.append("HR")
    if not leave.tl_voted:
        waiting.append("TL")
    if not leave.manager_voted:
        waiting.append("Manager")

    return _ok(
        {
            "decision": decision,
            "reason": reason,
            "status": leave.final_status,
            "waiting_for": waiting,
            "message": f"Approval recorded. Decision: {decision}",
        }
    )


@login_required
def reject_leave_api(request, leave_id):
    if request.method != "POST":
        return _err("Method not allowed.", status=405)

    leave = get_object_or_404(LeaveRequest, id=leave_id)
    voter = request.user
    role_name = get_user_role(voter)
    is_admin = request.user.is_superuser or role_name == "Admin"
    old_status = leave.final_status

    if is_admin:
        if old_status == "APPROVED":
            _restore_leave_balance(leave)
        leave.status = "REJECTED"
        leave.final_status = "REJECTED"
        leave.save()
        Notification.objects.create(
            user=leave.employee,
            message="Your leave request has been rejected by Admin.",
        )
        return _ok({"message": "Admin override: Leave rejected.", "status": "REJECTED"})

    if voter not in leave.approvers.all():
        if role_name in ("TL", "HR", "Manager") and leave.employee != voter:
            leave.approvers.add(voter)
        else:
            return _forbidden("You are not an approver for this leave.")

    if leave.employee == voter:
        return _forbidden("You cannot reject your own leave request.")

    if leave.final_status != "PENDING" and role_name != "Manager" and not is_admin:
        return _ok(
            {
                "message": f"This leave is already {leave.final_status}. Only Manager can override.",
                "status": leave.final_status,
            }
        )

    already_voted = (
        (role_name == "TL" and leave.tl_voted)
        or (role_name == "HR" and leave.hr_voted)
        or (role_name == "Manager" and leave.manager_voted)
    )
    if already_voted:
        return _err("You have already voted on this leave.", status=409)

    if role_name == "TL":
        leave.tl_rejected = True
        leave.tl_approved = False
        leave.tl_voted = True
        leave.tl_acted_at = timezone.now()
    elif role_name == "HR":
        leave.hr_rejected = True
        leave.hr_approved = False
        leave.hr_voted = True
        leave.hr_acted_at = timezone.now()
    elif role_name == "Manager":
        leave.manager_rejected = True
        leave.manager_approved = False
        leave.manager_voted = True
        leave.manager_acted_at = timezone.now()
    else:
        return _forbidden("You don't have voting rights.")

    leave.rejection_count += 1
    leave.save()

    decision, reason = _evaluate_leave_decision(leave)

    if decision == "REJECTED":
        if old_status == "APPROVED":
            _restore_leave_balance(leave)
        leave.final_status = "REJECTED"
        leave.status = "REJECTED"
        leave.save()
        Notification.objects.create(
            user=leave.employee,
            message=f"Your leave request has been rejected. {reason}.",
        )
    elif decision == "APPROVED" and old_status != "APPROVED":
        leave.final_status = "APPROVED"
        leave.status = "APPROVED"
        leave.balance_deducted_at = timezone.now()
        leave.save()
        _deduct_leave_balance(leave)
    else:
        if old_status == "APPROVED":
            _restore_leave_balance(leave)
        leave.final_status = "PENDING"
        leave.status = "PENDING"
        leave.save()

    waiting = []
    if not leave.hr_voted:
        waiting.append("HR")
    if not leave.tl_voted:
        waiting.append("TL")
    if not leave.manager_voted:
        waiting.append("Manager")

    return _ok(
        {
            "decision": decision,
            "reason": reason,
            "status": leave.final_status,
            "waiting_for": waiting,
            "message": f"Rejection recorded. Decision: {decision}",
        }
    )


# ════════════════════════════════════════════════════════════════════
#  LEAVE DETAIL
# ════════════════════════════════════════════════════════════════════

@login_required
def leave_detail_api(request, leave_id):
    leave = get_object_or_404(LeaveRequest, id=leave_id)
    role = get_user_role(request.user)
    allowed = (
        leave.employee == request.user
        or request.user.is_superuser
        or role in ("HR", "Admin", "Manager", "TL")
    )
    if not allowed:
        return _forbidden()

    approver_order = {"Manager": 0, "HR": 1, "TL": 2}
    approvers_info = []
    for approver in leave.approvers.all():
        r = get_user_role(approver)
        vote_map = {
            "TL": (leave.tl_approved, leave.tl_rejected, leave.tl_acted_at),
            "HR": (leave.hr_approved, leave.hr_rejected, leave.hr_acted_at),
            "Manager": (leave.manager_approved, leave.manager_rejected, leave.manager_acted_at),
        }
        approved, rejected, acted_at = vote_map.get(r, (False, False, None))
        vote = "approved" if approved else ("rejected" if rejected else "pending")
        approvers_info.append({
            "name": approver.get_full_name() or approver.username,
            "email": approver.email,
            "role": r,
            "vote": vote,
            "acted_at": acted_at.isoformat() if acted_at else None,
            "initials": (
                (approver.first_name[:1] + approver.last_name[:1]).upper()
                if approver.first_name and approver.last_name
                else approver.username[:2].upper()
            ),
        })
    approvers_info.sort(key=lambda x: approver_order.get(x["role"], 9))

    total_days = calculate_leave_days(leave)

    can_approve = False
    if leave.final_status == "PENDING" and leave.employee != request.user:
        if role == "HR" and not leave.hr_voted:
            can_approve = True
        elif role == "TL" and not leave.tl_voted:
            can_approve = True
        elif role == "Manager" and not leave.manager_voted:
            can_approve = True
        elif request.user.is_superuser:
            can_approve = True

    return _ok(
        {
            **_serialize_leave(leave),
            "total_days": total_days,
            "employee": _serialize_user(leave.employee),
            "approvers": approvers_info,
            "can_approve": can_approve,
        }
    )


# ════════════════════════════════════════════════════════════════════
#  TL DASHBOARD
# ════════════════════════════════════════════════════════════════════

from django.core.paginator import Paginator
@login_required
@role_required(["TL"])
def tl_dashboard_api(request):
    """
    TL Dashboard JSON API - returns pure JSON for AJAX calls
    """
    today = date.today()
    current_year = timezone.now().year

    # Team members reporting to this TL
    team_members = User.objects.filter(
        reporting_manager=request.user
    ).select_related("role", "department")

    # Pending leaves (TL hasn't voted yet)
    all_pending = LeaveRequest.objects.filter(
        tl_voted=False,
        final_status="PENDING",
        employee__reporting_manager=request.user,
    ).select_related("employee", "employee__department").order_by("-created_at")

    # Team members on leave today
    on_leave_today = LeaveRequest.objects.filter(
        final_status="APPROVED",
        employee__reporting_manager=request.user,
        start_date__lte=today,
        end_date__gte=today,
    ).select_related("employee")

    # All team leaves for current year
    all_team = LeaveRequest.objects.filter(
        employee__reporting_manager=request.user,
        start_date__year=current_year,
    ).select_related("employee", "employee__department").order_by("-created_at")

    # TL's own leaves
    my_leaves_qs = LeaveRequest.objects.filter(
        employee=request.user
    ).order_by("-created_at")

    # Build team data with leave summaries
    team_data = []
    for member in team_members:
        member_leaves = all_team.filter(employee=member)
        summary = get_employee_leave_summary(member, current_year)

        # Get balance values from summary breakdown
        casual_balance = 0
        sick_balance = 0
        for b in summary.get("breakdown", []):
            code = str(b.get("code") or "").upper()
            name = str(b.get("name") or "").upper()
            if code == "CASUAL" or "CASUAL" in name:
                casual_balance = b.get("remaining", 0)
            elif code == "SICK" or "SICK" in name:
                sick_balance = b.get("remaining", 0)

        total_remaining = float(summary.get("total_remaining") or 0)
        if float(casual_balance or 0) == 0 and float(sick_balance or 0) == 0:
            casual_balance = total_remaining
        
        team_data.append({
            "member": _serialize_user(member),
            "total_leaves": member_leaves.count(),
            "approved": member_leaves.filter(final_status="APPROVED").count(),
            "pending": member_leaves.filter(final_status="PENDING").count(),
            "casual_balance": casual_balance,
            "sick_balance": sick_balance,
            "total_remaining": total_remaining,
            "is_on_leave": on_leave_today.filter(employee=member).exists(),
        })

    # Get pagination parameters
    pending_page = int(request.GET.get('pending_page', 1))
    history_page = int(request.GET.get('history_page', 1))
    my_leaves_page = int(request.GET.get('my_leaves_page', 1))
    
    per_page = 10

    # Paginate pending leaves
    paginator_pending = Paginator(all_pending, per_page)
    pending_page_obj = paginator_pending.get_page(pending_page)
    
    # Paginate team history
    paginator_history = Paginator(all_team, per_page)
    history_page_obj = paginator_history.get_page(history_page)
    
    # Paginate my leaves
    paginator_myleaves = Paginator(my_leaves_qs, per_page)
    my_leaves_page_obj = paginator_myleaves.get_page(my_leaves_page)

    # Get active leave types for apply form
    active_leave_types = []
    leave_type_display_map = {}
    if POLICY_ENABLED:
        active_leave_type_qs = LeaveTypeConfig.objects.filter(is_active=True).order_by("name")
        active_leave_types = [
            {"id": lt.id, "code": lt.code, "name": lt.name, "color": lt.color}
            for lt in active_leave_type_qs
        ]
        for lt in active_leave_type_qs:
            leave_type_display_map[lt.code.upper()] = lt.name
            leave_type_display_map[lt.name.upper()] = lt.name

    def _serialize_leave_with_employee(leave):
        data = _serialize_leave(leave)
        data["employee"] = _serialize_user(leave.employee)
        leave_type_key = (leave.leave_type or "").strip().upper()
        data["leave_type_display"] = leave_type_display_map.get(leave_type_key, leave.leave_type or "—")
        return data

    # Get unread notification count
    unread_count = Notification.objects.filter(user=request.user, read_status=False).count()

    # Get TL's own leave summary
    my_leave_summary = get_employee_leave_summary(request.user, current_year)

    return _ok({
        "user": _serialize_user(request.user),
        
        # Counts
        "pending_count": all_pending.count(),
        "on_leave_count": on_leave_today.count(),
        "team_count": team_members.count(),
        "approved_count": all_team.filter(final_status="APPROVED").count(),
        "my_leave_count": my_leaves_qs.count(),
        "unread_count": unread_count,
        
        # Team data
        "team_data": team_data,
        
        # Paginated data
        "pending_leaves": {
            "page": pending_page_obj.number,
            "num_pages": paginator_pending.num_pages,
            "total_count": paginator_pending.count,
            "has_next": pending_page_obj.has_next(),
            "has_previous": pending_page_obj.has_previous(),
            "start_index": pending_page_obj.start_index(),
            "end_index": pending_page_obj.end_index(),
            "results": [_serialize_leave_with_employee(leave) for leave in pending_page_obj],
        },
        "team_history": {
            "page": history_page_obj.number,
            "num_pages": paginator_history.num_pages,
            "total_count": paginator_history.count,
            "has_next": history_page_obj.has_next(),
            "has_previous": history_page_obj.has_previous(),
            "start_index": history_page_obj.start_index(),
            "end_index": history_page_obj.end_index(),
            "results": [_serialize_leave_with_employee(leave) for leave in history_page_obj],
        },
        "my_leaves": {
            "page": my_leaves_page_obj.number,
            "num_pages": paginator_myleaves.num_pages,
            "total_count": paginator_myleaves.count,
            "has_next": my_leaves_page_obj.has_next(),
            "has_previous": my_leaves_page_obj.has_previous(),
            "start_index": my_leaves_page_obj.start_index(),
            "end_index": my_leaves_page_obj.end_index(),
            "results": [_serialize_leave_with_employee(leave) for leave in my_leaves_page_obj],
        },
        
        # Additional data
        "active_leave_types": active_leave_types,
        "my_leave_summary": my_leave_summary,
        "current_year": current_year,
    })

# ════════════════════════════════════════════════════════════════════
#  HR DASHBOARD
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["HR", "Admin"])
def hr_dashboard_api(request):
    today = date.today()
    current_year = timezone.now().year
    current_month = timezone.now().month

    all_emps = User.objects.exclude(is_superuser=True)
    total_employees = all_emps.count()
    active_count = all_emps.filter(is_active=True).count()

    pending_leaves = (
        LeaveRequest.objects.filter(hr_voted=False, manager_voted=False)
        .exclude(employee=request.user)
        .select_related("employee", "employee__department")
        .order_by("-created_at")
    )
    pending_count = pending_leaves.count()

    new_joiners_count = all_emps.filter(
        date_joined__year=current_year, date_joined__month=current_month
    ).count()

    on_leave_today_count = LeaveRequest.objects.filter(
        status="APPROVED", start_date__lte=today, end_date__gte=today
    ).count()

    my_leave_summary = get_employee_leave_summary(request.user, current_year)

    active_leave_types = []
    if POLICY_ENABLED:
        active_leave_types = [
            {"id": lt.id, "code": lt.code, "name": lt.name, "color": lt.color}
            for lt in LeaveTypeConfig.objects.filter(is_active=True).order_by("name")
        ]

    on_leave_today_preview = [
        {
            "employee": _serialize_user(l.employee),
            "leave": _serialize_leave(l),
        }
        for l in LeaveRequest.objects.filter(
            status="APPROVED", start_date__lte=today, end_date__gte=today
        )
        .select_related("employee", "employee__department")
        .order_by("employee__first_name")[:5]
    ]

    recent_activity = [
        _serialize_leave(l)
        for l in LeaveRequest.objects.select_related("employee").order_by("-updated_at")[:6]
    ]

    recent_joiners = [
        _serialize_user(u)
        for u in User.objects.exclude(is_superuser=True)
        .select_related("role")
        .order_by("-date_joined")[:6]
    ]

    my_recent_leaves = [
        _serialize_leave(l)
        for l in LeaveRequest.objects.filter(employee=request.user).order_by("-created_at")[:4]
    ]

    unread = Notification.objects.filter(user=request.user, read_status=False).count()

    return _ok(
        {
            "user": _serialize_user(request.user),
            "total_employees": total_employees,
            "active_count": active_count,
            "pending_count": pending_count,
            "new_joiners_count": new_joiners_count,
            "on_leave_today_count": on_leave_today_count,
            "on_leave_today_preview": on_leave_today_preview,
            "my_leave_summary": my_leave_summary,
            "active_leave_types": active_leave_types,
            "recent_pending": [
                {
                    **_serialize_leave(l),
                    "employee": _serialize_user(l.employee),
                }
                for l in pending_leaves[:5]
            ],
            "recent_activity": recent_activity,
            "recent_joiners": recent_joiners,
            "my_recent_leaves": my_recent_leaves,
            "unread_count": unread,
        }
    )


# ════════════════════════════════════════════════════════════════════
#  HR — PENDING LEAVES
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["HR", "Admin"])
def hr_pending_leaves_api(request):
    leaves = (
        LeaveRequest.objects.filter(hr_voted=False, manager_voted=False)
        .exclude(employee=request.user)
        .select_related("employee", "employee__department")
        .order_by("-created_at")
    )

    page_data = _paginate(
        [
            {**_serialize_leave(l), "employee": _serialize_user(l.employee)}
            for l in leaves
        ],
        request,
        per_page=10,
    )
    page_data.pop("_page_obj")

    return _ok({"pending_count": leaves.count(), **page_data})


# ════════════════════════════════════════════════════════════════════
#  HR — LEAVE ANALYTICS
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["HR", "Admin", "Manager"])
def hr_leave_analytics_api(request):
    today = date.today()
    current_year = timezone.now().year
    current_month = today.month

    monthly_all, monthly_approved, monthly_rejected, monthly_pending = [], [], [], []
    for m in range(1, 13):
        qs = LeaveRequest.objects.filter(start_date__year=current_year, start_date__month=m)
        monthly_all.append(qs.count())
        monthly_approved.append(qs.filter(final_status="APPROVED").count())
        monthly_rejected.append(qs.filter(final_status="REJECTED").count())
        monthly_pending.append(qs.filter(final_status="PENDING").count())

    type_labels, type_counts, type_colors = [], [], []
    if POLICY_ENABLED:
        for lt in LeaveTypeConfig.objects.filter(is_active=True).order_by("name"):
            cnt = LeaveRequest.objects.filter(
                final_status="APPROVED", start_date__year=current_year, leave_type=lt.code
            ).count()
            if cnt > 0:
                type_labels.append(lt.name)
                type_counts.append(cnt)
                type_colors.append(lt.color)

    if not type_labels:
        for code, label, color in [
            ("CASUAL", "Casual Leave", "#00c6d4"),
            ("SICK", "Sick Leave", "#05c98a"),
            ("URGENT", "Urgent Leave", "#f5a623"),
        ]:
            cnt = LeaveRequest.objects.filter(
                final_status="APPROVED", start_date__year=current_year, leave_type=code
            ).count()
            if cnt > 0 and label not in type_labels:
                type_labels.append(label)
                type_counts.append(cnt)
                type_colors.append(color)

    total_this_year = LeaveRequest.objects.filter(start_date__year=current_year).count()
    approved_count = LeaveRequest.objects.filter(
        final_status="APPROVED", start_date__year=current_year
    ).count()
    rejected_count = LeaveRequest.objects.filter(
        final_status="REJECTED", start_date__year=current_year
    ).count()
    pending_total = LeaveRequest.objects.filter(
        final_status="PENDING", start_date__year=current_year
    ).count()
    on_leave_today = LeaveRequest.objects.filter(
        final_status="APPROVED", start_date__lte=today, end_date__gte=today
    ).count()
    this_month_total = LeaveRequest.objects.filter(
        start_date__year=current_year, start_date__month=current_month
    ).count()
    this_month_approved = LeaveRequest.objects.filter(
        final_status="APPROVED", start_date__year=current_year, start_date__month=current_month
    ).count()
    decided = approved_count + rejected_count
    approval_rate = round((approved_count / decided * 100), 1) if decided else 0

    top_takers = list(
        LeaveRequest.objects.filter(final_status="APPROVED", start_date__year=current_year)
        .values("employee", "employee__first_name", "employee__last_name", "employee__department__name")
        .annotate(
            total_days=Sum(
                Case(
                    When(duration="FULL", then=Value(1.0)),
                    When(duration="HALF", then=Value(0.5)),
                    When(duration="SHORT", then=Value(0.25)),
                    default=Value(0.0),
                    output_field=FloatField(),
                )
            )
        )
        .order_by("-total_days")[:8]
    )

    dept_leave_data = list(
        LeaveRequest.objects.filter(final_status="APPROVED", start_date__year=current_year)
        .values("employee__department__name")
        .annotate(count=Count("id"))
        .order_by("-count")[:8]
    )

    week_labels, week_counts = [], []
    for i in range(7, -1, -1):
        week_start = today - timedelta(days=today.weekday() + 7 * i)
        week_end = week_start + timedelta(days=6)
        cnt = LeaveRequest.objects.filter(
            start_date__gte=week_start, start_date__lte=week_end
        ).count()
        week_labels.append(week_start.strftime("%d %b").lstrip("0"))
        week_counts.append(cnt)

    return _ok(
        {
            "current_year": current_year,
            "totals": {
                "total_this_year": total_this_year,
                "approved": approved_count,
                "rejected": rejected_count,
                "pending": pending_total,
                "on_leave_today": on_leave_today,
                "this_month_total": this_month_total,
                "this_month_approved": this_month_approved,
                "approval_rate": approval_rate,
            },
            "monthly_chart": {
                "labels": [month_name[m][:3] for m in range(1, 13)],
                "all": monthly_all,
                "approved": monthly_approved,
                "rejected": monthly_rejected,
                "pending": monthly_pending,
            },
            "type_chart": {
                "labels": type_labels,
                "counts": type_counts,
                "colors": type_colors,
            },
            "department_chart": {
                "labels": [d["employee__department__name"] or "Unknown" for d in dept_leave_data],
                "counts": [d["count"] for d in dept_leave_data],
            },
            "weekly_chart": {
                "labels": week_labels,
                "counts": week_counts,
            },
            "top_takers": [
                {
                    "employee_id": item["employee"],
                    "name": f"{item['employee__first_name']} {item['employee__last_name']}".strip(),
                    "department": item["employee__department__name"] or "No dept",
                    "total_days": item["total_days"] or 0,
                }
                for item in top_takers
            ],
        }
    )


# ════════════════════════════════════════════════════════════════════
#  HR — ON LEAVE TODAY
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["HR", "Admin", "Manager"])
def hr_on_leave_today_api(request):
    today = date.today()
    on_leave = LeaveRequest.objects.filter(
        status="APPROVED", start_date__lte=today, end_date__gte=today
    ).select_related("employee", "employee__department", "employee__role").order_by(
        "employee__first_name"
    )

    dept_breakdown = list(
        on_leave.values("employee__department__name")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    return _ok(
        {
            "today": str(today),
            "on_leave_count": on_leave.count(),
            "on_leave": [
                {
                    "employee": _serialize_user(l.employee),
                    "leave": _serialize_leave(l),
                }
                for l in on_leave
            ],
            "department_breakdown": [
                {"department": d["employee__department__name"] or "Unknown", "count": d["count"]}
                for d in dept_breakdown
            ],
        }
    )


# ════════════════════════════════════════════════════════════════════
#  HR — NEW JOINERS
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["HR", "Admin"])
def hr_new_joiners_api(request):
    today = date.today()
    current_year = timezone.now().year
    current_month = timezone.now().month
    filter_period = request.GET.get("period", "30")

    if filter_period == "month":
        joiners = User.objects.exclude(is_superuser=True).filter(
            date_joined__year=current_year, date_joined__month=current_month
        )
    elif filter_period == "year":
        joiners = User.objects.exclude(is_superuser=True).filter(date_joined__year=current_year)
    else:
        since = today - timedelta(days=30)
        joiners = User.objects.exclude(is_superuser=True).filter(date_joined__date__gte=since)

    joiners = joiners.select_related("role", "department").order_by("-date_joined")
    return _ok(
        {
            "filter_period": filter_period,
            "joiners_count": joiners.count(),
            "joiners": [_serialize_user(u) for u in joiners],
        }
    )


# ════════════════════════════════════════════════════════════════════
#  HR — DEPARTMENTS
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["HR", "Admin"])
def hr_departments_api(request):
    today = date.today()
    role_name = get_user_role(request.user)
    is_admin = request.user.is_superuser or role_name == "Admin"
    departments = Department.objects.select_related("hr").annotate(
        total_employees=Count("user", filter=Q(user__is_superuser=False)),
        active_employees=Count("user", filter=Q(user__is_superuser=False, user__is_active=True)),
    ).order_by("-total_employees")

    dept_data = []
    total_employees = 0
    total_active_employees = 0
    total_on_leave = 0
    for dept in departments:
        on_leave_count = LeaveRequest.objects.filter(
            status="APPROVED",
            start_date__lte=today,
            end_date__gte=today,
            employee__department=dept,
        ).count()
        total_employees += dept.total_employees
        total_active_employees += dept.active_employees
        total_on_leave += on_leave_count

        if is_admin:
            detail_url = reverse("department_detail", args=[dept.id])
            edit_url = reverse("department_edit", args=[dept.id])
        else:
            detail_url = f"{reverse('hr_employee_list')}?department={dept.id}"
            edit_url = None

        dept_data.append(
            {
                "id": dept.id,
                "name": dept.name,
                "hr": {
                    "id": dept.hr.id,
                    "name": dept.hr.get_full_name() or dept.hr.email,
                    "email": dept.hr.email,
                } if dept.hr else None,
                "total_employees": dept.total_employees,
                "active_employees": dept.active_employees,
                "on_leave": on_leave_count,
                "attendance_percentage": round((dept.active_employees / dept.total_employees) * 100, 1) if dept.total_employees else 0,
                "detail_url": detail_url,
                "edit_url": edit_url,
            }
        )
    return _ok(
        {
            "departments": dept_data,
            "stats": {
                "departments_count": len(dept_data),
                "total_employees": total_employees,
                "active_employees": total_active_employees,
                "on_leave_today": total_on_leave,
            },
        }
    )


# ════════════════════════════════════════════════════════════════════
#  HR — MY LEAVE BALANCE
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["HR", "Admin"])
def hr_my_leave_balance_api(request):
    today = date.today()
    current_year = today.year
    current_month = today.month

    leave_summary = get_employee_leave_summary(request.user, current_year)
    my_leaves = LeaveRequest.objects.filter(employee=request.user).order_by("-created_at")

    monthly_leaves = LeaveRequest.objects.filter(
        employee=request.user,
        final_status="APPROVED",
        start_date__year=current_year,
        start_date__month=current_month,
    )
    monthly_paid = monthly_leaves.aggregate(total=Sum("paid_days"))["total"] or 0
    monthly_unpaid = monthly_leaves.aggregate(total=Sum("unpaid_days"))["total"] or 0

    month_start = date(current_year, current_month, 1)
    total_deduction_this_month = (
        SalaryDeduction.objects.filter(employee=request.user, deduction_month=month_start)
        .aggregate(total=Sum("deduction_amount"))["total"] or 0
    )
    total_deduction_all_time = (
        SalaryDeduction.objects.filter(employee=request.user)
        .aggregate(total=Sum("deduction_amount"))["total"] or 0
    )

    upcoming_holidays = []
    if HOLIDAYS_ENABLED:
        upcoming_holidays = [
            {"id": h.id, "name": h.name, "date": str(h.date), "holiday_type": h.holiday_type}
            for h in Holiday.objects.filter(date__gte=today, is_active=True).order_by("date")[:10]
        ]

    return _ok(
        {
            "leave_summary": leave_summary,
            "approved_count": my_leaves.filter(status="APPROVED").count(),
            "rejected_count": my_leaves.filter(status="REJECTED").count(),
            "pending_count": my_leaves.filter(status="PENDING").count(),
            "recent_leaves": [_serialize_leave(l) for l in my_leaves[:10]],
            "monthly_paid": round(float(monthly_paid), 1),
            "monthly_unpaid": round(float(monthly_unpaid), 1),
            "total_deduction_this_month": float(total_deduction_this_month),
            "total_deduction_all_time": float(total_deduction_all_time),
            "upcoming_holidays": upcoming_holidays,
        }
    )


# ════════════════════════════════════════════════════════════════════
#  HR — EMPLOYEE LIST
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["HR", "Admin", "Manager"])
def hr_employee_list_api(request):
    today = date.today()
    search_query = request.GET.get("search", "").strip()
    dept_filter = request.GET.get("department", "").strip()
    role_filter = request.GET.get("role", "").strip()
    status_filter = request.GET.get("status", "").strip()

    employees = (
        User.objects.exclude(role__name__iexact="Admin")
        .exclude(is_superuser=True)
        .select_related("role", "department")
        .order_by("-date_joined")
    )
    if search_query:
        employees = employees.filter(
            Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(username__icontains=search_query)
            | Q(department__name__icontains=search_query)
            | Q(role__name__icontains=search_query)
        )
    if dept_filter:
        employees = employees.filter(department__pk=dept_filter)
    if role_filter:
        employees = employees.filter(role__pk=role_filter)

    employee_data = []
    for emp in employees:
        on_leave = LeaveRequest.objects.filter(
            employee=emp, status="APPROVED", start_date__lte=today, end_date__gte=today
        ).exists()
        if status_filter == "on_leave" and not on_leave:
            continue
        if status_filter == "active" and not emp.is_active:
            continue
        if status_filter == "inactive" and emp.is_active:
            continue
        employee_data.append({**_serialize_user(emp), "on_leave": on_leave})

    all_emps = User.objects.exclude(role__name__iexact="Admin").exclude(is_superuser=True)
    on_leave_count = (
        LeaveRequest.objects.filter(
            status="APPROVED", start_date__lte=today, end_date__gte=today
        )
        .values("employee")
        .distinct()
        .count()
    )

    page_data = _paginate(employee_data, request, per_page=20)
    results = page_data.pop("_page_obj")  # already list-of-dicts, Paginator wraps them
    page_data["results"] = list(page_data["results"])

    return _ok(
        {
            "total_count": all_emps.count(),
            "active_count": all_emps.filter(is_active=True).count(),
            "inactive_count": all_emps.filter(is_active=False).count(),
            "on_leave_count": on_leave_count,
            "result_count": len(employee_data),
            "employees": page_data,
            "filters": {
                "search": search_query,
                "department": dept_filter,
                "role": role_filter,
                "status": status_filter,
            },
            "departments": [{"id": d.id, "name": d.name} for d in Department.objects.all().order_by("name")],
            "roles": [{"id": r.id, "name": r.name} for r in Role.objects.exclude(name="Admin").order_by("name")],
        }
    )


# ════════════════════════════════════════════════════════════════════
#  MANAGER DASHBOARD
# ════════════════════════════════════════════════════════════════════
@login_required
@role_required(["Manager"])
def manager_dashboard_api(request):
    today = date.today()
    current_year = timezone.now().year

    team_members = User.objects.filter(
        reporting_manager=request.user
    ).select_related("role", "department")

    team_pending = (
        LeaveRequest.objects.filter(
            manager_voted=False,
            final_status="PENDING",
        )
        .filter(Q(employee__reporting_manager=request.user) | Q(approvers=request.user))
        .exclude(employee=request.user)
        .distinct()
        .select_related("employee", "employee__department")
        .order_by("-created_at")
    )

    team_on_leave = (
        LeaveRequest.objects.filter(
            status="APPROVED",
            start_date__lte=today,
            end_date__gte=today,
        )
        .filter(Q(employee__reporting_manager=request.user) | Q(approvers=request.user))
        .distinct()
        .select_related("employee")
    )

    team_history_qs = (
        LeaveRequest.objects.filter(start_date__year=current_year)
        .filter(Q(employee__reporting_manager=request.user) | Q(approvers=request.user))
        .distinct()
        .select_related("employee")
        .order_by("-created_at")
    )

    my_leaves_qs = LeaveRequest.objects.filter(employee=request.user).order_by("-created_at")

    leave_type_display_map = {}
    active_leave_types = []
    if POLICY_ENABLED:
        active_leave_type_qs = LeaveTypeConfig.objects.filter(is_active=True).order_by("name")
        active_leave_types = [
            {"id": lt.id, "code": lt.code, "name": lt.name, "color": lt.color}
            for lt in active_leave_type_qs
        ]
        for lt in active_leave_type_qs:
            leave_type_display_map[lt.code.upper()] = lt.name
            leave_type_display_map[lt.name.upper()] = lt.name

    def _serialize_leave_with_employee(leave):
        data = _serialize_leave(leave)
        data["employee"] = _serialize_user(leave.employee)
        leave_type_key = (leave.leave_type or "").strip().upper()
        data["leave_type_display"] = leave_type_display_map.get(leave_type_key, leave.leave_type or "—")
        return data

    # Check for AJAX request
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    
    # Pending — paginated
    pending_page = _paginate(
        [_serialize_leave_with_employee(l) for l in team_pending],
        request,
        "page",
        8,
    )
    page_obj_pending = pending_page.pop("_page_obj") if "_page_obj" in pending_page else None

    history_page = _paginate(
        [_serialize_leave_with_employee(l) for l in team_history_qs],
        request,
        "hpage",
        10,
    )
    page_obj_history = history_page.pop("_page_obj") if "_page_obj" in history_page else None

    my_leaves_page = _paginate(
        [_serialize_leave_with_employee(l) for l in my_leaves_qs], request, "mypage", 10
    )
    page_obj_myleaves = my_leaves_page.pop("_page_obj") if "_page_obj" in my_leaves_page else None

    team_data = []
    for member in team_members:
        member_leaves = LeaveRequest.objects.filter(
            employee=member, start_date__year=current_year
        )
        summary = get_employee_leave_summary(member, current_year)
        breakdown = summary.get("breakdown", [])
        casual_balance = 0
        sick_balance = 0
        for item in breakdown:
            if item.get("code") == "CASUAL":
                casual_balance = item.get("remaining", 0)
            elif item.get("code") == "SICK":
                sick_balance = item.get("remaining", 0)
        team_data.append(
            {
                "member": _serialize_user(member),
                "total_leaves": member_leaves.count(),
                "approved": member_leaves.filter(status="APPROVED").count(),
                "pending": member_leaves.filter(status="PENDING").count(),
                "leave_summary": summary,
                "is_on_leave": team_on_leave.filter(employee=member).exists(),
                "casual_balance": casual_balance,
                "sick_balance": sick_balance,
            }
        )

    my_leave_summary = get_employee_leave_summary(request.user, current_year)

    unread = Notification.objects.filter(user=request.user, read_status=False).count()

    # Handle AJAX requests for pagination
    if is_ajax:
        partial_type = request.GET.get('partial')
        
        if partial_type == 'pending' and page_obj_pending:
            html = render_to_string('partials/mgr_pending_leaves.html', {
                'pending_page': page_obj_pending,
            }, request=request)
            return JsonResponse({'success': True, 'html': html})
        
        elif partial_type == 'history' and page_obj_history:
            html = render_to_string('partials/mgr_team_history.html', {
                'team_history_page': page_obj_history,
                'current_year': current_year,
            }, request=request)
            return JsonResponse({'success': True, 'html': html})
        
        elif partial_type == 'myleaves' and page_obj_myleaves:
            html = render_to_string('partials/mgr_my_leaves.html', {
                'my_leaves_page': page_obj_myleaves,
            }, request=request)
            return JsonResponse({'success': True, 'html': html})

    # Return JSON for regular API calls
    return _ok(
        {
            "user": _serialize_user(request.user),
            "pending": pending_page,
            "pending_count": team_pending.count(),
            "team_count": team_members.count(),
            "team_data": team_data,
            "team_on_leave": [_serialize_user(l.employee) for l in team_on_leave],
            "team_on_leave_count": team_on_leave.count(),
            "history": history_page,
            "my_leaves": my_leaves_page,
            "my_leave_summary": my_leave_summary,
            "active_leave_types": active_leave_types,
            "unread_count": unread,
            "current_year": current_year,
        }
    )

# ════════════════════════════════════════════════════════════════════
#  NOTIFICATIONS
# ════════════════════════════════════════════════════════════════════

@login_required
def notifications_api(request):
    notes = Notification.objects.filter(user=request.user).order_by("-created_at")
    notes.filter(read_status=False).update(read_status=True)

    page_data = _paginate(
        [
            {
                "id": n.id,
                "message": n.message,
                "link": n.link,
                "read_status": n.read_status,
                "created_at": n.created_at.isoformat(),
            }
            for n in notes
        ],
        request,
        per_page=20,
    )
    page_data.pop("_page_obj")
    return _ok(page_data)


# ════════════════════════════════════════════════════════════════════
#  EMPLOYEE DETAIL
# ════════════════════════════════════════════════════════════════════

@login_required
def employee_detail_api(request, pk):
    role = get_user_role(request.user)
    if not (
        request.user.is_superuser
        or role in ("HR", "Admin", "Manager", "TL")
        or request.user.pk == pk
    ):
        return _forbidden()

    employee = get_object_or_404(User, pk=pk)
    current_year = timezone.now().year
    leave_summary = get_employee_leave_summary(employee, current_year)
    leaves = LeaveRequest.objects.filter(employee=employee).order_by("-created_at")

    return _ok(
        {
            "employee": _serialize_user(employee),
            "leave_summary": leave_summary,
            "recent_leaves": [_serialize_leave(l) for l in leaves[:10]],
            "leave_counts": {
                "total": leaves.count(),
                "approved": leaves.filter(status="APPROVED").count(),
                "rejected": leaves.filter(status="REJECTED").count(),
                "pending": leaves.filter(status="PENDING").count(),
            },
        }
    )


# ════════════════════════════════════════════════════════════════════
#  CREATE EMPLOYEE
# ════════════════════════════════════════════════════════════════════

@login_required
def create_employee_api(request):
    if not (request.user.is_superuser or user_has_permission(request.user, "user_create")):
        return _forbidden("You don't have permission to create employees.")
    if request.method != "POST":
        return _err("Method not allowed.", status=405)

    username = request.POST.get("username")
    email = request.POST.get("email")
    password = request.POST.get("password")

    if User.objects.filter(username=username).exists():
        return _err("Username already exists.", status=409)

    dept_id = request.POST.get("department_id")
    manager_email = request.POST.get("reporting_manager_email")
    role_id = request.POST.get("role_id")

    manager_user = User.objects.filter(email=manager_email).first() if manager_email else None
    dept_obj = None
    if dept_id:
        try:
            dept_obj = Department.objects.get(id=dept_id)
        except Department.DoesNotExist:
            pass

    try:
        employee_role = (
            Role.objects.get(id=role_id) if role_id else Role.objects.get(name="Employee")
        )
    except Role.DoesNotExist:
        employee_role = None

    new_emp = User.objects.create_user(
        username=username,
        email=email,
        password=password,
        first_name=request.POST.get("first_name", ""),
        last_name=request.POST.get("last_name", ""),
        designation=request.POST.get("designation", "").strip() or None,
        phone=request.POST.get("phone", "").strip() or None,
        role=employee_role,
        reporting_manager=manager_user,
        department=dept_obj,
    )

    if POLICY_ENABLED:
        current_year = timezone.now().year
        as_of_date = timezone.now().date()
        leave_types = list(LeaveTypeConfig.objects.filter(is_active=True))
        if leave_types:
            EmployeeLeaveAllocation.objects.bulk_create(
                [
                    EmployeeLeaveAllocation(
                        employee=new_emp,
                        leave_type=lt,
                        year=current_year,
                        allocated_days=(
                            min(float(lt.days_per_year or 0), float(lt.monthly_accrual or 0) * as_of_date.month)
                            if lt.is_accrual_based
                            else float(lt.days_per_year or 0)
                        ),
                    )
                    for lt in leave_types
                ]
            )

    return _ok(
        {"message": "Employee created successfully.", "employee": _serialize_user(new_emp)},
        status=201,
    )


# ════════════════════════════════════════════════════════════════════
#  TOGGLE EMPLOYEE STATUS
# ════════════════════════════════════════════════════════════════════

@login_required
def toggle_employee_status_api(request, user_id):
    if request.method != "POST":
        return _err("Method not allowed.", status=405)

    employee = get_object_or_404(User, id=user_id)
    if employee.is_active:
        if not (request.user.is_superuser or user_has_permission(request.user, "user_deactivate")):
            return _forbidden("You don't have permission to deactivate employees.")
    else:
        if not (request.user.is_superuser or user_has_permission(request.user, "user_activate")):
            return _forbidden("You don't have permission to activate employees.")
    if employee.pk == request.user.pk:
        return _err("You cannot change your own active status from this screen.", status=400)
    employee.is_active = not employee.is_active
    employee.save()
    return _ok(
        {
            "message": f"Status updated for {employee.get_full_name() or employee.username}.",
            "is_active": employee.is_active,
        }
    )


@login_required
def update_employee_api(request, pk):
    if not (request.user.is_superuser or user_has_permission(request.user, "user_update")):
        return _forbidden("You don't have permission to update employees.")
    if request.method != "POST":
        return _err("Method not allowed.", status=405)

    employee = get_object_or_404(User, pk=pk, is_superuser=False)
    employee.first_name = request.POST.get("first_name", employee.first_name).strip()
    employee.last_name = request.POST.get("last_name", employee.last_name).strip()

    new_email = request.POST.get("email", employee.email).strip()
    if new_email and User.objects.exclude(pk=employee.pk).filter(email=new_email).exists():
        return _err("Email already exists.", status=409)
    employee.email = new_email

    new_username = request.POST.get("username", employee.username).strip()
    if new_username and User.objects.exclude(pk=employee.pk).filter(username=new_username).exists():
        return _err("Username already exists.", status=409)
    employee.username = new_username

    employee.designation = request.POST.get("designation", employee.designation or "").strip() or None
    employee.phone = request.POST.get("phone", employee.phone or "").strip() or None

    dept_id = request.POST.get("department_id", "").strip()
    role_id = request.POST.get("role_id", "").strip()
    reporting_manager_id = request.POST.get("reporting_manager_id", "").strip()

    employee.department = Department.objects.filter(pk=dept_id).first() if dept_id else None
    selected_role = Role.objects.filter(pk=role_id).first() if role_id else None
    if selected_role and selected_role.name != "Admin":
        employee.role = selected_role
    employee.reporting_manager = User.objects.filter(pk=reporting_manager_id, is_superuser=False).exclude(pk=employee.pk).first() if reporting_manager_id else None

    password = request.POST.get("password", "").strip()
    if password:
        employee.set_password(password)

    employee.save()
    return _ok({"message": "Employee updated successfully.", "employee": _serialize_user(employee)})


@login_required
def delete_employee_api(request, pk):
    if not (request.user.is_superuser or user_has_permission(request.user, "user_delete")):
        return _forbidden("You don't have permission to delete employees.")
    if request.method != "POST":
        return _err("Method not allowed.", status=405)

    employee = get_object_or_404(User, pk=pk, is_superuser=False)
    if employee.pk == request.user.pk:
        return _err("You cannot delete your own account.", status=400)
    if employee.role and employee.role.name == "Admin":
        return _err("Admin users cannot be deleted from this screen.", status=400)

    employee_name = employee.get_full_name() or employee.username or employee.email
    employee.delete()
    return _ok({"message": f"{employee_name} deleted successfully."})


# ════════════════════════════════════════════════════════════════════
#  ADMIN — LEAVE POLICY (overview)
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["Admin"])
def admin_leave_policy_api(request):
    if not POLICY_ENABLED:
        return _err("Run migrations first: python manage.py migrate", status=503)

    current_year = timezone.now().year
    leave_types = LeaveTypeConfig.objects.all().order_by("-is_active", "name")
    policies = LeavePolicy.objects.all().order_by("-is_default", "name")

    lt_stats = []
    for lt in leave_types:
        allocs = EmployeeLeaveAllocation.objects.filter(leave_type=lt, year=current_year)
        lt_stats.append(
            {
                "id": lt.id,
                "code": lt.code,
                "name": lt.name,
                "description": lt.description,
                "color": lt.color,
                "is_paid": lt.is_paid,
                "is_accrual_based": lt.is_accrual_based,
                "monthly_accrual": float(lt.monthly_accrual or 0),
                "starting_month": int(getattr(lt, "starting_month", 4) or 4),
                "starting_month_name": month_name[int(getattr(lt, "starting_month", 4) or 4)],
                "days_per_year": lt.days_per_year,
                "current_month_entitlement": _target_allocation_days_for_leave_type(lt, sync_mode="monthly"),
                "is_active": lt.is_active,
                "max_consecutive_days": lt.max_consecutive_days,
                "advance_notice_days": lt.advance_notice_days,
                "document_required_after": lt.document_required_after,
                "carry_forward": lt.carry_forward,
                "carry_forward_limit": lt.carry_forward_limit,
                "applicable_to": lt.applicable_to,
                "employees_covered": allocs.count(),
                "total_used": float(sum(a.used_days for a in allocs)),
            }
        )

    return _ok(
        {
            "leave_types": lt_stats,
            "policies": [
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "max_days_per_request": p.max_days_per_request,
                    "min_advance_days": p.min_advance_days,
                    "weekend_counts_as_leave": p.weekend_counts_as_leave,
                    "holiday_counts_as_leave": p.holiday_counts_as_leave,
                    "allow_half_day": p.allow_half_day,
                    "allow_short_leave": p.allow_short_leave,
                    "approval_threshold": p.approval_threshold,
                    "is_default": p.is_default,
                    "is_active": p.is_active,
                }
                for p in policies
            ],
            "stats": {
                "total_employees": User.objects.filter(is_active=True)
                .exclude(is_superuser=True)
                .count(),
                "total_leave_types": leave_types.filter(is_active=True).count(),
                "total_policies": policies.filter(is_active=True).count(),
            },
            "current_year": current_year,
        }
    )


# ════════════════════════════════════════════════════════════════════
#  UNIFIED LEAVE POLICY API — All operations in JSON
# ════════════════════════════════════════════════════════════════════

@csrf_exempt
@login_required
def leave_policy_unified_api(request):
    """
    Unified endpoint for leave policy management.
    
    GET:
      - Returns HTML template (default) with embedded API data
      - Returns JSON if ?format=json or Accept: application/json
      - Requires: Admin role
    
    POST actions:
      - action=apply_leave        → Apply for leave (any user)
      - action=create_leave_type  → Create/update leave type (admin only)
      - action=add_policy         → Create/update policy (admin only)
      - action=toggle_leave_type  → Toggle leave type (admin only)
      - action=delete_leave_type  → Delete leave type (admin only)
      - action=toggle_policy      → Toggle policy (admin only)
      - action=delete_policy      → Delete policy (admin only)
      - action=sync_allocations   → Sync to all employees (admin only)
    """
    
    if request.method == "GET":
        # GET requires Admin role
        role = get_user_role(request.user)
        if not (request.user.is_superuser or role == "Admin"):
            return _forbidden("Requires Admin role to view leave policy.")
        
        # Check if user wants JSON response
        wants_json = (
            request.GET.get("format") == "json"
            or "application/json" in request.headers.get("Accept", "")
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        
        # Get the data directly
        if not POLICY_ENABLED:
            api_data = {"error": "Run migrations first: python manage.py migrate"}
        else:
            current_year = timezone.now().year
            leave_types = LeaveTypeConfig.objects.all().order_by("-is_active", "name")
            policies = LeavePolicy.objects.all().order_by("-is_default", "name")

            lt_stats = []
            for lt in leave_types:
                allocs = EmployeeLeaveAllocation.objects.filter(leave_type=lt, year=current_year)
                lt_stats.append({
                    "id": lt.id,
                    "code": lt.code,
                    "name": lt.name,
                    "description": lt.description,
                    "color": lt.color,
                    "is_paid": lt.is_paid,
                    "is_accrual_based": lt.is_accrual_based,
                    "monthly_accrual": float(lt.monthly_accrual or 0),
                    "starting_month": int(getattr(lt, "starting_month", 4) or 4),
                    "starting_month_name": month_name[int(getattr(lt, "starting_month", 4) or 4)],
                    "days_per_year": lt.days_per_year,
                    "current_month_entitlement": _target_allocation_days_for_leave_type(lt, sync_mode="monthly"),
                    "is_active": lt.is_active,
                    "max_consecutive_days": lt.max_consecutive_days,
                    "advance_notice_days": lt.advance_notice_days,
                    "document_required_after": lt.document_required_after,
                    "carry_forward": lt.carry_forward,
                    "carry_forward_limit": lt.carry_forward_limit,
                    "applicable_to": lt.applicable_to,
                    "employees_covered": allocs.count(),
                    "total_used": float(sum(a.used_days for a in allocs)),
                })

            api_data = {
                "success": True,
                "leave_types": lt_stats,
                "policies": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "description": p.description,
                        "max_days_per_request": p.max_days_per_request,
                        "min_advance_days": p.min_advance_days,
                        "weekend_counts_as_leave": p.weekend_counts_as_leave,
                        "holiday_counts_as_leave": p.holiday_counts_as_leave,
                        "allow_half_day": p.allow_half_day,
                        "allow_short_leave": p.allow_short_leave,
                        "approval_threshold": p.approval_threshold,
                        "is_default": p.is_default,
                        "is_active": p.is_active,
                    }
                    for p in policies
                ],
                "stats": {
                    "total_employees": User.objects.filter(is_active=True).exclude(is_superuser=True).count(),
                    "total_leave_types": leave_types.filter(is_active=True).count(),
                    "total_policies": policies.filter(is_active=True).count(),
                },
                "current_year": current_year,
            }
        
        # If JSON is requested, return it
        if wants_json:
            return JsonResponse(api_data)
        
        # Otherwise, render HTML template with the data
        context = {
            "profile": _build_profile_context(request.user),
            "leave_types": api_data.get("leave_types", []),
            "policies": api_data.get("policies", []),
            "stats": api_data.get("stats", {}),
            "current_year": api_data.get("current_year", timezone.now().year),
            "api_data_json": json.dumps(api_data),
        }
        return render(request, "admin_leave_policy.html", context)
    
    if request.method != "POST":
        return _err("Method not allowed. Use GET or POST.", status=405)
    
    action = request.POST.get("action", "").lower().strip()
    
    # ── APPLY LEAVE (any user) ─────────────────────────────────────────
    if action == "apply_leave":
        return apply_leave_api(request)
    
    # ── ADMIN-ONLY ACTIONS ─────────────────────────────────────────────
    role = get_user_role(request.user)
    if not (request.user.is_superuser or role == "Admin"):
        return _forbidden("This action requires Admin role.")
    
    # ── CREATE/UPDATE LEAVE TYPE ───────────────────────────────────────
    if action == "create_leave_type":
        return admin_leave_type_save_api(request)
    
    # ── CREATE/UPDATE POLICY ───────────────────────────────────────────
    elif action == "add_policy":
        return admin_policy_save_api(request)
    
    # ── TOGGLE LEAVE TYPE ──────────────────────────────────────────────
    elif action == "toggle_leave_type":
        lt_id = request.POST.get("lt_id")
        if not lt_id:
            return _err("Missing 'lt_id' parameter.", status=400)
        return admin_leave_type_toggle_api(request, int(lt_id))
    
    # ── DELETE LEAVE TYPE ──────────────────────────────────────────────
    elif action == "delete_leave_type":
        lt_id = request.POST.get("lt_id")
        if not lt_id:
            return _err("Missing 'lt_id' parameter.", status=400)
        return admin_leave_type_delete_api(request, int(lt_id))
    
    # ── TOGGLE POLICY ──────────────────────────────────────────────────
    elif action == "toggle_policy":
        policy_id = request.POST.get("policy_id")
        if not policy_id:
            return _err("Missing 'policy_id' parameter.", status=400)
        return admin_policy_toggle_api(request, int(policy_id))
    
    # ── DELETE POLICY ──────────────────────────────────────────────────
    elif action == "delete_policy":
        policy_id = request.POST.get("policy_id")
        if not policy_id:
            return _err("Missing 'policy_id' parameter.", status=400)
        return admin_policy_delete_api(request, int(policy_id))
    
    # ── SYNC ALLOCATIONS TO ALL EMPLOYEES ──────────────────────────────
    elif action == "sync_allocations":
        return admin_apply_to_all_employees_api(request)
    
    else:
        return _err(
            f"Unknown action '{action}'. Valid actions: apply_leave (any user), "
            "create_leave_type, add_policy, toggle_leave_type, delete_leave_type, "
            "toggle_policy, delete_policy, sync_allocations (admin only)",
            status=400,
        )



# ════════════════════════════════════════════════════════════════════
#  ADMIN — SAVE LEAVE TYPE (create or update)
# ════════════════════════════════════════════════════════════════════

@csrf_exempt
@login_required
@role_required(["Admin"])
def admin_leave_type_save_api(request):
    if request.method != "POST":
        return _err("Method not allowed.", status=405)
    if not POLICY_ENABLED:
        return _err("Run migrations first.", status=503)

    lt_id = request.POST.get("lt_id")
    code = request.POST.get("code", "").upper().strip()
    name = request.POST.get("name", "").strip()
    apply_to_all = request.POST.get("apply_to_all") == "on"
    update_existing = request.POST.get("update_existing") == "on"

    if lt_id:
        lt = get_object_or_404(LeaveTypeConfig, id=lt_id)
        lt.name = name
        lt.description = request.POST.get("description", "")
        lt.days_per_year = float(request.POST.get("days_per_year", lt.days_per_year))
        lt.is_paid = request.POST.get("is_paid") == "on"
        lt.is_accrual_based = request.POST.get("is_accrual_based") == "on"
        lt.monthly_accrual = float(request.POST.get("monthly_accrual", lt.monthly_accrual))
        lt.starting_month = int(request.POST.get("starting_month", getattr(lt, "starting_month", 4) or 4))
        lt.max_consecutive_days = int(request.POST.get("max_consecutive_days", 0))
        lt.advance_notice_days = int(request.POST.get("advance_notice_days", 0))
        lt.document_required_after = int(request.POST.get("document_required_after", 0))
        lt.carry_forward = request.POST.get("carry_forward") == "on"
        lt.carry_forward_limit = float(request.POST.get("carry_forward_limit", 0))
        lt.color = request.POST.get("color", lt.color)
        lt.applicable_to = request.POST.get("applicable_to", lt.applicable_to)
        lt.is_active = request.POST.get("is_active") == "on"
        lt.save()
        action = "updated"

        if update_existing:
            target_days = _target_allocation_days_for_leave_type(lt, sync_mode="monthly")
            n = EmployeeLeaveAllocation.objects.filter(
                leave_type=lt, year=lt.get_current_leave_year()
            ).update(allocated_days=target_days)
            extra_msg = f"{n} employee allocation(s) refreshed."
        else:
            extra_msg = ""
    else:
        if LeaveTypeConfig.objects.filter(code=code).exists():
            return _err(f"Leave type with code '{code}' already exists.", status=409)

        lt = LeaveTypeConfig.objects.create(
            code=code,
            name=name,
            description=request.POST.get("description", ""),
            days_per_year=float(request.POST.get("days_per_year", 12)),
            is_paid=request.POST.get("is_paid") == "on",
            is_accrual_based=request.POST.get("is_accrual_based") == "on",
            monthly_accrual=float(request.POST.get("monthly_accrual", 1.0)),
            starting_month=int(request.POST.get("starting_month", 4)),
            max_consecutive_days=int(request.POST.get("max_consecutive_days", 0)),
            advance_notice_days=int(request.POST.get("advance_notice_days", 0)),
            document_required_after=int(request.POST.get("document_required_after", 0)),
            carry_forward=request.POST.get("carry_forward") == "on",
            carry_forward_limit=float(request.POST.get("carry_forward_limit", 0)),
            color=request.POST.get("color", "#00c6d4"),
            applicable_to=request.POST.get("applicable_to", "ALL"),
            is_active=request.POST.get("is_active") == "on",
            created_by=request.user,
        )
        action = "created"
        extra_msg = ""

    allocation_result = {}
    if apply_to_all:
        created, updated = _apply_leave_type_to_all_employees(lt, update_existing=update_existing)
        allocation_result = {"allocations_created": created, "allocations_updated": updated}

    return _ok(
        {
            "message": f"Leave type '{name}' {action} successfully. {extra_msg}".strip(),
            "leave_type": {"id": lt.id, "code": lt.code, "name": lt.name},
            **allocation_result,
        }
    )


# ════════════════════════════════════════════════════════════════════
#  ADMIN — TOGGLE LEAVE TYPE
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["Admin"])
def admin_leave_type_toggle_api(request, lt_id):
    if not POLICY_ENABLED:
        return _err("Run migrations first.", status=503)
    lt = get_object_or_404(LeaveTypeConfig, id=lt_id)
    lt.is_active = not lt.is_active
    lt.save()
    return _ok(
        {
            "message": f"Leave type '{lt.name}' {'activated' if lt.is_active else 'deactivated'}.",
            "is_active": lt.is_active,
        }
    )


# ════════════════════════════════════════════════════════════════════
#  ADMIN — DELETE LEAVE TYPE
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["Admin"])
def admin_leave_type_delete_api(request, lt_id):
    if request.method != "POST":
        return _err("Method not allowed.", status=405)
    if not POLICY_ENABLED:
        return _err("Run migrations first.", status=503)

    lt = get_object_or_404(LeaveTypeConfig, id=lt_id)
    used_days_total = (
        EmployeeLeaveAllocation.objects.filter(leave_type=lt).aggregate(total=Sum("used_days"))[
            "total"
        ] or 0
    )
    if used_days_total > 0:
        return _err(
            f"Cannot delete '{lt.name}' — employees have already used {used_days_total} day(s). "
            "Deactivate it instead to preserve history.",
            status=409,
        )

    EmployeeLeaveAllocation.objects.filter(leave_type=lt).delete()
    name = lt.name
    lt.delete()
    return _ok({"message": f"Leave type '{name}' deleted successfully."})


# ════════════════════════════════════════════════════════════════════
#  ADMIN — SAVE POLICY
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["Admin"])
def admin_policy_save_api(request):
    if request.method != "POST":
        return _err("Method not allowed.", status=405)
    if not POLICY_ENABLED:
        return _err("Run migrations first.", status=503)

    policy_id = request.POST.get("policy_id")
    if policy_id:
        policy = get_object_or_404(LeavePolicy, id=policy_id)
        action = "updated"
    else:
        policy = LeavePolicy(created_by=request.user)
        action = "created"

    policy.name = request.POST.get("name", "").strip()
    policy.description = request.POST.get("description", "")
    policy.max_days_per_request = int(request.POST.get("max_days_per_request", 5))
    policy.min_advance_days = int(request.POST.get("min_advance_days", 1))
    policy.weekend_counts_as_leave = request.POST.get("weekend_counts_as_leave") == "on"
    policy.holiday_counts_as_leave = request.POST.get("holiday_counts_as_leave") == "on"
    policy.allow_half_day = request.POST.get("allow_half_day") == "on"
    policy.allow_short_leave = request.POST.get("allow_short_leave") == "on"
    policy.approval_threshold = int(request.POST.get("approval_threshold", 2))
    policy.is_default = request.POST.get("is_default") == "on"
    policy.is_active = request.POST.get("is_active") == "on"
    policy.save()

    return _ok({"message": f"Policy '{policy.name}' {action} successfully.", "policy_id": policy.id})


# ════════════════════════════════════════════════════════════════════
#  ADMIN — TOGGLE POLICY
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["Admin"])
def admin_policy_toggle_api(request, policy_id):
    if not POLICY_ENABLED:
        return _err("Run migrations first.", status=503)

    policy = get_object_or_404(LeavePolicy, id=policy_id)
    policy.is_active = not policy.is_active
    policy.save()
    return _ok(
        {
            "message": f"Policy '{policy.name}' {'activated' if policy.is_active else 'deactivated'}.",
            "is_active": policy.is_active,
        }
    )


# ════════════════════════════════════════════════════════════════════
#  ADMIN — DELETE POLICY
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["Admin"])
def admin_policy_delete_api(request, policy_id):
    if request.method != "POST":
        return _err("Method not allowed.", status=405)
    if not POLICY_ENABLED:
        return _err("Run migrations first.", status=503)

    policy = get_object_or_404(LeavePolicy, id=policy_id)
    if policy.is_default:
        other_active = (
            LeavePolicy.objects.filter(is_default=True, is_active=True).exclude(id=policy_id).count()
        )
        if other_active == 0:
            return _err(
                f"Cannot delete '{policy.name}' — it is the only active default policy. "
                "Set another policy as default first.",
                status=409,
            )

    name = policy.name
    policy.delete()
    return _ok({"message": f"Policy '{name}' deleted successfully."})


# ════════════════════════════════════════════════════════════════════
#  ADMIN — SYNC ALLOCATIONS TO ALL EMPLOYEES
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["Admin"])
def admin_apply_to_all_employees_api(request):
    if request.method != "POST":
        return _err("Method not allowed.", status=405)
    if not POLICY_ENABLED:
        return _err("Run migrations first.", status=503)

    force_update = request.POST.get("force_update") == "on"
    sync_mode = request.POST.get("sync_mode", "monthly").strip().lower() or "monthly"
    if sync_mode not in ("monthly", "full_year"):
        return _err("Invalid sync_mode. Use 'monthly' or 'full_year'.", status=400)
    
    # For each leave type, use its own starting_month to determine the year
    total_created = total_updated = 0
    
    for lt in LeaveTypeConfig.objects.filter(is_active=True):
        # Determine the current leave year for this leave type
        current_leave_year = lt.get_current_leave_year()
        
        c, u = _apply_leave_type_to_all_employees(
            lt,
            year=current_leave_year,
            update_existing=force_update,
            sync_mode=sync_mode,
        )
        total_created += c
        total_updated += u

    return _ok(
        {
            "message": (
                f"{sync_mode.replace('_', ' ').title()} sync complete! "
                f"{total_created} new allocations created, "
                f"{total_updated} existing allocations updated."
            ),
            "allocations_created": total_created,
            "allocations_updated": total_updated,
        }
    )
# ════════════════════════════════════════════════════════════════════
#  HOLIDAYS — LIST
# ════════════════════════════════════════════════════════════════════

@login_required
def holiday_list_api(request):
    if not HOLIDAYS_ENABLED:
        return _err("Holiday module not available.", status=503)

    year = int(request.GET.get("year", datetime.now().year))
    month = request.GET.get("month", "")
    holiday_type = request.GET.get("type", "")
    search = request.GET.get("search", "")

    holidays = Holiday.objects.filter(date__year=year)
    if month and month.isdigit():
        holidays = holidays.filter(date__month=int(month))
    if holiday_type:
        holidays = holidays.filter(holiday_type=holiday_type)
    if search:
        holidays = holidays.filter(Q(name__icontains=search) | Q(description__icontains=search))

    today = date.today()
    upcoming = [
        {"id": h.id, "name": h.name, "date": str(h.date), "holiday_type": h.holiday_type}
        for h in Holiday.objects.filter(date__gte=today, is_active=True).order_by("date")[:5]
    ]

    calendar_data = []
    for m in range(1, 13):
        mh = holidays.filter(date__month=m)
        if mh.exists():
            calendar_data.append(
                {
                    "month": m,
                    "month_name": month_name[m],
                    "count": mh.count(),
                    "holidays": [
                        {
                            "id": h.id,
                            "name": h.name,
                            "date": str(h.date),
                            "holiday_type": h.holiday_type,
                            "is_half_day": h.is_half_day,
                            "is_active": h.is_active,
                        }
                        for h in mh
                    ],
                }
            )

    return _ok(
        {
            "current_year": year,
            "total_holidays": holidays.count(),
            "upcoming": upcoming,
            "calendar": calendar_data,
            "holidays": [
                {
                    "id": h.id,
                    "name": h.name,
                    "date": str(h.date),
                    "holiday_type": h.holiday_type,
                    "is_half_day": h.is_half_day,
                    "is_recurring": h.is_recurring,
                    "is_active": h.is_active,
                    "description": h.description,
                }
                for h in holidays.order_by("date")
            ],
        }
    )


# ════════════════════════════════════════════════════════════════════
#  HOLIDAYS — CREATE
# ════════════════════════════════════════════════════════════════════

@csrf_exempt
@login_required
@role_required(["HR", "Admin"])
def holiday_create_api(request):
    if request.method != "POST":
        return _err("Method not allowed.", status=405)
    if not HOLIDAYS_ENABLED:
        return _err("Holiday module not available.", status=503)

    name = request.POST.get("name")
    date_str = request.POST.get("date")
    end_date_str = request.POST.get("end_date") or date_str
    holiday_type = request.POST.get("holiday_type")
    is_half_day = request.POST.get("is_half_day") == "on"

    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return _err("Invalid date format. Use YYYY-MM-DD.")

    try:
        parsed_end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        parsed_end_date = parsed_date

    if Holiday.objects.filter(name=name, date=parsed_date).exists():
        return _err(f"Holiday '{name}' already exists on {date_str}.", status=409)

    h = Holiday.objects.create(
        name=name,
        description=request.POST.get("description", ""),
        holiday_type=holiday_type,
        date=parsed_date,
        end_date=parsed_end_date,
        is_recurring=request.POST.get("is_recurring") == "on",
        is_half_day=is_half_day,
        half_day_type=request.POST.get("half_day_type") if is_half_day else None,
        applicable_to_all=request.POST.get("applicable_to_all") == "on",
        created_by=request.user,
    )
    return _ok(
        {"message": f"Holiday '{name}' created successfully.", "holiday": {"id": h.id, "name": h.name, "date": str(h.date)}},
        status=201,
    )


# ════════════════════════════════════════════════════════════════════
#  HOLIDAYS — DETAIL
# ════════════════════════════════════════════════════════════════════

@login_required
def holiday_detail_api(request, holiday_id):
    if not HOLIDAYS_ENABLED:
        return _err("Holiday module not available.", status=503)
    h = get_object_or_404(Holiday, id=holiday_id)
    return _ok(
        {
            "id": h.id,
            "name": h.name,
            "description": h.description,
            "date": str(h.date),
            "end_date": str(h.end_date) if h.end_date else None,
            "holiday_type": h.holiday_type,
            "is_half_day": h.is_half_day,
            "half_day_type": h.half_day_type,
            "is_recurring": h.is_recurring,
            "applicable_to_all": h.applicable_to_all,
            "is_active": h.is_active,
            "duration": h.duration,
        }
    )


# ════════════════════════════════════════════════════════════════════
#  HOLIDAYS — EDIT
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["HR", "Admin"])
def holiday_edit_api(request, holiday_id):
    if request.method != "POST":
        return _err("Method not allowed.", status=405)
    if not HOLIDAYS_ENABLED:
        return _err("Holiday module not available.", status=503)

    holiday = get_object_or_404(Holiday, id=holiday_id)
    date_str = request.POST.get("date")
    end_date_str = request.POST.get("end_date") or date_str

    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return _err("Invalid date format.")

    try:
        parsed_end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        parsed_end_date = parsed_date

    holiday.name = request.POST.get("name")
    holiday.description = request.POST.get("description", "")
    holiday.holiday_type = request.POST.get("holiday_type")
    holiday.date = parsed_date
    holiday.end_date = parsed_end_date
    holiday.is_recurring = request.POST.get("is_recurring") == "on"
    holiday.is_half_day = request.POST.get("is_half_day") == "on"
    holiday.half_day_type = request.POST.get("half_day_type") if holiday.is_half_day else None
    holiday.applicable_to_all = request.POST.get("applicable_to_all") == "on"
    holiday.is_active = request.POST.get("is_active") == "on"
    holiday.save()
    return _ok({"message": f"Holiday '{holiday.name}' updated successfully."})


# ════════════════════════════════════════════════════════════════════
#  HOLIDAYS — DELETE
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["HR", "Admin"])
def holiday_delete_api(request, holiday_id):
    if request.method != "POST":
        return _err("Method not allowed.", status=405)
    if not HOLIDAYS_ENABLED:
        return _err("Holiday module not available.", status=503)

    holiday = get_object_or_404(Holiday, id=holiday_id)
    name = holiday.name
    holiday.delete()
    return _ok({"message": f"Holiday '{name}' deleted successfully."})


# ════════════════════════════════════════════════════════════════════
#  HOLIDAYS — TOGGLE STATUS
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["HR", "Admin"])
def holiday_toggle_status_api(request, holiday_id):
    if not HOLIDAYS_ENABLED:
        return _err("Holiday module not available.", status=503)

    holiday = get_object_or_404(Holiday, id=holiday_id)
    holiday.is_active = not holiday.is_active
    holiday.save()
    status_word = "activated" if holiday.is_active else "deactivated"
    return _ok({"message": f"Holiday '{holiday.name}' {status_word}.", "is_active": holiday.is_active})


# ════════════════════════════════════════════════════════════════════
#  HOLIDAYS — BULK CREATE
# ════════════════════════════════════════════════════════════════════

@login_required
@role_required(["HR", "Admin"])
def holiday_bulk_create_api(request):
    if request.method != "POST":
        return _err("Method not allowed.", status=405)
    if not HOLIDAYS_ENABLED:
        return _err("Holiday module not available.", status=503)

    year = int(request.POST.get("year", datetime.now().year))
    holidays_text = request.POST.get("holidays_text", "")
    created = skipped = 0
    errors = []

    for line in holidays_text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) >= 2:
            name = parts[0].strip()
            date_str = parts[1].strip()
            h_type = parts[2].strip() if len(parts) > 2 else "NATIONAL"
            try:
                parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                if not Holiday.objects.filter(name=name, date=parsed_date).exists():
                    Holiday.objects.create(
                        name=name,
                        holiday_type=h_type,
                        date=parsed_date,
                        created_by=request.user,
                        is_recurring=True,
                    )
                    created += 1
                else:
                    skipped += 1
            except Exception as e:
                errors.append(f"Error on line '{line}': {e}")

    return _ok(
        {
            "message": f"Created {created} holidays. Skipped {skipped} duplicates.",
            "created": created,
            "skipped": skipped,
            "errors": errors[:5],
        }
    )


# ════════════════════════════════════════════════════════════════════
#  HOLIDAYS — PUBLIC (calendar grid, no auth required)
# ════════════════════════════════════════════════════════════════════

def public_holidays_api(request):
    if not HOLIDAYS_ENABLED:
        return _err("Holiday module not available.", status=503)

    year = int(request.GET.get("year", datetime.now().year))
    holidays = Holiday.objects.filter(is_active=True, date__year=year).order_by("date")
    today = date.today()

    upcoming = [
        {"id": h.id, "name": h.name, "date": str(h.date), "holiday_type": h.holiday_type}
        for h in Holiday.objects.filter(date__gte=today, is_active=True).order_by("date")[:10]
    ]

    # Calendar grid — only months that have holidays
    calendar_data = []
    for m in range(1, 13):
        mh = holidays.filter(date__month=m)
        if not mh.exists():
            continue
        cal = calendar.monthcalendar(year, m)
        weeks = []
        for week in cal:
            week_days = []
            for day in week:
                if day != 0:
                    d = date(year, m, day)
                    day_holidays = [
                        {"id": h.id, "name": h.name, "holiday_type": h.holiday_type}
                        for h in mh.filter(date=d)
                    ]
                    week_days.append(
                        {"date": str(d), "day": day, "is_holiday": bool(day_holidays), "holidays": day_holidays}
                    )
                else:
                    week_days.append({"day": 0, "is_holiday": False, "holidays": []})
            weeks.append(week_days)
        calendar_data.append(
            {"month": m, "month_name": month_name[m], "weeks": weeks, "count": mh.count()}
        )

    type_stats = list(
        holidays.values("holiday_type").annotate(count=Count("id")).order_by("-count")
    )

    return _ok(
        {
            "year": year,
            "prev_year": year - 1,
            "next_year": year + 1,
            "total_holidays": holidays.count(),
            "upcoming": upcoming,
            "calendar": calendar_data,
            "type_stats": type_stats,
        }
    )
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
    if not POLICY_ENABLED:
        return 0
    created_count = 0
    for lt in LeaveTypeConfig.objects.filter(is_active=True):
        target_year = year if year is not None else lt.get_current_leave_year()
        target_days = _target_allocation_days_for_leave_type(lt, sync_mode="monthly")
        _, created = EmployeeLeaveAllocation.objects.get_or_create(
            employee=employee, leave_type=lt, year=target_year,
            defaults={'allocated_days': target_days}
        )
        if created:
            created_count += 1
    return created_count

def _apply_leave_type_to_all_employees(leave_type_config, year=None, update_existing=False, sync_mode="monthly"):
    if year is None:
        year = leave_type_config.get_current_leave_year()
    
    employees = User.objects.filter(is_active=True).exclude(is_superuser=True)
    created = updated = 0

    target_days = _target_allocation_days_for_leave_type(leave_type_config, sync_mode=sync_mode)

    for emp in employees:
        alloc, was_created = EmployeeLeaveAllocation.objects.get_or_create(
            employee=emp, leave_type=leave_type_config, year=year,
            defaults={'allocated_days': target_days}
        )
        if was_created:
            created += 1

        should_update = update_existing
        if sync_mode == "monthly" and leave_type_config.is_accrual_based:
            should_update = should_update or float(alloc.allocated_days or 0) < target_days

        if should_update:
            alloc.allocated_days = target_days
            alloc.save(update_fields=['allocated_days', 'updated_at'])
            updated += 1
    return created, updated

def _render_template_page(request, template_name, extra_context=None):
    context = {"profile": _build_profile_context(request.user)} if request.user.is_authenticated else {}
    if request.user.is_authenticated:
        unread = Notification.objects.filter(user=request.user, read_status=False).count()
        context["unread_count"] = unread
        context["notification_count"] = unread
    if extra_context:
        context.update(extra_context)
    return render(request, template_name, context)


def _wants_json_response(request):
    return (
        request.GET.get("format") == "json"
        or "application/json" in request.headers.get("Accept", "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )


@login_required
def unified_dashboard(request):
    role = get_user_role(request.user)
    if role == "Admin":
        return admin_dashboard_page(request)
    if role == "HR":
        return hr_dashboard(request)
    if role == "Manager":
        return manager_dashboard(request)
    if role == "TL":
        return tl_dashboard(request)
    return employee_dashboard(request)


@login_required
def employee_dashboard(request):
    print(request)
    return _render_template_page(request, "employee_dashboard.html")


# @login_required
# def tl_dashboard(request):
#     return _render_template_page(request, "tl_dashboard.html")

@login_required
@role_required(["TL"])
def tl_dashboard(request, tab=None):
    """Unified TL Dashboard view"""
    today = date.today()
    current_year = timezone.now().year
    
    # Common data queries
    team_members = User.objects.filter(
        reporting_manager=request.user
    ).select_related("role", "department")
    
    all_pending = LeaveRequest.objects.filter(
        tl_voted=False,
        employee__reporting_manager=request.user,
    ).select_related("employee", "employee__department").order_by("-created_at")
    
    on_leave_today = LeaveRequest.objects.filter(
        status="APPROVED",
        employee__reporting_manager=request.user,
        start_date__lte=today,
        end_date__gte=today,
    ).select_related("employee")
    
    all_team = LeaveRequest.objects.filter(
        employee__reporting_manager=request.user,
        start_date__year=current_year,
    ).select_related("employee", "employee__department").order_by("-created_at")
    
    my_leaves_qs = LeaveRequest.objects.filter(
        employee=request.user
    ).order_by("-created_at")
    
    # Build team data
    team_data = []
    for member in team_members:
        ml = all_team.filter(employee=member)
        summary = get_employee_leave_summary(member, current_year)
        team_data.append({
            "member": member,
            "total_leaves": ml.count(),
            "approved": ml.filter(status="APPROVED").count(),
            "pending": ml.filter(status="PENDING").count(),
            "casual_balance": summary.get("casual_balance", 0),
            "sick_balance": summary.get("sick_balance", 0),
            "is_on_leave": on_leave_today.filter(employee=member).exists(),
        })
    
    # Pagination
    page = request.GET.get('page', 1)
    leaves = Paginator(all_pending, 8).get_page(page)  # ← Changed variable name to 'leaves'
    
    history_page = Paginator(all_team, 10).get_page(request.GET.get('hpage', 1))
    my_leaves_page = Paginator(my_leaves_qs, 10).get_page(request.GET.get('mypage', 1))
    
    # Check if AJAX request
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    
    if is_ajax:
        from django.template.loader import render_to_string
        
        # Get specific partial if requested
        partial = request.GET.get('partial')
        
        if partial == 'pending_leaves':
            # Pass 'leaves' to match your template
            html = render_to_string('partials/tl_pending_leaves.html', {
                'leaves': leaves  # ← Changed from 'pending_page' to 'leaves'
            }, request=request)
            return JsonResponse({
                'success': True,
                'html': html,
                'has_next': leaves.has_next(),
                'has_prev': leaves.has_previous(),
                'current_page': leaves.number,
                'total_pages': leaves.paginator.num_pages,
            })
        
        elif partial == 'my_leaves':
            html = render_to_string('partials/tl_my_leaves.html', {
                'my_leaves_page': my_leaves_page
            }, request=request)
            return JsonResponse({
                'success': True,
                'html': html,
            })
        
        elif partial == 'team_history':
            html = render_to_string('partials/tl_team_history.html', {
                'history_page': history_page
            }, request=request)
            return JsonResponse({
                'success': True,
                'html': html,
            })
        
        # Full dashboard JSON
        return JsonResponse({
            'success': True,
            'pending_count': all_pending.count(),
            'on_leave_count': on_leave_today.count(),
            'team_count': team_members.count(),
            'approved_count': all_team.filter(status="APPROVED").count(),
            'my_leave_count': my_leaves_qs.count(),
            'pending_html': render_to_string('partials/tl_pending_leaves.html', {
                'leaves': leaves  # ← Changed to 'leaves'
            }, request=request),
            'my_leaves_html': render_to_string('partials/tl_my_leaves.html', {
                'my_leaves_page': my_leaves_page
            }, request=request),
            'team_history_html': render_to_string('partials/tl_team_history.html', {
                'history_page': history_page
            }, request=request),
        })
    
    # Return HTML for normal request
    return render(request, "tl_dashboard.html", {
        "leaves": leaves,  # ← Pass 'leaves' to main template too
        "history_page": history_page,
        "my_leaves_page": my_leaves_page,
        "pending_count": all_pending.count(),
        "on_leave_today": on_leave_today,
        "on_leave_count": on_leave_today.count(),
        "team_count": team_members.count(),
        "approved_count": all_team.filter(status="APPROVED").count(),
        "my_leave_count": my_leaves_qs.count(),
        "team_data": team_data,
        "current_year": current_year,
    })

@login_required
def hr_dashboard(request):
    return _render_template_page(request, "hr_dashboard.html")


@login_required
@role_required(["Manager"])
def manager_dashboard(request):
    today = date.today()
    current_year = timezone.now().year

    team_members = User.objects.filter(
        reporting_manager=request.user
    ).select_related("role", "department")

    team_pending = (
        LeaveRequest.objects.filter(
            manager_voted=False,
            final_status="PENDING",
        )
        .filter(Q(employee__reporting_manager=request.user) | Q(approvers=request.user))
        .exclude(employee=request.user)
        .distinct()
        .select_related("employee", "employee__department")
        .order_by("-created_at")
    )

    team_on_leave = (
        LeaveRequest.objects.filter(
            status="APPROVED",
            start_date__lte=today,
            end_date__gte=today,
        )
        .filter(Q(employee__reporting_manager=request.user) | Q(approvers=request.user))
        .distinct()
        .select_related("employee")
    )

    team_history_qs = (
        LeaveRequest.objects.filter(start_date__year=current_year)
        .filter(Q(employee__reporting_manager=request.user) | Q(approvers=request.user))
        .distinct()
        .select_related("employee")
        .order_by("-created_at")
    )

    my_leaves_qs = LeaveRequest.objects.filter(employee=request.user).order_by("-created_at")

    pending_page = Paginator(team_pending, 8).get_page(request.GET.get("page", 1))
    team_history_page = Paginator(team_history_qs, 10).get_page(request.GET.get("hpage", 1))
    my_leaves_page = Paginator(my_leaves_qs, 10).get_page(request.GET.get("mypage", 1))

    team_data = []
    for member in team_members:
        member_leaves = LeaveRequest.objects.filter(employee=member, start_date__year=current_year)
        summary = get_employee_leave_summary(member, current_year)
        breakdown = summary.get("breakdown", [])
        casual_balance = 0
        sick_balance = 0
        for item in breakdown:
            if item.get("code") == "CASUAL":
                casual_balance = item.get("remaining", 0)
            elif item.get("code") == "SICK":
                sick_balance = item.get("remaining", 0)
        team_data.append(
            {
                "member": member,
                "total_leaves": member_leaves.count(),
                "approved": member_leaves.filter(status="APPROVED").count(),
                "pending": member_leaves.filter(status="PENDING").count(),
                "leave_summary": summary,
                "is_on_leave": team_on_leave.filter(employee=member).exists(),
                "casual_balance": casual_balance,
                "sick_balance": sick_balance,
            }
        )

    active_leave_types = []
    if POLICY_ENABLED:
        active_leave_types = [
            {"id": lt.id, "code": lt.code, "name": lt.name, "color": lt.color}
            for lt in LeaveTypeConfig.objects.filter(is_active=True).order_by("name")
        ]

    context = {
        "profile": _build_profile_context(request.user),
        "pending_count": team_pending.count(),
        "team_count": team_members.count(),
        "team_data": team_data,
        "team_on_leave": team_on_leave,
        "pending_page": pending_page,
        "team_history_page": team_history_page,
        "my_leaves_page": my_leaves_page,
        "current_year": current_year,
        "active_leave_types": active_leave_types,
    }
    return render(request, "manager_dashboard.html", context)


@login_required
def manager_pending_leaves(request):
    role_name = get_user_role(request.user)
    if role_name != "Manager" and not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("dashboard")

    search_query = request.GET.get("search", "").strip()

    pending_qs = (
        LeaveRequest.objects.filter(
            manager_voted=False,
            final_status="PENDING",
        )
        .filter(Q(employee__reporting_manager=request.user) | Q(approvers=request.user))
        .exclude(employee=request.user)
        .distinct()
        .select_related("employee", "employee__department")
        .order_by("-created_at")
    )

    if search_query:
        pending_qs = pending_qs.filter(
            Q(employee__first_name__icontains=search_query)
            | Q(employee__last_name__icontains=search_query)
            | Q(employee__email__icontains=search_query)
            | Q(employee__department__name__icontains=search_query)
            | Q(leave_type__icontains=search_query)
        )

    pending_page = Paginator(pending_qs, 10).get_page(request.GET.get("page", 1))

    if _wants_json_response(request):
        html = render_to_string(
            "partials/manager_pending_leaves_table.html",
            {
                "pending_page": pending_page,
                "search_query": search_query,
            },
            request=request,
        )
        return JsonResponse(
            {
                "success": True,
                "html": html,
                "pending_count": pending_qs.count(),
            }
        )

    return _render_template_page(
        request,
        "manager_pending_leaves.html",
        {
            "pending_page": pending_page,
            "pending_count": pending_qs.count(),
            "search_query": search_query,
        },
    )


@login_required
def manager_leave_balance(request):
    return employee_leave_balance(request)


@login_required
def admin_dashboard_page(request):
    wants_json = (
        request.GET.get("format") == "json"
        or "application/json" in request.headers.get("Accept", "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )
    if wants_json:
        return api_admin_dashboard(request)
    return _render_template_page(request, "admin_dashboard.html")


@login_required
def hr_pending_leaves(request):
    return _render_template_page(request, "hr_pending_leaves.html")


@login_required
@role_required(["HR", "Admin"])
def hr_leave_analytics(request):
    """Render leave analytics page with data from the API"""
    try:
        api_response = hr_leave_analytics_api(request)
        data = json.loads(api_response.content.decode("utf-8"))
        
        if data.get("success"):
            analytics = data
            
            # Prepare context for template
            context = {
                "profile": _build_profile_context(request.user),
                "pending_count": LeaveRequest.objects.filter(status="PENDING").count(),
                "current_year": analytics.get("current_year"),
                
                # KPI data
                "total_this_year": analytics.get("totals", {}).get("total_this_year", 0),
                "approved_count": analytics.get("totals", {}).get("approved", 0),
                "rejected_count": analytics.get("totals", {}).get("rejected", 0),
                "pending_total": analytics.get("totals", {}).get("pending", 0),
                "on_leave_today": analytics.get("totals", {}).get("on_leave_today", 0),
                "this_month_total": analytics.get("totals", {}).get("this_month_total", 0),
                "this_month_approved": analytics.get("totals", {}).get("this_month_approved", 0),
                "approval_rate": analytics.get("totals", {}).get("approval_rate", 0),
                
                # Chart data for both Django-rendered markup and JavaScript
                "monthly_all": analytics.get("monthly_chart", {}).get("all", []),
                "monthly_approved": analytics.get("monthly_chart", {}).get("approved", []),
                "monthly_rejected": analytics.get("monthly_chart", {}).get("rejected", []),
                "monthly_pending": analytics.get("monthly_chart", {}).get("pending", []),
                "type_labels": analytics.get("type_chart", {}).get("labels", []),
                "type_counts": analytics.get("type_chart", {}).get("counts", []),
                "type_colors": analytics.get("type_chart", {}).get("colors", []),
                "dept_labels": analytics.get("department_chart", {}).get("labels", []),
                "dept_counts": analytics.get("department_chart", {}).get("counts", []),
                "week_labels": analytics.get("weekly_chart", {}).get("labels", []),
                "week_counts": analytics.get("weekly_chart", {}).get("counts", []),
                "monthly_all_json": json.dumps(analytics.get("monthly_chart", {}).get("all", [])),
                "monthly_approved_json": json.dumps(analytics.get("monthly_chart", {}).get("approved", [])),
                "monthly_rejected_json": json.dumps(analytics.get("monthly_chart", {}).get("rejected", [])),
                "monthly_pending_json": json.dumps(analytics.get("monthly_chart", {}).get("pending", [])),
                "type_labels_json": json.dumps(analytics.get("type_chart", {}).get("labels", [])),
                "type_counts_json": json.dumps(analytics.get("type_chart", {}).get("counts", [])),
                "type_colors_json": json.dumps(analytics.get("type_chart", {}).get("colors", [])),
                "dept_labels_json": json.dumps(analytics.get("department_chart", {}).get("labels", [])),
                "dept_counts_json": json.dumps(analytics.get("department_chart", {}).get("counts", [])),
                "week_labels_json": json.dumps(analytics.get("weekly_chart", {}).get("labels", [])),
                "week_counts_json": json.dumps(analytics.get("weekly_chart", {}).get("counts", [])),
                "type_stats": [
                    {
                        "label": label,
                        "count": count,
                        "color": color,
                    }
                    for label, count, color in zip(
                        analytics.get("type_chart", {}).get("labels", []),
                        analytics.get("type_chart", {}).get("counts", []),
                        analytics.get("type_chart", {}).get("colors", []),
                    )
                ],
                
                # Top takers data
                "top_takers": analytics.get("top_takers", []),
            }
            return render(request, "hr_leave_analytics.html", context)
    except Exception as e:
        pass
    
    # Fallback: just render empty template
    empty_chart_context = {
        "current_year": timezone.now().year,
        "pending_count": 0,
        "total_this_year": 0,
        "approved_count": 0,
        "rejected_count": 0,
        "pending_total": 0,
        "on_leave_today": 0,
        "this_month_total": 0,
        "this_month_approved": 0,
        "approval_rate": 0,
        "monthly_all": [],
        "monthly_approved": [],
        "monthly_rejected": [],
        "monthly_pending": [],
        "type_labels": [],
        "type_counts": [],
        "type_colors": [],
        "dept_labels": [],
        "dept_counts": [],
        "week_labels": [],
        "week_counts": [],
        "monthly_all_json": "[]",
        "monthly_approved_json": "[]",
        "monthly_rejected_json": "[]",
        "monthly_pending_json": "[]",
        "type_labels_json": "[]",
        "type_counts_json": "[]",
        "type_colors_json": "[]",
        "dept_labels_json": "[]",
        "dept_counts_json": "[]",
        "week_labels_json": "[]",
        "week_counts_json": "[]",
        "type_stats": [],
        "top_takers": [],
    }
    return _render_template_page(request, "hr_leave_analytics.html", empty_chart_context)


@login_required
def hr_on_leave_today(request):
    today = date.today()
    on_leave = LeaveRequest.objects.filter(
        status="APPROVED", start_date__lte=today, end_date__gte=today
    ).select_related("employee", "employee__department", "employee__role").order_by(
        "employee__first_name"
    )

    dept_breakdown = list(
        on_leave.values("employee__department__name")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    return _render_template_page(request, "hr_on_leave_today.html", {
        "today": today,
        "on_leave": on_leave,
        "on_leave_count": on_leave.count(),
        "dept_breakdown": dept_breakdown,
    })


@login_required
def hr_new_joiners(request):
    today = date.today()
    current_year = timezone.now().year
    current_month = timezone.now().month
    filter_period = request.GET.get("period", "30")

    if filter_period == "month":
        joiners = User.objects.exclude(is_superuser=True).filter(
            date_joined__year=current_year, date_joined__month=current_month
        )
    elif filter_period == "year":
        joiners = User.objects.exclude(is_superuser=True).filter(date_joined__year=current_year)
    else:
        since = today - timedelta(days=30)
        joiners = User.objects.exclude(is_superuser=True).filter(date_joined__date__gte=since)

    joiners = joiners.select_related("role", "department").order_by("-date_joined")
    return _render_template_page(request, "hr_new_joiners.html", {
        "filter_period": filter_period,
        "joiners_count": joiners.count(),
        "joiners": joiners,
    })


@login_required
def hr_departments(request):
    if _wants_json_response(request):
        return hr_departments_api(request)
    return _render_template_page(request, "hr_departments.html")


@login_required
def hr_my_leave_balance(request):
    if _wants_json_response(request):
        return hr_my_leave_balance_api(request)

    current_year = date.today().year
    leave_summary = get_employee_leave_summary(request.user, current_year)
    my_leaves_qs = LeaveRequest.objects.filter(employee=request.user).order_by("-created_at")
    my_leaves = my_leaves_qs[:10]

    leave_breakdown = leave_summary.get("breakdown", [])
    for item in leave_breakdown:
        allocated = float(item.get("allocated", 0) or 0)
        used = float(item.get("used", 0) or 0)
        item["used_percent"] = round((used / allocated) * 100, 1) if allocated > 0 else 0

    context = {
        "leave_summary": leave_summary,
        "total_remaining": leave_summary.get("total_remaining", 0),
        "total_allocated": leave_summary.get("total_allocated", 0),
        "total_used_new": leave_summary.get("total_used", 0),
        "pending_leaves": my_leaves_qs.filter(status="PENDING").count(),
        "my_leaves": my_leaves,
        "leave_breakdown": leave_breakdown,
    }
    return _render_template_page(request, "hr_my_leave_balance.html", context)


@login_required
def hr_employee_list(request):
    if not (
        request.user.is_superuser
        or user_has_permission(request.user, "user_view")
        or user_has_permission(request.user, "user_create")
        or user_has_permission(request.user, "user_update")
        or user_has_permission(request.user, "user_delete")
        or user_has_permission(request.user, "user_activate")
        or user_has_permission(request.user, "user_deactivate")
    ):
        raise PermissionDenied
    today = date.today()
    search_query = request.GET.get("search", "").strip()
    dept_filter = request.GET.get("department", "").strip()
    role_filter = request.GET.get("role", "").strip()
    status_filter = request.GET.get("status", "").strip()

    employees = (
        User.objects.exclude(role__name__iexact="Admin")
        .exclude(is_superuser=True)
        .select_related("role", "department")
        .order_by("-date_joined")
    )
    if search_query:
        employees = employees.filter(
            Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(username__icontains=search_query)
            | Q(department__name__icontains=search_query)
            | Q(role__name__icontains=search_query)
        )
    if dept_filter:
        employees = employees.filter(department__pk=dept_filter)
    if role_filter:
        employees = employees.filter(role__pk=role_filter)

    employee_rows = []
    for emp in employees:
        on_leave = LeaveRequest.objects.filter(
            employee=emp, status="APPROVED", start_date__lte=today, end_date__gte=today
        ).exists()
        if status_filter == "on_leave" and not on_leave:
            continue
        if status_filter == "active" and not emp.is_active:
            continue
        if status_filter == "inactive" and emp.is_active:
            continue
        employee_rows.append({
            "emp": emp,
            "role": getattr(emp.role, "name", "") or "Employee",
            "department": getattr(emp.department, "name", "") or "—",
            "on_leave": on_leave,
        })

    page_obj = Paginator(employee_rows, 20).get_page(request.GET.get("page", 1))

    context = {
        "total_count": User.objects.exclude(role__name__iexact="Admin").exclude(is_superuser=True).count(),
        "active_count": User.objects.exclude(role__name__iexact="Admin").exclude(is_superuser=True).filter(is_active=True).count(),
        "inactive_count": User.objects.exclude(role__name__iexact="Admin").exclude(is_superuser=True).filter(is_active=False).count(),
        "on_leave_count": LeaveRequest.objects.filter(
            status="APPROVED", start_date__lte=today, end_date__gte=today
        ).values("employee").distinct().count(),
        "result_count": len(employee_rows),
        "search_query": search_query,
        "dept_filter": dept_filter,
        "role_filter": role_filter,
        "status_filter": status_filter,
        "page_obj": page_obj,
        "departments": Department.objects.all().order_by("name"),
        "roles": Role.objects.exclude(name="Admin").order_by("name"),
    }

    if _wants_json_response(request):
        html = render_to_string("partials/hr_employee_table.html", context, request=request)
        return JsonResponse({"success": True, "html": html})

    return _render_template_page(request, "hr_employee_list.html", context)


def employee_list(request):
    if not request.user.is_authenticated:
        return JsonResponse(
            {"success": False, "error": "Authentication required."},
            status=401,
        )
    return hr_employee_list_api(request)


@login_required
def employee_list_page(request):
    if not (
        request.user.is_superuser
        or user_has_permission(request.user, "user_view")
        or user_has_permission(request.user, "user_create")
        or user_has_permission(request.user, "user_update")
        or user_has_permission(request.user, "user_delete")
    ):
        raise PermissionDenied
    return _render_template_page(request, "employee_list.html")


@login_required
def employee_detail(request, pk):
    if not (
        request.user.is_superuser
        or user_has_permission(request.user, "user_view")
        or user_has_permission(request.user, "user_update")
        or user_has_permission(request.user, "user_delete")
        or request.user.pk == pk
    ):
        raise PermissionDenied
    employee = get_object_or_404(User, pk=pk)
    return _render_template_page(request, "employee_detail.html", {
        "employee": employee,
        "departments": Department.objects.all().order_by("name"),
        "roles": Role.objects.exclude(name="Admin").order_by("name"),
        "managers": User.objects.exclude(pk=pk).exclude(is_superuser=True).order_by("first_name", "last_name"),
        "edit_mode": request.GET.get("edit") == "1",
    })


@login_required
def apply_leave(request):
    if request.method == "POST":
        return apply_leave_api(request)
    
    current_year = date.today().year
    leave_summary = get_employee_leave_summary(request.user, current_year)
    
    active_leave_types = []
    active_policy = None
    max_days = 5
    leave_breakdown = []
    
    if POLICY_ENABLED:
        # Get all active leave types applicable to this employee
        active_leave_types = list(_get_applicable_leave_types_for_employee(request.user))
        
        # Get active policy
        active_policy = _get_active_policy_for_employee(request.user)
        if active_policy:
            max_days = active_policy.max_days_per_request
        
        # Get leave breakdown from leave_summary
        leave_breakdown = leave_summary.get("breakdown", [])
        
        # Add remaining and used_percent fields for template
        for item in leave_breakdown:
            item["used_percent"] = 0
            total = item.get("allocated", 0)
            if total > 0:
                used = item.get("used", 0)
                item["used_percent"] = min(100, round((used / total) * 100))
    
    context = {
        "active_leave_types": active_leave_types,
        "leave_breakdown": leave_breakdown,
        "active_policy": active_policy,
        "available_balance": leave_summary.get("total_remaining", 0),
        "max_days": max_days,
    }
    
    return _render_template_page(request, "apply_leave.html", context)


@login_required
def approve_leave(request, leave_id):
    return approve_leave_api(request, leave_id)


@login_required
def reject_leave(request, leave_id):
    return reject_leave_api(request, leave_id)


@login_required
def create_employee(request):
    return create_employee_api(request)


@login_required
def update_employee(request, pk):
    return update_employee_api(request, pk)


@login_required
def delete_employee(request, pk):
    return delete_employee_api(request, pk)


@login_required
def toggle_employee_status(request, user_id):
    return toggle_employee_status_api(request, user_id)


@login_required
@role_required()
def notifications(request):
    return _render_template_page(request, "notification.html")


@login_required
def holiday_list(request):
    if _wants_json_response(request):
        return holiday_list_api(request)

    if not HOLIDAYS_ENABLED:
        return _render_template_page(request, "holiday_list.html", {
            "current_year": timezone.now().year,
            "current_month": "",
            "current_type": "",
            "search_query": "",
            "years": [],
            "months": [],
            "holiday_types": [],
            "total_holidays": 0,
            "total_days": 0,
            "upcoming": [],
            "calendar_data": [],
        })

    today = date.today()
    current_year = int(request.GET.get("year", today.year))
    current_month = (request.GET.get("month") or "").strip()
    current_type = (request.GET.get("type") or "").strip()
    search_query = (request.GET.get("search") or "").strip()

    holidays = Holiday.objects.filter(date__year=current_year)
    if current_month and str(current_month).isdigit():
        holidays = holidays.filter(date__month=int(current_month))
    if current_type:
        holidays = holidays.filter(holiday_type=current_type)
    if search_query:
        holidays = holidays.filter(Q(name__icontains=search_query) | Q(description__icontains=search_query))

    holidays = holidays.order_by("date")

    icon_map = {
        "NATIONAL": "fa-flag",
        "RELIGIOUS": "fa-place-of-worship",
        "REGIONAL": "fa-location-dot",
        "COMPANY": "fa-building",
        "BANK": "fa-building-columns",
        "OTHER": "fa-star",
    }
    color_map = {
        "NATIONAL": "badge-info",
        "RELIGIOUS": "badge-warning",
        "REGIONAL": "badge-info",
        "COMPANY": "badge-success",
        "BANK": "badge-warning",
        "OTHER": "badge-info",
    }

    calendar_data = []
    for m in range(1, 13):
        month_qs = holidays.filter(date__month=m)
        if not month_qs.exists():
            continue
        month_items = []
        for h in month_qs:
            month_items.append({
                "id": h.id,
                "name": h.name,
                "date": h.date,
                "end_date": h.end_date,
                "holiday_type": h.holiday_type,
                "get_holiday_type_display": h.get_holiday_type_display(),
                "is_active": h.is_active,
                "icon": icon_map.get(h.holiday_type, "fa-star"),
                "color_class": color_map.get(h.holiday_type, "badge-info"),
            })
        calendar_data.append({
            "month": m,
            "month_name": month_name[m],
            "count": len(month_items),
            "holidays": month_items,
        })

    upcoming = Holiday.objects.filter(date__gte=today, is_active=True).order_by("date")[:5]
    years = [date(y, 1, 1) for y in range(today.year - 2, today.year + 3)]
    months = [(i, month_name[i]) for i in range(1, 13)]

    return _render_template_page(request, "holiday_list.html", {
        "current_year": current_year,
        "current_month": str(current_month),
        "current_type": current_type,
        "search_query": search_query,
        "years": years,
        "months": months,
        "holiday_types": Holiday.HOLIDAY_TYPES,
        "total_holidays": holidays.count(),
        "total_days": holidays.count(),
        "upcoming": upcoming,
        "calendar_data": calendar_data,
    })

@login_required
@role_required(["HR", "Admin"])
def holiday_create(request):
    if request.method == "POST":
        return holiday_create_api(request)
    if _wants_json_response(request):
        return _ok({"holiday": None, "holiday_types": list(Holiday.HOLIDAY_TYPES), "today": str(date.today())})
    if not HOLIDAYS_ENABLED:
        return _render_template_page(request, "holiday_form.html", {
            "holiday_types": [],
            "today": date.today(),
        })
    return _render_template_page(request, "holiday_form.html", {
        "holiday": None,
        "holiday_types": Holiday.HOLIDAY_TYPES,
        "today": date.today(),
    })


@login_required
def holiday_bulk_create(request):
    if request.method == "POST":
        return holiday_bulk_create_api(request)
    if _wants_json_response(request):
        return _ok({"year": timezone.now().year})
    return _render_template_page(request, "holiday_bulk_form.html")


@login_required
@role_required(["HR", "Admin"])
def holiday_edit(request, holiday_id):
    if request.method == "POST":
        return holiday_edit_api(request, holiday_id)
    if not HOLIDAYS_ENABLED:
        return _render_template_page(request, "holiday_form.html", {
            "holiday_types": [],
            "today": date.today(),
        })
    holiday = get_object_or_404(Holiday, id=holiday_id)
    if _wants_json_response(request):
        return holiday_detail_api(request, holiday_id)
    return _render_template_page(request, "holiday_form.html", {
        "holiday": holiday,
        "holiday_types": Holiday.HOLIDAY_TYPES,
        "today": date.today(),
    })


@login_required
def holiday_delete(request, holiday_id):
    return holiday_delete_api(request, holiday_id)


@login_required
def holiday_toggle_status(request, holiday_id):
    return holiday_toggle_status_api(request, holiday_id)


@login_required
def public_holidays(request):
    if _wants_json_response(request):
        return public_holidays_api(request)
    return _render_template_page(request, "public_holidays.html")


@login_required
def employee_search_json(request):
    query = (request.GET.get("q") or request.GET.get("search") or "").strip()
    employees = User.objects.filter(is_superuser=False).select_related("role", "department").order_by("first_name", "last_name", "email")
    if query:
        employees = employees.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
            | Q(role__name__icontains=query)
            | Q(department__name__icontains=query)
        )
    results = []
    for employee in employees[:20]:
        results.append({
            "id": employee.id,
            "name": employee.get_full_name() or employee.username or employee.email,
            "email": employee.email,
            "role": getattr(employee.role, "name", "") or "-",
            "department": getattr(employee.department, "name", "") or "-",
            "is_active": employee.is_active,
        })
    return JsonResponse({"success": True, "results": results})


@login_required
def api_admin_dashboard(request):
    if not (request.user.is_superuser or get_user_role(request.user) == "Admin"):
        return JsonResponse({"success": False, "error": "Unauthorized"}, status=403)

    tab = (request.GET.get("tab") or "all").lower()
    search = (request.GET.get("search") or "").strip()
    unread_count = Notification.objects.filter(user=request.user, read_status=False).count()
    employees = User.objects.filter(is_superuser=False).select_related("role", "department").order_by("-date_joined", "-id")

    if tab == "active":
        employees = employees.filter(is_active=True)
    elif tab == "inactive":
        employees = employees.filter(is_active=False)

    if search:
        employees = employees.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
            | Q(role__name__icontains=search)
            | Q(department__name__icontains=search)
        )

    page_data = _paginate(employees, request, per_page=10)
    page_obj = page_data.pop("_page_obj")
    page_data.pop("results", None)

    employee_rows = []
    for employee in page_obj.object_list:
        employee_rows.append({
            "id": employee.id,
            "name": employee.get_full_name() or employee.username or employee.email,
            "email": employee.email,
            "role": getattr(employee.role, "name", "") or "-",
            "department": getattr(employee.department, "name", "") or "",
            "is_active": employee.is_active,
        })

    recent_activity = []
    for employee in User.objects.filter(is_superuser=False).order_by("-date_joined")[:5]:
        recent_activity.append({
            "type": "joined",
            "title": f"{employee.get_full_name() or employee.email} joined the company",
            "time_ago": timesince(employee.date_joined) + " ago",
        })

    top_leave_takers = []
    top_leave_rows = (
        LeaveRequest.objects.filter(final_status="APPROVED")
        .values("employee__first_name", "employee__last_name", "employee__email", "employee__department__name")
        .annotate(total_days=Sum("paid_days"))
        .order_by("-total_days")[:5]
    )
    for row in top_leave_rows:
        name = f"{row['employee__first_name']} {row['employee__last_name']}".strip() or row["employee__email"]
        top_leave_takers.append({
            "name": name,
            "department": row["employee__department__name"] or "-",
            "total_days": float(row["total_days"] or 0),
        })

    role_aliases = {
        "employee": ["employee"],
        "hr": ["hr", "human resources"],
        "tl": ["tl", "team lead", "teamlead"],
        "manager": ["manager"],
    }
    role_labels = {
        "employee": "Employee",
        "hr": "HR",
        "tl": "TL",
        "manager": "Manager",
    }
    permission_features = [
        {"key": "dashboard_admin", "label": "Admin Dashboard"},
        {"key": "dashboard_hr", "label": "HR Dashboard"},
        {"key": "dashboard_manager", "label": "Manager Dashboard"},
        {"key": "dashboard_employee", "label": "Employee Dashboard"},
        {"key": "user_view", "label": "View Users"},
        {"key": "user_create", "label": "Create Users"},
        {"key": "user_update", "label": "Update Users"},
        {"key": "user_delete", "label": "Delete Users"},
        {"key": "user_activate", "label": "Activate Users"},
        {"key": "user_deactivate", "label": "Deactivate Users"},
        {"key": "user_assign_role", "label": "Assign User Roles"},
        {"key": "role_view", "label": "View Roles"},
        {"key": "role_create", "label": "Create Roles"},
        {"key": "role_update", "label": "Update Roles"},
        {"key": "role_delete", "label": "Delete Roles"},
        {"key": "role_assign_permissions", "label": "Assign Role Permissions"},
        {"key": "permission_view", "label": "View Permissions"},
        {"key": "permission_assign", "label": "Assign Permissions"},
        {"key": "leave_apply", "label": "Apply Leave"},
        {"key": "leave_view_own", "label": "View Own Leave"},
        {"key": "leave_view_all", "label": "View All Leave"},
        {"key": "leave_approve", "label": "Approve Leave"},
        {"key": "leave_reject", "label": "Reject Leave"},
        {"key": "leave_update_own", "label": "Update Own Leave"},
        {"key": "leave_delete_own", "label": "Delete Own Leave"},
        {"key": "leave_cancel", "label": "Cancel Leave"},
        {"key": "leave_policy_view", "label": "View Leave Policy"},
        {"key": "leave_policy_create", "label": "Create Leave Policy"},
        {"key": "leave_policy_update", "label": "Update Leave Policy"},
        {"key": "leave_policy_delete", "label": "Delete Leave Policy"},
        {"key": "leave_balance_view", "label": "View Leave Balance"},
        {"key": "leave_balance_update", "label": "Update Leave Balance"},
        {"key": "holiday_view", "label": "View Holidays"},
        {"key": "holiday_create", "label": "Create Holidays"},
        {"key": "holiday_update", "label": "Update Holidays"},
        {"key": "holiday_delete", "label": "Delete Holidays"},
        {"key": "report_view", "label": "View Reports"},
        {"key": "report_export", "label": "Export Reports"},
        {"key": "team_view", "label": "View Team"},
        {"key": "team_manage", "label": "Manage Team"},
        {"key": "settings_view", "label": "View Settings"},
        {"key": "settings_update", "label": "Update Settings"},
        {"key": "audit_view", "label": "View Audit"},
        {"key": "notification_view", "label": "View Notifications"},
        {"key": "salary_view", "label": "View Salary"},
        {"key": "salary_update", "label": "Update Salary"},
        {"key": "bank_view", "label": "View Bank"},
        {"key": "bank_update", "label": "Update Bank"},
        {"key": "verification_view", "label": "View Verification"},
        {"key": "verification_update", "label": "Update Verification"},
    ]

    available_roles = {
        (role.name or "").strip().lower(): role
        for role in Role.objects.filter(is_active=True)
    }
    tracked_roles = {}
    for role_key, aliases in role_aliases.items():
        tracked_roles[role_key] = None
        for alias in aliases:
            matched_role = available_roles.get(alias)
            if matched_role is not None:
                tracked_roles[role_key] = matched_role
                break

    tracked_role_ids = [role.id for role in tracked_roles.values() if role is not None]
    assignment_rows = RolePermissionAssignment.objects.filter(
        role_id__in=tracked_role_ids,
        permission__is_active=True,
        is_enabled=True,
    ).values("role_id", "permission__codename")

    role_permission_codes = {role.id: set() for role in tracked_roles.values() if role is not None}
    for row in assignment_rows:
        role_permission_codes.setdefault(row["role_id"], set()).add(row["permission__codename"])

    frontend_permission_matrix = {}
    for role_key, role_obj in tracked_roles.items():
        codes = role_permission_codes.get(role_obj.id, set()) if role_obj is not None else set()
        frontend_permission_matrix[role_key] = {
            "label": role_labels[role_key],
            "configured": role_obj is not None,
        }
        for feature in permission_features:
            frontend_permission_matrix[role_key][feature["key"]] = feature["key"] in codes

    return JsonResponse({
        "success": True,
        "filters": {"tab": tab, "search": search},
        "stats": {
            "total_employees": User.objects.filter(is_superuser=False).count(),
            "active_count": User.objects.filter(is_superuser=False, is_active=True).count(),
            "inactive_count": User.objects.filter(is_superuser=False, is_active=False).count(),
            "pending_count": LeaveRequest.objects.filter(final_status="PENDING").count(),
            "leave_types_count": LeaveTypeConfig.objects.filter(is_active=True).count() if POLICY_ENABLED else 0,
            "unread_count": unread_count,
        },
        "pagination": page_data,
        "employees": employee_rows,
        "roles": list(Role.objects.exclude(name="Admin").order_by("name").values("id", "name")),
        "departments": list(Department.objects.order_by("name").values("id", "name")),
        "recent_joined": [],
        "recent_approved": [],
        "recent_rejected": [],
        "activity_log": recent_activity,
        "top_leave_takers": top_leave_takers,
        "frontend_permission_features": permission_features,
        "frontend_permission_matrix": frontend_permission_matrix,
        "unread_count": unread_count,
    })


@login_required
# ⚠️  DEPRECATED (v2.0+): Not exposed via any URL endpoint
# This was an HTML-only view that has been superseded by the JSON API:
#   Use: /api/leave-policy/ (admin_leave_policy_api)
# Can be removed in a future cleanup once all templates are updated.
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
            'starting_month_name': month_name[int(getattr(lt, "starting_month", 4) or 4)],
            'current_month_entitlement': _target_allocation_days_for_leave_type(lt, sync_mode="monthly"),
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

@csrf_exempt
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
            target_days = _target_allocation_days_for_leave_type(lt, sync_mode="monthly")
            n = EmployeeLeaveAllocation.objects.filter(
                leave_type=lt, year=timezone.now().year
            ).update(allocated_days=target_days)
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
    sync_mode     = request.POST.get("sync_mode", "monthly").strip().lower() or "monthly"
    if sync_mode not in ("monthly", "full_year"):
        sync_mode = "monthly"
    year          = int(request.POST.get("year", timezone.now().year))
    total_created = total_updated = 0

    for lt in LeaveTypeConfig.objects.filter(is_active=True):
        c, u = _apply_leave_type_to_all_employees(
            lt, year=year, update_existing=force_update, sync_mode=sync_mode)
        total_created += c
        total_updated += u

    messages.success(
        request,
        f"✅ {sync_mode.replace('_', ' ').title()} sync complete for {year}! "
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

    leave_types = _get_applicable_leave_types_for_employee(request.user)

    result = []
    for lt in leave_types:
        year = get_leave_year_for_date(timezone.now().date(), getattr(lt, "starting_month", 1))
        _ensure_leave_allocations_for_employee(request.user, year, leave_type_config=lt)
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
            'starting_month':          getattr(lt, "starting_month", 1),
            'leave_year':              year,
        })

    return JsonResponse({'leave_types': result, 'year': timezone.now().year})


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
# views.py
from django.shortcuts import render
from .models import Department  # adjust if your model is named differently

def department_list(request):
    from django.db.models import Count
    departments = Department.objects.annotate(emp_count=Count('user')).all()
    return render(request, 'department_list.html', {'departments': departments})

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

    leave_summary = get_employee_leave_summary(request.user, current_year)

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

    next_month_balance = available_balance + _get_projected_next_month_accrual(request.user)

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
    
    # Handle AJAX request for approval/rejection
    if request.method == 'POST' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        action = request.POST.get('action')
        remarks = request.POST.get('remarks', '')
        
        if action in ['approve', 'reject'] and can_approve:
            try:
                # Update the leave based on user role
                if role_name == 'TL':
                    if action == 'approve':
                        leave.tl_approved = True
                        leave.tl_rejected = False
                    else:
                        leave.tl_approved = False
                        leave.tl_rejected = True
                    leave.tl_voted = True
                    leave.tl_acted_at = timezone.now()
                    leave.tl_remarks = remarks
                    
                elif role_name == 'Manager':
                    if action == 'approve':
                        leave.manager_approved = True
                        leave.manager_rejected = False
                    else:
                        leave.manager_approved = False
                        leave.manager_rejected = True
                    leave.manager_voted = True
                    leave.manager_acted_at = timezone.now()
                    leave.manager_remarks = remarks
                    
                elif role_name == 'HR':
                    if action == 'approve':
                        leave.hr_approved = True
                        leave.hr_rejected = False
                    else:
                        leave.hr_approved = False
                        leave.hr_rejected = True
                    leave.hr_voted = True
                    leave.hr_acted_at = timezone.now()
                    leave.hr_remarks = remarks
                
                # Keep vote counters in sync with the action performed.
                if action == 'approve':
                    leave.approval_count = int(leave.approval_count or 0) + 1
                else:
                    leave.rejection_count = int(leave.rejection_count or 0) + 1

                old_status = leave.final_status
                leave.save()

                decision, _reason = _evaluate_leave_decision(leave)

                if decision == 'APPROVED' and old_status != 'APPROVED':
                    leave.final_status = 'APPROVED'
                    leave.status = 'APPROVED'
                    leave.balance_deducted_at = timezone.now()
                    leave.save()
                    _deduct_leave_balance(leave)
                elif decision == 'REJECTED':
                    if old_status == 'APPROVED':
                        _restore_leave_balance(leave)
                    leave.final_status = 'REJECTED'
                    leave.status = 'REJECTED'
                    leave.save()
                else:
                    if old_status == 'APPROVED':
                        _restore_leave_balance(leave)
                    leave.final_status = 'PENDING'
                    leave.status = 'PENDING'
                    leave.save()
                
                # Rebuild approvers list after update
                updated_approvers = []
                
                # Refresh TL data
                if tl:
                    updated_approvers.append({
                        'name': tl.get_full_name() or tl.username,
                        'email': tl.email,
                        'role': 'Team Leader',
                        'vote': 'approved' if leave.tl_approved else ('rejected' if leave.tl_rejected else 'pending'),
                        'acted_at': leave.tl_acted_at.strftime('%d %b %Y, %I:%M %p') if leave.tl_acted_at else None,
                        'remarks': getattr(leave, 'tl_remarks', ''),
                    })
                
                # Refresh HR data
                if hr:
                    updated_approvers.append({
                        'name': hr.get_full_name() or hr.username,
                        'email': hr.email,
                        'role': 'HR',
                        'vote': 'approved' if leave.hr_approved else ('rejected' if leave.hr_rejected else 'pending'),
                        'acted_at': leave.hr_acted_at.strftime('%d %b %Y, %I:%M %p') if leave.hr_acted_at else None,
                        'remarks': getattr(leave, 'hr_remarks', ''),
                    })
                
                # Refresh Manager data
                if manager:
                    updated_approvers.append({
                        'name': manager.get_full_name() or manager.username,
                        'email': manager.email,
                        'role': 'Manager',
                        'vote': 'approved' if leave.manager_approved else ('rejected' if leave.manager_rejected else 'pending'),
                        'acted_at': leave.manager_acted_at.strftime('%d %b %Y, %I:%M %p') if leave.manager_acted_at else None,
                        'remarks': getattr(leave, 'manager_remarks', ''),
                    })
                
                return JsonResponse({
                    'success': True,
                    'message': f'Leave {action}d successfully!',
                    'final_status': leave.final_status,
                    'approvers': updated_approvers,
                    'can_approve': False,  # User has already voted
                })
                
            except Exception as e:
                return JsonResponse({
                    'success': False,
                    'message': f'Error processing request: {str(e)}'
                }, status=400)
        
        return JsonResponse({
            'success': False,
            'message': 'You are not authorized to perform this action.'
        }, status=403)
    
    return render(request, 'leave_detail.html', context)
