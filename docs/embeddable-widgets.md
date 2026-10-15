# Fasting guidance and daily readings embeds

These two dependency-free JavaScript widgets use the anonymous, read-only
[public v1 contract](public-api-contract.md). They require no API keys, login,
cookies, framework, or CSS build. They run in modern browsers with Custom
Elements, Shadow DOM, Fetch, and Intl timezone support.

## Add to a page

1. Copy `hub/static/widgets/bahk-widgets.js` to your own static hosting. The
   existing Django staticfiles pipeline also collects it as
   `widgets/bahk-widgets.js`. No new backend route or deployment is needed for
   the file itself. Use a versioned asset URL when publishing updates.
2. Discover the appropriate church ID using
   `GET https://api.fastandpray.app/api/v1/churches/` (paginated; follow `next`).
   Do not infer a church ID from a parish name or assume every installation
   uses the example ID below.
3. Include the script once, then either or both elements. Replace the static
   host and example church ID with your own values.

```html
<script defer src="https://YOUR-STATIC-HOST/widgets/bahk-widgets.js"></script>

<bahk-fasting-guidance church-id="1" time-zone="America/Los_Angeles">
  <a href="https://fastandpray.app">Fasting guidance at Fast &amp; Pray</a>
</bahk-fasting-guidance>

<bahk-daily-readings church-id="1" time-zone="America/Los_Angeles">
  <a href="https://fastandpray.app">Daily readings at Fast &amp; Pray</a>
</bahk-daily-readings>
```

The example script host is a placeholder, not a published CDN. Light-DOM links
remain available when JavaScript is disabled or custom elements cannot load.
Use HTTPS for production hosting. With a Content Security Policy, permit the
script's static origin in `script-src` and the API origin in `connect-src`;
the widget currently uses a shadow-root `<style>` element, which must also be
permitted by `style-src` (it does not provide a CSP nonce option).

## Configuration

Both elements support the same attributes; changes reload the element.

| Attribute | Default | Behavior |
| --- | --- | --- |
| `church-id` | Required | Canonical positive church ID from the churches endpoint. |
| `api-host` | `https://api.fastandpray.app` | HTTP(S) origin only, without a path, credentials, query, or fragment. HTTP is intended for local testing. |
| `date` | Today | Strict, valid `YYYY-MM-DD`. An explicit date stays fixed, including across midnight. |
| `time-zone` | Browser's IANA timezone, then `UTC` | Determines today's date. Set a parish timezone for consistent guidance across visitors. Invalid names show an error. |
| `language` | `en` | API content language: `en` or `hy`. Widget labels remain English; API text is marked with its requested language. The API may fall back to canonical text. |

An omitted `date` is checked locally every minute. At the next check after
midnight in the selected timezone, the widget requests the new day. Browser
timer suspension can delay that check until the page becomes active. No
requests are made for an unchanged day. Date-only values are formatted in UTC
to avoid moving them to the preceding day in the viewer's timezone.

You can update attributes from JavaScript or manually reload the same date:

```js
const readings = document.querySelector("bahk-daily-readings");
await customElements.whenDefined("bahk-daily-readings");
readings.setAttribute("date", "2026-03-01");
// Later, after stored content has changed:
await readings.refresh();
```

The components use shadow DOM to avoid changing the host site's styles. They
fit their container up to 42rem and expose two optional CSS properties:

```css
bahk-fasting-guidance,
bahk-daily-readings {
  --bahk-accent: #205c46;
  --bahk-background: #fffdf8;
}
```

Maintain readable contrast if overriding these colors. Each widget has an h2
heading, a named section, a machine-readable date, a polite status region,
busy state, visible keyboard focus, and a keyboard-operable retry button.

## API behavior and content limits

Both widgets request:

```http
GET https://api.fastandpray.app/api/v1/calendar/?church_id=1&date=2026-03-01&lang=en&tz=America%2FLos_Angeles
Accept: application/json
```

Concurrent requests with identical configuration share one in-flight fetch
within the page. Widgets added later or configured differently may make
separate requests. No response data is stored in browser storage. Requests
explicitly omit credentials, disable HTTP caching, and send no tracking or
authorization headers. Nothing calls the legacy `/api/` product routes.

- Fasting guidance displays the calendar's `fast.name`, `fast.description`,
  and an optional safe `fast.learn_more_url`. The calendar contract chooses
  the lowest-ID active fast when several exist. Descriptions are rendered
  as plain text, preserving newlines; HTML and Markdown are not interpreted.
- Daily readings display `readings[].book`, `start_chapter`, `start_verse`,
  `end_chapter`, and `end_verse`, preserving API order. Single verses,
  same-chapter ranges, and cross-chapter ranges have distinct formatting.
- V1 provides **citations only**, without Scripture text, passage links, or
  AI context. These widgets do not need an API.Bible key or retrieve Bible
  text. The fasting schema provides descriptions, not structured dietary
  rules; widgets do not infer food restrictions.
- Missing stored data shows “No … recorded for this date.” An empty response
  is not evidence that the church observes no fast or has no readings.
  A fast with no description says instructions are not recorded. A feast-only
  `partial_failures` entry does not block either of these widgets.
- API text is assigned with DOM text nodes, never HTML. Learn-more links
  allow only absolute HTTP(S) URLs without embedded credentials. No API
  images or scripts are loaded.

Requests time out after ten seconds and show a manual retry. HTTP 404 advises
checking the church ID and public-resource activation. HTTP 429/503 honors
an available `Retry-After` header with small jitter, disabling retry until
then. When the header is absent or unreadable, it uses the documented
`details.retry_after` error field, falling back to 60 seconds if neither is available.
There are no automatic network retries. Failed or stale responses are not
rendered as today's data; disconnecting or changing attributes invalidates
an earlier request's render.

## Public-resource activation and CORS

The repository registers these public resources only when
`PUBLIC_API_RESOURCES_ENABLED=true`; the default is **off**, and the public
root describes v1 as pre-release. This widget change does not enable resources
or change backend settings. Follow the existing
[operations runbook](public-api-operations.md) for activation requirements.

The API's existing Django CORS settings default to denying external origins:
`CORS_ORIGIN_ALLOW_ALL=false`, `CORS_ALLOWED_ORIGINS` empty. A public resource
being reachable by curl does not establish browser access. The API operator
must allow each embedding page's exact origin, including scheme and port,
or intentionally permit all origins according to their deployment policy.
The existing public contract exposes `Retry-After` for browser callers.

Verify deployment from the actual target origin before publishing an embed:

```sh
curl -i 'https://api.fastandpray.app/api/v1/churches/' \
  -H 'Origin: https://YOUR-PARISH-SITE.example'

curl -i 'https://api.fastandpray.app/api/v1/calendar/?church_id=1&date=2026-03-01&lang=en&tz=UTC' \
  -H 'Origin: https://YOUR-PARISH-SITE.example'
```

Use a discovered church ID and relevant date. Expect HTTP 200 with the
documented JSON and `Access-Control-Allow-Origin` matching the embedding
origin (or `*`). Then load the demo on that origin and inspect the browser
network panel. There is no client-side workaround for denied CORS: neither
`no-cors` mode nor a private API key makes the response readable.

**Live verification limitation (2026-10-15):** this task's cloud container
could not reach `https://api.fastandpray.app`. The network proxy rejected
CONNECT with HTTP 403; without the proxy, DNS resolution was unavailable.
Consequently, live resource activation, church IDs, response data, and CORS
headers remain unverified. No API error status was inferred from the proxy's
403. Endpoint and field choices were verified against repository URLconfs,
views, serializers, and golden contract tests. Browser tests verify real
allowed/denied CORS locally using separate origins and a contract fixture.

## Demo and tests

From the repository root:

```sh
python3 -m http.server 8765 --bind 127.0.0.1
# Open http://127.0.0.1:8765/docs/examples/widgets.html
```

The demo defaults to the live API and includes church, date, and timezone
controls. It deliberately displays API errors when resources or CORS are
unavailable; it never substitutes fixture data for a live response.

```sh
npm ci
# Node.js 20 or newer is required for the browser test tooling.
npx playwright install chromium  # Or set CHROMIUM_PATH to an installed Chromium.
npm run test:widgets
```

Browser tests start their own static server and a separate-origin fixture
API on ports 8765/8766. They cover rendering, loading, empty/null data,
configuration errors, XSS, date rollover, fixed dates, localization metadata,
retry behavior, timeout, stale requests, duplicate script loading, request
sharing, credential omission, actual CORS enforcement, and the demo at 320px.
`tests.unit.test_widget_contract` checks the fixture against the actual Django
calendar view. Run Python validation using the project runtime instructions
in [AGENTS.md](../AGENTS.md). No backend implementation changes are included.
