import discord
from discord.ext import tasks, commands
import requests
import os
import asyncio

# Configuration
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = 1484325069860769953
API_URL = "https://civiweb-api-prd.azurewebsites.net/api/Offers/search"
DB_FILE = "seen_jobs.txt"

# Payload Amérique Latine (Zone 3)
PAYLOAD = {
    "limit": 20, # Augmenté à 20 pour ne rien rater
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

# Les HEADERS sont parfois nécessaires pour éviter le blocage
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
        # Cette ligne lance la boucle AUTOMATIQUEMENT au démarrage.
        # Pas besoin d'appels supplémentaires ailleurs.
        self.check_jobs.start()

    @tasks.loop(minutes=30)
    async def check_jobs(self):
        # On attend que le bot soit prêt pour être sûr d'avoir accès au salon
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        if not channel: return

        print("🔍 Scan des offres en cours...")
        try:
            # Note : J'utilise HEADERS ici pour être plus proche de ta requête Burp
            response = requests.post(API_URL, json=PAYLOAD, headers=HEADERS, timeout=15)
            if response.status_code == 200:
                data = response.json()
                offers = data.get('result', [])
                
                if not os.path.exists(DB_FILE):
                    open(DB_FILE, 'w').close()
                
                seen = {}
                with open(DB_FILE, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if ':' in line:
                            job_id, msg_id = line.split(':', 1)
                            seen[job_id] = int(msg_id)

                new_count = 0
                for job in reversed(offers):
                    job_id = str(job['id'])
                    
                    if job_id not in seen:
                        # Make Mexico jobs more visible with red color
                        color = discord.Color.red() if job['countryName'] == 'Mexique' else discord.Color.blue()
                        embed = discord.Embed(
                            title=f"🌎 {job['countryName']} : {job['missionTitle']}",
                            url=f"https://mon-vie-via.businessfrance.fr/offres/{job_id}",
                            color=color
                        )
                        embed.add_field(name="🏢 Entreprise", value=job['organizationName'], inline=True)
                        embed.add_field(name="📍 Ville", value=job['cityName'], inline=True)
                        embed.add_field(name="💰 Indemnité", value=f"{job['indemnite']}€ / mois", inline=False)
                        # Add description for all jobs
                        description = job.get('description', 'Aucune description disponible')
                        embed.add_field(name="📄 Description", value=description[:1000], inline=False)  # Limit to 1000 chars
                        
                        msg = await channel.send(embed=embed)
                        seen[job_id] = msg.id
                        new_count += 1
                        await asyncio.sleep(1.5)
                
                # Synchronize DB_FILE with current offers and remove old messages
                current_ids = set(str(job['id']) for job in offers)
                for job_id, msg_id in seen.items():
                    if job_id not in current_ids:
                        try:
                            msg = await channel.fetch_message(msg_id)
                            await msg.delete()
                        except discord.NotFound:
                            pass  # Message already deleted
                        except Exception as e:
                            print(f"Error deleting message {msg_id}: {e}")
                
                with open(DB_FILE, 'w') as f:
                    for job_id in current_ids:
                        if job_id in seen:
                            f.write(f"{job_id}:{seen[job_id]}\n")
                
                print(f"✅ Terminé. {new_count} nouvelles offres.")
        except Exception as e:
            print(f"💥 Erreur : {e}")

bot = JobBot()

@bot.event
async def on_ready():
    # On ne met QUE des logs ici, on n'appelle PAS check_jobs
    print(f'--- {bot.user.name} est en ligne ---')

bot.run(TOKEN)
