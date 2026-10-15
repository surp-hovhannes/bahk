/* Fast & Pray embeds. Dependency-free; see docs/embeddable-widgets.md. */
(function () {
  "use strict";

  const DEFAULT_HOST = "https://api.fastandpray.app";
  const pending = new Map();
  const cooldowns = new Map();
  // Fast & Pray mark (bahk_landing public/favicon.svg), drawn in currentColor.
  const LOGO_PATH = "M50.4 78.5a75.1 75.1 0 0 0-28.5 6.9l24.2-65.7c.7-2 1.9-3.2 3.4-3.2h29c1.5 0 2.7 1.2 3.4 3.2l24.2 " +
    "65.7s-11.6-7-28.5-7L67 45.5c-.4-1.7-1.6-2.8-2.9-2.8-1.3 0-2.5 1.1-2.9 2.7L50.4 78.5Zm-1.1 28.2Zm-4.2-20.2c-2 " +
    "6.6-.6 15.8 4.2 20.2a17.5 17.5 0 0 1 .2-.7 5.5 5.5 0 0 1 5.7-4.5c2.8.1 4.3 1.5 4.7 4.7.2 1.1.2 2.3.2 3.5v.4c0 " +
    "2.7.7 5.2 2.2 7.4a13 13 0 0 0 5.7 4.9v-.3l-.2-.3c-1.8-5.6-.5-9.5 4.4-12.8l1.5-1a73 73 0 0 0 3.2-2.2 16 16 0 0 0 " +
    "6.8-11.4c.3-2 .1-4-.6-6l-.8.6-1.6 1a37 37 0 0 1-22.4 2.7c-5-.7-9.7-2-13.2-6.2Z";
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
    .content { margin-top: 1rem; }
    h3 { margin: 0 0 .5rem; font-size: 1.375rem; font-weight: 700; line-height: 1.25;
      letter-spacing: -.01em; }
    p { margin: .75rem 0; }
    .content > :first-child { margin-top: 0; }
    .content > :last-child { margin-bottom: 0; }
    .meta { color: var(--_muted); font-size: .875rem; }
    .empty { color: var(--_fg-muted); }
    .description { white-space: pre-wrap; }
    ul { list-style: none; padding: 0; margin: 0; border-top: 1px solid var(--_border); }
    li { display: flex; flex-wrap: wrap; align-items: baseline; column-gap: .375rem;
      padding: .625rem 0; border-bottom: 1px solid var(--_border); }
    li > span:first-child { font-weight: 600; }
    li .ref { color: var(--_fg-muted); font-variant-numeric: tabular-nums; }
    ul + .meta { margin-top: .75rem; font-size: .8125rem; }
    a { color: var(--_accent); font-weight: 600; text-decoration-thickness: 1px;
      text-underline-offset: .2em; }
    a:hover { color: var(--_accent-hover); }
    .more { display: inline-block; margin-top: .25rem; }
    .more::after { content: " →"; content: " →" / ""; }
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
    footer svg { width: 1rem; height: 1rem; flex: none; fill: currentColor; }
    .skeleton { display: grid; gap: .625rem; }
    .skeleton i { display: block; height: .875rem; border-radius: .25rem;
      background: linear-gradient(90deg, #f3eef0 25%, #faf6f7 50%, #f3eef0 75%) 0 0 / 200% 100%;
      animation: shimmer 1.4s ease-in-out infinite; }
    .skeleton i:first-child { width: 55%; height: 1.25rem; }
    .skeleton i:nth-child(3) { width: 80%; }
    @keyframes shimmer { to { background-position: -200% 0; } }
    @media (prefers-reduced-motion: reduce) { .skeleton i { animation: none; } }
    @media (forced-colors: active) { section::before { display: none; } footer svg { fill: CanvasText; } }
  `;

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function logo() {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 128 128");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", LOGO_PATH);
    svg.append(path);
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

  class FastingGuidance extends CalendarWidget {
    get heading() { return "Fasting guidance"; }

    render(data, options) {
      const fast = data.fast;
      if (fast !== null && (!fast || typeof fast !== "object" ||
          !["name", "description", "learn_more_url"].every((key) => fast[key] === null || typeof fast[key] === "string"))) {
        throw new Error("The API returned unexpected fasting guidance.");
      }
      this.status.textContent = data.church.name;
      if (fast === null) {
        this.content.append(node("p", "No fasting guidance is recorded for this date.", "empty"));
        return;
      }
      const heading = node("h3", fast.name || "Current fast");
      if (fast.name) heading.lang = options.language;
      this.content.append(heading);
      const description = node("p", fast.description || "No fasting instructions are recorded for this fast.", "description");
      if (fast.description) description.lang = options.language;
      this.content.append(description);
      const href = publicLink(fast.learn_more_url);
      if (href) {
        const link = node("a", "Learn more about this fast", "more");
        link.href = href;
        this.content.append(link);
      }
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
      this.status.textContent = data.church.name;
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

  if (!customElements.get("bahk-fasting-guidance")) customElements.define("bahk-fasting-guidance", FastingGuidance);
  if (!customElements.get("bahk-daily-readings")) customElements.define("bahk-daily-readings", DailyReadings);
})();
