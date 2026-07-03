# PLC-ROS2 Bridge Stack

OpenPLC Runtime v4 + ROS2 (arm_api2) integracija preko OPC UA i Modbus TCP.

## Sadržaj
- `arms_ws/src/plc_bridge/` — ROS2 paket (bridge_node = OPC UA, modbus_sensor_bridge = Modbus senzor server)
- `openplc_runtime/` — PLC projekt (plc.xml), plugin config, generirani Modbus/OPC UA configi

## Preduvjeti
- Docker
- ROS2 Jazzy (unutar arm_api2_tutorial containera, vidi CroboticSolutions/docker_files)
- OpenPLC Runtime v4 (ghcr.io/autonomy-logic/openplc-runtime)
- OpenPLC Editor v4 (AppImage)

## Bitno
- `openplc_runtime` container mora ići s `--network host`
- `modbus_slave` plugin u `plugins.conf` mora biti isključen (0) da ne blokira port 502
- `bridge_node.py` čita OPCUA_HOST/OPCUA_PASS iz environment varijabli
