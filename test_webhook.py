import os
import requests

# Rode assim:  WEBHOOK_URL="https://discord.com/api/webhooks/..." python test_webhook.py
WEBHOOK_URL = os.environ["WEBHOOK_URL"]

r = requests.post(WEBHOOK_URL, json={
    "content": "🧪 Teste do webhook! Se você vê esta mensagem, o webhook está funcionando!"
})
print(f"Status: {r.status_code}")
print(f"Resposta: {r.text}")
