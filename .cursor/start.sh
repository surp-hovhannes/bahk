#!/bin/bash

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

# Wait for Redis to be ready
until redis-cli ping; do
    echo "Waiting for Redis to start..."
    sleep 1
done

echo "Redis is ready!"

# Run Django commands
python manage.py migrate
python manage.py seed