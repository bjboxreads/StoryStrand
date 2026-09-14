DEFAULT_THEME = "Emerald Grimoire"

# ---------------- FIX: custom color scheme support ----------------
#
# "Custom" isn't a palette living in THEMES above - it's a placeholder
# name whose actual colors come from whatever the user has saved for
# themselves (state["custom_palette"] in main.py). Keeping it out of
# THEMES means every existing THEMES[...] lookup elsewhere keeps
# working untouched; get_palette() below is the one place that knows
# how to resolve "Custom" to real colors.

import json
import os

APP_DATA_DIR = os.getenv("FLET_APP_STORAGE_DATA", os.path.dirname(__file__))
SETTINGS_FILE = os.path.join(APP_DATA_DIR, "settings.json")

CUSTOM_THEME_NAME = "Custom"

PALETTE_KEYS = ["page", "surface", "surface2", "card", "text", "muted", "accent", "accent2", "line"]

# Friendly labels for the palette-editor dialog - the raw keys above
# are meant for code, not for a user picking colors.
PALETTE_LABELS = {
    "page": "Background",
    "surface": "Panel",
    "surface2": "Panel (Deeper)",
    "card": "Card",
    "text": "Text",
    "muted": "Muted Text",
    "accent": "Accent",
    "accent2": "Accent 2",
    "line": "Divider Line",
}


def theme_names():
    """Every selectable theme name. "Custom" is appended after the
    premade palettes so it always sits at the bottom of the picker."""
    return list(THEMES.keys()) + [CUSTOM_THEME_NAME]


def get_palette(theme_name: str, custom_palette: dict | None) -> dict:
    """Resolves any theme name (premade or "Custom") to a real color
    dict. Falls back to the default theme's colors if "Custom" is
    selected but the user hasn't saved a palette yet (or it's
    incomplete), so a widget can never end up with a missing key."""
    if theme_name == CUSTOM_THEME_NAME:
        base = dict(THEMES[DEFAULT_THEME])
        if custom_palette:
            base.update({k: v for k, v in custom_palette.items() if k in PALETTE_KEYS})
        return base
    return THEMES.get(theme_name, THEMES[DEFAULT_THEME])


def load_settings() -> dict:
    """Loads {"theme": name, "custom_palette": {...}} saved from a
    previous session, so the app reopens showing the theme the user
    actually left it on (including a custom one) instead of always
    resetting to the default. Returns safe defaults if nothing's been
    saved yet or the file is unreadable."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "theme": data.get("theme") or DEFAULT_THEME,
                "custom_palette": data.get("custom_palette") or None,
            }
        except (json.JSONDecodeError, OSError):
            pass
    return {"theme": DEFAULT_THEME, "custom_palette": None}


def save_settings(theme_name: str, custom_palette: dict | None) -> None:
    """Persists the active theme (and the user's custom palette, if
    any) so it survives an app restart."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"theme": theme_name, "custom_palette": custom_palette}, f, indent=2)
    except OSError:
        pass
