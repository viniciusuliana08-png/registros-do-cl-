import os
import asyncio
import difflib
import aiohttp
import discord
from discord.ext import commands
from aiohttp import web
import pymongo

# --- Configurações de Variáveis de Ambiente ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WARGAMING_APP_ID = os.getenv("WARGAMING_APP_ID")
MONGO_URI = os.getenv("MONGO_URI")

# --- Conexão com o Banco de Dados MongoDB ---
try:
    mongo_client = pymongo.MongoClient(MONGO_URI)
    db = mongo_client["wotb_bot_db"]
    servers_col = db["servers"]
    mappings_col = db["mappings"]
    print("✅ Conexão inicial com o MongoDB configurada com sucesso!")
except Exception as e:
    print(f"❌ Erro ao conectar ao MongoDB: {e}")

# --- Funções de Banco de Dados (MongoDB) ---

def get_server_config(guild_id):
    doc = servers_col.find_one({"guild_id": str(guild_id)})
    if doc:
        return {"clan_tag": doc.get("clan_tag"), "clan_id": doc.get("clan_id")}
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

# --- Configuração do Bot Discord ---

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Servidor HTTP Mínimo (Evita erro de porta no Render) ---

async def handle_ping(request):
    return web.Response(text="Bot WOTB online e rodando no Render!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Servidor HTTP rodando na porta {port} (Render OK)")

# --- Funções da API Wargaming ---

async def fetch_clan_id_by_tag(tag):
    clean_tag = tag.strip().replace("[", "").replace("]", "").replace("–", "-").replace("—", "-")

    url = "https://api.wotblitz.com/wotb/clans/list/"
    params = {
        "application_id": WARGAMING_APP_ID,
        "search": clean_tag
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("status") == "ok" and data.get("data"):
                    # Busca por correspondência exata de TAG
                    for clan in data["data"]:
                        clan_tag_api = clan.get("tag", "").strip()
                        if clan_tag_api.lower() == clean_tag.lower():
                            return clan.get("clan_id"), clan_tag_api
                    
                    # Caso não ache o exato, pega a primeira opção de resultado
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

    url = "https://api.wotblitz.com/wotb/clans/info/"
    params = {
        "application_id": WARGAMING_APP_ID,
        "clan_id": clan_id,
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

# --- Eventos do Bot ---

@bot.event
async def on_ready():
    await start_web_server()
    total_vinculos = mappings_col.count_documents({})
    print(f"✅ Bot online com sucesso como: {bot.user}")
    print(f"📊 Banco de Dados carregado: {total_vinculos} vínculos salvos na nuvem.")

# --- Comandos do Bot ---

@bot.command(name="setcla")
@commands.has_permissions(administrator=True)
async def setcla(ctx, *, tag: str = None):
    """Configura a TAG do Clã do servidor."""
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    if not tag:
        await ctx.send("⚙️ **Qual a TAG do clã que deseja configurar para este servidor?** (ex: `MR-S`)")
        try:
            msg = await bot.wait_for('message', check=check, timeout=30.0)
            tag = msg.content.strip()
        except asyncio.TimeoutError:
            await ctx.send("⏰ Tempo esgotado! Configuração cancelada.")
            return

    loading = await ctx.send(f"🔎 Validando clã `[{tag}]` na Wargaming...")
    clan_id, real_tag = await fetch_clan_id_by_tag(tag)
    
    try:
        await loading.delete()
    except Exception:
        pass

    if not clan_id:
        await ctx.send(f"❌ Não foi possível encontrar o clã com a TAG `[{tag}]` na Wargaming. Verifique a grafia.")
        return

    set_server_config(ctx.guild.id, real_tag, clan_id)
    await ctx.send(f"✅ Clã **[{real_tag}]** (ID: `{clan_id}`) configurado com sucesso e salvo na nuvem!")

@bot.command(name="vincular")
async def vincular(ctx, *, nick_direto: str = None):
    """Vincular jogador do WOTB ao Discord."""
    config = get_server_config(ctx.guild.id)
    if not config or not config.get('clan_tag'):
        await ctx.send("⚠️ Nenhum clã foi configurado neste servidor. Um Admin precisa usar `!setcla TAG` primeiro.")
        return

    def check_author(m):
        return m.author == ctx.author and m.channel == ctx.channel

    if not nick_direto:
        await ctx.send("🎮 **Qual é o Nick no jogo do jogador que deseja vincular?**")
        try:
            msg_game = await bot.wait_for('message', check=check_author, timeout=30.0)
            search_nick = msg_game.content.strip().lower()
        except asyncio.TimeoutError:
            await ctx.send("⏰ **Tempo esgotado!** Processo cancelado.")
            return
    else:
        search_nick = nick_direto.strip().lower()

    loading_msg = await ctx.send("🔎 Buscando jogador no clã...")
    tag, clan_members = await fetch_clan_members(config['clan_tag'])

    try:
        await loading_msg.delete()
    except Exception:
        pass

    if not clan_members:
        await ctx.send("❌ Não foi possível obter os membros do clã na Wargaming.")
        return

    matches = [m for m in clan_members if search_nick in m['account_name'].lower()]

    if not matches:
        await ctx.send(f"❌ O jogador **{search_nick}** não foi encontrado na lista de membros do clã **[{tag}]**.")
        return

    selected_player = None
    if len(matches) == 1:
        selected_player = matches[0]
    else:
        options_text = "\n".join([f"**{i+1}.** {m['account_name']}" for i, m in enumerate(matches[:5])])
        await ctx.send(
            f"🔎 Encontrei mais de um jogador compatível no clã **[{tag}]**:\n{options_text}\n\n"
            f"👉 Digite apenas o **número** da opção desejada (1 a {min(len(matches), 5)}):"
        )
        try:
            msg_choice = await bot.wait_for('message', check=check_author, timeout=30.0)
            if msg_choice.content.strip().isdigit():
                idx = int(msg_choice.content.strip()) - 1
                if 0 <= idx < len(matches[:5]):
                    selected_player = matches[idx]
        except asyncio.TimeoutError:
            await ctx.send("⏰ Tempo esgotado! Seleção cancelada.")
            return

    if not selected_player:
        await ctx.send("❌ Opção inválida. Processo de vinculação cancelado.")
        return

    acc_id = selected_player['account_id']
    real_game_nick = selected_player['account_name']

    target_member = ctx.author
    if ctx.author.guild_permissions.administrator:
        clean_game_nick = real_game_nick.lower()
        members_map = {m.name.lower(): m for m in ctx.guild.members}
        members_map.update({m.display_name.lower(): m for m in ctx.guild.members})

        found_match = None
        if clean_game_nick in members_map:
            found_match = members_map[clean_game_nick]
        else:
            close = difflib.get_close_matches(clean_game_nick, list(members_map.keys()), n=1, cutoff=0.4)
            if close:
                found_match = members_map[close[0]]

        if found_match and found_match != ctx.author:
            await ctx.send(
                f"🔎 Conta do jogo: **{real_game_nick}**.\n"
                f"Deseja vincular ao membro {found_match.mention}? *(Responda 's' para Sim ou 'n' para Não)*"
            )
            try:
                msg_confirm = await bot.wait_for('message', check=check_author, timeout=30.0)
                if msg_confirm.content.strip().lower() in ['s', 'sim', 'yes', 'y']:
                    target_member = found_match
                else:
                    await ctx.send("💬 Mencione (`@membro`) ou envie o ID do membro do Discord:")
                    msg_target = await bot.wait_for('message', check=check_author, timeout=30.0)
                    if msg_target.mentions:
                        target_member = msg_target.mentions[0]
                    elif msg_target.content.strip().isdigit():
                        fetched = ctx.guild.get_member(int(msg_target.content.strip()))
                        if fetched:
                            target_member = fetched
            except asyncio.TimeoutError:
                await ctx.send("⏰ Tempo esgotado! Processo cancelado.")
                return

    set_mapping(acc_id, real_game_nick, target_member.id, str(target_member))
    await ctx.send(f"🎉 **Vinculação registrada na nuvem!**\n🎮 **Jogo:** `{real_game_nick}` ➔ 💬 **Discord:** {target_member.mention}")

@bot.command(name="vinculados")
async def vinculados(ctx):
    """Lista todos os vínculos salvos no MongoDB."""
    mappings = get_all_mappings()
    if not mappings:
        await ctx.send("ℹ️ Nenhum jogador está vinculado até o momento.")
        return

    msg = "**📋 Lista de Jogadores Vinculados:**\n"
    for data in mappings:
        msg += f"• `{data['account_name']}` ➔ <@{data['discord_user_id']}>\n"

    if len(msg) > 2000:
        chunks = [msg[i:i+1900] for i in range(0, len(msg), 1900)]
        for chunk in chunks:
            await ctx.send(chunk)
    else:
        await ctx.send(msg)

@bot.command(name="desvincular")
@commands.has_permissions(administrator=True)
async def desvincular(ctx, target: str = None):
    """Remove o vínculo de um jogador do banco de dados."""
    if not target:
        await ctx.send("❓ Mencione o usuário (`@membro`) ou digite o `Nick no Jogo` que deseja remover.")
        return

    removed = False
    if ctx.message.mentions:
        target_discord_id = ctx.message.mentions[0].id
        removed = remove_mapping_by_discord(target_discord_id)
    else:
        removed = remove_mapping_by_nick(target)

    if removed:
        await ctx.send("✅ Vínculo removido do banco de dados na nuvem!")
    else:
        await ctx.send("❌ Vínculo não encontrado no banco de dados.")

@bot.command(name="ajuda")
async def ajuda(ctx):
    """Exibe os comandos do bot."""
    embed = discord.Embed(
        title="🤖 Comandos de Gestão do Clã",
        color=discord.Color.blue()
    )
    embed.add_field(name="`!vincular [NICK]`", value="Vincula sua conta do jogo ao perfil do Discord.", inline=False)
    embed.add_field(name="`!vinculados`", value="Lista todos os membros salvos.", inline=False)
    embed.add_field(name="`!setcla [TAG]`", value="(Admin) Configura o clã ativo do servidor.", inline=False)
    embed.add_field(name="`!desvincular [@membro/Nick]`", value="(Admin) Remove um vínculo.", inline=False)
    await ctx.send(embed=embed)

# --- Execução do Bot ---
bot.run(DISCORD_TOKEN)
