"""Paired orchestration, replay provenance and dispatch accounting without network."""

import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from hub.management.commands.evaluate_icon_matching_paired import (
    PairedReplayProvider,
    ReplayHTTPError,
    WireBudget,
    canonical_request,
    evaluate_paired,
    replay_binding,
    usage_summary,
)
from hub.services.icon_match_profiles import CONTROL, LUNA, LUNA_V2, DEFAULT_PROFILES
from hub.services.icon_match_service import VERIFICATION, match_icons, provider_payload, normalize_provider_matches
from hub.tests.icon_match_fixtures import FixtureProvider, analysis_for, candidate


class RecordingFixture(FixtureProvider):
    def __init__(self, profile, matches=(), mutate=None):
        super().__init__(analysis_for("Elder Vela"), matches, mutate)
        self.profile = profile
        self.entries = []

    def call(self, stage, payload, schema, timeout):
        response = super().call(stage, payload, schema, timeout)
        if stage != "analyze":
            response["matches"] = provider_payload({"candidates": response["matches"]})["candidates"]
        self.entries.append(
            {
                **replay_binding(stage, payload, schema, self.profile),
                "response": deepcopy(response),
                "model": self.profile.model + "-recorded",
            }
        )
        # The fixture returns internal evidence; stored entries are wire evidence.
        return normalize_provider_matches(response, schema, payload["catalogue"]) if stage != "analyze" else response


class PairedEvaluationTests(SimpleTestCase):
    catalogue = [{"id": 2, "title": "Other", "tags": []}, {"id": 1, "title": "Elder Vela", "tags": ["portrait"]}]
    requests = [{"kind": "feast", "title": "Elder Vela", "tags": ["courage"]}, "Elder Vela"]

    def recordings(self, *, matches=True, profiles=DEFAULT_PROFILES):
        skeleton = evaluate_paired(self.catalogue, self.requests, profiles=profiles)
        replays = {}
        providers = {}
        for pair, item in zip(skeleton["pairs"], self.requests, strict=True):
            replays[pair["case_id"]] = {}
            for profile in profiles:
                provider = RecordingFixture(profile, [candidate(1, "Elder Vela")] if matches else [])
                match_icons(self.catalogue, canonical_request(item), provider=provider, profile=profile)
                replays[pair["case_id"]][profile.id] = provider.entries
                providers[(pair["case_id"], profile.id)] = provider
        return replays, providers

    def test_selected_pairs_replay_same_inputs_and_keep_case_ids(self):
        default = evaluate_paired(self.catalogue, self.requests)
        self.assertEqual(DEFAULT_PROFILES, (CONTROL, LUNA))
        self.assertEqual(default["selected_profile_ids"], ["control-v1", "luna-v1"])
        for profiles in ((CONTROL, LUNA), (LUNA, LUNA_V2), (CONTROL, LUNA_V2), (LUNA_V2, LUNA)):
            with self.subTest(profiles=[p.id for p in profiles]):
                replays, _ = self.recordings(profiles=profiles)
                with (
                    patch("hub.management.commands.evaluate_icon_matching_paired.OpenAIIconProvider") as live,
                    patch(
                        "hub.management.commands.evaluate_icon_matching_paired.match_icons", wraps=match_icons
                    ) as matcher,
                ):
                    report = evaluate_paired(
                        self.catalogue, self.requests, replays=replays, profiles=[p.id for p in profiles]
                    )
                live.assert_not_called()
                self.assertEqual(report["wire_calls"], 0)
                self.assertEqual(report["complete_pair_count"], 2)
                ids = [p.id for p in profiles]
                self.assertEqual(report["selected_profile_ids"], ids)
                self.assertEqual(list(report["summary"]), ids)
                self.assertEqual(report["pairs"][0]["arm_order"], ids)
                self.assertEqual(report["pairs"][1]["arm_order"], ids[::-1])
                self.assertEqual("Prompt-only" in report["comparison"], CONTROL not in profiles)
                for index in (0, 2):
                    self.assertEqual(matcher.call_args_list[index].args, matcher.call_args_list[index + 1].args)
                for old, new in zip(default["pairs"], report["pairs"], strict=True):
                    self.assertEqual(old["case_id"], new["case_id"])
                    self.assertEqual(old["input_digest"], new["input_digest"])
                    self.assertTrue(all(a["input_digest"] == new["input_digest"] for a in new["arms"].values()))
                    self.assertEqual(set(new["arms"]), set(ids))

    def test_profile_preflight_rejects_invalid_selection_without_billing(self):
        for profiles in (
            None,
            [],
            [LUNA],
            [CONTROL, LUNA, LUNA_V2],
            "luna-v1",
            [LUNA, LUNA],
            ["luna-v1", LUNA],
            ["unknown", "luna-v2"],
            [object(), LUNA_V2],
        ):
            with (
                self.subTest(profiles=profiles),
                patch("hub.management.commands.evaluate_icon_matching_paired.OpenAIIconProvider") as live,
                patch("hub.management.commands.evaluate_icon_matching_paired.PairedReplayProvider") as replay,
                patch("hub.management.commands.evaluate_icon_matching_paired.OfflineUnavailableProvider") as offline,
                patch("hub.management.commands.evaluate_icon_matching_paired.WireBudget.consume") as dispatch,
                patch("openai.AsyncOpenAI") as sdk,
            ):
                for mode in ({}, {"replays": {}}, {"live": True, "maximum_wire_calls": 10}):
                    with self.assertRaises(ValueError):
                        evaluate_paired(self.catalogue, self.requests, profiles=profiles, **mode)
                for mocked in (live, replay, offline, dispatch, sdk):
                    mocked.assert_not_called()

    def test_luna_replay_cannot_cross_prompt_versions(self):
        for source, target in ((LUNA, LUNA_V2), (LUNA_V2, LUNA)):
            replays, _ = self.recordings(profiles=(CONTROL, source))
            for arms in replays.values():
                arms[target.id] = arms.pop(source.id)
            report = evaluate_paired(self.catalogue, self.requests, profiles=(CONTROL, target), replays=replays)
            for pair in report["pairs"]:
                arm = pair["arms"][target.id]
                self.assertEqual(arm["state"], "failed")
                self.assertEqual(arm["outcome"]["matches"], [])
                self.assertIn("replay_binding_mismatch", arm["replay_diagnostics"])
                self.assertEqual(arm["usage"]["per_call"], [])
            self.assertEqual(report["wire_calls"], 0)

    def test_command_profile_selection_and_invalid_selection_preserve_output(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalogue, requests, output = (root / name for name in ("catalogue.json", "requests.json", "output.json"))
            catalogue.write_text(json.dumps(self.catalogue))
            requests.write_text(json.dumps(self.requests))
            kwargs = dict(catalogue_json=str(catalogue), requests_json=str(requests), output_json=str(output))
            call_command("evaluate_icon_matching_paired", "--profiles", "luna-v1", "luna-v2", **kwargs)
            saved = output.read_text()
            self.assertEqual(json.loads(saved)["selected_profile_ids"], ["luna-v1", "luna-v2"])
            for profiles in (["luna-v1", "luna-v1"], ["unknown", "luna-v2"], ["luna-v2"]):
                with (
                    patch("hub.management.commands.evaluate_icon_matching_paired.OpenAIIconProvider") as live,
                    patch("hub.management.commands.evaluate_icon_matching_paired.WireBudget.consume") as dispatch,
                ):
                    with self.assertRaises(CommandError):
                        call_command(
                            "evaluate_icon_matching_paired",
                            profiles=profiles,
                            live=True,
                            maximum_wire_calls=10,
                            **kwargs,
                        )
                    live.assert_not_called()
                    dispatch.assert_not_called()
                    self.assertEqual(output.read_text(), saved)

    def test_offline_default_and_live_require_positive_budget_before_initialization(self):
        with patch("hub.management.commands.evaluate_icon_matching_paired.OpenAIIconProvider") as provider:
            report = evaluate_paired(self.catalogue, self.requests)
            for maximum in (None, 0, -1, True):
                with self.assertRaises(ValueError):
                    evaluate_paired(self.catalogue, self.requests, live=True, maximum_wire_calls=maximum)
        provider.assert_not_called()
        self.assertEqual(report["wire_calls"], 0)
        self.assertEqual(len(report["pairs"]), 2)
        self.assertEqual(report["summary"][CONTROL.id]["state_counts"], {"failed": 2})
        self.assertEqual(report["summary"][CONTROL.id]["complete_no_match_count"], 0)
        self.assertIsNone(report["pairs"][0]["arms"][LUNA.id]["usage"]["totals"]["total_tokens"])

    def test_preflight_rejects_malformed_later_requests_before_any_provider_or_dispatch(self):
        invalid_items = [
            None,
            42,
            ["Elder Vela"],
            {"title": "Elder Vela", "context_terms": None},
            {"title": "Elder Vela", "context_terms": "courage"},
            {"title": "Elder Vela", "tags": {"courage": True}},
            {"title": "Elder Vela", "tags": [None]},
            {"title": "Elder Vela", "context_terms": ["courage"] * 33},
            {"title": None},
            {"title": " "},
            {"title": "Elder Vela", "kind": None},
            *[{"title": "Elder Vela", "max_results": value} for value in (None, True, 1.5, 0, 101)],
            {"title": "x" * 16001},
            # JSON can decode this string, but canonical UTF-8 digest encoding cannot.
            {"title": "\ud800"},
        ]
        for invalid in invalid_items:
            for mode in ({}, {"replays": {}}, {"live": True, "maximum_wire_calls": 10}):
                with (
                    self.subTest(invalid=repr(invalid)[:100], mode=mode),
                    patch("hub.management.commands.evaluate_icon_matching_paired.OpenAIIconProvider") as live,
                    patch("hub.management.commands.evaluate_icon_matching_paired.PairedReplayProvider") as replay,
                    patch(
                        "hub.management.commands.evaluate_icon_matching_paired.OfflineUnavailableProvider"
                    ) as offline,
                    patch("hub.management.commands.evaluate_icon_matching_paired.WireBudget.consume") as dispatch,
                    patch("openai.AsyncOpenAI") as sdk,
                ):
                    with self.assertRaisesRegex(ValueError, "Invalid request at index 1:"):
                        evaluate_paired(self.catalogue, ["Elder Vela", invalid], **mode)
                    for mocked in (live, replay, offline, dispatch, sdk):
                        mocked.assert_not_called()

    def test_preflight_keeps_valid_aliases_precedence_and_defaults(self):
        request = canonical_request("Elder Vela")
        self.assertEqual(
            (request.kind, request.auto_assign_policy, request.context_terms, request.max_results),
            ("feast", "feast_strict", (), 10),
        )
        for alias in ("primary_text", "title", "name", "text"):
            request = canonical_request({alias: "Elder Vela", "tags": ["courage"]})
            self.assertEqual(
                (request.primary_text, request.kind, request.auto_assign_policy, request.context_terms),
                ("Elder Vela", "content", "content_suggest", ("courage",)),
            )
        request = canonical_request(
            {
                "primary_text": "Elder Vela",
                "title": "ignored",
                "context_terms": (),
                "tags": ["ignored"],
                "max_results": 100,
            }
        )
        self.assertEqual((request.primary_text, request.context_terms, request.max_results), ("Elder Vela", (), 100))

    def test_command_invalid_later_request_is_clear_command_error_and_preserves_output(self):
        invalid_items = [
            (7, "expected a string or object"),
            ({"title": "Elder Vela", "context_terms": None}, "context_terms/tags must be an array"),
            ({"title": "Elder Vela", "context_terms": "courage"}, "context_terms/tags must be an array"),
            ({"title": "\ud800"}, "cannot serialize canonical input"),
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalogue, requests, output = (root / name for name in ("catalogue.json", "requests.json", "output.json"))
            catalogue.write_text(json.dumps(self.catalogue))
            output.write_text("previous report")
            for invalid, message in invalid_items:
                requests.write_text(json.dumps(["Elder Vela", invalid]))
                with (
                    self.subTest(message=message),
                    patch("hub.management.commands.evaluate_icon_matching_paired.OpenAIIconProvider") as provider,
                    patch("hub.management.commands.evaluate_icon_matching_paired.WireBudget.consume") as dispatch,
                ):
                    with self.assertRaisesRegex(CommandError, f"Invalid request at index 1: {message}"):
                        call_command(
                            "evaluate_icon_matching_paired",
                            catalogue_json=str(catalogue),
                            requests_json=str(requests),
                            output_json=str(output),
                            live=True,
                            maximum_wire_calls=10,
                        )
                    provider.assert_not_called()
                    dispatch.assert_not_called()
                    self.assertEqual(output.read_text(), "previous report")

    def test_paired_canonical_inputs_order_configuration_and_independent_replays(self):
        replays, _ = self.recordings()
        original = deepcopy(replays)
        with (
            patch("hub.management.commands.evaluate_icon_matching_paired.match_icons", wraps=match_icons) as matcher,
            patch("hub.management.commands.evaluate_icon_matching_paired.OpenAIIconProvider") as live,
        ):
            report = evaluate_paired(self.catalogue, self.requests, replays=replays)
        live.assert_not_called()
        self.assertEqual(replays, original)
        self.assertEqual(report["complete_pair_count"], 2)
        self.assertEqual(report["top_result_agreement_count"], 2)
        self.assertEqual(report["pairs"][0]["arm_order"], [CONTROL.id, LUNA.id])
        self.assertEqual(report["pairs"][1]["arm_order"], [LUNA.id, CONTROL.id])
        calls = matcher.call_args_list
        self.assertEqual(len({id(c.kwargs["provider"]) for c in calls}), 4)
        for index in (0, 2):
            self.assertEqual(calls[index].args, calls[index + 1].args)
            self.assertIsNot(calls[index].args[0], calls[index + 1].args[0])
            self.assertEqual([r["id"] for r in calls[index].args[0]], [1, 2])
        for pair in report["pairs"]:
            for profile in (CONTROL, LUNA):
                arm = pair["arms"][profile.id]
                self.assertEqual(arm["profile_hash"], profile.metadata()["profile_hash"])
                self.assertEqual(arm["configured_model"], profile.model)
                self.assertEqual(arm["returned_models"], [profile.model + "-recorded"])
                self.assertEqual(arm["reasoning_effort"], profile.reasoning_effort)
                self.assertEqual(arm["input_digest"], pair["input_digest"])
                self.assertEqual(arm["limits"]["positive_limit"], profile.positive_limit)
                self.assertTrue(arm["outcome"]["matches"][0]["auto_assignable"])

    def test_failure_and_missing_replay_keep_both_arms_and_pairs(self):
        replays, _ = self.recordings(matches=False)
        first, second = replays.values()
        first[LUNA.id][0]["error"] = {"category": "invalid_response"}
        del second[CONTROL.id]
        report = evaluate_paired(self.catalogue, self.requests, replays=replays)
        self.assertEqual(len(report["pairs"]), 2)
        self.assertTrue(all(len(p["arms"]) == 2 for p in report["pairs"]))
        for arm in (CONTROL, LUNA):
            self.assertEqual(report["summary"][arm.id]["state_counts"], {"evaluated": 1, "failed": 1})
            self.assertEqual(report["summary"][arm.id]["complete_no_match_count"], 1)
        self.assertEqual(report["complete_pair_count"], 0)

    def test_replay_binding_rejects_stage_payload_schema_profile_and_missing_hash(self):
        replays, providers = self.recordings()
        case_id = next(iter(replays))
        fixture = providers[(case_id, LUNA.id)]
        stage, payload, _ = fixture.calls[-1]
        entry = replays[case_id][LUNA.id][-1]
        self.assertEqual(stage, "verify")
        for key in ("stage", "payload_hash", "schema_hash", "profile_hash"):
            for replacement in (None, "stale"):
                record = {**entry, key: replacement}
                provider = PairedReplayProvider([record], LUNA)
                with self.assertRaisesRegex(ValueError, "replay_binding_mismatch"):
                    provider.call(stage, payload, VERIFICATION, 1)
                self.assertEqual(provider.usage_records, [])
                self.assertEqual(provider.model_ids, set())
        # A prior verifier answer cannot survive a changed shortlist or rationale.
        for change in ("candidates", "catalogue"):
            changed = deepcopy(payload)
            changed[change] = []
            with self.assertRaisesRegex(ValueError, "replay_binding_mismatch"):
                PairedReplayProvider([entry], LUNA).call(stage, changed, VERIFICATION, 1)
        replays[case_id][LUNA.id][-1]["profile_hash"] = "old-policy"
        report = evaluate_paired(self.catalogue, self.requests, replays=replays)
        arm = report["pairs"][0]["arms"][LUNA.id]
        self.assertEqual(arm["state"], "partial")
        self.assertIn("replay_binding_mismatch", arm["replay_diagnostics"])
        self.assertTrue(all(not m["auto_assignable"] for m in arm["outcome"]["matches"]))

    def test_sanitized_replay_transients_retry_and_terminal_errors_do_not(self):
        for error, retries in (
            ({"category": "http", "status": 429}, True),
            ({"category": "timeout"}, True),
            ({"category": "http", "status": 400}, False),
            ({"category": "invalid_response"}, False),
            ({"category": "arbitrary", "body": "private"}, False),
        ):
            replays, _ = self.recordings(matches=False)
            case_id = next(iter(replays))
            entries = replays[case_id][CONTROL.id]
            entries.insert(0, {**entries[0], "error": error})
            with patch("hub.services.icon_match_service.time.sleep"):
                report = evaluate_paired(self.catalogue, self.requests[:1], replays=replays)
            arm = report["pairs"][0]["arms"][CONTROL.id]
            self.assertEqual(arm["state"], "evaluated" if retries else "failed")
            self.assertEqual(arm["outcome"]["call_count"], 3 if retries else 1)
            self.assertEqual(arm["wire_calls"], 0)
            self.assertNotIn("private", json.dumps(report))

    @override_settings(OPENAI_API_KEY="test-key", ICON_MATCH_MODEL="must-not-leak")
    @patch("openai.AsyncOpenAI")
    def test_actual_dispatch_budget_counts_transient_retry_across_arms(self, client):
        client.return_value.__aenter__.return_value = client.return_value
        client.return_value.chat.completions.create = AsyncMock(
            side_effect=[ReplayHTTPError(429), self.response(analysis_for("Elder Vela"))]
        )
        report = evaluate_paired(self.catalogue, self.requests, live=True, maximum_wire_calls=2)
        self.assertEqual(client.return_value.chat.completions.create.await_count, 2)
        self.assertEqual(report["wire_calls"], 2)
        first = report["pairs"][0]["arms"]
        self.assertEqual(first[CONTROL.id]["state"], "partial_budget")
        self.assertEqual(first[CONTROL.id]["wire_calls"], 2)
        self.assertEqual(first[LUNA.id]["state"], "skipped_budget")
        self.assertEqual(report["summary"][LUNA.id]["complete_no_match_count"], 0)
        self.assertEqual(len(report["pairs"]), 2)
        self.assertTrue(
            all(c.kwargs["model"] == "gpt-4.1-mini" for c in client.return_value.chat.completions.create.call_args_list)
        )

    @override_settings(OPENAI_API_KEY="test-key")
    @patch("openai.AsyncOpenAI")
    def test_actual_dispatch_budget_includes_analysis_format_repair(self, client):
        client.return_value.__aenter__.return_value = client.return_value
        client.return_value.chat.completions.create = AsyncMock(
            side_effect=[self.response(analysis_for("invented")), self.response(analysis_for("Elder Vela"))]
        )
        report = evaluate_paired(self.catalogue, self.requests[:1], live=True, maximum_wire_calls=2)
        self.assertEqual(client.return_value.chat.completions.create.await_count, 2)
        payloads = [
            json.loads(c.kwargs["messages"][1]["content"])
            for c in client.return_value.chat.completions.create.call_args_list
        ]
        self.assertIn("validation_feedback", payloads[1])
        self.assertEqual(payloads[0]["request"], payloads[1]["request"])
        self.assertEqual(report["pairs"][0]["arms"][CONTROL.id]["state"], "partial_budget")

    @staticmethod
    def response(value, *, usage=None, content=None, finish="stop"):
        return SimpleNamespace(
            model="returned-model-snapshot",
            usage=usage,
            choices=[
                SimpleNamespace(
                    finish_reason=finish,
                    message=SimpleNamespace(content=json.dumps(value) if content is None else content),
                )
            ],
        )

    @override_settings(OPENAI_API_KEY="test-key")
    @patch("openai.AsyncOpenAI")
    def test_usage_and_returned_model_recorded_before_malformed_or_truncated_output(self, client):
        usage_data = {
            "prompt_tokens": 20,
            "completion_tokens": 3,
            "total_tokens": 23,
            "prompt_tokens_details": {"cached_tokens": 10},
            "completion_tokens_details": {"reasoning_tokens": 1},
        }
        usage = SimpleNamespace(model_dump=lambda **kw: usage_data)
        client.return_value.__aenter__.return_value = client.return_value
        for finish in ("stop", "length"):
            client.return_value.chat.completions.create = AsyncMock(
                return_value=self.response({}, usage=usage, content="malformed JSON", finish=finish)
            )
            report = evaluate_paired(self.catalogue, self.requests[:1], live=True, maximum_wire_calls=1)
            arm = report["pairs"][0]["arms"][CONTROL.id]
            self.assertEqual(arm["state"], "failed")
            self.assertEqual(arm["returned_models"], ["returned-model-snapshot"])
            self.assertEqual(arm["usage"]["per_call"], [usage_data])
            self.assertEqual(arm["usage"]["totals"]["total_tokens"], 23)
            self.assertEqual(report["usage"]["totals"]["total_tokens"], 23)
            self.assertEqual(report["pairs"][0]["arms"][LUNA.id]["state"], "skipped_budget")
        self.assertIsNone(usage_summary([None, usage_data])["totals"]["total_tokens"])
        self.assertFalse(usage_summary([None])["complete"])
        self.assertFalse(usage_summary([{}])["complete"])

    @override_settings(OPENAI_API_KEY="test-key")
    @patch("openai.AsyncOpenAI")
    def test_unsupported_model_parameters_fail_without_substitution(self, client):
        client.return_value.__aenter__.return_value = client.return_value
        client.return_value.chat.completions.create = AsyncMock(side_effect=ReplayHTTPError(400))
        report = evaluate_paired(self.catalogue, self.requests[:1], live=True, maximum_wire_calls=4)
        calls = client.return_value.chat.completions.create.call_args_list
        self.assertEqual([c.kwargs["model"] for c in calls], [CONTROL.model, LUNA.model])
        self.assertNotIn("reasoning_effort", calls[0].kwargs)
        self.assertEqual(calls[1].kwargs["reasoning_effort"], "none")
        self.assertTrue(all(a["state"] == "failed" for a in report["pairs"][0]["arms"].values()))

    def test_command_offline_and_input_protection(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalogue, requests, output = (root / name for name in ("catalogue.json", "requests.json", "output.json"))
            catalogue.write_text(json.dumps(self.catalogue))
            requests.write_text(json.dumps(self.requests))
            kwargs = dict(catalogue_json=str(catalogue), requests_json=str(requests), output_json=str(output), limit=1)
            with patch("hub.management.commands.evaluate_icon_matching_paired.OpenAIIconProvider") as live:
                call_command("evaluate_icon_matching_paired", **kwargs)
                with self.assertRaises(CommandError):
                    call_command("evaluate_icon_matching_paired", **kwargs, live=True)
            live.assert_not_called()
            self.assertEqual(json.loads(output.read_text())["request_count"], 1)
            with self.assertRaises(CommandError):
                call_command("evaluate_icon_matching_paired", **{**kwargs, "output_json": str(catalogue)})

    def test_budget_denial_does_not_increment_wire_usage(self):
        budget = WireBudget(1)
        budget.consume()
        with self.assertRaisesRegex(RuntimeError, "wire_budget_exhausted"):
            budget.consume()
        self.assertEqual((budget.used, budget.denied), (1, 1))
