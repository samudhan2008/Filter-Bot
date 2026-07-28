"""
Indexed-file storage + search.
File-id packing logic and the fast bulk-insert /index path are the two
pieces worth keeping from the old codebase; everything else here is
rewritten — in particular the search regex now strips ALL separator
noise (., _, -, +, brackets, extra spaces) from both the query and the
stored file name before matching, which was the root cause of "movie is
in the DB but the bot can't find it".
"""

import logging
import re
import base64
from datetime import datetime, timezone
from struct import pack

import difflib

from pyrogram.file_id import FileId
from pymongo.errors import DuplicateKeyError, BulkWriteError
from pymongo import InsertOne, TEXT, DESCENDING
from umongo import Instance, Document, fields
from marshmallow.exceptions import ValidationError

import info
from utils.query import extract_episode, extract_year
from utils.cache import TTLCache
from database.mongo import db, client

logger = logging.getLogger(__name__)

instance = Instance.from_db(db)


@instance.register
class Media(Document):
    file_id = fields.StrField(attribute='_id')
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    normalized_name = fields.StrField(required=True)  # separator-stripped, for fast/robust search
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)
    season_number = fields.IntField(allow_none=True)
    ep_number = fields.IntField(allow_none=True)
    file_date = fields.DateTimeField(allow_none=True)   # when it was originally posted to the source channel
    indexed_at = fields.DateTimeField(allow_none=True)  # when *this bot* saved it — fallback sort key
    # normalized_name/caption with every space removed too — backs a
    # last-resort search pass for cases like "spider man" (query, two
    # words) vs a file actually named "spiderman" (one word, no space).
    # New field; documents indexed before this update won't have it until
    # re-indexed, so the fallback pass simply won't reach them yet — every
    # other search path is unaffected.
    squeezed_name = fields.StrField(allow_none=True)
    squeezed_caption = fields.StrField(allow_none=True)
    # Release year parsed from the filename (or caption, as a fallback) at
    # index time — lets search disambiguate two different titles that
    # happen to share the exact same name but released in different years
    # (remakes, regional versions, etc.). None when neither the filename
    # nor caption mentions a year.
    release_year = fields.IntField(allow_none=True)

    class Meta:
        indexes = ('$normalized_name',)
        collection_name = info.COLLECTION_NAME


async def ensure_indexes():
    """Called once at startup. Adds a Mongo text index (only used when
    USE_MONGO_TEXT_SEARCH=True) — scales much better than regex scans once
    the collection is large, since it uses an inverted index instead of a
    full collection scan."""
    try:
        await Media.collection.create_index([('file_name', TEXT), ('caption', TEXT)], name='scfiles_text_idx')
        await Media.collection.create_index('season_number')
        await Media.collection.create_index('ep_number')
        await Media.collection.create_index([('file_date', DESCENDING), ('indexed_at', DESCENDING)],
                                             name='scfiles_recency_idx')
        logger.info("Ensured filesdb indexes (text + season/episode + recency).")
    except Exception as e:
        logger.warning(f"Could not ensure indexes (non-fatal): {e}")


# Everything that isn't a letter/number becomes a single space. This is what
# fixes ".", "-", "_", "+", brackets etc breaking search: both the stored
# name and every incoming query go through this before matching.
_SEP_RE = re.compile(r"[^a-z0-9]+")

# Apostrophes (straight/curly/backtick) sit *inside* a word — a possessive
# or contraction like "Nobita's" — not between two words. Stripping them
# outright (rather than treating them as a separator like _SEP_RE does)
# keeps "Nobita's" as one token, "nobitas", on both the query and the
# stored-name side. Without this, "Nobita's" -> "nobita" + a standalone,
# meaningless "s" token, which the word-fallback pass would then treat as
# a real search term — matching almost any other file with an apostrophe
# in its name (e.g. "Watchman's" -> "watchman" + "s") and returning wildly
# unrelated results.
_APOSTROPHE_RE = re.compile(r"[\u2019\u2018'`]")


def normalize(text: str) -> str:
    text = _APOSTROPHE_RE.sub("", (text or "").lower())
    return _SEP_RE.sub(" ", text).strip()


def encode_file_id(s: bytes) -> str:
    r, n = b"", 0
    for i in s + bytes([22]) + bytes([4]):
        if i == 0:
            n += 1
        else:
            if n:
                r += b"\x00" + bytes([n])
                n = 0
            r += bytes([i])
    return base64.urlsafe_b64encode(r).decode().rstrip("=")


def encode_file_ref(file_ref: bytes) -> str:
    return base64.urlsafe_b64encode(file_ref).decode().rstrip("=")


def unpack_new_file_id(new_file_id):
    decoded = FileId.decode(new_file_id)
    file_id = encode_file_id(
        pack("<iiqq", int(decoded.file_type), decoded.dc_id, decoded.media_id, decoded.access_hash)
    )
    file_ref = encode_file_ref(decoded.file_reference)
    return file_id, file_ref


def _doc_from_media(media):
    file_id, file_ref = unpack_new_file_id(media.file_id)
    name = str(media.file_name)
    _, season, episode = extract_episode(name)
    norm_name = normalize(name)
    caption_html = media.caption.html if media.caption else None

    _, release_year = extract_year(name)
    if release_year is None and caption_html:
        _, release_year = extract_year(caption_html)

    return {
        '_id': file_id,
        'file_ref': file_ref,
        'file_name': name,
        'normalized_name': norm_name,
        'file_size': media.file_size,
        'file_type': media.file_type,
        'mime_type': media.mime_type,
        'caption': caption_html,
        'season_number': season,
        'ep_number': episode,
        'file_date': getattr(media, 'file_date', None),
        'indexed_at': datetime.now(timezone.utc),
        'squeezed_name': norm_name.replace(' ', ''),
        'squeezed_caption': normalize(caption_html).replace(' ', '') if caption_html else None,
        'release_year': release_year,
    }


async def save_file(media):
    try:
        doc = _doc_from_media(media)
        file = Media(
            file_id=doc['_id'], file_ref=doc['file_ref'], file_name=doc['file_name'],
            normalized_name=doc['normalized_name'], file_size=doc['file_size'],
            file_type=doc['file_type'], mime_type=doc['mime_type'], caption=doc['caption'],
            season_number=doc['season_number'], ep_number=doc['ep_number'],
            file_date=doc['file_date'], indexed_at=doc['indexed_at'],
            squeezed_name=doc['squeezed_name'], squeezed_caption=doc['squeezed_caption'],
            release_year=doc['release_year'],
        )
    except ValidationError:
        logger.exception('Error building Media doc')
        return False, 2
    try:
        await file.commit()
    except DuplicateKeyError:
        return False, 0
    return True, 1


async def save_files_batch(media_list):
    """Bulk insert for /index — this is what makes indexing fast."""
    if not media_list:
        return 0, 0, 0
    ops, error_count = [], 0
    for media in media_list:
        try:
            ops.append(InsertOne(_doc_from_media(media)))
        except Exception:
            logger.exception('Error preparing file for batch save')
            error_count += 1
    if not ops:
        return 0, 0, error_count
    try:
        result = await Media.collection.bulk_write(ops, ordered=False)
        saved = result.inserted_count
        return saved, len(ops) - saved, error_count
    except BulkWriteError as bwe:
        details = bwe.details
        write_errors = details.get('writeErrors', [])
        dup_count = sum(1 for e in write_errors if e.get('code') == 11000)
        other = len(write_errors) - dup_count
        return details.get('nInserted', 0), dup_count, error_count + other
    except Exception:
        logger.exception('Bulk write failed entirely')
        return 0, 0, error_count + len(ops)


def _build_regex_and(query: str):
    """All words must appear somewhere in the text, in any order — this is
    the 'combined' / full-phrase match, and gets top priority."""
    q = normalize(query)
    words = [w for w in q.split(' ') if w]
    if not words:
        return re.compile('.', re.IGNORECASE)
    lookaheads = ''.join(f'(?=.*\\b{re.escape(w)}\\b)' for w in words)
    return re.compile(lookaheads, re.IGNORECASE)


def _build_regex_any(words):
    """Any single word matches — used as the lower-priority fallback pass
    (e.g. query "hi hello" also separately searches "hi" and "hello")."""
    escaped = [re.escape(w) for w in words if w]
    if not escaped:
        return re.compile('$^')  # matches nothing
    pattern = '|'.join(r'\b' + w + r'\b' for w in escaped)
    return re.compile(pattern, re.IGNORECASE)


# Common short connector words that provide essentially no search signal
# on their own — matching a file purely because it happens to also contain
# "and" (extremely common) produces false positives, not real matches.
# Only used to filter the word-level fallback pass; the exact/combined
# match still requires these words verbatim, since that's correct there
# (a title genuinely containing "and" should still need it in the file name).
_STOPWORDS = {
    'and', 'the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'for', 'with',
    'is', 'it', 'as', 'by', 'or', 'from', 'this', 'that',
}


def _is_significant_word(word: str) -> bool:
    """Same reasoning as _STOPWORDS, plus a length floor: a 1-2 letter
    token (whether a stray normalization artifact or just genuinely short)
    matches so many unrelated files in an OR-based fallback that it isn't
    worth treating as a real search term there."""
    return len(word) >= 3 and word not in _STOPWORDS


def _base_filter(file_type, season, episode, year=None):
    filt = {}
    if file_type:
        filt['file_type'] = file_type
    if season is not None:
        filt['season_number'] = season
    if episode is not None:
        filt['ep_number'] = episode
    if year is not None:
        filt['release_year'] = year
    return filt


async def get_search_results(query, file_type=None, max_results=None, offset=0, season=None, episode=None, year=None):
    """
    Two-pass search, with pass 2 only ever used when pass 1 finds nothing:

    1. Combined pass — every word in the query must appear (any order).
       e.g. "hi hello" -> matches names containing both "hi" and "hello".
       If this finds anything at all, it's the *only* thing returned.
    2. Word pass — only runs when pass 1 found zero matches. Splits the
       query into words (minus common stopwords like "and"/"the") and
       matches any one of them, so e.g. a file named just "hello.mkv"
       still turns up for a query "hi hello" that otherwise matches
       nothing exactly.

    `year`, when given, filters to files whose parsed release_year matches
    — for disambiguating two different titles that happen to share the
    exact same name but released in different years. Callers are expected
    to retry without it if it finds nothing (not every file is tagged with
    a year), same pattern as season/episode — see plugins/search.py's
    _search_with_fallback.
    """
    max_results = max_results or info.MAX_RESULTS
    base = _base_filter(file_type, season, episode, year)
    words = [w for w in normalize(query).split(' ') if w]
    coll = Media.collection
    recency_sort = [('file_date', -1), ('indexed_at', -1)]

    # ---- Pass 1 filter (combined / priority) ----
    if info.USE_MONGO_TEXT_SEARCH and query.strip():
        combined_filt = {**base, '$text': {'$search': query}}
        pass1_sort = [('score', {'$meta': 'textScore'})] + recency_sort
        pass1_projection = {'score': {'$meta': 'textScore'}}
    else:
        regex_and = _build_regex_and(query)
        combined_filt = {**base, 'normalized_name': regex_and}
        if info.USE_CAPTION_FILTER:
            combined_filt = {**base, '$or': [{'normalized_name': regex_and}, {'caption': regex_and}]}
        pass1_sort = recency_sort
        pass1_projection = None

    total1 = await coll.count_documents(combined_filt)

    # ---- Pass 2 filter (individual words) — ONLY when pass 1 found nothing ----
    # Originally this ran whenever pass 1 didn't fill a full page, "topping
    # up" good exact/combined matches with word-level ones. That's exactly
    # what caused a search for e.g. "Pritam and Pedro" to also return
    # something like "Cousins and Kalyanams" — both share the word "and",
    # and pass 2 would pad in that unrelated match purely because pass 1's
    # results didn't fill the page on their own. If the exact/combined
    # match exists at all, it should be the *only* thing shown — word-level
    # fallback is only useful when there's truly nothing else to show.
    # Common stopwords are also excluded from the fallback's OR-list, since
    # a word like "and" matches almost any file and provides no real signal
    # on its own.
    total2 = 0
    word_filt = None
    if total1 == 0 and len(words) > 1:
        significant_words = [w for w in words if _is_significant_word(w)]
        if significant_words:
            regex_any = _build_regex_any(significant_words)
            word_filt = {**base, 'normalized_name': regex_any}
            total2 = await coll.count_documents(word_filt)

    total = total1 if total1 > 0 else total2
    next_offset = offset + max_results
    if next_offset >= total:
        next_offset = ''

    # ---- Fetch: pass 1 if it has anything at all, else pass 2 ----
    raw1, raw2 = [], []
    if total1 > 0:
        cursor1 = coll.find(combined_filt, pass1_projection).sort(pass1_sort).skip(offset).limit(max_results)
        raw1 = await cursor1.to_list(length=max_results)
    elif word_filt is not None:
        cursor2 = coll.find(word_filt).sort(recency_sort).skip(offset).limit(max_results)
        raw2 = await cursor2.to_list(length=max_results)

    # ---- Pass 3: last resort, only when passes 1+2 found nothing at all ----
    # Catches spacing/formatting mismatches between the query and the
    # stored name that word-boundary matching can't — e.g. searching
    # "spider man" (two words) against a file actually named "spiderman"
    # (one word, no space): neither pass above matches "spider" or "man"
    # as a *whole word* inside "spiderman", but squeezing both sides down
    # to letters-only and checking substring containment does.
    if total == 0 and offset == 0:
        squeezed_query = normalize(query).replace(' ', '')
        if squeezed_query:
            pattern = re.compile(re.escape(squeezed_query), re.IGNORECASE)
            squeeze_filt = {**base, 'squeezed_name': pattern}
            if info.USE_CAPTION_FILTER:
                squeeze_filt = {**base, '$or': [{'squeezed_name': pattern}, {'squeezed_caption': pattern}]}
            total3 = await coll.count_documents(squeeze_filt)
            if total3:
                cursor3 = coll.find(squeeze_filt).sort(recency_sort).limit(max_results)
                raw3 = await cursor3.to_list(length=max_results)
                files = [_normalize_doc(d) for d in raw3]
                next_offset3 = max_results if max_results < total3 else ''
                return files, next_offset3, total3

    files = [_normalize_doc(d) for d in (raw1 + raw2)]
    return files, next_offset, total


def _normalize_doc(doc: dict) -> dict:
    """Raw motor docs use '_id' (the umongo storage attribute); every
    caller elsewhere in the bot expects 'file_id'. Normalize once here."""
    doc = dict(doc)
    doc['file_id'] = doc.pop('_id', doc.get('file_id'))
    return doc


_season_data_cache = TTLCache(ttl=300, max_size=2000)  # 5 min — cheap query, but called on every series search


async def has_season_data(query: str) -> bool:
    """Is there at least one file matching this title with a recognized
    season tag at all? Gates whether the series flow should show a
    season/episode picker — if a show's files were never tagged with a
    recognizable S01E01-style marker (a real and common case — plenty of
    uploads use naming conventions our patterns don't cover, or none at
    all), forcing a season_number filter downstream would silently exclude
    every one of them. Better to fall back to a flat, movie-style listing
    than to show a picker that dead-ends into "no files found" every time.
    """
    cache_key = normalize(query)
    cached = _season_data_cache.get(cache_key)
    if cached is not None:
        return cached

    regex_and = _build_regex_and(query)
    doc = await Media.collection.find_one(
        {'normalized_name': regex_and, 'season_number': {'$ne': None}}, {'_id': 1}
    )
    result = doc is not None
    _season_data_cache.set(cache_key, result)
    return result


async def get_distinct_episodes(query: str, season: int):
    """Which episode numbers actually exist in the DB for this series +
    season, so the bot can show real episode-number buttons instead of
    guessing. Uses the same AND-regex as the main combined search pass."""
    regex_and = _build_regex_and(query)
    filt = {'normalized_name': regex_and, 'season_number': season, 'ep_number': {'$ne': None}}
    eps = await Media.collection.distinct('ep_number', filt)
    return sorted(e for e in eps if e is not None)


async def get_suggestions(query: str, limit: int = None):
    """
    Zero-results fallback: pulls a sample of distinct titles that share at
    least one word with the query, then ranks them by string similarity.
    Cheap enough for moderate collection sizes; if this ever becomes a
    bottleneck at scale, USE_MONGO_TEXT_SEARCH's index can back this too.
    """
    limit = limit or info.SUGGEST_MAX_RESULTS
    words = [w for w in normalize(query).split(' ') if len(w) >= 3]
    if not words:
        return []

    or_clauses = [{'normalized_name': re.compile(re.escape(w), re.IGNORECASE)} for w in words]
    cursor = Media.collection.find({'$or': or_clauses}, {'file_name': 1}).limit(300)
    sample = await cursor.to_list(length=300)

    seen, ranked = set(), []
    target = normalize(query)
    for doc in sample:
        name = doc.get('file_name', '')
        norm = normalize(name)
        if norm in seen:
            continue
        seen.add(norm)
        score = difflib.SequenceMatcher(None, target, norm).ratio()
        if score >= info.SUGGEST_MATCH_CUTOFF:
            ranked.append((score, name))

    ranked.sort(key=lambda x: -x[0])
    return [name for _, name in ranked[:limit]]


async def get_file_by_id(file_id):
    cursor = Media.find({'file_id': file_id})
    res = await cursor.to_list(length=1)
    return res[0] if res else None
