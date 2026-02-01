"""
Email service for StackAlpha.

This module provides a comprehensive email service using Jinja2 templates
and Zoho ZeptoMail API for async delivery. All emails are designed to be
sent asynchronously via Celery tasks to avoid blocking API requests.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

from app.config import settings
from app.core.exceptions import EmailError
from app.utils.email import (
    EmailTemplates,
    format_currency,
    format_date,
    format_datetime,
    format_duration,
    format_percentage,
    get_base_email_context,
    get_email_subject,
    truncate_address,
)

logger = logging.getLogger(__name__)


class EmailService:
    """
    Async email service using Jinja2 templates and Zoho ZeptoMail API.

    This service handles all email operations including template rendering
    and API delivery. It is designed to be used within Celery tasks for
    asynchronous email sending.
    """

    def __init__(self) -> None:
        """Initialize the email service with ZeptoMail configuration."""
        self.api_url = settings.zeptomail_api_url
        self.api_key = settings.zeptomail_api_key
        self.from_name = settings.email_from_name
        self.from_email = settings.email_from_address

        # Set up Jinja2 template environment
        template_dir = Path(__file__).parent.parent / "templates" / "email"
        self.template_env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )

        # Add custom filters
        self.template_env.filters["format_currency"] = format_currency
        self.template_env.filters["format_date"] = format_date
        self.template_env.filters["format_datetime"] = format_datetime
        self.template_env.filters["format_duration"] = format_duration
        self.template_env.filters["format_percentage"] = format_percentage
        self.template_env.filters["truncate_address"] = truncate_address

    def _render_template(
        self,
        template_name: str,
        context: dict[str, Any],
        is_html: bool = True,
    ) -> str:
        """
        Render an email template with the given context.

        Args:
            template_name: Name of the template file (without extension).
            context: Template context dictionary.
            is_html: Whether to render HTML or text template.

        Returns:
            Rendered template string.

        Raises:
            EmailError: If template cannot be found or rendered.
        """
        extension = "html" if is_html else "txt"
        full_template_name = f"{template_name}.{extension}"

        try:
            template = self.template_env.get_template(full_template_name)
            return template.render(**context)
        except TemplateNotFound as err:
            logger.error(f"Email template not found: {full_template_name}")
            raise EmailError(f"Email template not found: {full_template_name}") from err
        except Exception as e:
            logger.error(f"Failed to render template {full_template_name}: {e}")
            raise EmailError(f"Failed to render email template: {str(e)}") from e

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: str | None = None,
        to_name: str | None = None,
    ) -> bool:
        """
        Send an email via Zoho ZeptoMail API.

        Args:
            to_email: Recipient email address.
            subject: Email subject line.
            html_content: HTML email body.
            text_content: Plain text email body (optional).
            to_name: Recipient name (optional).

        Returns:
            True if email was sent successfully.

        Raises:
            EmailError: If email sending fails.
        """
        if not self.api_key:
            logger.warning(f"ZeptoMail API key not configured, skipping email to {to_email}")
            return False

        payload = {
            "from": {"address": self.from_email, "name": self.from_name},
            "to": [
                {
                    "email_address": {
                        "address": to_email,
                        "name": to_name or to_email.split("@")[0],
                    }
                }
            ],
            "subject": subject,
            "htmlbody": html_content,
        }

        if text_content:
            payload["textbody"] = text_content

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Zoho-enczapikey {self.api_key}",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                )

                if response.status_code == 200:
                    logger.info(f"Email sent successfully to {to_email}: {subject}")
                    return True
                else:
                    error_data = response.json() if response.content else {}
                    error_msg = error_data.get("message", response.text)
                    logger.error(
                        f"ZeptoMail API error ({response.status_code}) for {to_email}: {error_msg}"
                    )
                    raise EmailError(f"ZeptoMail API error: {error_msg}")

        except httpx.TimeoutException as e:
            logger.error(f"Timeout sending email to {to_email}: {e}")
            raise EmailError(f"Email request timed out: {str(e)}") from e
        except httpx.RequestError as e:
            logger.error(f"Request error sending email to {to_email}: {e}")
            raise EmailError(f"Email request failed: {str(e)}") from e
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            raise EmailError(f"Failed to send email: {str(e)}") from e

    async def send_template_email(
        self,
        to_email: str,
        template_name: str,
        context: dict[str, Any] | None = None,
        subject: str | None = None,
        name: str | None = None,
    ) -> bool:
        """
        Send an email using a template.

        Args:
            to_email: Recipient email address.
            template_name: Name of the template to use.
            context: Additional template context.
            subject: Custom subject line (optional).
            name: Recipient name (optional).

        Returns:
            True if email was sent successfully.

        Raises:
            EmailError: If email sending fails.
        """
        # Build context
        full_context = get_base_email_context(to_email, name)
        if context:
            full_context.update(context)

        # Determine subject
        if subject is None:
            subject = get_email_subject(template_name, **full_context)

        # Render templates
        html_content = self._render_template(template_name, full_context, is_html=True)
        text_content = self._render_template(template_name, full_context, is_html=False)

        return await self.send_email(to_email, subject, html_content, text_content, name)

    # Convenience methods for specific email types

    async def send_welcome_email(self, to_email: str, name: str | None = None) -> bool:
        """Send welcome email to new user."""
        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.WELCOME,
            name=name,
        )

    async def send_verification_email(
        self, to_email: str, token: str, name: str | None = None
    ) -> bool:
        """Send email verification email."""
        verification_url = f"https://stackalpha.xyz/verify?token={token}"
        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.VERIFICATION,
            context={"verification_url": verification_url},
            name=name,
        )

    async def send_password_reset_email(
        self, to_email: str, token: str, name: str | None = None
    ) -> bool:
        """Send password reset email."""
        reset_url = f"https://stackalpha.xyz/reset-password?token={token}"
        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.PASSWORD_RESET,
            context={"reset_url": reset_url},
            name=name,
        )

    async def send_subscription_activated_email(
        self,
        to_email: str,
        plan: str,
        expires_at: datetime,
        name: str | None = None,
    ) -> bool:
        """Send subscription activation email."""
        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.SUBSCRIPTION_ACTIVATED,
            context={
                "plan": plan,
                "expires_at": format_date(expires_at),
            },
            name=name,
        )

    async def send_subscription_expiring_email(
        self,
        to_email: str,
        days_remaining: int,
        expires_at: datetime,
        name: str | None = None,
    ) -> bool:
        """Send subscription expiring reminder email."""
        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.SUBSCRIPTION_EXPIRING,
            context={
                "days_remaining": days_remaining,
                "expires_at": format_date(expires_at),
            },
            subject=f"Your StackAlpha subscription expires in {days_remaining} days",
            name=name,
        )

    async def send_subscription_expired_email(
        self,
        to_email: str,
        grace_period_ends: datetime,
        name: str | None = None,
    ) -> bool:
        """Send subscription expired email."""
        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.SUBSCRIPTION_EXPIRED,
            context={
                "grace_period_ends": format_date(grace_period_ends),
            },
            name=name,
        )

    async def send_payment_received_email(
        self,
        to_email: str,
        amount_usd: float,
        currency: str,
        transaction_id: str,
        payment_date: datetime,
        plan: str | None = None,
        expires_at: datetime | None = None,
        name: str | None = None,
    ) -> bool:
        """Send payment received confirmation email."""
        context = {
            "amount_usd": f"{amount_usd:.2f}",
            "currency": currency,
            "transaction_id": transaction_id,
            "payment_date": format_datetime(payment_date),
        }
        if plan:
            context["plan"] = plan
        if expires_at:
            context["expires_at"] = format_date(expires_at)

        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.PAYMENT_RECEIVED,
            context=context,
            name=name,
        )

    async def send_payment_failed_email(
        self,
        to_email: str,
        amount_usd: float,
        error_message: str | None = None,
        name: str | None = None,
    ) -> bool:
        """Send payment failed notification email."""
        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.PAYMENT_FAILED,
            context={
                "amount_usd": f"{amount_usd:.2f}",
                "error_message": error_message,
            },
            name=name,
        )

    async def send_trade_opened_email(
        self,
        to_email: str,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        position_size_usd: float,
        leverage: int,
        take_profit_price: float,
        stop_loss_price: float,
        signal_confidence: float | None = None,
        signal_reason: str | None = None,
        name: str | None = None,
    ) -> bool:
        """Send trade opened notification email."""
        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.TRADE_OPENED,
            context={
                "trade_id": trade_id,
                "symbol": symbol,
                "direction": direction,
                "entry_price": f"{entry_price:,.2f}",
                "position_size_usd": f"{position_size_usd:,.2f}",
                "leverage": leverage,
                "take_profit_price": f"{take_profit_price:,.2f}",
                "stop_loss_price": f"{stop_loss_price:,.2f}",
                "signal_confidence": f"{signal_confidence:.0f}" if signal_confidence else None,
                "signal_reason": signal_reason,
            },
            subject=f"Trade opened: {symbol} {direction.upper()}",
            name=name,
        )

    async def send_trade_closed_email(
        self,
        to_email: str,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        position_size_usd: float,
        leverage: int,
        pnl: float,
        pnl_percent: float,
        fees_paid: float,
        close_reason: str,
        duration_seconds: int,
        name: str | None = None,
    ) -> bool:
        """Send trade closed notification email."""
        pnl_display = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.TRADE_CLOSED,
            context={
                "trade_id": trade_id,
                "symbol": symbol,
                "direction": direction,
                "entry_price": f"{entry_price:,.2f}",
                "exit_price": f"{exit_price:,.2f}",
                "position_size_usd": f"{position_size_usd:,.2f}",
                "leverage": leverage,
                "pnl": f"{abs(pnl):.2f}" if pnl < 0 else f"{pnl:.2f}",
                "pnl_percent": f"{pnl_percent:.2f}",
                "fees_paid": f"{fees_paid:.2f}",
                "close_reason": close_reason,
                "duration": format_duration(duration_seconds),
            },
            subject=f"Trade closed: {symbol} - {pnl_display}",
            name=name,
        )

    async def send_affiliate_commission_email(
        self,
        to_email: str,
        commission_amount: float,
        commission_rate: float,
        original_amount: float,
        referral_email: str,
        commission_type: str,
        commission_date: datetime,
        pending_earnings: float,
        total_earnings: float,
        referral_code: str,
        initial_commission_rate: float = 20.0,
        renewal_commission_rate: float = 5.0,
        name: str | None = None,
    ) -> bool:
        """Send affiliate commission earned notification email."""
        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.AFFILIATE_COMMISSION,
            context={
                "commission_amount": f"{commission_amount:.2f}",
                "commission_rate": f"{commission_rate:.0f}",
                "original_amount": f"{original_amount:.2f}",
                "referral_email": referral_email,
                "commission_type": commission_type,
                "commission_date": format_date(commission_date),
                "pending_earnings": f"{pending_earnings:.2f}",
                "total_earnings": f"{total_earnings:.2f}",
                "referral_code": referral_code,
                "initial_commission_rate": f"{initial_commission_rate:.0f}",
                "renewal_commission_rate": f"{renewal_commission_rate:.0f}",
            },
            subject=f"You earned ${commission_amount:.2f} in commission!",
            name=name,
        )

    async def send_affiliate_payout_email(
        self,
        to_email: str,
        amount: float,
        currency: str,
        payout_address: str,
        status: str,
        payout_date: datetime,
        transaction_hash: str | None = None,
        error_message: str | None = None,
        name: str | None = None,
    ) -> bool:
        """Send affiliate payout notification email."""
        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.AFFILIATE_PAYOUT,
            context={
                "amount": f"{amount:.2f}",
                "currency": currency,
                "payout_address": payout_address,
                "status": status,
                "payout_date": format_datetime(payout_date),
                "transaction_hash": transaction_hash,
                "error_message": error_message,
            },
            subject=f"Affiliate payout {status}",
            name=name,
        )

    async def send_wallet_connected_email(
        self,
        to_email: str,
        wallet_address: str,
        connected_at: datetime,
        trading_enabled: bool = True,
        name: str | None = None,
    ) -> bool:
        """Send wallet connected notification email."""
        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.WALLET_CONNECTED,
            context={
                "wallet_address": wallet_address,
                "connected_at": format_datetime(connected_at),
                "trading_enabled": trading_enabled,
            },
            name=name,
        )

    async def send_security_alert_email(
        self,
        to_email: str,
        alert_type: str,
        alert_title: str,
        alert_description: str,
        alert_time: datetime,
        ip_address: str | None = None,
        location: str | None = None,
        device: str | None = None,
        name: str | None = None,
    ) -> bool:
        """Send security alert notification email."""
        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.SECURITY_ALERT,
            context={
                "alert_type": alert_type,
                "alert_title": alert_title,
                "alert_description": alert_description,
                "alert_time": format_datetime(alert_time),
                "ip_address": ip_address,
                "location": location,
                "device": device,
            },
            subject=f"Security alert: {alert_title}",
            name=name,
        )

    async def send_login_notification_email(
        self,
        to_email: str,
        ip_address: str,
        location: str,
        device: str,
        login_time: datetime,
        browser: str | None = None,
        os: str | None = None,
        timezone: str | None = None,
        isp: str | None = None,
        is_suspicious: bool = False,
        is_vpn: bool = False,
        is_new_location: bool = False,
        is_new_device: bool = False,
        name: str | None = None,
    ) -> bool:
        """
        Send login notification email with device and location details.

        This is an enterprise-grade login notification that includes:
        - Accurate device information (browser, OS, device type)
        - Geolocation data (city, region, country, timezone)
        - IP address and ISP information
        - Suspicious activity flags (VPN, new location, new device)
        """
        # Determine subject based on suspicious activity
        if is_suspicious:
            subject = "Security Alert: New sign-in to your StackAlpha account"
        else:
            subject = "New sign-in to your StackAlpha account"

        return await self.send_template_email(
            to_email=to_email,
            template_name=EmailTemplates.LOGIN_NOTIFICATION,
            context={
                "ip_address": ip_address,
                "location": location,
                "device": device,
                "login_time": format_datetime(login_time),
                "browser": browser,
                "os": os,
                "timezone": timezone,
                "isp": isp,
                "is_suspicious": is_suspicious,
                "is_vpn": is_vpn,
                "is_new_location": is_new_location,
                "is_new_device": is_new_device,
            },
            subject=subject,
            name=name,
        )


# Singleton instance
_email_service_instance: EmailService | None = None


def get_email_service() -> EmailService:
    """Get the singleton email service instance."""
    global _email_service_instance
    if _email_service_instance is None:
        _email_service_instance = EmailService()
    return _email_service_instance
