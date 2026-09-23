import argparse
import os
import threading
from pathlib import Path

from .controller import Journal, Settings, offline_demo, run


def main():
    parser = argparse.ArgumentParser(description="Jev Factorio controller. Never launches Factorio.")
    parser.add_argument("mode", nargs="?", choices=["gui", "demo", "preview", "run"], default="gui")
    parser.add_argument("--execute", action="store_true", help="Required with run to enable game actions")
    parser.add_argument("--port", type=int, default=27015)
    parser.add_argument("--player", type=int, default=1)
    parser.add_argument("--steps", type=int, default=0, help="Request cap; 0 runs continuously")
    parser.add_argument("--minutes", type=float, default=0, help="Minute cap; 0 runs continuously")
    parser.add_argument("--confidence", type=float, default=0)
    parser.add_argument("--bounded", action="store_true", help="Disable autonomous recovery and rocket milestones")
    parser.add_argument("--goal", default=Settings().goal)
    parser.add_argument("--model", default="jev-latest")
    args = parser.parse_args()
    if args.mode == "gui":
        from .gui import main as gui
        gui()
    elif args.mode == "demo":
        offline_demo()
    else:
        if args.mode == "run" and not args.execute:
            parser.error("run requires --execute; use preview to select without acting")
        if args.mode == "preview" and args.execute:
            parser.error("preview cannot use --execute")
        settings = Settings(port=args.port, player=args.player, model=args.model, goal=args.goal,
                            max_decisions=args.steps, max_minutes=args.minutes, min_confidence=args.confidence,
                            autonomous=not args.bounded)
        try:
            run(settings, os.environ.get("TYPESAFE_API_KEY", ""), os.environ.get("FACTORIO_RCON_PASSWORD", ""),
                args.execute, threading.Event(), print, Journal(Path(__file__).resolve().parent.parent / "logs"))
        except KeyboardInterrupt:
            print("Stopped.")
        except Exception as exc:
            parser.exit(1, f"Stopped: {exc}\n")


if __name__ == "__main__":
    main()
