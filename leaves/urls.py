from django.urls import path
from . import views

urlpatterns = [
    # -----------------------------
    # DASHBOARDS
    # -----------------------------
    path('employee_dashboard/', views.employee_dashboard, name='employee_dashboard'),
    path('tl_dashboard/', views.tl_dashboard, name='tl_dashboard'),
    path('hr_dashboard/', views.hr_dashboard, name='hr_dashboard'),
    path('manager_dashboard/', views.manager_dashboard, name='manager_dashboard'),
    path('admin_dashboard/', views.admin_dashboard, name='admin_dashboard'),

    # -----------------------------
    # LEAVE ACTIONS
    # -----------------------------
    path('leave/apply/', views.apply_leave, name='apply_leave'),
    path('leave/approve/<int:leave_id>/', views.approve_leave, name='approve_leave'),
    path('leave/reject/<int:leave_id>/', views.reject_leave, name='reject_leave'),

    # -----------------------------
    # EMPLOYEE MANAGEMENT
    # -----------------------------
    path('employee/<int:pk>/', views.employee_detail, name='employee_detail'),
    path('employee/create/', views.create_employee, name='create_employee'),
    path('employee/toggle-status/<int:user_id>/', views.toggle_employee_status, name='toggle_employee_status'),

    # -----------------------------
    # NOTIFICATIONS
    # -----------------------------
    path('notifications/', views.notifications, name='notifications'),

    # -----------------------------
    # HR / MANAGER API
    # -----------------------------
    path('hr/employees/', views.hr_employee_list, name='hr_employee_list'),
    path('admin/employees/search/', views.admin_employee_search, name='admin_employee_search'),
]