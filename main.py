import os
import sqlite3
import asyncio
import difflib
import aiohttp
import discord
from discord.ext import commands, tasks
from flask import Flask
from threading import Thread
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Fuso horário de Brasília
TIMEZONE_BR = ZoneInfo("America/Sao_Paulo")

def now_br():
    return datetime.now(TIMEZONE_BR)

# --- 1. SERVIDOR WEB (FLASK) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot do Clã online!"

def run():
    port = int(os.environ.get("PORT", 10002))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- 2. BANCO DE DADOS ---
DB_NAME = "clan_manager.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clan_mappings_v2 (
            discord_id INTEGER PRIMARY KEY,
            account_id INTEGER,
            account_name TEXT,
            discord_name TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS server_clans (
            guild_id INTEGER PRIMARY KEY,
            clan_tag TEXT,
            channel_id INTEGER,
            panel_message_id INTEGER,
            online_panel_message_id INTEGER,
            last_members TEXT,
            absent_channel_id INTEGER
        )
    ''')
    
    try:
        cursor.execute("ALTER TABLE server_clans ADD COLUMN absent_channel_id INTEGER")
    except sqlite3.OperationalError:
        pass
    
    conn.commit()
    conn.close()

def set_server_clan(guild_id: int, clan_tag: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO server_clans (guild_id, clan_tag)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET clan_tag=excluded.clan_tag
    ''', (guild_id, clan_tag.upper()))
    conn.commit()
    conn.close()

def set_absent_channel(guild_id: int, channel_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE server_clans SET absent_channel_id = ? WHERE guild_id = ?
    ''', (channel_id, guild_id))
    conn.commit()
    conn.close()

def set_server_panel(guild_id: int, channel_id: int, message_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE server_clans SET channel_id = ?, panel_message_id = ? WHERE guild_id = ?
    ''', (channel_id, message_id, guild_id))
    conn.commit()
    conn.close()

def set_online_panel(guild_id: int, channel_id: int, message_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE server_clans SET channel_id = ?, online_panel_message_id = ? WHERE guild_id = ?
    ''', (channel_id, message_id, guild_id))
    conn.commit()
    conn.close()

def update_last_members(guild_id: int, member_ids_str: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE server_clans SET last_members = ? WHERE guild_id = ?
    ''', (member_ids_str, guild_id))
    conn.commit()
    conn.close()

def get_server_config(guild_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT clan_tag, channel_id, panel_message_id, online_panel_message_id, last_members, absent_channel_id FROM server_clans WHERE guild_id = ?', (guild_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'clan_tag': row[0],
            'channel_id': row[1],
            'panel_message_id': row[2],
            'online_panel_message_id': row[3],
            'last_members': row[4],
            'absent_channel_id': row[5]
        }
    return None

def get_all_configured_servers():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT guild_id FROM server_clans WHERE clan_tag IS NOT NULL')
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

def set_mapping(account_id: int, account_name: str, discord_id: int, discord_name: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO clan_mappings_v2 (discord_id, account_id, account_name, discord_name)
        VALUES (?, ?, ?, ?)
    ''', (discord_id, account_id, account_name, discord_name))
    conn.commit()
    conn.close()

def update_account_nickname_by_id(account_id: int, new_account_name: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE clan_mappings_v2
        SET account_name = ?
        WHERE account_id = ?
    ''', (new_account_name, account_id))
    conn.commit()
    conn.close()

def remove_mapping(discord_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM clan_mappings_v2 WHERE discord_id = ?', (discord_id,))
    conn.commit()
    conn.close()

def get_all_mappings():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT account_id, account_name, discord_id, discord_name FROM clan_mappings_v2')
    rows = cursor.fetchall()
    conn.close()
    
    mappings = {}
    for row in rows:
        acc_id = row[0]
        if acc_id not in mappings:
            mappings[acc_id] = []
        mappings[acc_id].append({
            'acc_name': row[1],
            'discord_id': row[2],
            'discord_name': row[3]
        })
    return mappings

init_db()

# --- 3. CONFIGURAÇÕES DO BOT ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

APPLICATION_ID = os.environ.get("APPLICATION_ID")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

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

def build_clan_embed(tag, in_game_members):
    mappings = get_all_mappings()
    
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
    embed.timestamp = datetime.now(TIMEZONE_BR)
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
    embed.timestamp = datetime.now(TIMEZONE_BR)
    return embed

async def send_inactivity_warning(guild, in_game_members):
    config = get_server_config(guild.id)
    if not config or not config.get('absent_channel_id'):
        return

    absent_channel = guild.get_channel(config['absent_channel_id'])
    if not absent_channel:
        return

    mappings = get_all_mappings()
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
        await absent_channel.send(f"⚠️ **Jogadores inativos (+30 dias) sem vínculo no Discord:**\n{list_str}\n*(Use `!vincular` para registrá-los)*")

# --- 4. TAREFAS AUTOMÁTICAS ---

@tasks.loop(hours=1)
async def auto_update_job():
    guild_ids = get_all_configured_servers()
    mappings = get_all_mappings()

    for g_id in guild_ids:
        config = get_server_config(g_id)
        if not config or not config['clan_tag']:
            continue
            
        clan_tag = config['clan_tag']
        tag, in_game_members = await fetch_clan_members(clan_tag)
        if not in_game_members:
            continue

        channel = bot.get_channel(config['channel_id']) if config.get('channel_id') else None

        for m in in_game_members:
            acc_id = m['account_id']
            current_nick = m['account_name']
            
            if acc_id in mappings:
                old_nick = mappings[acc_id][0]['acc_name']
                if old_nick != current_nick:
                    update_account_nickname_by_id(acc_id, current_nick)
                    if channel:
                        discord_mentions = " / ".join(f"<@{d['discord_id']}>" for d in mappings[acc_id])
                        await channel.send(
                            f"✏️ **TROCA DE NICK DETECTADA:** O membro {discord_mentions} "
                            f"alterou o seu nick de `{old_nick}` para **`{current_nick}`**!"
                        )

        mappings = get_all_mappings()

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
        update_last_members(g_id, current_ids_str)
        
        if config.get('channel_id') and config.get('panel_message_id') and channel:
            try:
                panel_msg = await channel.fetch_message(config['panel_message_id'])
                embed = build_clan_embed(tag, in_game_members)
                await panel_msg.edit(embed=embed)
            except Exception as e:
                print(f"Erro painel principal: {e}")

@tasks.loop(minutes=5)
async def fast_online_update_job():
    guild_ids = get_all_configured_servers()
    for g_id in guild_ids:
        config = get_server_config(g_id)
        if not config or not config['clan_tag'] or not config.get('online_panel_message_id'):
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
    print(f"✅ Bot do Clã online como: {bot.user} | Horário de Brasília: {now_br().strftime('%d/%m/%Y %H:%M:%S')}")
    if not auto_update_job.is_running():
        auto_update_job.start()
    if not fast_online_update_job.is_running():
        fast_online_update_job.start()

# --- 5. COMANDOS ---

@bot.command()
@commands.has_permissions(administrator=True)
async def setcla(ctx, tag: str):
    clean_tag = tag.strip().upper()
    set_server_clan(ctx.guild.id, clean_tag)
    
    embed = discord.Embed(
        title=f"✅ Clã Configurado: [{clean_tag}]",
        description="O clã foi associado a este servidor com sucesso.\n\n**Manual Resumido de Comandos:**",
        color=0x2ECC71
    )
    embed.add_field(
        name="🛠️ Comandos Principais",
        value=(
            "• `!setausentes #canal` ➔ Define canal para alertas de inatividade (+30d).\n"
            "• `!painel` ➔ Cria o painel fixo do clã (auto-atualiza a cada 1 hora).\n"
            "• `!painelonline` ➔ Cria o painel de atividade recente (auto-atualiza a cada 5 min).\n"
            "• `!membros` ➔ Exibe a lista completa de membros na tela instantaneamente.\n"
            "• `!vincular` ➔ Inicia o assistente para ligar conta do jogo ao Discord.\n"
            "• `!desvincular @membro` ➔ Remove a vinculação de um usuário.\n"
            "• `!vinculados` ➔ Lista todas as contas vinculadas do servidor.\n"
            "• `!removerausentes` ➔ Desativa o canal de alertas de ausentes.\n"
            "• `!ajuda` ➔ Exibe detalhes e explicações de cada função."
        ),
        inline=False
    )
    embed.set_footer(text="Horário oficial: Brasília (UTC-3)")
    await ctx.send(embed=embed)

@bot.command()
async def ajuda(ctx):
    embed = discord.Embed(
        title="📖 Guia do Bot de Gestão de Clã",
        description="Manual de comandos e detalhes de funcionamento do bot.",
        color=0x3498DB
    )
    embed.add_field(
        name="👑 Hierarquia de Exibição",
        value="Os membros são listados por cargo Wargaming: **Líder 👑** ➔ **Vice-Líder ⚔️** ➔ **Oficial 📜** ➔ **Membro**. Dentro do mesmo cargo, a ordenação é feita pela atividade mais recente.",
        inline=False
    )
    embed.add_field(
        name="🔗 Vincular Contas (`!vincular` / `!desvincular`)",
        value="O `!vincular` busca o Nick exclusivamente dentro da lista do seu clã e associa ao membro no Discord.",
        inline=False
    )
    embed.add_field(
        name="⚡ Painéis Fixo e Online",
        value="• `!painel`: Painel principal fixo com todos os membros, organizados por status no Discord.\n• `!painelonline`: Exibe apenas quem participou de batalhas nas últimas 2 horas.",
        inline=False
    )
    embed.set_footer(text="Horário de Brasília (UTC-3)")
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def setausentes(ctx, channel: discord.TextChannel):
    set_absent_channel(ctx.guild.id, channel.id)
    await ctx.send(f"✅ **Canal de ausentes definido para:** {channel.mention}")

@bot.command()
@commands.has_permissions(administrator=True)
async def removerausentes(ctx):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE server_clans SET absent_channel_id = NULL WHERE guild_id = ?', (ctx.guild.id,))
    conn.commit()
    conn.close()
    await ctx.send("🧹 **O canal de alertas de ausentes foi desativado com sucesso.**")

@bot.command()
@commands.has_permissions(administrator=True)
async def painel(ctx):
    config = get_server_config(ctx.guild.id)
    if not config or not config['clan_tag']:
        await ctx.send("⚠️ Nenhum clã foi configurado neste servidor. Use `!setcla TAG` primeiro.")
        return

    loading_msg = await ctx.send(f"🔄 Gerando painel do clã **[{config['clan_tag']}]**...")
    tag, in_game_members = await fetch_clan_members(config['clan_tag'])
    if not in_game_members:
        await loading_msg.edit(content="❌ Erro ao buscar os dados na Wargaming.")
        return
        
    embed = build_clan_embed(tag, in_game_members)
    await loading_msg.delete()
    
    panel_msg = await ctx.send(embed=embed)
    set_server_panel(ctx.guild.id, ctx.channel.id, panel_msg.id)
    
    current_ids_str = ",".join(str(m['account_id']) for m in in_game_members)
    update_last_members(ctx.guild.id, current_ids_str)
    
    await send_inactivity_warning(ctx.guild, in_game_members)

@bot.command()
@commands.has_permissions(administrator=True)
async def painelonline(ctx):
    config = get_server_config(ctx.guild.id)
    if not config or not config['clan_tag']:
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
    set_online_panel(ctx.guild.id, ctx.channel.id, online_msg.id)

@bot.command()
async def vincular(ctx):
    """Comando interativo para vincular nick do WOTB (restrito aos membros do clã) ao Discord."""
    config = get_server_config(ctx.guild.id)
    if not config or not config['clan_tag']:
        await ctx.send("⚠️ Nenhum clã foi configurado neste servidor. Peça para um Admin usar `!setcla TAG` primeiro.")
        return

    def check_author(m):
        return m.author == ctx.author and m.channel == ctx.channel

    await ctx.send("🎮 **Qual é o Nick no WOTB do jogador do clã que deseja vincular?**")
    try:
        msg_game = await bot.wait_for('message', check=check_author, timeout=60.0)
    except asyncio.TimeoutError:
        await ctx.send("⏰ **Tempo esgotado!** Processo de vinculação cancelado.")
        return

    search_nick = msg_game.content.strip().lower()
    
    # Carrega os membros ATUAIS do clã
    loading_msg = await ctx.send("🔎 Buscando jogador na lista do clã...")
    tag, clan_members = await fetch_clan_members(config['clan_tag'])
    await loading_msg.delete()

    if not clan_members:
        await ctx.send("❌ Não foi possível carregar os membros do clã no momento.")
        return

    # Busca correspondências dentro da lista do clã
    matches = [m for m in clan_members if search_nick in m['account_name'].lower()]

    if not matches:
        await ctx.send(f"❌ O jogador **{msg_game.content.strip()}** não foi encontrado dentro do clã **[{tag}]**.")
        return

    selected_player = None
    if len(matches) == 1:
        selected_player = matches[0]
    else:
        # Se mais de um membro corresponder ao nome digitado
        options_text = "\n".join([f"**{i+1}.** {m['account_name']}" for i, m in enumerate(matches[:5])])
        await ctx.send(
            f"🔎 Encontrei mais de um jogador parecido no clã **[{tag}]**:\n{options_text}\n\n"
            f"👉 Digite o **número** do jogador correspondente (1 a {min(len(matches), 5)}):"
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
        await ctx.send("❌ Seleção inválida. Tente o comando `!vincular` novamente.")
        return

    acc_id = selected_player['account_id']
    real_game_nick = selected_player['account_name']

    # Se quem usou for administrador, ele pode escolher qual conta do Discord associar
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
                f"Deseja vincular esta conta ao membro {found_match.mention}? *(Responda 'sim' ou 'nao')*"
            )
            try:
                msg_confirm = await bot.wait_for('message', check=check_author, timeout=30.0)
                if msg_confirm.content.strip().lower() in ['s', 'sim', 'yes', 'y']:
                    target_member = found_match
                else:
                    await ctx.send("💬 Mencione (`@membro`) ou digite o ID do membro no Discord para vincular:")
                    msg_target = await bot.wait_for('message', check=check_author, timeout=40.0)
                    if msg_target.mentions:
                        target_member = msg_target.mentions[0]
                    elif msg_target.content.strip().isdigit():
                        target_member = ctx.guild.get_member(int(msg_target.content.strip()))
            except asyncio.TimeoutError:
                await ctx.send("⏰ Tempo esgotado! Processo cancelado.")
                return

    set_mapping(acc_id, real_game_nick, target_member.id, str(target_member))
    await ctx.send(f"🎉 **Vinculação realizada com sucesso!**\n🎮 **Jogo:** `{real_game_nick}` ➔ 💬 **Discord:** {target_member.mention}")

@bot.command()
@commands.has_permissions(administrator=True)
async def desvincular(ctx, member: discord.Member):
    remove_mapping(member.id)
    await ctx.send(f"🗑️ A vinculação do membro {member.mention} foi removida com sucesso.")

@bot.command()
async def vinculados(ctx):
    mappings = get_all_mappings()
    if not mappings:
        await ctx.send("ℹ️ Nenhum membro está vinculado no momento.")
        return
    
    lines = []
    for acc_id, d_list in mappings.items():
        game_nick = d_list[0]['acc_name']
        mentions = " / ".join(f"<@{d['discord_id']}>" for d in d_list)
        lines.append(f"• **{game_nick}** ➔ {mentions}")
        
    embed = discord.Embed(title="🔗 Membros Vinculados", description="\n".join(lines), color=0x2ECC71)
    await ctx.send(embed=embed)

@bot.command()
async def membros(ctx):
    config = get_server_config(ctx.guild.id)
    if not config or not config['clan_tag']:
        await ctx.send("⚠️ Nenhum clã foi configurado neste servidor. Use `!setcla TAG` primeiro.")
        return

    loading_msg = await ctx.send("🔄 Sincronizando dados...")
    tag, in_game_members = await fetch_clan_members(config['clan_tag'])
    if not in_game_members:
        await loading_msg.edit(content="❌ Erro ao buscar os dados na Wargaming.")
        return

    embed = build_clan_embed(tag, in_game_members)
    await loading_msg.edit(content="", embed=embed)

keep_alive()
bot.run(DISCORD_TOKEN)
