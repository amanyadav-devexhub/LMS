from datetime import date

from django.test import TestCase
from django.utils import timezone

from users.models import Department, Role, User, SalaryDetails
from .models import AcademicLeaveSettings, LeaveAllocationLedger, LeaveRequest, LeaveTypeConfig, SalaryDeduction
from .views import _available_paid_days_for_leave, _deduct_leave_balance, sync_prorated_allocations_for_employee


class ProratedLeaveAllocationTests(TestCase):
	def setUp(self):
		self.role = Role.objects.create(name="Employee")
		self.department = Department.objects.create(name="Engineering")
		self.admin_user = User.objects.create_user(
			username="admin",
			email="admin@example.com",
			password="pass12345",
			is_superuser=True,
			is_staff=True,
		)

		self.leave_type = LeaveTypeConfig.objects.create(
			name="Paid Leave",
			code="PAID",
			days_per_year=24,
			is_active=True,
			is_paid=True,
			is_accrual_based=False,
			quota_type="STANDARD",
			starting_month=1,
			created_by=self.admin_user,
		)

	def _create_employee(self, username, joining_date):
		return User.objects.create_user(
			username=username,
			email=f"{username}@example.com",
			password="pass12345",
			role=self.role,
			department=self.department,
			date_of_joining=joining_date,
		)

	def test_joining_on_first_of_month(self):
		employee = self._create_employee("emp1", date(2026, 7, 1))
		sync_prorated_allocations_for_employee(
			employee,
			reason="Pro-rated allocation generated on employee onboarding",
			as_of_date=employee.date_of_joining,
			force_recalculate=True,
		)

		allocation = employee.leave_allocations.get(leave_type=self.leave_type, year=2026)
		self.assertEqual(allocation.allocated_days, 12.0)

	def test_joining_on_fifteenth_includes_month(self):
		employee = self._create_employee("emp15", date(2026, 7, 15))
		sync_prorated_allocations_for_employee(
			employee,
			reason="Pro-rated allocation generated on employee onboarding",
			as_of_date=employee.date_of_joining,
			force_recalculate=True,
		)

		allocation = employee.leave_allocations.get(leave_type=self.leave_type, year=2026)
		self.assertEqual(allocation.allocated_days, 12.0)

	def test_joining_after_fifteenth_starts_next_month(self):
		employee = self._create_employee("emp20", date(2026, 7, 20))
		sync_prorated_allocations_for_employee(
			employee,
			reason="Pro-rated allocation generated on employee onboarding",
			as_of_date=employee.date_of_joining,
			force_recalculate=True,
		)

		allocation = employee.leave_allocations.get(leave_type=self.leave_type, year=2026)
		self.assertEqual(allocation.allocated_days, 10.0)

	def test_joining_in_december_after_fifteenth(self):
		employee = self._create_employee("empdec", date(2026, 12, 20))
		sync_prorated_allocations_for_employee(
			employee,
			reason="Pro-rated allocation generated on employee onboarding",
			as_of_date=employee.date_of_joining,
			force_recalculate=True,
		)

		allocation = employee.leave_allocations.get(leave_type=self.leave_type, year=2026)
		self.assertEqual(allocation.allocated_days, 0.0)

	def test_leap_year_joining_date(self):
		leave_type = LeaveTypeConfig.objects.create(
			name="Casual Leave",
			code="CASUAL_TEST",
			days_per_year=12,
			is_active=True,
			is_paid=True,
			is_accrual_based=False,
			quota_type="STANDARD",
			starting_month=1,
			created_by=self.admin_user,
		)
		employee = self._create_employee("empleap", date(2024, 2, 29))
		sync_prorated_allocations_for_employee(
			employee,
			reason="Pro-rated allocation generated on employee onboarding",
			as_of_date=employee.date_of_joining,
			force_recalculate=True,
		)

		allocation = employee.leave_allocations.get(leave_type=leave_type, year=2024)
		self.assertEqual(allocation.allocated_days, 10.0)

	def test_ledger_entry_created(self):
		employee = self._create_employee("empledger", date(2026, 9, 10))
		sync_prorated_allocations_for_employee(
			employee,
			reason="Pro-rated allocation generated on employee onboarding",
			as_of_date=employee.date_of_joining,
			force_recalculate=True,
		)

		ledger = LeaveAllocationLedger.objects.filter(employee=employee, leave_type=self.leave_type).first()
		self.assertIsNotNone(ledger)
		self.assertIn("Pro-rated allocation", ledger.note)
		self.assertEqual(ledger.annual_quota, 24.0)

	def test_department_scoped_leave_types_get_separate_quotas(self):
		department_b = Department.objects.create(name="Operations")
		lt_a = LeaveTypeConfig.objects.create(
			name="Dept A Leave",
			code="DEPT_A",
			days_per_year=18,
			is_active=True,
			is_paid=True,
			is_accrual_based=False,
			quota_type="STANDARD",
			starting_month=1,
			applicable_to="DEPARTMENTS",
			created_by=self.admin_user,
		)
		lt_a.applicable_departments.add(self.department)

		lt_b = LeaveTypeConfig.objects.create(
			name="Dept B Leave",
			code="DEPT_B",
			days_per_year=12,
			is_active=True,
			is_paid=True,
			is_accrual_based=False,
			quota_type="STANDARD",
			starting_month=1,
			applicable_to="DEPARTMENTS",
			created_by=self.admin_user,
		)
		lt_b.applicable_departments.add(department_b)

		emp_a = self._create_employee("deptauser", date(2026, 9, 10))
		emp_b = User.objects.create_user(
			username="deptbuser",
			email="deptbuser@example.com",
			password="pass12345",
			role=self.role,
			department=department_b,
			date_of_joining=date(2026, 9, 10),
		)

		sync_prorated_allocations_for_employee(emp_a, reason="dept policy", as_of_date=emp_a.date_of_joining)
		sync_prorated_allocations_for_employee(emp_b, reason="dept policy", as_of_date=emp_b.date_of_joining)

		alloc_a = emp_a.leave_allocations.get(leave_type=lt_a, year=2026)
		alloc_b = emp_b.leave_allocations.get(leave_type=lt_b, year=2026)

		self.assertEqual(alloc_a.allocated_days, 6.0)  # Sep 10 => 4 months of 18/12
		self.assertEqual(alloc_b.allocated_days, 4.0)  # Sep 10 => 4 months of 12/12

	def test_monthly_quota_exhaustion_creates_unpaid_and_salary_deduction(self):
		settings_obj = AcademicLeaveSettings.get_solo()
		settings_obj.leave_year_start_month = 1
		settings_obj.annual_leave_quota = 12
		settings_obj.save()

		urgent_type = LeaveTypeConfig.objects.create(
			name="Urgent Leave",
			code="URGENT",
			days_per_year=12,
			is_active=True,
			is_paid=True,
			is_accrual_based=True,
			monthly_accrual=1,
			quota_type="ANNUAL_POOL",
			starting_month=1,
			created_by=self.admin_user,
		)

		employee = self._create_employee("empunpaid", date(2026, 1, 1))
		SalaryDetails.objects.create(user=employee, salary_in_hand=30000)
		sync_prorated_allocations_for_employee(
			employee,
			reason="Pro-rated allocation generated on employee onboarding",
			as_of_date=employee.date_of_joining,
			force_recalculate=True,
		)

		leave_date = date(2026, 4, 10)
		available, _ = _available_paid_days_for_leave(employee, urgent_type, leave_date)
		self.assertEqual(available, 4.0)

		leave_obj = LeaveRequest.objects.create(
			employee=employee,
			leave_type="URGENT",
			duration="FULL",
			start_date=leave_date,
			end_date=date(2026, 4, 15),
			reason="Emergency personal work",
			status="APPROVED",
			final_status="APPROVED",
		)
		leave_obj.calculate_paid_unpaid(available)
		leave_obj.balance_deducted_at = timezone.now()
		leave_obj.save()

		self.assertEqual(leave_obj.paid_days, 4.0)
		self.assertEqual(leave_obj.unpaid_days, 2.0)

		_deduct_leave_balance(leave_obj)

		deduction = SalaryDeduction.objects.filter(leave_request=leave_obj).first()
		self.assertIsNotNone(deduction)
		self.assertEqual(deduction.unpaid_days, 2.0)
		self.assertGreaterEqual(float(deduction.deduction_amount), 0.0)
