import io
import os
import sqlite3
import logging
import requests
import yt_dlp as youtubedl
from typing import Optional
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

def download_all_videos() -> None:
    logging.info("Starting download_all_videos function.")
    cursor.execute("SELECT task_id, task_link FROM tasks;")
    tasks = cursor.fetchall()

    logging.info(f"Fetched {len(tasks)} tasks from the database.")

    for task_id, video_url in tasks:
        filepath = f'./videos/task_{task_id}.mp4'
        if os.path.exists(filepath):
            logging.info(f"Video {filepath} already downloaded.")
            continue
        
        logging.info(f"Starting download for task_id: {task_id}, video_url: {video_url}")
        video_id, p_token = extract_rutube_video_id(video_url)
        if not video_id or not p_token:
            logging.error(f"Неверный формат ссылки Rutube для задачи {task_id}")
            continue
            
            logging.info(f"Extracted video_id: {video_id}, p_token: {p_token}")
            data = get_rutube_json(video_id, p_token)
            if not data or 'video_balancer' not in data or 'm3u8' not in data['video_balancer']:
                logging.error(f"Ошибка получения данных видео для задачи {task_id}")
                continue
            
            m3u8_url = data['video_balancer']['m3u8']
            logging.info(f"Extracted m3u8_url: {m3u8_url}")
            
            from typing import Any, Dict

            ydl_opts: Dict[str, Any] = {
                'outtmpl': filepath,
                'format': 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]',
                'quiet': True,
                'merge_output_format': 'mp4',
                'postprocessors': [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mp4',
                }],
                'postprocessor_args': [
                    '-crf', '24',
                    '-preset', 'veryfast',
                    '-b:v', '96k',
                    '-b:a', '24k',
                    '-pix_fmt', 'yuv420p',
                ],
            }
            logging.info(f"Starting download with options: {ydl_opts}")
            try:
                with youtubedl.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([m3u8_url])
            except Exception as e:
                logging.error(f"Ошибка обработки видео для задачи {task_id}: {str(e)}")
            finally:
                if os.path.exists(filepath):
                    logging.info(f'Video {filepath} downloaded.')
                else:
                    logging.error(f'Failed to download video для task {task_id}.')
    conn.commit()
    conn.close()
    logging.info(f"Completed all downloads.")

if __name__ == "__main__":
    if not os.path.exists('./videos'):
        os.makedirs('./videos')
    download_all_videos()
