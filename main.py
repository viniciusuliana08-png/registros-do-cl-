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

# --- 1. SERVIDOR WEB (FLASK PARA MANTER ON-LINE 24/7) ---
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
    
    # Migração do banco antigo se existir
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='clan_mappings'")
    if cursor.fetchone():
        cursor.execute("SELECT account_id, account_name, discord_id, discord_name FROM clan_mappings")
        old_rows = cursor.fetchall()
        for r in old_rows:
            cursor.execute('''
                INSERT OR REPLACE INTO clan_mappings_v2 (discord_id, account_id, account_name, discord_name)
                VALUES (?, ?, ?, ?)
            ''', (r[2], r[0], r[1], r[3]))
        cursor.execute("DROP TABLE clan_mappings")

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
        UPDATE server_clans 
        SET absent_channel_id = ? 
        WHERE guild_id = ?
    ''', (channel_id, guild_id))
    conn.commit()
    conn.close()

def set_server_panel(guild_id: int, channel_id: int, message_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE server_clans 
        SET channel_id = ?, panel_message_id = ? 
        WHERE guild_id = ?
    ''', (channel_id, message_id, guild_id))
    conn.commit()
    conn.close()

def set_online_panel(guild_id: int, channel_id: int, message_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE server_clans 
        SET channel_id = ?, online_panel_message_id = ? 
        WHERE guild_id = ?
    ''', (channel_id, message_id, guild_id))
    conn.commit()
    conn.close()

def update_last_members(guild_id: int, member_ids_str: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE server_clans 
        SET last_members = ? 
        WHERE guild_id = ?
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

# --- 3. BOT CONFIG ---
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
        return 1, " 👑 [Líder]"
    elif r in ['executive_officer', 'vice_leader', 'co_leader', 'deputy_commander', 'sub_commander']:
        return 2, " ⚔️ [Vice-Líder]"
    elif r in ['commander_assistant', 'recruiter', 'diplomat', 'quartermaster', 'personnel_officer', 'combat_officer']:
        return 3, " 📜 [Oficial]"
        
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
                    
                    acc_info_url = f"{base_url}/wotb/account/info/?application_id={APPLICATION_ID}&account_id={acc_ids_str}&fields=last_battle_time"
                    
                    last_battle_dict = {}
                    async with session.get(acc_info_url, timeout=aiohttp.ClientTimeout(total=8)) as resp2:
                        if resp2.status == 200:
                            acc_data = await resp2.json()
                            player_stats = acc_data.get('data', {})
                            for pid, pinfo in player_stats.items():
                                if pinfo and 'last_battle_time' in pinfo:
                                    last_battle_dict[str(pid)] = pinfo['last_battle_time']

                    now = now_br()
                    one_month_ago = now - timedelta(days=30)
                    
                    member_list = []
                    for m_id, m_info in members.items():
                        last_battle_ts = last_battle_dict.get(str(m_id), 0)
                        role = m_info.get('role', '')
                        role_order, role_badge = get_role_info(role)
                        
                        is_inactive_30d = False
                        if last_battle_ts > 0:
                            dt_last_battle = datetime.fromtimestamp(last_battle_ts, tz=TIMEZONE_BR)
                            last_battle_str = dt_last_battle.strftime('%d/%m/%Y %H:%M')
                            if dt_last_battle < one_month_ago:
                                is_inactive_30d = True
                        else:
                            last_battle_str = "Sem registros"
                            is_inactive_30d = True
                            
                        member_list.append({
                            'account_id': m_info['account_id'],
                            'account_name': m_info['account_name'],
                            'role': role,
                            'role_order': role_order,
                            'role_badge': role_badge,
                            'last_battle': last_battle_str,
                            'raw_ts': last_battle_ts,
                            'is_inactive_30d': is_inactive_30d
                        })
                    
                    # Ordenação: Líder -> Vice-Líder -> Oficial -> Membro
                    member_list.sort(key=lambda x: (x['role_order'], -x['raw_ts']))
                    return clan_data.get('tag'), member_list
        except Exception as e:
            print(f"Erro ao carregar membros do clã: {e}")
            
    return None, []

def build_clan_embed(tag, in_game_members):
    mappings = get_all_mappings()
    
    em_ambos = []
    apenas_jogo = []
    inativos_30d = []
    
    for m in in_game_members:
        acc_id = m['account_id']
        acc_name = m['account_name']
        last_b = m['last_battle']
        role_badge = m['role_badge']
        
        if m['is_inactive_30d']:
            inativos_30d.append(f"• **{acc_name}**{role_badge} | ⚠️ *Inativo (+30d) - Última: {last_b}*")
        
        if acc_id in mappings:
            discord_list = mappings[acc_id]
            mentions_str = " / ".join(f"<@{d['discord_id']}>" for d in discord_list)
            em_ambos.append(f"• **{acc_name}**{role_badge} ➔ {mentions_str} | 🕒 *{last_b}*")
        else:
            apenas_jogo.append(f"• **{acc_name}**{role_badge} | 🕒 *{last_b}*")
            
    now_str = now_br().strftime('%d/%m/%Y às %H:%M (Horário de Brasília)')
    
    embed = discord.Embed(
        title=f"📋 Organização do Clã [{tag}]",
        description=(
            f"Total no Jogo: **{len(in_game_members)}** | Vinculados: **{len(em_ambos)}** | "
            f"Pendentes: **{len(apenas_jogo)}**\n⚠️ **Inativos (+30 dias):** {len(inativos_30d)} membros"
        ),
        color=0x3498DB
    )
    
    def add_safe_fields(embed_obj, title, item_list):
        if not item_list:
            embed_obj.add_field(name=title, value="Nenhum membro.", inline=False)
            return
            
        current_text = ""
        part = 1
        for item in item_list:
            if len(current_text) + len(item) + 1 > 950:
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
        add_safe_fields(embed, "⚠️ Membros Inativos no Jogo (+ de 1 Mês)", inativos_30d)
    
    embed.set_footer(text=f"Última atualização: {now_str} • Auto-atualiza de hora em hora.")
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
                recent_activity.append(f"🎮 **{m['account_name']}**{role_badge} — *Jogou {time_str}*")

    now_str = now.strftime('%H:%M:%S (Horário de Brasília)')
    embed = discord.Embed(
        title=f"⚡ Atividade Recente / Online [{tag}]",
        description="Mostra jogadores que batalharam nos últimos **120 minutos**.",
        color=0x2ECC71
    )
    
    if recent_activity:
        embed.add_field(name="🟢 Ativos Recentemente", value="\n".join(recent_activity), inline=False)
    else:
        embed.add_field(name="💤 Status do Clã", value="Nenhum membro esteve em batalha nas últimas 2 horas.", inline=False)
        
    embed.set_footer(text=f"Atualizado em tempo real às {now_str} • Atualiza a cada 5 minutos.")
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
            msg = f"{mentions_str} você está há um mês sem jogar, por favor para não considerarmos um jogador inativo jogue uma única batalha no modo REGULAR, obrigado pela atenção."
            await absent_channel.send(msg)
        else:
            unlinked_inactives.append(acc_name)

    if unlinked_inactives:
        list_str = ", ".join(f"**{nick}**" for nick in unlinked_inactives)
        await absent_channel.send(f"⚠️ **Jogadores inativos há +30 dias sem vínculo no Discord:**\n{list_str}\n*(Use `!vincular` para vinculá-los)*")

# --- 4. TAREFAS AUTOMÁTICAS ---

@tasks.loop(hours=1)
async def auto_update_job():
    guild_ids = get_all_configured_servers()
    for g_id in guild_ids:
        config = get_server_config(g_id)
        if not config or not config['clan_tag']:
            continue
            
        clan_tag = config['clan_tag']
        tag, in_game_members = await fetch_clan_members(clan_tag)
        if not in_game_members:
            continue
            
        current_ids = {m['account_id']: m['account_name'] for m in in_game_members}
        last_ids_str = config.get('last_members')
        channel = bot.get_channel(config['channel_id']) if config.get('channel_id') else None
        
        if last_ids_str and channel:
            old_ids = set(map(int, last_ids_str.split(','))) if last_ids_str else set()
            new_ids = set(current_ids.keys())
            
            for e_id in (new_ids - old_ids):
                await channel.send(f"🎉 **NOVO MEMBRO:** O jogador **{current_ids[e_id]}** entrou no clã **[{tag}]**!")
            for s_id in (old_ids - new_ids):
                await channel.send(f"🚪 **SAÍDA:** O jogador de ID `{s_id}` deixou o clã **[{tag}]**.")

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
        description="O clã foi atrelado a este servidor. Confira abaixo o **manual resumido de comandos**:",
        color=0x2ECC71
    )
    embed.add_field(
        name="🛠️ Comandos Principais",
        value=(
            "• `!setausentes #canal` ➔ Define canal de avisos de inativos (+30d).\n"
            "• `!painel` ➔ Cria o painel fixo de organização do clã (auto-atualiza a cada 1h).\n"
            "• `!painelonline` ➔ Cria o painel fixo de membros online/batalhas recentes (auto-atualiza a cada 5m).\n"
            "• `!membros` ➔ Exibe instantaneamente a lista atual de membros.\n"
            "• `!vincular` ➔ Inicia o assistente para ligar conta do jogo ao Discord.\n"
            "• `!desvincular @membro` ➔ Remove a vinculação de um membro.\n"
            "• `!vinculados` ➔ Lista todas as contas vinculadas do servidor.\n"
            "• `!ajuda` ➔ Exibe o guia detalhado sobre o funcionamento do bot."
        ),
        inline=False
    )
    embed.set_footer(text="Horário configurado: Brasília (UTC-3) • Digite !ajuda para detalhes.")
    
    await ctx.send(embed=embed)

@bot.command()
async def ajuda(ctx):
    """Exibe o manual detalhado de funcionamento e uso do Bot."""
    embed = discord.Embed(
        title="📖 Guia e Manual do Bot de Gestão de Clã",
        description="Este bot gerencia a organização do clã no WOTB integrando dados do jogo com o seu servidor do Discord.",
        color=0x3498DB
    )
    
    embed.add_field(
        name="👑 Hierarquia e Exibição",
        value=(
            "A lista do clã organiza os jogadores automaticamente por **Cargo Wargaming**:\n"
            "1️⃣ **Líder** 👑\n"
            "2️⃣ **Vice-Líderes** ⚔️\n"
            "3️⃣ **Oficiais** 📜\n"
            "4️⃣ **Membros**\n"
            "*Dentro de cada grupo, os mais ativos recentemente ficam no topo.*"
        ),
        inline=False
    )

    embed.add_field(
        name="🔗 Vincular Contas (!vincular / !desvincular)",
        value=(
            "• `!vincular`: O bot pergunta o nick do WOTB e busca automaticamente pelo nome ou apelido equivalente no servidor. "
            "É possível vincular **mais de uma conta do Discord na mesma conta do jogo**.\n"
            "• `!desvincular @Membro`: Remove o vínculo da conta selecionada."
        ),
        inline=False
    )

    embed.add_field(
        name="⚠️ Controle de Inatividade (+30 dias)",
        value=(
            "Jogadores sem batalhar há mais de 30 dias entram na lista de inativos. "
            "Se o canal de ausentes estiver definido (`!setausentes #canal`), o bot menciona automaticamente os vinculados que estiverem ausentes."
        ),
        inline=False
    )

    embed.add_field(
        name="⚡ Painéis em Tempo Real (!painel / !painelonline)",
        value=(
            "• **Painel Principal (`!painel`)**: Fixo no canal, atualizado de hora em hora. Registra entradas e saídas de membros.\n"
            "• **Painel Online (`!painelonline`)**: Fixo no canal, atualizado a cada 5 minutos com quem jogou nas últimas 2 horas."
        ),
        inline=False
    )
    
    embed.set_footer(text="Horário do Bot: Brasília (UTC-3)")
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def setausentes(ctx, channel: discord.TextChannel):
    set_absent_channel(ctx.guild.id, channel.id)
    await ctx.send(f"✅ **Canal dos Ausentes configurado:** {channel.mention}")

@bot.command()
@commands.has_permissions(administrator=True)
async def painel(ctx):
    config = get_server_config(ctx.guild.id)
    if not config or not config['clan_tag']:
        await ctx.send("⚠️ NENHUM CLÃ CONFIGURADO! Use `!setcla SUA_TAG` primeiro.")
        return

    loading_msg = await ctx.send(f"🔄 Gerando painel do clã **[{config['clan_tag']}]**...")
    tag, in_game_members = await fetch_clan_members(config['clan_tag'])
    if not in_game_members:
        await loading_msg.edit(content="❌ Erro ao buscar dados na Wargaming.")
        return
        
    embed = build_clan_embed(tag, in_game_members)
    await loading_msg.delete()
    
    panel_msg = await ctx.send(embed=embed)
    set_server_panel(ctx.guild.id, ctx.channel.id, panel_msg.id)
    
    current_ids_str = ",".join(str(m['account_id']) for m in in_game_members)
    update_last_members(ctx.guild.id, current_ids_str)
    
    await send_inactivity_warning(ctx.guild, in_game_members)
    await ctx.send("📌 **Painel Principal fixado com sucesso!**", delete_after=10)

@bot.command()
@commands.has_permissions(administrator=True)
async def painelonline(ctx):
    config = get_server_config(ctx.guild.id)
    if not config or not config['clan_tag']:
        await ctx.send("⚠️ NENHUM CLÃ CONFIGURADO! Use `!setcla SUA_TAG` primeiro.")
        return

    loading_msg = await ctx.send(f"🔄 Gerando painel de atividade para **[{config['clan_tag']}]**...")
    tag, in_game_members = await fetch_clan_members(config['clan_tag'])
    if not in_game_members:
        await loading_msg.edit(content="❌ Erro ao buscar dados na Wargaming.")
        return
        
    embed = build_online_embed(tag, in_game_members)
    await loading_msg.delete()
    
    online_msg = await ctx.send(embed=embed)
    set_online_panel(ctx.guild.id, ctx.channel.id, online_msg.id)
    await ctx.send("⚡ **Painel Online/Atividade (5min) fixado com sucesso!**", delete_after=10)

@bot.command()
@commands.has_permissions(administrator=True)
async def vincular(ctx):
    """[Admin] Processo interativo de vinculação com suporte a múltiplos perfis."""
    def check_author(m):
        return m.author == ctx.author and m.channel == ctx.channel

    await ctx.send("🎮 **Qual é o Nick do jogador no WOTB?**")
    try:
        msg_game = await bot.wait_for('message', check=check_author, timeout=40.0)
    except asyncio.TimeoutError:
        await ctx.send("⏰ **Tempo esgotado!** Processo cancelado.")
        return

    game_nick = msg_game.content.strip()
    
    async with aiohttp.ClientSession() as session:
        regions = ["https://api.wotblitz.com", "https://api.wotblitz.eu", "https://api.wotblitz.asia"]
        acc_id = None
        real_game_nick = game_nick
        for reg in regions:
            url = f"{reg}/wotb/account/list/?application_id={APPLICATION_ID}&search={game_nick}"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('data'):
                            for player in data['data']:
                                if player['nickname'].lower() == game_nick.lower():
                                    acc_id = player['account_id']
                                    real_game_nick = player['nickname']
                                    break
                            if not acc_id and data['data']:
                                acc_id = data['data'][0]['account_id']
                                real_game_nick = data['data'][0]['nickname']
                            break
            except Exception:
                pass

    if not acc_id:
        await ctx.send(f"❌ Não encontrei nenhum jogador com o nick **{game_nick}** no WOTB. Processo cancelado.")
        return

    clean_game_nick = real_game_nick.lower()
    members_map = {}
    for member in ctx.guild.members:
        members_map[member.name.lower()] = member
        members_map[member.display_name.lower()] = member

    target_member = None

    if clean_game_nick in members_map:
        target_member = members_map[clean_game_nick]
    else:
        all_discord_names = list(members_map.keys())
        matches = difflib.get_close_matches(clean_game_nick, all_discord_names, n=1, cutoff=0.45)
        if matches:
            target_member = members_map[matches[0]]

    if target_member:
        await ctx.send(
            f"🔎 Encontrei o jogador **{real_game_nick}** na Wargaming.\n"
            f"❓ Analisando a lista do servidor, esse jogador é o membro {target_member.mention}? *(Responda 'sim' ou 'nao')*"
        )
        try:
            msg_confirm = await bot.wait_for('message', check=check_author, timeout=25.0)
            if msg_confirm.content.strip().lower() not in ['s', 'sim', 'yes', 'y']:
                target_member = None
        except asyncio.TimeoutError:
            await ctx.send("⏰ Tempo esgotado. Processo cancelado.")
            return

    if not target_member:
        await ctx.send("💬 Por favor, mencione (`@membro`), digite o Nome/Apelido exato ou o ID do membro do Discord:")
        try:
            msg_discord = await bot.wait_for('message', check=check_author, timeout=40.0)
        except asyncio.TimeoutError:
            await ctx.send("⏰ **Tempo esgotado!** Processo cancelado.")
            return

        input_user = msg_discord.content.strip()

        if msg_discord.mentions:
            target_member = msg_discord.mentions[0]
        elif input_user.isdigit():
            target_member = ctx.guild.get_member(int(input_user))

        if not target_member:
            clean_input = input_user.lstrip('@').lower()
            if clean_input in members_map:
                target_member = members_map[clean_input]

    if not target_member:
        await ctx.send(f"❌ Não foi possível encontrar nenhum membro no servidor referente a essa resposta.")
        return

    set_mapping(acc_id, real_game_nick, target_member.id, str(target_member))
    await ctx.send(f"🎉 **Vinculação realizada com sucesso!**\n🎮 **Jogo:** `{real_game_nick}` ➔ 💬 **Discord:** {target_member.mention}")

@bot.command()
@commands.has_permissions(administrator=True)
async def desvincular(ctx, member: discord.Member):
    remove_mapping(member.id)
    await ctx.send(f"🗑️ Vinculação do membro {member.mention} foi removida!")

@bot.command()
@commands.has_permissions(administrator=True)
async def vinculados(ctx):
    mappings = get_all_mappings()
    if not mappings:
        await ctx.send("ℹ️ Nenhum membro vinculado ainda.")
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
        await ctx.send("⚠️ NENHUM CLÃ CONFIGURADO! Use `!setcla SUA_TAG`")
        return

    loading_msg = await ctx.send(f"🔄 Sincronizando dados...")
    tag, in_game_members = await fetch_clan_members(config['clan_tag'])
    if not in_game_members:
        await loading_msg.edit(content="❌ Erro ao buscar clã na Wargaming.")
        return

    embed = build_clan_embed(tag, in_game_members)
    await loading_msg.edit(content="", embed=embed)
    
    await send_inactivity_warning(ctx.guild, in_game_members)

keep_alive()
bot.run(DISCORD_TOKEN)
