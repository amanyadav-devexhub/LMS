# leaves/decorators.py
from django.shortcuts import redirect
from django.contrib import messages
from functools import wraps
from django.http import JsonResponse, HttpResponse
from django.urls import reverse, NoReverseMatch

from users.rbac import log_access_attempt, user_has_permission


VIEW_PERMISSION_MAP = {
    "unified_dashboard": ["dashboard_admin", "dashboard_hr", "dashboard_manager", "dashboard_tl", "dashboard_employee"],
    "unified_dashboard_api": ["dashboard_admin", "dashboard_hr", "dashboard_manager", "dashboard_tl", "dashboard_employee"],
    "employee_dashboard": ["dashboard_employee"],
    "employee_dashboard_api": ["dashboard_employee"],
    "hr_dashboard": ["dashboard_hr"],
    "hr_dashboard_api": ["dashboard_hr"],
    "manager_dashboard": ["dashboard_manager"],
    "manager_dashboard_api": ["dashboard_manager"],
    "tl_dashboard": ["dashboard_tl"],
    "tl_dashboard_api": ["dashboard_tl"],
    "employee_leave_balance": ["leave_balance_view", "leave_view_own"],
    "employee_leave_balance_api": ["leave_balance_view", "leave_view_own"],
    "apply_leave": ["leave_apply"],
    "apply_leave_api": ["leave_apply"],
    "approve_leave": ["leave_approve"],
    "approve_leave_api": ["leave_approve"],
    "reject_leave": ["leave_reject"],
    "reject_leave_api": ["leave_reject"],
    "leave_detail_page": ["leave_view_own", "leave_view_all"],
    "leave_detail_api": ["leave_view_own", "leave_view_all"],
    "tl_dashboard_page": ["dashboard_tl"],
    "hr_pending_leaves": ["leave_view_all", "leave_approve"],
    "hr_pending_leaves_api": ["leave_view_all", "leave_approve"],
    "hr_leave_analytics": ["report_view"],
    "hr_leave_analytics_api": ["report_view"],
    "hr_on_leave_today": ["team_view"],
    "hr_on_leave_today_api": ["team_view"],
    "hr_new_joiners": ["user_view"],
    "hr_new_joiners_api": ["user_view"],
    "hr_departments": ["team_manage"],
    "hr_departments_api": ["team_manage"],
    "hr_my_leave_balance": ["leave_balance_view", "leave_view_own"],
    "hr_my_leave_balance_api": ["leave_balance_view", "leave_view_own"],
    "hr_employee_list": ["user_view", "team_view"],
    "hr_employee_list_api": ["user_view", "team_view"],
    "manager_pending_leaves": ["leave_view_all", "leave_approve"],
    "manager_dashboard_api": ["dashboard_manager"],
    "manager_leave_balance": ["leave_balance_view", "leave_view_own"],
    "employee_leave_balance": ["leave_balance_view", "leave_view_own"],
    "employee_list": ["user_view"],
    "employee_list_page": ["user_view"],
    "employee_detail": ["user_view"],
    "department_list": ["team_view"],
    "holiday_list": ["holiday_view"],
    "holiday_create": ["holiday_create"],
    "holiday_edit": ["holiday_update"],
    "holiday_delete": ["holiday_delete"],
    "holiday_toggle_status": ["holiday_update"],
    "public_holidays": ["holiday_view"],
    "role_list": ["role_view"],
    "role_create": ["role_create"],
    "role_edit": ["role_update"],
    "role_delete": ["role_delete"],
    "role_permission_list": ["role_view", "permission_view"],
    "role_permission_save": ["role_assign_permissions", "permission_assign"],
    "assign_role": ["user_assign_role"],
    "assign_role_page": ["user_assign_role"],
    "assign_role_bulk": ["user_assign_role", "team_manage"],
    "admin_dashboard_page": ["dashboard_admin"],
    "admin_leave_type_create_page": ["leave_policy_create"],
    "admin_leave_type_edit_page": ["leave_policy_update"],
    "create_employee_api": ["user_create"],
    "admin_settings": ["settings_view"],
    "admin_settings_save": ["settings_update"],
    "toggle_employee_status_api": ["user_activate", "user_deactivate"],
    "admin_leave_policy": ["leave_policy_view", "settings_view"],
    "admin_leave_policy_api": ["leave_policy_view", "settings_view"],
    "leave_policy_unified_api": ["leave_policy_view", "settings_view"],
    "admin_leave_type_save_api": ["settings_update"],
    "admin_leave_type_toggle_api": ["settings_update"],
    "admin_leave_type_delete_api": ["settings_update"],
    "admin_policy_save_api": ["settings_update"],
    "admin_policy_toggle_api": ["settings_update"],
    "admin_policy_delete_api": ["settings_update"],
    "admin_apply_to_all_employees_api": ["settings_update"],
    "notifications": ["notification_view"],
    "notifications_api": ["notification_view"],
    "holiday_list": ["holiday_view"],
    "holiday_list_api": ["holiday_view"],
    "holiday_create": ["holiday_create"],
    "holiday_create_api": ["holiday_create"],
    "holiday_edit": ["holiday_update"],
    "holiday_edit_api": ["holiday_update"],
    "holiday_delete": ["holiday_delete"],
    "holiday_delete_api": ["holiday_delete"],
    "holiday_toggle_status": ["holiday_update"],
    "holiday_toggle_status_api": ["holiday_update"],
    "holiday_bulk_create": ["holiday_create"],
    "holiday_bulk_create_api": ["holiday_create"],
    "public_holidays": ["holiday_view"],
    "public_holidays_api": ["holiday_view"],
    "role_list": ["role_view"],
    "role_create": ["role_create"],
    "role_edit": ["role_update"],
    "role_delete": ["role_delete"],
    "role_permission_list": ["permission_view", "role_view"],
    "role_permission_save": ["role_assign_permissions", "permission_assign"],
    "assign_role": ["user_assign_role"],
    "assign_role_page": ["user_assign_role"],
    "assign_role_bulk": ["user_assign_role", "team_manage"],
}


def _is_api_request(request):
    return (
        request.path.startswith("/api/")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )


def _auth_failure_response(request, message, status, redirect_to="dashboard"):
    log_access_attempt(
        getattr(request, "user", None),
        action=message,
        status="denied",
        request=request,
    )
    if _is_api_request(request):
        return JsonResponse({"success": False, "error": message}, status=status)
    if status >= 403:
        messages.error(request, message)
    # Avoid redirect loops (e.g., dashboard -> dashboard) when permission is missing.
    try:
        target_url = reverse(redirect_to)
    except NoReverseMatch:
        target_url = None
    if target_url and request.path == target_url:
        return HttpResponse(message, status=status)
    return redirect(redirect_to)


def role_required(allowed_roles=None):
    """
    Decorator to check if user has required role.
    Usage: @role_required(['HR', 'Admin'])
    """
    allowed_roles = allowed_roles or []

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return _auth_failure_response(request, "Authentication required.", 401, redirect_to="login")

            permission_codes = VIEW_PERMISSION_MAP.get(view_func.__name__, [])
            # Strict mode for mapped views: access is permission-driven.
            if permission_codes:
                if request.user.is_superuser or any(user_has_permission(request.user, code) for code in permission_codes):
                    log_access_attempt(
                        request.user,
                        action=f"view:{view_func.__name__}",
                        status="allowed",
                        request=request,
                    )
                    return view_func(request, *args, **kwargs)

                return _auth_failure_response(
                    request,
                    "You don't have permission to access this page.",
                    403,
                )

            # Backward-compatible fallback for unmapped views only.
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            if not request.user.role:
                return _auth_failure_response(request, "You don't have a role assigned.", 403)

            if request.user.role.name in allowed_roles:
                log_access_attempt(
                    request.user,
                    action=f"view:{view_func.__name__}",
                    status="allowed",
                    request=request,
                )
                return view_func(request, *args, **kwargs)

            return _auth_failure_response(request, f"Required roles: {', '.join(allowed_roles)}", 403)

        return _wrapped_view

    return decorator

def permission_required(permission_codename):
    """
    Decorator to check if user has specific permission
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return _auth_failure_response(request, "Authentication required.", 401, redirect_to="login")

            if user_has_permission(request.user, permission_codename):
                log_access_attempt(
                    request.user,
                    action=f"permission:{permission_codename}",
                    status="allowed",
                    request=request,
                    permission_code=permission_codename,
                )
                return view_func(request, *args, **kwargs)

            return _auth_failure_response(
                request,
                "You don't have permission to perform this action.",
                403,
            )

        return _wrapped_view

    return decorator


def hr_required(view_func):
    """Specific decorator for HR only"""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _auth_failure_response(request, "Authentication required.", 401, redirect_to="login")

        if user_has_permission(request.user, "system_manage") or (request.user.role and request.user.role.name == 'HR'):
            log_access_attempt(request.user, action="hr_required", status="allowed", request=request)
            return view_func(request, *args, **kwargs)

        return _auth_failure_response(
            request,
            "This page is only accessible to HR personnel.",
            403,
        )

    return _wrapped_view


def admin_required(view_func):
    """Specific decorator for Admin only"""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _auth_failure_response(request, "Authentication required.", 401, redirect_to="login")

        if user_has_permission(request.user, "system_manage") or (request.user.role and request.user.role.name == 'Admin'):
            log_access_attempt(request.user, action="admin_required", status="allowed", request=request)
            return view_func(request, *args, **kwargs)

        return _auth_failure_response(
            request,
            "This page is only accessible to Administrators.",
            403,
        )

    return _wrapped_view
