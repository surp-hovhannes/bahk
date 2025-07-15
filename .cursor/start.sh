#!/bin/bash
# -----------------------------------------
# Development container setup script
# Installs dependencies, starts Redis, runs
# migrations & seeds, then exits cleanly.
# -----------------------------------------

# Exit on any error
set -e

# DEBUG: Copy redis.conf into system if present
if [ -f ".cursor/redis.conf" ]; then
    sudo cp .cursor/redis.conf /etc/redis/redis.conf
    sudo chown vscode:vscode /etc/redis/redis.conf
fi

# DEBUG: Install Python dependencies (if any)
if [ -f "requirements.txt" ]; then
    pip install --no-cache-dir -r requirements.txt
fi

# DEBUG: Launch Redis in background; capture PID for later clean shutdown
sudo redis-server /etc/redis/redis.conf &
REDIS_PID=$!

# DEBUG: Wait until Redis responds to PING before continuing
until redis-cli ping; do
    echo "Waiting for Redis to start..."
    sleep 1
done

echo "Redis is ready!"

# DEBUG: Apply Django migrations
python manage.py migrate
# DEBUG: Populate database with seed data
python manage.py seed

# DEBUG: Stop Redis to avoid lingering background process (container restarts it next time)
echo "Stopping Redis server (PID: $REDIS_PID)"
sudo kill $REDIS_PID

# DEBUG: Setup finished; exiting script
echo "Setup completed successfully!"
exit 0