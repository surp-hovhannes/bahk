"""Private, additive taxonomy storage. No calendar definitions live here."""

from django.db import models
from django.utils import timezone


class TaxonomyRelease(models.Model):
    version = models.CharField(max_length=80, unique=True)
    source = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class TaxonomyConcept(models.Model):
    release = models.ForeignKey(TaxonomyRelease, on_delete=models.CASCADE)
    key = models.CharField(max_length=200)
    kind = models.CharField(max_length=16, choices=[(x, x) for x in ("subject", "event", "group", "theme")])
    label = models.CharField(max_length=500)
    definition = models.JSONField(default=dict)
    source = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["release", "key"], name="taxonomy_concept_key")]


class TaxonomyAlias(models.Model):
    concept = models.ForeignKey(TaxonomyConcept, on_delete=models.CASCADE, related_name="aliases")
    language = models.CharField(max_length=16, default="und")
    text = models.CharField(max_length=500)
    normalized = models.CharField(max_length=500, db_index=True)
    source = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["concept", "language", "text"], name="taxonomy_alias_source")]


class TaxonomyRelation(models.Model):
    source_concept = models.ForeignKey(TaxonomyConcept, on_delete=models.CASCADE, related_name="relations")
    target = models.ForeignKey(TaxonomyConcept, on_delete=models.CASCADE, related_name="incoming_relations")
    kind = models.CharField(max_length=32)
    source = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source_concept", "target", "kind"], name="taxonomy_relation_key")
        ]


class IconAnalysis(models.Model):
    icon = models.ForeignKey("icons.Icon", on_delete=models.CASCADE, related_name="taxonomic_analyses")
    church = models.ForeignKey("hub.Church", on_delete=models.CASCADE)
    digest = models.CharField(max_length=64)
    image_digest = models.CharField(max_length=64)
    versions = models.JSONField(default=dict)
    dependencies = models.JSONField(default=dict)
    observation = models.ForeignKey("IconObservation", on_delete=models.SET_NULL, null=True)
    claims = models.JSONField(default=dict)
    comparison = models.JSONField(default=dict)
    state = models.CharField(max_length=24, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    usage = models.JSONField(default=list)
    error = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, state="complete").exists():
            from django.core.exceptions import ValidationError

            raise ValidationError("Completed taxonomy analyses are immutable; create a new revision.")
        return super().save(*args, **kwargs)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["icon", "digest"], name="icon_analysis_input")]


class IconObservation(models.Model):
    church = models.ForeignKey("hub.Church", on_delete=models.CASCADE)
    key = models.CharField(max_length=64, unique=True)
    evidence = models.JSONField(default=dict)
    state = models.CharField(max_length=20, default="pending")
    lease_token = models.CharField(max_length=36, blank=True)
    lease_until = models.DateTimeField(null=True)
    returned_model = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class IconAssertion(models.Model):
    analysis = models.ForeignKey(IconAnalysis, on_delete=models.CASCADE, related_name="assertions")
    concept = models.ForeignKey(TaxonomyConcept, on_delete=models.PROTECT, null=True)
    attribute = models.CharField(max_length=40)
    status = models.CharField(max_length=16)
    evidence_level = models.CharField(max_length=32)
    rule = models.CharField(max_length=80)
    evidence = models.JSONField(default=list)

    class Meta:
        indexes = [models.Index(fields=["concept", "status", "evidence_level"])]


class IconTaxonomyProjection(models.Model):
    icon = models.OneToOneField("icons.Icon", on_delete=models.CASCADE, related_name="taxonomy_projection")
    church = models.ForeignKey("hub.Church", on_delete=models.CASCADE)
    analysis = models.ForeignKey(IconAnalysis, on_delete=models.CASCADE)
    revision = models.PositiveBigIntegerField()
    fingerprint = models.CharField(max_length=64)
    # Accepted attributes also live in indexed assertion rows via analysis.
    attributes = models.JSONField(default=list)


class IconTaxonomyWork(models.Model):
    icon = models.OneToOneField("icons.Icon", on_delete=models.CASCADE, related_name="taxonomy_work")
    revision = models.PositiveBigIntegerField(default=1)
    fingerprint = models.CharField(max_length=64)
    state = models.CharField(max_length=24, default="pending", db_index=True)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    lease_until = models.DateTimeField(null=True)
    lease_token = models.CharField(max_length=36, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=80, blank=True)


class TaxonomyBudget(models.Model):
    """Explicitly provisioned cumulative caps; reservations are never refunded."""

    name = models.CharField(max_length=80, unique=True)
    max_calls = models.PositiveIntegerField(default=0)
    max_tokens = models.PositiveBigIntegerField(default=0)
    max_microdollars = models.PositiveBigIntegerField(default=0)
    calls = models.PositiveIntegerField(default=0)
    tokens = models.PositiveBigIntegerField(default=0)
    microdollars = models.PositiveBigIntegerField(default=0)
    enabled = models.BooleanField(default=False)


class TaxonomyCall(models.Model):
    budget = models.ForeignKey(TaxonomyBudget, on_delete=models.PROTECT)
    analysis = models.ForeignKey(IconAnalysis, on_delete=models.CASCADE, null=True)
    stage = models.CharField(max_length=20)
    reserved_tokens = models.PositiveBigIntegerField()
    reserved_microdollars = models.PositiveBigIntegerField()
    usage = models.JSONField(null=True)
    returned_model = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=24, default="reserved_unknown")
    created_at = models.DateTimeField(auto_now_add=True)


class TaxonomyRequestInterpretation(models.Model):
    church = models.ForeignKey("hub.Church", on_delete=models.CASCADE, null=True)
    key = models.CharField(max_length=64, unique=True)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
