"""Minimal notices: private review details remain behind object authorization."""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.urls import reverse

logger = logging.getLogger(__name__)


def notify(kind, pk, capability="general", subject="Moderation item needs review"):
    try:
        addresses = (
            get_user_model()
            .objects.filter(is_active=True, **{f"responsibility__{capability}": True})
            .exclude(email="")
            .values_list("email", flat=True)
        )
        recipients = sorted({email.strip().lower() for email in addresses if email.strip()})
        url = settings.SITE_URL.rstrip("/") + reverse("moderation-detail", args=[kind, pk])
    except Exception:
        # A notification failure must not change the core moderation decision.
        logger.exception("Failed to prepare moderation notice for %s %s", kind, pk)
        return
    body = f"A moderation item needs your attention. Sign in to review it: {url}"
    if not recipients:
        # Never drop a notice silently: an unstaffed responsibility is an
        # operational fault. The fallback notice still carries only a link.
        logger.error("No active %s moderation responder for %s %s; using fallback inbox", capability, kind, pk)
        recipients = list(getattr(settings, "MODERATION_FALLBACK_EMAILS", []))
        body += (
            f"\n\nNobody is currently assigned the {capability} moderation responsibility. "
            "Assign a responder so this item can be reviewed."
        )
    for address in recipients:
        try:
            send_mail(
                subject,
                body,
                settings.DEFAULT_FROM_EMAIL,
                [address],
                fail_silently=False,
            )
        except Exception:
            logger.exception("Failed to deliver moderation notice for %s %s", kind, pk)
