import requests
import os
import random
from dotenv import load_dotenv
import hendler
import database
load_dotenv()

def gettoken():
    url = "https://graph.facebook.com/v22.0/me/accounts"

    params = {
        "fields": "access_token,name,id",  # The fields you want to retrieve
        "access_token": os.getenv('FACE_TOKEN')
    }

    response = requests.get(url, params=params)
    # יצירת מילון עם הקשר בין ID הדף והטוקן
    page_tokens = {page['id']: page['access_token'] for page in response.json()['data']}

    #return page_tokens.get('598255833361498')

    # גישה לטוקן של דף לפי ID
    return page_tokens

def facepost(data, id, token):

    words = ["feed", "photos"]

    random_word = random.choice(words)

    # Construct the URL for posting to the photos endpoint of a Facebook Page
    url = f"https://graph.facebook.com/{id}/{random_word}"

    # Construct the data dictionary for posting an image with a message
    if random_word == 'feed':
        data_to_send = {
            'message': data[3],  # Concatenating message with new line
            'link': data[0],  # The URL of the website (use the correct link URL)
            }
    else:  
        new_message = data[3]
        data_to_send = {

            'message': new_message,  # Concatenating message with new line
            'url': data[1],  # The URL of the image (use the correct image URL)
            }

    # Parameters for the API request
    params = {
        'access_token': token,  # Your page access token
    }

    # Make the request to Facebook Graph API
    response = requests.post(url, data=data_to_send, params=params)

    print("Response Status Code:", response.status_code)
    print("Response Text:", response.text)

    post_id = response.json().get('post_id')

    if post_id:
        return post_id
    
    post_id = response.json().get('id')
    if post_id:
        return post_id
    
    return None

    
def get_url_link(data, token):

    url = f"https://graph.facebook.com/{data}"

    params = {
        'access_token': token,  # Your page access token
        "fields" : "permalink_url"
    }

    response = requests.get(url, params=params)
    
    link = response.json().get("permalink_url")
    if not link:
        return None
    return link

def test():
    token = "EAAaQZCjJxnj4BOZBZAXP3f5GvsvW1aLIq0MmYeDzKOKlwZAKZBlZAAQpv9cXAbQj95ZAW8wXDzecrAKQtaZBhUO2jB93t6SBZA5uXqGt1Gm2DzZCQNPXo5kEXd2BXLZCFhxfsUqO7hIuZAuAonF8PXTxK3Av9OKa3mVoxvZBpS3qEZCEyVXJFOGkNX3Xs3humyC5VhrKQm7nf5r4C0W3oqN7c9JlSf"
    
    id = "270044559535582"

    words = ["feed", "photos"]

    url = f"https://graph.facebook.com/{id}/photos"

    params = {
        'access_token': token,  # Your page access token
    }

    data_to_send = {
        'message': "test",  # Concatenating message with new line
        'url': "https://i.sstatic.net/tz8TK.png",  # The URL of the image (use the correct image URL)
        #'link': "https://www.google.com",  # The URL of the website (use the correct link URL)
        }

    response = requests.post(url, data=data_to_send, params=params)
    return response.json().get('id')

#print(test())

