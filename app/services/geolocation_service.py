"""
Geolocation service for IP address lookup.

This module provides IP-based geolocation using the ip-api.com free service.
It's used for login notifications and security alerts.
"""

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class GeoLocation:
    """Geolocation data from IP lookup."""

    ip: str
    country: str | None = None
    country_code: str | None = None
    region: str | None = None
    region_name: str | None = None
    city: str | None = None
    zip_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    timezone: str | None = None
    isp: str | None = None
    org: str | None = None
    is_vpn: bool = False
    is_proxy: bool = False
    is_hosting: bool = False

    @property
    def display_location(self) -> str:
        """Get a formatted display string for the location."""
        parts = []
        if self.city:
            parts.append(self.city)
        if self.region_name and self.region_name != self.city:
            parts.append(self.region_name)
        if self.country:
            parts.append(self.country)

        if parts:
            return ", ".join(parts)
        return "Unknown location"

    @property
    def short_location(self) -> str:
        """Get a short location string (city, country)."""
        if self.city and self.country:
            return f"{self.city}, {self.country}"
        if self.country:
            return self.country
        return "Unknown"


class GeolocationService:
    """
    Service for IP-based geolocation lookups.

    Uses ip-api.com free API for lookups. Rate limited to 45 requests/minute
    on the free tier, so use caching in production.
    """

    API_URL = "http://ip-api.com/json"
    TIMEOUT = 5.0

    def __init__(self) -> None:
        """Initialize the geolocation service."""
        self._cache: dict[str, GeoLocation] = {}

    async def lookup(self, ip_address: str) -> GeoLocation:
        """
        Look up geolocation data for an IP address.

        Args:
            ip_address: The IP address to look up.

        Returns:
            GeoLocation object with location data.
        """
        # Return cached result if available
        if ip_address in self._cache:
            return self._cache[ip_address]

        # Handle localhost/private IPs
        if self._is_private_ip(ip_address):
            return GeoLocation(
                ip=ip_address,
                city="Local",
                country="Local Network",
            )

        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.get(
                    f"{self.API_URL}/{ip_address}",
                    params={
                        "fields": "status,message,country,countryCode,region,regionName,"
                        "city,zip,lat,lon,timezone,isp,org,proxy,hosting,query"
                    },
                )

                if response.status_code != 200:
                    logger.warning(f"Geolocation API error for {ip_address}: {response.status_code}")
                    return GeoLocation(ip=ip_address)

                data = response.json()

                if data.get("status") != "success":
                    logger.warning(f"Geolocation lookup failed for {ip_address}: {data.get('message')}")
                    return GeoLocation(ip=ip_address)

                location = GeoLocation(
                    ip=ip_address,
                    country=data.get("country"),
                    country_code=data.get("countryCode"),
                    region=data.get("region"),
                    region_name=data.get("regionName"),
                    city=data.get("city"),
                    zip_code=data.get("zip"),
                    latitude=data.get("lat"),
                    longitude=data.get("lon"),
                    timezone=data.get("timezone"),
                    isp=data.get("isp"),
                    org=data.get("org"),
                    is_proxy=data.get("proxy", False),
                    is_hosting=data.get("hosting", False),
                )

                # Cache the result
                self._cache[ip_address] = location

                return location

        except httpx.TimeoutException:
            logger.warning(f"Geolocation lookup timed out for {ip_address}")
            return GeoLocation(ip=ip_address)
        except Exception as e:
            logger.error(f"Geolocation lookup error for {ip_address}: {e}")
            return GeoLocation(ip=ip_address)

    def _is_private_ip(self, ip: str) -> bool:
        """Check if an IP address is private/local."""
        if not ip:
            return True

        # Handle common local addresses
        if ip in ("localhost", "127.0.0.1", "::1"):
            return True

        # Check private IP ranges
        try:
            parts = ip.split(".")
            if len(parts) != 4:
                return False

            first_octet = int(parts[0])
            second_octet = int(parts[1])

            # 10.0.0.0/8
            if first_octet == 10:
                return True
            # 172.16.0.0/12
            if first_octet == 172 and 16 <= second_octet <= 31:
                return True
            # 192.168.0.0/16
            if first_octet == 192 and second_octet == 168:
                return True
            # 169.254.0.0/16 (link-local)
            if first_octet == 169 and second_octet == 254:
                return True

        except (ValueError, IndexError):
            pass

        return False


# Singleton instance
_geolocation_service: GeolocationService | None = None


def get_geolocation_service() -> GeolocationService:
    """Get the singleton geolocation service instance."""
    global _geolocation_service
    if _geolocation_service is None:
        _geolocation_service = GeolocationService()
    return _geolocation_service
