"""Relie les CD de Phonotek à la bibliothèque musicale de l'Eversolo.

Pour chaque album, on relève l'identifiant Eversolo de l'album et de chaque piste. Phonotek peut alors
lancer un morceau « depuis la bibliothèque » (playMusic type=4) : l'album entier passe dans la file,
dans l'ordre du disque, avec l'affichage habituel de l'Eversolo.

Usage : python3 eversolo_ids.py   (Eversolo réveillé ; le relevé est volontairement lent)
"""
import json
import os
import time
import urllib.parse
import urllib.request

import scan
import sync

API = scan.EVERSOLO_API + "/ZidooMusicControl/v2"
PAUSE = 1.0   # la base musicale de l'Eversolo se bloque si on l'interroge trop vite


def get(path, **params):
    url = "%s/%s?%s" % (API, path, urllib.parse.urlencode(params))
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def norm(s):
    return (s or "").replace("’", "'").replace("‐", "-").strip().lower()


def crawl(log=print):
    """Renvoie {(dossier, nom de fichier): (id album, id piste, numéro)} pour toute la bibliothèque."""
    albums, start = [], 0
    while True:
        page = get("getAlbums", start=start, count=100).get("array", [])
        albums += page
        if len(page) < 100:
            break
        start += 100
        time.sleep(PAUSE)
    index = {}
    for i, album in enumerate(albums):
        time.sleep(PAUSE)
        try:
            tracks = get("getAlbumMusics", id=album["id"], start=0, count=200).get("array", [])
        except Exception as e:
            log("  album %s ignoré (%s)" % (album.get("name"), e))
            continue
        for t in tracks:
            folder, _, filename = (t.get("uri") or "").rpartition("/")
            index[(norm(os.path.basename(folder)), norm(filename))] = (album["id"], t["id"], t.get("number"))
        if (i + 1) % 25 == 0:
            log("  %d / %d albums relevés" % (i + 1, len(albums)))
    return index


def simple(s):
    """Version simplifiée d'un titre pour comparer (sans accents, ponctuation ni casse)."""
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", norm(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s)


def link_by_name(log=print):
    """Secours pour les albums dont le dossier ne correspond pas : on compare album, artiste, puis titres."""
    albums, start = [], 0
    while True:
        page = get("getAlbums", start=start, count=100).get("array", [])
        albums += page
        if len(page) < 100:
            break
        start += 100
        time.sleep(PAUSE)
    owner = json.load(open(sync.CONFIG_FILE))["owner"]
    rows = sync.call("GET", "/rest/v1/albums?select=id,data&owner=eq." + owner)
    linked = 0
    for row in rows:
        d = row["data"]
        if d.get("source") != "eversolo" or d.get("missing") or d.get("eversoloAlbumId") or not d.get("trackList"):
            continue
        candidates = [a for a in albums if simple(a.get("name")) == simple(d["album"])
                      and (not a.get("artist") or simple(a["artist"]) in simple(d["artist"])
                           or simple(d["artist"]) in simple(a["artist"]))]
        best, best_hits = None, 0
        for a in candidates:   # doublons possibles : on garde l'album qui couvre le plus de pistes
            time.sleep(PAUSE)
            tracks = get("getAlbumMusics", id=a["id"], start=0, count=200).get("array", [])
            by_title = {simple(t.get("title")): t for t in tracks}
            hits = {t["n"]: by_title[simple(t["title"])] for t in d["trackList"] if simple(t["title"]) in by_title}
            if len(hits) > best_hits:
                best, best_hits, best_map = a, len(hits), hits
        if not best:
            log("  introuvable : %s - %s" % (d["artist"], d["album"]))
            continue
        d["eversoloAlbumId"] = best["id"]
        for t in d["trackList"]:
            if t["n"] in best_map:
                t["eversoloId"] = best_map[t["n"]]["id"]
        sync.call("PATCH", "/rest/v1/albums?id=eq." + row["id"], {"data": d})
        linked += 1
        log("  relié : %s - %s (%d/%d pistes)" % (d["artist"], d["album"], best_hits, len(d["trackList"])))
    return linked


def complete_tracks(log=print):
    """Pistes encore sans identifiant dans un album relié : on les retrouve par leur titre."""
    owner = json.load(open(sync.CONFIG_FILE))["owner"]
    left = []
    for row in sync.call("GET", "/rest/v1/albums?select=id,data&owner=eq." + owner):
        d = row["data"]
        missing = [t for t in d.get("trackList") or [] if not t.get("eversoloId")]
        if not d.get("eversoloAlbumId") or not missing:
            continue
        time.sleep(PAUSE)
        tracks = get("getAlbumMusics", id=d["eversoloAlbumId"], start=0, count=200).get("array", [])
        by_title = {simple(t.get("title")): t for t in tracks}
        by_number = {t.get("number"): t for t in tracks}
        for t in missing:
            hit = by_title.get(simple(t["title"]))
            if not hit and len(tracks) == len(d["trackList"]):
                hit = by_number.get(t["n"])
            if hit:
                t["eversoloId"] = hit["id"]
        sync.call("PATCH", "/rest/v1/albums?id=eq." + row["id"], {"data": d})
        left += ["%s - %s : %s" % (d["artist"], d["album"], t["title"]) for t in d["trackList"] if not t.get("eversoloId")]
    return left


def main():
    index = crawl()
    owner = json.load(open(sync.CONFIG_FILE))["owner"]
    rows = sync.call("GET", "/rest/v1/albums?select=id,data&owner=eq." + owner)
    linked = partial = 0
    for row in rows:
        d = row["data"]
        if d.get("source") != "eversolo" or not d.get("trackList"):
            continue
        folder = norm(os.path.basename(d["folder"]))
        hits = 0
        for t in d["trackList"]:
            found = index.get((folder, norm(t.get("file"))))
            if found:
                d["eversoloAlbumId"], t["eversoloId"] = found[0], found[1]
                hits += 1
        if hits:
            sync.call("PATCH", "/rest/v1/albums?id=eq." + row["id"], {"data": d})
            linked += 1
            partial += hits < len(d["trackList"])
    print("%d albums reliés à la bibliothèque de l'Eversolo (%d partiellement)." % (linked, partial))
    extra = link_by_name()
    if extra:
        print("%d albums supplémentaires reliés par leur nom." % extra)
    left = complete_tracks()
    print("Pistes encore non reliées : %d" % len(left))
    for line in left:
        print("  " + line)


if __name__ == "__main__":
    main()
