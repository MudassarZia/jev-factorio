-- Jev never sends Lua. It can select only a server-stored candidate ID.
local PROTOCOL = 1
local LEASE = 3600 -- 60 game seconds; mining itself lasts at most 180 ticks.
local directions = {
  {"north", 0, -1}, {"northeast", 1, -1}, {"east", 1, 0}, {"southeast", 1, 1},
  {"south", 0, 1}, {"southwest", -1, 1}, {"west", -1, 0}, {"northwest", -1, -1}
}
local build_names = {"stone-furnace", "burner-mining-drill", "wooden-chest", "transport-belt",
  "burner-inserter", "inserter", "small-electric-pole", "pipe", "pipe-to-ground", "offshore-pump",
  "boiler", "steam-engine", "electric-mining-drill", "assembling-machine-1", "assembling-machine-2",
  "assembling-machine-3", "lab", "steel-furnace", "electric-furnace", "iron-chest", "steel-chest",
  "medium-electric-pole", "big-electric-pole", "substation", "long-handed-inserter", "fast-inserter",
  "underground-belt", "splitter", "pumpjack", "oil-refinery", "chemical-plant", "storage-tank",
  "pump", "solar-panel", "accumulator", "rocket-silo", "radar", "landfill"}
local resource_names = {"iron-ore", "copper-ore", "coal", "stone", "crude-oil"}
local machine_types = {"furnace", "mining-drill", "container", "assembling-machine", "lab", "boiler",
  "generator", "inserter", "electric-pole", "offshore-pump", "pipe", "pipe-to-ground", "pump",
  "storage-tank", "rocket-silo", "transport-belt", "underground-belt", "splitter", "solar-panel", "accumulator", "radar"}

local function init()
  storage.jev = storage.jev or {players = {}, counter = 0}
  storage.jev.world = storage.jev.world or (tostring(game.tick) .. "-" .. tostring(math.random(1, 2147483646)))
end
script.on_init(init)
script.on_configuration_changed(init)

local function state_for(index)
  init()
  storage.jev.players[index] = storage.jev.players[index] or {armed = false}
  return storage.jev.players[index]
end
local function distance(a, b)
  return math.sqrt((a.x - b.x)^2 + (a.y - b.y)^2)
end
local function position(p)
  return {x = p.x, y = p.y}
end
local function inventory(inv)
  if not inv then return {} end
  return inv.get_contents()
end
local function count(inv, name)
  return inv.get_item_count({name = name, quality = "normal"})
end
local function valid_player(index)
  assert(type(index) == "number" and index >= 1 and index % 1 == 0, "Invalid player index")
  local p = game.get_player(index)
  assert(p and p.valid and p.connected and p.character and p.character.valid, "Player must be connected with a living character")
  assert(p.controller_type == defines.controllers.character and not p.driving, "Exit vehicles and remote/map view first")
  return p
end
local function halt(p, s)
  if s.active and p and p.valid and p.character and p.controller_type == defines.controllers.character then
    p.walking_state = {walking = false, direction = defines.direction.north}
    p.mining_state = {mining = false}
    p.selected = nil
  end
  s.active = nil
end
local function button(p, s)
  if not p or not p.valid or not p.gui then return end
  local b = p.gui.top["jev-bridge-toggle"]
  if not b then b = p.gui.top.add{type = "button", name = "jev-bridge-toggle"} end
  b.caption = s.armed and "Jev: ON (click to stop)" or "Jev: OFF (click to enable)"
end
local function disarm(p, s)
  halt(p, s)
  s.task = nil
  s.armed, s.session, s.snapshot, s.lease = false, nil, nil, nil
  button(p, s)
end
local function describe(e, p)
  return e.name .. " at (" .. string.format("%.1f, %.1f", e.position.x, e.position.y) .. ")"
    .. ", distance " .. string.format("%.1f", distance(p.position, e.position))
end
local function nearest(p, filter, limit)
  filter.position, filter.radius = p.position, 32
  local entities = p.surface.find_entities_filtered(filter)
  table.sort(entities, function(a, b)
    local da, db = distance(a.position, p.position), distance(b.position, p.position)
    if da ~= db then return da < db end
    if a.position.x ~= b.position.x then return a.position.x < b.position.x end
    return a.position.y < b.position.y
  end)
  local out = {}
  for i = 1, math.min(limit, #entities) do out[i] = entities[i] end
  return out
end
local function toward(p, target)
  local dx, dy = target.x - p.x, target.y - p.y
  local x = math.abs(dx) > math.abs(dy) * 0.4 and (dx > 0 and 1 or -1) or 0
  local y = math.abs(dy) > math.abs(dx) * 0.4 and (dy > 0 and 1 or -1) or 0
  for _, d in ipairs(directions) do if d[2] == x and d[3] == y then return d[1] end end
  return "north"
end
local function snap(value, width)
  if width % 2 == 0 then return math.floor(value + 0.5) end
  return math.floor(value) + 0.5
end

-- A task survives the three-second input lease. Only a fresh, authenticated
-- continuation can start another segment; observing a task never moves a player.
local function task_key(a)
  local pos = a.position or (a.entity and a.entity.valid and a.entity.position)
  return a.kind .. ":" .. (a.recipe or (a.entity and a.entity.valid and a.entity.name) or "")
    .. (pos and (":" .. pos.x .. ":" .. pos.y) or "")
end
local function task_view(s)
  local t = s.task
  if not t then return nil end
  return {id = t.id, kind = t.action.kind, description = t.description, status = t.status,
    result = t.result, progress = t.progress, target = t.action.position,
    resume = t.status == "running" and not s.active and t.action.kind ~= "craft"}
end
local function finish_task(p, s, status, result)
  local t = s.task
  if not t or t.status ~= "running" then return end
  halt(p, s)
  t.status, t.result = status, result
  if status == "blocked" then
    s.blocked_tasks = s.blocked_tasks or {}
    s.blocked_tasks[t.key] = game.tick + 3600
  end
end
local function begin_task(p, s, a)
  s.task_counter = (s.task_counter or 0) + 1
  s.task = {id = tostring(p.index) .. ":" .. s.task_counter, action = a,
    description = a.description or a.kind, status = "running", key = task_key(a),
    surface = p.surface.index, started = game.tick, progress_tick = game.tick,
    stalls = 0, path_failures = 0, last_amount = 0}
  return s.task
end
local function update_task(p, s)
  local t = s.task
  if not t or t.status ~= "running" then return end
  local a, inv = t.action, p.get_main_inventory()
  if p.surface.index ~= t.surface then finish_task(p, s, "blocked", "Player changed surface"); return end
  if a.kind == "navigate" then
    local remaining = distance(p.position, a.position)
    t.progress = string.format("%.1f tiles remaining", remaining)
    if remaining <= (a.radius or 2) + .25 then
      finish_task(p, s, "completed", "Arrived at the chosen destination")
    elseif t.stalls >= 4 or t.path_failures >= 3 or game.tick - t.started >= 7200 then
      finish_task(p, s, "blocked", "Could not reach the destination after repeated path attempts or 120 game seconds")
    end
  elseif a.kind == "mine" then
    if t.item then
      local gained = math.max(0, count(inv, t.item) - t.baseline)
      t.progress = gained .. "/" .. t.quantity .. " additional " .. t.item
      if gained > t.last_amount then t.progress_tick, t.last_amount = game.tick, gained end
      if gained >= t.quantity then finish_task(p, s, "completed", "Gathered " .. gained .. " additional " .. t.item); return end
      if not inv.can_insert{name = t.item, count = 1, quality = "normal"} then
        finish_task(p, s, "blocked", "Inventory is full before the mining target was reached"); return
      end
      if not a.entity or not a.entity.valid then
        -- Depletion may move mining to another reachable tile of the SAME resource.
        for _, e in ipairs(nearest(p, {name = t.item, type = "resource"}, 32)) do
          if e.valid and e.minable and distance(p.position, e.position) <= p.resource_reach_distance then a.entity = e; break end
        end
      end
    elseif not a.entity or not a.entity.valid then
      finish_task(p, s, "completed", "Finished mining the chosen entity"); return
    end
    if not a.entity or not a.entity.valid or a.entity.surface ~= p.surface
      or (t.item and distance(p.position, a.entity.position) > p.resource_reach_distance)
      or (not t.item and not p.can_reach_entity(a.entity)) then
      finish_task(p, s, "blocked", "No reachable mining target remains"); return
    end
    if game.tick - t.progress_tick >= 1800 or game.tick - t.started >= 10800 then
      finish_task(p, s, "blocked", "Mining made no inventory progress for 30 game seconds")
    end
  elseif a.kind == "craft" then
    t.progress = p.crafting_queue_size .. " recipe(s) still queued"
    if p.crafting_queue_size == 0 then
      local complete = true
      for _, product in ipairs(t.products or {}) do
        if count(inv, product.name) < product.target then complete = false end
      end
      finish_task(p, s, complete and "completed" or "blocked",
        complete and "The selected crafting batch finished" or "Crafting ended without the expected output (possibly cancelled)")
    elseif game.tick - t.started >= 36000 then
      finish_task(p, s, "blocked", "Crafting has not finished after 10 game minutes; queued recipes remain in the game")
    end
  end
end

local function end_segment(p, s)
  local a, t = s.active, s.task
  if a and t and t.status == "running" and a.kind == "navigate" then
    if a.path and a.segment_position and distance(p.position, a.segment_position) < .5 then
      t.stalls = t.stalls + 1
      a.path, a.waypoint = nil, nil -- retry pathfinding around the obstruction
    elseif a.path then t.stalls = 0 end
  end
  halt(p, s)
  update_task(p, s)
end

local function sorted_names(values)
  local names = {}
  for name in pairs(values) do names[#names + 1] = name end
  table.sort(names)
  return names
end

local function remember(p, s, entities)
  s.sites, s.known = s.sites or {}, s.known or {}
  s.base = s.base or position(p.position)
  for _, e in ipairs(entities) do
    local key = e.name .. ":" .. e.position.x .. ":" .. e.position.y
    s.known[key] = e
  end
  local known, stale = {}, {}
  for key, e in pairs(s.known) do
    if e.valid and e.surface == p.surface then
      known[#known + 1] = {name = e.name, position = position(e.position)}
    else stale[#stale + 1] = key end
  end
  for _, key in ipairs(stale) do s.known[key] = nil end
  table.sort(known, function(a, b) return distance(a.position, p.position) < distance(b.position, p.position) end)
  while #known > 100 do table.remove(known) end
  if not s.scanned or game.tick - s.scanned > 300 then
    s.scanned = game.tick
    for _, name in ipairs(resource_names) do
      local found = p.surface.find_entities_filtered{position = p.position, radius = 128, name = name, type = "resource"}
      local best
      for _, e in ipairs(found) do
        local chunk = {math.floor(e.position.x / 32), math.floor(e.position.y / 32)}
        if (not p.force.is_chunk_charted or p.force.is_chunk_charted(p.surface, chunk)) and
          (not best or distance(e.position, p.position) < distance(best.position, p.position)) then best = e end
      end
      if best then s.sites[name] = position(best.position) end
    end
    if p.surface.find_tiles_filtered then
      local tiles = p.surface.find_tiles_filtered{position = p.position, radius = 32, name = {"water", "deepwater"}, limit = 2000}
      table.sort(tiles, function(a, b) return distance(a.position, p.position) < distance(b.position, p.position) end)
      if tiles[1] then s.sites.water = position(tiles[1].position) end
    end
  end
  return known
end

local function catalog(p)
  local recipes, technologies = {}, {}
  for _, name in ipairs(sorted_names(p.force.recipes)) do
    local r = p.force.recipes[name]
    recipes[#recipes + 1] = {name = name, category = r.category, ingredients = r.ingredients, products = r.products}
  end
  for _, name in ipairs(sorted_names(p.force.technologies)) do
    local t, prereqs = p.force.technologies[name], {}
    for pname in pairs(t.prerequisites) do prereqs[#prereqs + 1] = pname end
    table.sort(prereqs)
    technologies[#technologies + 1] = {name = name, prerequisites = prereqs,
      count = t.research_unit_count, ingredients = t.research_unit_ingredients,
      trigger = t.prototype.research_trigger}
  end
  return {ok = true, recipes = recipes, technologies = technologies}
end

local function observe(p, priorities, research_priority)
  local s = state_for(p.index)
  local recipe_names, rank = sorted_names(p.force.recipes), {}
  for i, name in ipairs(priorities or {}) do if i <= 64 and type(name) == "string" then rank[name] = i end end
  table.sort(recipe_names, function(a, b)
    if (rank[a] or 1000) ~= (rank[b] or 1000) then return (rank[a] or 1000) < (rank[b] or 1000) end
    return a < b
  end)
  if s.lease and game.tick > s.lease then disarm(p, s) end
  if s.session then s.lease = game.tick + LEASE end
  update_task(p, s)
  for key, expires in pairs(s.blocked_tasks or {}) do
    if expires <= game.tick then s.blocked_tasks[key] = nil end
  end
  local actions, stored = {}, {}
  local function add(a, description, id)
    if #actions >= 2000 then return end
    if s.blocked_tasks and (s.blocked_tasks[task_key(a)] or 0) > game.tick then return end
    id = id or ("a" .. tostring(#actions + 1))
    a.description = description
    stored[id] = a
    actions[#actions + 1] = {id = id, description = description}
  end
  add({kind = "wait", ticks = 60}, "Wait for 1 game second", "wait")
  add({kind = "stop"}, "Stop this controller run; goal achieved or no useful supported action", "stop")
  if s.task and s.task.status == "running" and s.task.action.kind ~= "craft" then
    add({kind = "continue_task", task_id = s.task.id}, "Continue committed task: " .. s.task.description, "continue_task")
  end
  for _, d in ipairs(directions) do
    add({kind = "walk", direction = d[1], ticks = 60}, "Walk " .. d[1] .. " for 1 game second")
  end

  local resources, resource_entities = {}, {}
  for _, name in ipairs(resource_names) do
    local found = nearest(p, {name = name, type = "resource"}, 2)
    for _, e in ipairs(found) do
      resource_entities[#resource_entities + 1] = e
      resources[#resources + 1] = {name = e.name, position = position(e.position), amount = e.amount}
      if name ~= "crude-oil" and distance(p.position, e.position) <= p.resource_reach_distance and e.minable then
        add({kind = "mine", entity = e, ticks = 180}, "Mine " .. describe(e, p) .. " until 10 additional " .. name .. " are collected")
      else
        add({kind = "navigate", position = position(e.position), radius = 2, ticks = 180}, "Navigate toward " .. describe(e, p) .. " until arrival")
      end
    end
  end
  for _, e in ipairs(nearest(p, {type = "tree"}, 2)) do
    resources[#resources + 1] = {name = e.name, position = position(e.position), yields = "wood"}
    if p.can_reach_entity(e) and e.minable then
      add({kind = "mine", entity = e, ticks = 180}, "Chop " .. describe(e, p) .. " until the tree is removed")
    else
      add({kind = "navigate", position = position(e.position), radius = 2, ticks = 180}, "Navigate toward wood: " .. describe(e, p))
    end
  end

  local inv = p.get_main_inventory()
  if p.crafting_queue_size == 0 then
    for _, name in ipairs(recipe_names) do
      local recipe = p.force.recipes[name]
      if recipe and recipe.enabled and p.get_craftable_count(name) > 0 then
        add({kind = "craft", recipe = name}, "Handcraft 1 recipe batch of " .. name .. " using inventory ingredients")
        local n = math.min(20, p.get_craftable_count(name))
        if n >= 5 then add({kind = "craft", recipe = name, count = n}, "Handcraft " .. n .. " recipe batches of " .. name) end
      end
    end
  end

  local researched = {}
  local technology_names = sorted_names(p.force.technologies)
  if research_priority and p.force.technologies[research_priority] then
    for i, name in ipairs(technology_names) do if name == research_priority then table.remove(technology_names, i); break end end
    table.insert(technology_names, 1, research_priority)
  end
  for _, name in ipairs(technology_names) do
    local tech = p.force.technologies[name]
    if tech.researched then researched[#researched + 1] = name
    elseif not p.force.current_research and tech.enabled and not tech.prototype.research_trigger then
      local ready = true
      for _, prerequisite in pairs(tech.prerequisites) do if not prerequisite.researched then ready = false end end
      if ready then add({kind = "research", name = name}, "Select research " .. name .. "; labs still need power and science") end
    end
  end
  local machines, entities = {}, nearest(p, {force = p.force, type = machine_types}, 24)
  local known = remember(p, s, entities)
  for _, name in ipairs(sorted_names(s.sites)) do
    local pos = s.sites[name]
    if distance(p.position, pos) > 4 then
      add({kind = "navigate", position = pos, radius = 2, ticks = 180}, "Navigate to known " .. name .. " at (" .. pos.x .. ", " .. pos.y .. ")")
    end
  end
  if distance(p.position, s.base) > 10 then
    add({kind = "navigate", position = s.base, radius = 2, ticks = 180}, "Return toward the starting base")
  end
  local nav_count = 0
  for _, e in ipairs(known) do
    if distance(p.position, e.position) > 5 and nav_count < 8 then
      add({kind = "navigate", position = e.position, radius = 3, ticks = 180}, "Navigate to known " .. e.name .. " at (" .. e.position.x .. ", " .. e.position.y .. ")")
      nav_count = nav_count + 1
    end
  end
  local extra_positions, fluid_targets = {}, {}
  local feed_names = {}
  for _, item in ipairs(inventory(inv)) do if item.quality == "normal" then feed_names[#feed_names + 1] = item.name end end
  table.sort(feed_names)
  for _, e in ipairs(entities) do
    local entry = {name = e.name, position = position(e.position), direction = e.direction,
      status = e.status, fuel = inventory(e.get_fuel_inventory()), output = inventory(e.get_output_inventory()),
      energy = e.energy, electric_network = e.electric_network_id}
    for key, val in pairs(defines.entity_status) do if val == e.status then entry.status = key; break end end
    if e.type == "furnace" or e.type == "assembling-machine" or e.type == "rocket-silo" then
      local recipe = e.get_recipe()
      entry.recipe = recipe and recipe.name or nil
    end
    local input
    if e.type == "furnace" then input = e.get_inventory(defines.inventory.furnace_source)
    elseif e.type == "assembling-machine" then input = e.get_inventory(defines.inventory.assembling_machine_input)
    elseif e.type == "rocket-silo" then input = e.get_inventory(defines.inventory.rocket_silo_input)
    elseif e.type == "lab" then input = e.get_inventory(defines.inventory.lab_input)
    elseif e.type == "container" then input = e.get_inventory(defines.inventory.chest) end
    entry.input = inventory(input)
    entry.fluids = {}
    if e.fluidbox then
      for i = 1, #e.fluidbox do
        local box = e.fluidbox[i]
        local f = {contents = box, connections = {}}
        for _, connection in ipairs(e.fluidbox.get_pipe_connections(i)) do
          f.connections[#f.connections + 1] = {position = position(connection.position),
            target = position(connection.target_position), connected = connection.target ~= nil, flow = connection.flow_direction}
          if not connection.target then fluid_targets[#fluid_targets + 1] = position(connection.target_position) end
        end
        entry.fluids[#entry.fluids + 1] = f
      end
    end
    if e.type == "rocket-silo" then
      entry.rocket_parts, entry.rocket_status = e.rocket_parts, e.rocket_silo_status
      if p.can_reach_entity(e) then add({kind = "launch", entity = e}, "Launch rocket from " .. describe(e, p) .. " if ready") end
    end
    if e.type == "inserter" then entry.pickup, entry.drop = position(e.pickup_position), position(e.drop_position) end
    machines[#machines + 1] = entry
    if e.type == "mining-drill" and e.drop_position then extra_positions[#extra_positions + 1] = position(e.drop_position) end
    if p.can_reach_entity(e) then
      local fuel = e.get_fuel_inventory()
      for _, name in ipairs(feed_names) do
        local n = math.min(count(inv, name), (name == "wood" or name == "coal") and 10 or 100)
        if n > 0 then
          if fuel and fuel.can_insert({name = name, count = 1}) then
            add({kind = "put", entity = e, slot = "fuel", name = name, count = n}, "Fuel " .. describe(e, p) .. " with up to " .. n .. " " .. name)
          end
          if input and e.type ~= "container" and input.can_insert({name = name, count = 1}) then
            add({kind = "put", entity = e, slot = "input", name = name, count = n}, "Insert up to " .. n .. " " .. name .. " into " .. describe(e, p))
          end
        end
      end
      local output = e.get_output_inventory()
      if e.type == "container" then output = e.get_inventory(defines.inventory.chest) end
      for _, item in ipairs(inventory(output)) do
        if item.quality == "normal" and inv.can_insert({name = item.name, count = 1}) then
          add({kind = "take", entity = e, name = item.name, count = math.min(100, item.count)}, "Take up to " .. math.min(100, item.count) .. " " .. item.name .. " from " .. describe(e, p))
        end
      end
      -- Configure empty assemblers only, so changing a recipe cannot discard ingredients.
      if e.type == "assembling-machine" and input.is_empty() and e.get_output_inventory().is_empty() then
        for _, name in ipairs(recipe_names) do
          local recipe = p.force.recipes[name]
          if recipe and recipe.enabled and e.prototype.crafting_categories[recipe.category] then
            add({kind = "recipe", entity = e, recipe = name}, "Set empty " .. describe(e, p) .. " to produce " .. name)
          end
        end
      end
      if e.rotatable then
        add({kind = "rotate", entity = e}, "Rotate " .. describe(e, p) .. " clockwise")
      end
      if e.minable and e.name ~= "crash-site-spaceship" then
        add({kind = "mine", entity = e, ticks = 180}, "Recover misplaced " .. describe(e, p) .. " until mining finishes")
      end
    end
  end

  -- Positions are concrete choices. Execution rechecks the real player's build rules.
  local available_builds = {}
  for _, name in ipairs(build_names) do
    if count(inv, name) > 0 and prototypes.entity[name] then available_builds[#available_builds + 1] = name end
  end
  for _, name in ipairs(available_builds) do
    if s.focus ~= name then add({kind = "focus", name = name}, "Show construction positions for " .. name) end
  end
  local focus_valid = false
  for _, name in ipairs(available_builds) do if name == s.focus then focus_valid = true end end
  if not focus_valid then s.focus = available_builds[1] end
  for _, name in ipairs(available_builds) do
    if name == s.focus then
      local proto = prototypes.entity[name]
      local positions = {}
      if name == "burner-mining-drill" or name == "electric-mining-drill" or name == "pumpjack" then
        for _, e in ipairs(resource_entities) do
          if (name == "pumpjack") == (e.name == "crude-oil") then positions[#positions + 1] = e.position end
        end
      else
        for _, pos in ipairs(fluid_targets) do
          if name == "pipe" then positions[#positions + 1] = pos end
        end
        for _, pos in ipairs(extra_positions) do if name:find("chest") then positions[#positions + 1] = pos end end
        local offset = math.max(3, math.ceil(math.max(proto.tile_width, proto.tile_height) / 2) + 1)
        for _, d in ipairs({directions[1], directions[3], directions[5], directions[7]}) do
          positions[#positions + 1] = {x = p.position.x + d[2] * offset, y = p.position.y + d[3] * offset}
        end
        -- Nearby grid positions allow shore pumps, belts, poles and compact layouts.
        for radius = 1, math.floor(p.build_distance) do
          for x = -radius, radius do
            for _, y in ipairs({-radius, radius}) do positions[#positions + 1] = {x = p.position.x + x, y = p.position.y + y} end
          end
          for y = -radius + 1, radius - 1 do
            for _, x in ipairs({-radius, radius}) do positions[#positions + 1] = {x = p.position.x + x, y = p.position.y + y} end
          end
        end
      end
      local seen, offered = {}, 0
      -- Align each prospective fluid port with open ports of existing machines.
      for _, target in ipairs(fluid_targets) do
        for _, box in ipairs(proto.fluidbox_prototypes or {}) do
          for _, port in ipairs(box.pipe_connections) do
            for i, dir in ipairs({"north", "east", "south", "west"}) do
              local offset = port.positions[i]
              if offset then
                local pos = {x = target.x - offset.x, y = target.y - offset.y}
                if offered < 64 and distance(p.position, pos) <= p.build_distance and p.surface.can_place_entity{name = name, position = pos,
                  direction = defines.direction[dir], force = p.force, build_check_type = defines.build_check_type.manual} then
                  add({kind = "build", name = name, position = pos, direction = dir}, "Build " .. name .. " at (" .. pos.x .. ", " .. pos.y .. ") facing " .. dir .. " aligned to an open fluid port")
                  offered = offered + 1
                end
              end
            end
          end
        end
      end
      for _, pos in ipairs(positions) do
        local target = {x = snap(pos.x, proto.tile_width), y = snap(pos.y, proto.tile_height)}
        local key = target.x .. ":" .. target.y
        if offered < 64 and not seen[key] and distance(p.position, target) <= p.build_distance then
          seen[key] = true
          for _, dir in ipairs({"north", "east", "south", "west"}) do
            if p.surface.can_place_entity{name = name, position = target, direction = defines.direction[dir], force = p.force,
              build_check_type = defines.build_check_type.manual} then
              add({kind = "build", name = name, position = target, direction = dir},
                "Build " .. name .. " at (" .. target.x .. ", " .. target.y .. ") facing " .. dir)
              offered = offered + 1
              if name == "stone-furnace" or name == "wooden-chest" or name == "small-electric-pole" or name == "lab" then break end
            end
          end
        end
      end
    end
  end
  local enemies = {}
  for _, e in ipairs(nearest(p, {force = "enemy", type = {"unit", "unit-spawner", "turret"}}, 8)) do
    enemies[#enemies + 1] = {name = e.name, position = position(e.position)}
  end
  storage.jev.counter = storage.jev.counter + 1
  -- Round-robin categories so a large recipe/placement list cannot hide navigation,
  -- transfers, research or Stop. Keep only IDs that were actually offered.
  local buckets, kinds, selected, selected_stored = {}, {}, {}, {}
  for _, action in ipairs(actions) do
    local kind = stored[action.id].kind
    if not buckets[kind] then buckets[kind] = {}; kinds[#kinds + 1] = kind end
    buckets[kind][#buckets[kind] + 1] = action
  end
  local row, more = 1, true
  while #selected < 220 and more do
    more = false
    for _, kind in ipairs(kinds) do
      local action = buckets[kind][row]
      if action and #selected < 220 then
        more = true
        selected[#selected + 1], selected_stored[action.id] = action, stored[action.id]
      end
    end
    row = row + 1
  end
  actions, stored = selected, selected_stored
  local observation = tostring(game.tick) .. ":" .. tostring(storage.jev.counter)
  s.snapshot = {id = observation, tick = game.tick, actions = stored, surface = p.surface.index, position = position(p.position)}
  return {ok = true, protocol = PROTOCOL, task_protocol = 1, task = task_view(s), world = storage.jev.world, observation = observation, tick = game.tick,
    armed = s.armed == true, busy_ticks = s.active and math.max(0, s.active.until_tick - game.tick) or 0,
    player = {index = p.index, position = position(p.position), surface = p.surface.name, health = p.character.health,
      mining_progress = p.character_mining_progress},
    inventory = inventory(inv), crafting_queue_size = p.crafting_queue_size,
    resources = resources, machines = machines, enemies = enemies, known_machines = known, known_sites = s.sites,
    construction_focus = s.focus, researched = researched, rockets_launched = p.force.rockets_launched or 0,
    research = p.force.current_research and p.force.current_research.name or nil,
    research_progress = p.force.research_progress, actions = actions}
end

local function entity_for(p, a)
  local e = a.entity
  assert(e and e.valid and e.surface == p.surface, "Target no longer exists on this surface")
  assert(e.force == p.force, "Target does not belong to this player's force")
  assert(p.can_reach_entity(e), "Target is now out of reach")
  return e
end
local function input_for(e)
  if e.type == "furnace" then return e.get_inventory(defines.inventory.furnace_source) end
  if e.type == "assembling-machine" then return e.get_inventory(defines.inventory.assembling_machine_input) end
  if e.type == "rocket-silo" then return e.get_inventory(defines.inventory.rocket_silo_input) end
  if e.type == "lab" then return e.get_inventory(defines.inventory.lab_input) end
end
local function transfer(source, target, name, n)
  assert(source and target, "Inventory is unavailable")
  n = math.min(n, count(source, name))
  assert(n > 0 and target.can_insert({name = name, count = 1, quality = "normal"}), "No items or destination is full")
  local removed = source.remove({name = name, count = n, quality = "normal"})
  local ok, inserted = pcall(function() return target.insert({name = name, count = removed, quality = "normal"}) end)
  if not ok then
    source.insert({name = name, count = removed, quality = "normal"})
    error("Destination rejected item transfer")
  end
  if inserted < removed then source.insert({name = name, count = removed - inserted, quality = "normal"}) end
  return "Transferred " .. inserted .. " " .. name
end
local function execute(p, s, a)
  local continuing = a.kind == "continue_task"
  if continuing then
    assert(s.task and s.task.id == a.task_id and s.task.status == "running", "Committed task is no longer active")
    a = s.task.action
  end
  if a.kind == "focus" then s.focus = a.name; return "Construction focus: " .. a.name end
  if a.kind == "stop" then disarm(p, s); return "Stopped" end
  if a.kind == "navigate" then
    if not continuing then begin_task(p, s, a) end
    update_task(p, s)
    if s.task.status ~= "running" then return s.task.result end
    a.until_tick = game.tick + a.ticks
    a.segment_position = position(p.position)
    if not a.path or not a.path[a.waypoint] then
      a.path, a.waypoint = nil, nil
      a.request = p.surface.request_path{bounding_box = p.character.prototype.collision_box,
      collision_mask = p.character.prototype.collision_mask, start = p.position, goal = a.position,
      force = p.force, radius = a.radius or 2, entity_to_ignore = p.character, can_open_gates = true,
      pathfind_flags = {cache = false, allow_paths_through_own_entities = false}}
    end
    s.active = a
    return "Committed to this destination until arrival; continuing the same path in short segments"
  end
  if a.kind == "wait" or a.kind == "walk" or a.kind == "mine" then
    if a.kind == "mine" then
      assert(a.entity and a.entity.valid and a.entity.surface == p.surface and a.entity.minable, "Mining target disappeared")
      if a.entity.type == "resource" then
        assert(distance(p.position, a.entity.position) <= p.resource_reach_distance, "Resource out of reach")
      else assert(p.can_reach_entity(a.entity), "Tree out of reach") end
      if not continuing then
        local t = begin_task(p, s, a)
        if a.entity.type == "resource" then
          t.item, t.quantity = a.entity.name, 10
          t.baseline = count(p.get_main_inventory(), t.item)
        end
      end
    end
    a.until_tick = game.tick + a.ticks
    s.active = a
    return "Started " .. a.kind .. " for " .. a.ticks .. " ticks"
  end
  if a.kind == "craft" then
    assert(p.force.recipes[a.recipe].enabled and p.crafting_queue_size == 0, "Recipe unavailable or crafting queue busy")
    local products = {}
    for _, product in ipairs(p.force.recipes[a.recipe].products or {}) do
      if product.type == "item" and product.amount and (not product.probability or product.probability == 1) then
        products[#products + 1] = {name = product.name, baseline = count(p.get_main_inventory(), product.name), amount = product.amount}
      end
    end
    local n = p.begin_crafting{count = a.count or 1, recipe = a.recipe, silent = true}
    assert(n > 0, "Missing ingredients")
    local t = begin_task(p, s, a)
    t.products = {}
    for _, product in ipairs(products) do
      t.products[#t.products + 1] = {name = product.name, target = product.baseline + product.amount * n}
    end
    return "Crafting " .. a.recipe
  end
  if a.kind == "build" then
    assert(distance(p.position, a.position) <= p.build_distance, "Build position out of reach")
    assert(p.clear_cursor(), "Clear the cursor manually first")
    local stack = p.get_main_inventory().find_item_stack({name = a.name, quality = "normal"})
    assert(stack and p.cursor_stack.transfer_stack(stack), "Building item is no longer in inventory")
    local before = p.cursor_stack.count
    local params = {position = a.position, direction = defines.direction[a.direction], build_mode = defines.build_mode.normal}
    local ok, err = pcall(function()
      assert(p.can_build_from_cursor(params), "Build position is blocked")
      p.build_from_cursor(params)
    end)
    local after = p.cursor_stack.valid_for_read and p.cursor_stack.count or 0
    p.clear_cursor()
    assert(ok, tostring(err))
    assert(after < before, "No building was placed")
    return "Built " .. a.name
  end
  if a.kind == "put" or a.kind == "take" then
    local e = entity_for(p, a)
    local inv = p.get_main_inventory()
    if a.kind == "put" then
      return transfer(inv, a.slot == "fuel" and e.get_fuel_inventory() or input_for(e), a.name, a.count)
    end
    local output = e.type == "container" and e.get_inventory(defines.inventory.chest) or e.get_output_inventory()
    return transfer(output, inv, a.name, a.count)
  end
  if a.kind == "recipe" then
    local e = entity_for(p, a)
    assert(input_for(e).is_empty() and e.get_output_inventory().is_empty(), "Assembler is no longer empty")
    for i = 1, #e.fluidbox do assert(not e.fluidbox[i] or e.fluidbox[i].amount < .01, "Drain fluids before changing recipe") end
    assert(p.force.recipes[a.recipe].enabled, "Recipe unavailable")
    e.set_recipe(a.recipe)
    return "Set recipe " .. a.recipe
  end
  if a.kind == "research" then
    assert(p.force.add_research(a.name), "Research unavailable")
    return "Selected research " .. a.name
  end
  if a.kind == "rotate" then
    assert(entity_for(p, a).rotate{by_player = p}, "This entity cannot be rotated")
    return "Rotated entity"
  end
  if a.kind == "launch" then
    assert(entity_for(p, a).launch_rocket(), "Rocket is not ready for launch")
    return "Rocket launch ordered"
  end
  error("Unsupported action")
end

script.on_event(defines.events.on_tick, function()
  if not storage.jev then return end
  for index, s in pairs(storage.jev.players) do
    if s.armed or s.active then
      local p = game.get_player(index)
      if not p or not p.valid or not p.connected or not p.character or p.driving
        or p.controller_type ~= defines.controllers.character or (s.lease and game.tick > s.lease) then
        disarm(p, s)
      else
        if game.tick % 15 == 0 then update_task(p, s) end
        if s.active then
        local a = s.active
        if game.tick >= a.until_tick then end_segment(p, s)
        elseif a.kind == "navigate" and a.path then
          local waypoint = a.path[a.waypoint]
          while waypoint and distance(p.position, waypoint.position) < .35 do
            a.waypoint = a.waypoint + 1
            waypoint = a.path[a.waypoint]
          end
          if waypoint then p.walking_state = {walking = true, direction = defines.direction[toward(p.position, waypoint.position)]}
          else end_segment(p, s) end
        elseif a.kind == "walk" then p.walking_state = {walking = true, direction = defines.direction[a.direction]}
        elseif a.kind == "mine" then
          if a.entity and a.entity.valid and a.entity.surface == p.surface then
            if p.selected ~= a.entity then p.selected = a.entity end
            p.mining_state = {mining = true, position = a.entity.position}
          else end_segment(p, s) end
        end
        end
      end
    end
  end
end)

if defines.events.on_script_path_request_finished then
  script.on_event(defines.events.on_script_path_request_finished, function(event)
    if not storage.jev then return end
    for index, s in pairs(storage.jev.players) do
      if s.active and s.active.request == event.id then
        if event.path then s.active.path, s.active.waypoint = event.path, 1
        else
          if s.task and not event.try_again_later then s.task.path_failures = s.task.path_failures + 1 end
          end_segment(game.get_player(index), s)
        end
        return
      end
    end
  end)
end

-- A newly joined player always starts unarmed, including when hosting a saved game.
script.on_event(defines.events.on_player_joined_game, function(event)
  local p = game.get_player(event.player_index)
  disarm(p, state_for(event.player_index))
end)

local function arm(p, s)
  disarm(p, s)
  s.armed, s.lease = true, game.tick + LEASE
  button(p, s)
  p.print("Jev armed for player index " .. p.index .. ". Start the controller within 60 game seconds. Click Jev: ON or use /jev-disable to stop.")
end
script.on_event(defines.events.on_gui_click, function(event)
  if not event.element or not event.element.valid or event.element.name ~= "jev-bridge-toggle" then return end
  local p = game.get_player(event.player_index)
  local s = state_for(event.player_index)
  if s.armed then disarm(p, s); return end
  local ok, err = pcall(function() arm(valid_player(event.player_index), s) end)
  if not ok then p.print(tostring(err)) end
end)

-- Arming is exclusively a command from the player who will be controlled.
commands.add_command("jev-enable", "Allow Jev to control your character for a controller run.", function(cmd)
  if not cmd.player_index then rcon.print("Enable from the player's in-game console using /jev-enable"); return end
  local ok, err = pcall(function()
    local p = valid_player(cmd.player_index)
    local s = state_for(p.index)
    arm(p, s)
  end)
  if not ok then game.get_player(cmd.player_index).print(tostring(err)) end
end)
commands.add_command("jev-disable", "Immediately stop and disable Jev control of your character.", function(cmd)
  if not cmd.player_index then return end
  local p = game.get_player(cmd.player_index)
  disarm(p, state_for(p.index))
  p.print("Jev disabled. Previously queued crafting and factory production may finish normally.")
end)
commands.add_command("jev-ping", "Controller reply barrier.", function(cmd)
  if not cmd.player_index then rcon.print(cmd.parameter or "pong") end
end)

local function command(name, handler)
  commands.add_command("jev-" .. name, "Local Jev controller protocol.", function(cmd)
    if cmd.player_index then game.get_player(cmd.player_index).print("This command is for the local controller."); return end
    local ok, result = pcall(function()
      local args = helpers.json_to_table(cmd.parameter or "{}")
      assert(type(args) == "table", "Expected JSON object")
      return handler(args)
    end)
    rcon.print(helpers.table_to_json(ok and result or {ok = false, error = tostring(result)}))
  end)
end
command("players", function()
  local players = {}
  for _, p in pairs(game.connected_players) do
    players[#players + 1] = {index = p.index, name = p.name, has_character = p.character ~= nil}
  end
  return {ok = true, protocol = PROTOCOL, players = players}
end)
command("observe", function(args)
  assert(args.recipe_priorities == nil or type(args.recipe_priorities) == "table", "Invalid recipe priorities")
  assert(args.research_priority == nil or type(args.research_priority) == "string", "Invalid research priority")
  return observe(valid_player(args.player), args.recipe_priorities, args.research_priority)
end)
command("catalog", function(args) return catalog(valid_player(args.player)) end)
command("start", function(args)
  local p = valid_player(args.player)
  local s = state_for(p.index)
  assert(s.armed and s.lease and game.tick <= s.lease, "Type /jev-enable in Factorio before starting live control")
  assert(type(args.session) == "string" and #args.session == 32 and args.session:match("^[0-9a-f]+$"), "Invalid session")
  assert(not s.session or s.session == args.session, "Another controller owns this player")
  s.session, s.lease = args.session, game.tick + LEASE
  return {ok = true}
end)
command("act", function(args)
  local p = valid_player(args.player)
  local s = state_for(p.index)
  assert(s.armed and s.session and s.session == args.session and game.tick <= s.lease, "Control is not armed for this session")
  assert(not s.active, "Previous timed action is still running")
  local snap = s.snapshot
  assert(snap and snap.id == args.observation and game.tick - snap.tick <= 600, "Observation expired; request a new one")
  assert(snap.surface == p.surface.index and distance(snap.position, p.position) < 1, "Player moved since observation")
  local action = snap.actions[args.action]
  assert(action, "Action was not offered by this observation")
  assert(not s.task or s.task.status ~= "running" or action.kind == "continue_task" or action.kind == "stop",
    "Finish the committed task before choosing a different action")
  if action.kind ~= "continue_task" then s.task = nil end
  s.snapshot = nil -- one use, including failed actions; never replay a mutation.
  s.lease = game.tick + LEASE
  local ok, result = pcall(execute, p, s, action)
  if not ok then
    finish_task(p, s, "blocked", tostring(result))
    halt(p, s)
  end
  return {ok = true, result = ok and result or ("Action failed: " .. tostring(result)), action_succeeded = ok, task = task_view(s),
    busy_ticks = s.active and math.max(0, s.active.until_tick - game.tick) or 0}
end)
command("stop", function(args)
  local p = game.get_player(args.player)
  local s = state_for(args.player)
  assert(s.session == args.session, "Session does not own this player")
  disarm(p, s)
  return {ok = true}
end)
