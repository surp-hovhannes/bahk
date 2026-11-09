"""Offline admin-action fixture, installed only by visual_qa_settings."""
from threading import RLock
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from exponent_server_sdk import PushServerError
from requests import Response

TOKEN = 'ExponentPushToken[visual-qa-transport]'
USERNAME = 'visual-qa-anna'
# Patches and override_settings are process-global. Serialize all requests in this
# disposable server, including unrelated scenarios, until each patch is restored.
_LOCK = RLock()


def require_visual_settings():
    if settings.SETTINGS_MODULE != 'tests.visual_qa_settings':
        raise ImproperlyConfigured('Transport fixture requires visual QA settings')


def seed():
    require_visual_settings()
    from django.contrib.auth import get_user_model
    from notifications.models import DeviceToken
    from hub.management.commands.seed_visual_qa import upsert

    user = get_user_model().objects.get(username=USERNAME)
    upsert(DeviceToken, key={'token': TOKEN}, user=user, device_type='ios', is_active=True)


class TransportWarningFixtureMiddleware:
    def __init__(self, get_response):
        require_visual_settings()
        self.get_response = get_response

    def __call__(self, request):
        from notifications.models import DeviceToken

        with _LOCK:
            selected = request.POST.getlist('_selected_action') if request.method == 'POST' else []
            fixture = (
                request.path == '/admin/notifications/devicetoken/'
                and request.POST.get('action') == 'send_test_notification'
                and request.user.is_authenticated and request.user.is_superuser
                and len(selected) == 1 and selected[0].isdecimal()
                and DeviceToken.objects.filter(
                    pk__in=selected, token=TOKEN, user__username=USERNAME, is_active=True,
                ).exists()
            )
            if not fixture:
                # The synchronous admin sender does not consult SEND_PUSH_NOTIFICATIONS.
                with patch('notifications.utils.PushClient', side_effect=RuntimeError('Visual QA provider disabled')):
                    return self.get_response(request)
            response = Response()
            response.status_code = 503
            body = {'errors': [{'message': 'Synthetic offline rejection'}]}
            error = PushServerError('Synthetic offline rejection', response, response_data=body)
            with (
                override_settings(NOTIFICATION_TELEMETRY_ENABLED=False),
                patch('notifications.utils.PushClient') as provider,
                patch('notifications.utils.time.sleep'),
            ):
                provider.return_value.publish.side_effect = error
                return self.get_response(request)
