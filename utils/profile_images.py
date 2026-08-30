import os

from flask import url_for

R2_DEFAULT_ACCOUNT_ID = "c60933f634439b0fb2e6c7762535ba6c"
R2_DEFAULT_BUCKET_NAME = "workout-tracker-avatars"
R2_PUBLIC_BASE_URL = "https://pub-b7699fec85f44832bc1255cae990054b.r2.dev"


def normalize_profile_image_key(profile_image: str | None) -> str:
    key = str(profile_image or "").strip().lstrip("/")
    if key.startswith("static/uploads/"):
        key = key[len("static/uploads/"):]
    elif key.startswith("uploads/"):
        key = key[len("uploads/"):]
    return key


def get_local_profile_image_path(profile_image: str | None) -> str | None:
    key = normalize_profile_image_key(profile_image)
    if not key:
        return None
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    return os.path.join(base_dir, "static", "uploads", key)


def has_r2_profile_image_storage() -> bool:
    return bool(os.environ.get("R2_ACCESS_KEY_ID") and os.environ.get("R2_SECRET_ACCESS_KEY"))


def get_r2_account_id() -> str:
    return os.environ.get("R2_ACCOUNT_ID", R2_DEFAULT_ACCOUNT_ID)


def get_r2_endpoint_url() -> str:
    return f"https://{get_r2_account_id()}.r2.cloudflarestorage.com"


def get_r2_bucket_name() -> str:
    return os.environ.get("R2_BUCKET_NAME", R2_DEFAULT_BUCKET_NAME)


def get_r2_profile_image_client():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=get_r2_endpoint_url(),
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def get_profile_image_url(profile_image: str | None) -> str | None:
    key = normalize_profile_image_key(profile_image)
    if not key:
        return None

    local_path = get_local_profile_image_path(key)
    if os.path.exists(local_path):
        return url_for("static", filename=f"uploads/{key}")

    return f"{R2_PUBLIC_BASE_URL}/{key}"
