











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


VERSIONE_REGOLE = 5
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

    s = senza_accenti(str(s or '')).lower()
    s = re.sub(r'^(il|lo|la|i|gli|le|l\'|un|uno|una|the|a|an)\s+', '', s)
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()





NON_LATINO = re.compile(
    r'[\u0400-\u04FF\u0590-\u05FF\u0600-\u06FF\u0E00-\u0E7F'
    r'\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]')


SEGNAPOSTO = re.compile(r'^(ep|episodio|puntata|prog|programma)[\s./-]*\d*$', re.I)


def da_scartare(titolo):
    n = norm(titolo)


    if len(n) < 3:
        return True



    if not re.search(r'[a-z]', n):
        if not re.search(r'\d{3}', n):
            return True
    if SEGNAPOSTO.match(titolo.strip()):
        return True
    return any(re.search(p, n) for p in SCARTA)


def chiedi_tmdb(titolo):



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








    candidati = []
    for v in (d.get('results') or [])[:8]:
        tipo = v.get('media_type')
        if tipo not in ('movie', 'tv'):
            continue
        mostrato = v.get('title') or v.get('name') or ''

        if NON_LATINO.search(mostrato):
            continue
        nomi = [v.get('title'), v.get('name'),
                v.get('original_title'), v.get('original_name')]
        if not any(norm(x) == atteso for x in nomi if x):
            continue
        candidati.append((v.get('vote_count') or 0, v, tipo))

    if not candidati:
        return None
    voti, v, tipo = max(candidati, key=lambda x: x[0])
    data = v.get('release_date') or v.get('first_air_date') or ''
    return {
        'id': v.get('id'),
        'm': tipo,
        'p': v.get('poster_path') or '',
        'a': data[:4],
        'v': voti,
        't': v.get('title') or v.get('name') or titolo,
    }


def episodio_da(desc):

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
    if cache.get('_versione') != VERSIONE_REGOLE:
        print('regole cambiate: la memoria dei titoli riparte da zero')
        cache = {'_versione': VERSIONE_REGOLE}
    print('titoli gia\' noti in cache:', len(cache) - 1)

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
        if titolo == '_versione' or titolo in cache:
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
    scartati_puntata = 0
    for g in grezzi:
        s = cache.get(g['titolo'])
        if not s:
            continue





        if g['ep'] and s['m'] == 'movie':
            scartati_puntata += 1
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

    print('puntate agganciate a un film, scartate:', scartati_puntata)
    film = sum(1 for t in tenuti if t['m'] == 'movie')
    print('TENUTI:', len(tenuti), 'programmi (' + str(film), 'film,',
          len(tenuti) - film, 'serie) su', len(risultato['canali']), 'canali')
    print('SCARTATI:', len(grezzi) - len(tenuti))
    print('peso del file:', os.path.getsize(USCITA) // 1024, 'KB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
