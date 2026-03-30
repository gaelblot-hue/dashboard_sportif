from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import time
import os
import json
import base64
import io
from datetime import datetime
from PIL import Image

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# ============================================================
# CONFIGURATION & CLÉS
# ============================================================
BALLDONTLIE_KEY   = os.getenv('BALLDONTLIE_KEY')
APISPORTS_KEY     = os.getenv('APISPORTS_KEY')
GROQ_API_KEY      = os.getenv('GROQ_API_KEY')
UPSTASH_URL       = os.getenv('UPSTASH_REDIS_REST_URL', '')
UPSTASH_TOKEN     = os.getenv('UPSTASH_REDIS_REST_TOKEN', '')

# ============================================================
# MÉMOIRE REDIS
# ============================================================
def redis_get(key):
    try:
        r = requests.get(f"{UPSTASH_URL}/get/{key}", headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, timeout=5)
        result = r.json()
        if result.get('result'): return json.loads(result['result'])
    except: return None
    return None

def redis_set(key, value, ex=None):
    try:
        data = json.dumps(value, ensure_ascii=False)
        url = f"{UPSTASH_URL}/set/{key}"
        if ex: url += f"?ex={ex}"
        requests.post(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}", "Content-Type": "application/json"}, json=data, timeout=5)
    except: pass

# ============================================================
# GESTION BANKROLL GAËL
# ============================================================
BOOKMAKERS_BANKROLL = {"Betclic": 14.99, "Winamax": 28.88, "Betify": 58.86, "Mystake": 42.48}
BANKROLL_TOTALE = 145.21

def load_bankroll():
    data = redis_get('bankroll')
    return data if data else {"total": BANKROLL_TOTALE, "disponible": BANKROLL_TOTALE, "mises": []}

def save_bankroll(data):
    redis_set('bankroll', data)

# ============================================================
# CERVEAU RADAR V6 (CHAT & ANALYSE)
# ============================================================
@app.route('/radar/chat', methods=['POST'])
def chat():
    data = request.get_json()
    message = data.get('message')
    image_b64 = data.get('image')
    
    if data.get('reset'):
        redis_set('chat_history', [])
        return jsonify({"status": "reset"})

    history = redis_get('chat_history') or []
    
    system_prompt = (
        "Tu es RADAR V6, l'IA d'élite de Gaël. Tu es son allié stratégique, passionné et ultra-réactif. "
        "TON STYLE : Tranchant, direct, pro. Pas de politesse type 'Je vais chercher' ou 'Malheureusement'. "
        "Tu es là pour gagner. Si les datas manquent, dis 'DATA MANQUANTE' et n'invente rien. "
        "RÈGLES DE SPORT : "
        "1. BASKET : Parle toujours en POINTS (ex: 170.5). Interdiction de dire 'Buts' ou '2.5'. "
        "2. FOOTBALL : Format classique (ex: 2.5 buts). "
        "LOGIQUE RADAR : "
        "- Edge minimum : 15%. Calcule en interne via Log-Odds. "
        "- Mise (Bankroll 145.21€) : 60% conf = 2.90€ | 70% = 5.81€ | 80% = 8.71€ | 90%+ = 11.62€. "
        "STRUCTURE : "
        "### [MATCH] "
        "**Verdict :** JOUER / NE PAS JOUER "
        "**Confiance :** X/10 | **Risque :** [NIVEAU] "
        "**Pari :** [Type @ Cote] | **Mise :** [X]€ sur [BOOKMAKER] "
        "**Analyse :** 2-3 points clés (H2H, Forme, Stats)."
    )

    model = "meta-llama/llama-4-scout-17b-16e-instruct" if image_b64 else "llama-3.3-70b-versatile"
    user_content = [{"type": "text", "text": message or "Analyse !"}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}] if image_b64 else message

    messages = [{"role": "system", "content": system_prompt}]
    for msg in history[-10:]:
        messages.append({"role": msg["role"], "content": msg["content"] if not isinstance(msg["content"], list) else "[Image]"})
    messages.append({"role": "user", "content": user_content})

    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={"model": model, "messages": messages, "max_tokens": 800, "temperature": 0.2}, timeout=20)
        reply = r.json()['choices'][0]['message']['content']
        history.append({"role": "user", "content": message or "Image"})
        history.append({"role": "assistant", "content": reply})
        redis_set('chat_history', history[-50:])
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/radar/bankroll/resultat', methods=['POST'])
def resultat_mise():
    data = request.get_json()
    bankroll = load_bankroll()
    message_ia = ""
    for m in bankroll['mises']:
        if m['id'] == data.get('id'):
            m['statut'] = data.get('statut')
            if m['statut'] == 'WIN':
                gain = round(m['montant'] * m['cote'] - m['montant'], 2)
                m['gain'] = gain
                bankroll['disponible'] = round(bankroll['disponible'] + m['montant'] + gain, 2)
                message_ia = f"🔥 BOOM ! Pari validé sur {m['match']}. +{gain}€ ! Le Radar V6 est imbattable ! 🚀"
            else:
                m['gain'] = -m['montant']
                message_ia = f"📉 Perte sur {m['match']}. On reste focus Gaël, l'Edge finira par payer. 📡"
            break
    save_bankroll(bankroll)
    return jsonify({"status": "ok", "bankroll": bankroll, "message_ia": message_ia})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
  
