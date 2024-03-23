"""Models for bahk hub."""
from django.contrib.auth.models import User
from django.db import models


class Church(models.Model):
    """Model for a church."""
    name = models.CharField(max_length=128)


class Fast(models.Model):
    """Model for a fast."""
    name = models.CharField(max_length=128)
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="fasts")


class Profile(models.Model):
    """Model for a user profile.
    
    Based on https://simpleisbetterthancomplex.com/tutorial/2016/07/22/how-to-extend-django-user-model.html#onetoone
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    church = models.ForeignKey(Church, null=True, blank=True, on_delete=models.SET_NULL, related_name="profiles")
    fasts = models.ManyToManyField(Fast, related_name="profiles")


class Day(models.Model):
    """Model for a day in time."""
    date = models.DateField()
    fasts = models.ManyToManyField(Fast, related_name="days")
