import argparse
import json
import sys
import time

from .config import Settings


def main(argv=None):
    parser = argparse.ArgumentParser(description="INBOX-only job labels and application tracking")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("auth", help="Authorize Gmail in your browser")
    sync = commands.add_parser("sync", help="Run an incremental INBOX sync")
    sync.add_argument(
        "--dry-run", action="store_true", help="Preview counts without labels or state"
    )
    watch = commands.add_parser("watch", help="Sync repeatedly until interrupted")
    watch.add_argument(
        "--interval", type=int, default=300, help="Seconds between syncs (minimum 30)"
    )
    for command in (sync, watch):
        command.add_argument(
            "--max-messages", type=int, help="Limit work per run; safely resume remaining messages"
        )
        command.add_argument(
            "--label-only",
            action="store_true",
            help="Organize job mail under JEV only; do not save application summaries",
        )
    serve = commands.add_parser("serve", help="Run the authenticated read-only API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.command == "watch" and args.interval < 30:
        parser.error("--interval must be at least 30 seconds")
    if getattr(args, "max_messages", None) is not None and args.max_messages < 1:
        parser.error("--max-messages must be positive")
    settings = Settings.from_env()
    try:
        if args.command == "serve":
            import uvicorn

            from .api import create_app

            app = create_app(settings.db_path, settings.api_token)
            uvicorn.run(app, host=args.host, port=args.port, access_log=False)
        else:
            from .gmail import connect
            from .store import Store
            from .sync import synchronize

            gmail = connect(settings, interactive=args.command == "auth")
            if args.command == "auth":
                print("Gmail authorization saved locally.")
                return 0
            db_path = settings.data_dir / "labels.sqlite3" if args.label_only else settings.db_path
            store = Store(db_path)
            try:
                while True:
                    try:
                        result = synchronize(
                            gmail,
                            store,
                            dry_run=getattr(args, "dry_run", False),
                            label_only=args.label_only,
                            max_messages=args.max_messages,
                        )
                        print(json.dumps(result.to_dict()), flush=True)
                    except Exception as error:
                        if args.command != "watch":
                            raise
                        # Third-party exceptions can contain mail, request URLs or credentials.
                        print(
                            f"Sync failed ({type(error).__name__}); retrying next interval.",
                            file=sys.stderr,
                            flush=True,
                        )
                    if args.command != "watch":
                        break
                    time.sleep(args.interval)
            finally:
                store.close()
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(
            f"Operation failed ({type(error).__name__}). Check authorization, configuration, "
            "database access and connectivity; see README troubleshooting.",
            file=sys.stderr,
        )
        return 1
    return 0
