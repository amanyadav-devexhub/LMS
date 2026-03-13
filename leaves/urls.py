from django.urls import path
from . import views

urlpatterns = [
    # ── Dashboards ───────────────────────────────────────────────
    path('employee_dashboard/', views.employee_dashboard, name='employee_dashboard'),
    path('tl_dashboard/',       views.tl_dashboard,       name='tl_dashboard'),
    path('hr_dashboard/',       views.hr_dashboard,       name='hr_dashboard'),
    path('manager_dashboard/',  views.manager_dashboard,  name='manager_dashboard'),
    path('admin_dashboard/',    views.admin_dashboard,    name='admin_dashboard'),

    # ── HR pages ─────────────────────────────────────────────────
    path('hr/pending/',        views.hr_pending_leaves,   name='hr_pending_leaves'),
    path('hr/analytics/',      views.hr_leave_analytics,  name='hr_leave_analytics'),
    path('hr/on-leave-today/', views.hr_on_leave_today,   name='hr_on_leave_today'),
    path('hr/new-joiners/',    views.hr_new_joiners,      name='hr_new_joiners'),
    path('hr/departments/',    views.hr_departments,      name='hr_departments'),
    path('hr/my-balance/',     views.hr_my_leave_balance, name='hr_my_leave_balance'),
    path('hr/employees/',      views.hr_employee_list,    name='hr_employee_list'),
    path('hr/employees/<int:pk>/',       views.employee_detail, name='hr_employee_detail'),
    path('hr/employees/<int:pk>/edit/',  views.employee_detail, name='hr_employee_edit'),

    # ── Leave actions ─────────────────────────────────────────────
    path('leave/apply/',                     views.apply_leave,   name='apply_leave'),
    path('leave/approve/<int:leave_id>/',    views.approve_leave, name='approve_leave'),
    path('leave/reject/<int:leave_id>/',     views.reject_leave,  name='reject_leave'),

    # ── Employee management ───────────────────────────────────────
    path('employee/list/',                        views.employee_list,           name='employee_list'),
    path('employee/<int:pk>/',                    views.employee_detail,         name='employee_detail'),
    path('employee/create/',                      views.create_employee,         name='create_employee'),
    path('employee/toggle-status/<int:user_id>/', views.toggle_employee_status,  name='toggle_employee_status'),

    # ── Notifications ─────────────────────────────────────────────
    path('notifications/', views.notifications, name='notifications'),

    # ── Search ────────────────────────────────────────────────────
    path('admin/employees/search/', views.employee_search_json, name='employee_search_json'),
    path('admin/employees/search/', views.employee_search_json, name='admin_employee_search'),

    # ── Holidays ──────────────────────────────────────────────────
    path('holidays/',                          views.holiday_list,          name='holiday_list'),
    path('holidays/create/',                   views.holiday_create,        name='holiday_create'),
    path('holidays/bulk-create/',              views.holiday_bulk_create,   name='holiday_bulk_create'),
    path('holidays/<int:holiday_id>/edit/',    views.holiday_edit,          name='holiday_edit'),
    path('holidays/<int:holiday_id>/delete/',  views.holiday_delete,        name='holiday_delete'),
    path('holidays/<int:holiday_id>/toggle/',  views.holiday_toggle_status, name='holiday_toggle_status'),
    path('public-holidays/',                   views.public_holidays,       name='public_holidays'),
    path('api/check-today-holiday/',           views.check_today_holiday,   name='check_today_holiday'),
]