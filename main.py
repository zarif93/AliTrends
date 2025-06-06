import os
import time
import itertools
from dotenv import load_dotenv
import hendler
import database
import telegrampost
import facebook
import smm

load_dotenv()

langlist =[
    'English',
    'Arabic',
    'Portuguese', 
    'French',
    'Spanish',
    'Hebrew'
]

lists = [
    'main',
    'Electronics & Technology',
    'Fashion & Accessories',
    'Home & Living',
    'Sports & Outdoor',
    'Toys & Kids',
    'Automotive & Motorcycle',
    'Beauty & Health',
    'Office & Education',
    'Security & Tools'
]

def haspost(data, language):
    #print(data)

#    IF not have post create one
    if database.getpost(data['ProductId'], language):
        data['post'] = database.getpost(data['ProductId'], language)
        return data
    else:
        data['post'] = hendler.setpost(data, language)
        database.insertpost(data, language)
        return data
     
num = 1
while True:

    telegrampost.chacker('start sending massages', True)
    pagetoken = facebook.get_tokens()
    time_sleep = 1

    for leng, list in itertools.product(langlist, lists):
        facebook_key_id = leng + ' Facebook '+list

        telegram_key_id = leng +' '+ list

        if "main" in list:
            data = database.selectrandom(False)
        else:
            data = database.selectrandom(list)

        telegram_id = os.getenv(telegram_key_id)
        facebook_id = os.getenv(facebook_key_id)

        # post to telegram 
        if telegram_id == 'false':
            print(f'{telegram_key_id} has no telegram channel')
            time_sleep = 1
        else:
            post = haspost(data, leng)
            telegrampost.send_photo_and_data(post, telegram_id)
            print(f'{telegram_key_id} post to telegram productid { data["ProductId"] }')
            time_sleep = 30

        # post to facebook
        if num % 2 == 0:
            # sec time to facebook
            if facebook_id == 'false':
                print(f'{facebook_key_id} has no Facebook page')
            else:
                print(f'{telegram_key_id} post to facebook productid {data["ProductId"]}')
                token = pagetoken.get(facebook_id)
                postid = facebook.facepost(post, facebook_id, token)

                if list == 'main' and postid:
                    linktolike = facebook.get_url_link(postid, token)

                    if linktolike:
                        smm.set_order(linktolike)

        time.sleep(time_sleep)

    num = num + 1
    if num == 11:
        num = 1

    #  need to sleep 3600 sec
    print('going to sleep for 1 hour')
    telegrampost.chacker('stop sending massages', True)
    time.sleep(3000)
