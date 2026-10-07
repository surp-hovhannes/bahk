"""Small review surface with object authorization before every read and write."""

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
from moderation.access import allowed, is_crisis, visible_prayers
from moderation.models import Review

MODELS = {"prayer": PrayerRequest, "icon": IconFeedback, "reading": ReadingContext, "feast": FeastContext}


def protected(view):
    @wraps(view)
    def check(request, *args, **kwargs):
        if not (allowed(request.user, "general") or allowed(request.user, "crisis")):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return never_cache(login_required(check, login_url="moderation-login"))


def authorized(kind, user):
    if kind not in MODELS:
        raise Http404
    queryset = MODELS[kind].objects.all()
    if kind == "prayer":
        return visible_prayers(queryset, user)
    if not allowed(user, "general"):
        raise PermissionDenied
    return queryset


def version(kind, obj):
    if kind == "prayer":
        return str(obj.moderated_at or obj.created_at)
    if kind == "icon":
        return str(obj.created_at)
    return str(obj.thumbs_down)


def reviewed(kind, obj):
    return (
        Review.objects.filter(kind=kind, object_id=obj.pk, signal_version=version(kind, obj))
        .exclude(outcome="misclassification")
        .exists()
    )


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
    )


@protected
@require_http_methods(["GET"])
def dashboard(request):
    groups = []
    selected = request.GET.get("type", "")
    audit = request.GET.get("status") == "audit"
    for kind in MODELS:
        if selected and selected != kind:
            continue
        if kind != "prayer" and not allowed(request.user, "general"):
            continue
        queryset = authorized(kind, request.user)
        if kind == "prayer":
            queryset = (
                queryset.filter(status="rejected")
                if audit
                else queryset.filter(Q(requires_human_review=True) | Q(status="pending_moderation"))
            )
        elif kind == "icon":
            queryset = queryset.filter(is_resolved=audit)
        else:
            threshold = getattr(settings, f"{kind.upper()}_CONTEXT_REGENERATION_THRESHOLD", 5)
            queryset = queryset.filter(active=True, thumbs_down__gte=threshold)
        stamp = "time_of_generation" if kind in {"reading", "feast"} else "created_at"
        items = [describe(kind, obj) for obj in queryset.order_by(stamp, "pk") if audit or not reviewed(kind, obj)]
        groups.append(dict(kind=kind, count=len(items), oldest=items[0] if items else None, items=items[:25]))
    return render(request, "moderation/dashboard.html", {"groups": groups, "audit": audit, "selected_type": selected})


@protected
@require_http_methods(["GET", "POST"])
def detail(request, kind, pk):
    with transaction.atomic():
        obj = get_object_or_404(authorized(kind, request.user).select_for_update(), pk=pk)
        actions = ["acknowledge", "misclassification"]
        crisis = kind == "prayer" and is_crisis(obj)
        if kind == "prayer" and not crisis and obj.status == "pending_moderation":
            actions = ["approve", "reject", "misclassification"]
        if kind == "icon" and not obj.is_resolved:
            actions += ["resolve"]
        if crisis:
            actions += ["escalated"]
        if request.method == "POST":
            action = request.POST.get("action")
            note = request.POST.get("note", "").strip()
            expected = request.POST.get("expected_outcome", "").strip()[:80]
            reference = request.POST.get("regression_reference", "").strip()[:200]
            if (
                action not in actions
                or not note
                or len(note) > 4000
                or (action == "misclassification" and not expected)
            ):
                raise PermissionDenied(
                    "Choose an available action and provide a review note; classification errors need an expected outcome."
                )
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
        return render(
            request,
            "moderation/detail.html",
            dict(item=describe(kind, obj), actions=actions, history=history, crisis=crisis, admin_url=admin_url),
        )
