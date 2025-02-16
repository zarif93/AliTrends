import telebot
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.types import InputMediaPhoto, InputMediaVideo
import os
from dotenv import load_dotenv
load_dotenv()

# הכנס את הטוקן של הבוט שלך
TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

def send_photo_and_data(data,id):

    #print(data[6])

    # Create the buttons
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton(text="Product Page", url=data[0]))
    keyboard.add(InlineKeyboardButton(text="Telegram Main Channel", url="https://t.me/AliTrends_Aliexpress"))

    caption = data[3]

    if len(caption) > 1050:
        caption = caption[:1021] + '...'  # קיצור הכיתוב אם הוא ארוך מדי

    # chack cat to send 
    c_id = id

    # Create media objects correctly
    media_group = []
    
    if data[2].startswith('http'):
        photo = InputMediaPhoto(data[1], caption="This is a photo caption.")
        video = InputMediaVideo(data[2], caption="This is a video caption.")
        try:
             # Send media group (photo + video)
            bot.send_media_group(chat_id=c_id, media=[photo, video])

            # Send the caption separately (since send_media_group only allows one caption)
            bot.send_message(chat_id=c_id, text=caption, reply_markup=keyboard, parse_mode="HTML")

        except Exception as e:
            print(e)
            pass
    else:
        photo = data[1]
        try:
            bot.send_photo(chat_id=c_id, photo=photo, caption=caption, reply_markup=keyboard)
        except:
            pass
