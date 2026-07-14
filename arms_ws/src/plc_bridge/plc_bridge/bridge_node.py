import asyncio
import os
import threading
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Int32, Float64
from asyncua import Client, ua

# Overridable via env vars, e.g.:
#   OPCUA_URL=opc.tcp://127.0.0.1:4840/openplc/opcua ros2 run plc_bridge bridge_node
# For anonymous login (no user/pass), set OPCUA_USER to an empty string:
#   OPCUA_USER= ros2 run plc_bridge bridge_node
OPCUA_URL = os.environ.get("OPCUA_URL", "opc.tcp://127.0.0.1:4840/openplc/opcua")
OPCUA_USER = os.environ.get("OPCUA_USER", "admin")
OPCUA_PASS = os.environ.get("OPCUA_PASS", "1234")
POLL_INTERVAL = 0.1

# OPC UA variant type -> (ROS msg class, python cast). Integer flavours all map to
# Int32 (states/counts fit); reals to Float64; booleans to Bool.
_INT_TYPES = {
    ua.VariantType.SByte, ua.VariantType.Byte,
    ua.VariantType.Int16, ua.VariantType.UInt16,
    ua.VariantType.Int32, ua.VariantType.UInt32,
    ua.VariantType.Int64, ua.VariantType.UInt64,
}
_FLOAT_TYPES = {ua.VariantType.Float, ua.VariantType.Double}


def ros_type_for(vtype):
    """Map an OPC UA VariantType to (ROS msg class, python cast) or (None, None)."""
    if vtype == ua.VariantType.Boolean:
        return Bool, bool
    if vtype in _INT_TYPES:
        return Int32, int
    if vtype in _FLOAT_TYPES:
        return Float64, float
    return None, None


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
            vtype = await child.read_data_type_as_variant_type()
            discovered[browse_name] = (child, topic_name, writable, vtype)
        elif node_class == ua.NodeClass.Object:
            nested = await discover_variables(client, child, f"{prefix}/{browse_name}")
            discovered.update(nested)

    return discovered


class PlcBridge(Node):
    def __init__(self):
        super().__init__('plc_opcua_bridge')
        self.publishers_map = {}   # name -> (publisher, MsgClass, cast)
        self.last_values = {}
        self.write_nodes = {}      # name -> (opcua_node, vtype, cast)
        self.pending_writes = {}
        self._lock = threading.Lock()
        self.get_logger().info("PLC bridge node pokrenut, cekam discovery...")

    def setup_topics(self, variables: dict):
        for name, (opcua_node, topic_name, writable, vtype) in variables.items():
            MsgClass, cast = ros_type_for(vtype)
            if MsgClass is None:
                self.get_logger().warn(
                    f"Preskacem '{name}': nepodrzan OPC UA tip {vtype}"
                )
                continue

            self.publishers_map[name] = (
                self.create_publisher(MsgClass, topic_name, 10), MsgClass, cast
            )
            self.last_values[name] = None

            if writable:
                self.write_nodes[name] = (opcua_node, vtype, cast)
                write_topic = f"/plc/write/{name}"
                self.create_subscription(
                    MsgClass, write_topic,
                    lambda msg, n=name: self._queue_write(n, msg.data),
                    10
                )

        writable_names = [n for n in variables if n in self.write_nodes]
        self.get_logger().info(
            f"Discovery gotov, {len(self.publishers_map)} varijabli: "
            f"{list(self.publishers_map.keys())}"
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
        pub, MsgClass, cast = self.publishers_map[name]
        msg = MsgClass()
        msg.data = cast(value)
        pub.publish(msg)
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
            for name in node.publishers_map:
                opcua_node = variables[name][0]
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
                    opcua_node, vtype, cast = node.write_nodes[name]
                    await opcua_node.write_value(ua.Variant(cast(value), vtype))
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
