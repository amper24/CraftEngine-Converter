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
- **Микро-нейросеть**: определяет, что за объект конвертируется (еда, инструмент,
  оружие, растение, механизм, мебель…), какое представление в CraftEngine ему
  нужно (auto_state, прозрачность, entity-renderer) и выдаёт готовые игровые
  команды для проверки каждого объекта.
- Отчёты (summary / semantics / commands / unsupported / partial / manual-tasks /
  warnings / validation), manifest, source-map и README результата.
- **Режим ItemsAdder → CraftEngine**: читает пак `contents/` плагина ItemsAdder
  (предметы, блоки, мебель, категории, lang, текстуры и модели) и строит из него
  пакет CraftEngine тем же конвейером.

## Режим ItemsAdder → CraftEngine

Конвертер принимает не только мод-JAR, но и **пак контента ItemsAdder** — папку
`plugins/ItemsAdder/contents/` (или саму `contents/`, или папку одного
namespace). Формат определяется автоматически; принудительно — флагом
`--source itemsadder` или `source_mode: itemsadder` в `settings.yml`.

ItemsAdder описывает контент декларативно в YAML, поэтому вместо чтения
байткода конвертер разбирает эти файлы и строит **тот же самый IR**. Дальше
работает обычный конвейер: семантика → capability → генератор → пакет →
валидатор. Никакого отдельного «второго конвертера» нет.

```bash
# автоопределение
python -m converter convert plugins/ItemsAdder/contents --output converted/mypack

# то же самое явно
python -m converter convert plugins/ItemsAdder/contents \
  --source itemsadder --output converted/mypack
```

### Что переносится

| ItemsAdder | CraftEngine |
| --- | --- |
| `display_name` / `name` (включая ключи `display-name-*` из `lang:`) | `data.item_name` |
| `lore` | `data.lore` |
| `resource.material` | `material` (сохраняется: в IA это предмет-основа) |
| `resource.generate: true` + `textures` | `texture` / `textures` (или `minecraft:item/handheld` для инструментов) |
| `resource.generate: false` + `model_path` | `model: {type: minecraft:model, path: …}` |
| `attribute_modifiers` (camelCase, `SCREAMING_SNAKE`, snake_case и списковая форма) | `data.attribute_modifiers` |
| `durability.max_durability` / `unbreakable` / `disappear_when_broken` | `data.max_damage` / `data.unbreakable` / `settings.prevent_break` |
| `enchants`, `item_flags`, `max_stack_size`, `glint`, `fuel` | `data.enchantments`, `data.hide_tooltip`, `data.components`, `settings.fuel_time` |
| `consumable` и старый `events.eat/drink.feed` | `data.food` + `data.consumable` |
| `specific_properties.armor` + `armors_rendering` | `data.equippable` (`slot`, `asset_id`) |
| `behaviours.block` (`placed_model.type`, `hardness`, `light_level`, звуки, инструменты) | блок + `behavior: block_item`, `state.auto_state`, `settings.*` |
| `behaviours.furniture` (hitbox, `display_transformation`, `placeable_on`, свет) | мебель + `behavior: furniture_item`, `variants`, `glowing_furniture` |
| `drop.break_block.loots` (шанс 0–100) | loot-таблица (вероятность 0–1) |
| `categories` (включая `ns:*` и regex) | `categories` |
| `lang` | `assets/<ns>/lang/<locale>.json` |
| `textures/`, `models/`, `resources/resourcepack/assets/` | `resourcepack/assets/<ns>/…` |
| `data/<ns>/recipes/*.json` внутри пака | рецепты (тот же парсер, что и для модов) |
| `template` / `variant_of` | разворачивается наследование, сам шаблон не генерируется |

Соответствия вынесены в data-driven таблицы `mappings/itemsadder/`
(`attributes.json`, `block_types.json`, `item_flags.json`, `behaviours.json`) —
их можно править без изменения кода.

### Отчёт о полном переносе

Каждый ключ исходника попадает в `reports/itemsadder.md`: что во что
превратилось и что требует ручной работы. Ключ без эквивалента не исчезает
молча — он получает статус `partial` или `unsupported` с пояснением.

```text
## Summary
| Support | Keys |
| direct | 51 |
| transform | 13 |
| partial | 1 |
| unsupported | 5 |
```

### Настройки

```yaml
source_mode: auto                    # auto | mod | itemsadder
ia_preserve_material: true           # сохранять resource.material
ia_explosion_immune_resistance: 3600000.0   # для блоков с no_explosion: true
ia_force_custom_model_data: false    # не навязывать model_id из IA
ia_generate_furniture: true
ia_default_locale: en                # откуда брать переводы display-name-*
ia_emit_consumable_details: true
```

В GUI формат выбирается в выпадающем списке «Источник». Для пака ItemsAdder
диалог маппинга namespace (он нужен только SliceBoard) пропускается, а в лог
выводится распознанная раскладка: namespace'ы, число конфигов и где лежат
ресурсы.

### Вход: папка или архив

Конвертер принимает любую из трёх форм, и результат одинаков:

- папку `plugins/ItemsAdder/contents/` (или саму `contents/`);
- ZIP-архив этой папки;
- ZIP-архив с папкой-обёрткой (`MyAwesomePack/contents/...`) — корень находится сам.

Расширение не важно: архив открывается через `zipfile`, поэтому `.zip` и `.jar`
идут по одному пути. В GUI фильтр файла предлагает и то, и другое.

### События и действия

`events:` из ItemsAdder переводится в events-DSL CraftEngine
(`mappings/itemsadder/events.json`). На каждое событие ItemsAdder создаётся
отдельная запись `on/functions/conditions`, чтобы шанс одного события не
перетекал на соседнее с тем же триггером.

| ItemsAdder | CraftEngine |
| --- | --- |
| `attack` | `attack` |
| `block_break` / `item_break` | `block_break` / `item_break` |
| `interact`, `interact_mainhand`, `interact_offhand` | `right_click` |
| `eat`, `drink` | `consume` |
| `pickup` | `pick_up` |
| `chance: 0.35` | условие `random` |

Действия: `message`, `actionbar`, `title`, `execute_commands`, `play_sound`,
`play_particle`, `potion_effect`, `remove_potion_effect`, `feed`,
`increment/decrement_amount`, `set_block`, `drop_item`, `place/remove/replace_furniture`,
`mythic_mobs_skill`, `cancel`, `swing_hand`, `teleport`, `toast`. Повторы вида
`play_sound_2` разворачиваются, имена эффектов приводятся к ванильным
(`SPEED` → `minecraft:speed`).

Триггеры без аналога (`wear`, `unwear`, `held`, `drop`, `kill`, `bow_shot`,
`fishing_*`, `gun_*`, `book_*`, `bucket_*`) и действия без аналога
(`veinminer`, `script`, `explosion`, `damage_entity`, `feed`-соседи и т.д.)
**не выбрасываются молча**: каждая такая строка попадает в
`reports/itemsadder.md` с пояснением, что делать. Недопустимые значения тоже не
генерируются — например, `open_inventory: my_custom_menu` не станет
`open_window` с чужеродным `gui_type`, а уйдёт в отчёт.

Встроенный `cooldown` не переносится: в CraftEngine кулдауны именные
(`set_cooldown` + условие `on_cooldown`), это тоже фиксируется в отчёте.

### Рендер брони

`armors_rendering` больше не оставляет висячую ссылку. Для каждого id
генерируется `assets/<ns>/equipment/<id>.json`, а текстуры `layer_1`/`layer_2`
переезжают в ванильные каталоги `textures/entity/equipment/humanoid/` и
`.../humanoid_leggings/` — иначе `data.equippable.asset_id` ссылается в никуда и
броня не отрисовывается. Если PNG слоёв в паке нет, ассет не выдумывается: в
`reports/itemsadder.md` появляется строка `partial` с указанием, чего не хватает.

### Реестр звуков и корневые ключи

Корневой `sounds:` превращается в `assets/<ns>/sounds.json` — без него `.ogg`
файлы просто лежат в паке и ни один id звука не разрешается. `path` становится
подкаталогом, `settings.subtitle` переносится как есть.

Корневые ключи, у которых в CraftEngine нет аналога (`entities`, `liquids`,
`hud`, `emotes`, `biomes`, `world_populators`, `paintings`, `books`,
`music_discs`, `trims`, `fonts`), больше не исчезают бесследно: каждый даёт
строку в `reports/itemsadder.md` с указанием файла-источника и пояснением, куда
это содержание девать.

### Книги и луки

`behaviours.book` переносится в `data.written_book_content`: статические
страницы, `title` и `author`. Страницы с плейсхолдерами или интерактивным
содержимым статическим текстом не становятся — они пропускаются, и отчёт
точно называет их количество (вывод и отчёт не расходятся).

`bow`/`crossbow`/`quiver` помечены как `unsupported`, а не `partial`: в
CraftEngine 26.8 нет ключа-списка снарядов, поэтому скорость натяжения и урон
стрелы берутся из ванильного материала. Раньше таблица маппинга обещала
`allowed_projectiles` — ключ, которого схема не знает и который не генерировался.

### Сиденья (мебель)

`behaviours.furniture_sit` превращается в `seats` у хитбокса CraftEngine —
именно так устроены паки сидений вроде «Cushions»:

```yaml
variants:
  ground:
    hitboxes:
      - type: interaction
        seats: ["0,0.5,0"]   # x,y,z, опционально yaw через пробел
```

`sit_height` становится координатой Y. У IA-ключа `sit_all_solid_blocks`
(сидеть на любом твёрдом блоке) аналога нет — в CraftEngine сиденье принадлежит
конкретной мебели, это фиксируется в отчёте.

### Строковый NBT

`nbt:` в строковой форме (`'{my-tag:"hello"}'`) разбирается собственным
парсером SNBT и раскладывается в `data.components`. То, что парсер не понимает
полностью, отдаётся человеку через отчёт — догадок не подставляется.

## Режим «ресурс-пак → CraftEngine»

Конвертер умеет брать **обычный ресурспак** и генерировать конфиги из того, что
в нём уже нарисовано: `assets/<ns>/models/item/*.json` и
`assets/<ns>/textures/item/*.png`. Художнику не нужно писать по YAML на каждую
текстуру.

```bash
python -m converter convert path/to/resourcepack --output out --source resourcepack
```

`--source auto` (значение по умолчанию) выбирает формат по убыванию
специфичности: мод-JAR (есть метаданные загрузчика) → ItemsAdder (`contents/`) →
ресурспак (`assets/`). Это важно, потому что и мод, и пак ItemsAdder тоже
содержат `assets/`.

### Что во что превращается

| В ресурспаке | В CraftEngine |
| --- | --- |
| только текстура | `texture: <ns>:item/<name>` — модель генерирует CraftEngine |
| есть `models/item/<name>.json` | `model: <ns>:item/<name>`, **без** блока `generation` |
| текстура с «инструментальным» именем (`_sword`, `_pickaxe`, …) | `generation.parent: minecraft:item/handheld` |
| `lang/en_us.json` → `item.<ns>.<name>` | `data.item_name` |

Правило про `generation` принципиальное: блок `generation` рядом с настоящим
`.json` модели переопределяет авторскую модель, поэтому он добавляется только
там, где модели в паке нет.

### Что не конвертируется и почему

- `assets/<ns>/models/block/*.json` — для блока нужен конфиг CraftEngine со
  states, из одной модели он не выводится;
- `assets/minecraft/` — это переопределение ванильных предметов, а не новый
  контент.

Оба случая попадают в `reports/resourcepack.md`, а не исчезают.

### Настройки

| Ключ | По умолчанию | Смысл |
| --- | --- | --- |
| `rp_default_material` | `nether_brick` | Материал для всех предметов: в ресурспаке его нет, а CraftEngine иначе молча подставит свой |
| `rp_skip_vanilla_overrides` | `true` | Не создавать предметы из `assets/minecraft/` |

## Тесты

```bash
.venv/bin/python -m pytest tests -q
```

Часть тестов байткода требует внешний мод
`mod-for-tests/VeggiesDelight-1.21.1-1.9.3.jar` (в репозитории его нет — он
слишком большой). Без него они **пропускаются** с явной причиной, а не падают:
падение должно означать поломку, а не отсутствие необязательного файла. Чтобы
запустить их, положите jar в `mod-for-tests/`.

## Структура

```text
converter/
  cli.py        CLI (scan/analyze/convert/validate/report/gui)
  driver.py     оркестрация конвертации
  detector.py   ModDetector (loader/namespace/metadata)
  archive.py    чтение JAR/директории
  analyzer.py   IR: items/blocks/states/recipes/loot/tags/lang
  itemsadder.py импорт пака ItemsAdder (contents/) в тот же IR
  ir.py         IR-модели
  capability.py capability/mapping engine
  generator.py  генерация CraftEngine YAML
  sliceboard.py генерация рецептов SliceBoard
  packager.py   output-пакет (manifest/reports/source-map/resourcepack)
  validator.py  валидатор выходного пакета
  gui.py        GUI (tkinter)
  semantics.py  нейросемантический проход (применение вердиктов к IR)
  brain/        микро-нейросеть (features / labels / runtime / dataset / train)
  schema.py     target-schema registry + validator
  config.py     настройки + settings.yml
  util.py, paths.py, status.py

models/brain/               веса микро-нейросети (.npz, ~2 МБ на голову)
schemas/craftengine/26.8/   машиночитаемые target-схемы
mappings/                   data-driven mapping-правила
mappings/itemsadder/        таблицы соответствия ItemsAdder -> CraftEngine
tests/                      фикстура и проверки
```

## Установка

Требуется Python 3.10+, PyYAML и NumPy (GUI — стандартный tkinter).

```bash
python -m pip install pyyaml numpy
```

NumPy нужен только микро-нейросети. Без него конвертер продолжает работать,
просто возвращается к прежним эвристикам по именам и тегам.

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
#   C r a f t E n g i n e - C o n v e r t e r 
 
 

## Микро-нейросеть

Конвертер содержит небольшую собственную нейросеть, которая на этапе анализа
отвечает на три вопроса по каждому найденному объекту:

1. **Что это за предмет?** — категория (`Tools`, `Weapons`, `Drinks`, `Meals`,
   `Sweets`, `Crops`, `Cabinets`, …), тип снаряжения (`tool` / `weapon` / `bow`
   / `spear` / `shield` / `armor`), пищевая семья (`food` / `drink` / `soup` /
   `sweet`) и материал-тир (`iron`, `netherite`, …).
2. **Как его представить в CraftEngine?** — тип блока (`solid`, `crop`,
   `leaves`, `thin`, `glass`, `machine`, `furniture`, …), значение
   `auto_state`, прозрачность и нужен ли entity-renderer.
3. **Какие команды дать игроку?** — готовые `/ce give`, `/ce setblock`,
   `/ce debug …` для проверки результата прямо в игре.

### Архитектура

Многоголовый MLP на чистом NumPy: хэшированные символьные n-граммы + слова +
отдельное пространство для «главного существительного» имени, затем два
скрытых слоя (256 → 128) с GELU и по одной softmax-голове на задачу. Веса —
два `.npz` файла примерно по 2 МБ, никакого torch/ONNX, инференс мгновенный
и полностью детерминированный.

### Правила применения

* **Данные из мода важнее предсказания.** Если анализатор нашёл настоящий
  food-компонент в байткоде, тег `minecraft:mineable/axe`, свойство `age` у
  блока или категорию из recipe-book — нейросеть их не перезаписывает.
* **Порог уверенности.** Предсказание ниже порога записывается как совет, но
  не применяется.
* **Всё видно.** Каждое решение (и применённое, и отклонённое) попадает в
  `reports/semantics.md`, `reports/semantics.json` и в лог конвертации.
* **Отключается одним флагом** — `brain_enabled: false`.

### Что появляется в выводе

```text
reports/semantics.md     таблица «объект → что это → что применено → команды»
reports/semantics.json   машиночитаемая версия
reports/commands.txt     готовый список команд CraftEngine для проверки
reports/manual-tasks.md  рекомендации, которые требуют ручного решения
```

Пример строки из лога конвертации:

```text
[INFO] BRAIN farmersdelight:iron_knife: оружие (Weapons, 100%); снаряжение: weapon (100%); тир: iron
       commands=['/ce give @s farmersdelight:iron_knife 1', '/ce debug item farmersdelight:iron_knife']
```

### Настройки

```yaml
brain_enabled: true              # включить/выключить нейросеть целиком
brain_min_confidence: 0.6        # порог для предметов
brain_block_min_confidence: 0.7  # порог для блоков
brain_log_every_object: true     # строка в логе на каждый объект
brain_categories: true           # выбирать категорию, если нет тегов
brain_food_detection: true       # находить еду за пределами keyword-списка
```

Те же переключатели есть в GUI на вкладке семантики.

### Переобучение

Датасет синтезируется детерминированно из словаря игровых и модовых
соглашений об именовании (`converter/brain/dataset.py`) с аугментацией:
пропуск структурных признаков, ложные флаги и бессмысленные имена, чтобы сеть
опиралась на морфологию, а не на один признак.

```bash
python -m converter.brain.train --samples 16000 --epochs 45 --report
```

Веса сохраняются в `models/brain/`. Пространства меток в
`converter/brain/labels.py` — часть контракта модели: менять порядок нельзя,
нужно переобучать.
