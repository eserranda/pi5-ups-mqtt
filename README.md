# pi5-ups-mqtt

Kirim data UPS **Geekworm X1202** pada **Raspberry Pi 5** ke **Home Assistant** lewat MQTT: level baterai, tegangan, status charging, estimasi sisa waktu, dan safe shutdown otomatis.

> Proyek komunitas. Tidak berafiliasi dengan Geekworm, SupTronics, atau Raspberry Pi Ltd.

## Fitur

- Level baterai (%), tegangan, status listrik (AC), dan status charging
- Estimasi sisa waktu saat memakai baterai, dan estimasi waktu sampai penuh saat mengisi
- Safe shutdown otomatis saat listrik padam dan baterai mencapai batas (bawaan 20%)
- Entity muncul otomatis di Home Assistant lewat MQTT Discovery, dengan status online/offline
- Berjalan sebagai service systemd, kredensial disimpan terpisah dari kode

## Kebutuhan

- Raspberry Pi 5 + UPS Geekworm X1202 dengan 4 baterai 18650 **tanpa proteksi bawaan**
- Raspberry Pi OS dengan I2C aktif
- Broker MQTT (misalnya Mosquitto) dan integrasi MQTT di Home Assistant
- Paket Python: `smbus2`, `paho-mqtt`, dan `gpiod` (libgpiod **versi 1**, memakai `chip.get_line`)

```bash
sudo raspi-config nonint do_i2c 0
sudo apt install -y i2c-tools python3-smbus2 python3-paho-mqtt python3-libgpiod
i2cdetect -y 1    # harus muncul angka 36
```

Jika memakai versi `python3-gpiod` 2.x, bagian GPIO di script perlu disesuaikan karena APInya berbeda.

## Instalasi

```bash
git clone https://github.com/<eserranda>/pi5-ups-mqtt.git
cd pi5-ups-mqtt
sudo ./install.sh
```

Saat pertama kali dijalankan, installer membuat `/etc/x1202-mqtt.env` dengan nilai contoh. Edit username dan password MQTT, lalu jalankan service:

```bash
sudo nano /etc/x1202-mqtt.env
sudo systemctl enable --now x1202-ups.service
sudo journalctl -u x1202-ups.service -f
```

Jika tidak ada tulisan `Error reading UPS` di log, entity akan muncul otomatis di HA pada perangkat **Raspberry Pi 5 UPS (X1202)**.

## Konfigurasi

Variabel berikut dibaca dari `/etc/x1202-mqtt.env`:

| Variabel | Bawaan | Fungsi |
|---|---|---|
| `MQTT_USER` | - | Username MQTT |
| `MQTT_PASS` | - | Password MQTT |
| `MQTT_BROKER` | `127.0.0.1` | Alamat broker |
| `MQTT_PORT` | `1883` | Port broker |
| `SAFE_SHUTDOWN_PERCENT` | `20` | Batas persen untuk shutdown otomatis |

Parameter lain (jeda pembacaan, penghalusan estimasi, jendela riwayat) ada di bagian atas `x1202_mqtt.py`.

## Entity di Home Assistant

| Entity | Isi |
|---|---|
| UPS Battery Level | Persen baterai |
| UPS Battery Voltage | Tegangan baterai (V) |
| UPS Status | Discharging, Charging, Charging (Topping Off), Fully Charged, atau AC Connected (Idle) |
| UPS AC Power | On jika listrik tersambung |
| UPS Charging | On jika baterai sedang diisi |
| UPS Charge Rate | Laju perubahan persen per jam (positif saat mengisi, negatif saat dipakai) |
| UPS Estimated Runtime | Perkiraan sisa waktu sampai batas shutdown |
| UPS Time To Full | Perkiraan waktu sampai penuh |

## Cara kerja singkat

- Tegangan dan persen dibaca dari fuel gauge lewat I2C (alamat `0x36`). Status listrik dibaca dari GPIO 6.
- Laju perubahan persen dihitung dari riwayat 30 menit terakhir, bukan dari register CRATE, karena register itu tidak berubah pada board yang diuji.
- Estimasi sisa waktu adalah `(persen - batas shutdown) / laju turun per jam`, dihaluskan dengan rata-rata bergerak. Angkanya perkiraan berdasarkan beban saat ini dan baru akurat setelah baterai berjalan beberapa jam.
- Shutdown dijalankan jika listrik padam dan persen di bawah batas selama 3 pembacaan berturut-turut (sekitar 30 detik).

## Catatan penting

- Jangan memberi daya ke Pi lewat USB-C Pi sendiri saat memakai X1202. Gunakan port USB-C atau jack DC pada X1202.
- Jangan pakai baterai 18650 yang memiliki rangkaian proteksi bawaan, dan perhatikan polaritas saat memasang.
- Jika UPS tidak mendeteksi Pi (kontak pogo pin kurang baik), UPS mati sendiri dalam sekitar 3 detik.
- Service berjalan sebagai root karena butuh akses I2C, GPIO, dan `poweroff`. Pastikan `/etc/x1202-mqtt.env` berizin `600`.
- Gunakan baterai berkualitas dari produsen yang jelas. Pemakaian baterai Li-ion adalah tanggung jawab pengguna.

## Pemecahan masalah

| Gejala | Solusi |
|---|---|
| `Connection Refused: not authorised` | Cek username dan password di `/etc/x1202-mqtt.env` |
| Semua sensor unavailable | Cek `systemctl status x1202-ups.service` dan log-nya |
| `Error reading UPS` berulang | Pastikan I2C aktif dan `i2cdetect -y 1` menampilkan 36 |
| UPS Charge Rate Unknown | Normal, tunggu sekitar 10 menit setelah restart atau perubahan listrik |
| Entity lama tidak bisa dihapus | Kirim pesan kosong dengan flag retain ke topik `homeassistant/sensor/<nama>/config`, lalu hapus di HA |

## Menghapus

```bash
sudo systemctl disable --now x1202-ups.service
sudo rm /etc/systemd/system/x1202-ups.service /usr/local/bin/x1202_mqtt.py /etc/x1202-mqtt.env
sudo systemctl daemon-reload
```

## Lisensi

MIT, lihat file [LICENSE](LICENSE).
