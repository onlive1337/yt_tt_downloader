import logging
import os
import shutil
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
import yt_dlp

# Constants
API_TOKEN = ''
FFMPEG_AVAILABLE = shutil.which('ffmpeg') is not None

# Logging setup
logging.basicConfig(
    filename='bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Bot and dispatcher setup
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

class DownloadOption:
    VIDEO_HIGH = 'video_high'
    VIDEO_LOW = 'video_low'
    AUDIO = 'audio'

def create_progress_bar(percent, width=20):
    filled_length = int(width * percent // 100)
    bar = '█' * filled_length + '░' * (width - filled_length)
    return f"[{bar}] {percent:.1f}%"

async def progress_hook(d, chat_id, message_id):
    if d['status'] == 'downloading':
        percent = float(d['_percent_str'].replace('%', ''))
        speed = d['_speed_str']
        eta = d['_eta_str']
        progress_bar = create_progress_bar(percent)
        message = f"Скачивание: {progress_bar}\nСкорость: {speed} | Осталось: {eta}"
        
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=message)
        except Exception as e:
            logger.error(f"Error updating progress: {str(e)}")

@dp.message(Command(commands=['start', 'help']))
async def send_welcome(message: types.Message):
    await message.answer("Отправьте мне ссылку на видео YouTube или TikTok, и я скачаю его для вас.")
    logger.info(f"User {message.from_user.id} started the bot")

@dp.message()
async def handle_url(message: types.Message):
    url = message.text
    logger.info(f"Received URL: {url} from user {message.from_user.id}")
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Видео (Высокое качество)", callback_data=f"{DownloadOption.VIDEO_HIGH}:{url}"),
            InlineKeyboardButton(text="Видео (Низкое качество)", callback_data=f"{DownloadOption.VIDEO_LOW}:{url}")
        ]
    ])
    
    if FFMPEG_AVAILABLE:
        markup.inline_keyboard.append([
            InlineKeyboardButton(text="Только аудио", callback_data=f"{DownloadOption.AUDIO}:{url}")
        ])
    else:
        await message.answer("Внимание: FFmpeg не установлен. Опция 'Только аудио' недоступна.")
    
    await message.answer("Выберите формат загрузки:", reply_markup=markup)

@dp.callback_query()
async def callback_query_handler(callback_query: types.CallbackQuery):
    option, url = callback_query.data.split(':', 1)
    chat_id = callback_query.message.chat.id
    
    progress_message = await bot.send_message(chat_id, "Начинаю загрузку...")
    
    try:
        ydl_opts = get_ydl_opts(option, chat_id, progress_message.message_id)
        downloaded_file = await download_file(url, ydl_opts)
        await send_file(chat_id, downloaded_file, option)
        os.remove(downloaded_file)
        await bot.edit_message_text(chat_id=chat_id, message_id=progress_message.message_id, text="Загрузка завершена!")
    except Exception as e:
        error_message = f"Произошла ошибка при скачивании: {str(e)}"
        await bot.send_message(chat_id, error_message)
        logger.error(f"Error for user {chat_id}: {str(e)}", exc_info=True)

def get_ydl_opts(option, chat_id, message_id):
    ydl_opts = {
        'outtmpl': '%(title)s.%(ext)s',
        'progress_hooks': [lambda d: asyncio.create_task(progress_hook(d, chat_id, message_id))],
        'restrictfilenames': True,
    }
    
    if option.startswith('video'):
        ydl_opts['format'] = 'bestvideo+bestaudio/best' if option == DownloadOption.VIDEO_HIGH else 'worstvideo+worstaudio/worst'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }]
    elif option == DownloadOption.AUDIO:
        if FFMPEG_AVAILABLE:
            ydl_opts['format'] = 'bestaudio/best'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        else:
            raise Exception("Опция 'Только аудио' недоступна без FFmpeg.")
    
    return ydl_opts

async def download_file(url, ydl_opts):
    logger.info(f"Starting download for URL: {url}")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info['title']
            if ydl_opts.get('postprocessors') and ydl_opts['postprocessors'][0]['key'] == 'FFmpegExtractAudio':
                downloaded_file = f"{title}.mp3"
            else:
                downloaded_file = f"{title}.mp4"
        
        if not os.path.exists(downloaded_file):
            logger.error(f"File not found: {downloaded_file}")
            raise Exception(f"Файл не был скачан или имеет неожиданное имя: {downloaded_file}")
        
        file_size = os.path.getsize(downloaded_file)
        logger.info(f"Downloaded file: {downloaded_file}, size: {file_size} bytes")
        
        if file_size == 0:
            raise Exception("Скачанный файл пуст")
        
        return downloaded_file
    except Exception as e:
        logger.error(f"Error in download_file: {str(e)}", exc_info=True)
        raise

async def send_file(chat_id, file_path, option):
    try:
        file = FSInputFile(file_path)
        if option.startswith('video'):
            await bot.send_video(chat_id, video=file)
        else:
            await bot.send_audio(chat_id, audio=file)
        logger.info(f"File sent successfully: {file_path}")
    except Exception as e:
        logger.error(f"Error sending file: {str(e)}", exc_info=True)
        await bot.send_message(chat_id, f"Произошла ошибка при отправке файла: {str(e)}")

async def main():
    logger.info("Bot started")
    try:
        await dp.start_polling(bot)
    except TelegramAPIError as e:
        logger.error(f"Telegram API Error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())