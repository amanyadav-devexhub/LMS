from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import AccessLog, RBACPermission, RolePermissionAssignment


@dataclass(frozen=True)
class PermissionSeed:
    module: str
    action: str
    codename: str
    name: str
    description: str = ""


PERMISSION_SEEDS: tuple[PermissionSeed, ...] = (
    PermissionSeed("dashboard", "view", "dashboard_admin", "Admin dashboard"),
    PermissionSeed("dashboard", "view", "dashboard_hr", "HR dashboard"),
    PermissionSeed("dashboard", "view", "dashboard_manager", "Manager dashboard"),
    PermissionSeed("dashboard", "view", "dashboard_employee", "Employee dashboard"),
    PermissionSeed("user", "view", "user_view", "View users"),
    PermissionSeed("user", "add", "user_create", "Create users"),
    PermissionSeed("user", "edit", "user_update", "Update users"),
    PermissionSeed("user", "delete", "user_delete", "Delete users"),
    PermissionSeed("user", "manage", "user_activate", "Activate users"),
    PermissionSeed("user", "manage", "user_deactivate", "Deactivate users"),
    PermissionSeed("user", "manage", "user_assign_role", "Assign roles to users"),
    PermissionSeed("system", "role_view", "role_view", "View roles"),
    PermissionSeed("system", "role_create", "role_create", "Create roles"),
    PermissionSeed("system", "role_update", "role_update", "Update roles"),
    PermissionSeed("system", "role_delete", "role_delete", "Delete roles"),
    PermissionSeed("system", "role_assign_permissions", "role_assign_permissions", "Assign permissions to roles"),
    PermissionSeed("system", "permission_view", "permission_view", "View permissions"),
    PermissionSeed("system", "permission_assign", "permission_assign", "Assign permissions"),
    PermissionSeed("leave", "view", "leave_view_own", "View own leave"),
    PermissionSeed("leave", "add", "leave_apply", "Apply for leave"),
    PermissionSeed("leave", "edit", "leave_update_own", "Update own leave"),
    PermissionSeed("leave", "delete", "leave_delete_own", "Delete own leave"),
    PermissionSeed("leave", "manage", "leave_cancel", "Cancel leave"),
    PermissionSeed("leave", "view", "leave_view_all", "View all leave"),
    PermissionSeed("leave", "approve", "leave_approve", "Approve leave"),
    PermissionSeed("leave", "reject", "leave_reject", "Reject leave"),
    PermissionSeed("leave", "view", "leave_policy_view", "View leave policy"),
    PermissionSeed("leave", "add", "leave_policy_create", "Create leave policy"),
    PermissionSeed("leave", "edit", "leave_policy_update", "Update leave policy"),
    PermissionSeed("leave", "delete", "leave_policy_delete", "Delete leave policy"),
    PermissionSeed("leave", "view", "leave_balance_view", "View leave balances"),
    PermissionSeed("leave", "edit", "leave_balance_update", "Update leave balances"),
    PermissionSeed("holiday", "view", "holiday_view", "View holidays"),
    PermissionSeed("holiday", "add", "holiday_create", "Create holidays"),
    PermissionSeed("holiday", "edit", "holiday_update", "Update holidays"),
    PermissionSeed("holiday", "delete", "holiday_delete", "Delete holidays"),
    PermissionSeed("report", "view", "report_view", "View reports"),
    PermissionSeed("report", "manage", "report_export", "Export reports"),
    PermissionSeed("team", "view", "team_view", "View team"),
    PermissionSeed("team", "manage", "team_manage", "Manage team"),
    PermissionSeed("system", "view", "settings_view", "View settings"),
    PermissionSeed("system", "manage", "settings_update", "Update settings"),
    PermissionSeed("system", "view", "audit_view", "View audit logs"),
    PermissionSeed("system", "view", "notification_view", "View notifications"),
    PermissionSeed("system", "view", "salary_view", "View salary details"),
    PermissionSeed("system", "edit", "salary_update", "Update salary details"),
    PermissionSeed("system", "view", "bank_view", "View bank details"),
    PermissionSeed("system", "edit", "bank_update", "Update bank details"),
    PermissionSeed("system", "view", "verification_view", "View verification details"),
    PermissionSeed("system", "edit", "verification_update", "Update verification details"),
)

LEGACY_MATRIX_ACTIONS = {
    # Active matrix modules (must match users.views.MODULES keys)
    "dashboard": {
        "can_view": ["dashboard_admin", "dashboard_hr", "dashboard_manager", "dashboard_employee"],
        "can_create": [],
        "can_edit": [],
        "can_delete": [],
    },
    "leaves": {
        "can_view": ["leave_view_own", "leave_view_all", "leave_balance_view"],
        "can_create": ["leave_apply"],
        "can_edit": ["leave_approve", "leave_reject", "leave_update_own", "leave_cancel"],
        "can_delete": ["leave_delete_own"],
    },
    "leave_type": {
        "can_view": ["leave_policy_view"],
        "can_create": ["leave_policy_create"],
        "can_edit": ["leave_policy_update"],
        "can_delete": ["leave_policy_delete"],
    },
    "leave_policy": {
        "can_view": ["leave_policy_view", "leave_balance_view"],
        "can_create": ["leave_policy_create"],
        "can_edit": ["leave_policy_update", "leave_balance_update"],
        "can_delete": ["leave_policy_delete"],
    },
    "academic_settings": {
        "can_view": ["settings_view"],
        "can_create": [],
        "can_edit": ["settings_update"],
        "can_delete": [],
    },
    "holiday": {
        "can_view": ["holiday_view"],
        "can_create": ["holiday_create"],
        "can_edit": ["holiday_update"],
        "can_delete": ["holiday_delete"],
    },
    "employees": {
        "can_view": ["user_view", "team_view"],
        "can_create": ["user_create"],
        "can_edit": ["user_update", "user_activate", "user_deactivate", "user_assign_role"],
        "can_delete": ["user_delete"],
    },
    "departments": {
        "can_view": ["team_view"],
        "can_create": ["team_manage"],
        "can_edit": ["team_manage"],
        "can_delete": ["team_manage"],
    },
    "salary": {
        "can_view": ["salary_view"],
        "can_create": [],
        "can_edit": ["salary_update"],
        "can_delete": [],
    },
    "bank": {
        "can_view": ["bank_view"],
        "can_create": [],
        "can_edit": ["bank_update"],
        "can_delete": [],
    },
    "verification": {
        "can_view": ["verification_view"],
        "can_create": [],
        "can_edit": ["verification_update"],
        "can_delete": [],
    },
    "reports": {
        "can_view": ["report_view"],
        "can_create": [],
        "can_edit": ["report_export"],
        "can_delete": [],
    },
    "notifications": {
        "can_view": ["notification_view"],
        "can_create": [],
        "can_edit": [],
        "can_delete": [],
    },

    # Legacy aliases kept for backward compatibility with older payload keys.
    "users": {
        "can_view": ["user_view"],
        "can_create": ["user_create"],
        "can_edit": ["user_update", "user_activate", "user_deactivate", "user_assign_role"],
        "can_delete": ["user_delete"],
    },
    "roles": {
        "can_view": ["role_view"],
        "can_create": ["role_create"],
        "can_edit": ["role_update", "role_assign_permissions", "permission_assign"],
        "can_delete": ["role_delete"],
    },
    "leave": {
        "can_view": ["leave_view_own", "leave_view_all"],
        "can_create": ["leave_apply"],
        "can_edit": ["leave_update_own", "leave_cancel"],
        "can_delete": ["leave_delete_own"],
    },
}


DEFAULT_ROLE_PERMISSION_CODES = {
    # Admin
    "Admin": {
        "dashboard_admin",
        "leave_apply", "leave_view_own", "leave_view_all", "leave_balance_view",
        "user_view", "role_view", "permission_view", "notification_view",
    },
    "Administrator": {
        "dashboard_admin",
        "leave_apply", "leave_view_own", "leave_view_all", "leave_balance_view",
        "user_view", "role_view", "permission_view", "notification_view",
    },
    
    # HR - maps to HR Dashboard
    "HR": {
        "dashboard_hr",
        "leave_apply", "leave_view_own", "leave_view_all", "leave_approve", "leave_reject", "leave_balance_view",
        "user_view", "team_view", "team_manage", "report_view", "notification_view",
    },
    "Hr": {
        "dashboard_hr",
        "leave_apply", "leave_view_own", "leave_view_all", "leave_approve", "leave_reject", "leave_balance_view",
        "user_view", "team_view", "team_manage", "report_view", "notification_view",
    },
    "Human Resources": {
        "dashboard_hr",
        "leave_apply", "leave_view_own", "leave_view_all", "leave_approve", "leave_reject", "leave_balance_view",
        "user_view", "team_view", "team_manage", "report_view", "notification_view",
    },
    
    # Manager
    "Manager": {
        "dashboard_manager",
        "leave_view_all", "leave_approve", "leave_reject", "team_view", "notification_view",
    },
    
    # TL - maps to TL Dashboard (same as Manager dashboard but different permission detection)
    "TL": {
        "dashboard_tl",
        "leave_apply", "leave_view_own", "leave_view_all", "leave_approve", "leave_reject", "leave_balance_view", "team_view", "notification_view",
    },
    "Tl": {
        "dashboard_tl",
        "leave_apply", "leave_view_own", "leave_view_all", "leave_approve", "leave_reject", "leave_balance_view", "team_view", "notification_view",
    },
    "Team Lead": {
        "dashboard_tl",
        "leave_apply", "leave_view_own", "leave_view_all", "leave_approve", "leave_reject", "leave_balance_view", "team_view", "notification_view",
    },
    "Team Leader": {
        "dashboard_tl",
        "leave_apply", "leave_view_own", "leave_view_all", "leave_approve", "leave_reject", "leave_balance_view", "team_view", "notification_view",
    },
    "Lead": {
        "dashboard_tl",
        "leave_apply", "leave_view_own", "leave_view_all", "leave_approve", "leave_reject", "leave_balance_view", "team_view", "notification_view",
    },
    
    # Employee
    "Employee": {
        "dashboard_employee",
        "leave_apply", "leave_view_own", "leave_balance_view", "notification_view",
    },
    "Staff": {
        "dashboard_employee",
        "leave_apply", "leave_view_own", "leave_balance_view", "notification_view",
    },
}


def ensure_permission_catalog():
    for seed in PERMISSION_SEEDS:
        RBACPermission.objects.get_or_create(
            codename=seed.codename,
            defaults={
                "module": seed.module,
                "action": seed.action,
                "name": seed.name,
                "description": seed.description,
                "is_active": True,
            },
        )


def _permission_queryset_for_codes(codes: Iterable[str]):
    ensure_permission_catalog()
    return RBACPermission.objects.filter(codename__in=list(codes), is_active=True)


def grant_permissions(role, codenames: Iterable[str]):
    permissions = _permission_queryset_for_codes(codenames)
    RolePermissionAssignment.objects.filter(role=role).delete()

    for permission in permissions:
        RolePermissionAssignment.objects.update_or_create(
            role=role,
            permission=permission,
            defaults={"is_enabled": True},
        )


def sync_matrix_permissions(role, matrix_payload: dict):
    ensure_permission_catalog()
    selected_codes: set[str] = set()

    for module, flags in matrix_payload.items():
        module_flags = LEGACY_MATRIX_ACTIONS.get(module, {})
        for flag_name, codes in module_flags.items():
            if flags.get(flag_name):
                selected_codes.update(codes)

    all_relevant_codes = set()
    for codes_by_flag in LEGACY_MATRIX_ACTIONS.values():
        for codes in codes_by_flag.values():
            all_relevant_codes.update(codes)

    relevant_permissions = RBACPermission.objects.filter(codename__in=all_relevant_codes)

    RolePermissionAssignment.objects.filter(role=role).delete()

    for permission in relevant_permissions:
        RolePermissionAssignment.objects.update_or_create(
            role=role,
            permission=permission,
            defaults={"is_enabled": permission.codename in selected_codes},
        )


def role_has_permission(role, codename: str) -> bool:
    if not role or not role.is_active:
        return False
    ensure_permission_catalog()
    normalized = (codename or "").strip().lower()
    if not normalized:
        return False

    return RolePermissionAssignment.objects.filter(
        role=role,
        permission__codename=normalized,
        permission__is_active=True,
        is_enabled=True,
    ).exists()


def user_has_permission(user, codename: str) -> bool:
    if not user:
        return False
    if getattr(user, "is_superuser", False):
        return True
    role = getattr(user, "role", None)
    return role_has_permission(role, codename)


def get_user_permission_codes(user):
    if not user or not getattr(user, "is_authenticated", False):
        return []
    if getattr(user, "is_superuser", False):
        ensure_permission_catalog()
        return list(RBACPermission.objects.filter(is_active=True).values_list("codename", flat=True))
    role = getattr(user, "role", None)
    if not role or not role.is_active:
        return []
    assignment_rows = list(
        RolePermissionAssignment.objects.filter(
            role=role,
            permission__is_active=True,
        ).values_list("permission__codename", "is_enabled")
    )

    defaults = DEFAULT_ROLE_PERMISSION_CODES.get(getattr(role, "name", ""), set())

    # If this role has no RBAC rows yet, use baseline defaults by role name.
    if not assignment_rows:
        return sorted(defaults)

    assigned_enabled = {code for code, is_enabled in assignment_rows if is_enabled}
    assigned_any = {code for code, _ in assignment_rows}

    # Strict mode for configured roles: once assignment rows exist,
    # frontend visibility must follow admin-saved permissions only.
    return sorted(assigned_enabled)


def log_access_attempt(user, action: str, status: str, request=None, permission_code: str = ""):
    AccessLog.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        action=action,
        permission_code=permission_code,
        path=getattr(request, "path", "") if request else "",
        method=getattr(request, "method", "") if request else "",
        status=status,
        ip_address=(request.META.get("REMOTE_ADDR") if request else None),
        user_agent=(request.META.get("HTTP_USER_AGENT", "") if request else ""),
    )


def menu_permission_flags(user):
    if not user or not getattr(user, "is_authenticated", False):
        return {}

    perms = set(get_user_permission_codes(user))
    permission_codes = {
        seed.codename for seed in PERMISSION_SEEDS
    } | {
        "dashboard_admin",
        "dashboard_hr",
        "dashboard_manager",
        "dashboard_employee",
        "user_view",
        "role_view",
        "leave_apply",
        "leave_view_own",
        "leave_view_all",
        "leave_approve",
        "leave_reject",
        "leave_policy_view",
        "leave_policy_create",
        "leave_policy_update",
        "leave_policy_delete",
        "leave_balance_view",
        "leave_balance_update",
        "holiday_view",
        "report_view",
        "team_view",
        "settings_view",
        "audit_view",
        "user_create",
        "user_update",
        "user_delete",
        "user_activate",
        "user_deactivate",
        "user_assign_role",
        "role_create",
        "role_update",
        "role_delete",
        "role_assign_permissions",
        "permission_view",
        "permission_assign",
        "leave_update_own",
        "leave_delete_own",
        "leave_cancel",
        "holiday_create",
        "holiday_update",
        "holiday_delete",
        "report_export",
        "team_manage",
        "settings_update",
        "notification_view",
        "salary_view",
        "salary_update",
        "bank_view",
        "bank_update",
        "verification_view",
        "verification_update",
    }
    return {code: code in perms for code in sorted(permission_codes)}
