import discord
from discord.ext import tasks, commands
import requests
import os
import asyncio

# --- CONFIGURATION ---
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = 1484325069860769953
API_URL = "https://civiweb-api-prd.azurewebsites.net/api/Offers/search"
DB_FILE = "seen_jobs.txt"

# On augmente un peu la limite pour le scan de nettoyage (ex: 50 ou 100)
# pour éviter de supprimer des offres valides mais un peu anciennes
PAYLOAD = {
    "limit": 50, 
    "skip": 0,
    "query": None,
    "geographicZones": ["3"],
    "teletravail": ["0"],
    "porteEnv": ["0"],
    "activitySectorId": [],
    "missionsTypesIds": [],
    "missionsDurations": [],
    "countriesIds": [],
    "studiesLevelId": [],
    "companiesSizes": [],
    "specializationsIds": [],
    "entreprisesIds": [0],
    "missionStartDate": None
}

HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://mon-vie-via.businessfrance.fr",
    "Referer": "https://mon-vie-via.businessfrance.fr/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
}

class JobBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.check_jobs.start()

    @tasks.loop(minutes=30)
    async def check_jobs(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        if not channel: return

        print("🔍 Scan et nettoyage des offres en cours...")
        try:
            response = requests.post(API_URL, json=PAYLOAD, headers=HEADERS, timeout=15)
            if response.status_code == 200:
                data = response.json()
                offers = data.get('result', [])
                current_api_ids = {str(job['id']) for job in offers}
                
                # 1. Charger la base de données locale (ID_JOB:ID_MESSAGE)
                seen_data = {}
                if os.path.exists(DB_FILE):
                    with open(DB_FILE, 'r') as f:
                        for line in f:
                            if ':' in line:
                                j_id, m_id = line.strip().split(':')
                                seen_data[j_id] = int(m_id)

                # 2. NETTOYAGE : Supprimer les messages des offres disparues
                ids_to_remove = []
                for job_id, msg_id in seen_data.items():
                    if job_id not in current_api_ids:
                        try:
                            msg = await channel.fetch_message(msg_id)
                            await msg.delete()
                            print(f"🗑️ Offre {job_id} plus d'actualité, message supprimé.")
                        except discord.NotFound:
                            pass # Déjà supprimé manuellement
                        except Exception as e:
                            print(f"⚠️ Erreur suppression message {msg_id}: {e}")
                        ids_to_remove.append(job_id)
                
                # On retire les IDs supprimés de notre dictionnaire en mémoire
                for j_id in ids_to_remove:
                    del seen_data[j_id]

                # 3. AJOUT : Poster les nouvelles offres
                new_count = 0
                for job in reversed(offers): # Du plus ancien au plus récent
                    job_id = str(job['id'])
                    
                    if job_id not in seen_data:
                        color = discord.Color.red() if job['countryName'] == 'Mexique' else discord.Color.blue()
                        
                        embed = discord.Embed(
                            title=f"🌎 {job['countryName']} : {job['missionTitle']}",
                            url=f"https://mon-vie-via.businessfrance.fr/offres/{job_id}",
                            color=color
                        )
                        embed.add_field(name="🏢 Entreprise", value=f"**{job['organizationName']}**", inline=True)
                        embed.add_field(name="📍 Ville", value=job['cityName'], inline=True)
                        embed.add_field(name="💰 Indemnité", value=f"{job['indemnite']}€ / mois", inline=False)
                        
                        # Description (limitée pour l'embed)
                        desc = job.get('missionDescription', 'Pas de description.')
                        embed.description = (desc[:300] + '...') if len(desc) > 300 else desc

                        msg = await channel.send(embed=embed)
                        seen_data[job_id] = msg.id
                        new_count += 1
                        await asyncio.sleep(1.5)

                # 4. SAUVEGARDE : Mettre à jour le fichier texte
                with open(DB_FILE, 'w') as f:
                    for j_id, m_id in seen_data.items():
                        f.write(f"{j_id}:{m_id}\n")
                
                print(f"✅ Cycle terminé. {new_count} nouveaux, {len(ids_to_remove)} supprimés.")

        except Exception as e:
            print(f"💥 Erreur globale : {e}")

bot = JobBot()

@bot.event
async def on_ready():
    print(f'--- {bot.user.name} est en ligne (Scan + Purge) ---')

bot.run(TOKEN)