# Use AFTER cert exists: /etc/letsencrypt/live/mcp.seleric.com/
#   sudo cp /opt/seleric/Seleric_Agent_Core/deploy/nginx/mcp.seleric.com /etc/nginx/sites-available/mcp.seleric.com
#   sudo nginx -t && sudo systemctl reload nginx

server {
    listen 80;
    listen [::]:80;
    server_name mcp.seleric.com;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        default_type "text/plain";
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name mcp.seleric.com;

    ssl_certificate     /etc/letsencrypt/live/mcp.seleric.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcp.seleric.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # MCP OAuth discovery + authorize/token/register (ChatGPT custom MCP).
    # Must sit above the catch-all that 301s unknown paths to /mcp.
    location /.well-known/oauth-protected-resource {
        proxy_pass         http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
    location /.well-known/oauth-authorization-server {
        proxy_pass         http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
    location /oauth/ {
        proxy_pass         http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_buffering    off;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        client_max_body_size 1m;
    }

    # ClickHouse serve MCP (no extra DNS) — https://mcp.seleric.com/serve/sse
    location /serve/ {
        proxy_pass         http://127.0.0.1:3012/;
        proxy_http_version 1.1;
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
        proxy_set_header   Connection '';
        proxy_set_header   X-Accel-Buffering no;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }

    # Seleric Agent Core MCP (streamable-http) — https://mcp.seleric.com/mcp
    location /mcp {
        proxy_pass         http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
        proxy_set_header   Connection '';
        proxy_set_header   X-Accel-Buffering no;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        client_max_body_size 16m;
    }

    # Dashboard BI analyst SSE — idle Azure/LLM waits must not 504.
    # https://mcp.seleric.com/agent/ask/stream
    location ^~ /agent/ask/stream {
        proxy_pass         http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_buffering    off;
        proxy_cache        off;
        gzip               off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_set_header   Connection '';
        proxy_set_header   X-Accel-Buffering no;
        proxy_set_header   Host $host;
        proxy_set_header   Authorization $http_authorization;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        client_max_body_size 16m;
    }

    # https://mcp.seleric.com/agent/health | /agent/ask
    location /agent/ {
        proxy_pass         http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_buffering    off;
        proxy_cache        off;
        gzip               off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_set_header   Connection '';
        proxy_set_header   X-Accel-Buffering no;
        proxy_set_header   Host $host;
        proxy_set_header   Authorization $http_authorization;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        client_max_body_size 16m;
    }

    location / {
        return 301 https://$host/mcp;
    }
}
