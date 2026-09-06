#!/usr/bin/env python3
"""One-time helper for generating a Google OAuth refresh token.

This script is meant to be run manually on your Mac. It is not used by GitHub
Actions and should not be committed with any credentials.
"""

from pathlib import Path
import sys

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ModuleNotFoundError:
    sys.exit(
        "Missing dependency: google-auth-oauthlib\n"
        "Install it with: python3 -m pip install google-auth-oauthlib"
    )


SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CLIENT_SECRET_PATH = Path.home() / "oauth_client_secret.json"


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    client_secret_path = CLIENT_SECRET_PATH.resolve()

    if repo_root == client_secret_path or repo_root in client_secret_path.parents:
        sys.exit(
            "Refusing to read OAuth client credentials from inside this project.\n"
            f"Move the file to: {CLIENT_SECRET_PATH}"
        )

    if not client_secret_path.exists():
        sys.exit(
            "OAuth client credentials file was not found.\n"
            f"Expected file: {CLIENT_SECRET_PATH}"
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_path),
        scopes=SCOPES,
    )

    credentials = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    if not credentials.refresh_token:
        sys.exit(
            "Google did not return a refresh token.\n"
            "Try running this script again and make sure you approve access when prompted."
        )

    print()
    print(f"REFRESH TOKEN: {credentials.refresh_token}")
    print()
    print(
        "This refresh token is sensitive. Copy it into your notes file immediately "
        "and do not commit it to the repository."
    )


if __name__ == "__main__":
    main()
