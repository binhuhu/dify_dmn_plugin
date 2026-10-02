# Official Node 24.19.0 multi-arch manifest, verified 2026-09-30.
FROM node:24.19.0-bookworm-slim@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY src ./src
COPY LICENSE NOTICE COMMERCIAL-LICENSE.md THIRD_PARTY_NOTICES.md ./
COPY third_party ./third_party
# Build-only tools have their own dependency trees; do not ship them in the
# runtime filesystem. A fresh final stage avoids retaining their lower layers.
RUN rm -rf /usr/local/lib/node_modules /opt/yarn-v* /root/.npm \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/yarn /usr/local/bin/yarnpkg \
             /usr/local/bin/corepack /usr/local/bin/pnpm /usr/local/bin/pnpx

FROM scratch
COPY --from=build / /
WORKDIR /app
LABEL org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.source="https://github.com/binhuhu/dify_dmn_plugin"
USER 10001:10001
ENV NODE_ENV=production TZ=UTC DMN_HOST=0.0.0.0 PORT=8787
EXPOSE 8787
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["node", "src/server.js"]
