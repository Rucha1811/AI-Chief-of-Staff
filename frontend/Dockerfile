FROM node:20-alpine AS builder

WORKDIR /app

# NEXT_PUBLIC_* values are INLINED into the browser bundle by `next build`;
# a runtime `environment:` entry cannot change an already-built bundle.
# docker-compose.yml / docker-compose.prod.yml therefore pass this as a build
# argument (production uses ${FRONTEND_API_URL:?...}, so a production image
# cannot be built without it). Left empty for a bare local `docker build`,
# which then falls back to the development default in src/lib/config.ts.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

COPY package.json .
RUN npm install
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
# Production runs as the unprivileged `node` user (uid 1000) that this image
# already ships; nothing in the standalone server needs root. The writable
# paths it does need are declared as tmpfs in docker-compose.prod.yml
# (/tmp and .next/cache), so the root filesystem can stay read-only - which
# is why .next/cache is created here even though the standalone output does
# not necessarily contain it: it has to exist in the image for a tmpfs to be
# mounted over it.
RUN mkdir -p /app/.next/cache && chown -R node:node /app
USER node
EXPOSE 3000
CMD ["node", "server.js"]
