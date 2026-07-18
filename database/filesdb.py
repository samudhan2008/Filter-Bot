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

from pyrogram.file_id import FileId
from pymongo.errors import DuplicateKeyError, BulkWriteError
from pymongo import InsertOne
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow.exceptions import ValidationError

import info

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

    class Meta:
        indexes = ('$normalized_name',)
        collection_name = info.COLLECTION_NAME


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
    return {
        '_id': file_id,
        'file_ref': file_ref,
        'file_name': name,
        'normalized_name': normalize(name),
        'file_size': media.file_size,
        'file_type': media.file_type,
        'mime_type': media.mime_type,
        'caption': media.caption.html if media.caption else None,
    }


async def save_file(media):
    try:
        doc = _doc_from_media(media)
        file = Media(
            file_id=doc['_id'], file_ref=doc['file_ref'], file_name=doc['file_name'],
            normalized_name=doc['normalized_name'], file_size=doc['file_size'],
            file_type=doc['file_type'], mime_type=doc['mime_type'], caption=doc['caption'],
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


def _build_regex(query: str):
    """Build a regex over normalized (separator-free) text, so punctuation
    in either the query or the stored name never breaks a match."""
    q = normalize(query)
    if not q:
        return re.compile('.', re.IGNORECASE)
    parts = [re.escape(p) for p in q.split(' ') if p]
    # Require all query words to appear, in order, separated by anything.
    pattern = r'.*'.join(parts)
    return re.compile(pattern, re.IGNORECASE)


async def get_search_results(query, file_type=None, max_results=None, offset=0):
    max_results = max_results or info.MAX_RESULTS
    regex = _build_regex(query)
    filt = {'normalized_name': regex}
    if info.USE_CAPTION_FILTER:
        filt = {'$or': [{'normalized_name': regex}, {'caption': regex}]}
    if file_type:
        filt['file_type'] = file_type

    total = await Media.count_documents(filt)
    next_offset = offset + max_results
    if next_offset >= total:
        next_offset = ''

    cursor = Media.find(filt).sort('$natural', -1).skip(offset).limit(max_results)
    files = await cursor.to_list(length=max_results)
    return files, next_offset, total


async def get_file_by_id(file_id):
    cursor = Media.find({'file_id': file_id})
    res = await cursor.to_list(length=1)
    return res[0] if res else None
