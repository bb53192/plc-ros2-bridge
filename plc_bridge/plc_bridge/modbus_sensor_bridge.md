# `modbus_sensor_bridge.py` — dokumentacija

ROS2 → Modbus TCP most, **suprotnog oblika** od `bridge_node.py` (OPC UA) — vidi
[bridge_node.md](bridge_node.md) za usporedbu. Ovaj node ne otkriva ništa i ne čita ništa s
PLC-a; on samo **gura stanje senzora prema PLC-u** preko tri fiksne, ručno-adresirane
Modbus adrese.

---

## 1. Ključna razlika: ROS je Modbus SERVER, PLC je klijent

```python
server = ModbusTcpServer(context, address=(MODBUS_HOST, MODBUS_PORT))   # linija 65
```

Kod OPC UA bridgea, ROS se spaja VANI na OpenPLC-ov server (ROS = klijent). Ovdje je
obrnuto: ovaj proces **sam pokreće** TCP server na portu `502` (`MODBUS_HOST = "0.0.0.0"`,
linija 17 — sluša na svim sučeljima), i OpenPLC se spaja NA NJEGA kao Modbus master i
periodički ga ispituje (polling), po tempu koji je konfiguriran na OpenPLC-ovoj strani, ne
ovdje.

ROS node ovdje glumi senzorski uređaj (slave); PLC je taj koji pита.

---

## 2. Nema discoverya — sve je hardkodirano (linije 20–22)

```python
ADDR_MOTION = 1
ADDR_DOOR = 2
ADDR_PART_PRESENT = 3
```

Tri fiksne ROS pretplate → tri fiksne Modbus adrese. Za novi senzor treba ručno dodati
`create_subscription` + novu adresu u kodu — nema rekurzivnog pretraživanja kao kod OPC UA
varijabli. Adrese se moraju ručno poklapati s OpenPLC-ovom vlastitom konfiguracijom Modbus
uređaja (mapiranje adresa 1/2/3 na `%IX` bitove, na PLC strani).

---

## 3. Samo jedan smjer — nema `/write/...` topica ovdje

Za razliku od OPC UA bridgea, ovaj node nikad ne prima naredbe od PLC-a. Senzor je po
prirodi jednosmjeran signal (motion, vrata zatvorena, dio prisutan) — PLC samo čita, nikad
ne piše natrag kroz ovaj kanal.

---

## 4. Podatkovni model — Discrete Inputs (linije 61–63)

```python
di_block = ModbusSequentialDataBlock(1, [0] * 10)          # 10 bitova, od adrese 1
device_context = ModbusDeviceContext(di=di_block)            # SAMO discrete inputs
context = ModbusServerContext(devices=device_context, single=True)  # 1 slave, bez unit-id routinga
```

`di` (Discrete Inputs) je Modbus tip registra koji je po definiciji **read-only za mastera**
— PLC ga smije samo čitati, nikad pisati, što se poklapa sa senzorskom semantikom. Server
priprema 10 bitova ali koristi samo 3 (adrese 1, 2, 3); `single=True` znači da nema
routiranja po više "device id"-ova — jedan virtualni uređaj, `DEVICE_ID = 0` catch-all
(linija 23).

---

## 5. Push, ne poll — piše se odmah kad ROS poruka stigne (linije 38–43)

```python
def _write_value(self, address, value):
    coro = self.server.async_setValues(DEVICE_ID, 2, address, [int(value)])
    asyncio.run_coroutine_threadsafe(coro, self.loop)
```

Nema periodičke petlje na ROS strani (za razliku od OPC UA bridgeovog `POLL_INTERVAL`) —
vrijednost se upiše u Modbus registar **odmah** kad ROS callback primi poruku. `2` je
Modbus function code za Discrete Inputs (odgovara `di_block`).

`run_coroutine_threadsafe` je isti obrazac kao u `bridge_node.py`: ROS callback trči u
`rclpy.spin()` threadu, Modbus server živi na asyncio event loopu glavnog threada — poziv
se mora sigurno prebaciti preko granice threada.

---

## 6. Threading model — isti oblik kao OPC UA bridge (linije 71–84)

- **`main()`** kreira `rclpy.spin(node)` u zasebnom daemon threadu (linija 75-76) — obrada
  dolaznih `/sensors/*` poruka.
- **Glavni thread** vozi `asyncio.run(run_modbus_server(node))` (linija 79) — Modbus TCP
  server (`serve_forever()`) na asyncio loopu.

`node.loop` se postavlja unutar `run_modbus_server()` (linija 59), prije nego server
krene — ako ROS poruka stigne PRIJE nego je server spreman, `_write_value()` je tiho
preskoči uz warning (linije 39–41: `if self.server is None or self.loop is None`).

---

## 7. Gdje se ovo koristi u projektu

Od svih PLC↔ROS signala u stacku, **samo `part_present` ide preko Modbusa** (vidi
docstring u `cell_io.py`) — sve ostalo (handshake, job dispatch, stanje) ide preko OPC UA
bridgea jer treba dvosmjernost i više varijabli bez ručnog adresiranja svake. Modbus je
ovdje ostao kao namjerno jednostavan, jednosmjeran kanal za jedan fizički senzor.

---

## 8. Primjer end-to-end (`part_present`)

1. Gazebo contact senzor javlja da je dio naslonjen na graničnik trake.
2. `cell_io.py` (ili `tending_node.py`) publisha `std_msgs/Bool` na `/sensors/part_present`.
3. `part_present_callback()` (linija 53–55) poziva `_write_value(ADDR_PART_PRESENT, msg.data)`.
4. Vrijednost se odmah upiše u Modbus discrete-input bit na adresi 3.
5. OpenPLC (Modbus master), prema svojoj vlastitoj konfiguraciji Modbus uređaja, pročita tu
   adresu u svom sljedećem scan ciklusu i mapira je na `%IX` bit u ST programu.

Usporedi s `job_slot` primjerom u [bridge_node.md](bridge_node.md) §8 — ista ideja, ali
suprotan smjer (ovdje ROS → PLC, tamo PLC → ROS) i bez ikakvog discoverya.
