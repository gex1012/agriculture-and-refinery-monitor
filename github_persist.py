"""Tiny persistence layer that uses the app's own GitHub repo as a free key-value store for the
small (tens of KB) parsed Wood Mackenzie JSON. Needed because Render's free tier has an ephemeral
filesystem — every sleep/wake or redeploy is a fresh container, so anything saved only to local
disk (like an uploaded PDF) disappears. The raw PDF itself isn't backed up, only the already-parsed
structured data, which is all the app actually serves.

No-ops safely (returns False / None) if GITHUB_TOKEN isn't configured — e.g. for local dev, where
the local filesystem already persists and this layer isn't needed at all.
"""
import base64
import os

import requests

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "gex1012/agriculture-and-refinery-monitor")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
DATA_PATH_PREFIX = "persisted_data"
TIMEOUT = 15


def enabled():
    return bool(GITHUB_TOKEN and GITHUB_REPO)


def push_json(filename, content_bytes, message):
    """Best-effort: create or update persisted_data/<filename> in the repo. Returns True on success."""
    if not enabled():
        return False
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_PATH_PREFIX}/{filename}"
    try:
        existing = requests.get(url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=TIMEOUT)
        sha = existing.json().get("sha") if existing.status_code == 200 else None
        payload = {"message": message, "content": base64.b64encode(content_bytes).decode(),
                   "branch": GITHUB_BRANCH}
        if sha:
            payload["sha"] = sha
        r = requests.put(url, headers=headers, json=payload, timeout=TIMEOUT)
        return r.status_code in (200, 201)
    except requests.RequestException:
        return False


def pull_json(filename):
    """Best-effort fetch of a previously-pushed file. Returns raw bytes, or None if unavailable."""
    if not enabled():
        return None
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{DATA_PATH_PREFIX}/{filename}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        return r.content if r.status_code == 200 else None
    except requests.RequestException:
        return None
