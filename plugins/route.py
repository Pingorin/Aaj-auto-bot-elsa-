import math
import logging
import re
from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine
from bot import app # Hum app ko import kar rahe hain file fetch karne ke liye
from utils import get_hash

routes = web.RouteTableDef()

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ByteModels:
    def __init__(self, start, end, length):
        self.start = start
        self.end = end
        self.length = length

def get_byte_range(range_header, file_size):
    if not range_header:
        return ByteModels(0, file_size - 1, file_size)

    match = re.match(r"bytes=(\d+)-(\d*)", range_header)
    if not match:
        return ByteModels(0, file_size - 1, file_size)

    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else file_size - 1

    if start > end or start >= file_size:
        return None

    return ByteModels(start, min(end, file_size - 1), file_size)

async def stream_telegram_file(request, file_id, file_hash, is_download=False):
    # Hash check (security ke liye taaki koi randomly id guess na kar sake)
    try:
        # File info get karne ke liye temporary message ka tarika use karte hain but usse behtar caching hoti hai.
        # Aapke paas BIN_CHANNEL hai, jahan file upload hoti hai.
        # file_id yahan actual message_id ya file_id ho sakti hai.
        # Hum assume kar rahe hain 'stream' callback query id bhej rahi hai jisko pehle BIN channel mein send_cached_media se bheja gaya hai.
        message_id = int(file_id) 
        from info import BIN_CHANNEL
        
        # message fetch karna 
        message = await app.get_messages(BIN_CHANNEL, message_id)
        if not message or not message.media:
             return web.Response(status=404, text="File Not Found")
        
        # media object nikalna (document ya video)
        media = getattr(message, message.media.value)
        file_size = media.file_size
        file_name = getattr(media, 'file_name', 'video.mp4')
        mime_type = getattr(media, 'mime_type', 'video/mp4')

        # Hash verify karna
        actual_hash = get_hash(message)
        if actual_hash != file_hash:
            return web.Response(status=403, text="Invalid Hash")

    except Exception as e:
        logger.error(f"Error fetching message: {e}")
        return web.Response(status=404, text="File Not Found or Error Occurred")

    # Range Headers ke mutabik stream karna
    range_header = request.headers.get('Range')
    byte_range = get_byte_range(range_header, file_size)

    if not byte_range:
        return web.Response(status=416, text="Requested Range Not Satisfiable")

    headers = {
        "Content-Range": f"bytes {byte_range.start}-{byte_range.end}/{byte_range.length}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(byte_range.end - byte_range.start + 1),
        "Content-Type": mime_type,
    }

    if is_download:
        headers["Content-Disposition"] = f'attachment; filename="{file_name}"'
    else:
        headers["Content-Disposition"] = f'inline; filename="{file_name}"'

    response = web.StreamResponse(
        status=206 if range_header else 200,
        headers=headers
    )
    
    await response.prepare(request)

    # File stream logic
    chunk_size = 1024 * 1024 # 1MB chunk size
    current_offset = byte_range.start
    limit = byte_range.end - byte_range.start + 1

    try:
        async for chunk in app.stream_media(message, limit=limit, offset=current_offset):
            await response.write(chunk)
    except Exception as e:
        logger.error(f"Error while streaming: {e}")
        # Client disconnect ho gaya ya stream ruk gayi toh ignore karein
        pass

    return response

@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return web.json_response({"status": "Elsa Bot Stream Server Running!"})

@routes.get("/watch/{id}", allow_head=True)
async def stream_handler(request):
    try:
        file_id = request.match_info['id']
        file_hash = request.query.get("hash")
        return await stream_telegram_file(request, file_id, file_hash, is_download=False)
    except Exception as e:
        logger.error(f"Stream handler error: {e}")
        return web.Response(status=500, text="Internal Server Error")

@routes.get("/{id}", allow_head=True)
async def download_handler(request):
    try:
        file_id = request.match_info['id']
        file_hash = request.query.get("hash")
        return await stream_telegram_file(request, file_id, file_hash, is_download=True)
    except Exception as e:
        logger.error(f"Download handler error: {e}")
        return web.Response(status=500, text="Internal Server Error")
