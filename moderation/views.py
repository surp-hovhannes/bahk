"""Small review surface with object authorization before every read and write."""

import json
from functools import wraps

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from hub.models import FeastContext, ReadingContext
from icons.models import IconFeedback
from prayers.models import PrayerRequest
from moderation.access import can, is_crisis, visible_prayers
from moderation.models import Review
from moderation.context import shell_context

MODELS = {"prayer": PrayerRequest, "icon": IconFeedback, "reading": ReadingContext, "feast": FeastContext}


def protected(view):
    @wraps(view)
    def check(request, *args, **kwargs):
        if not (can(request, "general") or can(request, "crisis")):
            # Explain the refusal and offer a way out instead of a bare 403.
            return render(
                request,
                "moderation/no_access.html",
                {**shell_context(request), "title": "No moderation access"},
                status=403,
            )
        return view(request, *args, **kwargs)

    return never_cache(login_required(check, login_url="moderation-login"))


def authorized(kind, request):
    if kind not in MODELS:
        raise Http404
    queryset = MODELS[kind].objects.all()
    if kind == "prayer":
        return visible_prayers(queryset, request)
    if not can(request, "general"):
        raise PermissionDenied
    return queryset


def version(kind, obj):
    if kind == "prayer":
        return str(obj.moderated_at or obj.created_at)
    if kind == "icon":
        return str(obj.created_at)
    return str(obj.thumbs_down)


VERSION_FIELDS = {"prayer": ("moderated_at", "created_at"), "icon": ("created_at",)}


def reviewed_signals(kind):
    """(object_id, signal_version) pairs already closed by a human review."""
    return set(
        Review.objects.filter(kind=kind).exclude(outcome="misclassification").values_list("object_id", "signal_version")
    )


def outstanding_pks(kind, queryset):
    """Primary keys whose current signal has not been reviewed, from two lightweight queries."""
    done = reviewed_signals(kind)
    fields = VERSION_FIELDS.get(kind, ("thumbs_down",))
    pks = []
    for pk, *values in queryset.values_list("pk", *fields):
        signal = str(next((value for value in values if value), values[-1]))
        if (pk, signal) not in done:
            pks.append(pk)
    return pks


def describe(kind, obj):
    if kind == "prayer":
        label, text, stamp = obj.title, obj.description, obj.created_at
        status = obj.get_status_display()
        if obj.moderation_severity == "critical":
            status = "CRISIS · " + status
        elif obj.requires_human_review:
            status = "Human review · " + status
        source = "Request creation (not first flag)"
    elif kind == "icon":
        label, text, stamp = obj.icon_title_at_time or f"Icon #{obj.icon_id}", obj.description, obj.created_at
        status = "Resolved" if obj.is_resolved else obj.get_feedback_type_display()
        source = "Feedback submission"
    else:
        label, text, stamp = f"{kind.title()} context #{obj.pk}", obj.text, obj.time_of_generation
        status = f"{obj.thumbs_down} downvotes; feedback signal, not publication hold"
        if not obj.active:
            status += " · replaced by regeneration"
        source = "Context generation (not first flag)"
    return dict(
        kind=kind,
        obj=obj,
        label=label,
        text=text,
        timestamp=stamp,
        source=source,
        status=status,
        url=reverse("moderation-detail", args=[kind, obj.pk]),
        crisis=kind == "prayer" and is_crisis(obj),
    )


PAGE_SIZE = 25

ACTION_LABELS = {
    "approve": "Approve and publish",
    "reject": "Reject",
    "acknowledge": "Acknowledge",
    "resolve": "Resolve",
    "escalated": "Escalated",
    "misclassification": "Misclassification",
}


@protected
@require_http_methods(["GET"])
def dashboard(request):
    groups = []
    selected = request.GET.get("type", "")
    audit = request.GET.get("status") == "audit"
    for kind in MODELS:
        if selected and selected != kind:
            continue
        if kind != "prayer" and not can(request, "general"):
            continue
        queryset = authorized(kind, request)
        if kind == "prayer":
            queue = Q(requires_human_review=True) | Q(status="pending_moderation")
            history = Q(status="rejected")
        elif kind == "icon":
            queue, history = Q(is_resolved=False), Q(is_resolved=True)
        else:
            # Downvoted contexts stay queued after regeneration replaces them:
            # the notice points at the downvoted text, not its successor.
            threshold = getattr(settings, f"{kind.upper()}_CONTEXT_REGENERATION_THRESHOLD", 5)
            queue, history = Q(thumbs_down__gte=threshold), Q(pk__in=[])
        stamp = "time_of_generation" if kind in {"reading", "feast"} else "created_at"
        if audit:
            # Audit = everything already decided: rejected/resolved, or carrying a human review.
            reviewed_ids = Review.objects.filter(kind=kind).values("object_id")
            queryset = queryset.filter(history | Q(pk__in=reviewed_ids)).order_by(f"-{stamp}", "-pk")
            count = queryset.count()
            objects = list(queryset[:PAGE_SIZE])
        else:
            queryset = queryset.filter(queue).order_by(stamp, "pk")
            pks = outstanding_pks(kind, queryset)
            # Signal IDs are not an authorization snapshot: moderation may
            # reclassify or delete a prayer between these reads. Retain the
            # permission and queue filters for both the count and final fetch.
            outstanding = queryset.filter(pk__in=pks)
            count = outstanding.count()
            objects = list(outstanding[:PAGE_SIZE])
        items = [describe(kind, obj) for obj in objects]
        groups.append(
            dict(kind=kind, count=count, oldest=None if audit else (items[0] if items else None), items=items)
        )
    return render(
        request,
        "moderation/dashboard.html",
        {
            **shell_context(request),
            "title": "Review dashboard",
            "groups": groups,
            "audit": audit,
            "selected_type": selected,
            "page_size": PAGE_SIZE,
        },
    )


@protected
@require_http_methods(["GET", "POST"])
def detail(request, kind, pk):
    with transaction.atomic():
        obj = get_object_or_404(authorized(kind, request).select_for_update(), pk=pk)
        actions = ["acknowledge", "misclassification"]
        crisis = kind == "prayer" and is_crisis(obj)
        if kind == "prayer" and not crisis and obj.status == "pending_moderation":
            actions = ["approve", "reject", "misclassification"]
        if kind == "icon" and not obj.is_resolved:
            actions += ["resolve"]
        if crisis:
            actions += ["escalated"]
        errors = {}
        submitted = {}
        if request.method == "POST":
            action = request.POST.get("action", "")
            note = request.POST.get("note", "").strip()
            expected = request.POST.get("expected_outcome", "").strip()[:80]
            reference = request.POST.get("regression_reference", "").strip()[:200]
            submitted = dict(action=action, note=note, expected_outcome=expected, regression_reference=reference)
            if action and action not in actions:
                # Only a forged or stale form submits an outcome that was never offered.
                raise PermissionDenied("That outcome is not available for this item.")
            if not action:
                errors["action"] = "Choose an outcome."
            if not note:
                errors["note"] = "A review note is required."
            elif len(note) > 4000:
                errors["note"] = "Keep the note under 4,000 characters."
            if action == "misclassification" and not expected:
                errors["expected_outcome"] = "Say what the correct outcome should have been."
        if request.method == "POST" and not errors:
            signal = version(kind, obj)
            if action in {"approve", "reject"}:
                # Reuse existing event, milestone and acceptance behavior.
                handler = admin.site._registry[PrayerRequest]
                getattr(handler, f"{action}_requests")(request, PrayerRequest.objects.filter(pk=pk))
            elif kind == "prayer" and action != "misclassification":
                obj.requires_human_review = False
                obj.reviewed = True
                obj.save(update_fields=["requires_human_review", "reviewed", "updated_at"])
            elif kind == "icon" and action != "misclassification":
                obj.is_resolved = True
                obj.resolved_at = timezone.now()
                obj.admin_notes = note
                obj.save(update_fields=["is_resolved", "resolved_at", "admin_notes"])
            Review.objects.create(
                kind=kind,
                object_id=pk,
                reviewer=request.user,
                outcome=action,
                note=note,
                expected_outcome=expected,
                regression_reference=reference,
                signal_version=signal,
            )
            return redirect("moderation-detail", kind=kind, pk=pk)
        history = Review.objects.filter(kind=kind, object_id=pk).select_related("reviewer")
        admin_url = None
        opts = obj._meta
        if request.user.is_staff and request.user.has_perm(f"{opts.app_label}.view_{opts.model_name}"):
            admin_url = reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[pk])
        item = describe(kind, obj)
        evidence = None
        if kind == "prayer" and obj.moderation_result:
            evidence = json.dumps(obj.moderation_result, indent=2, sort_keys=True, default=str)
        return render(
            request,
            "moderation/detail.html",
            dict(
                **shell_context(request),
                title=item["label"],
                item=item,
                actions=[(action, ACTION_LABELS.get(action, action.title())) for action in actions],
                history=history,
                crisis=crisis,
                admin_url=admin_url,
                evidence=evidence,
                errors=errors,
                submitted=submitted,
            ),
            status=400 if errors else 200,
        )
