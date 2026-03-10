from django import forms
from .models import User, SalaryDetails, BankDetails, VerificationDetails, AdditionalDetails

# -------------------------------
# PROFILE UPDATE FORM
# -------------------------------
class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            "first_name", "last_name", "email", "phone",
            "department", "role", "reporting_manager", "date_of_joining", "is_senior"
        ]


# -------------------------------
# SALARY FORM
# -------------------------------
class SalaryForm(forms.ModelForm):
    class Meta:
        model = SalaryDetails
        fields = ["basic_salary", "hra", "bonus"]


# -------------------------------
# BANK FORM
# -------------------------------
class BankForm(forms.ModelForm):
    class Meta:
        model = BankDetails
        fields = ["bank_name", "account_number", "ifsc_code"]


# -------------------------------
# VERIFICATION FORM
# -------------------------------
class VerificationForm(forms.ModelForm):
    class Meta:
        model = VerificationDetails
        fields = ["aadhar_number", "pan_number", "is_verified"]


# -------------------------------
# ADDITIONAL FORM
# -------------------------------
class AdditionalForm(forms.ModelForm):
    class Meta:
        model = AdditionalDetails
        fields = ["address", "emergency_contact", "notes"]