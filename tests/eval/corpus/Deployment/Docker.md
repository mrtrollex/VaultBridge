# Container Deployment

## Compose Deployment

Build the Docker image, configure environment variables, mount persistent volumes, and start the container with Docker Compose. The deployment health check must pass before traffic reaches the service.

## Persistent Volumes

Container data survives image replacement because database files and configuration live in mounted volumes. Back up each volume before a deployment and restore it only after stopping the container.
