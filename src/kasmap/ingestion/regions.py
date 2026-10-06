"""Named groups of countries for `kasmap ingest region <name>`.

Continents are not shipped by Overture divisions; groups are explicit until a vetted
ISO-3166 → continent table is added to the registry.
"""

REGION_GROUPS: dict[str, tuple[str, ...]] = {
    "pilot": ("KZ",),
    "central-asia": ("KZ", "UZ", "KG", "TJ", "TM"),
    # Pipeline test set from the World Coverage Engine plan (STEP 10).
    "test-5": ("DE", "US", "AE", "JP", "BR"),
}
