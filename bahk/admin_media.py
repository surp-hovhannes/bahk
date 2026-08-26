"""Reusable, storage-safe media rendering for Django admin pages."""

from collections.abc import Iterable
from typing import Any

from django.utils.html import format_html


_MEDIA_EXCEPTIONS = (AttributeError, OSError, ValueError)
_THUMBNAIL_SIZES = {
    "small": (56, 56),
    "content": (84, 56),
    "portrait": (42, 64),
}


def _resolve_media_url(obj: Any, source: str | None) -> str:
    """Resolve a dotted attribute path to a URL without generating or saving media."""
    if not obj or not source:
        return ""

    value = obj
    try:
        for part in source.split("."):
            value = getattr(value, part)
        if not value:
            return ""
        if isinstance(value, str):
            return value
        raw_value = str(value)
        if raw_value.startswith(("https://", "http://")):
            return raw_value
        return value.url
    except _MEDIA_EXCEPTIONS:
        return ""


def admin_thumbnail(
    obj: Any,
    *,
    sources: Iterable[str],
    alt: str,
    size: str = "content",
    fallback: str = "No image",
    link_source: str | None = None,
):
    """Render the first available media source as an accessible admin thumbnail."""
    if size not in _THUMBNAIL_SIZES:
        raise ValueError(f"Unsupported admin thumbnail size: {size}")

    preview_url = next(
        (url for source in sources if (url := _resolve_media_url(obj, source))),
        "",
    )
    if not preview_url:
        return fallback

    width, height = _THUMBNAIL_SIZES[size]
    image = format_html(
        '<img class="fp-admin-thumbnail fp-admin-thumbnail--{}" '
        'src="{}" alt="{}" loading="lazy" width="{}" height="{}">',
        size,
        preview_url,
        alt,
        width,
        height,
    )

    link_url = _resolve_media_url(obj, link_source) if link_source else preview_url
    if not link_url:
        return image
    return format_html(
        '<a class="fp-admin-thumbnail__link" href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
        link_url,
        image,
    )


def admin_video_player(
    obj: Any,
    *,
    source: str,
    title: str,
    poster_sources: Iterable[str] = (),
    fallback: str = "No video",
):
    """Render a storage-safe HTML video player for an admin change form."""
    video_url = _resolve_media_url(obj, source)
    if not video_url:
        return fallback

    poster_url = next(
        (url for poster_source in poster_sources if (url := _resolve_media_url(obj, poster_source))),
        "",
    )
    return format_html(
        '<div class="fp-admin-video">'
        '<video class="fp-admin-video__player" controls preload="metadata" playsinline '
        'poster="{}" aria-label="Preview: {}">'
        '<source src="{}">'
        'Your browser does not support embedded video playback.'
        '</video>'
        '<a class="fp-admin-video__link" href="{}" target="_blank" '
        'rel="noopener noreferrer">Open video in a new tab</a>'
        '</div>',
        poster_url,
        title,
        video_url,
        video_url,
    )
