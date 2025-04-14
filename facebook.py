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
        new_choice = random.randint(0,1)
        new_choice = 1
        if new_choice == 0 :
            new_message = f"""Comment "link" to get the product link in a private message!
            *
            *
            *
            *
            *
            {hendler.split_post_content(data[3])[1]}"""
            print(new_message)
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

    # Print the response for debugging
    print("Response Status Code:", response.status_code)
    print("Response Text:", response.text)
    database.saveposts(response.json().get('id'), data[0])
