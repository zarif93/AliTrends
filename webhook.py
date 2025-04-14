from flask import Flask, request, render_template, jsonify ,send_from_directory
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import os
import requests
import database
import hendler
import threading
import sqlite3


# טוען משתני סביבה
load_dotenv()

app = Flask(__name__)

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
UPLOAD_PASSWORD = os.getenv("UPLOAD_PASSWORD")

# תקיית שמירת הקבצים
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'CSVS')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# פונקציה לשליפת ה־page tokens מה־API של פייסבוק
def get_page_tokens():
    url = "https://graph.facebook.com/v22.0/me/accounts"
    params = {
        "fields": "access_token,name,id",
        "access_token": os.getenv('FACE_TOKEN')
    }

    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    page_tokens = {page['id']: page['access_token'] for page in data.get('data', [])}
    return page_tokens

# דף הבית שמציג את טופס ההעלאה
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/down')
def download_db():
    # הגדרת נתיב לקובץ ה-DB
    db_file = '/root/ali/AliTrends/aliexpress.db'
    directory = os.path.dirname(db_file)  # נתיב לתיקיה בה נמצא הקובץ

    # שולח את הקובץ לדפדפן להורדה
    return send_from_directory(directory, os.path.basename(db_file), as_attachment=True)


# webhook של פייסבוק
@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    print('Webhook called')
    if request.method == 'GET':
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')

        if token == VERIFY_TOKEN:
            return challenge, 200
        else:
            return 'Invalid verification token', 403

    elif request.method == 'POST':
        data = request.json
        print("Received POST data:", data)
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
def get_product_details_by_post(post_id):
    conn = sqlite3.connect('aliexpress.db')
    cur = conn.cursor()
    # שליפת ה-ProductId מהפוסט
    cur.execute("""
        SELECT ProductId FROM post WHERE PostId = ?
    """, (post_id,))
    product_id = cur.fetchone()
    if product_id:
        # שליפת פרטי המוצר לפי ה-ProductId
        product_id = product_id[0]
        cur.execute("""
            SELECT * FROM products WHERE PromotionUrl = ?
        """, (product_id,))
        product_details = cur.fetchone()
        conn.close()
        if product_details:
            return {
                'ImageUrl': product_details[1],
                'ProductDesc': product_details[3],
                'Feedback': product_details[5],
                'PromotionUrl': product_details[6],
            }
        else:
            return None
    else:
        return None

def send_private_message(post_id, user_id, sender_name):
    product_data = get_product_details_by_post(post_id)
    page_tokens = get_page_tokens()

    page_id = post_id.split('_')[0]
    
    page_token = page_tokens.get(page_id)
    if not page_token:
        return

    # השתמש בטוקן של הדף לשליחת ההודעה
    url = f'https://graph.facebook.com/v11.0/me/messages'
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

    # השתמש בטוקן של הדף
    params = {'access_token': page_token}
    response = requests.post(url, headers=headers, json=data, params=params)

    print(response.status_code, response.text)

# העלאת קובץ XLS עם סיסמה
@app.route('/upload', methods=['POST'])
def upload_file():
    print("Webhook called")
    password = request.form.get('password')

    if password != UPLOAD_PASSWORD:
        return jsonify({'error': 'Unauthorized'}), 401

    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file:
        file_path = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(file_path)

        # מפעיל פונקציה נוספת אחרי העלאה מוצלחת
        threading.Thread(target=hendler.insetdata, args=(file.filename,)).start()

        return jsonify({'success': f'File {file.filename} uploaded and processed successfully'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
