"""Met en forme un album rippé sans informations (« 01 Track-01.wav ») à partir d'une liste de titres.

- tags ID3 (artiste, artiste de l'album, album, titre, numéro, année) écrits sur une copie locale,
  musique vérifiée identique octet pour octet, puis remplacement sur l'Eversolo ;
- fichiers renommés « NN Titre.wav », pochette renommée « folder.jpg », .cue réécrit ;
- dossier renommé « Artiste - Album ».

Usage : python3 formater_album.py <dossier> <fichier JSON {artist, album, year, titles: [...]}>
"""
import json
import os
import re
import shutil
import sys
import tempfile

from mutagen.id3 import TALB, TDRC, TIT2, TPE1, TPE2, TRCK
from mutagen.wave import WAVE

import corriger_artiste as ca


def safe(name):
    """Nom de fichier sans caractères interdits sur le partage réseau."""
    return re.sub(r'[\\/:*?"<>|]', "", name).strip()


def main(folder, spec_file):
    spec = json.load(open(spec_file, encoding="utf-8"))
    wavs = sorted(f for f in os.listdir(folder) if f.lower().endswith(".wav") and not f.startswith("."))
    if len(wavs) != len(spec["titles"]):
        sys.exit("ARRÊT : %d fichiers pour %d titres." % (len(wavs), len(spec["titles"])))
    tmpdir = tempfile.mkdtemp(prefix="phonotek-")
    cue_tracks = []
    for n, (name, title) in enumerate(zip(wavs, spec["titles"]), 1):
        src = os.path.join(folder, name)
        work = os.path.join(tmpdir, name)
        shutil.copyfile(src, work)
        audio = WAVE(work)
        if audio.tags is None:
            audio.add_tags()
        for frame in (TPE1(encoding=3, text=spec["artist"]), TPE2(encoding=3, text=spec["artist"]),
                      TALB(encoding=3, text=spec["album"]), TIT2(encoding=3, text=title),
                      TRCK(encoding=3, text="%d/%d" % (n, len(wavs))), TDRC(encoding=3, text=str(spec["year"]))):
            audio.tags.setall(frame.FrameID, [frame])
        audio.save()
        if ca.data_digest(src, ca.read_chunks(src)) != ca.data_digest(work, ca.read_chunks(work)):
            sys.exit("ARRÊT : la musique de %s ne serait pas identique." % name)
        new_name = "%02d %s.wav" % (n, safe(title))
        tmp_target = os.path.join(folder, ".phonotek-" + new_name)
        shutil.copyfile(work, tmp_target)
        if os.path.getsize(tmp_target) != os.path.getsize(work):
            os.remove(tmp_target)
            sys.exit("ARRÊT : copie incomplète de %s." % new_name)
        os.replace(tmp_target, os.path.join(folder, new_name))
        if new_name != name:
            os.remove(src)
        os.remove(work)
        cue_tracks.append((new_name, title))
        print("  %s -> %s" % (name, new_name))
    os.rmdir(tmpdir)

    # Pochette et .cue
    for f in os.listdir(folder):
        path = os.path.join(folder, f)
        if f.startswith("._"):
            os.remove(path)   # fichiers techniques du Mac, sans contenu utile
        elif f.lower().endswith(".jpg") and f != "folder.jpg":
            os.replace(path, os.path.join(folder, "folder.jpg"))
        elif f.lower().endswith(".cue"):
            os.remove(path)
    base = "%s - %s" % (spec["artist"], spec["album"])
    lines = ['PERFORMER "%s"' % spec["artist"], 'TITLE "%s"' % spec["album"], "REM DATE %s" % spec["year"]]
    for n, (fname, title) in enumerate(cue_tracks, 1):
        lines += ['FILE "%s" WAVE' % fname, "  TRACK %02d AUDIO" % n, '    TITLE "%s"' % title,
                  '    PERFORMER "%s"' % spec["artist"], "    INDEX 01 00:00:00"]
    with open(os.path.join(folder, safe(base) + ".cue"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    new_folder = os.path.join(os.path.dirname(folder.rstrip("/")), safe(base))
    if os.path.abspath(new_folder) != os.path.abspath(folder):
        # Le partage ignore la casse : un simple changement de majuscules passe par un nom temporaire.
        temp = folder.rstrip("/") + "-phonotek-tmp"
        os.rename(folder, temp)
        os.rename(temp, new_folder)
    print("Dossier : %s" % new_folder)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
