# ==============================================================================
# AI Project Reviewer - web image
#
# Two stages: Node builds the bundle, nginx serves it. The runtime image carries
# no Node, no node_modules and no source - only static files and a config.
#
# NOTE on VITE_ variables: Vite inlines them at BUILD time, so they are baked
# into the JavaScript and readable by anyone who opens the page. Never pass a
# secret as a build argument here. The default `/api` is what this image wants
# anyway, because nginx proxies /api to the backend on the same origin.
# ==============================================================================

# --- build --------------------------------------------------------------------
FROM node:24-alpine AS build

WORKDIR /app

# `npm ci` installs exactly the lockfile, which is what a reproducible build
# means. Copying the manifests alone keeps this layer cached across source edits.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG VITE_API_BASE_URL="/api"
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# --- serve --------------------------------------------------------------------
FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost/ || exit 1
