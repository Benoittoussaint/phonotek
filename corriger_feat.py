"""Regroupe les « Artiste featuring X » sous l'artiste principal (tags ID3 des fichiers WAV).

Pour chaque morceau dont l'artiste est « Gang Starr featuring Jadakiss » :
- artiste (TPE1) -> « Gang Starr » ;
- titre (TIT2) -> « Titre (feat. Jadakiss) », pour garder l'information.

Même sécurité que corriger_artiste.py : on part de la sauvegarde, on vérifie que la musique est
identique octet pour octet, puis on remplace le fichier sur l'Eversolo via un nom temporaire.

Usage : python3 corriger_feat.py <dossier sauvegarde> <dossier sur l'Eversolo>
"""
import os
import re
import shutil
import sys
import tempfile

from mutagen.id3 import TIT2, TPE1
from mutagen.wave import WAVE

import corriger_artiste as ca

FEAT = re.compile(r"^(.*?)\s+(?:featuring|feat\.?|ft\.?)\s+(.+)$", re.I)


def main(backup_dir, target_dir):
    tmpdir = tempfile.mkdtemp(prefix="phonotek-")
    for name in sorted(os.listdir(backup_dir)):
        if not name.lower().endswith(".wav") or name.startswith("."):
            continue
        src = os.path.join(backup_dir, name)
        tags = WAVE(src).tags
        artist = str(tags.get("TPE1")) if tags and tags.get("TPE1") else ""
        m = FEAT.match(artist)
        if not m:
            continue
        main_artist, guests = m.group(1).strip(), m.group(2).strip()
        title = str(tags.get("TIT2")) if tags.get("TIT2") else os.path.splitext(name)[0]
        new_title = title if "feat" in title.lower() else "%s (feat. %s)" % (title, guests)

        fixed = os.path.join(tmpdir, name)
        shutil.copyfile(src, fixed)
        audio = WAVE(fixed)
        audio.tags.setall("TPE1", [TPE1(encoding=3, text=main_artist)])
        audio.tags.setall("TIT2", [TIT2(encoding=3, text=new_title)])
        audio.save()
        if ca.data_digest(src, ca.read_chunks(src)) != ca.data_digest(fixed, ca.read_chunks(fixed)):
            sys.exit("ARRÊT : la musique de %s ne serait pas identique. Rien n'a été remplacé." % name)

        target = os.path.join(target_dir, name)
        temp_target = os.path.join(target_dir, ".phonotek-" + name)
        shutil.copyfile(fixed, temp_target)
        if os.path.getsize(temp_target) != os.path.getsize(fixed):
            os.remove(temp_target)
            sys.exit("ARRÊT : copie incomplète de %s sur l'Eversolo." % name)
        os.replace(temp_target, target)
        os.remove(fixed)
        print("  %s : « %s » -> « %s » / « %s »" % (name, artist, main_artist, new_title))
    os.rmdir(tmpdir)


if __name__ == "__main__":
    main(*sys.argv[1:3])
