from openai import OpenAI, OpenAIError
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

def split_post_content(content):
    # ביטוי רגולרי כדי למצוא את כל ההאשטאגים
    hashtags = re.findall(r'#\w+', content)

    # הסרת ההאשטאגים מהתוכן
    content_without_hashtags = re.sub(r'#\w+', '', content)

    # החזרת שני החלקים
    return content_without_hashtags.strip(), ' '.join(hashtags)

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
        You are a professional social media marketer. Create a **high-converting Facebook post** that presents the following product data in a way that grabs attention, builds trust, and encourages clicks and purchases.

        Follow this exact structure:

        1. **Link (first thing in post)**  
        - Only include the product link: {data["PromotionUrl"]}

        2. **Headline (strong, attention-grabbing)**  
        - Write a short and powerful sentence that highlights the main benefit, discount, or urgency of the product.
        - Make sure it builds excitement and encourages the user to keep reading.

        3. **Product Description (natural and persuasive)**  
        - Describe what the product does and why it's useful or exciting.
        - Focus on benefits to the customer, not just technical details.
        - Include product name naturally.
        - Keep it easy to read, 2–3 short sentences max.

        4. **Price & Discount**  
        - Show original price: {data["OriginPrice"]} USD  
        - Show discount price: {data["DiscountPrice"]} USD  
        - Highlight the discount: {data["Discount"]}% OFF

        5. **Rating & Social Proof**  
        - Rating: ⭐ {data["Feedback"]} out of 5  
        - Sales: {data["Sales180Day"]} sold in the last 180 days

        6. **Hashtags**  
        - Add exactly 17 highly relevant hashtags for the product’s niche, features, and shopping intent.  
        - Use a mix of general and specific hashtags.  
        - Use hashtags in English unless {leng} is Hebrew, then translate/adapt them.

        Write the post in {leng}. Use an engaging, friendly, and sales-oriented tone. Avoid sounding robotic. Use emojis only if natural.
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
    except OpenAIError  as e:
        telegrampost.chacker(f"OpenAI API error: {e}", False)
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
    except OpenAIError  as e:
        telegrampost.chacker(f"OpenAI API error: {e}", False)
        return None 
    
############################################
# TO DO 
# 1. add Origin Price to database
# 2. add % Discount to database
# 3. delete Video Url from database
# 4. add % Discount to post
# 5. rewirthing the post to be more engaging and clear
############################################

def insetdata(data):

    data = pd.read_excel('csvs/'+data)

    for row in data.iterrows():

        # chack data exsit
        if database.isset(row[1]['ProductId']):
            print("data is alredy exists")
        else:
            if str(row[1]['Video Url']) == 'nan':
                row[1]['Video Url'] = False

            da = ( 
                row[1]['ProductId'], 
                row[1]['Image Url'], 
                row[1]['Product Desc'],
                extract_number(row[1]['Origin Price']), 
                extract_number(row[1]['Discount Price']),
                extract_number(row[1]['Discount']),
                row[1]['Sales180Day'],
                float('%.1f' % (extract_number(row[1]['Positive Feedback'])*5/100)), 
                row[1]['Promotion Url'],
                setcategory(row[1]['Product Desc'])
                )
            database.insertdatatotable(da)
            print(f'inset one more row {row[1]["ProductId"]}')
