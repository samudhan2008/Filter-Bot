import logging
from motor.motor_asyncio import AsyncIOMotorClient
import info

logger = logging.getLogger(__name__)

client = AsyncIOMotorClient(info.DATABASE_URI)
db = client[info.DATABASE_NAME]

users_col = db['users']
groups_col = db['groups']
banned_col = db['banned']


# ---- Users (for broadcast) ----
async def add_user(user_id: int):
    await users_col.update_one({'_id': user_id}, {'$setOnInsert': {'_id': user_id}}, upsert=True)


async def is_user_known(user_id: int) -> bool:
    return await users_col.find_one({'_id': user_id}) is not None


async def all_user_ids():
    return [doc['_id'] async for doc in users_col.find({}, {'_id': 1})]


async def total_users_count():
    return await users_col.count_documents({})


# ---- Groups (for broadcast + admin-authorization flow) ----
async def add_group(chat_id: int, title: str = ""):
    await groups_col.update_one(
        {'_id': chat_id}, {'$set': {'title': title}, '$setOnInsert': {'_id': chat_id, 'authorized': False}},
        upsert=True,
    )


async def all_group_ids():
    return [doc['_id'] async for doc in groups_col.find({}, {'_id': 1})]


async def total_groups_count():
    return await groups_col.count_documents({})


async def set_group_authorized(chat_id: int, authorized: bool):
    await groups_col.update_one({'_id': chat_id}, {'$set': {'authorized': authorized}}, upsert=True)


async def is_group_authorized(chat_id: int) -> bool:
    doc = await groups_col.find_one({'_id': chat_id})
    return bool(doc and doc.get('authorized'))


# ---- Banned users ----
async def ban_user(user_id: int, reason: str = ""):
    await banned_col.update_one({'_id': user_id}, {'$set': {'reason': reason}}, upsert=True)


async def unban_user(user_id: int):
    await banned_col.delete_one({'_id': user_id})


async def is_banned(user_id: int) -> bool:
    return await banned_col.find_one({'_id': user_id}) is not None
