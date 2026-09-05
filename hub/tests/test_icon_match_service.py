"""Provider-injected orchestration contracts, with no network or catalogue writes."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, override_settings

from hub.services.icon_matching import IconMatchRequest
from hub.services.icon_match_service import (
    BATCH,
    STAGE_PROMPTS,
    MatchLimits,
    normalize_provider_matches,
    provider_payload,
    wire_schema,
    OpenAIIconProvider,
    match_icons,
    serialize_catalogue,
)
from hub.tests.icon_match_fixtures import FixtureProvider, analysis_for, candidate


class SemanticMatchingTests(SimpleTestCase):
    def setUp(self):
        self.request = IconMatchRequest(
            kind="feast", primary_text="Սուրբ Նարեկ", auto_assign_policy="feast_strict", max_results=10
        )
        self.records = [
            {"id": 1, "title": "Saint Narek of the Lake", "tags": ["Սուրբ Նարեկ"]},
            {"id": 2, "title": "Other scene", "tags": []},
        ]
        self.analysis = analysis_for()
        self.match = candidate(1, self.records[0]["title"])

    def run_match(self, *, records=None, matches=None, mutate=None, limits=None, request=None):
        provider = FixtureProvider(self.analysis, [self.match] if matches is None else matches, mutate)
        outcome = match_icons(
            self.records if records is None else records, request or self.request, provider=provider, limits=limits
        )
        return outcome, provider

    def test_unregistered_translation_is_verified_and_assignable(self):
        outcome, provider = self.run_match()
        self.assertEqual(outcome.status, "complete")
        self.assertTrue(outcome.matches[0]["auto_assignable"])
        self.assertEqual(outcome.matches[0]["provenance"], "independent_semantic_verification")
        self.assertEqual([c[0] for c in provider.calls], ["analyze", "assess", "verify"])
        self.assertIn("Սուրբ Նարեկ", json.dumps(provider.calls, ensure_ascii=False))

    def test_single_category_requires_title_proof_even_for_one_group_span(self):
        subject = "Saints Luma and Aster"
        self.analysis = analysis_for(subject)
        for category in ("single", " SINGLE ", "group", "portrait"):
            for title_proof in (False, True):
                with self.subTest(category=category, title_proof=title_proof):
                    title = subject if title_proof else "Untitled Icon #4"
                    records = [{"id": 1, "title": title, "tags": [category, "saint-luma-valley", "saint-aster-hill"]}]
                    match = candidate(1, title)
                    if not title_proof:
                        match["evidence"] = [
                            {"ref": ref, "quote": quote, "role": "identity", "subject_indices": [0]}
                            for ref, quote in serialize_catalogue(records)[0]["sources"].items()
                            if quote in ("saint-luma-valley", "saint-aster-hill")
                        ]
                    outcome, _ = self.run_match(
                        request=replace(self.request, primary_text=subject), records=records, matches=[match]
                    )
                    self.assertEqual(len(outcome.matches), 1)
                    self.assertEqual(
                        outcome.matches[0]["auto_assignable"], title_proof or category.strip().lower() != "single"
                    )

    def test_single_qualified_title_remains_auto_with_both_stages_title_proof(self):
        title = "Saint Luma of the Valley"
        self.analysis = analysis_for(title)
        for weakened in (None, "assess", "verify"):

            def mutate(stage, payload, result):
                if stage == weakened:
                    result["matches"][0]["evidence"] = [
                        {
                            "ref": "icon:1:tag:0",
                            "quote": "saint-luma-of-the-valley",
                            "role": "identity",
                            "subject_indices": [0],
                        }
                    ]

            outcome, _ = self.run_match(
                request=replace(self.request, primary_text=title),
                records=[{"id": 1, "title": title, "tags": ["single", "saint-luma-of-the-valley"]}],
                matches=[candidate(1, title)],
                mutate=mutate,
            )
            self.assertEqual(len(outcome.matches), 1)
            self.assertEqual(outcome.matches[0]["auto_assignable"], weakened is None)

    def test_partial_exact_identity_is_normalized_before_shortlist_and_verification(self):
        subject = "Saints Luma and Aster"
        for subset in (False, True):
            self.analysis = analysis_for(subject)
            if subset:
                self.analysis["subjects"] = analysis_for("Luma")["subjects"] + analysis_for("Aster")["subjects"]
            full = [0, 1] if subset else [0]
            records = [{"id": 1, "title": "Saint Luma", "tags": []}, {"id": 2, "title": subject, "tags": []}]
            matches = [
                candidate(1, "Saint Luma", full_request_coverage=subset, relevance=100),
                candidate(2, subject, covered_subjects=full, relevance=50),
            ]
            for limit in (1, 2):
                with self.subTest(subset=subset, limit=limit):

                    def mutate(stage, payload, result):
                        if stage == "verify":
                            # Verifier repeats the model's original direct label.
                            for match in result["matches"]:
                                match["relation"] = "exact_subject"

                    outcome, provider = self.run_match(
                        request=replace(self.request, primary_text=subject, max_results=limit),
                        records=records,
                        matches=matches,
                        limits=MatchLimits(verification_limit=limit),
                        mutate=mutate,
                    )
                    self.assertEqual(outcome.matches[0]["id"], 2)
                    if limit == 1:
                        self.assertEqual([r["id"] for r in provider.calls[-1][1]["catalogue"]], [2])
                    else:
                        partial = outcome.matches[1]
                        self.assertEqual(partial["relation"], "related_specific")
                        self.assertEqual(partial["match_tier"], "related_specific")
                        self.assertFalse(partial["auto_assignable"])
                        self.assertTrue(outcome.matches[0]["auto_assignable"])

    def test_analysis_quote_repair_is_once_and_preserves_original_request(self):
        request_text = "Հոգեգալստյան"
        self.analysis = analysis_for(None, request_text)
        for repeated in (False, True):
            attempts = []

            def mutate(stage, payload, result):
                if stage == "analyze":
                    attempts.append(payload)
                    if repeated or len(attempts) == 1:
                        result["event"][0]["quote"] = "Հոգեգալուստ"

            outcome, provider = self.run_match(
                request=replace(self.request, primary_text=request_text),
                records=[{"id": 1, "title": request_text, "tags": []}],
                matches=[candidate(1, request_text, "exact_event", covered_subjects=[])],
                mutate=mutate,
            )
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[0]["request"], attempts[1]["request"])
            self.assertIn("exact substring", attempts[1]["validation_feedback"])
            self.assertIn("never omit", attempts[1]["validation_feedback"])
            self.assertEqual(outcome.status, "unavailable" if repeated else "complete")
            self.assertEqual(outcome.call_count, 2 if repeated else 4)
            if repeated:
                self.assertFalse(outcome.matches)
                self.assertEqual([c[0] for c in provider.calls], ["analyze", "analyze"])

    def test_analysis_schema_source_and_structure_repair(self):
        for corruption in ("schema", "source", "structure"):
            attempts = []

            def mutate(stage, payload, result):
                if stage == "analyze":
                    attempts.append(payload)
                    if len(attempts) == 1:
                        if corruption == "schema":
                            result["extra"] = "invalid"
                        elif corruption == "source":
                            result["subjects"][0]["span"]["ref"] = "unknown"
                        else:
                            result["intent"] = "theme"

            outcome, _ = self.run_match(mutate=mutate)
            self.assertEqual(outcome.status, "complete")
            self.assertEqual(len(attempts), 2)

    def test_deadline_prevents_analysis_repair(self):
        now = [0.0]

        def mutate(stage, payload, result):
            now[0] = 20
            result["subjects"][0]["span"]["quote"] = "invalid quote"

        with patch("hub.services.icon_match_service.time.monotonic", side_effect=lambda: now[0]):
            outcome, provider = self.run_match(mutate=mutate, limits=MatchLimits(total_seconds=20))
        self.assertEqual(outcome.status, "unavailable")
        self.assertEqual([c[0] for c in provider.calls], ["analyze"])

    def event_only_case(self, mutate=None, limits=None, multiple=False):
        title = "Festival of first light"
        self.analysis = analysis_for(None, title)
        records = [{"id": i, "title": title, "tags": []} for i in range(1, 3 if multiple else 2)]
        matches = [
            candidate(
                r["id"],
                title,
                "exact_event",
                covered_subjects=[],
                evidence=[
                    {
                        "ref": f"icon:{r['id']}:title",
                        "quote": title,
                        "role": "event",
                        "subject_indices": [],
                    }
                ],
            )
            for r in records
        ]
        return self.run_match(
            records=records,
            matches=matches,
            mutate=mutate,
            limits=limits,
            request=replace(self.request, primary_text=title),
        )

    @staticmethod
    def corrupt_event_only(result):
        for match in result["matches"]:
            match["covered_subjects"] = [0]
            match["evidence"][0].update(role="identity", subject_indices=[0])
        result["matches"].append({})

    def test_recorded_event_only_contract_failure_repairs_once_then_verifies(self):
        for repeated in (False, True):
            attempts = []

            def mutate(stage, payload, result):
                if stage == "assess":
                    attempts.append(payload)
                    if repeated or len(attempts) == 1:
                        self.corrupt_event_only(result)
                elif stage == "verify":
                    # Supply independent original event evidence, not a repaired
                    # identity hypothesis copied from the assessment payload.
                    result["analysis"] = analysis_for(None, "Festival of first light")
                    result["matches"][0]["evidence"] = [
                        {
                            "ref": "icon:1:title",
                            "quote": "Festival of first light",
                            "role": "event",
                            "subject_indices": [],
                        }
                    ]

            outcome, provider = self.event_only_case(mutate)
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[0], {k: v for k, v in attempts[1].items() if k != "validation_feedback"})
            self.assertEqual(attempts[1]["analysis"], self.analysis)
            self.assertIn("no\nidentity-role evidence", attempts[1]["validation_feedback"])
            self.assertEqual(outcome.status, "unavailable" if repeated else "complete")
            self.assertEqual(
                [c[0] for c in provider.calls], ["analyze", "assess", "assess"] + ([] if repeated else ["verify"])
            )
            if repeated:
                self.assertEqual(outcome.matches, [])
            else:
                self.assertTrue(outcome.matches[0]["auto_assignable"])
                self.assertEqual(outcome.matches[0]["covered_subjects"], [])
                self.assertEqual(outcome.matches[0]["relation"], "exact_event")

    def test_grounding_repair_preserves_attested_event_and_rejects_summary_disagreement(self):
        self.analysis = analysis_for(event="Arrival")
        records = [self.records[0], {"id": 2, "title": "Arrival of Saint Narek of the Lake", "tags": []}]
        for portrait_repair in (True, False):
            with self.subTest(portrait_repair=portrait_repair):

                def mutate(stage, payload, result):
                    if stage != "assess":
                        return
                    if "validation_feedback" not in payload:
                        for evidence in result["matches"][0]["evidence"]:
                            evidence["quote"] = "fabricated"
                    elif portrait_repair:
                        result.update(
                            matches=[candidate(1, records[0]["title"], "subject_portrait")],
                            exact_event_exists=False,
                        )

                outcome, provider = self.run_match(
                    records=records,
                    matches=[candidate(2, records[1]["title"], "exact_event")],
                    request=replace(self.request, primary_text="Arrival of Սուրբ Նարեկ"),
                    mutate=mutate,
                )
                self.assertEqual([s for s, _, _ in provider.calls], ["analyze", "assess", "assess", "verify"])
                original, repaired, verification = [p for _, p, _ in provider.calls[1:]]
                self.assertEqual(original, {k: v for k, v in repaired.items() if k != "validation_feedback"})
                self.assertEqual(verification["request"], original["request"])
                self.assertEqual(verification["analysis"], original["analysis"])
                self.assertTrue(verification["exact_event_exists"])
                self.assertEqual(outcome.status, "partial" if portrait_repair else "complete")
                self.assertEqual("assessment_event_summary_disagreement" in outcome.diagnostics, portrait_repair)
                self.assertEqual(len(outcome.matches), 1)
                self.assertEqual(
                    outcome.matches[0]["relation"], "subject_portrait" if portrait_repair else "exact_event"
                )
                self.assertEqual(outcome.matches[0]["auto_assignable"], not portrait_repair)

    def test_assessment_repair_is_global_across_batches(self):
        def mutate(stage, payload, result):
            if stage == "assess" and "validation_feedback" not in payload:
                self.corrupt_event_only(result)

        outcome, provider = self.event_only_case(mutate, MatchLimits(batch_records=1), multiple=True)
        assessments = [p for s, p, _ in provider.calls if s == "assess"]
        self.assertEqual([p["catalogue"][0]["id"] for p in assessments], [1, 1, 2])
        self.assertEqual(outcome.status, "partial")
        self.assertEqual(outcome.assessed_count, 1)
        self.assertFalse(outcome.matches[0]["auto_assignable"])

    def test_deadline_prevents_assessment_repair(self):
        now = [0.0]

        def mutate(stage, payload, result):
            if stage == "assess":
                self.corrupt_event_only(result)
                now[0] = 20

        with patch("hub.services.icon_match_service.time.monotonic", side_effect=lambda: now[0]):
            outcome, provider = self.event_only_case(mutate, MatchLimits(total_seconds=20))
        self.assertEqual(outcome.status, "unavailable")
        self.assertEqual([c[0] for c in provider.calls], ["analyze", "assess"])

    def test_assessment_semantics_and_attestation_never_trigger_format_repair(self):
        for fault in (
            "attestation",
            "batch_id",
            "conflict",
            "event_summary",
            "incompatible",
            "event_disagrees",
            "empty",
        ):
            with self.subTest(fault=fault):

                def mutate(stage, payload, result):
                    if stage != "assess":
                        return
                    self.corrupt_event_only(result)
                    if fault == "attestation":
                        result["assessment_complete"] = False
                    elif fault == "batch_id":
                        result["batch_id"] = "other-batch"
                    elif fault == "conflict":
                        result["matches"][0]["conflict"] = True
                    elif fault == "event_summary":
                        result["exact_event_exists"] = False
                    elif fault == "incompatible":
                        result["matches"][0]["relation"] = "exact_subject"
                    elif fault == "event_disagrees":
                        result["matches"][0]["event_agrees"] = False
                    else:
                        result.update(matches=[], exact_event_exists=False)

                outcome, provider = self.event_only_case(mutate)
                self.assertEqual([c[0] for c in provider.calls], ["analyze", "assess"])
                self.assertEqual(outcome.status, "complete" if fault == "empty" else "unavailable")

    def test_event_only_valid_neighbor_is_not_repaired(self):
        def mutate(stage, payload, result):
            if stage == "assess":
                result["matches"][1]["covered_subjects"] = [0]
                result["matches"].append({})

        outcome, provider = self.event_only_case(mutate, multiple=True)
        self.assertEqual([c[0] for c in provider.calls], ["analyze", "assess", "verify"])
        self.assertEqual([m["id"] for m in outcome.matches], [1])
        self.assertEqual(outcome.status, "partial")

    def test_event_only_requires_independent_event_evidence(self):
        for weakened in ("assess", "verify"):

            def mutate(stage, payload, result):
                if stage == weakened:
                    result["matches"][0]["evidence"][0]["role"] = "topic"

            outcome, provider = self.event_only_case(mutate)
            self.assertEqual(outcome.status, "complete")
            self.assertFalse(outcome.matches[0]["auto_assignable"])
            self.assertEqual([c[0] for c in provider.calls], ["analyze", "assess", "verify"])

    def test_all_records_seen_once_even_with_literal_hit_and_late_semantic_match(self):
        records = [{"id": i, "title": "Սուրբ Նարեկ" if i == 1 else f"Image {i}", "tags": []} for i in range(1, 441)]
        outcome, provider = self.run_match(records=records, matches=[candidate(440, "Image 440", "thematic")])
        ids = [r["id"] for stage, payload, _ in provider.calls if stage == "assess" for r in payload["catalogue"]]
        self.assertEqual(ids, list(range(1, 441)))
        self.assertEqual(outcome.assessed_count, 440)
        for stage, payload, _ in provider.calls:
            if stage in ("assess", "verify"):
                self.assertTrue(all(set(r) == {"id", "title", "tags"} for r in payload["catalogue"]))
        self.assertEqual(outcome.call_count, 3)
        self.assertEqual(outcome.matches[0]["id"], 440)
        self.assertFalse(outcome.matches[0]["auto_assignable"])
        self.assertFalse(outcome.positives_complete)

    def test_compact_wire_preserves_sorted_unicode_refs_and_byte_bounds(self):
        title = "Arrival of Արամ of the Lake"
        self.analysis = analysis_for("Արամ of the Lake", "Arrival")
        tags = ["բ", "Արամ of the Lake", "ա", "Արամ of the Lake"]
        records = [{"id": 1, "title": title, "tags": tags}]
        ordered = serialize_catalogue(records)[0]["tags"]
        evidence = [
            {
                "ref": f"icon:1:tag:{ordered.index('Արամ of the Lake')}",
                "quote": "Արամ of the Lake",
                "role": "identity",
                "subject_indices": [0],
            },
            {"ref": "icon:1:title", "quote": "Arrival", "role": "event", "subject_indices": []},
        ]
        outcome, provider = self.run_match(
            records=records,
            request=replace(self.request, primary_text=title),
            matches=[candidate(1, title, "exact_event", evidence=evidence)],
        )
        self.assertTrue(outcome.matches[0]["auto_assignable"])
        for stage, payload, _ in provider.calls[1:]:
            self.assertEqual(payload["catalogue"], [{"id": 1, "title": title, "tags": ordered}])
            size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
            self.assertLessEqual(size, 160000)

    def test_event_evidence_required_independently_in_both_stages(self):
        title = "Arrival of Սուրբ Նարեկ"
        self.analysis = analysis_for(event="Arrival")
        for weakened in (None, "assess", "verify"):

            def mutate(stage, payload, result):
                if stage == weakened:
                    result["matches"][0]["evidence"] = result["matches"][0]["evidence"][:1]

            outcome, _ = self.run_match(
                records=[{"id": 1, "title": title, "tags": ["Narek"]}],
                request=replace(self.request, primary_text=title),
                matches=[candidate(1, title, "exact_event")],
                mutate=mutate,
            )
            self.assertEqual(outcome.matches[0]["auto_assignable"], weakened is None)

    def test_invalid_event_positive_cannot_unlock_prior_batch_portrait(self):
        self.analysis = analysis_for(event="Arrival")
        for failure in ("invalid_positive", "false_attestation", "other_batch"):

            def mutate(stage, payload, result):
                if stage == "assess" and payload["catalogue"][0]["id"] == 2:
                    if failure == "invalid_positive":
                        result["matches"][0]["evidence"][0]["quote"] = "fabricated"
                    elif failure == "false_attestation":
                        result["assessment_complete"] = False
                    else:
                        result["batch_id"] = "prior-batch"

            outcome, _ = self.run_match(
                records=[self.records[0], {"id": 2, "title": "Arrival of Saint Narek", "tags": []}],
                matches=[
                    candidate(1, self.records[0]["title"], "subject_portrait"),
                    candidate(2, "Arrival of Saint Narek", "exact_event"),
                ],
                request=replace(self.request, primary_text="Arrival of Սուրբ Նարեկ"),
                limits=MatchLimits(batch_records=1),
                mutate=mutate,
            )
            self.assertEqual(outcome.status, "partial")
            self.assertFalse(outcome.catalogue_complete)
            self.assertEqual(outcome.assessed_count, 1)
            self.assertFalse(any(m["auto_assignable"] for m in outcome.matches))

    def test_shuffled_input_and_response_ids_are_stable(self):
        a, _ = self.run_match()
        b, _ = self.run_match(records=list(reversed(self.records)))
        self.assertEqual(a.matches, b.matches)
        self.assertEqual(a.catalogue_digest, b.catalogue_digest)

    def test_event_in_later_batch_defeats_portrait(self):
        self.analysis = analysis_for(event="Arrival")
        request = replace(self.request, primary_text="Arrival of Սուրբ Նարեկ")
        records = [self.records[0], {"id": 2, "title": "Arrival of Saint Narek of the Lake", "tags": []}]
        matches = [
            candidate(1, records[0]["title"], "subject_portrait"),
            candidate(2, records[1]["title"], "exact_event"),
        ]
        outcome, _ = self.run_match(
            records=records, matches=matches, request=request, limits=MatchLimits(batch_records=1)
        )
        self.assertEqual([m["id"] for m in outcome.matches], [2, 1])
        self.assertTrue(outcome.matches[0]["auto_assignable"])
        self.assertFalse(outcome.matches[1]["auto_assignable"])
        outcome, _ = self.run_match(records=records[:1], matches=matches[:1], request=request)
        self.assertTrue(outcome.matches[0]["auto_assignable"])

    def test_summary_of_omitted_exact_event_suppresses_portrait(self):
        self.analysis = analysis_for(event="Arrival")

        def mutate(stage, payload, result):
            if stage == "assess":
                result["exact_event_exists"] = True

        outcome, _ = self.run_match(
            matches=[candidate(1, self.records[0]["title"], "subject_portrait")],
            request=replace(self.request, primary_text="Arrival of Սուրբ Նարեկ"),
            mutate=mutate,
        )
        self.assertFalse(outcome.matches[0]["auto_assignable"])

    def test_invalid_batch_contracts_fail_explicitly(self):
        mutations = {
            "missing": lambda r: r.pop("assessment_complete"),
            "wrong_batch": lambda r: r.update(batch_id="wrong"),
            "incomplete": lambda r: r.update(assessment_complete=False),
            "legacy_truncated": lambda r: r.update(truncated=True),
            "unknown": lambda r: r["matches"][0].update(id=999),
            "quote": lambda r: r["matches"][0]["evidence"][0].update(quote="fabricated"),
            "cross_record_ref": lambda r: r["matches"][0]["evidence"][0].update(ref="icon:2:title"),
            "schema": lambda r: r.update(extra="secret"),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):

                def mutate(stage, payload, result):
                    if stage == "assess":
                        mutation(result)

                outcome, _ = self.run_match(mutate=mutate)
                self.assertEqual(outcome.status, "unavailable")
                self.assertFalse(outcome.catalogue_complete)
                self.assertEqual(outcome.matches, [])
                self.assertEqual(outcome.diagnostics, ["batch_failed"])

    def test_partial_catalogue_never_assigns_portrait(self):
        self.analysis = analysis_for(event="Arrival")

        def mutate(stage, payload, result):
            if stage == "assess" and payload["catalogue"][0]["id"] == 2:
                raise RuntimeError("private provider error")

        outcome, _ = self.run_match(
            matches=[candidate(1, self.records[0]["title"], "subject_portrait")],
            request=replace(self.request, primary_text="Arrival of Սուրբ Նարեկ"),
            limits=MatchLimits(batch_records=1),
            mutate=mutate,
        )
        self.assertEqual(outcome.status, "partial")
        self.assertEqual(outcome.assessed_count, 1)
        self.assertFalse(outcome.matches[0]["auto_assignable"])
        self.assertNotIn("private", json.dumps(outcome.to_dict()))

    def test_verifier_catches_unrecognized_composite_and_wrong_event(self):
        mutations = [
            lambda r: r.update(request_coverage_complete=False),
            lambda r: r["matches"][0].update(conflict=True),
            lambda r: r["matches"][0].update(full_request_coverage=False),
            lambda r: r["matches"][0].update(identity_qualified=False),
            lambda r: r["matches"][0].update(covered_subjects=[]),
            lambda r: r["analysis"]["unresolved"].append({"ref": "request:primary", "quote": "Սուրբ"}),
        ]
        for mutation in mutations:

            def mutate(stage, payload, result):
                if stage == "verify":
                    mutation(result)

            outcome, _ = self.run_match(mutate=mutate)
            self.assertFalse(any(m["auto_assignable"] for m in outcome.matches))

    def test_verification_failure_is_partial_and_candidates_are_only_suggestions(self):
        def mutate(stage, payload, result):
            if stage == "verify":
                result["reviewed_ids"] = []

        outcome, _ = self.run_match(mutate=mutate)
        self.assertEqual(outcome.status, "partial")
        self.assertTrue(outcome.catalogue_complete)
        self.assertEqual(outcome.matches, [])

    def test_analysis_quotes_are_validated_and_failure_is_unavailable(self):
        self.analysis["subjects"][0]["span"]["quote"] = "Invented person"
        outcome, provider = self.run_match()
        self.assertEqual(outcome.status, "unavailable")
        self.assertEqual(len(provider.calls), 2)

    def test_record_and_byte_budgets_are_explicit(self):
        outcome, provider = self.run_match(limits=MatchLimits(batch_records=1, max_batches=1))
        self.assertEqual(outcome.status, "partial")
        self.assertIn("catalogue_budget_exceeded", outcome.diagnostics)
        self.assertFalse(outcome.matches[0]["auto_assignable"])
        outcome, provider = self.run_match(limits=MatchLimits(batch_bytes=10))
        self.assertEqual(outcome.status, "unavailable")
        self.assertIn("oversized_record", outcome.diagnostics)
        self.assertEqual(len(provider.calls), 1)

    def test_empty_catalogue_and_complete_no_match_are_distinct_from_failure(self):
        outcome, provider = self.run_match(records=[])
        self.assertEqual(outcome.status, "complete")
        self.assertEqual(provider.calls, [])
        outcome, provider = self.run_match(matches=[])
        self.assertEqual(outcome.status, "complete")
        self.assertEqual(outcome.matches, [])
        self.assertEqual(outcome.assessed_count, 2)

    def test_duplicate_catalogue_ids_rejected_without_provider_call(self):
        outcome, provider = self.run_match(records=[self.records[0], self.records[0]])
        self.assertEqual(outcome.diagnostics, ["invalid_input"])
        self.assertEqual(provider.calls, [])

    @patch("hub.services.icon_match_service.time.sleep")
    def test_transient_retry_budget_and_no_error_body_leak(self, sleep):
        class Busy(Exception):
            status_code = 429
            response = SimpleNamespace(headers={"retry-after": "999"})

        def mutate(stage, payload, result):
            raise Busy("secret")

        outcome, provider = self.run_match(mutate=mutate)
        self.assertEqual(outcome.call_count, 2)
        sleep.assert_called_once_with(2)
        self.assertNotIn("secret", json.dumps(outcome.to_dict()))

    def test_total_time_budget_prevents_calls(self):
        outcome, provider = self.run_match(limits=MatchLimits(total_seconds=0))
        self.assertEqual(outcome.call_count, 0)
        self.assertEqual(provider.calls, [])
        self.assertEqual(outcome.status, "unavailable")

    @override_settings(OPENAI_API_KEY="test-key")
    @patch("openai.AsyncOpenAI")
    def test_adapter_sends_strict_schema_and_disables_retries(self, client):
        fixture = FixtureProvider(self.analysis, [self.match])

        async def complete(**kwargs):
            stage = kwargs["response_format"]["json_schema"]["name"].removeprefix("icon_")
            payload = json.loads(kwargs["messages"][1]["content"])
            result = fixture.call(stage, payload, None, kwargs["timeout"])
            # FixtureProvider is the internal canonical-ref interface; emulate wire JSON.
            if stage == "assess":
                result["matches"] = provider_payload({"candidates": result["matches"]})["candidates"]
            return SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(result)))]
            )

        client.return_value.__aenter__.return_value = client.return_value
        client.return_value.chat.completions.create = AsyncMock(side_effect=complete)
        outcome = match_icons(self.records, self.request)
        self.assertTrue(outcome.matches[0]["auto_assignable"])
        self.assertEqual(client.call_count, 3)
        client.assert_called_with(api_key="test-key", max_retries=0)
        for call in client.return_value.chat.completions.create.call_args_list:
            self.assertTrue(call.kwargs["response_format"]["json_schema"]["strict"])
            self.assertLessEqual(call.kwargs["timeout"], 45)

    @override_settings(OPENAI_API_KEY="test-key")
    @patch("openai.AsyncOpenAI")
    def test_adapter_rejects_actual_output_truncation(self, client):
        client.return_value.__aenter__.return_value = client.return_value
        client.return_value.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason="length")])
        )
        outcome = match_icons(self.records, self.request, provider=OpenAIIconProvider())
        self.assertEqual(outcome.status, "unavailable")

    def test_catalogue_serialization_preserves_unicode_and_orders_tags(self):
        records = serialize_catalogue([{"id": 1, "title": "Հայերեն", "tag_list": ["բ", "ա"]}])
        self.assertEqual(records[0]["title"], "Հայերեն")
        self.assertEqual(records[0]["sources"]["icon:1:tag:0"], "ա")

    def test_identity_labels_cannot_drop_qualifiers_or_use_bare_name_tags(self):
        for evidence in (
            {"ref": "icon:1:title", "quote": "Saint Narek", "role": "identity"},
            {"ref": "icon:1:tag:0", "quote": "Սուրբ Նարեկ", "role": "topic"},
        ):
            match = {**self.match, "evidence": [evidence]}
            outcome, _ = self.run_match(matches=[match])
            self.assertFalse(any(m["auto_assignable"] for m in outcome.matches))
        records = [{"id": 1, "title": "Unknown scene", "tags": ["John"]}]
        match = candidate(1, "Unknown scene", evidence=[{"ref": "icon:1:tag:0", "quote": "John", "role": "identity"}])
        outcome, _ = self.run_match(records=records, matches=[match])
        self.assertEqual(outcome.status, "complete")
        self.assertEqual(len(outcome.matches), 1)
        self.assertFalse(outcome.matches[0]["auto_assignable"])

    def test_full_composite_and_qualifier_coverage_is_required(self):
        self.analysis["subjects"].append({"span": {"ref": "request:primary", "quote": "Արամ"}, "qualifiers": []})
        request = replace(self.request, primary_text="Սուրբ Նարեկ and Արամ")
        outcome, _ = self.run_match(request=request)
        self.assertFalse(outcome.matches[0]["auto_assignable"])
        self.analysis["subjects"][0]["qualifiers"] = [{"ref": "request:primary", "quote": "Lake"}]
        request = replace(request, primary_text=request.primary_text + " of the Lake")

        def mutate(stage, payload, result):
            if stage == "verify":
                result["analysis"]["subjects"][0]["qualifiers"] = []

        outcome, _ = self.run_match(
            request=request, mutate=mutate, matches=[{**self.match, "covered_subjects": [0, 1]}]
        )
        self.assertFalse(outcome.matches[0]["auto_assignable"])

    def test_other_events_and_unknown_scenes_are_never_portrait_fallback(self):
        self.analysis = analysis_for(event="Arrival")
        request = replace(self.request, primary_text="Arrival of Սուրբ Նարեկ")
        for changes in ({"generic_portrait": False}, {"conflict": True}, {"event_agrees": False}):
            match = candidate(1, self.records[0]["title"], "subject_portrait", **changes)
            outcome, _ = self.run_match(request=request, matches=[match])
            self.assertFalse(outcome.matches[0]["auto_assignable"])

    def test_generic_positive_output_is_bounded_without_claiming_exhaustive_positives(self):
        records = [{"id": i, "title": f"Picture {i}", "tags": []} for i in range(1, 41)]
        matches = [candidate(r["id"], r["title"], "thematic") for r in records]
        outcome, provider = self.run_match(records=records, matches=matches, limits=MatchLimits(positive_limit=3))
        self.assertEqual(outcome.assessed_count, 40)
        self.assertEqual(len(outcome.matches), 3)
        self.assertEqual(len(provider.calls[-1][1]["candidates"]), 3)
        self.assertFalse(outcome.positives_complete)
        self.assertEqual(outcome.status, "complete")

    @patch("hub.services.icon_match_service.time.sleep")
    def test_transient_retry_retains_success_and_continues_all_stages(self, sleep):
        calls = []

        class Busy(Exception):
            status_code = 503

        def mutate(stage, payload, result):
            calls.append(stage)
            if len(calls) == 1:
                raise Busy()

        outcome, _ = self.run_match(mutate=mutate)
        self.assertEqual(outcome.status, "complete")
        self.assertEqual(outcome.call_count, 4)
        self.assertTrue(outcome.matches[0]["auto_assignable"])

    def test_verified_rejection_is_complete_no_match(self):
        def mutate(stage, payload, result):
            if stage == "verify":
                result["matches"] = []

        outcome, _ = self.run_match(mutate=mutate)
        self.assertEqual(outcome.status, "complete")
        self.assertEqual(outcome.matches, [])

    def test_independent_subject_order_does_not_block_full_composite(self):
        self.analysis["subjects"].append({"span": {"ref": "request:primary", "quote": "Արամ"}, "qualifiers": []})
        title = "Սուրբ Նարեկ and Արամ"

        def mutate(stage, payload, result):
            if stage == "verify":
                result["analysis"]["subjects"].reverse()

        outcome, _ = self.run_match(
            request=replace(self.request, primary_text=title),
            records=[{"id": 1, "title": title, "tags": []}],
            matches=[candidate(1, title, covered_subjects=[0, 1])],
            mutate=mutate,
        )
        self.assertTrue(outcome.matches[0]["auto_assignable"])

    def test_every_subject_needs_identity_links_in_both_stages(self):
        self.analysis = analysis_for("John the Baptist", "Beheading")
        request = replace(self.request, primary_text="Beheading of John the Baptist")
        for title, tags, evidence in (
            (
                "Transfiguration of Christ",
                ["John"],
                [
                    {"ref": "icon:1:tag:0", "quote": "John", "role": "topic", "subject_indices": []},
                ],
            ),
            (
                "Beheading",
                [],
                [
                    {"ref": "icon:1:title", "quote": "Beheading", "role": "event", "subject_indices": []},
                ],
            ),
        ):
            with self.subTest(title=title):
                outcome, _ = self.run_match(
                    request=request,
                    records=[{"id": 1, "title": title, "tags": tags}],
                    matches=[candidate(1, title, "exact_event", evidence=evidence)],
                )
                self.assertEqual(outcome.status, "complete")
                self.assertFalse(outcome.matches[0]["auto_assignable"])
        for stage_to_weaken in ("assess", "verify"):

            def mutate(stage, payload, result):
                if stage == stage_to_weaken:
                    for evidence in result["matches"][0]["evidence"]:
                        evidence["subject_indices"] = []

            outcome, _ = self.run_match(
                request=request,
                records=[{"id": 1, "title": "John the Baptist", "tags": []}],
                matches=[candidate(1, "John the Baptist", "subject_portrait")],
                mutate=mutate,
            )
            self.assertFalse(outcome.matches[0]["auto_assignable"])
        outcome, _ = self.run_match(
            request=request,
            records=[{"id": 1, "title": "John the Baptist", "tags": []}],
            matches=[candidate(1, "John the Baptist", "subject_portrait")],
        )
        self.assertTrue(outcome.matches[0]["auto_assignable"])

    def test_full_coverage_boolean_cannot_replace_second_subject_evidence(self):
        self.analysis = analysis_for("John the Baptist")
        self.analysis["subjects"].append({"span": {"ref": "request:primary", "quote": "Արամ"}, "qualifiers": []})
        match = candidate(1, "John the Baptist", covered_subjects=[0, 1])
        match["evidence"][0]["subject_indices"] = [0]
        outcome, _ = self.run_match(
            request=replace(self.request, primary_text="John the Baptist and Արամ"),
            records=[{"id": 1, "title": "John the Baptist", "tags": []}],
            matches=[match],
        )
        self.assertFalse(outcome.matches[0]["auto_assignable"])

    def test_verifier_downgrade_survives_independent_invalid_candidate(self):
        def mutate(stage, payload, result):
            if stage == "verify":
                result["matches"][0].update(relation="related_specific", reason="Only a related subject.")
                result["matches"].append(candidate(999, "Invented"))

        outcome, _ = self.run_match(mutate=mutate)
        self.assertEqual(outcome.status, "partial")
        self.assertEqual(len(outcome.matches), 1)
        self.assertEqual(outcome.matches[0]["relation"], "related_specific")
        self.assertFalse(outcome.matches[0]["auto_assignable"])
        self.assertIn("invalid_verified_candidate", outcome.diagnostics)

    def test_supported_verifier_downgrade_is_complete_and_upgrade_is_rejected(self):
        for relation, expected in (("related_specific", 1), ("exact_event", 0)):

            def mutate(stage, payload, result):
                if stage == "verify":
                    result["matches"][0]["relation"] = relation

            outcome, _ = self.run_match(mutate=mutate)
            self.assertEqual(len(outcome.matches), expected)
            if expected:
                self.assertEqual(outcome.status, "complete")
                self.assertEqual(outcome.matches[0]["relation"], relation)
                self.assertFalse(outcome.matches[0]["auto_assignable"])

    def test_theme_rejects_false_direct_relations_before_shortlisting(self):
        self.analysis = analysis_for(subject=None)
        records = [
            {"id": 1, "title": "Saint Luma", "tags": []},
            {"id": 2, "title": "Humility scene", "tags": []},
        ]
        for direct_relation in ("exact_event", "exact_subject", "subject_portrait"):
            for valid_relation in ("related_specific", "thematic"):
                for max_results in (1, 10):
                    with self.subTest(
                        direct_relation=direct_relation, valid_relation=valid_relation, max_results=max_results
                    ):
                        outcome, provider = self.run_match(
                            records=records,
                            request=replace(self.request, primary_text="Humility", max_results=max_results),
                            matches=[
                                candidate(1, "Saint Luma", direct_relation, covered_subjects=[], relevance=25),
                                candidate(2, "Humility scene", valid_relation, covered_subjects=[], relevance=100),
                            ],
                            limits=MatchLimits(verification_limit=1),
                        )
                        self.assertEqual(outcome.status, "partial")
                        self.assertFalse(outcome.catalogue_complete)
                        self.assertIn("invalid_assessed_candidate", outcome.diagnostics)
                        self.assertNotIn("verification_shortlist", outcome.diagnostics)
                        self.assertEqual([m["id"] for m in outcome.matches], [2])
                        self.assertEqual(outcome.matches[0]["match_tier"], valid_relation)
                        self.assertFalse(outcome.matches[0]["auto_assignable"])
                        verification = [payload for stage, payload, _ in provider.calls if stage == "verify"]
                        self.assertEqual(len(verification), 1)
                        self.assertEqual([m["id"] for m in verification[0]["candidates"]], [2])

    def test_global_semantic_order_precedes_id_across_batches(self):
        records = [{"id": 1, "title": "Adjacent theme", "tags": []}, {"id": 20, "title": "Best theme", "tags": []}]
        matches = [candidate(20, "Best theme", "thematic"), candidate(1, "Adjacent theme", "thematic")]
        for batch_records in (1, 256):

            def mutate(stage, payload, result):
                if stage == "verify":
                    for match in result["matches"]:
                        match["relevance"] = 100 if match["id"] == 20 else 50

            outcome, _ = self.run_match(
                records=records,
                matches=matches,
                mutate=mutate,
                request=replace(self.request, max_results=1),
                limits=MatchLimits(batch_records=batch_records),
            )
            self.assertEqual(outcome.matches[0]["id"], 20)

    def test_true_event_summary_without_candidates_is_incomplete(self):
        def mutate(stage, payload, result):
            if stage == "assess":
                result["exact_event_exists"] = True

        outcome, _ = self.run_match(matches=[], mutate=mutate)
        self.assertEqual(outcome.status, "partial")
        self.assertEqual(outcome.matches, [])
        self.assertIn("contradictory_event_summary", outcome.diagnostics)

    def test_short_identity_substring_in_unrelated_provenance_is_rejected(self):
        self.analysis = analysis_for("John")
        title = "Landscape from the collection of John, donated in memory of a friend"
        match = candidate(
            1,
            title,
            evidence=[
                {
                    "ref": "icon:1:title",
                    "quote": "John",
                    "role": "identity",
                    "subject_indices": [0],
                }
            ],
        )
        outcome, _ = self.run_match(
            request=replace(self.request, primary_text="John"),
            records=[{"id": 1, "title": title, "tags": []}],
            matches=[match],
        )
        self.assertEqual(outcome.status, "unavailable")
        self.assertFalse(outcome.matches)

    def test_qualifier_order_is_normalized_without_dropping_constraints(self):
        title = "Սուրբ Նարեկ of the Lake the Elder"
        self.analysis["subjects"][0]["qualifiers"] = [
            {"ref": "request:primary", "quote": "of the Lake"},
            {"ref": "request:primary", "quote": "the Elder"},
        ]

        def mutate(stage, payload, result):
            if stage == "verify":
                result["analysis"]["subjects"][0]["qualifiers"].reverse()

        outcome, _ = self.run_match(
            request=replace(self.request, primary_text=title),
            records=[{"id": 1, "title": title, "tags": []}],
            matches=[candidate(1, title)],
            mutate=mutate,
        )
        self.assertTrue(outcome.matches[0]["auto_assignable"])

    def test_invalid_verifier_evidence_does_not_resurrect_other_downgrade(self):
        def mutate(stage, payload, result):
            if stage == "verify":
                result["matches"][0]["relation"] = "related_specific"
                result["matches"][1]["evidence"][0]["quote"] = "Invented quotation"

        outcome, _ = self.run_match(
            matches=[self.match, candidate(2, "Other scene", "thematic")],
            mutate=mutate,
        )
        self.assertEqual([(m["id"], m["relation"]) for m in outcome.matches], [(1, "related_specific")])
        self.assertEqual(outcome.status, "partial")

    def test_invalid_verifier_schema_cannot_revive_contradicted_hypothesis(self):
        def mutate(stage, payload, result):
            if stage == "verify":
                result["matches"][0]["relation"] = "related_specific"
                result["extra"] = "invalid envelope"

        outcome, _ = self.run_match(mutate=mutate)
        self.assertEqual(outcome.status, "partial")
        self.assertEqual(outcome.matches, [])

    def test_cross_batch_score_selects_later_best_from_48_positives(self):
        records = [{"id": i, "title": f"Theme {i}", "tags": []} for i in range(1, 49)]
        matches = [candidate(r["id"], r["title"], "thematic", relevance=100 if r["id"] == 48 else 50) for r in records]
        outcome, provider = self.run_match(
            records=records,
            matches=matches,
            request=replace(self.request, max_results=1),
            limits=MatchLimits(batch_records=24, positive_limit=24),
        )
        self.assertEqual(outcome.matches[0]["id"], 48)
        self.assertIn(48, [r["id"] for r in provider.calls[-1][1]["catalogue"]])
        self.assertEqual(outcome.status, "partial")
        self.assertIn("verification_shortlist", outcome.diagnostics)

    def test_relevance_score_must_be_bounded_integer(self):
        for score in (-1, 101, True, 2.5):
            with self.subTest(score=score):
                outcome, _ = self.run_match(matches=[{**self.match, "relevance": score}])
                self.assertFalse(outcome.matches)

    def test_honorific_shared_label_cannot_identify_qualified_subject(self):
        self.analysis = analysis_for("John the Baptist")
        for label in ("st-john", "Saint John", "John"):
            for source in ("title", "tag:0"):
                with self.subTest(label=label, source=source):
                    title = label if source == "title" else "Transfiguration"
                    match = candidate(
                        1,
                        title,
                        evidence=[
                            {
                                "ref": f"icon:1:{source}",
                                "quote": label,
                                "role": "identity",
                                "subject_indices": [0],
                            }
                        ],
                    )
                    outcome, _ = self.run_match(
                        request=replace(self.request, primary_text="John the Baptist"),
                        records=[{"id": 1, "title": title, "tags": [label]}],
                        matches=[match],
                    )
                    self.assertEqual(len(outcome.matches), 1)
                    self.assertFalse(outcome.matches[0]["auto_assignable"])

    def test_qualified_titles_can_omit_dates_and_redundant_aliases(self):
        for subject, title in (
            ("42 Elders of the Holy Assembly of Aster (AD 612)", "42 Elders of the Assembly of Aster"),
            ("Saint Luma the Guide (Wayfinder)", "Saint Luma the Guide"),
        ):
            self.analysis = analysis_for(subject)
            for weakened in (None, "assess", "verify"):
                with self.subTest(subject=subject, weakened=weakened):

                    def mutate(stage, payload, result):
                        if stage == weakened:
                            result["matches"][0]["full_request_coverage"] = False

                    outcome, _ = self.run_match(
                        request=replace(self.request, primary_text=subject),
                        records=[{"id": 1, "title": title, "tags": []}],
                        matches=[candidate(1, title)],
                        mutate=mutate,
                    )
                    self.assertEqual(outcome.status, "complete")
                    self.assertEqual(len(outcome.matches), 1)
                    self.assertEqual(outcome.matches[0]["auto_assignable"], weakened is None)

    def test_weak_extra_identity_tag_does_not_poison_strong_title(self):
        title = "Saint Luma the Guide"
        self.analysis = analysis_for("Saint Luma the Guide (Wayfinder)")
        for weakened in (None, "assess", "verify"):
            with self.subTest(weakened=weakened):
                match = candidate(1, title)
                match["evidence"].append(
                    {
                        "ref": "icon:1:tag:0",
                        "quote": "wayfinder",
                        "role": "identity",
                        "subject_indices": [0],
                    }
                )

                def mutate(stage, payload, result):
                    if stage == weakened:
                        result["matches"][0]["evidence"] = result["matches"][0]["evidence"][1:]

                outcome, _ = self.run_match(
                    request=replace(self.request, primary_text=self.analysis["subjects"][0]["span"]["quote"]),
                    records=[{"id": 1, "title": title, "tags": ["wayfinder"]}],
                    matches=[match],
                    mutate=mutate,
                )
                self.assertEqual(outcome.status, "complete")
                self.assertEqual(len(outcome.matches), 1)
                self.assertEqual(outcome.matches[0]["auto_assignable"], weakened is None)

    def test_event_topic_links_are_canonicalized_without_identity_coverage(self):
        title = "Arrival of Luma the Guide"
        self.analysis = analysis_for("Luma the Guide", "Arrival")
        for role in ("event", "topic"):
            for weakened in (None, "assess", "verify"):
                with self.subTest(role=role, weakened=weakened):
                    match = candidate(1, title, "exact_event")
                    match["evidence"].append(
                        {
                            "ref": "icon:1:title",
                            "quote": "Arrival",
                            "role": role,
                            "subject_indices": [0],
                        }
                    )

                    def mutate(stage, payload, result):
                        if stage == weakened:
                            result["matches"][0]["evidence"] = result["matches"][0]["evidence"][1:]

                    outcome, _ = self.run_match(
                        request=replace(self.request, primary_text=title),
                        records=[{"id": 1, "title": title, "tags": []}],
                        matches=[match],
                        mutate=mutate,
                    )
                    self.assertEqual(outcome.status, "complete")
                    self.assertEqual(outcome.matches[0]["auto_assignable"], weakened is None)
                    self.assertTrue(
                        all(
                            e["subject_indices"] == []
                            for e in outcome.matches[0]["evidence"]
                            if e["role"] != "identity"
                        )
                    )

    def test_event_links_and_quotes_still_require_valid_schema_and_grounding(self):
        title = "Arrival of Luma the Guide"
        self.analysis = analysis_for("Luma the Guide", "Arrival")
        for stage_to_break in ("assess", "verify"):
            for changes in (
                {"subject_indices": [1]},
                {"subject_indices": [True]},
                {"subject_indices": ["0"]},
                {"quote": "Invented event"},
                {"ref": "icon:999:title"},
            ):
                with self.subTest(stage=stage_to_break, changes=changes):

                    def mutate(stage, payload, result):
                        if stage == stage_to_break:
                            result["matches"][0]["evidence"][1].update(changes)

                    outcome, _ = self.run_match(
                        request=replace(self.request, primary_text=title),
                        records=[{"id": 1, "title": title, "tags": []}],
                        matches=[candidate(1, title, "exact_event")],
                        mutate=mutate,
                    )
                    self.assertFalse(outcome.matches)
                    self.assertNotEqual(outcome.status, "complete")

    def test_missing_event_portrait_requires_independent_positive_recognition(self):
        title = "Luma of the Valley"
        self.analysis = analysis_for(title, "Arrival")
        for weakened in (None, "assess", "verify"):
            with self.subTest(weakened=weakened):

                def mutate(stage, payload, result):
                    if stage == weakened:
                        result["matches"][0]["generic_portrait"] = False

                outcome, _ = self.run_match(
                    request=replace(self.request, primary_text="Arrival of " + title),
                    records=[{"id": 1, "title": title, "tags": ["portrait"]}],
                    matches=[candidate(1, title, "subject_portrait")],
                    mutate=mutate,
                )
                self.assertEqual(outcome.status, "complete")
                self.assertEqual(outcome.matches[0]["auto_assignable"], weakened is None)

    def test_single_word_non_latin_title_identity_remains_valid(self):
        self.analysis = analysis_for("Արամ")
        outcome, _ = self.run_match(
            request=replace(self.request, primary_text="Արամ"),
            records=[{"id": 1, "title": "Արամ", "tags": []}],
            matches=[candidate(1, "Արամ")],
        )
        self.assertTrue(outcome.matches[0]["auto_assignable"])

    def test_elapsed_calls_and_retry_sleep_share_total_deadline(self):
        now = [0.0]

        class Busy(Exception):
            status_code = 503
            response = SimpleNamespace(headers={"retry-after": "2"})

        def mutate(stage, payload, result):
            now[0] += 7 if stage == "analyze" else 12
            if stage == "assess":
                raise Busy()

        def sleep(seconds):
            now[0] += seconds

        with (
            patch("hub.services.icon_match_service.time.monotonic", side_effect=lambda: now[0]),
            patch(
                "hub.services.icon_match_service.time.sleep",
                side_effect=sleep,
            ) as sleeper,
        ):
            outcome, provider = self.run_match(mutate=mutate, limits=MatchLimits(total_seconds=20, call_seconds=20))
        self.assertEqual([call[2] for call in provider.calls], [20, 13])
        sleeper.assert_called_once_with(1)
        self.assertEqual(outcome.call_count, 2)
        self.assertEqual(outcome.elapsed_seconds, 20)

    @override_settings(OPENAI_API_KEY="test-key")
    @patch("openai.AsyncOpenAI")
    def test_provider_cancels_slow_nonstreaming_body(self, client):
        cancelled = []

        async def trickle(**kwargs):
            try:
                while True:
                    await asyncio.sleep(0.001)
            finally:
                cancelled.append(True)

        client.return_value.__aenter__.return_value = client.return_value
        client.return_value.chat.completions.create = AsyncMock(side_effect=trickle)
        with self.assertRaises(TimeoutError):
            OpenAIIconProvider().call("analyze", {}, {}, 0.02)
        self.assertEqual(cancelled, [True])
        self.assertFalse(client.return_value.chat.completions.create.call_args.kwargs["stream"])
        client.return_value.__aexit__.assert_awaited_once()


class ProviderWireContractTests(SimpleTestCase):
    def test_wire_enum_resolves_canonical_candidate_local_quotes(self):
        records = [{"id": 7, "title": "Arrival of Luma", "tags": ["three companions", "Luma of the Valley"]}]
        match = candidate(7, "Arrival of Luma", "exact_event")
        wire = provider_payload({"candidates": [match]})["candidates"][0]
        wire["evidence"][1]["quote"] = "Arrival"
        wire["evidence"].append({"source": "tag", "quote": "three companions", "role": "topic", "subject_indices": []})
        response = {"batch_id": "test", "assessment_complete": True, "matches": [wire], "exact_event_exists": True}
        result = normalize_provider_matches(response, BATCH, records)
        refs = [e["ref"] for e in result["matches"][0]["evidence"]]
        self.assertEqual(refs, ["icon:7:title", "icon:7:title", "icon:7:tag:1"])
        schema = wire_schema(BATCH)["properties"]["matches"]["items"]["properties"]["evidence"]["items"]
        self.assertEqual(schema["properties"]["source"]["enum"], ["title", "tag"])
        self.assertNotIn("ref", schema["properties"])
        for source, quote in [
            ("request:primary", "Arrival"),
            ("icon:7:title", "Arrival"),
            ("title", "Invented"),
            ("tag", "companions"),
            ("title", "Other entity"),
        ]:
            with self.subTest(source=source, quote=quote):
                wire["evidence"][1].update(source=source, quote=quote)
                self.assertEqual(
                    normalize_provider_matches(response, BATCH, records)["matches"],
                    [{**wire, "invalid_wire_contract": True}],
                )
        wire["evidence"][1].update(source="title", quote="Arrival")
        wire["evidence"][0]["quote"] = "Luma"
        self.assertEqual(
            normalize_provider_matches(response, BATCH, records)["matches"], [{**wire, "invalid_wire_contract": True}]
        )

    def test_invalid_wire_retains_conflict_but_cannot_supply_internal_refs(self):
        match = candidate(1, "Festival of first light", "exact_event", conflict=True)
        response = {"batch_id": "test", "assessment_complete": True, "matches": [match], "exact_event_exists": True}
        result = normalize_provider_matches(response, BATCH, [{"id": 1, "title": "Festival of first light"}])
        self.assertTrue(result["matches"][0]["conflict"])
        self.assertTrue(result["matches"][0]["invalid_wire_contract"])
        self.assertNotIn("invalid_wire_contract", response["matches"][0])

    def test_prompts_separate_analysis_and_candidate_roles(self):
        self.assertNotIn("positive_limit", STAGE_PROMPTS["analyze"])
        self.assertNotIn("subject_portrait", STAGE_PROMPTS["analyze"])
        for stage in ("analyze", "verify"):
            self.assertIn("never abstract virtues", STAGE_PROMPTS[stage])
            self.assertIn("user's action", STAGE_PROMPTS[stage])
            self.assertIn("not an identity qualifier", " ".join(STAGE_PROMPTS[stage].split()))


class RequestIntentTests(SimpleTestCase):
    setUp = SemanticMatchingTests.setUp
    run_match = SemanticMatchingTests.run_match

    def test_theme_intent_blocks_exact_booleans(self):
        self.analysis = analysis_for(None)
        self.analysis["context"] = [{"ref": "request:primary", "quote": "patience"}]
        match = candidate(1, self.records[0]["title"], covered_subjects=[])
        outcome, _ = self.run_match(
            matches=[match], request=replace(self.request, primary_text="A prayer about patience")
        )
        self.assertEqual(outcome.status, "unavailable")
        self.assertEqual(outcome.matches, [])
        self.assertIn("batch_failed", outcome.diagnostics)

    def test_theme_cannot_encode_abstract_subject_or_request_action_event(self):
        for subject, event in [("patience", None), (None, "A prayer about patience")]:
            self.analysis = analysis_for(subject, event)
            self.analysis["intent"] = "theme"
            outcome, _ = self.run_match(request=replace(self.request, primary_text="A prayer about patience"))
            self.assertEqual(outcome.status, "unavailable")
            self.assertIn("analysis_failed", outcome.diagnostics)

    def test_independent_intent_correction_disables_auto(self):
        def mutate(stage, payload, result):
            if stage == "verify":
                result["analysis"] = analysis_for(None)
                result["analysis"]["context"] = [{"ref": "request:primary", "quote": "patience"}]

        self.analysis = analysis_for("patience")
        outcome, _ = self.run_match(
            request=replace(self.request, primary_text="A prayer about patience"), mutate=mutate
        )
        self.assertFalse(outcome.matches[0]["auto_assignable"])

    def test_valid_recommendation_survives_bad_neighbor_in_either_stage(self):
        for broken_stage in ("assess", "verify"):
            for corruption in ("quote", "schema"):

                def mutate(stage, payload, result):
                    if stage == broken_stage:
                        if corruption == "schema":
                            result["matches"][1] = {"id": 2}
                        else:
                            result["matches"][1]["evidence"][0]["quote"] = "Not in metadata"

                outcome, _ = self.run_match(matches=[self.match, candidate(2, "Other scene")], mutate=mutate)
                self.assertEqual(outcome.status, "partial")
                self.assertEqual([m["id"] for m in outcome.matches], [1])
                self.assertFalse(outcome.matches[0]["auto_assignable"])
                if broken_stage == "assess":
                    self.assertFalse(outcome.catalogue_complete)

    def test_event_action_is_not_entity_qualifier(self):
        self.analysis = analysis_for("three companions", "Arrival")
        self.analysis["subjects"][0]["qualifiers"] = [{"ref": "request:primary", "quote": "three"}]
        outcome, _ = self.run_match(
            records=[{"id": 1, "title": "Arrival of the three companions", "tags": []}],
            matches=[candidate(1, "Arrival of the three companions", "exact_event")],
            request=replace(self.request, primary_text="Arrival of the three companions"),
        )
        self.assertTrue(outcome.matches[0]["auto_assignable"])
