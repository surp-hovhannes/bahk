"""Views to perform actions upon API requests."""
import datetime
import json
import logging

from django.contrib.auth.models import Group, User
from django.shortcuts import render
from rest_framework import permissions, response, views, viewsets

from hub.models import Fast
from hub import serializers


def _get_fast_for_user_on_date(request):
    user = request.user
    date_str = request.query_params.get("date")
    if date_str is None:
        # get today by default
        date = datetime.date.today()
    else:
        date = _parse_date_str(date_str)

    return _get_user_fast_on_date(user, date)


def _get_user_fast_on_date(user, date):
    # TODO: add a check that there is only one fast?
    return Fast.objects.filter(profiles__user=user, days__date=date).first()


def _parse_date_str(date_str):
    """Parses a date string in the format yyyymmdd into a date object.
    
    Args:
        date_str (str): string to parse into a date. Expects the format yyyymmdd (e.g., 20240331 for March 31, 2024).

    Returns:
        datetime.date: date object corresponding to the date string. None if the date is invalid.
    """
    try:
        date = datetime.datetime.strptime(date_str, "%Y%m%d").date()
    except ValueError as e:
        logging.error("Date string %s did not follow the expected format yyyymmdd. Returning None.", date_str)
        return None

    return date


class UserViewSet(viewsets.ModelViewSet):
    """API endpoint that allows user to be viewed or edited."""
    queryset = User.objects.all().order_by("-date_joined")
    serializer_class = serializers.UserSerializer
    permission_classes = [permissions.IsAuthenticated]


class GroupViewSet(viewsets.ModelViewSet):
    """API endpoint that allows groups to be viewed or edited."""
    queryset = Group.objects.all()
    serializer_class = serializers.GroupSerializer
    permission_classes = [permissions.IsAuthenticated]
    

class FastOnDate(views.APIView):
    """Returns fast data on the date specified in query params (`?date=<yyyymmdd>`) for the user.
    
    If no date provided, defaults to today. If there is no fast on the given date, or the date string provided is
    invalid or malformed, the response will contain no fast information.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        fast = _get_fast_for_user_on_date(request)
        return response.Response(serializers.FastSerializer(fast).data)


def home(request):
    """View function for home page of site."""
    view = FastOnDate.as_view()
    response = view(request).data

    context = {
        "church": request.user.profile.church.name,
        "fast": response["name"],
        "user": request.user,
    }

    return render(request, "home.html", context=context)