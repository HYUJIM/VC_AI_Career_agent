# syntax=docker/dockerfile:1
# FE 빌드 + nginx 운영 이미지 (deploy/docker-compose.yml의 nginx 서비스).
# 빌드 컨텍스트는 FE/, 설정 파일은 compose의 additional_contexts(nginx-conf = deploy/nginx)에서 가져온다.
#   docker compose -f deploy/docker-compose.yml build nginx

ARG NODE_IMAGE=node:22-bookworm-slim
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:stable-alpine

FROM ${NODE_IMAGE} AS fe-build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# API는 같은 출처의 /api (nginx가 backend로 넘김). 다른 값이 번들에 들어가지 않게 비워 둔다.
ENV VITE_API_BASE_URL=
RUN npm run build

# 비루트(uid 101) nginx. 컨테이너 안에서 8080(HTTP)·8443(HTTPS)으로 듣고 compose가 80·443으로 연다.
FROM ${NGINX_IMAGE}
COPY --from=nginx-conf http.conf /etc/nginx/conf.d/00-vc-http.conf
COPY --from=nginx-conf vc/ /etc/nginx/vc/
COPY --from=nginx-conf --chmod=755 40-vc-site.sh /docker-entrypoint.d/40-vc-site.sh
COPY --from=fe-build /app/dist/ /usr/share/nginx/html/
