"""
Tiene dal palinsesto soltanto film e serie che hanno una scheda su TMDB.
Tutto il resto - telegiornali, meteo, talk, televendite - viene scartato.

Gira sul computer di GitHub, una volta a notte. L'app scarica solo il
risultato, che e' piccolo e gia' pulito.

I titoli gia' cercati vengono conservati in cache-titoli.json: la prima notte
le ricerche sono molte, dalla seconda solo quelle nuove.
"""

import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

CHIAVE = os.environ.get('TMDB_KEY', '').strip()
GUIDA = 'guida.xml'
CACHE = 'cache-titoli.json'
USCITA = 'guida-film-serie.json'



SCARTA = [
    r'\btg\s?\d*\b', r'\btelegiornale\b', r'\bmeteo\b', r'\brassegna stampa\b',
    r'\bnews\b', r'\bnotizie\b', r'\bedicola\b', r'\bapprofondimento\b',
    r'\breplica\b', r'\btelevendit', r'\boroscopo\b', r'\bprevisioni\b',
    r'\bgiornale\b', r'\bnewsline\b', r'\brainews\b',
    r'\bdirett[ao]\b', r'\bmezzora\b', r'\bdibattito\b', r'\bparlamento\b',
    r'\bsanta messa\b', r'\budienza\b', r'\bconferenza stampa\b',
    r'\bstudio aperto\b', r'\bporta a porta\b', r'\bquarto grado\b',
]


def senza_accenti(s):
    s = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in s if not unicodedata.combining(c))


def norm(s):
    """Forma confrontabile: niente accenti, niente punteggiatura, minuscolo."""
    s = senza_accenti(str(s or '')).lower()
    s = re.sub(r'^(il|lo|la|i|gli|le|l\'|un|uno|una|the|a|an)\s+', '', s)
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def da_scartare(titolo):
    n = norm(titolo)
    if len(n) < 2:
        return True
    return any(re.search(p, n) for p in SCARTA)


def chiedi_tmdb(titolo):
    """Cerca il titolo su TMDB. Restituisce la scheda solo se il nome
    corrisponde davvero: una somiglianza vaga produrrebbe accostamenti
    sbagliati, ed e' meglio perdere un titolo che mostrarne uno errato."""
    url = ('https://api.themoviedb.org/3/search/multi?api_key=' + CHIAVE +
           '&language=it-IT&include_adult=false&query=' +
           urllib.parse.quote(titolo))
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            d = json.load(r)
    except Exception as e:
        print('   ricerca fallita per', titolo, '->', e, file=sys.stderr)
        return None

    atteso = norm(titolo)
    for v in (d.get('results') or [])[:8]:
        tipo = v.get('media_type')
        if tipo not in ('movie', 'tv'):
            continue
        nomi = [v.get('title'), v.get('name'),
                v.get('original_title'), v.get('original_name')]
        if any(norm(x) == atteso for x in nomi if x):
            data = v.get('release_date') or v.get('first_air_date') or ''
            return {
                'id': v.get('id'),
                'm': tipo,
                'p': v.get('poster_path') or '',
                'a': data[:4],
                't': v.get('title') or v.get('name') or titolo,
            }
    return None


def episodio_da(desc):
    """La descrizione porta spesso "S6 Ep18": e' un indizio utile da mostrare."""
    m = re.search(r'\bS(\d+)\s*Ep?\.?\s*(\d+)', desc or '', re.I)
    return f'S{int(m.group(1))} Ep{int(m.group(2))}' if m else ''


def main():
    if not CHIAVE:
        print('Manca la chiave TMDB (variabile TMDB_KEY).', file=sys.stderr)
        return 1
    if not os.path.exists(GUIDA):
        print('Manca il file', GUIDA, file=sys.stderr)
        return 1

    try:
        cache = json.load(open(CACHE, encoding='utf-8'))
    except Exception:
        cache = {}
    print('titoli gia\' noti in cache:', len(cache))

    radice = ET.parse(GUIDA).getroot()

    canali = {}
    for c in radice.findall('channel'):
        nome = c.find('display-name')
        canali[c.get('id')] = (nome.text or c.get('id')) if nome is not None else c.get('id')

    grezzi = []
    for p in radice.findall('programme'):
        t = p.find('title')
        if t is None or not (t.text or '').strip():
            continue
        d = p.find('desc')
        grezzi.append({
            'canale': p.get('channel'),
            'inizio': (p.get('start') or '')[:12],
            'fine': (p.get('stop') or '')[:12],
            'titolo': t.text.strip(),
            'ep': episodio_da(d.text if d is not None else ''),
        })
    print('programmi nel palinsesto:', len(grezzi))

    unici = sorted({g['titolo'] for g in grezzi})
    print('titoli diversi:', len(unici))

    nuovi = cercati = 0
    for titolo in unici:
        if titolo in cache:
            continue
        nuovi += 1
        if da_scartare(titolo):
            cache[titolo] = None
            continue
        cache[titolo] = chiedi_tmdb(titolo)
        cercati += 1
        time.sleep(0.06)
        if cercati % 100 == 0:
            print('   cercati', cercati, '...')
            json.dump(cache, open(CACHE, 'w', encoding='utf-8'),
                      ensure_ascii=False)
    print('titoli nuovi:', nuovi, '- di cui cercati su TMDB:', cercati)

    tenuti = []
    for g in grezzi:
        s = cache.get(g['titolo'])
        if not s:
            continue
        tenuti.append({
            'c': g['canale'], 'i': g['inizio'], 'f': g['fine'],
            't': s['t'], 'id': s['id'], 'm': s['m'],
            'p': s['p'], 'a': s['a'], 'e': g['ep'],
        })

    usati = {t['c'] for t in tenuti}
    risultato = {
        'aggiornato': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'canali': {k: v for k, v in canali.items() if k in usati},
        'programmi': tenuti,
    }

    json.dump(risultato, open(USCITA, 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    json.dump(cache, open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False)

    film = sum(1 for t in tenuti if t['m'] == 'movie')
    print('TENUTI:', len(tenuti), 'programmi (' + str(film), 'film,',
          len(tenuti) - film, 'serie) su', len(risultato['canali']), 'canali')
    print('SCARTATI:', len(grezzi) - len(tenuti))
    print('peso del file:', os.path.getsize(USCITA) // 1024, 'KB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
