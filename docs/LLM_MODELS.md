# LLM model inventory

Tracks issue #561. The code source of truth is `hub/services/llm_models.py`; this page
records why each entry is there. Lifecycle facts were verified on **2026-09-30** against the
provider pages listed at the end; reverify before any rollout.

## How models are chosen at runtime

- `LLMPrompt.model` stays what the admin saved. It is the provenance of generated content and is
  never rewritten by code.
- Every request goes through `resolve_model()` (in `anthropic_message` / `openai_chat_completion`).
  Before a model's shutdown date the request is sent to the saved ID unchanged. After it, the
  request goes to the reviewed replacement with a warning in the logs, or fails with
  `UnsupportedModelError` if no replacement has been reviewed yet.
- `LLMPrompt.clean()` (admin forms) and the admin "Make active" action refuse to newly select or
  activate a deprecated or retired model. Existing rows stay editable.
- Prayer-request moderation calls Anthropic directly, so its prompts must use a Claude model. A
  saved non-Claude moderation prompt falls back to `DEFAULT_MODERATION_MODEL` and logs an error.
  Refusals and empty replies raise errors, which send the request to human review.
- Claude responses are read by block `type` (`anthropic_text`), never `content[0]`. Models that
  think by default (Sonnet 5.5, Haiku 5.5) get `output_config.effort = "low"` and
  `THINKING_HEADROOM_TOKENS` on top of the caller's reply budget, because thinking counts toward
  `max_tokens`.

## Inventory

| Model ID | Where | Status | Shutdown | Treatment |
| --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | default generation, moderation fallback | active | – | `DEFAULT_CLAUDE_MODEL`, `DEFAULT_MODERATION_MODEL` |
| `claude-haiku-5-5` | feast-designation fallback | active | – | `DEFAULT_CLAUDE_CLASSIFIER_MODEL` |
| `claude-sonnet-4-6` | feast reference filter, seed | active | – | kept |
| `claude-haiku-4-5-20251001` | dropdown, seed | active | – | kept |
| `claude-sonnet-4-5-20250929` | dropdown; former fallback | deprecated | 2026-10-30 (conservative; notices say Nov 24 or Nov 30) | → `claude-sonnet-5-5` |
| `claude-3-5-sonnet-20241022` | dropdown; former seed | retired 2025-10-28 | passed | → `claude-sonnet-5-5` |
| `o4-mini` | dropdown | deprecated | 2026-10-23 | **replacement not chosen** |
| `gpt-5`, `gpt-5-mini`, `gpt-5-nano`, `gpt-5-mini-2025-08-07` | dropdown | deprecated (Aug 7 2025 snapshots) | 2026-12-11 | **replacement not chosen** |
| `gpt-4o-mini` | dropdown, seed, OpenAI designation fallback | no notice found | – | kept |
| `gpt-4.1-mini` | `ICON_MATCH_MODEL` default, icon control profile | outside the dropdown; not reviewed here | – | see #511 / #549 |
| `gpt-5.6-luna`, `gpt-5.6-terra` | icon taxonomy | outside the dropdown; not reviewed here | – | see #511 / #549 |

The OpenAI replacements are left unset on purpose. OpenAI's deprecation table names
`gpt-5.6-terra` / `-luna` / `-sol`, and `gpt-6-luna` / `gpt-6.1-sol` are newer candidates, but each
needs a per-workload evaluation first. Request handling already treats `gpt-6*` as a reasoning
family (`max_completion_tokens`, no sampling parameters).

## Not in this change

- **Dropdown choices.** Adding `claude-sonnet-5-5` / `claude-haiku-5-5` (and any OpenAI successor)
  to `LLMPrompt.MODEL_CHOICES` needs an `AlterField` migration. Under AGENTS.md that goes in a
  separate PR with Der Hayr's approval.
- **Saved-row migration.** Moving active rows onto new models needs an approved read-only
  production audit first.
- **Production.** Paid evaluations and rollout each need their own approval.

## Audit

```bash
python manage.py audit_llm_models           # read-only; prints IDs and counts, never prompt text
python manage.py audit_llm_models --strict  # non-zero exit if an active prompt is affected
```

## Sources (verified 2026-09-30)

- Anthropic: <https://platform.claude.com/docs/en/about-claude/model-deprecations>,
  <https://platform.claude.com/docs/en/models/overview>,
  <https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide>
- OpenAI: <https://developers.openai.com/api/docs/deprecations>
