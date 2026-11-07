# Parcări cu plată în Arad — date Recons

## Portal public

[Site, hartă și descărcări CSV / JSON / GeoJSON](https://baditaflorin.github.io/arad-parking-data/)

Date prelucrate, gata de descărcat, plus codul care le reconstruiește din sursele publice.
Inventarul descrie **străzi, tronsoane, piețe și zone de parcare**, nu locuri individuale.

## Descărcări

| Fișier | Conținut |
| --- | --- |
| [CSV](data/records.csv) | 79 de poziții din inventarul Recons |
| [JSON](data/records.json) | Aceleași poziții, cu tipuri și valori `null` păstrate |
| [GeoJSON](data/records.geojson) | Toate cele 79 de poziții: 67 au geometrie OSM de **referință stradală**, 12 au `geometry: null` |
| [Tarife CSV](data/tariffs.csv) · [JSON](data/tariffs.json) | 82 de combinații categorie / zonă / durată / modalitate de plată |
| [Zone CSV](data/zones.csv) · [JSON](data/zones.json) | Totalurile declarate de sursă și sumele calculate |
| [Reguli](data/rules.json) | Programul citat din sursă și excepția de duminică pentru zona Obor |
| [Metadate](data/metadata.json) | Acoperire, număr de înregistrări, licență, limitări, surse |
| [Dicționarul datelor](data/schema.json) | Semnificația câmpurilor și a coordonatelor |

Pagina din `docs/`, pregătită separat, pune descărcările înaintea hărții. Exemplele
din `examples/` arată consumul în Go, Python, curl și Node.js.

## Surse și ce reprezintă datele

- Inventar, capacități, tarife, reguli: [Recons — Parcări cu plată](https://www.reconsarad.ro/parcari-cu-plata).
- Geometrie de referință: [OpenStreetMap](https://www.openstreetmap.org/copyright), extrasă printr-o singură interogare Overpass pentru străzi cu nume în zona Aradului. Interogarea și răspunsul sunt păstrate în `raw/`.
- `raw/sources.json` păstrează data preluării și SHA-256 al HTML-ului; `raw/osm-sources.json` păstrează interogarea, timestampul bazei OSM, atribuirea și SHA-256.
- Nu sunt incluse date despre titulari de abonamente, autoturisme, licitații rezidențiale sau alte persoane.
- Snapshotul oficial nu oferă o dată unică de intrare în vigoare pentru toate informațiile. `retrieved_at` este data preluării, nu data de aplicare a unui tarif.

## Calitatea datelor: discrepanțe păstrate explicit

| Zonă | Poziții | Sumă din poziții | Total scris de Recons | Diferență |
| --- | ---: | ---: | ---: | ---: |
| 0 | 3 | 690 | 690 | 0 |
| 1 | 36 | 2.371 | 2.392 | -21 |
| 2 | 39 | 3.508 | 3.608 | -100 |
| 3 | 1 | 187 | Nu este indicat separat | — |
| Total | 79 | **6.756** | **6.877** | **-121** |

Nu corectăm totalurile prin distribuirea arbitrară a diferenței. Cele două poziții
Corneliu Coposu (60 și 77 de locuri) rămân separate. Descrierea Pieței Spitalului
conține 33 + 21 + 18 = 72; totalul de 72 este folosit o singură dată.

Tabelul SMS are antete „lei”, dar celulele spun **EUR + TVA**. Exportul folosește
moneda din celulă și păstrează antetul, textul și avertismentul. Trei tarife de
rezervare sunt scrise neclar (`21,900,00`, `17,520,00`, `13,140,00`): `amount` rămâne
`null`, iar `source_value` păstrează valoarea originală. Tarifele rezervărilor și
abonamentelor nu sunt tarife orare și au categorii separate.

## Coordonate și hartă

**Harta reprezintă străzi de referință, nu suprafața exactă în care se poate parca.**

Recons nu publică în inventarul HTML coordonate pentru fiecare poziție. Hărțile
încorporate în subsol indică sediul operatorului; acestea nu sunt folosite.

Pentru 67 de poziții, numele străzii este asociat cu geometria reală a unor
segmente OpenStreetMap, cu extinderea transparentă a abrevierilor din sursă.
`geometry_role` este `reference_street`, iar `location_precision` este
`street_reference_not_parking_extent`. Un tronson limitat prin două intersecții
în descrierea Recons poate avea aici și porțiuni OSM în afara tronsonului:
nu am inventat o delimitare exactă pe baza textului.

`latitude` și `longitude` sunt coordonatele unui **nod real de pe geometria OSM**,
folosit numai ca reper aproximativ al străzii. Nu indică intrarea, un loc liber
sau o destinație verificată pentru navigație. Toate ID-urile OSM sunt păstrate
în `geocoding_reference_ids`. Folosiți GeoJSON pentru geometrie; perechea lat/lon
este doar o conveniență pentru sisteme care acceptă numai puncte.

12 poziții rămân fără geometrie: cazuri complexe precum medianele, incinta
Ștrandului, accesul Obor sau un nume fără potrivire verificată. Sunt enumerate
în [geocoding-unmatched.json](data/geocoding-unmatched.json). Nu folosim centrul
orașului sau coordonatele unei străzi vecine ca soluție de rezervă.

## Reproducere — Python 3.10+, fără dependințe externe

```bash
# Reface inventarul și tarifele din snapshotul verificat, fără internet.
python scripts/scrape_recons.py --offline

# Reface potrivirile OSM din snapshotul verificat.
python scripts/geocode_osm.py --offline
python scripts/scrape_recons.py --offline

# Actualizează inventarul din pagina Recons.
python scripts/scrape_recons.py

# Opțional, actualizează stratul OSM (o singură cerere, cache local).
python scripts/geocode_osm.py
python scripts/scrape_recons.py --offline

# Teste de parsare, sume, monede, identificatori și geometrii.
python -m unittest discover -s tests -v
```

La schimbarea structurii paginii, parserul eșuează cu o eroare explicită în loc
să publice un fișier gol. Snapshotul este verificat cu SHA-256 înainte de parsare.
Parserul nu execută JavaScript și nu are nevoie de cont, chei sau tokenuri.

Actualizarea inventarului poate produce poziții noi care nu au încă o potrivire
geografică. Rulați din nou pasul de geocodare și verificați lista cazurilor fără
potrivire. Numele extinse în `NAME_EXPANSIONS` sunt decizii de potrivire vizibile
în cod; ele nu reprezintă o redenumire oficială a străzilor.

## Licențe

Codul propriu este MIT. Pentru datele oficiale nu am identificat o licență open
data explicită; această limitare rămâne în metadate. Geometria OpenStreetMap
are licența ODbL-1.0 și atribuirea „© OpenStreetMap contributors”.
Detaliile sunt în [LICENSE-DATA.md](LICENSE-DATA.md).

## Site, exemple și publicare

Vezi [PAGES.md](PAGES.md) pentru site-ul static din `docs/`, cele patru exemple
executabile și pașii de publicare. Repository-ul este public; site-ul și descărcările sunt publicate prin GitHub Pages. Sursa, datele și exemplele
sunt disponibile și în arhiva `docs/downloads/source.zip`.
