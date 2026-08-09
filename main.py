import os
import asyncio
import aiohttp
import discord
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

# ------------------------------------------------------------------------------
# CONFIGURAÇÕES E VARIÁVEIS DE AMBIENTE
# ------------------------------------------------------------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "SEU_TOKEN_DO_DISCORD_AQUI")
WARGAMING_APP_ID = os.getenv("WARGAMING_APP_ID", "SEU_APP_ID_WARGAMING_AQUI")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "wotb_bot_db"

# Região da API Wargaming ('com', 'eu', 'asia')
WG_REGION = os.getenv("WG_REGION", "com")
WG_API_URL = f"https://api.wotblitz.{WG_REGION}"

# ------------------------------------------------------------------------------
# INICIALIZAÇÃO DO BOT E BANCO DE DADOS
# ------------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Necessário para buscar membros do servidor

bot = commands.Bot(command_prefix="!", intents=intents)

mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client[DB_NAME]
config_collection = db["server_config"]
mappings_collection = db["player_mappings"]

# ------------------------------------------------------------------------------
# FUNÇÕES DE BANCO DE DADOS E API
# ------------------------------------------------------------------------------
async def get_server_config(guild_id: int):
    """Obtém a configuração do servidor (como a tag do clã)."""
    return await config_collection.find_one({"guild_id": guild_id})

async def set_server_config(guild_id: int, clan_tag: str):
    """Salva ou atualiza a tag do clã associada ao servidor."""
    await config_collection.update_one(
        {"guild_id": guild_id},
        {"$set": {"guild_id": guild_id, "clan_tag": clan_tag.upper()}},
        upsert=True
    )

async def set_mapping(account_id: int, account_name: str, discord_id: int, discord_name: str):
    """Mapeia o ID/Nick do jogador no WOTB com o ID/Nome do usuário no Discord."""
    await mappings_collection.update_one(
        {"account_id": account_id},
        {
            "$set": {
                "account_id": account_id,
                "account_name": account_name,
                "discord_id": discord_id,
                "discord_name": discord_name
            }
        },
        upsert=True
    )

async def fetch_clan_members(clan_tag: str):
    """Busca os membros de um clã no WOTB usando a API da Wargaming."""
    async with aiohttp.ClientSession() as session:
        # 1. Busca a ID do clã pela Tag
        search_url = f"{WG_API_URL}/wotb/clans/list/?application_id={WARGAMING_APP_ID}&search={clan_tag}"
        async with session.get(search_url) as resp:
            data = await resp.json()
            if data.get("status") != "ok" or not data.get("data"):
                return clan_tag, []
            
            clan_info = None
            for clan in data["data"]:
                if clan["tag"].lower() == clan_tag.lower():
                    clan_info = clan
                    break
            
            if not clan_info:
                return clan_tag, []
            
            clan_id = clan_info["clan_id"]

        # 2. Detalhes do clã para obter os IDs e nomes dos membros
        info_url = f"{WG_API_URL}/wotb/clans/info/?application_id={WARGAMING_APP_ID}&clan_id={clan_id}&extra=members"
        async with session.get(info_url) as resp:
            data = await resp.json()
            if data.get("status") != "ok" or str(clan_id) not in data.get("data", {}):
                return clan_tag, []
            
            members_data = data["data"][str(clan_id)].get("members", {})
            members_list = []
            for acc_id, details in members_data.items():
                members_list.append({
                    "account_id": int(acc_id),
                    "account_name": details["account_name"]
                })
            
            return clan_info["tag"], members_list

# ------------------------------------------------------------------------------
# EVENTOS DO BOT
# ------------------------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"🤖 Bot conectado com sucesso como {bot.user} (ID: {bot.user.id})")
    print("------")

# ------------------------------------------------------------------------------
# COMANDOS
# ------------------------------------------------------------------------------
@bot.command()
async def setcla(ctx, tag: str = None):
    """Define a tag do clã do WOTB para este servidor."""
    if not tag:
        await ctx.send("⚠️ Por favor, informe a tag do clã. Exemplo: `!setcla TAG`")
        return
    
    await set_server_config(ctx.guild.id, tag)
    await ctx.send(f"✅ Clã configurado com sucesso como **[{tag.upper()}]** para este servidor!")


@bot.command()
async def vincular(ctx, *, nick_jogo: str = None):
    """Vincula um jogador do WOTB buscando no Discord e confirmando com Sim/Não."""
    config = await get_server_config(ctx.guild.id)
    if not config or not config.get('clan_tag'):
        await ctx.send("⚠️ Nenhum clã configurado neste servidor. Use `!setcla TAG` primeiro.")
        return

    def check_author(m):
        return m.author == ctx.author and m.channel == ctx.channel

    # Se não informou o nick no comando, solicita via chat
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

    # Busca membro correspondente pelos nomes ou apelidos no Discord
    target_member = discord.utils.find(
        lambda m: real_game_nick.lower() in m.name.lower() or (m.nick and real_game_nick.lower() in m.nick.lower()),
        ctx.guild.members
    )

    # Caso encontre um candidato no Discord, confirma com Sim/Não
    if target_member:
        await ctx.send(
            f"🎯 Encontrado no jogo: **{real_game_nick}**!\n"
            f"💬 A conta do Discord é {target_member.mention}? *(Responda com **sim** ou **não** em 30s)*"
        )
        try:
            msg_confirm = await bot.wait_for('message', check=check_author, timeout=30.0)
            resposta = msg_confirm.content.strip().lower()

            if resposta not in ['sim', 's', 'yes', 'y']:
                target_member = None  # Respondeu 'não', reseta para pedir o @membro abaixo
        except asyncio.TimeoutError:
            await ctx.send("⏰ **Tempo esgotado!** Vinculação cancelada.")
            return

    # Se não encontrou de primeira OU se a resposta acima foi "não", solicita o @username
    if not target_member:
        await ctx.send("💬 Por favor, mencione o membro correto do Discord (ex: `@username` ou digite `eu` se for você):")
        try:
            msg_user = await bot.wait_for('message', check=check_author, timeout=30.0)
            content = msg_user.content.strip()

            if msg_user.mentions:
                target_member = msg_user.mentions[0]
            elif content.lower() in ['eu', 'me', 'minha', 'mim']:
                target_member = ctx.author
            else:
                target_member = discord.utils.find(
                    lambda m: content.lower() in m.name.lower() or (m.nick and content.lower() in m.nick.lower()),
                    ctx.guild.members
                )

            if not target_member:
                await ctx.send("❌ **Não encontrei esse membro no Discord.** Vinculação cancelada.")
                return

        except asyncio.TimeoutError:
            await ctx.send("⏰ **Tempo esgotado!** Vinculação cancelada.")
            return

    # Salva o vínculo no MongoDB
    await set_mapping(acc_id, real_game_nick, target_member.id, str(target_member))
    await ctx.send(f"🎉 **Vinculação realizada com sucesso!**\n🎮 **Jogo:** `{real_game_nick}` ➔ 💬 **Discord:** {target_member.mention}")

# ------------------------------------------------------------------------------
# EXECUÇÃO DO BOT
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
