import asyncio
import os
import threading
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
from asyncua import Client, ua

# Overridable via env vars, e.g.:
#   OPCUA_URL=opc.tcp://127.0.0.1:4840/openplc/opcua ros2 run plc_bridge bridge_node
# For anonymous login (no user/pass), set OPCUA_USER to an empty string:
#   OPCUA_USER= ros2 run plc_bridge bridge_node
OPCUA_URL = os.environ.get("OPCUA_URL", "opc.tcp://127.0.0.1:4840/openplc/opcua")
OPCUA_USER = os.environ.get("OPCUA_USER", "admin")
OPCUA_PASS = os.environ.get("OPCUA_PASS", "1234")
POLL_INTERVAL = 0.1


async def discover_variables(client, start_node=None, prefix="/plc"):
    if start_node is None:
        start_node = client.nodes.objects

    discovered = {}
    children = await start_node.get_children()

    for child in children:
        node_id = child.nodeid

        if node_id.NamespaceIndex == 0:
            continue

        node_class = await child.read_node_class()
        browse_name = (await child.read_browse_name()).Name

        if node_class == ua.NodeClass.Variable:
            topic_name = f"{prefix}/{browse_name}"
            access_level = await child.get_access_level()
            writable = ua.AccessLevel.CurrentWrite in access_level
            discovered[browse_name] = (child, topic_name, writable)
        elif node_class == ua.NodeClass.Object:
            nested = await discover_variables(client, child, f"{prefix}/{browse_name}")
            discovered.update(nested)

    return discovered


class PlcBridge(Node):
    def __init__(self):
        super().__init__('plc_opcua_bridge')
        self.publishers_map = {}
        self.last_values = {}
        self.write_nodes = {}
        self.pending_writes = {}
        self._lock = threading.Lock()
        self.get_logger().info("PLC bridge node pokrenut, cekam discovery...")

    def setup_topics(self, variables: dict):
        for name, (opcua_node, topic_name, writable) in variables.items():
            self.publishers_map[name] = self.create_publisher(Bool, topic_name, 10)
            self.last_values[name] = None

            if writable:
                self.write_nodes[name] = opcua_node
                write_topic = f"/plc/write/{name}"
                self.create_subscription(
                    Bool, write_topic,
                    lambda msg, n=name: self._queue_write(n, msg.data),
                    10
                )

        writable_names = [n for n, (_, _, w) in variables.items() if w]
        self.get_logger().info(
            f"Discovery gotov, {len(variables)} varijabli: {list(variables.keys())}"
        )
        self.get_logger().info(
            "Write topici (samo writable): " + ", ".join(f"/plc/write/{n}" for n in writable_names)
        )

    def _queue_write(self, name, value):
        with self._lock:
            self.pending_writes[name] = value

    def publish_value(self, name, value):
        # Publish every poll so late-joining / reconnecting subscribers always get the
        # current state (a change-only publish is lost if the subscriber matched late).
        msg = Bool()
        msg.data = bool(value)
        self.publishers_map[name].publish(msg)
        if value != self.last_values.get(name):
            self.get_logger().info(f"{name} -> {value}")
            self.last_values[name] = value


async def opcua_loop(node: PlcBridge):
    client = Client(url=OPCUA_URL)
    if OPCUA_USER:
        client.set_user(OPCUA_USER)
        client.set_password(OPCUA_PASS)
        node.get_logger().info(f"Spajam se na {OPCUA_URL} kao '{OPCUA_USER}'")
    else:
        node.get_logger().info(f"Spajam se na {OPCUA_URL} anonimno")

    async with client:
        node.get_logger().info("Spojen na OPC UA server, pokrecem discovery")
        variables = await discover_variables(client)
        node.setup_topics(variables)

        while rclpy.ok():
            for name, (opcua_node, _, _) in variables.items():
                try:
                    value = await opcua_node.read_value()
                    node.publish_value(name, value)
                except Exception as e:
                    node.get_logger().warn(f"Greska pri citanju {name}: {e}")

            with node._lock:
                writes = dict(node.pending_writes)
                node.pending_writes.clear()

            for name, value in writes.items():
                try:
                    await node.write_nodes[name].write_value(value)
                    node.get_logger().info(f"Upisano {name} = {value}")
                except Exception as e:
                    node.get_logger().warn(f"Greska pri upisu {name}: {e}")

            await asyncio.sleep(POLL_INTERVAL)


def main():
    rclpy.init()
    node = PlcBridge()

    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    try:
        asyncio.run(opcua_loop(node))
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
