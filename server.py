"""Serveur local de Phonotek.

Usage : python3 server.py   puis ouvrir http://localhost:8765
"""
import json
import os
import re
import subprocess
import tempfile
import threading
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import datetime
import hashlib

import discogs
import scan

PORT = 8765
WEB_DIR = os.path.join(scan.ROOT, "web")
USER_AGENT = "Phonotek/0.1 (catalogue musical personnel)"
COVER_HOSTS = ("mzstatic.com", "dzcdn.net", "deezer.com", "discogs.com")
lock = threading.Lock()


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def search_covers(query):
    """Pochettes candidates sur iTunes et Deezer (gratuits, sans compte)."""
    results = []
    q = urllib.parse.quote(query)
    try:
        data = fetch_json("https://itunes.apple.com/search?entity=album&limit=8&term=" + q)
        for r in data.get("results", []):
            art = r.get("artworkUrl100")
            if art:
                results.append({"source": "iTunes", "label": "%s - %s" % (r.get("artistName"), r.get("collectionName")),
                                "thumb": art.replace("100x100", "300x300"), "url": art.replace("100x100", "1000x1000")})
    except Exception:
        pass
    try:
        data = fetch_json("https://api.deezer.com/search/album?limit=8&q=" + q)
        for r in data.get("data", []):
            if r.get("cover_xl"):
                results.append({"source": "Deezer", "label": "%s - %s" % (r["artist"]["name"], r["title"]),
                                "thumb": r["cover_medium"], "url": r["cover_xl"]})
    except Exception:
        pass
    return results


def set_cover(album_id, url):
    host = urllib.parse.urlparse(url).hostname or ""
    if not url.startswith("https://") or not host.endswith(COVER_HOSTS):
        raise ValueError("Adresse de pochette non autorisée")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as r, tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(r.read())
    try:
        cover = scan.make_cover(tmp.name, album_id)
    finally:
        os.unlink(tmp.name)
    if not cover:
        raise ValueError("Image illisible")
    return cover


def deezer_link(artist, title, kind):
    """Lien Deezer d'un titre (kind="track") ou d'un album (kind="album")."""
    field = "track" if kind == "track" else "album"
    q = urllib.parse.quote('artist:"%s" %s:"%s"' % (artist, field, title))
    results = fetch_json("https://api.deezer.com/search/%s?limit=5&q=%s" % (field, q)).get("data", [])
    if not results:  # recherche plus souple
        q = urllib.parse.quote("%s %s" % (artist, title))
        results = fetch_json("https://api.deezer.com/search/%s?limit=5&q=%s" % (field, q)).get("data", [])
    if not results:
        return None
    return results[0]["link"]


def eversolo_play(album, n):
    """Lance une piste sur l'Eversolo (à la maison uniquement)."""
    track = next((t for t in album.get("trackList", []) if t["n"] == n), None)
    if not track or album.get("source") != "eversolo":
        raise ValueError("Ce morceau n’est pas sur l’Eversolo.")
    path = scan.device_path(album["folder"], track["file"])
    url = "%s/ZidooFileControl/openFile?type=0&path=%s" % (scan.EVERSOLO_API, urllib.parse.quote(path, safe=""))
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            result = json.load(r)
    except Exception:
        raise ValueError("Eversolo injoignable : es-tu à la maison, et est-il réveillé ?")
    if result.get("status") != 200:
        raise ValueError("L’Eversolo a refusé la lecture (code %s)." % result.get("status"))
    return {"playing": track["title"]}


def add_album(body):
    """Ajoute un vinyle (depuis un résultat Discogs ou saisi à la main)."""
    artist, album = (body.get("artist") or "").strip(), (body.get("album") or "").strip()
    if not artist or not album:
        raise ValueError("Il faut au moins un artiste et un album.")
    now = datetime.datetime.now().isoformat()
    item = {
        "id": hashlib.sha1((now + artist + album).encode()).hexdigest()[:10],
        "artist": artist, "album": album,
        "genre": body.get("genre") or None, "genreChecked": True,
        "support": body.get("support") or "Vinyle",
        "wish": bool(body.get("wish")),
        "source": "discogs" if body.get("discogsId") else "manuel",
        "discogsId": body.get("discogsId"),
        "year": body.get("year") or "", "label": body.get("label") or "", "country": body.get("country") or "",
        "format": body.get("format") or "",
        "added": now[:10], "cover": None, "coverSource": None,
    }
    if body.get("cover"):
        try:
            item["cover"], item["coverSource"] = set_cover(item["id"], body["cover"]), "discogs"
        except Exception:
            pass
    if item["discogsId"]:
        try:
            item["price"] = dict(discogs.price(item["discogsId"]), checked=now[:10])
        except RuntimeError:
            pass
    if not item["genre"]:
        item["genre"], item["genreRaw"] = scan.fetch_genre(artist, album)
    with lock:
        data = scan.load_collection()
        data["albums"].append(item)
        scan.save_collection(data)
    return item


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/collection":
            return self.send_json(scan.load_collection())
        if url.path == "/api/discogs/status":
            return self.send_json({"configured": discogs.configured()})
        if url.path == "/api/discogs/search":
            params = urllib.parse.parse_qs(url.query)
            try:
                return self.send_json(discogs.search(params.get("q", [""])[0], params.get("barcode", [""])[0]))
            except RuntimeError as e:
                return self.send_json({"error": str(e)}, 400)
        if url.path == "/api/discogs/price":
            params = urllib.parse.parse_qs(url.query)
            try:
                info = discogs.price(params["id"][0])
            except RuntimeError as e:
                return self.send_json({"error": str(e)}, 400)
            album_id = params.get("album", [""])[0]
            if album_id:
                with lock:
                    data = scan.load_collection()
                    for a in data["albums"]:
                        if a["id"] == album_id:
                            a["price"] = dict(info, checked=datetime.date.today().isoformat())
                    scan.save_collection(data)
            return self.send_json(info)
        if url.path == "/api/deezer":
            p = urllib.parse.parse_qs(url.query)
            link = deezer_link(p.get("artist", [""])[0], p.get("title", [""])[0], p.get("kind", ["track"])[0])
            return self.send_json({"link": link} if link else {"error": "Introuvable sur Deezer."}, 200 if link else 404)
        if url.path == "/api/tracks":
            album_id = urllib.parse.parse_qs(url.query).get("album", [""])[0]
            data = scan.load_collection()
            album = next((a for a in data["albums"] if a["id"] == album_id), None)
            if not album:
                return self.send_json({"error": "Album introuvable"}, 404)
            if not album.get("trackList") and album.get("discogsId"):
                try:
                    tracks = discogs.tracklist(album["discogsId"])
                except RuntimeError as e:
                    return self.send_json({"error": str(e)}, 400)
                with lock:
                    data = scan.load_collection()
                    for a in data["albums"]:
                        if a["id"] == album_id:
                            a["trackList"] = tracks
                    scan.save_collection(data)
                album["trackList"] = tracks
            return self.send_json(album.get("trackList") or [])
        if url.path == "/api/covers/search":
            q = urllib.parse.parse_qs(url.query).get("q", [""])[0]
            return self.send_json(search_covers(q))
        return super().do_GET()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/scan":
                with lock:
                    return self.send_json(scan.scan(log=lambda *_: None))
            if path == "/api/eversolo/play":
                body = self.read_body()
                album = next((a for a in scan.load_collection()["albums"] if a["id"] == body.get("album")), None)
                if not album:
                    return self.send_json({"error": "Album introuvable"}, 404)
                try:
                    return self.send_json(eversolo_play(album, int(body.get("n", 1))))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
            if path == "/api/albums":
                return self.send_json(add_album(self.read_body()))
            m = re.match(r"^/api/albums/([0-9a-z]+)/delete$", path)
            if m:
                with lock:
                    data = scan.load_collection()
                    album = next((a for a in data["albums"] if a["id"] == m.group(1)), None)
                    if not album or album.get("source") == "eversolo":
                        return self.send_json({"error": "Seuls les disques ajoutés à la main peuvent être supprimés."}, 400)
                    data["albums"].remove(album)
                    scan.save_collection(data)
                cover = os.path.join(WEB_DIR, "covers", album["id"] + ".jpg")
                if os.path.exists(cover):
                    os.remove(cover)
                return self.send_json({"deleted": album["id"]})
            m = re.match(r"^/api/albums/([0-9a-z]+)(/cover)?$", path)
            if m:
                body = self.read_body()
                with lock:
                    data = scan.load_collection()
                    album = next((a for a in data["albums"] if a["id"] == m.group(1)), None)
                    if not album:
                        return self.send_json({"error": "Album introuvable"}, 404)
                    if m.group(2):
                        album["cover"] = set_cover(album["id"], body["url"])
                        album["coverSource"] = "manuel"
                        album["coverVersion"] = album.get("coverVersion", 0) + 1
                    else:
                        for field in ("artist", "album", "genre", "year", "label"):
                            if body.get(field, "").strip():
                                album[field] = body[field].strip()
                        if "wish" in body:
                            album["wish"] = bool(body["wish"])
                            if not album["wish"]:
                                album["added"] = datetime.date.today().isoformat()
                        album["edited"] = True
                    scan.save_collection(data)
                    return self.send_json(album)
            self.send_json({"error": "Inconnu"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print("Phonotek tourne déjà : ouvre http://localhost:%d" % PORT)
        raise SystemExit(0)
    print("Phonotek : http://localhost:%d (Ctrl + C pour arrêter)" % PORT)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nPhonotek arrêté.")
