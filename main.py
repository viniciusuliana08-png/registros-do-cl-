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
    return "Bot do Clã MR-S online!"

def run():
    port = int(os.environ.get("PORT", 10002))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- 2. BANCO DE DADOS DE MAPEAMENTO DO CLÃ ---
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
    conn.commit()
    conn.close()

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
CLAN_SEARCH = os.environ.get("CLAN_TAG", "MR-S")

async def fetch_clan_members():
    regions = [
        "https://api.wotblitz.com",
        "https://api.wotblitz.eu",
        "https://api.wotblitz.asia"
    ]
    async with aiohttp.ClientSession() as session:
        clan_id = None
        base_url = None
        
        for reg in regions:
            url = f"{reg}/wotb/clans/list/?application_id={APPLICATION_ID}&search={CLAN_SEARCH}"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('data'):
                            clan_id = data['data'][0]['clan_id']
                            base_url = reg
                            break
            except Exception as e:
                print(f"Erro na busca do clã: {e}")
                
        if not clan_id:
            return None, []
            
        clan_info_url = f"{base_url}/wotb/clans/info/?application_id={APPLICATION_ID}&clan_id={clan_id}&extra=members"
        try:
            async with session.get(clan_info_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
                    
                    member_list.sort(key=lambda x: x['raw_ts'], reverse=True)
                    return clan_data.get('tag'), member_list
        except Exception as e:
            print(f"Erro ao carregar membros do clã: {e}")
            
    return None, []

@tasks.loop(hours=1)
async def check_clan_changes():
    print("🔄 Verificando membros do clã no jogo...")
    tag, members = await fetch_clan_members()
    if members:
        print(f"Clã [{tag}] possui {len(members)} membros no momento.")

@bot.event
async def on_ready():
    print(f"✅ Bot do Clã online como: {bot.user}")
    check_clan_changes.start()

@bot.command()
@commands.has_permissions(administrator=True)
async def vincular(ctx, member: discord.Member, *, nickname_jogo: str):
    loading = await ctx.send(f"🔍 Verificando jogador **{nickname_jogo}**...")
    
    async with aiohttp.ClientSession() as session:
        regions = ["https://api.wotblitz.com", "https://api.wotblitz.eu"]
        acc_id = None
        real_nick = nickname_jogo
        
        for reg in regions:
            url = f"{reg}/wotb/account/list/?application_id={APPLICATION_ID}&search={nickname_jogo}"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
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
    remove_mapping(member.id)
    await ctx.send(f"🗑️ Vinculação do membro {member.mention} foi removida do banco de dados.")

@bot.command()
async def membros(ctx):
    loading_msg = await ctx.send("🔄 Sincronizando dados com a Wargaming...")
    
    tag, in_game_members = await fetch_clan_members()
    
    if not in_game_members:
        await loading_msg.edit(content="❌ Não foi possível obter os dados do clã no momento.")
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
            apenas_jogo.append(f"• **{acc_name}** | 🕒 *Última batalha: {last_b}*")
            
    embed = discord.Embed(
        title=f"📋 Organização do Clã [{tag}]",
        description=f"Total de Membros no Jogo: **{len(in_game_members)}**\nVinculados no Discord: **{len(em_ambos)}** | Pendentes: **{len(apenas_jogo)}**",
        color=0x3498DB
    )
    
    txt_ambos = "\n".join(em_ambos) if em_ambos else "Nenhum membro vinculado ainda."
    if len(txt_ambos) > 1024:
        txt_ambos = txt_ambos[:1000] + "\n... (lista truncada por tamanho)"
    embed.add_field(name="🟢 Presentes no Jogo E no Discord", value=txt_ambos, inline=False)
    
    txt_jogo = "\n".join(apenas_jogo) if apenas_jogo else "Todos os membros estão no Discord! 🎉"
    if len(txt_jogo) > 1024:
        txt_jogo = txt_jogo[:1000] + "\n... (lista truncada por tamanho)"
    embed.add_field(name="🔴 Apenas no Clã do Jogo (Falta vincular/entrar)", value=txt_jogo, inline=False)
    
    embed.set_footer(text="Lista ordenada pelos jogadores com atividade recente.")
    await loading_msg.edit(content="", embed=embed)

keep_alive()
bot.run(DISCORD_TOKEN)
