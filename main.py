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

# --- CLÉS API ---
ODDS_API_KEY = os.getenv('ODDS_API_KEY')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
SPORTSDATA_API_KEY = os.getenv('SPORTSDATA_API_KEY')

# --- MÉMOIRE VOLATILE ---
ALERTES = []
CHAT_HISTORY = []

# --- FICHIERS ---
HISTORIQUE_FILE = "historique.json"
BANKROLL_FILE = "bankroll.json"
RESUME_FILE = "resume.json"

# --- LIGUES ODDS API ---
LIGUES = {
    "hockey": ["icehockey_nhl", "icehockey_sweden_allsvenskan"],
    "football": ["soccer_epl", "soccer_france_ligue_1", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga", "soccer_usa_mls"],
    "tennis": ["tennis_atp_miami", "tennis_wta_miami", "tennis_atp_challenger", "tennis_itf_men", "tennis_itf_women"],
    "rugby": ["rugbyunion_top_14", "rugbyunion_premiership", "rugbyunion_six_nations", "rugbyunion_championship", "rugbyleague_nrl"]
}

# ============================================================
# CONVERSION UNIVERSELLE MONEYLINE → DÉCIMAL
# ============================================================

def american_to_decimal(american):
    """Convertit cote américaine en décimale européenne"""
    try:
        american = float(american)
        if american > 0:
            return round((american / 100) + 1, 2)
        else:
            return round((100 / abs(american)) + 1, 2)
    except:
        return None

def format_odds_sportsdata(game):
    """Formate les cotes SportsDataIO en format lisible avec conversion décimale"""
    pregame = game.get('PregameOdds', [])
    bookmakers = {}
    
    for odd in pregame[:10]:
        sportsbook = odd.get('Sportsbook', 'Unknown')
        if sportsbook == 'Scrambled':
            continue
        if sportsbook not in bookmakers:
            away_ml = odd.get('AwayMoneyLine')
            home_ml = odd.get('HomeMoneyLine')
            bookmakers[sportsbook] = {
                "title": sportsbook,
                "url": odd.get('SportsbookUrl', ''),
                # Format américain original
                "away_moneyline_us": away_ml,
                "home_moneyline_us": home_ml,
                # Conversion décimale européenne
                "away_cote_eu": american_to_decimal(away_ml),
                "home_cote_eu": american_to_decimal(home_ml),
                "spread": odd.get('AwayPointSpread'),
                "over_under": odd.get('OverUnder'),
                "over_payout_eu": american_to_decimal(odd.get('OverPayout')),
                "under_payout_eu": american_to_decimal(odd.get('UnderPayout'))
            }
    
    return list(bookmakers.values())

# ============================================================
# MÉTÉO (Open-Meteo, gratuit)
# ============================================================

def get_weather_for_match(lat=25.76, lon=-80.19):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        r = requests.get(url, timeout=5).json()
        w = r.get('current_weather', {})
        return {
            "temp": w.get('temperature'),
            "vent": w.get('windspeed'),
            "condition_code": w.get('weathercode'),
            "is_day": w.get('is_day')
        }
    except:
        return None

# ============================================================
# SPORTSDATA IO
# ============================================================

def fetch_sportsdata(sport, endpoint):
    """Fonction générique SportsDataIO"""
    today = datetime.now().strftime("%Y-%b-%d").upper()
    try:
        url = f"https://api.sportsdata.io/v3/{sport}/odds/json/{endpoint}/{today}?key={SPORTSDATA_API_KEY}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200 and isinstance(r.json(), list):
            return r.json()
        return []
    except Exception as e:
        print(f"Erreur SportsDataIO {sport}: {e}")
        return []

def fetch_nba_sportsdata():
    matchs = []
    for game in fetch_sportsdata("nba", "GameOddsByDate"):
        bookmakers = format_odds_sportsdata(game)
        if not bookmakers:
            continue
        matchs.append({
            "id": str(game.get('GameId')),
            "sport_key": "basketball_nba",
            "sport_title": "NBA",
            "commence_time": game.get('DateTime'),
            "home_team": game.get('HomeTeamName'),
            "away_team": game.get('AwayTeamName'),
            "status": game.get('Status'),
            "bookmakers_sportsdata": bookmakers,
            "source": "sportsdata"
        })
    return matchs

def fetch_tennis_sportsdata():
    matchs = []
    for game in fetch_sportsdata("tennis", "TennisOddsByDate"):
        bookmakers = format_odds_sportsdata(game)
        if not bookmakers:
            continue
        matchs.append({
            "id": str(game.get('MatchId', game.get('GameId', ''))),
            "sport_key": "tennis",
            "sport_title": "Tennis",
            "commence_time": game.get('DateTime'),
            "home_team": game.get('HomeTeamName', game.get('Player1', '')),
            "away_team": game.get('AwayTeamName', game.get('Player2', '')),
            "status": game.get('Status'),
            "bookmakers_sportsdata": bookmakers,
            "source": "sportsdata"
        })
    return matchs

def fetch_football_sportsdata():
    matchs = []
    for game in fetch_sportsdata("soccer", "GameOddsByDate"):
        bookmakers = format_odds_sportsdata(game)
        if not bookmakers:
            continue
        matchs.append({
            "id": str(game.get('GameId')),
            "sport_key": "soccer",
            "sport_title": "Football",
            "commence_time": game.get('DateTime'),
            "home_team": game.get('HomeTeamName'),
            "away_team": game.get('AwayTeamName'),
            "status": game.get('Status'),
            "bookmakers_sportsdata": bookmakers,
            "source": "sportsdata"
        })
    return matchs

def fetch_mlb_sportsdata():
    matchs = []
    for game in fetch_sportsdata("mlb", "GameOddsByDate"):
        bookmakers = format_odds_sportsdata(game)
        if not bookmakers:
            continue
        matchs.append({
            "id": str(game.get('GameId')),
            "sport_key": "baseball_mlb",
            "sport_title": "MLB",
            "commence_time": game.get('DateTime'),
            "home_team": game.get('HomeTeamName'),
            "away_team": game.get('AwayTeamName'),
            "status": game.get('Status'),
            "bookmakers_sportsdata": bookmakers,
            "source": "sportsdata"
        })
    return matchs

# ============================================================
# ODDS API — Sports non couverts par SportsDataIO
# ============================================================

def fetch_global_data(sport_key):
    all_results = []
    for league in LIGUES.get(sport_key, []):
        try:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h,spreads,totals&oddsFormat=decimal"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                all_results.extend(r.json())
            time.sleep(0.1)
        except:
            continue
    all_results.sort(key=lambda x: x.get('commence_time', ''))
    return all_results

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
    pct = pourcentages.get(confiance, 0.02)
    return round(bankroll_disponible * pct, 2)

# ============================================================
# ANALYSE IA
# ============================================================

def analyze_with_claude(match):
    meteo = get_weather_for_match()
    source = match.get('source', 'odds_api')

    odds_summary = ""
    if source == 'sportsdata':
        for b in match.get('bookmakers_sportsdata', [])[:5]:
            odds_summary += f"\n- {b['title']}: "
            odds_summary += f"Away {b['away_cote_eu']} | Home {b['home_cote_eu']}"
            if b.get('spread'):
                odds_summary += f" | Spread: {b['spread']}"
            if b.get('over_under'):
                odds_summary += f" | O/U: {b['over_under']}"
    else:
        for b in match.get('bookmakers', [])[:3]:
            odds_summary += f"\n- {b['title']}: "
            for m in b.get('markets', []):
                for o in m.get('outcomes', []):
                    odds_summary += f"{o['name']}@{o['price']} "

    prompt = f"""Expert Radar V5 - Analyse Paris Sportifs.
Match: {match.get('away_team')} @ {match.get('home_team')}
Sport: {match.get('sport_key')}
Heure: {match.get('commence_time')}
Météo: {meteo if meteo else 'Stable'}
Cotes (format décimal européen): {odds_summary}

Réponds UNIQUEMENT en JSON valide :
{{
  "value_bet": true ou false,
  "confiance": nombre entre 0 et 10,
  "pari_recommande": "conseil court",
  "cote": nombre décimal européen ex: 2.45,
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

    # Tous les sports SportsDataIO
    tous_matchs_sd = []
    tous_matchs_sd.extend(fetch_nba_sportsdata()[:3])
    tous_matchs_sd.extend(fetch_tennis_sportsdata()[:3])
    tous_matchs_sd.extend(fetch_football_sportsdata()[:3])
    tous_matchs_sd.extend(fetch_mlb_sportsdata()[:3])

    for match in tous_matchs_sd:
        analyse = analyze_with_claude(match)
        if not analyse:
            continue
        if analyse.get('value_bet') and analyse.get('confiance', 0) >= 7:
            nouvelles_alertes.append({
                "id": f"{match.get('id', '')}_{int(time.time())}",
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "match": f"{match.get('away_team')} @ {match.get('home_team')}",
                "sport": match.get('sport_title', match.get('sport_key')),
                "confiance": analyse.get('confiance'),
                "pari": analyse.get('pari_recommande'),
                "cote": analyse.get('cote'),
                "bookmaker": analyse.get('bookmaker'),
                "raison": analyse.get('raison'),
                "risque": analyse.get('risque'),
                "impact_meteo": analyse.get('impact_meteo', 'aucun')
            })
        time.sleep(1)

    # Rugby via Odds API
    for match in fetch_global_data("rugby")[:3]:
        if not match.get('bookmakers'):
            continue
        analyse = analyze_with_claude(match)
        if not analyse:
            continue
        if analyse.get('value_bet') and analyse.get('confiance', 0) >= 7:
            nouvelles_alertes.append({
                "id": f"{match.get('id', '')}_{int(time.time())}",
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "match": f"{match.get('home_team')} vs {match.get('away_team')}",
                "sport": "rugby",
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
    tous_matchs.extend(fetch_nba_sportsdata()[:3])
    tous_matchs.extend(fetch_tennis_sportsdata()[:2])
    tous_matchs.extend(fetch_football_sportsdata()[:2])
    tous_matchs.extend(fetch_mlb_sportsdata()[:2])
    tous_matchs.extend(fetch_global_data("rugby")[:2])

    if not tous_matchs:
        return None

    matchs_str = ""
    for m in tous_matchs:
        matchs_str += f"- {m.get('sport_title', m.get('sport_key'))} : {m.get('away_team')} @ {m.get('home_team')} | {m.get('commence_time')}\n"

    prompt = f"""Tu es un expert en paris sportifs. Matchs disponibles aujourd'hui :
{matchs_str}

Réponds UNIQUEMENT en JSON :
{{
  "date": "{datetime.now().strftime('%d/%m/%Y')}",
  "resume_general": "2-3 phrases sur la journée",
  "top_matchs": [
    {{
      "match": "nom",
      "sport": "sport",
      "raison": "pourquoi intéressant",
      "pari_suggere": "pari recommandé",
      "niveau_interet": 1-5
    }}
  ],
  "conseil_du_jour": "conseil général",
  "sports_chauds": ["sport1", "sport2"]
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
# ENDPOINTS TEST
# ============================================================

@app.route('/')
def health():
    return "RADAR V5.4 : SYSTEM READY 📡🌦️🏉🏀🎾⚽"

@app.route('/test')
def test_connexion():
    today = datetime.now().strftime("%Y-%b-%d").upper()
    try:
        url = f"https://api.sportsdata.io/v3/nba/odds/json/GameOddsByDate/{today}?key={SPORTSDATA_API_KEY}"
        r = requests.get(url, timeout=5)
        return jsonify({"status": r.status_code, "nb_matchs": len(r.json()), "apercu": r.json()[:1]})
    except Exception as e:
        return jsonify({"erreur": str(e)})

@app.route('/test_ultime_sports')
def test_all_sports():
    today = datetime.now().strftime("%Y-%b-%d").upper()
    results = {}
    targets = {
        "NBA": f"https://api.sportsdata.io/v3/nba/odds/json/GameOddsByDate/{today}",
        "TENNIS": f"https://api.sportsdata.io/v3/tennis/odds/json/TennisOddsByDate/{today}",
        "FOOT_EPL": f"https://api.sportsdata.io/v3/soccer/odds/json/GameOddsByDate/{today}",
        "MLB": f"https://api.sportsdata.io/v3/mlb/odds/json/GameOddsByDate/{today}"
    }
    for sport, url in targets.items():
        try:
            r = requests.get(f"{url}?key={SPORTSDATA_API_KEY}", timeout=10)
            if r.status_code == 200:
                data = r.json()
                results[sport] = {
                    "status": "✅ OK",
                    "matchs": len(data) if isinstance(data, list) else 0,
                    "apercu": data[:1] if isinstance(data, list) and len(data) > 0 else "Vide"
                }
            else:
                results[sport] = {"status": f"❌ {r.status_code}"}
        except Exception as e:
            results[sport] = {"status": "🚨 CRASH", "erreur": str(e)}
    return jsonify({"date": today, "resultats": results})

# ============================================================
# ENDPOINTS RADAR
# ============================================================

@app.route('/radar/nba')
def get_nba():
    return jsonify({"data": fetch_nba_sportsdata()})

@app.route('/radar/global_basket')
def get_basket():
    return jsonify({"data": fetch_nba_sportsdata()})

@app.route('/radar/tennis')
def get_tennis():
    data = fetch_tennis_sportsdata()
    if not data:
        data = fetch_global_data("tennis")
    return jsonify({"data": data})

@app.route('/radar/football')
def get_foot():
    data = fetch_football_sportsdata()
    if not data:
        data = fetch_global_data("football")
    return jsonify({"data": data})

@app.route('/radar/mlb')
def get_mlb():
    return jsonify({"data": fetch_mlb_sportsdata()})

@app.route('/radar/global_hockey')
def get_hockey():
    return jsonify({"data": fetch_global_data("hockey")})

@app.route('/radar/rugby')
def get_rugby():
    return jsonify({"data": fetch_global_data("rugby")})

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
        "sport": match.get('sport_key', ''),
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
    value_bets = [h for h in historique if h.get('value_bet')]
    wins = [h for h in historique if h.get('resultat') == 'WIN']
    confiance_moy = round(
        sum(h.get('confiance', 0) for h in historique) / total, 1
    ) if total > 0 else 0
    return jsonify({
        "historique": historique,
        "stats": {
            "total": total,
            "value_bets": len(value_bets),
            
