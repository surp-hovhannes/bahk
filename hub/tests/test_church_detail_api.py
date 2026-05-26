from django.test import TestCase
from django.urls import reverse
from rest_framework import status

from hub.models import Church


class ChurchDetailAPITests(TestCase):
    def test_church_detail_returns_serialized_church(self):
        church = Church.objects.create(name="Detail Test Church")

        response = self.client.get(reverse("church-detail", args=[church.pk]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"id": church.pk, "name": church.name})

    def test_church_detail_returns_404_for_missing_church(self):
        response = self.client.get(reverse("church-detail", args=[999999]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
