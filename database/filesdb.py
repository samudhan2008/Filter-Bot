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
from struct import pack

import difflib

from pyrogram.file_id import FileId
from pymongo.errors import DuplicateKeyError, BulkWriteError
from pymongo import InsertOne, TEXT
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow.exceptions import ValidationError

import info
from utils.query import extract_episode

logger = logging.getLogger(__name__)

client = AsyncIOMotorClient(info.DATABASE_URI)
db = client[info.DATABASE_NAME]
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
        logger.info("Ensured filesdb indexes (text + season/episode).")
    except Exception as e:
        logger.warning(f"Could not ensure indexes (non-fatal): {e}")


# Everything that isn't a letter/number becomes a single space. This is what
# fixes ".", "-", "_", "+", brackets etc breaking search: both the stored
# name and every incoming query go through this before matching.
_SEP_RE = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    return _SEP_RE.sub(" ", (text or "").lower()).strip()


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
    return {
        '_id': file_id,
        'file_ref': file_ref,
        'file_name': name,
        'normalized_name': normalize(name),
        'file_size': media.file_size,
        'file_type': media.file_type,
        'mime_type': media.mime_type,
        'caption': media.caption.html if media.caption else None,
        'season_number': season,
        'ep_number': episode,
    }


async def save_file(media):
    try:
        doc = _doc_from_media(media)
        file = Media(
            file_id=doc['_id'], file_ref=doc['file_ref'], file_name=doc['file_name'],
            normalized_name=doc['normalized_name'], file_size=doc['file_size'],
            file_type=doc['file_type'], mime_type=doc['mime_type'], caption=doc['caption'],
            season_number=doc['season_number'], ep_number=doc['ep_number'],
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
    lookaheads = ''.join(f'(?=.*\\b{re.escape(w)})' for w in words)
    return re.compile(lookaheads, re.IGNORECASE)


def _build_regex_any(words):
    """Any single word matches — used as the lower-priority fallback pass
    (e.g. query "hi hello" also separately searches "hi" and "hello")."""
    escaped = [re.escape(w) for w in words if w]
    if not escaped:
        return re.compile('$^')  # matches nothing
    pattern = '|'.join(r'\b' + w + r'\b' for w in escaped)
    return re.compile(pattern, re.IGNORECASE)


def _base_filter(file_type, season, episode):
    filt = {}
    if file_type:
        filt['file_type'] = file_type
    if season is not None:
        filt['season_number'] = season
    if episode is not None:
        filt['ep_number'] = episode
    return filt


async def get_search_results(query, file_type=None, max_results=None, offset=0, season=None, episode=None):
    """
    Two-pass search, combined query given priority over individual words:

    1. Combined pass — every word in the query must appear (any order).
       e.g. "hi hello" -> matches names containing both "hi" and "hello".
    2. Word pass — query split on spaces, each word searched on its own and
       OR'd together, for files the combined pass missed. e.g. a file named
       just "hello.mkv" still turns up for "hi hello", just ranked after
       anything that matched both words.

    Results are merged with pass 1 first, pass 2 appended after, deduped,
    capped at max_results. `total` reflects the count across both passes.
    """
    max_results = max_results or info.MAX_RESULTS
    base = _base_filter(file_type, season, episode)
    words = [w for w in normalize(query).split(' ') if w]
    coll = Media.collection

    # ---- Pass 1: combined (priority) ----
    if info.USE_MONGO_TEXT_SEARCH and query.strip():
        combined_filt = {**base, '$text': {'$search': query}}
        cursor1 = coll.find(combined_filt, {'score': {'$meta': 'textScore'}}).sort(
            [('score', {'$meta': 'textScore'})])
    else:
        regex_and = _build_regex_and(query)
        combined_filt = {**base, 'normalized_name': regex_and}
        if info.USE_CAPTION_FILTER:
            combined_filt = {**base, '$or': [{'normalized_name': regex_and}, {'caption': regex_and}]}
        cursor1 = coll.find(combined_filt).sort('$natural', -1)

    total1 = await coll.count_documents(combined_filt)
    raw1 = await cursor1.skip(offset).limit(max_results).to_list(length=max_results)
    seen_ids = {d['_id'] for d in raw1}

    # ---- Pass 2: individual words (fallback, lower priority) ----
    raw2, total2 = [], 0
    remaining = max_results - len(raw1)
    if len(words) > 1 and remaining > 0:
        regex_any = _build_regex_any(words)
        word_filt = {**base, '_id': {'$nin': list(seen_ids)}, 'normalized_name': regex_any}
        total2 = await coll.count_documents(word_filt)
        cursor2 = coll.find(word_filt).sort('$natural', -1).limit(remaining)
        raw2 = await cursor2.to_list(length=remaining)

    total = total1 + total2
    next_offset = offset + max_results
    if next_offset >= total:
        next_offset = ''

    files = [_normalize_doc(d) for d in (raw1 + raw2)]
    return files, next_offset, total


def _normalize_doc(doc: dict) -> dict:
    """Raw motor docs use '_id' (the umongo storage attribute); every
    caller elsewhere in the bot expects 'file_id'. Normalize once here."""
    doc = dict(doc)
    doc['file_id'] = doc.pop('_id', doc.get('file_id'))
    return doc


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
