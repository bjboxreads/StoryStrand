"""
themes.py — StoryStrand color palettes.

Ported from the old app's `data.THEMES`. Same 10 named palettes,
same key names (page/surface/surface2/card/text/muted/accent/accent2/
line) so the rest of the UI can stay palette-agnostic and any widget
that expects these keys just works.

Usage in main.py:

    from themes import THEMES, DEFAULT_THEME, theme_names

    def color(key):
        return THEMES[state["theme"]][key]
"""

THEMES = {
    "Emerald Grimoire": {
        "page": "#051614", "surface": "#0C2B24", "surface2": "#124A3C",
        "card": "#1B6650", "text": "#FFFBEF", "muted": "#B9D4C6",
        "accent": "#F2C14E", "accent2": "#3FCDA8", "line": "#E0972E",
    },
    "Gilded Midnight": {
        "page": "#04101F", "surface": "#0A2242", "surface2": "#123B6E",
        "card": "#1B5493", "text": "#FFF9E8", "muted": "#B7CBE8",
        "accent": "#FFC94D", "accent2": "#4FC3E8", "line": "#E8A426",
    },
    "Velvet Rose": {
        "page": "#170512", "surface": "#2E0A24", "surface2": "#4F1140",
        "card": "#731B5C", "text": "#FFF3F8", "muted": "#E3BFD2",
        "accent": "#FF6F91", "accent2": "#C77DFF", "line": "#E23E75",
    },
    "Autumn Ember": {
        "page": "#1C0D04", "surface": "#361708", "surface2": "#5E2A0C",
        "card": "#873F13", "text": "#FFF4DE", "muted": "#E8C79A",
        "accent": "#FFB238", "accent2": "#FF6A3D", "line": "#D9601F",
    },
    "Moonlit Violet": {
        "page": "#08071A", "surface": "#12103A", "surface2": "#221D66",
        "card": "#362D93", "text": "#FFFAEE", "muted": "#C9C2ED",
        "accent": "#FFD54F", "accent2": "#9D7BFF", "line": "#7A5CE0",
    },
    "Verdant Garden": {
        "page": "#04191C", "surface": "#093733", "surface2": "#0E5652",
        "card": "#157A70", "text": "#FFFAE9", "muted": "#BFE0D6",
        "accent": "#FFCD3C", "accent2": "#FF7A5C", "line": "#E85A3E",
    },
    "Old World Atlas": {
        "page": "#0F1A18", "surface": "#1C2F2A", "surface2": "#2E4C41",
        "card": "#456B55", "text": "#FFF6DC", "muted": "#D2DABF",
        "accent": "#F0BB4E", "accent2": "#B7D25C", "line": "#D68C2E",
    },
    "Arcane Spell": {
        "page": "#080916", "surface": "#151637", "surface2": "#232463",
        "card": "#332F94", "text": "#FFFFFF", "muted": "#C9C9F2",
        "accent": "#FFD23F", "accent2": "#4FE8DD", "line": "#B15CFF",
    },
    "Scarlet Manor": {
        "page": "#180505", "surface": "#340A0A", "surface2": "#5C1010",
        "card": "#831A1A", "text": "#FFF6E8", "muted": "#EFC7B0",
        "accent": "#FFC145", "accent2": "#FF7D5C", "line": "#E33A2E",
    },
    "Obsidian Vale": {
        "page": "#050505", "surface": "#0F0D0D", "surface2": "#1A1616",
        "card": "#241E1E", "text": "#F2EDEA", "muted": "#8C8080",
        "accent": "#8A1F2B", "accent2": "#9C9C9C", "line": "#5C141C",
    },
}

DEFAULT_THEME = "Emerald Grimoire"


def theme_names():
    return list(THEMES.keys())
