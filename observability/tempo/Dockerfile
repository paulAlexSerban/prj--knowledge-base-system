# grafana/tempo:2.8.2 is distroless and has no shell or dynamic linker.
# The musl busybox is a static binary, copied in only so Compose can hit /ready.
FROM busybox:1.36.1-musl AS tools
FROM grafana/tempo:2.8.2
COPY --from=tools /bin/busybox /bin/busybox
