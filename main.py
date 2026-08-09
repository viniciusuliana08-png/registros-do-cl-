import os
import sqlite3
import aiohttp
import discord
from discord.ext import commands, tasks
from flask import Flask
from threading import Thread
from datetime import datetime

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

# --- 2. BANCO DE DADOS DE MAPEAMENTO E CONFIGURAÇÕES DE CLÃ ---
DB_NAME = "clan_manager.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Tabela de Vinculação: Account_ID <-> Discord ID
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clan_mappings (
            account_id INTEGER PRIMARY KEY,
            account_name TEXT,
            discord_id INTEGER,
            discord_name TEXT
        )
    ''')
    
    # Configurações do servidor: Guild_ID <-> Clan_Tag, Channel_ID, Message_ID, Last_Members
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS server_clans (
            guild_id INTEGER PRIMARY KEY,
            clan_tag TEXT,
            channel_id INTEGER,
            panel_message_id INTEGER,
            last_members TEXT
        )
    ''')
    
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
    cursor.execute('SELECT clan_tag, channel_id, panel_message_id, last_members FROM server_clans WHERE guild_id = ?', (guild_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'clan_tag': row[0],
            'channel_id': row[1],
            'panel_message_id': row[2],
            'last_members': row[3]
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

# --- 3. CONFIGURAÇÕES DO BOT DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

APPLICATION_ID = os.environ.get("APPLICATION_ID")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

async def fetch_clan_members(target_tag: str):
    """Busca o clã pela TAG exata, e depois busca a última batalha de cada membro."""
    regions = [
        "https://api.wotblitz.com",
        "https://api.wotblitz.eu",
        "https://api.wotblitz.asia"
    ]
    async with aiohttp.ClientSession() as session:
        clan_id = None
        base_url = None
        exact_tag = target_tag.upper()
        
        # 1. Procura o clã com a TAG EXATA
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
            
        # 2. Puxa os IDs de todos os membros do clã
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
                    
                    # 3. Puxa last_battle_time de todos os membros
                    acc_info_url = f"{base_url}/wotb/account/info/?application_id={APPLICATION_ID}&account_id={acc_ids_str}&fields=last_battle_time"
                    
                    last_battle_dict = {}
                    async with session.get(acc_info_url, timeout=aiohttp.ClientTimeout(total=8)) as resp2:
                        if resp2.status == 200:
                            acc_data = await resp2.json()
                            player_stats = acc_data.get('data', {})
                            for pid, pinfo in player_stats.items():
                                if pinfo and 'last_battle_time' in pinfo:
                                    last_battle_dict[str(pid)] = pinfo['last_battle_time']

                    member_list = []
                    for m_id, m_info in members.items():
                        last_battle_ts = last_battle_dict.get(str(m_id), 0)
                        
                        if last_battle_ts > 0:
                            last_battle_str = datetime.fromtimestamp(last_battle_ts).strftime('%d/%m/%Y %H:%M')
                        else:
                            last_battle_str = "Sem registros"
                            
                        member_list.append({
                            'account_id': m_info['account_id'],
                            'account_name': m_info['account_name'],
                            'last_battle': last_battle_str,
                            'raw_ts': last_battle_ts
                        })
                    
                    member_list.sort(key=lambda x: x['raw_ts'], reverse=True)
                    return clan_data.get('tag'), member_list
        except Exception as e:
            print(f"Erro ao carregar membros do clã: {e}")
            
    return None, []

def build_clan_embed(tag, in_game_members):
    """Constrói o embed formatado com os membros organizados."""
    mappings = get_all_mappings()
    
    em_ambos = []
    apenas_jogo = []
    
    for m in in_game_members:
        acc_id = m['account_id']
        acc_name = m['account_name']
        last_b = m['last_battle']
        
        if acc_id in mappings:
            d_info = mappings[acc_id]
            em_ambos.append(f"• **{acc_name}** ➔ <@{d_info['discord_id']}> | 🕒 *{last_b}*")
        else:
            apenas_jogo.append(f"• **{acc_name}** | 🕒 *{last_b}*")
            
    now_str = datetime.now().strftime('%d/%m/%Y às %H:%M')
    
    embed = discord.Embed(
        title=f"📋 Organização do Clã [{tag}]",
        description=f"Total no Jogo: **{len(in_game_members)}** | Vinculados: **{len(em_ambos)}** | Pendentes: **{len(apenas_jogo)}**",
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
    
    embed.set_footer(text=f"Última atualização: {now_str} • Atualiza automaticamente a cada 1 hora.")
    return embed

# --- 4. TAREFA AUTOMÁTICA DE 1 EM 1 HORA ---
@tasks.loop(hours=1)
async def auto_update_job():
    """Rotina que roda de hora em hora atualizando o painel e notificando alterações de membros."""
    guild_ids = get_all_configured_servers()
    
    for g_id in guild_ids:
        config = get_server_config(g_id)
        if not config or not config['clan_tag']:
            continue
            
        clan_tag = config['clan_tag']
        tag, in_game_members = await fetch_clan_members(clan_tag)
        
        if not in_game_members:
            continue
            
        # 1. Checa entradas e saídas de jogadores
        current_ids = {m['account_id']: m['account_name'] for m in in_game_members}
        last_ids_str = config.get('last_members')
        
        channel = None
        if config.get('channel_id'):
            channel = bot.get_channel(config['channel_id'])
            
        if last_ids_str and channel:
            old_ids = set(map(int, last_ids_str.split(','))) if last_ids_str else set()
            new_ids = set(current_ids.keys())
            
            entraram = new_ids - old_ids
            sairam = old_ids - new_ids
            
            for e_id in entraram:
                await channel.send(f"🎉 **NOVO MEMBRO:** O jogador **{current_ids[e_id]}** entrou no clã **[{tag}]** no jogo!")
                
            for s_id in sairam:
                await channel.send(f"🚪 **MEMBRO SAIU:** Um jogador (ID: `{s_id}`) deixou o clã **[{tag}]**.")

        # Atualiza a lista gravada no banco
        new_ids_str = ",".join(map(str, current_ids.keys()))
        update_last_members(g_id, new_ids_str)
        
        # 2. Atualiza a mensagem do Painel Fixo (se existir)
        if config.get('channel_id') and config.get('panel_message_id') and channel:
            try:
                panel_msg = await channel.fetch_message(config['panel_message_id'])
                embed = build_clan_embed(tag, in_game_members)
                await panel_msg.edit(embed=embed)
            except Exception as e:
                print(f"Erro ao editar o painel do servidor {g_id}: {e}")

@auto_update_job.before_loop
async def before_auto_update():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    print(f"✅ Bot do Clã online como: {bot.user}")
    if not auto_update_job.is_running():
        auto_update_job.start()

# --- 5. COMANDOS DO BOT ---

@bot.command()
@commands.has_permissions(administrator=True)
async def setcla(ctx, tag: str):
    """[Admin] Define a TAG do clã para este servidor."""
    clean_tag = tag.strip().upper()
    set_server_clan(ctx.guild.id, clean_tag)
    await ctx.send(f"✅ **Clã configurado com sucesso!** A TAG definida para este servidor é **[{clean_tag}]**.\nUse `!painel` no canal desejado para fixar a lista auto-atualizável.")

@bot.command()
@commands.has_permissions(administrator=True)
async def painel(ctx):
    """[Admin] Cria o painel fixo de membros que se atualiza sozinho a cada 1 hora."""
    config = get_server_config(ctx.guild.id)
    if not config or not config['clan_tag']:
        await ctx.send("⚠️ NENHUM CLÃ CONFIGURADO!\nPrimeiro defina a TAG com: `!setcla SUA_TAG`")
        return

    clan_tag = config['clan_tag']
    loading_msg = await ctx.send(f"🔄 Gerando painel fixo para o clã **[{clan_tag}]**...")
    
    tag, in_game_members = await fetch_clan_members(clan_tag)
    if not in_game_members:
        await loading_msg.edit(content=f"❌ Não foi possível carregar os dados do clã **[{clan_tag}]**.")
        return
        
    embed = build_clan_embed(tag, in_game_members)
    await loading_msg.delete()
    
    # Envia a mensagem do painel
    panel_msg = await ctx.send(embed=embed)
    
    # Salva o canal e o ID da mensagem para poder editar depois
    set_server_panel(ctx.guild.id, ctx.channel.id, panel_msg.id)
    
    # Salva membros atuais para poder alertar quem entra/sai
    current_ids_str = ",".join(str(m['account_id']) for m in in_game_members)
    update_last_members(ctx.guild.id, current_ids_str)
    
    await ctx.send("📌 **Painel fixado com sucesso neste canal!** Ele será atualizado automaticamente a cada 1 hora.", delete_after=10)

@bot.command()
@commands.has_permissions(administrator=True)
async def vincular(ctx, member: discord.Member, *, nickname_jogo: str):
    """[Admin] Vincula um membro do Discord a uma conta do jogo."""
    loading = await ctx.send(f"🔍 Verificando jogador **{nickname_jogo}**...")
    
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
            await loading.edit(content=f"❌ Jogador **{nickname_jogo}** não foi encontrado na Wargaming.")
            return

        set_mapping(acc_id, real_nick, member.id, str(member))
        await loading.edit(content=f"✅ **Sucesso!** O perfil {member.mention} foi vinculado à conta **{real_nick}**.")

@bot.command()
@commands.has_permissions(administrator=True)
async def desvincular(ctx, member: discord.Member):
    """[Admin] Remove a vinculação de um membro do Discord."""
    remove_mapping(member.id)
    await ctx.send(f"🗑️ Vinculação do membro {member.mention} foi removida com sucesso!")

@bot.command()
@commands.has_permissions(administrator=True)
async def vinculados(ctx):
    """[Admin] Exibe a lista de todas as contas vinculadas cadastradas no banco."""
    mappings = get_all_mappings()
    if not mappings:
        await ctx.send("ℹ️ Não há nenhum membro vinculado no banco de dados ainda.")
        return
        
    lines = []
    for acc_id, info in mappings.items():
        lines.append(f"• **{info['acc_name']}** (ID: `{acc_id}`) ➔ <@{info['discord_id']}>")
        
    embed = discord.Embed(
        title="🔗 Lista de Membros Vinculados",
        description="\n".join(lines),
        color=0x2ECC71
    )
    await ctx.send(embed=embed)

@bot.command()
async def membros(ctx):
    """Exibe a lista organizada do clã em uma mensagem no chat."""
    config = get_server_config(ctx.guild.id)
    if not config or not config['clan_tag']:
        await ctx.send("⚠️ NENHUM CLÃ CONFIGURADO!\nDefina com: `!setcla SUA_TAG`")
        return

    clan_tag = config['clan_tag']
    loading_msg = await ctx.send(f"🔄 Sincronizando dados do clã **[{clan_tag}]**...")
    
    tag, in_game_members = await fetch_clan_members(clan_tag)
    if not in_game_members:
        await loading_msg.edit(content=f"❌ Não foi possível encontrar o clã **[{clan_tag}]**.")
        return

    embed = build_clan_embed(tag, in_game_members)
    await loading_msg.edit(content="", embed=embed)

keep_alive()
bot.run(DISCORD_TOKEN)
