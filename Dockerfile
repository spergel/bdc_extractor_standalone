FROM node:22-slim

WORKDIR /srv

COPY app/package.json app/package-lock.json ./app/
RUN cd app && npm ci --omit=dev

COPY app ./app

WORKDIR /srv/app
ENV PORT=8787
EXPOSE 8787

CMD ["node", "src/index.ts"]
