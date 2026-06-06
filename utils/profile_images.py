import os

from flask import url_for


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


def get_profile_image_url(profile_image: str | None) -> str | None:
    key = normalize_profile_image_key(profile_image)
    if not key:
        return None

    local_path = get_local_profile_image_path(key)
    if os.path.exists(local_path):
        return url_for("static", filename=f"uploads/{key}")

    bucket = os.environ.get("AWS_S3_BUCKET", "workout-logger-uploads")
    region = os.environ.get("AWS_S3_REGION", "ap-southeast-2")
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
