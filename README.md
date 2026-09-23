# Jev × Factorio

An experimental Windows controller by [MudassarZia](https://github.com/MudassarZia) that lets [TypeSafe's Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) choose actions in Factorio, with the goal of launching a rocket.

## Outcome

**This project turned out to be a flop.** In testing, Jev kept getting stuck going back and forth between two processes—repeating mining and crafting instead of building a productive factory or advancing research. My conclusion is that Jev just isn't intelligent enough for this kind of autonomous gameplay yet. No autonomous rocket launch was achieved.

This repository preserves the earlier **Jev-controlled version**, before the scripted factory planner was added. Jev chooses every new gameplay action from the bridge's available commands. The controller provides game state, recipe/research guidance, pathfinding and task completion; it does not execute a predefined factory-building sequence.

## Run

Requires Windows, Python 3.10+ with Tkinter, Factorio, and a TypeSafe API key with Jev access. Tested with Factorio **2.0.77**; the 2.1 mod package is unverified in-game.

1. Run `python build.py` to generate the mod packages.
2. Open `Play-Test-World.cmd` and select `factorio.exe` if prompted. It creates a separate world with enemies disabled and opens the controller.
3. Enter your TypeSafe key. **Save API key** remembers it using Windows account encryption.
4. Select **Rocket mission**, set request/time limits, then click **Jev: OFF** in Factorio. Within 60 game seconds, tick **Execute actions** and click **Preview / Start** in the controller.

Limits of **0 mean unlimited** and can incur ongoing API charges. Stop with the in-game Jev button or the controller's **STOP** button. Save the game manually before closing it.

`Start.cmd` opens only the controller. **Offline Demo** uses simulated decisions without API calls. Keys, local settings, logs and game saves are excluded from this repository.

## Development

```powershell
python -m pip install lupa==2.8
python -m unittest discover -s tests -v
python build.py
```

Lupa is optional and only needed for Lua tests; the application has no third-party Python runtime dependencies. See [START-HERE.html](START-HERE.html) for the local guide and [VALIDATION.md](VALIDATION.md) for test results and limitations.
