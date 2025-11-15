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

async def download_and_send_video(update, context, video_url: str, task_id: int):
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

async def send_videos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = None
    try:
        conn = sqlite3.connect('sales_in_stories.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT task_id, task_link FROM tasks ORDER BY task_id ASC")
        videos = cursor.fetchall()
        
        for task_id, task_link in videos:
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"Обрабатываю видео {task_id}: {task_link}"
                )
            try:
                # Передаем ссылку из базы данных напрямую
                await download_and_send_video(update, context, video_url=task_link, task_id=task_id)
            except Exception as e:
                if update.effective_chat:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"Ошибка обработки видео {task_id}: {str(e)}"
                    )
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if conn:
            conn.close()


def main() -> None:
    if not BOT_TOKEN:
        return
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler('video', send_videos))
    
    application.run_polling()

if __name__ == '__main__':
    main()
