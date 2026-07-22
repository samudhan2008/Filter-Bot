import re

_YEAR_RE = re.compile(r'\b(19\d{2}|20\d{2})\b')

# Flexible separator: matches a space, dot, underscore, dash, or nothing at
# all between the pieces of a season/episode tag — covers "S01E01",
# "S01.E01", "S01_E01", "S01 - E01", "S01E01" all the same way.
_SEP = r'[\s._-]*'

# Broad set of season/episode filename conventions, tried in order:
#   S01E01, S1E1, S01.E01, S01_EP01, S01 EP 01, S01EP01
#   Season 1 Episode 1, Season.1.EP1, Season1E1
#   1x01
_EPISODE_PATTERNS = [
    re.compile(rf'\bS(\d{{1,2}}){_SEP}E(?:P)?\.?{_SEP}(\d{{1,3}})\b', re.IGNORECASE),
    re.compile(rf'\bSeason{_SEP}(\d{{1,2}}){_SEP}(?:Episode|EP|E)\.?{_SEP}(\d{{1,3}})\b', re.IGNORECASE),
    re.compile(rf'\b(\d{{1,2}})x(\d{{1,3}})\b', re.IGNORECASE),
]
# A bare "S01" / "Season 1" / "Season.1" with no episode number — series-wide, one season.
_SEASON_ONLY_PATTERNS = [
    re.compile(r'\bS(\d{1,2})\b', re.IGNORECASE),
    re.compile(rf'\bSeason{_SEP}(\d{{1,2}})\b', re.IGNORECASE),
]


def extract_year(text: str):
    """Returns (clean_text_without_year, year_or_None)."""
    m = _YEAR_RE.search(text)
    if not m:
        return text.strip(), None
    year = int(m.group(1))
    clean = (text[:m.start()] + text[m.end():]).strip()
    clean = re.sub(r'\s+', ' ', clean)
    return clean, year


def extract_episode(text: str):
    """
    Returns (clean_text, season_or_None, episode_or_None). Used both for
    parsing a user's search query ("GOT S01E03") and for tagging indexed
    filenames, which show up in all sorts of conventions — S01E01,
    Season.1.EP1, S01 EP1, S1x01, etc. Tries full season+episode patterns
    first, then falls back to a bare season-only pattern.
    """
    for pat in _EPISODE_PATTERNS:
        m = pat.search(text)
        if m:
            season, episode = int(m.group(1)), int(m.group(2))
            clean = re.sub(r'\s+', ' ', (text[:m.start()] + text[m.end():])).strip()
            return clean, season, episode

    for pat in _SEASON_ONLY_PATTERNS:
        m = pat.search(text)
        if m:
            season = int(m.group(1))
            clean = re.sub(r'\s+', ' ', (text[:m.start()] + text[m.end():])).strip()
            return clean, season, None

    return text.strip(), None, None
