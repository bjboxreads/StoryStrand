"""
StoryStrand - themes.py
Ten color themes. Each theme provides the palette used to build a Flet
ft.Theme plus a few raw hex values the UI reaches for directly (ghost
placeholder tint, tree branch lines, favorite star, etc).
"""

THEMES = {
    "Emerald Grimoire": {
        "primary": "#1B4332",
        "secondary": "#52796F",
        "background": "#0D1B14",
        "surface": "#16281F",
        "text": "#E8F0E6",
        "accent": "#D4AF37",   # gilded gold accent
        "branch_line": "#52796F",
        "favorite": "#D4AF37",
    },
    "Gilded Midnight": {
        "primary": "#10131F",
        "secondary": "#3A3F58",
        "background": "#0A0C15",
        "surface": "#171B2B",
        "text": "#F1EAD8",
        "accent": "#C9A227",
        "branch_line": "#C9A227",
        "favorite": "#C9A227",
    },
    "Velvet Rose": {
        "primary": "#5C1A2B",
        "secondary": "#8C3A50",
        "background": "#2B0C15",
        "surface": "#3D141F",
        "text": "#F6E3E8",
        "accent": "#E7A8B8",
        "branch_line": "#8C3A50",
        "favorite": "#E7A8B8",
    },
    "Autumn Ember": {
        "primary": "#7A2E12",
        "secondary": "#B5651D",
        "background": "#2A1409",
        "surface": "#3E2013",
        "text": "#FBE9D6",
        "accent": "#E8A33D",
        "branch_line": "#B5651D",
        "favorite": "#E8A33D",
    },
    "Moonlit Violet": {
        "primary": "#2E1A47",
        "secondary": "#5E3B8C",
        "background": "#150C24",
        "surface": "#221334",
        "text": "#EDE3F7",
        "accent": "#B08CE0",
        "branch_line": "#5E3B8C",
        "favorite": "#B08CE0",
    },
    "Verdant Garden": {
        "primary": "#355E3B",
        "secondary": "#7CB27A",
        "background": "#101F13",
        "surface": "#1B2E1E",
        "text": "#EAF3E5",
        "accent": "#EFC85B",
        "branch_line": "#7CB27A",
        "favorite": "#EFC85B",
    },
    "Old World Atlas": {
        "primary": "#4A3728",
        "secondary": "#8A6D4E",
        "background": "#241A11",
        "surface": "#33261A",
        "text": "#F1E6D2",
        "accent": "#8FA6A3",   # faded map-teal accent
        "branch_line": "#8A6D4E",
        "favorite": "#C97B4A",
    },
    "Arcane Spell": {
        "primary": "#1B2A4A",
        "secondary": "#3E5C8A",
        "background": "#0B1220",
        "surface": "#141F35",
        "text": "#E4ECFB",
        "accent": "#7FD9C4",
        "branch_line": "#3E5C8A",
        "favorite": "#7FD9C4",
    },
    "Scarlet Manor": {
        "primary": "#6B0F1A",
        "secondary": "#9C2B39",
        "background": "#240508",
        "surface": "#380B10",
        "text": "#F7E4E0",
        "accent": "#D9A441",
        "branch_line": "#9C2B39",
        "favorite": "#D9A441",
    },
    "Obsidian Vale": {
        "primary": "#1A1A1D",
        "secondary": "#4E4E56",
        "background": "#0A0A0C",
        "surface": "#18181B",
        "text": "#EDEDEF",
        "accent": "#7A8B99",
        "branch_line": "#4E4E56",
        "favorite": "#9FB4C7",
    },
}

DEFAULT_THEME = "Emerald Grimoire"


def theme_names():
    return list(THEMES.keys())
