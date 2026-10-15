# The compiler runs on the build worker; the only produced binary is linux/arm64.
FROM --platform=$BUILDPLATFORM golang:1.24-alpine AS builder
ARG VERSION=dev
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build \
      -trimpath -ldflags="-s -w -X main.version=${VERSION}" \
      -o /out/opencode2api ./cmd/opencode2api

FROM --platform=linux/arm64 alpine:3.22
RUN test "$(apk --print-arch)" = aarch64 \
    && apk add --no-cache ca-certificates su-exec tzdata \
    && addgroup -S -g 10001 opencode2api \
    && adduser -S -D -u 10001 -G opencode2api -h /var/lib/opencode2api opencode2api \
    && mkdir -p /app /var/lib/opencode2api \
    && chown opencode2api:opencode2api /app /var/lib/opencode2api
COPY --from=builder --chmod=0755 /out/opencode2api /usr/local/bin/opencode2api
COPY --chmod=0755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint
COPY --chown=opencode2api:opencode2api config.example.json /app/config.example.json
COPY LICENSE /usr/share/licenses/opencode2api/LICENSE
LABEL org.opencontainers.image.licenses="GPL-3.0-or-later"
ENV CONFIG_PATH=/var/lib/opencode2api/config.json \
    CONFIG_SEED_PATH= \
    LISTEN_ADDRESS= \
    WEBUI_LISTEN_ADDRESS= \
    STATE_DIR=/var/lib/opencode2api \
    PORT=8080
WORKDIR /app
EXPOSE 8080
VOLUME ["/var/lib/opencode2api"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/usr/local/bin/opencode2api", "-healthcheck"]
ENTRYPOINT ["/usr/local/bin/docker-entrypoint"]
