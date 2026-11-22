#!/bin/sh
# Starts the question box's service (ask/server.py) in the background. The nginx image runs this before nginx starts.
/opt/ask/bin/python /opt/ask/server.py &
