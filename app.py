from flask import Flask, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
CORS(app)

arg_tz = timezone(timedelta(hours=-3))

@app.route('/')
def home():
    return "🤖 Bot meteorológico Córdoba - API activa."

@app.route('/api/live-weather', methods=['GET'])
def live_weather():
    now = datetime.now(arg_tz)
    current_time_str = now.strftime("%d/%m/%Y %H:%M:%S")

    # NOTA: estos datos de seguimiento de tormenta y alertas SMN siguen siendo
    # valores fijos de ejemplo. Los tuits de @dimarcorafael ya NO se generan acá:
    # se embeben en vivo directo desde X en el frontend (ver index.html).
    # Cuando quieras, el siguiente paso es reemplazar este bloque por datos
    # reales del SMN (alertas) en vez de texto fijo.
    data = {
        "smn_status": "Monitoreo activo",
        "alerts": [
            {
                "title": "Sistema de Alerta Temprana - Córdoba Capital",
                "severity": "Moderada / Seguimiento en Vivo",
                "description": f"Sincronizado a las {now.strftime('%H:%M')} hs."
            }
        ],
        "storm_tracking": {
            "current_location": "Traslasierra / Sierras Chicas",
            "trajectory_path": ["Traslasierra", "Sierras Chicas", "Córdoba Capital"],
            "current_step_index": 1,
            "eta_capital_minutes": 40,
            "eta_display": "40 min",
            "hail_confirmed": False,
            "hail_probability": "Baja en Capital / Vigente en Altas Cumbres",
            "wind_speed": "Ráfagas de hasta 81.7 km/h en origen",
            "accumulated_rain": "Variable"
        },
        "last_update": current_time_str
    }
    return jsonify(data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
