from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import anthropic
import time
import os
import json
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ============================================================
# CLÉS API
# ============================================================
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
BALLDONTLIE_KEY = os.getenv('BALLDONTLIE_KEY')
OPENWEATHER_KEY = os.getenv('OPENWEATHER_KEY')
HIGHLIGHTLY_KEY = os.getenv('HIGHLIGHTLY_KEY')

# ============================================================
# MÉMOIRE & FICHIERS
# ============================================================
ALERTES = []
CHAT_HISTORY = []
CACHE = {}
CACHE_DURATION = 900  # 15 minutes

HISTORIQUE_FILE = "historique.json"
BANKROLL_FILE = "bankroll.json"
RESUME_FILE = "resume.json"

# ============================================================
# ROTATION DES SPORTS (1 par minute)
# ============================================================
SPORTS_ROTATION = [
    {"nom": "NBA",           "endpoint": "/nba/v1/games",      "odds": "/nba/v2/odds"},
    {"nom": "NHL",           "endpoint": "/nhl/v1/games",      "odds": "/nhl/v1/odds"},
    {"nom": "MLB",           "endpoint": "/mlb/v1/games",      "odds": "/mlb/v1/odds"},
    {"nom": "NFL",           "endpoint": "/nfl/v1/games",      "odds": "/nfl/v1/odds"},
    {"nom": "MMA",           "endpoint": "/mma/v1/fights",     "odds": "/mma/v1/odds"},
    {"nom": "EPL",           "endpoint": "/epl/v2/matches",    "odds": "/epl/v2/odds"},
    {"nom": "La Liga",       "endpoint": "/laliga/v1/matches", "odds": "/laliga/v1/odds"},
    {"nom": "Bundesliga",    "endpoint": "/bundesliga/v1/matches", "odds": "/bundesliga/v1/odds"},
    {"nom": "Ligue 1",       "endpoint": "/ligue1/v1/matches", "odds": "/ligue1/v1/odds"},
    {"nom": "Serie A",       "endpoint": "/seriea/v1/matches", "odds": "/seriea/v1/odds"},
    {"nom": "Champions League", "endpoint": "/ucl/v1/matches", "odds": "/ucl/v1/odds"},
    {"nom": "ATP Tennis",    "endpoint": "/atp/v1/matches",    "odds": "/atp/v1/odds"},
    {"nom": "WTA Tennis",    "endpoint": "/wta/v1/matches",    "odds": "/wta/v1/odds"},
    {"nom": "MLS",           "endpoint": "/mls/v1/matches",    "odds": "/mls/v1/odds"},
]

# ============================================================
# CACHE
# ============================================================
def get_cache(key):
    if key in CACHE:
        data, timestamp = CACHE[key]
        if time.time() - timestamp < CACHE_DURATION:
            print(f"✅ Cache hit : {key}")
            return data
    return None

def set_cache(key, data):
    CACHE[key] = (data, time.time())

# ============================================================
# BALLDONTLIE — Fonction générique
# ============================================================
def bdl_get(endpoint, params={}):
    """Appel générique BallDontLie avec cache"""
    cache_key = f"bdl_{endpoint}_{str(params)}"
    cached = get_cache(cache_key)
    if cached:
        return cached

    try:
        url = f"https://api.balldontlie.io{endpoint}"
        r = requests.get(url,
            headers={"Authorization": BALLDONTLIE_KEY},
            params=params,
            timeout=10)
        if r.status_code == 200:
            data = r.json()
            set_cache(cache_key, data)
            return data
        else:
            print(f"❌ BallDontLie {endpoint} : {r.status_code}")
            return None
    except Exception as e:
        print(f"🚨 BallDontLie erreur {endpoint} : {e}")
        return None

def bdl_get_today(endpoint):
    """Récupère les matchs du jour"""
    today = datetime.now().strftime("%Y-%m-%d")
    return bdl_get(endpoint, {"dates[]": today, "per_page": 100})

def bdl_get_odds(endpoint):
    """Récupère les cotes du jour"""
    today = datetime.now().strftime("%Y-%m-%d")
    return bdl_get(endpoint, {"date": today, "per_page": 100})

# ============================================================
# FETCH PAR SPORT
# ============================================================
def fetch_sport(sport):
    """Récupère matchs + cotes pour un sport"""
    cache_key = f"sport_{sport['nom']}"
    cached = get_cache(cache_key)
    if cached:
        return cached

    matchs = []

    # Récupère les matchs
    data = bdl_get_today(sport["endpoint"])
    if not data or not data.get('data'):
        return []

    # Récupère les cotes
    odds_data = bdl_get_odds(sport["odds"])
    odds_map = {}
    if odds_data and odds_data.get('data'):
        for odd in odds_data['data']:
            game_id = odd.get('game_id') or odd.get('match_id') or odd.get('fight_id')
            if game_id:
                if game_id not in odds_map:
                    odds_map[game_id] = []
                odds_map[game_id].append(odd)

    # Combine matchs + cotes
    for game in data['data']:
        game_id = game.get('id')
        home = game.get('home_team', {})
        away = game.get('visitor_team', game.get('away_team', {}))

        home_name = home.get('full_name') or home.get('name') or str(home)
        away_name = away.get('full_name') or away.get('name') or str(away)

        # Formate les cotes
        cotes = []
        for odd in odds_map.get(game_id, [])[:5]:
            cote_entry = {
                "bookmaker": odd.get('book') or odd.get('sportsbook', 'Unknown'),
                "home_cote": odd.get('home_moneyline') or odd.get('home_odds'),
                "away_cote": odd.get('away_moneyline') or odd.get('away_odds'),
                "over_under": odd.get('over_under'),
                "spread": odd.get('home_spread') or odd.get('spread')
            }
            cotes.append(cote_entry)

        matchs.append({
            "id": str(game_id),
            "sport_key": sport['nom'].lower().replace(' ', '_'),
            "sport_title": sport['nom'],
            "commence_time": game.get('date') or game.get('datetime') or game.get('scheduled'),
            "home_team": home_name,
            "away_team": away_name,
            "status": game.get('status', 'scheduled'),
            "cotes": cotes,
            "source": "balldontlie"
        })

    set_cache(cache_key, matchs)
    return matchs

# ============================================================
# MÉTÉO
# ============================================================
def get_weather(city="Paris"):
    cached = get_cache(f"weather_{city}")
    if cached:
        return cached
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHER_KEY}&units=metric&lang=fr"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            meteo = {
                "ville": city,
                "temp": data['main']['temp'],
                "conditions": data['weather'][0]['description'],
                "vent": data['wind']['speed'],
                "pluie": 'rain' in data['weather'][0]['main'].lower()
            }
            set_cache(f"weather_{city}", meteo)
            return meteo
    except Exception as e:
        print(f"Erreur météo : {e}")
    return None

# ============================================================
# UTILITAIRES
# ============================================================
def load_historique():
    try:
        with open(HISTORIQUE_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_historique(entry):
    historique = load_historique()
    historique.insert(0, entry)
    historique = historique[:100]
    with open(HISTORIQUE_FILE, 'w') as f:
        json.dump(historique, f)

def load_bankroll():
    try:
        with open(BANKROLL_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"total": 0, "disponible": 0, "mises": []}

def save_bankroll(data):
    with open(BANKROLL_FILE, 'w') as f:
        json.dump(data, f)

def calculer_mise(bankroll_disponible, confiance):
    pourcentages = {7: 0.02, 8: 0.04, 9: 0.06, 10: 0.08}
    return round(bankroll_disponible * pourcentages.get(confiance, 0.02), 2)

# ============================================================
# ANALYSE IA
# ============================================================
def analyze_with_claude(match):
    meteo = get_weather()

    cotes_summary = ""
    for c in match.get('cotes', [])[:5]:
        cotes_summary += f"\n- {c['bookmaker']}: Home {c['home_cote']} | Away {c['away_cote']}"
        if c.get('spread'):
            cotes_summary += f" | Spread: {c['spread']}"
        if c.get('over_under'):
            cotes_summary += f" | O/U: {c['over_under']}"

    meteo_str = f"{meteo['temp']}°C, {meteo['conditions']}, vent {meteo['vent']}m/s" if meteo else "Stable"

    prompt = f"""Expert Radar V6 - Analyse Paris Sportifs.
Match: {match.get('away_team')} @ {match.get('home_team')}
Sport: {match.get('sport_title')}
Heure: {match.get('commence_time')}
Météo: {meteo_str}
Cotes: {cotes_summary if cotes_summary else 'Non disponibles'}

Réponds UNIQUEMENT en JSON valide :
{{
  "value_bet": true ou false,
  "confiance": nombre entre 0 et 10,
  "pari_recommande": "conseil court",
  "cote": nombre décimal ex: 2.45,
  "bookmaker": "nom",
  "raison": "explication courte",
  "risque": "risque principal",
  "impact_meteo": "aucun ou description",
  "mise_conseillee": "% bankroll"
}}"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        res_text = response.content[0].text
        raw = res_text[res_text.find("{"):res_text.rfind("}")+1]
        return json.loads(raw)
    except Exception as e:
        print(f"Erreur Claude : {e}")
        return None

# ============================================================
# SCAN VALUE BETS
# ============================================================
def scan_value_bets():
    global ALERTES
    nouvelles_alertes = []

    for sport in SPORTS_ROTATION:
        matchs = fetch_sport(sport)
        for match in matchs[:3]:
            if not match.get('cotes'):
                continue
            analyse = analyze_with_claude(match)
            if not analyse:
                continue
            if analyse.get('value_bet') and analyse.get('confiance', 0) >= 7:
                nouvelles_alertes.append({
                    "id": f"{match.get('id')}_{int(time.time())}",
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "match": f"{match.get('away_team')} @ {match.get('home_team')}",
                    "sport": match.get('sport_title'),
                    "confiance": analyse.get('confiance'),
                    "pari": analyse.get('pari_recommande'),
                    "cote": analyse.get('cote'),
                    "bookmaker": analyse.get('bookmaker'),
                    "raison": analyse.get('raison'),
                    "risque": analyse.get('risque'),
                    "impact_meteo": analyse.get('impact_meteo', 'aucun')
                })
            time.sleep(1)

    ALERTES = (nouvelles_alertes + ALERTES)[:20]
    print(f"✅ Scan terminé : {len(nouvelles_alertes)} value bets détectés")

# ============================================================
# RÉSUMÉ QUOTIDIEN
# ============================================================
def generate_daily_resume():
    tous_matchs = []
    for sport in SPORTS_ROTATION[:6]:
        tous_matchs.extend(fetch_sport(sport)[:2])

    if not tous_matchs:
        return None

    matchs_str = "\n".join([
        f"- {m.get('sport_title')} : {m.get('away_team')} @ {m.get('home_team')} | {m.get('commence_time')}"
        for m in tous_matchs
    ])

    prompt = f"""Tu es un expert en paris sportifs. Matchs disponibles aujourd'hui :
{matchs_str}

Réponds UNIQUEMENT en JSON :
{{
  "date": "{datetime.now().strftime('%d/%m/%Y')}",
  "resume_general": "2-3 phrases sur la journée sportive",
  "top_matchs": [
    {{
      "match": "Equipe1 vs Equipe2",
      "sport": "NBA",
      "raison": "pourquoi ce match est interessant",
      "pari_suggere": "pari recommande",
      "niveau_interet": 4
    }}
  ],
  "conseil_du_jour": "un conseil general",
  "sports_chauds": ["NBA", "EPL"]
}}"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        res_text = response.content[0].text
        raw = res_text[res_text.find("{"):res_text.rfind("}")+1]
        resume = json.loads(raw)
        with open(RESUME_FILE, 'w') as f:
            json.dump(resume, f)
        return resume
    except Exception as e:
        print(f"Erreur résumé : {e}")
        return None

# ============================================================
# ENDPOINTS SANTÉ
# ============================================================
@app.route('/')
def health():
    return "RADAR V6 : SYSTEM READY 📡🏀🏒⚾🏈🥊⚽🎾"

@app.route('/test')
def test():
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        r = requests.get("https://api.balldontlie.io/nba/v1/games",
            headers={"Authorization": BALLDONTLIE_KEY},
            params={"dates[]": today},
            timeout=5)
        return jsonify({
            "status": r.status_code,
            "matchs_nba": len(r.json().get('data', [])),
            "apercu": r.json().get('data', [])[:2]
        })
    except Exception as e:
        return jsonify({"erreur": str(e)})

# ============================================================
# ENDPOINTS RADAR
# ============================================================
@app.route('/radar/<sport_id>')
def get_sport(sport_id):
    sport_map = {
        "nba": "NBA", "nhl": "NHL", "mlb": "MLB", "nfl": "NFL",
        "mma": "MMA", "epl": "EPL", "laliga": "La Liga",
        "bundesliga": "Bundesliga", "ligue1": "Ligue 1",
        "seriea": "Serie A", "ucl": "Champions League",
        "atp": "ATP Tennis", "wta": "WTA Tennis", "mls": "MLS",
        "global_basket": "NBA", "global_hockey": "NHL",
        "football": "EPL", "tennis": "ATP Tennis", "rugby": None
    }
    nom = sport_map.get(sport_id)
    if not nom:
        return jsonify({"data": []})
    sport = next((s for s in SPORTS_ROTATION if s['nom'] == nom), None)
    if not sport:
        return jsonify({"data": []})
    return jsonify({"data": fetch_sport(sport)})

# ============================================================
# ENDPOINTS ANALYSE
# ============================================================
@app.route('/radar/analyze', methods=['POST'])
def analyze_match():
    data = request.get_json()
    match = data.get('match')
    if not match:
        return jsonify({"error": "No data"}), 400

    analyse = analyze_with_claude(match)
    if not analyse:
        return jsonify({"error": "Erreur IA"}), 500

    save_historique({
        "id": f"{match.get('id', '')}_{int(time.time())}",
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "match": f"{match.get('away_team', '')} @ {match.get('home_team', '')}",
        "sport": match.get('sport_title', match.get('sport_key', '')),
        "value_bet": analyse.get('value_bet'),
        "confiance": analyse.get('confiance'),
        "pari": analyse.get('pari_recommande'),
        "cote": analyse.get('cote'),
        "bookmaker": analyse.get('bookmaker'),
        "raison": analyse.get('raison'),
        "impact_meteo": analyse.get('impact_meteo', 'aucun'),
        "resultat": None
    })

    return jsonify({"analyse": analyse})

# ============================================================
# ENDPOINTS ALERTES
# ============================================================
@app.route('/radar/alertes')
def get_alertes():
    return jsonify({"alertes": ALERTES, "last_scan": datetime.now().strftime("%H:%M:%S")})

@app.route('/radar/scan', methods=['POST'])
def trigger_scan():
    scan_value_bets()
    return jsonify({"status": "Scan terminé", "alertes": len(ALERTES)})

# ============================================================
# ENDPOINTS HISTORIQUE
# ============================================================
@app.route('/radar/historique')
def get_historique():
    historique = load_historique()
    total = len(historique)
    wins = [h for h in historique if h.get('resultat') == 'WIN']
    confiance_moy = round(sum(h.get('confiance', 0) for h in historique) / total, 1) if total > 0 else 0
    return jsonify({
        "historique": historique,
        "stats": {
            "total": total,
            "value_bets": len([h for h in historique if h.get('value_bet')]),
            "wins": len(wins),
            "losses": len([h for h in historique if h.get('resultat') == 'LOSS']),
            "confiance_moyenne": confiance_moy
        }
    })

@app.route('/radar/historique/resultat', methods=['POST'])
def update_resultat():
    data = request.get_json()
    historique = load_historique()
    for entry in historique:
        if entry.get('id') == data.get('id'):
            entry['resultat'] = data.get('resultat')
            break
    with open(HISTORIQUE_FILE, 'w') as f:
        json.dump(historique, f)
    return jsonify({"status": "ok"})

# ============================================================
# ENDPOINTS BANKROLL
# ============================================================
@app.route('/radar/bankroll')
def get_bankroll():
    return jsonify(load_bankroll())

@app.route('/radar/bankroll/init', methods=['POST'])
def init_bankroll():
    data = request.get_json()
    montant = float(data.get('montant', 0))
    bankroll = {"total": montant, "disponible": montant, "mises": []}
    save_bankroll(bankroll)
    return jsonify({"status": "ok", "bankroll": bankroll})

@app.route('/radar/bankroll/miser', methods=['POST'])
def ajouter_mise():
    data = request.get_json()
    bankroll = load_bankroll()
    mise = {
        "id": f"mise_{int(time.time())}",
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "match": data.get('match'),
        "pari": data.get('pari'),
        "cote": float(data.get('cote', 0)),
        "montant": float(data.get('montant', 0)),
        "confiance": int(data.get('confiance', 0)),
        "statut": "EN COURS",
        "gain": None
    }
    bankroll['disponible'] = round(bankroll['disponible'] - mise['montant'], 2)
    bankroll['mises'].insert(0, mise)
    save_bankroll(bankroll)
    return jsonify({"status": "ok", "bankroll": bankroll})

@app.route('/radar/bankroll/resultat', methods=['POST'])
def resultat_mise():
    data = request.get_json()
    bankroll = load_bankroll()
    for mise in bankroll['mises']:
        if mise['id'] == data.get('id'):
            mise['statut'] = data.get('statut')
            if data.get('statut') == 'WIN':
                gain = round(mise['montant'] * mise['cote'] - mise['montant'], 2)
                mise['gain'] = gain
                bankroll['disponible'] = round(bankroll['disponible'] + mise['montant'] + gain, 2)
            else:
                mise['gain'] = -mise['montant']
            break
    save_bankroll(bankroll)
    return jsonify({"status": "ok", "bankroll": bankroll})

@app.route('/radar/bankroll/historique_pnl')
def get_historique_pnl():
    bankroll = load_bankroll()
    mises = [m for m in bankroll.get('mises', []) if m.get('gain') is not None]
    mises.reverse()
    solde = bankroll.get('total', 0)
    points = [{"date": "Départ", "solde": solde, "gain": 0}]
    for mise in mises:
        solde = round(solde + mise['gain'], 2)
        points.append({
            "date": mise['timestamp'],
            "solde": solde,
            "gain": mise['gain'],
            "match": mise['match']
        })
    return jsonify({"points": points, "total_initial": bankroll.
