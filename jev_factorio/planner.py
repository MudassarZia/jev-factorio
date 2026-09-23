"""Compact rocket milestones derived from the current game's recipe/technology data."""
from collections import Counter

ROCKET_GOAL = "Autonomously develop a factory on Nauvis, research the rocket silo, build and fuel it, and launch a rocket."


class RocketPlanner:
    def __init__(self, catalog):
        self.recipes = {r["name"]: r for r in catalog.get("recipes", [])}
        self.product_recipes = {}
        for recipe in self.recipes.values():
            for product in recipe.get("products", []):
                if product.get("type", "item") in {"item", "fluid"}:
                    self.product_recipes.setdefault(product["name"], recipe)
        for product, recipe in {"petroleum-gas": "basic-oil-processing", "heavy-oil": "advanced-oil-processing",
                                "light-oil": "advanced-oil-processing", "solid-fuel": "solid-fuel-from-light-oil"}.items():
            if recipe in self.recipes:
                self.product_recipes[product] = self.recipes[recipe]
        self.technologies = {t["name"]: t for t in catalog.get("technologies", [])}
        self.order = []
        seen = set()
        def visit(name):
            if name in seen:
                return
            seen.add(name)
            technology = self.technologies.get(name, {})
            for prerequisite in sorted(technology.get("prerequisites", [])):
                visit(prerequisite)
            self.order.append(name)
        visit("rocket-silo")

    def context(self, state):
        researched = set(state.get("researched", []))
        remaining = [name for name in self.order if name not in researched]
        machines = state.get("known_machines") or state.get("machines") or []
        counts = Counter(m["name"] for m in machines)
        research = state.get("research")
        next_research = research or (remaining[0] if remaining else None)
        technology = self.technologies.get(next_research, {})
        packs = [i["name"] for i in technology.get("ingredients", [])]
        if state.get("rockets_launched", 0):
            milestone, targets = "Rocket launched; the objective is complete.", []
        elif not counts["stone-furnace"]:
            milestone, targets = "Bootstrap smelting: place a furnace; mine coal and iron/copper ore; fuel, load and collect plates.", ["stone-furnace", "iron-plate", "copper-plate"]
        elif technology.get("trigger"):
            trigger = technology["trigger"]
            item = trigger.get("item", {})
            item_name = item.get("name") if isinstance(item, dict) else item
            milestone = f"Unlock {next_research} by satisfying its game trigger: {trigger}. This unlock comes before later factory construction."
            targets = [item_name] if item_name else ["iron-plate", "copper-plate"]
        elif not (counts["offshore-pump"] and counts["boiler"] and counts["steam-engine"]):
            milestone = "Build reliable steam power near water: offshore pump -> boiler water input, boiler steam output -> steam engine, and a pole covering the engine. Fuel the boiler. Use aligned fluid-port construction choices; connect any gaps with pipes."
            targets = ["offshore-pump", "boiler", "steam-engine", "small-electric-pole", "pipe"]
        elif not counts["lab"]:
            milestone, targets = "Build and power a lab within pole coverage. Craft science, insert it and select the next research.", ["lab", "small-electric-pole", "automation-science-pack"]
        elif next_research:
            milestone = f"Advance rocket prerequisites. Research {next_research}; continuously supply its science packs to powered labs. Expand mining, smelting, power and assemblers as needed."
            if technology.get("trigger"):
                milestone += " This research uses an in-game trigger instead of lab science: " + str(technology["trigger"])
            targets = packs or ["electronic-circuit", "iron-plate", "copper-plate"]
        elif not counts["rocket-silo"]:
            milestone, targets = "Craft and place the rocket silo on clear ground, then connect it to power.", ["rocket-silo", "medium-electric-pole"]
        else:
            milestone = "Supply the powered silo with rocket-part ingredients. When ready, choose Launch rocket. A satellite is unnecessary for the goal of launching a rocket."
            targets = ["rocket-part"]
        recipes = []
        seen = set()
        def expand(name, depth=0):
            if name in seen or depth > 6 or len(recipes) >= 40:
                return
            seen.add(name)
            recipe = self.recipes.get(name) or self.product_recipes.get(name)
            if recipe:
                recipes.append(recipe)
                for ingredient in recipe.get("ingredients", []):
                    expand(ingredient["name"], depth + 1)
        for name in targets:
            expand(name)
        return {"milestone": milestone, "next_research": next_research,
                "science_packs": packs, "research_units": technology.get("count"),
                "research_trigger": technology.get("trigger"),
                "remaining_research": remaining[:12], "factory_counts": dict(counts),
                "recipes_for_milestone": recipes,
                "working_method": "Use existing inventory first. Take finished products from machines. Handcraft intermediates when possible. Mine resources and smelt plates when inputs are missing. Engine units, chemicals, fluids and rocket parts require machines. Expand construction focus to get precise placement choices. Use normal-quality items. Return to known machines when out of reach. Keep machines fueled/powered. Never treat selecting an action as proof of completed production."}
