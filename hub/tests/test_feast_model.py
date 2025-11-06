"""Tests for the Feast model."""
from datetime import date

from django.db import IntegrityError
from django.test import TestCase

from hub.models import Church, Feast


class FeastModelTests(TestCase):
    """Tests for the Feast model."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)

    def test_create_feast_basic(self):
        """Test creating a basic feast."""
        feast = Feast.objects.create(
            date=self.test_date,
            church=self.church,
            name="Christmas",
        )

        self.assertEqual(feast.name, "Christmas")
        self.assertEqual(feast.date, self.test_date)
        self.assertEqual(feast.church, self.church)
        self.assertIsNone(feast.name_hy)

    def test_feast_with_armenian_translation(self):
        """Test creating feast with Armenian translation."""
        feast = Feast.objects.create(
            date=self.test_date,
            church=self.church,
            name="Christmas",
        )
        feast.name_hy = "Սուրբ Ծնունդ"
        feast.save(update_fields=['i18n'])

        # Refresh and verify translation
        feast.refresh_from_db()
        self.assertEqual(feast.name, "Christmas")
        self.assertEqual(feast.name_hy, "Սուրբ Ծնունդ")

    def test_feast_unique_constraint(self):
        """Test that the unique constraint on date and church works."""
        Feast.objects.create(
            date=self.test_date,
            church=self.church,
            name="First Feast",
        )

        # Try to create another feast on same date and church
        with self.assertRaises(IntegrityError):
            Feast.objects.create(
                date=self.test_date,
                church=self.church,
                name="Second Feast",
            )

    def test_feast_different_churches_same_date(self):
        """Test that different churches can have feasts on the same date."""
        # Create another church
        other_church = Church.objects.create(
            name="Other Church"
        )

        # Create feast for first church
        feast1 = Feast.objects.create(
            date=self.test_date,
            church=self.church,
            name="Feast in Church 1",
        )

        # Create feast for second church on same date - should succeed
        feast2 = Feast.objects.create(
            date=self.test_date,
            church=other_church,
            name="Feast in Church 2",
        )

        self.assertEqual(feast1.date, feast2.date)
        self.assertNotEqual(feast1.church, feast2.church)
        self.assertEqual(Feast.objects.filter(date=self.test_date).count(), 2)

    def test_feast_str_representation(self):
        """Test the string representation of Feast."""
        feast = Feast.objects.create(
            date=self.test_date,
            church=self.church,
            name="Epiphany",
        )

        expected = f'Epiphany (2025-12-25, {self.church.name})'
        self.assertEqual(str(feast), expected)

    def test_feast_related_name(self):
        """Test that feasts can be accessed through church.feasts."""
        feast1 = Feast.objects.create(
            date=date(2025, 1, 1),
            church=self.church,
            name="New Year",
        )
        feast2 = Feast.objects.create(
            date=date(2025, 1, 6),
            church=self.church,
            name="Epiphany",
        )

        # Access feasts through church
        church_feasts = self.church.feasts.all()
        self.assertEqual(church_feasts.count(), 2)
        self.assertIn(feast1, church_feasts)
        self.assertIn(feast2, church_feasts)

    def test_feast_translation_field_access(self):
        """Test accessing translation fields through i18n."""
        feast = Feast.objects.create(
            date=self.test_date,
            church=self.church,
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
        feast = Feast.objects.create(
            date=self.test_date,
            church=self.church,
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
        feast = Feast.objects.create(
            date=self.test_date,
            church=self.church,
            name="Test Feast",
        )

        feast_id = feast.id
        feast.delete()

        # Verify feast is deleted
        self.assertFalse(Feast.objects.filter(id=feast_id).exists())

    def test_feast_ordering_by_date(self):
        """Test querying feasts ordered by date."""
        feast1 = Feast.objects.create(
            date=date(2025, 1, 6),
            church=self.church,
            name="Epiphany",
        )
        feast2 = Feast.objects.create(
            date=date(2025, 1, 1),
            church=self.church,
            name="New Year",
        )
        feast3 = Feast.objects.create(
            date=date(2025, 1, 15),
            church=self.church,
            name="Mid-January Feast",
        )

        # Query ordered by date
        feasts = Feast.objects.filter(church=self.church).order_by('date')
        feast_list = list(feasts)

        self.assertEqual(feast_list[0], feast2)  # Jan 1
        self.assertEqual(feast_list[1], feast1)  # Jan 6
        self.assertEqual(feast_list[2], feast3)  # Jan 15

    def test_feast_filter_by_date_range(self):
        """Test filtering feasts by date range."""
        feast1 = Feast.objects.create(
            date=date(2025, 1, 1),
            church=self.church,
            name="New Year",
        )
        feast2 = Feast.objects.create(
            date=date(2025, 1, 6),
            church=self.church,
            name="Epiphany",
        )
        feast3 = Feast.objects.create(
            date=date(2025, 1, 15),
            church=self.church,
            name="Mid-January Feast",
        )
        feast4 = Feast.objects.create(
            date=date(2025, 2, 1),
            church=self.church,
            name="February Feast",
        )

        # Filter feasts in January
        january_feasts = Feast.objects.filter(
            church=self.church,
            date__gte=date(2025, 1, 1),
            date__lt=date(2025, 2, 1)
        )

        self.assertEqual(january_feasts.count(), 3)
        self.assertIn(feast1, january_feasts)
        self.assertIn(feast2, january_feasts)
        self.assertIn(feast3, january_feasts)
        self.assertNotIn(feast4, january_feasts)

    def test_feast_default_church(self):
        """Test that feast uses default church if not specified."""
        feast = Feast.objects.create(
            date=self.test_date,
            name="Test Feast",
            # church not specified, should use default
        )

        # Verify default church is used
        self.assertEqual(feast.church.pk, Church.get_default_pk())

    def test_feast_translation_null_handling(self):
        """Test that None/null translations are handled correctly."""
        feast = Feast.objects.create(
            date=self.test_date,
            church=self.church,
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
