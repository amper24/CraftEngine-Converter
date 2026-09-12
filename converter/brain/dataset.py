"""Training-set synthesis for the micro neural network.

There is no public corpus of "mod item name -> CraftEngine semantics", so the
training set is generated from a curated vocabulary of Minecraft / modded
naming conventions combined with realistic structured features. Each sample is
(name, struct, labels); the generator applies prefix/suffix/material noise so
the network learns morphology instead of memorizing exact strings.

Run ``python -m converter.brain.train`` to regenerate the weights.
"""

from __future__ import annotations

import random
from typing import Any, Iterator

from . import features, labels

# --- vocabulary -------------------------------------------------------------

MATERIALS = [
    "wooden", "stone", "copper", "iron", "golden", "diamond", "netherite", "flint",
    "obsidian", "amethyst", "bone", "emerald", "steel", "bronze", "silver", "ruby",
    "crimson", "warped", "oak", "birch", "spruce", "jungle", "acacia", "cherry",
    "bamboo", "mangrove", "dark_oak", "blackstone", "deepslate", "quartz",
]

TIER_WORDS = {
    "wood": ["wooden", "wood", "oak", "birch", "spruce", "bamboo", "jungle"],
    "stone": ["stone", "cobblestone", "flint", "deepslate", "blackstone", "andesite"],
    "copper": ["copper", "bronze"],
    "iron": ["iron", "steel"],
    "gold": ["golden", "gold"],
    "diamond": ["diamond", "emerald", "amethyst"],
    "netherite": ["netherite", "obsidian", "ancient"],
}

TOOLS = ["pickaxe", "axe", "shovel", "spade", "hoe", "shears", "wrench", "hammer_tool", "sickle", "chisel", "trowel", "mattock", "drill", "saw", "pliers"]
WEAPONS = ["sword", "dagger", "katana", "greatsword", "rapier", "mace", "club", "cleaver", "knife", "scimitar", "sabre", "warhammer", "battleaxe", "cutlass", "machete"]
RANGED = ["bow", "longbow", "shortbow", "recurve_bow", "crossbow", "heavy_crossbow", "repeating_crossbow", "hunting_bow"]
SPEARS = ["spear", "lance", "javelin", "halberd", "glaive", "pike", "naginata", "harpoon"]
TRIDENTS = ["trident", "storm_trident", "abyssal_trident", "tidal_trident"]
SHIELDS = ["shield", "buckler", "tower_shield", "kite_shield", "round_shield"]
ARMOR = ["helmet", "chestplate", "leggings", "boots", "cap", "tunic", "pants", "greaves", "gauntlets", "cuirass", "hood", "chainmail_helmet"]

DRINKS = ["juice", "cider", "tea", "coffee", "milk", "syrup", "nectar", "smoothie", "lemonade", "soda", "wine", "mead", "latte", "cocoa", "kombucha", "tonic", "ale", "brew", "shake", "elixir", "cocktail", "punch"]
SOUPS = ["soup", "stew", "chowder", "broth", "bisque", "hotpot", "gumbo", "ramen", "goulash", "porridge", "curry"]
SWEETS = ["cake", "cookie", "pie", "brownie", "cupcake", "muffin", "donut", "pudding", "custard", "popsicle", "ice_cream", "candy", "truffle", "cheesecake", "tart", "jelly", "marshmallow", "waffle", "eclair"]
MEALS = ["sandwich", "burger", "wrap", "pizza", "pasta", "noodles", "salad", "skewer", "roast", "dinner", "platter", "bowl_meal", "casserole", "stir_fry", "bento", "kebab", "taco", "burrito", "omelette", "risotto"]
FOODS = ["bread", "cheese", "jerky", "bacon", "ham", "steak", "fillet", "cutlet", "nugget", "pancake", "toast", "dumpling", "pretzel", "cracker", "chips", "fries", "biscuit", "sausage", "pastry", "roll", "teriyaki", "wheel", "loaf", "bun", "wrap_food", "fritter", "croquette", "meatball", "rib", "wing", "drumstick", "sushi", "onigiri", "tempura", "gyoza", "quiche", "souffle", "terrine", "confit", "kimchi", "pickles", "preserve", "jam", "butter", "yogurt", "curd", "tofu_block_food", "egg_dish", "porridge_bowl"]

CROPS = ["cabbage", "tomato", "onion", "rice", "carrot", "potato", "wheat", "barley", "corn", "lettuce", "pepper", "cucumber", "zucchini", "eggplant", "pumpkin", "melon", "garlic", "ginger", "beet", "radish", "spinach", "peanut", "soybean", "grape", "strawberry", "blueberry"]
CROP_SUFFIX = ["seeds", "seed", "crop", "sapling", "sprout", "bush", "vine", "panicle", "stalk", "wild_plant"]

INGREDIENTS = ["dough", "flour", "slice", "slices", "minced_meat", "patty", "paste", "powder", "extract", "essence", "shard", "ingot", "nugget_metal", "plate_metal", "rod", "gear", "bolt", "fiber", "canvas", "straw", "bark", "resin", "wax", "dye", "pulp", "chunk", "cut", "strip", "shreds", "crumbs"]

BLOCK_ITEMS = ["block", "bricks", "tiles", "planks", "log", "stairs", "slab", "fence", "wall", "door", "trapdoor", "pillar", "crate", "barrel", "pot", "lamp", "lantern", "table", "chair", "stool", "bench", "shelf", "counter", "sign"]
CABINETS = ["cabinet", "cupboard", "wardrobe", "locker", "drawer", "chest_cabinet", "pantry", "dresser", "sideboard"]

MISC_ITEMS = ["coin", "token", "key", "map_piece", "compass_part", "book", "scroll", "charm", "amulet", "ring", "badge", "ticket", "core", "battery", "circuit", "module", "canister", "flask", "bottle_empty", "bucket_empty", "net", "rope", "bandage", "manual", "orb", "talisman", "sigil", "rune", "totem", "relic", "idol", "medallion", "lens_item", "prism", "catalyst", "upgrade", "blueprint", "schematic", "fuse", "spark", "crystal_item", "feather_item", "horn", "whistle", "lantern_item", "banner_item", "trophy"]

# --- block vocabulary -------------------------------------------------------

SOLID_BLOCKS = ["block", "bricks", "tiles", "planks", "stone_block", "concrete", "smooth_block", "polished_block", "ore", "deepslate_ore", "compressed_block", "storage_block", "cobbled_block"]
LOG_BLOCKS = ["log", "stem", "pillar", "wood", "stripped_log", "column", "beam", "bamboo_block"]
LEAVES_BLOCKS = ["leaves", "foliage", "canopy", "leaf_block", "hedge"]
PLANT_BLOCKS = ["flower", "sapling", "bush", "shrub", "fern", "grass_plant", "mushroom", "sprout", "weed", "herb", "cactus", "sugar_cane", "kelp", "vine", "moss_carpet"]
CROP_BLOCKS = [f"{c}_crop" for c in CROPS[:16]] + ["wheat_crop", "rice_paddy", "berry_bush_crop"]

# --- mod-vocabulary extension ----------------------------------------------
# Extra vocabulary harvested from popular content mods (Farmer's Delight,
# Create, Mekanism, Thermal, Immersive Engineering, Tinkers, Botania, Ars
# Nouveau, Aquaculture, Pam's HarvestCraft, Supplementaries, Chipped,
# Decorative Blocks) and from historical/weapon armouries. Appending here
# widens the synthetic training vocabulary without touching the label space.

TOOLS += [
    "excavator", "jackhammer", "chainsaw", "mining_laser", "prospectors_pick",
    "geologists_hammer", "hand_drill", "screwdriver", "calipers", "mortar_pestle",
    "mixing_bowl", "rolling_pin", "cutting_board", "kitchen_knife", "whisk",
    "ladle", "spatula", "peeler", "grater", "strainer", "smiths_hammer",
    "bellows", "anvil_hammer", "file", "rasp", "awl", "needle", "loom_shuttle",
    "spinning_wheel", "quill", "inkwell", "magnifying_glass", "divining_rod",
    "dowsing_rod", "tuning_fork", "wrench_pipe", "wire_cutter", "soldering_iron",
    "multimeter", "crowbar", "pry_bar", "paint_brush", "palette_knife", "chisel_wood",
    "gouge", "mallet", "clamp", "vise", "tongs", "crucible_tongs", "swage",
    "mandrel", "burnisher", "polisher", "grinder", "lathe", "potter_wheel",
]
WEAPONS += [
    "longsword", "broadsword", "estoc", "falchion", "gladius", "spatha", "kopis",
    "khopesh", "tulwar", "shamshir", "tanto", "wakizashi", "nodachi", "zweihander",
    "flamberge", "morningstar", "flail", "quarterstaff", "bo_staff", "warpick",
    "bardiche", "voulge", "fauchard", "ranseur", "partisan", "dirk", "stiletto",
    "katar", "chakram", "shuriken", "kunai", "blowgun", "sling", "atlatl", "bola",
    "lasso", "whip", "chain_whip", "spiked_club", "bone_club", "obsidian_blade",
    "void_blade", "soul_reaper", "doom_blade", "great_axe", "war_scythe",
    "shadow_dagger", "frost_brand", "flame_tongue", "vorpal_blade", "sun_blade",
]
RANGED += [
    "musket", "blunderbuss", "flintlock", "pistol", "revolver", "rifle",
    "sniper_rifle", "carbine", "shotgun", "hand_cannon", "arquebus", "ballista",
    "dart_gun", "nail_gun", "speargun", "harpoon_gun", "net_launcher",
    "grappling_hook", "firework_launcher", "compound_bow", "composite_bow",
    "flatbow", "yumi", "horn_bow", "bone_bow", "crystal_bow", "wind_bow",
    "storm_bow", "void_bow", "aether_bow", "elven_bow", "dwarven_crossbow",
]
SPEARS += [
    "trishula", "dory", "sarissa", "kontos", "xyston", "yari", "boar_spear",
    "fishing_spear", "eel_spear", "throwing_spear", "gaff", "trident_spear",
]
TRIDENTS += [
    "poseidons_trident", "leviathan_trident", "kraken_trident", "coral_trident",
    "depth_trident", "riptide_trident", "nether_trident", "end_trident",
]
SHIELDS += [
    "pavise", "heater_shield", "scutum", "hoplon", "targe", "riot_shield",
    "energy_shield", "magic_barrier", "ward", "aegis", "guardian_shield",
    "paladin_shield", "crusader_shield", "obsidian_shield", "crystal_shield",
]
ARMOR += [
    "pauldrons", "spaulders", "vambraces", "rerebraces", "sabatons", "sollerets",
    "gorget", "bevor", "breastplate", "brigandine", "hauberk", "gambeson",
    "surcoat", "coif", "arming_cap", "sallet", "bascinet", "armet", "close_helm",
    "barbute", "kabuto", "menpo", "do_maru", "hakama", "straw_hat",
    "miners_helmet", "divers_helmet", "gas_mask", "hazmat_suit", "lab_coat",
    "chefs_hat", "apron", "wizard_robe", "necromancer_robe", "druid_vestment",
    "monk_robe", "priests_alb", "bishops_mitre", "crown", "tiara", "diadem",
    "circlet", "warplate", "dreadplate", "shadowmail", "sunforged_plate",
    "frostguard_plate", "emberweave_robe", "cloak", "cape", "mantle", "tabard",
    "goggles", "monocle", "visor", "faceplate", "shoulder_guards", "knee_guards",
    "shin_guards", "elbow_guards", "belt", "girdle", "sash", "bandolier",
]
DRINKS += [
    "espresso", "cappuccino", "mocha", "frappuccino", "chai", "matcha", "boba",
    "bubble_tea", "kvass", "ayran", "lassi", "kefir", "sake", "soju", "baijiu",
    "rum", "vodka", "whiskey", "gin", "tequila", "brandy", "perry", "absinthe",
    "liqueur", "schnapps", "port", "sherry", "vermouth", "bitters", "sangria",
    "mulled_wine", "hot_chocolate", "eggnog", "slushie", "energy_drink",
    "spring_water", "mineral_water", "sparkling_water", "glow_berry_juice",
    "sweet_berry_juice", "melon_juice", "carrot_juice", "beet_juice",
    "tomato_juice", "green_smoothie", "protein_shake", "herbal_tea", "mint_tea",
    "chamomile_tea", "ginger_tea", "black_tea", "green_tea", "oolong",
    "rooibos", "yerba_mate", "iced_tea", "sun_tea",
]
SOUPS += [
    "pho", "udon_soup", "soba_soup", "miso_soup", "wonton_soup", "egg_drop_soup",
    "tortilla_soup", "minestrone", "gazpacho", "vichyssoise", "borscht",
    "solyanka", "shchi", "okroshka", "harira", "laksa", "tom_yum", "sinigang",
    "caldo", "pozole", "menudo", "cawl", "scouse", "cock_a_leekie",
    "corn_chowder", "potato_soup", "pumpkin_soup", "tomato_soup",
    "mushroom_soup", "chicken_noodle", "beef_stew", "lamb_stew", "fish_stew",
    "seafood_stew", "vegetable_stew", "hearty_stew", "onion_soup", "leek_soup",
]
SWEETS += [
    "macaron", "madeleine", "financier", "canele", "churro", "baklava", "halva",
    "loukoum", "mochi", "daifuku", "dorayaki", "taiyaki", "wagashi", "anmitsu",
    "parfait", "sundae", "sorbet", "gelato", "sherbet", "semifreddo", "tiramisu",
    "cannoli", "sfogliatella", "babka", "stollen", "panettone", "kugelhopf",
    "gingerbread", "speculoos", "stroopwafel", "beignet", "zeppole", "cruller",
    "apple_turnover", "danish", "cinnamon_roll", "sticky_bun", "scone",
    "shortbread", "biscotti", "cantuccini", "amaretti", "meringue", "pavlova",
    "mousse", "bavarois", "panna_cotta", "blancmange", "syllabub", "trifle",
    "bakewell", "profiterole", "croquembouche", "nougat", "marzipan", "praline",
    "fudge", "toffee", "brittle", "caramel", "lollipop", "gummy", "licorice",
    "taffy", "rock_candy", "chocolate_bar", "hot_fudge", "flan", "creme_brulee",
]
MEALS += [
    "lasagna", "ravioli", "tortellini", "gnocchi", "polenta_meal", "paella",
    "jambalaya", "biryani", "pilaf", "fried_rice", "chow_mein", "lo_mein",
    "pad_thai", "yakisoba", "bulgogi", "bibimbap", "galbi", "samgyeopsal",
    "tonkatsu", "katsu_curry", "gyudon", "oyakodon", "katsudon", "tempura_bowl",
    "unagi_don", "sashimi_platter", "chirashi", "poke_bowl", "ceviche",
    "carpaccio", "tartare", "fondue", "raclette", "hot_pot", "shabu_shabu",
    "sukiyaki", "dim_sum", "har_gow", "siu_mai", "char_siu_bao", "spring_roll",
    "egg_roll", "wonton_dish", "potsticker", "baozi", "mantou", "naan", "roti",
    "paratha", "chapati", "pita", "lavash", "tortilla_wrap", "arepa", "empanada",
    "pastel", "pirozhki", "samosa", "pakora", "dosa", "idli", "uttapam", "vada",
    "falafel", "shawarma", "gyros", "souvlaki", "moussaka", "dolma",
    "stuffed_pepper", "stuffed_cabbage", "chili_con_carne", "beef_wellington",
    "coq_au_vin", "pot_roast_meal", "shepherd_pie", "cottage_pie", "fish_pie",
    "cornish_pasty", "steak_pie", "quiche_lorraine", "frittata", "shakshuka",
    "eggs_benedict", "full_breakfast", "brunch_platter", "fish_and_chips",
]
FOODS += [
    "baguette", "ciabatta", "focaccia", "sourdough", "rye_bread", "pumpernickel",
    "brioche", "challah", "bagel", "croissant", "pain_au_chocolat", "crumpet",
    "english_muffin", "johnnycake", "cornbread", "hushpuppy", "popover",
    "yorkshire_pudding", "spaetzle", "pierogi", "varenyky", "pelmeni", "manti",
    "khinkali", "orecchiette", "penne", "fusilli", "farfalle", "rigatoni",
    "spaghetti", "linguine", "fettuccine", "macaroni", "orzo", "couscous",
    "quinoa", "bulgur", "farro", "grits", "oatmeal", "muesli", "granola",
    "cereal", "brie", "camembert", "gouda", "cheddar", "mozzarella_ball",
    "parmesan_wedge", "feta_block", "halloumi", "paneer", "queso_fresco",
    "ricotta", "mascarpone", "cream_cheese", "blue_cheese", "gorgonzola",
    "smoked_salmon", "caviar", "anchovy", "sardine", "herring", "mackerel",
    "trout_fillet", "bass_fillet", "cod_fillet", "tuna_steak", "swordfish_steak",
    "mahi_mahi", "catfish", "pike_fillet", "perch", "walleye", "crab_leg",
    "lobster_tail", "shrimp", "prawn", "crayfish", "scallop", "oyster", "mussel",
    "clam", "squid", "octopus_tentacle", "sea_urchin", "seaweed_snack",
    "nori_sheet", "kelp_snack", "biltong", "pemmican", "salt_pork",
    "corned_beef", "luncheon_meat", "meatloaf", "sausage_link", "bratwurst",
    "chorizo", "andouille", "kielbasa", "salami", "pepperoni", "prosciutto",
    "pancetta", "guanciale", "lardons", "crackling", "pork_rind", "chicken_wing",
    "buffalo_wing", "fried_chicken", "roast_chicken", "turkey_leg", "duck_confit",
    "goose_roast", "pheasant", "quail", "venison", "elk_steak", "bison_steak",
    "boar_roast", "mutton", "lamb_chop", "pork_chop", "beef_rib", "short_rib",
    "brisket", "flank_steak", "sirloin", "tenderloin", "filet_mignon", "t_bone",
    "porterhouse", "prime_rib", "stew_meat", "ground_beef", "meat_patty",
    "bacon_strip", "ham_slice", "egg_fried", "egg_boiled", "egg_scrambled",
    "poached_egg", "deviled_egg", "hash_browns", "potato_cake", "latke", "rosti",
    "tater_tot", "onion_ring", "mozzarella_stick", "jalapeno_popper",
    "stuffed_mushroom", "garlic_bread", "cheese_toast", "bruschetta", "crostini",
    "canape", "tapas", "mezze_platter", "antipasto", "charcuterie",
    "cheese_board", "pickle_spear", "sauerkraut", "relish", "chutney", "salsa",
    "guacamole", "hummus", "tzatziki", "aioli", "pesto", "tapenade", "romesco",
    "chimichurri", "gravy", "marinara", "alfredo", "bolognese", "arrabbiata",
]
CROPS += [
    "cassava", "taro", "yam", "parsnip", "turnip", "rutabaga", "kohlrabi",
    "celeriac", "fennel", "leek", "shallot", "chive", "basil", "oregano",
    "thyme", "rosemary", "sage", "mint", "cilantro", "dill", "parsley",
    "lavender", "chamomile_plant", "hops", "flax", "cotton_plant", "sesame",
    "mustard_plant", "rapeseed", "sunflower", "saffron", "vanilla", "cacao",
    "tea_plant", "coffee_plant", "pineapple", "banana", "coconut", "mango",
    "papaya", "fig", "date_palm", "olive", "avocado", "lemon", "lime",
    "orange", "grapefruit", "pomegranate", "persimmon", "plum", "cherry",
    "apricot", "peach", "pear", "apple_crop", "raspberry", "blackberry",
    "cranberry", "gooseberry", "mulberry", "elderberry", "juniper",
]
CROP_BLOCKS = [f"{c}_crop" for c in CROPS] + [
    "wheat_crop", "rice_paddy", "berry_bush_crop", "vine_crop", "trellis_crop",
    "bush_crop", "stem_crop", "double_crop", "paddy", "plantation",
]
INGREDIENTS += [
    "batter", "breadcrumb", "pastry_dough", "pie_crust", "phyllo", "puff_pastry",
    "shortcrust", "meringue_base", "ganache", "praline_paste", "marzipan_paste",
    "caramel_sauce", "custard_base", "bechamel", "roux", "stock_cube",
    "bouillon", "gelatin", "agar", "pectin", "yeast", "baking_powder",
    "baking_soda", "cornstarch", "tapioca", "arrowroot", "semolina", "bran",
    "germ", "gluten", "wheat_germ", "malt", "malt_syrup", "molasses",
    "honey_comb", "honeycomb", "royal_jelly", "propolis", "beeswax_block",
    "salt", "peppercorn", "cinnamon_stick", "star_anise", "cardamom_pod",
    "clove", "nutmeg", "allspice", "coriander_seed", "cumin_seed",
    "fennel_seed", "caraway", "fenugreek", "turmeric_root", "paprika",
    "cayenne", "chili_flake", "chili_powder", "curry_powder", "garam_masala",
    "five_spice", "herbes_de_provence", "bouquet_garni", "zaatar", "sumac",
    "asafoetida", "msg", "citric_acid", "cream_of_tartar", "vanilla_extract",
    "almond_extract", "rose_water", "orange_blossom", "coconut_milk",
    "almond_milk", "oat_milk", "soy_milk", "rice_milk", "buttermilk",
    "heavy_cream", "clotted_cream", "creme_fraiche", "ghee", "tallow",
    "lard", "dripping", "suet", "margarine", "oil_bottle", "vinegar_bottle",
    "brine", "whey", "curd_cheese", "cheese_cloth", "rennet", "culture",
]
BLOCK_ITEMS += [
    "pressure_plate", "button", "lever", "grate", "grille", "lattice",
    "glass_pane", "bars", "chain_block", "ladder", "scaffolding", "gate",
    "hatch", "portcullis", "pedestal", "plinth", "statue", "fountain",
    "brazier", "torch_holder", "candelabra", "chandelier", "rug", "carpet_block",
    "cushion", "pillow", "hammock", "lectern", "podium", "altar", "shrine",
    "obelisk", "monument", "gravestone", "tombstone", "coffin", "sarcophagus",
    "urn", "amphora", "vase", "jug", "pitcher", "ewer", "chalice",
    "cauldron_block", "crucible", "kiln", "smelter", "blast_furnace", "smoker",
    "campfire", "bonfire", "hearth", "firepit", "oven", "stove", "cooker",
    "grill", "spit", "fryer", "steamer", "wok", "skillet", "sink", "basin",
    "bathtub", "mirror", "clock_block", "hourglass", "sundial", "barometer",
    "telescope", "orrery", "globe", "atlas", "chalkboard", "notice_board",
    "poster", "painting_block", "frame", "portrait", "tapestry", "curtain",
    "drape", "shutter", "awning", "pergola", "gazebo", "trellis", "arbor",
    "arch", "capital", "molding", "cornice", "wainscot", "trim_block",
    "border_block", "inlay", "mosaic", "mural", "relief", "carving",
    "beehive", "apiary", "composter", "loom_block", "stonecutter",
    "brewing_stand_block", "fletching_table", "cartography_table",
    "smithing_table", "grindstone_block", "flower_pot", "planter", "trough",
    "feeding_trough", "nest_box", "birdhouse", "dog_house", "stable", "pen",
    "coop", "silo", "granary", "mill", "watermill", "windmill", "sawmill",
]
CABINETS += [
    "armoire", "chiffonier", "buffet", "hutch", "credenza", "console_table",
    "nightstand", "bedside_table", "medicine_cabinet", "tool_cabinet",
    "filing_cabinet", "display_case", "curio_cabinet", "gun_cabinet",
    "wine_rack", "spice_rack", "pot_rack", "knife_block", "utensil_crocks",
    "linen_closet", "broom_closet", "larder", "root_cellar_shelf",
    "workbench_cabinet", "alchemy_cabinet", "bookcase_cabinet", "scroll_rack",
    "trophy_case", "reliquary", "shrine_cabinet", "offering_table",
]
SOLID_BLOCKS += [
    "gem_block", "crystal_block", "obsidian_block", "basalt_block",
    "blackstone_block", "end_stone_block", "purpur_block", "quartz_block",
    "prismarine_block", "terracotta_block", "glazed_terracotta",
    "sandstone_block", "red_sandstone_block", "limestone", "granite_block",
    "diorite_block", "andesite_block", "tuff_block", "calcite_block",
    "dripstone_block", "amethyst_block", "copper_block", "waxed_copper",
    "bronze_block", "steel_block", "tin_block", "lead_block", "silver_block",
    "aluminum_block", "nickel_block", "zinc_block", "titanium_block",
    "cobalt_block", "ardite_block", "manyullyn_block", "uranium_block",
    "thorium_block", "iridium_block", "osmium_block", "tungsten_block",
    "mithril_block", "adamantite_block", "orichalcum_block", "electrum_block",
    "constantan_block", "invar_block", "signalum_block", "lumium_block",
    "enderium_block", "manasteel_block", "terrasteel_block", "elementium_block",
    "alfsteel_block", "neutronium_block", "infinity_block", "dragonsteel_block",
    "starmetal_block", "meteoric_iron_block", "cold_iron_block",
    "dark_steel_block", "soul_steel_block", "sky_stone_block",
    "certus_quartz_block", "fluix_block", "meteorite_block", "slag_block",
    "coke_block", "charcoal_block", "sulfur_block", "saltpeter_block",
    "niter_block", "phosphor_block", "cinnabar_block", "galena_block",
    "sphalerite_block", "bauxite_block", "cassiterite_block", "hematite_block",
    "magnetite_block", "malachite_block", "azurite_block", "bornite_block",
]
LOG_BLOCKS += [
    "stripped_wood", "wood_beam", "timber", "post", "support_beam", "rafter",
    "joist", "girder", "lumber", "dowel", "shingle_block", "shake_block",
    "bark_block", "cork_block", "palm_log", "rubber_log", "willow_log",
    "maple_log", "cherry_log", "walnut_log", "mahogany_log", "teak_log",
    "ebony_log", "baobab_log", "jacaranda_log", "redwood_log", "sequoia_log",
    "cypress_log", "fir_log", "hemlock_log", "larch_log", "cedar_log",
    "aspen_log", "poplar_log", "sycamore_log", "chestnut_log", "hickory_log",
]
LEAVES_BLOCKS += [
    "leaf_pile", "leaf_carpet", "leaf_litter", "fallen_leaves", "flowering_leaves",
    "fruit_leaves", "blossom", "cherry_blossom", "wisteria", "maple_leaves",
    "oak_leaves_block", "pine_needles", "fir_needles", "spruce_needles",
    "palm_frond", "banana_leaf", "bamboo_leaves", "ivy_block", "moss_block",
    "lichen_block", "algae_block", "seagrass_block", "reed_block",
]
PLANT_BLOCKS += [
    "tulip", "rose_bush", "peony", "lilac", "orchid", "dahlia", "iris",
    "lily_pad_flower", "water_lily", "lotus", "hibiscus", "plumeria",
    "bird_of_paradise", "protea", "banksia", "waratah", "foxglove",
    "delphinium", "lupin", "snapdragon", "marigold", "zinnia", "petunia",
    "geranium", "begonia", "impatiens", "hosta", "heuchera", "astilbe",
    "hydrangea", "camellia", "azalea", "rhododendron", "magnolia",
    "dogwood", "forsythia", "jasmine", "gardenia", "oleander", "yucca",
    "agave", "aloe", "succulent", "echeveria", "haworthia", "sedum",
    "saxifrage", "primrose", "violet", "pansy", "crocus", "snowdrop",
    "hyacinth", "daffodil", "fritillary", "anemone", "ranunculus",
    "bluebell", "periwinkle", "trillium", "mayapple", "bloodroot",
    "trout_lily", "jack_in_the_pulpit", "skunk_cabbage", "pitcher_plant",
    "venus_flytrap", "sundew", "corpse_flower", "rafflesia", "amanita",
    "chanterelle", "morel", "truffle_fungus", "puffball", "stinkhorn",
    "ink_cap", "oyster_mushroom", "shiitake_log", "enoki_cluster",
]
THIN_BLOCKS = ["cutting_board", "tray", "plate_block", "pan", "carpet", "pressure_plate", "rug", "mat", "board", "basket", "pie_block", "cake_block", "bowl_block", "paper_wall"]
GLASS_BLOCKS = ["glass", "glass_pane", "stained_glass", "window", "ice", "crystal", "lens", "amethyst_glass"]
SLAB_BLOCKS = ["slab", "stairs", "fence", "wall", "fence_gate", "step", "ledge"]
MACHINE_BLOCKS = ["furnace", "generator", "crusher", "smelter", "assembler", "reactor", "tank", "pipe", "conveyor", "terminal", "controller", "press", "mixer", "kiln", "brewer", "cooking_pot", "grill", "stove", "oven"]
FURNITURE_BLOCKS = ["chair", "stool", "table", "bench", "shelf", "counter", "cabinet", "wardrobe", "lamp", "lantern", "candle_holder", "sofa", "desk", "clock_block", "vase", "statue", "sign_post", "curtain"]

PREFIXES = ["", "wild_", "ancient_", "royal_", "rustic_", "dark_", "enchanted_", "reinforced_", "primitive_", "advanced_", "arcane_", "frozen_", "molten_", "gilded_", "shadow_"]
NAMESPACES = ["mymod", "farmersdelight", "veggiesdelight", "createmod", "adventure", "tech", "cuisine", "artifacts", "nature", "industry"]


def _tier_for(name: str) -> str:
    low = name.lower()
    for tier, words in TIER_WORDS.items():
        if any(w in low for w in words):
            return tier
    return "none"


def _decorate(rng: random.Random, base: str, allow_material: bool = True, food_prefix: bool = False) -> str:
    """Compose a realistic modded id around ``base``.

    The *head noun* (``base``) must stay the decisive token: qualifiers are only
    ever prepended. Food-like objects get ingredient qualifiers (``pumpkin_``,
    ``tofu_``) because that is how mods actually name them, while gear/blocks
    get material qualifiers.
    """
    parts: list[str] = []
    if food_prefix and rng.random() < 0.6:
        parts.append(rng.choice(CROPS + ["tofu", "chicken", "beef", "pork", "fish", "salmon", "cheese", "honey", "chocolate", "mushroom"]))
    elif allow_material and rng.random() < 0.55:
        parts.append(rng.choice(MATERIALS))
    if rng.random() < 0.2:
        adjective = rng.choice(PREFIXES).strip("_")
        if adjective:
            parts.insert(0, adjective)
    parts.append(base)
    name = "_".join(p for p in parts if p)
    return f"{rng.choice(NAMESPACES)}:{name}"


# --- item sample generation -------------------------------------------------


def _item_sample(rng: random.Random, category: str, base: str) -> tuple[str, dict[str, float], dict[str, str]]:
    gear = "none"
    food = "none"
    struct: dict[str, float] = {}
    allow_material = True

    if category == "Tools":
        gear = "tool"
    elif category == "Weapons":
        gear = "weapon"
    elif category == "Ranged":
        gear = "crossbow" if "crossbow" in base else "bow"
    elif category == "Spears":
        gear = "spear"
    elif category == "Tridents":
        gear = "trident"
    elif category == "Shields":
        gear = "shield"
    elif category == "Armor":
        gear = "armor"

    if category == "Drinks":
        food, allow_material = "drink", False
    elif category == "Meals":
        food, allow_material = ("soup" if base in SOUPS else "food"), False
    elif category == "Sweets":
        food, allow_material = "sweet", False
    elif category == "Food":
        food, allow_material = "food", False

    # 15% of non-food objects also get an edible-sounding qualifier
    # (e.g. "pumpkin_hammer"): a hard negative that forces the head noun, not
    # the qualifier, to decide the category.
    food_prefix = food != "none" or (category == "Crops") or rng.random() < 0.15
    name = _decorate(rng, base, allow_material, food_prefix=food_prefix and category != "Crops")

    # Structured signals correlated with the label, plus realistic noise.
    if gear in ("tool", "weapon", "spear", "trident"):
        struct["has_durability"] = 1.0
        struct["has_attack_damage"] = 1.0 if gear != "tool" else float(rng.random() < 0.4)
        struct["model_parent_handheld"] = float(rng.random() < 0.8)
        struct["has_max_stack_1"] = 1.0
        struct["tag_tools"] = 1.0 if gear == "tool" and rng.random() < 0.6 else 0.0
        struct["tag_weapons"] = 1.0 if gear == "weapon" and rng.random() < 0.6 else 0.0
    if gear == "armor":
        struct.update({"has_durability": 1.0, "has_equippable": 1.0, "has_max_stack_1": 1.0})
        struct["tag_armor"] = float(rng.random() < 0.6)
    if gear in ("bow", "crossbow", "shield"):
        struct.update({"has_durability": 1.0, "has_max_stack_1": 1.0})
    if food != "none":
        struct["has_food_component"] = float(rng.random() < 0.75)
        struct["has_consumable_component"] = float(rng.random() < 0.6)
        struct["tag_food"] = float(rng.random() < 0.5)
        struct["model_parent_generated"] = float(rng.random() < 0.85)
        if food in ("drink", "soup"):
            struct["has_use_remainder"] = float(rng.random() < 0.7)
            struct["has_max_stack_1"] = float(rng.random() < 0.6)
    if category in ("Blocks", "Cabinets"):
        struct["has_block_binding"] = 1.0
        struct["has_3d_model"] = float(rng.random() < 0.7)
        struct["hint_building"] = float(rng.random() < 0.5)
    if category == "Crops":
        struct["tag_crops"] = float(rng.random() < 0.6)
        struct["model_parent_generated"] = float(rng.random() < 0.9)
    if category == "Ingredients":
        struct["tag_ingredients"] = float(rng.random() < 0.4)
        struct["model_parent_generated"] = float(rng.random() < 0.9)

    struct["texture_count_log"] = features.log1p_scaled(rng.randint(1, 3))
    struct["name_word_count"] = features.log1p_scaled(len(features.tokenize(name)), 5)
    struct["name_length_log"] = features.log1p_scaled(len(name), 32)
    struct["from_recipe_output"] = float(rng.random() < 0.5)

    tier = _tier_for(name) if gear in ("tool", "weapon", "spear", "armor") else "none"
    tier_index = {"wood": 0.15, "stone": 0.3, "copper": 0.4, "iron": 0.55, "gold": 0.7, "diamond": 0.85, "netherite": 1.0}
    struct["tier_hint"] = tier_index.get(tier, 0.0) * (1.0 if rng.random() < 0.8 else 0.0)

    return name, struct, {"category": category, "gear_kind": gear, "food_family": food, "tool_tier": tier}


ITEM_GROUPS: list[tuple[str, list[str]]] = [
    ("Tools", TOOLS),
    ("Weapons", WEAPONS),
    ("Ranged", RANGED),
    ("Spears", SPEARS),
    ("Tridents", TRIDENTS),
    ("Shields", SHIELDS),
    ("Armor", ARMOR),
    ("Drinks", DRINKS),
    ("Meals", SOUPS + MEALS),
    ("Sweets", SWEETS),
    ("Food", FOODS),
    ("Crops", [f"{c}_{s}" for c in CROPS for s in CROP_SUFFIX[:4]][:120] + CROPS),
    ("Ingredients", INGREDIENTS),
    ("Blocks", BLOCK_ITEMS),
    ("Cabinets", CABINETS),
    ("Items", MISC_ITEMS),
]


_SYLLABLES = ["zor", "kel", "vun", "mip", "tra", "quo", "blen", "shu", "grim", "wid", "nal", "phe", "rus", "tik", "oben", "xar", "yul", "dro", "sev", "lum"]


def _nonsense_word(rng: random.Random) -> str:
    return "".join(rng.choice(_SYLLABLES) for _ in range(rng.randint(2, 3)))


def _ood_item_sample(rng: random.Random) -> tuple[str, dict[str, float], dict[str, str]]:
    """An id whose head noun carries no known meaning.

    These teach the network the honest answer for unknown vocabulary — a
    generic item with no gear kind and no food family — instead of confidently
    matching a random n-gram neighbour.
    """
    name = f"{rng.choice(NAMESPACES)}:{_nonsense_word(rng)}"
    if rng.random() < 0.4:
        name = f"{name}_{_nonsense_word(rng)}"
    struct = {
        "texture_count_log": features.log1p_scaled(1),
        "name_word_count": features.log1p_scaled(len(features.tokenize(name)), 5),
        "name_length_log": features.log1p_scaled(len(name), 32),
    }
    return name, struct, {"category": "Items", "gear_kind": "none", "food_family": "none", "tool_tier": "none"}


# Share of synthesized samples with unknown vocabulary.
OOD_RATIO = 0.12


def generate_item_samples(count: int, seed: int = 20260906) -> Iterator[tuple[str, dict[str, float], dict[str, str]]]:
    rng = random.Random(seed)
    groups = ITEM_GROUPS
    for i in range(count):
        if rng.random() < OOD_RATIO:
            yield _ood_item_sample(rng)
            continue
        category, bases = groups[i % len(groups)]
        yield _item_sample(rng, category, rng.choice(bases))


# --- block sample generation ------------------------------------------------

BLOCK_GROUPS: list[tuple[str, list[str]]] = [
    ("solid", SOLID_BLOCKS),
    ("log", LOG_BLOCKS),
    ("leaves", LEAVES_BLOCKS),
    ("plant", PLANT_BLOCKS),
    ("crop", CROP_BLOCKS),
    ("thin", THIN_BLOCKS),
    ("glass", GLASS_BLOCKS),
    ("slab_like", SLAB_BLOCKS),
    ("machine", MACHINE_BLOCKS),
    ("furniture", FURNITURE_BLOCKS),
]

# Canonical CraftEngine representation per detected block kind.
KIND_TO_AUTO_STATE: dict[str, str] = {
    "solid": "note_block",
    "log": "note_block",
    "leaves": "leaves",
    "plant": "sapling",
    "crop": "higher_tripwire",
    "thin": "lower_tripwire",
    "glass": "note_block",
    "slab_like": "note_block",
    "machine": "note_block",
    "furniture": "lower_tripwire",
}

KIND_TRANSPARENT = {"leaves", "plant", "crop", "thin", "glass", "furniture"}
KIND_ENTITY = {"thin", "furniture", "crop"}


def _block_sample(rng: random.Random, kind: str, base: str) -> tuple[str, dict[str, float], dict[str, str]]:
    name = _decorate(rng, base, allow_material=kind in ("solid", "log", "slab_like", "glass", "furniture", "thin"))
    struct: dict[str, float] = {}

    if kind == "crop":
        struct.update({"has_age_property": 1.0, "render_cutout": 1.0, "parent_cross": float(rng.random() < 0.7),
                       "state_count_log": features.log1p_scaled(1, 6),
                       "variant_count_log": features.log1p_scaled(rng.randint(4, 8), 16),
                       "tag_crops": float(rng.random() < 0.6)})
    elif kind == "plant":
        struct.update({"render_cutout": 1.0, "parent_cross": float(rng.random() < 0.8),
                       "hardness_log": 0.0})
    elif kind == "leaves":
        struct.update({"render_cutout": 1.0, "parent_cube_all": float(rng.random() < 0.7),
                       "tag_leaves": float(rng.random() < 0.7), "tag_mineable_axe": float(rng.random() < 0.3)})
    elif kind == "glass":
        struct.update({"render_translucent": float(rng.random() < 0.6), "render_cutout": float(rng.random() < 0.4),
                       "parent_cube_all": float(rng.random() < 0.6)})
    elif kind == "thin":
        struct.update({"model_non_full_cube": 1.0, "model_element_count_log": features.log1p_scaled(rng.randint(1, 6), 12),
                       "render_cutout": float(rng.random() < 0.5), "has_facing_property": float(rng.random() < 0.6)})
    elif kind == "furniture":
        struct.update({"model_non_full_cube": 1.0, "model_has_rotation": float(rng.random() < 0.5),
                       "has_facing_property": 1.0,
                       "model_element_count_log": features.log1p_scaled(rng.randint(3, 12), 12)})
    elif kind == "machine":
        struct.update({"has_block_entity": 1.0, "has_facing_property": float(rng.random() < 0.7),
                       "has_powered_property": float(rng.random() < 0.5),
                       "parent_cube_all": float(rng.random() < 0.5),
                       "tag_mineable_pickaxe": float(rng.random() < 0.6)})
    elif kind == "slab_like":
        struct.update({"parent_slab_or_stairs": float("slab" in base or "stairs" in base),
                       "parent_fence_or_wall": float("fence" in base or "wall" in base),
                       "has_half_property": float(rng.random() < 0.7),
                       "model_non_full_cube": 1.0,
                       "is_multipart": float("fence" in base or "wall" in base)})
    elif kind == "log":
        struct.update({"has_axis_property": 1.0, "tag_logs": float(rng.random() < 0.6),
                       "tag_mineable_axe": float(rng.random() < 0.7), "parent_cube_all": float(rng.random() < 0.4)})
    else:  # solid
        struct.update({"parent_cube_all": float(rng.random() < 0.85),
                       "tag_mineable_pickaxe": float(rng.random() < 0.6),
                       "hardness_log": features.log1p_scaled(rng.uniform(1.0, 5.0), 10)})

    struct["has_block_item"] = float(rng.random() < 0.9)
    struct["has_loot_table"] = float(rng.random() < 0.8)
    struct["name_word_count"] = features.log1p_scaled(len(features.tokenize(name)), 5)
    struct["name_length_log"] = features.log1p_scaled(len(name), 32)

    return name, struct, {
        "block_kind": kind,
        "auto_state": KIND_TO_AUTO_STATE[kind],
        "transparent": "transparent" if kind in KIND_TRANSPARENT else "opaque",
        "entity_renderer": "entity" if kind in KIND_ENTITY else "native",
    }


def generate_block_samples(count: int, seed: int = 20260907) -> Iterator[tuple[str, dict[str, float], dict[str, str]]]:
    rng = random.Random(seed)
    for i in range(count):
        kind, bases = BLOCK_GROUPS[i % len(BLOCK_GROUPS)]
        yield _block_sample(rng, kind, rng.choice(bases))


def build_matrices(samples: Any, kind: str, seed: int = 1234) -> tuple[Any, dict[str, Any]]:
    """Vectorize samples into (X, {head: y}) NumPy arrays.

    Real mods rarely expose every structured signal, so each sample is emitted
    several times with different *feature dropout* masks:

    * full features (analyzer found everything),
    * name only (a bare jar with no models/tags/components),
    * partial structured features.

    Without this augmentation the network learns to rely exclusively on the
    structured block and collapses to a constant prediction whenever the
    analyzer could not fill it in.
    """
    import numpy as np

    rng = random.Random(seed)
    head_space = labels.ITEM_HEADS if kind == "item" else labels.BLOCK_HEADS
    vec_fn = features.item_vector if kind == "item" else features.block_vector
    struct_keys = features.ITEM_STRUCT_FEATURES if kind == "item" else features.BLOCK_STRUCT_FEATURES

    xs: list[Any] = []
    ys: dict[str, list[int]] = {head: [] for head in head_space}
    for name, struct, label_map in samples:
        variants = [struct, {}]
        partial = {k: v for k, v in struct.items() if rng.random() < 0.5}
        variants.append(partial)
        # Occasionally inject misleading structure so the model does not treat
        # any single flag as an absolute rule.
        noisy = dict(struct)
        noisy[rng.choice(struct_keys)] = 1.0
        variants.append(noisy)
        for variant in variants:
            xs.append(vec_fn(name, variant))
            for head, space in head_space.items():
                ys[head].append(space.index(label_map[head]))
    return np.stack(xs), {head: np.array(v, dtype=np.int64) for head, v in ys.items()}
