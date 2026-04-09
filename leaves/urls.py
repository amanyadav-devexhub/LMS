# leaves/urls.py
from functools import wraps

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.urls import path
from . import views
from users.rbac import log_access_attempt
from users.rbac import user_has_permission

api_views = views


API_PERMISSION_MAP = {
    "unified_dashboard_api": ["dashboard_admin", "dashboard_hr", "dashboard_manager", "dashboard_employee"],
    "employee_dashboard_api": ["dashboard_employee"],
    "tl_dashboard_api": ["dashboard_manager"],
    "hr_dashboard_api": ["dashboard_hr"],
    "manager_dashboard_api": ["dashboard_manager"],
    "employee_leave_balance_api": ["leave_balance_view", "leave_view_own"],
    "apply_leave_api": ["leave_apply"],
    "leave_detail_api": ["leave_view_own", "leave_view_all"],
    "employee_leave_detail": ["leave_view_own", "leave_view_all"],
    "approve_leave_api": ["leave_approve"],
    "reject_leave_api": ["leave_reject"],
    "hr_pending_leaves_api": ["leave_view_all", "leave_approve"],
    "hr_leave_analytics_api": ["report_view"],
    "hr_on_leave_today_api": ["team_view"],
    "hr_new_joiners_api": ["user_view"],
    "hr_departments_api": ["team_manage"],
    "hr_my_leave_balance_api": ["leave_balance_view", "leave_view_own"],
    "hr_employee_list_api": ["user_view", "team_view"],
    "api_admin_dashboard": ["dashboard_admin"],
    "employee_search_json": ["user_view"],
    "create_employee_api": ["user_create"],
    "employee_detail_api": ["user_view"],
    "update_employee_api": ["user_update"],
    "delete_employee_api": ["user_delete"],
    "toggle_employee_status_api": ["user_activate", "user_deactivate"],
    "admin_leave_policy_api": ["leave_policy_view", "settings_view"],
    "admin_leave_type_save_api": ["leave_policy_create", "leave_policy_update"],
    "admin_leave_type_toggle_api": ["leave_policy_update"],
    "admin_leave_type_delete_api": ["leave_policy_delete"],
    "admin_policy_save_api": ["leave_policy_create", "leave_policy_update"],
    "admin_policy_toggle_api": ["leave_policy_update"],
    "admin_policy_delete_api": ["leave_policy_delete"],
    "admin_apply_to_all_employees_api": ["leave_balance_update", "settings_update"],
    "holiday_list_api": ["holiday_view"],
    "holiday_create_api": ["holiday_create"],
    "holiday_detail_api": ["holiday_view"],
    "holiday_edit_api": ["holiday_update"],
    "holiday_delete_api": ["holiday_delete"],
    "holiday_toggle_status_api": ["holiday_update"],
    "holiday_bulk_create_api": ["holiday_create"],
    "notifications_api": ["notification_view"],
    "api_leave_types": ["leave_apply", "leave_view_own", "leave_view_all"],
}


def api_endpoint(view_func, auth_required=True):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if auth_required and not request.user.is_authenticated:
            log_access_attempt(getattr(request, "user", None), action="api_auth_required", status="denied", request=request)
            return JsonResponse(
                {"success": False, "error": "Authentication required."},
                status=401,
            )

        required_permissions = API_PERMISSION_MAP.get(view_func.__name__, [])
        if required_permissions and not any(user_has_permission(request.user, code) for code in required_permissions):
            log_access_attempt(
                getattr(request, "user", None),
                action=f"api_permission_denied:{view_func.__name__}",
                status="denied",
                request=request,
            )
            return JsonResponse({"success": False, "error": "Access Denied"}, status=403)

        try:
            response = view_func(request, *args, **kwargs)
        except Http404:
            log_access_attempt(getattr(request, "user", None), action="api_not_found", status="denied", request=request)
            return JsonResponse(
                {"success": False, "error": "Requested resource was not found."},
                status=404,
            )
        except PermissionDenied:
            log_access_attempt(getattr(request, "user", None), action="api_permission_denied", status="denied", request=request)
            return JsonResponse(
                {"success": False, "error": "You don't have permission to access this resource."},
                status=403,
            )
        except ValidationError as exc:
            message = exc.message if hasattr(exc, "message") else str(exc)
            return JsonResponse({"success": False, "error": message}, status=400)
        except Exception as exc:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Unexpected server error.",
                    "detail": str(exc),
                },
                status=500,
            )

        content_type = response.get("Content-Type", "")

        if 300 <= response.status_code < 400:
            log_access_attempt(getattr(request, "user", None), action="api_redirect_blocked", status="denied", request=request)
            return JsonResponse(
                {"success": False, "error": "Redirect responses are not allowed for API endpoints."},
                status=403,
            )

        if "application/json" not in content_type:
            return JsonResponse(
                {
                    "success": False,
                    "error": "API endpoint returned a non-JSON response.",
                    "status_code": response.status_code,
                },
                status=500,
            )

        return response

    return _wrapped

urlpatterns = [

    # ════════════════════════════════════════════════════════════════
    #  HTML VIEWS  (kept for backward compatibility / server-side pages)
    # ════════════════════════════════════════════════════════════════

    # ── Dashboards ───────────────────────────────────────────────────
    path('dashboard/',          views.unified_dashboard,      name='dashboard'),
    path('dashboard/<str:tab>',  views.tl_dashboard,           name='tl_dashboard_tab'),
    path('dashboard/<str:tab>/', views.tl_dashboard,           name='tl_dashboard_tab_slash'),
    path('employee_dashboard/', views.employee_dashboard,     name='employee_dashboard'),
    path('tl_dashboard/',       views.tl_dashboard,           name='tl_dashboard'),
    path('hr_dashboard/',       views.hr_dashboard,           name='hr_dashboard'),
    path('manager_dashboard/',  views.manager_dashboard,      name='manager_dashboard'),
    path('manager/pending/',    views.manager_pending_leaves, name='manager_pending_leaves'),
    path('manager/balance/',    views.manager_leave_balance,  name='manager_leave_balance'),
    path('admin_dashboard/',    views.admin_dashboard_page,   name='admin_dashboard'),

    # ── HR pages ─────────────────────────────────────────────────────
    path('pending/',        views.hr_pending_leaves,   name='hr_pending_leaves'),
    path('analytics/',      views.hr_leave_analytics,  name='hr_leave_analytics'),
    path('on-leave-today/', views.hr_on_leave_today,   name='hr_on_leave_today'),
    path('new-joiners/',    views.hr_new_joiners,      name='hr_new_joiners'),
    path('departments/',    views.department_list,     name='department_list'),
    path('hr/departments/', views.hr_departments,      name='hr_departments'),
    path('my-balance/',     views.hr_my_leave_balance, name='hr_my_leave_balance'),
    path('hr/employees/',             views.hr_employee_list, name='hr_employee_list'),

    # ── Leave actions ─────────────────────────────────────────────────
    path('apply/',                              views.apply_leave,   name='apply_leave'),
    path('approve_leave/<int:leave_id>/',       views.approve_leave, name='approve_leave'),
    path('reject/leave/<int:leave_id>/',        views.reject_leave,  name='reject_leave'),
    path('leave-detail/<int:leave_id>/',        views.leave_detail_page,      name='leave_detail'),

    # ── Employee management ───────────────────────────────────────────
    path('employee/list/',                         views.employee_list,          name='employee_list'),
    path('employee/list/page/',                    views.employee_list_page,     name='employee_list_page'),
    path('employee/<int:pk>/',                     views.employee_detail,        name='employee_detail'),
    path('employee/create/',                       views.create_employee,        name='create_employee'),
    path('employee/<int:pk>/update/',              views.update_employee,        name='update_employee'),
    path('employee/<int:pk>/delete/',              views.delete_employee,        name='delete_employee'),
    path('employee/toggle-status/<int:user_id>/', views.toggle_employee_status, name='toggle_employee_status'),
    path('employee/leave-balance/',                views.employee_leave_balance, name='employee_leave_balance'),

    # ── Notifications ─────────────────────────────────────────────────
    path('notifications/', views.notifications, name='notifications'),

    # ── Holidays ──────────────────────────────────────────────────────
    path('holidays/',                           views.holiday_list,          name='holiday_list'),
    path('holidays/create/',                    views.holiday_create,        name='holiday_create'),
    path('holidays/bulk-create/',               views.holiday_bulk_create,   name='holiday_bulk_create'),
    path('holidays/<int:holiday_id>/edit/',     views.holiday_edit,          name='holiday_edit'),
    path('holidays/<int:holiday_id>/delete/',   views.holiday_delete,        name='holiday_delete'),
    path('holidays/<int:holiday_id>/toggle/',   views.holiday_toggle_status, name='holiday_toggle_status'),
    path('public-holidays/',                    views.public_holidays,       name='public_holidays'),

    # ── Admin — Leave policy ───────────────────────────────────────────
    path('leave-policy/',                       views.admin_leave_policy,    name='admin_leave_policy'),
    path('leave-policy/type/new/',             views.admin_leave_type_create_page, name='admin_leave_type_create'),
    path('leave-policy/type/<int:lt_id>/edit/', views.admin_leave_type_edit_page, name='admin_leave_type_edit'),
    path('admin/leave-type/save/',                    views.admin_leave_type_save,         name='admin_leave_type_save'),
    path('admin/leave-type/<int:lt_id>/toggle/',      views.admin_leave_type_toggle,       name='admin_leave_type_toggle'),
    path('admin/leave-type/<int:lt_id>/delete/',      views.admin_leave_type_delete,       name='admin_leave_type_delete'),
    path('admin/policy/save/',                        views.admin_policy_save,             name='admin_policy_save'),
    path('admin/policy/<int:policy_id>/toggle/',      views.admin_policy_toggle,           name='admin_policy_toggle'),
    path('admin/policy/<int:policy_id>/delete/',      views.admin_policy_delete,           name='admin_policy_delete'),
    path('admin/apply-to-all/',                       views.admin_apply_to_all_employees,  name='admin_apply_to_all_employees'),
    path('admin/settings/',                  views.admin_academic_settings,       name='admin_academic_settings'),
    path('admin/settings/save/',             views.admin_academic_settings_save,  name='admin_academic_settings_save'),

    # ════════════════════════════════════════════════════════════════
    #  JSON API VIEWS  (all responses are pure JSON — no HTML)
    #  Prefix: /api/
    # ════════════════════════════════════════════════════════════════

    # ── Auth / Role redirect ──────────────────────────────────────────
    path('api/dashboard/',          api_endpoint(api_views.unified_dashboard_api),  name='api_dashboard'),
    path('api/dashboard/employee/', api_endpoint(api_views.employee_dashboard_api), name='api_employee_dashboard'),
    path('api/dashboard/tl/',       api_endpoint(api_views.tl_dashboard_api),       name='api_tl_dashboard'),
    path('api/dashboard/hr/',       api_endpoint(api_views.hr_dashboard_api),       name='api_hr_dashboard'),
    path('api/dashboard/manager/',  api_endpoint(api_views.manager_dashboard_api),  name='api_manager_dashboard'),

    # ── Leave ─────────────────────────────────────────────────────────
    path('api/leave/balance/',                api_endpoint(api_views.employee_leave_balance_api), name='api_leave_balance'),
    path('api/leave/apply/',                  api_endpoint(api_views.apply_leave_api),            name='api_apply_leave'),
    path('api/leave/types/',                  api_endpoint(views.api_leave_types),                name='api_leave_types'),
    path('api/leave/<int:leave_id>/',         api_endpoint(api_views.leave_detail_api),           name='api_leave_detail'),
    path('api/leave/<int:leave_id>/approve/', api_endpoint(api_views.approve_leave_api),          name='api_approve_leave'),
    path('api/leave/<int:leave_id>/reject/',  api_endpoint(api_views.reject_leave_api),           name='api_reject_leave'),

    # kept from old urls.py — same view, cleaner name
    path('api/leave-detail/<int:leave_id>/',  api_endpoint(views.employee_leave_detail),          name='employee_leave_detail'),

    # ── HR ─────────────────────────────────────────────────────────────
    path('api/hr/pending/',        api_endpoint(api_views.hr_pending_leaves_api),  name='api_hr_pending'),
    path('api/hr/analytics/',      api_endpoint(api_views.hr_leave_analytics_api), name='api_hr_analytics'),
    path('api/hr/on-leave-today/', api_endpoint(api_views.hr_on_leave_today_api),  name='api_hr_on_leave_today'),
    path('api/hr/new-joiners/',    api_endpoint(api_views.hr_new_joiners_api),     name='api_hr_new_joiners'),
    path('api/hr/departments/',    api_endpoint(api_views.hr_departments_api),     name='api_hr_departments'),
    path('api/hr/my-balance/',     api_endpoint(api_views.hr_my_leave_balance_api),name='api_hr_my_balance'),
    path('api/hr/employees/',      api_endpoint(api_views.hr_employee_list_api),   name='api_hr_employees'),

    # ── Admin ──────────────────────────────────────────────────────────
    path('api/admin/dashboard/',                      api_endpoint(views.api_admin_dashboard),                    name='admin_dashboard_api'),
    path('api/admin/employees/search/',               api_endpoint(views.employee_search_json),                   name='employee_search_json'),
    path('api/admin/employees/create/',               api_endpoint(api_views.create_employee_api),                name='api_create_employee'),
    path('api/admin/employees/<int:pk>/',             api_endpoint(api_views.employee_detail_api),                name='api_employee_detail'),
    path('api/admin/employees/<int:pk>/update/',      api_endpoint(api_views.update_employee_api),                name='api_update_employee'),
    path('api/admin/employees/<int:pk>/delete/',      api_endpoint(api_views.delete_employee_api),                name='api_delete_employee'),
    path('api/admin/employees/<int:user_id>/toggle/', api_endpoint(api_views.toggle_employee_status_api),         name='api_toggle_employee'),
    path('api/leave-policy/',                         api_endpoint(api_views.admin_leave_policy_api),             name='api_admin_leave_policy'),
    path('api/admin/leave-type/save/',                api_endpoint(api_views.admin_leave_type_save_api),          name='api_admin_lt_save'),
    path('api/admin/leave-type/<int:lt_id>/toggle/',  api_endpoint(api_views.admin_leave_type_toggle_api),        name='api_admin_lt_toggle'),
    path('api/admin/leave-type/<int:lt_id>/delete/',  api_endpoint(api_views.admin_leave_type_delete_api),        name='api_admin_lt_delete'),
    path('api/admin/policy/save/',                    api_endpoint(api_views.admin_policy_save_api),              name='api_admin_policy_save'),
    path('api/admin/policy/<int:policy_id>/toggle/',  api_endpoint(api_views.admin_policy_toggle_api),            name='api_admin_policy_toggle'),
    path('api/admin/policy/<int:policy_id>/delete/',  api_endpoint(api_views.admin_policy_delete_api),            name='api_admin_policy_delete'),
    path('api/admin/allocations/sync/',               api_endpoint(api_views.admin_apply_to_all_employees_api),   name='api_admin_sync_alloc'),

    # ── Holidays ───────────────────────────────────────────────────────
    path('api/holidays/',                              api_endpoint(api_views.holiday_list_api),          name='api_holiday_list'),
    path('api/holidays/public/',                       api_endpoint(api_views.public_holidays_api, auth_required=False),       name='api_public_holidays'),
    path('api/check-today-holiday/',                   api_endpoint(views.check_today_holiday, auth_required=False),           name='check_today_holiday'),
    path('api/holidays/create/',                       api_endpoint(api_views.holiday_create_api),        name='api_holiday_create'),
    path('api/holidays/bulk-create/',                  api_endpoint(api_views.holiday_bulk_create_api),   name='api_holiday_bulk_create'),
    path('api/holidays/<int:holiday_id>/',             api_endpoint(api_views.holiday_detail_api),        name='api_holiday_detail'),
    path('api/holidays/<int:holiday_id>/edit/',        api_endpoint(api_views.holiday_edit_api),          name='api_holiday_edit'),
    path('api/holidays/<int:holiday_id>/delete/',      api_endpoint(api_views.holiday_delete_api),        name='api_holiday_delete'),
    path('api/holidays/<int:holiday_id>/toggle/',      api_endpoint(api_views.holiday_toggle_status_api), name='api_holiday_toggle'),

    # ── Misc ───────────────────────────────────────────────────────────
    path('api/notifications/', api_endpoint(api_views.notifications_api), name='api_notifications'),
]
