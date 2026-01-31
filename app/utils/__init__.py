"""
Utility modules for StackAlpha.
"""

from app.utils.crypto import generate_random_string, hash_string
from app.utils.email import (
    EMAIL_SUBJECTS,
    EmailTemplates,
    format_currency,
    format_date,
    format_datetime,
    format_duration,
    format_percentage,
    get_base_email_context,
    get_email_subject,
    get_greeting,
    sanitize_html,
    truncate_address,
    validate_email_address,
)
from app.utils.validators import validate_ethereum_address

__all__ = [
    # Crypto utilities
    "generate_random_string",
    "hash_string",
    # Email utilities
    "EMAIL_SUBJECTS",
    "EmailTemplates",
    "format_currency",
    "format_date",
    "format_datetime",
    "format_duration",
    "format_percentage",
    "get_base_email_context",
    "get_email_subject",
    "get_greeting",
    "sanitize_html",
    "truncate_address",
    "validate_email_address",
    # Validators
    "validate_ethereum_address",
]
