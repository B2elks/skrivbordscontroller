# Kopiera till config.py (gitignorerad) och fyll i.
WIFI_SSID = "[FYLL I: nätverksnamn]"
WIFI_LOSEN = "[FYLL I: wifi-lösenord]"
# Serverns publika adress, utan avslutande snedstreck.
SERVER = "https://bord.example.com"
ENHET_TOKEN = "[FYLL I: samma som ENHET_TOKEN i /etc/skrivbord/secrets.env]"
# Hårt tak per drag i ms. Sätt till bordets fulla restid + 2 s efter kalibreringen.
MAX_MS = 40000
# True: relämodulen drar när ingången är HÖG (aktivt hög, "high level trigger").
# False: den drar när ingången är LÅG (aktivt låg). Se README, "Aktivt hög eller låg".
AKTIVT_HOG = True
