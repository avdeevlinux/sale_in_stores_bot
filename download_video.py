import io
import os
import sqlite3
import logging
import requests
import yt_dlp as youtubedl
from typing import Optional, Any, Dict
from dotenv import load_dotenv
import re
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("Не указан BOT_TOKEN в переменных окружения")
ADMIN_ID = os.getenv('ADMIN_ID')
if not ADMIN_ID:
    raise ValueError("Не указан ADMIN_ID в переменных окружения")

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Connect to the SQLite database
conn = sqlite3.connect('sales_in_stories.db', check_same_thread=False)
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

def download_video_with_size_limit(url: str, filepath: str, max_size_mb: int = 50) -> None:
    ydl_opts: Dict[str, Any] = {
        'outtmpl': filepath,
        'quiet': True,
        'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]',
        'merge_output_format': 'mp4',
    }

    with youtubedl.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        duration: Optional[int] = info.get('duration')
        max_size_bytes = max_size_mb * 1024 * 1024
        if duration is not None and duration > 0:
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }]
            ydl_opts['postprocessor_args'] = [
                '-c:v', 'libx264',
                '-crf', '22',
                '-preset', 'veryfast',
                '-pix_fmt', 'yuv420p',
                '-movflags', '+faststart',
            ]

        with youtubedl.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

def download_all_videos() -> None:
    logging.info("Starting download_all_videos function.")
    cursor.execute("SELECT task_id, task_link FROM tasks;")
    tasks = cursor.fetchall()

    logging.info(f"Fetched {len(tasks)} tasks from the database.")

    for task_id, video_url in tasks:
        filepath = f'./videos/task_{task_id}.mp4'
        if not os.path.exists(filepath):
            logging.info(f"Starting download for task_id: {task_id}, video_url: {video_url}")
            try:
                download_video_with_size_limit(video_url, filepath, max_size_mb=50)
                logging.info(f'Video {filepath} downloaded.')
            except Exception as e:
                logging.error(f"Ошибка обработки видео для задачи {task_id}: {str(e)}")
            finally:
                if not os.path.exists(filepath):
                    logging.error(f'Failed to download video для task {task_id}.')
        else:
            print(f"The file task_{task_id}.mp4 was uploaded earlier")
    conn.commit()
    conn.close()
    logging.info(f"Completed all downloads.")

if __name__ == "__main__":
    download_all_videos()
