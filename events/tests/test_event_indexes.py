from collections import Counter

from django.db import connection
from django.test import TestCase


class EventIndexTests(TestCase):
    def test_event_analytics_indexes_are_not_duplicated(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, 'events_event')

        index_columns = Counter(
            tuple(info['columns'])
            for info in constraints.values()
            if info.get('index') and info.get('columns')
        )

        self.assertEqual(index_columns[('event_type_id', 'timestamp')], 1)
        self.assertEqual(index_columns[('content_type_id', 'object_id', 'timestamp')], 1)
