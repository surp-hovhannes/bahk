"""Views for web app."""
import datetime  # Standard library import

from django.contrib.auth import authenticate, login  # Django auth imports
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.db.models import Min  # Django ORM imports

from hub.forms import CustomUserCreationForm, ProfileForm
from hub.models import Church, Fast, Profile
from hub.views.fast import FastOnDateWithoutUser
from hub.serializers import FastSerializer


from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from PIL import Image
from io import BytesIO
from ..constants import NUMBER_PARTICIPANTS_TO_SHOW_WEB
from ..models import Profile
from django.conf import settings
from django.core.files.storage import default_storage

from app_management.models import Changelog


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
    serialized_current_fast = FastSerializer(current_fast, context={'request': request}).data if current_fast else None


    # get link to learn more if available
    fast_url = current_fast.url if current_fast is not None else None

    # Check if the user is participating in the fast
    is_participating = current_fast in request.user.profile.fasts.all() if current_fast else False

    # Query up to 6, for other participants for avatar display
    other_participants = current_fast.profiles.all()[:NUMBER_PARTICIPANTS_TO_SHOW_WEB] if current_fast else None

    # Query for all upcoming fasts and order by first day of the fast from today onward
    upcoming_fasts = Fast.objects.filter(
        church=church,
        days__date__gte=datetime.date.today()
    ).annotate(first_day=Min('days__date')).order_by("first_day")[:3]

    serialized_upcoming_fasts = FastSerializer(upcoming_fasts, many=True, context={'request': request}).data

    # calculate days until next upcoming fast
    if upcoming_fasts:
        next_fast = upcoming_fasts.first()
        next_fast_date = next_fast.days.filter(date__gte=datetime.date.today()).first().date
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
        "other_participants": other_participants,
        "countdown": response.get("countdown"),
        "upcoming_fasts": serialized_upcoming_fasts,
        "days_until_next": days_until_next,
        "has_passed": response.get("has_passed", False)
    }

    # if user doesn't have a profile image or location create a message to prompt them to update their profile
    if not request.user.profile.profile_image or not request.user.profile.location:
        profile_url = reverse('edit_profile')
        message = f'Please <a href="{profile_url}">update your profile</a> with a profile image and location.'
        messages.warning(request, mark_safe(message))

    return render(request, "home.html", context=context)


def register(request):
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            church_name = form.cleaned_data["church"]
            form.save()
            Profile.objects.get_or_create(user=User.objects.get(email=email), 
                                          church=Church.objects.get(name=church_name))

            password = form.cleaned_data["password1"]
            user = authenticate(request, username=email, password=password)
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
    all_fasts = Fast.objects.annotate(
        start_date=Min('days__date')
    ).filter(
        days__isnull=False
    ).order_by('start_date')
    
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


@login_required
def remove_fast_from_profile(request, fast_id):
    fast = get_object_or_404(Fast, id=fast_id)
    request.user.profile.fasts.remove(fast)
    messages.success(request, f"You have left {fast.name}.")
    return redirect(request.META.get('HTTP_REFERER', 'home'))


@login_required
def test_email_view(request):
    # Create some test data
    user = User.objects.first()
    fast = Fast.objects.first()
    serialized_current_fast = FastSerializer(fast, context={'request': request}).data if fast else None

    context = {
        'user': user,
        'fast': serialized_current_fast
    }
    
    return render(request, 'email/upcoming_fasts_reminder.html', context)


def changelog(request):

    # get all changelogs sorted by version
    changelogs = Changelog.objects.all().order_by('-version')

    context = {
        "changelogs": changelogs
    }
    return render(request, 'changelog.html', context)
