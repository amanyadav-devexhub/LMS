from django.contrib import admin
from .models import LeaveRequest, LeaveBalance, Notification

# ---------------------------
# LEAVE REQUEST ADMIN
# ---------------------------
@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "employee",
        "leave_type",
        "duration",
        "start_date",
        "end_date",
        "short_hours",
        "status",
        "created_at",
        "updated_at"
    )
    list_filter = ("leave_type", "duration", "status", "created_at")
    search_fields = ("employee__username", "employee__email", "reason")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "start_date"
    ordering = ("-created_at",)
    fieldsets = (
        (None, {
            "fields": ("employee", "leave_type", "duration", "status", "reason")
        }),
        ("Dates & Session", {
            "fields": ("start_date", "end_date", "session", "short_hours")
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at")
        }),
    )


# ---------------------------
# LEAVE BALANCE ADMIN
# ---------------------------
@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = ("employee", "casual_leave", "sick_leave", "updated_at")
    search_fields = ("employee__username", "employee__email")
    readonly_fields = ("updated_at",)
    ordering = ("employee",)


# ---------------------------
# NOTIFICATION ADMIN
# ---------------------------
@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "message", "read_status", "created_at")
    list_filter = ("read_status", "created_at")
    search_fields = ("user__username", "user__email", "message")
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)