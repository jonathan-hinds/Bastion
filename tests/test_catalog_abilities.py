import random
import unittest

import pygame

from bastion.game.abilities import (
    AggroFadeOnAbilityUsePassive,
    ArcaneFocusAbility,
    AttackRangeSlowAuraPassive,
    ConsecrationAbility,
    DamageBlockAbility,
    DragonBreathAbility,
    ElectricJoltPassive,
    FrostNovaAbility,
    GuardianInterceptAbility,
    HolyAuraPassive,
    InnerFireRetaliationAbility,
    MissingHealthDamageBoostPassive,
    OutOfCombatRegenerationPassive,
    SiphonLifeAbility,
    VanishAbility,
    VisionMarkConeAbility,
    WarMachineAbility,
    catalog_ability_definitions,
    configure_troop_abilities,
)
from bastion.game.aggro import AggroComponent, melee_aggro_profile
from bastion.game.hero_trees import HERO_ORB_LEVEL_INTERVAL, HERO_TREES
from bastion.game.state import GameState
from bastion.game.tower_defs import xp_needed
from bastion.game.units import Troop, troop_ability_cards


class DummyEnemy:
    radius = 8.0
    mass = 1.0
    faction_type = "living"
    resistances = {"physical": 1.0, "fire": 1.0, "ice": 1.0, "lightning": 1.0, "holy": 1.0}

    def __init__(self, x=80.0, y=0.0, health=100.0):
        self.pos = pygame.Vector2(x, y)
        self.health = health
        self.max_health = health
        self.alive = True
        self.vel = pygame.Vector2()
        self.stun_time = 0.0
        self.slow_time = 0.0
        self.slow_multiplier = 1.0
        self.attack_slow_multiplier = 1.0
        self.burn_time = 0.0
        self.burn_dps = 0.0
        self.burn_owner = None
        self.last_hit_by = None
        self.reward = 1
        self.aggro = AggroComponent(self, melee_aggro_profile())
        self.damage_vulnerability_time = 0.0
        self.damage_vulnerability_multiplier = 1.0
        self.damage_vulnerability_source_classes = set()

    def take_damage(self, amount, owner=None):
        self.health -= amount
        self.last_hit_by = owner
        return self.health <= 0

    def apply_knockback(self, amount, source_pos):
        return

    def apply_stun(self, duration):
        self.stun_time = max(self.stun_time, duration)

    def apply_slow(self, multiplier, duration, attack_multiplier=None):
        self.slow_multiplier = min(self.slow_multiplier, multiplier)
        self.slow_time = max(self.slow_time, duration)
        if attack_multiplier is not None:
            self.attack_slow_multiplier = min(self.attack_slow_multiplier, attack_multiplier)

    def apply_burn(self, dps, duration, owner, spread_radius=0.0, spread_falloff=0.5, can_spread=True):
        self.burn_dps = max(self.burn_dps, dps)
        self.burn_time = max(self.burn_time, duration)
        self.burn_owner = owner

    def apply_damage_vulnerability(self, multiplier, duration, source_classes=("troop", "tower")):
        self.damage_vulnerability_multiplier = max(self.damage_vulnerability_multiplier, multiplier)
        self.damage_vulnerability_time = max(self.damage_vulnerability_time, duration)
        self.damage_vulnerability_source_classes.update(source_classes)

    def damage_taken_multiplier(self, source):
        if self.damage_vulnerability_time <= 0:
            return 1.0
        source_class = "tower" if source.__class__.__name__ == "Tower" else getattr(source, "target_class", "")
        return self.damage_vulnerability_multiplier if source_class in self.damage_vulnerability_source_classes else 1.0


def clean_game():
    game = GameState.__new__(GameState)
    game.fog = None
    game.active_item_buffs = []
    game.core_targets = []
    game.troops = []
    game.enemies = []
    game.towers = []
    game.buildings = []
    game.gold = 0
    game.projectiles = []
    game.enemy_projectiles = []
    game.particles = []
    game.damage_pulses = []
    game.ability_zones = []
    game.beams = []
    game.texts = []
    game.spatial_cell_size = 64
    game._enemy_bins = {}
    game._troop_bins = {}
    game._spatial_ready = False
    return game


def troop(kind="warrior", x=0.0, y=0.0):
    unit = Troop(kind, pygame.Vector2(x, y), pygame.Vector2(x, y))
    unit.abilities.clear()
    unit.cooldown = 0.0
    return unit


class CatalogAbilityTests(unittest.TestCase):
    def test_catalog_registry_is_unassigned(self):
        definitions = catalog_ability_definitions()
        self.assertEqual(set(definitions), {1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18})
        ability_ids = [definition.ability_id for definition in definitions.values()]
        self.assertEqual(len(ability_ids), len(set(ability_ids)))

        default_ids = {card.ability_id for card in troop_ability_cards("warrior")}
        self.assertFalse(default_ids.intersection(ability_ids))

    def test_bloodied_fury_scales_outgoing_damage(self):
        game = clean_game()
        owner = troop()
        owner.max_health = 100.0
        owner.health = 40.0
        owner.abilities.add(MissingHealthDamageBoostPassive(owner))
        enemy = DummyEnemy(health=100.0)
        game.troops = [owner]
        game.enemies = [enemy]

        game.damage_enemy(enemy, 10.0, owner, quiet=True)

        self.assertAlmostEqual(enemy.health, 84.0)

    def test_guardian_intercept_redirects_fatal_damage_and_aggro(self):
        game = clean_game()
        ally = troop("cleric", 0, 0)
        guardian = troop("warrior", 24, 0)
        ally.health = 5.0
        guardian.health = 100.0
        guardian.max_health = 100.0
        guardian.abilities.add(GuardianInterceptAbility(guardian))
        enemy = DummyEnemy(12, 0)
        enemy.aggro.add_threat(ally, 40.0, "damage")
        enemy.aggro.current_target = ally
        game.troops = [ally, guardian]
        game.enemies = [enemy]

        game.damage_friendly(ally, 10.0, source=enemy, source_pos=enemy.pos)

        self.assertTrue(ally.alive)
        self.assertEqual(ally.health, 5.0)
        self.assertAlmostEqual(guardian.health, 90.0)
        self.assertIs(enemy.aggro.current_target, guardian)
        self.assertIn(guardian, enemy.aggro.threat)

    def test_perfect_guard_blocks_incoming_damage(self):
        game = clean_game()
        owner = troop()
        ability = DamageBlockAbility(owner)
        owner.abilities.add(ability)
        owner.health = 80.0
        game.troops = [owner]

        game.damage_friendly(owner, 50.0, source=DummyEnemy())

        self.assertEqual(owner.health, 80.0)
        self.assertGreater(ability.block_remaining, 0.0)
        self.assertGreater(ability.cooldown_remaining, 0.0)

    def test_hunters_mark_cone_increases_troop_damage(self):
        game = clean_game()
        owner = troop("archer", 0, 0)
        target = DummyEnemy(120, 0)
        behind = DummyEnemy(-80, 0)
        owner.target = target
        ability = VisionMarkConeAbility(owner)
        owner.abilities.add(ability)
        game.troops = [owner]
        game.enemies = [target, behind]

        self.assertTrue(ability.activate(game))
        game.damage_enemy(target, 10.0, owner, quiet=True)
        game.damage_enemy(behind, 10.0, owner, quiet=True)

        self.assertAlmostEqual(target.health, 88.0)
        self.assertAlmostEqual(behind.health, 90.0)

    def test_regeneration_and_holy_aura_heal_over_time(self):
        game = clean_game()
        owner = troop("cleric", 0, 0)
        ally = troop("warrior", 30, 0)
        owner.max_health = 120.0
        owner.health = 60.0
        ally.max_health = 120.0
        ally.health = 60.0
        regen = OutOfCombatRegenerationPassive(owner)
        aura = HolyAuraPassive(owner)
        aura.timer = 0.0
        owner.abilities.add(regen)
        owner.abilities.add(aura)
        game.troops = [owner, ally]

        regen.update(12.0, game)
        aura.update(0.5, game)

        self.assertGreater(owner.health, 72.0)
        self.assertGreater(ally.health, 60.0)

    def test_consecration_persistent_zone_damages_and_heals(self):
        game = clean_game()
        owner = troop("cleric", 0, 0)
        ally = troop("warrior", 20, 0)
        ally.health = 50.0
        enemy = DummyEnemy(30, 0)
        ability = ConsecrationAbility(owner)
        owner.abilities.add(ability)
        game.troops = [owner, ally]
        game.enemies = [enemy]

        self.assertTrue(ability.activate(game))
        self.assertEqual(len(game.ability_zones), 1)
        game.ability_zones[0].update(1.0, game)

        self.assertLess(enemy.health, 100.0)
        self.assertGreater(ally.health, 50.0)

    def test_inner_fire_and_static_jolt_trigger_when_struck(self):
        game = clean_game()
        owner = troop("wizard", 0, 0)
        enemy = DummyEnemy(25, 0)
        owner.abilities.add(InnerFireRetaliationAbility(owner))
        owner.abilities.add(ElectricJoltPassive(owner))
        game.troops = [owner]
        game.enemies = [enemy]

        game.damage_friendly(owner, 5.0, source=enemy, source_pos=enemy.pos)

        self.assertGreater(enemy.burn_time, 0.0)
        self.assertGreaterEqual(enemy.stun_time, 2.0)

    def test_vanish_clears_aggro_and_aggro_fade_reduces_threat(self):
        game = clean_game()
        owner = troop("archer", 0, 0)
        enemy = DummyEnemy(80, 0)
        vanish = VanishAbility(owner)
        owner.abilities.add(vanish)
        enemy.aggro.add_threat(owner, 110.0, "damage")
        enemy.aggro.current_target = owner
        game.troops = [owner]
        game.enemies = [enemy]

        vanish.update(0.1, game)

        self.assertGreater(owner.stealth_time, 0.0)
        self.assertNotIn(owner, enemy.aggro.threat)
        self.assertNotIn(owner, list(game.aggro_candidates(enemy.pos, 200.0)))

        owner.stealth_time = 0.0
        owner.abilities.clear()
        fade = AggroFadeOnAbilityUsePassive(owner)
        block = DamageBlockAbility(owner)
        owner.abilities.add(fade)
        owner.abilities.add(block)
        enemy.aggro.add_threat(owner, 100.0, "damage")
        before = enemy.aggro.threat[owner].score

        block.activate(game)
        fade.update(1.0, game)

        self.assertLess(enemy.aggro.threat[owner].score, before)

    def test_war_machine_siphon_life_and_arcane_focus_damage_channels(self):
        random.seed(1)
        game = clean_game()
        owner = troop("wizard", 0, 0)
        enemy = DummyEnemy(70, 0, health=160.0)
        owner.target = enemy
        owner.health = 40.0
        owner.max_health = 100.0
        game.troops = [owner]
        game.enemies = [enemy]

        war = WarMachineAbility(owner, duration=1.0, cooldown=1.0, fire_interval=0.1, accuracy=1.0)
        owner.abilities.add(war)
        self.assertTrue(war.activate(game))
        war.update(0.3, game)
        self.assertLess(enemy.health, 160.0)

        siphon = SiphonLifeAbility(owner, duration=1.0, cooldown=1.0, tick_interval=0.1)
        owner.abilities.add(siphon)
        self.assertTrue(siphon.activate(game))
        siphon.update(0.2, game)
        self.assertGreater(owner.health, 40.0)

        focus = ArcaneFocusAbility(owner, duration=0.5, cooldown=1.0, tick_interval=0.1)
        owner.abilities.add(focus)
        before = enemy.health
        self.assertTrue(focus.activate(game, enemy))
        focus.update(0.3, game)
        self.assertLess(enemy.health, before)

    def test_frost_nova_dragon_breath_and_slowing_presence(self):
        game = clean_game()
        owner = troop("wizard", 0, 0)
        enemy = DummyEnemy(40, 0)
        owner.target = enemy
        game.troops = [owner]
        game.enemies = [enemy]

        nova = FrostNovaAbility(owner)
        owner.abilities.add(nova)
        self.assertTrue(nova.activate(game))
        self.assertGreaterEqual(enemy.stun_time, 2.0)
        self.assertGreater(enemy.vel.length(), 0.0)

        breath = DragonBreathAbility(owner)
        owner.abilities.add(breath)
        self.assertTrue(breath.activate(game, enemy))
        self.assertGreater(enemy.burn_time, 0.0)

        enemy.slow_multiplier = 1.0
        slow = AttackRangeSlowAuraPassive(owner)
        slow.timer = 0.0
        owner.abilities.add(slow)
        slow.update(0.22, game)
        self.assertLess(enemy.slow_multiplier, 1.0)


class HeroTreeTests(unittest.TestCase):
    def test_hero_tree_data_covers_combat_troops(self):
        expected = {"warrior", "archer", "cleric", "engineer", "wizard", "rune_mage"}
        self.assertEqual(set(HERO_TREES), expected)
        self.assertEqual(HERO_ORB_LEVEL_INTERVAL, 3)
        self.assertNotIn("grunt", HERO_TREES)
        valid_ability_ids = {definition.ability_id for definition in catalog_ability_definitions().values()}
        valid_ability_ids.add("archer_multi_shot")
        for tree in HERO_TREES.values():
            self.assertEqual(len(tree.branches), 3)
            for branch in tree.branches:
                self.assertEqual(len(branch.nodes), 3)
                self.assertTrue(branch.nodes[0].repeatable)
                self.assertTrue(branch.nodes[1].repeatable)
                self.assertFalse(branch.nodes[2].repeatable)
                self.assertIn(branch.nodes[2].ability_id, valid_ability_ids)

    def test_orbs_unlock_repeatable_chained_nodes(self):
        unit = Troop("warrior", pygame.Vector2(0, 0), pygame.Vector2(0, 0))
        tree = HERO_TREES["warrior"]
        node_1 = tree.branches[0].nodes[0]
        node_2 = tree.branches[0].nodes[1]

        unit.xp = xp_needed(unit.level)
        self.assertTrue(unit.level_up())
        self.assertEqual(unit.level, 2)
        self.assertEqual(unit.hero_orbs, 0)

        unit.xp = xp_needed(unit.level)
        self.assertTrue(unit.level_up())
        self.assertEqual(unit.level, 3)
        self.assertEqual(unit.hero_orbs, 1)

        self.assertFalse(unit.can_purchase_hero_node(node_2.node_id))
        self.assertTrue(unit.purchase_hero_node(node_1.node_id))
        self.assertEqual(unit.hero_orbs, 0)
        self.assertEqual(unit.hero_node_rank(node_1.node_id), 1)

        unit.hero_orbs = 2
        self.assertTrue(unit.purchase_hero_node(node_1.node_id))
        self.assertTrue(unit.purchase_hero_node(node_2.node_id))
        self.assertEqual(unit.hero_node_rank(node_1.node_id), 2)
        self.assertEqual(unit.hero_node_rank(node_2.node_id), 1)

    def test_archer_multishot_starts_locked_behind_hero_tree(self):
        unit = Troop("archer", pygame.Vector2(0, 0), pygame.Vector2(0, 0))
        cards = unit.abilities.cards()
        self.assertIn("Single Shot", {card.name for card in cards})
        self.assertNotIn("Multi Shot", {card.name for card in cards})

        siege = HERO_TREES["archer"].branches[2]
        for node in siege.nodes:
            unit.hero_node_ranks[node.node_id] = 1
        configure_troop_abilities(unit)

        cards = unit.abilities.cards()
        self.assertIn("Multi Shot", {card.name for card in cards})
        self.assertNotIn("Single Shot", {card.name for card in cards})

    def test_armor_reduces_incoming_troop_damage(self):
        unit = Troop("warrior", pygame.Vector2(0, 0), pygame.Vector2(0, 0))
        armor_node = HERO_TREES["warrior"].branches[1].nodes[1]
        unit.hero_node_ranks[armor_node.node_id] = 1

        reduced = unit.reduce_damage_by_armor(100.0)

        self.assertLess(reduced, 100.0)
        self.assertGreater(reduced, 85.0)


if __name__ == "__main__":
    unittest.main()
