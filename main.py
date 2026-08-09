import os
import sqlite3
import aiohttp
import discord
from discord.ext import commands, tasks
from flask import Flask
from threading import Thread
from datetime import datetime, timedelta

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
        CREATE TABLE IF NOT EXISTS clan_mappings (
            account_id INTEGER PRIMARY KEY,
            account_name TEXT,
            discord_id INTEGER,
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
    
    # Adiciona a coluna absent_channel_id caso o banco já existisse antes
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
        INSERT OR REPLACE INTO clan_mappings (account_id, account_name, discord_id, discord_name)
        VALUES (?, ?, ?, ?)
    ''', (account_id, account_name, discord_id, discord_name))
    conn.commit()
    conn.close()

def remove_mapping(discord_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM clan_mappings WHERE discord_id = ?', (discord_id,))
    conn.commit()
    conn.close()

def get_all_mappings():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT account_id, account_name, discord_id, discord_name FROM clan_mappings')
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: {'acc_name': row[1], 'discord_id': row[2], 'discord_name': row[3]} for row in rows}

init_db()

# --- 3. BOT CONFIG ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

APPLICATION_ID = os.environ.get("APPLICATION_ID")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

def get_role_badge(role: str) -> str:
    if not role:
        return ""
    
    r = str(role).lower().strip()
    
    if r in ['leader', 'commander', 'clan_commander', 'leader_clan']:
        return " 👑 [Líder]"
    elif r in ['executive_officer', 'vice_leader', 'co_leader', 'deputy_commander', 'sub_commander']:
        return " ⚔️ [Vice-Líder]"
    elif r in ['commander_assistant', 'recruiter', 'diplomat', 'quartermaster', 'personnel_officer', 'combat_officer']:
        return " 📜 [Oficial]"
        
    return ""

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

                    now = datetime.now()
                    one_month_ago = now - timedelta(days=30)
                    
                    member_list = []
                    for m_id, m_info in members.items():
                        last_battle_ts = last_battle_dict.get(str(m_id), 0)
                        role = m_info.get('role', '')
                        
                        is_inactive_30d = False
                        if last_battle_ts > 0:
                            dt_last_battle = datetime.fromtimestamp(last_battle_ts)
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
                            'last_battle': last_battle_str,
                            'raw_ts': last_battle_ts,
                            'is_inactive_30d': is_inactive_30d
                        })
                    
                    member_list.sort(key=lambda x: x['raw_ts'], reverse=True)
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
        role_badge = get_role_badge(m['role'])
        
        if m['is_inactive_30d']:
            inativos_30d.append(f"• **{acc_name}**{role_badge} | ⚠️ *Inativo (+30d) - Última: {last_b}*")
        
        if acc_id in mappings:
            d_info = mappings[acc_id]
            em_ambos.append(f"• **{acc_name}**{role_badge} ➔ <@{d_info['discord_id']}> | 🕒 *{last_b}*")
        else:
            apenas_jogo.append(f"• **{acc_name}**{role_badge} | 🕒 *{last_b}*")
            
    now_str = datetime.now().strftime('%d/%m/%Y às %H:%M')
    
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
    now = datetime.now()
    recent_activity = []
    
    for m in in_game_members:
        if m['raw_ts'] > 0:
            dt_battle = datetime.fromtimestamp(m['raw_ts'])
            diff = now - dt_battle
            if diff <= timedelta(hours=2):
                mins = int(diff.total_seconds() / 60)
                time_str = f"há {mins} min" if mins > 0 else "agora mesmo"
                role_badge = get_role_badge(m['role'])
                recent_activity.append(f"🎮 **{m['account_name']}**{role_badge} — *Jogou {time_str}*")

    now_str = now.strftime('%H:%M:%S')
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
    """Envia as notificações de inatividade para o canal configurado de ausentes."""
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
            discord_id = mappings[acc_id]['discord_id']
            msg = f"<@{discord_id}> você está ha um mês sem jogar, por favor para não considerarmos um jogador inativo jogue uma unica batalha no modo REGULAR, obrigado pela atenção"
            await absent_channel.send(msg)
        else:
            unlinked_inactives.append(acc_name)

    if unlinked_inactives:
        list_str = ", ".join(f"**{nick}**" for nick in unlinked_inactives)
        await absent_channel.send(f"⚠️ **Jogadores inativos há +30 dias sem vínculo no Discord:**\n{list_str}\n*(Use `!vincular @Membro Nick` para vinculá-los)*")

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
    print(f"✅ Bot do Clã online como: {bot.user}")
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
    await ctx.send(f"✅ **Clã configurado!** TAG: **[{clean_tag}]**.\nUse `!setausentes #canal` para definir o canal de avisos de ausência.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setausentes(ctx, channel: discord.TextChannel):
    """[Admin] Define o canal onde os avisos de ausência/inatividade serão enviados."""
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
    
    # Envia os avisos para o canal de ausentes
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
async def vincular(ctx, member: discord.Member, *, nickname_jogo: str):
    loading = await ctx.send(f"🔍 Buscando jogador **{nickname_jogo}**...")
    async with aiohttp.ClientSession() as session:
        regions = ["https://api.wotblitz.com", "https://api.wotblitz.eu"]
        acc_id = None
        real_nick = nickname_jogo
        for reg in regions:
            url = f"{reg}/wotb/account/list/?application_id={APPLICATION_ID}&search={nickname_jogo}"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('data'):
                            acc_id = data['data'][0]['account_id']
                            real_nick = data['data'][0]['nickname']
                            break
            except Exception:
                pass
                
        if not acc_id:
            await loading.edit(content=f"❌ Jogador **{nickname_jogo}** não encontrado.")
            return

        set_mapping(acc_id, real_nick, member.id, str(member))
        await loading.edit(content=f"✅ Sucesso! {member.mention} foi vinculado à conta **{real_nick}**.")

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
    lines = [f"• **{info['acc_name']}** ➔ <@{info['discord_id']}>" for acc_id, info in mappings.items()]
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
    
    # Envia os avisos para o canal de ausentes
    await send_inactivity_warning(ctx.guild, in_game_members)

keep_alive()
bot.run(DISCORD_TOKEN)
