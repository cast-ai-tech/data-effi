# Data Effi web. Multi-stage: the runtime image carries no source and no toolchain.

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Baked in at build time: Next inlines NEXT_PUBLIC_* into the client bundle, so
# it cannot come from the runtime environment.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

RUN addgroup -g 10001 -S dataeffi && adduser -u 10001 -S dataeffi -G dataeffi

COPY --from=builder /app/public ./public
COPY --from=builder --chown=dataeffi:dataeffi /app/.next/standalone ./
COPY --from=builder --chown=dataeffi:dataeffi /app/.next/static ./.next/static

USER dataeffi
EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:3000/login || exit 1

CMD ["node", "server.js"]
