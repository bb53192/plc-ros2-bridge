# `bridge_node.py` — dokumentacija

OPC UA ↔ ROS2 most. Spaja se na OpenPLC-ov OPC UA server, otkriva sve izložene varijable,
i za svaku otvara odgovarajući ROS2 topic. Ovo je jedini smjer istine za PLC↔ROS signale u
cijelom stacku — sve ostalo (`door_bridge_node.py`, `tending_node.py`, HMI...) razgovara
preko topica koje OVAJ node stvara.

---

## 1. Konfiguracija (linije 9–16)

| Env var | Default | Značenje |
|---|---|---|
| `OPCUA_URL` | `opc.tcp://127.0.0.1:4840/openplc/opcua` | adresa OPC UA servera |
| `OPCUA_USER` | `admin` | korisnik za login; **prazan string = anonimni login** |
| `OPCUA_PASS` | `1234` | lozinka |

```bash
OPCUA_URL=opc.tcp://127.0.0.1:4840/openplc/opcua OPCUA_USER=user OPCUA_PASS=1234 \
  ros2 run plc_bridge bridge_node
```

`POLL_INTERVAL = 0.1` (linija 16) — koliko često se PLC čita/piše, hardkodirano, nije env-ovojivo.

---

## 2. Threading model — DVA paralelna toka

Ovo je lako previdjeti, ali bitno je za razumjeti ponašanje pod opterećenjem:

- **`main()` (linije 165–182)** kreira `rclpy.spin(node)` u **zasebnom daemon threadu**
  (linija 169) — to je ono što drži ROS2 executor živim (obrada dolaznih poruka na
  `/plc/write/<var>` subscriptionima, tj. `_queue_write` callback).
- **Glavni thread** vozi `asyncio.run(opcua_loop(node))` (linija 173) — cijela OPC UA
  komunikacija (read/write prema PLC-u) je asinkrona, na glavnom threadu.

Dva threada dijele state preko `node.pending_writes` dicta, zaštićenog s `threading.Lock`
(`node._lock`, linija 76) — `_queue_write()` (ROS spin thread) samo upisuje u dict,
`opcua_loop()` (asyncio thread) ga prazni pod istim lockom (linije 150–152). Nema drugog
dijeljenog mutabilnog state-a između threadova.

---

## 3. Tip mapiranje (linije 18–37)

| OPC UA `VariantType` | ROS msg | Python cast |
|---|---|---|
| `Boolean` | `std_msgs/Bool` | `bool` |
| `SByte, Byte, Int16, UInt16, Int32, UInt32, Int64, UInt64` | `std_msgs/Int32` | `int` |
| `Float, Double` | `std_msgs/Float64` | `float` |
| bilo što drugo | — | **varijabla se preskače**, warn log |

---

## 4. Discovery — `discover_variables()` (linije 40–66)

Rekurzivna funkcija koja **live browsa** OPC UA adresni prostor servera, počevši od
`client.nodes.objects` (root). Za svaki child node:

- ako je `NamespaceIndex == 0` → **preskoči** (linija 50-51; to su OPC UA standardni/interni
  node-ovi, ne PLC varijable)
- ako je `NodeClass.Variable` → pročita ime, tip, `access_level` (writable ili ne),
  spremi kao `(node, topic_name, writable, vtype)` u `discovered` dict, ključ = ime varijable
- ako je `NodeClass.Object` → **rekurzija** dublje, s produženim prefixom (linija 63) —
  ovo je kako `PLC.main.job_slot` postaje `/plc/job_slot`: `main` je Object node, `job_slot`
  je Variable unutar njega

**Poziva se TOČNO JEDNOM**, u `opcua_loop()` odmah nakon spajanja (linija 138). Nema
ponovnog pozivanja tijekom rada procesa — vidi §7 Ograničenja.

Bitno: ovo NE čita `opcua.json`. Taj fajl konfigurira OpenPLC-ovu stranu (koje varijable
server uopće izlaže); `discover_variables()` samo čita rezultat toga preko same OPC UA
protokola, uživo sa servera.

---

## 5. `PlcBridge` klasa (linije 69–124)

State koji drži:
- `publishers_map[name] = (publisher, MsgClass, cast)` — za objavljivanje na `/plc/<name>`
- `write_nodes[name] = (opcua_node, vtype, cast)` — samo za writable varijable
- `last_values[name]` — zadnja objavljena vrijednost, samo za odlučivanje kad logirati promjenu
- `pending_writes` + `_lock` — red čekanja za upise, opisan u §2

### `setup_topics(variables)` (linije 79–109)

Za svaku otkrivenu varijablu:
1. Odredi ROS tip preko `ros_type_for()`. Ako je nepodržan tip → **preskoči**, warn log.
2. **Uvijek** kreiraj publisher na `/plc/<name>`.
3. **Ako je writable** (permission "rw" u OPC UA), dodatno kreiraj subscriber na
   `/plc/write/<name>` koji poziva `_queue_write()`.

### `_queue_write(name, value)` (linije 111–113)

Samo sprema `value` u `pending_writes[name]` pod lockom. Ne piše ništa direktno — pravi
upis se događa u `opcua_loop()`, sljedeći poll ciklus (do 100ms kašnjenja).

### `publish_value(name, value)` (linije 115–124)

Objavljuje na ROS **svaki poll ciklus, bez obzira je li vrijednost promijenjena** (komentar
linije 116–117 objašnjava zašto: kasni/reconnect-ani subscriberi moraju odmah dobiti trenutno
stanje — "change only" pristup bi to izgubio ako subscriber matcha kasno). Log poruka
("`{name} -> {value}`") se ispisuje SAMO kad se vrijednost stvarno promijenila (linija 122),
da log ne bude zatrpan identičnim porukama svakih 100ms.

---

## 6. `opcua_loop()` — glavna petlja (linije 127–162)

1. Kreira OPC UA klijenta, login (user+pass ili anonimno).
2. `async with client:` — spoji se.
3. Discovery (§4), zatim `setup_topics()` (§5).
4. `while rclpy.ok():` beskonačna petlja, svakih `POLL_INTERVAL` (100ms):
   - **Read faza** (linije 142–148): za svaku otkrivenu varijablu, pročita trenutnu
     vrijednost s PLC-a (`opcua_node.read_value()`), objavi je. Greška po varijabli se
     samo logira kao warning — ne ruši petlju, ne prekida ostale varijable.
   - **Write faza** (linije 150–160): izvadi sve pending upise (pod lockom), za svaki
     upiše na OPC UA node (`write_value`). Isto, greška po varijabli je samo warning.
   - `await asyncio.sleep(POLL_INTERVAL)`.

---

## 7. Ograničenja — ČITAJ OVO PRIJE NEGO MIJENJAŠ PLC PROGRAM

- **Nema re-discovery.** Ako PLC program promijeniš tako da dodaješ/brišeš/mijenjaš tip
  neke OPC UA varijable (npr. nova varijabla u `program.st` + `PROGRAM main`), taj proces
  MORA biti restartan (`Ctrl+C` pa opet `ros2 run plc_bridge bridge_node`) da ponovno
  prođe kroz discovery. Dok se ne restarta, radi sa starim setom topica.
- **Nema reconnect/retry.** Ako OPC UA server padne (npr. restartaš OpenPLC kontejner),
  `async with client:` blok baca exception, `opcua_loop()` puca, `asyncio.run()` u `main()`
  propagira grešku — proces se gasi. Treba ga ručno ponovno pokrenuti.
- **Nema CLI argumenata.** Sva konfiguracija je isključivo kroz env varijable (§1); nema
  `argparse`, nema launch fajla u repou (pokreće se direktno s `ros2 run`).

---

## 8. Primjer end-to-end (varijabla `job_slot`)

1. `program.st` → `PROGRAM main` postavlja `job_slot := CNC1.job_slot;` svaki PLC scan.
2. OpenPLC OPC UA server plugin izlaže tu varijablu kao `PLC.main.job_slot` (permission "r").
3. `discover_variables()` je nađe kao Object `main` → Variable `job_slot`, topic postaje
   `/plc/job_slot`, `writable = False` (jer je "r", ne "rw").
4. `setup_topics()` kreira publisher na `/plc/job_slot`, **ne** kreira write subscriber
   (nije writable od strane ROS-a).
5. Svakih 100ms, `opcua_loop()` pročita trenutnu vrijednost preko OPC UA i objavi je.
6. `tending_node.py` na ROS strani subscrira `/plc/job_slot`, reagira kad vidi 1..6.

Kontra-primjer, `robot_busy` (permission "rw", ROS piše natrag u PLC):
1. `tending_node.py` publisha `Bool` na `/plc/write/robot_busy`.
2. `_queue_write("robot_busy", True)` sprema u `pending_writes`.
3. Sljedeći poll ciklus, `opcua_loop()` upiše `True` na OPC UA node preko `write_value()`.
4. PLC (`FB_CNCTendingCycle` unutar `CNC1` instance) sljedeći scan vidi `robot_busy = TRUE`
   preko svog `VAR_INPUT`.
