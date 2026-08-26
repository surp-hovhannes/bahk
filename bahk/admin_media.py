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
