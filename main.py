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
    
    # Mapeamento de membros: Account_ID <-> Discord ID
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clan_mappings (
            account_id INTEGER PRIMARY KEY,
            account_name TEXT,
            discord_id INTEGER,
            discord_name TEXT
        )
    ''')
    
    # Configuração de clã por servidor do Discord: Guild_ID <-> Clan_Tag
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS server_clans (
            guild_id INTEGER PRIMARY KEY,
            clan_tag TEXT
        )
    ''')
    
    conn.commit()
    conn.close()

def set_server_clan(guild_id: int, clan_tag: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO server_clans (guild_id, clan_tag)
        VALUES (?, ?)
    ''', (guild_id, clan_tag.upper()))
    conn.commit()
    conn.close()

def get_server_clan(guild_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT clan_tag FROM server_clans WHERE guild_id = ?', (guild_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

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
    """Busca o clã pela TAG exata nas regiões e retorna os membros."""
    regions = [
        "https://api.wotblitz.com",
        "https://api.wotblitz.eu",
        "https://api.wotblitz.asia"
    ]
    async with aiohttp.ClientSession() as session:
        clan_id = None
        base_url = None
        exact_tag = target_tag.upper()
        
        # Procura o clã com a TAG EXATA
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
            except Exception as e:
                print(f"Erro na busca do clã em {reg}: {e}")
                
        if not clan_id:
            return None, []
            
        # Puxa informações detalhadas do clã
        clan_info_url = f"{base_url}/wotb/clans/info/?application_id={APPLICATION_ID}&clan_id={clan_id}&extra=members"
        try:
            async with session.get(clan_info_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    clan_data = data.get('data', {}).get(str(clan_id), {})
                    members = clan_data.get('members', {})
                    
                    member_list = []
                    for m in members.values():
                        last_battle_ts = m.get('last_battle_time', 0)
                        if last_battle_ts > 0:
                            last_battle_str = datetime.fromtimestamp(last_battle_ts).strftime('%d/%m/%Y %H:%M')
                        else:
                            last_battle_str = "Sem registros"
                            
                        member_list.append({
                            'account_id': m['account_id'],
                            'account_name': m['account_name'],
                            'last_battle': last_battle_str,
                            'raw_ts': last_battle_ts
                        })
                    
                    # Ordena do mais recente para o mais inativo
                    member_list.sort(key=lambda x: x['raw_ts'], reverse=True)
                    return clan_data.get('tag'), member_list
        except Exception as e:
            print(f"Erro ao carregar membros do clã: {e}")
            
    return None, []

@bot.event
async def on_ready():
    print(f"✅ Bot do Clã online como: {bot.user}")

# --- 4. COMANDOS DO BOT ---

@bot.command()
@commands.has_permissions(administrator=True)
async def setcla(ctx, tag: str):
    """[Admin] Define a TAG do clã para este servidor."""
    clean_tag = tag.strip().upper()
    set_server_clan(ctx.guild.id, clean_tag)
    await ctx.send(f"✅ **Clã configurado com sucesso!** A TAG definida para este servidor é **[{clean_tag}]**.\nUse `!membros` para visualizar a lista.")

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
    """[Admin] Remove a vinculação de um membro."""
    remove_mapping(member.id)
    await ctx.send(f"🗑️ Vinculação do membro {member.mention} foi removida do banco de dados.")

@bot.command()
async def membros(ctx):
    """Exibe a lista organizada do clã configurado no servidor."""
    clan_tag = get_server_clan(ctx.guild.id)
    
    if not clan_tag:
        await ctx.send("⚠️ NENHUM CLÃ CONFIGURADO!\nPor favor, defina a TAG do clã do servidor usando o comando: `!setcla SUA_TAG` (Ex: `!setcla MR-S`)")
        return

    loading_msg = await ctx.send(f"🔄 Sincronizando dados do clã **[{clan_tag}]** com a Wargaming...")
    
    try:
        tag, in_game_members = await fetch_clan_members(clan_tag)
    except Exception as e:
        await loading_msg.edit(content=f"❌ Erro ao conectar à API: {e}")
        return
    
    if not in_game_members:
        await loading_msg.edit(content=f"❌ Não foi possível encontrar o clã com a TAG **[{clan_tag}]** na Wargaming.")
        return

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
    
    embed.set_footer(text="Lista ordenada pelos jogadores com atividade recente.")
    await loading_msg.edit(content="", embed=embed)

keep_alive()
bot.run(DISCORD_TOKEN)
