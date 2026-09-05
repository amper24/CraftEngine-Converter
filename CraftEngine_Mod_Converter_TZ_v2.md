# Техническое задание
# «Ебанутый конвертер» — универсальный конвертер контента Minecraft-модов в CraftEngine

**Версия ТЗ:** 1.1
**Статус:** Detailed / implementation-ready draft
**Целевая платформа:** CraftEngine 26.8.x (точная поддерживаемая patch-версия задаётся профилем target)
**Язык ТЗ:** русский

---

## 1. Назначение проекта

Разработать программу, которая автоматически анализирует Minecraft-мод в формате JAR/директории/набора ресурсов, извлекает его контент и игровые зависимости, преобразует данные во внутреннее промежуточное представление (IR), а затем генерирует максимально полный пакет контента для CraftEngine.

Система должна работать не как простой конвертер файлов `assets → YAML`, а как **анализатор и транслятор игрового контента**.

**Ключевое обязательное требование:** каждый обнаруженный предмет, блок и рецепт должен либо получить собственный generated artifact (для Item/Block/Recipe — отдельный YAML), либо быть представлен как diagnostic-only объект с полным сохранением исходной семантики в IR и отчётах. Silent drop запрещён.

Целевой принцип:

```text
Minecraft Mod
    ↓
Scanner / Importer
    ↓
Intermediate Representation (IR)
    ↓
Semantic / Behavior Analyzer
    ↓
Capability Analyzer
    ↓
CraftEngine Translator
    ↓
CraftEngine YAML + Resource Pack + Scripts/Extensions + Report
```

Основная задача — преобразовать максимально возможную часть мода в CraftEngine-контент, а то, что нельзя преобразовать напрямую, явно классифицировать как:

- `DIRECT` — переносится напрямую;
- `TRANSFORM` — переносится с преобразованием;
- `PARTIAL` — переносится частично;
- `SCRIPT` — требует генерируемого скрипта/логики;
- `EXTENSION` — требует Java-плагина/расширения;
- `MANUAL` — требует ручной доработки;
- `UNSUPPORTED` — невозможно корректно перенести средствами текущего target-профиля.

---

# 2. Область проекта

## 2.1. Входные форматы

Система должна принимать:

1. Mod JAR.
2. Распакованный каталог мода.
3. Каталог `assets/` и `data/`.
4. При необходимости — несколько модов одновременно для анализа зависимостей.
5. Исходный код Java/Kotlin, если предоставлен отдельно.
6. Bytecode `.class`, если source отсутствует.
7. Modpack/архив, содержащий набор модов — опциональный режим batch import.

## 2.2. Источники данных внутри мода

Сканер должен анализировать, когда присутствуют:

```text
META-INF/
mods.toml
fabric.mod.json
quilt.mod.json

assets/<namespace>/
  blockstates/
  models/
  textures/
  sounds/
  lang/
  particles/
  fonts/
  shaders/
  atlases/
  equipment/
  other resources

data/<namespace>/
  recipes/
  loot_tables/
  tags/
  predicates/
  advancements/
  worldgen/
  functions/
  structures/
  other datapack resources

Java/Kotlin classes
Mixins
Registries
Config classes
Network handlers
BlockEntity classes
Entity classes
Renderer classes
Menu/Screen classes
Recipe serializers
Event handlers
Capabilities / Components
```

Поддержка конкретного источника должна определяться plugin/loader-профилем.

---

# 3. Цели

## 3.1. Основные

Система должна:

- распознавать тип мода и loader;
- извлекать весь доступный контент;
- сохранять исходные ресурсы без потерь;
- строить IR, независимый от CraftEngine;
- определять семантику контента;
- сопоставлять исходную семантику с возможностями CraftEngine;
- генерировать валидный CraftEngine-пакет;
- сохранять невозможные части в виде диагностик;
- генерировать отчёт о качестве переноса;
- поддерживать повторяемую конвертацию;
- не перезаписывать пользовательские данные без явного разрешения;
- быть расширяемым через адаптеры и mapping profiles.

## 3.2. Вторичные

- создавать шаблоны для ручной доработки;
- генерировать документацию по результату конвертации;
- позволять LLM участвовать только там, где статический анализ недостаточен;
- использовать LLM с жёсткими ограничениями целевой схемы;
- запускать validation без генерации;
- повторно использовать IR для других target-платформ в будущем.

---

# 4. Необходимые свойства системы

## 4.1. Determinism

Одинаковый вход + одинаковый target profile + одинаковая версия mapping = одинаковый результат.

## 4.2. Traceability

Каждый сгенерированный объект должен иметь ссылку на исходные элементы:

```text
source file
source class
resource path
registry name
source code symbol
analysis rule
mapping rule
```

## 4.3. Explainability

Для каждого нестандартного решения программа должна уметь объяснить:

- что найдено;
- как распознано;
- почему выбран конкретный CraftEngine-эквивалент;
- какие предположения сделаны;
- что остаётся неподдержанным.

## 4.4. Safe generation

Генератор не должен молча придумывать CraftEngine-ключи.

Любой ключ должен проходить через target schema registry.

Если ключ не известен текущему профилю CraftEngine:

```text
ERROR / UNSUPPORTED_TARGET_KEY
```

а не генерация неизвестного YAML.

---

# 5. Архитектура

```text
┌─────────────────────────────────────────┐
│                  INPUT                  │
│ JAR / directory / modpack / source     │
└──────────────────┬──────────────────────┘
                   ↓
┌─────────────────────────────────────────┐
│              MOD DETECTOR               │
│ Loader / version / namespace / metadata │
└──────────────────┬──────────────────────┘
                   ↓
┌─────────────────────────────────────────┐
│               IMPORT LAYER              │
│ Assets / Data / Bytecode / Source      │
└──────────────────┬──────────────────────┘
                   ↓
┌─────────────────────────────────────────┐
│                  IR                     │
│ Items / Blocks / Entities / Recipes     │
│ Models / Textures / Behaviors / Events  │
└──────────────────┬──────────────────────┘
                   ↓
┌─────────────────────────────────────────┐
│          SEMANTIC ANALYZER              │
│ Registry / State / Calls / Dependencies │
└──────────────────┬──────────────────────┘
                   ↓
┌─────────────────────────────────────────┐
│       CAPABILITY / MAPPING ENGINE       │
│ Direct / Transform / Partial / etc.     │
└──────────────────┬──────────────────────┘
                   ↓
┌─────────────────────────────────────────┐
│       CRAFTENGINE TRANSLATOR            │
│ YAML / JSON / Models / Scripts / Data   │
└──────────────────┬──────────────────────┘
                   ↓
┌─────────────────────────────────────────┐
│              VALIDATOR                  │
│ Syntax / Schema / Dependency / Runtime  │
└──────────────────┬──────────────────────┘
                   ↓
┌─────────────────────────────────────────┐
│               OUTPUT                    │
│ CraftEngine package + report + logs     │
└─────────────────────────────────────────┘
```

---

# 6. Компоненты системы

## 6.1. `ModDetector`

Назначение: определить loader, Minecraft version, mod id, namespace, name, dependencies.

Вход:

- JAR;
- directory.

Выход:

```text
ModMetadata
├── id
├── name
├── version
├── loader
├── minecraftVersions[]
├── dependencies[]
├── optionalDependencies[]
└── sourceFiles[]
```

Если metadata конфликтуют между manifest и code analysis, обе версии сохраняются, а конфликт помечается.

---

# 7. Import Layer

## 7.1. Resource Importer

Должен извлекать:

- textures;
- models;
- blockstates;
- lang;
- sounds;
- fonts;
- atlases;
- recipes;
- loot tables;
- tags;
- predicates;
- datapack resources.

Каждый ресурс должен иметь:

```text
ResourceRecord
├── sourcePath
├── namespace
├── type
├── rawBytes/hash
├── parsedRepresentation
└── references[]
```

## 7.2. Bytecode Importer

При наличии `.class` должен извлекать:

- class hierarchy;
- methods;
- fields;
- annotations;
- constant values;
- invoked methods;
- constructor calls;
- registry registration calls;
- event registration;
- renderer registrations;
- recipe serializers;
- block/item/entity definitions;
- block state properties;
- block entity tickers;
- menu registrations;
- data component usage.

Использовать AST/source parsing при наличии source и bytecode analysis как fallback.

## 7.3. Source Importer

При наличии Java/Kotlin source желательно использовать AST для:

- semantic resolution;
- control-flow extraction;
- registry calls;
- annotations;
- method invocation graph;
- event listeners;
- custom logic extraction.

---

# 8. Intermediate Representation (IR)

IR — обязательный слой между модом и CraftEngine.

## 8.1. BaseNode

Каждый IR объект имеет:

```yaml
id:
namespace:
source:
kind:
confidence:
status:
references: []
metadata: {}
```

`confidence` диапазон `0.0..1.0`.

`status`:

```text
DETECTED
ANALYZED
MAPPED
GENERATED
VALIDATED
PARTIAL
MANUAL
UNSUPPORTED
ERROR
```

---

# 9. IR: Item

```text
ItemNode
├── id
├── baseMaterial
├── display
├── lore
├── components[]
├── attributes[]
├── enchantments[]
├── durability
├── food
├── equip
├── use
├── projectile
├── recipeReferences[]
├── model
├── textures[]
├── behavior
├── events
├── tags[]
├── customData
└── sourceTrace
```

Каждое поле должно иметь provenance.

Пример:

```yaml
id: farmersdelight:tomato
source:
  registry: net.example.ModItems#TOMATO
  resources:
    - assets/farmersdelight/textures/item/tomato.png
confidence: 0.99
```

---

# 10. IR: Block

```text
BlockNode
├── id
├── material
├── states[]
├── settings
├── loot
├── model
├── textures[]
├── placement
├── break
├── interaction
├── tick
├── randomTick
├── redstone
├── collision
├── shape
├── blockEntity
├── behavior
├── events
├── tags[]
└── sourceTrace
```

---

# 11. IR: BlockState

Для каждого state необходимо хранить:

```text
BlockStateNode
├── property name
├── property type
├── allowed values
├── default value
├── source declaration
└── variants
```

Типы IR должны включать минимум:

```text
boolean
integer
enum
direction
axis
block_face
custom_enum
```

---

# 12. IR: Behavior

```text
BehaviorNode
├── id
├── target
├── trigger
├── requiredProperties[]
├── parameters{}
├── dependencies[]
├── conflicts[]
├── tick
├── placement
├── interaction
├── break
├── neighborUpdate
├── sourceTrace
└── confidence
```

Behavior не считается безопасно конвертируемым только по имени класса. Необходимо анализировать фактическую семантику.

---

# 13. IR: Recipe

```text
RecipeNode
├── type
├── ingredients
├── result
├── count
├── experience
├── time
├── conditions
├── unlock
├── postProcessors
├── transformProcessors
└── sourceTrace
```

---

# 14. IR: Entity / Furniture

Entity analysis должна классифицировать сущность:

```text
VANILLA_ENTITY
CUSTOM_ENTITY
BLOCK_ENTITY
DISPLAY_ONLY
FURNITURE_CANDIDATE
UNSUPPORTED_ACTIVE_ENTITY
```

Для BlockEntity отдельно анализировать:

- inventory;
- ticking;
- recipes/processing;
- persistent data;
- openable GUI;
- interactions;
- client sync/networking.

---

# 15. Resource Mapping

## 15.1. Textures

Копировать без изменения, если формат поддерживается target.

Сохранять:

```text
sourcePath
outputPath
namespace
hash
referenceCount
```

## 15.2. Models

Анализировать:

- parent;
- textures;
- elements;
- display transforms;
- predicates/overrides;
- item model tree;
- blockstate variants;
- multipart.

Модели должны быть сопоставлены с актуальной CraftEngine Model System.

## 15.3. Blockstates

Источник:

```text
assets/<modid>/blockstates/*.json
```

Нужно преобразовать:

```text
variant condition
multipart condition
rotation
model
uvlock
```

в CraftEngine state/model configuration.

---

# 16. Capability Matrix

Каждая функция источника должна иметь capability result.

Пример:

```yaml
source:
  type: block_entity_tick

target:
  craftengine: behavior.tick

result: PARTIAL

reason:
  - target supports ticking
  - source accesses custom Java fields
  - persistent processing logic requires extension

suggested:
  type: extension
```

## 16.1. Уровни поддержки

### DIRECT

Механика выражается нативными средствами CraftEngine.

### TRANSFORM

Механика выражается через другое, но эквивалентное API.

### PARTIAL

Только часть поведения сохраняется.

### SCRIPT

Можно автоматически вывести скриптовую реализацию.

### EXTENSION

Необходим Java/Kotlin extension plugin.

### MANUAL

Система создаёт основу, но человек должен дописать логику.

### UNSUPPORTED

Нет безопасного способа переноса.

---

# 17. CraftEngine Target Schema

Target schema должна храниться отдельно от Markdown.

Рекомендуемая структура:

```text
schemas/
└── craftengine/
    └── 26.8/
        ├── items.json
        ├── item_data.json
        ├── item_models.json
        ├── item_settings.json
        ├── item_behaviors.json
        ├── blocks.json
        ├── block_states.json
        ├── block_settings.json
        ├── block_behaviors.json
        ├── furniture.json
        ├── recipes.json
        ├── events.json
        ├── functions.json
        ├── conditions.json
        ├── commands.json
        └── resources.json
```

Каждый schema node:

```json
{
  "name": "fuel_time",
  "type": "integer",
  "required": false,
  "default": 0,
  "versions": [">=26.8"],
  "description": "...",
  "examples": [],
  "dependencies": [],
  "conflicts": [],
  "source": []
}
```

---

# 18. Правила генерации CraftEngine

## 18.1. Нельзя генерировать неизвестные keys

Если отсутствует schema entry:

```text
DO NOT GENERATE
```

## 18.2. Проверка dependencies

Перед генерацией behavior:

```text
behavior
 ↓
required properties?
 ↓
state contains them?
 ↓
values valid?
 ↓
generate
```

## 18.3. Проверка version

Каждый target definition должен привязываться к:

```text
CraftEngine version
Minecraft version
configuration version
```

## 18.4. Source precedence

При конфликте источников:

```text
current repository source
    > official wiki
    > official common-files/examples
    > release notes
    > generated schemas
    > old research report
    > model inference
```

---

# 19. LLM Layer

LLM не должна быть основным parser.

LLM используется для:

- восстановления семантики из Java/Kotlin;
- классификации behavior;
- построения mapping proposals;
- анализа нестандартных игровых механик;
- генерации скриптов;
- объяснения unsupported/partial механик;
- создания manual TODO.

## 19.1. Запреты для LLM

LLM запрещено:

- придумывать CraftEngine properties;
- придумывать behavior IDs;
- использовать неизвестные events/functions;
- смешивать версии CraftEngine;
- заменять валидный schema lookup собственными знаниями;
- считать любой Java-код «переводимым в YAML».

## 19.2. Формат запроса LLM

```json
{
  "source_object": {},
  "source_code": [],
  "resources": [],
  "ir": {},
  "target_version": "26.8",
  "target_schema": {},
  "allowed_output": ["yaml", "mapping", "script", "diagnostics"]
}
```

Ответ LLM:

```json
{
  "mapping": {},
  "generated": {},
  "confidence": 0.0,
  "unsupported": [],
  "manual_tasks": [],
  "reasoning_summary": "..."
}
```

Chain-of-thought в продукт не сохранять; сохранять только безопасное краткое explanation/decision summary.

---

# 19A. Обязательная полнота конвертации контента

Это обязательное функциональное требование, а не рекомендация.

Конвертер должен пытаться перенести **каждый обнаруженный**:

- Item;
- Block;
- BlockState/Property;
- Recipe;
- Loot;
- Furniture;
- Resource.

## 19A.1. Никакого silent drop

Запрещено:

```text
обнаружен объект
↓
не удалось преобразовать
↓
просто проигнорировать
```

Правильно:

```text
обнаружен объект
↓
IR node создан
↓
mapping analysis
↓
DIRECT / TRANSFORM / PARTIAL / SCRIPT / EXTENSION / MANUAL / UNSUPPORTED
↓
generated artifact ИЛИ diagnostic artifact
```

## 19A.2. Property completeness

Каждое обнаруженное property должно иметь запись:

```text
source property
source type
source values
default
source provenance
target mapping
mapping status
reason
```

Даже если target mapping отсутствует.

## 19A.3. Recipe completeness

Каждый найденный recipe serializer/type должен быть проанализирован.

Если serializer неизвестен:

```text
status: UNSUPPORTED
reason: UNKNOWN_SOURCE_SERIALIZER
```

но recipe node всё равно сохраняется в IR.

## 19A.4. BlockState completeness

Каждое состояние должно сохраняться в IR независимо от target:

```text
property name
type
allowed values
default
all observed combinations
variant mapping
model
rotation
condition
```

Нельзя сохранять только default state и терять остальные варианты.

## 19A.5. Item/Block reference completeness

Все зависимости должны сохраняться:

```text
item → recipe
item → model
item → texture
item → tag
item → behavior

block → item
block → model
block → loot
block → tag
block → state
block → behavior
block → block entity
```

Это должно быть представлено dependency graph.


# 20. Output Package и организация файлов

Это обязательный архитектурный раздел. Генератор **НЕ ДОЛЖЕН** складывать все предметы, блоки или рецепты в один общий YAML-файл.

## 20.1. Основное правило: один объект = один YAML

Для каждого отдельного объектного ID создаётся отдельный файл:

```text
Item       → отдельный .yml
Block      → отдельный .yml
Furniture  → отдельный .yml
Recipe     → отдельный .yml
Loot       → отдельный .yml
```

Например:

```text
items/farmersdelight/tomato.yml
items/farmersdelight/bacon.yml
items/farmersdelight/stove.yml

blocks/farmersdelight/stove.yml
blocks/farmersdelight/cutting_board.yml
blocks/farmersdelight/skillet.yml

recipes/farmersdelight/stove_bacon.yml
recipes/farmersdelight/cutting_bacon.yml
```

Запрещено без специального target-specific исключения:

```text
items/all.yml
blocks/all.yml
recipes/all.yml
```

с десятками независимых объектов внутри.

Причины:

- удобное ручное редактирование;
- понятный diff в Git;
- локальная перегенерация одного объекта;
- возможность удалить/заменить один объект;
- удобный поиск;
- минимизация конфликтов при merge;
- возможность использовать файл как единицу provenance;
- удобная работа LLM/IDE;
- независимая валидация.

## 20.2. Каноническая структура результата

```text
<output>/
│
├── manifest.yml
├── README.md
├── conversion-report.md
├── conversion-report.json
│
├── craftengine/
│   │
│   ├── items/
│   │   ├── <namespace>/
│   │   │   ├── item_a.yml
│   │   │   ├── item_b.yml
│   │   │   └── ...
│   │   └── ...
│   │
│   ├── blocks/
│   │   ├── <namespace>/
│   │   │   ├── block_a.yml
│   │   │   ├── block_b.yml
│   │   │   └── ...
│   │   └── ...
│   │
│   ├── furniture/
│   │   ├── <namespace>/
│   │   │   └── furniture_id.yml
│   │   └── ...
│   │
│   ├── recipes/
│   │   ├── <namespace>/
│   │   │   └── recipe_id.yml
│   │   └── ...
│   │
│   ├── loot/
│   │   ├── <namespace>/
│   │   │   └── loot_id.yml
│   │   └── ...
│   │
│   ├── tags/
│   │   ├── blocks/
│   │   ├── items/
│   │   └── ...
│   │
│   ├── translations/
│   │   └── ...
│   │
│   └── config/
│       └── ...
│
├── resourcepack/
│   ├── pack.mcmeta
│   ├── assets/
│   │   ├── <namespace>/
│   │   │   ├── blockstates/
│   │   │   ├── models/
│   │   │   ├── textures/
│   │   │   ├── sounds/
│   │   │   ├── fonts/
│   │   │   ├── equipment/
│   │   │   ├── items/
│   │   │   └── ...
│   │   └── ...
│   └── pack-manifest.json
│
├── extensions/
│   ├── js/
│   │   ├── <namespace>/
│   │   │   └── ...
│   │   └── ...
│   ├── java/
│   │   ├── src/
│   │   └── build/
│   └── README.md
│
├── source-map/
│   ├── objects/
│   │   ├── <namespace>/
│   │   │   ├── item_a.json
│   │   │   ├── block_a.json
│   │   │   └── ...
│   │   └── ...
│   ├── resources.json
│   ├── classes.json
│   ├── registries.json
│   └── mappings.json
│
├── schema/
│   └── target-profile.json
│
├── reports/
│   ├── summary.md
│   ├── summary.json
│   ├── unsupported.md
│   ├── partial.md
│   ├── manual-tasks.md
│   ├── warnings.md
│   └── validation.md
│
└── cache/
    └── ...
```

## 20.3. Namespace directories

На диске namespace должен становиться отдельной директорией:

```text
<type>/<namespace>/<id>.yml
```

Например:

```text
blocks/farmersdelight/stove.yml
```

а не:

```text
blocks/farmersdelight:stove.yml
```

Это снижает проблемы с файловыми системами, shell-командами, IDE и tooling.

## 20.4. Имя файла

Имя файла должно соответствовать последней части ID:

```text
namespace:item_name
        ↓
items/namespace/item_name.yml
```

Для `recipe` аналогично:

```text
namespace:recipe_id
        ↓
recipes/namespace/recipe_id.yml
```

Имя должно быть filesystem-safe.

Если исходный ID содержит символ, запрещённый конкретной ОС, generator обязан использовать безопасное имя и записывать original ID в manifest/source-map.

## 20.5. Один файл — один canonical ID

Файл:

```text
craftengine/items/farmersdelight/tomato.yml
```

обязан содержать именно:

```yaml
id: farmersdelight:tomato
```

Если target-specific синтаксис задаёт ID как mapping key, он всё равно обязан быть однозначно связан с путем файла и manifest.

## 20.6. Никакого случайного смешивания типов

Запрещено:

```text
items/
  tomato.yml
  tomato_recipe.yml
  stove.yml
```

где содержимое одного типа находится в чужой директории.

Должно быть:

```text
items/
  farmersdelight/
    tomato.yml

recipes/
  farmersdelight/
    tomato.yml
```

Одинаковые названия допустимы, потому что тип определяется directory scope.

## 20.7. Полнота каждого generated Item

Для каждого исходного Item генератор обязан создать либо:

1. полноценный `items/<namespace>/<id>.yml`, либо
2. специальный diagnostic artifact, если объект нельзя генерировать безопасно.

**Нельзя молча пропускать Item.**

Перед генерацией Item необходимо попытаться извлечь:

```text
identity
base material
display name
lore
stack size
durability / max damage
food
use / consume
cooldown
equipment
attributes
enchantments
repair
fuel
projectile
components / custom data
tags
model
textures
behavior
events
recipes
references
```

Даже если отдельное исходное свойство не имеет target-аналогов, оно должно попасть в:

```text
IR
↓
source-map
↓
conversion report
```

с результатом:

```text
UNSUPPORTED
PARTIAL
MANUAL
EXTENSION
```

## 20.8. Полнота каждого generated Block

Для каждого исходного Block генератор обязан создать отдельный:

```text
blocks/<namespace>/<id>.yml
```

и попытаться перенести все доступные свойства:

```text
identity
material/carrier
hardness
resistance
light
map color
friction
speed factor
jump factor
replaceable
burnable
fire spread
push reaction
sound
instrument
tool requirements
tags
loot
collision
occlusion
suffocation
view blocking
redstone behavior
waterlogging
placement
breaking
interaction
random tick
scheduled tick
neighbor update
states
properties
default state
variants
models
textures
block entity
behaviors
events
```

**Все state properties обязательны к извлечению в IR**, даже если часть невозможно выразить target-native средствами.

## 20.9. Полнота каждого generated Recipe

Для каждого рецепта:

```text
recipes/<namespace>/<id>.yml
```

с обязательным извлечением всей обнаруженной семантики:

```text
recipe type
ingredients
pattern
key mapping
result
result count
remainder
station
time
experience
conditions
unlock
tags
catalysts
post-processors
transform/processors
custom serializer information
source dependencies
```

Рецепт не может быть удалён только потому, что его исходный serializer неизвестен.

В таком случае создаётся diagnostic artifact:

```text
reports/manual-tasks.md
```

и сохраняется полное описание рецепта в IR/source-map.

## 20.10. Генерация BlockState / State definitions

State не является отдельным объектом-файлом по умолчанию.

Если CraftEngine target требует state внутри block definition:

```text
blocks/namespace/block.yml
    └── state/states/variants
```

то всё состояние конкретного блока должно оставаться внутри его собственного YAML.

Пример организационно:

```text
blocks/farmersdelight/stove.yml
```

может содержать:

```yaml
id: farmersdelight:stove

state:
  # state/properties/variants принадлежат именно этому block
```

Такой подход позволяет одному Block ID иметь произвольное число state combinations без создания сотен файлов.

## 20.11. Не создавать файл на каждый BlockState

По умолчанию:

```text
1 Block ID = 1 Block YAML
N BlockStates = N state combinations внутри этого YAML
```

а не:

```text
stove_north_unlit.yml
stove_north_lit.yml
stove_south_unlit.yml
...
```

Это правило предотвращает взрыв количества файлов.

## 20.12. Индексы

Помимо отдельных YAML-файлов генератор обязан создавать machine-readable index:

```text
source-map/objects/
items-index.json
blocks-index.json
recipes-index.json
furniture-index.json
```

Или:

```text
source-map/index.json
```

Пример:

```json
{
  "items": {
    "farmersdelight:tomato": {
      "file": "craftengine/items/farmersdelight/tomato.yml",
      "status": "DIRECT",
      "confidence": 0.99
    }
  },
  "blocks": {
    "farmersdelight:stove": {
      "file": "craftengine/blocks/farmersdelight/stove.yml",
      "status": "PARTIAL",
      "confidence": 0.94
    }
  }
}
```

Это необходимо для:

- GUI;
- поиска;
- diff;
- повторной генерации;
- LLM;
- IDE integration;
- source mapping.

## 20.13. Manifest

Корень output обязан содержать:

```text
manifest.yml
```

Минимальный состав:

```yaml
converter:
  version: 0.1.0

source:
  loader: neoforge
  mod_id: farmersdelight
  mod_version: "..."
  minecraft_version: "1.21.1"
  input_hash: "..."

target:
  platform: craftengine
  version: "26.8"
  schema_version: "..."

generation:
  timestamp: "..."
  deterministic: true
  mapping_version: "..."
```

Дополнительно:

```yaml
counts:
  items: 0
  blocks: 0
  recipes: 0
  furniture: 0
  resources: 0

statuses:
  direct: 0
  transform: 0
  partial: 0
  script: 0
  extension: 0
  manual: 0
  unsupported: 0
```

## 20.14. README результата

В корне:

```text
README.md
```

должен объяснять:

- какой мод конвертирован;
- какая версия мода;
- под какую версию Minecraft;
- под какую версию CraftEngine;
- какие файлы сгенерированы;
- как установить результат;
- какие части требуют ручной работы;
- где лежит отчет;
- где лежит resource pack;
- где лежат generated extensions.

Программа должна сделать README автоматически.

## 20.15. Reports

## `summary.md`

Человекочитаемая сводка.

## `summary.json`

Машинночитаемая статистика.

## `unsupported.md`

Все полностью неподдержанные объекты.

## `partial.md`

Все частично перенесённые объекты с перечислением потерянных свойств.

## `manual-tasks.md`

Чёткий список действий, которые должен выполнить человек.

Например:

```text
[HIGH] farmersdelight:stove

Причина:
BlockEntity использует custom cooking logic.

Перенесено:
- block
- states
- model
- sounds
- hardness
- inventory representation

Не перенесено:
- custom processing tick

Рекомендуется:
- generated Java extension

Файлы:
- blocks/farmersdelight/stove.yml
- extensions/java/...
```

## 20.16. Resource provenance

Для каждого скопированного resource:

```text
resource path
source hash
output path
output hash
source object references
```

Например:

```json
{
  "source": "assets/farmersdelight/textures/item/tomato.png",
  "sha256": "...",
  "output": "resourcepack/assets/farmersdelight/textures/item/tomato.png",
  "referenced_by": [
    "farmersdelight:tomato"
  ]
}
```

## 20.17. Режимы записи output

CLI должен поддерживать как минимум:

```text
--output <dir>
--clean
--update
--merge
--dry-run
--overwrite
--fail-on-conflict
```

### `--clean`

Удалить ранее сгенерированные converter-owned файлы перед генерацией.

### `--update`

Перегенерировать только изменившиеся исходные объекты.

### `--merge`

Изменять только converter-owned части и сохранять пользовательские файлы.

### `--dry-run`

Ничего не записывать, только показать план генерации.

### `--overwrite`

Разрешить перезапись конфликтующих converter-owned файлов.

### `--fail-on-conflict`

Остановиться при обнаружении файлов, которые нельзя безопасно заменить.

## 20.18. Ownership metadata

Каждый сгенерированный файл должен быть идентифицирован как принадлежащий converter.

Рекомендуемый механизм:

```text
manifest
+
source-map
+
output hash
```

Нельзя полагаться только на комментарий в YAML, поскольку target parser может не сохранять комментарии.

Пример записи:

```json
{
  "path": "craftengine/items/farmersdelight/tomato.yml",
  "owner": "mod-converter",
  "generated_from": "farmersdelight:tomato",
  "source_hash": "...",
  "generator_hash": "..."
}
```

Это позволяет безопасно обновлять только свои файлы.

## 20.19. Incremental conversion

Если изменился только:

```text
assets/farmersdelight/textures/item/tomato.png
```

не нужно повторно анализировать весь мод.

Dependency graph должен определить:

```text
texture
 ↓
model
 ↓
item
```

и перегенерировать только зависимые результаты.

Если изменился:

```text
Tomato item registration
```

перегенерируются:

```text
item
recipe references
loot references
models if needed
source-map entries
```

## 20.20. Stable ordering

Генератор обязан стабилизировать:

- порядок YAML keys;
- порядок списков, где порядок семантически не важен;
- порядок файлов в indexes;
- порядок diagnostics;
- порядок resources в manifest.

Цель:

```text
same input
+
same tool version
+
same mapping
=
same byte-for-byte output
```

где формат файла это позволяет.

## 20.21. Git-friendly output

Формат генерации должен быть оптимизирован для Git:

- один object per file;
- стабильное форматирование;
- стабильный порядок ключей;
- отсутствие случайных UUID;
- отсутствие timestamp внутри object YAML;
- timestamp только в отчётах/manifest при необходимости;
- отсутствие временных данных в production output;
- короткие diff;
- удаление объекта = удаление одного файла.

## 20.22. Пример результата для мода

Для:

```text
farmersdelight
```

результат должен выглядеть примерно так:

```text
converted/farmersdelight/
│
├── craftengine/
│   ├── items/
│   │   └── farmersdelight/
│   │       ├── tomato.yml
│   │       ├── onion.yml
│   │       ├── rice.yml
│   │       ├── skillet.yml
│   │       └── ...
│   │
│   ├── blocks/
│   │   └── farmersdelight/
│   │       ├── stove.yml
│   │       ├── cutting_board.yml
│   │       ├── basket.yml
│   │       └── ...
│   │
│   ├── recipes/
│   │   └── farmersdelight/
│   │       ├── tomato_soup.yml
│   │       ├── fried_egg.yml
│   │       ├── bacon.yml
│   │       └── ...
│   │
│   ├── furniture/
│   │   └── farmersdelight/
│   │       └── ...
│   │
│   ├── loot/
│   │   └── farmersdelight/
│   │       └── ...
│   │
│   └── tags/
│
├── resourcepack/
│   └── assets/
│       └── farmersdelight/
│           ├── models/
│           ├── textures/
│           ├── blockstates/
│           ├── sounds/
│           └── ...
│
├── extensions/
│   ├── js/
│   └── java/
│
├── source-map/
│   ├── index.json
│   ├── objects/
│   ├── resources.json
│   └── mappings.json
│
├── reports/
│   ├── summary.md
│   ├── summary.json
│   ├── unsupported.md
│   ├── partial.md
│   ├── manual-tasks.md
│   └── validation.md
│
├── manifest.yml
└── README.md
```

## 20.23. Запрещённые output patterns

Нельзя:

```text
❌ всё в одном items.yml
❌ всё в одном blocks.yml
❌ всё в одном recipes.yml
❌ случайное смешивание namespaces
❌ перезапись чужих файлов без подтверждения
❌ неотслеживаемые generated files
❌ нестабильная сортировка
❌ UUID/random values без deterministic seed
❌ временные analyzer files рядом с production output
```

Разрешены aggregate/index файлы:

```text
✅ index.json
✅ manifest.yml
✅ reports/*
✅ source-map/*
```

но они не заменяют individual object files.

## 20.24. Acceptance criteria для файловой организации

Для каждого входного:

```text
N Items
M Blocks
K Recipes
F Furniture
```

после успешной генерации должно существовать минимум:

```text
N item YAML files
M block YAML files
K recipe YAML files
F furniture YAML files
```

если соответствующий тип поддерживается target profile.

Если объект не может быть безопасно сгенерирован, вместо молчаливого пропуска должен существовать:

```text
IR node
+
source-map entry
+
report entry
```

Таким образом:

```text
Detected object count
=
Generated object count
+
Diagnostic-only object count
```

и:

```text
Detected object count
-
Generated object count
-
Diagnostic-only object count
= 0
```

Это обязательная проверка полноты конвертации.

# 20.25. Разделение generated, user и imported файлов

Output должен различать три класса файлов:

```text
GENERATED  — полностью управляются конвертером
USER      — созданы/изменены пользователем
IMPORTED  — исходные ресурсы, которые просто перенесены
```

Рекомендуемая структура:

```text
resourcepack/
  generated/
  imported/
  user/
```

Точная структура resource-pack может адаптироваться под target, но ownership должен сохраняться в manifest/source-map.

## 20.26. Temporary files

Никакие analyzer cache, decompiler output, intermediate bytecode и временные файлы не должны попадать в production output.

Они должны находиться в:

```text
.cache/
```

или в системном cache directory.

## 20.27. Конфликты идентификаторов

Если несколько источников определяют одинаковый target ID:

```text
mod_a:thing
mod_b:thing
```

они не конфликтуют, потому что namespaces различаются.

Если один и тот же source namespace импортируется дважды, converter обязан определить:

```text
same source ID
same source hash
```

и переиспользовать объект без дубликатов.

Если:

```text
same source ID
different source hash
```

то результат должен быть:

```text
ERROR: SOURCE_ID_COLLISION
```

## 20.28. Частичная генерация

Конвертация не должна становиться all-or-nothing только из-за одного сложного объекта.

По умолчанию:

```text
one object failure
≠
whole mod failure
```

но `--strict` должен позволять требовать:

```text
any unsupported required object
→ exit code != 0
```

# 21. Conversion Report

Обязательные показатели:

```text
source mod
source version
loader
Minecraft version
target CraftEngine version

items detected
items direct
items partial
items unsupported

blocks detected
blocks direct
blocks partial
blocks unsupported

recipes detected
recipes converted
recipes partial
recipes unsupported

resources copied
resources transformed
resources failed

scripts generated
extensions required
manual tasks
warnings
errors
```

## 21.1. Example

```text
Farmers Delight
────────────────────────
Items:               84
  DIRECT:            71
  PARTIAL:            9
  EXTENSION:          2
  UNSUPPORTED:        2

Blocks:              113
  DIRECT:            74
  PARTIAL:           28
  SCRIPT:             7
  EXTENSION:          3
  UNSUPPORTED:        1

Recipes:             167
  DIRECT:            159
  PARTIAL:             8

Textures:            241 / 241
Models:              173 / 173

Manual tasks:         19
Errors:                0
```

---

# 22. CLI

Минимальный CLI:

```bash
converter scan mod.jar
converter analyze mod.jar
converter convert mod.jar --target craftengine:26.8
converter validate output/
converter report output/
converter diff old-output new-output
```

Дополнительные параметры:

```bash
--minecraft 1.21.4
--loader neoforge
--target craftengine:26.8
--output ./output
--llm enabled|disabled|auto
--strict
--copy-unsupported
--generate-stubs
--generate-report
--overwrite
```

## 22.1. Strict mode

В strict mode:

- неизвестный target key = error;
- unsupported behavior = error;
- schema conflict = error;
- missing dependency = error;
- low-confidence auto mapping = warning/error согласно threshold.

---

# 23. GUI

GUI должна показывать:

```text
Mod
├── metadata
├── items
├── blocks
├── entities
├── recipes
├── resources
├── behaviors
└── errors
```

Для каждого объекта:

```text
[Source]
[IR]
[Mapping]
[CraftEngine Output]
[Warnings]
[Manual Tasks]
[Preview]
```

Для Blocks обязательно отображать:

- state properties;
- variant preview;
- model preview;
- carrier block, если применяется;
- required behaviors;
- event flow.

---

# 24. Preview / Validation

Система должна иметь два режима:

### Static validation

Проверяет:

- YAML parse;
- schema;
- required keys;
- enum values;
- property dependencies;
- IDs;
- references;
- resource paths;
- duplicate IDs.

### Runtime validation

Опционально запускать изолированный Minecraft test server с CraftEngine и автоматически:

- загрузить generated package;
- смотреть console errors;
- проверять `/ce reload`;
- получить generated item;
- разместить generated block;
- проверить state transitions;
- проверить recipes;
- сохранить screenshots/logs.

Runtime validation является главным критерием «conversion succeeded».

---

# 25. Test Architecture

## 25.1. Unit tests

Проверять:

- parser;
- IR builders;
- mappings;
- schema resolver;
- YAML generation;
- resource copying;
- variant generation.

## 25.2. Golden tests

Для каждого известного объекта:

```text
source fixture
→ expected IR
→ expected YAML
```

## 25.3. Integration tests

Минимальный набор:

- простой item;
- food;
- tool;
- simple block;
- block with facing;
- boolean state;
- integer state;
- fence;
- lamp;
- furniture;
- shapeless recipe;
- shaped recipe;
- loot table;
- block interaction;
- item right click;
- tick behavior.

## 25.4. Regression corpus

Нужно иметь набор реальных mods:

- vanilla-like content;
- Farmers Delight;
- small utility mods;
- decoration mods;
- recipe-heavy mods;
- block-entity-heavy mods;
- entity-heavy mods.

---

# 26. Farmers Delight — обязательный эталонный проект

Farmers Delight должен использоваться как reference conversion corpus.

Причина:

- много custom blocks;
- много food items;
- много recipes;
- cutting board;
- stove;
- ropes/fences;
- block states;
- custom models;
- staged consumables;
- complex interactions.

Каждая ошибка на этом проекте должна превращаться в regression test.

---

# 27. Специальная система Mapping Rules

Mapping должен быть data-driven.

```text
mappings/
├── common/
├── forge/
├── fabric/
├── neoforge/
└── craftengine/
```

Пример:

```yaml
source: minecraft.block.FenceBlock
rule: fence_block
requires:
  - north:boolean
  - east:boolean
  - south:boolean
  - west:boolean
target:
  behavior: fence_block
```

Mapping rule не должна быть зашита исключительно в код.

---

# 28. Priority of Conversion

Приоритет автоматизации:

### Tier 1 — полностью автоматизируемое

- items;
- basic blocks;
- textures;
- models;
- translations;
- basic recipes;
- loot;
- simple states;
- tags.

### Tier 2 — автоматизация с mapping

- food;
- tools;
- directional blocks;
- fence/wall/stem-like blocks;
- furniture;
- conditional models;
- simple behaviors;
- interaction actions.

### Tier 3 — LLM-assisted

- сложный custom Java behavior;
- BlockEntity logic;
- unusual interactions;
- complex processing;
- scripted state transitions.

### Tier 4 — extension/manual

- custom network protocols;
- arbitrary server systems;
- complex GUI handlers;
- deep client renderer logic;
- arbitrary AI;
- custom shaders requiring unsupported client modifications.

---

# 29. Security

JAR/bytecode/source является недоверенным входом.

Сканер не должен выполнять код мода.

Запрещено при анализе:

- loading mod into JVM;
- invoking mod static initializers;
- classpath execution;
- launching arbitrary native processes.

Bytecode анализировать статически.

LLM prompt должен очищать потенциальные prompt injection payloads из источника.

Generated scripts/extensions не должны автоматически выполняться до прохождения validation.

---

# 30. Производительность

Сканирование должно быть многоступенчатым:

```text
Phase 1: metadata
Phase 2: resources
Phase 3: registries
Phase 4: targeted bytecode
Phase 5: deep semantic analysis
Phase 6: LLM only where needed
```

LLM не использовать на объектах с `DIRECT` mapping.

Кэшировать:

- file hashes;
- parsed JSON;
- class analysis;
- resource graph;
- mapping decisions;
- LLM decisions.

---

# 31. Logging

Каждое действие должно иметь structured log:

```json
{
  "timestamp": "...",
  "stage": "mapping",
  "object": "farmersdelight:stove",
  "decision": "PARTIAL",
  "rule": "block_entity_tick_to_behavior",
  "confidence": 0.91
}
```

---

# 32. Версионирование

Должны быть независимо версионируемы:

```text
converter version
source loader profile
Minecraft source version
target CraftEngine version
target schema version
mapping version
```

Пример:

```text
converter 0.1.0
source neoforge-1.21.1
target craftengine-26.8
schema craftengine-26.8.0
mapping 0.4.2
```

---

# 33. Расширяемость

Плагинная система должна позволять добавлять:

```text
SourceLoaderAdapter
ResourceAdapter
BytecodeAdapter
IRAnalyzer
MappingProvider
TargetGenerator
Validator
```

В будущем:

```text
Forge → IR → CraftEngine
Fabric → IR → CraftEngine
NeoForge → IR → CraftEngine
Quilt → IR → CraftEngine

IR → CraftEngine
IR → ItemsAdder
IR → Oraxen
IR → custom target
```

---

# 34. MVP

Первый рабочий релиз должен поддерживать:

1. NeoForge/Fabric metadata detection.
2. Items.
3. Basic blocks.
4. Block states.
5. Models.
6. Textures.
7. Lang.
8. Basic recipes.
9. Loot.
10. Tags.
11. Basic CraftEngine behaviors.
12. YAML generation.
13. Validation.
14. Conversion report.
15. CLI.

LLM на MVP — только optional.

---

# 35. Version 2

Добавить:

- complex behaviors;
- block entity detection;
- generated scripts;
- furniture mapping;
- advanced recipes;
- source-code semantic graph;
- GUI;
- runtime test server;
- regression corpus.

---

# 36. Version 3

Добавить:

- multi-mod dependency graph;
- automatic behavior synthesis;
- cross-loader mappings;
- automated Java/Kotlin extension generation;
- visual model/state editor;
- one-click runtime verification;
- alternative target platforms.

---

# 37. Критерии готовности проекта

Проект считается готовым к production, если:

- 95%+ простых item/block assets переводятся без ручного вмешательства;
- ни один unknown target key не генерируется молча;
- каждый generated object имеет source provenance;
- каждый partial/unsupported объект имеет причину;
- runtime validation может обнаружить load errors;
- повторный запуск выдаёт воспроизводимый результат;
- regression corpus не деградирует;
- target schema полностью отделена от LLM;
- LLM не имеет права обходить schema validation.

---

# 38. Ключевой принцип проекта

Конвертер не должен обещать:

> «Любой мод можно автоматически переписать в CraftEngine».

Он должен обещать:

> **«Мы автоматически переносим всё, что можем доказать как совместимое, интеллектуально реконструируем сложные механики, а всё остальное прозрачно показываем разработчику.»**

Именно этот принцип является главным критерием качества системы.

---

# 39. Связь с CraftEngine documentation

Документ `CraftEngine_Complete_Documentation_FINAL.md` используется как исходный knowledge/reference слой для target platform. Он должен быть дополнен machine-readable schema, чтобы генератор не использовал Markdown как единственный источник истины.

Приоритет:

```text
CraftEngine source / актуальный schema
        ↓
CraftEngine common-files/examples
        ↓
official wiki/reference
        ↓
machine-readable target schema
        ↓
LLM context
```

Markdown предназначен для разработчика и reasoning-контекста.

JSON/YAML schema предназначена для validator и generator.

---

# 40. Финальный результат продукта

Пользователь должен иметь возможность выполнить:

```bash
converter convert farmersdelight-1.0.0.jar \
  --target craftengine:26.8 \
  --minecraft 1.21.4 \
  --output ./converted/farmersdelight
```

После выполнения каждый независимый Item/Block/Recipe должен быть отдельным YAML-файлом. Converter обязан автоматически создать namespace-папки, индексы, manifest, source-map и отчёты.


```bash
converter convert farmersdelight-1.0.0.jar \
  --target craftengine:26.8 \
  --minecraft 1.21.4 \
  --output ./converted/farmersdelight
```

и получить:

```text
converted/farmersdelight/
├── craftengine/
├── resourcepack/
├── extensions/
├── reports/
└── converter-manifest.json
```

После чего пользователь может открыть `reports/summary.md` и увидеть:

```text
✅ 356 objects converted
⚠  42 objects require review
⚠  17 objects require generated behavior
❌  3 objects unsupported

Resource pack: VALID
YAML schema: VALID
Dependencies: VALID
Runtime smoke test: PASSED
```

Это является целевым состоянием продукта.
