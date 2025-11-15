import io
import re
import os
import sqlite3
import logging
import requests
from rutube import Rutube
import yt_dlp as youtubedl
from typing import Optional
from PIL import Image
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, Bot, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
# Ensure the environment variable is set
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("Не указан BOT_TOKEN в переменных окружения")
ADMIN_ID = os.getenv('ADMIN_ID')
if not ADMIN_ID:
    raise ValueError("Не указан ADMIN_ID в переменных окружения")

bot = Bot(token=BOT_TOKEN)

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Connect to the SQLite database
conn = sqlite3.connect('sales_in_stories.db', check_same_thread=False)
cursor = conn.cursor()

async def get_admin_photo(admin_id):
    try:
        admin_chat = await bot.get_chat(admin_id)
    except Exception as e:
        logging.error(f"Ошибка получения чата администратора: {e}")
        admin_chat = None
    if admin_chat and admin_chat.photo:
        try:
            logging.info(f"Попытка получить фото профиля администратора {ADMIN_ID}")
            photo = await bot.get_file(admin_chat.photo.big_file_id)
            logging.info(f"Получена информация о фото: {photo}")
            
            if photo and photo.file_path:
                logging.info(f"Загрузка фото по пути: {photo.file_path}")
                photo_bytes = await photo.download_as_bytearray()
                
                if photo_bytes:
                    logging.info(f"Размер загруженного фото: {len(photo_bytes)} байт")
                    
                    return InputFile(io.BytesIO(photo_bytes), filename='admin_photo.jpg')
                else:
                    logging.warning("Не удалось загрузить фото: photo_bytes пуст")
            else:
                logging.warning("Не удалось получить информацию о фото или file_path")
        except Exception as e:
            logging.error(f"Ошибка при работе с фото профиля: {e}")

    # Если фотографии нет, возвращаем None
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message and update.message.chat:
            chat_id = update.message.chat.id
            logging.info(f"User ID: {chat_id}")
            photo = await get_admin_photo(ADMIN_ID)
            logging.info(f"Starting course for chat_id {chat_id}")
            if photo:
                await update.message.reply_photo(
                    photo=photo
                )
            else:
                await update.message.chat.send_message('У администратора нет фото.')
            await update.message.chat.send_message(
                text="Рада приветствовать вас на моём авторском курсе 'Продажи в сториз за 12 дней'\n\n"
                    "Ольга Авдеева — наставник по продажам и эксперт в создании стратегий для роста бизнеса.\n"
                    "Более 5 лет я помогаю самозанятым, экспертам и предпринимателям привлекать клиентов из соцсетей, выстраивать систему продаж и масштабировать свои проекты.\n\n"
                    "Моя миссия — помочь вам найти точку роста, увидеть свою уникальность и превратить это в стратегию действий, которая работает.\n\n"
                    "Форматы работы:\n\n"
                    "⭕️Видео — описание условий для успешного выполнения задачи, обяснение способа выполнения.\n"
                    "⭕️Текст — текстовое описание, инфоповод и важные особенности для достижения успешного результата.\n"
                    "⭕️ДЗ — задание для оттачивания навыка.\n\n"
                    "Этот бот создан как помощник для обучения экспертов и предпринимателей без выгорания.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Начать курс", callback_data='start_course')]])
            )
    except Exception as e:
        logging.error(f"Error in start function: {e}")
        if update.message:
            await update.message.reply_text(
                text="Произошла ошибка. Пожалуйста, попробуйте снова."
            )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        logging.error("query is None for button callback")
        if update.effective_chat:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Произошла ошибка. Пожалуйста, попробуйте снова.")
        return

    chat_id = None
    if query.from_user:
        chat_id = query.from_user.id
    elif query.message and query.message.chat:
        chat_id = query.message.chat.id
    else:
        logging.error("Both query.from_user and query.message.chat are None for button callback")
        await query.answer(text="Произошла ошибка. Пожалуйста, попробуйте снова.", show_alert=True)
        return

    if chat_id is None:
        logging.error("chat_id is None for button callback")
        await query.answer(text="Произошла ошибка. Пожалуйста, попробуйте снова.", show_alert=True)
        return

    if query.data == 'start_course':
        logging.info("Handling 'Начать курс' button")
        task_id = 1
    elif query.data is not None:
        try:
            task_id = int(query.data)
        except ValueError:
            logging.error(f"Invalid task_id: {query.data}")
            await query.answer(text="Произошла ошибка. Пожалуйста, попробуйте снова.", show_alert=True)
            if chat_id:
                await context.bot.send_message(chat_id=chat_id, text="Произошла ошибка. Пожалуйста, попробуйте снова.")
            return
    else:
        logging.error("query.data is None for button callback")
        await query.answer(text="Произошла ошибка. Пожалуйста, попробуйте снова.", show_alert=True)
        if chat_id:
            await context.bot.send_message(chat_id=chat_id, text="Произошла ошибка. Пожалуйста, попробуйте снова.")
        return

    cursor.execute("SELECT task_name, task_content, task_link FROM tasks WHERE task_id = ?", (task_id,))
    task = cursor.fetchone()

    if task:
        task_name, task_content, task_link = task
        next_task_id = task_id + 1 if query.data != 'start_course' else 2
        keyboard = [[InlineKeyboardButton("Следующий урок", callback_data=str(next_task_id))]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if query.message and isinstance(query.message, Message):
            logging.info(f"Editing message for task_id {task_id if query.data != 'start_course' else 1}")
            if task_id == 1:
                await query.message.edit_text(text=f"{task_name}\n{task_content}")
            else:
                await context.bot.send_message(chat_id=chat_id, text=f"{task_name}\n{task_content}")
            # Передаем ссылку из базы данных напрямую
            await download_and_send_video(update, context, video_url=task_link, task_id=task_id, query=query)
        else:
            logging.error(f"query.message is None or not an instance of Message for task_id {task_id if query.data != 'start_course' else 1}")
            await query.answer(text="Произошла ошибка. Пожалуйста, попробуйте снова.", show_alert=True)
            if chat_id:
                await context.bot.send_message(chat_id=chat_id, text="Произошла ошибка. Пожалуйста, попробуйте снова.")
    else:
        if query.message and isinstance(query.message, Message):
            logging.info("All lessons completed")
            await query.message.edit_text(text="Задания на моем курсе успешно завершены, и я хотела бы выразить искреннюю благодарность Вам, за участие в обучении. Я очень благодарена за проявленные эрудицию и креативность с которыми Вы выполняли задания на протяжении всего курса, и считаю, что это поможет Вам в дальнейшем. Спасибо за Ваш труд и упорство в достижении цели. Искренне желаю Вам успехов!")
        else:
            logging.error("query.message is None or not an instance of Message when all lessons are completed")
            await query.answer(text="Произошла ошибка. Пожалуйста, попробуйте снова.", show_alert=True)
            if chat_id:
                await context.bot.send_message(chat_id=chat_id, text="Произошла ошибка. Пожалуйста, попробуйте снова.")

def extract_rutube_video_id(url: str) -> tuple[Optional[str], Optional[str]]:
    """Извлекает ID видео и токен p из URL Rutube"""
    match = re.search(
        r'/video/private/(?P<video_id>[a-f0-9]+)/.*[?&]p=(?P<p_token>[a-zA-Z0-9_-]+)', 
        url, 
        re.IGNORECASE
    )
    return (match.group('video_id'), match.group('p_token')) if match else (None, None)

def get_rutube_json(video_id: str, p_token: str) -> Optional[dict]:
    """Получаем JSON данные видео через API Rutube"""
    url = f'https://rutube.ru/api/play/options/{video_id}/?p={p_token}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://rutube.ru/',
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"API Error: {str(e)}")
        return None

async def download_and_send_video(update: Update, context: ContextTypes.DEFAULT_TYPE, video_url: str, task_id: int, query) -> None:
    if not update.effective_chat:
        return
    
    video_id, p_token = extract_rutube_video_id(video_url)
    if not video_id or not p_token:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Неверный формат ссылки Rutube"
        )
        return
        
    data = get_rutube_json(video_id, p_token)
    if not data or 'video_balancer' not in data or 'm3u8' not in data['video_balancer']:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Ошибка получения данных видео"
        )
        return
        
    m3u8_url = data['video_balancer']['m3u8']
    
    filepath = 'video.mp4'
    try:
        ydl_opts = {
            'outtmpl': filepath,
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
            'quiet': True,
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4'
            }],
            'format': 'bestvideo[height<=640][ext=mp4]+bestaudio[ext=m4a]/best[height<=640]',
            'concurrent-fragment-downloads': 3,
            'outtmpl': filepath,
            'quiet': True,
            'audio-quality': '96K',
            'video-multistreams': True,
            'fragment-retries': 10
        }
        with youtubedl.YoutubeDL(ydl_opts) as ydl:
            ydl.download([m3u8_url])
        
        with open(filepath, 'rb') as f:
            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=f,
                caption=f'Задание №{task_id}',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Следующий урок", callback_data=str(task_id + 1))]]),
                protect_content=True
            )
            
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Ошибка обработки видео: {str(e)}"
        )
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)

def main() -> None:
    if BOT_TOKEN is None:
        raise ValueError("BOT_TOKEN is not set in the environment variables.")
    
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button))

    application.run_polling()

if __name__ == '__main__':
    main()
