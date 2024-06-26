# Use the official Python 3.12 image from the Docker Hub
FROM mcr.microsoft.com/devcontainers/python:3.12

# Install Redis server
RUN apt-get update && apt-get install -y redis-server

# Expose the Redis default port
EXPOSE 6379

# Start Redis server in the background
CMD ["redis-server", "--daemonize", "yes"]
