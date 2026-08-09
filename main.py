# Configuração de variáveis com validação
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WARGAMING_APP_ID = os.getenv("WARGAMING_APP_ID", "demo") # Usa "demo" como fallback se faltar
MONGO_URI = os.getenv("MONGO_URI")

async def fetch_clan_id_by_tag(tag):
    clean_tag = tag.strip().replace("[", "").replace("]", "").replace("–", "-").replace("—", "-")

    app_id = WARGAMING_APP_ID or "demo"
    url = "https://api.wotblitz.com/wotb/clans/list/"
    params = {
        "application_id": str(app_id),
        "search": str(clean_tag)
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("status") == "ok" and data.get("data"):
                    for clan in data["data"]:
                        clan_tag_api = clan.get("tag", "").strip()
                        if clan_tag_api.lower() == clean_tag.lower():
                            return clan.get("clan_id"), clan_tag_api
                    
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

    app_id = WARGAMING_APP_ID or "demo"
    url = "https://api.wotblitz.com/wotb/clans/info/"
    params = {
        "application_id": str(app_id),
        "clan_id": str(clan_id),
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
