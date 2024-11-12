"""Backend for authentication."""
import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrUsernameBackend(ModelBackend):
    """Solution to log in with email instead of username.
    
    Adapted from:
    https://stackoverflow.com/questions/37332190/django-login-with-email
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        # the "username" field can now also be treated as an email
        possible_user = UserModel.objects.filter(Q(email=username) | Q(username=username))
        if not possible_user.exists():
            return None
        if possible_user.count() > 1:
            logging.error("Multiple users found with the email/username %s. Fix database before proceeding.", username)
            return None

        user = possible_user.first()
        if not user.check_password(password):
            return None
        
        return user
