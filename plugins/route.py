import math
import logging
import re
import traceback
import urllib.parse
from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine
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
    from bot import app 
    from info import BIN_CHANNEL

    try:
        message_id = int(file_id) 
        message = await app.get_messages(BIN_CHANNEL, message_id)
        
        if not message or not message.media:
             return web.Response(status=404, text="File Not Found")
        
        media = getattr(message, message.media.value)
        file_size = media.file_size
        file_name = getattr(media, 'file_name', 'video.mp4')
        mime_type = getattr(media, 'mime_type', 'video/mp4') or 'application/octet-stream'

        # Hash verify
        actual_hash = get_hash(message)
        if actual_hash != file_hash:
            return web.Response(status=403, text="Invalid Hash")

    except Exception as e:
        logger.error(f"Error fetching message: {e}")
        return web.Response(status=404, text=f"File Not Found or Error Occurred: {e}")

    range_header = request.headers.get('Range')
    byte_range = get_byte_range(range_header, file_size)

    if not byte_range:
        return web.Response(status=416, text="Requested Range Not Satisfiable")

    # FIX: Encode file name to prevent HTTP Header crashes
    encoded_name = urllib.parse.quote(file_name)

    headers = {
        "Content-Range": f"bytes {byte_range.start}-{byte_range.end}/{byte_range.length}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(byte_range.end - byte_range.start + 1),
        "Content-Type": str(mime_type),
    }

    if is_download:
        headers["Content-Disposition"] = f'attachment; filename*=UTF-8\'\'{encoded_name}'
    else:
        headers["Content-Disposition"] = f'inline; filename*=UTF-8\'\'{encoded_name}'

    response = web.StreamResponse(
        status=206 if range_header else 200,
        headers=headers
    )
    
    await response.prepare(request)

    current_offset = byte_range.start
    limit = byte_range.end - byte_range.start + 1

    try:
        async for chunk in app.stream_media(message, limit=limit, offset=current_offset):
            await response.write(chunk)
    except Exception as e:
        logger.error(f"Error while streaming: {e}")
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
        err_str = traceback.format_exc()
        logger.error(f"Stream handler error: {err_str}")
        # Ab browser screen par exact error print hoga
        return web.Response(status=500, text=f"Internal Server Error\n\nDetails:\n{err_str}")

@routes.get("/{id}", allow_head=True)
async def download_handler(request):
    try:
        file_id = request.match_info['id']
        file_hash = request.query.get("hash")
        return await stream_telegram_file(request, file_id, file_hash, is_download=True)
    except Exception as e:
        err_str = traceback.format_exc()
        logger.error(f"Download handler error: {err_str}")
        return web.Response(status=500, text=f"Internal Server Error\n\nDetails:\n{err_str}")
