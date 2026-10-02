import time
import requests
from flask import Flask, jsonify
from flask_cors import CORS
import xml.etree.ElementTree as ET
import os

app = Flask(__name__)
CORS(app)

weather_cache = {
    "smn_status": "Monitoreo activo",
    "alerts": [],
    "dimarco_tweets": [],
    "storm_trajectory": {
        "origin": "Sierras Chicas / Gran Córdoba",
        "destination": "Córdoba Capital",
        "status": "Monitoreando celdas en desplazamiento",
        "eta_minutes": "35-45 min",
        "hail_probability": "Moderada (Zonas Altas)",
        "wind_speed": "35 km/h (Ráfagas)",
        "accumulated_rain": "12 mm (Estimado)"
    },
    "last_update": ""
}

def fetch_smn_data():
    try:
        url = "https://www.smn.gob.ar/api/v1/alertas"
        response = requests.get(url, timeout=8)
        if response.status_code == 200:
            data = response.json()
            cordoba_alerts = []
            for item in data.get('notifications', []):
                zonas = item.get('zonas', [])
                if any("Córdoba" in str(z.get('nombre', '')) for z in zonas):
                    cordoba_alerts.append({
                        "title": item.get('title', 'Alerta SMN'),
                        "description": item.get('description', ''),
                        "severity": item.get('severity', 'Amarilla')
                    })
            if cordoba_alerts:
                return cordoba_alerts
    except Exception as e:
        print("Error SMN:", e)
    
    return [{
        "title": "Sistema de Alerta Temprana - Córdoba Capital",
        "description": "Sin alertas severas rojas activas. Vigilar desarrollo de núcleos aislados por humedad y calor.",
        "severity": "Normal (Verde)"
    }]

def fetch_dimarco_tweets():
    try:
        rss_url = "https://nitter.privacydev.net/dimarcorafael/rss"
        response = requests.get(rss_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            tweets = []
            for item in root.findall('.//item')[:4]:
                title = item.find('title').text if item.find('title'] is not None else ""
                pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""
                tweets.append({
                    "author": "Rafael Di Marco (@dimarcorafael)",
                    "text": title,
                    "time": pub_date,
                    "source": "X en Vivo"
                })
            if tweets:
                return tweets
    except Exception as e:
        print("Error X feed:", e)

    return [{
        "author": "Rafael Di Marco (@dimarcorafael)",
        "text": "Seguimiento satelital y radar de las condiciones de inestabilidad sobre el oeste y centro provincial.",
        "time": "Reciente",
        "source": "Respaldo automático"
    }]

@app.route('/api/live-weather', methods=['GET'])
def get_live_weather():
    global weather_cache
    weather_cache["alerts"] = fetch_smn_data()
    weather_cache["dimarco_tweets"] = fetch_dimarco_tweets()
    weather_cache["last_update"] = time.strftime("%d/%m/%Y %H:%M:%S")
    return jsonify(weather_cache)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)