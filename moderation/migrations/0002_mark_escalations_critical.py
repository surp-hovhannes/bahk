from django.db import migrations


def mark_escalations_critical(apps, schema_editor):
    """Crisis access now reads only ``moderation_severity``; carry older escalations onto it.

    The moderation task has always read the action stripped and lower-cased (" ESCALATE "
    escalates), so this matches in Python rather than on the exact stored string.
    """
    PrayerRequest = apps.get_model("prayers", "PrayerRequest")
    rows = PrayerRequest.objects.exclude(moderation_severity="critical").filter(moderation_result__isnull=False)
    escalated = []
    for pk, result in rows.values_list("pk", "moderation_result").iterator():
        llm_check = result.get("llm_check") if isinstance(result, dict) else None
        action = llm_check.get("suggested_action") if isinstance(llm_check, dict) else None
        if isinstance(action, str) and action.strip().lower() == "escalate":
            escalated.append(pk)
    PrayerRequest.objects.filter(pk__in=escalated).update(moderation_severity="critical")


class Migration(migrations.Migration):
    dependencies = [
        ("moderation", "0001_initial"),
        ("prayers", "0011_prayerlibraryoperation"),
    ]
    operations = [migrations.RunPython(mark_escalations_critical, migrations.RunPython.noop)]
