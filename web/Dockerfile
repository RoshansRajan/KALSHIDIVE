FROM node:22-alpine
WORKDIR /app

# Manifests first so `npm ci` is cached independently of source changes.
COPY web/package.json web/package-lock.json* ./
RUN npm install

COPY web/ ./
EXPOSE 5173
CMD ["npm", "run", "dev"]
