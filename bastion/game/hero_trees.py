from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "hero_trees.json"


@dataclass(frozen=True)
class HeroNodeDefinition:
    node_id: str
    branch_id: str
    tier: int
    name: str
    description: str
    repeatable: bool
    effects: dict[str, float]
    ability_id: str | None = None
    requires: str | None = None
    cost: int = 1

    @property
    def max_rank(self) -> int | None:
        return None if self.repeatable else 1


@dataclass(frozen=True)
class HeroBranchDefinition:
    branch_id: str
    name: str
    nodes: tuple[HeroNodeDefinition, ...]


@dataclass(frozen=True)
class HeroTreeDefinition:
    troop_kind: str
    branches: tuple[HeroBranchDefinition, ...]

    def node(self, node_id: str) -> HeroNodeDefinition | None:
        for branch in self.branches:
            for node in branch.nodes:
                if node.node_id == node_id:
                    return node
        return None

    def nodes(self) -> tuple[HeroNodeDefinition, ...]:
        return tuple(node for branch in self.branches for node in branch.nodes)


def load_hero_trees(path: Path | None = None) -> tuple[int, dict[str, HeroTreeDefinition]]:
    path = DATA_PATH if path is None else path
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("Hero tree data must be a JSON object.")

    interval = max(1, int(raw.get("orb_interval_levels", 3)))
    tree_records = raw.get("trees", {})
    if not isinstance(tree_records, dict):
        raise ValueError("Hero tree data requires a trees object.")

    trees: dict[str, HeroTreeDefinition] = {}
    for troop_kind, tree_record in tree_records.items():
        if not isinstance(tree_record, dict):
            continue
        branches: list[HeroBranchDefinition] = []
        branch_records = tree_record.get("branches", [])
        if not isinstance(branch_records, list):
            continue
        for branch_record in branch_records:
            branch = _parse_branch(str(troop_kind), branch_record)
            if branch is not None:
                branches.append(branch)
        if branches:
            trees[str(troop_kind)] = HeroTreeDefinition(str(troop_kind), tuple(branches))
    return interval, trees


def tree_for_troop(kind: str) -> HeroTreeDefinition | None:
    return HERO_TREES.get(kind)


def node_for_troop(kind: str, node_id: str) -> HeroNodeDefinition | None:
    tree = tree_for_troop(kind)
    return None if tree is None else tree.node(node_id)


def troop_has_tree(kind: str) -> bool:
    return kind in HERO_TREES


def _parse_branch(troop_kind: str, raw: Any) -> HeroBranchDefinition | None:
    if not isinstance(raw, dict):
        return None
    branch_id = str(raw.get("id", "")).strip()
    if not branch_id:
        return None
    branch_name = str(raw.get("name", branch_id.replace("_", " ").title()))
    nodes: list[HeroNodeDefinition] = []
    previous_id: str | None = None
    node_records = raw.get("nodes", [])
    if not isinstance(node_records, list):
        return None
    for index, node_record in enumerate(node_records, start=1):
        node = _parse_node(troop_kind, branch_id, index, previous_id, node_record)
        if node is None:
            continue
        nodes.append(node)
        previous_id = node.node_id
    return HeroBranchDefinition(branch_id, branch_name, tuple(nodes)) if nodes else None


def _parse_node(
    troop_kind: str,
    branch_id: str,
    tier: int,
    previous_id: str | None,
    raw: Any,
) -> HeroNodeDefinition | None:
    if not isinstance(raw, dict):
        return None
    local_id = str(raw.get("id", f"node_{tier}")).strip()
    if not local_id:
        return None
    effects = _parse_effects(raw.get("effects", {}))
    ability_id = raw.get("ability_id")
    node_id = f"{troop_kind}:{branch_id}:{local_id}"
    return HeroNodeDefinition(
        node_id=node_id,
        branch_id=branch_id,
        tier=tier,
        name=str(raw.get("name", f"Notch {tier}")),
        description=str(raw.get("description", "")),
        repeatable=bool(raw.get("repeatable", False)),
        effects=effects,
        ability_id=str(ability_id) if ability_id else None,
        requires=previous_id,
        cost=max(1, int(raw.get("cost", 1))),
    )


def _parse_effects(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    effects: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(value, (int, float)):
            effects[str(key)] = float(value)
    return effects


HERO_ORB_LEVEL_INTERVAL, HERO_TREES = load_hero_trees()
