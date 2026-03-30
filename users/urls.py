from django.urls import path
from . import views

from django.conf import settings
from django.conf.urls.static import static



urlpatterns = [
    path('', views.home_view, name='home'),  # Root URL
    path('login/', views.login_view, name='login'),
    path('logout/', views.user_logout, name='logout'),
    path('register/', views.register_view, name='register'),
    # path('dashboard/', views.dashboard_view, name='dashboard'),

    path('forgot-password/', views.forgot_password, name='forgot_password'),
    path('reset-password/<uidb64>/<token>/', views.ResetPasswordAPIView.as_view(), name='reset_password'),
    # ── Dashboard / Profile page (renders dashboard.html) ──
    path('dashboard/',         views.dashboard_template_view, name='dashboard'),
    path('profile/<int:user_id>/', views.dashboard_template_view, name='profile_detail'),

    # ── Profile form POST handler ──
    path('dashboard/update/',  views.update_profile,          name='update_profile'),

    # ── REST API endpoints (used by JS / mobile) ──
    path('api/dashboard/data/',   views.dashboard_data_api,   name='dashboard_data_api'),
    path('api/dashboard/update/', views.dashboard_update_api, name='dashboard_update_api'),

    path('departments/',                views.department_list,   name='department_list'),
    path('departments/create/',         views.department_create, name='department_create'),
    path('departments/<int:pk>/edit/',  views.department_edit,   name='department_edit'),
    path('departments/<int:pk>/delete/',views.department_delete, name='department_delete'),
    path('departments/<int:pk>/',       views.department_detail, name='department_detail'),

    path('roles/',                views.role_list,   name='role_list'),
    path('roles/create/',         views.role_create, name='role_create'),
    path('roles/<int:pk>/edit/',  views.role_edit,   name='role_edit'),
    path('roles/<int:pk>/delete/',views.role_delete, name='role_delete'),

    # ── Permissions (Admin only) ─────────────────────────────────
    path('permissions/',          views.role_permission_list, name='role_permission_list'),
    path('permissions/save/',     views.role_permission_save, name='role_permission_save'),

    # ── Assign Roles (Admin only) ────────────────────────────────
    path('assign-role/',          views.assign_role,      name='assign_role'),
    path('assign-role/bulk/',     views.assign_role_bulk, name='assign_role_bulk'),

 

]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)