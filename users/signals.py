from django.db.models.signals import post_save
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
