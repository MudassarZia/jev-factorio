import json
import math
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .jev import ApiError, Decision, Jev
from .planner import ROCKET_GOAL, RocketPlanner
from .rcon import BridgeError, Rcon


@dataclass
class Settings:
    port: int = 27015
    player: int = 1
    model: str = "jev-latest"
    goal: str = ROCKET_GOAL
    max_decisions: int = 0
    max_minutes: float = 0
    min_confidence: float = 0
    interval: float = 0.75
    autonomous: bool = True

    def validate(self):
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise BridgeError("RCON port must be between 1 and 65535.")
        if type(self.player) is not int or self.player < 1:
            raise BridgeError("Player index must be a positive integer.")
        if type(self.max_decisions) is not int or not 0 <= self.max_decisions <= 10000000:
            raise BridgeError("Decision limit must be between 0 (unlimited) and 10000000.")
        if type(self.autonomous) is not bool:
            raise BridgeError("Autonomous mode must be true or false.")
        for value, low, high, label in [(self.max_minutes, 0, 525600, "Run minutes"),
                                        (self.min_confidence, 0, 1, "Confidence"),
                                        (self.interval, .25, 60, "Interval")]:
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise BridgeError(f"{label} must be between {low} and {high}.")
        if not self.goal.strip() or len(self.goal) > 2000:
            raise BridgeError("Enter a goal between 1 and 2000 characters.")
        if not self.model.strip() or len(self.model) > 100:
            raise BridgeError("Enter a valid model name.")


class Journal:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6] + ".jsonl")
        self.memory = directory / "mission-memory.json"

    def write(self, record):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=True, allow_nan=False) + "\n")

    def recall(self, world, goal):
        try:
            data = json.loads(self.memory.read_text(encoding="utf-8"))
            if world and data.get("world") == world and data.get("goal") == goal:
                return data.get("recent_actions", [])[-16:]
        except (OSError, ValueError, TypeError):
            pass
        return []

    def checkpoint(self, state, goal, history, calls, tokens):
        data = {"world": state.get("world"), "goal": goal, "recent_actions": list(history),
                "request_count_this_run": calls, "input_tokens_this_run": tokens,
                "last_inventory": state.get("inventory"), "last_position": state.get("player", {}).get("position"),
                "rockets_launched": state.get("rockets_launched", 0)}
        temporary = self.memory.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(self.memory)


def run(settings, api_key, password, execute, stop, emit, journal=None,
        bridge_factory=Rcon, model_factory=Jev):
    """Only this explicit entry point can send game actions or paid API requests."""
    settings.validate()
    if not password:
        raise BridgeError("Enter the RCON password you chose when starting the host.")
    model = model_factory(api_key, settings.model)
    history = deque(maxlen=16)
    session = uuid.uuid4().hex
    deadline = time.monotonic() + settings.max_minutes * 60 if settings.max_minutes else math.inf
    calls, tokens, uncertain, failures = 0, 0, 0, 0
    pending = False
    reported_task = None
    next_task_update = 0
    rocket_mode = settings.autonomous and "rocket" in settings.goal.lower()
    progress = deque(maxlen=20)
    with bridge_factory(settings.port, password) as bridge:
        started = False
        try:
            state = bridge.call("observe", player=settings.player)
            if state.get("protocol") != 1:
                raise BridgeError("Bridge protocol mismatch. Install the bundled mod.")
            if execute and state.get("task_protocol") != 1:
                raise BridgeError("Restart Factorio with the updated bundled mod to enable committed tasks.")
            if journal:
                history.extend(journal.recall(state.get("world"), settings.goal))
            planner = RocketPlanner(bridge.call("catalog", player=settings.player)) if rocket_mode else None
            if execute:
                # Set this before the request so a lost start reply still triggers cleanup.
                started = True
                bridge.call("start", player=settings.player, session=session)
            emit("Live actions enabled." if execute else "Preview: Jev decisions only; no game actions.")
            # A request cap prevents another paid decision, but lets its chosen task finish.
            while (pending or not settings.max_decisions or calls < settings.max_decisions) and time.monotonic() < deadline and not stop.is_set():
                priority = {}
                if planner:
                    plan = planner.context(state)
                    priority = {"recipe_priorities": [r["name"] for r in plan["recipes_for_milestone"]],
                                "research_priority": plan["next_research"]}
                state = bridge.call("observe", player=settings.player, **priority)
                if execute and not state.get("armed"):
                    raise BridgeError("Control was disabled in Factorio. Run stopped.")
                task = state.get("task") if execute else None
                if task and task["status"] in {"completed", "blocked"} and task["id"] != reported_task:
                    reported_task = task["id"]
                    outcome = {"event": "task_finished", "task_id": task["id"], "tick": state.get("tick"),
                               "status": task["status"], "description": task["description"], "result": task["result"]}
                    for entry in reversed(history):
                        if entry.get("task_id") == task["id"]:
                            entry.update(result=task["result"], task_status=task["status"],
                                         action_succeeded=task["status"] == "completed")
                            break
                    emit(f"Task {task['status']}: {task['result']}")
                    if journal:
                        journal.write(outcome)
                        journal.checkpoint(state, settings.goal, history, calls, tokens)
                if state.get("busy_ticks", 0) > 0:
                    stop.wait(.25)
                    continue
                pending = bool(task and task["status"] == "running")
                if pending:
                    if time.monotonic() >= next_task_update:
                        emit(f"Continuing task: {task['description']} | {task.get('progress', 'working')}")
                        next_task_update = time.monotonic() + 10
                    if task.get("resume"):
                        if not any(a["id"] == "continue_task" for a in state["actions"]):
                            raise BridgeError("The mod did not offer a continuation for its active task.")
                        if stop.is_set() or time.monotonic() >= deadline:
                            break
                        try:
                            bridge.call("act", player=settings.player, session=session,
                                        observation=state["observation"], action="continue_task")
                        except BridgeError as exc:
                            if "Observation expired" not in str(exc):
                                raise
                    stop.wait(.25)
                    continue
                if settings.max_decisions and calls >= settings.max_decisions:
                    break
                if rocket_mode and state.get("rockets_launched", 0) > 0:
                    emit("Goal verified by Factorio: a rocket has launched. Run complete.")
                    break
                if planner:
                    state["rocket_plan"] = planner.context(state)
                    # A model's Stop guess cannot terminate an unfinished rocket attempt.
                    state["actions"] = [a for a in state["actions"] if a["id"] != "stop"]
                if settings.autonomous:
                    state["autonomy"] = "Continue toward the goal. Low confidence is allowed. Use another approach when blocked; selecting the same failed action does not help."
                    marker = json.dumps({"inventory": state.get("inventory"), "position": state.get("player", {}).get("position"),
                        "research": state.get("research"), "progress": round(state.get("research_progress", 0), 3),
                        "machines": state.get("known_machines")}, sort_keys=True)
                    progress.append(marker)
                    if len(progress) == progress.maxlen and len(set(progress)) == 1:
                        state["stuck_hint"] = "No inventory, movement, research or building progress for 20 decisions. Change approach: recover a misplaced structure, move around an obstruction, find needed resources, or configure/fuel a machine. Repeating the same action is not making progress."
                    failed = {r["description"] for r in list(history)[-6:] if r.get("action_succeeded") is False}
                    alternatives = [a for a in state["actions"] if a["description"] not in failed]
                    if len(alternatives) >= 2:
                        state["actions"] = alternatives
                calls += 1
                try:
                    decision = model.decide(state, settings.goal, list(history))
                    failures = 0
                except ApiError as exc:
                    if not settings.autonomous or not exc.retryable:
                        raise
                    failures += 1
                    delay = min(60, 2 ** min(failures, 6))
                    emit(f"{exc} Retrying with fresh game state after {delay} seconds.")
                    if journal:
                        journal.write({"event": "api_retry", "step": calls, "delay_seconds": delay, "error": str(exc)})
                    until = min(time.monotonic() + delay, deadline)
                    while time.monotonic() < until and not stop.is_set():
                        stop.wait(min(5, until - time.monotonic()))
                        if execute and not stop.is_set():
                            heartbeat = bridge.call("observe", player=settings.player)
                            if not heartbeat.get("armed"):
                                raise BridgeError("Control was disabled in Factorio. Run stopped.")
                    continue
                tokens += decision.input_tokens
                # Cancellation/deadline are checked again AFTER the network call.
                if stop.is_set() or time.monotonic() >= deadline:
                    break
                action = next(a for a in state["actions"] if a["id"] == decision.choice)
                emit(f"{calls}/{settings.max_decisions or 'continuous'} | {action['description']} | confidence {decision.confidence:.2f}")
                record = {"step": calls, "tick": state.get("tick"), "description": action["description"],
                          "choice": decision.choice, "confidence": decision.confidence,
                          "probability": decision.probability, "model": decision.model,
                          "input_tokens": decision.input_tokens, "mode": "live" if execute else "preview"}
                if planner:
                    record["milestone"] = state["rocket_plan"]["milestone"]
                if decision.choice == "stop":
                    record["result"] = "Jev selected Stop"
                    if journal:
                        journal.write(record)
                    emit("Jev selected Stop.")
                    break
                if decision.confidence < settings.min_confidence:
                    uncertain += 1
                    record["result"] = "skipped: confidence below threshold"
                    emit("No action sent: confidence below threshold.")
                elif execute:
                    uncertain = 0
                    try:
                        result = bridge.call("act", player=settings.player, session=session,
                                             observation=state["observation"], action=decision.choice)
                    except BridgeError as exc:
                        if settings.autonomous and "Observation expired" in str(exc):
                            emit("Decision arrived after the observation expired; obtaining a fresh decision.")
                            continue
                        raise
                    record["result"] = result.get("result", "accepted")
                    record["action_succeeded"] = result.get("action_succeeded", True)
                    chosen_task = result.get("task")
                    pending = bool(chosen_task or result.get("busy_ticks", 0))
                    if chosen_task:
                        record.update(task_id=chosen_task["id"], task_status=chosen_task["status"])
                        next_task_update = 0
                else:
                    record["result"] = "preview only"
                history.append(record)
                if journal:
                    journal.write(record)
                    journal.checkpoint(state, settings.goal, history, calls, tokens)
                if uncertain >= 3 and not settings.autonomous:
                    emit("Stopped after three uncertain decisions. Adjust the goal or threshold before restarting.")
                    break
                stop.wait(settings.interval)
        finally:
            if started:
                try:
                    bridge.call("stop", player=settings.player, session=session)
                    emit("Control released and disabled in Factorio.")
                except Exception:
                    emit("Could not deliver Stop. Timed movement/mining ends within 3 game seconds; use /jev-disable in Factorio.")
            emit(f"Finished: {calls} API request(s), {tokens} reported input tokens.")


def offline_demo(emit=print, stop=None):
    """A deterministic harness demonstration. Does not contact Jev or Factorio."""
    stop = stop or threading.Event()
    inventory = {"iron-ore": 0, "iron-plate": 0, "coal": 5}
    emit("OFFLINE DEMO — simulated state and decisions. No API, sockets, or game process.")
    for i in range(4):
        if stop.is_set():
            break
        if i < 2:
            inventory["iron-ore"] += 5
            action = "Mine nearby iron ore (simulated)"
        elif i == 2:
            inventory["iron-ore"] -= 10
            inventory["coal"] -= 1
            action = "Load and fuel stone furnace (simulated)"
        else:
            inventory["iron-plate"] += 10
            action = "Take furnace output (simulated)"
        emit(f"{i + 1}/4 | {action} | inventory: {inventory}")
    emit("Offline demo complete. This demo uses simulated decisions.")
