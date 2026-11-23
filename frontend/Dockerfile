FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# The gateway: serves the app and proxies the API (config mounted by Compose).
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
