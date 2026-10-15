import argparse
import json
import sys
import time

from .config import Settings


def main(argv=None):
    parser = argparse.ArgumentParser(description="INBOX-only job labels and application tracking")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("auth", help="Authorize Gmail in your browser")
    scope = commands.add_parser(
        "scope", help="Select the latest N INBOX messages, then future mail"
    )
    scope.add_argument("--latest", type=int, required=True)
    scope.add_argument(
        "--label-only", action="store_true", help="Configure the folder-only database"
    )
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
            "--require-scope",
            action="store_true",
            help="Refuse to run without a saved recent-mail scope",
        )
        command.add_argument(
            "--max-messages", type=int, help="Limit work per run; safely resume remaining messages"
        )
        command.add_argument(
            "--label-only",
            action="store_true",
            help="Organize job mail under grok only; do not save application summaries",
        )
    serve = commands.add_parser("serve", help="Run the authenticated read-only API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.command == "watch" and args.interval < 30:
        parser.error("--interval must be at least 30 seconds")
    if getattr(args, "max_messages", None) is not None and args.max_messages < 1:
        parser.error("--max-messages must be positive")
    if args.command == "scope" and args.latest < 1:
        parser.error("--latest must be positive")
    settings = Settings.from_env()
    from .classifier import JevClassifier, JevError

    classifier = None
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

            if args.command not in {"auth", "scope"}:
                # Fail before reading Gmail when TypeSafe access is not configured.
                classifier = JevClassifier(settings.typesafe_key())
            gmail = connect(settings, interactive=args.command == "auth")
            if args.command == "auth":
                print("Gmail authorization saved locally.")
                return 0
            db_path = settings.labels_db_path if args.label_only else settings.db_path
            store = Store(db_path)
            try:
                if args.command == "scope":
                    from .scope import configure_scope

                    selected = configure_scope(gmail, store, args.latest)
                    print(json.dumps({"initial_messages": selected, "upcoming_only": True}))
                    return 0
                while True:
                    try:
                        result = synchronize(
                            gmail,
                            store,
                            classifier,
                            dry_run=getattr(args, "dry_run", False),
                            label_only=args.label_only,
                            max_messages=args.max_messages,
                            require_scope=args.require_scope,
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
    except JevError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:
        print(
            f"Operation failed ({type(error).__name__}). Check authorization, configuration, "
            "database access and connectivity; see README troubleshooting.",
            file=sys.stderr,
        )
        return 1
    finally:
        if classifier is not None:
            classifier.close()
    return 0
