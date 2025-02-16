import hendler
import database
import time
import telegrampost
import facebook
import os
from dotenv import load_dotenv

load_dotenv()

lists = {
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
}

def haspost(data):

#    IF not have post create one
    if database.getpost(data[0]):
        post = (data[6],data[1],data[2],database.getpost(data[0])[1])
        return post
    else:
        setpost = hendler.setpost(data)
        post = (data[6],data[1],data[2],setpost)
        data = (data,setpost)
        database.insertpost(data)
        return post 
     
num = 1
while True:

    for list in lists:
        face = 'Facebook '+list
        if list == 'main':
            data = database.selectrandom(False)
            id = os.getenv('main')
            f_id = os.getenv('Facebook main')
        else:
            data = database.selectrandom(list)
            id = os.getenv(data[7])
            face = 'Facebook '+list
            f_id = os.getenv(face)

        post = haspost(data)
        # post to telegram 
        telegrampost.send_photo_and_data(post,id)
        print(f'post to telegram productid {data[0]}')

        # post to face book 
        if num % 2 == 0:
            # sec time to facebook
            if f_id == 'false':
                print(face)
                print('no has page')
            else:
                print(f'post to facebook productid {data[0]}')
                facebook.facepost(post,f_id)
        time.sleep(30)
    num = num + 1

    #  need to sleep 3600 sec
    print('going to sleep for 1 hour')
    time.sleep(3600)
