from django.db import migrations


def set_screen_view_source(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    EventType = apps.get_model('events', 'EventType')

    try:
        screen_view_type = EventType.objects.get(code='screen_view')
    except EventType.DoesNotExist:
        return

    screen_views = Event.objects.filter(event_type=screen_view_type)

    for event in screen_views.iterator():
        data = event.data or {}
        if data.get('source'):
            continue

        path = data.get('path', '') or ''
        source = 'api' if str(path).startswith('/api/') else 'app_ui'
        data['source'] = source
        Event.objects.filter(pk=event.pk).update(data=data)


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0009_announcement_i18n_event_i18n_useractivityfeed_i18n'),
    ]

    operations = [
        migrations.RunPython(set_screen_view_source, migrations.RunPython.noop),
    ]
