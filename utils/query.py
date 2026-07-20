import re

_YEAR_RE = re.compile(r'\b(19\d{2}|20\d{2})\b')

# Matches S01E02, S1E2, Season 1 Episode 2, 1x02, s01 e02, etc.
_EPISODE_PATTERNS = [
    re.compile(r'\bS(\d{1,2})\s*E(\d{1,3})\b', re.IGNORECASE),
    re.compile(r'\bSeason\s*(\d{1,2})\s*Episode\s*(\d{1,3})\b', re.IGNORECASE),
    re.compile(r'\b(\d{1,2})x(\d{1,3})\b', re.IGNORECASE),
]
# Matches a bare "S01" / "Season 1" with no episode number — series-wide, one season.
_SEASON_ONLY_PATTERNS = [
    re.compile(r'\bS(\d{1,2})\b', re.IGNORECASE),
    re.compile(r'\bSeason\s*(\d{1,2})\b', re.IGNORECASE),
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
    Returns (clean_text, season_or_None, episode_or_None).
    Tries full S..E.. style patterns first, then falls back to a bare
    season-only pattern (e.g. "GOT season 2" -> whole season, no episode
    filter).
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
