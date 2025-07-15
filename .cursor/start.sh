#!/bin/bash

# Exit on any error
set -e

# Copy configuration files if they exist in the repository
if [ -f ".cursor/redis.conf" ]; then
    sudo cp .cursor/redis.conf /etc/redis/redis.conf
    sudo chown vscode:vscode /etc/redis/redis.conf
fi

# Install Python dependencies if requirements.txt exists
if [ -f "requirements.txt" ]; then
    pip install --no-cache-dir -r requirements.txt
fi

# Start Redis server
sudo redis-server /etc/redis/redis.conf &
REDIS_PID=$!

# 2️⃣  wait until it’s accepting connections
until redis-cli ping >/dev/null 2>&1 ; do
  echo "Waiting for Redis…"
  sleep 1
done

echo "Redis is ready!"

# Run Django commands
python manage.py migrate
python manage.py seed

echo "Setup completed successfully!"
exit 0