from flask import Flask, render_template, jsonify, Response, request, session, redirect, url_for
import sqlite3, json, threading, time
import paho.mqtt.client as mqtt
import config 
import time

app = Flask(__name__)
app.secret_key = config.SECRET_KEY # Necesario para el Login

mqtt_client = None
GRUPO = "grupo-8" 
TOPIC_LECTURAS = f"sismografo/{GRUPO}/lecturas"
TOPIC_EVENTOS = f"sismografo/{GRUPO}/eventos"
TOPIC_COMANDOS = f"sismografo/{GRUPO}/comandos"
TOPIC_ESTADO = f"sismografo/{GRUPO}/estado"

# Variable global para guardar el estado del ESP32
estado_dispositivo = {"online": False, "uptime_s": 0, "mem_free": 0, "rssi": 0}

def init_db():
    conn = sqlite3.connect('sismografo.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS lecturas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT, ts INTEGER, vib_g REAL, rms REAL, pico REAL, estado TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        evento_id TEXT, device_id TEXT, inicio INTEGER, duracion_ms INTEGER, vib_max_g REAL, severidad TEXT)''')
    conn.commit()
    conn.close()


def on_message(client, userdata, msg):
    global estado_dispositivo
    try:
        topic = msg.topic
        data = json.loads(msg.payload.decode())
        
        # Ignoramos el tiempo del ESP32 y usamos la hora exacta de la PC
        ahora = int(time.time()) 

        if topic == TOPIC_ESTADO:
            estado_dispositivo = data
            return

        conn = sqlite3.connect('sismografo.db')
        c = conn.cursor()
        
        if topic == TOPIC_LECTURAS:
            # Usamos 'ahora' en lugar de data['ts']
            c.execute("INSERT INTO lecturas (device_id, ts, vib_g, rms, pico, estado) VALUES (?, ?, ?, ?, ?, ?)",
                      (data['device_id'], ahora, data['vib_g'], data['rms'], data['pico'], data['estado']))
                      
        elif topic == TOPIC_EVENTOS:
            # También podemos usar la hora de la PC para los eventos
            c.execute("INSERT INTO eventos (evento_id, device_id, inicio, duracion_ms, vib_max_g, severidad) VALUES (?, ?, ?, ?, ?, ?)",
                      (data['evento_id'], data['device_id'], ahora, data['duracion_ms'], data['vib_max_g'], data['severidad']))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print("Error procesando mensaje:", e)

def mqtt_thread():
    global mqtt_client
    while True:
        try:
            mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
            mqtt_client.on_message = on_message
            mqtt_client.connect("test.mosquitto.org", 1883, 60)
            
            mqtt_client.subscribe(TOPIC_LECTURAS)
            mqtt_client.subscribe(TOPIC_EVENTOS)
            mqtt_client.subscribe(TOPIC_ESTADO)
            
            print(" Servidor MQTT conectado y escuchando...")
            mqtt_client.loop_forever()
        except Exception as e:
            print(f" Error MQTT: {e}. Reintentando en 5s...")
            time.sleep(5)

# --- RUTA DE LOGIN ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['username'] == config.USUARIO and request.form['password'] == config.PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        return render_template('login.html', error="Usuario o contraseña incorrectos")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# --- RUTAS PROTEGIDAS DEL DASHBOARD ---
@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/api/datos')
def datos():
    if not session.get('logged_in'): return jsonify({"error": "No autorizado"}), 401
    conn = sqlite3.connect('sismografo.db')
    c = conn.cursor()
    c.execute("SELECT vib_g, ts FROM lecturas ORDER BY id DESC LIMIT 30")
    rows = c.fetchall()
    conn.close()
    return jsonify([{"vib_g": r[0], "ts": r[1]} for r in reversed(rows)])

@app.route('/api/eventos')
def eventos():
    if not session.get('logged_in'): return jsonify({"error": "No autorizado"}), 401
    conn = sqlite3.connect('sismografo.db')
    c = conn.cursor()
    c.execute("SELECT * FROM eventos ORDER BY id DESC LIMIT 15")
    rows = c.fetchall()
    conn.close()
    return jsonify(rows)

@app.route('/api/estado')
def estado():
    if not session.get('logged_in'): return jsonify({"error": "No autorizado"}), 401
    return jsonify(estado_dispositivo)

@app.route('/api/exportar')
def exportar_csv():
    if not session.get('logged_in'): return "No autorizado", 401
    conn = sqlite3.connect('sismografo.db')
    c = conn.cursor()
    c.execute("SELECT * FROM eventos")
    rows = c.fetchall()
    conn.close()
    csv_data = "ID,Evento_ID,Device_ID,Inicio_Epoch,Duracion_ms,Vib_Max_g,Severidad\n"
    for r in rows:
        csv_data += ",".join(map(str, r)) + "\n"
    return Response(csv_data, mimetype="text/csv", headers={"Content-disposition": "attachment; filename=eventos_sismicos.csv"})

@app.route('/api/comando', methods=['POST'])
def enviar_comando():
    if not session.get('logged_in'): return jsonify({"error": "No autorizado"}), 401
    global mqtt_client
    datos = request.json
    try:
        if mqtt_client and mqtt_client.is_connected():
            payload = json.dumps(datos)
            mqtt_client.publish(TOPIC_COMANDOS, payload)
            return jsonify({"status": "ok", "msg": "Comando enviado"})
        else:
            return jsonify({"status": "error", "msg": "MQTT no conectado"}), 500
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500

if __name__ == '__main__':
    init_db()
    threading.Thread(target=mqtt_thread, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=True)