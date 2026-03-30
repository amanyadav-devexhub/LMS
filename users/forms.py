from django import forms
from .models import User, SalaryDetails, BankDetails, VerificationDetails, AdditionalDetails


# ─────────────────────────────────────────
#  EMPLOYEE — Basic Details form
#  (what an employee can edit themselves)
# ─────────────────────────────────────────
class EmployeeBasicForm(forms.Form):
    """
    Handles the 'basic_employee' section.
    Fields split across User model + AdditionalDetails model.
    """
    # User fields
    first_name = forms.CharField(max_length=150, required=False)
    last_name  = forms.CharField(max_length=150, required=False)
    email      = forms.EmailField(required=False)

    # AdditionalDetails fields
    personal_email    = forms.EmailField(required=False)
    alternate_phone   = forms.CharField(max_length=15, required=False)
    date_of_birth     = forms.DateField(required=False, input_formats=['%Y-%m-%d'])
    gender            = forms.ChoiceField(
        choices=[('', ''), ('Male', 'Male'), ('Female', 'Female'), ('Other', 'Other')],
        required=False
    )
    marital_status    = forms.ChoiceField(
        choices=[('', ''), ('Single', 'Single'), ('Married', 'Married')],
        required=False
    )
    emergency_contact  = forms.CharField(max_length=100, required=False)
    emergency_relation = forms.CharField(max_length=50,  required=False)
    emergency_phone    = forms.CharField(max_length=15,  required=False)
    current_address    = forms.CharField(required=False, widget=forms.Textarea)
    permanent_address  = forms.CharField(required=False, widget=forms.Textarea)


# ─────────────────────────────────────────
#  HR/Admin — Basic Details form
#  (HR can edit extra fields like employee_id, department, designation, DOJ)
# ─────────────────────────────────────────
class HRBasicForm(forms.Form):
    first_name    = forms.CharField(max_length=150, required=False)
    last_name     = forms.CharField(max_length=150, required=False)
    email         = forms.EmailField(required=False)
    phone           = forms.CharField(max_length=15,  required=False)
    designation     = forms.CharField(max_length=150, required=False)
    date_of_joining = forms.DateField(required=False, input_formats=['%Y-%m-%d'])


# ─────────────────────────────────────────
#  SALARY FORM  (HR/Admin only)
# ─────────────────────────────────────────
class SalaryForm(forms.ModelForm):
    class Meta:
        model  = SalaryDetails
        fields = ['basic_salary', 'hra', 'bonus', 'salary_in_hand']


# ─────────────────────────────────────────
#  BANK FORM  (HR/Admin only)
# ─────────────────────────────────────────
class BankForm(forms.ModelForm):
    class Meta:
        model  = BankDetails
        fields = ['bank_name', 'account_number', 'ifsc_code']


# ─────────────────────────────────────────
#  VERIFICATION FORM  (HR/Admin only)
# ─────────────────────────────────────────
class VerificationForm(forms.ModelForm):
    class Meta:
        model  = VerificationDetails
        fields = ['aadhar_number', 'pan_number', 'is_verified']


# ─────────────────────────────────────────
#  ADDITIONAL FORM  (HR/Admin only)
# ─────────────────────────────────────────
class AdditionalForm(forms.ModelForm):
    class Meta:
        model  = AdditionalDetails
        fields = ['blood_group', 'notes']


# ─────────────────────────────────────────
#  PROFILE UPDATE FORM  (used by REST API)
# ─────────────────────────────────────────
class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model  = User
        fields = [
            'first_name', 'last_name', 'email', 'phone',
            'department', 'designation', 'role', 'reporting_manager',
            'date_of_joining', 'is_senior',
        ]