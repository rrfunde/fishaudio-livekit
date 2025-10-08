#!/bin/bash

# Fish Audio LiveKit Cloud Deployment Script
# Supports: Docker, Fly.io, Railway, Render, and generic cloud platforms

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Helper functions
print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}→ $1${NC}"
}

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Display usage
usage() {
    cat << EOF
Usage: $0 [COMMAND] [OPTIONS]

Commands:
  docker          Build and run Docker container locally
  fly             Deploy to Fly.io
  railway         Deploy to Railway
  render          Deploy to Render
  build           Build Docker image only
  push            Push Docker image to registry
  help            Show this help message

Options:
  --registry      Docker registry (default: docker.io)
  --tag           Docker image tag (default: latest)
  --env-file      Path to .env file (default: .env)

Examples:
  $0 docker                     # Run locally with Docker
  $0 build --tag v0.1.5         # Build Docker image
  $0 fly --env-file .env.prod   # Deploy to Fly.io
  $0 railway                    # Deploy to Railway

EOF
}

# Parse arguments
COMMAND="${1:-help}"
REGISTRY="docker.io"
TAG="latest"
ENV_FILE=".env"

shift || true
while [[ $# -gt 0 ]]; do
    case $1 in
        --registry)
            REGISTRY="$2"
            shift 2
            ;;
        --tag)
            TAG="$2"
            shift 2
            ;;
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        *)
            print_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Get image name from pyproject.toml or use default
IMAGE_NAME="fishaudio-livekit"
if [ -f "pyproject.toml" ]; then
    IMAGE_NAME=$(grep -E '^name = ' pyproject.toml | cut -d'"' -f2 || echo "fishaudio-livekit")
fi

FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${TAG}"

# Check environment file
check_env_file() {
    if [ ! -f "$ENV_FILE" ]; then
        print_error "Environment file not found: $ENV_FILE"
        print_info "Please create $ENV_FILE with required variables:"
        echo "  FISHAUDIO_API_KEY=your_key"
        echo "  LIVEKIT_URL=wss://your-livekit-server.com"
        echo "  LIVEKIT_API_KEY=your_livekit_key"
        echo "  LIVEKIT_API_SECRET=your_livekit_secret"
        exit 1
    fi
    print_success "Found environment file: $ENV_FILE"
}

# Build Docker image
build_docker() {
    print_info "Building Docker image: $FULL_IMAGE"

    if [ ! -f "Dockerfile" ]; then
        print_info "Creating Dockerfile..."
        create_dockerfile
    fi

    docker build -t "$FULL_IMAGE" .
    print_success "Docker image built: $FULL_IMAGE"
}

# Create Dockerfile
create_dockerfile() {
    cat > Dockerfile << 'EOF'
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy package files
COPY pyproject.toml README.md ./
COPY fishaudio_livekit ./fishaudio_livekit

# Install the package
RUN pip install --no-cache-dir -e .

# Install additional dependencies for agents
RUN pip install --no-cache-dir \
    livekit-agents \
    livekit-plugins-openai \
    livekit-plugins-deepgram \
    livekit-plugins-silero \
    python-dotenv

# Expose port (optional, for health checks)
EXPOSE 8080

# Default command (can be overridden)
CMD ["python", "-m", "fishaudio_livekit"]
EOF
    print_success "Created Dockerfile"
}

# Create .dockerignore
create_dockerignore() {
    if [ ! -f ".dockerignore" ]; then
        cat > .dockerignore << 'EOF'
__pycache__
*.pyc
*.pyo
*.pyd
.Python
*.so
*.egg
*.egg-info
dist
build
.git
.github
.vscode
.idea
*.md
!README.md
.env
.env.*
.DS_Store
*.log
EOF
        print_success "Created .dockerignore"
    fi
}

# Run Docker locally
run_docker() {
    check_env_file
    build_docker
    create_dockerignore

    print_info "Running Docker container..."
    docker run --rm -it \
        --env-file "$ENV_FILE" \
        "$FULL_IMAGE"
}

# Push Docker image
push_docker() {
    print_info "Pushing Docker image: $FULL_IMAGE"
    docker push "$FULL_IMAGE"
    print_success "Docker image pushed: $FULL_IMAGE"
}

# Deploy to Fly.io
deploy_fly() {
    if ! command_exists fly; then
        print_error "Fly CLI not found. Install from: https://fly.io/docs/hands-on/install-flyctl/"
        exit 1
    fi

    check_env_file

    # Create fly.toml if it doesn't exist
    if [ ! -f "fly.toml" ]; then
        print_info "Creating fly.toml..."
        cat > fly.toml << EOF
app = "fishaudio-livekit-${USER}"

[build]
  dockerfile = "Dockerfile"

[env]
  # Non-secret environment variables can go here

[[services]]
  internal_port = 8080
  protocol = "tcp"

  [[services.ports]]
    handlers = ["http"]
    port = 80

  [[services.ports]]
    handlers = ["tls", "http"]
    port = 443
EOF
        print_success "Created fly.toml"
    fi

    # Set secrets from env file
    print_info "Setting secrets from $ENV_FILE..."
    while IFS='=' read -r key value; do
        # Skip comments and empty lines
        [[ "$key" =~ ^#.*$ ]] && continue
        [[ -z "$key" ]] && continue

        # Remove quotes and whitespace
        value=$(echo "$value" | sed -e 's/^"//' -e 's/"$//' -e 's/^'"'"'//' -e 's/'"'"'$//')
        fly secrets set "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"

    print_info "Deploying to Fly.io..."
    fly deploy
    print_success "Deployed to Fly.io"
}

# Deploy to Railway
deploy_railway() {
    if ! command_exists railway; then
        print_error "Railway CLI not found. Install from: https://docs.railway.app/develop/cli"
        exit 1
    fi

    check_env_file

    print_info "Initializing Railway project..."
    railway init || true

    print_info "Setting environment variables..."
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^#.*$ ]] && continue
        [[ -z "$key" ]] && continue
        value=$(echo "$value" | sed -e 's/^"//' -e 's/"$//' -e 's/^'"'"'//' -e 's/'"'"'$//')
        railway variables set "$key=$value" || true
    done < "$ENV_FILE"

    print_info "Deploying to Railway..."
    railway up
    print_success "Deployed to Railway"
}

# Deploy to Render
deploy_render() {
    print_info "Deploying to Render..."

    if [ ! -f "render.yaml" ]; then
        print_info "Creating render.yaml..."
        cat > render.yaml << 'EOF'
services:
  - type: web
    name: fishaudio-livekit
    env: docker
    plan: starter
    dockerfilePath: ./Dockerfile
    envVars:
      - key: FISHAUDIO_API_KEY
        sync: false
      - key: LIVEKIT_URL
        sync: false
      - key: LIVEKIT_API_KEY
        sync: false
      - key: LIVEKIT_API_SECRET
        sync: false
      - key: OPENAI_API_KEY
        sync: false
      - key: DEEPGRAM_API_KEY
        sync: false
EOF
        print_success "Created render.yaml"
    fi

    print_success "render.yaml created. Please:"
    print_info "1. Connect your repository to Render: https://dashboard.render.com/"
    print_info "2. Set environment variables in the Render dashboard"
    print_info "3. Deploy from the dashboard"
}

# Main command handler
case "$COMMAND" in
    docker)
        run_docker
        ;;
    build)
        build_docker
        create_dockerignore
        ;;
    push)
        push_docker
        ;;
    fly)
        deploy_fly
        ;;
    railway)
        deploy_railway
        ;;
    render)
        deploy_render
        ;;
    help|--help|-h)
        usage
        ;;
    *)
        print_error "Unknown command: $COMMAND"
        usage
        exit 1
        ;;
esac
