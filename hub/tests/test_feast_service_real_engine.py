"""Regression tests for the released ``armenian_lectionary`` engine.

The ordinary feast-service suite mocks the engine.  These cases exercise the
installed wheel so known feast-name regressions cannot pass behind those mocks.
"""
from datetime import date

from django.test import TestCase

from hub.models import Church
from hub.services.feast_service import get_feast_for_date


RELEASED_ENGINE_CASES = (
    (date(2011, 2, 4), "Fast day"),
    (date(2011, 2, 6), "Fourth Sunday after Nativity"),
    (date(2011, 2, 9), "Fast day"),
    (date(2011, 2, 11), "Fast day"),
    (date(2011, 2, 13), "Fifth Sunday after Nativity — Eve of Fast of Catechumens"),
    (date(2022, 2, 4), "Fast day"),
)


class FeastServiceRealEngineTests(TestCase):
    """Check reviewed feast names against the installed engine wheel."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())

    def test_released_engine_regressions(self):
        for date_obj, expected_name in RELEASED_ENGINE_CASES:
            with self.subTest(date=date_obj):
                result = get_feast_for_date(date_obj, self.church)

                self.assertIsNotNone(result)
                self.assertEqual(result["name"], expected_name)
                self.assertEqual(result["name_en"], expected_name)
                self.assertTrue(result["name_hy"].strip())
