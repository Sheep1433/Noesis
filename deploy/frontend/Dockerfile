# syntax=docker/dockerfile:1
# Noesis 前端生产镜像：Node 构建 dist + Nginx 托管（由 deploy/docker-compose.yml 引用）
FROM node:22-bookworm AS fe-builder

WORKDIR /src/frontend

RUN corepack enable && corepack prepare pnpm@9.15.4 --activate

COPY frontend/package.json frontend/pnpm-lock.yaml ./

RUN pnpm install --frozen-lockfile

COPY frontend/ ./

RUN pnpm build

FROM nginx:1.27-alpine

COPY deploy/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=fe-builder /src/frontend/dist /usr/share/nginx/html

EXPOSE 80
