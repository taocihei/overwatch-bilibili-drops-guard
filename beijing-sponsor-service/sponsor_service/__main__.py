from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from .config import Config
from .http_server import SponsorHTTPServer
from .service import PoolWorker, SponsorService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Beijing sponsor payment service")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="run the HTTP service")
    prefill = subparsers.add_parser("prefill", help="synchronously fill preset pools")
    prefill.add_argument("--minimum", type=int, default=1, choices=range(1, 21))
    args = parser.parse_args(argv)

    config = Config.from_environment()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    service = SponsorService(config)

    if args.command == "prefill":
        created = service.fill_pool_once(target=args.minimum)
        logging.info("pool ready; created=%s counts=%s", created, service.pool_counts())
        return 0

    worker = PoolWorker(service)
    server = SponsorHTTPServer((config.listen_host, config.listen_port), service)
    stopping = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        if stopping.is_set():
            return
        stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker.start()
    logging.info("listening on %s:%s", config.listen_host, config.listen_port)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        worker.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
