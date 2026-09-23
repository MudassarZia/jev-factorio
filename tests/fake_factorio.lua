-- Test double only. This is NOT Factorio and does not emulate its physics.
storage = {}
registered, handlers = {}, {}
script = {
  on_init = function(f) initialize = f end,
  on_configuration_changed = function(f) configure = f end,
  on_event = function(event, f) handlers[event] = f end
}
commands = {add_command = function(name, _, f) registered[name] = f end}
rcon = {print = function(value) last_reply = value end}
helpers = {json_to_table = function(_) return arguments end, table_to_json = function(v) return v end}
defines = {
  direction = {north=0,northeast=2,east=4,southeast=6,south=8,southwest=10,west=12,northwest=14},
  controllers = {character=1,remote=2}, events={on_tick=1,on_player_joined_game=2,on_gui_click=3,on_script_path_request_finished=4}, entity_status={working=1},
  inventory = {furnace_source=1, assembling_machine_input=2, lab_input=3, chest=4},
  build_check_type = {manual=1}, build_mode={normal=1}
}
function new_inventory(items, capacity)
  local i = {items=items or {}, capacity=capacity or 1000}
  i.get_contents = function()
    local out = {}
    for name, n in pairs(i.items) do if n > 0 then out[#out+1] = {name=name,count=n,quality="normal"} end end
    table.sort(out, function(a,b) return a.name < b.name end)
    return out
  end
  i.get_item_count = function(item) return i.items[item.name] or 0 end
  i.is_empty = function() return #i.get_contents() == 0 end
  i.can_insert = function(item) return i.get_item_count(item) < i.capacity end
  i.insert = function(item)
    if i.fail_insert then error("injected insert failure") end
    local n = math.max(0,math.min(item.count, i.capacity-i.get_item_count(item)))
    i.items[item.name] = i.get_item_count(item) + n
    return n
  end
  i.remove = function(item)
    local n = math.min(item.count, i.get_item_count(item))
    i.items[item.name] = i.get_item_count(item)-n
    return n
  end
  i.find_item_stack = function(item)
    if i.get_item_count(item) == 0 then return nil end
    return {name=item.name, count=i.get_item_count(item), owner=i}
  end
  return i
end
main_inventory = new_inventory({["iron-ore"]=10, coal=5, ["stone-furnace"]=1, ["iron-plate"]=10})
fuel_inventory = new_inventory({}, 2)
input_inventory = new_inventory({})
output_inventory = new_inventory({["iron-plate"]=5})
force = {recipes={ ["stone-furnace"]={enabled=true}, ["iron-gear-wheel"]={enabled=true}},
  technologies={}, research_progress=0}
force.add_research = function(_) return true end
surface = {index=1,name="nauvis"}
player = {index=1,name="Test player",valid=true,connected=true,character={valid=true,health=250},
  controller_type=1,driving=false,position={x=0,y=0},surface=surface,force=force,
  resource_reach_distance=3, build_distance=6,crafting_queue_size=0}
player.print = function(v) player.last_message=v end
player.character.prototype = {collision_box={{-.2,-.2},{.2,.2}}, collision_mask={layers={player=true}}}
surface.request_path = function(parameters) requested_path=parameters; return 7 end
player.get_main_inventory = function() return main_inventory end
player.can_reach_entity = function(e) return e.position.x <= 6 end
player.get_craftable_count = function(_) return 1 end
player.begin_crafting = function(_) player.crafting_queue_size=1; return 1 end
player.cursor_stack = {count=0,valid_for_read=false}
player.cursor_stack.transfer_stack = function(stack)
  player.cursor_stack.count=stack.count
  player.cursor_stack.name=stack.name
  player.cursor_stack.valid_for_read=true
  stack.owner.remove{name=stack.name,count=stack.count}
  return true
end
player.clear_cursor = function()
  if player.cursor_stack.valid_for_read then
    main_inventory.insert{name=player.cursor_stack.name,count=player.cursor_stack.count}
    player.cursor_stack.count=0; player.cursor_stack.valid_for_read=false
  end
  return true
end
player.can_build_from_cursor = function(_) return not blocked_build end
player.build_from_cursor = function(_)
  player.cursor_stack.count=player.cursor_stack.count-1
  player.cursor_stack.valid_for_read=player.cursor_stack.count>0
  built_count=(built_count or 0)+1
end
ore = {name="iron-ore",type="resource",position={x=1,y=0},amount=1000,valid=true,minable=true,surface=surface}
furnace = {name="stone-furnace",type="furnace",position={x=3,y=0},direction=0,status=1,
  valid=true,surface=surface,force=force}
furnace.get_fuel_inventory = function() return fuel_inventory end
furnace.get_output_inventory = function() return output_inventory end
furnace.get_inventory = function(_) return input_inventory end
furnace.get_recipe = function() return nil end
surface.find_entities_filtered = function(filter)
  if filter.name == "iron-ore" then return {ore} end
  if type(filter.type) == "table" and filter.force == force then return {furnace} end
  return {}
end
surface.can_place_entity = function(_) return true end
prototypes = {entity={["stone-furnace"]={tile_width=2,tile_height=2}}}
game = {tick=0,get_player=function(index) if index==1 then return player end end,connected_players={player}}
function invoke(name, args, player_index)
  arguments=args or {}; last_reply=nil
  registered["jev-"..name]({parameter="{}",player_index=player_index,tick=game.tick})
  return last_reply
end
function tick(n)
  for _=1,n do game.tick=game.tick+1; handlers[1]() end
end
