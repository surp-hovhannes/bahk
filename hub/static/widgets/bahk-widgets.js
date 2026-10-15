/* Fast & Pray embeds. Dependency-free; see docs/embeddable-widgets.md. */
(function () {
  "use strict";

  const DEFAULT_HOST = "https://api.fastandpray.app";
  const pending = new Map();
  const cooldowns = new Map();
  // Fast & Pray grapevine mark, traced from the app icon (bahk_landing public/app-icon-1024.png).
  // The vine path is in potrace units: drawn with translate(0 1024) scale(.1 -.1).
  const LOGO_VINE = "M4655 9173 c-881 -79 -1711 -887 -1536 -1496 38 -134 55 -282 41 -356 -5 -29 -10 -57 -10 -62 0 -4 46 14 " +
    "103 41 123 58 171 75 302 105 85 19 100 25 97 41 -62 461 113 932 436 1174 281 210 408 195 359 -42 -70 " +
    "-344 -376 -673 -644 -695 -51 -5 -62 -9 -68 -27 -15 -45 -17 -339 -3 -381 l13 -40 135 -7 c74 -3 166 -11 " +
    "204 -17 76 -13 95 -6 215 82 432 318 641 897 572 1586 -10 103 -27 111 -216 94z M2355 8366 c-449 -104 -708 " +
    "-685 -498 -1116 203 -418 728 -474 1108 -118 101 94 119 141 113 307 -8 207 -51 295 -184 372 -70 39 -70 39 " +
    "-84 -48 -64 -401 -388 -604 -609 -383 -170 170 -60 491 195 570 159 49 396 13 557 -84 l57 -35 0 36 c0 50 " +
    "37 225 60 283 l20 50 -77 45 c-195 114 -470 164 -658 121z M8665 7525 c-392 -85 -620 -449 -655 -1045 -12 " +
    "-209 -14 -217 -41 -225 -34 -11 -55 4 -113 85 -241 337 -537 480 -994 480 -729 0 -1234 -344 -1431 -975 -53 " +
    "-170 -51 -173 104 -150 116 17 207 11 294 -20 24 -8 45 -14 46 -13 2 2 10 28 19 60 121 420 521 698 1006 " +
    "698 412 0 695 -193 895 -612 189 -397 229 -793 146 -1438 -11 -80 -25 -190 -31 -245 -81 -683 -454 -1220 " +
    "-1125 -1622 -953 -572 -1877 -774 -2546 -557 -543 175 -1564 948 -1893 1432 -313 461 -379 1247 -161 1921 " +
    "133 410 351 733 762 1132 535 517 897 652 1204 447 217 -145 206 -497 -17 -564 -249 -75 -463 108 -504 432 " +
    "-5 42 -12 79 -14 82 -12 12 -315 -186 -384 -251 -71 -67 5 -287 161 -469 251 -293 790 -273 1075 40 333 365 " +
    "205 944 -253 1146 -312 139 -910 27 -1189 -222 -73 -65 -182 -134 -280 -176 -260 -113 -768 -798 -940 -1267 " +
    "-185 -503 -225 -1103 -115 -1729 242 -1389 1452 -2393 3106 -2579 961 -109 2001 268 2770 1003 740 707 1053 " +
    "2045 742 3170 -36 129 -48 161 -135 357 -52 118 -64 156 -64 198 0 308 316 705 750 941 266 145 325 198 324 " +
    "295 -1 197 -230 303 -519 240z M4860 5735 c-416 -146 -562 -520 -321 -823 52 -66 89 -82 183 -82 69 0 211 " +
    "-26 271 -49 29 -11 32 -6 17 33 -83 220 8 547 207 743 100 99 93 120 -57 167 -123 39 -209 42 -300 11z " +
    "M5550 5624 c-149 -26 -214 -58 -299 -145 -271 -279 -241 -759 59 -927 234 -131 487 -101 656 79 309 328 282 " +
    "761 -59 927 -101 49 -273 81 -357 66z M6250 5038 c-53 -300 -257 -556 -502 -630 -47 -14 -47 -11 3 -67 22 " +
    "-25 55 -66 72 -91 74 -104 315 -123 466 -37 371 211 411 648 76 849 -100 60 -100 60 -115 -24z M4680 4740 " +
    "c-352 -64 -535 -438 -368 -752 79 -148 280 -233 438 -186 l45 14 7 97 c19 264 130 450 329 552 68 35 68 47 " +
    "3 121 -105 120 -294 184 -454 154z M5253 4445 c-110 -47 -260 -181 -306 -271 -156 -308 -20 -604 332 -725 " +
    "299 -103 634 293 535 632 -51 176 -160 279 -342 324 -54 13 -114 31 -134 40 -44 18 -44 18 -85 0z M5894 " +
    "4087 c52 -189 -22 -427 -182 -585 l-84 -84 34 -29 c337 -291 862 151 666 562 -19 41 -47 89 -63 107 l-28 34 " +
    "-116 -7 c-90 -6 -129 -4 -174 8 l-59 16 6 -22z M4775 3723 c-29 -11 -64 -13 -130 -10 -119 6 -133 -1 -183 " +
    "-102 -136 -270 -21 -553 257 -635 228 -67 469 97 488 331 7 72 -3 85 -106 142 -87 49 -167 125 -226 215 -27 " +
    "42 -52 76 -55 75 -3 0 -23 -7 -45 -16z M5490 3369 c-46 -12 -85 -16 -132 -12 l-68 6 0 -41 c0 -51 -30 -148 " +
    "-63 -204 -51 -87 -220 -218 -282 -218 -49 0 -3 -101 86 -186 188 -178 499 -173 667 11 77 84 96 143 97 290 " +
    "0 155 -10 176 -125 268 -47 37 -92 75 -100 85 -14 17 -18 17 -80 1z";
  // Tokens mirror bahk_landing src/styles/global.css (hex forms). Figtree is used
  // when the host page already loads it; the widget never fetches fonts itself.
  const styles = `
    :host { --_accent: var(--bahk-accent, #80273e); --_bg: var(--bahk-background, #fff);
      --_fg: var(--bahk-text, #3b0714); --_fg-muted: #5b2030; --_muted: #6b7280;
      --_border: var(--bahk-border, #e5e7eb);
      --_accent-hover: var(--bahk-accent-hover, color-mix(in srgb, var(--_accent) 80%, black));
      display: block; color-scheme: light; max-width: 42rem; color: var(--_fg);
      font: 400 1rem/1.6 var(--bahk-font, Figtree, ui-sans-serif, system-ui, -apple-system,
        "Segoe UI", Roboto, sans-serif);
      -webkit-font-smoothing: antialiased; }
    * { box-sizing: border-box; }
    section { position: relative; background: var(--_bg); border: 1px solid var(--_border);
      border-radius: .5rem; padding: clamp(1.125rem, 4vw, 1.5rem); overflow: hidden;
      overflow-wrap: anywhere; box-shadow: 0 1px 2px rgb(59 7 20 / .04); }
    section::before { content: ""; position: absolute; inset: 0 0 auto; height: 3px;
      background: linear-gradient(to right, #b5244a, #e54670); }
    h2 { margin: 0; color: var(--_accent); font-size: .75rem; font-weight: 700;
      line-height: 1.4; letter-spacing: .08em; text-transform: uppercase; }
    .byline { margin: .25rem 0 0; font-size: .875rem; line-height: 1.5; }
    .byline p { margin: 0; }
    .byline [role=status] { color: var(--_fg-muted); font-weight: 600; }
    .byline [role=status]:empty { display: none; }
    .content { margin-top: 1rem; }
    h3 { margin: 0 0 .5rem; font-size: 1.375rem; font-weight: 700; line-height: 1.25;
      letter-spacing: -.01em; }
    p { margin: .75rem 0; }
    .content > :first-child { margin-top: 0; }
    .content > :last-child { margin-bottom: 0; }
    .meta { color: var(--_muted); font-size: .875rem; }
    .empty { color: var(--_fg-muted); }
    .description { white-space: pre-wrap; }
    .day { position: relative; display: flex; gap: 1rem; align-items: flex-start; margin: 0 -.5rem;
      padding: .75rem .5rem; border-radius: .5rem; transition: background-color .15s; }
    .content:has(> .day) { margin-top: .5rem; }
    .day:has(a):hover { background: #fbf6f7; }
    .day:has(a:focus-visible) { outline: 2px solid var(--_accent); outline-offset: 0; }
    .art { flex: none; width: 5.5rem; height: auto; aspect-ratio: 3 / 4; border-radius: .375rem;
      object-fit: cover; object-position: center top; background: #f3e8eb; border: 1px solid var(--_border); }
    .body { min-width: 0; flex: 1; }
    .tag { display: inline-block; padding: .125rem .5rem; border-radius: 9999px; font-size: .6875rem;
      font-weight: 700; line-height: 1.5; letter-spacing: .06em; text-transform: uppercase; }
    .fast .tag { color: var(--_fg-muted); background: #f3e8eb; }
    .feast .tag { color: #fff; background: #b5244a; }
    .day h3 { margin: .375rem 0 0; font-size: 1.25rem; }
    .day h3 a { color: inherit; font-weight: inherit; text-decoration: none; }
    .day h3 a::after { content: ""; position: absolute; inset: 0; border-radius: .5rem; }
    .day h3 a:focus-visible { outline: none; }
    .day:has(a):hover h3 a { text-decoration: underline; text-decoration-thickness: 1px; }
    .day .description { margin: .375rem 0 0; color: var(--_fg-muted); white-space: normal;
      display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; }
    .cta { display: inline-block; margin-top: .5rem; color: var(--_accent); font-size: .875rem; font-weight: 600; }
    .cta::after { content: " →"; content: " →" / ""; }
    .also { margin: .75rem 0 0; padding-top: .75rem; border-top: 1px solid var(--_border);
      color: var(--_fg-muted); font-size: .875rem; }
    .also span { font-weight: 600; }
    ul { list-style: none; padding: 0; margin: 0; border-top: 1px solid var(--_border); }
    li { display: flex; flex-wrap: wrap; align-items: baseline; column-gap: .375rem;
      padding: .625rem 0; border-bottom: 1px solid var(--_border); }
    li > span:first-child { font-weight: 600; }
    li .ref { color: var(--_fg-muted); font-variant-numeric: tabular-nums; }
    ul + .meta { margin-top: .75rem; font-size: .8125rem; }
    a { color: var(--_accent); font-weight: 600; text-decoration-thickness: 1px;
      text-underline-offset: .2em; }
    a:hover { color: var(--_accent-hover); }
    button { font: inherit; font-weight: 600; color: #fff; background: var(--_accent);
      border: 1px solid transparent; border-radius: 9999px; padding: .5rem 1.375rem; min-height: 44px; cursor: pointer;
      transition: background-color .15s; }
    button:hover:not(:disabled) { background: var(--_accent-hover); }
    button:disabled { color: var(--_fg-muted); background: #f3e8eb; cursor: wait; }
    :is(a, button):focus-visible { outline: 2px solid var(--_accent); outline-offset: 2px;
      border-radius: .25rem; }
    button:focus-visible { border-radius: 9999px; }
    footer { display: flex; align-items: center; margin-top: 1.25rem; padding-top: .875rem;
      border-top: 1px solid var(--_border); font-size: .8125rem; line-height: 1; }
    footer a { display: inline-flex; align-items: center; gap: .375rem; color: var(--_muted);
      font-weight: 600; text-decoration: none; }
    footer a:hover { color: var(--_accent); }
    footer svg { width: 1.125rem; height: 1.125rem; flex: none; }
    .skeleton { display: grid; gap: .625rem; }
    .skeleton i { display: block; height: .875rem; border-radius: .25rem;
      background: linear-gradient(90deg, #f3eef0 25%, #faf6f7 50%, #f3eef0 75%) 0 0 / 200% 100%;
      animation: shimmer 1.4s ease-in-out infinite; }
    .skeleton i:first-child { width: 55%; height: 1.25rem; }
    .skeleton i:nth-child(3) { width: 80%; }
    @keyframes shimmer { to { background-position: -200% 0; } }
    @media (prefers-reduced-motion: reduce) { .skeleton i { animation: none; } }
    @media (forced-colors: active) { section::before { display: none; } footer svg { forced-color-adjust: none; }
      .tag { border: 1px solid CanvasText; } .day:has(a:focus-visible) { outline-color: Highlight; } }
  `;

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function logo() {
    const ns = "http://www.w3.org/2000/svg";
    const make = (tag, attributes) => {
      const element = document.createElementNS(ns, tag);
      for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
      return element;
    };
    const svg = make("svg", { viewBox: "88 88 856 856", "aria-hidden": "true", focusable: "false" });
    const gradient = make("linearGradient", { id: "bahk-logo-fill", x1: "1", y1: "0", x2: "0", y2: "1" });
    gradient.append(make("stop", { offset: "0", "stop-color": "#b8264c" }),
      make("stop", { offset: "1", "stop-color": "#85213b" }));
    const defs = make("defs", {});
    defs.append(gradient);
    svg.append(defs, make("circle", { cx: "502", cy: "523", r: "401", fill: "url(#bahk-logo-fill)" }),
      make("path", { transform: "translate(0 1024) scale(.1 -.1)", fill: "#ff6555", d: LOGO_VINE }));
    return svg;
  }

  function validDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return false;
    const parsed = new Date(value + "T00:00:00Z");
    return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  }

  function today(timeZone) {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone, year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(new Date());
    const part = (type) => parts.find((entry) => entry.type === type).value;
    return `${part("year")}-${part("month")}-${part("day")}`;
  }

  function config(element) {
    const church = element.getAttribute("church-id") || "";
    if (!/^[1-9]\d*$/.test(church) || church.length > 19 || BigInt(church) > 9223372036854775807n) {
      throw new Error("Set church-id to an ID from /api/v1/churches/.");
    }
    const host = new URL(element.getAttribute("api-host") || DEFAULT_HOST);
    if (!["https:", "http:"].includes(host.protocol) || host.username || host.password ||
        host.pathname !== "/" || host.search || host.hash) {
      throw new Error("api-host must be an HTTP(S) origin, without credentials or a path.");
    }
    const requestedZone = element.getAttribute("time-zone") ||
      Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    const timeZone = new Intl.DateTimeFormat("en", { timeZone: requestedZone }).resolvedOptions().timeZone;
    if (/^[+-]/.test(timeZone)) throw new Error("time-zone must be an IANA timezone name.");
    // Intl validates the timezone even when an explicit date is supplied.
    const currentDate = today(timeZone);
    const date = element.hasAttribute("date") ? element.getAttribute("date") : currentDate;
    if (!validDate(date)) throw new Error("date must be a valid YYYY-MM-DD calendar date.");
    const language = element.getAttribute("language") || "en";
    if (!["en", "hy"].includes(language)) throw new Error("language must be en or hy.");
    const url = new URL("/api/v1/calendar/", host);
    url.search = new URLSearchParams({ church_id: church, date, lang: language, tz: timeZone });
    return { url, date, timeZone, language, church };
  }

  function publicLink(value) {
    try {
      const url = new URL(value);
      return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (_) {
      return null;
    }
  }

  function unavailable(message, retryAt) {
    const error = new Error(message);
    error.retryAt = retryAt;
    return error;
  }

  async function calendar(options) {
    const { url } = options;
    const retryAt = cooldowns.get(url.origin) || 0;
    if (retryAt > Date.now()) throw unavailable("The API has asked us to wait before trying again.", retryAt);
    const key = url.href;
    if (pending.has(key)) return pending.get(key);
    const request = (async () => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(url, {
          credentials: "omit", mode: "cors", cache: "no-store", redirect: "error",
          referrerPolicy: "no-referrer", headers: { Accept: "application/json" }, signal: controller.signal,
        });
        if (!response.ok) {
          const hint = response.headers.get("Retry-After");
          let waitUntil = /^\d+$/.test(hint || "") ? Date.now() + Number(hint) * 1000 : Date.parse(hint);
          if ([429, 503].includes(response.status) && !Number.isFinite(waitUntil)) {
            const body = await response.json().catch(() => null);
            const seconds = body && body.details && body.details.retry_after;
            if (Number.isSafeInteger(seconds) && seconds >= 0) waitUntil = Date.now() + seconds * 1000;
          }
          if (![429, 503].includes(response.status) || !Number.isFinite(waitUntil)) {
            waitUntil = Date.now() + ([429, 503].includes(response.status) ? 60000 : 0);
          }
          // Manual retries only; the small jitter also prevents synchronized retries.
          if (waitUntil > Date.now()) {
            waitUntil += Math.floor(Math.random() * 1000) + 1;
            cooldowns.set(url.origin, waitUntil);
          }
          const message = response.status === 404
            ? "The public calendar is unavailable. Check the church ID and whether public v1 resources are enabled."
            : `The public API is unavailable (HTTP ${response.status}).`;
          throw unavailable(message, waitUntil);
        }
        return await response.json();
      } finally {
        clearTimeout(timeout);
      }
    })();
    pending.set(key, request);
    try {
      return await request;
    } finally {
      pending.delete(key);
    }
  }

  function checkEnvelope(data, options) {
    if (!data || data.date !== options.date || !data.church ||
        String(data.church.id) !== options.church || typeof data.church.name !== "string") {
      throw new Error("The API returned an unexpected calendar response.");
    }
  }

  class CalendarWidget extends HTMLElement {
    static get observedAttributes() { return ["church-id", "api-host", "date", "time-zone", "language"]; }

    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this.revision = 0;
    }

    connectedCallback() {
      this.refresh();
      // Check the date locally. No polling requests for an unchanged day.
      this.dayTimer = setInterval(() => {
        if (!this.hasAttribute("date") && this.options && today(this.options.timeZone) !== this.options.date) {
          this.refresh();
        }
      }, 60000);
    }

    disconnectedCallback() {
      this.revision += 1;
      clearInterval(this.dayTimer);
      clearTimeout(this.retryTimer);
    }

    attributeChangedCallback() {
      if (this.isConnected) this.refresh();
    }

    frame(options) {
      const style = node("style", styles);
      const section = node("section");
      section.lang = "en";
      section.setAttribute("aria-label", this.heading);
      section.append(node("h2", this.heading));
      const byline = node("div", undefined, "byline");
      this.status = node("p", "Loading…");
      this.status.setAttribute("role", "status");
      this.status.setAttribute("aria-live", "polite");
      this.status.setAttribute("aria-atomic", "true");
      byline.append(this.status);
      if (options) {
        const meta = node("p", undefined, "meta");
        const time = node("time", new Intl.DateTimeFormat("en", {
          dateStyle: "long", timeZone: "UTC",
        }).format(new Date(options.date + "T12:00:00Z")));
        time.dateTime = options.date;
        meta.append(time, document.createTextNode(` · ${options.timeZone}`));
        byline.append(meta);
      }
      this.content = node("div", undefined, "content");
      this.content.setAttribute("aria-busy", "true");
      const skeleton = node("div", undefined, "skeleton");
      skeleton.setAttribute("aria-hidden", "true");
      skeleton.append(node("i"), node("i"), node("i"));
      this.content.append(skeleton);
      const footer = node("footer");
      const link = node("a");
      link.href = "https://fastandpray.app";
      link.append(logo(), document.createTextNode("Fast & Pray"));
      footer.append(link);
      section.append(byline, this.content, footer);
      this.shadowRoot.replaceChildren(style, section);
    }

    async refresh() {
      const revision = ++this.revision;
      clearTimeout(this.retryTimer);
      this.options = null;
      try {
        const options = config(this);
        this.options = options;
        this.frame(options);
        const data = await calendar(options);
        if (revision !== this.revision || !this.isConnected) return;
        checkEnvelope(data, options);
        this.content.replaceChildren();
        this.render(data, options);
        this.content.setAttribute("aria-busy", "false");
      } catch (error) {
        if (revision !== this.revision || !this.isConnected) return;
        if (!this.options) this.frame();
        this.content.replaceChildren();
        this.content.setAttribute("aria-busy", "false");
        this.status.textContent = "Unable to load this widget.";
        const message = error instanceof TypeError || error.name === "AbortError"
          ? "Could not reach the public API. Check your connection, API host, and the API's CORS settings."
          : error.message;
        this.content.append(node("p", message));
        const button = node("button", "Try again");
        button.type = "button";
        button.addEventListener("click", () => this.refresh());
        if (error.retryAt > Date.now()) {
          button.disabled = true;
          this.content.append(node("p", `Please wait until ${new Date(error.retryAt).toLocaleTimeString()} to retry.`, "meta"));
          this.retryTimer = setTimeout(() => { button.disabled = false; },
            Math.min(error.retryAt - Date.now(), 2147483647));
        }
        this.content.append(button);
      }
    }
  }

  function publicImage(value) {
    const href = publicLink(value);
    // HTTPS only: mixed-content images are blocked on HTTPS host pages anyway.
    return href && href.startsWith("https:") ? href : null;
  }

  function nullableStrings(value, keys) {
    return value && typeof value === "object" &&
      keys.every((key) => value[key] === null || value[key] === undefined || typeof value[key] === "string");
  }

  // Cut on a word boundary so the card stays short even where line-clamp is unsupported.
  function truncate(text, limit = 180) {
    if (text.length <= limit) return text;
    const cut = text.slice(0, limit);
    return cut.slice(0, Math.max(cut.lastIndexOf(" "), limit * 0.6)).replace(/[\s,.;:–-]+$/, "") + "…";
  }

  class FastOrFeast extends CalendarWidget {
    get heading() { return "Fast or Feast"; }

    render(data, options) {
      const { fast, feasts } = data;
      if (fast !== null && !nullableStrings(fast, ["name", "description", "learn_more_url", "share_url",
          "image_url", "thumbnail_url"])) {
        throw new Error("The API returned an unexpected fast.");
      }
      if (!Array.isArray(feasts) || !feasts.every((feast) =>
          nullableStrings(feast, ["name", "description", "share_url"]) &&
          (!feast.icon || nullableStrings(feast.icon, ["image_url", "thumbnail_url"])))) {
        throw new Error("The API returned unexpected feasts.");
      }
      const feastsUnavailable = Array.isArray(data.partial_failures) &&
        data.partial_failures.some((failure) => failure && failure.component === "feasts");
      this.status.textContent = "";

      // A day is shown as one thing. A stored fast takes precedence, as in the app,
      // where that day's commemorations become secondary remembrances.
      let others = feasts;
      if (fast) {
        this.content.append(this.card({
          kind: "fast", label: "Fast day", title: fast.name || "Current fast",
          titled: Boolean(fast.name), description: fast.description,
          href: publicLink(fast.share_url) || publicLink(fast.learn_more_url),
          image: publicImage(fast.thumbnail_url) || publicImage(fast.image_url),
        }, options));
      } else if (feasts.length) {
        const [feast] = feasts;
        others = feasts.slice(1);
        this.content.append(this.card({
          kind: "feast", label: "Feast day", title: feast.name || "Feast",
          titled: Boolean(feast.name), description: feast.description,
          href: publicLink(feast.share_url),
          image: feast.icon && (publicImage(feast.icon.thumbnail_url) || publicImage(feast.icon.image_url)),
        }, options));
      } else if (!feastsUnavailable) {
        this.content.append(node("p", "Nothing is recorded for this date.", "empty"));
      }

      const names = others.map((feast) => feast.name).filter(Boolean);
      if (names.length) {
        const also = node("p", "Also commemorated: ", "also");
        names.forEach((name, index) => {
          if (index) also.append(document.createTextNode(", "));
          const span = node("span", name);
          span.lang = options.language;
          also.append(span);
        });
        this.content.append(also);
      }
      if (feastsUnavailable) {
        this.content.append(node("p", "Feast information is temporarily unavailable.", "meta"));
      }
    }

    card({ kind, label, title, titled, description, href, image }, options) {
      const card = node("article", undefined, `day ${kind}`);
      if (image) {
        const picture = node("img", undefined, "art");
        // Decorative: the title is the adjacent text.
        picture.alt = "";
        picture.width = 88;
        picture.height = 116;
        picture.loading = "lazy";
        picture.decoding = "async";
        picture.referrerPolicy = "no-referrer";
        picture.addEventListener("error", () => picture.remove());
        picture.src = image;
        card.append(picture);
      }
      const body = node("div", undefined, "body");
      body.append(node("span", label, "tag"));
      const heading = node("h3");
      if (titled) heading.lang = options.language;
      if (href) {
        const link = node("a", title);
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener";
        heading.append(link);
      } else {
        heading.textContent = title;
      }
      body.append(heading);
      if (description) {
        const summary = node("p", truncate(description.trim()), "description");
        summary.lang = options.language;
        body.append(summary);
      }
      if (href) body.append(node("span", "Read more in Fast & Pray", "cta"));
      card.append(body);
      return card;
    }
  }

  class DailyReadings extends CalendarWidget {
    get heading() { return "Daily readings"; }

    render(data, options) {
      if (!Array.isArray(data.readings) || !data.readings.every((reading) => reading &&
          (reading.book === null || typeof reading.book === "string") &&
          ["start_chapter", "start_verse", "end_chapter", "end_verse"].every((key) =>
            Number.isInteger(reading[key]) && reading[key] > 0))) {
        throw new Error("The API returned unexpected daily readings.");
      }
      this.status.textContent = "";
      if (data.readings.length === 0) {
        this.content.append(node("p", "No readings are recorded for this date.", "empty"));
        return;
      }
      const list = node("ul");
      for (const reading of data.readings) {
        const { start_chapter: sc, start_verse: sv, end_chapter: ec, end_verse: ev } = reading;
        const start = `${sc}:${sv}`;
        const end = sc === ec ? (sv === ev ? "" : `–${ev}`) : `–${ec}:${ev}`;
        const item = node("li");
        const book = node("span", reading.book || "Unknown book");
        if (reading.book) book.lang = options.language;
        // The leading space keeps the item's text content readable as "Book 1:2".
        item.append(book, node("span", ` ${start}${end}`, "ref"));
        list.append(item);
      }
      this.content.append(list);
      this.content.append(node("p", "Scripture references from the daily lectionary.", "meta"));
    }
  }

  if (!customElements.get("bahk-fast-or-feast")) customElements.define("bahk-fast-or-feast", FastOrFeast);
  if (!customElements.get("bahk-daily-readings")) customElements.define("bahk-daily-readings", DailyReadings);
})();
