import discord
from discord.ext import tasks, commands
import requests
import os
import asyncio
import json

# --- CONFIGURATION ---
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = 1484325069860769953
API_URL = "https://civiweb-api-prd.azurewebsites.net/api/Offers/search"
DB_FILE = "seen_jobs.txt"
CONFIG_FILE = "bot_config.json"
MAPPING_FILE = "mapping.json"

with open(MAPPING_FILE, 'r', encoding='utf-8') as f:
    mapping = json.load(f)

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            try:
                data = json.load(f)
                for key in ["geographicZones", "specializationsIds", "missionsDurations"]:
                    if not data.get(key) or data[key] == [""] or data[key] == ["ALL"]:
                        data[key] = []
                return data
            except: pass
    return {"limit": 50, "geographicZones": [], "specializationsIds": [], "missionsDurations": [], "countryalert": None}

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f)

# --- INTERFACE DISCORD ---
class ConfigView(discord.ui.View):
    def __init__(self, bot_instance, user_ctx=None):
        super().__init__(timeout=None)
        self.bot = bot_instance
        self.ctx = user_ctx
        self.temp_payload = self.bot.current_payload.copy()
        self._set_defaults()

    def _set_defaults(self):
        zones = self.temp_payload.get("geographicZones", [])
        for opt in self.select_zone.options:
            opt.default = (opt.value in zones) if zones else (opt.value == "ALL")
        specs = self.temp_payload.get("specializationsIds", [])
        for opt in self.select_spec.options:
            opt.default = (opt.value in specs) if specs else (opt.value == "ALL")

    @discord.ui.select(
        placeholder="1️⃣ Zone géographique...",
        options=[discord.SelectOption(label="🌍 TOUTES LES ZONES", value="ALL")] + 
                [discord.SelectOption(label=k, value=v) for k, v in mapping["ZONES"].items()],
        row=0
    )
    async def select_zone(self, interaction: discord.Interaction, select: discord.ui.Select):
        val = select.values[0]
        self.temp_payload["geographicZones"] = [] if val == "ALL" else [val]
        for opt in select.options: opt.default = (opt.value == val)
        await interaction.response.edit_message(view=self)

    @discord.ui.select(
        placeholder="2️⃣ Spécialisation...",
        options=[discord.SelectOption(label="💼 TOUTES LES SPÉCIALITÉS", value="ALL")] + 
                [discord.SelectOption(label=k, value=v) for k, v in mapping["SPECIALIZATIONS"].items()],
        row=1
    )
    async def select_spec(self, interaction: discord.Interaction, select: discord.ui.Select):
        val = select.values[0]
        self.temp_payload["specializationsIds"] = [] if val == "ALL" else [val]
        for opt in select.options: opt.default = (opt.value == val)
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="💾 Enregistrer et passer aux alertes", style=discord.ButtonStyle.green, row=3)
    async def save_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.bot.current_payload.update(self.temp_payload)
        save_config(self.bot.current_payload)
        
        zone_id = self.bot.current_payload.get("geographicZones", [None])[0]
        
        if not zone_id:
            await interaction.response.send_message("✅ Configuration globale enregistrée. Lancement du scan...", ephemeral=True)
            await interaction.message.delete()
            await self.bot.check_jobs()
            self.stop()
            return

        all_pays = mapping.get("PAYS_PAR_ZONE", {}).get(zone_id, {})
        if not all_pays:
             await interaction.response.send_message("✅ Filtres enregistrés. Lancement du scan...", ephemeral=True)
             await interaction.message.delete()
             await self.bot.check_jobs()
             self.stop()
             return

        await interaction.response.defer()
        new_view = AutoAlerteView(self.bot, self.ctx, all_pays)
        await interaction.edit_original_response(content="🔔 **Filtres enregistrés !**\nSouhaitez-vous activer une alerte rouge pour un pays ?", view=new_view)
        self.stop()

class AutoAlerteView(discord.ui.View):
    def __init__(self, bot_ref, parent_ctx, pays_dict):
        super().__init__(timeout=60)
        self.bot_ref = bot_ref
        self.ctx = parent_ctx
        sorted_pays = sorted(pays_dict.items())
        
        if len(sorted_pays) > 25:
            mid = len(sorted_pays) // 2
            self.add_item(AutoCountrySelect(sorted_pays[:mid], "📍 Pays (A - L)..."))
            self.add_item(AutoCountrySelect(sorted_pays[mid:], "📍 Pays (M - Z)..."))
        else:
            self.add_item(AutoCountrySelect(sorted_pays, "📍 Choisir un pays..."))

    @discord.ui.button(label="❌ Ne pas activer d'alerte", style=discord.ButtonStyle.gray, row=4)
    async def no_alert(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.bot_ref.current_payload["countryalert"] = None
        save_config(self.bot_ref.current_payload)
        await interaction.response.send_message("⚪ Alerte désactivée. Lancement du scan...", ephemeral=True)
        await interaction.message.delete()
        await self.bot_ref.check_jobs()
        self.stop()

class AutoCountrySelect(discord.ui.Select):
    def __init__(self, pays_list, placeholder):
        options = [discord.SelectOption(label=nom, value=code) for nom, code in pays_list]
        super().__init__(placeholder=placeholder, options=options)

    async def callback(self, interaction: discord.Interaction):
        view: AutoAlerteView = self.view
        view.bot_ref.current_payload["countryalert"] = self.values[0]
        save_config(view.bot_ref.current_payload)
        await interaction.response.send_message(f"🚩 Alerte activée pour `{self.values[0]}`. Scan...", ephemeral=True)
        await interaction.message.delete()
        await view.bot_ref.check_jobs()
        view.stop()

# --- LE BOT ---
class JobBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.current_payload = load_config()

    async def setup_hook(self):
        self.check_jobs.start()

    @tasks.loop(minutes=30)
    async def check_jobs(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        if not channel: return
        
        print("🔍 Scan en cours...")
        try:
            api_payload = self.current_payload.copy()
            api_payload.pop('countryalert', None) 

            for key in ['specializationsIds', 'missionsDurations', 'geographicZones']:
                if api_payload.get(key) == [''] or api_payload.get(key) is None:
                    api_payload[key] = []

            response = requests.post(API_URL, json=api_payload, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                offers = data.get('result', [])
                current_api_ids = {str(job['id']) for job in offers}
                
                seen_data = {}
                if os.path.exists(DB_FILE):
                    with open(DB_FILE, 'r') as f:
                        for line in f:
                            if ':' in line:
                                j_id, m_id = line.strip().split(':')
                                seen_data[j_id] = int(m_id)

                for job_id, msg_id in list(seen_data.items()):
                    if job_id not in current_api_ids:
                        try:
                            msg = await channel.fetch_message(msg_id)
                            await msg.delete()
                        except: pass
                        del seen_data[job_id]

                alert_country = self.current_payload.get("countryalert")
                for job in reversed(offers):
                    job_id = str(job['id'])
                    if job_id not in seen_data:
                        # --- LOGIQUE ALERTE ---
                        is_alert = False
                        if alert_country:
                            c_name = str(job.get('countryName', '')).upper()
                            c_code = str(job.get('countryCode', '')).upper()
                            target = str(alert_country).upper()
                            if target in c_name or target == c_code:
                                is_alert = True

                        color = discord.Color.red() if is_alert else discord.Color.blue()
                        prefix = "🚨 " if is_alert else "🌎 "
                        
                        # --- PREPARATION DES DONNÉES ---
                        ville = job.get('cityName', 'Non spécifiée').upper()
                        entreprise = job.get('organizationName', 'Inconnue').upper()
                        desc = job.get('missionDescription', 'Pas de description.')
                        if len(desc) > 250: desc = desc[:247] + "..."
                        
                        indemnite = job.get('indemnite', '0')
                        indemnite_str = f"{indemnite}€" if isinstance(indemnite, str) else f"{indemnite:,.2f}€".replace(",", " ")

                        # --- CONSTRUCTION EMBED ---
                        embed = discord.Embed(
                            title=f"{prefix}{job['countryName'].upper()} : {job['missionTitle']}", 
                            url=f"https://mon-vie-via.businessfrance.fr/offres/{job_id}",
                            description=f"**Présentation de la société :**\n\n{desc}",
                            color=color
                        )
                        embed.add_field(name="🏛️ Entreprise", value=f"**{entreprise}**", inline=True)
                        embed.add_field(name="📍 Ville", value=f"**{ville}**", inline=True)
                        embed.add_field(name="💰 Indemnité", value=f"**{indemnite_str} / mois**", inline=False)

                        msg = await channel.send(embed=embed)
                        seen_data[job_id] = msg.id
                        await asyncio.sleep(1)

                with open(DB_FILE, 'w') as f:
                    for j_id, m_id in seen_data.items(): f.write(f"{j_id}:{m_id}\n")
        except Exception as e:
            print(f"💥 Erreur lors du scan : {e}")

bot = JobBot()

@bot.event
async def on_ready():
    print(f'--- {bot.user.name} prêt ---')
    
    # On récupère le salon
    channel = bot.get_channel(CHANNEL_ID)
    
    # Si get_channel échoue (cache vide), on tente fetch_channel
    if not channel:
        try:
            channel = await bot.fetch_channel(CHANNEL_ID)
        except Exception as e:
            print(f"❌ Impossible de trouver le salon {CHANNEL_ID} : {e}")
            return

    # On prépare la vue
    view = ConfigView(bot)
    
    # On envoie le message de bienvenue
    try:
        await channel.send("👋 **Bienvenue !**\nLe bot est prêt. Réglez vos filtres ci-dessous pour commencer à recevoir les offres :", view=view)
        print("✅ Message de bienvenue envoyé avec succès.")
    except Exception as e:
        print(f"❌ Erreur lors de l'envoi du message : {e}")
@bot.command()
async def config(ctx):
    view = ConfigView(bot, ctx)
    await ctx.send("⚙️ **Configuration**", view=view)

@bot.command()
async def force_query(ctx):
    await ctx.send("🔄 **Scan forcé...**")
    await bot.check_jobs()

bot.run(TOKEN)