import os
import asyncio
import difflib
import aiohttp
import discord
import motor.motor_asyncio
from discord.ext import commands, tasks
from flask import Flask
from threading import Thread
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# --- 1. CONFIGURAÇÕES E FUSO HORÁRIO ---
TIMEZONE_BR = ZoneInfo("America/Sao_Paulo")

def now_br():
    return datetime.now(TIMEZONE_BR)

APPLICATION_ID = os.environ.get("APPLICATION_ID")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")

# --- 2. SERVIDOR WEB (FLASK FOR KEEP-ALIVE) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot do Clã Online (MongoDB)!"

def run_flask():
    port = int(os.environ.get("PORT", 10002))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()

# --- 3. BANCO DE DADOS (MONGODB / MOTOR) ---
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = mongo_client["clan_manager_db"]

mappings_col = db["mappings"]
servers_col = db["servers"]

async def set_server_clan(guild_id: int, clan_tag: str):
    await servers_col.update_one(
        {"guild_id": guild_id},
        {"$set": {"clan_tag": clan_tag.upper()}},
        upsert=True
    )

async def set_absent_channel(guild_id: int, channel_id: int):
    await servers_col.update_one(
        {"guild_id": guild_id},
        {"$set": {"absent_channel_id": channel_id}},
        upsert=True
    )

async def remove_absent_channel(guild_id: int):
    await servers_col.update_one(
        {"guild_id": guild_id},
        {"$unset": {"absent_channel_id": ""}}
    )

async def set_server_panel(guild_id: int, channel_id: int, message_id: int):
    await servers_col.update_one(
        {"guild_id": guild_id},
        {"$set": {"channel_id": channel_id, "panel_message_id": message_id}},
        upsert=True
    )

async def set_online_panel(guild_id: int, channel_id: int, message_id: int):
    await servers_col.update_one(
        {"guild_id": guild_id},
        {"$set": {"channel_id": channel_id, "online_panel_message_id": message_id}},
        upsert=True
    )

async def update_last_members(guild_id: int, member_ids_str: str):
    await servers_col.update_one(
        {"guild_id": guild_id},
        {"$set": {"last_members": member_ids_str}},
        upsert=True
    )

async def get_server_config(guild_id: int):
    return await servers_col.find_one({"guild_id": guild_id})

async def get_all_configured_servers():
    cursor = servers_col.find({"clan_tag": {"$ne": None}})
    servers = await cursor.to_list(length=None)
    return [s["guild_id"] for s in servers]

async def set_mapping(account_id: int, account_name: str, discord_id: int, discord_name: str):
    await mappings_col.update_one(
        {"discord_id": discord_id},
        {"$set": {
            "account_id": account_id,
            "account_name": account_name,
            "discord_name": discord_name
        }},
        upsert=True
    )

async def update_account_nickname_by_id(account_id: int, new_account_name: str):
    await mappings_col.update_many(
        {"account_id": account_id},
        {"$set": {"account_name": new_account_name}}
    )

async def remove_mapping(discord_id: int):
    await mappings_col.delete_one({"discord_id": discord_id})

async def get_all_mappings():
    cursor = mappings_col.find({})
    docs = await cursor.to_list(length=None)
    
    mappings = {}
    for doc in docs:
        acc_id = doc["account_id"]
        if acc_id not in mappings:
            mappings[acc_id] = []
        mappings[acc_id].append({
            'acc_name': doc["account_name"],
            'discord_id': doc["discord_id"],
            'discord_name': doc.get("discord_name", "")
        })
    return mappings

# --- 4. CONFIGURAÇÃO DO BOT ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")  # Remove o comando de ajuda padrão para não causar conflito

def get_role_info(role: str):
    if not role:
        return 99, ""
    r = str(role).lower().strip()
    if r in ['leader', 'commander', 'clan_commander', 'leader_clan']:
        return 1, " 👑 `Líder`"
    elif r in ['executive_officer', 'vice_leader', 'co_leader', 'deputy_commander', 'sub_commander']:
        return 2, " ⚔️ `Vice-Líder`"
    elif r in ['commander_assistant', 'recruiter', 'diplomat', 'quartermaster', 'personnel_officer', 'combat_officer']:
        return 3, " 📜 `Oficial`"
    return 4, ""

async def fetch_clan_members(target_tag: str):
    regions = [
        "https://api.wotblitz.com",
        "https://api.wotblitz.eu",
        "https://api.wotblitz.asia"
    ]
    async with aiohttp.ClientSession() as session:
        clan_id = None
        base_url = None
        exact_tag = target_tag.upper()
        
        for reg in regions:
            url = f"{reg}/wotb/clans/list/?application_id={APPLICATION_ID}&search={exact_tag}"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        clan_list = data.get('data', [])
                        for clan in clan_list:
                            if clan.get('tag', '').upper() == exact_tag:
                                clan_id = clan['clan_id']
                                base_url = reg
                                break
                        if clan_id:
                            break
            except Exception:
                pass
                
        if not clan_id:
            return None, []
            
        clan_info_url = f"{base_url}/wotb/clans/info/?application_id={APPLICATION_ID}&clan_id={clan_id}&extra=members"
        try:
            async with session.get(clan_info_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    clan_data = data.get('data', {}).get(str(clan_id), {})
                    members = clan_data.get('members', {})
                    
                    if not members:
                        return clan_data.get('tag'), []
                        
                    account_ids = list(members.keys())
                    acc_ids_str = ",".join(account_ids)
                    
                    acc_info_url = f"{base_url}/wotb/account/info/?application_id={APPLICATION_ID}&account_id={acc_ids_str}&fields=last_battle_time,nickname"
                    
                    last_battle_dict = {}
                    real_nickname_dict = {}
                    async with session.get(acc_info_url, timeout=aiohttp.ClientTimeout(total=8)) as resp2:
                        if resp2.status == 200:
                            acc_data = await resp2.json()
                            player_stats = acc_data.get('data', {})
                            for pid, pinfo in player_stats.items():
                                if pinfo:
                                    if 'last_battle_time' in pinfo:
                                        last_battle_dict[str(pid)] = pinfo['last_battle_time']
                                    if 'nickname' in pinfo:
                                        real_nickname_dict[str(pid)] = pinfo['nickname']

                    now = now_br()
                    one_month_ago = now - timedelta(days=30)
                    
                    member_list = []
                    for m_id, m_info in members.items():
                        last_battle_ts = last_battle_dict.get(str(m_id), 0)
                        updated_nick = real_nickname_dict.get(str(m_id), m_info.get('account_name'))
                        role = m_info.get('role', '')
                        role_order, role_badge = get_role_info(role)
                        
                        is_inactive_30d = False
                        if last_battle_ts > 0:
                            dt_last_battle = datetime.fromtimestamp(last_battle_ts, tz=TIMEZONE_BR)
                            if dt_last_battle < one_month_ago:
                                is_inactive_30d = True
                        else:
                            is_inactive_30d = True
                            
                        member_list.append({
                            'account_id': m_info['account_id'],
                            'account_name': updated_nick,
                            'role': role,
                            'role_order': role_order,
                            'role_badge': role_badge,
                            'raw_ts': last_battle_ts,
                            'is_inactive_30d': is_inactive_30d
                        })
                    
                    member_list.sort(key=lambda x: (x['role_order'], -x['raw_ts']))
                    return clan_data.get('tag'), member_list
        except Exception as e:
            print(f"Erro ao carregar membros do clã: {e}")
            
    return None, []

def format_ts_discord(ts: int) -> str:
    if ts <= 0:
        return "❌ *Sem registros*"
    return f"<t:{ts}:f>"

async def build_clan_embed(tag, in_game_members):
    mappings = await get_all_mappings()
    
    em_ambos = []
    apenas_jogo = []
    inativos_30d = []
    
    for m in in_game_members:
        acc_id = m['account_id']
        acc_name = m['account_name']
        ts = m['raw_ts']
        role_badge = m['role_badge']
        time_str = format_ts_discord(ts)
        
        if m['is_inactive_30d']:
            inativos_30d.append(f"• **{acc_name}**{role_badge} ▫️ ⚠️ *Inativo (+30d)* ▫️ {time_str}")
        
        if acc_id in mappings:
            discord_list = mappings[acc_id]
            mentions_str = " / ".join(f"<@{d['discord_id']}>" for d in discord_list)
            em_ambos.append(f"• **{acc_name}**{role_badge} ➔ {mentions_str}\n   └ 🕒 Última batalha: {time_str}")
        else:
            apenas_jogo.append(f"• **{acc_name}**{role_badge}\n   └ 🕒 Última batalha: {time_str}")
            
    embed = discord.Embed(
        title=f"📋 Organização do Clã [{tag}]",
        description=(
            f"📊 **Resumo:** Total no Jogo: **{len(in_game_members)}** │ Vinculados: **{len(em_ambos)}** │ Pendentes: **{len(apenas_jogo)}**\n"
            f"⚠️ **Inativos (+30 dias):** **{len(inativos_30d)}** membros\n"
            "───────────────────────────────"
        ),
        color=0x3498DB
    )
    
    def add_safe_fields(embed_obj, title, item_list):
        if not item_list:
            embed_obj.add_field(name=title, value="*Nenhum membro registrado.*", inline=False)
            return
            
        current_text = ""
        part = 1
        for item in item_list:
            if len(current_text) + len(item) + 2 > 950:
                field_title = f"{title} (Parte {part})" if part > 1 else title
                embed_obj.add_field(name=field_title, value=current_text, inline=False)
                current_text = item + "\n"
                part += 1
            else:
                current_text += item + "\n"
                
        if current_text:
            field_title = f"{title} (Parte {part})" if part > 1 else title
            embed_obj.add_field(name=field_title, value=current_text, inline=False)

    add_safe_fields(embed, "🟢 Presentes no Jogo E no Discord", em_ambos)
    add_safe_fields(embed, "🔴 Apenas no Clã do Jogo", apenas_jogo)
    if inativos_30d:
        add_safe_fields(embed, "⚠️ Inativos Há Mais de 30 Dias", inativos_30d)
    
    embed.set_footer(text="Auto-atualiza de hora em hora • Horário de Brasília")
    embed.timestamp = now_br()
    return embed

def build_online_embed(tag, in_game_members):
    now = now_br()
    recent_activity = []
    
    for m in in_game_members:
        if m['raw_ts'] > 0:
            dt_battle = datetime.fromtimestamp(m['raw_ts'], tz=TIMEZONE_BR)
            diff = now - dt_battle
            if diff <= timedelta(hours=2):
                mins = int(diff.total_seconds() / 60)
                time_str = f"há {mins} min" if mins > 0 else "agora mesmo"
                role_badge = m['role_badge']
                ts_disc = format_ts_discord(m['raw_ts'])
                recent_activity.append(f"🎮 **{m['account_name']}**{role_badge} ▫️ *{time_str}* ({ts_disc})")

    embed = discord.Embed(
        title=f"⚡ Atividade Recente / Online [{tag}]",
        description="Jogadores que entraram em batalha nas **últimas 2 horas**:\n───────────────────────────────",
        color=0x2ECC71
    )
    
    if recent_activity:
        embed.add_field(name="🟢 Ativos Recentemente", value="\n".join(recent_activity), inline=False)
    else:
        embed.add_field(name="💤 Status do Clã", value="Nenhum membro esteve em batalha nas últimas 2 horas.", inline=False)
        
    embed.set_footer(text="Atualizado a cada 5 minutos • Horário de Brasília")
    embed.timestamp = now_br()
    return embed

async def send_inactivity_warning(guild, in_game_members):
    config = await get_server_config(guild.id)
    if not config or not config.get('absent_channel_id'):
        return

    absent_channel = guild.get_channel(config['absent_channel_id'])
    if not absent_channel:
        return

    mappings = await get_all_mappings()
    inactives = [m for m in in_game_members if m['is_inactive_30d']]
    
    if not inactives:
        return

    unlinked_inactives = []

    for m in inactives:
        acc_id = m['account_id']
        acc_name = m['account_name']
        
        if acc_id in mappings:
            discord_list = mappings[acc_id]
            mentions_str = " ".join(f"<@{d['discord_id']}>" for d in discord_list)
            msg = f"⚠️ {mentions_str} Você está há mais de **30 dias sem jogar**. Por favor, faça ao menos uma batalha REGULAR no WOTB para manter o seu registro ativo!"
            await absent_channel.send(msg)
        else:
            unlinked_inactives.append(acc_name)

    if unlinked_inactives:
        list_str = ", ".join(f"**{nick}**" for nick in unlinked_inactives)
        await absent_channel.send(f"⚠️ **Jogadores inativos (+30 dias) sem vínculo no Discord:**\n{list_str}\n*(Use `!vincular NICK` para registrá-los)*")

# --- 5. TAREFAS AUTOMÁTICAS ---

@tasks.loop(hours=1)
async def auto_update_job():
    guild_ids = await get_all_configured_servers()

    for g_id in guild_ids:
        config = await get_server_config(g_id)
        if not config or not config.get('clan_tag'):
            continue
            
        clan_tag = config['clan_tag']
        tag, in_game_members = await fetch_clan_members(clan_tag)
        if not in_game_members:
            continue

        channel = bot.get_channel(config['channel_id']) if config.get('channel_id') else None
        mappings = await get_all_mappings()

        for m in in_game_members:
            acc_id = m['account_id']
            current_nick = m['account_name']
            
            if acc_id in mappings:
                old_nick = mappings[acc_id][0]['acc_name']
                if old_nick != current_nick:
                    await update_account_nickname_by_id(acc_id, current_nick)
                    if channel:
                        discord_mentions = " / ".join(f"<@{d['discord_id']}>" for d in mappings[acc_id])
                        await channel.send(
                            f"✏️ **TROCA DE NICK DETECTADA:** O membro {discord_mentions} "
                            f"alterou o seu nick de `{old_nick}` para **`{current_nick}`**!"
                        )

        current_ids = {m['account_id']: m['account_name'] for m in in_game_members}
        last_ids_str = config.get('last_members')
        
        if last_ids_str and channel:
            old_ids = set(map(int, last_ids_str.split(','))) if last_ids_str else set()
            new_ids = set(current_ids.keys())
            
            for e_id in (new_ids - old_ids):
                await channel.send(f"🎉 **NOVO MEMBRO:** O jogador **{current_ids[e_id]}** entrou no clã **[{tag}]**!")
            for s_id in (old_ids - new_ids):
                await channel.send(f"🚪 **SAÍDA:** Um jogador deixou o clã **[{tag}]**.")

        current_ids_str = ",".join(map(str, current_ids.keys()))
        await update_last_members(g_id, current_ids_str)
        
        if config.get('channel_id') and config.get('panel_message_id') and channel:
            try:
                panel_msg = await channel.fetch_message(config['panel_message_id'])
                embed = await build_clan_embed(tag, in_game_members)
                await panel_msg.edit(embed=embed)
            except Exception as e:
                print(f"Erro painel principal: {e}")

@tasks.loop(minutes=5)
async def fast_online_update_job():
    guild_ids = await get_all_configured_servers()
    for g_id in guild_ids:
        config = await get_server_config(g_id)
        if not config or not config.get('clan_tag') or not config.get('online_panel_message_id'):
            continue
            
        channel = bot.get_channel(config['channel_id'])
        if not channel:
            continue
            
        try:
            tag, in_game_members = await fetch_clan_members(config['clan_tag'])
            if in_game_members:
                online_msg = await channel.fetch_message(config['online_panel_message_id'])
                embed = build_online_embed(tag, in_game_members)
                await online_msg.edit(embed=embed)
        except Exception as e:
            print(f"Erro painel online (5m): {e}")

@auto_update_job.before_loop
@fast_online_update_job.before_loop
async def before_loops():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    print(f"✅ Bot do Clã online (MongoDB) como: {bot.user} | Brasília: {now_br().strftime('%d/%m/%Y %H:%M:%S')}")
    if not auto_update_job.is_running():
        auto_update_job.start()
    if not fast_online_update_job.is_running():
        fast_online_update_job.start()

# --- 6. TRATAMENTO GLOBAL DE ERROS ---
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
        
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ **Parâmetro ausente!** Uso correto: `{ctx.prefix}{ctx.command.name} {ctx.command.signature}`")
        return

    if isinstance(error, commands.MissingPermissions):
        await ctx.send("⚠️ Você precisa de permissão de **Administrador** para usar este comando.")
        return

    print(f"Erro no comando {ctx.command}: {error}")
    await ctx.send("⚠️ **Ocorreu um erro interno ao processar o comando.** Verifique se a variável MONGO_URI está configurada corretamente no Render.")

# --- 7. COMANDOS ---

@bot.command(name="ajuda", aliases=["help"])
async def ajuda(ctx):
    """Exibe a lista explicativa de todos os comandos do bot."""
    embed = discord.Embed(
        title="📖 Central de Ajuda — Bot do Clã",
        description="Confira abaixo a lista detalhada de todos os comandos disponíveis organizados por categoria:",
        color=0x3498DB
    )

    embed.add_field(
        name="👤 Comandos de Membros",
        value=(
            "• `!vincular [NICK]` — Vincula um Nick do jogo a uma conta do Discord.\n"
            "• `!vinculados` — Lista todas as contas do jogo vinculadas a membros do Discord.\n"
            "• `!pendentes` *(ou `!naovinculados`)* — Lista os membros do clã no jogo que ainda **NÃO** se vincularam.\n"
            "• `!membros` — Gera um relatório momentâneo da lista e status de atividade dos membros.\n"
            "• `!ajuda` — Exibe este painel explicativo de comandos."
        ),
        inline=False
    )

    embed.add_field(
        name="🛡️ Comandos de Administração",
        value=(
            "• `!setcla TAG` — Define a TAG exata do clã para este servidor do Discord.\n"
            "• `!painel` — Cria o painel fixo de organização do clã (auto-atualiza a cada 1 hora).\n"
            "• `!painelonline` — Cria o painel de atividade recente dos membros (auto-atualiza a cada 5 minutos).\n"
            "• `!setausentes #canal` — Define o canal onde serão enviados os alertas de membros inativos (+30 dias).\n"
            "• `!removerausentes` — Desativa os alertas automáticos do canal de ausentes.\n"
            "• `!desvincular @membro` — Remove manualmente o vínculo de um usuário do banco de dados."
        ),
        inline=False
    )

    embed.set_footer(text="Todos os dados são salvos em nuvem permanentemente no MongoDB Atlas.")
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def setcla(ctx, tag: str):
    clean_tag = tag.strip().upper()
    await set_server_clan(ctx.guild.id, clean_tag)
    
    embed = discord.Embed(
        title=f"✅ Clã Configurado: [{clean_tag}]",
        description="O clã foi associado a este servidor via MongoDB.\n\n**Manual Resumido:**",
        color=0x2ECC71
    )
    embed.add_field(
        name="🛠️ Comandos Principais",
        value=(
            "• `!vincular NICK_DO_JOGO` ➔ Vincula sua conta ao Discord em 1 passo.\n"
            "• `!painel` ➔ Painel fixo do clã (auto-atualiza a cada 1 hora).\n"
            "• `!painelonline` ➔ Painel de atividade recente (auto-atualiza a cada 5 min).\n"
            "• `!setausentes #canal` ➔ Alertas de ausentes (>30d).\n"
            "• `!vinculados` ➔ Lista todas as contas vinculadas.\n"
            "• `!pendentes` ➔ Lista quem falta se vincular.\n"
            "• `!ajuda` ➔ Exibe o painel explicativo completo de comandos.\n"
            "• `!desvincular @membro` ➔ Remove vinculação."
        ),
        inline=False
    )
    embed.set_footer(text="Dados salvos no MongoDB Atlas")
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def setausentes(ctx, channel: discord.TextChannel):
    await set_absent_channel(ctx.guild.id, channel.id)
    await ctx.send(f"✅ **Canal de ausentes definido para:** {channel.mention}")

@bot.command()
@commands.has_permissions(administrator=True)
async def removerausentes(ctx):
    await remove_absent_channel(ctx.guild.id)
    await ctx.send("🧹 **O canal de alertas de ausentes foi desativado com sucesso.**")

@bot.command()
@commands.has_permissions(administrator=True)
async def painel(ctx):
    config = await get_server_config(ctx.guild.id)
    if not config or not config.get('clan_tag'):
        await ctx.send("⚠️ Nenhum clã foi configurado neste servidor. Use `!setcla TAG` primeiro.")
        return

    loading_msg = await ctx.send(f"🔄 Gerando painel do clã **[{config['clan_tag']}]**...")
    tag, in_game_members = await fetch_clan_members(config['clan_tag'])
    if not in_game_members:
        await loading_msg.edit(content="❌ Erro ao buscar os dados na Wargaming.")
        return
        
    embed = await build_clan_embed(tag, in_game_members)
    await loading_msg.delete()
    
    panel_msg = await ctx.send(embed=embed)
    await set_server_panel(ctx.guild.id, ctx.channel.id, panel_msg.id)
    
    current_ids_str = ",".join(str(m['account_id']) for m in in_game_members)
    await update_last_members(ctx.guild.id, current_ids_str)
    
    await send_inactivity_warning(ctx.guild, in_game_members)

@bot.command()
@commands.has_permissions(administrator=True)
async def painelonline(ctx):
    config = await get_server_config(ctx.guild.id)
    if not config or not config.get('clan_tag'):
        await ctx.send("⚠️ Nenhum clã foi configurado neste servidor. Use `!setcla TAG` primeiro.")
        return

    loading_msg = await ctx.send(f"🔄 Gerando painel de atividade recente do clã **[{config['clan_tag']}]**...")
    tag, in_game_members = await fetch_clan_members(config['clan_tag'])
    if not in_game_members:
        await loading_msg.edit(content="❌ Erro ao buscar os dados na Wargaming.")
        return
        
    embed = build_online_embed(tag, in_game_members)
    await loading_msg.delete()
    
    online_msg = await ctx.send(embed=embed)
    await set_online_panel(ctx.guild.id, ctx.channel.id, online_msg.id)

@bot.command()
async def vincular(ctx, *, nick_jogo: str = None):
    """Vincula uma conta do jogo perguntando de quem é a conta no Discord."""
    config = await get_server_config(ctx.guild.id)
    if not config or not config.get('clan_tag'):
        await ctx.send("⚠️ Nenhum clã configurado neste servidor. Use `!setcla TAG` primeiro.")
        return

    def check_author(m):
        return m.author == ctx.author and m.channel == ctx.channel

    if not nick_jogo:
        await ctx.send("🎮 **Qual é o Nick no WOTB que você deseja vincular?** *(Responda nesta conversa em até 30 segundos)*")
        try:
            msg_game = await bot.wait_for('message', check=check_author, timeout=30.0)
            nick_jogo = msg_game.content.strip()
        except asyncio.TimeoutError:
            await ctx.send("⏰ **Tempo esgotado!** Digite `!vincular NICK` novamente.")
            return

    search_nick = nick_jogo.lower()
    loading_msg = await ctx.send("🔎 Buscando jogador no clã...")
    
    tag, clan_members = await fetch_clan_members(config['clan_tag'])
    await loading_msg.delete()

    if not clan_members:
        await ctx.send("❌ Não foi possível carregar os membros do clã.")
        return

    matches = [m for m in clan_members if search_nick in m['account_name'].lower()]

    if not matches:
        await ctx.send(f"❌ O jogador **{nick_jogo}** não foi encontrado dentro do clã **[{tag}]**.")
        return

    selected_player = matches[0]
    acc_id = selected_player['account_id']
    real_game_nick = selected_player['account_name']

    # Pergunta de quem é essa conta no Discord
    await ctx.send(
        f"🎯 Encontrado: **{real_game_nick}**!\n"
        f"💬 **Quem é o dono dessa conta no Discord?**\n"
        f"*(Mencione a pessoa ex: `@Fulano`, ou digite `eu` se for sua própria conta)*"
    )

    try:
        msg_discord = await bot.wait_for('message', check=check_author, timeout=30.0)
        content = msg_discord.content.strip()

        target_member = None
        if msg_discord.mentions:
            target_member = msg_discord.mentions[0]
        elif content.lower() in ['eu', 'me', 'minha', 'mim']:
            target_member = ctx.author
        else:
            # Tenta buscar pelo nome/apelido digitado
            target_member = discord.utils.find(
                lambda m: content.lower() in m.name.lower() or (m.nick and content.lower() in m.nick.lower()),
                ctx.guild.members
            )

        if not target_member:
            await ctx.send("❌ **Não consegui encontrar esse membro no Discord.** Vinculação cancelada.")
            return

        await set_mapping(acc_id, real_game_nick, target_member.id, str(target_member))
        await ctx.send(f"🎉 **Vinculação realizada com sucesso!**\n🎮 **Jogo:** `{real_game_nick}` ➔ 💬 **Discord:** {target_member.mention}")

    except asyncio.TimeoutError:
        await ctx.send("⏰ **Tempo esgotado!** Vinculação cancelada.")

@bot.command(name="pendentes", aliases=["naovinculados"])
async def pendentes(ctx):
    """Exibe a lista de membros do clã no jogo que ainda NÃO se vincularam ao Discord."""
    config = await get_server_config(ctx.guild.id)
    if not config or not config.get('clan_tag'):
        await ctx.send("⚠️ Nenhum clã configurado neste servidor. Use `!setcla TAG` primeiro.")
        return

    loading_msg = await ctx.send("🔎 Verificando membros pendentes de vinculação...")
    
    tag, in_game_members = await fetch_clan_members(config['clan_tag'])
    await loading_msg.delete()

    if not in_game_members:
        await ctx.send("❌ Não foi possível carregar os membros do clã.")
        return

    mappings = await get_all_mappings()

    unlinked_members = [
        m for m in in_game_members 
        if m['account_id'] not in mappings
    ]

    if not unlinked_members:
        await ctx.send(f"🎉 **Excelente!** Todos os **{len(in_game_members)}** membros do clã **[{tag}]** já estão vinculados!")
        return

    lines = []
    for m in unlinked_members:
        role_badge = m['role_badge']
        lines.append(f"• **{m['account_name']}**{role_badge}")

    embed = discord.Embed(
        title=f"⚠️ Membros Pendentes de Vinculação [{tag}]",
        description=(
            f"Existe(m) **{len(unlinked_members)}** de **{len(in_game_members)}** jogador(es) que ainda **NÃO** se vincularam:\n\n"
            + "\n".join(lines[:30])
        ),
        color=0xE74C3C
    )

    if len(lines) > 30:
        embed.set_footer(text=f"E mais {len(lines) - 30} membro(s)... • Use !vincular NICK para registrá-los.")
    else:
        embed.set_footer(text="Instrua os membros a usarem: !vincular SEU_NICK")

    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def desvincular(ctx, member: discord.Member):
    await remove_mapping(member.id)
    await ctx.send(f"🗑️ Vinculação do membro {member.mention} removida do MongoDB.")

@bot.command()
async def vinculados(ctx):
    mappings = await get_all_mappings()
    if not mappings:
        await ctx.send("ℹ️ Nenhum membro está vinculado no momento.")
        return
    
    lines = []
    for acc_id, d_list in mappings.items():
        game_nick = d_list[0]['acc_name']
        mentions = " / ".join(f"<@{d['discord_id']}>" for d in d_list)
        lines.append(f"• **{game_nick}** ➔ {mentions}")
        
    embed = discord.Embed(title="🔗 Membros Vinculados (MongoDB)", description="\n".join(lines), color=0x2ECC71)
    await ctx.send(embed=embed)

@bot.command()
async def membros(ctx):
    config = await get_server_config(ctx.guild.id)
    if not config or not config.get('clan_tag'):
        await ctx.send("⚠️ Nenhum clã configurado neste servidor.")
        return

    loading_msg = await ctx.send("🔄 Sincronizando dados...")
    tag, in_game_members = await fetch_clan_members(config['clan_tag'])
    if not in_game_members:
        await loading_msg.edit(content="❌ Erro ao buscar os dados na Wargaming.")
        return

    embed = await build_clan_embed(tag, in_game_members)
    await loading_msg.edit(content="", embed=embed)

# Inicializa o Flask e o Bot
keep_alive()
bot.run(DISCORD_TOKEN)
