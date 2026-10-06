"""Invented fixtures only. This suite does not claim private archive validation."""
import copy
import csv
import importlib.util
import io
import json
import socket
import stat
import tempfile
import unittest
import warnings
import zipfile
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('clef_archive_local', Path(__file__).resolve().parents[1] / 'scripts/clef_archive_local.py')
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def invented_roles():
    code, _ = harness.isolate(harness.ROOT / 'prayers/clef_moderation.py')
    namespace = {}
    exec(code, namespace)
    cases = []
    for index in range(1, 35):
        cases.append(dict(id=None, recovery_id=f'invented-{index}', language=None, input=f'Invented support request {index}', title=f'Invented title {index}', description=f'Invented details {index}', expected_outcome='approve', original_expected_label='invented label', category=None, provenance=dict(case_index=index, file='invented', line=index)))
    runs = []
    for line, model, count in ((149, 'clef-flash', 27), (149, 'clef', 27), (154, 'clef-flash', 27), (154, 'clef', 27), (154, 'clef', 7), (263, 'clef', 34)):
        for position in range(1, count + 1):
            row = copy.deepcopy(cases[position - 1])
            row['provenance'].update(result_line=line, tool_use_id='invented', line=count * 100 + position)
            row.update(run_id=f'invented-{line}-{model}-{count}', model=model, run_date='invented', observed_outcome=None if line != 263 else 'approve', probability_precision='printed 2 decimal places', observed_probabilities={key: 0.01 for key in harness.SCORES})
            row['observed_probabilities']['genuine'] = 0.95
            if count == 27 and model == 'clef-flash' and position <= 3:
                row['observed_probabilities'].update(genuine=0.01, spam=0.95)
            if count == 27 and model == 'clef' and position <= 2:
                row['expected_outcome'] = 'reject'
                row['observed_probabilities']['crisis'] = 0.90
            if count == 7 and position == 1:
                row['observed_probabilities'].update(genuine=0.01, spam=0.95)
            if count == 34 and position in (20, 26):
                row['expected_outcome'] = 'reject' if position == 20 else 'flag_for_review'
                row['observed_probabilities']['crisis'] = 0.90
            if position == 22:
                row['observed_probabilities']['crisis'] = 0.11 if model == 'clef-flash' else (0.54 if count == 34 else 0.53)
                # Keep the invented score pattern while exercising the safety miss.
                row['expected_outcome'] = 'escalate'
            runs.append(row)
    return dict(cases=cases, runs=runs, questions=dict(initial=namespace['QUESTIONS'], final=namespace['QUESTIONS'], final_source_session_line=185), evidence=[dict(session_line=i, timestamp='invented', block='invented') for i in range(27)], manifest=[dict(path='invented', size=0, mtime_utc='invented', sha256='invented') for _ in range(5)], report=b'Invented opaque report')


def archive_bytes(roles=None, malicious=None):
    roles = roles or invented_roles()
    output = io.BytesIO()
    table = io.StringIO()
    writer = csv.DictWriter(table, fieldnames=sorted(harness.CSV_KEYS))
    writer.writeheader()
    for row in roles['runs']:
        csv_row = {key: row[key] for key in harness.CSV_KEYS - set(harness.SCORES)}
        csv_row['provenance'] = json.dumps(row['provenance'])
        csv_row.update({key: f'{row["observed_probabilities"][key]:.2f}' for key in harness.SCORES})
        writer.writerow(csv_row)
    with zipfile.ZipFile(output, 'w') as archive:
        for role in ('cases', 'runs', 'questions', 'evidence', 'manifest'):
            archive.writestr(f'invented/{role}.json', json.dumps(roles[role]))
        archive.writestr('invented/table.csv', table.getvalue())
        if malicious is None:
            archive.writestr('invented/report.md', roles['report'])
        else:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                archive.writestr(malicious, b'invented')
    return output.getvalue()


def rewrite_member(data, suffix, transform):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, 'w') as target:
        for member in source.infolist():
            content = source.read(member)
            if member.filename.endswith(suffix):
                content = transform(content)
            target.writestr(member, content)
    return output.getvalue()


class LocalArchiveTests(unittest.TestCase):
    def parse(self, data, trusted=False):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'invented.zip'
            path.write_bytes(data)
            with harness.offline():
                return harness.read_archive(path, trusted=trusted)

    def test_invented_integration_preserves_nulls_and_precision(self):
        roles = self.parse(archive_bytes())
        before = copy.deepcopy(roles)
        with harness.offline():
            report = harness.replay(roles)
        self.assertEqual(roles, before)
        self.assertEqual(sum(row['observed_outcome'] is None for row in roles['runs']), 115)
        self.assertIsInstance(roles['runs'][0]['observed_probabilities']['crisis'], Decimal)
        self.assertEqual(roles['runs'][0]['observed_probabilities']['crisis'].as_tuple().exponent, -2)
        self.assertEqual(len(report['cohorts_count_strict_permissive']), 6)

    def test_missing_archive_sanitized(self):
        with self.assertRaisesRegex(harness.InvalidArchive, '^ARCHIVE_INVALID$'):
            with harness.offline():
                harness.read_archive('/nonexistent/invented-secret-path')

    def test_private_identity_required(self):
        with self.assertRaises(harness.InvalidArchive):
            self.parse(archive_bytes(), trusted=True)

    def test_malicious_members(self):
        for name in ('../escape', '/absolute', 'invented/cases.json', 'other/extra', 'invented\\escape'):
            with self.subTest(name=name), self.assertRaises(harness.InvalidArchive):
                self.parse(archive_bytes(malicious=name))

    def test_symlink_and_size_bounds(self):
        for kind in ('symlink', 'size'):
            output = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(archive_bytes())) as source, zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as target:
                for member in source.infolist():
                    data = source.read(member)
                    if member.filename.endswith('.md'):
                        if kind == 'symlink':
                            member.external_attr = (stat.S_IFLNK | 0o777) << 16
                        else:
                            data = b'x' * 2_000_001
                    target.writestr(member, data)
            with self.subTest(kind=kind), self.assertRaises(harness.InvalidArchive):
                self.parse(output.getvalue())

    def test_malformed_scores(self):
        good = dict.fromkeys(harness.SCORES, Decimal('0.10'))
        malformed = [None, [], {**good, 'extra': 0.1}, {k: v for k, v in good.items() if k != 'crisis'}]
        malformed += [{**good, 'crisis': value} for value in ('private invented payload', True, float('nan'), float('inf'), -0.1, 1.1, Decimal('0.111'))]
        for value in malformed:
            with self.assertRaisesRegex(harness.InvalidArchive, '^VALIDATION_FAILED$'):
                harness.scores(value)

    def test_extra_schema_and_csv_disagreement(self):
        for mutation in ('extra', 'precision', 'null', 'cohort'):
            roles = invented_roles()
            if mutation == 'extra':
                roles['cases'][0]['unknown'] = 'invented secret'
            elif mutation == 'precision':
                roles['runs'][0]['probability_precision'] = 'invented wrong precision'
            elif mutation == 'null':
                roles['runs'][0]['observed_outcome'] = 'approve'
            else:
                roles['runs'][0]['model'] = 'invented unknown model'
            with self.subTest(mutation=mutation), self.assertRaises(harness.InvalidArchive):
                self.parse(archive_bytes(roles))


    def test_mixed_cohorts_fail_replay(self):
        roles = self.parse(archive_bytes())
        roles['runs'][0]['run_id'] = 'invented mixed cohort'
        with harness.offline(), self.assertRaises(harness.InvalidArchive):
            harness.replay(roles)

    def test_csv_probability_authority(self):
        data = rewrite_member(archive_bytes(), 'table.csv', lambda data: data.replace(b'0.01', b'0.02', 1))
        with self.assertRaisesRegex(harness.InvalidArchive, '^ARCHIVE_INVALID$'):
            self.parse(data)

    def test_duplicate_json_keys_and_nonfinite_archive_scores(self):
        for replacement in (b'{"id": null, "id": null,', b'{"unknown": NaN,'):
            data = rewrite_member(
                archive_bytes(), 'cases.json',
                lambda data: data.replace(b'{"id": null,', replacement, 1),
            )
            with self.assertRaisesRegex(harness.InvalidArchive, '^ARCHIVE_INVALID$'):
                self.parse(data)
        for value in (float('nan'), float('inf'), 'invented invalid score'):
            def corrupt(data):
                rows = json.loads(data)
                rows[0]['observed_probabilities']['crisis'] = value
                return json.dumps(rows).encode()
            with self.assertRaises(harness.InvalidArchive):
                self.parse(rewrite_member(archive_bytes(), 'runs.json', corrupt))

    def test_malformed_zip_and_question_identity(self):
        with self.assertRaisesRegex(harness.InvalidArchive, '^ARCHIVE_INVALID$'):
            self.parse(b'invented malformed zip')
        roles = self.parse(archive_bytes())
        roles['questions']['final']['crisis']['instructions'] = 'Invented changed question'
        with harness.offline(), self.assertRaises(harness.InvalidArchive):
            harness.replay(roles)

    def test_network_attempts_denied(self):
        with harness.offline():
            for attempt in (lambda: socket.socket(), lambda: socket.create_connection(('localhost', 1)), lambda: socket.getaddrinfo('localhost', 1)):
                with self.assertRaisesRegex(harness.InvalidArchive, '^NETWORK_DENIED$'):
                    attempt()

    def test_cli_redacts_exception_and_requires_archive(self):
        output = io.StringIO()
        with patch('sys.argv', ['local', '--archive', 'invented-secret-path']), redirect_stdout(output):
            self.assertEqual(harness.main(), 1)
        self.assertEqual(output.getvalue(), 'PRIVATE_VALIDATION_FAILED\n')
        with patch('sys.argv', ['local']), patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
            harness.main()
        output = io.StringIO()
        with (
            patch('sys.argv', ['local', '--archive', 'invented']),
            patch.object(harness, 'read_archive', side_effect=RuntimeError('Invented private payload')),
            redirect_stdout(output),
        ):
            self.assertEqual(harness.main(), 1)
        self.assertEqual(output.getvalue(), 'PRIVATE_VALIDATION_FAILED\n')

    def test_invented_crisis_precedes_profanity_and_error(self):
        with harness.offline():
            code, _ = harness.isolate(harness.ROOT / 'prayers/clef_moderation.py')
            namespace = {}
            exec(code, namespace)
            for profane in (False, True):
                for crisis in (False, True):
                    probabilities = dict.fromkeys(harness.SCORES, Decimal('0.01'))
                    probabilities.update(genuine=Decimal('0.01'), spam=Decimal('0.95'), crisis=Decimal('0.90') if crisis else Decimal('0.01'))
                    decision = namespace['decide'](probabilities)
                    request, returned, email = harness.routing(decision, profane=profane)
                    self.assertTrue(returned['success'])
                    self.assertEqual(request.requires_human_review, crisis)
                    self.assertEqual(email.call_args.args[1], 'critical_safety_concern' if crisis else ('profanity_detected' if profane else 'llm_rejected'))
                request, returned, email = harness.routing({}, profane=profane, error=True)
                self.assertFalse(returned['success'])
                self.assertEqual(request.status, 'pending_moderation')
                self.assertTrue(request.requires_human_review)
                self.assertEqual(email.call_args.args[1], 'llm_error')


if __name__ == '__main__':
    unittest.main()
