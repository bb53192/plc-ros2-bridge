

#!/usr/bin/env python3
import asyncio
import threading
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusDeviceContext,
    ModbusServerContext,
)
from pymodbus.server import ModbusTcpServer

MODBUS_HOST = "0.0.0.0"
MODBUS_PORT = 502

ADDR_MOTION = 1
ADDR_DOOR = 2
ADDR_PART_PRESENT = 3  # gripper cell: komad naslonjen na graničnik na kraju trake
DEVICE_ID = 0  # catch-all device id za single=True context


class ModbusSensorBridge(Node):
    def __init__(self):
        super().__init__('modbus_sensor_bridge')
        self.server = None  # postavlja se kasnije, kad server krene
        self.loop = None

        self.create_subscription(Bool, '/sensors/motion', self.motion_callback, 10)
        self.create_subscription(Bool, '/sensors/door_closed', self.door_callback, 10)
        self.create_subscription(Bool, '/sensors/part_present', self.part_present_callback, 10)

        self.get_logger().info("Modbus sensor bridge node pokrenut")

    def _write_value(self, address, value):
        if self.server is None or self.loop is None:
            self.get_logger().warn("Server jos nije spreman, preskacem write")
            return
        coro = self.server.async_setValues(DEVICE_ID, 2, address, [int(value)])
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    def motion_callback(self, msg: Bool):
        self._write_value(ADDR_MOTION, msg.data)
        self.get_logger().info(f"Motion Sensor -> {msg.data}")

    def door_callback(self, msg: Bool):
        self._write_value(ADDR_DOOR, msg.data)
        self.get_logger().info(f"Door Closed -> {msg.data}")

    def part_present_callback(self, msg: Bool):
        self._write_value(ADDR_PART_PRESENT, msg.data)
        self.get_logger().info(f"Part Present -> {msg.data}")


async def run_modbus_server(node: ModbusSensorBridge):
    node.loop = asyncio.get_running_loop()

    di_block = ModbusSequentialDataBlock(1, [0] * 10)
    device_context = ModbusDeviceContext(di=di_block)
    context = ModbusServerContext(devices=device_context, single=True)

    server = ModbusTcpServer(context, address=(MODBUS_HOST, MODBUS_PORT))
    node.server = server

    await server.serve_forever()


def main():
    rclpy.init()
    node = ModbusSensorBridge()

    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    try:
        asyncio.run(run_modbus_server(node))
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
