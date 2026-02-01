"""
Device detection utility for parsing user-agent strings.

This module provides enterprise-grade device detection for login notifications
and security alerts.
"""

import re
from dataclasses import dataclass


@dataclass
class DeviceInfo:
    """Parsed device information from user-agent."""

    browser: str | None = None
    browser_version: str | None = None
    os: str | None = None
    os_version: str | None = None
    device_type: str = "Unknown"
    device_brand: str | None = None
    device_model: str | None = None
    is_mobile: bool = False
    is_tablet: bool = False
    is_bot: bool = False
    raw_user_agent: str | None = None

    @property
    def display_device(self) -> str:
        """Get a formatted display string for the device."""
        parts = []

        # Device type and model
        if self.device_brand and self.device_model:
            parts.append(f"{self.device_brand} {self.device_model}")
        elif self.device_type != "Unknown":
            parts.append(self.device_type)

        # Browser
        if self.browser:
            browser_str = self.browser
            if self.browser_version:
                browser_str += f" {self.browser_version}"
            parts.append(browser_str)

        # OS
        if self.os:
            os_str = self.os
            if self.os_version:
                os_str += f" {self.os_version}"
            parts.append(os_str)

        if parts:
            return " on ".join(parts[:2]) if len(parts) >= 2 else parts[0]
        return "Unknown device"

    @property
    def short_device(self) -> str:
        """Get a short device string (browser + OS)."""
        parts = []
        if self.browser:
            parts.append(self.browser)
        if self.os:
            parts.append(self.os)
        return " on ".join(parts) if parts else "Unknown device"


def parse_user_agent(user_agent: str | None) -> DeviceInfo:
    """
    Parse a user-agent string to extract device information.

    Args:
        user_agent: The user-agent string to parse.

    Returns:
        DeviceInfo object with parsed device details.
    """
    if not user_agent:
        return DeviceInfo()

    info = DeviceInfo(raw_user_agent=user_agent)

    # Detect bots
    bot_patterns = [
        r"bot", r"crawler", r"spider", r"scraper", r"curl", r"wget",
        r"python", r"java", r"perl", r"ruby", r"php", r"http"
    ]
    user_agent_lower = user_agent.lower()
    if any(pattern in user_agent_lower for pattern in bot_patterns):
        info.is_bot = True
        info.device_type = "Bot/Script"
        return info

    # Detect OS
    info.os, info.os_version = _detect_os(user_agent)

    # Detect browser
    info.browser, info.browser_version = _detect_browser(user_agent)

    # Detect device type and brand
    info.device_type, info.device_brand, info.device_model = _detect_device(user_agent)
    info.is_mobile = info.device_type in ("Mobile", "Smartphone")
    info.is_tablet = info.device_type == "Tablet"

    return info


def _detect_os(user_agent: str) -> tuple[str | None, str | None]:
    """Detect operating system from user-agent."""
    ua = user_agent

    # Windows
    if "Windows" in ua:
        version = None
        if "Windows NT 10.0" in ua:
            version = "10/11"
        elif "Windows NT 6.3" in ua:
            version = "8.1"
        elif "Windows NT 6.2" in ua:
            version = "8"
        elif "Windows NT 6.1" in ua:
            version = "7"
        return "Windows", version

    # macOS
    if "Mac OS X" in ua or "macOS" in ua:
        match = re.search(r"Mac OS X (\d+[._]\d+(?:[._]\d+)?)", ua)
        if match:
            version = match.group(1).replace("_", ".")
            return "macOS", version
        return "macOS", None

    # iOS
    if "iPhone" in ua or "iPad" in ua:
        match = re.search(r"OS (\d+_\d+(?:_\d+)?)", ua)
        if match:
            version = match.group(1).replace("_", ".")
            return "iOS", version
        return "iOS", None

    # Android
    if "Android" in ua:
        match = re.search(r"Android (\d+(?:\.\d+)?(?:\.\d+)?)", ua)
        if match:
            return "Android", match.group(1)
        return "Android", None

    # Linux
    if "Linux" in ua:
        if "Ubuntu" in ua:
            return "Ubuntu Linux", None
        if "Fedora" in ua:
            return "Fedora Linux", None
        return "Linux", None

    # Chrome OS
    if "CrOS" in ua:
        return "Chrome OS", None

    return None, None


def _detect_browser(user_agent: str) -> tuple[str | None, str | None]:
    """Detect browser from user-agent."""
    ua = user_agent

    # Edge (must check before Chrome)
    if "Edg/" in ua or "Edge/" in ua:
        match = re.search(r"Edg(?:e)?/(\d+(?:\.\d+)?)", ua)
        if match:
            return "Microsoft Edge", match.group(1)
        return "Microsoft Edge", None

    # Opera (must check before Chrome)
    if "OPR/" in ua or "Opera" in ua:
        match = re.search(r"OPR/(\d+(?:\.\d+)?)", ua)
        if match:
            return "Opera", match.group(1)
        return "Opera", None

    # Samsung Browser (must check before Chrome)
    if "SamsungBrowser" in ua:
        match = re.search(r"SamsungBrowser/(\d+(?:\.\d+)?)", ua)
        if match:
            return "Samsung Browser", match.group(1)
        return "Samsung Browser", None

    # Chrome
    if "Chrome/" in ua and "Chromium" not in ua:
        match = re.search(r"Chrome/(\d+(?:\.\d+)?)", ua)
        if match:
            return "Chrome", match.group(1)
        return "Chrome", None

    # Firefox
    if "Firefox/" in ua:
        match = re.search(r"Firefox/(\d+(?:\.\d+)?)", ua)
        if match:
            return "Firefox", match.group(1)
        return "Firefox", None

    # Safari (must check after Chrome)
    if "Safari/" in ua and "Chrome" not in ua:
        match = re.search(r"Version/(\d+(?:\.\d+)?)", ua)
        if match:
            return "Safari", match.group(1)
        return "Safari", None

    # Internet Explorer
    if "MSIE" in ua or "Trident" in ua:
        match = re.search(r"(?:MSIE |rv:)(\d+(?:\.\d+)?)", ua)
        if match:
            return "Internet Explorer", match.group(1)
        return "Internet Explorer", None

    return None, None


def _detect_device(user_agent: str) -> tuple[str, str | None, str | None]:
    """Detect device type, brand, and model from user-agent."""
    ua = user_agent

    # iPhone
    if "iPhone" in ua:
        match = re.search(r"iPhone(?:\s*)?(\d+)?", ua)
        model = f"iPhone {match.group(1)}" if match and match.group(1) else "iPhone"
        return "Smartphone", "Apple", model

    # iPad
    if "iPad" in ua:
        return "Tablet", "Apple", "iPad"

    # Samsung devices
    if "Samsung" in ua or "SM-" in ua:
        match = re.search(r"(SM-[A-Z]\d+[A-Z]?)", ua)
        if match:
            return "Smartphone", "Samsung", match.group(1)
        return "Smartphone", "Samsung", None

    # Pixel devices
    if "Pixel" in ua:
        match = re.search(r"(Pixel \d+[a-zA-Z]?)", ua)
        if match:
            return "Smartphone", "Google", match.group(1)
        return "Smartphone", "Google", "Pixel"

    # Generic mobile
    if "Mobile" in ua or "Android" in ua:
        if "Tablet" in ua:
            return "Tablet", None, None
        return "Mobile", None, None

    # Mac
    if "Macintosh" in ua:
        return "Desktop", "Apple", "Mac"

    # Windows PC
    if "Windows" in ua:
        return "Desktop", None, "Windows PC"

    # Linux
    if "Linux" in ua and "Android" not in ua:
        return "Desktop", None, "Linux PC"

    return "Unknown", None, None
