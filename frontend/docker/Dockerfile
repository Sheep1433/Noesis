# ========== Frontend Build Stage ==========
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files
COPY package.json pnpm-lock.yaml* ./

# Install dependencies
RUN corepack enable && corepack prepare pnpm@9.0.0 --activate
RUN pnpm install --frozen-lockfile

# Copy source code
COPY . .

# Build for production
RUN pnpm build

# ========== Frontend Runtime Stage ==========
FROM nginx:stable-alpine

# Copy custom nginx config
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

# Copy built assets from builder
COPY --from=builder /app/dist /usr/share/nginx/html

# Expose port
EXPOSE 80

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost:80/health || exit 1

# Start Nginx
CMD ["nginx", "-g", "daemon off;"]
