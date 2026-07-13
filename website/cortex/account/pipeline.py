"""
Social auth pipeline — marks social login users as email-verified.
"""
from django.contrib.auth import get_user_model

User = get_user_model()


def mark_email_verified(strategy, details, user=None, *args, **kwargs):
    """Set email_verified = True for social auth users (Google/GitHub)."""
    if user and kwargs.get("is_new", False):
        user.email_verified = True
        user.save()
    return None
