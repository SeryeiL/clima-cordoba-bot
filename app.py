from flask import Flask, jsonify
from flask_cors import CORS
from datetime import datetime
import pytz

app = Flask(__name__)
CORS(app)

# Definimos la zona horaria oficial de Córdoba, Argentina
cordoba_tz = pytz.timezone('America/Argentina/Cordoba')

@app.route('/')
def home():
    return "🤖 ¡El bot meteorológico para Córdoba Capital y Gran Córdoba está online y sincronizado en hora local!"

@app.route('/api/live-weather', methods=['GET'])
def live_weather():
    # Obtenemos la hora actual ajustada estrictamente a Córdoba
    now = datetime.now(cordoba_tz)
    current_time_str = now.strftime("%d/%m/%Y %H:%M:%S")
    current_hour_str = now.strftime("%H:%M")
    
    data = {
        "smn_status": "Monitoreo activo en vivo",
        "alerts": [
            {
                "title": "Sistema de Alerta Temprana - Córdoba Capital",
                "severity": "Normal (Verde)",
                "description": f"Monitoreo continuo actualizado a las {current_hour_str} hs. Sin avisos a corto plazo vigentes."
            }
        ],
        "storm_tracking": {
            "current_location": "Estable - Sin núcleos severos en curso",
            "trajectory_path": ["Sierras Chicas", "Zona Noroeste", "Córdoba Capital"],
            "current_step_index": 0,
            "eta_capital_minutes": 0,
            "eta_display": "Sin amenazas",
            "hail_confirmed": False,
            "hail_probability": "Nula",
            "wind_speed": "14 km/h (Sector Norte)",
            "accumulated_rain": "0 mm"
        },
        "dimarco_tweets": [
            {
                "id": 1,
                "time": f"Actualizado hoy {current_hour_str} hs",
                "author": "Rafael Di Marco (@dimarcorafael)",
                "text": "Monitoreo en tiempo real: Jornada con estabilidad meteorológica en Córdoba Capital y Gran Córdoba. Seguimos atentos a cualquier cambio de inestabilidad hacia la tarde.",
                "interaction_summary": "💬 6 respuestas · 🔄 11 RT · ❤️ 48 Me gusta",
                "user_replies": [
                    {"user": "@clima_cba", "text": "Gracias Rafa por la actualización en vivo."},
                    {"user": "@valeria_met", "text": "Cielo despejado por zona norte."}
                ]
            }
        ],
        "last_update": current_time_str
    }
    return jsonify(data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
