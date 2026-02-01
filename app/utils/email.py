"""
Email utilities for StackAlpha.

This module provides utility functions for email operations including
template context preparation, email validation, and formatting helpers.
"""

from datetime import UTC, datetime
from typing import Any

from email_validator import EmailNotValidError, validate_email


def validate_email_address(email: str) -> tuple[bool, str | None]:
    """
    Validate an email address.

    Args:
        email: Email address to validate.

    Returns:
        Tuple of (is_valid, normalized_email or error_message).
    """
    try:
        validation = validate_email(email, check_deliverability=False)
        return True, validation.normalized
    except EmailNotValidError as e:
        return False, str(e)


def get_base_email_context(email: str, name: str | None = None) -> dict[str, Any]:
    """
    Get the base context for all email templates.

    Args:
        email: Recipient email address.
        name: Recipient name (optional).

    Returns:
        Dictionary with base template context.
    """
    return {
        "email": email,
        "name": name or email.split("@")[0],
        "current_year": datetime.now(UTC).year,
        "app_name": "StackAlpha",
        "support_email": "tech@stackalpha.xyz",
        "finance_email": "finance@stackalpha.xyz",
        "base_url": "https://stackalpha.xyz",
    }


def format_currency(amount: float, currency: str = "USD", decimals: int = 2) -> str:
    """
    Format a currency amount.

    Args:
        amount: Amount to format.
        currency: Currency code (default: USD).
        decimals: Number of decimal places.

    Returns:
        Formatted currency string.
    """
    if currency == "USD":
        return f"${amount:,.{decimals}f}"
    return f"{amount:,.{decimals}f} {currency}"


def format_percentage(value: float, decimals: int = 2, include_sign: bool = False) -> str:
    """
    Format a percentage value.

    Args:
        value: Percentage value.
        decimals: Number of decimal places.
        include_sign: Whether to include + sign for positive values.

    Returns:
        Formatted percentage string.
    """
    sign = "+" if include_sign and value > 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def format_datetime(
    dt: datetime,
    format_str: str = "%B %d, %Y at %H:%M UTC",
) -> str:
    """
    Format a datetime object for display in emails.

    Args:
        dt: Datetime object to format.
        format_str: strftime format string.

    Returns:
        Formatted datetime string.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime(format_str)


def format_date(dt: datetime, format_str: str = "%B %d, %Y") -> str:
    """
    Format a date for display in emails.

    Args:
        dt: Datetime object to format.
        format_str: strftime format string.

    Returns:
        Formatted date string.
    """
    return dt.strftime(format_str)


def format_duration(seconds: int) -> str:
    """
    Format a duration in seconds to a human-readable string.

    Args:
        seconds: Duration in seconds.

    Returns:
        Human-readable duration string.
    """
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"

    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"

    hours = minutes // 60
    remaining_minutes = minutes % 60
    if hours < 24:
        if remaining_minutes:
            return f"{hours}h {remaining_minutes}m"
        return f"{hours} hour{'s' if hours != 1 else ''}"

    days = hours // 24
    remaining_hours = hours % 24
    if remaining_hours:
        return f"{days}d {remaining_hours}h"
    return f"{days} day{'s' if days != 1 else ''}"


def truncate_address(address: str, prefix_len: int = 10, suffix_len: int = 8) -> str:
    """
    Truncate a wallet/transaction address for display.

    Args:
        address: Full address string.
        prefix_len: Number of characters to show at start.
        suffix_len: Number of characters to show at end.

    Returns:
        Truncated address string.
    """
    if len(address) <= prefix_len + suffix_len + 3:
        return address
    return f"{address[:prefix_len]}...{address[-suffix_len:]}"


def sanitize_html(text: str) -> str:
    """
    Sanitize text for safe inclusion in HTML emails.

    Args:
        text: Text to sanitize.

    Returns:
        Sanitized text safe for HTML.
    """
    replacements = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#x27;",
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text


def get_greeting(name: str | None = None, time_of_day: datetime | None = None) -> str:
    """
    Get a time-appropriate greeting.

    Args:
        name: Optional name to include in greeting.
        time_of_day: Datetime to determine time of day (defaults to UTC now).

    Returns:
        Greeting string.
    """
    if time_of_day is None:
        time_of_day = datetime.now(UTC)

    hour = time_of_day.hour
    if 5 <= hour < 12:
        greeting = "Good morning"
    elif 12 <= hour < 17:
        greeting = "Good afternoon"
    elif 17 <= hour < 21:
        greeting = "Good evening"
    else:
        greeting = "Hello"

    if name:
        return f"{greeting}, {name}"
    return greeting


# Email template names enum-like constants
class EmailTemplates:
    """Available email template names."""

    WELCOME = "welcome"
    VERIFICATION = "verification"
    PASSWORD_RESET = "password_reset"
    LOGIN_NOTIFICATION = "login_notification"
    SUBSCRIPTION_ACTIVATED = "subscription_activated"
    SUBSCRIPTION_EXPIRING = "subscription_expiring"
    SUBSCRIPTION_EXPIRED = "subscription_expired"
    PAYMENT_RECEIVED = "payment_received"
    PAYMENT_FAILED = "payment_failed"
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    AFFILIATE_COMMISSION = "affiliate_commission"
    AFFILIATE_PAYOUT = "affiliate_payout"
    WALLET_CONNECTED = "wallet_connected"
    SECURITY_ALERT = "security_alert"


# Subject lines for each template
EMAIL_SUBJECTS = {
    EmailTemplates.WELCOME: "Welcome to StackAlpha!",
    EmailTemplates.VERIFICATION: "Verify your StackAlpha email address",
    EmailTemplates.PASSWORD_RESET: "Reset your StackAlpha password",
    EmailTemplates.LOGIN_NOTIFICATION: "New sign-in to your StackAlpha account",
    EmailTemplates.SUBSCRIPTION_ACTIVATED: "Your StackAlpha subscription is active!",
    EmailTemplates.SUBSCRIPTION_EXPIRING: "Your StackAlpha subscription expires in {days_remaining} days",
    EmailTemplates.SUBSCRIPTION_EXPIRED: "Your StackAlpha subscription has expired",
    EmailTemplates.PAYMENT_RECEIVED: "Payment received - Thank you!",
    EmailTemplates.PAYMENT_FAILED: "Payment failed - Action required",
    EmailTemplates.TRADE_OPENED: "Trade opened: {symbol} {direction}",
    EmailTemplates.TRADE_CLOSED: "Trade closed: {symbol} - {pnl_display}",
    EmailTemplates.AFFILIATE_COMMISSION: "You earned ${commission_amount} in commission!",
    EmailTemplates.AFFILIATE_PAYOUT: "Affiliate payout {status}",
    EmailTemplates.WALLET_CONNECTED: "Wallet connected successfully",
    EmailTemplates.SECURITY_ALERT: "Security alert: {alert_title}",
}


def get_email_subject(template: str, **kwargs: Any) -> str:
    """
    Get the subject line for an email template.

    Args:
        template: Template name.
        **kwargs: Variables to format into the subject.

    Returns:
        Formatted subject line.
    """
    subject = EMAIL_SUBJECTS.get(template, "StackAlpha Notification")
    try:
        return subject.format(**kwargs)
    except KeyError:
        return subject
