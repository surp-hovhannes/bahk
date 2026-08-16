"""Tests for the Feast model."""
from datetime import date

from django.db.utils import IntegrityError
from django.test import TestCase
from django.test.utils import tag

from hub.models import Church, Day, Feast


@tag('slow', 'integration')
class FeastModelTests(TestCase):
    """Tests for the Feast model."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)

    def test_create_feast_basic(self):
        """Test creating a basic feast."""
        feast = Feast.objects.create(
            church=self.church,
            name="Christmas",
        )

        self.assertEqual(feast.name, "Christmas")
        self.assertEqual(feast.church, self.church)
        self.assertIsNone(feast.name_hy)

    def test_feast_with_armenian_translation(self):
        """Test creating feast with Armenian translation."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = Feast.objects.create(
            church=day.church,
            name="Christmas",
        )
        feast.name_hy = "Սուրբ Ծնունդ"
        feast.save(update_fields=['i18n'])

        # Refresh and verify translation
        feast.refresh_from_db()
        self.assertEqual(feast.name, "Christmas")
        self.assertEqual(feast.name_hy, "Սուրբ Ծնունդ")

    def test_a_church_can_hold_many_distinct_commemorations(self):
        """Distinct names coexist; it is only the same name twice that the key forbids."""
        first_feast = Feast.objects.create(
            church=self.church,
            name="First Feast",
        )
        second_feast = Feast.objects.create(
            church=self.church,
            name="Second Feast",
        )

        self.assertNotEqual(first_feast.id, second_feast.id)
        self.assertEqual(Feast.objects.filter(church=self.church).count(), 2)

    def test_the_same_commemoration_can_exist_in_two_churches(self):
        """The key is (church, name), so the name is only unique within a church."""
        other_church = Church.objects.create(name="Other Church")

        feast1 = Feast.objects.create(church=self.church, name="Epiphany")
        feast2 = Feast.objects.create(church=other_church, name="Epiphany")

        self.assertNotEqual(feast1.church, feast2.church)
        self.assertEqual(Feast.objects.filter(name="Epiphany").count(), 2)

    def test_the_same_observance_twice_in_one_church_is_rejected(self):
        """The invariant the re-key exists to establish, enforced by the database."""
        Feast.objects.create(church=self.church, name="Epiphany", observance_key="epiphany")
        with self.assertRaises(IntegrityError):
            Feast.objects.create(church=self.church, name="Theophany",
                                 observance_key="epiphany")

    def test_the_same_name_under_two_observances_is_allowed(self):
        """Not interchangeable with the old name constraint, which would have refused this.

        The engine distinguishes observances English conflates: the source heads the Fast of St.
        Gregory the Illuminator's days with their ordinal in Armenian and flattens all of them to
        "Fast day" in English.
        """
        Feast.objects.create(church=self.church, name="Fast day", observance_key="fast_day")
        Feast.objects.create(church=self.church, name="Fast day",
                             observance_key="illuminator_fast_day_3")

        self.assertEqual(Feast.objects.filter(name="Fast day").count(), 2)

    def test_rows_with_no_observance_key_do_not_collide(self):
        """The constraint is partial, so unresolved rows coexist instead of clashing on NULL."""
        Feast.objects.create(church=self.church, name="One Unresolvable Name")
        Feast.objects.create(church=self.church, name="Another Unresolvable Name")

        self.assertEqual(
            Feast.objects.filter(church=self.church, observance_key__isnull=True).count(), 2)

    def test_feast_str_representation(self):
        """A feast names its church, not a date -- it no longer has one."""
        feast = Feast.objects.create(
            church=self.church,
            name="Epiphany",
        )

        self.assertEqual(str(feast), f"Epiphany ({self.church.name})")

    def test_feast_related_name(self):
        """Feasts are reached through church.feasts now, not day.feasts."""
        feast1 = Feast.objects.create(church=self.church, name="New Year")
        feast2 = Feast.objects.create(church=self.church, name="Epiphany")

        self.assertEqual(self.church.feasts.count(), 2)
        self.assertIn(feast1, self.church.feasts.all())
        self.assertIn(feast2, self.church.feasts.all())

    def test_feast_translation_field_access(self):
        """Test accessing translation fields through i18n."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = Feast.objects.create(
            church=day.church,
            name="Easter",
        )
        feast.name_hy = "Զատիկ"
        feast.save(update_fields=['i18n'])

        feast.refresh_from_db()

        # Test accessing translations
        self.assertEqual(feast.name, "Easter")
        self.assertEqual(feast.name_hy, "Զատիկ")
        # Default language should be English
        self.assertEqual(feast.name_i18n, "Easter")

    def test_feast_update_translation_only(self):
        """Test updating only the Armenian translation."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = Feast.objects.create(
            church=day.church,
            name="Pentecost",
        )

        # Add Armenian translation
        feast.name_hy = "Հոգեգալուստ"
        feast.save(update_fields=['i18n'])

        feast.refresh_from_db()
        self.assertEqual(feast.name, "Pentecost")
        self.assertEqual(feast.name_hy, "Հոգեգալուստ")

        # Update Armenian translation
        feast.name_hy = "Սուրբ Հոգեգալուստ"
        feast.save(update_fields=['i18n'])

        feast.refresh_from_db()
        self.assertEqual(feast.name, "Pentecost")  # English unchanged
        self.assertEqual(feast.name_hy, "Սուրբ Հոգեգալուստ")  # Armenian updated

    def test_feast_delete_cascade(self):
        """Test that feast deletion works correctly."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = Feast.objects.create(
            church=day.church,
            name="Test Feast",
        )

        feast_id = feast.id
        feast.delete()

        # Verify feast is deleted
        self.assertFalse(Feast.objects.filter(id=feast_id).exists())

    def test_a_commemoration_is_stored_once_however_often_it_recurs(self):
        """What replaced ordering/filtering feasts by date.

        These used to be two tests that built a Feast per day and queried them by date range.
        There is nothing left to query that way, and that is the point of the re-key: the engine
        names this commemoration on many dates, and all of them resolve to this one row.
        """
        feast = Feast.objects.create(church=self.church, name="Feast of the Holy Cross")

        for _ in range(3):
            again, created = Feast.objects.get_or_create(
                church=self.church, name="Feast of the Holy Cross")
            self.assertFalse(created)
            self.assertEqual(again.id, feast.id)

        self.assertEqual(Feast.objects.filter(church=self.church).count(), 1)

    def test_feast_default_church(self):
        """Test that a feast can be created against the default church."""
        feast = Feast.objects.create(
            church=Church.objects.get(pk=Church.get_default_pk()),
            name="Test Feast",
        )

        self.assertEqual(feast.church.pk, Church.get_default_pk())

    def test_feast_translation_null_handling(self):
        """Test that None/null translations are handled correctly."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = Feast.objects.create(
            church=day.church,
            name="Test Feast",
        )

        # Armenian translation should be None by default
        self.assertIsNone(feast.name_hy)

        # Set it to a value
        feast.name_hy = "Փորձարկման տոն"
        feast.save(update_fields=['i18n'])
        feast.refresh_from_db()
        self.assertEqual(feast.name_hy, "Փորձարկման տոն")

        # Set it back to None (clearing translation)
        feast.name_hy = None
        feast.save(update_fields=['i18n'])
        feast.refresh_from_db()
        self.assertIsNone(feast.name_hy)

    def test_feast_designation_field(self):
        """Test that designation field exists and can be set."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = Feast.objects.create(
            church=day.church,
            name="Christmas",
        )

        # Designation should be None by default
        self.assertIsNone(feast.designation)

        # Set designation
        feast.designation = Feast.Designation.NATIVITY_MOTHER_OF_GOD
        feast.save()

        feast.refresh_from_db()
        self.assertEqual(feast.designation, Feast.Designation.NATIVITY_MOTHER_OF_GOD)

    def test_feast_designation_choices(self):
        """Test that all designation choices are valid."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = Feast.objects.create(
            church=day.church,
            name="Test Feast",
        )

        # Test all designation choices
        designations = [
            Feast.Designation.SUNDAYS_DOMINICAL,
            Feast.Designation.ST_GREGORY_APOSTLES,
            Feast.Designation.PATRIARCHS_VARTAPETS,
            Feast.Designation.NATIVITY_MOTHER_OF_GOD,
            Feast.Designation.MARTYRS,
        ]

        for designation in designations:
            feast.designation = designation
            feast.save()
            feast.refresh_from_db()
            self.assertEqual(feast.designation, designation)

    def test_feast_designation_nullable(self):
        """Test that designation can be None/null."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = Feast.objects.create(
            church=day.church,
            name="Test Feast",
            designation=Feast.Designation.MARTYRS,
        )

        # Clear designation
        feast.designation = None
        feast.save()
        feast.refresh_from_db()
        self.assertIsNone(feast.designation)

    def test_feast_icon_field(self):
        """Test that icon field exists and can be set."""
        from icons.models import Icon
        from django.core.files.uploadedfile import SimpleUploadedFile
        
        day = Day.objects.create(date=self.test_date, church=self.church)
        test_image = SimpleUploadedFile(
            name='test_icon.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        icon = Icon.objects.create(
            title="Test Icon",
            church=self.church,
            image=test_image
        )
        feast = Feast.objects.create(
            church=day.church,
            name="Christmas",
        )

        # Icon should be None by default
        self.assertIsNone(feast.icon)

        # Set icon
        feast.icon = icon
        feast.save()

        feast.refresh_from_db()
        self.assertEqual(feast.icon, icon)
