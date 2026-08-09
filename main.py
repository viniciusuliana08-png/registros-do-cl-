import os
import json
import asyncio
import difflib
import aiohttp
import discord
from discord.ext import commands

# Token do Bot Discord (substitua ou use variável de ambiente)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "SEU_TOKEN_AQUI")

# API Key da Wargaming
WARGAMING_APP_ID = os.getenv("WARGAMING_APP_ID", "SEU_APP_ID_AQUI")

# Arquivo de dados para persistência do mapeamento e configurações por servidor
DATA_FILE = "data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Erro ao carregar dados: {e}")
    return {"servers": {}, "mappings": {}}

def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Erro ao salvar dados: {e}")

db = load_data()

def get_server_config(guild_id):
    guild_id_str = str(guild_id)
    return db["servers"].get(guild_id_str, {"clan_tag": None, "clan_id": None})

def set_server_config(guild_id, clan_tag, clan_id):
    guild_id_str = str(guild_id)
    if "servers" not in db:
        db["servers"] = {}
    db["servers"][guild_id_str] = {
        "clan_tag": clan_tag,
        "clan_id": clan_id
    }
    save_data(db)

def get_mapping(account_id):
    return db["mappings"].get(str(account_id))

def set_mapping(account_id, account_name, discord_user_id, discord_user_name):
    if "mappings" not in db:
        db["mappings"] = {}
    db["mappings"][str(account_id)] = {
        "account_id": account_id,
        "account_name": account_name,
        "discord_user_id": discord_user_id,
        "discord_user_name": discord_user_name
    }
    save_data(db)

def remove_mapping(account_id):
    if str(account_id) in db["mappings"]:
        del db["mappings"][str(account_id)]
        save_data(db)
        return True
    return False

# Configuração do Bot com as Intents necessárias
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Funções da API Wargaming
async def fetch_clan_id_by_tag(tag):
    url = "https://api.wotblitz.com/wotb/clans/list/"
    params = {
        "application_id": WARGAMING_APP_ID,
        "search": tag
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("status") == "ok" and data.get("data"):
                    for clan in data["data"]:
                        if clan.get("tag", "").lower() == tag.lower():
                            return clan.get("clan_id"), clan.get("tag")
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

@bot.event
async def on_ready():
    print(f"✅ Bot online com sucesso como: {bot.user}")
    print(f"📁 Banco de dados carregado com {len(db.get('mappings', {}))} vínculos.")

# COMANDO: !setcla [TAG]
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

    loading = await ctx.send(f"🔎 Validando clã `[{tag}]`...")
    clan_id, real_tag = await fetch_clan_id_by_tag(tag)
    
    try:
        await loading.delete()
    except Exception:
        pass

    if not clan_id:
        await ctx.send(f"❌ Não foi possível encontrar o clã com a TAG `[{tag}]` na Wargaming.")
        return

    set_server_config(ctx.guild.id, real_tag, clan_id)
    await ctx.send(f"✅ Clã **[{real_tag}]** (ID: `{clan_id}`) configurado com sucesso para este servidor!")

# COMANDO: !vincular [NICK]
@bot.command(name="vincular")
async def vincular(ctx, *, nick_direto: str = None):
    """Vincular jogador ao Discord."""
    config = get_server_config(ctx.guild.id)
    if not config or not config.get('clan_tag'):
        await ctx.send("⚠️ Nenhum clã foi configurado neste servidor. Peça para um Admin usar `!setcla TAG` primeiro.")
        return

    def check_author(m):
        return m.author == ctx.author and m.channel == ctx.channel

    if not nick_direto:
        await ctx.send("🎮 **Qual é o Nick no WOTB do jogador do clã que deseja vincular?**")
        try:
            msg_game = await bot.wait_for('message', check=check_author, timeout=30.0)
            search_nick = msg_game.content.strip().lower()
        except asyncio.TimeoutError:
            await ctx.send("⏰ **Tempo esgotado!** Processo de vinculação cancelado.")
            return
    else:
        search_nick = nick_direto.strip().lower()

    loading_msg = await ctx.send("🔎 Buscando jogador na lista do clã...")
    tag, clan_members = await fetch_clan_members(config['clan_tag'])

    try:
        await loading_msg.delete()
    except Exception:
        pass

    if not clan_members:
        await ctx.send("❌ Não foi possível carregar os membros do clã no momento.")
        return

    matches = [m for m in clan_members if search_nick in m['account_name'].lower()]

    if not matches:
        await ctx.send(f"❌ O jogador **{search_nick}** não foi encontrado dentro do clã **[{tag}]**.")
        return

    selected_player = None
    if len(matches) == 1:
        selected_player = matches[0]
    else:
        options_text = "\n".join([f"**{i+1}.** {m['account_name']}" for i, m in enumerate(matches[:5])])
        await ctx.send(
            f"🔎 Encontrei mais de um jogador parecido no clã **[{tag}]**:\n{options_text}\n\n"
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
                f"🔎 Jogador do clã: **{real_game_nick}**.\n"
                f"Deseja vincular esta conta ao membro {found_match.mention}? *(Responda 's' para Sim ou 'n' para Não)*"
            )
            try:
                msg_confirm = await bot.wait_for('message', check=check_author, timeout=30.0)
                if msg_confirm.content.strip().lower() in ['s', 'sim', 'yes', 'y']:
                    target_member = found_match
                else:
                    await ctx.send("💬 Mencione (`@membro`) ou digite o ID do membro no Discord para vincular:")
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
    await ctx.send(f"🎉 **Vinculação realizada!**\n🎮 **Jogo:** `{real_game_nick}` ➔ 💬 **Discord:** {target_member.mention}")

# COMANDO: !vinculados
@bot.command(name="vinculados")
async def vinculados(ctx):
    """Lista todos os jogadores que possuem vínculo cadastrado."""
    mappings = db.get("mappings", {})
    if not mappings:
        await ctx.send("ℹ️ Nenhum jogador está vinculado ainda.")
        return

    msg = "**📋 Lista de Jogadores Vinculados:**\n"
    for acc_id, data in mappings.items():
        msg += f"• `{data['account_name']}` ➔ <@{data['discord_user_id']}>\n"

    if len(msg) > 2000:
        chunks = [msg[i:i+1900] for i in range(0, len(msg), 1900)]
        for chunk in chunks:
            await ctx.send(chunk)
    else:
        await ctx.send(msg)

# COMANDO: !desvincular [MENTION/ID/NICK]
@bot.command(name="desvincular")
@commands.has_permissions(administrator=True)
async def desvincular(ctx, target: str = None):
    """Remove a vinculação de um jogador."""
    if not target:
        await ctx.send("❓ Digite `@membro` ou o `Nick do Jogo` que deseja desvincular. Exemplo: `!desvincular @Usuario`")
        return

    mappings = db.get("mappings", {})
    target_id = None

    if ctx.message.mentions:
        target_discord_id = ctx.message.mentions[0].id
        for acc_id, data in list(mappings.items()):
            if data.get("discord_user_id") == target_discord_id:
                target_id = acc_id
                break
    else:
        for acc_id, data in list(mappings.items()):
            if data.get("account_name").lower() == target.lower():
                target_id = acc_id
                break

    if target_id and remove_mapping(target_id):
        await ctx.send("✅ Vinculação removida com sucesso!")
    else:
        await ctx.send("❌ Não foi possível encontrar essa vinculação no banco de dados.")

# COMANDO: !ajuda
@bot.command(name="ajuda")
async def ajuda(ctx):
    """Mostra o painel de ajuda com todos os comandos."""
    embed = discord.Embed(
        title="🤖 Painel de Comandos do Bot",
        color=discord.Color.blue()
    )
    embed.add_field(name="`!vincular [NICK]`", value="Vincule seu nick do jogo à sua conta do Discord.", inline=False)
    embed.add_field(name="`!vinculados`", value="Exibe a lista de membros vinculados.", inline=False)
    embed.add_field(name="`!setcla [TAG]`", value="(Admin) Configura a TAG do clã do servidor.", inline=False)
    embed.add_field(name="`!desvincular [@membro/Nick]`", value="(Admin) Remove a vinculação de um usuário.", inline=False)
    await ctx.send(embed=embed)

bot.run(DISCORD_TOKEN)
