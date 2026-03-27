import os, requests
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route('/')
def home():
    return "📡 OMEGA RELAIS LIVE ✅"

@app.route('/mega-data')
def mega_data():
    api_key = os.environ.get('SPORTS_API_PRO')
    headers = {"x-api-key": api_key}
    try:
        # Test sur le live foot
        url = "https://v2.football.sportsapipro.com/api/live"
        r = requests.get(url, headers=headers, timeout=10).json()
        return jsonify(r)
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
  
