# LLM model inventory

Tracks issue #561. The code source of truth is `hub/services/llm_models.py`; this page
records why each entry is there. Lifecycle facts were verified against the provider pages
listed at the end (Anthropic **2026-09-30**, OpenAI **2026-10-08**); reverify before any rollout.

## How models are chosen at runtime

- `LLMPrompt.model` stays what the admin saved. It is the provenance of generated content and is
  never rewritten by code. A runtime redirect means the saved ID is the configured model,
  rather than proof of which provider model generated a new response.
- Every request goes through `resolve_model()` (in `anthropic_message` / `openai_chat_completion`).
  Sonnet 4.5 requests route immediately to `claude-sonnet-5-5` when this code is deployed,
  independent of the retirement calendar. This is an application policy, not a provider
  shutdown claim. October 30 is a conservative planning target; the November 24 email versus
  November 30 documentation discrepancy remains unresolved, so its shutdown field is unset.
  Other models retain their date-based rules: before shutdown they use the saved ID, and from
  shutdown they use the reviewed replacement with a warning, or fail with `UnsupportedModelError`
  if no replacement has been reviewed yet.
- `LLMPrompt.clean()` (admin forms) and the admin "Make active" action refuse to newly select or
  activate a deprecated or retired model. Existing rows stay editable and can be deactivated
  without changing their model, including legacy non-Claude moderation rows. Newly selecting,
  activating or assigning a moderation prompt still requires Claude; the admin activation action
  validates this before deactivating the current prompt.
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
| `claude-sonnet-5-5` | dropdown, seed, default generation, moderation fallback | active | – | `DEFAULT_CLAUDE_MODEL`, `DEFAULT_MODERATION_MODEL` |
| `claude-haiku-5-5` | dropdown, seed, feast-designation fallback | active | – | `DEFAULT_CLAUDE_CLASSIFIER_MODEL` |
| `claude-sonnet-4-6` | dropdown, feast reference filter | active | – | kept |
| `claude-haiku-4-5-20251001` | dropdown | active | – | kept |
| `claude-sonnet-4-5-20250929` | dropdown; former fallback | deprecated | unresolved (Nov 24 email vs Nov 30 docs) | immediately → `claude-sonnet-5-5` when this code is deployed |
| `claude-3-5-sonnet-20241022` | dropdown; former seed | retired 2025-10-28 | passed | → `claude-sonnet-5-5` |
| `o4-mini` | dropdown | deprecated | 2026-10-23 | → `gpt-5.6-terra` |
| `gpt-5` | dropdown | deprecated (2025-08-07 snapshot) | 2026-12-11 | → `gpt-5.6-sol` |
| `gpt-5-mini`, `gpt-5-mini-2025-08-07` | dropdown | deprecated (2025-08-07 snapshot) | 2026-12-11 | → `gpt-5.6-terra` |
| `gpt-5-nano` | dropdown | deprecated (2025-08-07 snapshot) | 2026-12-11 | → `gpt-5.6-luna` |
| `gpt-4o-mini` | dropdown, seed, OpenAI designation fallback | no notice found | – | kept |
| `gpt-4.1-mini` | `ICON_MATCH_MODEL` default, icon control profile | outside the dropdown; not reviewed here | – | see #511 / #549 |
| `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` | dropdown; OpenAI shutdown replacements; icon taxonomy (luna, terra) | active | – | selectable |

The OpenAI replacements are the ones OpenAI's deprecation table recommends (notices of
2026-04-22 and 2026-06-11). They are a safety net at shutdown, not chosen defaults:
`gpt-6-luna` / `gpt-6.1-sol` remain candidates for a per-workload evaluation before a new model is
picked on the prompts themselves. Request handling treats `gpt-5.6*` and `gpt-6*` as reasoning
families (`max_completion_tokens`, no sampling parameters).

## Dropdown

`LLMPrompt.MODEL_CHOICES` lists the selectable models first (Claude Sonnet 5.5, Haiku 5.5,
Sonnet 4.6, Haiku 4.5; GPT 5.6 Sol, Terra, Luna; GPT 4o Mini), then the legacy IDs labelled
deprecated or retired. The legacy IDs stay so saved rows remain valid and editable; `clean()`
stops them being newly selected or activated. Migration `0072` records the new choices; it is
state-only (`sqlmigrate` shows no SQL). Approved by Der Hayr on 2026-10-08.

## Not in this change

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
