"""Staff-only append-only FeastContext management API."""

from collections.abc import Mapping

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from hub.models import Feast, FeastContext
from hub.services.feast_contexts import (
    FeastContextRegenerationUnavailable,
    FeastContextVersionInput,
    append_feast_context_version,
    available_feast_context_languages,
    enqueue_feast_context_regeneration,
    feast_context_languages,
    get_feast_context_task_status,
    restore_feast_context_version,
)


class FeastContextHistoryPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


def _actor_data(context):
    if context.created_by_id is None:
        return None
    return {
        "id": context.created_by_id,
        "username": context.created_by.get_username(),
    }


def serialize_feast_context(context, *, include_text=True):
    data = {
        "feast_id": context.feast_id,
        "version": context.version,
        "active": context.active,
        "operation": context.operation,
        "created_at": context.time_of_generation,
        "created_by": _actor_data(context),
        "prompt": (
            {"id": context.prompt_id, "model": context.prompt.model}
            if context.prompt_id
            else None
        ),
        "additional_instructions": context.additional_instructions,
        "restored_from_version": (
            context.restored_from.version if context.restored_from_id else None
        ),
        "thumbs_up": context.thumbs_up,
        "thumbs_down": context.thumbs_down,
        "languages": feast_context_languages(context, include_text=include_text),
    }
    return data


def _validate_object_body(data, allowed_fields):
    if not isinstance(data, Mapping):
        return {"non_field_errors": ["Request body must be a JSON object."]}
    unknown = set(data) - set(allowed_fields)
    return {field: "Unknown field." for field in sorted(unknown)}


def _validate_nonblank_string(data, field, *, required):
    if field not in data:
        return "This field is required." if required else None
    value = data[field]
    if not isinstance(value, str) or not value.strip():
        return "Must be a nonblank string."
    return None


class FeastContextDetailView(APIView):
    """Read the active version or append one manual language edit."""

    permission_classes = [IsAdminUser]

    def get(self, request, pk):
        feast = get_object_or_404(Feast, pk=pk)
        context = (
            feast.contexts.filter(active=True)
            .select_related("created_by", "prompt", "restored_from")
            .first()
        )
        if context is None:
            return Response(
                {"detail": "No active context exists for this feast."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(serialize_feast_context(context))

    def patch(self, request, pk):
        feast = get_object_or_404(Feast, pk=pk)
        errors = _validate_object_body(
            request.data, {"language", "text", "short_text"}
        )
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        for field in ("language", "text", "short_text"):
            if error := _validate_nonblank_string(request.data, field, required=True):
                errors[field] = error
        language = request.data.get("language")
        if (
            isinstance(language, str)
            and language.strip()
            and language not in available_feast_context_languages()
        ):
            errors["language"] = "Unsupported language."
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        context = append_feast_context_version(
            feast.id,
            values=FeastContextVersionInput(
                operation=FeastContext.Operation.MANUAL_EDIT,
                actor_id=request.user.id,
            ),
            language_updates={
                language: {
                    "text": request.data["text"].strip(),
                    "short_text": request.data["short_text"].strip(),
                }
            },
        )
        context = FeastContext.objects.select_related(
            "created_by", "prompt", "restored_from"
        ).get(pk=context.pk)
        return Response(serialize_feast_context(context), status=status.HTTP_200_OK)


class FeastContextHistoryView(APIView):
    """Return bounded, newest-first version history."""

    permission_classes = [IsAdminUser]
    pagination_class = FeastContextHistoryPagination

    def get(self, request, pk):
        feast = get_object_or_404(Feast, pk=pk)
        include_text = request.query_params.get("include_text", "").lower() == "true"
        queryset = feast.contexts.select_related(
            "created_by", "prompt", "restored_from"
        ).order_by("-version", "-time_of_generation", "-pk")
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            [serialize_feast_context(context, include_text=include_text) for context in page]
        )


class FeastContextRegenerateView(APIView):
    """Queue an idempotent staff/editorial regeneration."""

    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        feast = get_object_or_404(Feast, pk=pk)
        errors = _validate_object_body(request.data, {"additional_instructions"})
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        if "additional_instructions" in request.data:
            if error := _validate_nonblank_string(
                request.data, "additional_instructions", required=False
            ):
                return Response(
                    {"additional_instructions": error},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        instructions = request.data.get("additional_instructions", "").strip()
        try:
            task_id, created = enqueue_feast_context_regeneration(
                feast.id,
                actor_id=request.user.id,
                additional_instructions=instructions,
            )
        except FeastContextRegenerationUnavailable:
            return Response(
                {
                    "detail": (
                        "Regeneration is temporarily unavailable because an "
                        "idempotency lock could not be established."
                    )
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        body = {
            "task_id": task_id,
            "status": "queued" if created else "already_queued",
            "feast_id": feast.id,
        }
        return Response(
            body,
            status=(
                status.HTTP_202_ACCEPTED if created else status.HTTP_409_CONFLICT
            ),
        )


class FeastContextRestoreView(APIView):
    """Restore an old body by copying it into a new version."""

    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        feast = get_object_or_404(Feast, pk=pk)
        errors = _validate_object_body(request.data, {"version"})
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        version = request.data.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            return Response(
                {"version": "Must be a positive integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            context = restore_feast_context_version(
                feast.id, version=version, actor_id=request.user.id
            )
        except FeastContext.DoesNotExist:
            return Response(
                {"version": "No context with this version exists for this feast."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        context = FeastContext.objects.select_related(
            "created_by", "prompt", "restored_from"
        ).get(pk=context.pk)
        return Response(serialize_feast_context(context), status=status.HTTP_201_CREATED)


class FeastContextTaskStatusView(APIView):
    """Read safe cache-backed status metadata for a task belonging to this feast."""

    permission_classes = [IsAdminUser]

    def get(self, request, pk, task_id):
        get_object_or_404(Feast, pk=pk)
        task = get_feast_context_task_status(str(task_id))
        if task is None or task["feast_id"] != pk:
            return Response(
                {"detail": "Task not found."}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(task)
