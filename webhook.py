from flask import Flask, request, render_template, jsonify ,send_from_directory
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import os
import requests
import database
import hendler
import threading
import sqlite3
import additem

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

@app.route('/upload', methods=['POST'])
def upload_files():
    print("Webhook called")
    password = request.form.get('password')

    if password != UPLOAD_PASSWORD:
        return jsonify({'error': 'Unauthorized'}), 401

    files = request.files.getlist('file')
    if not files or all(file.filename == '' for file in files):
        return jsonify({'error': 'No files provided'}), 400

    uploaded = []

    # שלב 1: שמירה של כל הקבצים
    for file in files:
        if file and file.filename:
            filename = secure_filename(file.filename)
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            file.save(file_path)
            uploaded.append(filename)


    threading.Thread(target=additem.run , args=(filename,)).start()

    return jsonify({
        'success': f'{len(uploaded)} files uploaded and are being processed',
        'files': uploaded
    }), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
