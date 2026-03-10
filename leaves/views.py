from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.conf import settings
from django.urls import reverse
from datetime import date


from .models import LeaveRequest, LeaveBalance, Notification
from users.models import Role

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .pagination import EmployeePagination
from users.serializers import HREmployeeSerializer

User = get_user_model()

# ---------------------------
# Helper Functions
# ---------------------------

def get_user_role(user):
    return user.role.name.strip().upper() if user.role else ""

def calculate_leave_days(leave):
    if leave.leave_type == "SHORT":
        return leave.short_hours / 8
    return (leave.end_date - leave.start_date).days + 1

def send_notification(users, message):
    for u in users:
        Notification.objects.create(user=u, message=message)


# ---------------------------
# EMPLOYEE DASHBOARD
# ---------------------------

@login_required
def employee_dashboard(request):
    leaves = LeaveRequest.objects.filter(employee=request.user)
    balance, _ = LeaveBalance.objects.get_or_create(employee=request.user)
    notification_count = Notification.objects.filter(user=request.user, read_status=False).count()
    return render(request, "employee_dashboard.html", {
        "leaves": leaves,
        "balance": balance,
        "notification_count": notification_count
    })


# ---------------------------
# APPLY LEAVE
# ---------------------------

@login_required
def apply_leave(request):
    if request.method == "POST":
        leave_type = request.POST.get("leave_type")  # CASUAL, SICK, SHORT
        duration = request.POST.get("duration")      # FULL, HALF, SHORT
        start_date = request.POST.get("start_date")
        end_date = request.POST.get("end_date")
        reason = request.POST.get("reason")
        short_session = request.POST.get("short_session")  # AM or PM
        short_hours = request.POST.get("short_hours")      # number of hours for short leave

        role_name = get_user_role(request.user)

        # Determine next approver
        if role_name == "EMPLOYEE":
            status = "TL_PENDING"
        elif role_name == "TL":
            status = "HR_PENDING"
        elif role_name == "HR":
            status = "MANAGER_PENDING"
        else:
            status = "TL_PENDING"

        # Handle duration logic
        if duration in ["FULL", "HALF"]:
            end_date = start_date  # Full/Half day leaves end on the same day
            short_session = None
            short_hours = None
        elif duration == "SHORT":
            if not short_session:
                short_session = "AM"  # default session
            if not short_hours:
                short_hours = 4       # default hours for short leave

        # Create leave request
        leave = LeaveRequest.objects.create(
            employee=request.user,
            leave_type=leave_type,
            duration=duration,
            start_date=start_date,
            end_date=end_date,
            reason=reason,
            short_session=short_session if duration == "SHORT" else None,
            short_hours=short_hours if duration == "SHORT" else None,
            status=status
        )

        # Notify next role
        next_role_mapping = {
            "TL_PENDING": "TL",
            "HR_PENDING": "HR",
            "MANAGER_PENDING": "Manager"
        }
        next_role_name = next_role_mapping.get(status)
        if next_role_name:
            try:
                next_role = Role.objects.get(name__iexact=next_role_name)
                next_users = User.objects.filter(role=next_role)
                send_notification(next_users, f"New leave request from {request.user.username}")
            except Role.DoesNotExist:
                pass

        # Redirect based on role
        if role_name == "EMPLOYEE":
            return redirect("employee_dashboard")
        elif role_name == "TL":
            return redirect("tl_dashboard")
        elif role_name == "HR":
            return redirect("hr_dashboard")
        else:
            return redirect("dashboard")

    return render(request, "apply_leave.html")

# ---------------------------
# TL DASHBOARD
# ---------------------------

@login_required
def tl_dashboard(request):
    if get_user_role(request.user) != "TL":
        return redirect("employee_dashboard")

    leaves = LeaveRequest.objects.filter(
        status="TL_PENDING",
        employee__reporting_manager=request.user
    )
    notification_count = Notification.objects.filter(user=request.user, read_status=False).count()
    return render(request, "tl_dashboard.html", {
        "leaves": leaves,
        "notification_count": notification_count
    })


# ---------------------------
# HR DASHBOARD
# ---------------------------

@login_required
def hr_dashboard(request):
    if get_user_role(request.user) != "HR":
        return redirect("employee_dashboard")

    leaves = LeaveRequest.objects.filter(
        status="HR_PENDING",
        employee__department__hr=request.user
    )
    notification_count = Notification.objects.filter(user=request.user, read_status=False).count()
    return render(request, "hr_dashboard.html", {
        "leaves": leaves,
        "notification_count": notification_count
    })


# ---------------------------
# MANAGER DASHBOARD
# ---------------------------

@login_required
def manager_dashboard(request):
    if get_user_role(request.user) != "MANAGER":
        return redirect("employee_dashboard")

    leaves = LeaveRequest.objects.filter(
        status="MANAGER_PENDING",
        employee__reporting_manager__reporting_manager=request.user
    )
    notification_count = Notification.objects.filter(user=request.user, read_status=False).count()
    return render(request, "manager_dashboard.html", {
        "leaves": leaves,
        "notification_count": notification_count
    })


# ---------------------------
# ADMIN DASHBOARD
# ---------------------------
from django.db.models import Q
@login_required
def admin_dashboard(request):
    if not request.user.is_superuser:
        return redirect("employee_dashboard")

    tab          = request.GET.get("tab",    "all")
    search_query = request.GET.get("search", "").strip()

    # ── Base queryset (exclude superusers) ──────────────────────
    employees = User.objects.exclude(is_superuser=True).select_related('role', 'department')

    # ── Search: first name, last name, full name, email, username ──
    if search_query:
        employees = employees.filter(
            Q(first_name__icontains=search_query)  |
            Q(last_name__icontains=search_query)   |
            Q(email__icontains=search_query)        |
            Q(username__icontains=search_query)     |
            Q(department__name__icontains=search_query) |
            Q(role__name__icontains=search_query)
        )

    # ── Tab filter ───────────────────────────────────────────────
    if tab == "active":
        employees = employees.filter(is_active=True)
    elif tab == "inactive":
        employees = employees.filter(is_active=False)

    # ── Order by latest joined ───────────────────────────────────
    employees = employees.order_by('-date_joined')

    # ── Last 5 recently added employees (always from full list) ──
    recent_employees = (
        User.objects
        .exclude(is_superuser=True)
        .select_related('role', 'department')
        .order_by('-date_joined')[:5]
    )

    leaves = LeaveRequest.objects.all().order_by("-start_date")

    context = {
        "employees":        employees,
        "recent_employees": recent_employees,
        "leaves":           leaves,
        "current_tab":      tab,
        "search_query":     search_query,
        "total_employees":  User.objects.exclude(is_superuser=True).count(),
        "active_count":     User.objects.filter(is_active=True).exclude(is_superuser=True).count(),
        "inactive_count":   User.objects.filter(is_active=False).exclude(is_superuser=True).count(),
        "roles":            Role.objects.exclude(name="Admin"),
    }

    return render(request, "admin_dashboard.html", context)


# ---------------------------
# APPROVE / REJECT LEAVE (ALL ROLES)
# ---------------------------

@login_required
def approve_leave(request, leave_id):
    leave = get_object_or_404(LeaveRequest, id=leave_id)
    role_name = get_user_role(request.user)

    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER"))

    if role_name == "TL" and leave.status == "TL_PENDING":
        leave.status = "HR_PENDING"
        leave.save()
        hr_users = User.objects.filter(role__name__iexact="HR")
        send_notification(hr_users, f"Leave from {leave.employee.username} needs approval.")
        Notification.objects.create(user=leave.employee, message="Your leave approved by TL.")

    elif role_name == "HR" and leave.status == "HR_PENDING":
        leave.status = "MANAGER_PENDING"
        leave.save()
        manager_users = User.objects.filter(role__name__iexact="MANAGER")
        send_notification(manager_users, f"Leave from {leave.employee.username} needs approval.")
        Notification.objects.create(user=leave.employee, message="Your leave approved by HR.")

    elif role_name == "MANAGER" and leave.status == "MANAGER_PENDING":
        leave.status = "APPROVED"
        leave.save()
        balance, _ = LeaveBalance.objects.get_or_create(employee=leave.employee)
        days = calculate_leave_days(leave)
        if leave.leave_type == "CASUAL":
            balance.casual_leave -= days
        elif leave.leave_type == "SICK":
            balance.sick_leave -= days
        balance.save()
        Notification.objects.create(user=leave.employee, message="Your leave approved by Manager.")

    elif request.user.is_superuser:
        leave.status = "APPROVED"
        leave.save()
        Notification.objects.create(user=leave.employee, message="Your leave approved by Admin.")

    else:
        messages.error(request, "You are not authorized to approve this leave.")
        return redirect(request.META.get("HTTP_REFERER"))

    messages.success(request, "Leave approved successfully.")
    return redirect(request.META.get("HTTP_REFERER"))


@login_required
def reject_leave(request, leave_id):
    leave = get_object_or_404(LeaveRequest, id=leave_id)
    role_name = get_user_role(request.user)

    valid_action = (
        (role_name == "TL" and leave.status == "TL_PENDING") or
        (role_name == "HR" and leave.status == "HR_PENDING") or
        (role_name == "MANAGER" and leave.status == "MANAGER_PENDING") or
        request.user.is_superuser
    )

    if not valid_action:
        messages.error(request, "You are not authorized to reject this leave.")
        return redirect(request.META.get("HTTP_REFERER"))

    leave.status = "REJECTED"
    leave.save()
    Notification.objects.create(user=leave.employee, message=f"Your leave rejected by {role_name or 'Admin'}.")
    messages.error(request, "Leave rejected.")
    return redirect(request.META.get("HTTP_REFERER"))


# ---------------------------
# NOTIFICATIONS
# ---------------------------

@login_required
def notifications(request):
    notes = Notification.objects.filter(user=request.user).order_by("-created_at")
    notes.filter(read_status=False).update(read_status=True)
    return render(request, "notification.html", {"notifications": notes})


# ---------------------------
# EMPLOYEE DETAIL
# ---------------------------

@login_required
def employee_detail(request, pk):
    employee = get_object_or_404(User, pk=pk)
    return render(request, 'employee_detail.html', {'employee': employee})


# ---------------------------
# CREATE EMPLOYEE
# ---------------------------

@login_required
def create_employee(request):
    if request.method == "POST" and (request.user.is_superuser or get_user_role(request.user) == "HR"):
        username = request.POST["username"]
        email = request.POST["email"]
        password = request.POST["password"]
        first_name = request.POST["first_name"]
        tl_id = request.POST.get("reporting_manager_id")
        role_id = request.POST.get("role_id")  # Get selected role

        tl_user = User.objects.get(id=tl_id) if tl_id else None
        employee_role = Role.objects.get(id=role_id) if role_id else Role.objects.get(name="Employee")

        User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            role=employee_role,
            reporting_manager=tl_user,
            department=tl_user.department if tl_user else None
        )

    return redirect("admin_dashboard" if request.user.is_superuser else "hr_dashboard")

# ---------------------------
# TOGGLE EMPLOYEE STATUS
# ---------------------------

@login_required
def toggle_employee_status(request, user_id):
    employee = get_object_or_404(User, id=user_id)
    if request.method == "POST" and request.user.is_superuser:
        employee.is_active = not employee.is_active
        employee.save()
    return redirect(request.META.get("HTTP_REFERER"))


# ---------------------------
# HR & MANAGER EMPLOYEE LIST API
# ---------------------------

# ══════════════════════════════════════════════════════════════
#  FILE 1: Add this view to your leave/views.py (or users/views.py)
#  Replace the existing hr_employee_list with this
# ══════════════════════════════════════════════════════════════

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from datetime import date


def get_user_role(user):
    """Helper — returns role name string or empty string."""
    return user.role.name if hasattr(user, 'role') and user.role else ''


@login_required
def hr_employee_list(request):
    """
    HTML view for HR / Manager / Admin to see all employees.
    Uses session-based auth (login_required) — no Bearer token needed.
    """
    role_name = get_user_role(request.user)

    # Access control
    if role_name not in ["HR", "Manager", "Admin"] and not request.user.is_superuser:
        messages.error(request, "You don't have permission to view this page.")
        return redirect('employee_dashboard')

    today        = date.today()
    search_query = request.GET.get('search', '').strip()
    dept_filter  = request.GET.get('department', '').strip()
    role_filter  = request.GET.get('role', '').strip()
    status_filter= request.GET.get('status', '').strip()  # 'active' | 'inactive' | 'on_leave'

    # Base queryset — HR sees all except Admin
    employees = (
        User.objects
        .exclude(role__name__iexact='Admin')
        .exclude(is_superuser=True)
        .select_related('role', 'department')
        .order_by('-date_joined')
    )

    # Search: name, email, username, department, role
    if search_query:
        employees = employees.filter(
            Q(first_name__icontains=search_query)       |
            Q(last_name__icontains=search_query)        |
            Q(email__icontains=search_query)            |
            Q(username__icontains=search_query)         |
            Q(department__name__icontains=search_query) |
            Q(role__name__icontains=search_query)
        )

    # Department filter
    if dept_filter:
        employees = employees.filter(department__pk=dept_filter)

    # Role filter
    if role_filter:
        employees = employees.filter(role__pk=role_filter)

    # Build employee data with on_leave flag
    employee_data = []
    for emp in employees:
        on_leave = LeaveRequest.objects.filter(
            employee=emp,
            status='APPROVED',
            start_date__lte=today,
            end_date__gte=today
        ).exists()

        # Status filter (applied post-annotation)
        if status_filter == 'on_leave' and not on_leave:
            continue
        if status_filter == 'active' and (not emp.is_active or on_leave):
            continue
        if status_filter == 'inactive' and emp.is_active:
            continue

        employee_data.append({
            'emp':        emp,
            'on_leave':   on_leave,
            'id':         emp.id,
            'full_name':  emp.get_full_name() or emp.username,
            'email':      emp.email,
            'role':       emp.role.name if emp.role else '—',
            'department': emp.department.name if emp.department else '—',
            'is_active':  emp.is_active,
            'date_joined':emp.date_joined,
        })

    # Counts for stat chips
    all_emps       = User.objects.exclude(role__name__iexact='Admin').exclude(is_superuser=True)
    total_count    = all_emps.count()
    active_count   = all_emps.filter(is_active=True).count()
    inactive_count = all_emps.filter(is_active=False).count()
    on_leave_count = sum(
        1 for e in all_emps
        if LeaveRequest.objects.filter(
            employee=e, status='APPROVED',
            start_date__lte=today, end_date__gte=today
        ).exists()
    )

    from .models import Department, Role  # adjust import path as needed
    context = {
        'employee_data':   employee_data,
        'search_query':    search_query,
        'dept_filter':     dept_filter,
        'role_filter':     role_filter,
        'status_filter':   status_filter,
        'departments':     Department.objects.all().order_by('name'),
        'roles':           Role.objects.exclude(name='Admin').order_by('name'),
        'total_count':     total_count,
        'active_count':    active_count,
        'inactive_count':  inactive_count,
        'on_leave_count':  on_leave_count,
        'result_count':    len(employee_data),
        'viewer_role':     role_name,
    }
    return render(request, 'hr_employee_list.html', context)


# ══════════════════════════════════════════════════════════════
#  FILE 2: urls.py  — add this line
# ══════════════════════════════════════════════════════════════

# path('hr/employees/', views.hr_employee_list, name='hr_employee_list'),


# ══════════════════════════════════════════════════════════════
#  FILE 3: Key fix — base.html sidebar link
#  Change this:
#    href="/leave/hr/employees/"          ← calls the API, needs Bearer token
#  To this:
#    href="{% url 'hr_employee_list' %}"  ← calls the HTML view, uses session
# ══════════════════════════════════════════════════════════════
@login_required
def admin_employee_search(request):
    if not request.user.is_superuser:
        return JsonResponse({"error": "Access denied"}, status=403)

    query = request.GET.get("q", "")
    employees = User.objects.exclude(is_superuser=True)
    if query:
        employees = employees.filter(
            Q(username__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query)
        )

    data = [
        {
            "id": emp.id,
            "name": emp.get_full_name() or emp.username,
            "email": emp.email,
            "role": emp.role.name if emp.role else "—",
            "is_active": emp.is_active,
        }
        for emp in employees[:20]  # limit to 20 results
    ]
    return JsonResponse({"employees": data})