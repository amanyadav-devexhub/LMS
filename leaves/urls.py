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
    path('hr/employees/<int:pk>/',      views.employee_detail, name='hr_employee_detail'),
    path('hr/employees/<int:pk>/edit/', views.employee_detail, name='hr_employee_edit'),

    # ── Leave actions ─────────────────────────────────────────────
    path('leave/apply/',                     views.apply_leave,   name='apply_leave'),
    path('leave/approve/<int:leave_id>/',    views.approve_leave, name='approve_leave'),
    path('leave/reject/<int:leave_id>/',     views.reject_leave,  name='reject_leave'),

    # ── Employee management ───────────────────────────────────────
    path('employee/list/',                        views.employee_list,          name='employee_list'),
    path('employee/<int:pk>/',                    views.employee_detail,        name='employee_detail'),
    path('employee/create/',                      views.create_employee,        name='create_employee'),
    path('employee/toggle-status/<int:user_id>/', views.toggle_employee_status, name='toggle_employee_status'),
#     path('leave-detail/<int:leave_id>/', views.employee_leave_detail, name='employee_leave_detail'),
    path('employee/leave-balance/', views.employee_leave_balance, name='employee_leave_balance'),

     path('leave-detail/<int:leave_id>/', views.leave_detail_page, name='leave_detail'),
     path('api/leave-detail/<int:leave_id>/', views.employee_leave_detail, name='employee_leave_detail'),
     

    # ── Notifications ─────────────────────────────────────────────
    path('notifications/', views.notifications, name='notifications'),

    # ── Search ────────────────────────────────────────────────────
    path('admin/employees/search/', views.employee_search_json, name='employee_search_json'),
    path('admin/employees/search/', views.employee_search_json, name='admin_employee_search'),

    # ── Holidays ──────────────────────────────────────────────────
    path('holidays/',                         views.holiday_list,          name='holiday_list'),
    path('holidays/create/',                  views.holiday_create,        name='holiday_create'),
    path('holidays/bulk-create/',             views.holiday_bulk_create,   name='holiday_bulk_create'),
    path('holidays/<int:holiday_id>/edit/',   views.holiday_edit,          name='holiday_edit'),
    path('holidays/<int:holiday_id>/delete/', views.holiday_delete,        name='holiday_delete'),
    path('holidays/<int:holiday_id>/toggle/', views.holiday_toggle_status, name='holiday_toggle_status'),
    path('public-holidays/',                  views.public_holidays,       name='public_holidays'),
    path('api/check-today-holiday/',          views.check_today_holiday,   name='check_today_holiday'),

    # ════════════════════════════════════════════════════════════
    # ── ADMIN — LEAVE POLICY MANAGEMENT (FULL CRUD) ────────────
    # ════════════════════════════════════════════════════════════

    # READ — main page listing all leave types + policies
    path('admin/leave-policy/',
         views.admin_leave_policy,
         name='admin_leave_policy'),

    # LEAVE TYPE — Create + Update (lt_id present in POST = update)
    path('admin/leave-type/save/',
         views.admin_leave_type_save,
         name='admin_leave_type_save'),

    # LEAVE TYPE — Toggle active / inactive
    path('admin/leave-type/<int:lt_id>/toggle/',
         views.admin_leave_type_toggle,
         name='admin_leave_type_toggle'),

    # LEAVE TYPE — Delete
    path('admin/leave-type/<int:lt_id>/delete/',
         views.admin_leave_type_delete,
         name='admin_leave_type_delete'),

    # LEAVE POLICY — Create + Update (policy_id present in POST = update)
    path('admin/policy/save/',
         views.admin_policy_save,
         name='admin_policy_save'),

    # LEAVE POLICY — Toggle active / inactive
    path('admin/policy/<int:policy_id>/toggle/',
         views.admin_policy_toggle,
         name='admin_policy_toggle'),

    # LEAVE POLICY — Delete
    path('admin/policy/<int:policy_id>/delete/',
         views.admin_policy_delete,
         name='admin_policy_delete'),

    # Bulk sync — push all active leave types to every active employee
    path('admin/apply-to-all/',
         views.admin_apply_to_all_employees,
         name='admin_apply_to_all_employees'),

    # API — active leave types + employee remaining balance (JSON)
    path('api/leave-types/',
         views.api_leave_types,
         name='api_leave_types'),

     

     
]