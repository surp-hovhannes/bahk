"""Frozen control contracts and evaluation-only Luna semantics, entirely synthetic."""

import hashlib
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, override_settings

from hub.services.icon_matching import IconMatchRequest
from hub.services.icon_match_profiles import CONTROL, LUNA, LUNA_V2, LUNA_EXAMPLES, LUNA_V2_EXAMPLES, luna_rank
from hub.services.icon_match_service import (
    ANALYSIS,
    BATCH,
    STAGE_PROMPTS,
    WIRE_MATCH,
    MatchLimits,
    OpenAIIconProvider,
    _analysis,
    _matches,
    _rank,
    match_icons,
    normalize_provider_matches,
    serialize_catalogue,
    validate_schema,
    wire_schema,
)
from hub.tests.icon_match_fixtures import FixtureProvider, analysis_for, candidate


class ProfileTests(SimpleTestCase):
    def test_frozen_profiles_and_v2_prompt_only_contract(self):
        baseline = json.loads(Path(__file__).with_name("icon_match_profile_baseline.json").read_text())
        before = {p.id: self.event_case(p) for p in (CONTROL, LUNA)}
        self.event_case(LUNA_V2)
        LUNA_V2.metadata()
        for profile in (CONTROL, LUNA):
            self.assertEqual(profile.metadata(), baseline[profile.id])
            result, provider = self.event_case(profile)
            old_result, old_provider = before[profile.id]
            self.assertEqual(result.matches, old_result.matches)
            self.assertEqual([(s, p) for s, p, _ in provider.calls], [(s, p) for s, p, _ in old_provider.calls])
        v1, v2 = LUNA.metadata(), LUNA_V2.metadata()
        self.assertEqual(
            {
                k: v
                for k, v in v1.items()
                if k not in ("profile_id", "prompt_version", "prompt_hash", "stage_prompt_hashes", "profile_hash")
            },
            {
                k: v
                for k, v in v2.items()
                if k not in ("profile_id", "prompt_version", "prompt_hash", "stage_prompt_hashes", "profile_hash")
            },
        )
        self.assertNotEqual(v1["prompt_hash"], v2["prompt_hash"])
        self.assertIs(LUNA_V2.rank_function, LUNA.rank_function)
        self.assertEqual(LUNA.stage_prompts["analyze"], LUNA_V2.stage_prompts["analyze"])
        with self.assertRaises(TypeError):
            LUNA_V2.stage_prompts["assess"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            LUNA_V2.positive_limit = 8

    def test_v2_examples_validate_envelopes_evidence_and_assignment_contracts(self):
        expected = ["subject_portrait", "related_specific", None, None, "exact_event", "related_specific", "thematic"]
        for encoded, relation in zip(LUNA_V2_EXAMPLES, expected, strict=True):
            example = json.loads(encoded)
            with self.subTest(request=example["request"]):
                analysis = example["analysis"]
                _analysis(analysis, example["request"]["sources"])
                validate_schema(example["assessment"], wire_schema(BATCH))
                result = normalize_provider_matches(example["assessment"], BATCH, example["catalogue"])
                _matches(result["matches"], serialize_catalogue(example["catalogue"]), analysis)
                matches = example["assessment"]["matches"]
                self.assertEqual([m["relation"] for m in matches], [] if relation is None else [relation])
                outcome = match_icons(
                    example["catalogue"],
                    IconMatchRequest(
                        kind="feast",
                        primary_text=example["request"]["sources"]["request:primary"],
                        auto_assign_policy="feast_strict",
                    ),
                    provider=FixtureProvider(analysis, result["matches"]),
                    profile=LUNA_V2,
                )
                self.assertEqual(outcome.status, "complete")
                if relation is None:
                    self.assertEqual(outcome.matches, [])
                    self.assertFalse(example["assessment"]["exact_event_exists"])
                elif relation == "related_specific":
                    self.assertFalse(matches[0]["full_request_coverage"])
                    self.assertFalse(outcome.matches[0]["auto_assignable"])
                elif relation in ("exact_event", "subject_portrait"):
                    self.assertTrue(outcome.matches[0]["auto_assignable"])
        partial = json.loads(LUNA_V2_EXAMPLES[1])
        self.assertEqual(len(partial["analysis"]["subjects"]), 2)
        self.assertEqual(partial["assessment"]["matches"][0]["covered_subjects"], [0])
        self.assertEqual(
            partial["assessment"]["matches"][0]["evidence"],
            [{"source": "title", "quote": "Sere", "role": "identity", "subject_indices": [0]}],
        )
        # Both decision stages see complete examples and exclusions before the rank instructions.
        for stage in ("assess", "verify"):
            prompt = LUNA_V2.stage_prompts[stage]
            self.assertLess(prompt.index("EXCLUDE BEFORE RANKING"), prompt.index("For event requests rank"))
            for example in LUNA_V2_EXAMPLES:
                self.assertIn(example, prompt)
            for example in LUNA_EXAMPLES:
                self.assertNotIn(example, prompt)

    def test_frozen_control_prompts_limits_and_rank_matrix(self):
        self.assertEqual(
            {k: hashlib.sha256(v.encode()).hexdigest() for k, v in STAGE_PROMPTS.items()},
            {
                "analyze": "23bf9d887465c4b90cc9e42ac27c9b6a1c78952c30a9cf70d9415e336e47ba0a",
                "assess": "fd32c2619024cff3d847c825af08474f5c05875c998aefe25921acae0f888d86",
                "verify": "ce40741f12bd30bf7dce45f84efb516bf8fd05311d6d39b338b68345dfaf48a3",
            },
        )
        self.assertEqual(dict(CONTROL.stage_prompts), STAGE_PROMPTS)
        self.assertIs(CONTROL.rank_function, _rank)
        self.assertEqual(
            asdict(MatchLimits()),
            dict(
                batch_records=512,
                batch_bytes=160000,
                max_batches=4,
                positive_limit=8,
                verification_limit=24,
                verification_bytes=160000,
                call_seconds=45,
                total_seconds=180,
            ),
        )
        matrix = [
            candidate(1, "Vela", "thematic", relevance=100),
            candidate(2, "Vela", "related_specific", relevance=100),
            candidate(3, "Vela", "subject_portrait", confidence="low", relevance=1),
            candidate(4, "Vela", "exact_subject", confidence="low", relevance=1),
            candidate(5, "Vela", "exact_event", confidence="low", relevance=1),
            candidate(6, "Vela", "exact_subject", covered_subjects=[0, 1], confidence="low"),
            candidate(7, "Vela", "exact_subject", confidence="high", relevance=5),
            candidate(8, "Vela", "exact_subject", confidence="high", relevance=6),
            candidate(9, "Vela", "exact_subject", confidence="high", relevance=6),
        ]
        self.assertEqual([m["id"] for m in sorted(matrix, key=_rank)], [5, 6, 8, 9, 7, 4, 3, 2, 1])
        for match in matrix:
            self.assertEqual(luna_rank(match, analysis_for("Vela")), _rank(match))
        with self.assertRaises(TypeError):
            LUNA.stage_prompts["analyze"] = "contamination"
        with self.assertRaises(FrozenInstanceError):
            LUNA.positive_limit = 20

    def test_full_luna_examples_validate_source_and_index_contract(self):
        for encoded in LUNA_EXAMPLES:
            example = json.loads(encoded)
            analysis = example["analysis"]
            _analysis(analysis, example["request"]["sources"])
            validate_schema(example["match"], WIRE_MATCH)
            envelope = {
                "batch_id": "example",
                "assessment_complete": True,
                "matches": [example["match"]],
                "exact_event_exists": example["match"]["relation"] == "exact_event",
            }
            result = normalize_provider_matches(envelope, BATCH, example["catalogue"])
            _matches(result["matches"], serialize_catalogue(example["catalogue"]), analysis)
            if not analysis["subjects"]:
                self.assertEqual(example["match"]["covered_subjects"], [])
                self.assertTrue(
                    all(e["subject_indices"] == [] and e["role"] != "identity" for e in example["match"]["evidence"])
                )
        self.assertEqual(json.loads(LUNA_EXAMPLES[0])["analysis"]["context"], [])
        self.assertEqual(json.loads(LUNA_EXAMPLES[1])["match"]["covered_subjects"], [0, 1])
        for stage in ("assess", "verify"):
            self.assertIn("CONFLICT-BEFORE-PORTRAIT AUDIT", LUNA.stage_prompts[stage])
            self.assertIn("ALL original title and tags", LUNA.stage_prompts[stage])
        self.assertIn("MAXIMUM, never a quota", LUNA.stage_prompts["assess"])
        self.assertIn("WHOLE", LUNA.stage_prompts["assess"])

    def test_luna_event_only_and_composite_examples_use_shared_assignment_gates(self):
        for index in (0, 1, 2):
            example = json.loads(LUNA_EXAMPLES[index])
            envelope = {
                "batch_id": "fixture",
                "assessment_complete": True,
                "matches": [example["match"]],
                "exact_event_exists": bool(example["analysis"]["event"]),
            }
            matches = normalize_provider_matches(envelope, BATCH, example["catalogue"])["matches"]
            provider = FixtureProvider(example["analysis"], matches)
            outcome = match_icons(
                example["catalogue"],
                IconMatchRequest(
                    kind="feast",
                    primary_text=example["request"]["sources"]["request:primary"],
                    auto_assign_policy="feast_strict",
                ),
                provider=provider,
                profile=LUNA,
            )
            self.assertEqual(outcome.status, "complete")
            self.assertTrue(outcome.matches[0]["auto_assignable"])
            self.assertEqual(outcome.matches[0]["covered_subjects"], list(range(len(example["analysis"]["subjects"]))))

    def test_luna_invalid_candidate_quotes_and_verifier_constraint_loss_stay_failclosed(self):
        analysis = analysis_for("Vela")
        analysis["subjects"][0]["qualifiers"] = [{"ref": "request:primary", "quote": "of the Ridge"}]
        for failure in ("quote", "qualifier"):

            def mutate(stage, payload, result):
                if stage == "assess" and failure == "quote":
                    result["matches"][0]["evidence"][0]["quote"] = "invented label"
                if stage == "verify" and failure == "qualifier":
                    result["analysis"]["subjects"][0]["qualifiers"] = []

            provider = FixtureProvider(analysis, [candidate(1, "Vela of the Ridge")], mutate)
            outcome = match_icons(
                [{"id": 1, "title": "Vela of the Ridge", "tags": []}],
                IconMatchRequest(kind="feast", primary_text="Vela of the Ridge", auto_assign_policy="feast_strict"),
                provider=provider,
                profile=LUNA,
            )
            self.assertTrue(all(not m["auto_assignable"] for m in outcome.matches))
            if failure == "quote":
                self.assertEqual(outcome.matches, [])
                self.assertEqual([stage for stage, _, _ in provider.calls], ["analyze", "assess", "assess"])
            else:
                self.assertEqual(len(outcome.matches), 1)

    def event_case(self, profile=None, *, incomplete=False):
        analysis = analysis_for("Elder Vela", "Arrival")
        records = [
            {"id": 1, "title": "Elder Vela", "tags": ["portrait"]},
            {"id": 2, "title": "Vela greets travelers", "tags": []},
            {"id": 3, "title": "Arrival of Elder Vela", "tags": []},
        ]
        matches = [
            candidate(1, records[0]["title"], "subject_portrait", confidence="low", relevance=20),
            candidate(2, records[1]["title"], "related_specific", relevance=95),
            candidate(3, records[2]["title"], "exact_event", relevance=70),
        ]
        limits = MatchLimits(positive_limit=4, verification_limit=2)
        if incomplete:
            limits = replace(limits, batch_records=2, max_batches=1)
        provider = FixtureProvider(analysis, matches)
        result = match_icons(
            records,
            IconMatchRequest(
                kind="feast", primary_text="Arrival of Elder Vela", auto_assign_policy="feast_strict", max_results=10
            ),
            provider=provider,
            profile=profile,
            limits=limits,
        )
        return result, provider

    def test_luna_shortlist_and_final_rank_do_not_upgrade_relation_or_assignment(self):
        control, _ = self.event_case()
        luna, provider = self.event_case(LUNA)
        self.assertEqual([m["id"] for m in control.matches], [3, 1])
        self.assertEqual([m["id"] for m in luna.matches], [3, 2])
        self.assertEqual([m["id"] for m in provider.calls[-1][1]["candidates"]], [3, 2])
        related = luna.matches[1]
        self.assertEqual(related["relation"], "related_specific")
        self.assertFalse(related["auto_assignable"])
        high_id = candidate(999, "Vela", "related_specific", relevance=99)
        low_id = candidate(1, "Vela", "subject_portrait", relevance=10)
        self.assertLess(
            luna_rank(high_id, analysis_for("Vela", "Arrival")), luna_rank(low_id, analysis_for("Vela", "Arrival"))
        )
        conflicting_exact = candidate(0, "Arrival", "exact_event", conflict=True)
        self.assertGreater(
            luna_rank(conflicting_exact, analysis_for("Vela", "Arrival")),
            luna_rank(high_id, analysis_for("Vela", "Arrival")),
        )

    def test_luna_first_cannot_contaminate_later_control_results_or_payloads(self):
        before, before_provider = self.event_case()
        self.event_case(LUNA)
        after, after_provider = self.event_case()
        self.assertEqual(before.matches, after.matches)
        self.assertEqual([(s, p) for s, p, _ in before_provider.calls], [(s, p) for s, p, _ in after_provider.calls])
        self.assertEqual(dict(CONTROL.stage_prompts), STAGE_PROMPTS)

    def test_incomplete_catalogue_and_explicit_conflict_remain_failclosed(self):
        outcome, _ = self.event_case(LUNA, incomplete=True)
        self.assertFalse(outcome.catalogue_complete)
        self.assertTrue(all(not m["auto_assignable"] for m in outcome.matches))
        for conflict in (False, True):
            analysis = analysis_for("Elder Vela", "Arrival")
            match = candidate(1, "Elder Vela", "subject_portrait", conflict=conflict)
            for incomplete in (False, True):
                records = [{"id": 1, "title": "Elder Vela", "tags": ["portrait"]}]
                if incomplete:
                    records.append({"id": 2, "title": "Other", "tags": []})
                result = match_icons(
                    records,
                    IconMatchRequest(
                        kind="feast", primary_text="Arrival of Elder Vela", auto_assign_policy="feast_strict"
                    ),
                    provider=FixtureProvider(analysis, [match]),
                    profile=LUNA,
                    limits=MatchLimits(batch_records=1, max_batches=1),
                )
                self.assertEqual(result.matches[0]["auto_assignable"], not conflict and not incomplete)

    def test_luna_preserves_context_qualifiers_and_rejects_invented_quotes(self):
        analysis = analysis_for("Vela")
        analysis["subjects"][0]["qualifiers"] = [{"ref": "request:primary", "quote": "of the Ridge"}]
        analysis["context"] = [{"ref": "request:context:0", "quote": "courage"}]
        request = IconMatchRequest(
            kind="content",
            primary_text="Vela of the Ridge",
            context_terms=("courage",),
            auto_assign_policy="content_suggest",
        )
        catalogue = [{"id": 1, "title": "Vela of the Ridge", "tags": []}]
        for invalid in (False, True):
            data = deepcopy(analysis)
            if invalid:
                data["subjects"][0]["qualifiers"][0]["quote"] = "invented constraint"
            provider = FixtureProvider(data, [candidate(1, "Vela of the Ridge")])
            result = match_icons(catalogue, request, provider=provider, profile=LUNA)
            self.assertEqual(bool(result.matches), not invalid)
            self.assertEqual(
                provider.calls[0][1]["request"],
                {
                    "kind": "content",
                    "sources": {"request:primary": "Vela of the Ridge", "request:context:0": "courage"},
                },
            )
            if invalid:
                self.assertEqual([s for s, _, _ in provider.calls], ["analyze", "analyze"])
                self.assertIn("never omit", provider.calls[1][1]["validation_feedback"])
            else:
                self.assertTrue(result.matches[0]["auto_assignable"])
                self.assertEqual(provider.calls[1][1]["positive_limit"], 4)
                self.assertEqual(provider.calls[2][1]["analysis"], analysis)

    @override_settings(OPENAI_API_KEY="test-key", ICON_MATCH_MODEL="incidental-production-model")
    @patch("openai.AsyncOpenAI")
    def test_provider_no_argument_contract_and_luna_then_control(self, client):
        client.return_value.__aenter__.return_value = client.return_value
        client.return_value.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop", message=SimpleNamespace(content=json.dumps(analysis_for("Vela")))
                    )
                ]
            )
        )
        for profile in (LUNA_V2, LUNA, CONTROL, None):
            provider = OpenAIIconProvider(profile=profile) if profile else OpenAIIconProvider()
            provider.call("analyze", {"request": {}}, ANALYSIS, 3)
            kwargs = client.return_value.chat.completions.create.call_args.kwargs
            expected = {
                "model": profile.model if profile else "incidental-production-model",
                "timeout": 3,
                "stream": False,
                "messages": [
                    {
                        "role": "system",
                        "content": profile.stage_prompts["analyze"] if profile else STAGE_PROMPTS["analyze"],
                    },
                    {"role": "user", "content": '{"request":{}}'},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "icon_analyze", "strict": True, "schema": ANALYSIS},
                },
            }
            if profile in (LUNA, LUNA_V2):
                expected.update(max_completion_tokens=16000, reasoning_effort="none")
            else:
                expected["max_tokens"] = 16000
            self.assertEqual(kwargs, expected)
            self.assertEqual(provider.wire_call_count, 1)
            self.assertEqual(provider.usage_records, [None])
        client.assert_called_with(api_key="test-key", max_retries=0)
