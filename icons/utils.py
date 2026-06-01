"""Utility functions for the icons app."""
import hashlib
import os
import uuid
from io import BytesIO

from django.utils import timezone
from django.utils.text import slugify
from django.db.models import Count
import imagehash
from PIL import Image


def generate_unique_filename(instance, filename):
    """Generate a unique filename with timestamp and UUID."""
    ext = os.path.splitext(filename)[1].lower()
    base_name = os.path.splitext(filename)[0]
    slug_name = slugify(base_name)
    unique_id = str(uuid.uuid4())[:8]
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    return f"{timestamp}_{unique_id}_{slug_name}{ext}"


def icon_image_upload_path(instance, filename):
    """Generate upload path for icon images."""
    return f"icons/images/{generate_unique_filename(instance, filename)}"


def compute_image_footprints_from_file(file_obj):
    """Compute exact and perceptual hashes from an uploaded file object."""
    position = None
    if hasattr(file_obj, 'tell') and hasattr(file_obj, 'seek'):
        try:
            position = file_obj.tell()
            file_obj.seek(0)
        except (OSError, ValueError):
            position = None

    raw_bytes = file_obj.read()
    if position is not None:
        file_obj.seek(position)

    image_hash = hashlib.sha256(raw_bytes).hexdigest()
    phash = ''
    try:
        with Image.open(BytesIO(raw_bytes)) as image:
            phash = str(imagehash.phash(image))
    except Exception:
        phash = ''

    return image_hash, phash


def hamming_distance(a, b):
    """Return the bit-level Hamming distance between two hex strings."""
    if not a or not b:
        return max(len(a or ''), len(b or '')) * 4

    width = max(len(a), len(b))
    left = int(a.zfill(width), 16)
    right = int(b.zfill(width), 16)
    return (left ^ right).bit_count()


def find_exact_image_hash(image_hash):
    """Return the first icon with the exact image hash, or None."""
    if not image_hash:
        return None

    from icons.models import Icon

    return Icon.objects.filter(image_hash=image_hash).order_by('pk').first()


def find_similar_phash(phash, threshold=3):
    """Return the first icon with a perceptual hash within the threshold."""
    if not phash:
        return None

    from icons.models import Icon

    icons = Icon.objects.exclude(phash='').only('id', 'title', 'phash').order_by('pk')
    for icon in icons.iterator(chunk_size=500):
        try:
            distance = hamming_distance(icon.phash, phash)
        except ValueError:
            continue
        if distance <= threshold:
            return icon
    return None


def icon_association_count_expression():
    """Return an annotation expression for icon FK association counts."""
    return (
        Count('prayers', distinct=True)
        + Count('prayer_sets', distinct=True)
        + Count('prayer_requests', distinct=True)
        + Count('feasts', distinct=True)
    )


def count_icon_associations(icon):
    """Count icon FK references across all icon-bearing models."""
    from hub.models import Feast
    from prayers.models import Prayer, PrayerRequest, PrayerSet

    return (
        Prayer.objects.filter(icon=icon).count()
        + PrayerSet.objects.filter(icon=icon).count()
        + PrayerRequest.objects.filter(icon=icon).count()
        + Feast.objects.filter(icon=icon).count()
    )
