"""Local private archive verification. No extraction, providers, or fixture publication.

CLI output is deliberately aggregate-only. AST replay is offline smoke, not Django proof.
"""
import _socket
import argparse
import ast
import csv
import hashlib
import io
import json
import socket
import stat
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from contextlib import ExitStack, contextmanager
from decimal import Decimal
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import Mock, patch

SCORES = ('genuine', 'crisis', 'spam', 'inappropriate', 'incoherent', 'private_info')
CASE_KEYS = set(
    'id recovery_id language input title description expected_outcome '
    'original_expected_label category provenance'.split()
)
RUN_KEYS = CASE_KEYS | set(
    'run_id model run_date observed_probabilities observed_outcome probability_precision'.split()
)
CSV_KEYS = (RUN_KEYS - {'title', 'description', 'observed_probabilities'}) | set(SCORES)
LIST_SCHEMAS = {
    'cases': CASE_KEYS,
    'runs': RUN_KEYS,
    'evidence': {'session_line', 'timestamp', 'block'},
    'manifest': {'path', 'size', 'mtime_utc', 'sha256'},
}
EXPECTED_SHA = '32c34c1483ee69a02ae2db3465652301d1b9a7ab4922b682e8e66edf8d362dea'
ROOT = Path(__file__).resolve().parents[1]


class InvalidArchive(Exception):
    """Constant error with no payload or source path."""


def require(condition):
    if not condition:
        raise InvalidArchive('VALIDATION_FAILED')


@contextmanager
def offline():
    def denied(*args, **kwargs):
        raise InvalidArchive('NETWORK_DENIED')
    with ExitStack() as guards:
        for module in (socket, _socket):
            for name in ('socket', 'getaddrinfo', 'gethostbyname', 'gethostbyname_ex', 'gethostbyaddr'):
                guards.enter_context(patch.object(module, name, denied))
        guards.enter_context(patch.object(socket, 'SocketType', denied))
        guards.enter_context(patch.object(socket, 'create_connection', denied))
        try:
            socket.socket()
        except InvalidArchive:
            pass
        else:
            raise InvalidArchive('NETWORK_GUARD_FAILED')
        yield


def scores(value):
    require(isinstance(value, dict) and set(value) == set(SCORES))
    for number in value.values():
        require(isinstance(number, (Decimal, int, float)) and not isinstance(number, bool))
        number = Decimal(str(number))
        require(number.is_finite() and 0 <= number <= 1 and number == number.quantize(Decimal('0.01')))
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def schema_matches(row, role):
    if not isinstance(row, dict):
        return False
    if set(row) == LIST_SCHEMAS[role]:
        return True
    # This optional metadata field exists only on some run records.
    return (
        role == 'runs'
        and set(row) == RUN_KEYS | {'script_pass'}
        and isinstance(row['script_pass'], bool)
    )


@offline()
def read_archive(path, trusted=True):
    """Return private objects in memory only; discover roles from exact schemas."""
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(1_000_001)
        require(len(raw) <= 1_000_000)
        if trusted:
            require(len(raw) == 36151 and hashlib.sha256(raw).hexdigest() == EXPECTED_SHA)
        roles = {}
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            require(len(members) == 7)
            names = set()
            roots = set()
            total = 0
            for member in members:
                name = PurePosixPath(member.filename)
                require(not name.is_absolute() and len(name.parts) >= 2 and '..' not in name.parts)
                require('\\' not in member.filename and ':' not in member.filename and member.filename not in names)
                require(member.filename == name.as_posix())
                require(not stat.S_ISLNK(member.external_attr >> 16) and not member.is_dir())
                require(not member.flag_bits & 1)
                names.add(member.filename)
                roots.add(name.parts[0])
                total += member.file_size
                require(member.file_size <= 2_000_000 and total <= 5_000_000)
                # Markdown is opaque: never decode or parse report prose.
                if name.suffix == '.md':
                    role, value = 'report', archive.read(member)
                else:
                    data = archive.read(member).decode('utf-8')
                    if name.suffix == '.csv':
                        reader = csv.DictReader(io.StringIO(data))
                        require(reader.fieldnames is not None and len(reader.fieldnames) == len(CSV_KEYS) and set(reader.fieldnames) == CSV_KEYS)
                        value = list(reader)
                        role = 'csv'
                    else:
                        require(name.suffix == '.json')
                        value = json.loads(data, parse_float=Decimal, parse_constant=lambda _: require(False), object_pairs_hook=unique_object)
                        if isinstance(value, dict) and set(value) == {'initial', 'final', 'final_source_session_line'}:
                            role = 'questions'
                        else:
                            require(isinstance(value, list) and value and isinstance(value[0], dict))
                            role = next((key for key in LIST_SCHEMAS if schema_matches(value[0], key)), None)
                            require(role is not None)
                            require(all(schema_matches(row, role) for row in value))
                require(role not in roles)
                roles[role] = value
            require(len(roots) == 1 and set(roles) == {'report', 'csv', 'questions', 'cases', 'runs', 'evidence', 'manifest'})
        validate(roles)
        return roles
    except Exception:
        raise InvalidArchive('ARCHIVE_INVALID') from None


def validate(roles):
    cases, runs, table = roles['cases'], roles['runs'], roles['csv']
    require(len(cases) == 34 and len(runs) == 149 and len(table) == 149)
    require(len(roles['evidence']) == 27 and len(roles['manifest']) == 5)
    require(type(roles['questions']['final_source_session_line']) is int)
    actions = {'approve', 'reject', 'escalate', 'flag_for_review'}
    for case in cases:
        require(all(case[key] is None for key in ('id', 'language', 'category')))
        require(all(isinstance(case[key], str) for key in ('input', 'title', 'description', 'expected_outcome', 'original_expected_label')))
        require(isinstance(case['provenance'], dict) and set(case['provenance']) == {'case_index', 'file', 'line'})
    require(Counter(row['model'] for row in runs) == {'clef': 95, 'clef-flash': 54})
    require(sum(row['observed_outcome'] is None for row in runs) == 115)
    for question in ('initial', 'final'):
        require(isinstance(roles['questions'][question], dict) and set(roles['questions'][question]) == set(SCORES))
        for value in roles['questions'][question].values():
            require(isinstance(value, dict) and set(value) == {'type', 'instructions'})
            require(value['type'] == 'noul' and isinstance(value['instructions'], str))
    for row, csv_row in zip(runs, table):
        require(set(csv_row) == CSV_KEYS and all(value is not None for value in csv_row.values()))
        require(all(isinstance(row[key], str) for key in ('input', 'title', 'description', 'original_expected_label', 'recovery_id', 'run_id')))
        require(isinstance(row['provenance'], dict))
        require(row['probability_precision'] == 'printed 2 decimal places')
        require(all(row[key] is None for key in ('id', 'language', 'category')))
        require(row['expected_outcome'] in actions)
        require(row['observed_outcome'] is None or row['observed_outcome'] in actions)
        scores(row['observed_probabilities'])
        for key in CSV_KEYS - set(SCORES) - {'provenance'}:
            require(csv_row[key] == ('' if row[key] is None else str(row[key])))
        for key in SCORES:
            require(Decimal(csv_row[key]) == Decimal(str(row['observed_probabilities'][key])))
        require(csv_row['provenance'] == row['provenance'] or json.loads(csv_row['provenance']) == row['provenance'])


def isolate(path, functions=None):
    source = path.read_bytes()
    tree = ast.parse(source)
    body = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and functions is None:
            body.append(node)
        elif isinstance(node, ast.FunctionDef) and (functions is None or node.name in functions):
            node.decorator_list = []
            body.append(node)
    return compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), '<offline-smoke>', 'exec'), hashlib.sha256(source).hexdigest()


def routing(
    result, title='Invented request', description='Invented details',
    profane=False, error=False, model='clef',
):
    """Execute real task body against invented/memory-only objects and mocks."""
    code, _ = isolate(ROOT / 'prayers/tasks.py', {'moderate_prayer_request_task'})
    request = SimpleNamespace(
        id=1, title=title, description=description, reviewed=False,
        status='pending_moderation', save=Mock(), requester=Mock(), is_anonymous=False,
    )
    request.requester.prayer_requests.filter.return_value.count.return_value = 2
    provider = Mock(return_value=result, side_effect=RuntimeError('SYNTHETIC_ERROR') if error else None)
    email = Mock()
    namespace = {
        'PrayerRequest': SimpleNamespace(
            objects=SimpleNamespace(get=Mock(return_value=request)),
            DoesNotExist=type('Missing', (Exception,), {}),
        ),
        'profanity': SimpleNamespace(contains_profanity=Mock(side_effect=[profane, False])),
        'settings': SimpleNamespace(PRAYER_MODERATION_ENGINE='clef', PRAYER_MODERATION_CLEF_MODEL=model),
        'clef_moderation_result': provider,
        '_send_moderation_alert_email': email,
        'timezone': SimpleNamespace(now=lambda: None),
        'logger': Mock(),
        'Event': Mock(),
        'EventType': Mock(),
        'UserMilestone': Mock(),
    }
    acceptance = SimpleNamespace(PrayerRequestAcceptance=Mock())
    with patch.dict(sys.modules, {'prayers.models': acceptance}):
        exec(code, namespace)
        returned = namespace['moderate_prayer_request_task'](Mock(), 1)
    require(provider.call_count == 1 and request.save.called)
    require(provider.call_args == ((request,), {'model': model}))
    require(returned.get('success') is (not error))
    if result.get('suggested_action') == 'escalate' and not error:
        require(request.status == 'rejected' and request.requires_human_review and request.moderation_severity == 'critical')
        require(email.call_args == ((request, 'critical_safety_concern'), {}))
    elif not error and not profane:
        action = result.get('suggested_action')
        statuses = {'approve': 'approved', 'reject': 'rejected', 'flag_for_review': 'pending_moderation'}
        require(request.status == statuses[action])
        require(request.requires_human_review == (action == 'flag_for_review'))
    return request, returned, email


@offline()
def replay(roles):
    code, source_sha = isolate(ROOT / 'prayers/clef_moderation.py')
    namespace = {}
    exec(code, namespace)
    require(namespace['QUESTIONS'] == roles['questions']['final'])
    groups = defaultdict(list)
    mismatches = []
    for row in roles['runs']:
        # Explicit run identity remains private and is used only for grouping.
        require(set(row['provenance']) == {'case_index', 'file', 'line', 'result_line', 'tool_use_id'})
        index = row['provenance']['case_index']
        require(type(index) is int and 1 <= index <= 34)
        line = row['provenance']['result_line']
        require(line in {149, 154, 263})
        require((row['observed_outcome'] is None) == (line != 263))
        groups[(line, row['run_id'], row['model'])].append(row)
    aggregates = []
    for (line, _, model), rows in groups.items():
        require(len({(row['provenance']['file'], row['provenance']['line'], row['provenance']['case_index']) for row in rows}) == len(rows))
        strict = permissive = 0
        for position, row in enumerate(rows, 1):
            decision = namespace['decide'](row['observed_probabilities'])
            actual, expected = decision['suggested_action'], row['expected_outcome']
            strict += actual == expected
            permissive += actual == expected or (actual == 'escalate' and expected in {'reject', 'flag_for_review'})
            routing(
                {**decision, 'probabilities': row['observed_probabilities'], 'engine': 'clef', 'model': model},
                row['title'], row['description'], model=model,
            )
            if len(rows) == 34 and actual != expected:
                mismatches.append(position)
        aggregates.append((line, model, len(rows), strict, permissive))
    expected = Counter([
        (149, 'clef-flash', 27, 23, 23), (154, 'clef-flash', 27, 23, 23),
        (149, 'clef', 27, 25, 27), (154, 'clef', 27, 25, 27),
        (154, 'clef', 7, 6, 6), (263, 'clef', 34, 32, 34),
    ])
    require(Counter(aggregates) == expected and sorted(mismatches) == [20, 26])
    final = next(rows for rows in groups.values() if len(rows) == 34)
    require(154 < roles['questions']['final_source_session_line'] < 263)
    require(final[19]['expected_outcome'] == 'reject')
    require(final[25]['expected_outcome'] == 'flag_for_review')
    require([row['observed_outcome'] for row in roles['runs'] if row['observed_outcome'] is not None] == [row['observed_outcome'] for row in final])
    case22 = [row for row in roles['runs'] if row['provenance']['case_index'] == 22]
    require(Decimal('0.11') in [row['observed_probabilities']['crisis'] for row in case22 if row['model'] == 'clef-flash'])
    require({Decimal('0.53'), Decimal('0.54')} <= {row['observed_probabilities']['crisis'] for row in case22 if row['model'] == 'clef'})
    for row in case22:
        require(row['expected_outcome'] == 'escalate')
        action = namespace['decide'](row['observed_probabilities'])['suggested_action']
        require((action == 'escalate') == (row['model'] == 'clef'))
    return {
        'offline_smoke_source_sha256': source_sha,
        'task_source_sha256': hashlib.sha256((ROOT / 'prayers/tasks.py').read_bytes()).hexdigest(),
        'question_set_sha256': {
            key: hashlib.sha256(json.dumps(roles['questions'][key], sort_keys=True).encode()).hexdigest()
            for key in ('initial', 'final')
        },
        'historical_question_sets': {
            'line-149': 'initial', 'line-154': 'initial', 'synthetic34-final': 'final',
        },
        'current_question_set': 'final',
        'cohorts_count_strict_permissive': sorted(aggregates),
        'strict_mismatch_positions': ['case-20', 'case-26'],
        'case22_safety_miss_preserved': True,
    }


def audit(roles):
    """Count exact-string overlaps and reject new private strings.

    Short labels/titles can already occur as ordinary repository vocabulary.
    Compare with HEAD in memory so these overlaps are counted, not concealed.
    """
    secrets = {
        row[key].encode()
        for row in roles['cases'] + roles['runs']
        for key in ('input', 'title', 'description', 'original_expected_label')
        if row[key]
    }
    baseline = set()
    entries = subprocess.check_output(['git', 'ls-tree', '-r', '-z', 'HEAD'], cwd=ROOT).split(b'\0')
    object_ids = [entry.split(b'\t')[0].split()[2] for entry in entries if entry and entry.split()[1] == b'blob']
    process = subprocess.run(['git', 'cat-file', '--batch'], input=b'\n'.join(object_ids) + b'\n', stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, check=True)
    stream = io.BytesIO(process.stdout)
    for _ in object_ids:
        header = stream.readline().split()
        data = stream.read(int(header[2]))
        stream.read(1)
        baseline.update(secret for secret in secrets if secret in data)
    paths = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).split(b'\0')
    overlaps = introduced = 0
    for name in paths:
        if name:
            path = ROOT / name.decode()
            if path.is_file():
                data = path.read_bytes()
                overlaps += sum(secret in data for secret in secrets)
                introduced += sum(secret in data for secret in secrets - baseline)
    require(introduced == 0)
    return {
        'source_audit_overlap_count': overlaps,
        'source_audit_new_private_string_count': introduced,
        'source_audit_pass': True,
    }


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'ARGUMENTS_INVALID\n')


def main():
    parser = SafeParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    args = parser.parse_args()
    try:
        with offline():
            roles = read_archive(args.archive)
            result = {
                'historical': {
                    'rows': 149, 'cases': 34, 'null_decisions': 115, 'recorded_decisions': 34,
                    'models': {'clef': 95, 'clef-flash': 54},
                    'recorded_cohort': {'position': 263, 'model': 'clef', 'questions': 'final', 'count': 34},
                    'preliminary_decisions_remain_null': True,
                },
                'current_offline_smoke': replay(roles),
                'network_denied': True,
                **audit(roles),
            }
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print('PRIVATE_VALIDATION_FAILED')
        return 1


if __name__ == '__main__':
    sys.exit(main())
