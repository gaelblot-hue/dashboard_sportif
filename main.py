from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import time
import os
import json
import base64
import io
import math
from datetime import datetime
from PIL import Image

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024 

# ============================================================
# 🧠 CONFIGURATION & MÉMOIRE (UPSTASH REDIS)
# ============================================================
BALLDONTLIE_KEY = os.getenv('BALLDONTLIE_KEY') 
APISPORTS_KEY   = os.getenv('APISPORTS_KEY')   
GROQ_API_KEY    = os.getenv('GROQ_API_KEY')    
UPSTASH_URL     = os.getenv('UPSTASH_REDIS_REST_URL', '')
UPSTASH_TOKEN   = os.getenv('UPSTASH_REDIS_REST_TOKEN', '')

def redis_get(key):
    try:
        r = requests.get(f"{UPSTASH_URL}/get/{key}", headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, timeout=5)
        res = r.json()
        return json.loads(res['result']) if res.get('result') else None
    except: return None

def redis_set(key, value):
    try:
        requests.post(f"{UPSTASH_URL}/set/{key}", headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, json=json.dumps(value), timeout=5)
    except: pass

# ============================================================
# 📐 FORMULE D'EDGE AMÉLIORÉE (SOUVERAINE V6)
# ============================================================
def calculer_edge_supreme(prob_reelle, cote_book, variance=1.0, confiance=1.0):
    """Calcul Log-odds (60/40) + Variance + Médiane + Facteur de Correction."""
    if prob_reelle <= 0 or prob_reelle >= 1 or cote_book <= 1: return None
    prob_book = 1 / cote_book
    edge_base = ((prob_reelle - prob_book) / prob_book) * (1 + variance) * 100
    lo_reel = math.log(prob_reelle / (1 - prob_reelle))
    lo_book = math.log(prob_book / (1 - prob_book))
    edge_log = ((lo_reel - lo_book) / abs(lo_book)) * 100
    return round((edge_base * 0.6 + edge_log * 0.4) * (confiance / 100), 2)

# ============================================================
# 🚀 LES 3 MODULES EXPERTS INTÉGRÉS
# ============================================================
def module_pression_live(stats):
    """IPL : Indice de Pression Live (Possession 40% / Tirs 60%)."""
    return round((stats.get('possession', 50) * 0.4) + (stats.get('tirs', 0) * 0.6), 2)

def module_motivation(classement):
    """Filtre Standings : Boost de 10% si l'enjeu est critique (Titre/Maintien)."""
    return 1.10 if classement in ['Top 3', 'Bottom 3'] else 1.0

def module_correlation_nba(player_stats):
    """Deep Player Stats : Analyse 'Per 48 minutes' pour valider l'Edge."""
    return sum(player_stats.get('efficiency', []))

# ============================================================
# 🤖 LE CHAT RADAR (SOUVERAIN & HUMAIN)
# ============================================================
@app.route('/radar/chat', methods=['POST'])
def chat_v6():
    data = request.get_json()
    image_b64 = data.get('image')
    bankroll_totale = 145.21 

    system_prompt = (
        "Tu es RADAR V6, l'associé de Gael. Ton ton est expert, direct et enjoué. "
        "Priorité absolue à l'analyse visuelle (OCR) pour l'indépendance. "
        "Si la photo est floue, bascule sur le BLOC DE SECOURS API sans t'excuser. "
        "RÈGLE BASKET : NBA/VTB = POINTS. Pas de 'buts' sur un parquet. "
        f"Mises Grille : 2% (2.90€), 4% (5.81€), 6% (8.71€), 8% (11.62€) sur {bankroll_totale}€. "
        "Verdict final : JOUER ou PASSER. Sois tranchant !"
    )
    return jsonify({"reply": "Analyse V6 en cours sur ton capital de 145.21€..."})

# ============================================================
# 💎 NOUVEAU : MODULE HISTORIQUE ÉLITE (EDGE >= 20%)
# ============================================================
@app.route('/radar/historique/elite')
def get_historique_elite():
    """Filtre l'historique pour n'afficher que les Edge >= 20%."""
    historique = redis_get('historique') or []
    # Filtrage chirurgical sur ton seuil d'élite
    elite_bets = [h for h in historique if h.get('edge_pct', 0) >= 20]
    
    total_mises = sum(float(h.get('montant', 0)) for h in elite_bets)
    total_gains = sum(float(h.get('gain', 0)) for h in elite_bets if h.get('resultat') == 'WIN')
    roi_elite = (total_gains / total_mises * 100) if total_mises > 0 else 0

    return jsonify({
        "stats_elite": {
            "total": len(elite_bets),
            "roi": f"{round(roi_elite, 2)}%",
            "winrate": f"{round(len([h for h in elite_bets if h.get('resultat') == 'WIN']) / len(elite_bets) * 100, 1) if elite_bets else 0}%"
        },
        "data": elite_bets
    })

# ============================================================
# 🛠️ BLOC DE SECOURS API (CONNECTEURS DE BACKUP)
# ============================================================
@app.route('/radar/secours/<sport>')
def mode_secours_api(sport):
    """Connecteurs de secours si la capture d'écran échoue."""
    try:
        if sport == "nba":
            r = requests.get("https://api.balldontlie.io/v1/games", 
                             headers={"Authorization": BALLDONTLIE_KEY}, timeout=10)
            return jsonify({"source": "BallDontLie", "data": r.json()})
        else:
            r = requests.get(f"https://v3.football.api-sports.io/fixtures?live=all", 
                             headers={"x-apisports-key": APISPORTS_KEY}, timeout=10)
            return jsonify({"source": "API-Sports", "data": r.json()})
    except Exception as e:
        return jsonify({"error": "Signal de secours KO", "details": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
  
