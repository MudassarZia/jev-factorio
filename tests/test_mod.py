"""Run with optional `lupa` to execute the real mod in a Lua 5.2 test double."""
from pathlib import Path
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parent.parent
SESSION = "0123456789abcdef0123456789abcdef"


@unittest.skipIf(LuaRuntime is None, "Optional development dependency lupa is not installed")
class ModTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute((ROOT / "tests/fake_factorio.lua").read_text(encoding="utf-8"))
        self.lua.execute((ROOT / "mod/jev-factorio_0.1.0/control.lua").read_text(encoding="utf-8"))
        self.g = self.lua.globals()
        self.g.initialize()

    def call(self, op, player_index=None, **args):
        return self.g.invoke(op, self.lua.table_from(args), player_index)

    def arm(self):
        self.call("enable", player_index=1)
        self.assertTrue(self.call("start", player=1, session=SESSION)["ok"])

    def observe(self):
        result = self.call("observe", player=1)
        self.assertTrue(result["ok"], result["error"])
        return result

    def act(self, description, **overrides):
        state = self.observe()
        action = next(a for a in state["actions"].values() if description in a["description"])
        args = dict(player=1, session=SESSION, observation=state["observation"], action=action["id"])
        args.update(overrides)
        return self.call("act", **args)

    def test_loading_is_idle_and_observation_does_not_control(self):
        state = self.observe()
        self.assertFalse(state["armed"])
        self.g.tick(200)
        self.assertIsNone(self.g.player["walking_state"])
        self.assertIsNone(self.g.player["mining_state"])

    def test_rcon_cannot_arm_player(self):
        self.call("enable")
        self.assertFalse(self.call("start", player=1, session=SESSION)["ok"])

    def test_wrong_session_unknown_action_and_stale_observation(self):
        self.arm()
        state = self.observe()
        args = dict(player=1, session="wrong", observation=state["observation"], action="wait")
        self.assertFalse(self.call("act", **args)["ok"])
        args.update(session=SESSION, action="/c game.reset()")
        self.assertFalse(self.call("act", **args)["ok"])
        args.update(action="wait")
        self.g.tick(601)
        self.assertFalse(self.call("act", **args)["ok"])

    def test_walking_is_reapplied_each_tick_and_expires(self):
        self.arm()
        self.assertTrue(self.act("Walk north for")["ok"])
        self.g.tick(1)
        self.assertTrue(self.g.player["walking_state"]["walking"])
        self.g.player["walking_state"] = self.lua.table_from({"walking": False, "direction": 0})
        self.g.tick(1)
        self.assertTrue(self.g.player["walking_state"]["walking"])
        self.g.tick(58)
        self.assertFalse(self.g.player["walking_state"]["walking"])

    def test_mining_selects_resource_and_expires(self):
        self.arm()
        self.assertTrue(self.act("Mine iron-ore")["ok"])
        self.g.tick(1)
        self.assertEqual(self.g.player["selected"]["name"], "iron-ore")
        self.assertTrue(self.g.player["mining_state"]["mining"])
        self.g.tick(179)
        self.assertFalse(self.g.player["mining_state"]["mining"])

    def test_navigation_follows_path_and_expires(self):
        self.g.ore['position']['x'] = 20
        self.arm()
        result = self.act('Navigate toward iron-ore')
        self.assertTrue(result['action_succeeded'], result['result'])
        self.lua.execute('handlers[4]{id=7,path={{position={x=2,y=0},needs_destroy_to_reach=false}}}')
        self.g.tick(1)
        self.assertTrue(self.g.player['walking_state']['walking'])
        self.assertEqual(self.g.player['walking_state']['direction'], 4)
        self.g.tick(179)
        self.assertFalse(self.g.player['walking_state']['walking'])

    def test_late_path_result_cannot_restart_disabled_control(self):
        self.g.ore['position']['x'] = 20
        self.arm()
        self.act('Navigate toward iron-ore')
        self.call('disable', player_index=1)
        self.lua.execute('handlers[4]{id=7,path={{position={x=2,y=0},needs_destroy_to_reach=false}}}')
        self.g.tick(1)
        self.assertFalse(self.observe()['armed'])
        self.assertFalse(self.g.player['walking_state']['walking'])
        self.assertIsNone(self.observe()['task'])

    def test_navigation_keeps_original_destination_and_path_until_arrival(self):
        self.g.ore['position']['x'] = 20
        self.arm()
        first = self.act('Navigate toward iron-ore')['task']
        self.lua.execute('handlers[4]{id=7,path={{position={x=10,y=0}},{position={x=18,y=0}}}}')
        self.g.player['position']['x'] = 7
        self.g.tick(180)
        state = self.observe()
        self.assertEqual(state['task']['status'], 'running')
        self.assertTrue(state['task']['resume'])
        # Newly discovered resource positions must not replace the chosen target.
        self.g.ore['position']['x'] = 30
        self.lua.execute('surface.request_path=function(_) error("Do not restart an existing path") end')
        resumed = self.act('Continue committed task:')
        self.assertTrue(resumed['action_succeeded'], resumed['result'])
        self.assertEqual(resumed['task']['id'], first['id'])
        self.assertEqual(resumed['task']['target']['x'], 20)
        self.g.player['position']['x'] = 18
        self.g.tick(15)
        self.assertEqual(self.observe()['task']['status'], 'completed')
        self.assertFalse(self.g.player['walking_state']['walking'])

    def test_committed_task_rejects_switching_to_a_different_action(self):
        self.g.ore['position']['x'] = 20
        self.arm()
        self.act('Navigate toward iron-ore')
        self.g.tick(180)
        result = self.act('Walk north for')
        self.assertFalse(result['ok'])
        self.assertIn('Finish the committed task', result['error'])

    def test_unreachable_destination_is_blocked_and_temporarily_removed(self):
        self.g.ore['position']['x'] = 20
        self.arm()
        self.act('Navigate toward iron-ore')
        for attempt in range(3):
            self.lua.execute('handlers[4]{id=7}')
            if attempt < 2:
                self.assertTrue(self.act('Continue committed task:')['action_succeeded'])
        state = self.observe()
        self.assertEqual(state['task']['status'], 'blocked')
        self.assertFalse(any('Navigate toward iron-ore' in a['description'] for a in state['actions'].values()))
        self.assertTrue(self.act('Walk north for')['action_succeeded'])

    def test_pathfinder_busy_is_retried_without_counting_as_no_path(self):
        self.g.ore['position']['x'] = 20
        self.arm()
        self.act('Navigate toward iron-ore')
        for _ in range(4):
            self.lua.execute('handlers[4]{id=7,try_again_later=true}')
            self.assertTrue(self.act('Continue committed task:')['action_succeeded'])
        self.assertEqual(self.observe()['task']['status'], 'running')

    def test_navigation_without_physical_progress_gives_up_after_four_segments(self):
        self.g.ore['position']['x'] = 20
        self.arm()
        self.act('Navigate toward iron-ore')
        for attempt in range(4):
            self.lua.execute('handlers[4]{id=7,path={{position={x=18,y=0}}}}')
            self.g.tick(180)
            if attempt < 3:
                self.assertTrue(self.act('Continue committed task:')['action_succeeded'])
        self.assertEqual(self.observe()['task']['status'], 'blocked')

    def test_resource_mining_waits_for_ten_actual_additional_items(self):
        self.arm()
        self.act('Mine iron-ore')
        self.g.tick(180)
        self.g.main_inventory['items']['iron-ore'] = 14
        state = self.observe()
        self.assertEqual(state['task']['status'], 'running')
        self.assertIn('4/10', state['task']['progress'])
        self.assertTrue(self.act('Continue committed task:')['action_succeeded'])
        self.g.main_inventory['items']['iron-ore'] = 20
        self.g.tick(15)
        self.assertEqual(self.observe()['task']['status'], 'completed')
        self.assertFalse(self.g.player['mining_state']['mining'])

    def test_mining_depletion_and_full_inventory_report_blocked(self):
        for failure in ('depleted', 'full'):
            with self.subTest(failure=failure):
                self.setUp()
                self.arm()
                self.act('Mine iron-ore')
                if failure == 'depleted':
                    self.g.ore['valid'] = False
                    # A real engine query does not return invalid entities.
                    self.lua.execute('surface.find_entities_filtered=function(_) return {} end')
                else:
                    self.g.main_inventory['capacity'] = 10
                self.g.tick(15)
                self.assertEqual(self.observe()['task']['status'], 'blocked')

    def test_crafting_waits_for_output_and_detects_cancellation(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                self.setUp()
                self.lua.execute('force.recipes["iron-gear-wheel"].products={{type="item",name="iron-gear-wheel",amount=1}}')
                self.arm()
                self.act('Handcraft 1 recipe batch of iron-gear-wheel')
                state = self.observe()
                self.assertEqual(state['task']['status'], 'running')
                self.assertFalse(state['task']['resume'])
                self.assertFalse(self.act('Walk north for')['ok'])
                self.g.player['crafting_queue_size'] = 0
                if not cancelled:
                    self.g.main_inventory['items']['iron-gear-wheel'] = 1
                self.assertEqual(self.observe()['task']['status'], 'blocked' if cancelled else 'completed')

    def test_large_recipe_list_preserves_other_controls_and_priorities(self):
        self.lua.execute('for i=1,300 do force.recipes["recipe-"..i]={enabled=true} end; force.recipes["zzz-critical"]={enabled=true}')
        state = self.call('observe', player=1, recipe_priorities=self.lua.table_from(['zzz-critical']))
        actions = list(state['actions'].values())
        self.assertLessEqual(len(actions), 220)
        descriptions = [a['description'] for a in actions]
        self.assertTrue(any('zzz-critical' in d for d in descriptions))
        self.assertTrue(any('Mine iron-ore' in d for d in descriptions))
        self.assertTrue(any('Build stone-furnace' in d for d in descriptions))
        self.assertIn('stop', [a['id'] for a in actions])

    def test_in_game_button_can_arm_and_disarm(self):
        event = self.lua.table_from({'player_index': 1, 'element': self.lua.table_from({'valid': True, 'name': 'jev-bridge-toggle'})})
        self.g.handlers[3](event)
        self.assertTrue(self.observe()['armed'])
        self.g.handlers[3](event)
        self.assertFalse(self.observe()['armed'])

    def test_disable_cancels_motion_and_session(self):
        self.arm()
        self.act("Walk north for")
        self.g.tick(1)
        self.call("disable", player_index=1)
        self.assertFalse(self.g.player["walking_state"]["walking"])
        self.assertFalse(self.observe()["armed"])

    def test_disconnect_disarms(self):
        self.arm()
        self.act("Walk north for")
        self.g.player["connected"] = False
        self.g.tick(1)
        self.assertFalse(self.g.storage["jev"]["players"][1]["armed"])

    def test_lease_expiry_disarms(self):
        self.arm()
        self.g.tick(3601)
        self.assertFalse(self.observe()["armed"])

    def test_join_disarms_saved_session(self):
        self.arm()
        self.g.handlers[2](self.lua.table_from({"player_index": 1}))
        self.assertFalse(self.observe()["armed"])

    def test_partial_transfer_preserves_items(self):
        self.arm()
        result = self.act("Fuel stone-furnace")
        self.assertTrue(result["action_succeeded"], result["result"])
        self.assertEqual(self.g.fuel_inventory["items"]["coal"], 2)
        self.assertEqual(self.g.main_inventory["items"]["coal"], 3)

    def test_transfer_exception_refunds_items(self):
        self.arm()
        self.g.fuel_inventory["fail_insert"] = True
        result = self.act("Fuel stone-furnace")
        self.assertFalse(result["action_succeeded"])
        self.assertEqual(self.g.main_inventory["items"]["coal"], 5)

    def test_take_output_preserves_items(self):
        self.arm()
        result = self.act("Take up to 5 iron-plate")
        self.assertTrue(result["action_succeeded"], result["result"])
        self.assertEqual(self.g.main_inventory["items"]["iron-plate"], 15)
        self.assertEqual(self.g.output_inventory["items"]["iron-plate"], 0)

    def test_build_consumes_existing_item(self):
        self.arm()
        result = self.act("Build stone-furnace")
        self.assertTrue(result["action_succeeded"], result["result"])
        self.assertEqual(self.g.built_count, 1)
        self.assertEqual(self.g.main_inventory["items"]["stone-furnace"], 0)

    def test_failed_build_restores_item_to_inventory(self):
        self.arm()
        self.g.blocked_build = True
        result = self.act("Build stone-furnace")
        self.assertFalse(result["action_succeeded"])
        self.assertEqual(self.g.main_inventory["items"]["stone-furnace"], 1)
        self.assertFalse(self.g.player["cursor_stack"]["valid_for_read"])

    def test_observation_cannot_be_replayed(self):
        self.arm()
        state = self.observe()
        args = dict(player=1, session=SESSION, observation=state["observation"], action="wait")
        self.assertTrue(self.call("act", **args)["ok"])
        self.g.tick(60)
        self.assertFalse(self.call("act", **args)["ok"])


if __name__ == "__main__":
    unittest.main()
