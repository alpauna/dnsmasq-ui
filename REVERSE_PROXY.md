# Reverse Proxy Configuration

dnsmasq-ui supports running behind reverse proxies (nginx, Traefik, HAProxy, etc.) with proper X-Forwarded header handling.

## Features

- ✅ X-Forwarded-For header support for accurate client IP tracking
- ✅ X-Forwarded-Proto (http/https) support
- ✅ X-Forwarded-Host header support for hostname detection
- ✅ X-Forwarded-Port support for port detection
- ✅ Request logging with client IP and user agent
- ✅ Path prefix support (PROXY_PATH_PREFIX)

## Environment Variables

```bash
# Optional: Path prefix for reverse proxy routing
# Use if dnsmasq-ui is served at a subpath (e.g., http://proxy/dnsmasq-ui/)
export PROXY_PATH_PREFIX=/dnsmasq-ui

# Optional: Trusted proxy IPs (comma-separated, or '*' for all)
# Default is '*' (trust all reverse proxy headers)
export TRUSTED_PROXIES=192.168.0.1,10.0.0.1
```

## Nginx Configuration

### Basic Reverse Proxy

```nginx
server {
    listen 80;
    server_name dashboard.alshowto.com;

    location / {
        proxy_pass http://192.168.0.233:5000;

        # Forward headers for IP tracking
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $server_name;
        proxy_set_header X-Forwarded-Port $server_port;
        proxy_set_header Host $host;
    }
}
```

### With Path Prefix

```nginx
server {
    listen 80;
    server_name dashboard.alshowto.com;

    location /dnsmasq-ui/ {
        proxy_pass http://192.168.0.233:5000/;

        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $server_name;
        proxy_set_header X-Forwarded-Port $server_port;
        proxy_set_header Host $host;
    }
}
```

## Traefik Configuration

### Docker Compose

```yaml
services:
  dnsmasq-ui:
    image: dnsmasq-ui:latest
    ports:
      - "5000:5000"
    environment:
      - PROXY_PATH_PREFIX=/dnsmasq-ui
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.dnsmasq-ui.rule=PathPrefix(`/dnsmasq-ui`)"
      - "traefik.http.routers.dnsmasq-ui.entrypoints=web"
      - "traefik.http.services.dnsmasq-ui.loadbalancer.server.port=5000"
      - "traefik.http.middlewares.dnsmasq-ui-strip.stripprefix.prefixes=/dnsmasq-ui"
      - "traefik.http.routers.dnsmasq-ui.middlewares=dnsmasq-ui-strip"
```

### Static Configuration

```yaml
http:
  routers:
    dnsmasq-ui:
      rule: "PathPrefix(`/dnsmasq-ui`)"
      service: "dnsmasq-ui"
      middlewares:
        - "dnsmasq-ui-strip"

  services:
    dnsmasq-ui:
      loadBalancer:
        servers:
          - url: "http://192.168.0.233:5000"

  middlewares:
    dnsmasq-ui-strip:
      stripPrefix:
        prefixes:
          - "/dnsmasq-ui"
```

## HAProxy Configuration

```haproxy
frontend public
    bind *:80

    acl dnsmasq-ui path_beg /dnsmasq-ui
    use_backend dnsmasq-ui if dnsmasq-ui

backend dnsmasq-ui
    balance roundrobin
    option httpclose
    option forwardfor

    http-request set-header X-Forwarded-Proto http
    http-request set-header X-Forwarded-Port %[dst_port]

    server dns03 192.168.0.233:5000 check
```

## Client IP Tracking

Once behind a reverse proxy, client IPs are automatically extracted from X-Forwarded-For headers.

### View Logs with Client IP

```bash
# On dns03:
sudo journalctl -u dnsmasq-ui.service -f | grep -i "client"
```

Log format:
```
[192.168.1.100] GET /api/zones | User-Agent: Mozilla/5.0...
[192.168.1.100] POST /api/zones | User-Agent: Mozilla/5.0...
```

## HTTPS/TLS

### Self-Signed Certificate (Development)

```nginx
server {
    listen 443 ssl http2;
    server_name dashboard.alshowto.com;

    ssl_certificate /etc/ssl/certs/self-signed.crt;
    ssl_certificate_key /etc/ssl/private/self-signed.key;

    location / {
        proxy_pass http://192.168.0.233:5000;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $server_name;
        proxy_set_header X-Forwarded-Port $server_port;
        proxy_set_header Host $host;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name dashboard.alshowto.com;
    return 301 https://$server_name$request_uri;
}
```

### Let's Encrypt (Certbot)

```bash
# Install certbot for nginx
sudo apt-get install certbot python3-certbot-nginx

# Obtain certificate
sudo certbot certonly --nginx -d dashboard.alshowto.com

# Auto-configure nginx
sudo certbot install --nginx -d dashboard.alshowto.com
```

## Load Balancing Multiple Instances

To run dnsmasq-ui on multiple ports/servers:

### Nginx

```nginx
upstream dnsmasq_ui {
    server 192.168.0.231:5000;
    server 192.168.0.232:5000;
    server 192.168.0.233:5000;
}

server {
    listen 80;
    server_name dashboard.alshowto.com;

    location / {
        proxy_pass http://dnsmasq_ui;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $server_name;
        proxy_set_header X-Forwarded-Port $server_port;
        proxy_set_header Host $host;
    }
}
```

## Troubleshooting

### Client IP Shows as Reverse Proxy IP

**Problem**: Client IP appears as reverse proxy IP (e.g., 192.168.0.1) instead of actual client

**Solution**: Ensure reverse proxy is sending X-Forwarded-For headers:

```bash
# Check headers received by dnsmasq-ui
curl -v -H "X-Forwarded-For: 203.0.113.100" http://192.168.0.233:5000/api/zones
```

Check logs:
```bash
sudo journalctl -u dnsmasq-ui.service -f
```

### Cookies/Sessions Not Working

**Problem**: Session cookies lost or not set properly

**Solution**: Ensure Host header is correctly forwarded:

```nginx
proxy_set_header Host $host;
```

### Redirects Go to Wrong URL

**Problem**: Links redirect to http://192.168.0.233:5000 instead of reverse proxy URL

**Solution**: Ensure all proxy headers are set:

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host $server_name;
proxy_set_header X-Forwarded-Port $server_port;
```

## Performance Tips

1. **Enable gzip compression** in reverse proxy:
   ```nginx
   gzip on;
   gzip_types text/plain text/css application/json;
   ```

2. **Use upstream keepalive** in nginx:
   ```nginx
   upstream dnsmasq_ui {
       server 192.168.0.233:5000;
       keepalive 32;
   }
   ```

3. **Set appropriate timeouts**:
   ```nginx
   proxy_connect_timeout 5s;
   proxy_send_timeout 10s;
   proxy_read_timeout 10s;
   ```

4. **Cache static assets** (if using CDN/edge):
   ```nginx
   location ~* \.(css|js|png|jpg)$ {
       expires 1y;
       add_header Cache-Control "public, immutable";
   }
   ```

## References

- [Werkzeug ProxyFix Documentation](https://werkzeug.palletsprojects.com/en/2.3.x/middleware/proxy_fix/)
- [Nginx proxy_set_header](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header)
- [Traefik ForwardedHeaders Middleware](https://doc.traefik.io/traefik/middlewares/http/forwardedheaders/)
