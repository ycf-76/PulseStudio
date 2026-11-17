"""
Custom uvicorn launcher for the project.

Note: On Windows, Python venv's python.exe is a launcher that delegates to
the base Python (e.g. C:\\Python312\\python.exe). This is NORMAL - the process
ExecutablePath will show the base Python, but sys.prefix and sys.path still
point to the venv, so all venv packages and project code are correctly loaded.
"""

import os
import sys

print(f"[start_server] PID={os.getpid()}", flush=True)
print(f"[start_server] sys.executable = {sys.executable}", flush=True)
print(f"[start_server] sys.prefix = {sys.prefix}", flush=True)

from uvicorn.config import Config
from uvicorn.server import Server

config = Config("app.main:app", host="0.0.0.0", port=8000, workers=1, reload=False)
config.load_app()

server = Server(config=config)
print(f"[start_server] Starting server on 0.0.0.0:8000 ...", flush=True)
server.run()
