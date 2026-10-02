from flask import Flask, Response, request, stream_with_context
import urllib.request
import requests
import json
import re
import urllib.parse

app = Flask(__name__)

USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.2171.95 Safari/537.36'
FETCH_TIMEOUT = 10

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
    """M3U8 içeriğindeki tüm URL'leri (Stream, Segment, Map, URI) proxy yapısına dönüştürür."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    output_lines = []
    
    for line in lines:
        if line.startswith("#EXTM3U") or line.startswith("#EXT-X-VERSION:") or line == "#EXT-X-INDEPENDENT-SEGMENTS":
            output_lines.append(line)
            continue
            
        # 1. EXT-X-MAP etiketi kontrolü (Örn: #EXT-X-MAP:URI="init.mp4")
        if 'EXT-X-MAP:' in line or 'URI="' in line:
            def fix_uri(match):
                uri_val = match.group(1)
                full_uri = uri_val if uri_val.startswith('http') else base_url + uri_val
                encoded_uri = urllib.parse.quote(full_uri, safe='')
                return f'URI="{host_url}/proxy?url={encoded_uri}"'
            line = re.sub(r'URI="([^"]+)"', fix_uri, line)
        
        # 2. Normal Satırlar (Segmentler, Alt Kalite M3U8 dosyaları veya .ts/.m4s uzantıları)
        elif not line.startswith('#'):
            full_url = line if line.startswith('http') else base_url + line
            encoded_uri = urllib.parse.quote(full_url, safe='')
            line = f"{host_url}/proxy?url={encoded_uri}"
            
        output_lines.append(line)
        
    return "\n".join(output_lines)

@app.route('/playlist.m3u8')
def playlist():
    host_url = request.host_url.rstrip('/')
    
    s = requests.Session()
    try:
        resplink = s.get('https://mediainfo.tf1.fr/mediainfocombo/L_LCI?context=ONEINFO&format=hls', headers={'User-Agent': USER_AGENT})
        response_json = json.loads(resplink.text)
        mastlnk = response_json["delivery"]["url"]
        base_url = mastlnk.replace("/LCI.m3u8", "/")
    except Exception as e:
        return f"Ana link alınamadı: {e}", 500

    m3u8_content = fetch_m3u8_content(mastlnk)
    
    if m3u8_content:
        return Response(process_m3u8_text(m3u8_content, base_url, host_url), mimetype="application/vnd.apple.mpegurl")
    else:
        # Yedek Senaryo
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

    # Eğer proxy'den çağrılan alt dosya bir .m3u8 manifestosu ise, onun içindeki yolları da dinamik olarak düzeltmeliyiz!
    if '.m3u8' in target_url:
        # Alt m3u8 dosyasının kendi base_url'ini bul (örneğin kalite alt klasöründeyse orası baz alınmalı)
        sub_base_url = target_url.rsplit('/', 1)[0] + '/'
        sub_content = fetch_m3u8_content(target_url)
        if sub_content:
            host_url = request.host_url.rstrip('/')
            processed_sub = process_m3u8_text(sub_content, sub_base_url, host_url)
            return Response(processed_sub, mimetype='application/vnd.apple.mpegurl')

    # Normal video segmenti (.ts, .m4s, init.mp4 vb.) ise doğrudan akıt
    return Response(stream_with_context(generate()), mimetype='video/mp2t')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
