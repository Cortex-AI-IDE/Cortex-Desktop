"""
Cortex Account — Forms

Auth forms (login, signup) + profile editing.
"""
from zoneinfo import available_timezones

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

User = get_user_model()


# Timezone choices — grouped by region for the dropdown
def _get_timezone_choices():
    """Build timezone choices grouped by continent for the profile dropdown."""
    choices = [("", "Select timezone...")]
    # Group timezones by region (e.g. America/, Asia/, Europe/)
    grouped = {}
    for tz_name in sorted(available_timezones()):
        parts = tz_name.split("/", 1)
        if len(parts) == 2:
            region = parts[0]
            if region not in grouped:
                grouped[region] = []
            grouped[region].append((tz_name, tz_name.replace("_", " ")))
        else:
            # Etc/ timezones and others without region prefix
            if "Other" not in grouped:
                grouped["Other"] = []
            grouped["Other"].append((tz_name, tz_name.replace("_", " ")))

    # Add grouped choices with optgroup labels
    # Django Select widget renders optgroup for tuples of tuples
    for region in sorted(grouped.keys()):
        choices.append((region, grouped[region]))
    return choices


TIMEZONE_CHOICES = _get_timezone_choices()


# =============================================================================
# Auth Forms
# =============================================================================

class LoginForm(forms.Form):
    """Email + password login form."""

    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                "class": "auth-input",
                "placeholder": "you@example.com",
                "autocomplete": "email",
                "autofocus": True,
            }
        ),
        label="Email",
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "class": "auth-input",
                "placeholder": "Password",
                "autocomplete": "current-password",
            }
        ),
        label="Password",
    )

    def clean(self):
        """Validate credentials against the database."""
        cleaned = super().clean()
        email = cleaned.get("email", "").strip().lower()
        password = cleaned.get("password", "")

        if not email or not password:
            return cleaned

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise forms.ValidationError(
                "No account found with this email address."
            )

        if not user.check_password(password):
            raise forms.ValidationError("Incorrect password.")

        if not user.is_active:
            raise forms.ValidationError("This account has been deactivated.")

        cleaned["user"] = user
        return cleaned


class SignupForm(forms.Form):
    """Email + password signup form."""

    first_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "auth-input",
                "placeholder": "First name",
                "autocomplete": "given-name",
            }
        ),
        label="First name",
    )
    last_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "auth-input",
                "placeholder": "Last name",
                "autocomplete": "family-name",
            }
        ),
        label="Last name",
    )
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                "class": "auth-input",
                "placeholder": "you@example.com",
                "autocomplete": "email",
                "autofocus": True,
            }
        ),
        label="Email",
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "class": "auth-input",
                "placeholder": "Create a password",
                "autocomplete": "new-password",
            }
        ),
        label="Password",
    )
    password_confirm = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "class": "auth-input",
                "placeholder": "Confirm password",
                "autocomplete": "new-password",
            }
        ),
        label="Confirm password",
    )
    agree_terms = forms.BooleanField(
        required=True,
        widget=forms.CheckboxInput(
            attrs={"class": "auth-checkbox"}
        ),
        label="I agree to the Terms of Service and Privacy Policy",
    )

    def clean_email(self):
        """Check email is not already registered."""
        email = self.cleaned_data.get("email", "").strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        """Validate passwords match and meet strength requirements."""
        cleaned = super().clean()
        password = cleaned.get("password", "")
        password_confirm = cleaned.get("password_confirm", "")

        if password and password_confirm and password != password_confirm:
            raise forms.ValidationError("Passwords do not match.")

        if password:
            # Validate against Django's password validators
            try:
                validate_password(password)
            except ValidationError as e:
                raise forms.ValidationError(e.messages[0])

        return cleaned

    def save(self):
        """Create the user and return them."""
        email = self.cleaned_data["email"].strip().lower()
        first_name = self.cleaned_data.get("first_name", "").strip()
        last_name = self.cleaned_data.get("last_name", "").strip()
        password = self.cleaned_data["password"]

        user = User.objects.create_user(
            username=email,  # Use email as username
            email=email,
            first_name=first_name,
            last_name=last_name,
            password=password,
        )
        return user


# =============================================================================
# Profile Forms
# =============================================================================

class ProfileForm(forms.ModelForm):
    """Form for editing user profile (name, display name, timezone)."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "display_name", "timezone"]
        widgets = {
            "first_name": forms.TextInput(
                attrs={
                    "class": "account-input",
                    "placeholder": "First name",
                }
            ),
            "last_name": forms.TextInput(
                attrs={
                    "class": "account-input",
                    "placeholder": "Last name",
                }
            ),
            "display_name": forms.TextInput(
                attrs={
                    "class": "account-input",
                    "placeholder": "Display name (optional)",
                }
            ),
            "timezone": forms.Select(
                choices=TIMEZONE_CHOICES,
                attrs={
                    "class": "account-input",
                },
            ),
        }


class AvatarUploadForm(forms.Form):
    """Form for uploading a new avatar image."""

    avatar = forms.ImageField(
        required=True,
        widget=forms.ClearableFileInput(
            attrs={
                "class": "account-input",
                "accept": "image/*",
            }
        ),
    )
