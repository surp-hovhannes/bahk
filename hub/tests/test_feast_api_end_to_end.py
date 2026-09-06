"""End-to-end: the feast API, the real engine, and real rows.

Every other feast test mocks something -- the engine, or ``get_or_create_feast_for_date``. This
one mocks nothing below the HTTP layer, so it is what actually catches a break in the chain from
``Observances`` through the ``Feast`` rows to the JSON the app renders cards from.

The three cases are the three answers the app has to handle, and each is a real date:

  * two commemorations -> two cards (185 days in the engine's range),
  * one -> one card (4,606 days),
  * none -> no card at all (5,070 days, the commonest answer).
"""
from datetime import date

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from hub.models import Church, Feast


class FeastApiEndToEndTests(TestCase):
    """No mocks: the installed engine, the database, and the serialized response."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.url = reverse("feast-for-date")
        cache.clear()

    def _feasts_on(self, day):
        response = self.client.get(self.url, {"date": day.isoformat()})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["date"], day.isoformat())
        self.assertNotIn("error", payload)
        return payload["feasts"]

    def test_a_day_naming_two_commemorations_serves_two(self):
        """2001-01-18 is `The Hermit St. Anton — The Hermit Sts. Tryphon, Barsauma and Onuphrius`.

        One card each. Under the old shape this was a single card carrying the joined string.
        """
        feasts = self._feasts_on(date(2001, 1, 18))

        self.assertEqual(
            [feast["name"] for feast in feasts],
            ["The Hermit St. Anton", "The Hermit Sts. Tryphon, Barsauma and Onuphrius"],
        )
        # Two distinct rows, each keyed by its own observance.
        self.assertEqual(
            sorted(Feast.objects.filter(church=self.church).values_list(
                "observance_id", flat=True)),
            ["hermit_st_anton", "hermit_sts_tryphon_barsauma"],
        )

    def test_a_day_naming_one_commemoration_serves_one(self):
        feasts = self._feasts_on(date(2001, 1, 16))

        self.assertEqual(
            [feast["name"] for feast in feasts],
            ["Sts. Peter the Patriarch, Blaise the Bishop and Absalom the Deacon"],
        )

    def test_a_position_label_beside_a_commemoration_serves_only_the_commemoration(self):
        """2004-11-21 also carries `Eleventh Sunday of the Holy Cross`, which commemorates nobody.

        The engine marks it, so the card for it never appears -- and the row is never created.
        """
        feasts = self._feasts_on(date(2004, 11, 21))

        self.assertEqual(
            [feast["name"] for feast in feasts],
            ["Presentation of the Holy Mother of God to the Temple", "Eve of the Fast of Advent"],
        )
        self.assertFalse(
            Feast.objects.filter(observance_id="eleventh_sunday_of_the_holy_cross").exists())

    def test_a_day_that_commemorates_nobody_serves_an_empty_list(self):
        """`Sixth day of Easter` is a calendar position. No card, no row, and no error."""
        feasts = self._feasts_on(date(2026, 4, 10))

        self.assertEqual(feasts, [])
        self.assertFalse(Feast.objects.filter(church=self.church).exists())

    def test_the_response_carries_what_a_card_needs(self):
        feast = self._feasts_on(date(2001, 1, 16))[0]

        self.assertEqual(
            sorted(feast),
            sorted(["id", "name", "designation", "context_eligible", "icon", "text",
                    "short_text", "context_thumbs_up", "context_thumbs_down", "prayer"]),
        )

    def test_the_armenian_name_is_served_under_the_hy_language(self):
        response = self.client.get(self.url, {"date": "2001-01-16", "lang": "hy"})

        name = response.json()["feasts"][0]["name"]
        self.assertTrue(name.strip())
        self.assertNotEqual(
            name, "Sts. Peter the Patriarch, Blaise the Bishop and Absalom the Deacon")

    def test_asking_twice_reuses_the_row_rather_than_duplicating_it(self):
        """The commemoration recurs; the row does not. Two dates, one row."""
        self._feasts_on(date(2001, 1, 16))
        cache.clear()
        self._feasts_on(date(2002, 1, 16))

        self.assertEqual(
            Feast.objects.filter(observance_id="peter_the_patriarch_blaise").count(), 1)
