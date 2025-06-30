# Use Python 3.11 as the base image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    REDIS_URL=redis://localhost:6379/1

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

# Set ownership and permissions
RUN chown -R vscode:vscode /home/vscode

# Switch to non-root user
USER vscode

# Add healthcheck for both application and Redis
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/ || exit 1

# Use startup script as entrypoint
CMD ["bash"]
