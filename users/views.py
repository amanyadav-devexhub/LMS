from django.db import models as django_models
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login as auth_login, logout
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.core.mail import send_mail
from django.db.models import Q, Count

from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.authentication import JWTAuthentication

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.utils.crypto import get_random_string

from .models import (
    Role, Department, RolePermission,
    SalaryDetails, BankDetails, VerificationDetails, AdditionalDetails,
)
from .serializers import *
from .forms import (
    EmployeeBasicForm, HRBasicForm,
    SalaryForm, BankForm, VerificationForm, AdditionalForm,
    ProfileUpdateForm,
)

User = get_user_model()


# ══════════════════════════════════════════════════════════════
#  AUTH VIEWS
# ══════════════════════════════════════════════════════════════

@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def login_view(request):

    if request.method == 'GET':
        return render(request, 'login.html')

    email    = request.data.get("email")
    password = request.data.get("password")

    if not email or not password:
        return Response(
            {"error": "Email and password are required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        user_obj = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response({"error": "Email is incorrect"}, status=status.HTTP_404_NOT_FOUND)

    if not user_obj.is_active:
        return Response(
            {"error": "Your account is inactive. Please contact admin."},
            status=status.HTTP_403_FORBIDDEN
        )

    user = authenticate(request, email=email, password=password)
    if user is None:
        return Response({"error": "Password is incorrect"}, status=status.HTTP_401_UNAUTHORIZED)

    auth_login(request, user)
    refresh = RefreshToken.for_user(user)

    role_redirect_map = {
        "Admin":    "/leave/admin_dashboard/",
        "HR":       "/leave/hr_dashboard/",
        "TL":       "/leave/tl_dashboard/",
        "Employee": "/leave/employee_dashboard/",
        "Manager":  "/leave/manager_dashboard/",
    }
    redirect_url = role_redirect_map.get(user.role.name if user.role else '', "/dashboard/")

    return Response({
        "access":   str(refresh.access_token),
        "refresh":  str(refresh),
        "redirect": redirect_url,
    }, status=status.HTTP_200_OK)


def user_logout(request):
    logout(request)
    return redirect("login")


# ══════════════════════════════════════════════════════════════
#  DASHBOARD — helper: build profile proxy from all models
# ══════════════════════════════════════════════════════════════

def _build_profile_context(user):
    """
    Returns a simple object with every field the dashboard.html
    template references as  {{ profile.xxx }}.
    Fetches/creates all related models on demand.
    """
    salary,       _ = SalaryDetails.objects.get_or_create(user=user)
    bank,         _ = BankDetails.objects.get_or_create(user=user)
    verification, _ = VerificationDetails.objects.get_or_create(user=user)
    additional,   _ = AdditionalDetails.objects.get_or_create(user=user)

    class P:
        pass

    p = P()

    # ── Identity ──────────────────────────────────────────────
    p.employee_id     = user.pk          # use user PK as employee ID
    p.avatar          = user.avatar if user.avatar else None

    # ── Contact (User model) ──────────────────────────────────
    p.phone           = additional.phone

    # ── Contact (AdditionalDetails) ───────────────────────────
    p.personal_email  = additional.personal_email
    p.alternate_phone = additional.alternate_phone

    # ── HR-set fields (User model) ────────────────────────────
    p.department      = user.department.name if user.department else None
    p.designation     = getattr(user, 'designation', None)   # add field if needed
    p.date_of_joining = user.date_of_joining

    # ── Personal (AdditionalDetails) ─────────────────────────
    p.date_of_birth   = additional.date_of_birth
    p.gender          = additional.gender
    p.marital_status  = additional.marital_status
    p.blood_group     = additional.blood_group

    # ── Emergency (AdditionalDetails) ────────────────────────
    p.emergency_contact  = additional.emergency_contact
    p.emergency_relation = additional.emergency_relation
    p.emergency_phone    = additional.emergency_phone

    # ── Address (AdditionalDetails) ──────────────────────────
    p.current_address   = additional.current_address
    p.permanent_address = additional.permanent_address

    # ── Salary ────────────────────────────────────────────────
    p.basic            = salary.basic_salary  or None
    p.hra              = salary.hra           or None
    p.other_allowances = salary.bonus         or None
    p.salary_in_hand   = salary.salary_in_hand or (
        (salary.basic_salary or 0) + (salary.hra or 0) + (salary.bonus or 0)
    ) or None

    # ── Bank ──────────────────────────────────────────────────
    p.account_number  = bank.account_number
    p.ifsc            = bank.ifsc_code
    p.bank_name       = bank.bank_name

    # ── Verification ─────────────────────────────────────────
    p.aadhaar         = verification.aadhar_number
    p.pan             = verification.pan_number

    # ── Additional / misc ────────────────────────────────────
    p.notes           = additional.notes

    # ── Leave balance ─────────────────────────────────────────
    p.leave_balance   = getattr(user, 'leave_balance', None)

    return p


# ══════════════════════════════════════════════════════════════
#  DASHBOARD — template view
# ══════════════════════════════════════════════════════════════

@login_required
def dashboard_template_view(request, user_id=None):
    if user_id:
        profile_user = get_object_or_404(User, pk=user_id)
    else:
        profile_user = request.user
        
    profile = _build_profile_context(profile_user)

    can_edit_profile = False
    if request.user == profile_user:
        can_edit_profile = True
    elif request.user.role and request.user.role.name == 'Admin':
        can_edit_profile = True
    elif request.user.role and request.user.role.name == 'HR' and (not profile_user.role or profile_user.role.name != 'Admin'):
        can_edit_profile = True

    total_leaves = approved_leaves = pending_leaves = 0
    try:
        from leaves.models import LeaveRequest
        qs              = LeaveRequest.objects.filter(user=profile_user)
        total_leaves    = qs.count()
        approved_leaves = qs.filter(status='Approved').count()
        pending_leaves  = qs.filter(status='Pending').count()
    except Exception:
        pass

    return render(request, 'dashboard.html', {
        'profile':         profile,
        'profile_user':    profile_user,
        'can_edit_profile': can_edit_profile,
        'total_leaves':    total_leaves,
        'approved_leaves': approved_leaves,
        'pending_leaves':  pending_leaves,
        'leave_balance':   profile.leave_balance,
    })


# ══════════════════════════════════════════════════════════════
#  UPDATE PROFILE — handles ALL form sections
# ══════════════════════════════════════════════════════════════

@login_required
def update_profile(request):
    """
    Handles POST from every edit form in dashboard.html.
    Hidden field  name="section"  identifies which form was submitted.
    Hidden field  name="target_user_id" identifies whose profile is being edited.
    """
    if request.method != 'POST':
        return redirect('dashboard')

    section = request.POST.get('section', '').strip()
    target_user_id = request.POST.get('target_user_id')

    if target_user_id:
        user = get_object_or_404(User, pk=target_user_id)
    else:
        user = request.user

    # Compute authorization
    can_edit_profile = False
    if request.user == user:
        can_edit_profile = True
    elif request.user.role and request.user.role.name == 'Admin':
        can_edit_profile = True
    elif request.user.role and request.user.role.name == 'HR' and (not user.role or user.role.name != 'Admin'):
        can_edit_profile = True

    if not can_edit_profile:
        messages.error(request, 'You do not have permission to edit this profile.')
        return redirect('profile_detail', user_id=user.pk) if target_user_id else redirect('dashboard')

    # pre-fetch related models
    additional,   _ = AdditionalDetails.objects.get_or_create(user=user)
    salary,       _ = SalaryDetails.objects.get_or_create(user=user)
    bank,         _ = BankDetails.objects.get_or_create(user=user)
    verification, _ = VerificationDetails.objects.get_or_create(user=user)

    def _str(key, default=''):
        return request.POST.get(key, default).strip()

    def _date(key):
        val = _str(key)
        return val if val else None

    def _decimal(key):
        val = _str(key)
        try:
            return float(val) if val else 0
        except ValueError:
            return 0

    try:

        # ── EMPLOYEE: basic details ──────────────────────────
        if section == 'basic_employee':

            # --- User model fields ---
            user.first_name = _str('first_name') or user.first_name
            user.last_name  = _str('last_name')  or user.last_name

            new_email = _str('email')
            if new_email and new_email != user.email:
                if User.objects.filter(email=new_email).exclude(pk=user.pk).exists():
                    messages.error(request, 'That email address is already in use.')
                    return redirect('profile_detail', user_id=user.pk) if target_user_id else redirect('dashboard')
                user.email = new_email

            user.save(update_fields=['first_name', 'last_name', 'email'])

            # --- Avatar ---
            if 'avatar' in request.FILES:
                user.avatar = request.FILES['avatar']
                user.save(update_fields=['avatar'])

            # --- AdditionalDetails fields ---
            additional.personal_email    = _str('personal_email')   or None
            additional.alternate_phone   = _str('alternate_phone')  or None
            additional.phone   = _str('phone')  or None
            additional.date_of_birth     = _date('date_of_birth')
            additional.gender            = _str('gender')           or None
            additional.marital_status    = _str('marital_status')   or None
            additional.emergency_contact = _str('emergency_contact') or None
            additional.emergency_relation= _str('emergency_relation') or None
            additional.emergency_phone   = _str('emergency_phone')  or None
            additional.current_address   = _str('current_address')  or None
            additional.permanent_address = _str('permanent_address') or None
            additional.save()

            messages.success(request, 'Profile updated successfully!')

        # ── HR/ADMIN: basic details (HR-controlled fields) ───
        elif section == 'basic_hr':

            # Only HR, Admin, Manager can use this section (handled by global can_edit_profile? Actually, basic_hr is specifically for HR/Admin fields like Designation/Dept).
            # Wait, an Employee editing their own profile should NOT use basic_hr.
            if request.user.role and request.user.role.name not in ('HR', 'Admin', 'Manager') and not request.user.is_superuser:
                messages.error(request, 'Access denied.')
                return redirect('profile_detail', user_id=user.pk) if target_user_id else redirect('dashboard')

            # target_user = User.objects.get(pk=...) # Wait, the current logic uses 'user = request.user'
            # The section is for HR/Admin to edit THEIR OWN profile or others?
            # In dashboard context, it is usually editing their own.
            
            user.first_name = _str('first_name') or user.first_name
            user.last_name  = _str('last_name')  or user.last_name

            new_email = _str('email')
            if new_email and new_email != user.email:
                if User.objects.filter(email=new_email).exclude(pk=user.pk).exists():
                    messages.error(request, 'That email is already in use.')
                    return redirect('profile_detail', user_id=user.pk) if target_user_id else redirect('dashboard')
                user.email = new_email
            
            # Update Designation (Added field)
            user.designation = _str('designation') or user.designation
            
            # Update Department
            dept_id = _str('department')
            if dept_id:
                try:
                    user.department = Department.objects.get(pk=dept_id)
                except (Department.DoesNotExist, ValueError):
                    pass

            # Update Phone (Sync both models)
            p_val = _str('phone')
            if p_val:
                user.phone = p_val
                additional.phone = p_val

            doj = _date('date_of_joining')
            if doj:
                user.date_of_joining = doj

            user.save()
            additional.save()
            messages.success(request, 'HR basic details updated successfully!')

        # ── SALARY ──────────────────────────────────────────
        elif section == 'salary':
            salary.basic_salary    = _decimal('basic')
            salary.hra             = _decimal('hra')
            salary.bonus           = _decimal('other_allowances')
            salary.salary_in_hand  = _decimal('salary_in_hand')
            salary.save()
            messages.success(request, 'Salary details updated successfully!')

        # ── BANK ────────────────────────────────────────────
        elif section == 'bank':
            bank.account_number = _str('account_number') or None
            bank.ifsc_code      = _str('ifsc')           or None
            bank.bank_name      = _str('bank_name')      or None
            bank.save()
            messages.success(request, 'Bank details updated successfully!')

        # ── VERIFICATION ────────────────────────────────────
        elif section == 'verification':
            verification.aadhar_number = _str('aadhaar') or None
            verification.pan_number    = _str('pan')     or None
            verification.save()
            messages.success(request, 'Verification details updated successfully!')

        # ── ADDITIONAL ──────────────────────────────────────
        elif section == 'additional':
            additional.blood_group = _str('blood_group') or None
            additional.notes       = _str('notes')       or None
            additional.save()
            messages.success(request, 'Additional details updated successfully!')

        else:
            messages.error(request, 'Unknown section. Nothing was saved.')

    except Exception as e:
        messages.error(request, f'Error saving data: {str(e)}')

    return redirect('profile_detail', user_id=user.pk) if target_user_id else redirect('dashboard')


# ══════════════════════════════════════════════════════════════
#  REST API — dashboard data & update (unchanged from original)
# ══════════════════════════════════════════════════════════════

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_data_api(request):
    user = request.user

    salary,       _ = SalaryDetails.objects.get_or_create(user=user)
    bank,         _ = BankDetails.objects.get_or_create(user=user)
    verification, _ = VerificationDetails.objects.get_or_create(user=user)
    additional,   _ = AdditionalDetails.objects.get_or_create(user=user)

    return Response({
        "profile": {
            "first_name": user.first_name,
            "last_name":  user.last_name,
            "email":      user.email,
            "phone":      additional.phone or "",
        },
        "salary": {
            "basic_salary":   str(salary.basic_salary),
            "hra":            str(salary.hra),
            "bonus":          str(salary.bonus),
            "salary_in_hand": str(salary.salary_in_hand),
        },
        "bank": {
            "bank_name":      bank.bank_name      or "",
            "account_number": bank.account_number or "",
            "ifsc_code":      bank.ifsc_code      or "",
        },
        "verification": {
            "aadhar_number": verification.aadhar_number or "",
            "pan_number":    verification.pan_number    or "",
            "is_verified":   verification.is_verified,
        },
        "additional": {
            "personal_email":    additional.personal_email    or "",
            "alternate_phone":   additional.alternate_phone   or "",
            "date_of_birth":     str(additional.date_of_birth) if additional.date_of_birth else "",
            "gender":            additional.gender            or "",
            "marital_status":    additional.marital_status    or "",
            "blood_group":       additional.blood_group       or "",
            "emergency_contact": additional.emergency_contact or "",
            "emergency_relation":additional.emergency_relation or "",
            "emergency_phone":   additional.emergency_phone   or "",
            "current_address":   additional.current_address   or "",
            "permanent_address": additional.permanent_address or "",
            "notes":             additional.notes             or "",
        },
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def dashboard_update_api(request):
    user = request.user

    salary,       _ = SalaryDetails.objects.get_or_create(user=user)
    bank,         _ = BankDetails.objects.get_or_create(user=user)
    verification, _ = VerificationDetails.objects.get_or_create(user=user)
    additional,   _ = AdditionalDetails.objects.get_or_create(user=user)

    data = request.data

    profile_form      = ProfileUpdateForm(data.get("profile",      {}), instance=user)
    salary_form       = SalaryForm(        data.get("salary",       {}), instance=salary)
    bank_form         = BankForm(          data.get("bank",         {}), instance=bank)
    verification_form = VerificationForm(  data.get("verification", {}), instance=verification)
    additional_form   = AdditionalForm(    data.get("additional",   {}), instance=additional)

    if all([
        profile_form.is_valid(),
        salary_form.is_valid(),
        bank_form.is_valid(),
        verification_form.is_valid(),
        additional_form.is_valid(),
    ]):
        profile_form.save()
        salary_form.save()
        bank_form.save()
        verification_form.save()
        additional_form.save()
        return Response({"success": True})
    else:
        return Response({
            "success": False,
            "errors": {
                "profile":      profile_form.errors,
                "salary":       salary_form.errors,
                "bank":         bank_form.errors,
                "verification": verification_form.errors,
                "additional":   additional_form.errors,
            },
        }, status=status.HTTP_400_BAD_REQUEST)


# ══════════════════════════════════════════════════════════════
#  PASSWORD RESET
# ══════════════════════════════════════════════════════════════

@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def forgot_password(request):
    
    if request.method == 'GET':
        return render(request, 'forgot_password.html')

    email = request.data.get('email')
    if not email:
        return Response({"error": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user  = User.objects.get(email=email)
        uid   = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        reset_link = f"http://127.0.0.1:8000/reset-password/{uid}/{token}/"
        send_mail(
            "Password Reset - LMS",
            f"Click below to reset your password:\n\n{reset_link}",
            "webmaster@localhost",
            [email],
        )
        return Response({"success": "Reset link sent to your email"})
    except User.DoesNotExist:
        return Response({"error": "Email not registered"}, status=status.HTTP_400_BAD_REQUEST)


class ResetPasswordAPIView(APIView):
    permission_classes = [AllowAny] 
    def get(self, request, uidb64, token):
        try:
            uid  = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
            if not default_token_generator.check_token(user, token):
                return render(request, "invalid_link.html")
            return render(request, "reset_password.html", {"uidb64": uidb64, "token": token})
        except (User.DoesNotExist, ValueError, TypeError):
            return render(request, "invalid_link.html")

    def post(self, request, uidb64, token):
        try:
            uid  = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
            if not default_token_generator.check_token(user, token):
                return Response({"error": "Reset link is invalid or expired"}, status=status.HTTP_400_BAD_REQUEST)

            password1 = request.data.get("password1")
            password2 = request.data.get("password2")
            if not password1 or not password2:
                return Response({"error": "All fields are required"}, status=status.HTTP_400_BAD_REQUEST)
            if password1 != password2:
                return Response({"error": "Passwords do not match"}, status=status.HTTP_400_BAD_REQUEST)

            user.set_password(password1)
            user.save()
            return Response({"message": "Password reset successful", "redirect": "/login/"})
        except (User.DoesNotExist, ValueError, TypeError):
            return Response({"error": "Invalid reset link"}, status=status.HTTP_400_BAD_REQUEST)


# ══════════════════════════════════════════════════════════════
#  REGISTER  (Admin only)
# ══════════════════════════════════════════════════════════════

@csrf_exempt
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def register_view(request):
    if not request.user.role or request.user.role.name != "Admin":
        return Response({"error": "Only Admin can register new users"}, status=403)

    email     = request.data.get("email")
    role_name = request.data.get("role")
    is_senior = request.data.get("is_senior", False)

    if not email or not role_name:
        return Response({"error": "Email and Role are required"}, status=400)
    if role_name != "HR":
        return Response({"error": "Admin can only assign role HR"}, status=403)
    if User.objects.filter(email=email).exists():
        return Response({"error": "Email already exists"}, status=400)

    role_obj = Role.objects.filter(name=role_name).first()
    if not role_obj:
        return Response({"error": "Role not found"}, status=400)

    random_password = get_random_string(length=8)
    user = User.objects.create_user(
        username=email, email=email, password=random_password,
        role=role_obj, is_senior=is_senior,
    )
    return Response({
        "message":            "User created successfully",
        "email":              email,
        "generated_password": random_password,
    }, status=201)


# ══════════════════════════════════════════════════════════════
#  HOME
# ══════════════════════════════════════════════════════════════

def home_view(request):
    if request.user.is_authenticated:
        role_name = getattr(request.user.role, "name", "")
        redirect_map = {
            "Admin":    "admin_dashboard",
            "HR":       "hr_dashboard",
            "TL":       "tl_dashboard",
            "Employee": "employee_dashboard",
            "Manager":  "manager_dashboard",
        }
        return redirect(redirect_map.get(role_name, "dashboard"))
    return render(request, "home.html")


# ══════════════════════════════════════════════════════════════
#  DEPARTMENT VIEWS  (unchanged from original)
# ══════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════

def _is_admin(request):
    role = getattr(request.user, 'role', None)
    return role and role.name == 'Admin'


def _is_hr_or_admin(request):
    role = getattr(request.user, 'role', None)
    return role and role.name in ['Admin', 'HR']


@login_required
def department_list(request):
    if not _is_admin(request):
        messages.error(request, "Access denied. Admins only.")
        return redirect('admin_dashboard')

    search      = request.GET.get('q', '').strip()
    departments = Department.objects.annotate(emp_count=Count('user')).order_by('name')
    if search:
        departments = departments.filter(name__icontains=search)

    hr_users = User.objects.filter(role__name__in=['HR', 'Manager']).order_by('first_name')
    return render(request, 'department_list.html', {
        'departments': departments,
        'hr_users':    hr_users,
        'search':      search,
        'total':       Department.objects.count(),
    })


@login_required
def department_create(request):
    if not _is_admin(request):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')

    if request.method == 'POST':
        name  = request.POST.get('name', '').strip()
        hr_id = request.POST.get('hr', '').strip()
        if not name:
            messages.error(request, "Department name is required.")
        elif Department.objects.filter(name__iexact=name).exists():
            messages.error(request, f'Department "{name}" already exists.')
        else:
            dept = Department(name=name)
            if hr_id:
                try:
                    dept.hr = User.objects.get(pk=hr_id)
                except User.DoesNotExist:
                    pass
            dept.save()
            messages.success(request, f'Department "{name}" created successfully!')
    return redirect('department_list')


@login_required
def department_edit(request, pk):
    if not _is_admin(request):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')

    dept = get_object_or_404(Department, pk=pk)
    if request.method == 'POST':
        name  = request.POST.get('name', '').strip()
        hr_id = request.POST.get('hr', '').strip()
        if not name:
            messages.error(request, "Department name is required.")
        elif Department.objects.filter(name__iexact=name).exclude(pk=pk).exists():
            messages.error(request, f'Another department named "{name}" already exists.')
        else:
            dept.name = name
            dept.hr   = User.objects.get(pk=hr_id) if hr_id else None
            dept.save()
            messages.success(request, f'Department "{name}" updated successfully!')
    return redirect('department_list')


@login_required
def department_delete(request, pk):
    if not _is_admin(request):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')

    dept = get_object_or_404(Department, pk=pk)
    if request.method == 'POST':
        name = dept.name
        User.objects.filter(department=dept).update(department=None)
        dept.delete()
        messages.success(request, f'Department "{name}" deleted successfully!')
    return redirect('department_list')


@login_required
def department_detail(request, pk):
    if not _is_admin(request):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')

    dept      = get_object_or_404(Department, pk=pk)
    employees = User.objects.filter(department=dept).select_related('role').order_by('first_name')
    return render(request, 'department_detail.html', {
        'dept':      dept,
        'employees': employees,
        'emp_count': employees.count(),
    })


# ══════════════════════════════════════════════════════════════
#  ROLE & PERMISSION VIEWS  (unchanged from original)
# ══════════════════════════════════════════════════════════════

MODULES = [
    ('dashboard',     'Dashboard',         'fa-gauge-high'),
    ('leaves',        'Leave Management',  'fa-calendar-days'),
    ('employees',     'Employees',         'fa-users'),
    ('departments',   'Departments',       'fa-building'),
    ('salary',        'Salary Details',    'fa-indian-rupee-sign'),
    ('bank',          'Bank Details',      'fa-building-columns'),
    ('verification',  'Verification',      'fa-shield-check'),
    ('reports',       'Reports',           'fa-chart-bar'),
    ('notifications', 'Notifications',     'fa-bell'),
]

ROLE_DEFAULTS = {
    'Admin':    dict(can_view=True,  can_create=True,  can_edit=True,  can_delete=True),
    'HR':       dict(can_view=True,  can_create=True,  can_edit=True,  can_delete=False),
    'Manager':  dict(can_view=True,  can_create=False, can_edit=True,  can_delete=False),
    'TL':       dict(can_view=True,  can_create=False, can_edit=True,  can_delete=False),
    'Employee': dict(can_view=True,  can_create=False, can_edit=False, can_delete=False),
}

MODULE_ROLE_RESTRICTIONS = {
    ('HR',       'departments'):  dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('TL',       'departments'):  dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('TL',       'salary'):       dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Employee', 'departments'):  dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Employee', 'salary'):       dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Employee', 'bank'):         dict(can_view=True,  can_create=False, can_edit=True,  can_delete=False),
    ('Employee', 'employees'):    dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Manager',  'departments'):  dict(can_view=True,  can_create=False, can_edit=True,  can_delete=False),
}


@login_required
def role_list(request):
    if not _is_admin(request):
        messages.error(request, "Access denied. Admins only.")
        return redirect('admin_dashboard')
    roles = Role.objects.annotate(user_count=Count('user', distinct=True)).order_by('name')
    return render(request, 'role_list.html', {
        'roles': roles, 'total_roles': roles.count(), 'total_users': User.objects.count(),
    })


@login_required
def role_create(request):
    if not _is_admin(request):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, "Role name is required.")
        elif Role.objects.filter(name__iexact=name).exists():
            messages.error(request, f'Role "{name}" already exists.')
        else:
            Role.objects.create(name=name)
            messages.success(request, f'Role "{name}" created successfully!')
    return redirect('role_list')


@login_required
def role_edit(request, pk):
    if not _is_admin(request):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')
    role = get_object_or_404(Role, pk=pk)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, "Role name is required.")
        elif Role.objects.filter(name__iexact=name).exclude(pk=pk).exists():
            messages.error(request, f'Role "{name}" already exists.')
        else:
            old_name = role.name
            role.name = name
            role.save()
            messages.success(request, f'Role "{old_name}" renamed to "{name}".')
    return redirect('role_list')


@login_required
def role_delete(request, pk):
    if not _is_admin(request):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')
    role = get_object_or_404(Role, pk=pk)
    if request.method == 'POST':
        if role.name == 'Admin':
            messages.error(request, "The Admin role cannot be deleted.")
            return redirect('role_list')
        name = role.name
        User.objects.filter(role=role).update(role=None)
        RolePermission.objects.filter(role=role).delete()
        role.delete()
        messages.success(request, f'Role "{name}" deleted.')
    return redirect('role_list')


@login_required
def role_permission_list(request):
    if not _is_admin(request):
        messages.error(request, "Access denied. Admins only.")
        return redirect('admin_dashboard')

    roles        = Role.objects.all().order_by('name')
    selected_pk  = request.GET.get('role', '').strip()
    selected_role = None
    perm_rows    = []

    if selected_pk:
        try:
            selected_role = Role.objects.get(pk=selected_pk)
            for mod_key, mod_label, mod_icon in MODULES:
                defaults = dict(can_view=False, can_create=False, can_edit=False, can_delete=False)
                role_def = ROLE_DEFAULTS.get(selected_role.name, defaults)
                restrict = MODULE_ROLE_RESTRICTIONS.get((selected_role.name, mod_key), role_def)
                RolePermission.objects.get_or_create(
                    role=selected_role, module=mod_key, defaults=restrict
                )
            perm_map = {p.module: p for p in RolePermission.objects.filter(role=selected_role)}
            for mod_key, mod_label, mod_icon in MODULES:
                perm_rows.append({'key': mod_key, 'label': mod_label, 'icon': mod_icon, 'perm': perm_map.get(mod_key)})
        except Role.DoesNotExist:
            messages.error(request, "Role not found.")

    return render(request, 'role_permission_list.html', {
        'roles': roles, 'selected_role': selected_role,
        'perm_rows': perm_rows, 'modules': MODULES,
    })


@login_required
def role_permission_save(request):
    if not _is_admin(request):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')

    if request.method == 'POST':
        role_id = request.POST.get('role_id', '').strip()
        if not role_id:
            messages.error(request, "No role specified.")
            return redirect('role_permission_list')
        try:
            role = Role.objects.get(pk=role_id)
        except Role.DoesNotExist:
            messages.error(request, "Role not found.")
            return redirect('role_permission_list')

        for mod_key, mod_label, _ in MODULES:
            perm, _ = RolePermission.objects.get_or_create(role=role, module=mod_key)
            perm.can_view   = f"{mod_key}_view"   in request.POST
            perm.can_create = f"{mod_key}_create" in request.POST
            perm.can_edit   = f"{mod_key}_edit"   in request.POST
            perm.can_delete = f"{mod_key}_delete" in request.POST
            perm.save()

        messages.success(request, f'Permissions for "{role.name}" saved successfully!')

    from django.urls import reverse
    return redirect(reverse('role_permission_list') + f'?role={role_id}')


@login_required
def assign_role(request):
    if not _is_hr_or_admin(request):
        messages.error(request, "Access denied. Admins or HR only.")
        return redirect('admin_dashboard')

    roles = Role.objects.all().order_by('name')
    departments = Department.objects.all().order_by('name')
    tl_users = User.objects.filter(role__name='TL').order_by('first_name')

    # Filters
    dept_filter = request.GET.get('dept', '').strip()
    search = request.GET.get('q', '').strip()
    filter_role = request.GET.get('role', '').strip()

    users = User.objects.select_related('role', 'department').order_by('first_name', 'last_name')

    if dept_filter:
        users = users.filter(department_id=dept_filter)

    if filter_role:
        users = users.filter(role_id=filter_role)

    if search:
        users = users.filter(
            Q(first_name__icontains=search) |
            Q(last_name__icontains=search) |
            Q(email__icontains=search)
        )

    # POST: Update role and department
    if request.method == 'POST':

        user_id = request.POST.get('user_id', '').strip()
        role_id = request.POST.get('role_id', '').strip()
        department_id = request.POST.get('department_id', '').strip()
        reporting_manager_id = request.POST.get('reporting_manager_id', '').strip()

        if not user_id or not role_id:
            messages.error(request, "User and role are required.")
            return redirect('assign_role')

        try:
            target = User.objects.select_related('role').get(pk=user_id)
            new_role = Role.objects.get(pk=role_id)

            # Prevent removing last admin
            if (
                target.role and target.role.name == 'Admin'
                and new_role.name != 'Admin'
                and User.objects.filter(role__name='Admin').count() <= 1
            ):
                messages.error(request, "Cannot remove the only Admin user.")
                return redirect('assign_role')

            old_role = target.role.name if target.role else "None"

            # Update role
            target.role = new_role

            # Update department
            if department_id:
                try:
                    target.department = Department.objects.get(pk=department_id)
                except Department.DoesNotExist:
                    target.department = None
            else:
                target.department = None

            # Update reporting manager
            if reporting_manager_id:
                try:
                    target.reporting_manager = User.objects.get(pk=reporting_manager_id)
                except User.DoesNotExist:
                    target.reporting_manager = None
            else:
                target.reporting_manager = None

            target.save()

            messages.success(
                request,
                f"{target.get_full_name() or target.email}: "
                f"{old_role} → {new_role.name} (Department Updated)"
            )

        except User.DoesNotExist:
            messages.error(request, "User not found.")

        except Role.DoesNotExist:
            messages.error(request, "Role not found.")

        except Exception as e:
            messages.error(request, f"Error updating user: {str(e)}")

        return redirect('assign_role')

    return render(request, 'assign_role.html', {
        'users': users,
        'roles': roles,
        'departments': departments,
        'tl_users': tl_users,
        'dept_id': dept_filter,
        'filter_role': filter_role,
        'search': search,
        'total_users': users.count(),
    })

@login_required
def assign_role_bulk(request):
    if not _is_hr_or_admin(request):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')

    if request.method == 'POST':
        dept_id     = request.POST.get('department_id', '').strip()
        new_role_id = request.POST.get('role_id',       '').strip()
        if not dept_id or not new_role_id:
            messages.error(request, "Department and role are both required.")
            return redirect('assign_role')
        try:
            dept     = Department.objects.get(pk=dept_id)
            new_role = Role.objects.get(pk=new_role_id)
            count    = User.objects.filter(department=dept).exclude(role__name='Admin').update(role=new_role)
            messages.success(request, f'Assigned "{new_role.name}" to {count} employee(s) in {dept.name}.')
        except (Department.DoesNotExist, Role.DoesNotExist):
            messages.error(request, "Invalid department or role.")
    return redirect('assign_role')