# Edge image of the hosted demo: the web app built for the HTTP API adapter and OIDC sign-in at ONE public host, served by nginx.
# Build context is the repository root (docker compose -f docker-compose.yml -f deploy/gcp/compose.gcp.yml).
FROM node:22-alpine AS build
WORKDIR /w
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
ARG VITE_PUBLIC_HOST
# VITE_HOSTED_DEMO=1: the reply text says the on-device model is not running here (web/src/i18n/strings.ts).
ENV VITE_API_ADAPTER=http \
    VITE_AUTH_MODE=oidc \
    VITE_HOSTED_DEMO=1 \
    VITE_OIDC_CLIENT_ID=screening-closure-web
RUN VITE_API_URL="https://${VITE_PUBLIC_HOST}/api" \
    VITE_OIDC_ISSUER="https://${VITE_PUBLIC_HOST}/auth/realms/screening-closure" \
    npx vite build

FROM nginx:1.27-alpine
# The official image renders /etc/nginx/templates/*.template with the container's environment (envsubst) at start.
COPY deploy/gcp/edge/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /w/dist /usr/share/nginx/html
ENV NGINX_ENVSUBST_FILTER=SC_
EXPOSE 8080
