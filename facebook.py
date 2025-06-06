import requests
import os
import random
from dotenv import load_dotenv
import hendler
import database
load_dotenv()

import os
import requests

def get_tokens():
    # Define the URL to get the list of pages the user manages
    url = "https://graph.facebook.com/v22.0/me/accounts"

    # Set the parameters for the GET request
    params = {
        "fields": "access_token,name,id",  # Request these fields for each page
        "access_token": os.getenv('FACE_TOKEN')  # Get your user access token from environment variables
    }

    # Send a GET request to the Facebook Graph API
    response = requests.get(url, params=params)

    # Create a dictionary mapping page ID to its access token
    page_tokens = {
        page['id']: page['access_token']
        for page in response.json()['data']
    }

    # Return the dictionary with page IDs and their corresponding tokens
    return page_tokens


def facepost(data, id, token):

    # Construct the URL for posting to the photos endpoint of a Facebook Page
    url = f"https://graph.facebook.com/{id}/photos"

    data_to_send = {

        'message': data['post'],  # Concatenating message with new line
        'url': data['ImageUrl'],  # The URL of the image (use the correct image URL)
        }

    # Parameters for the API request
    params = {
        'access_token': token,  # Your page access token
    }

    # Make the request to Facebook Graph API
    response = requests.post(url, data=data_to_send, params=params)

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


