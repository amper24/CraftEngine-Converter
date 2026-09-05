"""Tkinter desktop UI for the CraftEngine converter.

The UI intentionally stays dependency-free but provides a practical workflow:
preflight scan -> resolve external namespaces -> convert -> validate -> open
output. Settings are grouped into tabs instead of a single dense dialog.
"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
import tkinter as tk
from typing import Any

from . import __version__, driver, paths
from .config import SettingsManager


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"CraftEngine Mod Converter {__version__}")
        self.root.geometry("1000x720")
        self.root.minsize(860, 620)

        self.settings_manager = SettingsManager(paths.settings_file())
        self.settings = self.settings_manager.settings
        self.mod_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.minecraft_var = tk.StringVar(value=self.settings.minecraft_version)
        self.ce_var = tk.StringVar(value=self.settings.craftengine_version)
        self.interactive_var = tk.BooleanVar(value=self.settings.interactive_namespace_mapping)
        self.strict_var = tk.BooleanVar(value=self.settings.strict_recipe_mode)
        self.sliceboard_var = tk.BooleanVar(value=self.settings.sliceboard_enabled)
        self.status_var = tk.StringVar(value="Готов к работе")
        self.counts_var = tk.StringVar(value="")
        self.last_output: str | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="CraftEngine Mod Converter", font=("TkDefaultFont", 16, "bold")).pack(side="left")
        ttk.Label(header, text=f"v{__version__}").pack(side="left", padx=8, pady=(7, 0))
        ttk.Label(header, textvariable=self.status_var).pack(side="right", pady=(7, 0))

        paths_box = ttk.LabelFrame(outer, text="Проект", padding=8)
        paths_box.pack(fill="x", pady=(10, 8))
        paths_box.columnconfigure(1, weight=1)

        self._path_row(paths_box, 0, "Мод (JAR / папка):", self.mod_var, self._pick_mod, "Файл или папка")
        self._path_row(paths_box, 1, "Выход:", self.out_var, self._pick_out, "Папка результата")

        controls = ttk.Frame(paths_box)
        controls.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        ttk.Label(controls, text="Minecraft:").pack(side="left")
        ttk.Entry(controls, textvariable=self.minecraft_var, width=12).pack(side="left", padx=(5, 14))
        ttk.Label(controls, text="CraftEngine:").pack(side="left")
        ttk.Entry(controls, textvariable=self.ce_var, width=10).pack(side="left", padx=(5, 18))
        ttk.Checkbutton(controls, text="Показывать маппинг namespace перед генерацией", variable=self.interactive_var).pack(side="left")

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(0, 8))
        self.convert_btn = ttk.Button(buttons, text="▶  Конвертировать", command=self._convert)
        self.convert_btn.pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="🔍  Предпросмотр", command=self._preflight).pack(side="left", padx=6)
        ttk.Button(buttons, text="⚙  Настройки", command=self._open_settings).pack(side="left", padx=6)
        ttk.Button(buttons, text="📁  Открыть результат", command=self._open_output).pack(side="left", padx=6)

        summary = ttk.LabelFrame(outer, text="Результат", padding=8)
        summary.pack(fill="x", pady=(0, 8))
        ttk.Label(summary, textvariable=self.counts_var).pack(anchor="w")
        self.progress = ttk.Progressbar(summary, mode="indeterminate")
        self.progress.pack(fill="x", pady=(7, 0))

        self.log = scrolledtext.ScrolledText(outer, height=28, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

        footer = ttk.Label(outer, text="Fidelity-first • configuration/ + resourcepack/ • SliceBoard • Recipe namespace resolver")
        footer.pack(anchor="w", pady=(6, 0))

    @staticmethod
    def _path_row(parent: ttk.Frame, row: int, label: str, var: tk.StringVar, command: Any, placeholder: str) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=5, pady=4)
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", padx=5, pady=4)
        ttk.Button(parent, text="Обзор…", command=command).grid(row=row, column=2, padx=5, pady=4)

    def _pick_mod(self) -> None:
        path = filedialog.askopenfilename(title="Выберите мод", filetypes=[("Minecraft mod", "*.jar"), ("Все файлы", "*.*")])
        if not path:
            path = filedialog.askdirectory(title="Или выберите распакованный мод")
        if path:
            self.mod_var.set(path)
            if not self.out_var.get():
                self.out_var.set(str(Path(path).parent / f"{Path(path).stem}_craftengine"))

    def _pick_out(self) -> None:
        path = filedialog.askdirectory(title="Выберите папку вывода")
        if path:
            self.out_var.set(path)

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _sync_settings(self) -> None:
        self.settings.minecraft_version = self.minecraft_var.get().strip() or "1.21.4"
        self.settings.craftengine_version = self.ce_var.get().strip() or "26.8"
        self.settings.interactive_namespace_mapping = self.interactive_var.get()
        self.settings.strict_recipe_mode = self.strict_var.get()
        self.settings.sliceboard_enabled = self.sliceboard_var.get()
        self.settings_manager.save(self.settings)

    def _preflight(self) -> None:
        mod = self.mod_var.get().strip()
        if not mod:
            messagebox.showwarning("Нет мода", "Укажите JAR или папку мода.")
            return
        self.progress.start()
        self.status_var.set("Анализ исходника…")
        self._log("=== PREFLIGHT ===")

        def work() -> None:
            try:
                data = driver.suggest_namespace_mappings(mod, self.minecraft_var.get().strip() or "1.21.4", self.settings)
                self.root.after(0, lambda: self._on_preflight(data))
            except Exception as exc:
                self.root.after(0, lambda: self._on_error(exc))

        threading.Thread(target=work, daemon=True).start()

    def _on_preflight(self, data: dict[str, Any]) -> None:
        self.progress.stop()
        self.status_var.set("Предпросмотр готов")
        mod = data.get("mod", {})
        self._log(f"Мод: {mod.get('id')} | namespace: {mod.get('namespace')}")
        self._log(f"Dependencies: {', '.join(mod.get('dependencies', [])) or '—'}")
        mappings = data.get("namespaces", {})
        self._log(f"Внешних namespace: {len(mappings)}")
        for ns, item in mappings.items():
            self._log(f"  {ns} -> {item.get('prefix')} ({item.get('reason')})")
        if mappings and self.interactive_var.get():
            NamespaceMappingDialog(self.root, self.settings_manager, mappings)

    def _convert(self) -> None:
        mod = self.mod_var.get().strip()
        out = self.out_var.get().strip()
        if not mod or not out:
            messagebox.showwarning("Недостаточно данных", "Укажите мод и папку вывода.")
            return
        self._sync_settings()

        def launch() -> None:
            self.root.after(0, self._start_conversion)

        if self.interactive_var.get():
            # Preflight runs first so namespace mappings are visible/editable.
            self.progress.start()
            self.status_var.set("Проверяем внешние namespace…")
            self._log("=== PREFLIGHT BEFORE CONVERSION ===")

            def work() -> None:
                try:
                    data = driver.suggest_namespace_mappings(mod, self.settings.minecraft_version, self.settings)
                    self.root.after(0, lambda d=data: self._show_mapping_then_convert(d))
                except Exception as exc:
                    self.root.after(0, lambda: self._on_error(exc))

            threading.Thread(target=work, daemon=True).start()
        else:
            launch()

    def _show_mapping_then_convert(self, data: dict[str, Any]) -> None:
        self.progress.stop()
        mappings = data.get("namespaces", {})
        if mappings:
            dlg = NamespaceMappingDialog(self.root, self.settings_manager, mappings, on_done=self._start_conversion)
            self.root.wait_window(dlg)
            return
        self._start_conversion()

    def _start_conversion(self) -> None:
        mod = self.mod_var.get().strip()
        out = self.out_var.get().strip()
        self.progress.start()
        self.status_var.set("Конвертация…")
        self.convert_btn.configure(state="disabled")
        self._log(f"=== CONVERT ===\n{mod} -> {out}")

        def work() -> None:
            try:
                result = driver.convert(mod, out, target=f"craftengine:{self.settings.craftengine_version}", minecraft_version=self.settings.minecraft_version, settings=self.settings)
                self.root.after(0, lambda: self._on_done(result))
            except Exception as exc:
                self.root.after(0, lambda: self._on_error(exc))

        threading.Thread(target=work, daemon=True).start()

    def _on_done(self, result: dict[str, Any]) -> None:
        self.progress.stop()
        self.convert_btn.configure(state="normal")
        self.status_var.set("Готово")
        self.last_output = result.get("output")
        c = result.get("counts", {})
        s = result.get("statuses", {})
        validation = result.get("validation", {})
        fidelity = result.get("fidelity", {})
        self.counts_var.set(
            f"Items {c.get('items', 0)} • Blocks {c.get('blocks', 0)} • Recipes {c.get('recipes', 0)} • "
            f"Files {result.get('files_generated', 0)} • "
            f"DIRECT {s.get('DIRECT', 0)} / TRANSFORM {s.get('TRANSFORM', 0)} / PARTIAL {s.get('PARTIAL', 0)} / UNSUPPORTED {s.get('UNSUPPORTED', 0)}"
        )
        self._log("=== DONE ===")
        self._log(f"Validation: {'VALID' if validation.get('valid') else 'ERRORS'}")
        self._log(f"Fidelity: {'VALID' if fidelity.get('valid') else 'ERRORS'}")
        self._log(f"Output: {result.get('output')}")
        diagnostics = result.get("diagnostics", 0)
        if diagnostics:
            self._log(f"Diagnostics: {diagnostics} (см. reports/)")
        if not validation.get("valid") or not fidelity.get("valid"):
            messagebox.showwarning("Готово с проблемами", "Генерация завершена, но проверка нашла проблемы. Откройте reports/ для деталей.")
        else:
            messagebox.showinfo("Готово", f"Конвертация завершена.\n\n{result.get('output')}")

    def _on_error(self, exc: Exception) -> None:
        self.progress.stop()
        self.convert_btn.configure(state="normal")
        self.status_var.set("Ошибка")
        self._log("ERROR:\n" + traceback.format_exc())
        messagebox.showerror("Ошибка", str(exc))

    def _open_settings(self) -> None:
        SettingsDialog(self.root, self.settings_manager, on_done=self._refresh_settings)

    def _refresh_settings(self) -> None:
        self.settings = self.settings_manager.settings
        self.minecraft_var.set(self.settings.minecraft_version)
        self.ce_var.set(self.settings.craftengine_version)
        self.interactive_var.set(self.settings.interactive_namespace_mapping)
        self.strict_var.set(self.settings.strict_recipe_mode)
        self.sliceboard_var.set(self.settings.sliceboard_enabled)

    def _open_output(self) -> None:
        out = self.last_output or self.out_var.get().strip()
        if not out or not Path(out).exists():
            messagebox.showinfo("Папка", "Сначала выполните конвертацию.")
            return
        import os
        os.startfile(out)  # type: ignore[attr-defined]


class NamespaceMappingDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, manager: SettingsManager, proposals: dict[str, dict[str, Any]], on_done: Any | None = None) -> None:
        super().__init__(parent)
        self.title("Namespace resolver")
        self.geometry("760x480")
        self.transient(parent)
        self.grab_set()
        self.manager = manager
        self.settings = manager.settings
        self.proposals = proposals
        self.on_done = on_done
        self._build()

    def _build(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="Внешние namespace в рецептах", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        ttk.Label(root, text="Prefix используется SliceBoard как <prefix>:<path>. Можно оставить предложенный вариант или вписать свой.").pack(anchor="w", pady=(3, 10))

        cols = ("namespace", "prefix", "reason", "excluded")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", height=14)
        self.tree.heading("namespace", text="Namespace")
        self.tree.heading("prefix", text="Prefix")
        self.tree.heading("reason", text="Почему")
        self.tree.heading("excluded", text="Исключить")
        self.tree.column("namespace", width=180)
        self.tree.column("prefix", width=300)
        self.tree.column("reason", width=180)
        self.tree.column("excluded", width=90, anchor="center")
        self.tree.pack(fill="both", expand=True)
        for ns, item in sorted(self.proposals.items()):
            self.tree.insert("", "end", iid=ns, values=(ns, item.get("prefix", ""), item.get("reason", ""), "✓" if item.get("excluded") else ""))
        self.tree.bind("<<TreeviewSelect>>", self._selected)

        edit = ttk.Frame(root)
        edit.pack(fill="x", pady=8)
        self.ns_var = tk.StringVar()
        self.prefix_var = tk.StringVar()
        ttk.Label(edit, text="Prefix:").pack(side="left")
        ttk.Entry(edit, textvariable=self.prefix_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(edit, text="Применить к выбранному", command=self._apply).pack(side="left")
        ttk.Button(edit, text="Исключить / вернуть", command=self._toggle_excluded).pack(side="left", padx=6)

        buttons = ttk.Frame(root)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Сохранить", command=self._save).pack(side="right", padx=4)
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="right", padx=4)
        if self.on_done:
            ttk.Label(buttons, text="Эти значения будут использованы в текущей конвертации.").pack(side="left")

    def _selected(self, _event: Any = None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        self.ns_var.set(values[0])
        self.prefix_var.set(values[1])

    def _apply(self) -> None:
        sel = self.tree.selection()
        if not sel or not self.prefix_var.get().strip():
            return
        ns = sel[0]
        self.tree.item(ns, values=(ns, self.prefix_var.get().strip(), self.tree.item(ns, "values")[2]))

    def _toggle_excluded(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        values = self.tree.item(iid, "values")
        if not values:
            return
        excluded = "" if str(values[3]) == "✓" else "✓"
        self.tree.item(iid, values=(values[0], values[1], values[2], excluded))

    def _save(self) -> None:
        mappings = dict(self.settings.sliceboard_namespace_prefixes)
        for iid in self.tree.get_children():
            values = self.tree.item(iid, "values")
            if values and values[1]:
                mappings[str(values[0])] = str(values[1])
        self.settings.sliceboard_namespace_prefixes = mappings
        excluded = []
        for iid in self.tree.get_children():
            values = self.tree.item(iid, "values")
            if values and str(values[3]) == "✓":
                excluded.append(str(values[0]))
        self.settings.excluded_recipe_namespaces = sorted(set(excluded))
        self.manager.save(self.settings)
        if self.on_done:
            self.on_done()
        self.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, manager: SettingsManager, on_done: Any | None = None) -> None:
        super().__init__(parent)
        self.title("Настройки конвертера")
        self.geometry("820x620")
        self.manager = manager
        self.settings = manager.settings
        self.on_done = on_done
        self.vars: dict[str, tk.Variable] = {}
        self._build()

    def _build(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        general = ttk.Frame(notebook, padding=10)
        recipes = ttk.Frame(notebook, padding=10)
        slice_tab = ttk.Frame(notebook, padding=10)
        semantic = ttk.Frame(notebook, padding=10)
        notebook.add(general, text="Основные")
        notebook.add(recipes, text="Рецепты")
        notebook.add(slice_tab, text="SliceBoard")
        notebook.add(semantic, text="Bytecode / Semantics")

        self._bool(general, "interactive_namespace_mapping", "Показывать resolver namespace перед конвертацией", 0)
        self._bool(general, "strict_recipe_mode", "Не генерировать сомнительные рецепты (strict mode)", 1)
        self._bool(general, "preserve_unsupported_recipes", "Сохранять unsupported recipes в reports", 2)
        self._bool(general, "generate_source_map", "Генерировать source-map", 3)
        self._bool(general, "food_enabled", "Авто-распознавание food", 4)
        self._bool(general, "bytecode_food_enabled", "Извлекать food из bytecode", 5)
        self._bool(general, "bytecode_food_strict", "Строгий режим bytecode food", 7)
        self._bool(general, "generate_categories", "Генерировать категории CraftEngine", 8)
        self._bool(general, "generate_category_translations", "Переводы категорий RU / EN", 9)
        self._bool(general, "organize_recipes", "Раскладывать рецепты по типам", 10)
        self._bool(general, "write_conversion_log", "Записывать conversion.log + JSONL", 11)
        self._bool(general, "copy_all_assets", "Копировать весь assets/ исходного мода", 12)
        self._bool(general, "validate_resource_links", "Проверять ссылки моделей/текстур после генерации", 13)
        self._entry(general, "item_material_fallback", "Legacy fallback material (не выводится)", self.settings.item_material_fallback, 14)
        self._entry(general, "block_auto_state", "Fallback block state", self.settings.block_auto_state, 15)

        ttk.Label(recipes, text="Тип исходного рецепта → станция CraftEngine").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        cols = ("type", "station")
        self.recipe_tree = ttk.Treeview(recipes, columns=cols, show="headings", height=12)
        self.recipe_tree.heading("type", text="Источник")
        self.recipe_tree.heading("station", text="Цель")
        self.recipe_tree.column("type", width=380)
        self.recipe_tree.column("station", width=220)
        self.recipe_tree.grid(row=1, column=0, columnspan=3, sticky="nsew")
        for k, v in sorted(self.settings.recipe_stations.items()):
            self.recipe_tree.insert("", "end", values=(k, v))
        recipes.columnconfigure(0, weight=1); recipes.rowconfigure(1, weight=1)
        self.recipe_type = tk.StringVar(); self.recipe_station = tk.StringVar(value="crafting_table")
        ttk.Label(recipes, text="Источник").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(recipes, textvariable=self.recipe_type).grid(row=3, column=0, sticky="ew", padx=(0, 6))
        ttk.Label(recipes, text="Станция").grid(row=2, column=1, sticky="w", pady=6)
        ttk.Combobox(recipes, textvariable=self.recipe_station, values=["crafting_table", "sliceboard", "furnace", "blast_furnace", "smoker", "campfire", "stonecutter", "smithing_table", "unknown"], state="readonly").grid(row=3, column=1, sticky="ew")
        btn = ttk.Frame(recipes); btn.grid(row=4, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Button(btn, text="Добавить / изменить", command=self._add_recipe).pack(side="left", padx=(0,5))
        ttk.Button(btn, text="Удалить", command=self._remove_recipe).pack(side="left")
        self._bool(recipes, "remap_unknown_stations", "Неизвестные станции → указанная ниже", 5)
        self._entry(recipes, "remap_unknown_station_target", "Станция для unknown", self.settings.remap_unknown_station_target, 6)
        self._bool(recipes, "include_container_ingredient", "Добавлять container item при remap", 7)
        ttk.Label(recipes, text="Сортировка/структура рецептов").grid(row=8, column=0, sticky="w", pady=(8, 3))
        sort_var = tk.StringVar(value=self.settings.recipe_sort_mode)
        self.vars["recipe_sort_mode"] = sort_var
        ttk.Combobox(recipes, textvariable=sort_var, values=["type_then_id", "id", "type"], state="readonly", width=24).grid(row=9, column=0, sticky="w")

        self._bool(slice_tab, "sliceboard_enabled", "Включить генератор SliceBoard", 0)
        self._entry(slice_tab, "sliceboard_custom_provider", "Provider для custom refs", self.settings.sliceboard_custom_provider, 1)
        ttk.Label(slice_tab, text="Namespace prefixes (можно редактировать после preflight)").grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 5))
        self.ns_tree = ttk.Treeview(slice_tab, columns=("ns", "prefix"), show="headings", height=10)
        self.ns_tree.heading("ns", text="Namespace"); self.ns_tree.heading("prefix", text="Prefix")
        self.ns_tree.column("ns", width=220); self.ns_tree.column("prefix", width=360)
        self.ns_tree.grid(row=3, column=0, columnspan=2, sticky="nsew")
        for k, v in sorted(self.settings.sliceboard_namespace_prefixes.items()):
            self.ns_tree.insert("", "end", values=(k, v))
        ttk.Label(slice_tab, text="Примеры: farmersdelight → craftengine:farmersdelight; othermod → itemsadder:othermod").grid(row=4, column=0, columnspan=2, sticky="w", pady=6)
        ttk.Button(slice_tab, text="Удалить выбранный mapping", command=lambda: [self.ns_tree.delete(x) for x in self.ns_tree.selection()]).grid(row=5, column=0, sticky="w")
        slice_tab.columnconfigure(0, weight=1); slice_tab.rowconfigure(3, weight=1)

        ttk.Label(semantic, text="Восстановление механик из Java bytecode", font=("TkDefaultFont", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self._bool(semantic, "bytecode_semantic_enabled", "Общий semantic bytecode scanner", 1)
        self._bool(semantic, "gear_enabled", "Инструменты / оружие / броня / щиты", 2)
        self._bool(semantic, "reconstruct_item_components", "Восстанавливать item components", 3)
        self._bool(semantic, "reconstruct_block_behaviors", "Восстанавливать block behaviors", 4)
        self._bool(semantic, "reconstruct_events_from_bytecode", "Пытаться переводить bytecode events", 5)
        self._bool(semantic, "reconstruct_loot_from_datagen", "Учитывать datagen loot", 6)
        self._bool(semantic, "conservative_phantom_filter", "Строго отсеивать helper/stage ресурсы", 7)
        ttk.Label(semantic, text="Ключевые слова можно расширить через settings.yml.\nНеоднозначные значения записываются в reports/bytecode-semantics.json, а не угадываются молча.").grid(row=8, column=0, columnspan=2, sticky="w", pady=(10, 4))
        semantic.columnconfigure(1, weight=1)

        bottom = ttk.Frame(self); bottom.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bottom, text="Сохранить", command=self._save).pack(side="right", padx=4)
        ttk.Button(bottom, text="Отмена", command=self.destroy).pack(side="right", padx=4)

    def _bool(self, parent: ttk.Frame, name: str, text: str, row: int) -> None:
        var = tk.BooleanVar(value=bool(getattr(self.settings, name)))
        self.vars[name] = var
        ttk.Checkbutton(parent, text=text, variable=var).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)

    def _entry(self, parent: ttk.Frame, name: str, label: str, value: Any, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        var = tk.StringVar(value=str(value))
        self.vars[name] = var
        ttk.Entry(parent, textvariable=var, width=50).grid(row=row, column=1, sticky="ew", pady=5)
        parent.columnconfigure(1, weight=1)

    def _add_recipe(self) -> None:
        rtype = self.recipe_type.get().strip(); station = self.recipe_station.get().strip()
        if not rtype or not station: return
        for iid in self.recipe_tree.get_children():
            if self.recipe_tree.item(iid, "values")[0] == rtype:
                self.recipe_tree.item(iid, values=(rtype, station)); return
        self.recipe_tree.insert("", "end", values=(rtype, station))

    def _remove_recipe(self) -> None:
        for iid in self.recipe_tree.selection(): self.recipe_tree.delete(iid)

    def _save(self) -> None:
        for name, var in self.vars.items():
            current = getattr(self.settings, name)
            value = var.get()
            if isinstance(current, bool): setattr(self.settings, name, bool(value))
            elif isinstance(current, int): setattr(self.settings, name, int(value))
            elif isinstance(current, float): setattr(self.settings, name, float(value))
            else: setattr(self.settings, name, str(value))
        mapping = {}
        for iid in self.recipe_tree.get_children():
            values = self.recipe_tree.item(iid, "values")
            if values: mapping[str(values[0])] = str(values[1])
        self.settings.recipe_stations = mapping
        ns_map = {}
        for iid in self.ns_tree.get_children():
            values = self.ns_tree.item(iid, "values")
            if values and values[0] and values[1]: ns_map[str(values[0])] = str(values[1])
        self.settings.sliceboard_namespace_prefixes = ns_map
        self.manager.save(self.settings)
        if self.on_done: self.on_done()
        self.destroy()


def run_gui() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
