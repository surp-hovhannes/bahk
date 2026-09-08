"""Durable, leased two-stage analysis. All provider calls run outside transactions."""

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from icons.models import (
    Icon,
    IconAnalysis,
    IconAssertion,
    IconObservation,
    IconTaxonomyProjection,
    IconTaxonomyWork,
    TaxonomyCall,
)
from icons.services.taxonomy_budget import BudgetExhausted, reserve
from icons.services.taxonomy_inputs import (
    analysis_dependencies,
    dependencies_current,
    digest,
    image_input,
    metadata,
    versions,
)
from icons.services.taxonomy_rules import validate_assertions
from icons.services.taxonomy_vocabulary import catalogue_claims, catalogue_sources
from icons.services.vision_provider import (
    OBSERVATION_SCHEMA,
    VisionProvider,
    bounded_validate,
    comparison_schema,
)

MAX_ATTEMPTS = 3
LEASE_SECONDS = 600


class StaleInput(Exception):
    pass


class ObservationBusy(Exception):
    pass


def independent_observation(key, icon, provider, derivative, analysis, budget_name, check_current):
    token = str(uuid.uuid4())
    with transaction.atomic():
        cached, _ = IconObservation.objects.get_or_create(key=key, defaults={"church_id": icon.church_id})
        cached = IconObservation.objects.select_for_update().get(pk=cached.pk)
        if cached.state == "complete":
            return cached
        if cached.lease_until and cached.lease_until > timezone.now():
            raise ObservationBusy()
        updated = IconObservation.objects.filter(pk=cached.pk, lease_token=cached.lease_token).update(
            lease_token=token, lease_until=timezone.now() + timedelta(seconds=LEASE_SECONDS)
        )
        if not updated:
            raise ObservationBusy()
    try:
        check_current()
        evidence, returned_model, usage = wire_call(
            provider, "observe", {}, OBSERVATION_SCHEMA, image=derivative, analysis=analysis, budget_name=budget_name
        )
        IconObservation.objects.filter(pk=cached.pk, lease_token=token).update(
            evidence=evidence, returned_model=returned_model, state="complete", lease_token="", lease_until=None
        )
        cached.refresh_from_db()
        return cached
    finally:
        IconObservation.objects.filter(pk=cached.pk, lease_token=token).update(lease_token="", lease_until=None)


def due_work():
    now = timezone.now()
    return IconTaxonomyWork.objects.filter(
        Q(state__in=["pending", "retry"], available_at__lte=now) | Q(state="running", lease_until__lte=now)
    ).order_by("available_at", "pk")


@transaction.atomic
def claim(icon_id):
    if not Icon.objects.select_for_update().filter(pk=icon_id).exists():
        return None
    from icons.services.ingestion import INLINE_OWNED

    if INLINE_OWNED.get():
        work = (
            IconTaxonomyWork.objects.select_for_update()
            .filter(icon_id=icon_id, state="inline_pending", lease_token=INLINE_OWNED.get())
            .first()
        )
    else:
        work = due_work().select_for_update().filter(icon_id=icon_id).first()
    if work is None:
        return None
    if work.attempts >= MAX_ATTEMPTS:
        work.state, work.error = "unavailable", "attempts_exhausted"
        work.save(update_fields=["state", "error"])
        return None
    token = str(uuid.uuid4())
    # Conditional claim is an extra guard for databases without row locks.
    updated = IconTaxonomyWork.objects.filter(
        pk=work.pk, revision=work.revision, lease_token=work.lease_token, state=work.state, attempts=work.attempts
    ).update(
        state="inline_running" if INLINE_OWNED.get() else "running",
        lease_token=token,
        lease_until=timezone.now() + timedelta(seconds=LEASE_SECONDS),
        attempts=work.attempts + 1,
    )
    if not updated:
        return None
    work.refresh_from_db()
    return work


def current(work, input_metadata, image_digest, expected_versions, *, read_image=True, dependencies=None):
    if dependencies is not None and not dependencies_current(dependencies):
        raise StaleInput("meaning_changed")
    if versions() != expected_versions:
        raise StaleInput("versions_changed")
    if not IconTaxonomyWork.objects.filter(
        pk=work.pk,
        revision=work.revision,
        lease_token=work.lease_token,
        state=work.state,
        lease_until__gt=timezone.now(),
    ).exists():
        raise StaleInput("lease_or_revision_changed")
    icon = Icon.objects.filter(pk=work.icon_id).first()
    if icon is None or metadata(icon) != input_metadata:
        raise StaleInput("metadata_changed")
    if read_image and image_input(icon)[0] != image_digest:
        raise StaleInput("image_changed")
    return icon


def wire_call(provider, stage, payload, schema, *, image=None, analysis=None, budget_name=None, timeout=None):
    if connection.in_atomic_block:
        raise RuntimeError("provider_inside_transaction")
    if timeout is not None and timeout <= 0:
        raise TimeoutError("deadline")
    from icons.services.vision_provider import HARDENED_PROMPTS

    reservation = reserve(
        stage,
        payload,
        image=bool(image),
        analysis=analysis,
        budget_name=budget_name,
        schema=schema,
        instructions=HARDENED_PROMPTS[stage],
    )
    # Crash/timeout after this point retains the entire reservation as unknown.
    value, returned_model, usage = provider.call(
        stage, payload, schema, image=image, **({"timeout": timeout} if timeout is not None else {})
    )
    TaxonomyCall.objects.filter(pk=reservation.pk).update(
        usage=usage, returned_model=returned_model[:100], state="returned"
    )
    if stage == "compare":
        from icons.services.taxonomy_evidence import bounded_comparison

        bounded_comparison(value)
    else:
        bounded_validate(value, schema)
    return value, returned_model, usage


def finish(work, state, error=""):
    updates = dict(
        state="inline_retry" if state == "retry" and work.state == "inline_running" else state,
        error=error,
        lease_until=None,
        lease_token="",
    )
    if state == "retry":
        updates["available_at"] = timezone.now() + timedelta(seconds=30 * 2 ** (work.attempts - 1))
    return IconTaxonomyWork.objects.filter(pk=work.pk, revision=work.revision, lease_token=work.lease_token).update(
        **updates
    )


def transient(exc):
    from openai import APIConnectionError, APITimeoutError, RateLimitError, APIStatusError

    return isinstance(exc, (TimeoutError, ConnectionError, APIConnectionError, APITimeoutError, RateLimitError)) or (
        isinstance(exc, APIStatusError) and exc.status_code >= 500
    )


def compatible_retained_observation(icon, image_digest, version):
    """Explicit recovery only; never mix model/image/church identities.

    Legacy observations remain labelled legacy and pass the conservative legacy
    reader. They are not relabelled as a response to the new observation prompt.
    """
    from icons.services.vision_provider import LEGACY_OBSERVATION_SCHEMA

    for prior in (
        IconAnalysis.objects.filter(
            icon=icon,
            church_id=icon.church_id,
            image_digest=image_digest,
            observation__state="complete",
            versions__model=version["model"],
            versions__image_processor=version["image_processor"],
        )
        .select_related("observation")
        .order_by("-created_at")
    ):
        contract = (prior.versions.get("schema"), prior.versions.get("prompt"))
        if contract == (version["schema"], version["prompt"]) and prior.versions.get("profile") == version["profile"]:
            schema = OBSERVATION_SCHEMA
        elif contract == ("icon-evidence-v1", "observation-comparison-v1") and version["profile"] == "luna-none-v1":
            schema = LEGACY_OBSERVATION_SCHEMA
        else:
            continue
        try:
            bounded_validate(prior.observation.evidence, schema)
        except ValueError:
            continue
        return prior.observation
    return None


def process_icon(icon_id, *, provider=None, budget_name=None, reuse_observations=False):
    if provider is None and not getattr(settings, "ICON_TAXONOMY_DISPATCH_ENABLED", False):
        return "disabled"
    if connection.in_atomic_block:
        raise RuntimeError("pipeline_inside_transaction")
    work = claim(icon_id)
    if work is None:
        return "not_due"
    analysis = None
    try:
        icon = Icon.objects.get(pk=icon_id)
        inputs = metadata(icon)
        if digest(inputs) != work.fingerprint:
            raise StaleInput("unscheduled_edit")
        image_digest, derivative = image_input(icon)
        if image_digest != icon.image_content_digest:
            from icons.services.ingestion import refresh_content

            refresh_content(icon)
            raise StaleInput("content_generation_changed")
        claims = catalogue_claims(inputs)
        sources = catalogue_sources(inputs)
        version = versions()
        prior = (
            IconAnalysis.objects.filter(icon=icon, image_digest=image_digest, versions=version, claims__metadata=inputs)
            .order_by("-created_at")
            .first()
        )
        dependencies = analysis_dependencies(inputs, claims, prior=prior.dependencies if prior else None)
        input_digest = digest(
            {"metadata": inputs, "image": image_digest, "versions": version, "dependencies": dependencies["digest"]}
        )
        if prior and dependencies_current(prior.dependencies):
            analysis = prior
            dependencies = prior.dependencies
        else:
            analysis, _ = IconAnalysis.objects.get_or_create(
                icon=icon,
                digest=input_digest,
                defaults={
                    "church_id": icon.church_id,
                    "image_digest": image_digest,
                    "versions": version,
                    "dependencies": dependencies,
                    "claims": {"metadata": inputs, "claims": claims, "sources": sources},
                },
            )
        if analysis.state == "unavailable":
            finish(work, "unavailable", analysis.error)
            return "unavailable"
        if analysis.state != "complete":
            analysis.attempts += 1
            analysis.save(update_fields=["attempts"])
            provider = provider or VisionProvider()
            observation_key = digest(
                {
                    "church": icon.church_id,
                    "image": image_digest,
                    "model": version["model"],
                    "profile": version["profile"],
                    "prompt": version["prompt"],
                    "schema": version["schema"],
                    "processor": version["image_processor"],
                }
            )
            retained = None
            if reuse_observations:
                retained = compatible_retained_observation(icon, image_digest, version)
            cached = retained or independent_observation(
                observation_key,
                icon,
                provider,
                derivative,
                analysis,
                budget_name,
                lambda: current(work, inputs, image_digest, version, dependencies=dependencies),
            )
            current(work, inputs, image_digest, version, dependencies=dependencies)
            analysis.observation = cached
            analysis.claims["observation_reuse"] = "retained_compatible" if retained else "current_contract"
            analysis.claims["observation_key"] = cached.key
            analysis.save(update_fields=["observation", "claims"])
            observation = cached.evidence
            if not dependencies_current(dependencies):
                raise StaleInput("meaning_changed")
            dependencies = analysis_dependencies(inputs, claims, observation, prior=dependencies)
            analysis.dependencies = dependencies
            analysis.save(update_fields=["dependencies"])
            if not claims:
                analysis.comparison = {"assertions": []}
                analysis.save(update_fields=["comparison"])
            if not analysis.comparison:
                current(work, inputs, image_digest, version, dependencies=dependencies)
                from icons.models import TaxonomyConcept

                candidates = list(
                    TaxonomyConcept.objects.filter(pk__in=[c["concept"] for c in claims]).values(
                        "id", "kind", "label", "definition"
                    )
                )
                payload = {
                    "metadata": {key: inputs[key] for key in ("title", "tags", "filename", "filename_provenance")},
                    "sources": sources,
                    "claims": claims,
                    "concepts": candidates,
                    "observations": observation,
                }
                if len(str(payload).encode()) > 64000:
                    raise ValueError("claims_too_large")
                comparison, returned_model, usage = wire_call(
                    provider,
                    "compare",
                    payload,
                    comparison_schema(claims, observation),
                    analysis=analysis,
                    budget_name=budget_name,
                )
                analysis.comparison = comparison
                analysis.save(update_fields=["comparison"])
            from icons.services.taxonomy_evidence import normalize_comparison

            raw_comparison = {"assertions": analysis.comparison["assertions"]}
            _, _, diagnostics = normalize_comparison(claims, observation, raw_comparison)
            analysis.comparison = {**raw_comparison, "diagnostics": diagnostics}
            analysis.save(update_fields=["comparison"])
            assertions = validate_assertions(claims, observation, raw_comparison, sources=sources)
        else:
            assertions = list(
                analysis.assertions.values("concept_id", "attribute", "status", "evidence_level", "rule", "evidence")
            )
            assertions = [
                {**{k: v for k, v in a.items() if k != "concept_id"}, "concept": a["concept_id"]} for a in assertions
            ]
        # Lock order matches ingestion: icon, then work. Recheck actual bytes before publication.
        current(work, inputs, image_digest, version, dependencies=dependencies)
        with transaction.atomic():
            Icon.objects.select_for_update().get(pk=icon_id)
            IconTaxonomyWork.objects.select_for_update().get(pk=work.pk)
            current(work, inputs, image_digest, version, read_image=False, dependencies=dependencies)
            if analysis.state != "complete":
                IconAssertion.objects.bulk_create(
                    [
                        IconAssertion(
                            analysis=analysis, concept_id=a["concept"], **{k: v for k, v in a.items() if k != "concept"}
                        )
                        for a in assertions
                    ]
                )
                analysis.state, analysis.completed_at = "complete", timezone.now()
                analysis.usage = list(
                    analysis.taxonomycall_set.values("stage", "usage", "returned_model", "reserved_tokens", "state")
                )
                analysis.save(update_fields=["state", "completed_at", "usage"])
            IconTaxonomyProjection.objects.update_or_create(
                icon_id=icon_id,
                defaults={
                    "church_id": icon.church_id,
                    "analysis": analysis,
                    "revision": work.revision,
                    "fingerprint": work.fingerprint,
                    "attributes": assertions,
                },
            )
            finish(work, "complete")
        from icons.cache import IconViewCache

        IconViewCache.clear_all()
        return "complete"
    except StaleInput:
        if finish(work, "superseded", "stale_input"):
            from icons.services.ingestion import schedule

            schedule(icon_id, force=True)
        return "superseded"
    except ObservationBusy:
        if finish(work, "retry", "observation_leased"):
            IconTaxonomyWork.objects.filter(
                pk=work.pk, revision=work.revision, state__in=["retry", "inline_retry"]
            ).update(available_at=timezone.now() + timedelta(seconds=LEASE_SECONDS), attempts=max(0, work.attempts - 1))
        return "retry"
    except BudgetExhausted as exc:
        finish(work, "budget_blocked", str(exc))
        return "budget_blocked"
    except (Icon.DoesNotExist, IconTaxonomyWork.DoesNotExist):
        return "deleted"
    except Exception as exc:
        retry = transient(exc) and work.attempts < MAX_ATTEMPTS
        state = "retry" if retry else "unavailable"
        # Codes only: exceptions can contain private URLs or model response content.
        allowed_errors = {
            "schema",
            "duplicate_json_key",
            "legacy_release_requires_upgrade",
            "malformed_comparison",
            "oversized_evidence",
            "invalid_observation",
            "unknown_claim_concept",
            "missing_image",
            "image_too_large",
            "claims_too_large",
            "metadata_sources_too_large",
            "incomplete_response",
            "provider_disabled",
            "unsupported_taxonomy_model",
        }
        error = (
            str(exc)
            if isinstance(exc, ValueError) and str(exc) in allowed_errors
            else ("provider_transport_failure" if transient(exc) else "analysis_processing_failure")
        )
        finish(work, state, error)
        if analysis is not None:
            IconAnalysis.objects.filter(pk=analysis.pk).exclude(state="complete").update(state=state, error=error)
        return state
