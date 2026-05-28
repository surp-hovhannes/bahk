from django.test import SimpleTestCase
from django.urls import resolve

from notifications.views import DeviceTokenCreateView


class NotificationURLTests(SimpleTestCase):
    def test_mounted_hub_notifications_route_resolves_to_device_token_view(self):
        url = "/hub/notifications/register-device-token/"

        match = resolve(url)

        self.assertEqual(match.func.view_class, DeviceTokenCreateView)
        self.assertEqual(match.url_name, "register-device-token")
        self.assertEqual(match.namespace, "notifications")
