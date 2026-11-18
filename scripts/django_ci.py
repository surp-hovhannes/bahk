"""Plan and verify Django CI without starting application services."""

import argparse
import os
import re
import subprocess


# Reviewed human-readable files. Recheck runtime and test consumers before adding paths.
DOCUMENTATION_PATHS = {
    b"README.md",
    b"docs/admin-branding.md",
    b"docs/embeddable-widgets.md",
}
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")


def git(*args):
    return subprocess.run(["git", *args], check=True, stdout=subprocess.PIPE).stdout


def documentation_only(diff):
    # `git diff --raw -z --no-renames` emits metadata/path pairs ending in NUL.
    # Disabling rename detection exposes a rename as a deletion plus an addition.
    *records, end = diff.split(b"\0")
    if end or not records or len(records) % 2:
        return False
    for metadata, path in zip(records[::2], records[1::2]):
        if path not in DOCUMENTATION_PATHS:
            return False
        if not re.fullmatch(
            rb":(?:000000 100644 0{40} [0-9a-f]{40} A|100644 100644 [0-9a-f]{40} [0-9a-f]{40} M)",
            metadata,
        ):
            return False
    return True


def classify(event, base, head):
    if event == "push":
        return "application"
    if event != "pull_request" or not all(COMMIT_SHA.fullmatch(sha) for sha in (base, head)):
        raise ValueError("Django CI needs exact base and head commits for a pull request.")
    if git("rev-parse", "--is-shallow-repository").strip() != b"false":
        raise ValueError("Django CI needs complete Git history to classify a pull request.")

    merge_bases = git("merge-base", "--all", base, head).splitlines()
    if len(merge_bases) == 1:
        diff = git(
            "diff", "--raw", "-z", "--no-renames", "--no-abbrev", "--no-ext-diff",
            "--no-relative", "--ignore-submodules=none", merge_bases[0].decode("ascii"), head, "--",
        )
        if documentation_only(diff):
            return "documentation"
    return "application"


def completion_summary(plan_result, mode, django_result, postgres_result):
    if plan_result == "success":
        if mode == "documentation" and django_result == postgres_result == "skipped":
            return "Verified documentation-only diff. Django/Redis and PostgreSQL jobs were skipped."
        if mode == "application" and django_result == postgres_result == "success":
            return "Django/Redis and PostgreSQL jobs passed."
    raise RuntimeError(
        f"Django CI did not complete successfully (classification={plan_result}, "
        f"mode={mode}, Django/Redis={django_result}, PostgreSQL={postgres_result})."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("classify", "complete"))
    if parser.parse_args().command == "classify":
        mode = classify(os.environ["GITHUB_EVENT_NAME"], os.getenv("PR_BASE", ""), os.getenv("PR_HEAD", ""))
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            print(f"mode={mode}", file=output)
        summary = f"Django CI classification: {mode}."
    else:
        summary = completion_summary(
            os.environ["PLAN_RESULT"], os.environ["CI_MODE"], os.environ["DJANGO_RESULT"],
            os.environ["POSTGRES_RESULT"],
        )
    print(summary)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
        print(summary, file=output)


if __name__ == "__main__":
    main()
