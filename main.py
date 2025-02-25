import hendler
import database
import time
import telegrampost
import facebook
import os
from dotenv import load_dotenv

load_dotenv()

langlist =[
    'English',
    'Arabic',
    'Portuguese', 
    'French',
    'Spanish'
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


def haspost(data, leng):

#    IF not have post create one
    if database.getpost(data[0], leng):
        post = (data[6],data[1],data[2],database.getpost(data[0], leng)[1])
        return post
    else:
        setpost = hendler.setpost(data, leng)
        post = (data[6],data[1],data[2],setpost)
        data = (data,setpost)
        database.insertpost(data, leng)
        return post 
     
num = 1
while True:
    telegrampost.chacker('start sending massages')

    for leng in langlist:

        for list in lists:

            face = leng + ' Facebook '+list

            lng = leng +' '+ list

            if lng == leng+' main':
                data = database.selectrandom(False)
                id = os.getenv(lng)
                f_id = os.getenv(face)
            else:
                data = database.selectrandom(list)
                id = os.getenv(lng)
                f_id = os.getenv(face)

            # post to telegram 
            if id == 'false':
                print(f'{lng} has no telegram channel')
                t = 1
            else:
                post = haspost(data, leng)
                telegrampost.send_photo_and_data(post,id)
                print(f'{lng} post to telegram productid {data[0]}')
                t = 30

            # post to face book 
            if num % 2 == 0:
                # sec time to facebook
                if f_id == 'false':
                    print(f'{face} has no Facebook page')
                else:
                    print(f'{lng} post to facebook productid {data[0]}')
                    facebook.facepost(post,f_id)
            time.sleep(t)
    num = num + 1

    #  need to sleep 3600 sec
    print('going to sleep for 1 hour')
    telegrampost.chacker('stop sending massages')
    time.sleep(3000)
