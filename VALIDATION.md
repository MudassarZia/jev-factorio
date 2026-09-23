# Validation — September 19, 2026

## Verified with the real game

Installed game: **Factorio 2.0.77**, using its executable under `Desktop/games/Factorio`. Testing used separate configuration, mods, player data and saves in the workspace. Normal Factorio saves were not used.

- The mod loaded, the graphical local host exposed authenticated RCON on `127.0.0.1:27015`, and the controller read real player state and actions.
- A bounded scripted bridge test moved the character 8.90625 tiles, confirmed movement stopped at its deadline, mined one coal, placed a furnace while consuming the inventory item, transferred fuel, and disarmed control afterward. These were ordinary game actions, not spawned resources or teleportation.
- Mining initially failed because a two-second input window ended before completion. A three-second window and avoiding redundant selection updates fixed that test.
- The in-game ON/OFF button was exercised successfully.
- A graphical host needed `local-rcon-socket` and `local-rcon-password` in its configuration; the earlier command-line-only setup did not enable RCON.
- Saving initially failed because the isolated test setup had no `data/saves` directory. The launcher now creates it. A subsequent real `/server-save jev-verified` completed successfully.
- The expanded mod returned **217 recipes and 196 technologies**, including the actual rocket recipe and trigger-based research.

## Verified with Jev

The user entered their TypeSafe key in the local controller. An authenticated live run used **jev-1.13.0**, made **7 requests**, and reported **13,047 input tokens**.

Jev issued two accepted mining actions. A subsequent game observation showed **three coal in the player's inventory**, with control off and no action in progress. Five decisions were skipped under the original 0.5 threshold, and the original controller stopped after three consecutive low-confidence choices.

The continuous preset uses threshold 0, does not end on uncertainty, and supplies rocket milestones. User-run logs later reproduced an oscillation between iron at (44.5, 48.5) and a furnace at (107, -14): each three-second walk was followed by a new decision before arrival.

The committed-task revision was checked in the live world after saving a verified backup:

- Three scripted choices completed travel to the furnace, travel to the iron patch, and gathering **10 additional iron ore**. They used **12 continuation segments**, with no model decision between a task starting and its completion. This check used no paid requests, spawning, teleportation or free items.
- A scripted handcrafting check consumed normal ingredients and verified an iron gear wheel finished before the next decision.
- **Two authenticated Jev requests** then chose travel to the furnace followed by inserting the 10 iron ore. The navigation used three continuations before arrival; the second request was made only afterward. Jev reported **7,477 input tokens** across those two requests. Control was released and disabled afterward.
- This revision used **2 of the authorized 1,000 additional development requests**. User-initiated gameplay requests are separate from these bounded development checks.

These checks demonstrate task commitment and normal inventory changes, not reliable completion of the full rocket mission.

## Automated checks

**67 tests passed with no skips**, using Python 3.12.14 and Lupa 2.8's Lua 5.2 runtime:

- Jev HTTP construction and response validation; unknown choices, malformed distributions and duplicate actions rejected.
- RCON authentication, fragmented/multipart packets, UTF-8, response barriers, truncated packets and operation allowlisting.
- Preview never controls the player; request and time caps; cancellation; cleanup; low-confidence behavior.
- Continuous running until manual Stop; transient API retries with fresh observations; failed attempts count toward request caps; permanent authentication failures stop.
- Rocket completion requires the game's launched-rocket count. Stop is not offered for an unfinished autonomous rocket mission.
- Research prerequisites, trigger research, world/goal-scoped mission memory, isolated launcher configuration, saves-directory creation and credentials excluded from process arguments.
- Lua session ownership, observation expiry and one-time use; in-game arming; timed movement/mining; path following; cancellation of late path responses; disarming on disconnect/join/lease expiry; inventory conservation; placement and failed-placement recovery.
- Large recipe menus remain capped while preserving other action categories and prioritizing milestone recipes.
- Actual Windows DPAPI key encryption/decryption, replacement, invalid-input protection, corrupted-file rejection and forgetting, using disposable dummy keys without API calls.
- Fixed navigation targets and retained paths across segments; unrelated actions rejected while committed; repeated path failures and stationary movement release blocked tasks; temporary pathfinder congestion retries.
- Mining completion requires 10 actual additional items; depletion and full inventory report blockage; crafting waits for its queue and detects missing output after cancellation.
- Only one model request per committed task; a request cap permits its remaining segments; completion precedes the next decision; Stop/time limits still interrupt; preview never continues tasks; old mods fail before live control starts.

A hidden-window GUI smoke test also passed: opening stays idle, the Rocket mission preset selects continuous limits, Offline Demo completes, and no game/API connection is made. GUI checks required access to the installed Tk runtime outside the restricted test sandbox.

A second hidden-window GUI test saved a dummy key, destroyed the controller, reopened it and verified the key was restored, then used Forget key and verified removal. No real user key or paid request was used for this check.

## Additional Factorio engine fixture

A **separate disposable benchmark copy** loaded the final mod code and created test structures/items solely to exercise advanced APIs. It verified:

- Observation of 10 machine types and 18 fluid connections, with 220 offered actions.
- Rocket-silo input inventory access.
- Assembler recipe configuration, real fuel transfer and item-consuming placement.
- Rejection of a launch from an unready silo.
- Acceptance of the mod's pathfinder request parameters.
- Recipe and technology catalog serialization.

The fixture deliberately supplied structures/items and unlocked technologies in its disposable world. It was not Jev gameplay, did not alter the live test world, and is **not evidence of autonomous production, research or rocket-launch competence**. Fixture code is kept in development work files, outside the delivered mod.

## Not demonstrated

- An autonomous rocket launch, or reliable autonomous construction of a complete factory.
- A sustained multi-hour run, productive oil processing, fluid-network design, high-throughput science production or successful launch from a fully supplied silo.
- Factorio 2.1 engine execution, overhaul mods, other planets or Space Age missions. The 2.1 variant has reference checks only.
- Exact API monetary cost. Reported input tokens and request counts are logged; provider billing remains authoritative.

## Reproduce

From the application folder:

```text
python -m unittest discover -s tests -v
python -m jev_factorio demo
python build.py
```

The 27 Lua tests require optional development dependency Lupa; without it they skip. The other 40 tests use the standard library, with the four DPAPI tests requiring Windows. The running application has no third-party Python dependency. Packaging excludes saved API keys, API logs, user settings, local test credentials, saves and runtime caches.
