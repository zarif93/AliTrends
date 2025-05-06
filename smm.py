import os
from dotenv import load_dotenv
import requests

load_dotenv()

def set_order(link):

    token = os.getenv("SMM_TOKEN")

    url = "https://justanotherpanel.com/api/v2"

    order_data = {
        "service": "9541",      # לדוגמה
        "link": link, # קישור כלשהו
        "quantity": "50"      # כמות
    }

    payload = {
        "key": token,
        "action": "add",
        **order_data
    }

    req = requests.post(url , data=payload)
    if req.status_code == 200:
        print("Order placed successfully!")
    else:
        print(f"Failed to place order: {req.status_code} - {req.text}")
