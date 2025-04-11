from openai import OpenAI
import database
import pandas as pd
import time
import re
import os
from dotenv import load_dotenv
import telegrampost

load_dotenv()

ai_api = os.getenv("AI_API")

client = OpenAI(api_key=ai_api)

def setpost(data, leng):
    # תיאור מערכת ממוקד יותר
    sysrole = """
    You are an expert social media content creator. Your posts should be engaging, clear, and drive action. 
    Focus on creating concise, persuasive content that resonates deeply with the target audience and sparks emotion. 
    Ensure each post aligns with the brand's tone and voice, while strategically driving traffic and conversions. 
    The tone should be professional yet approachable, with a sense of urgency to compel users to act.
    """
    
    # יצירת הפורמט של הפוסט עם פרטי המוצר
    prompt = f"""
    You are a highly skilled social media content creator. Your task is to write a social media post with the following structure:

    1. **Description**: 
    - {data[3]}  

    2. **Discount Price**: 
    - Discount Price: {data[4]} USD 
    
    3. **Rating**:
    - Rating: {data[5]}  

    4. **Link**:
    - The link must be the only content in this section: {data[6]}  

    6. **Hashtags**: 
    - Include exactly 17 relevant and engaging hashtags that are aligned with the product, sale, and target audience. 
    - These hashtags should help generate excitement and engagement.

    The post should be clear, concise, and written in a natural, easy-to-read tone.

    **Please make sure to follow the structure above exactly as described.**
    **Write the price in USD.**
    **Please write in {leng}.**
    """
    
    # יצירת בקשה ל-API
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": sysrole},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content
    except OpenAI.error.OpenAIError as e:
        telegrampost.chacker(f"OpenAI API error: {e}")
        return None

    

def extract_number(st):
    if str(st) == 'nan':
        return False
    numbers = re.findall(r'\d+\.\d+|\d+', st)  # חיפוש מספרים שלמים ושברים עשרוניים
    return float(numbers[0]) if numbers else False  # המרה למספר או None אם לא נמצא

def setcategory(data):

    promt = f"""
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
"""
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": promt},
            ]
        )
        return response.choices[0].message.content
    except OpenAI.error.OpenAIError as e:
        telegrampost.chacker(f"OpenAI API error: {e}")
        return None 

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
                extract_number(row[1]['Discount Price']), 
                float('%.1f' % (extract_number(row[1]['Positive Feedback'])*5/100)), 
                row[1]['Promotion Url'],
                setcategory(row[1]['Product Desc'])
                )
            database.insertdatatotable(da)
            print(f'inset one more row {row[1]["ProductId"]}')
