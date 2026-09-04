"""Push the notes into Google Docs.

Builds one .docx and uploads it to Drive asking for conversion to a native
Google Doc. That is far more faithful than rebuilding the formatting through
documents.batchUpdate: heading styles become the Docs outline, and the
rendered diagram images come along inside the file.

Note on tabs: the Docs API cannot create them. Both it and Apps Script expose
only getTab / getTabs / getActiveTab / setActiveTab - there is no addTab in
either - so navigation here is the heading outline (View > Show outline),
not tabs.
"""

from __future__ import annotations

import json
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# A whole-course document runs to a couple of MB with diagrams embedded.
# httplib2's default socket timeout is short enough that one slow leg kills
# the transfer, hence the generous timeout and retries.
#
# Deliberately NOT a resumable upload: at this size it buys nothing, and
# httplib2 mishandles the 308 "Resume Incomplete" that chunked uploads reply
# with, failing as RedirectMissingLocation.
UPLOAD_TIMEOUT = 600
UPLOAD_RETRIES = 5
GDOC_MIME = "application/vnd.google-apps.document"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
HTML_MIME = "text/html"
PDF_MIME = "application/pdf"

# Drive refuses to export a Google-native file larger than this.
EXPORT_LIMIT_MB = 10

CREDENTIALS_HELP = """\
Google Docs upload needs a one-time OAuth client:

  1. Go to https://console.cloud.google.com/ and create (or pick) a project.
  2. APIs & Services > Library > enable "Google Drive API".
  3. APIs & Services > Credentials > Create credentials > OAuth client ID
     > Application type: Desktop app.
  4. Download the JSON and save it as:
         {path}

Then re-run. A browser opens once to authorise; the token is cached next to
that file so later runs are silent. The scope requested is drive.file, which
only grants access to files this tool itself creates.
"""


class GDocsError(RuntimeError):
    pass


class NotAuthorised(GDocsError):
    """No usable Google token, and we were told not to open a browser."""


def _paths(config_dir: Path) -> tuple[Path, Path]:
    return config_dir / "google-credentials.json", config_dir / "google-token.json"


def auth_status(config_dir: Path) -> dict:
    """Describe the Google credentials without ever starting a login flow.

    The server needs to answer "are we connected?" on every page load. Asking
    `_service()` would open a browser to find out, so this reads the two files
    and reports what it sees.
    """
    creds_file, token_file = _paths(config_dir)
    state = {
        "sdk": True,
        "client": creds_file.exists(),
        "token": token_file.exists(),
        "valid": False,
        "refreshable": False,
        "help": None,
    }

    try:
        from google.oauth2.credentials import Credentials  # noqa: F401
    except ImportError:
        state["sdk"] = False
        state["help"] = (
            "Google Docs upload needs:\n"
            "    pip install google-api-python-client google-auth-oauthlib"
        )
        return state

    if not state["client"]:
        state["help"] = CREDENTIALS_HELP.format(path=creds_file)
        return state
    if not state["token"]:
        state["help"] = "Not connected yet - authorise Google Drive access."
        return state

    from google.oauth2.credentials import Credentials

    try:
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    except Exception as exc:  # noqa: BLE001 - a corrupt token is just "reconnect"
        state["help"] = f"Stored Google token is unreadable ({exc}); reconnect."
        return state

    state["valid"] = bool(creds.valid)
    state["refreshable"] = bool(creds.expired and creds.refresh_token)
    if not state["valid"] and not state["refreshable"]:
        state["help"] = "Google access has expired or been revoked; reconnect."
    return state


def connected(config_dir: Path) -> bool:
    state = auth_status(config_dir)
    return bool(state["valid"] or state["refreshable"])


def connect(config_dir: Path):
    """Run the browser consent flow. The only caller of `run_local_server`."""
    config_dir.mkdir(parents=True, exist_ok=True)
    return _service(config_dir, interactive=True)


def _service(config_dir: Path, *, interactive: bool = False):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise GDocsError(
            "Google Docs upload needs:\n"
            "    pip install google-api-python-client google-auth-oauthlib"
        ) from exc

    creds_file, token_file = _paths(config_dir)
    if not creds_file.exists():
        raise GDocsError(CREDENTIALS_HELP.format(path=creds_file))

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif not interactive:
            # Blocking on a browser here would hang a queued job with no
            # explanation. Callers that can prompt ask for it explicitly.
            raise NotAuthorised(
                "Google Drive is not connected.\n"
                "  Connect it with:  python3 -m notesgen gdocs-auth\n"
                "  or from the web UI's Google panel."
            )
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
            print("  opening a browser to authorise Google Drive access...")
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")
        token_file.chmod(0o600)

    import google_auth_httplib2
    import httplib2

    http = google_auth_httplib2.AuthorizedHttp(
        creds, http=httplib2.Http(timeout=UPLOAD_TIMEOUT)
    )
    return build("drive", "v3", http=http, cache_discovery=False)


def _folder(service, name: str) -> str:
    """Find or create a Drive folder owned by this tool."""
    safe = name.replace("'", "\\'")
    existing = service.files().list(
        q=f"name='{safe}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id,name)", pageSize=1,
    ).execute().get("files", [])
    if existing:
        return existing[0]["id"]

    created = service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    return created["id"]


def export_pdf(doc_id: str, out_path: Path, config_dir: Path) -> Path:
    """Download a Google Doc as PDF.

    Exporting from the Doc rather than converting locally means the PDF is
    laid out by Google exactly as the document reads, diagrams included, with
    no LibreOffice or headless Word in the loop.
    """
    service = _service(config_dir)
    from googleapiclient.http import MediaIoBaseDownload

    try:
        request = service.files().export_media(fileId=doc_id, mimeType=PDF_MIME)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _status, done = downloader.next_chunk(num_retries=UPLOAD_RETRIES)
    except Exception as exc:  # noqa: BLE001
        out_path.unlink(missing_ok=True)
        if "exportSizeLimitExceeded" in str(exc):
            raise GDocsError(
                f"the document is too large for Drive to export as PDF "
                f"(limit {EXPORT_LIMIT_MB} MB).\n"
                "  Use --split-sections so each section is its own document, "
                "then export those."
            ) from exc
        raise GDocsError(f"PDF export failed: {exc}") from exc

    return out_path


def push_file(
    path: Path,
    title: str,
    config_dir: Path,
    manifest,
    *,
    mime: str,
    convert: bool,
    folder_name: str | None = None,
    key: str | None = None,
) -> str:
    """Upload any file to Drive, optionally converting it to a Google Doc.

    `convert=False` keeps the file as-is, which is what the HTML page wants:
    the exported page embeds its diagrams and needs no JavaScript, so Drive
    renders it in preview and the link is shareable as it stands.
    """
    service = _service(config_dir)
    from googleapiclient.http import MediaFileUpload

    key = key or f"__gdoc__/{title}"
    existing = manifest.entries.get(key, {}).get("doc_id")
    media = MediaFileUpload(str(path), mimetype=mime, resumable=False)

    if existing:
        try:
            service.files().get(fileId=existing, fields="id").execute()
            service.files().update(
                fileId=existing, media_body=media
            ).execute(num_retries=UPLOAD_RETRIES)
            url = _url(existing, convert)
            manifest.record(key, hash="", output=str(path), doc_id=existing,
                            status="ok", url=url)
            return url
        except Exception:  # noqa: BLE001 - deleted upstream; recreate below
            pass

    body: dict = {"name": title}
    if convert:
        body["mimeType"] = GDOC_MIME
    if folder_name:
        body["parents"] = [_folder(service, folder_name)]

    created = service.files().create(
        body=body, media_body=media, fields="id"
    ).execute(num_retries=UPLOAD_RETRIES)
    doc_id = created["id"]

    url = _url(doc_id, convert)
    manifest.record(key, hash="", output=str(path), doc_id=doc_id,
                    status="ok", url=url)
    return url


def push(
    docx_path: Path,
    title: str,
    config_dir: Path,
    manifest,
    *,
    folder_name: str | None = None,
    key: str | None = None,
) -> str:
    """Upload one .docx as a Google Doc, updating in place on re-runs."""
    # _service() first: it raises the actionable "pip install ..." / "create an
    # OAuth client" message before any bare ImportError can surface.
    service = _service(config_dir)
    from googleapiclient.http import MediaFileUpload

    key = key or f"__gdoc__/{title}"
    existing = manifest.entries.get(key, {}).get("doc_id")

    media = MediaFileUpload(str(docx_path), mimetype=DOCX_MIME, resumable=False)

    if existing:
        try:
            service.files().get(fileId=existing, fields="id").execute()
            # Re-uploading media to the same file id keeps the URL stable, so
            # links already shared keep working.
            service.files().update(
                fileId=existing, media_body=media
            ).execute(num_retries=UPLOAD_RETRIES)
            manifest.record(key, hash="", output=str(docx_path), doc_id=existing,
                            status="ok", url=_url(existing, True))
            return _url(existing, True)
        except Exception:  # noqa: BLE001 - the doc was deleted; fall through and recreate
            pass

    body = {"name": title, "mimeType": GDOC_MIME}
    if folder_name:
        body["parents"] = [_folder(service, folder_name)]

    created = service.files().create(
        body=body, media_body=media, fields="id"
    ).execute(num_retries=UPLOAD_RETRIES)
    doc_id = created["id"]
    manifest.record(key, hash="", output=str(docx_path), doc_id=doc_id,
                    status="ok", url=_url(doc_id, True))
    return _url(doc_id)


def _url(doc_id: str, convert: bool = True) -> str:
    if convert:
        return f"https://docs.google.com/document/d/{doc_id}/edit"
    # A non-converted file has no Docs editor; this is its Drive preview.
    return f"https://drive.google.com/file/d/{doc_id}/view"
