"""Views to perform actions upon API requests."""
import datetime

from django.contrib.auth.models import Group, User
from django.contrib.auth.decorators import permission_required
from rest_framework import permissions, response, views, viewsets

from hub.models import Fast
from hub.serializers import GroupSerializer, UserSerializer

import json



def _get_user_fast_on_date(user, date):
    return Fast.objects.filter(profiles__user=user, days__date=date).first()


def _parse_date_str(yyyymmdd):
    return datetime.date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:]))


class UserViewSet(viewsets.ModelViewSet):
    """API endpoint that allows user to be viewed or edited."""
    queryset = User.objects.all().order_by("-date_joined")
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]


class GroupViewSet(viewsets.ModelViewSet):
    """API endpoint that allows groups to be viewed or edited."""
    queryset = Group.objects.all()
    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAuthenticated]


class TodaysFast(views.APIView):
    """Returns fast for today for given user."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, format=None):
        user = request.user
        today = datetime.date.today()
        fast = _get_user_fast_on_date(user, today)
        # TODO: add a check that there is only one fast?
        # TODO: user serializer instead of casting as string

        return response.Response(str(fast))
    

class FastOnDate(views.APIView):
    """Returns fast for today for given user."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, yyyymmdd, format=None):
        user = request.user
        date = _parse_date_str(yyyymmdd)
        fast = _get_user_fast_on_date(user, date)
        # TODO: add a check that there is only one fast?
        # TODO: user serializer instead of casting as string

        return response.Response(str(fast))


class TodaysParticipantCount(views.APIView):
    """Returns fast for today for given user."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, format=None):
        user = request.user
        today = datetime.date.today()
        fast = _get_user_fast_on_date(user, today)
        ct = 0
        if fast is not None:
            ct = fast.profiles.all().count()

        return response.Response(json.dumps({"fast": str(fast), "ct": str(ct)}))
    

class ParticipantCountOnDate(views.APIView):
    """Returns fast for today for given user."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, yyyymmdd, format=None):
        user = request.user
        date = _parse_date_str(yyyymmdd)
        fast = _get_user_fast_on_date(user, date)
        ct = 0
        if fast is not None:
            ct = fast.profiles.all().count()

        return response.Response(json.dumps({"fast": str(fast), "ct": str(ct)}))
