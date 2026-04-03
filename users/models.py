from django.db import models
from django.contrib.auth.models import AbstractUser


# -----------------------
# ROLE MODEL
# -----------------------
class Role(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


# -----------------------
# DEPARTMENT MODEL
# -----------------------
class Department(models.Model):
    name = models.CharField(max_length=100)
    hr = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_departments"
    )

    def __str__(self):
        return self.name


# -----------------------
# CUSTOM USER MODEL
# -----------------------
class User(AbstractUser):

    email = models.EmailField(unique=True)

    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    reporting_manager = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="team_members"
    )

    phone = models.CharField(max_length=15, blank=True, null=True)

    designation = models.CharField(max_length=150, blank=True, null=True)

    date_of_joining = models.DateField(null=True, blank=True)

    is_senior = models.BooleanField(default=False)

    # ── Avatar (profile photo) ──
    avatar = models.ImageField(
        upload_to='avatars/',
        null=True,
        blank=True
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def __str__(self):
        return self.email

    def has_rbac_permission(self, codename):
        if self.is_superuser:
            return True
        if not self.role or not self.role.is_active:
            return False

        normalized = (codename or "").strip().lower()
        if not normalized:
            return False

        return RolePermissionAssignment.objects.filter(
            role=self.role,
            permission__codename=normalized,
            permission__is_active=True,
            is_enabled=True,
        ).exists()

    def has_perm(self, perm, obj=None):
        if self.is_superuser:
            return True
        if self.has_rbac_permission(perm):
            return True
        return super().has_perm(perm, obj=obj)


# -----------------------
# SALARY DETAILS
# -----------------------
class SalaryDetails(models.Model):

    user = models.OneToOneField(User, on_delete=models.CASCADE)

    basic_salary = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    hra          = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    bonus        = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Net / in-hand salary (HR sets this directly)
    salary_in_hand = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )

    def __str__(self):
        return f"Salary - {self.user.email}"


# -----------------------
# BANK DETAILS
# -----------------------
class BankDetails(models.Model):

    user = models.OneToOneField(User, on_delete=models.CASCADE)

    bank_name      = models.CharField(max_length=200, blank=True, null=True)
    account_number = models.CharField(max_length=50,  blank=True, null=True)
    ifsc_code      = models.CharField(max_length=20,  blank=True, null=True)

    def __str__(self):
        return f"Bank - {self.user.email}"


# -----------------------
# VERIFICATION DETAILS
# -----------------------
class VerificationDetails(models.Model):

    user = models.OneToOneField(User, on_delete=models.CASCADE)

    aadhar_number = models.CharField(max_length=20, blank=True, null=True)
    pan_number    = models.CharField(max_length=20, blank=True, null=True)
    is_verified   = models.BooleanField(default=False)

    def __str__(self):
        return f"Verification - {self.user.email}"


# -----------------------
# ADDITIONAL DETAILS
# (extended with all new profile fields)
# -----------------------

GENDER_CHOICES = [
    ('Male',   'Male'),
    ('Female', 'Female'),
    ('Other',  'Other'),
]

MARITAL_CHOICES = [
    ('Single',  'Single'),
    ('Married', 'Married'),
]

BLOOD_GROUP_CHOICES = [
    ('A+',  'A+'),
    ('A-',  'A-'),
    ('B+',  'B+'),
    ('B-',  'B-'),
    ('AB+', 'AB+'),
    ('AB-', 'AB-'),
    ('O+',  'O+'),
    ('O-',  'O-'),
]


class AdditionalDetails(models.Model):

    user = models.OneToOneField(User, on_delete=models.CASCADE)

    # ── Contact ──
    personal_email   = models.EmailField(blank=True, null=True)
    alternate_phone  = models.CharField(max_length=15, blank=True, null=True)
    phone = models.CharField(max_length=15, blank=True, null=True)

    # ── Personal ──
    date_of_birth  = models.DateField(null=True, blank=True)
    gender         = models.CharField(
        max_length=10, choices=GENDER_CHOICES, blank=True, null=True
    )
    marital_status = models.CharField(
        max_length=10, choices=MARITAL_CHOICES, blank=True, null=True
    )
    blood_group    = models.CharField(
        max_length=5, choices=BLOOD_GROUP_CHOICES, blank=True, null=True
    )

    # ── Emergency contact ──
    emergency_contact  = models.CharField(max_length=100, blank=True, null=True)  # name
    emergency_relation = models.CharField(max_length=50,  blank=True, null=True)
    emergency_phone    = models.CharField(max_length=15,  blank=True, null=True)

    # ── Address ──
    current_address   = models.TextField(blank=True, null=True)
    permanent_address = models.TextField(blank=True, null=True)

    # ── Legacy / misc (kept from original) ──
    address = models.TextField(blank=True, null=True)  # kept for backwards compat
    notes   = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Additional - {self.user.email}"


# -----------------------
# MODULE CHOICES & ROLE PERMISSIONS
# -----------------------
MODULE_CHOICES = [
    ('dashboard',     'Dashboard'),
    ('leaves',        'Leave Management'),
    ('employees',     'Employees'),
    ('departments',   'Departments'),
    ('salary',        'Salary Details'),
    ('bank',          'Bank Details'),
    ('verification',  'Verification Details'),
    ('reports',       'Reports'),
    ('notifications', 'Notifications'),
]


class RolePermission(models.Model):
    role   = models.ForeignKey(
        Role,
        on_delete=models.CASCADE,
        related_name='permissions'
    )
    module = models.CharField(max_length=50, choices=MODULE_CHOICES)

    can_view   = models.BooleanField(default=False)
    can_create = models.BooleanField(default=False)
    can_edit   = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)

    class Meta:
        unique_together     = ('role', 'module')
        ordering            = ['role__name', 'module']
        verbose_name        = 'Role Permission'
        verbose_name_plural = 'Role Permissions'

    def __str__(self):
        return f"{self.role.name} → {self.module}"


RBAC_MODULE_CHOICES = [
    ('user', 'User'),
    ('leave', 'Leave'),
    ('holiday', 'Holiday'),
    ('report', 'Report'),
    ('dashboard', 'Dashboard'),
    ('system', 'System'),
]

RBAC_ACTION_CHOICES = [
    ('view', 'View'),
    ('add', 'Add'),
    ('edit', 'Edit'),
    ('delete', 'Delete'),
    ('approve', 'Approve'),
    ('reject', 'Reject'),
    ('manage', 'Manage'),
]


class RBACPermission(models.Model):
    module = models.CharField(max_length=50, choices=RBAC_MODULE_CHOICES)
    action = models.CharField(max_length=20, choices=RBAC_ACTION_CHOICES)
    codename = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['module', 'action']
        verbose_name = 'RBAC Permission'
        verbose_name_plural = 'RBAC Permissions'

    def __str__(self):
        return self.codename


class RolePermissionAssignment(models.Model):
    role = models.ForeignKey(
        Role,
        on_delete=models.CASCADE,
        related_name='rbac_permissions',
    )
    permission = models.ForeignKey(
        RBACPermission,
        on_delete=models.CASCADE,
        related_name='role_assignments',
    )
    is_enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = (('role', 'permission'),)
        ordering = ['role__name', 'permission__module', 'permission__action']
        verbose_name = 'Role Permission Assignment'
        verbose_name_plural = 'Role Permission Assignments'

    def __str__(self):
        return f"{self.role.name} -> {self.permission.codename}"


class AccessLog(models.Model):
    STATUS_ALLOWED = 'allowed'
    STATUS_DENIED = 'denied'

    STATUS_CHOICES = [
        (STATUS_ALLOWED, 'Allowed'),
        (STATUS_DENIED, 'Denied'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=150)
    permission_code = models.CharField(max_length=100, blank=True, default='')
    path = models.CharField(max_length=255, blank=True, default='')
    method = models.CharField(max_length=10, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        user_label = self.user.email if self.user else 'anonymous'
        return f"{user_label} {self.status} {self.action}"