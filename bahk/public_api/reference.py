"""Reviewed, illustrative v1 examples. No resource or storage access on /docs/."""

import json


CHURCH = {"id": 1, "name": "Armenian Apostolic Church"}
ICON = {"id": 12, "title": "Holy Cross", "image_url": None, "thumbnail_url": None}
FAST = {
    "id": 7,
    "church_id": 1,
    "name": "Fast of the Holy Cross",
    "description": "A period of fasting before the Feast of the Holy Cross.",
    "start_date": "2026-09-07",
    "end_date": "2026-09-11",
    "culmination_feast": "Exaltation of the Holy Cross",
    "culmination_feast_date": "2026-09-13",
    "year": 2026,
    "image_url": None,
    "thumbnail_url": None,
    "learn_more_url": None,
}
READING = {
    "id": 42,
    "sequence": 1,
    "book": "John",
    "start_chapter": 3,
    "start_verse": 13,
    "end_chapter": 3,
    "end_verse": 21,
}
FEAST = {"id": 18, "name": "Exaltation of the Holy Cross", "icon": ICON}
ROOT = {"service": "fast-and-pray", "version": "v1", "base_path": "/api/v1/", "status": "pre-release"}
CALENDAR = {
    "date": "2026-09-13",
    "church": CHURCH,
    "readings": [READING],
    "fast": None,
    "feasts": [FEAST],
    "partial_failures": [],
}

PARAMETERS = {
    "church_id": "Canonical positive integer, at most 9223372036854775807. Discover IDs from Churches.",
    "id": "Fast ID in the path; discover IDs from a Fast collection.",
    "date": "Exact ISO YYYY-MM-DD calendar date; no time component.",
    "start_date": "Inclusive ISO YYYY-MM-DD; defaults to today minus 180 days in tz.",
    "end_date": "Inclusive ISO YYYY-MM-DD; defaults to today plus 180 days in tz.",
    "tz": "IANA timezone, e.g. America/Los_Angeles. Fast range default: server timezone (America/Los_Angeles); Calendar default: UTC.",
    "lang": "en or hy. Defaults to the request language, then en; untranslated text falls back to canonical text.",
    "limit": "Whole number 1–100; default 25. At most ten ASCII digits.",
    "offset": "Whole number 0–10000; default 0. At most ten ASCII digits.",
}


def collection(item):
    return {"count": 1, "next": None, "previous": None, "results": [item]}


# Names deliberately maintained independently from the URLconf: tests detect new routes.
ROUTES = [
    (
        "root",
        "Service root",
        "/api/v1/",
        (),
        (),
        "Read service and version metadata. The descriptor currently reports pre-release; it is not a resource availability inventory.",
        "",
        ROOT,
    ),
    (
        "church-list",
        "Churches",
        "/api/v1/churches/",
        (),
        ("limit", "offset"),
        "Discover churches and the IDs used by church-scoped requests. Names are canonical; lang is ignored.",
        "?limit=25&offset=0",
        collection(CHURCH),
    ),
    (
        "icon-list",
        "Icons",
        "/api/v1/icons/",
        (),
        ("church_id", "limit", "offset"),
        "Browse icons, optionally filtered by church. Titles are canonical; lang is ignored. Omit church_id to include all churches.",
        "?church_id=1&limit=25",
        collection(ICON),
    ),
    (
        "fast-list",
        "Fasts in a range",
        "/api/v1/fasts/",
        ("church_id",),
        ("start_date", "end_date", "tz", "lang", "limit", "offset"),
        "Find fasts with stored days overlapping the inclusive range. After filling either omitted bound, the range must be ordered and span at most 366 inclusive days.",
        "?church_id=1&start_date=2026-09-01&end_date=2026-09-30&lang=en",
        collection(FAST),
    ),
    (
        "fast-detail",
        "One fast",
        "/api/v1/fasts/{id}/",
        ("id",),
        ("lang",),
        "Read one fast by ID. An unknown ID returns resource_not_found (404).",
        "?lang=en",
        FAST,
    ),
    (
        "fast-by-date",
        "Fasts by date",
        "/api/v1/fasts/by-date/",
        ("church_id", "date"),
        ("lang", "limit", "offset"),
        "Find fasts with a stored day on the given inclusive calendar date.",
        "?church_id=1&date=2026-09-09&lang=en",
        collection(FAST),
    ),
    (
        "fast-by-feast-date",
        "Fasts by feast date",
        "/api/v1/fasts/by-feast-date/",
        ("church_id", "date"),
        ("lang", "limit", "offset"),
        "Find fasts whose culmination feast falls on the given date.",
        "?church_id=1&date=2026-09-13&lang=en",
        collection(FAST),
    ),
    (
        "reading-by-date",
        "Readings",
        "/api/v1/readings/",
        ("church_id", "date"),
        ("lang",),
        "Read stored Scripture citations, ordered by sequence then ID. An unimported day returns an empty readings array. No passage text is returned or retrieved.",
        "?church_id=1&date=2026-09-13&lang=en",
        {"date": "2026-09-13", "readings": [READING]},
    ),
    (
        "feast-by-date",
        "Feasts",
        "/api/v1/feasts/",
        ("church_id", "date"),
        ("lang",),
        "Resolve commemorations through the offline church calendar and return stored feasts in service order, without duplicates. No matches return an empty feasts array; no rows are created.",
        "?church_id=1&date=2026-09-13&lang=en",
        {"date": "2026-09-13", "feasts": [FEAST]},
    ),
    (
        "calendar",
        "Calendar",
        "/api/v1/calendar/",
        ("church_id", "date"),
        ("lang", "tz"),
        "Combine stored readings, church, fast and feasts for one day. fast is the lowest-ID active Fast or null. feasts and partial_failures are always arrays.",
        "?church_id=1&date=2026-09-13&lang=en&tz=UTC",
        CALENDAR,
    ),
]

ERRORS = [
    (
        400,
        "/api/v1/readings/?church_id=1&date=tomorrow",
        "invalid_date",
        "date must use ISO YYYY-MM-DD format.",
        {"parameter": "date", "value": "tomorrow"},
    ),
    (404, "/api/v1/unknown/", "not_found", "The requested route does not exist.", {}),
    (405, "POST /api/v1/", "method_not_allowed", 'Method "POST" not allowed.', {}),
    (406, "GET /api/v1/ with Accept: text/html", "not_acceptable", "Could not satisfy the request Accept header.", {}),
    (429, "Anonymous quota exceeded; Retry-After: 30", "throttled", "Request limit exceeded.", {"retry_after": 30}),
    (
        503,
        "Admission store unavailable; Retry-After: 5",
        "service_unavailable",
        "Please retry shortly.",
        {"retry_after": 5},
    ),
]


def reference_context():
    """Format plain strings; Django autoescaping remains enabled for all examples."""
    routes = []
    for name, title, path, required, optional, purpose, query, response in ROUTES:
        routes.append(
            {
                "name": name,
                "title": title,
                "path": path,
                "purpose": purpose,
                "parameters": [
                    {"name": key, "required": key in required, "description": PARAMETERS[key]}
                    for key in (*required, *optional)
                ],
                "request": f"GET {path.replace('{id}', '7')}{query} HTTP/1.1\nHost: YOUR_API_HOST\nAccept: application/json",
                "response": json.dumps(response, ensure_ascii=False, indent=2),
            }
        )
    return {
        "routes": routes,
        "errors": [
            {
                "status": status,
                "trigger": trigger,
                "response": json.dumps({"code": code, "message": message, "details": details}, indent=2),
            }
            for status, trigger, code, message, details in ERRORS
        ],
    }
