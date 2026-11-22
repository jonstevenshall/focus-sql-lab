FROM nginx:1.27-alpine
# The MCP server tab's question box (ask/server.py): Python with the Anthropic SDK, started before nginx by the image's
# entrypoint, which runs the scripts in /docker-entrypoint.d.
RUN apk add --no-cache python3 && python3 -m venv /opt/ask && /opt/ask/bin/pip install --no-cache-dir anthropic==2.12.0
COPY ask/server.py /opt/ask/server.py
COPY ask/start.sh /docker-entrypoint.d/90-ask.sh
RUN chmod 755 /docker-entrypoint.d/90-ask.sh
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY site/ /usr/share/nginx/html/
EXPOSE 80
