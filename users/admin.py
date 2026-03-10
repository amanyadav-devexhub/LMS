from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (
    User,
    Role,
    Department,
    SalaryDetails,
    BankDetails,
    VerificationDetails,
    AdditionalDetails
)


# -----------------------
# ROLE ADMIN
# -----------------------
@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)


# -----------------------
# DEPARTMENT ADMIN
# -----------------------
@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "hr")
    search_fields = ("name",)


# -----------------------
# USER ADMIN
# -----------------------
@admin.register(User)
class CustomUserAdmin(UserAdmin):

    list_display = (
        "id",
        "email",
        "username",
        "first_name",
        "role",
        "department",
        "reporting_manager",
        "is_active",
        "is_staff"
    )

    list_filter = (
        "role",
        "department",
        "is_active",
        "is_staff"
    )

    search_fields = (
        "email",
        "username",
        "first_name"
    )

    fieldsets = UserAdmin.fieldsets + (
        ("Company Info", {
            "fields": (
                "role",
                "department",
                "reporting_manager",
                "phone",
                "date_of_joining",
                "is_senior"
            )
        }),
    )


# -----------------------
# SALARY ADMIN
# -----------------------
@admin.register(SalaryDetails)
class SalaryAdmin(admin.ModelAdmin):

    list_display = (
        "user",
        "basic_salary",
        "hra",
        "bonus"
    )

    search_fields = (
        "user__email",
    )


# -----------------------
# BANK ADMIN
# -----------------------
@admin.register(BankDetails)
class BankAdmin(admin.ModelAdmin):

    list_display = (
        "user",
        "bank_name",
        "account_number",
        "ifsc_code"
    )

    search_fields = (
        "user__email",
        "bank_name"
    )


# -----------------------
# VERIFICATION ADMIN
# -----------------------
@admin.register(VerificationDetails)
class VerificationAdmin(admin.ModelAdmin):

    list_display = (
        "user",
        "aadhar_number",
        "pan_number",
        "is_verified"
    )

    search_fields = (
        "user__email",
    )


# -----------------------
# ADDITIONAL DETAILS ADMIN
# -----------------------
@admin.register(AdditionalDetails)
class AdditionalAdmin(admin.ModelAdmin):

    list_display = (
        "user",
        "emergency_contact"
    )

    search_fields = (
        "user__email",
    )