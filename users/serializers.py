from rest_framework import serializers
from .models import User, Role, Department, SalaryDetails, BankDetails, VerificationDetails, AdditionalDetails


# -----------------------
# ROLE SERIALIZER
# -----------------------
class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "name"]


# -----------------------
# DEPARTMENT SERIALIZER
# -----------------------
class DepartmentSerializer(serializers.ModelSerializer):
    hr = serializers.StringRelatedField()  # show HR email or name

    class Meta:
        model = Department
        fields = ["id", "name", "hr"]


# -----------------------
# USER SERIALIZER
# -----------------------
class UserSerializer(serializers.ModelSerializer):
    role = RoleSerializer(read_only=True)
    department = DepartmentSerializer(read_only=True)
    reporting_manager = serializers.StringRelatedField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "role",
            "department",
            "reporting_manager",
            "phone",
            "date_of_joining",
            "is_senior",
        ]


# -----------------------
# CREATE USER SERIALIZER
# -----------------------
class UserCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "password",
            "role",
            "department",
            "reporting_manager",
            "phone",
            "date_of_joining",
            "is_senior",
        ]
        extra_kwargs = {"password": {"write_only": True}}

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


# -----------------------
# PROFILE SERIALIZERS
# -----------------------
class SalaryDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalaryDetails
        fields = ["basic_salary", "hra", "bonus"]


class BankDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankDetails
        fields = ["bank_name", "account_number", "ifsc_code"]


class VerificationDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationDetails
        fields = ["aadhar_number", "pan_number", "is_verified"]


class AdditionalDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdditionalDetails
        fields = ["address", "emergency_contact", "notes"]


# -----------------------
# EMPLOYEE DATA FOR HR/MANAGER API
# -----------------------
class HREmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    email = serializers.EmailField()
    on_leave = serializers.BooleanField()
    user = UserSerializer(read_only=True)