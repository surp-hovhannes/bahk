/* Fast & Pray embeds. Dependency-free; see docs/embeddable-widgets.md. */
(function () {
  "use strict";

  const DEFAULT_HOST = "https://api.fastandpray.app";
  const pending = new Map();
  const cooldowns = new Map();
  const styles = `
    :host { display: block; color-scheme: light; font: 1rem/1.6 system-ui, sans-serif;
      color: #243630; max-width: 42rem; }
    * { box-sizing: border-box; }
    section { background: var(--bahk-background, #fffdf8); border: 1px solid #d8dfd8;
      border-radius: 1rem; padding: clamp(1rem, 4vw, 1.75rem); overflow-wrap: anywhere; }
    h2 { font-size: 1.25rem; line-height: 1.3; margin: 0 0 .4rem; }
    h3 { font-size: 1.1rem; margin: 1rem 0 .4rem; }
    p { margin: .65rem 0; }
    .meta, footer { color: #50645b; font-size: .875rem; }
    .description { white-space: pre-wrap; }
    ul { padding-inline-start: 1.4rem; margin: 1rem 0; }
    li { padding: .25rem 0; }
    a { color: var(--bahk-accent, #205c46); text-underline-offset: .2em; }
    button { font: inherit; color: #fff; background: var(--bahk-accent, #205c46);
      border: 1px solid transparent; border-radius: .4rem; padding: .5rem 1rem;
      min-height: 44px; cursor: pointer; }
    button:disabled { background: #53655c; cursor: wait; }
    :is(a, button):focus-visible { outline: 3px solid #a54f0e; outline-offset: 3px; }
    footer { margin-top: 1.25rem; border-top: 1px solid #d8dfd8; padding-top: .75rem; }
  `;

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
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
      section.setAttribute("aria-label", this.title);
      section.append(node("h2", this.title));
      if (options) {
        const meta = node("p", undefined, "meta");
        const time = node("time", new Intl.DateTimeFormat("en", {
          dateStyle: "long", timeZone: "UTC",
        }).format(new Date(options.date + "T12:00:00Z")));
        time.dateTime = options.date;
        meta.append(time, document.createTextNode(` · ${options.timeZone}`));
        section.append(meta);
      }
      this.status = node("p", "Loading…", "meta");
      this.status.setAttribute("role", "status");
      this.status.setAttribute("aria-live", "polite");
      this.status.setAttribute("aria-atomic", "true");
      this.content = node("div");
      this.content.setAttribute("aria-busy", "true");
      const footer = node("footer");
      const link = node("a", "Fast & Pray");
      link.href = "https://fastandpray.app";
      footer.append(document.createTextNode("From "), link);
      section.append(this.status, this.content, footer);
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
    get title() { return "Fasting guidance"; }

    render(data, options) {
      const fast = data.fast;
      if (fast !== null && (!fast || typeof fast !== "object" ||
          !["name", "description", "learn_more_url"].every((key) => fast[key] === null || typeof fast[key] === "string"))) {
        throw new Error("The API returned unexpected fasting guidance.");
      }
      this.status.textContent = data.church.name;
      if (fast === null) {
        this.content.append(node("p", "No fasting guidance is recorded for this date."));
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
        const link = node("a", "Learn more about this fast");
        link.href = href;
        this.content.append(link);
      }
    }
  }

  class DailyReadings extends CalendarWidget {
    get title() { return "Daily readings"; }

    render(data, options) {
      if (!Array.isArray(data.readings) || !data.readings.every((reading) => reading &&
          (reading.book === null || typeof reading.book === "string") &&
          ["start_chapter", "start_verse", "end_chapter", "end_verse"].every((key) =>
            Number.isInteger(reading[key]) && reading[key] > 0))) {
        throw new Error("The API returned unexpected daily readings.");
      }
      this.status.textContent = data.church.name;
      if (data.readings.length === 0) {
        this.content.append(node("p", "No readings are recorded for this date."));
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
        item.append(book, document.createTextNode(` ${start}${end}`));
        list.append(item);
      }
      this.content.append(list);
      this.content.append(node("p", "Scripture references from the daily lectionary.", "meta"));
    }
  }

  if (!customElements.get("bahk-fasting-guidance")) customElements.define("bahk-fasting-guidance", FastingGuidance);
  if (!customElements.get("bahk-daily-readings")) customElements.define("bahk-daily-readings", DailyReadings);
})();
