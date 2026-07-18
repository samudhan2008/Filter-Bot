RESULT_CAPTION = """🎬 <b>{title}</b> {year_part}
🗣 Language: <b>{language}</b>

📦 Found <b>{file_count}</b> file(s) matching your search.
{website_part}
Tap a button below to get your file 👇"""

NO_WEBSITE_HINT = ""  # intentionally blank — user said don't show a link if it's not on the site

WEBSITE_LINE = "🌐 Watch/Stream on SC Files: {link}\n"

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
