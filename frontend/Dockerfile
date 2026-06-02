FROM node:20-alpine AS build

WORKDIR /app

# Only copy package.json — NOT package-lock.json. The lock was generated on the
# host OS/arch and locks platform-specific native deps (Rollup) for that
# platform only. Fresh npm install inside the container picks the right ones.
# See npm bug: https://github.com/npm/cli/issues/4828
COPY package.json ./
RUN npm install --legacy-peer-deps

COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://localhost/ || exit 1
