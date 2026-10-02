# Official Node 24.19.0 multi-arch manifest, verified 2026-09-30.
FROM node:24.19.0-bookworm-slim@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY src ./src
COPY LICENSE NOTICE COMMERCIAL-LICENSE.md THIRD_PARTY_NOTICES.md ./
COPY third_party ./third_party
LABEL org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.source="https://github.com/binhuhu/dify_dmn_plugin"
USER 10001:10001
ENV NODE_ENV=production TZ=UTC DMN_HOST=0.0.0.0 PORT=8787
EXPOSE 8787
CMD ["node", "src/server.js"]
