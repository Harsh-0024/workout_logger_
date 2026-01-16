"""
Input validation utilities.
"""
import re
from typing import Optional
from utils.errors import ValidationError


def validate_username(username: str) -> str:
    """Validate and sanitize username."""
    if not username:
        raise ValidationError("Username cannot be empty")
    
    username = username.strip().lower()
    
    # Only allow alphanumeric and underscore
    if not re.match(r'^[a-z0-9_]+$', username):
        raise ValidationError("Username can only contain letters, numbers, and underscores")
    
    if len(username) < 2 or len(username) > 30:
        raise ValidationError("Username must be between 2 and 30 characters")
    
    return username


def validate_exercise_name(exercise_name: str) -> str:
    """Validate exercise name."""
    if not exercise_name or not exercise_name.strip():
        raise ValidationError("Exercise name cannot be empty")
    
    exercise_name = exercise_name.strip()
    
    if len(exercise_name) > 100:
        raise ValidationError("Exercise name is too long")
    
    return exercise_name


def sanitize_text_input(text: str, max_length: Optional[int] = None) -> str:
    """Sanitize text input."""
    if not text:
        return ""
    
    text = text.strip()
    
    if max_length and len(text) > max_length:
        text = text[:max_length]
    
    return text
