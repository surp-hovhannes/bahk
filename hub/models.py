"""Models for bahk hub."""
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import constraints
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFill
from imagekit.processors import Transpose

from hub.constants import CATENA_ABBREV_FOR_BOOK


class Church(models.Model):
    """Model for a church."""
    name = models.CharField(max_length=128, unique=True)

    @classmethod
    def get_default_pk(cls):
        church, _ = cls.objects.get_or_create(
            name="Armenian Apostolic Church"
        )
        return church.pk

    def __str__(self):
        return self.name


class Fast(models.Model):
    """Model for a fast."""
    name = models.CharField(max_length=128)
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="fasts")
    description = models.TextField(null=True, blank=True)
    culmination_feast = models.CharField(max_length=128, null=True, blank=True)
    culmination_feast_date = models.DateField(null=True, blank=True,
                                              help_text="You can enter in day/month/year format, e.g., 8/15/24")
    # auto-saved to be the year of the first day of the fast
    year = models.IntegerField(
        validators=[
            MinValueValidator(2024), 
            MaxValueValidator(3000)
        ],
        null=True,
        blank=True,
    )
    image = models.ImageField(upload_to='fast_images/', null=True, blank=True)
    image_thumbnail = ImageSpecField(source='image',
                                     processors=[ResizeToFill(500, 500)],
                                     format='JPEG',
                                     options={'quality': 60})
    # 2048 chars is the maximum URL length on Google Chrome
    url = models.URLField(verbose_name="Link to learn more", null=True, blank=True, max_length=2048,
                          help_text="URL to a link to learn more--must include protocol (e.g. https://)")

    def save(self, **kwargs):
        super().save(**kwargs)  # save first to create a primary key (needed to access self.days)
        if self.days.exists():
            self.year = self.days.first().date.year
        super().save(**kwargs)

    class Meta:
        constraints = [
            constraints.UniqueConstraint(fields=["name", "church", "year"], name="unique_name_church_year"),
            constraints.UniqueConstraint(fields=["culmination_feast_date", "church"], name="unique_feast_date_church"),
        ]

    @property
    def modal_id(self):
        return f"fastModal_{self.id}"

    def __str__(self):
        s = self.name
        if self.year:
            s += f" ({self.year})"
        return s


class Profile(models.Model):
    """Model for a user profile.
    
    Based on https://simpleisbetterthancomplex.com/tutorial/2016/07/22/how-to-extend-django-user-model.html#onetoone
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    church = models.ForeignKey(Church, null=True, blank=True, on_delete=models.SET_NULL, related_name="profiles")
    fasts = models.ManyToManyField(Fast, related_name="profiles")
    location = models.CharField(max_length=100, blank=True, null=True) 
    profile_image = models.ImageField(upload_to='profile_images/originals/', null=True, blank=True)
    profile_image_thumbnail = ImageSpecField(source='profile_image',
                                             processors=[Transpose(), ResizeToFill(100, 100)],
                                             format='JPEG',
                                             options={'quality': 60})
    receive_upcoming_fast_reminders = models.BooleanField(default=True)

    def __str__(self):
        return self.user.username


class Day(models.Model):
    """Model for a day in time."""
    date = models.DateField()
    fast = models.ForeignKey(Fast, on_delete=models.CASCADE, null=True, related_name="days")
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name="days", default=Church.get_default_pk)

    def __str__(self):
        return self.date.strftime("%B-%d-%Y")


class Reading(models.Model):
    """Stores details for a Bible reading."""
    day = models.ForeignKey(Day, on_delete=models.CASCADE, related_name="readings")
    book = models.CharField(max_length=64)
    start_chapter = models.IntegerField(verbose_name="Start Chapter")
    start_verse = models.IntegerField(verbose_name="Start Verse")
    end_chapter = models.IntegerField(verbose_name="End Chapter", help_text="May be same as start chapter")
    end_verse = models.IntegerField(verbose_name="End Verse", help_text="May be same as end verse")

    class Meta:
        constraints = [
            constraints.UniqueConstraint(
                fields=["day", "book", "start_chapter", "start_verse", "end_chapter", "end_verse"], 
                name="unique_reading_per_day"
            ),
        ]


    def create_url(self):
        """Creates URL to read the reading."""
        return f"https://catenabible.com/{CATENA_ABBREV_FOR_BOOK[self.book]}/{self.start_chapter:d}"


    def __str__(self):
        s = f"{self.book}: Chapter {self.start_chapter}, "
        if self.start_chapter == self.end_chapter and self.start_verse != self.end_verse:
            s += f"Verses {self.start_verse}-{self.end_verse}"
        else:
            s += f"Verse {self.start_verse}"
            if self.start_chapter != self.end_chapter:
                s += f" - Chapter {self.end_chapter}, Verse {self.end_verse}"
        return s