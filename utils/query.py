import re

_YEAR_RE = re.compile(r'\b(19\d{2}|20\d{2})\b')


def extract_year(text: str):
    """Returns (clean_text_without_year, year_or_None)."""
    m = _YEAR_RE.search(text)
    if not m:
        return text.strip(), None
    year = int(m.group(1))
    clean = (text[:m.start()] + text[m.end():]).strip()
    clean = re.sub(r'\s+', ' ', clean)
    return clean, year
