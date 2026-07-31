# Static SPA: build with Node, serve with nginx.
#
# There is no Node runtime in the final image because there is no server to
# run — the console is a static bundle that talks to the FastAPI service
# over HTTP. Dropping the Node runtime removes an entire class of
# server-side vulnerability from the deployment and cuts the image from
# hundreds of megabytes to ~50.

FROM node:20-alpine AS build
WORKDIR /app

# Lockfile first so a dependency-unchanged rebuild reuses this layer.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# `npm run build` runs `tsc --noEmit` first, so a type error fails the
# image build rather than shipping.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.27-alpine AS runtime

# Client-side routing: every unknown path must fall through to index.html,
# or a deep link like /cases/<id> 404s on refresh. Also sets the security
# headers a static host is responsible for.
RUN printf '%s\n' \
  'server {' \
  '  listen 3000;' \
  '  server_name _;' \
  '  root /usr/share/nginx/html;' \
  '  index index.html;' \
  '  add_header X-Content-Type-Options "nosniff" always;' \
  '  add_header X-Frame-Options "DENY" always;' \
  '  add_header Referrer-Policy "strict-origin-when-cross-origin" always;' \
  '  location /assets/ {' \
  '    expires 1y;' \
  '    add_header Cache-Control "public, immutable";' \
  '  }' \
  '  location / {' \
  '    try_files $uri $uri/ /index.html;' \
  '  }' \
  '}' > /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

# nginx:alpine ships a non-root `nginx` user; run as it rather than root.
RUN chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/run \
 && sed -i 's,^user  nginx;,,' /etc/nginx/nginx.conf \
 && sed -i 's,/var/run/nginx.pid,/tmp/nginx.pid,' /etc/nginx/nginx.conf
USER nginx

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://localhost:3000/ >/dev/null || exit 1
CMD ["nginx", "-g", "daemon off;"]
