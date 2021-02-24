import os
import sys
import uvicorn
import socket
import json
import argparse
from pathlib import Path
from typing import List
from ert_shared import __file__ as ert_shared_path
from ert_shared.storage import connection
from ert_shared.storage.command import add_parser_options
from uvicorn.supervisors import ChangeReload


class Server(uvicorn.Server):
    def __init__(self, config, connection_info, info_file):
        super().__init__(config)
        self.connection_info = connection_info
        self.info_file = info_file

    async def startup(self, sockets=None):
        """Overridden startup that also sends connection information"""
        await super().startup(sockets)
        if not self.started:
            return
        write_to_pipe(self.connection_info)
        write_to_file(self.connection_info, self.info_file)

    async def shutdown(self, sockets=None):
        """Overridden shutdown that deletes the lockfile"""
        await super().shutdown(sockets)
        self.info_file.unlink()


def generate_token():
    import secrets

    return secrets.token_urlsafe(nbytes=64)


def bind_socket(host: str, port: int) -> socket.socket:
    family = socket.AF_INET

    if host and ":" in host:
        # It's an IPv6 address.
        family = socket.AF_INET6

    sock = socket.socket(family=family)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.set_inheritable(True)
    return sock


def bind_open_socket(host: str) -> socket.socket:
    for port in range(51820, 51840):
        try:
            return bind_socket(host, port)
        except OSError:
            continue
    sys.exit("No ports available in range 51820-51840. Quitting.")


def write_to_file(connection_info: dict, lockfile):
    """Write connection information to 'storage_server.json'"""
    lockfile.write_text(connection_info)
    connection.set_global_info(os.getcwd())


def write_to_pipe(connection_info: dict):
    """Write connection information directly to the calling program (ERT) via a
    communication pipe."""
    fd = os.environ.get("ERT_COMM_FD")
    if fd is None:
        return
    with os.fdopen(int(fd), "w") as f:
        f.write(connection_info)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    add_parser_options(ap)
    return ap.parse_args()


def run_server(args=None, debug=False):
    if args is None:
        args = parse_args()

    if args.insecure:
        print("Starting ERT Storage server WITHOUT SECURITY")
        os.environ["ERT_STORAGE_INSECURE"] = "1"
    elif "ERT_STORAGE_INSECURE" in os.environ:
        # Unset just in case the user sets it deliberately
        del os.environ["ERT_STORAGE_INSECURE"]

    if "ERT_STORAGE_TOKEN" in os.environ:
        token = os.environ["ERT_STORAGE_TOKEN"]
    elif not args.debug:
        token = generate_token()
        os.environ["ERT_STORAGE_TOKEN"] = token
    else:
        token = None

    lockfile = Path.cwd() / "storage_server.json"
    if lockfile.exists():
        sys.exit("'storage_server.json' already exists")

    config_args = {}
    if debug:
        config_args.update(reload=True, reload_dirs=[os.path.dirname(ert_shared_path)])

    sock = bind_open_socket(args.host)
    connection_info = json.dumps(
        {
            "urls": [
                f"http://{host}:{sock.getsockname()[1]}"
                for host in (
                    sock.getsockname()[0],
                    socket.gethostname(),
                    socket.getfqdn(),
                )
            ],
            "token": token,
        }
    )

    # Appropriated from uvicorn.main:run
    config = uvicorn.Config("ert_shared.storage.app:app", **config_args)
    server = Server(config, connection_info, lockfile)

    print(connection_info)
    if config.should_reload:
        supervisor = ChangeReload(config, target=server.run, sockets=[sock])
        supervisor.run()
    else:
        server.run(sockets=[sock])
