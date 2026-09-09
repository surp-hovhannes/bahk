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
        shared = settings.PUBLIC_API_REDIS_MODE == 'shared'
        if not settings.PUBLIC_API_TRAFFIC_ENABLED:
            errors.append("Public traffic controls must be enabled.")
        if shared and settings.PUBLIC_API_RESPONSE_CACHE_ENABLED:
            errors.append("Shared Redis requires response caching to be disabled.")
        if not settings.PUBLIC_API_REDIS_URL:
            errors.append("Configure PUBLIC_API_REDIS_URL (or REDIS_URL in shared mode).")
        public_store = urlsplit(settings.PUBLIC_API_REDIS_URL)
        application_store = urlsplit(settings.REDIS_URL)
        if not shared and public_store.hostname and (public_store.hostname, public_store.port or 6379) == (
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
                policy = config.get("maxmemory-policy")
                if not shared and policy != "noeviction":
                    errors.append("Public Redis must use maxmemory-policy noeviction.")
                if shared:
                    if policy not in ('allkeys-lru', 'noeviction'):
                        errors.append('Shared Redis supports allkeys-lru or noeviction.')
                    memory = client.info('memory')
                    ceiling = int(config.get('maxmemory', 0))
                    if ceiling <= 0 or int(memory['used_memory']) >= ceiling * 0.8:
                        errors.append('Shared Redis requires at least 20% free memory before activation.')
                if int(config.get("maxmemory", 0)) <= 0:
                    errors.append("Public Redis needs an explicit maxmemory ceiling.")
                client.ping()
            except RedisError:
                errors.append("Cannot verify Redis connectivity/configuration; inspect the service policy.")
        if errors:
            raise CommandError("\n".join(errors))
        self.stdout.write(self.style.SUCCESS("Public API configuration and Redis checks passed."))
        if shared:
            self.stdout.write(self.style.WARNING(
                'Shared mode: eviction can reset quotas and telemetry; limits are best-effort. '
                'Monitor aggregate Redis memory, evictions, latency and Celery health. '
                'Headroom is a point-in-time check, not reserved capacity.'
            ))
        self.stdout.write(
            "Before enabling resources, verify trusted forwarding/origin access, CDN bypass, "
            "the metrics scrape, alert delivery, and upstream traffic protection. "
            "This command cannot certify those deployment gates."
        )
