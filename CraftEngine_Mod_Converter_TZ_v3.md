# ТЗ и размышление: путь к полноценной конвертации модов в CraftEngine

**Версия:** 1.0
**Дата:** 2026-09-06
**Статус:** draft / detailed
**Автор:** инженерная сессия `arena/01a07389-craftengine-converter`
**Репозиторий:** `amper24/CraftEngine-Converter`
**Целевая платформа:** CraftEngine 26.8.x
**Язык:** русский

---

## 0. Краткое резюме

Конвертер уже умеет многое: детектирует Fabric/Forge/NeoForge/Quilt, строит IR, классифицирует объекты
(DIRECT / TRANSFORM / PARTIAL / SCRIPT / EXTENSION / MANUAL / UNSUPPORTED), генерирует CraftEngine YAML,
которая проходит статическую валидацию и fidelity-проверки, копирует ресурспак как есть.

На сегодняшнем тестовом моде (`veggiesdelight`) конверсия даёт:

```text
items=257 blocks=29 recipes=192 resources=~385-637
DIRECT=357 TRANSFORM=62 UNSUPPORTED=46 PARTIAL=0
validation: valid=True, issues=0
resourcepack: verbatim=True
```

Но это не «полноценная» конвертация. Оставшиеся проблемы:

1. **UNSUPPORTED = 46** — это молча принятые потери после честного отчёта. Для пользователя это «не сделано».
2. **Определение моделей/текстур и предметов** всё ещё эвристическое; часть assets может копироваться corpus
   целиком, но не всегда сопоставляется с объектами (нет индекса «какая текстура какому предмету»).
3. **Нет настоящего семантического слоя** для блоков/поведений: auto_state/behaviors/functions/conditions
   генерируются частично, большая часть — по умолчанию/эвристике.
4. **Нет «горизонтальных» ресурсов**: шейдеры, atlases, fonts, particle definitions, equipment, GUI-слоты
   не разбираются в IR и не проверяются.
5. **Категории и «смысловые» группировки** построены на именах и малом наборе тегов, а не на реальном
   содержимом мода и CraftEngine-контрактах.
6. **Нет расширяемого плагинного слоя** для сторонних recipe/block/item serializers, которые сейчас
   уходят в UNSUPPORTED (Create, Botany Pots, Immersive Engineering и т.д.).

Этот документ — обновлённое **ТЗ + размышление** о том, что и как улучшить, чтобы конверсия стала
полноценной, а определение — «хорошим»: редкие пропуски, честная классификация, доверяемая генерация.

---

# ЧАСТЬ A. Размышление: что уже хорошо и где теряется точность

## A.1. Текущая архитектура (что есть)

```text
Мод (JAR/директория)
  │
  ├─ ModDetector (detector.py)
  │     loader/id/namespace/version/deps
  │
  ├─ Analyzer (analyzer.py)
  │     ресурсы → assets verbatim (все namespace)
  │     lang / sounds / recipes / loot / tags
  │     ItemNode / BlockNode / RecipeNode / LootNode / ResourceNode
  │     bytecode food + semantics + crop relations
  │     reference-driven items (создание предметов из рецептов/лута)
  │
  ├─ Capability (capability.py)
  │     DIRECT / TRANSFORM / PARTIAL / SCRIPT / EXTENSION / MANUAL / UNSUPPORTED
  │
  ├─ Generator (generator.py, modeldefs.py, sliceboard.py)
  │     items / blocks / recipes / loot / categories / translations / sliceboard
  │
  ├─ Packager (packager.py)
  │     configuration/, resourcepack/, source-map/, reports/, manifest, pack.yml
  │
  ├─ Validator (validator.py) + SchemaRegistry (schema.py)
  │     неизвестные ключи, типы поведений, рецептов, ресурсы
  │
  └─ Fidelity (fidelity.py)
        missing item/block/recipe/model/texture, lost variants
```

### Сильные стороны (на них стоит опираться)

- **Fidelity-first**: ничего не удаляется молча; всё либо генерируется, либо попадает в diagnostic/report.
- **`resourcepack/assets` копируется verbatim** (последний фикс): не теряются ни namespace, ни `assets/minecraft`
  оверрайды, ни шейдеры/атласы/шрифты.
- **Мульти-неймспейс анализ**: контент сканируется по всем namespace, а не только по modId.
- **Явная схема**: `schemas/craftengine/26.8/*.json` позволяет валидировать генерируемые ключи и типы,
  а `validator._validate_nested_keys` ловит неизвестные ключи на уровне предупреждений.
- **Ясные статусы**: DIRECT/TRANSFORM/PARTIAL/SCRIPT/EXTENSION/MANUAL/UNSUPPORTED + причина.
- **Использование реальных CraftEngine-источников**: свойства, поведения, recipe serializers, pack.yml,
  категории — сверены с Java-кодом CraftEngine 26.8.

## A.2. Главные корневые причины неполноты

| # | Причина | Где проявляется |
|---|---------|-----------------|
| 1 | Детекция «от ресурсов», а не «от регистраций» | Предметы/блоки определяются по наличию `models` / `blockstates`, а не по registry-факту (item registry / block registry). Если у предмета нет модели — он может потеряться, даже если он зарегистрирован в Java. |
| 2 | Слабый semantic-слой блоков | `auto_state`, `behaviors`, `settings`, `functions/conditions` в основном по эвристике/именам/умолчаниям. |
| 3 | Нет «горизонтального» разбора всех asset-доменов | `atlases`, `shaders`, `fonts`, `particles`, `equipment`, `gui`, `textures` индексируются как `asset`, но не проверяются на корректность/связность. |
| 4 | Сторонние рецепты закрыты хардкодом | `farmersdelight:*` — отдельные ветки; Create/Botany/ImmersiveEngineering — UNSUPPORTED. Нужен расширяемый реестр «source type → mapper/plugin». |
| 5 | Нет связи «данные рецептов → предметные факты» | `can_place_on`, `use_remainder`, `max_stack_size`, `repair_ingredient` и другие data components добываются из bytecode частично. |
| 6 | Категории строятся по именам | Нужно строить по реальным группам (теги, recipe book categories, `category` из рецептов, источники). |
| 7 | Нет coverage-метрик | Нет «сколько source items/blocks/recipes оперируем», «сколько ссылок не разрешено», «сколько текстур без владельца». |
| 8 | Нет runtime-смоук-теста | Статическая валидация не проверяет, что CraftEngine реально загрузил конфиг без ошибок. |

---

# ЧАСТЬ B. ТЗ улучшений

## B.1. Цели и критерии приёмки

### Цель

Сделать конвертер «полноценным»: предсказуемо переносить максимум контента, не изобретать ключи,
честно классифицировать не поддерживаемое, и давать пользователю понятную метрику полноты.

### Приёмка (измеримая)

- [ ] `UNSUPPORTED` для VeggiesDelight и типовых модов снижается до `< 10%` от рецептов и `< 5%` от предметов/блоков.
- [ ] Каждому `ItemNode`/`BlockNode` соответствует **либо generated YAML**, либо `diagnostic` с причиной и исходным `raw`.
- [ ] Для каждого сгенерированного `model`/`texture` любая ссылка на модель/текстуру разрешается в фактический файл `resourcepack/assets`.
- [ ] Всё содержимое `assets/` копируется verbatim (уже сделано) и подтверждается `pack-manifest.json` (`verbatim: true`, полный список).
- [ ] Валидатор не даёт параметру `valid=True`, если есть неизвестный ключ/тип в критичных секциях (`items/blocks/recipes`), которые CraftEngine отклонит.
- [ ] Все категории, созданные конвертером, ссылаются только на реально сгенерированные предметы; нет «битых» категорий.
- [ ] Появляется отчёт `reports/coverage.json` с метриками: detected, generated, unsupported, unresolved refs, orphan textures, model/texture coverage.
- [ ] Регрессионные тесты покрывают: мульти-неймспейс, `assets/minecraft` оверрайды, texture-only items, современные `items/*.json`, шейдеры/атласы/шрифты, сторонние рецепты, блоки с multipart, crops, loot.

## B.2. Нефункциональные требования

1. **Безопасность и предсказуемость.** Конвертер никогда не пишет в исходный мод; выходной каталог можно удалить и пересобрать.
2. **Детерминизм.** Тот же JAR → тот же выход при тех же настройках (уже частично выполняется; нужно зафиксировать сортировку и `utc_now`).
3. **Скорость.** Полная конверсия среднего мода (≈500-1500 ресурсов) — до 30 сек, инкрементальный повтор — до 5 сек.
4. **Расширяемость.** Новые recipe/behavior/item serializers добавляются конфигом/плагином, без правок генератора.
5. **Наблюдаемость.** Каждый шаг логируется с `phase`; отчёты содержат `object_id`, `status`, `reason`, `source`, `confidence`.
6. **Нет invented keys.** Любой генерируемый ключ должен быть в `schemas/craftengine/26.8/*.json` либо явно помечен как `unsafe/diagnostic`.

---

## B.3. Блок требований «Определение и детекция»

### B.3.1. Детектор мода (`detector.py`)

- [ ] Поддержка **Polymod / mixin / JIJ** и множественных метаданных: приоритет `neoforge.mods.toml` > `mods.toml` > `fabric.mod.json` > `quilt.mod.json`, с конфликтами в `metadata_conflicts`.
- [ ] **Набор namespace**: `meta.namespace` + все `assets/*`/`data/*` (кроме `minecraft`/`c`/`forge`/`fabric`/`neoforge`/`quilt`), сохраняя основной первым.
- [ ] **Resolve namespace из registry-источников**, не только из folder name: читать `neoforge.mods.toml`/`mods.toml` `[[mods]] displayName`, `iconFile`, `logoFile`, `modId`.
- [ ] Собирать **dependencies и optional** точнее: parse `[[dependencies.<owner>]]` уже есть; добавить `minecraft`/`java`/`fabricloader`/`quilt_loader` по типу `required`/`optional`, `mandatory`/`versionRange`.
- [ ] Метрики: `content_namespaces` + `dependency_namespaces` + `asset_namespaces` в `metadata.raw`.

### B.3.2. Настоящее «определение предметов» (registry-ориентированное)

Сейчас предмет определяется по `models/item/*.json` + `assets/items/*.json` + текстурам + lang.
Предлагается добавить **RegistryRefResolver**:

- [ ] **Fabric/Quilt**: парсить `Entrypoint` и `@Register`-подобные вызовы (`Registry.register(Registries.ITEM, Identifier, ...)`, `BuiltInRegistries.ITEM.register(...)`) из bytecode (bytecode-семантика уже есть; расширить на регистрации).
- [ ] **Forge/NeoForge**: парсить `DeferredRegister.Items`, `RegistryObject`, `@Mod.EventBusSubscriber`-события регистрации, `withAssociatedBlockItem`.
- [ ] Сопоставлять **регистрированный ID ↔ asset path**, даже если `models/item/<id>.json` отсутствует.
- [ ] Если предмет существует (registry факт), но нет модели/текстуры — создавать `ItemNode` с `confidence=0.7`, статусом `PARTIAL/MANUAL`, но **не терять**.
- [ ] Аналогично для блоков: registry факт ↔ `blockstates/<id>.json`; если blockstate нет, но есть registry item/block — `PARTIAL` с сообщением.

### B.3.3. Определение текстур и моделей с привязкой к объекту

- [ ] Построить **AssetIndex** одного прохода:
  - `model <- { ns, path, parsed }` для `models/{item,block}/*.json`,
  - `texture <- { ns, path }` для `textures/**/*.png`,
  - `blockstate <- ...`, `shader <- ...`, `atlas <- ...`, `font <- ...`, `particle <- ...`, `equipment <- ...`, `gui <- ...`.
- [ ] Ресолвить ссылки в моделях (`parent`, `textures.*`, `particle`, `gui/...`) **рекурсивно** и находить «осиротевшие» текстуры (файл есть, но не используется ни одним объектом).
- [ ] По каждому `ItemNode`/`BlockNode` сохранять не только `textures`, но и `model_dependencies: list[id]` и `texture_dependencies`.
- [ ] Для предмета без модели, но с `texture:<id>.png`, создавать `model: {type:minecraft:model, path: ns:item/<id>, generation:{parent:item/generated, textures:{layer0: ns:item/<id>}}}` (или `texture:` упрощённый — уже есть).
- [ ] Для **современных item model definitions** (`assets/ns/items/*.json`) обрабатывать `minecraft:model/composite/condition/select/range_dispatch/special` с валидацией полей (`model/on_true/on_false/cases/fallback/entries/property/transformation`). Уже частично в `modeldefs.py`; нужна валидация типов и `CheckModel` `Keys`.
- [ ] Не выдумывать модель из ничего: если модели нет и текстуры нет — **не генерировать** `model`, а пометить `MANUAL` (нет `material` тоже, только diagnostic).

---

## B.4. Блок требований «IR»

- [ ] `BlockNode.auto_state` сделать `str | dict[str,Any] | None` (поддержка `{type, id}`), и в `_block_states`/`_crop_appearances`/`_entity_variant_appearances` эмитить правильно. *(Это известный незакрытый пункт.)*
- [ ] `BlockNode.behaviors` → `list[dict]` (сейчас `list[str]`), `behavior_configs` — единый формат.
- [ ] `ItemNode` добавить:
  - `registry_source: dict` (класс/метод/event, откуда взят ID),
  - `model_dependencies`, `texture_dependencies`,
  - `data_components: dict` (собранные из bytecode + datagen),
  - `category_hints: list[str]` (recipe-book categories / tags / source groups),
  - `used_in_recipes`, `used_in_loot`, `used_as_tool`, `used_as_container`.
- [ ] `BlockNode` добавить:
  - `hardness`, `resistance`, `light_emission`, `sound_set`, `tags` (уже), `piston_behavior`, `instrument`, `push_reaction`, `replaceable`, `occludes`, `solid`, `collision` — когда реально известны.
  - `block_entity_type` + `block_entity_id` (для `EXTENSION`/`SCRIPT`).
- [ ] `RecipeNode` добавить `source_metadata` (recipe book tab, category, group, custom station).
- [ ] `LootNode` — единый формат `pools -> entries -> functions -> conditions` (основное есть).
- [ ] `ResourceNode` добавить `asset_domain` (`models|textures|blockstates|...`), `ascii_path`, `is_referenced`.

---

## B.5. Блок требований «Предметы»

- [ ] **Materials**: политика уже «без material кроме напитков/супов». Добавить:
  - `settings` → нет фабрикаций; для предметов без материала, но с `block_item` не эмитить `material`.
  - Проверять, что `data`-ключи соответствуют `schema.item_data_keys`.
- [ ] **Components из bytecode**:
  - `max_stack_size` (уже в `ItemNode`; эмитить как `max_stack_size` в data? — проверить schema, не invent).
  - `max_damage`, `damage`, `enchantment`-таблицы, `attribute_modifiers`, `equippable`, `food`, `consumable`, `tool`, `repairable`, `enchantable`, `glowing`, `fire_resistant`.
- [ ] **Байткод-имя → item**: canonicalize (уже есть) но расширить на:
  - `NamedEntry` и `RegistryObject` поля (`id`, `name`, `value`),
  - `public static final Item NAME` и `public static final RegistryObject<Item> NAME`,
  - `DeferredItem`.
- [ ] **Сегменты моделей/икон**:
  - `gui_icon_texture` уже есть; добавить `gui_icon_model` и `icon_for_*` (block_item icons),
  - автоматически «сжимать» предметы с блочной моделью в `block_item` behavior.
- [ ] **Категории**: `_item_category` → учитывать `category_hints` (recipe book) перед name-heuristics.

---

## B.6. Блок требований «Блоки»

- [ ] **auto_state map**: уже подтверждено, что CraftEngine принимает `{type, id}`; поддержать в `analyzer.py`/`generator.py`.
- [ ] **Блоки-варианты**:
  - полный parse `blockstates/...` `variants` + `multipart` (есть),
  - `weight`, `uvlock`, `x/y/z` уже берутся из variants (частично; проверить, что пишутся в `appearance`).
  - `when` (`OR`, `AND`, `IN`) и `part` в `multipart` — нормализовать в CE `condition`-подобные варианты или честно `PARTIAL`.
- [ ] **Блоки-кропы**:
  - `crop_block` behavior уже есть (grow_speed, light, bone_meal) — добавить `age_property`, `stage_count`, `state_dependencies`.
  - блок-лут для кропов уже генерируется; добавить `seed`/`produce` в `loot` schema.
  - пересчитать `stage_models` по `blockstates` (`age=N` → model) без reliance на bytecode-факты.
- [ ] **Блоки c block entities**: явно `PARTIAL`/`EXTENSION`, с `block_entity_type`, и **не** пытаться их эмитировать как обычные.
- [ ] **Блоки с поведением**: `strippable_block`, `sapling`, `lamp`, `door`, `stairs`, `slab`, `fence_gate`, `pressure_plate`, `waterlogged` — использовать авторитетный список behavior types из `BukkitBlockBehaviors`, если они реально нужны; иначе не выдумывать.
- [ ] **Block settings**: `hardness`, `resistance`, `light`, `sound`, `tags`, `require_correct_tools`, `correct_tools` — брать из `loot`/`tags`/`sounds.json`/данных, а не дефолтов.
- [ ] `BlockNode.settings` сейчас уходит в `settings`; проверить, что `hardness`/`resistance` действительно есть в `block_settings` schema.

---

## B.7. Блок требований «Рецепты»

- [ ] **Реестр сторонних serializers**: создать `mappings/recipe_mappings.json` или `converter/recipe_mappers/`:
  ```json
  {
    "farmersdelight:cooking": {"station": "crafting_table", "transform": "shapeless_transform", "mapper": "fd_cooking"},
    "farmersdelight:cutting": {"station": "sliceboard", "mapper": "sliceboard"},
    "create:milling": {"station": "unsupported", "reason": "...", "mapper": "diagnostic"},
    "botanypots:block_derived_crop": {"station": "unsupported", "reason": "...", "mapper": "diagnostic"}
  }
  ```
- [ ] **Для каждого стороннего type** — либо transform, либо честный `UNSUPPORTED` с причиной и ссылкой на `raw`.
- [ ] **Shapeless/Shaped**:
  - различать `source: true` (transform) — уже есть,
  - `ingredient count > 1` (map format) — уже есть в `CustomShapelessTransform`; перенести в `RecipeNode.ingredient_counts`.
  - **condition** и **transform_processors** из source — сохранять в диагностик, не invent в генераторе.
- [ ] **Cooking recipes**: `experience`, `time`, `category` уже есть для native; добавить `recipe_category` (`misc`, `food`, `blocks`) и `show_notification`.
- [ ] **Stonecutting / smithing** — уже native; проверить `template_type`/`template`/`base`/`addition` и `pattern` для trim.
- [ ] **Brewing** — `container`, `ingredient`, `result`, `time`, `experience`; проверить CE-контракт (`CustomBrewingRecipe`).
- [ ] **Recipe book** — сохранять `category`/`group`/`recipe_book_tab` в diagnostic, но не выдумывать в YAML, если схема их не поддерживает.
- [ ] **Unresolved tags**: политика `skip`/`keep` уже есть; добавить третий режим `expand_common_tags` с реестром общих тегов (`c:tools/knife`, `forge:dusts`, `minecraft:planks`).

---

## B.8. Блок требований «Лут, теги, зависимости»

- [ ] **Лут**: нормализовать `loot_table`/`loot` функции/условия в CE-native условия (`LootFunctions`, `CommonConditions`). Уже частично есть; добавить:
  - `set_count`, `apply_bonus`, `explosion_decay`, `drop_exp`, `limit_count`,
  - `match_block_property`, `random`, `survives_explosion`, `falls`, `biome`, `enchantment`, `table_bonus`.
- [ ] **Теги**: уже собираются; добавить:
  - перенос `tags` → `item.settings.tags` (есть) и `block.settings.tags` (есть),
  - отдельный **tag vendor** в resourcepack: если тег не vanilla и не создан конвертером, не ссылаться в YAML (иначе может быть пустой).
- [ ] **Зависимости**: `metadata.dependencies` → `fidelity` warning для внешних namespace; добавить `dependency_bundles` (например `farmersdelight` уже вскрыт через `container_items`).

---

## B.9. Блок требований «Категории»

- [ ] Строить по **реальным группам**, а не неймингу:
  1. технологические теги (`#mod:tools`, `#mod:food` и т.д.),
  2. recipe book categories / recipe `category`,
  3. generic categories (`misc`), если есть в source,
  4. fallback по имени.
- [ ] **Не создавать пустые категории**; `hidden: true` для подкатегорий — уже есть.
- [ ] **Иконки**: только реально сгенерированные предметы; если первая запись не иконопригодна (display item) — выбрать первый «plain» предмет.
- [ ] **Трансляции**: уже генерируются `en_us`/`ru_ru` и сливаются в resourcepack lang. Добавить поддержку всех локалей из мода (`de_de`, `fr_fr`, ...) и не перетирать существующие.
- [ ] **Main category**: `list: [#ns:item, ...]` — уже есть; проверить `priority` (у `main` 1, у остальных от 10) и отсутствие дуплей.

---

## B.10. Блок требований «Resource pack»

- [x] **Verbatim копирование всего `assets/`** — сделано.
- [ ] **Resourcepack** добавлять `pack_format` по target MC (сейчас 34); автоматически вычислять из `minecraft_version` (1.21.1→34, 1.21.4→46/... ) или взять из конфига.
- [ ] **Проверять отсутствие битых ссылок** внутри скопированных моделей (`parent`, `textures`): `fidelity.py` уже делает; расширить на `assets/minecraft` оверрайды (сейчас они попадут как `minecraft`, а fidelity может считать их внешними).
- [ ] **Atlases/fonts/particles/shaders**: не разбираются в IR, но копируются. Добавить `assettree` индексы и в отчёты какие из них используются.
- [ ] **Звуки**: `sounds.json` уже собирается; добавить блочные sound settings `block.<path>.break/step/place/hit/fall` с проверкой на наличие событий.

---

## B.11. Блок требований «Fidelity и валидация»

- [ ] **SchemaRegistry** расширить:
  - item model node types и их поля из `BaseItemModel` / `ConditionItemModel` / `SelectItemModel` / `CompositeItemModel` / `RangeDispatchItemModel` / `SpecialItemModel`,
  - block `states` поля (`properties`, `appearances`, `variants`), `auto_state` (str/map), `entity_renderer`, `culling`,
  - все behavior types (item/block) из `BukkitItemBehaviors`/`BukkitBlockBehaviors`,
  - conditions/functions из `CommonConditions`/`LootFunctions`.
- [ ] **Валидатор**:
  - `_validate_nested_keys` уже warning-level; сделать CLI-флаг `--strict-schema` / settings `strict_schema: true`, при котором unknown → `error` и `valid=false`.
  - Добавить **resource link validation** прямо в `validate_output`: каждый `model.path` / `texture` / `generation.textures.*` → файл существует.
  - Добавить **category validation**: список категорий содержит только существующие item IDs.
  - Добавить **recipe validation**: `type` в `recipe_confirmed_types` (сейчас warning).
- [ ] **Fidelity**:
  - считать покрытие: `generated / detected` на предметы, блоки, рецепты,
  - `unresolved_references` и `orphan_textures` как отдельные метрики,
  - `missing_model_reference` / `missing_texture_reference` уже есть — добавить `unused_asset`, `broken_asset_model_parent`, `lost_block_behavior`.

---

## B.12. Блок требований «Отчёты и UX»

- [ ] `reports/coverage.json`:
  ```json
  {
    "items": {"detected": 257, "generated": 250, "partial": 4, "unsupported": 3},
    "blocks": {...},
    "recipes": {...},
    "models": {"total": ..., "with_generation": ..., "referenced": ...},
    "textures": {"total": ..., "used": ..., "orphaned": ...},
    "unresolved_references": [...],
    "broken_references": [...]
  }
  ```
- [ ] `reports/unsupported.md` и `partial.md` — добавить `why` + `what_to_do` + `source_file`.
- [ ] `README.md` / GUI — показать «полнота конверсии X%» и итоговый чек-лист.
- [ ] CLI: добавить `--dump-ir` (вывести IR), `--strict`, `--no-assets`, `--minecraft` autodetect, `--settings` path.

---

## B.13. Блок требований «Байткод-семантика»

- [ ] Расширить `bytecode_semantics`:
  - `DeferredRegister` → `RegistryObject`,
  - `@Mod.EventBusSubscriber` + `RegisterEvent`,
  - `BlockBehaviour.Properties.of()`, `Item.Properties`, `FoodProperties` — в единые `facts`.
- [ ] **Crops**: уже есть `extract_crop_relations`; добавить парсинг `CropBlock`/`StemBlock`/`SugarCaneBlock` для auto_state.
- [ ] **Живые предметы (armor/tools)**: уже есть `gear_*` heuristics; добавить `attribute_modifiers` из bytecode (`sword`, `pickaxe`, `axe`, etc.).
- [ ] **Mods datagen**: `data/<mod>/recipe/`, `loot_table/` уже есть; добавить `data/<mod>/tags/`, `data/<mod>/advancement/`, `data/<mod>/worldgen/` как diagnostic-only.

---

## B.14. Блок требований «Расширяемость»

- [ ] **Plugin/contracts**:
  ```python
  class SourceMapper(Protocol):
      def accepts(self, rtype: str) -> bool: ...
      def map(self, recipe: RecipeNode, ctx: Context) -> list[GeneratedFile] | None: ...
  ```
  Зарегистрировать в `mappings/recipe_mappings.json`, чтобы новые сторонние рецепты не требовали правок ядра.
- [ ] **Projection** (`target_profile`): schema версия, авторитетные типы, доступные фичи — уже есть `target-profile.json`; расширить на item/block/recipe/loot.
- [ ] **Схема как источник правды**: убрать ручные списки из `generator.py` и `validator.py` в `schemas/`, чтобы новую версию CE поддержать добавлением схемы + мэппинга.

---

## C. Приоритизация и дорожная карта

### Фаза 1 — «Определение» (ближайшая, максимальный эффект)

1. Registry-oriented item/block detection (bytecode-регистрации + resource link).
2. AssetIndex + модели/текстуры → привязка к объектам, orphan/reference metrics.
3. `BlockNode.auto_state` — **map поддержка**.
4. Схемы: полные списки behavior/condition/function/item-model-типов; валидатор строгого режима.
5. Мульти-неймспейс (уже), reference-driven items (уже), verbatim resources (уже) — зафиксировать тестами.

### Фаза 2 — «Семантика и сторонние источники»

6. Recipe mapper registry; Create/Botany/Immersive → честный diagnostic/transform.
7. Block behaviors: авторитетные behavior types + `settings` из данных, не дефолтов.
8. Категории по recipe-book/tags/source-группам.
9. Coverage-отчёты и CLI флаги (`--strict`, `--dump-ir`, autodetect MC).

### Фаза 3 — «Полная точность»

10. Разбор `atlases`, `fonts`, `particles`, `shaders`, `equipment`, GUI.
11. Data components из bytecode (max_stack, damage, attributes, enchantments, tool, repairable, food).
12. Лут/условия/функции — полный набор CE.
13. Runtime smoke-test (инструмент: запустить `CraftEngine` в изолированном сервере и `/ce reload`).
14. Инкрементальные сборки, кэш IR, детерминированный порядок, асинхронный парсинг.

---

## D. Ожидаемая динамика на VeggiesDelight (ориентир)

| Метрика | Сейчас | Цель после P1 | Цель после P2/P3 |
|---|---|---|---|
| Items generated | 257 | 265+ | 270+ |
| Blocks generated | 29 | 32 | 35 |
| Recipes unsupported | 46 | <20 | <8 |
| `UNSUPPORTED` % от recipes | ~24% | <10% | <5% |
| Orphan textures | не считается | 0 | 0 |
| Broken model/texture refs | 0 (валидно) | 0 | 0 |
| Coverage report | нет | есть | есть + runtime |

---

## E. Риски

| Риск | Мититация |
|---|---|
| Ложная генерация предметов из registry, у которых нет assets | `confidence=0.7`, `PARTIAL/MANUAL`, без инвентаря; никогда не выдумывать `material`. |
| Разбор bytecode даёт ложные ID (`new Item`, а не registry) | только `Registry.register`/`DeferredRegister`/`RegistryObject`-паттерны; обязательная сверка `lang`/asset. |
| Неправильная эмитация behavior/auto_state | только авторитетные behavior types из `Bukkit*Behaviors`; иначе не эмитить, `PARTIAL`. |
| Переполнение/скорость на больших модах | кэширование IR, ограничение сложности рекурсивного model resolve, итеративный `multipart`-parse. |
| Ложные категории | строить только по реальным `generated item ids`, не создавать пустые. |
| Совместимость с разными версиями CE | `target-profile.json` + строгие схемы; если тип не подтверждён — не эмитить. |
| Конфликты namespace | перед генерацией предупреждать о дублях `Key` в `IdConfigParser`-семантике (CraftEngine сам это ловит). |

---

## F. Чек-лист «что уже сделано в этой ветке»

- [x] Мульти-неймспейс анализ `assets/` и `data/`.
- [x] Сканирование современных `assets/<ns>/items/*.json` (item model definitions).
- [x] Reference-driven создание предметов (из рецептов/лута с texture/model).
- [x] Verbatim копирование всего resourcepack `assets/` (включая `assets/minecraft`).
- [x] `pack-manifest.json` с `verbatim:true` + полным списком скопированного.
- [x] Регрессионные тесты на мульти-неймспейс и texture-only предметы.
- [x] Fidelity-проверки модели/текстур, потерянных вариантов, отсутствующих item/block/recipe.
- [x] `BlockNode.auto_state` (str) — *осталось поддержать map `{type,id}`*.
- [x] Recipe-классификация native serializers; `farmersdelight:*` по умолчанию.
- [x] `pack.yml` на уровне пакета.
- [x] Warnings-level nested-key validation для items/blocks/recipes.

---

## G. Итог

Конвертер уже вышел из стадии «proto» в стадию «работающий, но эвристический».
Чтобы сделать его по-настоящему полноценным, нужно три вещи:

1. **Определять контент по регистрациям (Java/bytecode + data), а не по наличию `.json` рядом** — это уберёт и ложные пропуски, и лишние «пустые» объекты.
2. **Связать модели/текстуры/поведения с объектами** через AssetIndex и единый семантический слой — это даст точное сопоставление «предмет ↔ модель ↔ текстура ↔ категория ↔ рецепт».
3. **Сделать источник правды схемой+мэппингом**, а не жёстким кодом — это позволит добавлять поддержку новых CraftEngine-версий и сторонних модов без переписывания генератора.

После этого конверсия будет «почти без компромиссов»: всё, что можно выразить в CE, — выражается; что нельзя — честно помечено с причиной и метрикой покрытия, а ресурспак остаётся максимально полным.

---

*Сопроводительный инженерный note: документ отражает текущее состояние ветки `arena/01a07389-craftengine-converter` и предлагает направление доработок; решение о реализации каждой фазы следует принимать отдельно по готовности соответствующей схемы и мэппинга.*
