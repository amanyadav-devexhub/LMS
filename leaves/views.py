# ═══════════════════════════════════════════════════════════════════
#  leaves/views.py  —  COMPLETE CLEAN VERSION
#  Flow:
#    • Employee applies → status = PENDING
#    • TL notified (their team only), HR notified (all), Manager notified (all)
#    • All three dashboards show the leave simultaneously
#    • TL can approve/reject own team only
#    • HR and Manager can approve/reject anyone
#    • First to act closes the request
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

# ── DRF ──────────────────────────────────────────────────────────────
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

# ── Leaves app models ────────────────────────────────────────────────
from .models import LeaveRequest, LeaveBalance, Notification

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


def send_notification(users, message):
    for u in users:
        Notification.objects.create(user=u, message=message)


def _hr_base_context(request):
    """
    Shared context injected into every HR page.
    Provides all four sidebar badge counts.
    """
    today         = date.today()
    current_year  = timezone.now().year
    current_month = timezone.now().month

    # All PENDING leaves (not filtered by department — HR sees everything)
    pending_hr_count = LeaveRequest.objects.filter(
        status="PENDING"
    ).count()

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
#  EMPLOYEE DASHBOARD
# ════════════════════════════════════════════════════════════════════

@login_required
def employee_dashboard(request):
    from django.template.loader import render_to_string

    all_leaves = LeaveRequest.objects.filter(employee=request.user).order_by('-created_at')
    balance, _ = LeaveBalance.objects.get_or_create(employee=request.user)
    unread     = Notification.objects.filter(user=request.user, read_status=False).count()

    paginator   = Paginator(all_leaves, 5)
    page_number = request.GET.get('page', 1)
    page_obj    = paginator.get_page(page_number)

    pending_leaves = all_leaves.filter(status="PENDING").count()
    total_leaves   = all_leaves.count()
    leave_balance  = (balance.casual_leave or 0) + (balance.sick_leave or 0)

    designation = getattr(request.user, 'designation', None) or ''
    role_name   = get_user_role(request.user)

    context = {
        "leaves":           page_obj,
        "all_leaves_count": total_leaves,
        "page_obj":         page_obj,
        "balance":          balance,
        "leave_balance":    leave_balance,
        "pending_leaves":   pending_leaves,
        "total_leaves":     total_leaves,
        "unread_count":     unread,
        "designation":      designation,
        "role_name":        role_name,
        "profile":          _build_profile_context(request.user),
    }

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        html = render_to_string('leave_table.html', context, request=request)
        return JsonResponse({'html': html, 'success': True})

    return render(request, "employee_dashboard.html", context)


# ════════════════════════════════════════════════════════════════════
#  APPLY LEAVE
#  → status = PENDING on submit
#  → TL (employee's reporting manager) notified
#  → ALL HR users notified
#  → ALL Manager users notified
#  → All three dashboards show the leave simultaneously
# ════════════════════════════════════════════════════════════════════

@login_required
def apply_leave(request):

    if request.method == "POST":
        leave_type     = request.POST.get("leave_type")
        duration       = request.POST.get("duration")
        start_date_str = request.POST.get("start_date", "").strip()
        end_date_str   = request.POST.get("end_date",   "").strip()
        reason         = request.POST.get("reason", "").strip()
        short_session  = request.POST.get("short_session")
        short_hours    = request.POST.get("short_hours")
        attachment     = request.FILES.get("attachment")

        # ── Parse start_date ──────────────────────────────────────
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            messages.error(request, "Invalid start date. Please select a valid date.")
            return redirect("apply_leave")

        today = date.today()

        if start_date < today:
            messages.error(request, "Start date cannot be in the past.")
            return redirect("apply_leave")

        # ── Set end_date based on duration ────────────────────────
        if duration in ("HALF", "SHORT"):
            end_date      = start_date
            short_session = (short_session or "AM") if duration == "SHORT" else None
            short_hours   = int(short_hours or 4)   if duration == "SHORT" else None
        else:
            short_session = None
            short_hours   = None
            if end_date_str:
                try:
                    end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    end_date = start_date
            else:
                end_date = start_date
            if end_date < start_date:
                end_date = start_date

        # ── Validations ───────────────────────────────────────────
        delta = (end_date - start_date).days + 1
        if delta > 5:
            messages.error(
                request,
                f"Maximum 5 days allowed per application. You selected {delta} days."
            )
            return redirect("apply_leave")

        if attachment and attachment.size > 5 * 1024 * 1024:
            messages.error(request, "Attachment exceeds 5 MB. Please upload a smaller file.")
            return redirect("apply_leave")

        # ── Create leave with universal PENDING status ────────────
        leave_obj = LeaveRequest.objects.create(
            employee      = request.user,
            leave_type    = leave_type,
            duration      = duration,
            start_date    = start_date,
            end_date      = end_date,
            reason        = reason,
            short_session = short_session if duration == "SHORT" else None,
            short_hours   = short_hours   if duration == "SHORT" else None,
            status        = "PENDING",
        )

        if attachment:
            try:
                leave_obj.attachment = attachment
                leave_obj.save(update_fields=["attachment"])
            except Exception:
                pass

        # ── Notify all approvers simultaneously ───────────────────
        applicant_name = request.user.get_full_name() or request.user.username
        notify_message = (
            f"New leave request from {applicant_name} "
            f"({leave_type}, {duration}, {start_date} to {end_date}). "
            f"Awaiting your approval."
        )

        # 1. Employee's direct TL (reporting manager) only
        if getattr(request.user, 'reporting_manager', None):
            send_notification([request.user.reporting_manager], notify_message)

        # 2. All HR users
        try:
            hr_role  = Role.objects.get(name="HR")
            hr_users = User.objects.filter(role=hr_role)
            send_notification(hr_users, notify_message)
        except Role.DoesNotExist:
            pass

        # 3. All Manager users
        try:
            mgr_role  = Role.objects.get(name="Manager")
            mgr_users = User.objects.filter(role=mgr_role)
            send_notification(mgr_users, notify_message)
        except Role.DoesNotExist:
            pass

        messages.success(request, "Leave request submitted successfully!")

        # ── Redirect based on applicant's own role ────────────────
        applicant_role = get_user_role(request.user)
        redirect_map = {
            "Employee": "employee_dashboard",
            "TL":       "tl_dashboard",
            "HR":       "hr_dashboard",
            "Manager":  "manager_dashboard",
        }
        return redirect(redirect_map.get(applicant_role, "employee_dashboard"))

    # ── GET ───────────────────────────────────────────────────────
    balance = None
    try:
        balance, _ = LeaveBalance.objects.get_or_create(employee=request.user)
    except Exception:
        pass

    return render(request, "apply_leave.html", {"balance": balance})


# ════════════════════════════════════════════════════════════════════
#  TL DASHBOARD
#  Shows only leaves from the TL's direct team (reporting_manager=request.user)
# ════════════════════════════════════════════════════════════════════

@login_required
def tl_dashboard(request):
    if get_user_role(request.user) != "TL":
        return redirect("employee_dashboard")

    from django.core.paginator import Paginator

    today        = date.today()
    current_year = timezone.now().year

    # ── Team members under this TL ───────────────────────────────
    team_members = User.objects.filter(
        reporting_manager=request.user
    ).select_related("role", "department")

    # ── Pending leaves – paginated (8/page) ──────────────────────
    all_pending = LeaveRequest.objects.filter(
        status="PENDING",
        employee__reporting_manager=request.user
    ).select_related("employee").order_by("-created_at")

    leaves_page = Paginator(all_pending, 8).get_page(request.GET.get("page", 1))

    # ── On leave today ────────────────────────────────────────────
    on_leave_today = LeaveRequest.objects.filter(
        status="APPROVED",
        employee__reporting_manager=request.user,
        start_date__lte=today,
        end_date__gte=today
    ).select_related("employee")

    # ── All team leaves this year ─────────────────────────────────
    all_team = LeaveRequest.objects.filter(
        employee__reporting_manager=request.user,
        start_date__year=current_year
    )

    # ── Team Leave History – paginated (10/page) ──────────────────
    history_qs   = all_team.select_related("employee").order_by("-created_at")
    history_page = Paginator(history_qs, 10).get_page(request.GET.get("hpage", 1))

    # ── My Leave History – paginated (10/page) ────────────────────
    my_leaves_qs   = LeaveRequest.objects.filter(
        employee=request.user
    ).order_by("-created_at")
    my_leaves_page = Paginator(my_leaves_qs, 10).get_page(request.GET.get("mypage", 1))

    # ── Per-member balance summary ────────────────────────────────
    team_data = []
    for member in team_members:
        ml       = all_team.filter(employee=member)
        bal, _   = LeaveBalance.objects.get_or_create(employee=member)
        team_data.append({
            "member":         member,
            "total_leaves":   ml.count(),
            "approved":       ml.filter(status="APPROVED").count(),
            "pending":        ml.filter(status="PENDING").count(),
            "rejected":       ml.filter(status="REJECTED").count(),
            "casual_balance": bal.casual_leave or 0,
            "sick_balance":   bal.sick_leave   or 0,
            "is_on_leave":    on_leave_today.filter(employee=member).exists(),
        })

    unread = Notification.objects.filter(
        user=request.user, read_status=False
    ).count()

    context = {
        # Pending panel
        "leaves":             leaves_page,
        "pending_count":      all_pending.count(),

        # On leave today panel
        "on_leave_today":     on_leave_today,
        "on_leave_count":     on_leave_today.count(),

        # Balance panel
        "team_members":       team_members,
        "team_data":          team_data,
        "team_count":         team_members.count(),
        "approved_count":     all_team.filter(status="APPROVED").count(),
        "rejected_count":     all_team.filter(status="REJECTED").count(),
        "total_leaves_count": all_team.count(),

        # History panels
        "history_page":       history_page,
        "my_leaves_page":     my_leaves_page,
        "my_leave_count":     my_leaves_qs.count(),

        # Sidebar badges + meta
        "notification_count": unread,
        "current_year":       current_year,

        # Profile panel — uses same _build_profile_context helper from users/views.py
        "profile":            _build_profile_context(request.user),
    }
 
    return render(request, "tl_dashboard.html", context)

#  hr_dashboard

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

    # HR sees ALL pending leaves across all departments
    pending_leaves = LeaveRequest.objects.filter(
        status="PENDING"
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

    my_balance, _ = LeaveBalance.objects.get_or_create(employee=request.user)

    on_leave_today_preview = LeaveRequest.objects.filter(
        status="APPROVED",
        start_date__lte=today,
        end_date__gte=today
    ).select_related("employee", "employee__department").order_by("employee__first_name")[:5]

    recent_activity = (
        LeaveRequest.objects.select_related("employee").order_by("-updated_at")[:6]
    )

    recent_joiners = (
        User.objects.exclude(is_superuser=True)
        .select_related("role")
        .order_by("-date_joined")[:6]
    )

    my_recent_leaves = (
        LeaveRequest.objects.filter(employee=request.user).order_by("-created_at")[:4]
    )

    context = {
        **_hr_base_context(request),
        "total_employees":        total_employees,
        "active_count":           active_count,
        "pending_count":          pending_count,
        "new_joiners_count":      new_joiners_count,
        "on_leave_today_count":   on_leave_today_count,
        "on_leave_today_preview": on_leave_today_preview,
        "my_balance":             my_balance,
        "recent_pending":         pending_leaves[:5],
        "recent_activity":        recent_activity,
        "recent_joiners":         recent_joiners,
        "my_recent_leaves":       my_recent_leaves,
        "profile":                _build_profile_context(request.user),
    }
    return render(request, "hr_dashboard.html", context)


# ════════════════════════════════════════════════════════════════════
#  HR — PENDING APPROVALS  (full list, all departments)
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_pending_leaves(request):
    if get_user_role(request.user) != "HR":
        return redirect("employee_dashboard")

    leaves = LeaveRequest.objects.filter(
        status="PENDING"
    ).select_related("employee", "employee__department").order_by("-created_at")

    context = {
        **_hr_base_context(request),
        "leaves":        leaves,
        "pending_count": leaves.count(),
    }
    return render(request, "hr_pending_leaves.html", context)


# ════════════════════════════════════════════════════════════════════
#  HR — LEAVE ANALYTICS
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_leave_analytics(request):
    if get_user_role(request.user) != "HR":
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
        status="APPROVED", start_date__year=current_year
    ).count()
    rejected_count = LeaveRequest.objects.filter(
        status="REJECTED", start_date__year=current_year
    ).count()
    pending_total = LeaveRequest.objects.filter(
        status="PENDING", start_date__year=current_year
    ).count()

    top_takers = (
        LeaveRequest.objects
        .filter(status="APPROVED", start_date__year=current_year)
        .values(
            "employee",
            "employee__first_name",
            "employee__last_name",
            "employee__department__name",
        )
        .annotate(total_days=Sum(
            Case(
                When(duration="FULL",  then=Value(1.0)),
                When(duration="HALF",  then=Value(0.5)),
                When(duration="SHORT", then=Value(0.25)),
                default=Value(0.0),
                output_field=FloatField()
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
#  HR — ON LEAVE TODAY
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_on_leave_today(request):
    if get_user_role(request.user) != "HR":
        return redirect("employee_dashboard")

    today    = date.today()
    on_leave = LeaveRequest.objects.filter(
        status="APPROVED",
        start_date__lte=today,
        end_date__gte=today
    ).select_related(
        "employee", "employee__department", "employee__role"
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
#  HR — NEW JOINERS
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_new_joiners(request):
    if get_user_role(request.user) != "HR":
        return redirect("employee_dashboard")

    today         = date.today()
    current_year  = timezone.now().year
    current_month = timezone.now().month
    filter_period = request.GET.get("period", "30")

    if filter_period == "month":
        joiners = User.objects.exclude(is_superuser=True).filter(
            date_joined__year=current_year,
            date_joined__month=current_month
        )
    elif filter_period == "year":
        joiners = User.objects.exclude(is_superuser=True).filter(
            date_joined__year=current_year
        )
    else:
        since   = today - timedelta(days=30)
        joiners = User.objects.exclude(is_superuser=True).filter(
            date_joined__date__gte=since
        )

    joiners = joiners.select_related("role", "department").order_by("-date_joined")

    context = {
        **_hr_base_context(request),
        "joiners":       joiners,
        "joiners_count": joiners.count(),
        "filter_period": filter_period,
    }
    return render(request, "hr_new_joiners.html", context)


# ════════════════════════════════════════════════════════════════════
#  HR — DEPARTMENTS OVERVIEW
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_departments(request):
    if get_user_role(request.user) != "HR":
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
#  HR — MY LEAVE BALANCE
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_my_leave_balance(request):
    if get_user_role(request.user) != "HR":
        return redirect("employee_dashboard")

    balance, _ = LeaveBalance.objects.get_or_create(employee=request.user)
    my_leaves  = LeaveRequest.objects.filter(employee=request.user).order_by("-created_at")

    context = {
        **_hr_base_context(request),
        "balance":        balance,
        "my_leaves":      my_leaves[:10],
        "approved_count": my_leaves.filter(status="APPROVED").count(),
        "rejected_count": my_leaves.filter(status="REJECTED").count(),
        "pending_count":  my_leaves.filter(status="PENDING").count(),
    }
    return render(request, "hr_my_leave_balance.html", context)


# ════════════════════════════════════════════════════════════════════
#  HR — EMPLOYEE LIST
# ════════════════════════════════════════════════════════════════════

@login_required
def hr_employee_list(request):
    role_name = get_user_role(request.user)
    if role_name not in ("HR", "Manager", "Admin") and not request.user.is_superuser:
        messages.error(request, "You don't have permission to view this page.")
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
            Q(first_name__icontains=search_query)       |
            Q(last_name__icontains=search_query)        |
            Q(email__icontains=search_query)            |
            Q(username__icontains=search_query)         |
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
            employee=emp,
            status="APPROVED",
            start_date__lte=today,
            end_date__gte=today
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
        status="APPROVED",
        start_date__lte=today,
        end_date__gte=today
    ).values("employee").distinct().count()

    context = {
        **_hr_base_context(request),
        "employee_data":  employee_data,
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
#  MANAGER DASHBOARD
#  Shows ALL employees' PENDING leave requests
# ════════════════════════════════════════════════════════════════════

@login_required
def manager_dashboard(request):
    if get_user_role(request.user) != "Manager":
        return redirect("employee_dashboard")

    from django.core.paginator import Paginator
    today        = date.today()
    current_year = timezone.now().year

    # ── 1. Direct Team Overview ──────────────────────────────────
    team_members = User.objects.filter(
        reporting_manager=request.user
    ).select_related("role", "department")

    # ── 2. Pending Approvals Queue ───────────────────────────────
    # Managers can approve anyone, but we'll show their team first
    team_pending = LeaveRequest.objects.filter(
        status="PENDING",
        employee__reporting_manager=request.user
    ).select_related("employee", "employee__department").order_by("-created_at")

    other_pending = LeaveRequest.objects.filter(
        status="PENDING"
    ).exclude(
        employee__reporting_manager=request.user
    ).select_related("employee", "employee__department").order_by("-created_at")

    # For the "Pending" tab, combine them or just show all with pagination
    all_pending = LeaveRequest.objects.filter(
        status="PENDING"
    ).select_related("employee", "employee__department").order_by("-created_at")
    
    pending_page = Paginator(all_pending, 8).get_page(request.GET.get("page", 1))

    # ── 3. Team On-Leave Today ────────────────────────────────────
    team_on_leave = LeaveRequest.objects.filter(
        status="APPROVED",
        employee__reporting_manager=request.user,
        start_date__lte=today,
        end_date__gte=today
    ).select_related("employee")

    # ── 4. Team History ───────────────────────────────────────────
    team_history_qs = LeaveRequest.objects.filter(
        employee__reporting_manager=request.user,
        start_date__year=current_year
    ).select_related("employee").order_by("-created_at")
    
    team_history_page = Paginator(team_history_qs, 10).get_page(request.GET.get("hpage", 1))

    # ── 5. My Leave History ───────────────────────────────────────
    my_leaves_qs = LeaveRequest.objects.filter(
        employee=request.user
    ).order_by("-created_at")
    
    my_leaves_page = Paginator(my_leaves_qs, 10).get_page(request.GET.get("mypage", 1))

    # ── 6. Per-member balance summary ─────────────────────────────
    team_data = []
    for member in team_members:
        member_leaves = LeaveRequest.objects.filter(employee=member, start_date__year=current_year)
        bal, _ = LeaveBalance.objects.get_or_create(employee=member)
        team_data.append({
            "member":         member,
            "total_leaves":   member_leaves.count(),
            "approved":       member_leaves.filter(status="APPROVED").count(),
            "pending":        member_leaves.filter(status="PENDING").count(),
            "casual_balance": bal.casual_leave or 0,
            "sick_balance":   bal.sick_leave or 0,
            "is_on_leave":    team_on_leave.filter(employee=member).exists(),
        })

    unread = Notification.objects.filter(user=request.user, read_status=False).count()

    context = {
        # Tab Content
        "pending_page":      pending_page,
        "team_pending":      team_pending,
        "other_pending":     other_pending,
        "pending_count":     all_pending.count(),
        
        "team_members":      team_members,
        "team_data":         team_data,
        "team_count":        team_members.count(),
        "team_on_leave":     team_on_leave,
        "team_on_leave_count": team_on_leave.count(),
        
        "team_history_page": team_history_page,
        "my_leaves_page":    my_leaves_page,
        
        # Sidebar/Meta
        "notification_count": unread,
        "unread_count":       unread, # base.html uses unread_count
        "current_year":       current_year,
        "profile":            _build_profile_context(request.user),
    }

    return render(request, "manager_dashboard.html", context)



# ════════════════════════════════════════════════════════════════════
#  ADMIN DASHBOARD
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
            Q(first_name__icontains=search_query)       |
            Q(last_name__icontains=search_query)        |
            Q(email__icontains=search_query)            |
            Q(username__icontains=search_query)         |
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
        .values(
            "employee",
            "employee__first_name",
            "employee__last_name",
            "employee__department__name",
        )
        .annotate(total_days=Sum(
            Case(
                When(duration="FULL",  then=Value(1.0)),
                When(duration="HALF",  then=Value(0.5)),
                When(duration="SHORT", then=Value(0.25)),
                default=Value(0.0),
                output_field=FloatField()
            )
        ))
        .order_by("-total_days")[:7]
    )

    context = {
        "employees":        page_obj.object_list,
        "page_obj":         page_obj,
        "current_tab":      tab,
        "search_query":     search_query,
        "total_employees":  all_qs.count(),
        "active_count":     all_qs.filter(is_active=True).count(),
        "inactive_count":   all_qs.filter(is_active=False).count(),
        "pending_count":    LeaveRequest.objects.filter(status="PENDING").count(),
        "roles":            Role.objects.exclude(name="Admin"),
        "departments":      Department.objects.annotate(employee_count=Count("user")).order_by("name"),
        "recent_joined":    User.objects.exclude(is_superuser=True).select_related("role").order_by("-date_joined")[:5],
        "recent_approved":  LeaveRequest.objects.filter(status="APPROVED").select_related("employee").order_by("-updated_at")[:5],
        "recent_rejected":  LeaveRequest.objects.filter(status="REJECTED").select_related("employee").order_by("-updated_at")[:5],
        "top_leave_takers": top_leave_takers,
        "profile":          _build_profile_context(request.user),
    }
    return render(request, "admin_dashboard.html", context)


# ════════════════════════════════════════════════════════════════════
#  EMPLOYEE LIST  (full page — Admin / HR sidebar)
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
    if tab == "active":
        qs = qs.filter(is_active=True)
    elif tab == "inactive":
        qs = qs.filter(is_active=False)
    if dept_filter:
        qs = qs.filter(department__id=dept_filter)
    if search_query:
        qs = qs.filter(
            Q(first_name__icontains=search_query)       |
            Q(last_name__icontains=search_query)        |
            Q(email__icontains=search_query)            |
            Q(username__icontains=search_query)         |
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
        "departments":     Department.objects.annotate(employee_count=Count("user")).order_by("name"),
    }
    return render(request, "employee_list.html", context)


# ════════════════════════════════════════════════════════════════════
#  LIVE SEARCH AJAX
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
    if tab == "active":
        qs = qs.filter(is_active=True)
    elif tab == "inactive":
        qs = qs.filter(is_active=False)
    if query:
        qs = qs.filter(
            Q(first_name__icontains=query)              |
            Q(last_name__icontains=query)               |
            Q(email__icontains=query)                   |
            Q(username__icontains=query)                |
            Q(department__name__icontains=query)        |
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
#  APPROVE LEAVE
#  • TL  → can approve only their direct team (reporting_manager=request.user)
#  • HR  → can approve anyone
#  • Manager → can approve anyone
#  • Admin/superuser → can approve anyone
#  • First to act wins — leave.status must be PENDING
# ════════════════════════════════════════════════════════════════════

@login_required
def approve_leave(request, leave_id):
    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER", "/"))

    leave     = get_object_or_404(LeaveRequest, id=leave_id)
    role_name = get_user_role(request.user)
    is_admin  = request.user.is_superuser or role_name == "Admin"

    # ── Authorization check ───────────────────────────────────────
    if role_name == "TL":
        if leave.employee.reporting_manager != request.user:
            messages.error(request, "You can only approve leaves for your own team.")
            return redirect(request.META.get("HTTP_REFERER", "/"))

    elif role_name not in ("HR", "Manager") and not is_admin:
        messages.error(request, "You are not authorized to approve leaves.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # ── Guard: already actioned ───────────────────────────────────
    if leave.status != "PENDING" and not is_admin:
        messages.error(request, "This leave has already been actioned.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # ── Approve & deduct balance ──────────────────────────────────
    leave.status = "APPROVED"
    leave.save()

    balance, _ = LeaveBalance.objects.get_or_create(employee=leave.employee)
    days = calculate_leave_days(leave)
    if leave.leave_type == "CASUAL":
        balance.casual_leave = (balance.casual_leave or 0) - days
    elif leave.leave_type == "SICK":
        balance.sick_leave = (balance.sick_leave or 0) - days
    balance.save()

    approver_label = role_name if role_name else "Admin"
    Notification.objects.create(
        user    = leave.employee,
        message = f"Your leave request was approved by {approver_label}."
    )

    messages.success(request, "Leave approved successfully.")
    return redirect(request.META.get("HTTP_REFERER", "/"))


# ════════════════════════════════════════════════════════════════════
#  REJECT LEAVE  — same permission rules as approve_leave
# ════════════════════════════════════════════════════════════════════

@login_required
def reject_leave(request, leave_id):
    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER", "/"))

    leave     = get_object_or_404(LeaveRequest, id=leave_id)
    role_name = get_user_role(request.user)
    is_admin  = request.user.is_superuser or role_name == "Admin"

    if role_name == "TL":
        if leave.employee.reporting_manager != request.user:
            messages.error(request, "You can only reject leaves for your own team.")
            return redirect(request.META.get("HTTP_REFERER", "/"))

    elif role_name not in ("HR", "Manager") and not is_admin:
        messages.error(request, "You are not authorized to reject leaves.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    if leave.status != "PENDING" and not is_admin:
        messages.error(request, "This leave has already been actioned.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    leave.status = "REJECTED"
    leave.save()

    Notification.objects.create(
        user    = leave.employee,
        message = f"Your leave request was rejected by {role_name or 'Admin'}."
    )

    messages.warning(request, "Leave rejected.")
    return redirect(request.META.get("HTTP_REFERER", "/"))


# ════════════════════════════════════════════════════════════════════
#  NOTIFICATIONS
# ════════════════════════════════════════════════════════════════════

@login_required
def notifications(request):
    notes = Notification.objects.filter(user=request.user).order_by("-created_at")
    notes.filter(read_status=False).update(read_status=True)
    return render(request, "notification.html", {"notifications": notes})


# ════════════════════════════════════════════════════════════════════
#  EMPLOYEE DETAIL / CREATE / TOGGLE STATUS
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

        # 🔹 CHECK IF USERNAME ALREADY EXISTS
        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists. Please choose another.")
            return redirect("admin_dashboard" if request.user.is_superuser else "hr_dashboard")

        # 🔹 DATA EXTRACTION
        dept_id        = request.POST.get("department_id")
        manager_email  = request.POST.get("reporting_manager_email")
        role_id        = request.POST.get("role_id")

        # 🔹 LOOKUP OBJECTS
        manager_user = None
        if manager_email:
            manager_user = User.objects.filter(email=manager_email).first()
            if not manager_user:
                messages.warning(request, f"Reporting Manager with email '{manager_email}' not found.")

        dept_obj = None
        if dept_id:
            try:
                dept_obj = Department.objects.get(id=dept_id)
            except Department.DoesNotExist:
                pass

        try:
            employee_role = (
                Role.objects.get(id=role_id) if role_id
                else Role.objects.get(name="Employee")
            )
        except Role.DoesNotExist:
            employee_role = None

        # 🔹 CREATE USER
        User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=request.POST.get("first_name", ""),
            last_name=request.POST.get("last_name", ""),
            role=employee_role,
            reporting_manager=manager_user,
            department=dept_obj,
        )

        messages.success(request, "Employee created successfully.")

    return redirect("admin_dashboard" if request.user.is_superuser else "hr_dashboard")

@login_required
def toggle_employee_status(request, user_id):
    employee = get_object_or_404(User, id=user_id)
    if request.method == "POST" and (
        request.user.is_superuser or get_user_role(request.user) == "Admin"
    ):
        employee.is_active = not employee.is_active
        employee.save()
    return redirect(request.META.get("HTTP_REFERER", "/"))


# ════════════════════════════════════════════════════════════════════
#  HOLIDAY VIEWS  (only available if Holiday model exists)
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
            Q(name__icontains=search) | Q(description__icontains=search)
        )

    today    = datetime.now().date()
    upcoming = Holiday.objects.filter(date__gte=today, is_active=True).order_by("date")[:5]

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
    return render(request, "leaves/holiday_list.html", context)


@login_required
def holiday_create(request):
    if not HOLIDAYS_ENABLED:
        return redirect("hr_dashboard")

    if request.method == "POST":
        name         = request.POST.get("name")
        date_str     = request.POST.get("date")
        end_date     = request.POST.get("end_date") or date_str
        holiday_type = request.POST.get("holiday_type")
        is_half_day  = request.POST.get("is_half_day") == "on"

        if Holiday.objects.filter(name=name, date=date_str).exists():
            messages.error(request, f"Holiday '{name}' already exists on {date_str}.")
            return redirect("holiday_create")

        Holiday.objects.create(
            name=name,
            description=request.POST.get("description", ""),
            holiday_type=holiday_type,
            date=date_str,
            end_date=end_date,
            is_recurring=request.POST.get("is_recurring") == "on",
            is_half_day=is_half_day,
            half_day_type=request.POST.get("half_day_type") if is_half_day else None,
            applicable_to_all=request.POST.get("applicable_to_all") == "on",
            created_by=request.user,
        )
        messages.success(request, f"Holiday '{name}' created successfully!")
        if "save_and_add" in request.POST:
            return redirect("holiday_create")
        return redirect("holiday_list")

    context = {
        **_hr_base_context(request),
        "holiday_types": Holiday.HOLIDAY_TYPES,
        "today":         datetime.now().date(),
        "current_year":  datetime.now().year,
    }
    return render(request, "leaves/holiday_form.html", context)


@login_required
def holiday_edit(request, holiday_id):
    if not HOLIDAYS_ENABLED:
        return redirect("hr_dashboard")

    holiday = get_object_or_404(Holiday, id=holiday_id)

    if request.method == "POST":
        holiday.name              = request.POST.get("name")
        holiday.description       = request.POST.get("description", "")
        holiday.holiday_type      = request.POST.get("holiday_type")
        holiday.date              = request.POST.get("date")
        holiday.end_date          = request.POST.get("end_date") or holiday.date
        holiday.is_recurring      = request.POST.get("is_recurring") == "on"
        holiday.is_half_day       = request.POST.get("is_half_day") == "on"
        holiday.half_day_type     = request.POST.get("half_day_type") if holiday.is_half_day else None
        holiday.applicable_to_all = request.POST.get("applicable_to_all") == "on"
        holiday.is_active         = request.POST.get("is_active") == "on"
        holiday.save()
        messages.success(request, f"Holiday '{holiday.name}' updated successfully!")
        return redirect("holiday_list")

    context = {
        **_hr_base_context(request),
        "holiday":       holiday,
        "holiday_types": Holiday.HOLIDAY_TYPES,
    }
    return render(request, "leaves/holiday_form.html", context)


@login_required
def holiday_delete(request, holiday_id):
    if not HOLIDAYS_ENABLED:
        return redirect("hr_dashboard")
    holiday = get_object_or_404(Holiday, id=holiday_id)
    if request.method == "POST":
        name = holiday.name
        holiday.delete()
        messages.success(request, f"Holiday '{name}' deleted successfully!")
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
                            date=parsed_date, created_by=request.user, is_recurring=True
                        )
                        created += 1
                    else:
                        skipped += 1
                except Exception as e:
                    errors.append(f"Error: {line} — {e}")

        for err in errors[:5]:
            messages.error(request, err)
        messages.success(request, f"Created {created} holidays. Skipped {skipped} duplicates.")
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
    return render(request, "leaves/holiday_bulk_form.html", context)


@login_required
def holiday_toggle_status(request, holiday_id):
    if not HOLIDAYS_ENABLED:
        return redirect("hr_dashboard")
    holiday = get_object_or_404(Holiday, id=holiday_id)
    holiday.is_active = not holiday.is_active
    holiday.save()
    status_word = "activated" if holiday.is_active else "deactivated"
    messages.success(request, f"Holiday '{holiday.name}' {status_word}.")
    return redirect("holiday_list")


@login_required
def public_holidays(request):
    if not HOLIDAYS_ENABLED:
        return redirect("employee_dashboard")

    year     = int(request.GET.get("year", datetime.now().year))
    holidays = Holiday.objects.filter(is_active=True, date__year=year).order_by("date")
    today    = datetime.now().date()
    upcoming = Holiday.objects.filter(date__gte=today, is_active=True).order_by("date")[:10]

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
        "type_stats":     holidays.values("holiday_type").annotate(count=Count("id")).order_by("-count"),
        "months":         [(i, month_name[i]) for i in range(1, 13)],
        "holiday_types":  dict(Holiday.HOLIDAY_TYPES) if HOLIDAYS_ENABLED else {},
    }
    return render(request, "leaves/public_holidays.html", context)


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