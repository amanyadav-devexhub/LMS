from rest_framework import serializers
from .models import LeaveRequest
from django.contrib.auth import get_user_model

User = get_user_model()

class HREmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    email = serializers.EmailField()
    on_leave = serializers.BooleanField()