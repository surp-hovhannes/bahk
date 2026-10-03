"""Church-locked staff library operations; public display serializers stay unchanged."""
import hashlib
import json
import uuid
import unicodedata

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.parsers import BaseParser
from rest_framework.response import Response
from rest_framework.views import APIView
from taggit.models import Tag

from hub.models import Church
from prayers.import_utils import _apply_translations, validate_import_json
from prayers.models import Prayer, PrayerLibraryChurchGrant, PrayerLibraryOperation, PrayerSet, PrayerSetMembership

MAX_BYTES = 5 * 1024 * 1024


class Conflict(APIException):
    status_code = 409
    default_detail = 'Library conflict; inspect current records and re-plan.'


def digest(value):
    try:
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    except (UnicodeError, RecursionError, TypeError, ValueError):
        raise ValidationError('Invalid canonical JSON payload.') from None


def positive(value):
    if type(value) is not int or value < 1:
        raise ValidationError('Expected positive integer ID/position.')
    return value


def strict(data, fields):
    if not isinstance(data, dict) or set(data) - set(fields):
        raise ValidationError('Expected object with supported fields only; use provenance sidecar.')


def snapshot(obj):
    if isinstance(obj, PrayerSet) and obj.memberships.exclude(prayer__church_id=obj.church_id).exists():
        raise Conflict('Existing membership crosses church scope; repair separately before management.')
    fields = ('title', 'description') if isinstance(obj, PrayerSet) else ('title', 'text')
    result = {'id': obj.pk, 'church_id': obj.church_id, 'category': obj.category}
    for field in fields:
        result[field] = getattr(obj, field)
        result[field + '_hy'] = (obj.i18n or {}).get(field + '_hy', '')
    if isinstance(obj, Prayer):
        result['tags'] = sorted(obj.tags.values_list('name', flat=True))
        result['affected_set_ids'] = list(obj.memberships.order_by('prayer_set_id').values_list('prayer_set_id', flat=True))
    else:
        result['prayers'] = [snapshot(member.prayer) for member in obj.memberships.select_related('prayer').prefetch_related('prayer__tags').order_by('order', 'pk')]
    return result


def revision(obj):
    return digest(snapshot(obj))


def metadata(data, is_set=False, partial=False):
    allowed = {'title', 'title_hy', 'category'} | ({'description', 'description_hy'} if is_set else {'text', 'text_hy', 'tags'})
    strict(data, allowed)
    required = set() if partial else ({'title', 'category'} if is_set else {'title', 'text', 'category'})
    if required - set(data) or (partial and not data):
        raise ValidationError('Missing required fields.')
    for key, value in data.items():
        if key == 'category':
            if not isinstance(value, str) or value not in {'morning', 'evening', 'general'}:
                raise ValidationError('Invalid category.')
        elif key == 'tags':
            if not isinstance(value, list) or any(not isinstance(tag, str) or not tag.strip() or tag != tag.strip() or len(tag) > 100 for tag in value):
                raise ValidationError('Tags must be an array of nonblank existing names.')
            if len({tag.casefold() for tag in value}) != len(value):
                raise ValidationError('Duplicate tag names.')
            existing = list(Tag.objects.select_for_update().filter(name__in=value).values_list('name', flat=True))
            if set(existing) != set(value):
                raise Conflict('Unknown tag names; discover and reuse the existing vocabulary. Tag creation is not supported.')
        else:
            if (key.endswith('_hy') or (is_set and key.startswith('description'))) and value in (None, ''):
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValidationError('Text/title fields must be nonblank strings.')
            try:
                value.encode('utf-8')
            except UnicodeError:
                raise ValidationError('Invalid Unicode.') from None
            if key.startswith('title') and len(value) > (128 if is_set else 200):
                raise ValidationError('Title exceeds field limit.')


def apply(obj, data):
    for field in ('title', 'text', 'description', 'category'):
        if field in data:
            setattr(obj, field, data[field])
    _apply_translations(obj, data, 'set' if isinstance(obj, PrayerSet) else 'prayer')
    for key, value in data.items():
        if key.endswith('_hy'):
            setattr(obj, key, value)
    obj.save()
    if 'tags' in data:
        # Pass existing Tag objects: never let taggit implicitly create a name.
        obj.tags.set(list(Tag.objects.filter(name__in=data['tags'])))
    return obj


def unique_title(model, church, title, exclude=None):
    matches = [row.pk for row in model.objects.filter(church=church).exclude(pk=exclude) if unicodedata.normalize('NFKC', row.title).strip().casefold() == unicodedata.normalize('NFKC', title).strip().casefold()]
    if matches:
        raise Conflict({'reason': 'Title conflict; default policy is error.', 'existing_ids': matches, 'kind': model._meta.model_name})


def get_record(kind, pk, church):
    return get_object_or_404((PrayerSet if kind == 'set' else Prayer).objects.select_for_update(), pk=positive(pk), church=church)


def resequence(prayer_set, ids):
    PrayerSetMembership.objects.filter(prayer_set=prayer_set).delete()
    PrayerSetMembership.objects.bulk_create([PrayerSetMembership(prayer_set=prayer_set, prayer_id=pk, order=position) for position, pk in enumerate(ids, 1)])


def run(action, payload, church, if_match, preview=False):
    strict(payload, {'record', 'id', 'prayers', 'prayer_sets', 'prayer_id', 'position', 'prayer_ids', 'affected_set_ids'})
    plan = {'action': action, 'church_id': church.pk, 'affected_ids': [], 'tags': [], 'positions': [], 'conflict_policy': 'error'}
    if action in {'prayer.import', 'set.import'}:
        root = 'prayers' if action == 'prayer.import' else 'prayer_sets'
        strict(payload, {root})
        items = payload.get(root)
        if not isinstance(items, list) or not items:
            raise ValidationError('Import requires a nonempty array.')
        sets = items if root == 'prayer_sets' else [{'title': 'Standalone validation', 'category': 'general', 'prayers': items}]
        new_titles, set_titles = set(), set()
        new_count = 0
        for item in sets:
            strict(item, {'title', 'title_hy', 'description', 'description_hy', 'category', 'prayers'})
            if root == 'prayer_sets':
                metadata({k: v for k, v in item.items() if k != 'prayers'}, True)
                unique_title(PrayerSet, church, item['title'])
                key = unicodedata.normalize('NFKC', item['title']).strip().casefold()
                if key in set_titles:
                    raise Conflict('Duplicate set titles in input.')
                set_titles.add(key)
            members = item.get('prayers')
            if not isinstance(members, list):
                raise ValidationError('Set requires a prayers array.')
            ids, ordered = set(), []
            for position, member in enumerate(members, 1):
                if isinstance(member, dict) and 'prayer_id' in member:
                    if root == 'prayers':
                        raise ValidationError('Standalone import accepts new prayers only.')
                    strict(member, {'prayer_id'})
                    obj = get_record('prayer', member['prayer_id'], church)
                    if obj.pk in ids:
                        raise Conflict('Duplicate membership reference.')
                    ids.add(obj.pk)
                    plan['affected_ids'].append(obj.pk)
                    ordered.append(obj)
                else:
                    metadata(member)
                    # Reuse existing admin validation for the new-record import contract.
                    validate_import_json({'prayer_sets': [{'title': 'Validation', 'category': 'general', 'prayers': [member]}]})
                    unique_title(Prayer, church, member['title'])
                    key = unicodedata.normalize('NFKC', member['title']).strip().casefold()
                    if key in new_titles:
                        raise Conflict('Duplicate prayer titles in input.')
                    new_titles.add(key)
                    new_count += 1
                    plan['tags'].extend(member.get('tags', []))
                    ordered.append(member)
                plan['positions'].append({'set_index': len(set_titles), 'position': position, **({'prayer_id': member['prayer_id']} if 'prayer_id' in member else {'new_record': new_count})})
            item['_ordered'] = ordered
        plan.update(new_prayers=new_count, new_sets=len(items) if root == 'prayer_sets' else 0)
        if preview:
            return plan
        created_ids, set_ids = [], []
        for item in sets:
            objects = []
            for member in item['_ordered']:
                if isinstance(member, Prayer):
                    objects.append(member)
                else:
                    obj = apply(Prayer(church=church), member)
                    created_ids.append(obj.pk)
                    objects.append(obj)
            if root == 'prayer_sets':
                obj = apply(PrayerSet(church=church), {k: v for k, v in item.items() if k not in {'prayers', '_ordered'}})
                resequence(obj, [prayer.pk for prayer in objects])
                set_ids.append(obj.pk)
        return {'created_prayer_ids': created_ids, 'reused_prayer_ids': sorted(set(plan['affected_ids'])), 'created_set_ids': set_ids, 'sets': [{'id': pk, 'revision': revision(PrayerSet.objects.get(pk=pk)), 'positions': list(PrayerSetMembership.objects.filter(prayer_set_id=pk).order_by('order').values('prayer_id', 'order'))} for pk in set_ids]}
    parts = action.split('.')
    if len(parts) != 2 or parts[0] not in {'prayer', 'set', 'members'}:
        raise ValidationError('Unknown library action.')
    kind, verb = parts
    is_set = kind in {'set', 'members'}
    model = PrayerSet if is_set else Prayer
    if verb == 'create' and kind != 'members':
        strict(payload, {'record'})
        data = payload.get('record')
        metadata(data, is_set)
        unique_title(model, church, data['title'])
        if preview:
            return {**plan, 'new_sets': int(is_set), 'new_prayers': int(not is_set), 'tags': data.get('tags', [])}
        obj = apply(model(church=church), data)
        return {'id': obj.pk, 'revision': revision(obj)}
    obj = get_record('set' if is_set else 'prayer', payload.get('id'), church)
    current = revision(obj)
    plan.update(affected_ids=[obj.pk], revision=current)
    if if_match != current:
        raise Conflict('Missing or stale revision; read current record and re-plan.')
    if verb == 'update' and kind != 'members':
        strict(payload, {'id', 'record'})
        data = payload.get('record')
        metadata(data, is_set, partial=True)
        if 'title' in data:
            unique_title(model, church, data['title'], obj.pk)
        if preview:
            return {**plan, 'changed_fields': sorted(data), 'tags': data.get('tags', [])}
        apply(obj, data)
    elif verb == 'delete' and kind != 'members':
        strict(payload, {'id', 'affected_set_ids'} if not is_set else {'id'})
        if not is_set:
            if obj.memberships.exclude(prayer_set__church_id=church.pk).exists():
                raise Conflict('Deletion would affect a set outside the selected church; repair separately.')
            acknowledged = payload.get('affected_set_ids', [])
            if not isinstance(acknowledged, list):
                raise ValidationError('affected_set_ids must be an array of positive integer IDs.')
            for pk in acknowledged:
                positive(pk)
            if len(set(acknowledged)) != len(acknowledged):
                raise ValidationError('Duplicate affected-set IDs.')
            affected = list(obj.memberships.order_by('prayer_set_id').values_list('prayer_set_id', flat=True))
            plan['affected_set_ids'] = affected
            if not preview and payload.get('affected_set_ids', []) != affected:
                raise Conflict('Prayer deletion requires exact affected_set_ids acknowledgement.')
        if preview:
            return plan
        affected = [] if is_set else list(obj.prayer_sets.all())
        obj.delete()
        for prayer_set in affected:
            resequence(prayer_set, list(prayer_set.memberships.order_by('order', 'pk').values_list('prayer_id', flat=True)))
        return {'deleted_id': payload['id'], 'affected_set_ids': [row.pk for row in affected]}
    elif kind == 'members' and verb in {'add', 'remove', 'reorder'}:
        ids = list(obj.memberships.order_by('order', 'pk').values_list('prayer_id', flat=True))
        if verb == 'reorder':
            strict(payload, {'id', 'prayer_ids'})
            proposed = payload.get('prayer_ids')
            if not isinstance(proposed, list):
                raise ValidationError('Expected prayer_ids array.')
            for pk in proposed:
                positive(pk)
            if len(set(proposed)) != len(proposed) or set(proposed) != set(ids):
                raise Conflict('Reorder must be an exact permutation of current membership.')
            ids = proposed
        else:
            strict(payload, {'id', 'prayer_id', 'position'} if verb == 'add' else {'id', 'prayer_id'})
            member = get_record('prayer', payload.get('prayer_id'), church)
            if verb == 'add':
                position = positive(payload.get('position'))
                if member.pk in ids or position > len(ids) + 1:
                    raise Conflict('Already a member or position outside 1..N+1.')
                ids.insert(position - 1, member.pk)
            else:
                if member.pk not in ids:
                    raise Conflict('Prayer is not a member.')
                ids.remove(member.pk)
        plan['positions'] = [{'prayer_id': pk, 'order': index} for index, pk in enumerate(ids, 1)]
        if preview:
            return plan
        resequence(obj, ids)
    else:
        raise ValidationError('Unknown library action.')
    return {'id': obj.pk, 'revision': revision(obj), 'positions': plan['positions']}


class StrictJSONParser(BaseParser):
    media_type = 'application/json'

    def parse(self, stream, media_type=None, parser_context=None):
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        def constant(value):
            raise ValueError

        raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValidationError('Request exceeds 5 MiB.')
        try:
            return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
        except (ValueError, UnicodeError, RecursionError):
            raise ValidationError('Invalid strict UTF-8 JSON.') from None


class StaffLibraryView(APIView):
    parser_classes = [StrictJSONParser]
    permission_classes = [IsAdminUser]

    def church(self, request, church_id):
        church = get_object_or_404(Church, pk=church_id)
        if not request.user.is_superuser and not PrayerLibraryChurchGrant.objects.filter(user=request.user, church=church).exists():
            raise PermissionDenied('An explicit staff prayer-library church grant is required.')
        return church

    def get(self, request, church_id):
        church = self.church(request, church_id)
        if 'operation_key' in request.query_params:
            receipt = get_object_or_404(PrayerLibraryOperation, church=church, user=request.user, key=request.query_params['operation_key'])
            return Response({'operation_key': receipt.key, 'digest': receipt.digest, 'status': 'completed', 'result': receipt.result})
        kind = request.query_params.get('kind', 'prayer')
        if kind not in {'prayer', 'set'}:
            raise ValidationError('kind must be prayer or set.')
        model = PrayerSet if kind == 'set' else Prayer
        if 'id' in request.query_params:
            try:
                pk = int(request.query_params['id'])
            except ValueError:
                raise ValidationError('Invalid ID.') from None
            with transaction.atomic():
                Church.objects.select_for_update().get(pk=church.pk)
                obj = get_object_or_404(model.objects.select_for_update(), pk=pk, church=church)
                record = snapshot(obj)
                return Response({'record': record, 'revision': digest(record)})
        return Response([{'id': obj.pk, 'title': obj.title, 'revision': revision(obj)} for obj in model.objects.filter(church=church).order_by('pk')])

    def post(self, request, church_id):
        church = self.church(request, church_id)
        strict(request.data, {'action', 'payload', 'if_match', 'preview', 'operation_key', 'digest'})
        action = request.data.get('action')
        if not isinstance(action, str):
            raise ValidationError('Action required.')
        payload = request.data.get('payload')
        preview = request.data.get('preview', True)
        if type(preview) is not bool:
            raise ValidationError('preview must be boolean.')
        fingerprint = digest({'action': action, 'payload': payload, 'if_match': request.data.get('if_match')})
        key = request.data.get('operation_key')
        if not preview:
            try:
                if not isinstance(key, str) or str(uuid.UUID(key)) != key:
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                raise ValidationError('Canonical UUID operation_key required.') from None
            if request.data.get('digest') != fingerprint:
                raise ValidationError('Payload digest mismatch.')
        with transaction.atomic():
            church = Church.objects.select_for_update().get(pk=church.pk)
            if not request.user.is_superuser:
                if not PrayerLibraryChurchGrant.objects.select_for_update().filter(user=request.user, church=church).exists():
                    raise PermissionDenied("Church grant was revoked; no write performed.")
            if not preview:
                receipt = PrayerLibraryOperation.objects.filter(church=church, key=key).first()
                if receipt:
                    if receipt.user_id != request.user.pk or receipt.digest != fingerprint:
                        raise Conflict('Operation key is already bound to another request.')
                    return Response({'operation_key': key, 'digest': fingerprint, 'status': 'completed', 'result': receipt.result})
            try:
                result = run(action, payload, church, request.data.get('if_match'), preview)
            except (Conflict, ValidationError) as exc:
                if not preview:
                    raise
                return Response({'preview': True, 'account_id': request.user.pk, 'plan': {'blocked': True, 'church_id': church.pk, 'reason_code': 'conflict' if isinstance(exc, Conflict) else 'invalid_input', 'existing_ids': [int(pk) for pk in exc.detail.get('existing_ids', [])] if isinstance(exc.detail, dict) else []}})
            if preview:
                return Response({'preview': True, 'account_id': request.user.pk, 'plan': result})
            PrayerLibraryOperation.objects.create(church=church, user=request.user, key=key, digest=fingerprint, result=result)
        return Response({'operation_key': key, 'digest': fingerprint, 'status': 'completed', 'result': result})
