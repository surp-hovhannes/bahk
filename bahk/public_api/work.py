"""Context-local guard against billable work originating in public HTTP reads."""

from contextvars import ContextVar


public_request = ContextVar("public_api_request", default=None)


def reject_public_work(kind):
    request = public_request.get()
    if request is None:
        return
    from bahk.public_api.traffic import report_failure

    counts = getattr(request, "public_blocked_work", {})
    counts[kind] = counts.get(kind, 0) + 1
    request.public_blocked_work = counts
    report_failure(f"forbidden_{kind}")
    raise RuntimeError("Public API reads cannot initiate external or background work")
