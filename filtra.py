#!/usr/bin/env python3
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
from datetime import datetime, timedelta, timezone

# Gli orari XMLTV arrivano con il fuso scritto accanto - "20260807205000 +0000"
# - e la fonte li pubblica in UTC. Troncando la stringa si otteneva l'ora di
# Greenwich spacciata per italiana: due ore indietro in estate, una in inverno.
# La conversione usa il fuso di Roma, cosi' il cambio dell'ora e' automatico.
try:
    from zoneinfo import ZoneInfo
    ROMA = ZoneInfo('Europe/Rome')
except Exception:
    ROMA = None

CHIAVE = os.environ.get('TMDB_KEY', '').strip()
# Cambiando le regole di riconoscimento, i risultati conservati non valgono
# piu': alzando questo numero la memoria si azzera da sola.
VERSIONE_REGOLE = 5
GUIDA = 'guida.xml'
CACHE = 'cache-titoli.json'
USCITA = 'guida-film-serie.json'

# Nomi che non sono mai film o serie. Serve a non sprecare ricerche: quello
# che passa di qui viene comunque verificato su TMDB.
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


# Alfabeti che un palinsesto italiano non usa mai. Se TMDB restituisce un
# titolo scritto cosi', l'accostamento e' sbagliato per costruzione: nessun
# canale italiano manda in onda un programma col nome in thailandese.
NON_LATINO = re.compile(
    r'[\u0400-\u04FF\u0590-\u05FF\u0600-\u06FF\u0E00-\u0E7F'
    r'\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]')

# Segnaposto che alcuni canali usano al posto del titolo vero.
SEGNAPOSTO = re.compile(r'^(ep|episodio|puntata|prog|programma)[\s./-]*\d*$', re.I)


def da_scartare(titolo):
    n = norm(titolo)
    # Sotto le tre lettere qualunque cosa trova un omonimo su TMDB: esistono
    # serie intitolate "16" o "81", e un "14" nel palinsesto non e' quello.
    if len(n) < 3:
        return True
    # Un titolo di sole cifre e' quasi sempre un numero di puntata. Fanno
    # eccezione i film che si chiamano davvero cosi' - "300", "1917", "2012" -
    # che hanno pero' almeno tre cifre di fila: "13" o "80" no.
    if not re.search(r'[a-z]', n):
        if not re.search(r'\d{3}', n):
            return True
    if SEGNAPOSTO.match(titolo.strip()):
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
    # Nessuna soglia sul numero di voti. Era stata introdotta per scartare i
    # titoli generici, ma i voti di TMDB misurano il pubblico internazionale:
    # applicandola sparivano le fiction italiane - Carabinieri, La fuggitiva,
    # Il testimone - che in Italia vanno in onda in prima serata e altrove non
    # le conosce nessuno. Il lavoro lo fa il marcatore di puntata, piu' avanti.

    # Fra piu' risultati validi si tiene il piu' conosciuto: se due opere si
    # chiamano allo stesso modo, quella trasmessa in TV e' la nota.
    candidati = []
    for v in (d.get('results') or [])[:8]:
        tipo = v.get('media_type')
        if tipo not in ('movie', 'tv'):
            continue
        mostrato = v.get('title') or v.get('name') or ''
        # Un titolo in alfabeto non latino non puo' essere quello trasmesso.
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


def quando(attributo):
    """Da "20260807205000 +0000" all'ora italiana "202608072250"."""
    m = re.match(r'\s*(\d{14})(?:\s*([+-])(\d{2})(\d{2}))?', str(attributo or ''))
    if not m:
        return ''
    dt = datetime.strptime(m.group(1), '%Y%m%d%H%M%S')
    if m.group(2):
        segno = 1 if m.group(2) == '+' else -1
        scarto = timedelta(hours=int(m.group(3)), minutes=int(m.group(4))) * segno
        dt = dt.replace(tzinfo=timezone(scarto))
    else:
        # Senza indicazione si assume UTC, come vuole lo standard XMLTV.
        dt = dt.replace(tzinfo=timezone.utc)
    if ROMA is not None:
        dt = dt.astimezone(ROMA)
    else:
        # Ripiego se mancano i dati dei fusi: l'ora legale europea va dall'ultima
        # domenica di marzo all'ultima di ottobre.
        u = dt.astimezone(timezone.utc)
        legale = (4 <= u.month <= 9
                  or (u.month == 3 and u.day >= 25)
                  or (u.month == 10 and u.day < 25))
        dt = u.astimezone(timezone(timedelta(hours=2 if legale else 1)))
    return dt.strftime('%Y%m%d%H%M')


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
    if cache.get('_versione') != VERSIONE_REGOLE:
        print('regole cambiate: la memoria dei titoli riparte da zero')
        cache = {'_versione': VERSIONE_REGOLE}
    print('titoli gia\' noti in cache:', len(cache) - 1)

    radice = ET.parse(GUIDA).getroot()

    canali = {}
    for c in radice.findall('channel'):
        nome = c.find('display-name')
        canali[c.get('id')] = (nome.text or c.get('id')) if nome is not None else c.get('id')

    fusi = {}
    grezzi = []
    for p in radice.findall('programme'):
        t = p.find('title')
        if t is None or not (t.text or '').strip():
            continue
        d = p.find('desc')
        mf = re.search(r'([+-]\d{4})\s*$', p.get('start') or '')
        fusi[mf.group(1) if mf else 'assente'] = fusi.get(mf.group(1) if mf else 'assente', 0) + 1
        grezzi.append({
            'canale': p.get('channel'),
            'inizio': quando(p.get('start')),
            'fine': quando(p.get('stop')),
            'titolo': t.text.strip(),
            'ep': episodio_da(d.text if d is not None else ''),
        })
    print('programmi nel palinsesto:', len(grezzi))
    print('fusi orari trovati nella fonte:', fusi, '-> convertiti all\'ora di Roma')

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
        time.sleep(0.06)   # gentilezza verso TMDB
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
        # Se il palinsesto segna "S6 Ep7", quello che va in onda e' una puntata:
        # un film non ha stagioni. Un aggancio a un film e' quindi sbagliato per
        # costruzione, per quanto il nome corrisponda. E' il criterio piu'
        # affidabile trovato: sui dati veri coglieva 271 errori su 271, senza
        # toccare un solo film autentico.
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
