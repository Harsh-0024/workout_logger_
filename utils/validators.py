"""
Input validation utilities.
"""
import re
import html
import math
from typing import Optional
from urllib.parse import urljoin, urlsplit
from utils.errors import ValidationError


def validate_username(username: str) -> str:
    """Validate and sanitize username."""
    if not username:
        raise ValidationError("Username cannot be empty")
    
    username = username.strip().lower()
    
    # Allow common username/email-safe characters used in URLs
    if not re.match(r'^[a-z0-9_.@-]+$', username):
        raise ValidationError(
            "Username can only contain letters, numbers, underscores, hyphens, dots, and @"
        )
    
    if len(username) < 3 or len(username) > 30:  # Align with auth service requirement
        raise ValidationError("Username must be between 3 and 30 characters")
    
    return username


# A username is also its home page's address (/<username>), so it can't be the first part of
# one of the app's own addresses: someone named "log" or "stats" never reached their Home.
_RESERVED_USERNAMES = {"admin", "api", "static", "share", "shortcut", "health", "login", "logout", "register"}
USERNAME_NOT_AVAILABLE = "That username isn't available. Please pick another."


def is_reserved_username(username: str) -> bool:
    name = (username or "").strip().lower()
    reserved = set(_RESERVED_USERNAMES)
    try:
        from flask import current_app

        for rule in current_app.url_map.iter_rules():
            first = rule.rule.strip("/").split("/", 1)[0].lower()
            if first and "<" not in first:
                reserved.add(first)
    except RuntimeError:  # no app running
        pass
    return name in reserved


def validate_exercise_name(exercise_name: str) -> str:
    """Validate exercise name."""
    if not exercise_name or not exercise_name.strip():
        raise ValidationError("Exercise name cannot be empty")
    
    exercise_name = exercise_name.strip()
    
    if len(exercise_name) > 100:
        raise ValidationError("Exercise name is too long")
    
    return exercise_name


def sanitize_text_input(text: str, max_length: Optional[int] = None, allow_html: bool = False) -> str:
    """
    Sanitize text input by removing/escaping dangerous content.
    
    Args:
        text: Input text to sanitize
        max_length: Maximum allowed length
        allow_html: If False, HTML tags will be escaped
    
    Returns:
        Sanitized text
    """
    if not text:
        return ""
    
    text = text.strip()
    
    if not allow_html:
        # Escape HTML entities to prevent XSS
        text = html.escape(text)
    
    # Remove null bytes and other control characters except newlines and tabs
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    
    # Remove script tags and their content (case insensitive)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.IGNORECASE | re.DOTALL)
    
    # Remove javascript: and data: URLs
    text = re.sub(r'javascript\s*:', '', text, flags=re.IGNORECASE)
    text = re.sub(r'data\s*:', '', text, flags=re.IGNORECASE)
    
    # Remove on* event handlers (onclick, onload, etc.)
    text = re.sub(r'\bon\w+\s*=', '', text, flags=re.IGNORECASE)
    
    if max_length and len(text) > max_length:
        text = text[:max_length]
    
    return text


def validate_email(email: str) -> str:
    """Validate email format."""
    if not email:
        raise ValidationError("Email cannot be empty")
    
    email = email.strip().lower()
    
    # Basic email validation
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        raise ValidationError("Invalid email format")
    
    if len(email) > 254:  # RFC 5321 limit
        raise ValidationError("Email address is too long")
    
    return email


def validate_password(password: str) -> str:
    """Validate password strength."""
    if not password:
        raise ValidationError("Password cannot be empty")
    
    if len(password) < 8:
        raise ValidationError("Password must be at least 8 characters")
    
    if len(password) > 128:
        raise ValidationError("Password is too long")
    
    # Check for at least one letter and one number
    if not re.search(r'[a-zA-Z]', password):
        raise ValidationError("Password must contain at least one letter")
    
    if not re.search(r'[0-9]', password):
        raise ValidationError("Password must contain at least one number")
    
    return password


def is_safe_redirect_url(target: Optional[str], host_url: str) -> bool:
    """True when ``target`` (e.g. a ``?next=`` value) stays on this site.

    Browsers read a backslash like a slash, so ``/\\evil.com`` means
    ``//evil.com``; backslashes and control characters are refused outright.
    """
    if not target or '\\' in target or any(ord(ch) < 32 or ord(ch) == 127 for ch in target):
        return False
    ref = urlsplit(host_url)
    test = urlsplit(urljoin(host_url, target))
    return test.scheme in ('http', 'https') and test.netloc == ref.netloc


MAX_BODYWEIGHT_KG = 500


def parse_bodyweight(raw) -> Optional[float]:
    """A bodyweight typed in kg: None when blank, else a number from 1 to 500.

    float() also takes "nan", "inf" and "1e5"; any of those would be used for every
    bodyweight exercise (and NaN can't even be sent to the Stats page as JSON)."""
    text = str(raw if raw is not None else '').strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValidationError("Bodyweight must be a number.")
    if not math.isfinite(value) or value < 1 or value > MAX_BODYWEIGHT_KG:
        raise ValidationError(f"Bodyweight must be between 1 and {MAX_BODYWEIGHT_KG} kg.")
    return value
