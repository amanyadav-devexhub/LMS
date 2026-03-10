from django import forms
from .models import LeaveRequest

class LeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = [
            "leave_type",
            "duration",
            "start_date",
            "end_date",
            "session",
            "short_hours",
            "reason",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "reason": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned_data = super().clean()
        duration = cleaned_data.get("duration")
        short_hours = cleaned_data.get("short_hours")
        end_date = cleaned_data.get("end_date")
        start_date = cleaned_data.get("start_date")

        # Half day and full day require start and end date
        if duration in ["HALF", "FULL"] and not end_date:
            cleaned_data["end_date"] = start_date

        # Short leave must have hours
        if duration == "SHORT" and (short_hours is None or short_hours <= 0):
            self.add_error("short_hours", "Short leave must have valid hours.")

        return cleaned_data