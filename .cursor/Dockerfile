# Use Python 3.11 as the base image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    REDIS_HOST=redis \
    REDIS_PORT=6379

# Create a non-root user with explicit UID/GID
RUN groupadd --gid 1000 vscode \
    && useradd --uid 1000 --gid 1000 -m vscode \
    && apt-get update \
    && apt-get install -y sudo \
    && echo vscode ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/vscode \
    && chmod 0440 /etc/sudoers.d/vscode

# Set work directory to user's home
WORKDIR /home/vscode

# Install system dependencies and Redis
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    libpq-dev \
    vim \
    htop \
    procps \
    redis-server \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Configure Redis
RUN mkdir -p /var/run/redis \
    && chown -R vscode:vscode /var/run/redis \
    && chown -R vscode:vscode /var/lib/redis \
    && chown -R vscode:vscode /var/log/redis

# Copy Redis configuration
COPY .cursor/redis.conf /etc/redis/redis.conf
RUN chown vscode:vscode /etc/redis/redis.conf

# Add redis to hosts file
RUN echo "127.0.0.1 redis" >> /etc/hosts

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf /root/.cache/pip/*

# Set ownership and permissions
RUN chown -R vscode:vscode /home/vscode

# Create startup script and set permissions
COPY .cursor/start.sh /home/vscode/start.sh
RUN chmod +x /home/vscode/start.sh

# Switch to non-root user
USER vscode

# Add healthcheck for both application and Redis
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/ || exit 1

# Use startup script as entrypoint
CMD ["/home/vscode/start.sh"]
