from flask import Flask, Response, request, stream_with_context
import urllib.request
import requests
import json
import re
import urllib.parse
import threading
import time
import os

app = Flask(__name__)

USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.2171.95 Safari/537.36'
FETCH_TIMEOUT = 10

# Önbellek (Cache) için global değişkenler
cached_mastlnk = None
cached_base_url = None
cache_lock = threading.Lock()

def update_stream_info():
    """TF1 API'sine bağlanarak güncel yayın linkini ve base_url'i çeker."""
    global cached_mastlnk, cached_base_url
    s = requests.Session()
    try:
        resplink = s.get('https://mediainfo.tf1.fr/mediainfocombo/L_LCI?context=ONEINFO&format=hls', headers={'User-Agent': USER_AGENT})
        response_json = json.loads(resplink.text)
        mastlnk = response_json["delivery"]["url"]
        base_url = mastlnk.replace("/LCI.m3u8", "/")
        
        with cache_lock:
            cached_mastlnk = mastlnk
            cached_base_url = base_url
        print("[BAKIM] Yayın bağlantıları başarıyla güncellendi.")
    except Exception as e:
        print(f"[HATA] Yayın bağlantıları güncellenemedi: {e}")

def background_updater():
    """Uygulama çalıştığı sürece her 2 saatte bir update_stream_info fonksiyonunu tetikler."""
    while True:
        # 2 saat = 7200 saniye
        time.sleep(7200)
        print("[BAKIM] 2 saatlik periyodik güncelleme başlatılıyor...")
        update_stream_info()

def fetch_m3u8_content(m3u8_url):
    try:
        req = urllib.request.Request(m3u8_url, headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as response:
            content = response.read().decode('utf-8', errors='ignore')
            if "#EXTM3U" in content:
                return content
    except Exception:
        pass
    return None

def process_m3u8_text(content, base_url, host_url):
    """M3U8 içeriğindeki tüm URL'leri, Streamleri, Segmentleri ve MAP-URI birleştirmelerini proxy'ye uyarlar."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    output_lines = []
    
    for line in lines:
        if line.startswith("#EXTM3U") or line.startswith("#EXT-X-VERSION:") or line == "#EXT-X-INDEPENDENT-SEGMENTS":
            output_lines.append(line)
            continue
            
        # 1. MAP-URI ve diğer URI içeren etiketler (Örn: #EXT-X-MAP:URI="init.mp4" veya EXT-X-MEDIA)
        if 'URI="' in line:
            def fix_uri(match):
                uri_val = match.group(1)
                full_uri = uri_val if uri_val.startswith('http') else base_url + uri_val
                encoded_uri = urllib.parse.quote(full_uri, safe='')
                return f'URI="{host_url}/proxy?url={encoded_uri}"'
            
            line = re.sub(r'URI="([^"]+)"', fix_uri, line)
        
        # 2. Normal Satırlar (Segmentler (.ts, .m4s) veya Alt Kalite M3U8 dosyaları)
        elif not line.startswith('#'):
            full_url = line if line.startswith('http') else base_url + line
            encoded_uri = urllib.parse.quote(full_url, safe='')
            line = f"{host_url}/proxy?url={encoded_uri}"
            
        output_lines.append(line)
        
    return "\n".join(output_lines)

@app.route('/auto-start')
def auto_start():
    """Manuel veya dış servisler ile güncelleme tetiklemek için endpoint."""
    update_stream_info()
    return {"status": "success", "message": "Yayın bağlantıları güncellendi, arka plan sayacı aktif."}, 200

@app.route('/playlist.m3u8')
def playlist():
    host_url = request.host_url.rstrip('/')
    
    with cache_lock:
        mastlnk = cached_mastlnk
        base_url = cached_base_url
        
    if not mastlnk or not base_url:
        update_stream_info()
        with cache_lock:
            mastlnk = cached_mastlnk
            base_url = cached_base_url
            
    if not mastlnk:
        return "Ana link alınamadı", 500

    m3u8_content = fetch_m3u8_content(mastlnk)
    
    if m3u8_content:
        return Response(process_m3u8_text(m3u8_content, base_url, host_url), mimetype="application/vnd.apple.mpegurl")
    else:
        output_lines = ['#EXTM3U', '#EXT-X-VERSION:6', '#EXT-X-INDEPENDENT-SEGMENTS', base_url]
        new3_string = mastlnk.replace("/LCI.m3u8", "/LCI-mp4a_140800_fra=20000.m3u8")
        encoded_audio = urllib.parse.quote(new3_string, safe='')
        output_lines.append(f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio-AACL-141",CHANNELS="2",LANGUAGE="fr",NAME="Français",DEFAULT=YES,AUTOSELECT=YES,URI="{host_url}/proxy?url={encoded_audio}"')
        return Response("\n".join(output_lines), mimetype="application/vnd.apple.mpegurl")

@app.route('/proxy')
def proxy():
    target_url = request.args.get('url')
    if not target_url:
        return "Eksik URL", 400

    def generate():
        try:
            with requests.get(target_url, headers={'User-Agent': USER_AGENT}, stream=True, timeout=15) as r:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
        except Exception:
            pass

    # Alt kalite m3u8 isteklerinde içerik içindeki yolları ve map etiketlerini de proxy'ye uyarla
    if '.m3u8' in target_url:
        sub_base_url = target_url.rsplit('/', 1)[0] + '/'
        sub_content = fetch_m3u8_content(target_url)
        if sub_content:
            host_url = request.host_url.rstrip('/')
            processed_sub = process_m3u8_text(sub_content, sub_base_url, host_url)
            return Response(processed_sub, mimetype='application/vnd.apple.mpegurl')

    return Response(stream_with_context(generate()), mimetype='video/mp2t')

if __name__ == '__main__':
    # Başlangıçta ilk bağlantı verisini çek
    update_stream_info()
    
    # 2 saatte bir güncelleyecek arka plan thread'ini başlat
    t = threading.Thread(target=background_updater, daemon=True)
    t.start()
    
    # Port yönetimi (Render veya yerel ortamlar için uyumlu)
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
