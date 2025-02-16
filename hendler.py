from openai import OpenAI
import database
import pandas as pd
import time
import re
import os
from dotenv import load_dotenv

load_dotenv()

ai_api = os.getenv("AI_API")

client = OpenAI(api_key=ai_api)

def setpost(data):

    response = client.chat.completions.create(model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a creative and engaging social media content creator. Your posts should be clear, captivating, and easy to read."},
            {"role": "user", "content": f"""
Create a product post with the following structure:
Description:
{data[3]}

Price:
{data[4]}

Rating:
{data[5]}

link: 
{data[6]}

Hashtags:
Include relevant 17 hashtags. Make sure they are engaging and relevant to the target audience.

Make the post feel natural and appealing, with a focus on clarity and readability.
after the link use only the url

"""},
        ]
    )
    return response.choices[0].message.content

def extract_number(st):
    if str(st) == 'nan':
        return False
    numbers = re.findall(r'\d+\.\d+|\d+', st)  # חיפוש מספרים שלמים ושברים עשרוניים
    return float(numbers[0]) if numbers else False  # המרה למספר או None אם לא נמצא

def setcategory(data):

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "user", "content": f"""
Given the product name, determine the most appropriate category from the following list:

Electronics & Technology
Fashion & Accessories
Home & Living
Sports & Outdoor
Toys & Kids
Automotive & Motorcycle
Beauty & Health
Office & Education
Security & Tools

The product name is:
{data} 
What is the category for this product?
             
             """},
        ]
    )
    return response.choices[0].message.content

def insetdata(data):

    data = pd.read_excel('csvs/'+data)

    for row in data.iterrows():

        # chack data exsit
        if database.isset(row[1]['ProductId']):
            print("data is alredy exists")
        else:
            if str(row[1]['Video Url']) == 'nan':
                row[1]['Video Url'] = False

            da = ( row[1]['ProductId'], 
                row[1]['Image Url'], 
                row[1]['Video Url'], 
                row[1]['Product Desc'], 
                extract_number(row[1]['Origin Price']), 
                float('%.1f' % (extract_number(row[1]['Positive Feedback'])*5/100)), 
                row[1]['Promotion Url'],
                setcategory(row[1]['Product Desc'])
                )
            database.insertdatatotable(da)
            print(f'inset one more row {row[1]["ProductId"]}')

