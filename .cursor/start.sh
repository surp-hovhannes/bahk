#!/bin/bash

# Start Redis server
redis-server /etc/redis/redis.conf &

# Wait for Redis to be ready
until redis-cli ping; do
    echo "Waiting for Redis to start..."
    sleep 1
done

echo "Redis is ready!"

# Run Django commands
python manage.py migrate
python manage.py seed

# Start Django server
python manage.py runserver 0.0.0.0:8000 