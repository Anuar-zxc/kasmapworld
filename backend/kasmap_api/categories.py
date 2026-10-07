# Vendored from src/kasmap/processing/ — keep in sync (CI checks equality).
"""KasMap category taxonomy and mappings from source taxonomies.

The taxonomy is the single source of truth: `kasmap db seed` writes it to
`poi.category` / `poi.category_map`, and the pipeline maps records with it in memory.

Overture keys are values of `basic_category` (preferred) or `categories.primary`
(deprecated, removed from Overture from September 2026). They are an initial
mapping: run `kasmap categories unmapped --country KZ` after the first ingest
and extend the table with real top-N unmapped values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    id: str
    parent_id: str | None
    ru: str
    kk: str
    en: str


TAXONOMY: tuple[Category, ...] = (
    Category("food", None, "Еда и напитки", "Тамақ және сусындар", "Food & drink"),
    Category("food.cafe", "food", "Кафе", "Кафе", "Cafe"),
    Category("food.cafe.coffee_shop", "food.cafe", "Кофейня", "Кофехана", "Coffee shop"),
    Category("food.restaurant", "food", "Ресторан", "Мейрамхана", "Restaurant"),
    Category("food.fast_food", "food", "Фастфуд", "Фастфуд", "Fast food"),
    Category("food.bakery", "food", "Пекарня", "Наубайхана", "Bakery"),
    Category("food.bar", "food", "Бар", "Бар", "Bar"),
    Category("retail", None, "Розница", "Бөлшек сауда", "Retail"),
    Category("retail.convenience", "retail", "Мини-маркет", "Шағын дүкен", "Convenience store"),
    Category("retail.supermarket", "retail", "Супермаркет", "Супермаркет", "Supermarket"),
    Category("retail.mall", "retail", "Торговый центр", "Сауда орталығы", "Shopping mall"),
    Category("health", None, "Здоровье", "Денсаулық", "Health"),
    Category("health.pharmacy", "health", "Аптека", "Дәріхана", "Pharmacy"),
    Category("health.clinic", "health", "Клиника", "Емхана", "Clinic"),
    Category("health.dentist", "health", "Стоматология", "Стоматология", "Dentist"),
    Category("health.hospital", "health", "Больница", "Аурухана", "Hospital"),
    Category("beauty", None, "Красота", "Сұлулық", "Beauty"),
    Category("beauty.salon", "beauty", "Салон красоты", "Сұлулық салоны", "Beauty salon"),
    Category("beauty.barber", "beauty", "Барбершоп", "Шаштараз", "Barber"),
    Category("fitness", None, "Спорт", "Спорт", "Sport"),
    Category("fitness.gym", "fitness", "Фитнес-клуб", "Фитнес-клуб", "Gym"),
    Category("education", None, "Образование", "Білім", "Education"),
    Category("education.university", "education", "Университет", "Университет", "University"),
    Category("education.school", "education", "Школа", "Мектеп", "School"),
    Category("education.kindergarten", "education", "Детский сад", "Балабақша", "Kindergarten"),
    Category("finance", None, "Финансы", "Қаржы", "Finance"),
    Category("finance.bank", "finance", "Банк", "Банк", "Bank"),
    Category("finance.atm", "finance", "Банкомат", "Банкомат", "ATM"),
    Category("transport", None, "Транспорт", "Көлік", "Transport"),
    Category("transport.bus_stop", "transport", "Остановка", "Аялдама", "Bus stop"),
    Category("transport.metro", "transport", "Метро", "Метро", "Metro station"),
    Category("transport.rail", "transport", "Вокзал", "Вокзал", "Railway station"),
    Category("office", None, "Офисы", "Кеңселер", "Offices"),
    Category("office.business_center", "office", "Бизнес-центр", "Бизнес-орталық",
             "Business center"),
    Category("lodging", None, "Проживание", "Тұру", "Lodging"),
    Category("lodging.hotel", "lodging", "Гостиница", "Қонақүй", "Hotel"),
)

CATEGORY_IDS = frozenset(c.id for c in TAXONOMY)

# Overture basic_category / categories.primary → KasMap category.
OVERTURE_MAP: Mapping[str, str] = {
    "coffee_shop": "food.cafe.coffee_shop",
    "cafe": "food.cafe",
    "restaurant": "food.restaurant",
    "fast_food_restaurant": "food.fast_food",
    "burger_restaurant": "food.fast_food",
    "pizza_restaurant": "food.fast_food",
    "bakery": "food.bakery",
    "bar": "food.bar",
    "pub": "food.bar",
    "convenience_store": "retail.convenience",
    "grocery_store": "retail.convenience",
    "supermarket": "retail.supermarket",
    "shopping_center": "retail.mall",
    "shopping_mall": "retail.mall",
    "pharmacy": "health.pharmacy",
    "drugstore": "health.pharmacy",
    "medical_center": "health.clinic",
    "clinic": "health.clinic",
    "doctor": "health.clinic",
    "dentist": "health.dentist",
    "hospital": "health.hospital",
    "beauty_salon": "beauty.salon",
    "nail_salon": "beauty.salon",
    "hair_salon": "beauty.salon",
    "barber": "beauty.barber",
    "gym": "fitness.gym",
    "fitness_center": "fitness.gym",
    "college_university": "education.university",
    "university": "education.university",
    "school": "education.school",
    "elementary_school": "education.school",
    "high_school": "education.school",
    "preschool": "education.kindergarten",
    "kindergarten": "education.kindergarten",
    "bank": "finance.bank",
    "atm": "finance.atm",
    "bus_station": "transport.bus_stop",
    "metro_station": "transport.metro",
    "train_station": "transport.rail",
    "hotel": "lodging.hotel",
    "office_building": "office.business_center",
    # Overture taxonomy / basic_category names seen in releases from September 2026
    "dental_clinic": "health.dentist",
    "dental_office": "health.dentist",
    "outpatient_care_facility": "health.clinic",
    "medical_clinic": "health.clinic",
    "bank_or_credit_union": "finance.bank",
    "casual_eatery": "food.restaurant",
    "fast_food_or_quick_service_restaurant": "food.fast_food",
    "coffee_or_tea_shop": "food.cafe.coffee_shop",
    "bakery_or_pastry_shop": "food.bakery",
    "bar_or_pub": "food.bar",
    "grocery_or_convenience_store": "retail.convenience",
    "pharmacy_or_drugstore": "health.pharmacy",
    "beauty_salon_or_barber": "beauty.salon",
    "barber_shop": "beauty.barber",
    "barbershop": "beauty.barber",
    "fitness_studio": "fitness.gym",
    "gym_or_fitness_center": "fitness.gym",
    "college_or_university": "education.university",
    "primary_or_secondary_school": "education.school",
    "childcare_or_preschool": "education.kindergarten",
    "day_care_preschool": "education.kindergarten",
    "hostel": "lodging.hotel",
    "bus_stop": "transport.bus_stop",
    "subway_station": "transport.metro",
    "railway_station": "transport.rail",
    "corporate_or_business_office": "office.business_center",
}

# OSM "key=value" → KasMap category. Order of keys checked: see OSM_KEYS.
OSM_MAP: Mapping[str, str] = {
    "amenity=cafe": "food.cafe",
    "amenity=restaurant": "food.restaurant",
    "amenity=fast_food": "food.fast_food",
    "amenity=bar": "food.bar",
    "amenity=pub": "food.bar",
    "shop=bakery": "food.bakery",
    "shop=convenience": "retail.convenience",
    "shop=supermarket": "retail.supermarket",
    "shop=mall": "retail.mall",
    "amenity=pharmacy": "health.pharmacy",
    "shop=chemist": "health.pharmacy",
    "amenity=clinic": "health.clinic",
    "amenity=doctors": "health.clinic",
    "amenity=dentist": "health.dentist",
    "amenity=hospital": "health.hospital",
    "shop=beauty": "beauty.salon",
    "leisure=fitness_centre": "fitness.gym",
    "amenity=university": "education.university",
    "amenity=college": "education.university",
    "amenity=school": "education.school",
    "amenity=kindergarten": "education.kindergarten",
    "amenity=bank": "finance.bank",
    "amenity=atm": "finance.atm",
    "highway=bus_stop": "transport.bus_stop",
    "railway=subway_entrance": "transport.metro",
    "station=subway": "transport.metro",
    "railway=station": "transport.rail",
    "tourism=hotel": "lodging.hotel",
}

OSM_KEYS: tuple[str, ...] = (
    "amenity", "shop", "leisure", "tourism", "office", "healthcare",
    "railway", "station", "highway", "public_transport", "craft",
)


def map_overture(
    basic_category: str | None,
    primary: str | None = None,
    hierarchy: list[str] | None = None,
) -> str | None:
    """Map an Overture place to a KasMap category id (None = unmapped).

    Most specific first: `taxonomy.primary`, then `taxonomy.hierarchy` from leaf to root,
    then `basic_category`.  (`categories.primary`, removed in late 2026 releases, is
    accepted as `primary` for older releases.)
    """
    keys = [primary, *reversed(hierarchy or []), basic_category]
    for key in keys:
        if key and key in OVERTURE_MAP:
            return OVERTURE_MAP[key]
    return None


def map_osm(tags: Mapping[str, str]) -> str | None:
    """Map OSM tags to a KasMap category id, with a few tag-combination rules."""
    if tags.get("amenity") == "cafe" and "coffee_shop" in tags.get("cuisine", "").split(";"):
        return "food.cafe.coffee_shop"
    if tags.get("shop") == "hairdresser" and tags.get("male") == "yes":
        return "beauty.barber"
    if tags.get("shop") == "hairdresser":
        return "beauty.salon"
    for key in OSM_KEYS:
        value = tags.get(key)
        if value is None:
            continue
        cat = OSM_MAP.get(f"{key}={value}")
        if cat:
            return cat
    return None


def osm_primary_tag(tags: Mapping[str, str]) -> str | None:
    """The 'key=value' pair that best describes the object, for unmapped reporting."""
    for key in OSM_KEYS:
        if key in tags:
            return f"{key}={tags[key]}"
    return None


def is_ancestor(ancestor: str, category: str) -> bool:
    return category == ancestor or category.startswith(ancestor + ".")


def compatible(a: str | None, b: str | None) -> bool:
    """Two categories can describe the same physical place (used by dedup)."""
    if a is None or b is None:
        return True
    return a.split(".")[0] == b.split(".")[0]
