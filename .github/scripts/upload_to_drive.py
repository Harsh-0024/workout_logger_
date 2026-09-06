#!/usr/bin/env python3
"""Upload the nightly database dump to Google Drive."""

from pathlib import Path
import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


# This script authenticates as the repository owner's own Google account using
# a pre-generated OAuth refresh token, not as a service account. See
# get_refresh_token.py for how that refresh token was originally generated.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} environment variable is missing or empty.")
    return value


def main() -> None:
    dump_file = Path(require_env("DUMP_FILE"))
    folder_id = require_env("GDRIVE_FOLDER_ID")
    client_id = require_env("GDRIVE_OAUTH_CLIENT_ID")
    client_secret = require_env("GDRIVE_OAUTH_CLIENT_SECRET")
    refresh_token = require_env("GDRIVE_OAUTH_REFRESH_TOKEN")

    if not dump_file.exists():
        sys.exit(f"Backup file was not found: {dump_file}")

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    credentials.refresh(Request())

    drive_service = build("drive", "v3", credentials=credentials)
    file_metadata = {
        "name": dump_file.name,
        "parents": [folder_id],
    }
    media = MediaFileUpload(
        str(dump_file),
        mimetype="application/octet-stream",
        resumable=True,
    )

    uploaded_file = (
        drive_service.files()
        .create(
            body=file_metadata,
            media_body=media,
            fields="id,name",
        )
        .execute()
    )

    print(
        "Uploaded "
        f"{uploaded_file['name']} to Google Drive "
        f"with file ID {uploaded_file['id']}."
    )


if __name__ == "__main__":
    main()
