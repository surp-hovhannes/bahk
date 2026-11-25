from django_redis.pool import ConnectionFactory


class EngagementSnapshotConnectionFactory(ConnectionFactory):
    # django-redis ordinarily shares pools by URL, even across cache aliases.
    _pools = {}
