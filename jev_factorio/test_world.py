"""Explicit launcher for a separate local Factorio world and an idle controller."""
import json
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox

from .gui import App, ROOT

RUNTIME = ROOT / "local-test"


def prepare(executable, directory=RUNTIME):
    """Write an isolated configuration. Does not start Factorio or call Jev."""
    executable, directory = Path(executable).resolve(), Path(directory).resolve()
    data_source = executable.parents[2] / "data"
    version = json.loads((data_source / "base/info.json").read_text(encoding="utf-8"))["version"]
    series = ".".join(version.split(".")[:2])
    package = ROOT / "packages" / ("factorio-" + series) / "jev-factorio_0.1.0.zip"
    if not package.is_file():
        raise ValueError(f"Factorio {version} is not supported. This package supports 2.0 and 2.1.")
    directory.mkdir(parents=True, exist_ok=True)
    connection_file = directory / "connection.json"
    if connection_file.is_file():
        connection = json.loads(connection_file.read_text(encoding="utf-8"))
    else:
        connection = {"port": 27015, "password": secrets.token_urlsafe(32)}
    if type(connection.get("port")) is not int or not 1024 <= connection["port"] <= 65535:
        raise ValueError("Invalid local test RCON port.")
    password = connection.get("password")
    if not isinstance(password, str) or not password or any(c in password for c in "\r\n\0"):
        raise ValueError("Invalid local test RCON password.")
    connection["executable"] = str(executable)
    connection_file.write_text(json.dumps(connection, indent=2), encoding="utf-8")
    mods, data = directory / "mods", directory / "data"
    mods.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)
    (data / "saves").mkdir(exist_ok=True)
    shutil.copyfile(package, mods / package.name)
    (mods / "mod-list.json").write_text(json.dumps({"mods": [
        {"name": name, "enabled": name in {"base", "jev-factorio"}}
        for name in ("base", "quality", "elevated-rails", "space-age", "jev-factorio")
    ]}), encoding="utf-8")
    (directory / "config.ini").write_text(
        f"[path]\nread-data={data_source.as_posix()}\nwrite-data={data.as_posix()}\n"
        "\n[general]\nlocale=en\n\n[graphics]\nfull-screen=false\n"
        "\n[other]\ncheck-updates=false\nautosave-interval=5\n"
        f"local-rcon-socket=127.0.0.1:{connection['port']}\nlocal-rcon-password={password}\n",
        encoding="utf-8")
    (directory / "map-gen.json").write_text(json.dumps({
        "seed": 19319, "peaceful_mode": True, "starting_area": 2,
        "autoplace_controls": {"enemy-base": {"frequency": 0, "size": 0}}
    }), encoding="utf-8")
    (directory / "server-settings.json").write_text(json.dumps({
        "name": "Jev local test", "description": "Isolated Jev test world",
        "visibility": {"public": False, "lan": False}, "max_players": 1,
        "require_user_verification": False, "allow_commands": "admins-only",
        "autosave_interval": 5, "auto_pause": True
    }), encoding="utf-8")
    return connection


def game_arguments(connection, directory=RUNTIME):
    directory = Path(directory).resolve()
    return [connection["executable"], "--config", str(directory / "config.ini"),
            "--mod-directory", str(directory / "mods")]


def host_arguments(connection, directory=RUNTIME):
    directory = Path(directory).resolve()
    # Load the newest isolated save, including manual saves and autosaves.
    saves = [directory / "jev-test.zip"]
    saves.extend((directory / "data/saves").glob("*.zip"))
    existing = [path for path in saves if path.is_file()]
    save = max(existing, key=lambda path: path.stat().st_mtime) if existing else saves[0]
    return game_arguments(connection, directory) + [
        "--host", str(save), "--server-settings", str(directory / "server-settings.json"),
        "--bind", "127.0.0.1", "--port", "34198",
        "--window-size", "1280x800", "--fullscreen=false"]


def main():
    root = tk.Tk()
    root.withdraw()
    try:
        saved = RUNTIME / "connection.json"
        connection = json.loads(saved.read_text(encoding="utf-8")) if saved.is_file() else {}
        executable = Path(connection.get("executable", str(Path.home() / "Desktop/games/Factorio/bin/x64/factorio.exe")))
        if not executable.is_file():
            chosen = filedialog.askopenfilename(parent=root, title="Choose your installed factorio.exe",
                                               filetypes=[("Factorio executable", "factorio.exe")])
            if not chosen:
                root.destroy()
                return
            executable = Path(chosen)
        # Refuse to open a second host on the configured RCON port.
        with socket.socket() as check:
            try:
                check.bind(("127.0.0.1", connection.get("port", 27015)))
            except OSError:
                raise ValueError("Close the existing Factorio test host before using this launcher again.") from None
        connection = prepare(executable)
        save = RUNTIME / "jev-test.zip"
        if not save.is_file():
            print("Creating the separate peaceful test world. This can take a minute.", flush=True)
            subprocess.run(game_arguments(connection) + ["--create", str(save), "--map-gen-settings",
                           str(RUNTIME / "map-gen.json")], check=True, timeout=180,
                           stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            if not save.is_file():
                raise RuntimeError("Factorio did not create the save. See local-test/data/factorio-current.log.")
        subprocess.Popen(host_arguments(connection))
        app = App(root)
        app.fields["port"].set(str(connection["port"]))
        app.fields["password"].set(connection["password"])
        app.rocket()
        app.emit("Separate test world is opening; local connection is prefilled. Enter your key if blank; Save API key remembers it.")
        app.emit("Once the world loads, enable the in-game Jev button before starting live actions.")
        root.deiconify()
        root.mainloop()
    except Exception as exc:
        messagebox.showerror("Jev test world", str(exc), parent=root)
        root.destroy()


if __name__ == "__main__":
    main()
