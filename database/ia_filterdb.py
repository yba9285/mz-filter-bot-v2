import logging
from struct import pack
import re
import base64
from pyrogram.file_id import FileId
from pymongo.errors import DuplicateKeyError
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow.exceptions import ValidationError
from info import *
from utils import get_settings, save_group_settings, temp, get_status
from .Imdbposter import get_movie_details, fetch_image
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

processed_movies = set()

MONGODB_SIZE = 512
MONGODB_MINIMUM_REMAINING = 80 
MONGODB_SIZE_LIMIT = MONGODB_SIZE - MONGODB_MINIMUM_REMAINING 

client = AsyncIOMotorClient(DATABASE_URI)
db = client[DATABASE_NAME]
instance = Instance.from_db(db)

client2 = AsyncIOMotorClient(DATABASE_URI2)
db2 = client2[DATABASE_NAME]
instance2 = Instance.from_db(db2)


@instance.register
class Media(Document):
    file_id = fields.StrField(attribute='_id')
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)
    class Meta:
        indexes = ('$file_name', )
        collection_name = COLLECTION_NAME

@instance2.register
class Media2(Document):
    file_id = fields.StrField(attribute='_id')
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)
    class Meta:
        indexes = ('$file_name', )
        collection_name = COLLECTION_NAME

async def check_db_size(db):
    try:
        stats = await db.command("dbstats")
        db_size = stats["dataSize"]
        db_size_mb = db_size / (1024 * 1024)
        print(f"📊 DB Size: {db_size_mb:.2f} MB")
        return db_size_mb
    except Exception as e:
        print(f"Error Checking Database Size: {e}")
        return 0 

         
async def save_file(bot, media):
    try:
        global saveMedia
        file_id, file_ref = unpack_new_file_id(media.file_id)
        file_name = re.sub(r"(_|\-|\.|\+)", " ", str(media.file_name))
        if await Media.count_documents({'file_id': file_id}, limit=1):
            print(f'{file_name} is already saved in the primary database!')
            return False, 0 
        if MULTIPLE_DB:
            try:
                primary_db_size = await check_db_size(db)
                if primary_db_size >= MONGODB_SIZE_LIMIT:
                    print("Primary Database Is Running Low On Space. Switching To Second Database.")
                    saveMedia = Media2
                else:
                    saveMedia = Media
            except Exception as e:
                print(f"Error Checking Primary Db Size: {e}")
                saveMedia = Media
        else:
            saveMedia = Media
        try:
            file = saveMedia(
                file_id=file_id,
                file_ref=file_ref,
                file_name=file_name,
                file_size=media.file_size,
                file_type=media.file_type,
                mime_type=media.mime_type,
                caption=media.caption.html if media.caption else None,
            )
        except ValidationError as e:
            print(f'Error Occurred While Saving File In Database - {e}')
            return False, 2
        else:
            try:
                await file.commit()
            except DuplicateKeyError:
                print(f'{getattr(media, "file_name", "NO_FILE")} is already saved in database')   
                return False, 0
            else:             
                print(f'{getattr(media, "file_name", "NO_FILE")} is saved to database')
#                if await get_status(bot.me.id):
#                    await send_msg(bot, file.file_name, file.caption)
                return True, 1
    except Exception as e:
        print(f"Error In Save File - {e}")

    
async def get_search_results(chat_id, query, file_type=None, max_results=10, offset=0, filter=False):
    if chat_id is not None:
        settings = await get_settings(int(chat_id))
        try:
            max_results = 10 if settings.get('max_btn') else int(MAX_B_TN)
        except KeyError:
            await save_group_settings(int(chat_id), 'max_btn', False)
            settings = await get_settings(int(chat_id))
            max_results = 10 if settings.get('max_btn') else int(MAX_B_TN)

    query = query.strip()
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r'(\b|[\.\+\-_])' + query + r'(\b|[\.\+\-_])'
    else:
        raw_pattern = query.replace(' ', r'.*[\s\.\+\-_()]')

    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except:
        return []
    if USE_CAPTION_FILTER:
        filter = {'$or': [{'file_name': regex}, {'caption': regex}]}
    else:
        filter = {'file_name': regex}
    if file_type:
        filter['file_type'] = file_type
    total_results = await Media.count_documents(filter)
    if MULTIPLE_DB:
        total_results += await Media2.count_documents(filter)
    if max_results % 2 != 0:
        logger.info(f"Since max_results Is An Odd Number ({max_results}), Bot Will Use {max_results + 1} As max_results To Make It Even.")
        max_results += 1
    cursor1 = Media.find(filter).sort('$natural', -1).skip(offset).limit(max_results)
    files1 = await cursor1.to_list(length=max_results)
    if MULTIPLE_DB:
        remaining_results = max_results - len(files1)
        cursor2 = Media2.find(filter).sort('$natural', -1).skip(offset).limit(remaining_results)
        files2 = await cursor2.to_list(length=remaining_results)
        files = files1 + files2
    else:
        files = files1
    next_offset = offset + len(files)
    if next_offset >= total_results:
        next_offset = ''
    return files, next_offset, total_results
    
async def get_bad_files(query, file_type=None):
    query = query.strip()
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r'(\b|[\.\+\-_])' + query + r'(\b|[\.\+\-_])'
    else:
        raw_pattern = query.replace(' ', r'.*[\s\.\+\-_()]')
    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except:
        return []
    if USE_CAPTION_FILTER:
        filter = {'$or': [{'file_name': regex}, {'caption': regex}]}
    else:
        filter = {'file_name': regex}
    if file_type:
        filter['file_type'] = file_type
    cursor1 = Media.find(search_filter).sort('$natural', -1)
    files1 = await cursor1.to_list(length=(await Media.count_documents(filter)))
    if MULTIPLE_DB:
        cursor2 = Media2.find(search_filter).sort('$natural', -1)
        files2 = await cursor2.to_list(length=(await Media2.count_documents(filter)))
        files = files1 + files2
    else:
        files = files1
    total_results = len(files)
    return files, total_results
    

async def get_file_details(query):
    filter = {'file_id': query}
    cursor = Media.find(filter)
    filedetails = await cursor.to_list(length=1)
    if not filedetails:
        cursor2 = Media2.find(filter)
        filedetails = await cursor2.to_list(length=1)
    return filedetails


def encode_file_id(s: bytes) -> str:
    r = b""
    n = 0
    for i in s + bytes([22]) + bytes([4]):
        if i == 0:
            n += 1
        else:
            if n:
                r += b"\x00" + bytes([n])
                n = 0
            r += bytes([i])
    return base64.urlsafe_b64encode(r).decode().rstrip("=")

def encode_file_ref(file_ref: bytes) -> str:
    return base64.urlsafe_b64encode(file_ref).decode().rstrip("=")

def unpack_new_file_id(new_file_id):
    decoded = FileId.decode(new_file_id)
    file_id = encode_file_id(
        pack(
            "<iiqq",
            int(decoded.file_type),
            decoded.dc_id,
            decoded.media_id,
            decoded.access_hash
        )
    )
    file_ref = encode_file_ref(decoded.file_reference)
    return file_id, file_ref


async def send_msg(bot, filename, caption): 
    try:
        filename = re.sub(r'\(\@\S+\)|\[\@\S+\]|\b@\S+|\bwww\.\S+', '', filename).strip()
        caption = re.sub(r'\(\@\S+\)|\[\@\S+\]|\b@\S+|\bwww\.\S+', '', caption).strip()        
        year_match = re.search(r"\b(19|20)\d{2}\b", caption)
        if year_match:
            year = year_match.group(0)
        else:
            year = None             
        pattern = r"(?i)(?:s|season)0*(\d{1,2})"
        season = re.search(pattern, caption)
        if not season:
            season = re.search(pattern, filename)        
        if year:
            filename = filename[: filename.find(year) + 4]            
        if not year:   
          if season:
            season = season.group(1) if season else None 
            filename = filename[: filename.find(season) +1 ]                    
        qualities = ["ORG", "WEB-DL", "WEBRip", "Web-Dl", "WEB-RIP", "WebRip", "Web-Rip", "WEB-Rip", "org", "hdcam", "HDCAM", "HQ", "hq", "HDRip", "hdrip", "camrip", "CAMRip", "hdtc", "predvd", "DVDscr", "dvdscr", "dvdrip", "dvdscr", "HDTC", "dvdscreen", "HDTS", "hdts"]
        quality = await get_qualities(caption, qualities) or ""
        language = ""
        possible_languages = CAPTION_LANGUAGES 
        for lang in possible_languages:
            if lang.lower() in caption.lower():
                language += f"{lang}, "
        if not language:
            language = ""
        else:
            language = language[:-2] 
        filename = re.sub(r"[\(\)\[\]\{\}:;'\-!]", "", filename)
        caption_messages = "<b>#New_File_Uploded\n\nName - {}\nLanguage- {}\nQuality - {}</b>"
        caption_message = caption_messages.format(filename, language, quality) # For Quality Option Added - quality 
        if filename in processed_movies:
            return
        processed_movies.add(filename)
        imdb = await get_movie_details(filename)
        resized_poster = None
        if imdb:
            poster_url = imdb.get('poster_url')
            if poster_url:
                resized_poster = await fetch_image(poster_url)            
        filenames = filename.replace(" ", '-')
        btn = [[InlineKeyboardButton('𝖲𝖾𝖺𝗋𝖼𝗁 𝖧𝖾𝗋𝖾', url=f"https://telegram.me/{temp.U_NAME}?start=getfile-{filenames}")]]
        if resized_poster:
            await bot.send_photo(chat_id=MOVIE_UPDATE_CHANNEL, photo=resized_poster, caption=caption_message, reply_markup=InlineKeyboardMarkup(btn))
        else:      
            await bot.send_message(chat_id=MOVIE_UPDATE_CHANNEL, text=caption_message, reply_markup=InlineKeyboardMarkup(btn))
    except Exception as e:
        print(f"Error Sending Movie Update - {e}")
        
async def get_qualities(text, qualities: list):
    quality = []
    for q in qualities:
        if q in text:
            quality.append(q)
    quality = ", ".join(quality)
    return quality[:-2] if quality.endswith(", ") else quality
