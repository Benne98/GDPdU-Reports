"""Country code normalization and display names for GL partner geo dimensions."""
from __future__ import annotations

COUNTRY_NAMES: dict[str, str] = {
    "DEU": "Germany",
    "FRA": "France",
    "GBR": "United Kingdom",
    "NLD": "Netherlands",
    "AUT": "Austria",
    "CHE": "Switzerland",
    "ITA": "Italy",
    "ESP": "Spain",
    "POL": "Poland",
    "USA": "United States",
    "CAN": "Canada",
    "BEL": "Belgium",
    "LUX": "Luxembourg",
    "CZE": "Czech Republic",
    "HUN": "Hungary",
    "IRL": "Ireland",
    "ROU": "Romania",
    "SVK": "Slovakia",
    "UNK": "Unknown",
}

LEGACY_TO_ISO3: dict[str, str] = {
    "D": "DEU",
    "B": "BEL",
    "L": "LUX",
}

ISO2_TO_ISO3: dict[str, str] = {
    "DE": "DEU",
    "FR": "FRA",
    "GB": "GBR",
    "UK": "GBR",
    "NL": "NLD",
    "AT": "AUT",
    "CH": "CHE",
    "IT": "ITA",
    "ES": "ESP",
    "PL": "POL",
    "US": "USA",
    "BE": "BEL",
    "LU": "LUX",
    "CZ": "CZE",
    "HU": "HUN",
    "IE": "IRL",
    "RO": "ROU",
    "SK": "SVK",
}


def normalize_country_code(raw: str | None) -> str | None:
    if raw is None:
        return None
    up = str(raw).strip().upper()
    if not up:
        return None
    if up in LEGACY_TO_ISO3:
        return LEGACY_TO_ISO3[up]
    if up in COUNTRY_NAMES:
        return up
    if up in ISO2_TO_ISO3:
        return ISO2_TO_ISO3[up]
    return up[:3]


def country_display_name(code: str | None) -> str:
    norm = normalize_country_code(code)
    if not norm:
        return "Unknown"
    return COUNTRY_NAMES.get(norm, norm)


def _label_by_code() -> dict[str, str]:
    out: dict[str, str] = {}
    for iso3, name in COUNTRY_NAMES.items():
        out[iso3] = name
    for src, iso3 in {**LEGACY_TO_ISO3, **ISO2_TO_ISO3}.items():
        out[src] = COUNTRY_NAMES.get(iso3, iso3)
    return out


def sql_country_label_expr(dc_alias: str = "dc") -> str:
    """SQL: dim_customer.country_code → English label (matches frontend countryLabels)."""
    cases = "\n".join(
        f"                WHEN '{code}' THEN '{name.replace(chr(39), chr(39)+chr(39))}'"
        for code, name in sorted(_label_by_code().items())
    )
    return f"""
        COALESCE(
            NULLIF(TRIM(dco.name_en), ''),
            NULLIF(TRIM(dco.name_de), ''),
            CASE TRIM({dc_alias}.country_code)
{cases}
                ELSE NULLIF(TRIM({dc_alias}.country_code), '')
            END,
            'Unknown'
        )
    """.strip()


def sql_end_customer_region_expr(dc_alias: str = "dc") -> str:
    """Region name when present; otherwise country label (GL has no sales territories)."""
    country = sql_country_label_expr(dc_alias)
    return f"""
        COALESCE(
            NULLIF(TRIM(dr.name_en), ''),
            NULLIF(TRIM(dr.name_de), ''),
            {country}
        )
    """.strip()
