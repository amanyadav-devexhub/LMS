from django.test import TestCase
from django.contrib.auth import get_user_model
from users.models import Role

User = get_user_model()

class SuperuserRoleTest(TestCase):
    def test_superuser_automatically_assigned_admin_role(self):
        """
        Test that a superuser is automatically assigned the 'Admin' role
        upon creation.
        """
        superuser = User.objects.create_superuser(
            username='admin@example.com',
            email='admin@example.com',
            password='password123'
        )
        
        # Check if 'Admin' role was created and assigned
        admin_role = Role.objects.get(name='Admin')
        self.assertEqual(superuser.role, admin_role)
        self.assertEqual(superuser.role.name, 'Admin')

    def test_normal_user_not_automatically_assigned_role(self):
        """
        Test that a normal user does not automatically get a role
        assigned by this signal (unless otherwise specified).
        """
        user = User.objects.create_user(
            username='user@example.com',
            email='user@example.com',
            password='password123'
        )
        self.assertIsNone(user.role)

class HRAssignTLTest(TestCase):
    def setUp(self):
        # Create roles
        self.hr_role = Role.objects.create(name='HR')
        self.tl_role = Role.objects.create(name='TL')
        self.emp_role = Role.objects.create(name='Employee')
        
        # Create users
        self.hr_user = User.objects.create_user(
            username='hr@example.com', email='hr@example.com', password='password123', role=self.hr_role
        )
        self.tl_user = User.objects.create_user(
            username='tl@example.com', email='tl@example.com', password='password123', role=self.tl_role
        )
        self.employee = User.objects.create_user(
            username='emp@example.com', email='emp@example.com', password='password123', role=self.emp_role
        )

    def test_hr_can_assign_tl_to_employee(self):
        """
        Test that an HR user can assign a reporting manager (TL) to an employee
        via the assign_role view.
        """
        self.client.login(email='hr@example.com', password='password123')
        
        response = self.client.post('/assign-role/', {
            'user_id': self.employee.pk,
            'role_id': self.emp_role.pk,
            'reporting_manager_id': self.tl_user.pk
        })
        
        # Check for redirect (success)
        self.assertEqual(response.status_code, 302)
        
        # Refresh employee from DB
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.reporting_manager, self.tl_user)
