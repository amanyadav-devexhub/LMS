import json
from datetime import datetime

from django.db import models as django_models
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login as auth_login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.utils.decorators import method_decorator
from django.core.mail import send_mail
from django.db.models import Q, Count
from django.urls import reverse

from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.authentication import JWTAuthentication

from django.conf import settings
from django.http import JsonResponse
from django.utils.crypto import get_random_string
from types import SimpleNamespace

from .models import (
    Role, Department, RolePermission,
    SalaryDetails, BankDetails, VerificationDetails, AdditionalDetails,
    RBACPermission, RolePermissionAssignment,
)
from .rbac import (
    ensure_permission_catalog,
    sync_matrix_permissions,
    role_has_permission,
    user_has_permission,
    menu_permission_flags,
    LEGACY_MATRIX_ACTIONS,
    get_user_permission_codes,
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

def _build_profile_context(user):
        return {
            "id": user.id,
            "name": user.get_full_name(),
            "email": user.email,
        }
from django.shortcuts import render

def profile_page(request):
    return render(request, 'profile.html')

@csrf_exempt
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

    # auth_login(request, user)  # Removed: requires SessionMiddleware
    refresh = RefreshToken.for_user(user)

    permission_codes = set(get_user_permission_codes(user))
    if "dashboard_admin" in permission_codes:
        redirect_url = "/dashboard/"
    elif "dashboard_hr" in permission_codes:
        redirect_url = "/hr_dashboard/"
    elif "dashboard_manager" in permission_codes:
        redirect_url = "/manager_dashboard/"
    elif "dashboard_employee" in permission_codes:
        redirect_url = "/employee_dashboard/"
    else:
        redirect_url = "/dashboard/"

    response = Response({
        "access":   str(refresh.access_token),
        "refresh":  str(refresh),
        "redirect": redirect_url,
        "permissions": sorted(permission_codes),
    }, status=status.HTTP_200_OK)

    # Set cookies
    response.set_cookie(
        'access_token',
        str(refresh.access_token),
        httponly=True,
        secure=not settings.DEBUG,
        samesite='Lax',
        max_age=3600 # 1 hour
    )
    response.set_cookie(
        'refresh_token',
        str(refresh),
        httponly=True,
        secure=not settings.DEBUG,
        samesite='Lax',
        max_age=86400 # 1 day
    )

    return response


def user_logout(request):
    logout(request)
    response = redirect("login")
    response.delete_cookie('access_token', path='/', samesite='Lax')
    response.delete_cookie('refresh_token', path='/', samesite='Lax')
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


# ══════════════════════════════════════════════════════════════
#  DASHBOARD — helper: build profile proxy from all models
# ══════════════════════════════════════════════════════════════

# def _build_profile_context(user):
#     """
#     Returns a simple object with every field the dashboard.html
#     template references as  {{ profile.xxx }}.
#     Fetches/creates all related models on demand.
#     """
#     salary,       _ = SalaryDetails.objects.get_or_create(user=user)
#     bank,         _ = BankDetails.objects.get_or_create(user=user)
#     verification, _ = VerificationDetails.objects.get_or_create(user=user)
#     additional,   _ = AdditionalDetails.objects.get_or_create(user=user)

#     class P:
#         pass

#     p = P()

#     # ── Identity ──────────────────────────────────────────────
#     p.employee_id     = user.pk          # use user PK as employee ID
#     p.avatar          = user.avatar if user.avatar else None

#     # ── Contact (User model) ──────────────────────────────────
#     p.phone           = additional.phone

#     # ── Contact (AdditionalDetails) ───────────────────────────
#     p.personal_email  = additional.personal_email
#     p.alternate_phone = additional.alternate_phone

#     # ── HR-set fields (User model) ────────────────────────────
#     p.department      = user.department.name if user.department else None
#     p.designation     = getattr(user, 'designation', None)   # add field if needed
#     p.date_of_joining = user.date_of_joining

#     # ── Personal (AdditionalDetails) ─────────────────────────
#     p.date_of_birth   = additional.date_of_birth
#     p.gender          = additional.gender
#     p.marital_status  = additional.marital_status
#     p.blood_group     = additional.blood_group

#     # ── Emergency (AdditionalDetails) ────────────────────────
#     p.emergency_contact  = additional.emergency_contact
#     p.emergency_relation = additional.emergency_relation
#     p.emergency_phone    = additional.emergency_phone

#     # ── Address (AdditionalDetails) ──────────────────────────
#     p.current_address   = additional.current_address
#     p.permanent_address = additional.permanent_address

#     # ── Salary ────────────────────────────────────────────────
#     p.basic            = salary.basic_salary  or None
#     p.hra              = salary.hra           or None
#     p.other_allowances = salary.bonus         or None
#     p.salary_in_hand   = salary.salary_in_hand or (
#         (salary.basic_salary or 0) + (salary.hra or 0) + (salary.bonus or 0)
#     ) or None

#     # ── Bank ──────────────────────────────────────────────────
#     p.account_number  = bank.account_number
#     p.ifsc            = bank.ifsc_code
#     p.bank_name       = bank.bank_name

#     # ── Verification ─────────────────────────────────────────
#     p.aadhaar         = verification.aadhar_number
#     p.pan             = verification.pan_number

#     # ── Additional / misc ────────────────────────────────────
#     p.notes           = additional.notes

#     # ── Leave balance ─────────────────────────────────────────
#     p.leave_balance   = getattr(user, 'leave_balance', None)

#     return p


# # ══════════════════════════════════════════════════════════════
# #  DASHBOARD — template view
# # ══════════════════════════════════════════════════════════════

# @login_required
# def dashboard_template_view(request, user_id=None):
#     if user_id:
#         profile_user = get_object_or_404(User, pk=user_id)
#     else:
#         profile_user = request.user
        
#     profile = _build_profile_context(profile_user)

#     can_edit_profile = False
#     if request.user == profile_user:
#         can_edit_profile = True
#     elif request.user.role and request.user.role.name == 'Admin':
#         can_edit_profile = True
#     elif request.user.role and request.user.role.name == 'HR' and (not profile_user.role or profile_user.role.name != 'Admin'):
#         can_edit_profile = True

#     total_leaves = approved_leaves = pending_leaves = 0
#     try:
#         from leaves.models import LeaveRequest
#         qs              = LeaveRequest.objects.filter(user=profile_user)
#         total_leaves    = qs.count()
#         approved_leaves = qs.filter(status='Approved').count()
#         pending_leaves  = qs.filter(status='Pending').count()
#     except Exception:
#         pass

#     return render(request, 'dashboard.html', {
#         'profile':         profile,
#         'profile_user':    profile_user,
#         'can_edit_profile': can_edit_profile,
#         'total_leaves':    total_leaves,
#         'approved_leaves': approved_leaves,
#         'pending_leaves':  pending_leaves,
#         'leave_balance':   profile.leave_balance,
#     })


# # ══════════════════════════════════════════════════════════════
# #  UPDATE PROFILE — handles ALL form sections
# # ══════════════════════════════════════════════════════════════

# @login_required
# def update_profile(request):
#     """
#     Handles POST from every edit form in dashboard.html.
#     Hidden field  name="section"  identifies which form was submitted.
#     Hidden field  name="target_user_id" identifies whose profile is being edited.
#     """
#     if request.method != 'POST':
#         return redirect('profile_dashboard')

#     section = request.POST.get('section', '').strip()
#     target_user_id = request.POST.get('target_user_id')

#     if target_user_id:
#         user = get_object_or_404(User, pk=target_user_id)
#     else:
#         user = request.user

#     # Compute authorization
#     can_edit_profile = False
#     if request.user == user:
#         can_edit_profile = True
#     elif request.user.role and request.user.role.name == 'Admin':
#         can_edit_profile = True
#     elif request.user.role and request.user.role.name == 'HR' and (not user.role or user.role.name != 'Admin'):
#         can_edit_profile = True

#     if not can_edit_profile:
#         messages.error(request, 'You do not have permission to edit this profile.')
#         return redirect('profile_detail', user_id=user.pk) if target_user_id else redirect('profile_dashboard')

#     # pre-fetch related models
#     additional,   _ = AdditionalDetails.objects.get_or_create(user=user)
#     salary,       _ = SalaryDetails.objects.get_or_create(user=user)
#     bank,         _ = BankDetails.objects.get_or_create(user=user)
#     verification, _ = VerificationDetails.objects.get_or_create(user=user)

#     def _str(key, default=''):
#         return request.POST.get(key, default).strip()

#     def _date(key):
#         val = _str(key)
#         return val if val else None

#     def _decimal(key):
#         val = _str(key)
#         try:
#             return float(val) if val else 0
#         except ValueError:
#             return 0

#     try:

#         # ── EMPLOYEE: basic details ──────────────────────────
#         if section == 'basic_employee':

#             # --- User model fields ---
#             user.first_name = _str('first_name') or user.first_name
#             user.last_name  = _str('last_name')  or user.last_name

#             new_email = _str('email')
#             if new_email and new_email != user.email:
#                 if User.objects.filter(email=new_email).exclude(pk=user.pk).exists():
#                     messages.error(request, 'That email address is already in use.')
#                     return redirect('profile_detail', user_id=user.pk) if target_user_id else redirect('profile_dashboard')
#                 user.email = new_email

#             user.save(update_fields=['first_name', 'last_name', 'email'])

#             # --- Avatar ---
#             if 'avatar' in request.FILES:
#                 user.avatar = request.FILES['avatar']
#                 user.save(update_fields=['avatar'])

#             # --- AdditionalDetails fields ---
#             additional.personal_email    = _str('personal_email')   or None
#             additional.alternate_phone   = _str('alternate_phone')  or None
#             additional.phone   = _str('phone')  or None
#             additional.date_of_birth     = _date('date_of_birth')
#             additional.gender            = _str('gender')           or None
#             additional.marital_status    = _str('marital_status')   or None
#             additional.emergency_contact = _str('emergency_contact') or None
#             additional.emergency_relation= _str('emergency_relation') or None
#             additional.emergency_phone   = _str('emergency_phone')  or None
#             additional.current_address   = _str('current_address')  or None
#             additional.permanent_address = _str('permanent_address') or None
#             additional.save()

#             messages.success(request, 'Profile updated successfully!')

#         # ── HR/ADMIN: basic details (HR-controlled fields) ───
#         elif section == 'basic_hr':

#             # Only HR, Admin, Manager can use this section (handled by global can_edit_profile? Actually, basic_hr is specifically for HR/Admin fields like Designation/Dept).
#             # Wait, an Employee editing their own profile should NOT use basic_hr.
#             if request.user.role and request.user.role.name not in ('HR', 'Admin', 'Manager') and not request.user.is_superuser:
#                 messages.error(request, 'Access denied.')
#                 return redirect('profile_detail', user_id=user.pk) if target_user_id else redirect('profile_dashboard')

#             # target_user = User.objects.get(pk=...) # Wait, the current logic uses 'user = request.user'
#             # The section is for HR/Admin to edit THEIR OWN profile or others?
#             # In dashboard context, it is usually editing their own.
            
#             user.first_name = _str('first_name') or user.first_name
#             user.last_name  = _str('last_name')  or user.last_name

#             new_email = _str('email')
#             if new_email and new_email != user.email:
#                 if User.objects.filter(email=new_email).exclude(pk=user.pk).exists():
#                     messages.error(request, 'That email is already in use.')
#                     return redirect('profile_detail', user_id=user.pk) if target_user_id else redirect('profile_dashboard')
#                 user.email = new_email
            
#             # Update Designation (Added field)
#             user.designation = _str('designation') or user.designation
            
#             # Update Department
#             dept_id = _str('department')
#             if dept_id:
#                 try:
#                     user.department = Department.objects.get(pk=dept_id)
#                 except (Department.DoesNotExist, ValueError):
#                     pass

#             # Update Phone (Sync both models)
#             p_val = _str('phone')
#             if p_val:
#                 user.phone = p_val
#                 additional.phone = p_val

#             doj = _date('date_of_joining')
#             if doj:
#                 user.date_of_joining = doj

#             user.save()
#             additional.save()
#             messages.success(request, 'HR basic details updated successfully!')

#         # ── SALARY ──────────────────────────────────────────
#         elif section == 'salary':
#             salary.basic_salary    = _decimal('basic')
#             salary.hra             = _decimal('hra')
#             salary.bonus           = _decimal('other_allowances')
#             salary.salary_in_hand  = _decimal('salary_in_hand')
#             salary.save()
#             messages.success(request, 'Salary details updated successfully!')

#         # ── BANK ────────────────────────────────────────────
#         elif section == 'bank':
#             bank.account_number = _str('account_number') or None
#             bank.ifsc_code      = _str('ifsc')           or None
#             bank.bank_name      = _str('bank_name')      or None
#             bank.save()
#             messages.success(request, 'Bank details updated successfully!')

#         # ── VERIFICATION ────────────────────────────────────
#         elif section == 'verification':
#             verification.aadhar_number = _str('aadhaar') or None
#             verification.pan_number    = _str('pan')     or None
#             verification.save()
#             messages.success(request, 'Verification details updated successfully!')

#         # ── ADDITIONAL ──────────────────────────────────────
#         elif section == 'additional':
#             additional.blood_group = _str('blood_group') or None
#             additional.notes       = _str('notes')       or None
#             additional.save()
#             messages.success(request, 'Additional details updated successfully!')

#         else:
#             messages.error(request, 'Unknown section. Nothing was saved.')

#     except Exception as e:
#         messages.error(request, f'Error saving data: {str(e)}')

#     return redirect('profile_detail', user_id=user.pk) if target_user_id else redirect('profile_dashboard')


# # ══════════════════════════════════════════════════════════════
# #  REST API — dashboard data & update (unchanged from original)
# # ══════════════════════════════════════════════════════════════

# @api_view(['GET'])
# @permission_classes([IsAuthenticated])
# def dashboard_data_api(request):
#     user = request.user

#     salary,       _ = SalaryDetails.objects.get_or_create(user=user)
#     bank,         _ = BankDetails.objects.get_or_create(user=user)
#     verification, _ = VerificationDetails.objects.get_or_create(user=user)
#     additional,   _ = AdditionalDetails.objects.get_or_create(user=user)

#     return Response({
#         "profile": {
#             "first_name": user.first_name,
#             "last_name":  user.last_name,
#             "email":      user.email,
#             "phone":      additional.phone or "",
#         },
#         "salary": {
#             "basic_salary":   str(salary.basic_salary),
#             "hra":            str(salary.hra),
#             "bonus":          str(salary.bonus),
#             "salary_in_hand": str(salary.salary_in_hand),
#         },
#         "bank": {
#             "bank_name":      bank.bank_name      or "",
#             "account_number": bank.account_number or "",
#             "ifsc_code":      bank.ifsc_code      or "",
#         },
#         "verification": {
#             "aadhar_number": verification.aadhar_number or "",
#             "pan_number":    verification.pan_number    or "",
#             "is_verified":   verification.is_verified,
#         },
#         "additional": {
#             "personal_email":    additional.personal_email    or "",
#             "alternate_phone":   additional.alternate_phone   or "",
#             "date_of_birth":     str(additional.date_of_birth) if additional.date_of_birth else "",
#             "gender":            additional.gender            or "",
#             "marital_status":    additional.marital_status    or "",
#             "blood_group":       additional.blood_group       or "",
#             "emergency_contact": additional.emergency_contact or "",
#             "emergency_relation":additional.emergency_relation or "",
#             "emergency_phone":   additional.emergency_phone   or "",
#             "current_address":   additional.current_address   or "",
#             "permanent_address": additional.permanent_address or "",
#             "notes":             additional.notes             or "",
#         },
#     })


# @api_view(['POST'])
# @permission_classes([IsAuthenticated])
# def dashboard_update_api(request):
#     user = request.user

#     salary,       _ = SalaryDetails.objects.get_or_create(user=user)
#     bank,         _ = BankDetails.objects.get_or_create(user=user)
#     verification, _ = VerificationDetails.objects.get_or_create(user=user)
#     additional,   _ = AdditionalDetails.objects.get_or_create(user=user)

#     data = request.data

#     profile_form      = ProfileUpdateForm(data.get("profile",      {}), instance=user)
#     salary_form       = SalaryForm(        data.get("salary",       {}), instance=salary)
#     bank_form         = BankForm(          data.get("bank",         {}), instance=bank)
#     verification_form = VerificationForm(  data.get("verification", {}), instance=verification)
#     additional_form   = AdditionalForm(    data.get("additional",   {}), instance=additional)

#     if all([
#         profile_form.is_valid(),
#         salary_form.is_valid(),
#         bank_form.is_valid(),
#         verification_form.is_valid(),
#         additional_form.is_valid(),
#     ]):
#         profile_form.save()
#         salary_form.save()
#         bank_form.save()
#         verification_form.save()
#         additional_form.save()
#         return Response({"success": True})
#     else:
#         return Response({
#             "success": False,
#             "errors": {
#                 "profile":      profile_form.errors,
#                 "salary":       salary_form.errors,
#                 "bank":         bank_form.errors,
#                 "verification": verification_form.errors,
#                 "additional":   additional_form.errors,
#             },
#         }, status=status.HTTP_400_BAD_REQUEST)




@login_required
def profile_api(request):
    """
    Dual-mode endpoint:
    - GET from browser → Render HTML template
    - GET from fetch/XHR → Return JSON
    - POST → Update profile data
    """
    user = request.user
    can_salary_view = user_has_permission(user, "salary_view")
    can_salary_update = user_has_permission(user, "salary_update")
    can_bank_view = user_has_permission(user, "bank_view")
    can_bank_update = user_has_permission(user, "bank_update")
    can_verification_view = user_has_permission(user, "verification_view")
    can_verification_update = user_has_permission(user, "verification_update")
    
    # related models
    salary, _ = SalaryDetails.objects.get_or_create(user=user)
    bank, _ = BankDetails.objects.get_or_create(user=user)
    verification, _ = VerificationDetails.objects.get_or_create(user=user)
    additional, _ = AdditionalDetails.objects.get_or_create(user=user)
    
    # Check if this is an AJAX/fetch request
    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
        'application/json' in request.headers.get('Accept', '')
    )
    
    # ─────────────────────────────────────
    # ✅ GET request
    # ─────────────────────────────────────
    if request.method == 'GET':
        profile_data = {
            "success": True,
            "data": {
                "profile": {
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "email": user.email,
                    "username": user.username,
                    "phone": additional.phone or user.phone,
                    "personal_email": additional.personal_email,
                    "alternate_phone": additional.alternate_phone,
                    "date_of_birth": additional.date_of_birth.isoformat() if additional.date_of_birth else None,
                    "gender": additional.gender,
                    "marital_status": additional.marital_status,
                    "emergency_contact": additional.emergency_contact,
                    "emergency_relation": additional.emergency_relation,
                    "emergency_phone": additional.emergency_phone,
                    "current_address": additional.current_address,
                    "permanent_address": additional.permanent_address,
                },
                "salary": {
                    "basic": (float(salary.basic_salary) if salary.basic_salary else 0) if can_salary_view else None,
                    "hra": (float(salary.hra) if salary.hra else 0) if can_salary_view else None,
                    "other": (float(salary.bonus) if salary.bonus else 0) if can_salary_view else None,
                    "in_hand": (float(salary.salary_in_hand) if salary.salary_in_hand else 0) if can_salary_view else None,
                },
                "bank": {
                    "account_number": bank.account_number if can_bank_view else None,
                    "ifsc": bank.ifsc_code if can_bank_view else None,
                    "bank_name": bank.bank_name if can_bank_view else None,
                },
                "verification": {
                    "aadhaar": verification.aadhar_number if can_verification_view else None,
                    "pan": verification.pan_number if can_verification_view else None,
                },
                "additional": {
                    "blood_group": additional.blood_group,
                    "notes": additional.notes,
                },
                "department": user.department.name if user.department else None,
                "designation": user.designation,
                "date_of_joining": user.date_of_joining.isoformat() if user.date_of_joining else None,
                "avatar_url": user.avatar.url if user.avatar else None,
            }
        }
        
        # Return JSON if AJAX request
        if is_ajax:
            return JsonResponse(profile_data)
        
        # Return HTML template otherwise
        from leaves.models import LeaveRequest
        total_leaves = LeaveRequest.objects.filter(employee=user).count()
        approved_leaves = LeaveRequest.objects.filter(employee=user, final_status='APPROVED').count()
        pending_leaves = LeaveRequest.objects.filter(employee=user, final_status='PENDING').count()
        
        # Build profile context for template
        profile = {
            'avatar': user.avatar,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email,
            'username': user.username,
            'phone': additional.phone or user.phone,
            'personal_email': additional.personal_email,
            'alternate_phone': additional.alternate_phone,
            'employee_id': user.id,
            'department': user.department.name if user.department else '',
            'designation': user.designation,
            'date_of_joining': user.date_of_joining,
            'date_of_birth': additional.date_of_birth,
            'gender': additional.gender,
            'marital_status': additional.marital_status,
            'blood_group': additional.blood_group,
            'current_address': additional.current_address,
            'permanent_address': additional.permanent_address,
            'emergency_contact': additional.emergency_contact,
            'emergency_relation': additional.emergency_relation,
            'emergency_phone': additional.emergency_phone,
            'basic': (float(salary.basic_salary) if salary.basic_salary else None) if can_salary_view else None,
            'hra': (float(salary.hra) if salary.hra else None) if can_salary_view else None,
            'other_allowances': (float(salary.bonus) if salary.bonus else None) if can_salary_view else None,
            'salary_in_hand': (float(salary.salary_in_hand) if salary.salary_in_hand else None) if can_salary_view else None,
            'account_number': bank.account_number if can_bank_view else None,
            'ifsc': bank.ifsc_code if can_bank_view else None,
            'bank_name': bank.bank_name if can_bank_view else None,
            'aadhaar': verification.aadhar_number if can_verification_view else None,
            'pan': verification.pan_number if can_verification_view else None,
            'notes': additional.notes,
        }
        
        return render(request, 'profile.html', {
            'profile': profile,
            'profile_user': user,
            'profile_data': profile_data['data'],
            'total_leaves': total_leaves,
            'approved_leaves': approved_leaves,
            'pending_leaves': pending_leaves,
            'can_edit_profile': True,
            'can_salary_view': can_salary_view,
            'can_salary_update': can_salary_update,
            'can_bank_view': can_bank_view,
            'can_bank_update': can_bank_update,
            'can_verification_view': can_verification_view,
            'can_verification_update': can_verification_update,
        })

    # ─────────────────────────────────────
    # ✅ POST → UPDATE PROFILE
    # ─────────────────────────────────────
    elif request.method == 'POST':
        try:
            # Get the section being updated
            section = request.POST.get('section', '')
            target_user_id = request.POST.get('target_user_id')
            
            # If editing another user's profile (HR/Admin)
            if target_user_id and int(target_user_id) != user.id:
                # Check permissions
                if user_has_permission(user, "user_update") or user_has_permission(user, "user_view"):
                    target_user = get_object_or_404(User, pk=target_user_id)
                else:
                    return JsonResponse({"success": False, "error": "You don't have permission to edit this profile."}, status=403)
            else:
                target_user = user
            
            # Get or create related models for target user
            target_salary, _ = SalaryDetails.objects.get_or_create(user=target_user)
            target_bank, _ = BankDetails.objects.get_or_create(user=target_user)
            target_verification, _ = VerificationDetails.objects.get_or_create(user=target_user)
            target_additional, _ = AdditionalDetails.objects.get_or_create(user=target_user)
            
            # ─── EMPLOYEE BASIC DETAILS ───
            if section == 'basic_employee':
                # Update User model fields
                if request.POST.get('first_name'):
                    target_user.first_name = request.POST.get('first_name').strip()
                if request.POST.get('last_name'):
                    target_user.last_name = request.POST.get('last_name').strip()
                
                # Update email if changed
                new_email = request.POST.get('email', '').strip()
                if new_email and new_email != target_user.email:
                    if User.objects.filter(email=new_email).exclude(pk=target_user.pk).exists():
                        return JsonResponse({"success": False, "error": "Email already in use by another user."}, status=400)
                    target_user.email = new_email
                
                target_user.save()
                
                # Handle avatar upload
                if 'avatar' in request.FILES:
                    target_user.avatar = request.FILES['avatar']
                    target_user.save()
                
                # Update AdditionalDetails
                if request.POST.get('personal_email'):
                    target_additional.personal_email = request.POST.get('personal_email').strip()
                if request.POST.get('alternate_phone'):
                    target_additional.alternate_phone = request.POST.get('alternate_phone').strip()
                if request.POST.get('phone'):
                    target_additional.phone = request.POST.get('phone').strip()
                if 'date_of_birth' in request.POST:
                    date_of_birth_raw = (request.POST.get('date_of_birth') or '').strip()
                    if date_of_birth_raw:
                        try:
                            target_additional.date_of_birth = datetime.strptime(date_of_birth_raw, '%Y-%m-%d').date()
                        except ValueError:
                            return JsonResponse({"success": False, "error": "Invalid date of birth format."}, status=400)
                    else:
                        target_additional.date_of_birth = None
                if request.POST.get('gender'):
                    target_additional.gender = request.POST.get('gender')
                if request.POST.get('marital_status'):
                    target_additional.marital_status = request.POST.get('marital_status')
                if request.POST.get('emergency_contact'):
                    target_additional.emergency_contact = request.POST.get('emergency_contact').strip()
                if request.POST.get('emergency_relation'):
                    target_additional.emergency_relation = request.POST.get('emergency_relation').strip()
                if request.POST.get('emergency_phone'):
                    target_additional.emergency_phone = request.POST.get('emergency_phone').strip()
                if request.POST.get('current_address'):
                    target_additional.current_address = request.POST.get('current_address').strip()
                if request.POST.get('permanent_address'):
                    target_additional.permanent_address = request.POST.get('permanent_address').strip()
                
                target_additional.save()
                
                return JsonResponse({
                    "success": True,
                    "message": "Profile updated successfully!",
                    "reload": True
                })
            
            # ─── HR BASIC DETAILS (Admin/HR only) ───
            elif section == 'basic_hr':
                # Check permission
                if not (user_has_permission(user, "user_update") or user_has_permission(user, "team_manage")):
                    return JsonResponse({"success": False, "error": "Access denied."}, status=403)
                
                if request.POST.get('first_name'):
                    target_user.first_name = request.POST.get('first_name').strip()
                if request.POST.get('last_name'):
                    target_user.last_name = request.POST.get('last_name').strip()
                if request.POST.get('email'):
                    new_email = request.POST.get('email').strip()
                    if new_email != target_user.email:
                        if User.objects.filter(email=new_email).exclude(pk=target_user.pk).exists():
                            return JsonResponse({"success": False, "error": "Email already in use."}, status=400)
                        target_user.email = new_email
                if request.POST.get('designation'):
                    target_user.designation = request.POST.get('designation').strip()
                if request.POST.get('phone'):
                    target_user.phone = request.POST.get('phone').strip()
                    target_additional.phone = request.POST.get('phone').strip()
                if 'date_of_joining' in request.POST:
                    date_of_joining_raw = (request.POST.get('date_of_joining') or '').strip()
                    if date_of_joining_raw:
                        try:
                            target_user.date_of_joining = datetime.strptime(date_of_joining_raw, '%Y-%m-%d').date()
                        except ValueError:
                            return JsonResponse({"success": False, "error": "Invalid date of joining format."}, status=400)
                    else:
                        target_user.date_of_joining = None
                
                # Update department
                dept_id = request.POST.get('department')
                if dept_id:
                    try:
                        target_user.department = Department.objects.get(pk=dept_id)
                    except Department.DoesNotExist:
                        pass
                
                target_user.save()
                target_additional.save()
                
                return JsonResponse({
                    "success": True,
                    "message": "HR details updated successfully!",
                    "reload": True
                })
            
            # ─── SALARY ───
            elif section == 'salary':
                if not user_has_permission(user, "salary_update"):
                    return JsonResponse({"success": False, "error": "Permission denied for salary updates."}, status=403)
                if request.POST.get('basic'):
                    target_salary.basic_salary = float(request.POST.get('basic') or 0)
                if request.POST.get('hra'):
                    target_salary.hra = float(request.POST.get('hra') or 0)
                if request.POST.get('other_allowances'):
                    target_salary.bonus = float(request.POST.get('other_allowances') or 0)
                if request.POST.get('salary_in_hand'):
                    target_salary.salary_in_hand = float(request.POST.get('salary_in_hand') or 0)
                target_salary.save()
                
                return JsonResponse({
                    "success": True,
                    "message": "Salary details updated successfully!",
                    "reload": True
                })
            
            # ─── BANK ───
            elif section == 'bank':
                if not user_has_permission(user, "bank_update"):
                    return JsonResponse({"success": False, "error": "Permission denied for bank updates."}, status=403)
                if request.POST.get('account_number'):
                    target_bank.account_number = request.POST.get('account_number').strip()
                if request.POST.get('ifsc'):
                    target_bank.ifsc_code = request.POST.get('ifsc').strip().upper()
                if request.POST.get('bank_name'):
                    target_bank.bank_name = request.POST.get('bank_name').strip()
                target_bank.save()
                
                return JsonResponse({
                    "success": True,
                    "message": "Bank details updated successfully!",
                    "reload": True
                })
            
            # ─── VERIFICATION ───
            elif section == 'verification':
                if not user_has_permission(user, "verification_update"):
                    return JsonResponse({"success": False, "error": "Permission denied for verification updates."}, status=403)
                if request.POST.get('aadhaar'):
                    target_verification.aadhar_number = request.POST.get('aadhaar').strip()
                if request.POST.get('pan'):
                    target_verification.pan_number = request.POST.get('pan').strip().upper()
                target_verification.save()
                
                return JsonResponse({
                    "success": True,
                    "message": "Verification details updated successfully!",
                    "reload": True
                })
            
            # ─── ADDITIONAL ───
            elif section == 'additional':
                if request.POST.get('blood_group'):
                    target_additional.blood_group = request.POST.get('blood_group')
                if request.POST.get('notes'):
                    target_additional.notes = request.POST.get('notes').strip()
                target_additional.save()
                
                return JsonResponse({
                    "success": True,
                    "message": "Additional details updated successfully!",
                    "reload": True
                })
            
            else:
                return JsonResponse({"success": False, "error": f"Unknown section: {section}"}, status=400)
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({"success": False, "error": str(e)}, status=400)

# ══════════════════════════════════════════════════════════════
#  PASSWORD RESET
# ══════════════════════════════════════════════════════════════

@csrf_exempt
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


@method_decorator(csrf_exempt, name='dispatch')
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
        return redirect("dashboard")
    return render(request, "home.html")


# ══════════════════════════════════════════════════════════════
#  DEPARTMENT VIEWS  (unchanged from original)
# ══════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════

def _is_admin(request):
    return (
        request.user.is_superuser
        or user_has_permission(request.user, "dashboard_admin")
        or user_has_permission(request.user, "settings_update")
        or user_has_permission(request.user, "role_assign_permissions")
    )


def _is_hr_or_admin(request):
    return (
        request.user.is_superuser
        or user_has_permission(request.user, "dashboard_admin")
        or user_has_permission(request.user, "dashboard_hr")
        or user_has_permission(request.user, "user_view")
        or user_has_permission(request.user, "leave_view_all")
        or user_has_permission(request.user, "leave_approve")
        or user_has_permission(request.user, "team_manage")
    )


def _wants_json_response(request):
    sec_fetch_mode = request.headers.get("Sec-Fetch-Mode", "")
    sec_fetch_dest = request.headers.get("Sec-Fetch-Dest", "")

    return (
        request.path.startswith("/api/")
        or request.GET.get("json") in {"1", "true", "True"}
        or request.GET.get("format") == "json"
        or "application/json" in request.headers.get("Accept", "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or (sec_fetch_mode and sec_fetch_mode != "navigate" and sec_fetch_dest == "empty")
    )


def _serialize_department(dept, employee_count=None):
    hr_name = None
    if dept.hr:
        hr_name = dept.hr.get_full_name() or dept.hr.email

    return {
        "id": dept.pk,
        "name": dept.name,
        "hr": {
            "id": dept.hr.pk,
            "name": hr_name,
            "email": dept.hr.email,
        } if dept.hr else None,
        "employee_count": employee_count if employee_count is not None else getattr(dept, "emp_count", 0),
        "detail_url": reverse('department_detail', args=[dept.pk]),
        "edit_url": reverse('department_edit', args=[dept.pk]),
        "delete_url": reverse('department_delete', args=[dept.pk]),
    }


def _serialize_department_employee(user):
    full_name = user.get_full_name() or user.username or user.email
    return {
        "id": user.pk,
        "full_name": full_name,
        "email": user.email,
        "role": user.role.name if user.role else None,
        "phone": user.phone,
        "date_of_joining": user.date_of_joining.isoformat() if user.date_of_joining else None,
    }


def _build_role_permission_rows(role):
    ensure_permission_catalog()

    perm_map = {
        permission.module: permission
        for permission in RBACPermission.objects.filter(is_active=True)
    }

    assignment_map = {
        assignment.permission.codename: assignment.is_enabled
        for assignment in RolePermissionAssignment.objects.filter(role=role, permission__is_active=True)
        .select_related('permission')
    }

    rows = []
    for mod_key, mod_label, mod_icon in MODULES:
        flag_map = LEGACY_MATRIX_ACTIONS.get(mod_key, {})
        perm = SimpleNamespace(
            can_view=any(assignment_map.get(code, False) for code in flag_map.get('can_view', [])),
            can_create=any(assignment_map.get(code, False) for code in flag_map.get('can_create', [])),
            can_edit=any(assignment_map.get(code, False) for code in flag_map.get('can_edit', [])),
            can_delete=any(assignment_map.get(code, False) for code in flag_map.get('can_delete', [])),
        )
        rows.append({'key': mod_key, 'label': mod_label, 'icon': mod_icon, 'perm': perm, 'definition': perm_map.get(mod_key)})

    return rows


def _permission_group_meta(module_key):
    module_map = {
        key: {"label": label, "icon": icon}
        for key, label, icon in MODULES
    }
    module_map.update({
        "dashboard": {"label": "Dashboard", "icon": "fa-gauge-high"},
        "user": {"label": "Users", "icon": "fa-users"},
        "system": {"label": "System", "icon": "fa-gears"},
        "leave": {"label": "Leave", "icon": "fa-calendar-days"},
        "holiday": {"label": "Holiday", "icon": "fa-umbrella-beach"},
        "report": {"label": "Reports", "icon": "fa-chart-bar"},
        "team": {"label": "Team", "icon": "fa-people-group"},
    })
    return module_map.get(
        module_key,
        {
            "label": module_key.replace("_", " ").title(),
            "icon": "fa-shield-halved",
        },
    )


def _build_role_permission_groups(role):
    ensure_permission_catalog()

    assignment_map = {
        assignment.permission.codename: assignment.is_enabled
        for assignment in RolePermissionAssignment.objects.filter(
            role=role,
            permission__is_active=True,
        ).select_related("permission")
    }

    grouped = {}
    for permission in RBACPermission.objects.filter(is_active=True).order_by("module", "name", "codename"):
        module_key = permission.module or "general"
        if module_key not in grouped:
            meta = _permission_group_meta(module_key)
            grouped[module_key] = {
                "key": module_key,
                "label": meta["label"],
                "icon": meta["icon"],
                "permissions": [],
            }

        grouped[module_key]["permissions"].append({
            "id": permission.id,
            "codename": permission.codename,
            "name": permission.name,
            "action": permission.action,
            "description": permission.description,
            "enabled": bool(assignment_map.get(permission.codename, False)),
        })

    ordered_group_keys = []
    for module_key, _, _ in MODULES:
        if module_key in grouped and module_key not in ordered_group_keys:
            ordered_group_keys.append(module_key)
    for module_key in ["user", "system", "leave", "holiday", "report", "team"]:
        if module_key in grouped and module_key not in ordered_group_keys:
            ordered_group_keys.append(module_key)
    for module_key in grouped.keys():
        if module_key not in ordered_group_keys:
            ordered_group_keys.append(module_key)

    return [grouped[module_key] for module_key in ordered_group_keys]


def _serialize_assign_role_user(user):
    full_name = user.get_full_name() or user.username or user.email
    return {
        "id": user.pk,
        "full_name": full_name,
        "email": user.email,
        "is_senior": user.is_senior,
        "department": {
            "id": user.department.pk,
            "name": user.department.name,
        } if user.department else None,
        "role": {
            "id": user.role.pk,
            "name": user.role.name,
        } if user.role else None,
        "reporting_manager": {
            "id": user.reporting_manager.pk,
            "name": user.reporting_manager.get_full_name() or user.reporting_manager.username or user.reporting_manager.email,
        } if user.reporting_manager else None,
    }


def _get_assign_role_context(request):
    roles = Role.objects.all().order_by('name')
    departments = Department.objects.all().order_by('name')
    tl_users = User.objects.filter(role__name='TL').order_by('first_name')

    dept_filter = request.GET.get('dept', '').strip()
    search = request.GET.get('q', '').strip()
    filter_role = request.GET.get('role', '').strip()

    users = User.objects.select_related('role', 'department', 'reporting_manager').order_by('first_name', 'last_name')

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

    return {
        'users': users,
        'roles': roles,
        'departments': departments,
        'tl_users': tl_users,
        'dept_id': dept_filter,
        'filter_role': filter_role,
        'search': search,
        'total_users': users.count(),
    }
                
    return redirect('department_list')


@login_required
def department_edit(request, pk):
    if not _is_admin(request):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')

    dept = get_object_or_404(Department, pk=pk)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        hr_id = request.POST.get('hr', '').strip()
        
        if not name:
            messages.error(request, "Department name is required.")
        elif Department.objects.filter(name__iexact=name).exclude(pk=pk).exists():
            messages.error(request, f'Another department named "{name}" already exists.')
        else:
            dept.name = name
            dept.hr = User.objects.get(pk=hr_id) if hr_id else None
            dept.save()
            messages.success(request, f'Department "{name}" updated successfully!')
            
            is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            if is_ajax:
                return JsonResponse({
                    "success": True, 
                    "message": f'Department "{name}" updated successfully.',
                    "redirect_url": reverse('department_list')
                })
                
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
        
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if is_ajax:
            return JsonResponse({
                "success": True, 
                "message": f'Department "{name}" deleted.',
                "redirect_url": reverse('department_list')
            })
            
    return redirect('department_list')


@login_required
def department_create(request):
    """Create a new department"""
    if not _is_admin(request):
        if _wants_json_response(request):
            return JsonResponse({"success": False, "error": "Access denied. Admins only."}, status=403)
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        hr_id = request.POST.get('hr', '').strip()
        
        if not name:
            if _wants_json_response(request):
                return JsonResponse({"success": False, "error": "Department name is required."}, status=400)
            messages.error(request, "Department name is required.")
        elif Department.objects.filter(name__iexact=name).exists():
            if _wants_json_response(request):
                return JsonResponse({"success": False, "error": f'Department "{name}" already exists.'}, status=400)
            messages.error(request, f'Department "{name}" already exists.')
        else:
            # Create the new department
            department = Department(name=name)
            
            # Assign HR if provided
            if hr_id:
                try:
                    department.hr = User.objects.get(pk=hr_id)
                except User.DoesNotExist:
                    pass
            
            department.save()
            
            # ✅ FIX: For AJAX requests, return JSON with redirect URL
            if _wants_json_response(request):
                return JsonResponse({
                    "success": True,
                    "message": f'Department "{name}" created successfully!',
                    "redirect_url": reverse('department_list'),
                    "department": {
                        "id": department.id,
                        "name": department.name,
                        "hr": department.hr.email if department.hr else None
                    }
                })
            
            messages.success(request, f'Department "{name}" created successfully!')
            return redirect('department_list')
    
    # GET request - show the create form
    users = User.objects.select_related('role').filter(role__name='HR').order_by('first_name')
    
    if _wants_json_response(request):
        return JsonResponse({
            "success": True,
            "hr_users": [
                {
                    "id": user.pk,
                    "name": user.get_full_name() or user.email,
                    "email": user.email
                }
                for user in users
            ]
        })
    
    return render(request, 'department_form.html', {
        'title': 'Create Department',
        'hr_users': users
    })

@login_required
def department_detail(request, pk):
    if not _is_admin(request):
        if _wants_json_response(request):
            return JsonResponse({"success": False, "error": "Access denied. Admins only."}, status=403)
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')

    dept      = get_object_or_404(Department, pk=pk)
    employees = User.objects.filter(department=dept).select_related('role').order_by('first_name')
    emp_count = employees.count()

    if _wants_json_response(request):
        return JsonResponse({
            "success": True,
            "department": {
                **_serialize_department(dept, employee_count=emp_count),
            },
            "employees": [_serialize_department_employee(emp) for emp in employees],
        })

    return render(request, 'department_detail.html', {
        'dept':      dept,
        'employees': employees,
        'emp_count': emp_count,
    })


# ══════════════════════════════════════════════════════════════
#  ROLE & PERMISSION VIEWS  (unchanged from original)
# ══════════════════════════════════════════════════════════════

MODULES = [
    ('dashboard',     'Dashboard',         'fa-gauge-high'),
    ('leaves',        'Leave Management',  'fa-calendar-days'),
    ('leave_type',    'Leave Type',        'fa-receipt'),
    ('leave_policy',  'Leave Policy/Balance', 'fa-sliders'),
    ('settings', 'Settings', 'fa-cog'),
    ('holiday',       'Holidays',          'fa-calendar-check'),
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
    ('HR',       'leave_type'): dict(can_view=True,  can_create=True,  can_edit=True,  can_delete=False),
    ('HR',       'leave_policy'): dict(can_view=True,  can_create=False, can_edit=False, can_delete=False),
    ('HR',       'settings'): dict(can_view=True,  can_create=False, can_edit=False, can_delete=False),
    ('HR',       'holiday'): dict(can_view=True,  can_create=True,  can_edit=True,  can_delete=True),
    ('HR',       'departments'):  dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('TL',       'leave_type'): dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('TL',       'leave_policy'): dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('TL',       'settings'): dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('TL',       'holiday'): dict(can_view=True,  can_create=False, can_edit=False, can_delete=False),
    ('TL',       'departments'):  dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('TL',       'salary'):       dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Employee', 'leave_type'): dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Employee', 'leave_policy'): dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Employee', 'settings'): dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Employee', 'holiday'): dict(can_view=True,  can_create=False, can_edit=False, can_delete=False),
    ('Employee', 'departments'):  dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Employee', 'salary'):       dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Employee', 'bank'):         dict(can_view=True,  can_create=False, can_edit=True,  can_delete=False),
    ('Employee', 'employees'):    dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Manager',  'leave_type'): dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Manager',  'leave_policy'): dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Manager',  'settings'): dict(can_view=False, can_create=False, can_edit=False, can_delete=False),
    ('Manager',  'holiday'): dict(can_view=True,  can_create=False, can_edit=False, can_delete=False),
    ('Manager',  'departments'):  dict(can_view=True,  can_create=False, can_edit=True,  can_delete=False),
}


@login_required
def role_list(request):
    if not (request.user.is_superuser or user_has_permission(request.user, "role_view") or _is_admin(request)):
        if _wants_json_response(request):
            return JsonResponse({"success": False, "error": "Access denied. Admins only."}, status=403)
        messages.error(request, "Access denied. Admins only.")
        return redirect('admin_dashboard')
    roles = Role.objects.annotate(user_count=Count('user', distinct=True)).order_by('name')
    if _wants_json_response(request):
        return JsonResponse({
            "success": True,
            "roles": [
                {
                    "id": role.id,
                    "name": role.name,
                    "user_count": role.user_count,
                    "is_protected": role.name == "Admin",
                    "permissions_url": f"{reverse('role_permission_list')}?role={role.pk}",
                    "assign_url": f"{reverse('assign_role_page')}?role={role.pk}",
                    "edit_url": reverse('role_edit', args=[role.pk]),
                    "delete_url": reverse('role_delete', args=[role.pk]),
                }
                for role in roles
            ],
            "stats": {
                "total_roles": roles.count(),
                "total_users": User.objects.count(),
                "permission_sets": RolePermissionAssignment.objects.values('role').distinct().count(),
            },
        })
    return render(request, 'role_list.html')

@csrf_exempt
@login_required
def role_create(request):
    if not (request.user.is_superuser or user_has_permission(request.user, "role_create") or _is_admin(request)):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        
        if not name:
            messages.error(request, "Role name is required.")
        else:
            # Get canonical role name
            canonical_name = Role.get_canonical_name(name)
            
            # Check if canonical role already exists
            if Role.objects.filter(name=canonical_name).exists():
                messages.error(
                    request, 
                    f'Role "{name}" is the same as "{canonical_name}" which already exists. '
                    f'Please use the existing role.'
                )
            else:
                # Create role with canonical name
                role = Role.objects.create(name=canonical_name)
                messages.success(request, f'Role "{canonical_name}" created successfully!')
                
                is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                if is_ajax:
                    return JsonResponse({
                        "success": True, 
                        "message": f'Role "{canonical_name}" created successfully.',
                        "role_id": role.id,
                        "role_name": canonical_name
                    })
                    
    return redirect('role_list')


@login_required
def role_edit(request, pk):
    if not (request.user.is_superuser or user_has_permission(request.user, "role_update") or _is_admin(request)):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')
    
    role = get_object_or_404(Role, pk=pk)
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        
        if not name:
            messages.error(request, "Role name is required.")
        else:
            canonical_name = Role.get_canonical_name(name)
            
            # Check if trying to change to a different canonical role
            if canonical_name != role.name and Role.objects.filter(name=canonical_name).exclude(pk=pk).exists():
                messages.error(
                    request, 
                    f'Cannot rename to "{name}" because "{canonical_name}" already exists.'
                )
            else:
                old_name = role.name
                role.name = canonical_name
                role.save()
                messages.success(request, f'Role "{old_name}" renamed to "{canonical_name}".')
                
                is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                if is_ajax:
                    return JsonResponse({
                        "success": True, 
                        "message": f'Role renamed to "{canonical_name}".',
                        "role_name": canonical_name
                    })
                    
    return redirect('role_list')


@login_required
def role_delete(request, pk):
    if not (request.user.is_superuser or user_has_permission(request.user, "role_delete") or _is_admin(request)):
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')
    role = get_object_or_404(Role, pk=pk)
    if request.method == 'POST':
        if role.name == 'Admin':
            messages.error(request, "The Admin role cannot be deleted.")
            return redirect('role_list')
        name = role.name
        User.objects.filter(role=role).update(role=None)
        RolePermissionAssignment.objects.filter(role=role).delete()
        role.delete()
        messages.success(request, f'Role "{name}" deleted.')
        
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if is_ajax:
            return JsonResponse({"success": True, "message": f'Role "{name}" deleted.'})
            
    return redirect('role_list')


@login_required
def role_permission_list(request):
    if not (request.user.is_superuser or user_has_permission(request.user, "role_view") or user_has_permission(request.user, "permission_view") or _is_admin(request)):
        if _wants_json_response(request):
            return JsonResponse({"success": False, "error": "Access denied. Admins only."}, status=403)
        messages.error(request, "Access denied. Admins only.")
        return redirect('admin_dashboard')

    roles        = Role.objects.all().order_by('name')
    selected_pk  = request.GET.get('role', '').strip()
    selected_role = None
    perm_rows    = []
    permission_groups = []

    if selected_pk:
        try:
            selected_role = Role.objects.get(pk=selected_pk)
            perm_rows = _build_role_permission_rows(selected_role)
            permission_groups = _build_role_permission_groups(selected_role)
        except Role.DoesNotExist:
            if _wants_json_response(request):
                return JsonResponse({"success": False, "error": "Role not found."}, status=404)
            messages.error(request, "Role not found.")

    if _wants_json_response(request):
        return JsonResponse({
            "success": True,
            "roles": [
                {
                    "id": role.id,
                    "name": role.name,
                    "selected": bool(selected_role and selected_role.pk == role.pk),
                    "permissions_url": f"{reverse('role_permission_list')}?role={role.pk}",
                }
                for role in roles
            ],
            "selected_role": {
                "id": selected_role.id,
                "name": selected_role.name,
            } if selected_role else None,
            "permissions": [
                {
                    "module": row['key'],
                    "label": row['label'],
                    "icon": row['icon'],
                    "can_view": bool(row['perm'] and row['perm'].can_view),
                    "can_create": bool(row['perm'] and row['perm'].can_create),
                    "can_edit": bool(row['perm'] and row['perm'].can_edit),
                    "can_delete": bool(row['perm'] and row['perm'].can_delete),
                }
                for row in perm_rows
            ],
            "permission_groups": permission_groups,
        })

    return render(request, 'role_permission_list.html', {
        'roles': roles, 'selected_role': selected_role,
        'perm_rows': perm_rows, 'modules': MODULES,
        'permission_groups': permission_groups,
    })


@login_required
def role_permission_save(request):
    if not (request.user.is_superuser or user_has_permission(request.user, "role_assign_permissions") or user_has_permission(request.user, "permission_assign") or _is_admin(request)):
        if _wants_json_response(request):
            return JsonResponse({"success": False, "error": "Access denied. Admins only."}, status=403)
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')

    role_id = ''
    if request.method == 'POST':
        payload = {}
        if request.content_type and "application/json" in request.content_type:
            try:
                payload = json.loads(request.body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return JsonResponse({"success": False, "error": "Invalid JSON body."}, status=400)

        role_id = (
            request.POST.get('role_id', '').strip()
            or str(payload.get("role_id", "")).strip()
        )
        if not role_id:
            if _wants_json_response(request):
                return JsonResponse({"success": False, "error": "No role specified."}, status=400)
            messages.error(request, "No role specified.")
            return redirect('role_permission_list')
        try:
            role = Role.objects.get(pk=role_id)
        except Role.DoesNotExist:
            if _wants_json_response(request):
                return JsonResponse({"success": False, "error": "Role not found."}, status=404)
            messages.error(request, "Role not found.")
            return redirect('role_permission_list')

        json_permissions = payload.get("permissions", {}) if isinstance(payload.get("permissions", {}), dict) else {}
        explicit_codes = payload.get("permission_codes", [])
        if not isinstance(explicit_codes, list):
            explicit_codes = []
        explicit_codes = {
            str(code).strip()
            for code in explicit_codes
            if str(code).strip()
        } or {
            code.strip()
            for code in request.POST.getlist("permission_codes")
            if code.strip()
        }

        if explicit_codes:
            ensure_permission_catalog()
            RolePermissionAssignment.objects.filter(role=role).delete()
            active_permissions = RBACPermission.objects.filter(is_active=True)
            for permission in active_permissions:
                RolePermissionAssignment.objects.update_or_create(
                    role=role,
                    permission=permission,
                    defaults={"is_enabled": permission.codename in explicit_codes},
                )
        else:
            sync_matrix_permissions(role, json_permissions or {
                mod_key: {
                    "can_view": f"{mod_key}_view" in request.POST,
                    "can_create": f"{mod_key}_create" in request.POST,
                    "can_edit": f"{mod_key}_edit" in request.POST,
                    "can_delete": f"{mod_key}_delete" in request.POST,
                }
                for mod_key, _, _ in MODULES
            })

        messages.success(request, f'Permissions for "{role.name}" saved successfully!')

        if _wants_json_response(request):
            return JsonResponse({
                "success": True,
                "message": f'Permissions for "{role.name}" saved successfully!',
                "role": {
                    "id": role.id,
                    "name": role.name,
                },
                "permissions": [
                    {
                        "module": row['key'],
                        "label": row['label'],
                        "can_view": bool(row['perm'] and row['perm'].can_view),
                        "can_create": bool(row['perm'] and row['perm'].can_create),
                        "can_edit": bool(row['perm'] and row['perm'].can_edit),
                        "can_delete": bool(row['perm'] and row['perm'].can_delete),
                    }
                    for row in _build_role_permission_rows(role)
                ],
                "permission_groups": _build_role_permission_groups(role),
            })
    elif _wants_json_response(request):
        return JsonResponse({"success": False, "error": "Method not allowed."}, status=405)

    from django.urls import reverse
    return redirect(reverse('role_permission_list') + f'?role={role_id}')

@csrf_exempt
@login_required
def assign_role(request):
    if not (request.user.is_superuser or user_has_permission(request.user, "user_assign_role") or user_has_permission(request.user, "user_update") or _is_hr_or_admin(request)):
        if _wants_json_response(request):
            return JsonResponse({"success": False, "error": "Access denied. Admins or HR only."}, status=403)
        messages.error(request, "Access denied. Admins or HR only.")
        return redirect('admin_dashboard')

    context = _get_assign_role_context(request)
    roles = context['roles']
    departments = context['departments']
    tl_users = context['tl_users']
    users = context['users']
    dept_filter = context['dept_id']
    search = context['search']
    filter_role = context['filter_role']

    if request.method == 'GET':
        return JsonResponse({
            "success": True,
            "filters": {
                "dept": dept_filter,
                "role": filter_role,
                "search": search,
            },
            "stats": {
                "total_users": users.count(),
            },
            "roles": [
                {"id": role.pk, "name": role.name}
                for role in roles
            ],
            "departments": [
                {"id": dept.pk, "name": dept.name}
                for dept in departments
            ],
            "tl_users": [
                {
                    "id": tl.pk,
                    "name": tl.get_full_name() or tl.username or tl.email,
                    "email": tl.email,
                }
                for tl in tl_users
            ],
            "users": [_serialize_assign_role_user(user) for user in users],
        })

    # POST: Update role and department
    if request.method == 'POST':
        payload = {}
        if request.content_type and "application/json" in request.content_type:
            try:
                payload = json.loads(request.body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return JsonResponse({"success": False, "error": "Invalid JSON body."}, status=400)

        user_id = request.POST.get('user_id', '').strip() or str(payload.get('user_id', '')).strip()
        role_id = request.POST.get('role_id', '').strip() or str(payload.get('role_id', '')).strip()
        department_id = request.POST.get('department_id', '').strip() or str(payload.get('department_id', '')).strip()
        reporting_manager_id = request.POST.get('reporting_manager_id', '').strip() or str(payload.get('reporting_manager_id', '')).strip()

        if not user_id or not role_id:
            if _wants_json_response(request):
                return JsonResponse({"success": False, "error": "User and role are required."}, status=400)
            messages.error(request, "User and role are required.")
            return redirect('assign_role_page')

        try:
            target = User.objects.select_related('role').get(pk=user_id)
            new_role = Role.objects.get(pk=role_id)

            # Prevent removing last admin
            if (
                target.role and target.role.name == 'Admin'
                and new_role.name != 'Admin'
                and User.objects.filter(role__name='Admin').count() <= 1
            ):
                if _wants_json_response(request):
                    return JsonResponse({"success": False, "error": "Cannot remove the only Admin user."}, status=400)
                messages.error(request, "Cannot remove the only Admin user.")
                return redirect('assign_role_page')

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

            # ===== NEW: Auto-assign manager for TL if not specified =====
            if new_role.name == "TL" and not reporting_manager_id:
                # Try to find a Manager in the same department
                if target.department:
                    manager = User.objects.filter(
                        department=target.department,
                        role__name='Manager'
                    ).first()
                    
                    if manager:
                        target.reporting_manager = manager
                        messages.info(
                            request,
                            f"Auto-assigned {manager.get_full_name() or manager.email} as manager for {target.get_full_name() or target.email}"
                        )
                    else:
                        # Try to find any Manager in the company
                        default_manager = User.objects.filter(role__name='Manager').first()
                        if default_manager:
                            target.reporting_manager = default_manager
                            messages.warning(
                                request,
                                f"No Manager found in {target.department.name}. Assigned default manager {default_manager.get_full_name() or default_manager.email}"
                            )
                        else:
                            messages.warning(
                                request,
                                f"No Manager found in the system. Please create a Manager user first."
                            )
                else:
                    # No department assigned
                    default_manager = User.objects.filter(role__name='Manager').first()
                    if default_manager:
                        target.reporting_manager = default_manager
                        messages.warning(
                            request,
                            f"User has no department. Assigned default manager {default_manager.get_full_name() or default_manager.email}"
                        )
                    else:
                        messages.warning(
                            request,
                            f"No Manager found in the system. Please create a Manager user first."
                        )

            # ===== Also auto-assign TL to Employee if missing =====
            if new_role.name == "Employee" and not reporting_manager_id:
                # Try to find a TL in the same department
                if target.department:
                    tl = User.objects.filter(
                        department=target.department,
                        role__name='TL'
                    ).first()
                    
                    if tl:
                        target.reporting_manager = tl
                        messages.info(
                            request,
                            f"Auto-assigned TL {tl.get_full_name() or tl.email} for {target.get_full_name() or target.email}"
                        )
                    else:
                        messages.warning(
                            request,
                            f"No TL found in {target.department.name}. Please assign manually."
                        )

            target.save()

            messages.success(
                request,
                f"{target.get_full_name() or target.email}: "
                f"{old_role} → {new_role.name} (Department Updated)"
            )

        except User.DoesNotExist:
            if _wants_json_response(request):
                return JsonResponse({"success": False, "error": "User not found."}, status=404)
            messages.error(request, "User not found.")

        except Role.DoesNotExist:
            if _wants_json_response(request):
                return JsonResponse({"success": False, "error": "Role not found."}, status=404)
            messages.error(request, "Role not found.")

        except Exception as e:
            if _wants_json_response(request):
                return JsonResponse({"success": False, "error": f"Error updating user: {str(e)}"}, status=400)
            messages.error(request, f"Error updating user: {str(e)}")

        if _wants_json_response(request):
            return JsonResponse({
                "success": True,
                "message": "Role assignment updated successfully.",
                "user": _serialize_assign_role_user(target),
                "reload": False,
            })
            
        return redirect('assign_role_page')

    return JsonResponse({"success": False, "error": "Method not allowed."}, status=405)

@login_required
def assign_role_page(request):
    if not (
        request.user.is_superuser
        or user_has_permission(request.user, "user_assign_role")
        or user_has_permission(request.user, "user_view")
        or _is_hr_or_admin(request)
    ):
        if request.headers.get('Accept') == 'application/json' or request.GET.get('format') == 'json':
            return JsonResponse({'success': False, 'error': 'Access denied'}, status=403)

        messages.error(request, "Access denied. Admins or HR only.")
        return redirect('admin_dashboard')

    context = _get_assign_role_context(request)
    
    # Get pagination parameters
    page = int(request.GET.get('page', 1))
    per_page = 10
    start = (page - 1) * per_page
    end = start + per_page
    
    users = context['users']
    total_users = len(users)
    total_pages = (total_users + per_page - 1) // per_page
    
    # Slice users for current page
    paginated_users = users[start:end]

    # ✅ FIX: Check for AJAX/JSON request FIRST
    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest' 
        or request.GET.get('format') == 'json'
        or request.headers.get('Accept') == 'application/json'
    )
    
    # ✅ Return JSON for AJAX requests
    if is_ajax:
        users_data = [
            {
                'id': u.id,
                'full_name': u.get_full_name() or u.username,
                'email': u.email,
                'is_senior': getattr(u, 'is_senior', False),
                'department': {
                    'id': u.department.id,
                    'name': u.department.name
                } if u.department else None,
                'role': {
                    'id': u.role.id,
                    'name': u.role.name
                } if u.role else None,
                'reporting_manager': {
                    'id': u.reporting_manager.id,
                    'name': u.reporting_manager.get_full_name() or u.reporting_manager.username
                } if getattr(u, 'reporting_manager', None) else None,
            }
            for u in paginated_users
        ]
        
        # Build roles data for select dropdowns
        roles_data = [
            {'id': role.id, 'name': role.name}
            for role in context['roles']
        ]
        
        departments_data = [
            {'id': dept.id, 'name': dept.name}
            for dept in context['departments']
        ]
        
        tl_users_data = [
            {
                'id': tl.id,
                'name': tl.get_full_name() or tl.username,
                'email': tl.email
            }
            for tl in context['tl_users']
        ]
        
        return JsonResponse({
            'success': True,
            'users': users_data,
            'roles': roles_data,
            'departments': departments_data,
            'tl_users': tl_users_data,
            'stats': {
                'total_users': total_users,
                'page': page,
                'per_page': per_page,
            }
        })

    # Add pagination info to context for initial render (HTML only)
    context.update({
        'paginated_users': paginated_users,
        'current_page': page,
        'total_pages': total_pages,
        'total_users': total_users,
        'start_index': start + 1,
        'end_index': min(end, total_users),
        'per_page': per_page,
    })

    return render(request, 'assign_role.html', context)

@login_required
def assign_role_bulk(request):
    if not (request.user.is_superuser or user_has_permission(request.user, "user_assign_role") or user_has_permission(request.user, "team_manage") or _is_hr_or_admin(request)):
        if _wants_json_response(request):
            return JsonResponse({"success": False, "error": "Access denied. Admins or HR only."}, status=403)
        messages.error(request, "Access denied.")
        return redirect('admin_dashboard')

    if request.method == 'POST':
        payload = {}
        if request.content_type and "application/json" in request.content_type:
            try:
                payload = json.loads(request.body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return JsonResponse({"success": False, "error": "Invalid JSON body."}, status=400)

        dept_id     = request.POST.get('department_id', '').strip() or str(payload.get('department_id', '')).strip()
        new_role_id = request.POST.get('role_id',       '').strip() or str(payload.get('role_id', '')).strip()
        if not dept_id or not new_role_id:
            if _wants_json_response(request):
                return JsonResponse({"success": False, "error": "Department and role are both required."}, status=400)
            messages.error(request, "Department and role are both required.")
            return redirect('assign_role_page')
        try:
            dept     = Department.objects.get(pk=dept_id)
            new_role = Role.objects.get(pk=new_role_id)
            count    = User.objects.filter(department=dept).exclude(role__name='Admin').update(role=new_role)
            messages.success(request, f'Assigned "{new_role.name}" to {count} employee(s) in {dept.name}.')
            if _wants_json_response(request):
                return JsonResponse({
                    "success": True,
                    "message": f'Assigned "{new_role.name}" to {count} employee(s) in {dept.name}.',
                    "department": {"id": dept.pk, "name": dept.name},
                    "role": {"id": new_role.pk, "name": new_role.name},
                    "updated_count": count,
                    "reload": False,
                })
        except (Department.DoesNotExist, Role.DoesNotExist):
            if _wants_json_response(request):
                return JsonResponse({"success": False, "error": "Invalid department or role."}, status=400)
            messages.error(request, "Invalid department or role.")

    if _wants_json_response(request):
        return JsonResponse({"success": False, "error": "Method not allowed."}, status=405)
        
    return redirect('assign_role_page')
