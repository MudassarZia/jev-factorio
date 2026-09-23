import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from jev_factorio.controller import Journal, Settings, run
from jev_factorio.jev import ApiError, Decision
from jev_factorio.planner import RocketPlanner
from jev_factorio.test_world import prepare, host_arguments
from test_controller import FakeBridge, FakeModel, STATE


class Clock:
    value = 0
    def monotonic(self): return self.value


class Stop:
    def __init__(self, clock): self.clock, self.stopped = clock, False
    def is_set(self): return self.stopped
    def set(self): self.stopped = True
    def wait(self, seconds): self.clock.value += max(0, seconds)


class AutonomyTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.stop = Stop(self.clock)
        self.bridge, self.model = FakeBridge(), FakeModel()
        self.messages = []

    def run_loop(self, **settings):
        with patch('jev_factorio.controller.time.monotonic', self.clock.monotonic):
            run(Settings(**settings), 'test-key', 'test-password', True, self.stop, self.messages.append,
                bridge_factory=lambda *a: self.bridge, model_factory=lambda *a: self.model)

    def test_continuous_run_reaches_manual_stop_without_request_cap(self):
        decide = self.model.decide
        def stop_after_five(*args):
            result = decide(*args)
            if self.model.calls == 5: self.stop.set()
            return result
        self.model.decide = stop_after_five
        self.run_loop(max_decisions=0, max_minutes=0)
        self.assertEqual(self.model.calls, 5)
        self.assertEqual(sum(op == 'act' for op, _ in self.bridge.operations), 4)
        self.assertEqual(self.bridge.operations[-1][0], 'stop')

    def test_autonomous_low_confidence_does_not_end_after_three(self):
        self.model.decide = lambda *a: Decision('a3', .1, .4, 'test', 1)
        self.run_loop(max_decisions=5, min_confidence=.5)
        self.assertEqual(sum(op == 'observe' for op, _ in self.bridge.operations), 6)
        self.assertFalse(any(op == 'act' for op, _ in self.bridge.operations))

    def test_temporary_api_failure_retries_with_new_observation_and_counts_budget(self):
        calls = []
        def decide(*args):
            calls.append(1)
            if len(calls) == 1: raise ApiError('temporary', retryable=True)
            return Decision('a3', .8, .8, 'test', 1)
        self.model.decide = decide
        self.run_loop(max_decisions=2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sum(op == 'act' for op, _ in self.bridge.operations), 1)
        self.assertGreaterEqual(sum(op == 'observe' for op, _ in self.bridge.operations), 4)

    def test_failed_attempts_respect_request_cap(self):
        def fail(*args): raise ApiError('temporary', retryable=True)
        self.model.decide = fail
        self.run_loop(max_decisions=1)
        self.assertTrue(any('Finished: 1 API request' in m for m in self.messages))
        self.assertFalse(any(op == 'act' for op, _ in self.bridge.operations))

    def test_authentication_failure_is_not_retried(self):
        def fail(*args): raise ApiError('key rejected', retryable=False)
        self.model.decide = fail
        with self.assertRaises(ApiError): self.run_loop(max_decisions=10)
        self.assertEqual(self.bridge.operations[-1][0], 'stop')

    def test_rocket_success_is_verified_without_another_api_request(self):
        original = self.bridge.call
        def launched(operation, **kwargs):
            result = original(operation, **kwargs)
            if operation == 'observe': result['rockets_launched'] = 1
            return result
        self.bridge.call = launched
        self.run_loop()
        self.assertEqual(self.model.calls, 0)
        self.assertTrue(any('Goal verified' in m for m in self.messages))

    def test_incomplete_rocket_mission_does_not_offer_model_stop(self):
        original = self.model.decide
        def inspect(state, *args):
            self.assertNotIn('stop', [a['id'] for a in state['actions']])
            self.assertIn('rocket_plan', state)
            return original(state, *args)
        self.model.decide = inspect
        self.run_loop(max_decisions=1)


class PlannerAndLauncherTests(unittest.TestCase):
    def test_trigger_research_precedes_locked_power_construction(self):
        planner = RocketPlanner({'technologies': [
            {'name': 'rocket-silo', 'prerequisites': ['steam-power']},
            {'name': 'steam-power', 'prerequisites': [], 'trigger': {'type': 'craft-item', 'item': {'name': 'iron-plate'}, 'count': 50}}],
            'recipes': [{'name': 'iron-plate', 'ingredients': [{'name': 'iron-ore'}]}]})
        result = planner.context({'known_machines': [{'name': 'stone-furnace'}]})
        self.assertIn('Unlock steam-power', result['milestone'])
        self.assertEqual(result['recipes_for_milestone'][0]['name'], 'iron-plate')

    def test_memory_is_scoped_to_world_and_goal(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory)
            journal.checkpoint({'world': 'world-one'}, 'rocket', [{'description': 'Mine coal'}], 1, 50)
            self.assertEqual(len(journal.recall('world-one', 'rocket')), 1)
            self.assertEqual(journal.recall('world-two', 'rocket'), [])
            self.assertEqual(journal.recall('world-one', 'other goal'), [])

    def test_research_prerequisites_and_science_follow_game_catalog(self):
        planner = RocketPlanner({'technologies': [
            {'name': 'rocket-silo', 'prerequisites': ['automation'], 'ingredients': [{'name': 'science'}], 'count': 100},
            {'name': 'automation', 'prerequisites': [], 'ingredients': [{'name': 'red-science'}], 'count': 10}],
            'recipes': [{'name': 'science', 'ingredients': [{'name': 'iron-plate'}]},
                        {'name': 'iron-plate', 'ingredients': [{'name': 'iron-ore'}]}]})
        state = {'researched': ['automation'], 'known_machines': [{'name': n} for n in
            ['stone-furnace', 'boiler', 'offshore-pump', 'steam-engine', 'lab']]}
        context = planner.context(state)
        self.assertEqual(context['next_research'], 'rocket-silo')
        self.assertEqual([r['name'] for r in context['recipes_for_milestone']], ['science', 'iron-plate'])

    def test_launcher_creates_saves_directory_and_keeps_password_out_of_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / 'installed/bin/x64/factorio.exe'
            executable.parent.mkdir(parents=True)
            executable.touch()
            info = root / 'installed/data/base/info.json'
            info.parent.mkdir(parents=True)
            info.write_text(json.dumps({'version': '2.0.77'}))
            runtime = root / 'isolated'
            with patch('subprocess.Popen', side_effect=AssertionError('prepare must not launch anything')):
                connection = prepare(executable, runtime)
            self.assertTrue((runtime / 'data/saves').is_dir())
            self.assertFalse((root / 'installed/config').exists())
            arguments = host_arguments(connection, runtime)
            self.assertNotIn(connection['password'], ' '.join(arguments))
            self.assertEqual(arguments[arguments.index('--bind') + 1], '127.0.0.1')
            self.assertEqual(prepare(executable, runtime)['password'], connection['password'])


if __name__ == '__main__':
    unittest.main()
