import os
import asyncio
import difflib
import traceback
import aiohttp
import discord
from discord.ext import commands
from aiohttp import web
import pymongo
import certifi

# --- Configurações de Variáveis de Ambiente ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WARGAMING_APP_ID = os.getenv("WARGAMING_APP_ID", "demo")
MONGO_URI = os.getenv("MONGO_URI")

# --- Conexão com o Banco de Dados MongoDB ---
try:
    mongo_client = pymongo.MongoClient(MONGO_URI, tlsCAFile=certifi.where())
    db = mongo_client["wotb_bot_db"]
    servers_col = db["servers"]
    mappings_col = db["mappings"]
    print("✅ Conexão com o MongoDB estabelecida!")
except Exception as e:
    print(f"❌ Erro ao conectar ao MongoDB: {e}")

# --- Funções do Banco de Dados ---

def get_server_config(guild_id):
    try:
        doc = servers_col.find_one({"guild_id": str(guild_id)})
        if doc:
            return {"clan_tag": doc.get("clan_tag"), "clan_id": doc.get("clan_id")}
    except Exception as e:
        print(f"Erro ao ler banco: {e}")
    return {"clan_tag": None, "clan_id": None}

def set_server_config(guild_id, clan_tag, clan_id):
    servers_col.update_one(
        {"guild_id": str(guild_id)},
        {"$set": {"clan_tag": clan_tag, "clan_id": clan_id}},
        upsert=True
    )

def set_mapping(account_id, account_name, discord_user_id, discord_user_name):
    mappings_col.update_one(
        {"account_id": str(account_id)},
        {"$set": {
            "account_id": str(account_id),
            "account_name": account_name,
            "discord_user_id": str(discord_user_id),
            "discord_user_name": str(discord_user_name)
        }},
        upsert=True
    )

def remove_mapping_by_discord(discord_user_id):
    result = mappings_col.delete_one({"discord_user_id": str(discord_user_id)})
    return result.deleted_count > 0

def remove_mapping_by_nick(account_name):
    result = mappings_col.delete_one({"account_name": {"$regex": f"^{account_name}$", "$options": "i"}})
    return result.deleted_count > 0

def get_all_mappings():
    return list(mappings_col.find({}, {"_id": 0}))

# --- Configuração do Bot ---

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Servidor HTTP (Health Check do Render) ---

async def handle_ping(request):
    return web.Response(text="Bot On!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Servidor Web rodando na porta {port}")

# --- Integração API Wargaming ---

async def fetch_clan_id_by_tag(tag):
    clean_tag = str(tag).strip().replace("[", "").replace("]", "").replace("–", "-").replace("—", "-")
    app_id = WARGAMING_APP_ID or "demo"
    url = "https://api.wotblitz.com/wotb/clans/list/"
    
    params = {
        "application_id": str(app_id),
        "search": clean_tag
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("status") == "ok" and data.get("data"):
                    # Tenta achar correspondência exata de tag
                    for clan in data["data"]:
                        if clan.get("tag", "").lower().replace("-", "") == clean_tag.lower().replace("-", ""):
                            return clan.get("clan_id"), clan.get("tag")
                    # Se não achar exato, pega a primeira opção retornada
                    first_clan = data["data"][0]
                    return first_clan.get("clan_id"), first_clan.get("tag")
            return None, None

async def fetch_clan_members(tag_or_id):
    clan_id = None
    clan_tag = None

    if str(tag_or_id).isdigit():
        clan_id = int(tag_or_id)
    else:
        clan_id, clan_tag = await fetch_clan_id_by_tag(str(tag_or_id))

    if not clan_id:
        return None, []

    app_id = WARGAMING_APP_ID or "demo"
    url = "https://api.wotblitz.com/wotb/clans/info/"
    params = {
        "application_id": str(app_id),
        "clan_id": str(clan_id),
        "extra": "members"
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("status") == "ok" and data.get("data") and str(clan_id) in data["data"]:
                    clan_info = data["data"][str(clan_id)]
                    tag = clan_info.get("tag", clan_tag)
                    members_dict = clan_info.get("members", {})
                    
                    members_list = []
                    for m_id, m_data in members_dict.items():
                        members_list.append({
                            "account_id": m_data.get("account_id"),
                            "account_name": m_data.get("account_name"),
                            "role": m_data.get("role")
                        })
                    return tag, members_list
            return None, []

# --- Eventos e Tratamento de Erros ---

@bot.event
async def on_ready():
    await start_web_server()
    print(f"✅ Bot conectado como: {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    # Garante que os comandos ! sejam processados corretamente
    await bot.process_commands(message)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Você não tem permissão para usar este comando.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        print(f"⚠️ Erro ao executar comando: {error}")
        traceback.print_exc()

# --- Comandos do Bot ---

@bot.command(name="setcla")
@commands.has_permissions(administrator=True)
async def setcla(ctx, *, tag: str = None):
    """Configura o clã para o servidor."""
    if not tag:
        await ctx.send("⚙️ Digite a TAG do clã. Exemplo: `!setcla MR-S`")
        return

    loading = await ctx.send(f"🔎 Buscando clã `[{tag}]` na Wargaming...")
    clan_id, real_tag = await fetch_clan_id_by_tag(tag)

    try:
        await loading.delete()
    except Exception:
        pass

    if not clan_id:
        await ctx.send(f"❌ Não foi possível encontrar o clã com a TAG `[{tag}]` na Wargaming.")
        return

    set_server_config(ctx.guild.id, real_tag, clan_id)
    await ctx.send(f"✅ Clã **[{real_tag}]** (ID: `{clan_id}`) configurado e salvo no banco de dados!")

@bot.command(name="vincular")
async def vincular(ctx, *, nick_direto: str = None):
    """Vincula o nick do jogador no WOTB com a conta do Discord."""
    config = get_server_config(ctx.guild.id)
    if not config or not config.get('clan_tag'):
        await ctx.send("⚠️ Nenhum clã configurado neste servidor. Use `!setcla TAG` primeiro.")
        return

    def check_author(m):
        return m.author == ctx.author and m.channel == ctx.channel

    if not nick_direto:
        await ctx.send("🎮 Qual seu nick no jogo?")
        try:
            msg = await bot.wait_for('message', check=check_author, timeout=30.0)
            search_nick = msg.content.strip().lower()
        except asyncio.TimeoutError:
            await ctx.send("⏰ Tempo esgotado!")
            return
    else:
        search_nick = nick_direto.strip().lower()

    loading = await ctx.send("🔎 Procurando no clã...")
    tag, members = await fetch_clan_members(config['clan_tag'])

    try:
        await loading.delete()
    except Exception:
        pass

    if not members:
        await ctx.send("❌ Não foi possível carregar a lista de membros do clã.")
        return

    matches = [m for m in members if search_nick in m['account_name'].lower()]

    if not matches:
        await ctx.send(f"❌ O nick `{search_nick}` não foi encontrado na lista do clã **[{tag}]**.")
        return

    selected_player = matches[0]
    acc_id = selected_player['account_id']
    real_nick = selected_player['account_name']

    target_member = ctx.author
    set_mapping(acc_id, real_nick, target_member.id, str(target_member))
    await ctx.send(f"🎉 **Vínculo criado!** 🎮 `{real_nick}` ➔ 💬 {target_member.mention}")

@bot.command(name="vinculados")
async def vinculados(ctx):
    """Lista todos os vínculos salvos no MongoDB."""
    mappings = get_all_mappings()
    if not mappings:
        await ctx.send("ℹ️ Nenhum vínculo cadastrado.")
        return

    msg = "**📋 Jogadores Vinculados:**\n"
    for item in mappings:
        msg += f"• `{item['account_name']}` ➔ <@{item['discord_user_id']}>\n"

    await ctx.send(msg)

@bot.command(name="desvincular")
@commands.has_permissions(administrator=True)
async def desvincular(ctx, target: str = None):
    """Remove um vínculo."""
    if not target:
        await ctx.send("❓ Uso: `!desvincular @membro` ou `!desvincular NickDoJogo`")
        return

    removed = False
    if ctx.message.mentions:
        removed = remove_mapping_by_discord(ctx.message.mentions[0].id)
    else:
        removed = remove_mapping_by_nick(target)

    if removed:
        await ctx.send("✅ Vínculo removido do banco de dados!")
    else:
        await ctx.send("❌ Vínculo não encontrado.")

bot.run(DISCORD_TOKEN)
