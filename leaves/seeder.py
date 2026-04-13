from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from leaves.models import (
    AcademicLeaveSettings,
    EmployeeLeaveAllocation,
    Holiday,
    LeavePolicy,
    LeaveRequest,
    LeaveTypeConfig,
    Notification,
)
from users.models import Department


User = get_user_model()


def _leave_year_for_date(target_date: date, starting_month: int) -> int:
    return target_date.year if target_date.month >= starting_month else target_date.year - 1


def seed_academic_settings(admin_user) -> dict:
    settings_obj = AcademicLeaveSettings.get_solo()
    settings_obj.leave_year_start_month = 4
    settings_obj.default_casual_quota = 12
    settings_obj.default_sick_quota = 8
    settings_obj.default_annual_quota = 18
    settings_obj.annual_leave_quota = 12
    settings_obj.show_only_monthly_in_balance = True
    settings_obj.working_hours_per_day = 8
    settings_obj.grace_period_minutes = 10
    settings_obj.auto_deduction_enabled = False
    settings_obj.updated_by = admin_user
    settings_obj.save()
    return {"academic_settings_seeded": 1}


def seed_leave_types(admin_user, departments) -> dict:
    leave_type_specs = [
        {
            "name": "Casual Leave",
            "code": "CASUAL",
            "days_per_year": 12,
            "is_paid": True,
            "is_accrual_based": False,
            "monthly_accrual": 1,
            "quota_type": "STANDARD",
            "starting_month": 4,
            "color": "#22c55e",
        },
        {
            "name": "Sick Leave",
            "code": "SICK",
            "days_per_year": 8,
            "is_paid": True,
            "is_accrual_based": False,
            "monthly_accrual": 0.67,
            "quota_type": "STANDARD",
            "starting_month": 4,
            "requires_document": True,
            "document_required_after": 2,
            "color": "#ef4444",
        },
        {
            "name": "Urgent Leave",
            "code": "URGENT",
            "days_per_year": 12,
            "is_paid": True,
            "is_accrual_based": True,
            "monthly_accrual": 1,
            "quota_type": "ANNUAL_POOL",
            "starting_month": 4,
            "color": "#f59e0b",
        },
        {
            "name": "Half Day Leave",
            "code": "HALF_DAY",
            "days_per_year": 6,
            "is_paid": True,
            "is_accrual_based": False,
            "monthly_accrual": 0.5,
            "quota_type": "STANDARD",
            "starting_month": 4,
            "max_consecutive_days": 1,
            "color": "#3b82f6",
        },
        {
            "name": "Short Leave",
            "code": "SHORT_LEAVE",
            "days_per_year": 6,
            "is_paid": True,
            "is_accrual_based": False,
            "monthly_accrual": 0.5,
            "quota_type": "STANDARD",
            "starting_month": 4,
            "max_consecutive_days": 1,
            "color": "#8b5cf6",
        },
        {
            "name": "Marriage Leave",
            "code": "MARRIAGE",
            "days_per_year": 10,
            "is_paid": True,
            "is_accrual_based": False,
            "monthly_accrual": 0,
            "quota_type": "SPECIAL_EVENT",
            "starting_month": 4,
            "requires_document": True,
            "usage_resets_yearly": False,
            "max_lifetime_usage": 10,
            "color": "#ec4899",
        },
    ]

    created_count = 0
    leave_types = {}
    for spec in leave_type_specs:
        code = spec["code"]
        defaults = {
            **spec,
            "description": f"Seeded config for {spec['name']}",
            "created_by": admin_user,
            "is_active": True,
            "applicable_to": "ALL",
        }
        leave_type, created = LeaveTypeConfig.objects.update_or_create(code=code, defaults=defaults)
        leave_types[code] = leave_type
        created_count += int(created)

    ops_holiday, _ = LeaveTypeConfig.objects.update_or_create(
        code="OPS_SPECIAL",
        defaults={
            "name": "Operations Special Leave",
            "description": "Department-scoped leave for Operations team",
            "days_per_year": 4,
            "is_paid": True,
            "is_accrual_based": False,
            "monthly_accrual": 0.33,
            "quota_type": "STANDARD",
            "starting_month": 4,
            "color": "#14b8a6",
            "created_by": admin_user,
            "is_active": True,
            "applicable_to": "DEPARTMENTS",
        },
    )
    ops_department = departments.get("Operations")
    if ops_department:
        ops_holiday.applicable_departments.set([ops_department])
    leave_types["OPS_SPECIAL"] = ops_holiday

    return {"leave_types_created": created_count, "leave_types": leave_types}


def seed_leave_policy(admin_user, departments) -> dict:
    policy, created = LeavePolicy.objects.update_or_create(
        name="Default Company Leave Policy",
        defaults={
            "description": "Default seeded leave policy",
            "max_days_per_request": 10,
            "min_advance_days": 1,
            "weekend_counts_as_leave": False,
            "holiday_counts_as_leave": False,
            "allow_half_day": True,
            "allow_short_leave": True,
            "approval_threshold": 2,
            "is_default": True,
            "is_active": True,
            "created_by": admin_user,
        },
    )
    policy.applicable_departments.set(departments.values())
    return {"leave_policies_created": int(created)}


def seed_holidays(admin_user, departments) -> dict:
    year = timezone.now().year
    holiday_specs = [
        ("New Year Holiday", date(year, 1, 1), "NATIONAL"),
        ("Republic Day", date(year, 1, 26), "NATIONAL"),
        ("Independence Day", date(year, 8, 15), "NATIONAL"),
        ("Gandhi Jayanti", date(year, 10, 2), "NATIONAL"),
        ("Diwali", date(year, 11, 1), "RELIGIOUS"),
    ]

    created_count = 0
    for name, event_date, kind in holiday_specs:
        holiday, created = Holiday.objects.get_or_create(
            name=name,
            date=event_date,
            defaults={
                "holiday_type": kind,
                "description": f"Seeded holiday: {name}",
                "created_by": admin_user,
                "is_active": True,
                "is_recurring": True,
                "applicable_to_all": True,
            },
        )
        if not holiday.applicable_to_all:
            holiday.applicable_departments.set(departments.values())
        created_count += int(created)

    return {"holidays_created": created_count}


def seed_allocations(employees, leave_types) -> dict:
    allocation_created = 0
    today = timezone.now().date()

    for employee in employees:
        for leave_type in leave_types.values():
            leave_year = _leave_year_for_date(today, leave_type.starting_month)
            allocated = float(leave_type.days_per_year or 0)
            allocation, created = EmployeeLeaveAllocation.objects.get_or_create(
                employee=employee,
                leave_type=leave_type,
                year=leave_year,
                defaults={"allocated_days": allocated, "used_days": 0, "carried_forward": 0},
            )
            if not created and allocation.allocated_days < allocated:
                allocation.allocated_days = allocated
                allocation.save(update_fields=["allocated_days", "updated_at"])
            allocation_created += int(created)

    return {"leave_allocations_created": allocation_created}


def seed_leave_requests(employees, hr_user, tl_user, manager_user) -> dict:
    created_count = 0
    today = timezone.now().date()

    request_specs = [
        {
            "employee": employees[0],
            "leave_type": "CASUAL",
            "duration": "FULL",
            "start_date": today + timedelta(days=4),
            "end_date": today + timedelta(days=5),
            "reason": "[SEED] Family function",
            "status": "PENDING",
            "final_status": "PENDING",
        },
        {
            "employee": employees[1],
            "leave_type": "SICK",
            "duration": "FULL",
            "start_date": today - timedelta(days=8),
            "end_date": today - timedelta(days=7),
            "reason": "[SEED] Fever and rest",
            "status": "APPROVED",
            "final_status": "APPROVED",
            "approval_count": 2,
            "tl_approved": True,
            "hr_approved": True,
            "tl_voted": True,
            "hr_voted": True,
        },
        {
            "employee": employees[2],
            "leave_type": "URGENT",
            "duration": "HALF",
            "start_date": today - timedelta(days=2),
            "end_date": today - timedelta(days=2),
            "reason": "[SEED] Urgent personal work",
            "status": "REJECTED",
            "final_status": "REJECTED",
            "rejection_count": 1,
            "manager_rejected": True,
            "manager_voted": True,
        },
    ]

    for spec in request_specs:
        leave, created = LeaveRequest.objects.update_or_create(
            employee=spec["employee"],
            leave_type=spec["leave_type"],
            start_date=spec["start_date"],
            reason=spec["reason"],
            defaults={
                "duration": spec["duration"],
                "end_date": spec["end_date"],
                "status": spec["status"],
                "final_status": spec["final_status"],
                "approval_count": spec.get("approval_count", 0),
                "rejection_count": spec.get("rejection_count", 0),
                "tl_approved": spec.get("tl_approved", False),
                "hr_approved": spec.get("hr_approved", False),
                "manager_approved": spec.get("manager_approved", False),
                "tl_rejected": spec.get("tl_rejected", False),
                "hr_rejected": spec.get("hr_rejected", False),
                "manager_rejected": spec.get("manager_rejected", False),
                "tl_voted": spec.get("tl_voted", False),
                "hr_voted": spec.get("hr_voted", False),
                "manager_voted": spec.get("manager_voted", False),
            },
        )
        leave.approvers.set([hr_user, tl_user, manager_user])
        created_count += int(created)

    return {"leave_requests_created": created_count}


def seed_notifications(users) -> dict:
    created_count = 0
    for user in [u for u in users if u is not None]:
        _, created = Notification.objects.get_or_create(
            user=user,
            message="[SEED] Welcome to LMS. Your profile has been initialized.",
            defaults={"read_status": False, "link": "/api/dashboard/"},
        )
        created_count += int(created)

    return {"notifications_created": created_count}


@transaction.atomic
def seed_leaves_data() -> dict:
    admin_user = User.objects.filter(is_superuser=True).first() or User.objects.filter(email="admin@lms.local").first()
    if not admin_user:
        raise ValueError("Admin user not found. Run users seeder first.")

    departments = {dept.name: dept for dept in Department.objects.all()}

    hr_user = User.objects.filter(role__name="HR").first()
    tl_user = User.objects.filter(role__name="TL").first()
    manager_user = User.objects.filter(role__name="Manager").first()
    employees = list(User.objects.filter(role__name="Employee", is_active=True).order_by("id")[:4])

    settings_result = seed_academic_settings(admin_user)
    leave_type_result = seed_leave_types(admin_user, departments)
    policy_result = seed_leave_policy(admin_user, departments)
    holiday_result = seed_holidays(admin_user, departments)
    allocation_result = seed_allocations(employees, leave_type_result["leave_types"])

    leave_result = {"leave_requests_created": 0}
    if hr_user and tl_user and manager_user and len(employees) >= 3:
        leave_result = seed_leave_requests(employees, hr_user, tl_user, manager_user)

    notification_result = seed_notifications([admin_user, hr_user, tl_user, manager_user, *employees])

    return {
        **settings_result,
        "leave_types_total": len(leave_type_result["leave_types"]),
        **{k: v for k, v in leave_type_result.items() if k != "leave_types"},
        **policy_result,
        **holiday_result,
        **allocation_result,
        **leave_result,
        **notification_result,
    }
