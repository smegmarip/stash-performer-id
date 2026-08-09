# Build the SPA, then serve the static bundle with nginx.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# The browser derives the service URL from window.location at runtime. VITE_API_PORT sets the
# port it targets (keep in sync with the service's SERVICE_PORT); VITE_API_BASE overrides the
# whole base if the service lives at a fixed URL. See src/lib/api.ts.
ARG VITE_API_PORT="15000"
ARG VITE_API_BASE=""
ENV VITE_API_PORT=${VITE_API_PORT} VITE_API_BASE=${VITE_API_BASE}
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
