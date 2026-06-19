"""Shared configuration for Rasa action server."""

import os

# Base URL for the FastAPI backend
FASTAPI_BASE_URL = os.environ.get("FASTAPI_BASE_URL", "http://localhost:8000")

# Base directory for uploaded files (must match backend)
UPLOAD_BASE_DIR = os.environ.get(
    "UPLOAD_BASE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads"),
)

# Directory containing the analytical Python scripts
SCRIPTS_DIR = os.environ.get(
    "SCRIPTS_DIR",
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
)

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

FILTER_OPERATORS = [
    {"label": "equals", "value": "eq"},
    {"label": "does not equal", "value": "ne"},
    {"label": "is in list", "value": "in"},
    {"label": "is not in list", "value": "not_in"},
    {"label": "greater than", "value": "gt"},
    {"label": "greater than or equal", "value": "gte"},
    {"label": "less than", "value": "lt"},
    {"label": "less than or equal", "value": "lte"},
    {"label": "between", "value": "between"},
    {"label": "contains", "value": "contains"},
    {"label": "starts with", "value": "startswith"},
    {"label": "ends with", "value": "endswith"},
    {"label": "is blank", "value": "isblank"},
    {"label": "is not blank", "value": "notblank"},
]
