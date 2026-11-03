import asyncio
import subprocess
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from yt_dlp import YoutubeDL

app = FastAPI()

# Cấu hình bộ tìm kiếm yt-dlp tối ưu tốc độ cho Render
ydl_opts = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'ytsearch',
    'extract_flat': False,
}

def stream_pcm_generator(audio_url: str):
    """Sử dụng ffmpeg có sẵn của Render để băm và dịch trực tiếp sang PCM 24kHz, Mono"""
    command = [
        'ffmpeg',
        '-i', audio_url,
        '-f', 's16le',
        '-ar', '24000',  # Tần số lấy mẫu 24000Hz chuẩn chip ES8311 của robot
        '-ac', '1',      # 1 Kênh (Mono)
        '-acodec', 'pcm_s16le',
        'pipe:1'         # Đẩy luồng dữ liệu thô ra stdout
    ]
    
    # Khởi tạo tiến trình chạy ffmpeg ngầm trên môi trường Linux của Render
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    
    try:
        while True:
            # Đọc từng block dữ liệu 4KB và đổ liên tục về cho ESP32-S3 qua Wi-Fi
            data = process.stdout.read(4096)
            if not data:
                break
            yield data
    finally:
        process.kill()

@app.get("/play")
async def play_music(q: str):
    if not q:
        raise HTTPException(status_code=400, detail="Thiếu từ khóa tìm kiếm 'q'")
        
    try:
        with YoutubeDL(ydl_opts) as ydl:
            # Tiến hành tìm kiếm 1 kết quả duy nhất dựa trên từ khóa người dùng yêu cầu
            info = ydl.extract_info(f"ytsearch1:{q}", download=False)
            if not info or 'entries' not in info or len(info['entries']) == 0:
                raise HTTPException(status_code=404, detail="Không tìm thấy bài hát phù hợp")
                
            video_info = info['entries'][0]
            direct_audio_url = video_info['url']  # Lấy trực tiếp URL luồng phát của bài nhạc
            
            # Phản hồi luồng streaming PCM thô về cho robot giải mã trực tiếp ra loa
            return StreamingResponse(
                stream_pcm_generator(direct_audio_url),
                media_type="audio/pcm"
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Thiết lập cổng 10000 theo đúng chuẩn quy định môi trường nhận diện của Render
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
