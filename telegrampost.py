import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
import os
from dotenv import load_dotenv

load_dotenv()

# הכנס את הטוקן של הבוט שלך
TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

def chacker(mass, bool):
    chat_id = '7902249875'
    bot.send_message(chat_id, mass, disable_notification=bool)

def send_photo_and_data(data,id):

    # Create the buttons
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton(text="Product Page", url=data['PromotionUrl']))
    #keyboard.add(InlineKeyboardButton(text="Telegram Main Channel", url="https://t.me/AliTrends_Aliexpress"))

    caption = data['post']

    if len(caption) > 1050:
        caption = caption[:1021] + '...'  # קיצור הכיתוב אם הוא ארוך מדי

    # chack cat to send 
    c_id = id

    # Create media objects correctly
    photo = data['ImageUrl']

    try:
        bot.send_photo(chat_id=c_id, photo=photo, caption=caption, reply_markup=keyboard, disable_notification=True)
    except:
        pass
