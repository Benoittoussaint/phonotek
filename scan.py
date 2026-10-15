"""Lecture de la bibliothèque Eversolo (lecture seule) vers data/collection.json.

Usage : python3 scan.py
"""
import collections
import datetime
import hashlib
import json
import os
import re
import subprocess
import time
import unicodedata
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(ROOT, "data", "collection.json")
COVERS_DIR = os.path.join(ROOT, "web", "covers")
MOUNT_DIR = os.path.join(ROOT, ".mnt", "eversolo")

EVERSOLO_HOST = "192.168.1.91"
EVERSOLO_SHARE = "Share"

AUDIO_EXT = {".wav", ".flac", ".mp3", ".m4a", ".aiff", ".aif", ".dsf", ".dff", ".ape", ".ogg", ".alac"}
IMAGE_EXT = {".jpg", ".jpeg", ".png"}
SKIP_DIRS = {"Android", "Alarms", "Notifications", "Ringtones", "Podcasts", "DCIM", "Pictures",
             "Movies", "Download", "Documents", "Audiobooks", "busyboxdown", "EverSoloMusic"}
COVER_SIZE = 600
# Dossier du partage réseau -> chemin vu par l'Eversolo lui-même (pour lancer la lecture).
DEVICE_ROOTS = {"Storage": "/storage/emulated/0"}
EVERSOLO_API = "http://%s:9529" % EVERSOLO_HOST


# ---------- Montage du partage Eversolo ----------

def mount():
    """Monte le partage en lecture seule. Renvoie (chemin, monté_par_nous).

    On n'utilise jamais /Volumes/Share ouvert dans le Finder : macOS peut en bloquer la lecture.
    """
    mounts = subprocess.run(["mount"], capture_output=True, text=True).stdout
    if MOUNT_DIR in mounts:
        return MOUNT_DIR, False
    os.makedirs(MOUNT_DIR, exist_ok=True)
    r = subprocess.run(["mount_smbfs", "-o", "rdonly,nobrowse",
                        "//guest:@%s/%s" % (EVERSOLO_HOST, EVERSOLO_SHARE), MOUNT_DIR],
                       capture_output=True, text=True)
    finder_path = "/Volumes/" + EVERSOLO_SHARE
    if r.returncode != 0 and "File exists" in r.stderr and os.access(finder_path, os.R_OK):
        # Le partage est déjà ouvert dans le Finder : on passe par lui (lecture uniquement).
        return finder_path, False
    if r.returncode != 0:
        raise RuntimeError("Eversolo injoignable (%s). Est-il allumé et sur le Wi-Fi ?" % r.stderr.strip())
    return MOUNT_DIR, True


def unmount():
    subprocess.run(["umount", MOUNT_DIR], capture_output=True)


# ---------- Analyse d'un dossier album ----------

def slug(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def read_cue(path):
    """Renvoie (titre, interprète) déclarés en tête du fichier .cue."""
    title = performer = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("FILE") or line.startswith("TRACK"):
                    break
                m = re.match(r'(TITLE|PERFORMER)\s+"(.*)"', line)
                if m:
                    if m.group(1) == "TITLE":
                        title = m.group(2).strip()
                    else:
                        performer = m.group(2).strip()
    except OSError:
        pass
    return title, performer


def read_cue_tracks(path):
    """Ordre des pistes d'après le .cue : liste de (fichier, titre)."""
    tracks = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                m = re.match(r'FILE\s+"(.*)"', line)
                if m:
                    tracks.append([m.group(1), ""])
                m = re.match(r'TITLE\s+"(.*)"', line)
                if m and tracks and not tracks[-1][1]:
                    tracks[-1][1] = m.group(1).strip()
    except OSError:
        pass
    return tracks


def title_from_file(name, artist):
    name = os.path.splitext(name)[0]
    name = re.sub(r"^\d+(-\d+)?\s*[-.]?\s*", "", name)
    if artist and name.lower().startswith(artist.lower() + " - "):
        name = name[len(artist) + 3:]
    elif artist and name.lower().endswith("-" + artist.lower()):
        name = name[:-len(artist) - 1]
    return name.replace("_", "?").strip() or name


def track_list(folder, tracks, artist):
    """Pistes numérotées : ordre du .cue s'il existe, sinon ordre des noms de fichiers."""
    cues = [f for f in os.listdir(folder) if f.lower().endswith(".cue")]
    ordered = []
    if cues:
        present = set(tracks)
        ordered = [(f, t) for f, t in read_cue_tracks(os.path.join(folder, cues[0])) if f in present]
    if len(ordered) != len(tracks):
        ordered = [(f, "") for f in sorted(tracks)]
    return [{"n": i + 1, "title": t or title_from_file(f, artist), "file": f} for i, (f, t) in enumerate(ordered)]


def clean_folder_name(name):
    name = re.sub(r"-\d{14}$", "", name)          # doublons datés de l'Eversolo
    name = re.sub(r"\s*\((WAV|FLAC|MP3)\)$", "", name, flags=re.I)
    return name.strip()


def artist_from_tracks(tracks):
    """Pistes nommées « Titre-Artiste.ext » ou « 01 - Artiste - Titre.ext » : on prend l'artiste majoritaire."""
    names = []
    for t in tracks:
        name = re.sub(r"^[\d\s.-]*-\s+", "", os.path.splitext(t)[0])
        if " - " in name:
            names.append(name.split(" - ", 1)[0].strip())
        elif "-" in name:
            names.append(name.rsplit("-", 1)[1].strip())
    if not names:
        return ""
    counts = collections.Counter(names)
    artist, n = counts.most_common(1)[0]
    if n >= max(2, len(tracks) * 0.5):
        return artist
    return "Artistes divers" if len(counts) > 2 else artist


def pick_cover(folder, images):
    if not images:
        return None
    preferred = ("album", "cover", "folder", "front")
    for p in preferred:
        for img in images:
            if os.path.splitext(img)[0].lower() == p:
                return os.path.join(folder, img)
    return max((os.path.join(folder, i) for i in images), key=os.path.getsize)


def analyse_album(folder):
    files = os.listdir(folder)
    tracks = sorted(f for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXT and not f.startswith("."))
    if not tracks:
        return None
    cues = [f for f in files if f.lower().endswith(".cue")]
    images = [f for f in files if os.path.splitext(f)[1].lower() in IMAGE_EXT and not f.startswith(".")]

    cue_title, cue_artist = read_cue(os.path.join(folder, cues[0])) if cues else ("", "")
    folder_name = clean_folder_name(os.path.basename(folder))
    folder_artist, folder_album = "", folder_name
    if " - " in folder_name:
        folder_artist, folder_album = folder_name.split(" - ", 1)

    album = cue_title or (folder_album if "~" not in folder_album else "") or folder_name
    artist = cue_artist or folder_artist or artist_from_tracks(tracks) or "Artiste inconnu"
    return {
        "artist": artist,
        "album": album,
        "tracks": len(tracks),
        "trackList": track_list(folder, tracks, artist),
        "format": os.path.splitext(tracks[0])[1].lstrip(".").upper(),
        "_cover_file": pick_cover(folder, images),
    }


def raise_error(err):
    raise err


def find_album_folders(share_root):
    for dirpath, dirnames, filenames in os.walk(share_root, onerror=raise_error):
        depth = os.path.relpath(dirpath, share_root).count(os.sep)
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        if depth >= 3:
            dirnames[:] = []
        if any(os.path.splitext(f)[1].lower() in AUDIO_EXT for f in filenames):
            dirnames[:] = []
            yield dirpath


def make_cover(src, album_id):
    os.makedirs(COVERS_DIR, exist_ok=True)
    dest = os.path.join(COVERS_DIR, album_id + ".jpg")
    r = subprocess.run(["sips", "-s", "format", "jpeg", "-Z", str(COVER_SIZE), src, "--out", dest],
                       capture_output=True)
    return "covers/%s.jpg" % album_id if r.returncode == 0 else None


# ---------- Style musical (Deezer, puis iTunes en secours) ----------

USER_AGENT = "Phonotek/0.1 (catalogue musical personnel)"

# Mots-clés des styles Deezer / iTunes -> style Phonotek (premier trouvé l'emporte).
GENRE_MAP = [
    ("rap français", "Rap français"), ("french rap", "Rap français"),
    ("rap", "Rap / Hip-hop"), ("hip", "Rap / Hip-hop"),
    ("reggae", "Reggae"), ("dub", "Reggae"), ("ska", "Reggae"),
    ("jazz", "Jazz"), ("blues", "Blues"),
    ("soul", "Soul / Funk"), ("funk", "Soul / Funk"), ("r&b", "Soul / Funk"),
    ("electro", "Électro"), ("dance", "Électro"), ("house", "Électro"), ("techno", "Électro"),
    ("alternative", "Rock"), ("indie", "Rock"), ("rock", "Rock"), ("metal", "Rock"), ("punk", "Rock"),
    ("chanson", "Chanson française"), ("variété française", "Chanson française"), ("french pop", "Chanson française"),
    ("world", "Musiques du monde"), ("afro", "Musiques du monde"), ("latin", "Musiques du monde"),
    ("classique", "Classique"), ("classical", "Classique"), ("soundtrack", "Bande originale"),
    ("films", "Bande originale"), ("pop", "Pop"),
]


def map_genre(raw):
    raw = (raw or "").lower()
    for key, genre in GENRE_MAP:
        if key in raw:
            return genre
    return None


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def best_match(results, artist, get_artist):
    target = slug(artist)
    for r in results:
        name = slug(get_artist(r) or "")
        if name and (name in target or target in name):
            return r
    return results[0] if results else None


def fetch_genre(artist, album):
    """Renvoie (style Phonotek, style brut) ou (None, None).

    Deezer classe parfois du rap en « Pop » ou « Electro » : dans ce cas, iTunes a le dernier mot.
    """
    query = urllib.parse.quote("%s %s" % ("" if artist == "Artistes divers" else artist, album))
    deezer = (None, None)
    try:
        found = best_match(fetch_json("https://api.deezer.com/search/album?limit=5&q=" + query).get("data", []),
                           artist, lambda r: r["artist"]["name"])
        if found:
            genres = fetch_json("https://api.deezer.com/album/%s" % found["id"]).get("genres", {}).get("data", [])
            raw = ", ".join(g["name"] for g in genres)
            deezer = (map_genre(raw), raw)
    except Exception:
        pass
    if deezer[0] and deezer[0] not in ("Pop", "Électro"):
        return deezer
    try:
        found = best_match(fetch_json("https://itunes.apple.com/search?entity=album&limit=5&term=" + query).get("results", []),
                           artist, lambda r: r.get("artistName"))
        if found and map_genre(found.get("primaryGenreName")):
            return map_genre(found["primaryGenreName"]), found["primaryGenreName"]
    except Exception:
        pass
    return deezer if deezer[0] else (None, None)


def harmonize_genres(data):
    """Un artiste garde le même style sur tous ses albums (style majoritaire), sauf corrections manuelles."""
    by_artist = collections.defaultdict(list)
    for a in data["albums"]:
        if a["artist"] != "Artistes divers" and a.get("genre"):
            by_artist[a["artist"]].append(a)
    for albums in by_artist.values():
        top, n = collections.Counter(a["genre"] for a in albums).most_common(1)[0]
        if n > len(albums) / 2:
            for a in albums:
                if not a.get("edited"):
                    a["genre"] = top


def fill_genres(data, log=print, pause=0.4):
    """Complète le style des albums qui n'en ont pas (jamais ceux corrigés à la main)."""
    for a in data["albums"]:
        if a.get("genre") or a.get("genreChecked"):
            continue
        a["genre"], a["genreRaw"] = fetch_genre(a["artist"], a["album"])
        a["genreChecked"] = True
        log("  %s - %s : %s" % (a["artist"], a["album"], a["genre"] or "?"))
        time.sleep(pause)
    harmonize_genres(data)


# ---------- Collection ----------

def load_collection():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"albums": []}


def save_collection(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def device_path(folder, filename):
    first, _, rest = folder.partition("/")
    root = DEVICE_ROOTS.get(first, "/storage/" + first)
    return "/".join(p for p in (root, rest, filename) if p)


def fill_track_lists(data, log=print):
    """Ajoute la liste des pistes aux albums Eversolo qui ne l'ont pas encore."""
    todo = [a for a in data["albums"] if a.get("source") == "eversolo" and not a.get("trackList")]
    if not todo:
        return 0
    share_root, mounted_here = mount()
    try:
        for a in todo:
            folder = os.path.join(share_root, a["folder"])
            try:
                files = sorted(f for f in os.listdir(folder)
                               if os.path.splitext(f)[1].lower() in AUDIO_EXT and not f.startswith("."))
            except OSError:
                continue
            a["trackList"] = track_list(folder, files, a["artist"])
            log("  %s - %s : %d pistes" % (a["artist"], a["album"], len(a["trackList"])))
    finally:
        if mounted_here:
            unmount()
    return len(todo)


def renumber(tracks):
    """Numérote les pistes ; pour un album à plusieurs CD, position « 1-01 », « 2-01 »..."""
    discs = {t.get("disc", 1) for t in tracks}
    count = {}
    for n, t in enumerate(tracks, 1):
        d = t.get("disc", 1)
        count[d] = count.get(d, 0) + 1
        t["n"], t["idx"] = n, count[d] - 1
        if len(discs) > 1:
            t["pos"] = "%d-%02d" % (d, count[d])


def merge_disc(album, rel, info):
    """Ajoute le CD trouvé dans « rel » comme disque supplémentaire, s'il n'a aucun titre en commun.

    L'Eversolo range le 2e CD d'un album double dans un dossier « Album-AAAAMMJJhhmmss ».
    """
    tracks = album.get("trackList") or []
    if rel in album.get("discFolders", []) or rel == album.get("folder"):
        return True
    known = {slug(t["title"]) for t in tracks}
    if not info.get("trackList") or known & {slug(t["title"]) for t in info["trackList"]}:
        return False   # titres en commun : vrai doublon
    disc = 1 + max([t.get("disc", 1) for t in tracks] or [0])
    for t in tracks:
        t.setdefault("disc", 1)
    tracks += [dict(t, disc=disc, folder=rel) for t in info["trackList"]]
    renumber(tracks)
    album["trackList"], album["tracks"] = tracks, len(tracks)
    album.setdefault("discFolders", []).append(rel)
    album.pop("eversoloAlbumId", None)   # à relier de nouveau
    return True


def scan(log=print):
    """Relit l'Eversolo et ajoute les nouveautés. Ne touche jamais aux corrections manuelles."""
    share_root, mounted_here = mount()
    try:
        data = load_collection()
        by_folder = {a.get("folder"): a for a in data["albums"] if a.get("source") == "eversolo"}
        # Doublons : on ne compare les CD qu'entre eux (un vinyle du même album n'est pas un doublon).
        by_key = {slug(a["artist"] + " " + a["album"]): a for a in data["albums"] if a.get("source") == "eversolo"}
        seen, added = set(), []
        today = datetime.date.today().isoformat()

        try:
            folders = sorted(find_album_folders(share_root))
        except OSError as e:
            raise RuntimeError("L’Eversolo répond mal (en veille ?). Réveille-le puis relance la mise à jour. [%s]" % e)
        known = sum(1 for a in data["albums"] if a.get("source") == "eversolo")
        if known and len(folders) < known * 0.5:
            # Sécurité : une lecture incomplète ne doit jamais masquer la collection.
            raise RuntimeError("Lecture incomplète de l’Eversolo (%d dossiers trouvés pour %d albums connus). "
                               "Réveille-le puis relance la mise à jour." % (len(folders), known))

        for folder in folders:
            rel = os.path.relpath(folder, share_root)
            seen.add(rel)
            if rel in by_folder:
                continue
            info = analyse_album(folder)
            if not info:
                continue
            key = slug(info["artist"] + " " + info["album"])
            if key in by_key:                      # même album dans un autre dossier
                if merge_disc(by_key[key], rel, info):
                    log("  + CD supplémentaire : %s - %s" % (info["artist"], info["album"]))
                else:
                    by_key[key].setdefault("duplicates", []).append(rel)
                seen.add(rel)
                continue
            album_id = hashlib.sha1(rel.encode()).hexdigest()[:10]
            cover_file = info.pop("_cover_file")
            album = dict(info, id=album_id, support="CD", source="eversolo", folder=rel,
                         added=today, cover=None, coverSource=None)
            if cover_file:
                album["cover"] = make_cover(cover_file, album_id)
                album["coverSource"] = "eversolo" if album["cover"] else None
            data["albums"].append(album)
            by_folder[rel] = by_key[key] = album
            added.append(album)
            log("  + %s - %s" % (album["artist"], album["album"]))

        missing = 0
        for a in data["albums"]:
            if a.get("source") == "eversolo":
                gone = a["folder"] not in seen
                a["missing"] = gone
                missing += gone
        fill_genres(data, log)
        data["lastScan"] = datetime.datetime.now().isoformat(timespec="seconds")
        save_collection(data)
        return {"added": len(added), "missing": missing, "total": len(data["albums"])}
    finally:
        if mounted_here:
            unmount()


if __name__ == "__main__":
    print("Lecture de l'Eversolo...")
    result = scan()
    print("Terminé : %(added)d nouveaux albums, %(total)d au total, %(missing)d introuvables." % result)
