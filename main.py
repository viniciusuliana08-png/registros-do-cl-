async def fetch_clan_id_by_tag(tag):
    # Remove colchetes ou espaços que o usuário possa ter digitado por engano
    clean_tag = tag.strip().replace("[", "").replace("]", "")
    
    # Substitui traços/hífens especiais por traço padrão
    clean_tag = clean_tag.replace("–", "-").replace("—", "-")

    url = "https://api.wotblitz.com/wotb/clans/list/"
    params = {
        "application_id": WARGAMING_APP_ID,
        "search": clean_tag
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("status") == "ok" and data.get("data"):
                    # Procura combinação exata
                    for clan in data["data"]:
                        clan_tag_api = clan.get("tag", "").strip()
                        if clan_tag_api.lower() == clean_tag.lower():
                            return clan.get("clan_id"), clan_tag_api
                    
                    # Se não achou exato, pega o primeiro retornado
                    first_clan = data["data"][0]
                    return first_clan.get("clan_id"), first_clan.get("tag")
            return None, None
