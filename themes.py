"""
StoryStrand - themes.py
Color themes, ported from SpineVesper's palette (data.py THEMES), plus
one StoryStrand-original: Enchanted Library.

SpineVesper used CSS-custom-property-style keys: page/surface/surface2/
card/text/muted/accent/accent2/line. This file's mapping onto
StoryStrand's key set:
    page    -> background
    card    -> primary       (top-level author band)
    accent2 -> secondary     (SpineVesper's "read"-status highlight color)
    surface -> surface       (book card background - same role in both)
    text    -> text
    accent  -> accent
    line    -> branch_line
    accent  -> favorite      (SpineVesper always used its accent color for the star icon)

SpineVesper's `surface2` (nested series-row band) and `muted` (caption
text) have no equivalent slot here and are dropped rather than
force-fit into an unrelated key.
"""

THEMES = {
    "Emerald Grimoire": {
        "primary": "#1B6650",
        "secondary": "#3FCDA8",
        "background": "#051614",
        "surface": "#0C2B24",
        "text": "#FFFBEF",
        "accent": "#F2C14E",
        "branch_line": "#E0972E",
        "favorite": "#F2C14E",
    },
    "Gilded Midnight": {
        "primary": "#1B5493",
        "secondary": "#4FC3E8",
        "background": "#04101F",
        "surface": "#0A2242",
        "text": "#FFF9E8",
        "accent": "#FFC94D",
        "branch_line": "#E8A426",
        "favorite": "#FFC94D",
    },
    "Velvet Rose": {
        "primary": "#731B5C",
        "secondary": "#C77DFF",
        "background": "#170512",
        "surface": "#2E0A24",
        "text": "#FFF3F8",
        "accent": "#FF6F91",
        "branch_line": "#E23E75",
        "favorite": "#FF6F91",
    },
    "Autumn Ember": {
        "primary": "#873F13",
        "secondary": "#FF6A3D",
        "background": "#1C0D04",
        "surface": "#361708",
        "text": "#FFF4DE",
        "accent": "#FFB238",
        "branch_line": "#D9601F",
        "favorite": "#FFB238",
    },
    "Moonlit Violet": {
        "primary": "#362D93",
        "secondary": "#9D7BFF",
        "background": "#08071A",
        "surface": "#12103A",
        "text": "#FFFAEE",
        "accent": "#FFD54F",
        "branch_line": "#7A5CE0",
        "favorite": "#FFD54F",
    },
    "Verdant Garden": {
        "primary": "#157A70",
        "secondary": "#FF7A5C",
        "background": "#04191C",
        "surface": "#093733",
        "text": "#FFFAE9",
        "accent": "#FFCD3C",
        "branch_line": "#E85A3E",
        "favorite": "#FFCD3C",
    },
    "Old World Atlas": {
        "primary": "#456B55",
        "secondary": "#B7D25C",
        "background": "#0F1A18",
        "surface": "#1C2F2A",
        "text": "#FFF6DC",
        "accent": "#F0BB4E",
        "branch_line": "#D68C2E",
        "favorite": "#F0BB4E",
    },
    "Arcane Spell": {
        "primary": "#332F94",
        "secondary": "#4FE8DD",
        "background": "#080916",
        "surface": "#151637",
        "text": "#FFFFFF",
        "accent": "#FFD23F",
        "branch_line": "#B15CFF",
        "favorite": "#FFD23F",
    },
    "Scarlet Manor": {
        "primary": "#831A1A",
        "secondary": "#FF7D5C",
        "background": "#180505",
        "surface": "#340A0A",
        "text": "#FFF6E8",
        "accent": "#FFC145",
        "branch_line": "#E33A2E",
        "favorite": "#FFC145",
    },
    "Obsidian Vale": {
        "primary": "#241E1E",
        "secondary": "#9C9C9C",
        "background": "#050505",
        "surface": "#0F0D0D",
        "text": "#F2EDEA",
        "accent": "#8A1F2B",
        "branch_line": "#5C141C",
        "favorite": "#8A1F2B",
    },
    "Enchanted Library": {
        # Warm parchment + gold-leaf, distinct from the other nine
        # (which all run dark/jewel-toned) — reads like old paper and
        # candlelight rather than a moody nightscape.
        "primary": "#8B5E34",
        "secondary": "#6B8F71",
        "background": "#F4E9D8",
        "surface": "#EBDBC0",
        "text": "#3B2A1A",
        "accent": "#C08A28",
        "branch_line": "#B08B5A",
        "favorite": "#C08A28",
    },
}

DEFAULT_THEME = "Emerald Grimoire"


def theme_names():
    return list(THEMES.keys())
