"""Small real application using PostgreSQL for durable data and Redis for a cache."""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import psycopg
import redis

cache = redis.Redis(host=os.environ["REDIS_HOST"], socket_timeout=3)

def database():
    return psycopg.connect(host=os.environ["DB_HOST"], dbname=os.environ["DB_NAME"],
                          user=os.environ["DB_USER"],
                          password=Path(os.environ["DB_PASSWORD_FILE"]).read_text().strip(),
                          connect_timeout=5)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            with database() as db:
                with db.cursor() as cur:
                    cur.execute("CREATE TABLE IF NOT EXISTS visits (id int PRIMARY KEY, count bigint NOT NULL)")
                    if self.path == "/health":
                        cur.execute("SELECT 1")
                        cache.ping()
                        data = {"status": "ok"}
                    else:
                        cur.execute("INSERT INTO visits VALUES (1, 1) ON CONFLICT (id) DO UPDATE SET count = visits.count + 1 RETURNING count")
                        count = cur.fetchone()[0]
                        cache.setex("last_visit", 60, count)
                        data = {"application": "nixlab demo", "visits": count, "cache": "connected"}
            body = json.dumps(data).encode()
            self.send_response(200)
        except Exception:
            body = b'{"status":"dependency unavailable"}'
            self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
