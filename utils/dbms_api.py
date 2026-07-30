"""
Backs the /dbms panel — a generic view/add/edit/delete interface over every
collection in the bot's own MongoDB database (info.DATABASE_NAME).

Deliberately scoped to just this one database, not the whole Atlas
cluster: a cluster commonly hosts multiple unrelated apps' databases side
by side (as seen in this project's own cluster — other databases entirely
unrelated to this bot), and the bot's own DB credentials typically can't
even see those anyway. Reaching across into other apps' data would need
separate, broader credentials and is a meaningfully different (and much
riskier) thing to expose through this bot's admin panel than "manage this
bot's own data" is.

Uses MongoDB Extended JSON (bson.json_util) to round-trip values the
frontend needs to see and send back correctly — ObjectId, datetime, etc.
— as plain `{"$oid": "..."}` / `{"$date": "..."}` style wrappers instead of
python objects.

Same ADMIN_API_SECRET gate as the rest of the admin API. Worth being
direct about what this is: full read/write/delete access to every
document in the bot's database, through a web form. It's exactly as
powerful as connecting a Mongo client directly — there's no schema
validation or guardrail beyond what you type into the JSON editor.
"""

import json
import logging

from aiohttp import web
from bson import json_util

from database.mongo import db
from database import auditdb

logger = logging.getLogger(__name__)


def _check_secret(request: web.Request) -> bool:
    import info
    if not info.ADMIN_API_SECRET:
        return False
    return request.headers.get('X-API-Secret') == info.ADMIN_API_SECRET


def _guarded(handler):
    async def wrapper(request):
        if not _check_secret(request):
            return web.json_response({'ok': False, 'error': 'unauthorized'}, status=401)
        try:
            return await handler(request)
        except Exception as e:
            logger.exception("DBMS API handler failed")
            return web.json_response({'ok': False, 'error': str(e)}, status=500)
    return wrapper


def _to_extended_json(obj):
    """Python/bson objects (ObjectId, datetime, ...) -> plain JSON-safe
    structure using Mongo Extended JSON wrappers, ready for web.json_response."""
    return json.loads(json_util.dumps(obj))


def _from_extended_json(obj):
    """The reverse — plain JSON structure (possibly containing
    {"$oid": ...}/{"$date": ...} wrappers) -> real bson/python objects."""
    return json_util.loads(json.dumps(obj))


@_guarded
async def api_list_collections(request: web.Request):
    import info
    names = sorted(await db.list_collection_names())
    collections = []
    for name in names:
        try:
            count = await db[name].count_documents({})
        except Exception:
            count = None
        collections.append({'name': name, 'count': count})
    return web.json_response({'ok': True, 'database': info.DATABASE_NAME, 'collections': collections})


@_guarded
async def api_list_documents(request: web.Request):
    name = request.match_info['name']
    if name not in await db.list_collection_names():
        return web.json_response({'ok': False, 'error': 'No such collection'}, status=404)

    try:
        page = max(1, int(request.query.get('page', '1')))
    except ValueError:
        page = 1
    try:
        page_size = min(100, max(1, int(request.query.get('page_size', '20'))))
    except ValueError:
        page_size = 20

    filt = {}
    q = request.query.get('q', '').strip()
    if q:
        try:
            filt = _from_extended_json(json.loads(q))
            if not isinstance(filt, dict):
                raise ValueError
        except Exception:
            return web.json_response({'ok': False, 'error': 'q must be a valid JSON object (a Mongo filter)'}, status=400)

    coll = db[name]
    total = await coll.count_documents(filt)
    cursor = coll.find(filt).skip((page - 1) * page_size).limit(page_size)
    docs = await cursor.to_list(length=page_size)

    return web.json_response({
        'ok': True,
        'collection': name,
        'total': total,
        'page': page,
        'page_size': page_size,
        'documents': _to_extended_json(docs),
    })


@_guarded
async def api_insert_document(request: web.Request):
    name = request.match_info['name']
    body = await request.json()
    doc = _from_extended_json(body.get('document', {}))
    if not isinstance(doc, dict):
        return web.json_response({'ok': False, 'error': 'document must be a JSON object'}, status=400)

    result = await db[name].insert_one(doc)
    await auditdb.log_action('dbms_insert', {'collection': name, 'id': str(result.inserted_id)})
    return web.json_response({'ok': True, 'inserted_id': _to_extended_json(result.inserted_id)})


@_guarded
async def api_update_document(request: web.Request):
    name = request.match_info['name']
    body = await request.json()
    raw_id = body.get('id')
    new_doc = _from_extended_json(body.get('document', {}))
    if not isinstance(new_doc, dict):
        return web.json_response({'ok': False, 'error': 'document must be a JSON object'}, status=400)

    doc_id = _from_extended_json(raw_id) if isinstance(raw_id, dict) else raw_id
    new_doc.pop('_id', None)  # _id is immutable — never touch it via this path

    result = await db[name].update_one({'_id': doc_id}, {'$set': new_doc})
    await auditdb.log_action('dbms_update', {'collection': name, 'id': str(doc_id), 'matched': result.matched_count})
    return web.json_response({'ok': True, 'matched': result.matched_count, 'modified': result.modified_count})


@_guarded
async def api_delete_document(request: web.Request):
    name = request.match_info['name']
    body = await request.json()
    raw_id = body.get('id')
    doc_id = _from_extended_json(raw_id) if isinstance(raw_id, dict) else raw_id

    result = await db[name].delete_one({'_id': doc_id})
    await auditdb.log_action('dbms_delete', {'collection': name, 'id': str(doc_id), 'deleted': result.deleted_count})
    return web.json_response({'ok': True, 'deleted': result.deleted_count})


def register_dbms_routes(app: web.Application, bot):
    app.router.add_get('/api/admin/dbms/collections', api_list_collections)
    app.router.add_get('/api/admin/dbms/collection/{name}', api_list_documents)
    app.router.add_post('/api/admin/dbms/collection/{name}', api_insert_document)
    app.router.add_put('/api/admin/dbms/collection/{name}', api_update_document)
    app.router.add_delete('/api/admin/dbms/collection/{name}', api_delete_document)
