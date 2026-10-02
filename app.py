from flask import Flask, jsonify
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

@app.route('/')
def home():
    return "🤖 ¡El bot meteorológico para Córdoba Capital y Gran Córdoba está online y funcionando!"

@app.route('/api/live-weather', methods=['GET'])
def live_weather():
    current_time_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    data = {
        "smn_status": "Monitoreo activo",
        "alerts": [
            {
                "title": "Sistema de Alerta Temprana - Córdoba Capital",
                "severity": "Normal (Verde)",
                "description": "Monitoreo permanente de núcleos convectivos aislados por altas temperaturas y humedad en las sierras."
            }
        ],
        "storm_tracking": {
            "current_location": "Sierras Chicas (Villa Allende / Mendiolaza)",
            "trajectory_path": ["Sierras Chicas", "Zona Noroeste (Argüello / Argüello Norte)", "Córdoba Capital (Centro / Nueva Cba)"],
            "current_step_index": 1, # 0: Sierras, 1: Noroeste, 2: Capital
            "eta_capital_minutes": 25,
            "eta_display": "25 min",
            "hail_confirmed": True, # Cambiar a False si no hay reporte de granizo
            "hail_probability": "Moderada (Zona Alta / Norte)",
            "wind_speed": "42 km/h (Ráfagas)",
            "accumulated_rain": "16 mm (Estimado)"
        },
        "dimarco_tweets": [
            {
                "id": 1,
                "time": "Hace 5 min",
                "author": "Rafael Di Marco (@dimarcorafael)",
                "text": "¡Atención! Núcleo con fuerte actividad eléctrica y caída confirmada de granizo pequeño en zona de Sierras Chicas avanzando hacia Córdoba Capital.",
                "interaction_summary": "💬 24 respuestas · 🔄 38 RT · ❤️️ 140 Me gusta",
                "user_replies": [
                    {"user": "@marcos_cba", "text": "¡Confirmo granizo chico por Villa Allende bajando hacia Argüello!"},
                    {"user": "@valeria_met", "text": "Cielo totalmente cerrado y granizo sectorizado en altura."}
                ]
            },
            {
                "id": 2,
                "time": "Hace 30 min",
                "author": "Rafael Di Marco (@dimarcorafael)",
                "text": "Seguimiento satelital de celdas aisladas con desplazamiento este-noreste hacia el Gran Córdoba.",
                "interaction_summary": "💬 12 respuestas · 🔄 15 RT · ❤️ 65 Me gusta",
                "user_replies": [
                    {"user": "@clima_unvm", "text": "Atentos a las ráfagas en ruta."}
                ]
            }
        ],
        "last_update": current_time_str
    }
    return jsonify(data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
