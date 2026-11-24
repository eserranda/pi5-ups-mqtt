#!/usr/bin/env python3
"""Kirim data UPS Geekworm X1202 (Raspberry Pi 5) ke Home Assistant lewat MQTT."""
import json
import os
import signal
import struct
import subprocess
import threading
import time
from collections import deque

import gpiod
import paho.mqtt.client as mqtt
import smbus2

# --- KONFIGURASI (kredensial dari environment) ---
MQTT_BROKER = os.getenv("MQTT_BROKER", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")
SAFE_SHUTDOWN_PERCENT = int(os.getenv("SAFE_SHUTDOWN_PERCENT", "20"))
SHUTDOWN_CONFIRM_READS = 3   # harus rendah 3x berturut-turut
INTERVAL = 10                # detik
EMA_ALPHA = 0.2              # makin kecil makin halus tapi lambat bereaksi
MIN_DRAIN_RATE = 3.0         # %/jam, di bawah ini estimasi runtime tidak dihitung
MIN_CHARGE_RATE = 3.0        # %/jam, di bawah ini estimasi waktu penuh tidak dihitung
RATE_WINDOW = 1800           # detik, jendela riwayat (30 menit)
MIN_SPAN = 600               # detik, minimal data sebelum laju dihitung (10 menit)

STATE_TOPIC = "rpi5/ups/state"
AVAIL_TOPIC = "rpi5/ups/availability"

# --- HARDWARE ---
bus = smbus2.SMBus(1)
I2C_ADDR = 0x36

# GPIO 6: 1 = AC terhubung, 0 = AC padam
chip = gpiod.Chip("gpiochip4")
pld_line = chip.get_line(6)
pld_line.request(consumer="x1202_ups", type=gpiod.LINE_REQ_DIR_IN)

history = deque()
last_ac = None


def calc_rate(capacity, ac_connected):
    """Laju perubahan persen per jam. None jika data belum cukup."""
    global last_ac
    now = time.time()
    if ac_connected != last_ac:      # status listrik berubah, mulai ulang
        history.clear()
        last_ac = ac_connected
    history.append((now, capacity))
    while history and now - history[0][0] > RATE_WINDOW:
        history.popleft()
    t0, c0 = history[0]
    span = now - t0
    if span < MIN_SPAN:
        return None
    return round((capacity - c0) / span * 3600, 1)


def read_ups():
    read_v = bus.read_word_data(I2C_ADDR, 2)
    swapped_v = struct.unpack("<H", struct.pack(">H", read_v))[0]
    voltage = round(swapped_v * 1.25 / 1000 / 16, 2)

    read_c = bus.read_word_data(I2C_ADDR, 4)
    swapped_c = struct.unpack("<H", struct.pack(">H", read_c))[0]
    capacity = min(round(swapped_c / 256, 1), 100.0)

    ac_connected = pld_line.get_value()
    crate = calc_rate(capacity, ac_connected)

    if ac_connected == 0:
        status = "Discharging (On Battery)"
        charging = 0
    elif capacity >= 99:
        status = "Fully Charged"
        charging = 0
    elif crate is not None and crate > 1.0:
        status = "Charging"
        charging = 1
    elif voltage >= 4.18:
        status = "Charging (Topping Off)"
        charging = 1
    elif crate is None:
        status = "Charging"          # data laju belum cukup, anggap mengisi
        charging = 1
    else:
        status = "AC Connected (Idle)"
        charging = 0

    return voltage, capacity, ac_connected, status, charging, crate


def format_duration(minutes):
    if minutes is None:
        return "Menghitung..."
    h, m = divmod(int(minutes), 60)
    if h > 0:
        return f"~{h} jam {m} menit"
    return f"~{m} menit"


def estimate_runtime(capacity, ema_rate):
    """Menit tersisa sampai batas shutdown. None jika belum bisa dihitung."""
    if ema_rate is None or -ema_rate < MIN_DRAIN_RATE:
        return None
    usable = max(capacity - SAFE_SHUTDOWN_PERCENT, 0)
    return round(usable / (-ema_rate) * 60)


def estimate_time_to_full(capacity, rate):
    """Teks estimasi waktu sampai penuh (100%)."""
    if capacity >= 99:
        return "Terisi penuh"
    if rate is None:
        return "Menghitung..."
    if rate < MIN_CHARGE_RATE:
        return "Hampir penuh" if capacity >= 90 else "Pengisian lambat"
    return format_duration(round((100 - capacity) / rate * 60))


# --- MQTT ---
try:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)  # paho 2.x
except AttributeError:
    client = mqtt.Client()                                   # paho 1.x

if MQTT_USER:
    client.username_pw_set(MQTT_USER, MQTT_PASS)
client.will_set(AVAIL_TOPIC, "offline", retain=True)
client.reconnect_delay_set(min_delay=1, max_delay=30)

device_info = {
    "identifiers": ["geekworm_x1202_ups"],
    "name": "Raspberry Pi 5 UPS (X1202)",
    "manufacturer": "Geekworm",
    "model": "X1202",
}

common = {
    "state_topic": STATE_TOPIC,
    "availability_topic": AVAIL_TOPIC,
    "expire_after": 60,
    "device": device_info,
}

configs = [
    ("homeassistant/sensor/rpi5_ups_battery/config", {
        **common,
        "name": "UPS Battery Level",
        "value_template": "{{ value_json.capacity }}",
        "unit_of_measurement": "%",
        "device_class": "battery",
        "state_class": "measurement",
        "unique_id": "rpi5_x1202_battery",
    }),
    ("homeassistant/sensor/rpi5_ups_voltage/config", {
        **common,
        "name": "UPS Battery Voltage",
        "value_template": "{{ value_json.voltage }}",
        "unit_of_measurement": "V",
        "device_class": "voltage",
        "state_class": "measurement",
        "unique_id": "rpi5_x1202_voltage",
    }),
    ("homeassistant/sensor/rpi5_ups_status/config", {
        **common,
        "name": "UPS Status",
        "value_template": "{{ value_json.status }}",
        "icon": "mdi:battery-charging",
        "unique_id": "rpi5_x1202_status",
    }),
    ("homeassistant/binary_sensor/rpi5_ups_power/config", {
        **common,
        "name": "UPS AC Power",
        "value_template": "{{ 'ON' if value_json.ac_connected == 1 else 'OFF' }}",
        "device_class": "power",
        "unique_id": "rpi5_x1202_ac_power",
    }),
    ("homeassistant/binary_sensor/rpi5_ups_charging/config", {
        **common,
        "name": "UPS Charging",
        "value_template": "{{ 'ON' if value_json.charging == 1 else 'OFF' }}",
        "device_class": "battery_charging",
        "unique_id": "rpi5_x1202_charging",
    }),
    ("homeassistant/sensor/rpi5_ups_crate/config", {
        **common,
        "name": "UPS Charge Rate",
        "value_template": "{{ value_json.crate }}",
        "unit_of_measurement": "%/h",
        "state_class": "measurement",
        "icon": "mdi:battery-sync",
        "unique_id": "rpi5_x1202_crate",
    }),
    ("homeassistant/sensor/rpi5_ups_runtime_text/config", {
        **common,
        "name": "UPS Estimated Runtime",
        "value_template": "{{ value_json.runtime_text }}",
        "icon": "mdi:timer-outline",
        "unique_id": "rpi5_x1202_runtime_text",
    }),
    ("homeassistant/sensor/rpi5_ups_time_to_full/config", {
        **common,
        "name": "UPS Time To Full",
        "value_template": "{{ value_json.charge_text }}",
        "icon": "mdi:battery-clock",
        "unique_id": "rpi5_x1202_time_to_full",
    }),
]


def on_connect(client, userdata, flags, rc, *args):
    # *args supaya kompatibel dengan paho 1.x dan 2.x
    for topic, payload in configs:
        client.publish(topic, json.dumps(payload), retain=True)
    client.publish(AVAIL_TOPIC, "online", retain=True)


client.on_connect = on_connect
client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
client.loop_start()

# --- LOOP UTAMA ---
stop_event = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
signal.signal(signal.SIGINT, lambda *_: stop_event.set())

low_count = 0
ema_rate = None

while not stop_event.is_set():
    try:
        voltage, capacity, ac_connected, status, charging, crate = read_ups()

        runtime_min = None
        if ac_connected == 0:
            # Memakai baterai: hitung estimasi sisa waktu
            if crate is not None and crate < 0:
                ema_rate = crate if ema_rate is None else (EMA_ALPHA * crate + (1 - EMA_ALPHA) * ema_rate)
            runtime_min = estimate_runtime(capacity, ema_rate)
            if runtime_min is not None:
                runtime_text = format_duration(runtime_min)
            elif ema_rate is None:
                runtime_text = "Menghitung..."
            else:
                runtime_text = "Beban sangat ringan"
            charge_text = "Tidak sedang mengisi"
        else:
            # Listrik tersambung: hitung estimasi waktu sampai penuh
            ema_rate = None
            runtime_text = "Listrik tersambung"
            charge_text = estimate_time_to_full(capacity, crate)

        client.publish(STATE_TOPIC, json.dumps({
            "voltage": voltage,
            "capacity": capacity,
            "ac_connected": ac_connected,
            "charging": charging,
            "crate": crate,
            "status": status,
            "runtime_min": runtime_min,
            "runtime_text": runtime_text,
            "charge_text": charge_text,
        }))

        if ac_connected == 0 and capacity <= SAFE_SHUTDOWN_PERCENT:
            low_count += 1
        else:
            low_count = 0

        if low_count >= SHUTDOWN_CONFIRM_READS:
            client.publish(STATE_TOPIC, json.dumps({
                "voltage": voltage,
                "capacity": capacity,
                "ac_connected": 0,
                "charging": 0,
                "crate": crate,
                "status": "Shutting down (low battery)",
                "runtime_min": 0,
                "runtime_text": "Shutting down",
                "charge_text": "Tidak sedang mengisi",
            }))
            subprocess.run(["systemctl", "poweroff"])
            break

    except Exception as e:
        print(f"Error reading UPS: {e}", flush=True)

    stop_event.wait(INTERVAL)

# --- CLEANUP ---
client.publish(AVAIL_TOPIC, "offline", retain=True).wait_for_publish(timeout=2)
client.loop_stop()
client.disconnect()
pld_line.release()
