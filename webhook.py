from flask import Flask, request
import os
import json
import requests
import database

app = Flask(__name__)

# הטוקן שאתה מגדיר בפייסבוק
VERIFY_TOKEN = "fb_webhook_2025"
PAGE_ACCESS_TOKEN = 'your_page_access_token_here'  # הטוקן של הדף

# דף הבית - סתם בשביל לבדוק שהשרת רץ
@app.route('/')
def home():
    return "Webhook is running!"

# ה־Webhook
@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        # שלב האימות עם פייסבוק
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        
        if token == VERIFY_TOKEN:
            return challenge, 200
        else:
            return 'Invalid verification token', 403

    elif request.method == 'POST':
        # קבלת מידע מה־Webhook
        data = request.json
        print("Received webhook data:", json.dumps(data, indent=2))

        # נבדוק אם מדובר בהודעה של comment
        for entry in data.get('entry', []):
            for change in entry.get('changes', []):
                field = change.get('field')
                if field == 'feed':
                    value = change.get('value', {})
                    item = value.get('item')  # comment או like

                    if item == 'comment':
                        comment_id = value.get('comment_id')
                        message = value.get('message')
                        post_id = value.get('post_id')
                        sender_id = value.get('from', {}).get('id')
                        sender_name = value.get('from', {}).get('name')

                        print(f"\n💬 New comment:")
                        print(f"- Comment ID: {comment_id}")
                        print(f"- Message: {message}")
                        print(f"- Post ID: {post_id}")
                        print(f"- Sender ID: {sender_id}")
                        print(f"- Sender Name: {sender_name}")

                        # אם התגובה היא "LINK", שלח הודעה פרטית למגיב
                        if 'LINK' in message.upper():
                            send_private_message(post_id, sender_id, sender_name)

        return 'EVENT_RECEIVED', 200

def send_private_message(post_id, user_id, sender_name):

    product_data = database.get_product_details_by_post(post_id)
    # שלח הודעה פרטית למגיב
    url = f'https://graph.facebook.com/v11.0/{user_id}/messages'
    headers = {
        'Content-Type': 'application/json',
    }

    data = {
        "recipient": {
            "id": user_id
        },
        "message": {
            "text": f"""Hey {sender_name},
              here's your link to! 🎉
              {product_data['ProductDesc']}


              {product_data['PromotionUrl']}
              Check it out!
              
              """
        }
    }

    params = {
        'access_token': PAGE_ACCESS_TOKEN
    }

    response = requests.post(url, headers=headers, json=data, params=params)

    if response.status_code == 200:
        print(f"Successfully sent message to {sender_name}")
    else:
        print(f"Failed to send message to {sender_name}. Error: {response.text}")

if __name__ == '__main__':
    app.run(port=5000, debug=True)
