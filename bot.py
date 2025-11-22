import io
import re
import os
import sqlite3
import logging
import subprocess
import requests
import yt_dlp as youtubedl
from typing import Optional
from PIL import Image
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, Bot, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from download_video import download_all_videos
import asyncio

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

async def list_videos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        logging.error("update.message is None")
        return
    try:
        video_files = os.listdir('./videos')
        await update.message.reply_text(f"Video files: {video_files}")
    except Exception as e:
        logging.error(f"Error listing video files: {e}")
        await update.message.reply_text("Error listing video files")

async def get_admin_photo(admin_id) -> Optional[InputFile]:
    try:
        admin_chat = await bot.get_chat(str(ADMIN_ID))
    except Exception as e:
        logging.error(f"Error getting admin chat: {e}")
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
            photo_message = None
            photo = await get_admin_photo(ADMIN_ID)

            logging.info(f"Starting course for chat_id {chat_id}")
            if photo:
                photo_message = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo
                )
                try:
                    if update.message.message_id:
                        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
                except Exception as e:
                    logging.error(f"Failed to delete message: {e}")
            
                if photo_message:
                    context.user_data['photo_message_id'] = photo_message.message_id

            welcome_message = await context.bot.send_message(
                chat_id=chat_id,
                text="Рада приветствовать вас на моём авторском курсе 'Продажи в сториз за 12 дней'\n\n"
                     "Ольга Авдеева — наставник по продажам и эксперт в создании стратегий для роста бизнеса.\n\n"
                     "Более 5 лет я помогаю самозанятым, экспертам и предпринимателям привлекать клиентов из соцсетей, выстраивать систему продаж и масштабировать свои проекты.\n\n"
                     "Моя миссия — помочь вам найти точку роста, увидеть свою уникальность и превратить это в стратегию действий, которая работает.\n\n"
                     "Форматы работы:\n\n"
                     "⭕️Видео — описание условий для успешного выполнения задачи, объяснение способа выполнения.\n\n"
                     "⭕️Текст — текстовое описание, инфоповод и важные особенности для достижения успешного результата.\n\n"
                     "Этот бот создан как помощник для обучения экспертов и предпринимателей без выгорания.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Начать курс", callback_data='start_course')]])
            )

            context.user_data['welcome_message_id'] = welcome_message.message_id
                
    except Exception as e:
        logging.error(f"Error in start function: {e}")
        if update.message:
            await update.message.reply_text(
                text="Произошла ошибка. Пожалуйста, попробуйте снова."
            )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        if query is None:
            logging.error("query is None for button callback")
            if update.effective_chat:
                await context.bot.send_message(chat_id=update.effective_chat.id, text="Произошла ошибка. Пожалуйста, попробуйте снова.")
            return

        if query.data == 'start_course':
            chat_id = update.effective_chat.id
            photo_msg_id = context.user_data.get('photo_message_id')
            welcome_msg_id = context.user_data.get('welcome_message_id')
            
            if photo_msg_id:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=photo_msg_id)
                except Exception as e:
                    logging.error(f"Failed to delete photo: {e}")
            
            if welcome_msg_id:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=welcome_msg_id)
                except Exception as e:
                    logging.error(f"Failed to delete welcome: {e}")
            
            # Очистить user_data
            context.user_data.pop('photo_message_id', None)
            context.user_data.pop('welcome_message_id', None)
            
            await query.answer()  # Важно для удаления индикатора загрузки кнопки

        task_id_str = query.data if query.data != 'start_course' else "1"
        try:
            task_id = int(task_id_str) if task_id_str else 1
        except ValueError:
            logging.error(f"Invalid task_id: {query.data}")
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Произошла ошибка. Пожалуйста, попробуйте снова.")
            return

        cursor.execute("SELECT task_name, task_content, task_link FROM tasks WHERE task_id = ?", (task_id,))
        task = cursor.fetchone()

        if task:
            task_name, task_content, task_link = task
            cursor.execute("SELECT COUNT(*) FROM tasks")
            total_tasks = cursor.fetchone()[0]
            next_task_id = task_id + 1 if query.data != 'start_course' else 2
            keyboard = [[InlineKeyboardButton("Следующий урок", callback_data=str(next_task_id))]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Remove button from previous task message if exists
            previous_msg_id = context.user_data.get('last_task_message_id')
            if previous_msg_id:
                try:
                    await context.bot.edit_message_reply_markup(chat_id=update.effective_chat.id, message_id=previous_msg_id, reply_markup=None)
                    logging.info(f"Removed button from previous task message {previous_msg_id}")
                except Exception as e:
                    logging.error(f"Failed to edit previous task message: {e}")

            try:
                await send_video(update, context, task_id)
            except Exception as e:
                logging.error(f"Error sending video: {e}")
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Error processing video files: {e}")
                return

            if task_id < total_tasks:
                task_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"{task_name}\n{task_content}", reply_markup=reply_markup)
                context.user_data['last_task_message_id'] = task_message.message_id
            else:
                task_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"{task_name}\n{task_content}")
                context.user_data['last_task_message_id'] = task_message.message_id
    except Exception as e:
        print('Что-то пошло не так')

async def send_video(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int) -> None:
    if not BOT_TOKEN:
        return
    try:
        video_files = [f for f in os.listdir('./videos') if f.endswith('.mp4') and f.startswith('task_')]

        # Sort video files by task_id
        video_files.sort(key=lambda x: int(re.search(r'task_(\d+)\.mp4', x).group(1)))
        video_file = f'task_{task_id}.mp4'
        match = re.search(r'task_(\d+)\.mp4', video_file)
        if not match or not match.group(1):
            logging.error(f"No task_id found in video file: {video_file}")
            return

        task_id_match = match.group(1)
        if not task_id_match:
            logging.error("task_id_match is None")
            return

        video_task_id = int(task_id_match)
        video_path = os.path.join('./videos', video_file)
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', video_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        width, height = map(int, result.stdout.strip().split('x'))
        try:
            with open(video_path, 'rb') as video_file:
                await context.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=video_file,
                    caption=f'Задание №{task_id}',
                    height=height,
                    width=width,
                    protect_content=True
                )
        except Exception as e:
            logging.error(f"Error sending video for task {task_id}: {e}, details: {e.__traceback__}")
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"Error sending video for task {task_id}: {e}, details: {e.__traceback__}"
                )
    except Exception as e:
        logging.error(f"General error in send_video: {e}, details: {e.__traceback__}")
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"Error processing video files: {e}, details: {e.__traceback__}"
            )

def main() -> None:
    if BOT_TOKEN is None:
        raise ValueError("BOT_TOKEN is not set in the environment variables.")

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list_videos", list_videos))
    application.add_handler(CallbackQueryHandler(button))

    application.run_polling()

if __name__ == '__main__':
    async def download_all_videos_async():
        download_all_videos()

        logging.info("Completed all downloads.")
    asyncio.run(download_all_videos_async())
    main()
