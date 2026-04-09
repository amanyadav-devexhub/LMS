from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import User, Role

@receiver(post_save, sender=User)
def assign_admin_role_to_superuser(sender, instance, created, **kwargs):
    """
    Automatically assigns the 'Admin' role to any user created with is_superuser=True.
    """
    if instance.is_superuser:
        admin_role, _ = Role.objects.get_or_create(name='Admin')
        if instance.role != admin_role:
            instance.role = admin_role
            # Use update_fields to avoid triggering post_save recursively if possible, 
            # though the 'instance.role != admin_role' check should prevent it anyway.
            instance.save(update_fields=['role'])


@receiver(pre_save, sender=User)
def capture_user_policy_fields_before_save(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_joining_date = None
        instance._previous_department_id = None
        instance._previous_role_id = None
        return

    try:
        previous = User.objects.get(pk=instance.pk)
        instance._previous_joining_date = previous.date_of_joining
        instance._previous_department_id = previous.department_id
        instance._previous_role_id = previous.role_id
    except User.DoesNotExist:
        instance._previous_joining_date = None
        instance._previous_department_id = None
        instance._previous_role_id = None


@receiver(post_save, sender=User)
def sync_prorated_leave_allocations_for_user(sender, instance, created, **kwargs):
    if instance.is_superuser:
        return

    previous_joining_date = getattr(instance, "_previous_joining_date", None)
    previous_department_id = getattr(instance, "_previous_department_id", None)
    previous_role_id = getattr(instance, "_previous_role_id", None)

    relevant_change = (
        created
        or previous_joining_date != instance.date_of_joining
        or previous_department_id != instance.department_id
        or previous_role_id != instance.role_id
    )
    if not relevant_change:
        return

    try:
        from leaves.views import sync_prorated_allocations_for_employee

        sync_prorated_allocations_for_employee(
            instance,
            reason=(
                "Pro-rated allocation generated on employee onboarding"
                if created
                else "Pro-rated allocation generated after employee profile update"
            ),
            as_of_date=instance.date_of_joining,
            force_recalculate=True,
        )
    except Exception:
        # Avoid blocking user save flow if leave module is unavailable during startup/migration.
        return
