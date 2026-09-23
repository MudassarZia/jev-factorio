import json
import os
import ctypes
import queue
import threading
from dataclasses import asdict
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from .controller import Journal, Settings, offline_demo, run
from .credentials import KeyStore, KeyStoreError
from .planner import ROCKET_GOAL
from .rcon import Rcon

ROOT = Path(__file__).resolve().parent.parent


class App:
    def __init__(self, root, credential_path=None):
        if os.name == "nt":
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JevFactorio.LocalController")
        self.root = root
        self.events = queue.Queue()
        self.worker = None
        self.stop = threading.Event()
        self.closing = False
        self.buttons = []
        self.key_store = KeyStore(credential_path or ROOT / "private" / "api-key.dpapi")
        saved_key = None
        try:
            saved_key = self.key_store.load()
        except KeyStoreError as exc:
            self.emit(str(exc))
        root.title("Jev × Factorio | Rocket controller")
        root.geometry("960x930")
        root.minsize(750, 700)
        root.configure(bg="#102127")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#102127")
        style.configure("TLabel", background="#102127", foreground="#edf2ed", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 25, "bold"))
        style.configure("Sub.TLabel", foreground="#9fbbb5")
        style.configure("TButton", font=("Segoe UI", 10), padding=7)
        style.configure("TCheckbutton", background="#102127", foreground="#edf2ed", padding=5)
        panel = ttk.Frame(root, padding=24)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Jev × Factorio", style="Title.TLabel").pack(anchor="w")
        ttk.Label(panel, text="Local game bridge  /  starts idle  /  never launches Factorio", style="Sub.TLabel").pack(anchor="w", pady=(4, 18))
        form = ttk.Frame(panel)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        defaults = asdict(Settings())
        try:
            saved = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
            defaults.update({k: v for k, v in saved.items() if k in defaults})
        except (OSError, ValueError, AttributeError):
            pass
        self.fields = {}
        rows = [("api_key", "TypeSafe API key", saved_key or os.environ.get("TYPESAFE_API_KEY", "")),
                ("password", "Local RCON password", os.environ.get("FACTORIO_RCON_PASSWORD", "")),
                ("port", "Local RCON port", defaults["port"]),
                ("player", "Player index", defaults["player"]),
                ("model", "Jev model", defaults["model"]),
                ("max_decisions", "Request limit (0 = continuous)", defaults["max_decisions"]),
                ("max_minutes", "Minute limit (0 = continuous)", defaults["max_minutes"]),
                ("min_confidence", "Minimum confidence (0–1)", defaults["min_confidence"])]
        for row, (name, label, value) in enumerate(rows):
            grid_row = row + (1 if row else 0)
            ttk.Label(form, text=label).grid(row=grid_row, column=0, sticky="w", pady=4, padx=(0, 15))
            variable = tk.StringVar(value=str(value))
            self.fields[name] = variable
            entry = ttk.Entry(form, textvariable=variable, show="•" if name in {"api_key", "password"} else "")
            entry.grid(row=grid_row, column=1, sticky="ew", pady=4)
        key_buttons = ttk.Frame(form)
        key_buttons.grid(row=1, column=1, sticky="w", pady=(0, 5))
        for label, command in [("Save API key", self.save_api_key), ("Forget key", self.forget_api_key)]:
            button = ttk.Button(key_buttons, text=label, command=command)
            button.pack(side="left", padx=(0, 7))
            self.buttons.append(button)
        self.key_status = tk.StringVar(value="Saved key loaded" if saved_key else "Optional: encrypted for your Windows account")
        ttk.Label(key_buttons, textvariable=self.key_status, style="Sub.TLabel").pack(side="left")
        ttk.Label(panel, text="Goal").pack(anchor="w", pady=(14, 5))
        self.goal = tk.Text(panel, height=2, wrap="word", font=("Segoe UI", 10), bg="#e8eeea", relief="flat", padx=8, pady=8)
        self.goal.insert("1.0", defaults["goal"])
        self.goal.pack(fill="x")
        self.live = tk.BooleanVar(value=False)
        self.autonomous = tk.BooleanVar(value=defaults["autonomous"])
        ttk.Checkbutton(panel, text="Autonomous mode: retry temporary API errors and continue past uncertainty", variable=self.autonomous).pack(anchor="w", pady=(8, 0))
        ttk.Checkbutton(panel, text="Execute actions in Factorio (also enable Jev with the in-game button)", variable=self.live).pack(anchor="w", pady=(12, 2))
        ttk.Label(panel, text="Preview / Start uses your TypeSafe API account. Limits of 0 allow ongoing API charges.\nSave API key remembers it securely on this Windows account. Stop in the app or with the game's Jev button.", style="Sub.TLabel").pack(anchor="w", pady=(2, 10))
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x")
        for label, command in [("Offline Demo", self.demo), ("Inspect connection", self.inspect),
                               ("Preview / Start", self.start), ("Rocket mission", self.rocket), ("Save settings", self.save)]:
            button = ttk.Button(buttons, text=label, command=command)
            button.pack(side="left", padx=(0, 7))
            self.buttons.append(button)
        ttk.Button(buttons, text="STOP", command=self.request_stop).pack(side="right")
        self.status = tk.StringVar(value="Idle — Factorio is not connected")
        ttk.Label(panel, textvariable=self.status, style="Sub.TLabel").pack(anchor="w", pady=(15, 6))
        self.log = scrolledtext.ScrolledText(panel, height=9, bg="#091519", fg="#bfddd0", insertbackground="white", font=("Consolas", 10), relief="flat", wrap="word")
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")
        self.emit("Ready. Read START-HERE.html for the one-time Factorio host setup.")
        if saved_key:
            self.emit("Saved API key loaded. Opening the app does not start a run.")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    def emit(self, text):
        self.events.put(("log", str(text)))

    def save_api_key(self):
        try:
            key = self.fields["api_key"].get().strip()
            self.key_store.save(key)
            self.fields["api_key"].set(key)
            self.key_status.set("Saved for this Windows account")
            self.emit("API key saved with Windows encryption. It will load automatically next time.")
        except KeyStoreError as exc:
            messagebox.showerror("API key", str(exc), parent=self.root)

    def forget_api_key(self):
        try:
            self.key_store.forget()
            self.fields["api_key"].set("")
            self.key_status.set("No saved key")
            self.emit("Saved API key removed and the key field cleared.")
        except KeyStoreError as exc:
            messagebox.showerror("API key", str(exc), parent=self.root)

    def settings(self):
        settings = Settings(port=int(self.fields["port"].get()), player=int(self.fields["player"].get()),
                            model=self.fields["model"].get().strip(), goal=self.goal.get("1.0", "end").strip(),
                            max_decisions=int(self.fields["max_decisions"].get()), max_minutes=float(self.fields["max_minutes"].get()),
                            min_confidence=float(self.fields["min_confidence"].get()), autonomous=self.autonomous.get())
        settings.validate()
        return settings

    def launch(self, function, status):
        if self.worker and self.worker.is_alive():
            return
        self.stop.clear()
        self.status.set(status)
        for button in self.buttons:
            button.configure(state="disabled")
        def job():
            try:
                function()
            except Exception as exc:
                # Only local validation / sanitized API errors are displayed.
                self.emit(f"Stopped: {exc}")
            finally:
                self.events.put(("done", None))
        self.worker = threading.Thread(target=job, daemon=False)
        self.worker.start()

    def demo(self):
        self.launch(lambda: offline_demo(self.emit, self.stop), "Offline demo")

    def rocket(self):
        self.goal.delete("1.0", "end")
        self.goal.insert("1.0", ROCKET_GOAL)
        self.fields["max_decisions"].set("0")
        self.fields["max_minutes"].set("0")
        self.fields["min_confidence"].set("0")
        self.autonomous.set(True)
        self.emit("Rocket mission selected. Continuous run; starts only when you enable live actions and click Preview / Start.")

    def inspect(self):
        try:
            settings = self.settings()
            password = self.fields["password"].get()
            if not password:
                raise ValueError("Enter your RCON password.")
        except Exception as exc:
            messagebox.showerror("Settings", str(exc))
            return
        def check():
            with Rcon(settings.port, password) as bridge:
                self.emit(json.dumps(bridge.call("players"), indent=2))
                state = bridge.call("observe", player=settings.player)
                self.emit(f"Mod connected. Tick {state['tick']}; {len(state['actions'])} actions; armed={state['armed']}.")
        self.launch(check, "Inspecting local host — no API calls or actions")

    def start(self):
        try:
            settings = self.settings()
            if not self.fields["api_key"].get().strip():
                raise ValueError("Enter your TypeSafe API key in the top field. Click Save API key to remember it for next time.")
        except Exception as exc:
            messagebox.showerror("Settings", str(exc))
            return
        key, password, execute = self.fields["api_key"].get().strip(), self.fields["password"].get(), self.live.get()
        def work():
            journal = Journal(ROOT / "logs")
            self.emit(f"Decision log: {journal.path.name}")
            try:
                run(settings, key, password, execute, self.stop, self.emit, journal)
            except Exception as exc:
                journal.write({"event": "run_error", "error": str(exc)})
                raise
            finally:
                journal.write({"event": "run_finished"})
        self.launch(work, "Running — live control" if execute else "Running — preview only")

    def save(self):
        try:
            settings = self.settings()
            (ROOT / "settings.json").write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
            self.emit("Settings saved. Use the separate Save API key button to remember your key; the RCON password is not included in settings.")
        except Exception as exc:
            messagebox.showerror("Settings", str(exc))

    def request_stop(self):
        self.stop.set()
        self.status.set("Stopping — any in-flight API call must return before cleanup")

    def close(self):
        if self.worker and self.worker.is_alive():
            self.closing = True
            self.request_stop()
        else:
            self.root.destroy()

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", value + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                else:
                    self.live.set(False)
                    self.status.set("Idle — run finished")
                    for button in self.buttons:
                        button.configure(state="normal")
                    if self.closing:
                        self.root.destroy()
                        return
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
