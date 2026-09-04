"""Backend for authentication."""
import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import ObjectDoesNotExist

from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer


class EmailBackend(ModelBackend):
    """Solution to log in with email instead of username.
    
    Adapted from:
    https://stackoverflow.com/questions/37332190/django-login-with-email
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        """A bit of a hack: the kwarg is username, but we expect it to be the email."""
        email = username
        UserModel = get_user_model()
        # the "username" field can now also be treated as an email
        possible_user = UserModel.objects.filter(email=email)
        if not possible_user.exists():
            return None
        if possible_user.count() > 1:
            logging.error("Multiple users found with the email %s. Fix database before proceeding.", email)
            return None

        user = possible_user.first()
        if not user.check_password(password):
            return None
        
        return user


class SafeTokenRefreshSerializer(TokenRefreshSerializer):
    """Token refresh that returns 401 instead of 500 when the account was deleted.

    SimpleJWT 5.5's refresh flow loads the user with a bare ``.get()`` (its
    ``USER_AUTHENTICATION_RULE`` check), so a refresh token belonging to a
    deleted account raises ``DoesNotExist`` and surfaces as a 500. Convert it
    to a ``TokenError`` so the client gets the standard 401 and logs out.
    """

    def validate(self, attrs):
        try:
            return super().validate(attrs)
        except ObjectDoesNotExist:
            # TokenViewMixin turns TokenError into InvalidToken (401). It reads
            # e.args[0], so the detail argument is required.
            raise TokenError("User account no longer exists")
