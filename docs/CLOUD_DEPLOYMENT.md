# Cloud Deployment Guide

This guide covers deploying Fish Audio LiveKit agents to various cloud platforms.

## Quick Start

```bash
# Make the script executable (first time only)
chmod +x cloud_deploy.sh

# Deploy to your preferred platform
./cloud_deploy.sh docker       # Run locally with Docker
./cloud_deploy.sh fly          # Deploy to Fly.io
./cloud_deploy.sh railway      # Deploy to Railway
./cloud_deploy.sh render       # Deploy to Render
```

## Prerequisites

### Required Environment Variables

Create a `.env` file with these variables:

```bash
# Fish Audio API Key (required)
FISHAUDIO_API_KEY=your_fishaudio_api_key

# LiveKit Configuration (required for production)
LIVEKIT_URL=wss://your-livekit-server.com
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

# LLM Configuration (optional, depends on your agent)
OPENAI_API_KEY=your_openai_api_key

# STT Configuration (optional, depends on your agent)
DEEPGRAM_API_KEY=your_deepgram_api_key
```

### Platform-Specific Requirements

**Docker:**
- Docker installed: https://docs.docker.com/get-docker/

**Fly.io:**
- Fly CLI installed: https://fly.io/docs/hands-on/install-flyctl/
- Fly.io account: https://fly.io/app/sign-up

**Railway:**
- Railway CLI installed: https://docs.railway.app/develop/cli
- Railway account: https://railway.app/

**Render:**
- Render account: https://dashboard.render.com/register
- Git repository connected to Render

## Deployment Options

### 1. Local Docker Deployment

Test your agent locally using Docker:

```bash
# Build and run
./cloud_deploy.sh docker

# Or with custom env file
./cloud_deploy.sh docker --env-file .env.local
```

This will:
1. Create a Dockerfile if it doesn't exist
2. Build the Docker image
3. Run the container with your environment variables

### 2. Fly.io Deployment

Deploy to Fly.io for scalable cloud hosting:

```bash
# First time setup
fly auth login

# Deploy
./cloud_deploy.sh fly

# Deploy with custom settings
./cloud_deploy.sh fly --env-file .env.production
```

**Features:**
- Automatic SSL/TLS
- Global CDN
- Auto-scaling
- Health checks
- Zero-downtime deployments

**Managing your deployment:**
```bash
fly status                    # Check deployment status
fly logs                      # View logs
fly ssh console              # SSH into container
fly scale count 2            # Scale to 2 instances
fly secrets list             # List secrets
```

### 3. Railway Deployment

Deploy to Railway for simple, GitHub-integrated hosting:

```bash
# First time setup
railway login

# Deploy
./cloud_deploy.sh railway
```

**Features:**
- GitHub integration
- Automatic deployments on push
- Environment variable management
- Built-in metrics

**Managing your deployment:**
```bash
railway status               # Check status
railway logs                 # View logs
railway run bash            # Run commands in environment
railway variables           # Manage environment variables
```

### 4. Render Deployment

Deploy to Render using infrastructure-as-code:

```bash
./cloud_deploy.sh render
```

This creates a `render.yaml` file. Then:

1. Push your code to GitHub/GitLab
2. Connect repository at https://dashboard.render.com/
3. Select "New Blueprint Instance"
4. Point to your repository
5. Set environment variables in dashboard
6. Deploy

**Features:**
- Free tier available
- Automatic SSL
- DDoS protection
- Auto-deploy from Git

## Advanced Usage

### Custom Docker Image

Build with a custom tag:

```bash
./cloud_deploy.sh build --tag v0.1.5
```

Push to a custom registry:

```bash
./cloud_deploy.sh build --registry ghcr.io/username --tag v0.1.5
./cloud_deploy.sh push --registry ghcr.io/username --tag v0.1.5
```

### Custom Dockerfile

The script auto-generates a Dockerfile, but you can customize it:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Your custom setup here
RUN apt-get update && apt-get install -y \
    your-dependencies

# Copy and install package
COPY . .
RUN pip install -e .

# Your custom CMD
CMD ["python", "your_agent.py", "start"]
```

### Environment-Specific Configurations

Create multiple environment files:

```bash
.env.development    # For local development
.env.staging        # For staging environment
.env.production     # For production
```

Deploy with specific environment:

```bash
./cloud_deploy.sh fly --env-file .env.production
./cloud_deploy.sh railway --env-file .env.staging
```

### Health Checks

Add a health check endpoint to your agent:

```python
from aiohttp import web

async def health_check(request):
    return web.json_response({"status": "healthy"})

app = web.Application()
app.router.add_get("/health", health_check)
```

Update Dockerfile to expose port:

```dockerfile
EXPOSE 8080
CMD ["python", "-m", "aiohttp.web", "-H", "0.0.0.0", "-P", "8080", "your_app:app"]
```

## Monitoring & Logging

### Fly.io Monitoring

```bash
# Real-time logs
fly logs

# Metrics
fly dashboard

# SSH for debugging
fly ssh console
```

### Railway Monitoring

```bash
# View logs
railway logs

# Open web dashboard
railway open
```

### Render Monitoring

- Access logs via dashboard: https://dashboard.render.com/
- Set up log drains for external monitoring
- Configure alerts for errors

## Troubleshooting

### Build Failures

**Issue:** Docker build fails

```bash
# Check Dockerfile syntax
docker build --no-cache -t test .

# View detailed build logs
./cloud_deploy.sh build 2>&1 | tee build.log
```

### Environment Variable Issues

**Issue:** Missing API keys

```bash
# Verify .env file
cat .env

# Check if secrets are set (Fly.io)
fly secrets list

# Check variables (Railway)
railway variables
```

### Connection Issues

**Issue:** Agent can't connect to LiveKit

1. Verify `LIVEKIT_URL` is correct (should start with `wss://`)
2. Check API key and secret are valid
3. Verify network/firewall settings
4. Check LiveKit server logs

### Performance Issues

**Issue:** Slow response times

1. Check resource limits (CPU/memory)
2. Scale horizontally:
   ```bash
   fly scale count 3              # Fly.io
   ```
3. Monitor metrics in platform dashboard
4. Consider upgrading to higher tier

## Cost Optimization

### Fly.io
- Free tier: 3 shared-CPU VMs + 3GB persistent storage
- Auto-scale down during low traffic
- Pay only for what you use

### Railway
- Free tier: $5 credit/month
- Pause deployments when not in use
- Monitor usage in dashboard

### Render
- Free tier available for web services
- Automatic sleep after inactivity
- Upgrade to paid for always-on

## Security Best Practices

1. **Never commit secrets**
   ```bash
   # Add to .gitignore
   echo ".env*" >> .gitignore
   echo "!.env.example" >> .gitignore
   ```

2. **Use platform secret management**
   - Fly.io: `fly secrets set KEY=value`
   - Railway: `railway variables set KEY=value`
   - Render: Dashboard environment variables

3. **Rotate API keys regularly**
   ```bash
   # Update secrets
   fly secrets set FISHAUDIO_API_KEY=new_key
   railway variables set FISHAUDIO_API_KEY=new_key
   ```

4. **Use HTTPS/WSS only**
   - All platforms provide SSL by default
   - Verify `LIVEKIT_URL` uses `wss://`

5. **Restrict API access**
   - Use LiveKit room permissions
   - Implement rate limiting
   - Monitor usage patterns

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Deploy to Fly.io

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: superfly/flyctl-actions/setup-flyctl@master
      - run: flyctl deploy --remote-only
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

### Railway Auto-Deploy

Railway automatically deploys on git push when connected to GitHub.

## Support

- **Fish Audio**: https://docs.fish.audio/
- **LiveKit**: https://docs.livekit.io/
- **Fly.io**: https://fly.io/docs/
- **Railway**: https://docs.railway.app/
- **Render**: https://render.com/docs/

## License

See [LICENSE](../LICENSE) file for details.
