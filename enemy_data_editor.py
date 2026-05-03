from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_PATH = Path(__file__).resolve().parent / "bastion" / "data" / "enemies.json"
STAT_FIELDS = ("health", "speed", "radius", "reward", "damage", "accel", "mass", "attack_range", "fire_rate", "projectile_speed")
RESISTANCE_FIELDS = ("physical", "fire", "ice", "lightning", "holy")
TYPE_OPTIONS = ("goblin", "undead", "human")
ROLE_OPTIONS = ("melee", "ranged")
SHAPE_OPTIONS = ("diamond", "square", "octagon", "cross_circle", "circle")


def load_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": 1, "enemies": []}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return {"schema": 1, "enemies": data}
    if not isinstance(data, dict):
        raise ValueError("Enemy data must be a JSON object or list.")
    data.setdefault("schema", 1)
    data.setdefault("enemies", [])
    if not isinstance(data["enemies"], list):
        raise ValueError("'enemies' must be a list.")
    return data


def save_document(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")


class EnemyDataEditor:
    def __init__(self, root, data_path: Path) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.data_path = data_path
        self.document = load_document(data_path)
        self.current_index: int | None = None
        self.dirty = False
        self.loading = False

        root.title("Bastion Enemy Data")
        root.geometry("920x700")

        self.listbox = tk.Listbox(root, width=24, exportselection=False)
        self.listbox.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 6), pady=10)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)

        panel = ttk.Frame(root)
        panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10), pady=10)

        self.vars: dict[str, Any] = {}
        self._build_form(panel)
        self._build_buttons(panel)
        self.refresh_list()
        if self.document["enemies"]:
            self.listbox.selection_set(0)
            self.load_index(0)

    def _build_form(self, panel) -> None:
        ttk = self.ttk
        row = 0
        for field in ("id", "name", "type", "combat_role", "shape", "tags"):
            ttk.Label(panel, text=field.upper()).grid(row=row, column=0, sticky="w", padx=4, pady=4)
            if field == "type":
                widget = ttk.Combobox(panel, values=TYPE_OPTIONS, state="readonly")
            elif field == "combat_role":
                widget = ttk.Combobox(panel, values=ROLE_OPTIONS, state="readonly")
            elif field == "shape":
                widget = ttk.Combobox(panel, values=SHAPE_OPTIONS, state="readonly")
            else:
                widget = ttk.Entry(panel)
            widget.grid(row=row, column=1, sticky="ew", padx=4, pady=4)
            self._bind_dirty(widget)
            self.vars[field] = widget
            row += 1

        ttk.Label(panel, text="STATS").grid(row=row, column=0, sticky="w", padx=4, pady=(14, 4))
        row += 1
        for field in STAT_FIELDS:
            ttk.Label(panel, text=field).grid(row=row, column=0, sticky="w", padx=4, pady=3)
            widget = ttk.Entry(panel)
            widget.grid(row=row, column=1, sticky="ew", padx=4, pady=3)
            self._bind_dirty(widget)
            self.vars[f"stats.{field}"] = widget
            row += 1

        ttk.Label(panel, text="RESISTANCES").grid(row=row, column=0, sticky="w", padx=4, pady=(14, 4))
        row += 1
        for field in RESISTANCE_FIELDS:
            ttk.Label(panel, text=field).grid(row=row, column=0, sticky="w", padx=4, pady=3)
            widget = ttk.Entry(panel)
            widget.grid(row=row, column=1, sticky="ew", padx=4, pady=3)
            self._bind_dirty(widget)
            self.vars[f"resistances.{field}"] = widget
            row += 1

        panel.columnconfigure(1, weight=1)

    def _bind_dirty(self, widget) -> None:
        widget.bind("<KeyRelease>", self.mark_dirty, add="+")
        widget.bind("<<ComboboxSelected>>", self.mark_dirty, add="+")

    def _build_buttons(self, panel) -> None:
        ttk = self.ttk
        buttons = ttk.Frame(panel)
        buttons.grid(row=99, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        ttk.Button(buttons, text="Create Enemy", command=self.new_enemy).pack(side="left", padx=4)
        ttk.Button(buttons, text="Duplicate", command=self.duplicate_current).pack(side="left", padx=4)
        ttk.Button(buttons, text="Apply Edit", command=self.apply_current).pack(side="left", padx=4)
        ttk.Button(buttons, text="Save JSON", command=self.save_file).pack(side="left", padx=4)
        ttk.Button(buttons, text="Delete", command=self.delete_current).pack(side="left", padx=4)
        ttk.Button(buttons, text="Reload", command=self.reload_file).pack(side="left", padx=4)
        self.status = ttk.Label(panel, text=f"Editing {self.data_path}")
        self.status.grid(row=100, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def mark_dirty(self, _event=None) -> None:
        if self.loading:
            return
        self.dirty = True
        self.status.configure(text="Unsaved form changes")

    def refresh_list(self) -> None:
        self.listbox.delete(0, self.tk.END)
        for enemy in self.document["enemies"]:
            self.listbox.insert(self.tk.END, f"{enemy.get('id', '<new>')}  {enemy.get('name', '')}")

    def on_select(self, _event=None) -> None:
        selection = self.listbox.curselection()
        if selection:
            new_index = int(selection[0])
            if self.current_index is not None and new_index != self.current_index and self.dirty:
                if not self.apply_current(show_status=False):
                    self.listbox.selection_clear(0, self.tk.END)
                    self.listbox.selection_set(self.current_index)
                    return
            self.load_index(new_index)

    def load_index(self, index: int) -> None:
        if index < 0 or index >= len(self.document["enemies"]):
            return
        self.loading = True
        self.current_index = index
        enemy = self.document["enemies"][index]
        stats = enemy.get("stats", {})
        resistances = enemy.get("resistances", {})
        values = {
            "id": enemy.get("id", ""),
            "name": enemy.get("name", ""),
            "type": enemy.get("type", "goblin"),
            "combat_role": enemy.get("combat_role", "melee"),
            "shape": enemy.get("shape", "circle"),
            "tags": ", ".join(enemy.get("tags", [])),
        }
        for field in STAT_FIELDS:
            values[f"stats.{field}"] = stats.get(field, "")
        for field in RESISTANCE_FIELDS:
            values[f"resistances.{field}"] = resistances.get(field, 1.0)
        for key, widget in self.vars.items():
            self.set_widget_value(widget, values.get(key, ""))
        self.dirty = False
        self.loading = False
        self.status.configure(text=f"Loaded {enemy.get('id', '<new>')}")

    def set_widget_value(self, widget, value: object) -> None:
        if hasattr(widget, "set"):
            widget.set(str(value))
            return
        widget.delete(0, self.tk.END)
        widget.insert(0, str(value))

    def read_form(self) -> dict[str, Any]:
        enemy = {
            "id": self.vars["id"].get().strip(),
            "name": self.vars["name"].get().strip(),
            "type": self.vars["type"].get().strip() or "goblin",
            "combat_role": self.vars["combat_role"].get().strip() or "melee",
            "shape": self.vars["shape"].get().strip() or "circle",
            "tags": [tag.strip() for tag in self.vars["tags"].get().split(",") if tag.strip()],
            "stats": {},
            "resistances": {},
        }
        if not enemy["id"]:
            raise ValueError("Enemy id is required.")
        if not enemy["name"]:
            enemy["name"] = enemy["id"].replace("_", " ").title()
        for field in STAT_FIELDS:
            try:
                enemy["stats"][field] = float(self.vars[f"stats.{field}"].get() or 0)
            except ValueError as exc:
                raise ValueError(f"{field} must be a number.") from exc
        for field in RESISTANCE_FIELDS:
            try:
                enemy["resistances"][field] = float(self.vars[f"resistances.{field}"].get() or 1)
            except ValueError as exc:
                raise ValueError(f"{field} resistance must be a number.") from exc
        return enemy

    def apply_current(self, show_status: bool = True) -> bool:
        try:
            enemy = self.read_form()
        except ValueError as exc:
            self.status.configure(text=str(exc))
            return False
        duplicate_index = self.find_enemy_id(enemy["id"])
        if duplicate_index is not None and duplicate_index != self.current_index:
            self.status.configure(text=f"Enemy id '{enemy['id']}' already exists.")
            return False
        if self.current_index is None:
            self.document["enemies"].append(enemy)
            self.current_index = len(self.document["enemies"]) - 1
        else:
            self.document["enemies"][self.current_index] = enemy
        self.refresh_list()
        self.listbox.selection_clear(0, self.tk.END)
        self.listbox.selection_set(self.current_index)
        self.dirty = False
        if show_status:
            self.status.configure(text=f"Applied edit for {enemy['id']} - use Save JSON to write the file")
        return True

    def find_enemy_id(self, enemy_id: str) -> int | None:
        for index, enemy in enumerate(self.document["enemies"]):
            if enemy.get("id") == enemy_id:
                return index
        return None

    def unique_enemy_id(self, base: str = "new_enemy") -> str:
        existing = {enemy.get("id") for enemy in self.document["enemies"]}
        if base not in existing:
            return base
        index = 2
        while f"{base}_{index}" in existing:
            index += 1
        return f"{base}_{index}"

    def new_enemy(self) -> None:
        if self.dirty and not self.apply_current(show_status=False):
            return
        enemy_id = self.unique_enemy_id()
        enemy = {
            "id": enemy_id,
            "name": "New Enemy",
            "type": "goblin",
            "combat_role": "melee",
            "shape": "circle",
            "tags": ["melee"],
            "stats": {field: 1.0 for field in STAT_FIELDS},
            "resistances": {field: 1.0 for field in RESISTANCE_FIELDS},
        }
        enemy["stats"].update({"health": 30, "speed": 70, "radius": 9, "reward": 5, "damage": 4, "accel": 500, "mass": 1})
        self.document["enemies"].append(enemy)
        self.refresh_list()
        self.listbox.selection_clear(0, self.tk.END)
        self.listbox.selection_set(len(self.document["enemies"]) - 1)
        self.load_index(len(self.document["enemies"]) - 1)
        self.dirty = True
        self.status.configure(text=f"Created {enemy_id} - edit fields, then Save JSON")

    def duplicate_current(self) -> None:
        if self.current_index is None:
            return
        if self.dirty and not self.apply_current(show_status=False):
            return
        enemy = copy.deepcopy(self.document["enemies"][self.current_index])
        base_id = str(enemy.get("id", "enemy")) + "_copy"
        enemy["id"] = self.unique_enemy_id(base_id)
        enemy["name"] = str(enemy.get("name", "Enemy")) + " Copy"
        self.document["enemies"].append(enemy)
        self.refresh_list()
        self.listbox.selection_clear(0, self.tk.END)
        self.listbox.selection_set(len(self.document["enemies"]) - 1)
        self.load_index(len(self.document["enemies"]) - 1)
        self.dirty = True
        self.status.configure(text=f"Duplicated as {enemy['id']} - Save JSON to write the file")

    def delete_current(self) -> None:
        if self.current_index is None:
            return
        old_index = self.current_index
        del self.document["enemies"][self.current_index]
        self.current_index = None
        self.refresh_list()
        if self.document["enemies"]:
            index = min(old_index, len(self.document["enemies"]) - 1)
            self.listbox.selection_set(index)
            self.load_index(index)
        self.dirty = False
        self.status.configure(text="Deleted enemy - use Save JSON to write the file")

    def save_file(self) -> None:
        if self.current_index is not None and not self.apply_current(show_status=False):
            return
        save_document(self.data_path, self.document)
        self.dirty = False
        self.status.configure(text=f"Saved {self.data_path}")

    def reload_file(self) -> None:
        self.document = load_document(self.data_path)
        self.current_index = None
        self.dirty = False
        self.refresh_list()
        if self.document["enemies"]:
            self.listbox.selection_set(0)
            self.load_index(0)
        self.status.configure(text=f"Reloaded {self.data_path}")


def check_data(path: Path) -> int:
    from bastion.game.enemy_defs import load_enemy_definitions

    definitions = load_enemy_definitions(path)
    print(f"Loaded {len(definitions)} enemies from {path}")
    for enemy_id, data in definitions.items():
        print(f"- {enemy_id}: {data['name']} ({data['faction_type']}, {data['combat_role']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Edit Bastion enemy JSON data.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH, help="Path to enemies.json.")
    parser.add_argument("--check", action="store_true", help="Validate and print enemy data without opening the UI.")
    args = parser.parse_args(argv)

    if args.check:
        return check_data(args.data)

    import tkinter as tk

    root = tk.Tk()
    EnemyDataEditor(root, args.data)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
