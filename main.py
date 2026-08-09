import os
import asyncio
import traceback
import aiohttp
import discord
from discord.ext import commands
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
import certifi

# --- Configurações ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
APPLICATION_ID = os.getenv("APPLICATION_ID") or os.getenv("WARGAMING_APP_ID", "demo")
MONGO_URI = os.getenv("MONGO_URI")

# --- Conexão MongoDB Assíncrona com Motor ---
mongo_client = AsyncIOMotorClient(MONGO_URI, tlsCAFile=certifi.where())
db = mongo_client["wotb_bot_db"]
servers_col = db["servers"]
mappings_col = db["mappings"]

# --- Funções do Banco de Dados (Agora com async/await) ---

async def get_server_config(guild_id):
    try:
        doc = await servers_col.find_one({"guild_id": str(guild_id)})
        if doc:
            return {"clan_tag": doc.get("clan_tag"), "clan_id": doc.get("clan_id")}
    except Exception as e:
        print(f"Erro ao ler banco: {e}")
    return {"clan_tag": None, "clan_id": None}

async def set_server_config(guild_id, clan_tag, clan_id):
    await servers_col.update_one(
        {"guild_id": str(guild_id)},
        {"$set": {"clan_tag": clan_tag, "clan_id": clan_id}},
        upsert=True
    )

async def set_mapping(account_id, account_name, discord_user_id, discord_user_name):
    await mappings_col.update_one(
        {"account_id": str(account_id)},
        {"$set": {
            "account_id": str(account_id),
            "account_name": account_name,
            "discord_user_id": str(discord_user_id),
            "discord_user_name": str(discord_user_name)
        }},
        upsert=True
    )

async def remove_mapping_by_discord(discord_user_id):
    result = await mappings_col.delete_one({"discord_user_id": str(discord_user_id)})
    return result.deleted_count > 0

async def remove_mapping_by_nick(account_name):
    result = await mappings_col.delete_one({"account_name": {"$regex": f"^{account_name}$", "$options": "i"}})
    return result.deleted_count > 0

async def get_all_mappings():
    cursor = mappings_col.find({}, {"_id": 0})
    return await cursor.to_list(length=100)
