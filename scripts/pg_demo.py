"""Disposable local PostgreSQL server for the demo (never touches an existing server or database).

    python scripts/pg_demo.py init      # create a private cluster in .pgdemo/ and start it on 127.0.0.1:55432
    python scripts/pg_demo.py dsn       # print the connection string to put in REPORTING_PG_DSN
    python scripts/pg_demo.py status
    python scripts/pg_demo.py stop | start
    python scripts/pg_demo.py destroy   # stop it and delete .pgdemo/ (only if it carries the demo marker)

Safety:

* the cluster lives in ``.pgdemo/`` inside this project (ignored by git) and is created with
  ``initdb``: it is a separate server process with its own data folder, not a database inside an
  existing server;
* ``init`` refuses if ``.pgdemo/`` already exists or if the port is already in use (another server);
* ``destroy`` only deletes a folder that contains the marker file written by ``init``;
* the server listens on 127.0.0.1 only and uses ``trust`` authentication **for this local demo only**
  (no password is stored anywhere). Do not reuse these settings for a real server.

The PostgreSQL binaries (initdb, pg_ctl, psql) are found through ``PG_BIN``, the PATH, or the default
Windows installation folder.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLUSTER = ROOT / ".pgdemo"
DATA = CLUSTER / "data"
MARKER = CLUSTER / "PIPELINE_DEMO_CLUSTER"
MARKER_TEXT = "python-reporting-pipeline disposable demo cluster\n"
PORT = int(os.environ.get("PG_DEMO_PORT", "55432"))
USER = "reporting"
DATABASE = "reporting_demo"


def binary(name: str) -> str:
    candidates = []
    if os.environ.get("PG_BIN"):
        candidates.append(str(Path(os.environ["PG_BIN"]) / name))
    found = shutil.which(name)
    if found:
        candidates.append(found)
    candidates += sorted(glob.glob(rf"C:\Program Files\PostgreSQL\*\bin\{name}.exe"), reverse=True)
    for candidate in candidates:
        if Path(candidate).exists() or Path(candidate + ".exe").exists():
            return candidate
    raise SystemExit(f"{name} not found: install PostgreSQL or set PG_BIN to its bin folder")


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def dsn() -> str:
    return f"host=127.0.0.1 port={PORT} dbname={DATABASE} user={USER}"


def run(*args: str) -> None:
    completed = subprocess.run(args, capture_output=True, text=True)
    if completed.returncode != 0:
        raise SystemExit(f"{Path(args[0]).name} failed:\n{completed.stdout}{completed.stderr}")


def is_running() -> bool:
    if not DATA.exists():
        return False
    return subprocess.run([binary("pg_ctl"), "-D", str(DATA), "status"], capture_output=True).returncode == 0


def start() -> None:
    if is_running():
        print(f"already running on 127.0.0.1:{PORT}")
        return
    if port_in_use(PORT):
        raise SystemExit(f"port {PORT} is already used by another program; set PG_DEMO_PORT to a free port")
    # no captured pipes: on Windows the server process would inherit them and the call would never return
    completed = subprocess.run(
        [
            binary("pg_ctl"),
            "-D",
            str(DATA),
            "-l",
            str(CLUSTER / "server.log"),
            "-w",
            "-t",
            "60",
            "-o",
            f"-p {PORT} -c listen_addresses=127.0.0.1",
            "start",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise SystemExit(f"pg_ctl start failed; see {CLUSTER.name}/server.log")
    print(f"started on 127.0.0.1:{PORT}")


def stop() -> None:
    if is_running():
        run(binary("pg_ctl"), "-D", str(DATA), "-w", "-m", "fast", "stop")
        print("stopped")
    else:
        print("not running")


def init() -> None:
    if CLUSTER.exists():
        raise SystemExit(f"{CLUSTER.name}/ already exists; use 'start', or 'destroy' first")
    if port_in_use(PORT):
        raise SystemExit(f"port {PORT} is already used by another program; set PG_DEMO_PORT to a free port")
    CLUSTER.mkdir()
    MARKER.write_text(MARKER_TEXT, encoding="utf-8")
    run(binary("initdb"), "-D", str(DATA), "-U", USER, "-A", "trust", "-E", "UTF8", "--no-locale")
    start()
    run(
        binary("psql"),
        "-h",
        "127.0.0.1",
        "-p",
        str(PORT),
        "-U",
        USER,
        "-d",
        "postgres",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        f"CREATE DATABASE {DATABASE}",
    )
    print(f"database {DATABASE} created")
    print(f"connection string (no password): {dsn()}")


def destroy() -> None:
    if not CLUSTER.exists():
        print("nothing to destroy")
        return
    if not MARKER.is_file() or MARKER.read_text(encoding="utf-8") != MARKER_TEXT:
        raise SystemExit(f"{CLUSTER} does not carry the demo marker; refusing to delete it")
    stop()
    shutil.rmtree(CLUSTER)
    print(f"deleted {CLUSTER.name}/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["init", "start", "stop", "status", "dsn", "destroy"])
    command = parser.parse_args().command
    if command == "init":
        init()
    elif command == "start":
        start()
    elif command == "stop":
        stop()
    elif command == "status":
        print(f"running on 127.0.0.1:{PORT}" if is_running() else "not running")
    elif command == "dsn":
        print(dsn())
    else:
        destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
