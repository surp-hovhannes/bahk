"""Read-only checks for the infrastructure needed before mounting public v1."""

import ipaddress
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from redis.exceptions import RedisError

from bahk.public_api.traffic import connection


class Command(BaseCommand):
    help = "Check public-v1 configuration and Redis safety without enabling routes."

    def handle(self, *args, **options):
        errors = []
        if not settings.PUBLIC_API_TRAFFIC_ENABLED or not settings.PUBLIC_API_RESPONSE_CACHE_ENABLED:
            errors.append("Public traffic and response caching must be enabled.")
        if not settings.PUBLIC_API_REDIS_URL:
            errors.append("Configure a dedicated PUBLIC_API_REDIS_URL.")
        public_store = urlsplit(settings.PUBLIC_API_REDIS_URL)
        application_store = urlsplit(settings.REDIS_URL)
        if public_store.hostname and (public_store.hostname, public_store.port or 6379) == (
            application_store.hostname,
            application_store.port or 6379,
        ):
            errors.append("Public Redis must be isolated from application cache/Celery eviction.")
        if len(settings.PUBLIC_API_METRICS_TOKEN) < 32:
            errors.append("Configure PUBLIC_API_METRICS_TOKEN with at least 32 random characters.")
        if not 0 < settings.PUBLIC_API_RATE_MINUTE <= settings.PUBLIC_API_RATE_HOUR:
            errors.append("Rate limits must be positive with minute <= hour.")
        try:
            for network in settings.PUBLIC_API_TRUSTED_PROXIES:
                parsed = ipaddress.ip_network(network)
                if parsed.prefixlen == 0:
                    errors.append("Do not trust all addresses as proxies.")
        except ValueError:
            errors.append("PUBLIC_API_TRUSTED_PROXIES must contain valid CIDR networks.")
        if not errors:
            try:
                client = connection()
                config = client.config_get("maxmemory*")
                if config.get("maxmemory-policy") != "noeviction":
                    errors.append("Public Redis must use maxmemory-policy noeviction.")
                if int(config.get("maxmemory", 0)) <= 0:
                    errors.append("Public Redis needs an explicit maxmemory ceiling.")
                client.ping()
            except RedisError:
                errors.append("Cannot verify Redis connectivity/configuration; inspect the service policy.")
        if errors:
            raise CommandError("\n".join(errors))
        self.stdout.write(self.style.SUCCESS("Public API configuration and Redis checks passed."))
        self.stdout.write(
            "Before enabling resources, verify trusted forwarding/origin access, CDN bypass, "
            "the metrics scrape, alert delivery, and upstream traffic protection. "
            "This command cannot certify those deployment gates."
        )
