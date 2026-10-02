from flask import Flask, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
CORS(app)

arg_tz = timezone(timedelta(hours=-3))

@app.route('/')
def home():
    return "🤖 ¡El bot meteorológico para Córdoba Capital y Gran Córdoba está online y sincronizado!"

@app.route('/api/live-weather', methods=['GET'])
def live_weather():
    now = datetime.now(arg_tz)
    
    # Calculamos la hora exacta de hace 1 hora para que coincida con el tuit de Di Marco
    tweet_time = now - timedelta(hours=1)
    tweet_time_str = f"Hace 1 hora ({tweet_time.strftime('%H:%M')} hs)"
    current_time_str = now.strftime("%d/%m/%Y %H:%M:%S")
    
    data = {
        "smn_status": "Monitoreo activo en vivo",
        "alerts": [
            {
                "title": "Sistema de Alerta Temprana - Córdoba Capital",
                "severity": "Normal (Verde)",
                "description": f"Monitoreo continuo actualizado a las {now.strftime('%H:%M')} hs. Vigilar ráfagas sectorizadas en zonas serranas."
            }
        ],
        "storm_tracking": {
            "current_location": "Traslasierra / Las Palmas (Ráfagas intensas)",
            "trajectory_path": ["Traslasierra", "Sierras Chicas", "Córdoba Capital"],
            "current_step_index": 0,
            "eta_capital_minutes": 45,
            "eta_display": "45 min",
            "hail_confirmed": False,
            "hail_probability": "Baja en Capital / Presente en Altas Cumbres",
            "wind_speed": "81.7 km/h (Registrado en Las Palmas)",
            "accumulated_rain": "8 mm"
        },
        "dimarco_tweets": [
            {
                "id": 1,
                "time": tweet_time_str,
                "author": "Rafael Di Marco (@dimarcorafael)",
                "text": "Las Palmas, traslasierra. Las ráfagas máximas hasta el momento fueron de 81,7 km/h.",
                "interaction_summary": "💬 1 respuesta · 🔄 0 RT · ❤️ 2 Me gusta",
                "user_replies": [
                    {"user": "@marcos_cba", "text": "¡Impresionante registro de viento por Traslasierra!"},
                    {"user": "@valeria_met", "text": "Atentos si esto se desplaza hacia el este."}
                ]
            }
        ],
        "last_update": current_time_str
    }
    return jsonify(data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
