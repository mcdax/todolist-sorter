# Reverse proxy examples

The app listens on plain HTTP `:8000`. Put a reverse proxy that terminates
TLS in front of it. Two gotchas that apply to every setup:

- **Forward `X-Forwarded-Proto`.** Without it the app generates HTTP
  links (e.g. the OAuth redirect URI shown on `/setup`) against the
  inner scheme. The bundled Dockerfile already runs uvicorn with
  `--proxy-headers --forwarded-allow-ips='*'`; keep that when you write
  your own compose.
- **Large / slow requests**: the webhook body is tiny, but LLM calls
  can take 30 s+. Use a read timeout of at least 60 s upstream.

Pick one of the snippets below.

---

## nginx (plain)

```nginx
server {
    listen 443 ssl http2;
    server_name sorter.example.com;

    ssl_certificate     /etc/letsencrypt/live/sorter.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sorter.example.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;

        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   X-Real-IP         $remote_addr;

        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}
```

## Caddy

```caddyfile
sorter.example.com {
    reverse_proxy 127.0.0.1:8000 {
        transport http {
            read_timeout 120s
        }
    }
}
```

Caddy auto-manages certificates; forwards `X-Forwarded-*` by default.

## SWAG (linuxserver.io)

Drop this in `nginx/proxy-confs/sorter.subdomain.conf`. Adjust
`$upstream_app` to the container name on your shared `proxynet`:

```nginx
## Version 2024/07/16
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;

    server_name sorter.*;
    include /config/nginx/ssl.conf;

    client_max_body_size 0;

    location / {
        include /config/nginx/proxy.conf;
        include /config/nginx/resolver.conf;
        set $upstream_app todolist-sorter;
        set $upstream_port 8000;
        set $upstream_proto http;
        proxy_pass $upstream_proto://$upstream_app:$upstream_port;
    }
}
```

## Traefik (labels on the service)

```yaml
services:
  todolist-sorter:
    image: ghcr.io/mcdax/todolist-sorter:latest
    env_file: [.env]
    volumes: [./data:/app/data]
    networks: [proxy]
    labels:
      traefik.enable: "true"
      traefik.http.routers.sorter.rule: "Host(`sorter.example.com`)"
      traefik.http.routers.sorter.entrypoints: "websecure"
      traefik.http.routers.sorter.tls.certresolver: "letsencrypt"
      traefik.http.services.sorter.loadbalancer.server.port: "8000"
```

---

## Checking it

After the proxy is live, from anywhere with public DNS:

```bash
curl -sS https://sorter.example.com/healthz
# {"status":"ok"}
```

And check that `/setup` shows the right redirect URI — it must start with
`https://` and match what's configured in the Todoist app console.
