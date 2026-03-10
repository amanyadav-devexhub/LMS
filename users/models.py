from django.db import models
from django.contrib.auth.models import AbstractUser


# -----------------------
# ROLE MODEL
# -----------------------
class Role(models.Model):
    name = models.CharField(max_length=50, unique=True)

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

    date_of_joining = models.DateField(null=True, blank=True)

    is_senior = models.BooleanField(default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def __str__(self):
        return self.email


# -----------------------
# SALARY DETAILS
# -----------------------
class SalaryDetails(models.Model):

    user = models.OneToOneField(User, on_delete=models.CASCADE)

    basic_salary = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    hra = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    bonus = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return f"Salary - {self.user.email}"


# -----------------------
# BANK DETAILS
# -----------------------
class BankDetails(models.Model):

    user = models.OneToOneField(User, on_delete=models.CASCADE)

    bank_name = models.CharField(max_length=200)
    account_number = models.CharField(max_length=50)
    ifsc_code = models.CharField(max_length=20)

    def __str__(self):
        return f"Bank - {self.user.email}"


# -----------------------
# VERIFICATION DETAILS
# -----------------------
class VerificationDetails(models.Model):

    user = models.OneToOneField(User, on_delete=models.CASCADE)

    aadhar_number = models.CharField(max_length=20, blank=True, null=True)
    pan_number = models.CharField(max_length=20, blank=True, null=True)

    is_verified = models.BooleanField(default=False)

    def __str__(self):
        return f"Verification - {self.user.email}"


# -----------------------
# ADDITIONAL DETAILS
# -----------------------
class AdditionalDetails(models.Model):

    user = models.OneToOneField(User, on_delete=models.CASCADE)

    address = models.TextField(blank=True, null=True)

    emergency_contact = models.CharField(max_length=15, blank=True, null=True)

    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Additional - {self.user.email}"



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