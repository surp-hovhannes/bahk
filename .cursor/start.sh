#!/bin/bash

# Start Redis server
redis-server /etc/redis/redis.conf &

# Wait for Redis to be ready
until redis-cli ping; do
    echo "Waiting for Redis to start..."
    sleep 1
done

echo "Redis is ready!"

# Keep container running
tail -f /var/log/redis/redis-server.log 