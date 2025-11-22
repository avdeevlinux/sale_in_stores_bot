import os
import sqlite3
import asyncio
import requests
from rutube import Rutube
import yt_dlp as youtubedl
from typing import Optional
import re
import json
from telegram import Update, InputFile
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print("BOT_TOKEN not set; bot functionality disabled.")
    # Continue without raising an exception; bot will not start.

# Подключение к базе данных
conn = sqlite3.connect('sales_in_stories.db')
cursor = conn.cursor()

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

async def send_videos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not BOT_TOKEN:
        return
    try:
        video_files = [f for f in os.listdir('./videos') if f.endswith('.mp4') and f.startswith('task_')]
        
        # Sort video files by task_id
        video_files.sort(key=lambda x: int(re.search(r'task_(\d+)\.mp4', x).group(1)))
        
        for video_file in video_files:
            match = re.search(r'task_(\d+)\.mp4', video_file)
            if match:
                task_id = int(match.group(1))
                video_path = os.path.join('./videos', video_file)
                try:
                    with open(video_path, 'rb') as video_file:
                        await context.bot.send_video(
                            chat_id=update.effective_chat.id,
                            video=video_file,
                            caption=f'Задание №{task_id}',
                            height=1024,
                            width=580,
                            protect_content=True
                        )
                except Exception as e:
                    print(f"Error sending video for task {task_id}: {e}")
                    if update.effective_chat:
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=f"Error sending video for task {task_id}: {e}"
                        )
    except Exception as e:
        print(f"Error processing video files: {e}")
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"Error processing video files: {e}"
            )

def main() -> None:
    if not BOT_TOKEN:
        return
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler('video', send_videos))

    application.run_polling()

if __name__ == '__main__':
    main()
