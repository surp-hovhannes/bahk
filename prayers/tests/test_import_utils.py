"""Tests for prayer set JSON import utilities."""

from django.test import TestCase

from hub.models import Church
from prayers.import_utils import detect_conflicts, execute_import, validate_import_json
from prayers.models import Prayer, PrayerSet, PrayerSetMembership


def valid_import_data():
    return {
        "prayer_sets": [
            {
                "title": "Morning Set",
                "title_hy": "Առավոտյան հավաքածու",
                "description": "Start the day",
                "description_hy": "Օրը սկսելու համար",
                "category": "morning",
                "prayers": [
                    {
                        "title": "First Prayer",
                        "text": "Lord, guide us.",
                        "category": "morning",
                        "tags": ["guidance", "morning"],
                        "translations": {
                            "hy": {
                                "title": "Առաջին աղոթք",
                                "text": "Տեր, առաջնորդիր մեզ։",
                            },
                        },
                    },
                    {
                        "title": "Second Prayer",
                        "text": "Lord, keep us.",
                        "category": "general",
                        "tags": "protection, daily",
                    },
                ],
            },
        ],
    }


class ValidateImportJsonTests(TestCase):
    """Validation coverage for prayer set import JSON."""

    def test_valid_json(self):
        validate_import_json(valid_import_data())

    def test_missing_prayer_sets(self):
        with self.assertRaisesMessage(ValueError, "prayer_sets"):
            validate_import_json({})

    def test_missing_set_required_field(self):
        data = valid_import_data()
        del data["prayer_sets"][0]["title"]

        with self.assertRaisesMessage(ValueError, 'missing required field "title"'):
            validate_import_json(data)

    def test_missing_prayer_required_field(self):
        data = valid_import_data()
        del data["prayer_sets"][0]["prayers"][0]["text"]

        with self.assertRaisesMessage(ValueError, 'missing required field "text"'):
            validate_import_json(data)

    def test_bad_category(self):
        data = valid_import_data()
        data["prayer_sets"][0]["category"] = "midday"

        with self.assertRaisesMessage(ValueError, 'invalid category "midday"'):
            validate_import_json(data)

    def test_empty_prayers_array(self):
        data = valid_import_data()
        data["prayer_sets"][0]["prayers"] = []

        with self.assertRaisesMessage(ValueError, "non-empty prayers array"):
            validate_import_json(data)


class DetectConflictsTests(TestCase):
    """Conflict detection should find church-scoped title matches."""

    def setUp(self):
        self.church = Church.objects.create(name="Import Church")
        self.other_church = Church.objects.create(name="Other Church")
        self.data = valid_import_data()

    def test_no_conflicts(self):
        self.assertEqual(detect_conflicts(self.data, self.church), [])

    def test_set_conflict(self):
        existing = PrayerSet.objects.create(
            title="Morning Set",
            category="morning",
            church=self.church,
        )
        PrayerSet.objects.create(
            title="Morning Set",
            category="morning",
            church=self.other_church,
        )

        self.assertEqual(
            detect_conflicts(self.data, self.church),
            [{"type": "Prayer Set", "title": "Morning Set", "existing_id": existing.id}],
        )

    def test_prayer_conflict(self):
        existing = Prayer.objects.create(
            title="First Prayer",
            text="Existing text",
            category="morning",
            church=self.church,
        )

        self.assertEqual(
            detect_conflicts(self.data, self.church),
            [{"type": "Prayer", "title": "First Prayer", "existing_id": existing.id}],
        )

    def test_both_conflicts(self):
        existing_set = PrayerSet.objects.create(
            title="Morning Set",
            category="morning",
            church=self.church,
        )
        existing_prayer = Prayer.objects.create(
            title="First Prayer",
            text="Existing text",
            category="morning",
            church=self.church,
        )

        self.assertEqual(
            detect_conflicts(self.data, self.church),
            [
                {"type": "Prayer Set", "title": "Morning Set", "existing_id": existing_set.id},
                {"type": "Prayer", "title": "First Prayer", "existing_id": existing_prayer.id},
            ],
        )


class ExecuteImportTests(TestCase):
    """Import execution should create records and preserve ordering."""

    def setUp(self):
        self.church = Church.objects.create(name="Execute Import Church")

    def test_execute_import_creates_records_memberships_and_tags(self):
        sets_created, prayers_created, created_ids = execute_import(valid_import_data(), self.church)

        self.assertEqual(sets_created, 1)
        self.assertEqual(prayers_created, 2)
        self.assertEqual(len(created_ids), 2)

        prayer_set = PrayerSet.objects.get(title="Morning Set")
        self.assertEqual(prayer_set.church, self.church)
        self.assertEqual(prayer_set.category, "morning")
        self.assertEqual(prayer_set.title_hy, "Առավոտյան հավաքածու")
        self.assertEqual(prayer_set.description_hy, "Օրը սկսելու համար")

        memberships = list(PrayerSetMembership.objects.filter(prayer_set=prayer_set).order_by("order"))
        self.assertEqual([membership.order for membership in memberships], [1, 2])
        self.assertEqual([membership.prayer.title for membership in memberships], ["First Prayer", "Second Prayer"])

        first_prayer = memberships[0].prayer
        self.assertEqual(first_prayer.title_hy, "Առաջին աղոթք")
        self.assertEqual(first_prayer.text_hy, "Տեր, առաջնորդիր մեզ։")
        self.assertEqual(
            set(first_prayer.tags.names()),
            {"guidance", "morning"},
        )
        self.assertEqual(
            set(memberships[1].prayer.tags.names()),
            {"protection", "daily"},
        )
