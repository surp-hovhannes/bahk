"""Views to perform actions upon API requests."""
import datetime
import logging

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.contrib import messages
from django.shortcuts import render
from rest_framework import permissions, response, views, viewsets

from hub.forms import CustomUserCreationForm, JoinFastsForm
from hub.models import Church, Fast, Profile
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
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]


class GroupViewSet(viewsets.ModelViewSet):
    """API endpoint that allows groups to be viewed or edited."""
    queryset = Group.objects.all()
    serializer_class = serializers.GroupSerializer
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]
    

class FastOnDate(views.APIView):
    """Returns fast data on the date specified in query params (`?date=<yyyymmdd>`) for the user.
    
    If no date provided, defaults to today. If there is no fast on the given date, or the date string provided is
    invalid or malformed, the response will contain no fast information.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        fast = _get_fast_for_user_on_date(request)
        return response.Response(serializers.FastSerializer(fast).data)


@login_required
def home(request):
    """View function for home page of site."""
    view = FastOnDate.as_view()
    response = view(request).data
    church = request.user.profile.church
    church_name = church.name if church is not None else ""

    context = {
        "church": church_name,
        "fast": response.get("name", ""),
        "user": request.user,
        "participant_count": response.get("participant_count", 1)
    }

    return render(request, "home.html", context=context)


def register(request):
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data["username"]
            church_name = form.cleaned_data["church"]
            form.save()
            profile, success = Profile.objects.get_or_create(user=User.objects.get(username=username), 
                                                             church=Church.objects.get(name=church_name))
            messages.success(request, 'Account created successfully')
    else:
        form = CustomUserCreationForm()

    context = {"form": form}

    return render(request, 'registration/register.html', context)


@login_required
def join_fasts(request):
    if request.method == 'POST':
        form = JoinFastsForm(request.POST, request=request)
        if form.is_valid():
            print("valid")
            new_fasts = set(form.cleaned_data["fasts"])
            request.user.profile.fasts.add(*new_fasts)
            print(request.user.profile.fasts)
    else:
        form = JoinFastsForm(request=request)

    context = {"form": form}

    return render(request, 'registration/join_fasts.html', context)
