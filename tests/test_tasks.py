"""Regression: one paid decision owns every segment until the task ends."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jev_factorio.controller import Journal, Settings, run
from jev_factorio.jev import Decision
from jev_factorio.rcon import BridgeError
from test_autonomy import Clock, Stop
from test_controller import FakeBridge, STATE


class TaskBridge(FakeBridge):
    def __init__(self, kind='navigate', blocked=False):
        super().__init__()
        self.kind, self.blocked = kind, blocked
        self.task, self.segments, self.observations, self.done = None, 0, 0, 0

    def call(self, operation, **payload):
        result = super().call(operation, **payload)
        if operation == 'act':
            if payload['action'] in {'a3', 'a4'}:
                if self.task and self.task['status'] == 'running':
                    raise AssertionError('New decision interrupted a committed task')
                self.task = {'id': str(self.done + 1), 'kind': self.kind, 'description': 'Reach iron',
                             'status': 'running', 'resume': self.kind != 'craft', 'progress': '30 tiles remaining'}
                self.segments, self.observations = 1, 0
            elif payload['action'] == 'continue_task':
                self.segments += 1
            result['task'] = copy.deepcopy(self.task)
        if operation == 'observe' and self.task:
            self.observations += 1
            if self.task['status'] == 'running' and (self.segments == 3 or (self.kind == 'craft' and self.observations == 4)):
                self.task.update(status='blocked' if self.blocked else 'completed', result='Verified task outcome')
                self.done += 1
            result.update(task=copy.deepcopy(self.task), observation=str(self.observations))
            result['actions'].append({'id': 'continue_task', 'description': 'Continue chosen task'})
        if operation == 'observe':
            result['actions'].append({'id': 'a4', 'description': 'Try a different resource'})
        return result


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.stop = Stop(self.clock)
        self.bridge = TaskBridge()
        self.calls, self.messages = 0, []

    def decide(self, state, goal, history):
        self.assertNotEqual((state.get('task') or {}).get('status'), 'running')
        self.calls += 1
        choice = next(a['id'] for a in state['actions'] if a['id'] in {'a3', 'a4'})
        return Decision(choice, .8, .8, 'fake', 100)

    def run_loop(self, execute=True, journal=None, **kwargs):
        with patch('jev_factorio.controller.time.monotonic', self.clock.monotonic):
            run(Settings(goal='Gather iron', max_decisions=kwargs.pop('max_decisions', 1), **kwargs),
                'test-key', 'test-password', execute, self.stop, self.messages.append, journal,
                bridge_factory=lambda *a: self.bridge, model_factory=lambda *a: self)

    def test_request_cap_allows_all_segments_and_no_extra_paid_decisions(self):
        self.run_loop()
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.bridge.done, 1)
        actions = [p['action'] for op, p in self.bridge.operations if op == 'act']
        self.assertEqual(actions, ['a3', 'continue_task', 'continue_task'])
        snapshots = [p['observation'] for op, p in self.bridge.operations if op == 'act']
        self.assertEqual(len(set(snapshots)), 3)
        self.assertEqual(self.bridge.operations[-1][0], 'stop')

    def test_second_decision_occurs_only_after_completion(self):
        self.run_loop(max_decisions=2)
        self.assertEqual((self.calls, self.bridge.done), (2, 2))
        self.assertEqual(sum('Task completed:' in m for m in self.messages), 2)

    def test_crafting_polls_until_finished_without_sending_new_actions(self):
        self.bridge = TaskBridge(kind='craft')
        self.run_loop()
        self.assertEqual((self.calls, self.bridge.done), (1, 1))
        self.assertEqual(sum(op == 'act' for op, _ in self.bridge.operations), 1)

    def test_blocked_outcome_is_logged_once_and_next_decision_may_proceed(self):
        self.bridge = TaskBridge(blocked=True)
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory)
            self.run_loop(journal=journal, max_decisions=2)
            self.assertEqual(journal.path.read_text().count('"event": "task_finished"'), 2)
            self.assertIn('"task_status": "blocked"', journal.memory.read_text())
        self.assertEqual(self.calls, 2)

    def test_manual_stop_interrupts_task_without_more_segments(self):
        original = self.bridge.call
        def stopping(operation, **payload):
            result = original(operation, **payload)
            if operation == 'act':
                self.stop.set()
            return result
        self.bridge.call = stopping
        self.run_loop()
        self.assertEqual(self.bridge.segments, 1)
        self.assertEqual(self.bridge.done, 0)
        self.assertEqual(self.bridge.operations[-1][0], 'stop')

    def test_time_limit_interrupts_incomplete_task(self):
        self.run_loop(max_minutes=.01)
        self.assertEqual(self.bridge.done, 0)
        self.assertEqual(self.bridge.operations[-1][0], 'stop')

    def test_preview_does_not_continue_or_change_a_task(self):
        self.run_loop(execute=False)
        self.assertEqual(self.calls, 1)
        self.assertFalse(any(op in {'act', 'start', 'stop'} for op, _ in self.bridge.operations))

    def test_old_mod_fails_before_live_control(self):
        original = self.bridge.call
        def old_mod(operation, **payload):
            result = original(operation, **payload)
            result.pop('task_protocol', None)
            return result
        self.bridge.call = old_mod
        with self.assertRaisesRegex(BridgeError, 'updated bundled mod'):
            self.run_loop()
        self.assertEqual(self.calls, 0)
        self.assertFalse(any(op == 'start' for op, _ in self.bridge.operations))
