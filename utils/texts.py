import random

RESULT_CAPTION = """🎬 <b>{title}</b> {year_part}
🗣 Language: <b>{language}</b>

📦 Found <b>{file_count}</b> file(s) matching your search.
{website_part}
Tap a button below to get your file 👇"""

NO_WEBSITE_HINT = ""  # intentionally blank — user said don't show a link if it's not on the site

WEBSITE_LINE = "🌐 Watch/Stream on SC Files: {link}\n"

# Picked at random so repeated "no results" hits don't feel like a canned
# error message every single time.
NOT_FOUND_VARIANTS = [
    "😔 No files found for <b>{query}</b>.\n\nTry a different spelling, or add the release year "
    "(e.g. <code>{query} 2023</code>) to narrow it down.",
    "🙁 Couldn't find anything for <b>{query}</b>.\n\nSry, pls check the spelling and try again — "
    "small typos can throw the search off completely.",
    "🔍 No matches for <b>{query}</b>.\n\nDouble-check the spelling, or try just the main title "
    "without extra words.",
    "😅 Nothing came up for <b>{query}</b>.\n\nMaybe try the original-language title, or a shorter "
    "version of the name?",
]

# Shown while checking TMDB / building the result — rotated for variety.
WAIT_MESSAGES = [
    "⏳ Please wait…",
    "🔄 Processing…",
    "🧐 Working on it…",
    "🚀 Almost there…",
]

# Shown as the quick toast when a picker button (disambiguation, season,
# episode) is tapped.
FETCHING_TOASTS = [
    "Please wait…",
    "Processing…",
    "Fetching…",
    "One sec…",
]


def not_found(query: str) -> str:
    return random.choice(NOT_FOUND_VARIANTS).format(query=query)


def wait_message() -> str:
    return random.choice(WAIT_MESSAGES)


def fetching_toast() -> str:
    return random.choice(FETCHING_TOASTS)


NO_FILES_FOUND = (
    "😔 No files found for <b>{query}</b>.\n\n"
    "Try a different spelling, or add the release year (e.g. <code>{query} 2023</code>) "
    "to narrow it down."
)

DISAMBIGUATION_PROMPT = "🔎 I found a few matches for <b>{query}</b> — which one did you mean?"

ADMIN_NOT_ON_WEBSITE = (
    "⚠️ <b>Not listed on SC Files</b>\n\n"
    "🎬 {title} ({year}) — {kind}\n"
    "TMDB ID: <code>{tmdb_id}</code>\n\n"
    "A user searched for this and files exist in the bot's index, but this title "
    "isn't on scfiles.vercel.app yet. You may want to add it."
)

FILE_BUTTON_LABEL = "📄 {name} ({size})"
