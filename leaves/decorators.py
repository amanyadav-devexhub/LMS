# leaves/decorators.py
from django.shortcuts import redirect
from django.contrib import messages
from functools import wraps

def role_required(allowed_roles=[]):
    """
    Decorator to check if user has required role
    Usage: @role_required(['HR', 'Admin'])
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            
            if not request.user.role:
                messages.error(request, "You don't have a role assigned.")
                return redirect('dashboard')
            
            if request.user.role.name in allowed_roles:
                return view_func(request, *args, **kwargs)
            
            messages.error(request, f"You don't have permission to access this page. Required roles: {', '.join(allowed_roles)}")
            return redirect('dashboard')
        return _wrapped_view
    return decorator

def permission_required(permission_codename):
    """
    Decorator to check if user has specific permission
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            
            if request.user.has_perm(permission_codename):
                return view_func(request, *args, **kwargs)
            
            messages.error(request, "You don't have permission to perform this action.")
            return redirect('dashboard')
        return _wrapped_view
    return decorator

def hr_required(view_func):
    """Specific decorator for HR only"""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        
        if request.user.role and request.user.role.name == 'HR':
            return view_func(request, *args, **kwargs)
        
        messages.error(request, "This page is only accessible to HR personnel.")
        return redirect('dashboard')
    return _wrapped_view

def admin_required(view_func):
    """Specific decorator for Admin only"""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        
        if request.user.role and request.user.role.name == 'Admin':
            return view_func(request, *args, **kwargs)
        
        messages.error(request, "This page is only accessible to Administrators.")
        return redirect('dashboard')
    return _wrapped_view