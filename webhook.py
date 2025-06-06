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
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'csvs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


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
        if os.path.exists(file_path):
            threading.Thread(target=hendler.insetdata, args=(file.filename,)).start()

        return jsonify({'success': f'File {file.filename} uploaded and processed successfully'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
