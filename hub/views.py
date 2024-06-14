"""Views to perform actions upon API requests."""
import datetime
import logging

from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from rest_framework import permissions, response, views, viewsets

from hub.forms import CustomUserCreationForm, JoinFastsForm, ProfileForm
from hub.models import Church, Fast, Profile
from hub import serializers
from .serializers import FastSerializer


from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from PIL import Image
from io import BytesIO
from .models import Profile
from django.conf import settings
from django.core.files.storage import default_storage



# Utilities


def _get_fast_for_user_on_date(request):
    """Returns the fast that the user is participating in on a given day."""
    user = request.user
    date_str = request.query_params.get("date")
    if date_str is None:
        # get today by default
        date = datetime.date.today()
    else:
        date = _parse_date_str(date_str)

    return _get_user_fast_on_date(user, date)


def _get_user_fast_on_date(user, date):
    """Given user, gets fast that the user is participating in on a given day."""
    # there should not be multiple fasts per day, but in case of bug, return last created
    return Fast.objects.filter(profiles__user=user, days__date=date).last()


def _get_fast_on_date(request):
    """Returns the fast for the user's church on a given day whether the user is participating in it or not."""
    date_str = request.query_params.get("date")
    if date_str is None:
        # get today by default
        date = datetime.date.today()
    else:
        date = _parse_date_str(date_str)

    # Get the church from the user's profile
    church = request.user.profile.church

    # Filter the fasts based on the church
    # there should not be multiple fasts per day, but in case of bug, return last created
    return Fast.objects.filter(church=church, days__date=date).last()


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


# Views


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
    
class FastOnDateWithoutUser(views.APIView):
    """Returns fast data on the date specified in query params (`?date=<yyyymmdd>`).
    
    If no date provided, defaults to today. If there is no fast on the given date, or the date string provided is
    invalid or malformed, the response will contain no fast information.
    """

    def get(self, request):
        fast = _get_fast_on_date(request)
        return response.Response(serializers.FastSerializer(fast).data)


@login_required
def home(request):
    """View function for home page of site."""
    view = FastOnDateWithoutUser.as_view()
    response = view(request).data
    church = request.user.profile.church
    church_name = church.name if church is not None else ""

    # Get the current fast
    current_fast_name = response.get("name", "")
    current_fast = Fast.objects.get(name=current_fast_name) if current_fast_name else None

    # user fast serializer to serialize the current_fast
    serialized_current_fast = FastSerializer(current_fast, context={'request': request}).data


    # get link to learn more if available
    fast_url = current_fast.url if current_fast is not None else None

    # Check if the user is participating in the fast
    is_participating = current_fast in request.user.profile.fasts.all() if current_fast else False

    # Query for all upcoming fasts
    upcoming_fasts = Fast.objects.filter(
        church=church,
        days__date__gte=datetime.date.today()
    ).order_by("days__date").distinct()

    serialized_fasts = FastSerializer(upcoming_fasts, many=True, context={'request': request}).data

    # calculate days until next upcoming fast
    if upcoming_fasts:
        next_fast_date = upcoming_fasts[0].days.all()[0].date
        days_until_next = (next_fast_date - datetime.date.today()).days
    else:
        days_until_next = None


    context = {
        "church": church_name,
        "fast": serialized_current_fast,
        "fast_url": fast_url,
        "user": request.user,
        "participant_count": response.get("participant_count", 1),
        "is_participating": is_participating,
        "description": response.get("description", ""),
        "countdown": response.get("countdown"),
        "upcoming_fasts": serialized_fasts,
        "days_until_next": days_until_next
    }

    return render(request, "home.html", context=context)


def register(request):
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data["username"]
            church_name = form.cleaned_data["church"]
            form.save()
            Profile.objects.get_or_create(user=User.objects.get(username=username), 
                                          church=Church.objects.get(name=church_name))

            password = form.cleaned_data["password1"]
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                
            return HttpResponseRedirect(reverse("web_home"))
        else:
            messages.error(request, 'Account creation failed')

    else:
        form = CustomUserCreationForm()

    context = {"form": form}

    return render(request, 'registration/register.html', context)


@login_required
def join_fasts(request):

    all_fasts = Fast.objects.all()

    serialized_fasts = FastSerializer(all_fasts, many=True, context={'request': request}).data

    context = {"all_fasts": serialized_fasts}

    return render(request, 'registration/join_fasts.html', context)


@login_required
def edit_profile(request):
    if request.method == 'POST':
        form = ProfileForm(request.POST, request.FILES, instance=request.user.profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your profile was successfully updated!')
            return redirect('edit_profile')
        else:
            messages.error(request, 'Please correct the error below.')
    else:
        form = ProfileForm(instance=request.user.profile)
    return render(request, 'registration/profile.html', {'form': form})


def resized_profile_image_view(request, pk, width, height):
    profile = get_object_or_404(Profile, pk=pk)
    image_file = profile.profile_image

    if settings.DEFAULT_FILE_STORAGE == 'storages.backends.s3boto3.S3Boto3Storage':
        # Open the image file from the S3 storage backend
        with default_storage.open(image_file.name, 'rb') as f:
            image = Image.open(f)
    else:
        # Open the image file from the local file system
        image = Image.open(image_file.path)

    image.thumbnail((width, height), Image.LANCZOS)

    buffer = BytesIO()
    image.save(buffer, format='JPEG')
    buffer.seek(0)

    return HttpResponse(buffer, content_type='image/jpeg')

@login_required
def add_fast_to_profile(request, fast_id):
    fast = get_object_or_404(Fast, id=fast_id)
    request.user.profile.fasts.add(fast)
    messages.success(request, f"You have joined {fast.name}.")
    return redirect(request.META.get('HTTP_REFERER', 'home'))