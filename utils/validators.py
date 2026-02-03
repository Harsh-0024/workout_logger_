"""
Input validation utilities.
"""
import re
import html
from typing import Optional
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
