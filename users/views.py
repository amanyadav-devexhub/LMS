from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import (
    urlsafe_base64_encode,
    urlsafe_base64_decode,
)
from django.utils.encoding import force_bytes, force_str
from django.core.mail import send_mail

from rest_framework.decorators import api_view
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User
from django.shortcuts import render
from django.utils.http import urlsafe_base64_decode
from django.utils.encoding import force_str
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from django.shortcuts import render, redirect
from .serializers import *

from django.contrib.auth import get_user_model
User = get_user_model()

# @api_view(['GET', 'POST'])
# def login_view(request):

#     # 🔹 GET → open login page
#     if request.method == 'GET':
#         return render(request, 'login.html')

#     # 🔹 POST → authenticate user
#     email = request.data.get("email")
#     password = request.data.get("password")

#     # ✅ Email empty check
#     if not email:
#         return Response(
#             {"error": "Email is required"},
#             status=status.HTTP_400_BAD_REQUEST
#         )

#     # ✅ Password empty check
#     if not password:
#         return Response(
#             {"error": "Password is required"},
#             status=status.HTTP_400_BAD_REQUEST
#         )

#     # ✅ Check if email exists in DB
#     try:
#         user_obj = User.objects.get(email=email)
#     except User.DoesNotExist:
#         return Response(
#             {"error": "Email does not exist"},
#             status=status.HTTP_404_NOT_FOUND
#         )

#     # ✅ Authenticate user (check password)
#     user = authenticate(
#         request,
#         email=email,
#         password=password
#     )

#     if user is None:
#         return Response(
#             {"error": "Password is incorrect"},
#             status=status.HTTP_401_UNAUTHORIZED
#         )

#     login(request, user) 
#     refresh = RefreshToken.for_user(user)

#     if user.role == "Admin":
#         redirect_url = "/leave/admin_dashboard/"
#     elif user.role == "HR":
#         redirect_url = "/leave/hr_dashboard/"
#     elif user.role == "TL":
#         redirect_url = "/leave/tl_dashboard/"
#     elif user.role == "Employee":
#         redirect_url = "/leave/employee_dashboard/"
#     else:
#         redirect_url = "/leave/dashboard/"

#     return Response({
#         "access": str(refresh.access_token),
#         "refresh": str(refresh),
#         "redirect": redirect_url
#     })
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import authenticate, login
from rest_framework_simplejwt.tokens import RefreshToken
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render
from django.contrib.auth import get_user_model
from rest_framework.permissions import IsAuthenticated

User = get_user_model()

@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def login_view(request):

    if request.method == 'GET':
        return render(request, 'login.html')

    email = request.data.get("email")
    password = request.data.get("password")

    if not email or not password:
        return Response(
            {"error": "Email and password are required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response(
            {"error": "Email does not exist"},
            status=status.HTTP_404_NOT_FOUND
        )

    user = authenticate(request, email=email, password=password)

    if not user:
        return Response(
            {"error": "Password is incorrect"},
            status=status.HTTP_401_UNAUTHORIZED
        )

    # ✅ Create Django session login
    login(request, user)

    refresh = RefreshToken.for_user(user)

    role_redirect_map = {
        "Admin": "/leave/admin_dashboard/",
        "HR": "/leave/hr_dashboard/",
        "TL": "/leave/tl_dashboard/",
        "Employee": "/leave/employee_dashboard/",
        "Manager": "/leave/manager_dashboard/"
    }

    redirect_url = role_redirect_map.get(user.role.name, "/dashboard/")

    return Response({
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "redirect": redirect_url
    }, status=status.HTTP_200_OK)

def user_logout(request):
    logout(request)
    return redirect("login")

from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from .models import SalaryDetails, BankDetails, VerificationDetails, AdditionalDetails
from .forms import ProfileUpdateForm, SalaryForm, BankForm, VerificationForm, AdditionalForm


# ──────────────────────────────────────────────────────
#  Helper: build the profile context dict
#  (all fields the dashboard.html template expects)
# ──────────────────────────────────────────────────────
def _build_profile_context(user):
    """
    Fetches / creates every related model and maps the fields
    dashboard.html uses onto a single dict-like object.
    We use a plain object so the template can do {{ profile.field }}.
    """
    salary,       _ = SalaryDetails.objects.get_or_create(user=user)
    bank,         _ = BankDetails.objects.get_or_create(user=user)
    verification, _ = VerificationDetails.objects.get_or_create(user=user)
    additional,   _ = AdditionalDetails.objects.get_or_create(user=user)

    # A simple namespace so template can do  {{ profile.salary_in_hand }} etc.
    class ProfileProxy:
        pass

    p = ProfileProxy()

    # ── Basic / identity (stored on User or AdditionalDetails) ──
    p.employee_id    = getattr(user, 'employee_id', None) or user.pk
    p.phone          = getattr(additional, 'phone',          None)
    p.department     = getattr(additional, 'department',     None)
    p.designation    = getattr(additional, 'designation',    None)
    p.date_of_birth  = getattr(additional, 'date_of_birth',  None)
    p.date_of_joining= getattr(additional, 'date_of_joining',None)
    p.address        = getattr(additional, 'address',        None)

    # ── Salary ──
    p.basic           = getattr(salary, 'basic_salary',    None)
    p.hra             = getattr(salary, 'hra',             None)
    p.other_allowances= getattr(salary, 'bonus',           None)   # maps bonus → other_allowances
    # salary_in_hand = basic + hra + bonus  (or a dedicated field if you have one)
    try:
        p.salary_in_hand = (
            (salary.basic_salary or 0) +
            (salary.hra          or 0) +
            (salary.bonus        or 0)
        ) or None
    except Exception:
        p.salary_in_hand = None

    # ── Bank ──
    p.account_number = getattr(bank, 'account_number', None)
    p.ifsc           = getattr(bank, 'ifsc_code',      None)
    p.bank_name      = getattr(bank, 'bank_name',      None)

    # ── Verification ──
    p.aadhaar        = getattr(verification, 'aadhar_number', None)
    p.pan            = getattr(verification, 'pan_number',    None)

    # ── Additional ──
    p.emergency_contact = getattr(additional, 'emergency_contact', None)
    p.blood_group       = getattr(additional, 'blood_group',       None)
    p.notes             = getattr(additional, 'notes',             None)

    # ── Leave balance (adjust model/field name to match yours) ──
    p.leave_balance = getattr(user, 'leave_balance', None)

    return p


# ──────────────────────────────────────────────────────
#  Render dashboard template  (was dashboard_template_view)
# ──────────────────────────────────────────────────────
@login_required
def dashboard_template_view(request):
    """
    Renders dashboard.html with full profile context so Django
    template tags ({{ profile.xxx }}) resolve without JS / API calls.
    """
    user    = request.user
    profile = _build_profile_context(user)

    # ── Leave stats (adjust queryset to your LeaveRequest model) ──
    total_leaves    = 0
    approved_leaves = 0
    pending_leaves  = 0

    try:
        from leaves.models import LeaveRequest          # adjust app/model name
        qs              = LeaveRequest.objects.filter(user=user)
        total_leaves    = qs.count()
        approved_leaves = qs.filter(status='Approved').count()
        pending_leaves  = qs.filter(status='Pending').count()
    except Exception:
        pass    # leave stats default to 0 if model not available yet

    context = {
        'profile':         profile,
        'total_leaves':    total_leaves,
        'approved_leaves': approved_leaves,
        'pending_leaves':  pending_leaves,
        'leave_balance':   profile.leave_balance,
    }
    return render(request, 'dashboard.html', context)


# ──────────────────────────────────────────────────────
#  Handle profile update form submissions
# ──────────────────────────────────────────────────────
@login_required
def update_profile(request):
    """
    Handles POST from every edit form in dashboard.html.
    Each form sends a hidden field  name="section"  with values:
        basic | salary | bank | verification | additional
    """
    if request.method != 'POST':
        return redirect('dashboard')

    user    = request.user
    section = request.POST.get('section', '')

    salary,       _ = SalaryDetails.objects.get_or_create(user=user)
    bank,         _ = BankDetails.objects.get_or_create(user=user)
    verification, _ = VerificationDetails.objects.get_or_create(user=user)
    additional,   _ = AdditionalDetails.objects.get_or_create(user=user)

    try:
        if section == 'basic':
            # Update Django User fields
            user.first_name = request.POST.get('first_name', user.first_name).strip()
            user.last_name  = request.POST.get('last_name',  user.last_name).strip()
            user.email      = request.POST.get('email',      user.email).strip()
            user.save(update_fields=['first_name', 'last_name', 'email'])

            # Update AdditionalDetails fields that hold basic info
            additional.phone           = request.POST.get('phone',           '').strip() or None
            additional.department      = request.POST.get('department',      '').strip() or None
            additional.designation     = request.POST.get('designation',     '').strip() or None
            additional.address         = request.POST.get('address',         '').strip() or None

            dob = request.POST.get('date_of_birth', '').strip()
            doj = request.POST.get('date_of_joining', '').strip()
            if hasattr(additional, 'date_of_birth'):
                additional.date_of_birth  = dob or None
            if hasattr(additional, 'date_of_joining'):
                additional.date_of_joining = doj or None

            additional.save()
            messages.success(request, 'Basic details updated successfully!')

        elif section == 'salary':
            salary.basic_salary = request.POST.get('basic',            '') or 0
            salary.hra          = request.POST.get('hra',              '') or 0
            salary.bonus        = request.POST.get('other_allowances', '') or 0
            salary.save()
            messages.success(request, 'Salary details updated successfully!')

        elif section == 'bank':
            bank.account_number = request.POST.get('account_number', '').strip() or None
            bank.ifsc_code      = request.POST.get('ifsc',           '').strip() or None
            bank.bank_name      = request.POST.get('bank_name',      '').strip() or None
            bank.save()
            messages.success(request, 'Bank details updated successfully!')

        elif section == 'verification':
            verification.aadhar_number = request.POST.get('aadhaar', '').strip() or None
            verification.pan_number    = request.POST.get('pan',     '').strip() or None
            verification.save()
            messages.success(request, 'Verification details updated successfully!')

        elif section == 'additional':
            additional.emergency_contact = request.POST.get('emergency_contact', '').strip() or None
            additional.notes             = request.POST.get('notes',             '').strip() or None
            if hasattr(additional, 'blood_group'):
                additional.blood_group   = request.POST.get('blood_group',       '').strip() or None
            additional.save()
            messages.success(request, 'Additional details updated successfully!')

        else:
            messages.error(request, 'Unknown section. Nothing was saved.')

    except Exception as e:
        messages.error(request, f'Error saving data: {str(e)}')

    return redirect('dashboard')


# ──────────────────────────────────────────────────────
#  REST API views — UNCHANGED from original
# ──────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_data_api(request):
    """
    Returns all dashboard data for the logged-in user.
    """
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
        },
        "salary": {
            "basic_salary": str(salary.basic_salary),
            "hra":          str(salary.hra),
            "bonus":        str(salary.bonus),
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
            "address":           additional.address           or "",
            "emergency_contact": additional.emergency_contact or "",
            "notes":             additional.notes             or "",
        }
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def dashboard_update_api(request):
    """
    Updates dashboard data for logged-in user.
    Expects JSON payload structured like the GET API.
    """
    user = request.user

    salary,       _ = SalaryDetails.objects.get_or_create(user=user)
    bank,         _ = BankDetails.objects.get_or_create(user=user)
    verification, _ = VerificationDetails.objects.get_or_create(user=user)
    additional,   _ = AdditionalDetails.objects.get_or_create(user=user)

    data = request.data

    profile_form     = ProfileUpdateForm(data.get("profile",      {}), instance=user)
    salary_form      = SalaryForm(        data.get("salary",       {}), instance=salary)
    bank_form        = BankForm(          data.get("bank",         {}), instance=bank)
    verification_form= VerificationForm(  data.get("verification", {}), instance=verification)
    additional_form  = AdditionalForm(    data.get("additional",   {}), instance=additional)

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
        errors = {
            "profile":      profile_form.errors,
            "salary":       salary_form.errors,
            "bank":         bank_form.errors,
            "verification": verification_form.errors,
            "additional":   additional_form.errors,
        }
        return Response({"success": False, "errors": errors}, status=status.HTTP_400_BAD_REQUEST)

from django.contrib.auth.decorators import login_required
from .forms import ProfileUpdateForm
@api_view(['GET', 'POST'])
def forgot_password(request):

    # 🔹 GET → Open forgot password page
    if request.method == 'GET':
        return render(request, 'forgot_password.html')

    # 🔹 POST → Process email & send reset link
    email = request.data.get('email')

    if not email:
        return Response(
            {"error": "Email is required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        user = User.objects.get(email=email)   # ✅ FIXED (was user.object)

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        reset_link = f"http://127.0.0.1:8000/reset-password/{uid}/{token}/"

        send_mail(
            "Password Reset - LMS",
            f"Click below to reset your password:\n\n{reset_link}",
            "webmaster@localhost",
            [email],
        )

        return Response({
            "success": "Reset link sent to your email"
        })

    except User.DoesNotExist:
        return Response(
            {"error": "Email not registered"},
            status=status.HTTP_400_BAD_REQUEST
        )
from django.contrib.auth import get_user_model
from django.shortcuts import render
from django.utils.http import urlsafe_base64_decode
from django.utils.encoding import force_str
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth import get_user_model
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

User = get_user_model()


class ResetPasswordAPIView(APIView):

    # 🔹 Handle GET request (Open reset page)
    def get(self, request, uidb64, token):

        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)

            # 🔐 Validate token
            if not default_token_generator.check_token(user, token):
                return render(request, "invalid_link.html")

            return render(request, "reset_password.html", {
                "uidb64": uidb64,
                "token": token
            })

        except (User.DoesNotExist, ValueError, TypeError):
            return render(request, "invalid_link.html")

        print("TOKEN FROM URL:", token)
        print("CHECK:", default_token_generator.check_token(user, token))

    # 🔹 Handle POST request (Reset password)
    def post(self, request, uidb64, token):

        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)

            # 🔐 Validate token again
            if not default_token_generator.check_token(user, token):
                return Response(
                    {"error": "Reset link is invalid or expired"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            password1 = request.data.get("password1")
            password2 = request.data.get("password2")

            # Validation
            if not password1 or not password2:
                return Response(
                    {"error": "All fields are required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if password1 != password2:
                return Response(
                    {"error": "Passwords do not match"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # ✅ Save new password
            user.set_password(password1)
            user.save()

            return Response({
                "message": "Password reset successful",
                "redirect": "/login/"
            }, status=status.HTTP_200_OK)

        except (User.DoesNotExist, ValueError, TypeError):
            return Response(
                {"error": "Invalid reset link"},
                status=status.HTTP_400_BAD_REQUEST
            )
            


from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.utils.crypto import get_random_string
from django.contrib.auth import get_user_model
from django.views.decorators.csrf import csrf_exempt
from rest_framework_simplejwt.authentication import JWTAuthentication

User = get_user_model()

@csrf_exempt
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def register_view(request):
    """
    Only Admin can create users.
    """
    # Check admin
    if not request.user.role or request.user.role.name != "Admin":
        return Response({"error": "Only Admin can register new users"}, status=403)

    email = request.data.get("email")
    role_name = request.data.get("role")
    is_senior = request.data.get("is_senior", False)

    if not email or not role_name:
        return Response({"error": "Email and Role are required"}, status=400)

    # Admin can only assign HR role
    if role_name != "HR":
        return Response({"error": "Admin can only assign role HR"}, status=403)

    # Check email uniqueness
    if User.objects.filter(email=email).exists():
        return Response({"error": "Email already exists"}, status=400)

    # Get Role instance
    role_obj = Role.objects.filter(name=role_name).first()
    if not role_obj:
        return Response({"error": "Role not found"}, status=400)

    # Generate password
    random_password = get_random_string(length=8)

    # Create user
    user = User.objects.create_user(
        username=email,
        email=email,
        password=random_password,
        role=role_obj,
        is_senior=is_senior
    )

    return Response({
        "message": "User created successfully",
        "email": email,
        "generated_password": random_password
    }, status=201)

def home_view(request):
    """
    Home page of the LMS application.
    Redirects logged-in users to dashboard automatically,
    otherwise shows a modern landing page.
    """
    if request.user.is_authenticated:
        # Redirect logged-in users to their dashboard
        if request.user.role == "Admin":
            return redirect("/admin_dashboard/")
        elif request.user.role == "HR":
            return redirect("/hr_dashboard/")
        elif request.user.role == "TL":
            return redirect("/tl_dashboard/")
        elif request.user.role == "Employee":
            return redirect("/employee_dashboard/")
        else:
            return redirect("/dashboard/")

    # Show landing page for non-authenticated users
    return render(request, "home.html")



from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Count
from .models import Department, User


# ── Admin-only guard ──────────────────────────────────
def _is_admin(request):
    role = getattr(request.user, 'role', None)
    return role and role.name == 'Admin'


# ─────────────────────────────────────────────────────
#  LIST  —  /departments/
# ─────────────────────────────────────────────────────
@login_required
def department_list(request):
    if not _is_admin(request):
        messages.error(request, "Access denied. Admins only.")
        return redirect('admin_dashboard')

    search = request.GET.get('q', '').strip()
    departments = Department.objects.annotate(
        emp_count=Count('user')          # reverse of User.department ForeignKey
    ).order_by('name')

    if search:
        departments = departments.filter(name__icontains=search)

    # HR users for the head dropdown (used in modals)
    hr_users = User.objects.filter(role__name__in=['HR', 'Manager']).order_by('first_name')

    context = {
        'departments': departments,
        'hr_users':    hr_users,
        'search':      search,
        'total':       Department.objects.count(),
    }
    return render(request, 'department_list.html', context)


# ─────────────────────────────────────────────────────
#  CREATE  —  POST /departments/create/
# ─────────────────────────────────────────────────────
@login_required
def department_create(request):
    if not _is_admin(request):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')

    if request.method == 'POST':
        name   = request.POST.get('name', '').strip()
        hr_id  = request.POST.get('hr', '').strip()

        if not name:
            messages.error(request, "Department name is required.")
            return redirect('department_list')

        if Department.objects.filter(name__iexact=name).exists():
            messages.error(request, f'Department "{name}" already exists.')
            return redirect('department_list')

        dept = Department(name=name)
        if hr_id:
            try:
                dept.hr = User.objects.get(pk=hr_id)
            except User.DoesNotExist:
                pass
        dept.save()
        messages.success(request, f'Department "{name}" created successfully!')

    return redirect('department_list')


# ─────────────────────────────────────────────────────
#  EDIT  —  GET/POST /departments/<pk>/edit/
# ─────────────────────────────────────────────────────
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
            return redirect('department_list')

        # Check duplicate name (excluding self)
        if Department.objects.filter(name__iexact=name).exclude(pk=pk).exists():
            messages.error(request, f'Another department named "{name}" already exists.')
            return redirect('department_list')

        dept.name = name
        dept.hr   = User.objects.get(pk=hr_id) if hr_id else None
        dept.save()
        messages.success(request, f'Department "{name}" updated successfully!')

    return redirect('department_list')


# ─────────────────────────────────────────────────────
#  DELETE  —  POST /departments/<pk>/delete/
# ─────────────────────────────────────────────────────
@login_required
def department_delete(request, pk):
    if not _is_admin(request):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')

    dept = get_object_or_404(Department, pk=pk)

    if request.method == 'POST':
        name = dept.name
        # Nullify department FK on all users in this dept before deleting
        User.objects.filter(department=dept).update(department=None)
        dept.delete()
        messages.success(request, f'Department "{name}" deleted successfully!')

    return redirect('department_list')


# ─────────────────────────────────────────────────────
#  DETAIL  —  /departments/<pk>/
#  Shows all employees in a department
# ─────────────────────────────────────────────────────
@login_required
def department_detail(request, pk):
    if not _is_admin(request):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')

    dept      = get_object_or_404(Department, pk=pk)
    employees = User.objects.filter(department=dept).select_related('role').order_by('first_name')

    context = {
        'dept':      dept,
        'employees': employees,
        'emp_count': employees.count(),
    }
    return render(request, 'department_detail.html', context)

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Q

from .models import Role, RolePermission, User, Department


# ─────────────────────────────────────────────────────────────────
#  ALL MODULES with display name + FA icon
# ─────────────────────────────────────────────────────────────────
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


def _is_admin(request):
    role = getattr(request.user, 'role', None)
    return bool(role and role.name == 'Admin')


# =================================================================
#  ROLE VIEWS
# =================================================================

@login_required
def role_list(request):
    if not _is_admin(request):
        messages.error(request, "Access denied. Admins only.")
        return redirect('admin_dashboard')

    roles = Role.objects.annotate(
        user_count=Count('user', distinct=True)
    ).order_by('name')

    context = {
        'roles':       roles,
        'total_roles': roles.count(),
        'total_users': User.objects.count(),
    }
    return render(request, 'role_list.html', context)


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
            old_name  = role.name
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
        messages.success(request, f'Role "{name}" deleted. Affected users have been unassigned.')

    return redirect('role_list')


# =================================================================
#  PERMISSION VIEWS  —  Two-panel: select role → manage permissions
# =================================================================

# Default permission sets per role (applied when a new role is selected/created)
ROLE_DEFAULTS = {
    'Admin':    dict(can_view=True,  can_create=True,  can_edit=True,  can_delete=True),
    'HR':       dict(can_view=True,  can_create=True,  can_edit=True,  can_delete=False),
    'Manager':  dict(can_view=True,  can_create=False, can_edit=True,  can_delete=False),
    'TL':       dict(can_view=True,  can_create=False, can_edit=True,  can_delete=False),
    'Employee': dict(can_view=True,  can_create=False, can_edit=False, can_delete=False),
}

# Module-level overrides — certain roles cannot access certain modules at all
MODULE_ROLE_RESTRICTIONS = {
    # (role_name, module_key) → dict of what is allowed (overrides defaults)
    ('HR',       'departments'):   dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('TL',       'departments'):   dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('TL',       'salary'):        dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Employee', 'departments'):   dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Employee', 'salary'):        dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Employee', 'bank'):          dict(can_view=True,  can_create=False, can_edit=True,  can_delete=False),
    ('Employee', 'employees'):     dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Manager',  'departments'):   dict(can_view=True,  can_create=False, can_edit=True,  can_delete=False),
}


@login_required
def role_permission_list(request):
    """
    GET  : Show all roles in left panel. If ?role=<pk> also show that role's permissions.
    """
    if not _is_admin(request):
        messages.error(request, "Access denied. Admins only.")
        return redirect('admin_dashboard')

    roles      = Role.objects.all().order_by('name')
    selected_pk = request.GET.get('role', '').strip()
    selected_role = None
    perm_rows     = []

    if selected_pk:
        try:
            selected_role = Role.objects.get(pk=selected_pk)

            # Ensure all module rows exist for this role
            for mod_key, mod_label, mod_icon in MODULES:
                defaults = dict(can_view=False, can_create=False,
                                can_edit=False, can_delete=False)
                # Apply role defaults
                role_def = ROLE_DEFAULTS.get(selected_role.name, defaults)
                # Apply module-level restrictions
                restrict = MODULE_ROLE_RESTRICTIONS.get(
                    (selected_role.name, mod_key), role_def
                )
                RolePermission.objects.get_or_create(
                    role=selected_role, module=mod_key,
                    defaults=restrict
                )

            # Fetch all permissions for selected role
            perm_map = {
                p.module: p
                for p in RolePermission.objects.filter(role=selected_role)
            }

            for mod_key, mod_label, mod_icon in MODULES:
                perm_rows.append({
                    'key':   mod_key,
                    'label': mod_label,
                    'icon':  mod_icon,
                    'perm':  perm_map.get(mod_key),
                })

        except Role.DoesNotExist:
            messages.error(request, "Role not found.")

    context = {
        'roles':         roles,
        'selected_role': selected_role,
        'perm_rows':     perm_rows,
        'modules':       MODULES,
    }
    return render(request, 'role_permission_list.html', context)


@login_required
def role_permission_save(request):
    """
    POST: Save permissions for ONE role (submitted from the right panel form).
    """
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


# =================================================================
#  ASSIGN ROLE VIEWS
# =================================================================

@login_required
def assign_role(request):
    if not _is_admin(request):
        messages.error(request, "Access denied. Admins only.")
        return redirect('admin_dashboard')

    roles       = Role.objects.all().order_by('name')
    departments = Department.objects.all().order_by('name')

    dept_id    = request.GET.get('dept', '').strip()
    search     = request.GET.get('q',    '').strip()
    filter_role = request.GET.get('role', '').strip()

    users = User.objects.select_related('role', 'department').order_by('first_name', 'last_name')

    if dept_id:
        users = users.filter(department_id=dept_id)
    if filter_role:
        users = users.filter(role_id=filter_role)
    if search:
        users = users.filter(
            Q(first_name__icontains=search) |
            Q(last_name__icontains=search)  |
            Q(email__icontains=search)
        )

    if request.method == 'POST':
        user_id     = request.POST.get('user_id',  '').strip()
        new_role_id = request.POST.get('role_id',  '').strip()

        if not user_id or not new_role_id:
            messages.error(request, "User and role are both required.")
            return redirect('assign_role')

        try:
            target   = User.objects.select_related('role').get(pk=user_id)
            new_role = Role.objects.get(pk=new_role_id)

            if (
                target.role and target.role.name == 'Admin'
                and new_role.name != 'Admin'
                and User.objects.filter(role__name='Admin').count() <= 1
            ):
                messages.error(request, "Cannot remove the only Admin user.")
                return redirect('assign_role')

            old_role    = target.role.name if target.role else 'None'
            target.role = new_role
            target.save(update_fields=['role'])

            messages.success(
                request,
                f'{target.get_full_name() or target.email}: {old_role} → {new_role.name}'
            )
        except User.DoesNotExist:
            messages.error(request, "User not found.")
        except Role.DoesNotExist:
            messages.error(request, "Role not found.")

        return redirect('assign_role')

    context = {
        'users':       users,
        'roles':       roles,
        'departments': departments,
        'dept_id':     dept_id,
        'filter_role': filter_role,
        'search':      search,
        'total_users': users.count(),
    }
    return render(request, 'assign_role.html', context)


@login_required
def assign_role_bulk(request):
    """Assigns the same role to ALL employees in a department."""
    if not _is_admin(request):
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

            count = User.objects.filter(
                department=dept
            ).exclude(role__name='Admin').update(role=new_role)

            messages.success(
                request,
                f'Assigned "{new_role.name}" to {count} employee(s) in {dept.name} '
                f'(Admin users skipped).'
            )
        except (Department.DoesNotExist, Role.DoesNotExist):
            messages.error(request, "Invalid department or role.")

    return redirect('assign_role')