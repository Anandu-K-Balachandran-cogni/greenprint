# ConstructOS web app: SvelteKit (adapter-node) with the same-origin engine gateway.
FROM node:22-slim AS build
WORKDIR /srv
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY . .
RUN npx svelte-kit sync && npm run build && npm prune --omit=dev

FROM node:22-slim
WORKDIR /srv
ENV NODE_ENV=production PORT=8080 BODY_SIZE_LIMIT=2M
COPY --from=build /srv/build ./build
COPY --from=build /srv/node_modules ./node_modules
COPY --from=build /srv/package.json ./
USER node
CMD ["node", "build/index.js"]
