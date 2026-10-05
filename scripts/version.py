#!/usr/bin/env python3
"""Compute the version from the commit history and the branch name.

Walks every non-merge commit oldest to newest, starting from 0.0.0:
  breaking change ("type!:" or a BREAKING CHANGE footer) -> major, minor/patch reset
  feat                                                    -> minor, patch reset
  anything else (fix, chore, docs, ...)                   -> patch
Branches other than main get the branch name as a suffix: 0.3.1-my-branch.

Prints the version; under GitHub Actions it also writes `version=` to $GITHUB_OUTPUT.
"""

import os
import re
import subprocess
import sys

MAIN_BRANCHES = ("main", "master")
BREAKING = re.compile(r"^\w+(\([^)]*\))?!:|^BREAKING[ -]CHANGE:", re.MULTILINE)
FEAT = re.compile(r"^feat(\([^)]*\))?:")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def branch_name() -> str:
    name = os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME")
    return name or git("rev-parse", "--abbrev-ref", "HEAD").strip()


def compute() -> str:
    major = minor = patch = 0
    log = git("log", "--reverse", "--topo-order", "--no-merges", "--format=%B%x00", "HEAD")
    for message in filter(None, (m.strip() for m in log.split("\0"))):
        if BREAKING.search(message):
            major, minor, patch = major + 1, 0, 0
        elif FEAT.match(message):
            minor, patch = minor + 1, 0
        else:
            patch += 1
    version = f"{major}.{minor}.{patch}"

    branch = branch_name()
    if branch not in MAIN_BRANCHES:
        slug = re.sub(r"[^a-z0-9-]+", "-", branch.lower()).strip("-")
        version += f"-{slug}"
    return version


if __name__ == "__main__":
    result = compute()
    print(result)
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            f.write(f"version={result}\n")
    sys.exit(0)
