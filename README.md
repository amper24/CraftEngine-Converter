# CraftEngine JAR Converter

Универсальный конвертер контента Minecraft-модов в конфиги **CraftEngine 26.8**.

Инструмент анализирует мод (`*.jar` или распакованную директорию), строит
промежуточное представление (IR), сопоставляет исходную семантику с
возможностями CraftEngine и генерирует валидный пакет контента, не
придумывая неизвестных YAML-ключей.

## Возможности

- Определение loader (Forge / NeoForge / Fabric / Quilt), namespace и метаданных.
- Извлечение **items**, **blocks**, **block states / properties / variants**,
  **recipes**, **loot tables**, **tags**, **lang** и ресурсов.
- Каноническая target-schema CraftEngine 26.8 (машиночитаемые JSON-схемы в `schemas/`).
- Проверка неизвестных ключей: генератор не эмитит ключи вне allowlist.
- Data-driven mapping-правила (`mappings/`): материалы, tool tiers, auto-state,
  property types, recipe types.
- Каждый объект — отдельный YAML-файл (`configuration/<type>/<namespace>/<id>.yml`).
- Генерация рецептов нарезки для **SliceBoard** (`sliceboard/`).
- Полнота: `Detected = Generated + Diagnostic-only` (silent drop запрещён).
- Отчёты (summary / unsupported / partial / manual-tasks / warnings / validation),
  manifest, source-map и README результата.

## Структура

```text
converter/
  cli.py        CLI (scan/analyze/convert/validate/report/gui)
  driver.py     оркестрация конвертации
  detector.py   ModDetector (loader/namespace/metadata)
  archive.py    чтение JAR/директории
  analyzer.py   IR: items/blocks/states/recipes/loot/tags/lang
  ir.py         IR-модели
  capability.py capability/mapping engine
  generator.py  генерация CraftEngine YAML
  sliceboard.py генерация рецептов SliceBoard
  packager.py   output-пакет (manifest/reports/source-map/resourcepack)
  validator.py  валидатор выходного пакета
  gui.py        GUI (tkinter)
  schema.py     target-schema registry + validator
  config.py     настройки + settings.yml
  util.py, paths.py, status.py

schemas/craftengine/26.8/   машиночитаемые target-схемы
mappings/                   data-driven mapping-правила
tests/                      фикстура и проверки
```

## Установка

Требуется Python 3.10+ и PyYAML (GUI — стандартный tkinter).

```bash
python -m pip install pyyaml
```

## Быстрый запуск

### Графический интерфейс

```bash
run.bat
```

или

```bash
python -m converter gui
```

GUI позволяет выбрать мод и папку вывода, задать версии Minecraft/CraftEngine,
настроить маппинг станций рецептов (верстак / SliceBoard) и запустить конвертацию.

### Сборка в EXE (Windows)

```bash
build.bat
```

Результат — единый самодостаточный файл **`build/CraftEngineConverter.exe`**
(onefile, windowed, без консоли). Схемы (`schemas/`) и маппинги (`mappings/`)
запакованы внутрь EXE; настройки (`settings.yml`) создаются автоматически
рядом с exe при первом запуске.

## Использование (CLI)

```bash
# сканирование метаданных (loader)
python -m converter scan mod.jar

# анализ в IR (JSON)
python -m converter analyze mod.jar --minecraft 1.21.4

# конвертация
python -m converter convert mod.jar \
  --target craftengine:26.8 \
  --minecraft 1.21.4 \
  --output ./converted/mymod

# валидация результата
python -m converter validate ./converted/mymod

# отчёт по результату
python -m converter report ./converted/mymod
```

После конвертации в `<output>/` появятся:

```text
converted/mymod/
├── configuration/    # items/ blocks/ recipes/ loot/ (один объект = один файл)
├── configuration/sliceboard/ # config.yml + recipes/<ns>.yml
├── resourcepack/      # pack.mcmeta + исходные контентные ресурсы без изменений
├── source-map/        # index.json, ownership, mappings, resources
├── reports/           # summary, unsupported, partial, manual-tasks, ...
├── manifest.yml
└── README.md
```

## Настройка станций рецептов

В `settings.yml` мапинг «тип исходного рецепта → целевая станция»:

```yaml
recipe_stations:
  farmersdelight:cooking: crafting_table   # перенос в верстак
  farmersdelight:cutting: sliceboard       # отдельная генерация в SliceBoard
```

Плюс настройки SliceBoard:

```yaml
sliceboard_enabled: true
sliceboard_custom_provider: craftengine   # или itemsadder
sliceboard_block_ids:
  - farmersdelight:cutting_board
```

## Принцип «no unknown keys»

Генератор работает только с ключами из `schemas/craftengine/26.8/*.json`.
Любой ключ вне target-schema не генерируется; вместо этого объект получает
статус `UNSUPPORTED`/`PARTIAL` и сохраняется в IR + source-map + отчётах.

## Тест

```bash
python tests/make_fixture.py
python -m converter convert tests/farmersdelight_fixture.jar --output converted/farmersdelight
python -m converter validate converted/farmersdelight
python tests/check_loaders.py    # проверка определения загрузчиков
python tests/check_paths.py converted/farmersdelight  # проверка путей ресурсов
```

## Текущие ограничения

Bytecode/source-импорт выполняется статически (по регистрациям и ресурсам).
Сложная кастомная логика (BlockEntity ticking, сетевые протоколы, GUI)
классифицируется как `PARTIAL`/`EXTENSION`/`MANUAL` и переносится в
`manual-tasks.md`, а не придумывается. LLM-слой и runtime smoke-test
(изолированный Minecraft-сервер) заложены в архитектуру, но не включены
в автоматический MVP.

## Fidelity-first conversion

The converter treats the source mod as the source of truth. It preserves source resource paths, blockstate properties and variants, and only transforms mechanics when the target has a documented equivalent. Unknown/custom recipe serializers are reported instead of being silently flattened into a crafting recipe.

Complex blocks (crops, tripwire-backed blocks, thin/irregular models and stateful display blocks) can use CraftEngine `entity_renderer` with per-state helper items. This keeps the internal block properties (`age`, `facing`, `servings`, etc.) while rendering the exact source model.

The conversion writes `reports/fidelity.json` in addition to normal validation. The fidelity check compares the source archive, IR and generated package and flags missing resources, generated objects, recipes or blockstate variants.

The implementation follows the current CraftEngine documentation for block states, entity renderers, item models and recipes.

## High-fidelity workflow

The converter now performs a preflight namespace scan before conversion, keeps Farmer's Delight cutting-board recipes as a separate SliceBoard package, preserves multiple cutting outputs and per-output chances, and validates both the generated CraftEngine YAML and source-to-output fidelity.

### SliceBoard

SliceBoard recipes are generated under `configuration/sliceboard/recipes/`. Farmer's Delight 1.21+ cutting recipes with multiple results are preserved as multiple `outputs` entries with exact `min`, `max` and `chance` values. Vanilla tool tags such as `minecraft:axes` are expanded to the concrete vanilla materials used by the SliceBoard format. Farmer's Delight knife tags are resolved to `craftengine:farmersdelight:knife` by default, and namespace prefixes can be overridden in the GUI.

### Namespace resolver

Before generation, the GUI can inspect recipe references such as `farmersdelight:knife` or another mod namespace and propose a SliceBoard prefix. The proposed mapping is editable and saved to `sliceboard_namespace_prefixes` in `settings.yml`. Vanilla Minecraft IDs never go through a custom provider.

### Settings

Useful controls include strict recipe mode, unsupported-recipe preservation, source-map generation, unknown-station remapping, container ingredients, SliceBoard provider/prefixes, food heuristics, target versions, and recipe station mappings.


## NO-MATERIAL POLICY

Generated custom items intentionally omit `material`. Appearance is defined with `texture` or `model`; food receives food/consumable data without inventing a vanilla material.

## RESOURCE FIDELITY

The converter copies the complete `assets/<source-namespace>/` tree, including `lang/` and modern item-model resources. Fidelity validation distinguishes missing resources owned by the source pack from resources supplied by declared dependencies.


## Bytecode food extraction

When `bytecode_food_enabled: true`, the analyzer scans compiled `.class` files for NeoForge/Mojang `FoodProperties.Builder` and Fabric `FoodComponent.Builder` chains. It extracts `nutrition`, the source `saturationModifier`, and `alwaysEdible`. Generated CraftEngine `data.food.saturation` contains the actual saturation restored by vanilla (`2 * nutrition * saturationModifier`), while `reports/bytecode-food.json` retains the source modifier and class/field provenance.

This layer is intentionally conservative: a food definition that cannot be matched to a registered item is reported instead of being guessed onto an unrelated item.

## Unified release

This build merges the native CraftEngine layout, resource/model/lang fidelity, nested populated categories, editable/excludable namespaces, SliceBoard generation, block sound registration, bytecode food extraction, and semantic food carrier materials (drinks -> honey_bottle, soups/stews -> mushroom_stew).

## GUI icon / 3D model handling

The converter keeps one logical item when a source mod has a dedicated 2D inventory icon for a 3D/block-backed item. It generates a single CraftEngine `minecraft:select` model using `minecraft:display_context`: `gui` renders a generated `minecraft:item/generated` icon while other contexts use the original 3D model. A texture is never promoted to an item by itself, and a texture already consumed by the 3D model is not treated as a separate GUI icon.

Config:

```yaml
gui_icon_enabled: true
gui_icon_suffixes: ["_icon", "_inventory", "_gui"]
gui_icon_prefer_exact_item_texture: true
```

## Category generation

Categories are generated as the final semantic generation phase, after items, blocks, recipes, loot, sounds and SliceBoard. The generated structure follows the hand-authored CraftEngine reference:

- `<namespace>:main` is the visible root category and its `list` contains `#<namespace>:<subcategory>` references.
- Subcategories are `hidden: true` and contain a concrete non-empty `list` of generated item IDs.
- Category membership prefers source item tags (`c:crops`, `c:tools`, `c:foods`, etc.) and falls back to semantic/gear classification.
- Category localization uses `<l10n:category.*>` and writes both `en_us` and `ru_ru` language keys.
- Only successfully generated, user-facing items are included; display/stage helper items are excluded.

## FINAL FIX

This build includes CE-native loot normalization, crop bytecode reconstruction, seed-to-crop block binding, common-tag materialization, and SliceBoard kept outside the CraftEngine configuration tree.

Loot conversion normalizes vanilla `minecraft:item`, `minecraft:alternatives`, `minecraft:block_state_property`, `minecraft:uniform`, and related function/condition identifiers to the corresponding CraftEngine forms.
#   C r a f t E n g i n e - C o n v e r t e r  
 