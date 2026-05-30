"""Cross-app tag API views."""

from django.contrib.contenttypes.models import ContentType
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from taggit.models import TaggedItem


SUPPORTED_MODELS = {
    "prayer": ("prayers", "prayer"),
    "icon": ("icons", "icon"),
    "patristic_quote": ("hub", "patristicquote"),
}

MODEL_ALIASES = {
    "patristicquote": "patristic_quote",
}


class SystemTagsView(APIView):
    """Return unique django-taggit tag names used by supported content models."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        raw_model_filter = request.query_params.get("model")
        selected_models, invalid_models = self._parse_model_filter(raw_model_filter)

        if invalid_models:
            return Response(
                {
                    "detail": f"Unsupported model filter: {', '.join(invalid_models)}",
                    "allowed_models": list(SUPPORTED_MODELS.keys()),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        tags_by_model = {
            model_key: self._get_tags_for_model(model_key)
            for model_key in selected_models
        }

        if raw_model_filter is not None and len(selected_models) == 1:
            return Response(tags_by_model[selected_models[0]])

        return Response(tags_by_model)

    def _parse_model_filter(self, raw_model_filter):
        if raw_model_filter is None or raw_model_filter.strip() == "":
            return list(SUPPORTED_MODELS.keys()), []

        selected_models = []
        invalid_models = []
        seen_models = set()

        for fragment in raw_model_filter.split(","):
            model_key = fragment.strip().lower()
            if not model_key:
                continue

            model_key = MODEL_ALIASES.get(model_key, model_key)

            if model_key not in SUPPORTED_MODELS:
                invalid_models.append(fragment.strip().lower())
                continue

            if model_key not in seen_models:
                selected_models.append(model_key)
                seen_models.add(model_key)

        if not selected_models and not invalid_models:
            return list(SUPPORTED_MODELS.keys()), []

        return selected_models, invalid_models

    def _get_tags_for_model(self, model_key):
        app_label, model_name = SUPPORTED_MODELS[model_key]
        content_type = ContentType.objects.get_by_natural_key(app_label, model_name)
        names = TaggedItem.objects.filter(
            content_type=content_type,
        ).values_list("tag__name", flat=True).distinct()

        return sorted(set(names), key=str.casefold)
