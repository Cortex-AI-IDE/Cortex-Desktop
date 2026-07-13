"""
Cortex Account — Authentication Decorators

Custom login_required that redirects to /account/login/ instead of /accounts/login/.
"""
from functools import wraps

from django.conf import settings
from django.shortcuts import redirect


def login_required(view_func):
    """
    Decorator that checks if user is authenticated.
    Redirects to login page if not.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        return view_func(request, *args, **kwargs)

    return _wrapped
