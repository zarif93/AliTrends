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
        You are an expert social media content creator specializing in high-converting posts.  
        Your content must be engaging, clear, and emotionally compelling to deeply resonate with the target audience.  
        Focus on concise, persuasive language that drives immediate action and boosts traffic and sales.  
        Maintain a professional yet approachable tone, blending authority with warmth.  
        Create a sense of urgency without being pushy, encouraging users to act now.  
        Ensure every post aligns perfectly with the brand’s voice and messaging strategy.
        """

    
    # יצירת הפורמט של הפוסט עם פרטי המוצר
    prompt = f"""
        You are a professional social media copywriter and performance marketer. Create a **high-converting Facebook post** that presents the following product in a way that feels 100% natural, human-written, and emotionally engaging in {leng}.

        The post must read like it was written by an experienced native copywriter — warm, persuasive, fluent, and never robotic or generic.

        Use the following product data:

        - Product link: {data["PromotionUrl"]}
        - Product description (raw): {data["ProductDesc"]}
        - Original price: {data["OriginPrice"]} USD
        - Discounted price: {data["DiscountPrice"]} USD
        - Discount: {data["Discount"]}% OFF
        - Rating: {data["Feedback"]} out of 5
        - Sales in last 180 days: {data["Sales180Day"]}
        - Product category: {data["Category"]}
        - Use emojis: True

        **If a clean product name is not provided, extract a short and natural-sounding product name from the raw description, and use it once in the post.**

        Follow this exact structure and requirements:

        ---

        1. **Link (first line, standalone)**  
        🔗 {data["PromotionUrl"]}

        ---

        2. **Headline (bold, emotional, and attention-grabbing)**  
        - One short sentence (5–12 words) that instantly grabs attention.  
        - Highlight the biggest **benefit**, **pain point**, or **urgency**.  
        - Write naturally in {leng}, no generic or AI-like phrasing.  
        - Match the tone to the product **category**.

        ---

        3. **Product Description (short, persuasive, and natural)**  
        - Rewrite the base description ({data["ProductDesc"]}) into a short, engaging paragraph.  
        - Use **real-life benefits**, not just features.  
        - Mention the product name **once, naturally**.  
        - Follow a **Problem → Solution → Result** structure.  
        - Write in **second person** (את/ה, you) to connect with the reader.  
        - Use no more than 3 short sentences.  
        - Match tone to the category: practical, emotional, playful, etc.

        ---

        4. **Price & Discount**  
        - **Only include this section if there is a real discount** (i.e., {data["Discount"]} > 0).  
        - Show original and discounted price in USD.  
        - Highlight the discount percentage naturally.  
        - Keep it conversational – not like a receipt.  
        - Example: "Usually costs 79.99 USD, now only 39.99 USD – that’s 50% off!"

        ---

        5. **Rating & Social Proof**  
        - Mention the average rating and number of sales in a **natural** and **trust-building** way.  
        - Write it as a full sentence.  
        - Localize into {leng}.  
        - Example (in Hebrew): "עם דירוג של 4.8 מתוך 5 ויותר מ־2,000 מכירות – אין פלא שזה אחד המוצרים הכי נמכרים ברשת."

        ---

        6. **Call to Action (CTA)**  
        - End with one powerful sentence that **encourages action**, but doesn’t sound pushy.  
        - No clichés like “Buy now!” – make it match the tone of the post.  
        - Example: "שדרגו את השגרה שלכם – זה הזמן."

        ---

        7. **Hashtags (exactly 17)**  
        - Add 17 hashtags, all highly relevant to the product’s niche, features, use case, and shopper intent.  
        - Use a mix of **broad** and **specific** hashtags.  
        - All hashtags must be in {leng} (translate and localize, don’t just transliterate).  
        - Put hashtags **on a new line at the end**.

        ---

        🛑 Output must **not exceed 120 words** (excluding hashtags).  
        ✅ Use emojis — but only when it feels natural and enhances the message.  
        🚫 Don’t sound like AI. Don’t repeat words. Don’t oversell.  
        ✅ Make it feel like it was written by a native speaker for real people on Facebook.
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
