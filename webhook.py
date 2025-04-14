from flask import Flask, request
from dotenv import load_dotenv
import os
import json
import requests
import database

# טוען משתני סביבה
load_dotenv()

app = Flask(__name__)

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")  # VERIFY_TOKEN מקובץ .env

# פונקציה לשליפת ה־page tokens מה־API של פייסבוק
def get_page_tokens():
    url = "https://graph.facebook.com/v22.0/me/accounts"
    params = {
        "fields": "access_token,name,id",
        "access_token": os.getenv('FACE_TOKEN')  # טוקן אישי מה־.env
    }

    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    page_tokens = {page['id']: page['access_token'] for page in data.get('data', [])}
    return page_tokens

# דף הבית
@app.route('/')
def home():
    return "Webhook is running!"

# ה־Webhook
@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')

        if token == VERIFY_TOKEN:
            return challenge, 200
        else:
            return 'Invalid verification token', 403

    elif request.method == 'POST':
        data = request.json

        for entry in data.get('entry', []):
            for change in entry.get('changes', []):
                if change.get('field') == 'feed' and change.get('value', {}).get('item') == 'comment':
                    value = change.get('value', {})
                    message = value.get('message', '')
                    post_id = value.get('post_id')
                    sender_id = value.get('from', {}).get('id')
                    sender_name = value.get('from', {}).get('name')

                    if 'LINK' in message.upper():
                        send_private_message(post_id, sender_id, sender_name)

        return 'EVENT_RECEIVED', 200

def send_private_message(post_id, user_id, sender_name):
    product_data = database.get_product_details_by_post(post_id)
    page_tokens = get_page_tokens()

    # שליפת הטוקן המתאים לפי post_id
    page_id = post_id.split('_')[0]
    page_token = page_tokens.get(page_id)

    if not page_token:
        return

    url = f'https://graph.facebook.com/v11.0/{user_id}/messages'
    headers = {'Content-Type': 'application/json'}

    data = {
        "recipient": {
            "id": user_id
        },
        "message": {
            "text": f"""Hey {sender_name},
here's your link! 🎉

{product_data['ProductDesc']}

{product_data['PromotionUrl']}
Check it out!
"""
        }
    }

    params = {'access_token': page_token}
    requests.post(url, headers=headers, json=data, params=params)

if __name__ == '__main__':
    app.run(port=5000, debug=False)
