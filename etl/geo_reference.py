"""Country code normalization for ETL partner loads."""
from __future__ import annotations

# Keep in sync with backend/app/services/geo_reference.py
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

LEGACY_TO_ISO3: dict[str, str] = {"D": "DEU", "B": "BEL", "L": "LUX"}

ISO2_TO_ISO3: dict[str, str] = {
    "DE": "DEU", "FR": "FRA", "GB": "GBR", "UK": "GBR", "NL": "NLD",
    "AT": "AUT", "CH": "CHE", "IT": "ITA", "ES": "ESP", "PL": "POL",
    "US": "USA", "BE": "BEL", "LU": "LUX", "CZ": "CZE", "HU": "HUN",
    "IE": "IRL", "RO": "ROU", "SK": "SVK",
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
