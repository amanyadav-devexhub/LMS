from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction

from users.models import (
    AdditionalDetails,
    BankDetails,
    Department,
    Role,
    SalaryDetails,
    VerificationDetails,
)
from users.rbac import DEFAULT_ROLE_PERMISSION_CODES, ensure_permission_catalog, grant_permissions


User = get_user_model()


ROLE_NAMES = ("Admin", "HR", "Manager", "TL", "Employee")


def _upsert_user(email: str, username: str, defaults: dict):
    user, created = User.objects.get_or_create(email=email, defaults={"username": username, **defaults})
    if not created:
        changed = False
        for key, value in defaults.items():
            if getattr(user, key) != value:
                setattr(user, key, value)
                changed = True
        if changed:
            user.save()
    return user, created


def seed_roles_and_permissions() -> dict:
    ensure_permission_catalog()

    roles = {}
    created_count = 0
    for role_name in ROLE_NAMES:
        role, created = Role.objects.get_or_create(name=role_name)
        roles[role_name] = role
        if created:
            created_count += 1

    for role_name, permission_codes in DEFAULT_ROLE_PERMISSION_CODES.items():
        role = roles.get(role_name)
        if role:
            grant_permissions(role, permission_codes)

    return {"created_roles": created_count, "roles": roles}


def seed_departments(roles: dict) -> dict:
    hr_role = roles["HR"]

    hr_user, _ = _upsert_user(
        email="hr@lms.local",
        username="hr",
        defaults={
            "first_name": "Ava",
            "last_name": "Sharma",
            "is_active": True,
            "role": hr_role,
            "designation": "HR Manager",
            "date_of_joining": date(2025, 1, 15),
            "phone": "9000000001",
        },
    )
    if not hr_user.check_password("Pass@123"):
        hr_user.set_password("Pass@123")
        hr_user.save(update_fields=["password"])

    department_specs = [
        ("Engineering", hr_user),
        ("Human Resources", hr_user),
        ("Operations", hr_user),
        ("Finance", hr_user),
    ]

    departments = {}
    created_count = 0
    for name, hr in department_specs:
        dept, created = Department.objects.get_or_create(name=name, defaults={"hr": hr})
        if not created and dept.hr_id != hr.id:
            dept.hr = hr
            dept.save(update_fields=["hr"])
        departments[name] = dept
        if created:
            created_count += 1

    return {"created_departments": created_count, "departments": departments, "hr_user": hr_user}


def seed_users(roles: dict, departments: dict, hr_user) -> dict:
    admin_role = roles["Admin"]
    manager_role = roles["Manager"]
    tl_role = roles["TL"]
    employee_role = roles["Employee"]

    admin, admin_created = _upsert_user(
        email="admin@lms.local",
        username="admin",
        defaults={
            "first_name": "System",
            "last_name": "Admin",
            "is_superuser": True,
            "is_staff": True,
            "is_active": True,
            "role": admin_role,
            "designation": "Administrator",
            "date_of_joining": date(2024, 1, 1),
        },
    )
    if not admin.check_password("Admin@123"):
        admin.set_password("Admin@123")
        admin.save(update_fields=["password"])

    manager, manager_created = _upsert_user(
        email="manager@lms.local",
        username="manager",
        defaults={
            "first_name": "Rahul",
            "last_name": "Verma",
            "is_active": True,
            "role": manager_role,
            "department": departments["Engineering"],
            "designation": "Engineering Manager",
            "date_of_joining": date(2024, 6, 10),
            "phone": "9000000002",
        },
    )
    if not manager.check_password("Pass@123"):
        manager.set_password("Pass@123")
        manager.save(update_fields=["password"])

    tl, tl_created = _upsert_user(
        email="tl@lms.local",
        username="teamlead",
        defaults={
            "first_name": "Neha",
            "last_name": "Iyer",
            "is_active": True,
            "role": tl_role,
            "department": departments["Engineering"],
            "reporting_manager": manager,
            "designation": "Team Lead",
            "date_of_joining": date(2025, 2, 1),
            "phone": "9000000003",
        },
    )
    if not tl.check_password("Pass@123"):
        tl.set_password("Pass@123")
        tl.save(update_fields=["password"])

    if hr_user.department_id != departments["Human Resources"].id:
        hr_user.department = departments["Human Resources"]
        hr_user.reporting_manager = manager
        hr_user.save(update_fields=["department", "reporting_manager"])

    employee_specs = [
        ("emp1@lms.local", "emp1", "Aman", "Kumar", date(2026, 1, 3)),
        ("emp2@lms.local", "emp2", "Sara", "Ali", date(2026, 2, 14)),
        ("emp3@lms.local", "emp3", "John", "Mathew", date(2026, 3, 20)),
        ("emp4@lms.local", "emp4", "Priya", "Singh", date(2026, 4, 8)),
    ]

    created_employees = 0
    employees = []
    for email, username, first_name, last_name, joining_date in employee_specs:
        user, created = _upsert_user(
            email=email,
            username=username,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
                "is_active": True,
                "role": employee_role,
                "department": departments["Engineering"],
                "reporting_manager": tl,
                "designation": "Software Engineer",
                "date_of_joining": joining_date,
                "phone": "9000000009",
            },
        )
        if not user.check_password("Pass@123"):
            user.set_password("Pass@123")
            user.save(update_fields=["password"])
        employees.append(user)
        if created:
            created_employees += 1

    return {
        "admin": admin,
        "hr": hr_user,
        "manager": manager,
        "tl": tl,
        "employees": employees,
        "created_admin": int(admin_created),
        "created_manager": int(manager_created),
        "created_tl": int(tl_created),
        "created_employees": created_employees,
    }


def seed_profile_details(users_to_seed: list) -> dict:
    salary_created = 0
    bank_created = 0
    verification_created = 0
    additional_created = 0

    for user in users_to_seed:
        _, created = SalaryDetails.objects.get_or_create(
            user=user,
            defaults={
                "basic_salary": 30000,
                "hra": 8000,
                "bonus": 2500,
                "salary_in_hand": 38000,
            },
        )
        salary_created += int(created)

        _, created = BankDetails.objects.get_or_create(
            user=user,
            defaults={
                "bank_name": "State Bank of India",
                "account_number": f"10000000{user.id:03d}",
                "ifsc_code": "SBIN0001234",
            },
        )
        bank_created += int(created)

        _, created = VerificationDetails.objects.get_or_create(
            user=user,
            defaults={
                "aadhar_number": f"12341234{user.id:04d}",
                "pan_number": f"ABCDE{user.id:04d}F",
                "is_verified": True,
            },
        )
        verification_created += int(created)

        _, created = AdditionalDetails.objects.get_or_create(
            user=user,
            defaults={
                "personal_email": f"personal.{user.username}@mail.local",
                "alternate_phone": "9111111111",
                "phone": user.phone,
                "emergency_contact": "Family Contact",
                "emergency_relation": "Sibling",
                "emergency_phone": "9222222222",
                "current_address": "Bengaluru, India",
                "permanent_address": "Bengaluru, India",
            },
        )
        additional_created += int(created)

    return {
        "salary_details_created": salary_created,
        "bank_details_created": bank_created,
        "verification_details_created": verification_created,
        "additional_details_created": additional_created,
    }


@transaction.atomic
def seed_users_data() -> dict:
    role_result = seed_roles_and_permissions()
    department_result = seed_departments(role_result["roles"])
    user_result = seed_users(
        role_result["roles"],
        department_result["departments"],
        department_result["hr_user"],
    )

    user_list = [
        user_result["admin"],
        user_result["hr"],
        user_result["manager"],
        user_result["tl"],
        *user_result["employees"],
    ]
    profile_result = seed_profile_details(user_list)

    return {
        "roles_created": role_result["created_roles"],
        "departments_created": department_result["created_departments"],
        "admin_created": user_result["created_admin"],
        "manager_created": user_result["created_manager"],
        "tl_created": user_result["created_tl"],
        "employees_created": user_result["created_employees"],
        **profile_result,
    }
