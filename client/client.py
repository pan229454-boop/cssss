#!/usr/bin/env python3
"""
NAT Tunnel Client - 内网穿透客户端
运行在内网设备上，连接到公网服务器建立隧道
"""

import asyncio
import json
import logging
import struct
import time
import yaml
import sys
import signal
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('nat-tunnel-client')

# 协议常量（与服务端保持一致）
PROTO_VERSION = 1
MSG_AUTH = 0x01
MSG_AUTH_RESP = 0x02
MSG_NEW_TUNNEL = 0x03
MSG_NEW_TUNNEL_RESP = 0x04
MSG_DATA = 0x05
MSG_CLOSE_CONN = 0x06
MSG_HEARTBEAT = 0x07
MSG_HEARTBEAT_RESP = 0x08
MSG_CLOSE_TUNNEL = 0x09
MSG_LIST_TUNNELS = 0x0A
MSG_LIST_TUNNELS_RESP = 0x0B
MSG_UDP_DATA = 0x0C        # UDP数据报

HEADER_SIZE = 6
MAX_BODY_SIZE = 1024 * 1024


def load_config():
    config_path = Path(__file__).parent / 'config.yaml'
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    logger.error('配置文件 config.yaml 不存在')
    sys.exit(1)


def pack_message(msg_type: int, body: bytes) -> bytes:
    header = struct.pack('!BBL', PROTO_VERSION, msg_type, len(body))
    return header + body


async def read_message(reader: asyncio.StreamReader):
    header = await reader.readexactly(HEADER_SIZE)
    version, msg_type, body_length = struct.unpack('!BBL', header)
    if version != PROTO_VERSION:
        raise ValueError(f'协议版本不匹配: {version}')
    if body_length > MAX_BODY_SIZE:
        raise ValueError(f'消息体过大: {body_length}')
    body = await reader.readexactly(body_length) if body_length > 0 else b''
    return msg_type, body


def pack_data_header(conn_id: str, data: bytes) -> bytes:
    conn_id_bytes = conn_id.encode()
    return struct.pack('!H', len(conn_id_bytes)) + conn_id_bytes + data


def unpack_data_header(body: bytes):
    conn_id_len = struct.unpack('!H', body[:2])[0]
    conn_id = body[2:2 + conn_id_len].decode()
    data = body[2 + conn_id_len:]
    return conn_id, data


class _UDPLocalProtocol(asyncio.DatagramProtocol):
    """UDP本地转发协议 - 将本地UDP服务的响应发回服务端"""

    def __init__(self, conn_id, client):
        self.conn_id = conn_id
        self.client = client
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        payload = pack_data_header(self.conn_id, data)
        asyncio.ensure_future(self.client._send(MSG_UDP_DATA, payload))

    def error_received(self, exc):
        pass

    def connection_lost(self, exc):
        self.client._udp_connections.pop(self.conn_id, None)


class NATTunnelClient:
    """NAT隧道客户端"""

    def __init__(self, config):
        self.config = config
        self.server_addr = config['server_addr']
        self.server_port = config.get('server_port', 7000)
        self.client_name = config['client_name']
        self.token = config['auth_token']
        self.tunnels = config.get('tunnels', [])
        self.reader = None
        self.writer = None
        self._running = False
        self._local_connections = {}   # conn_id -> (local_reader, local_writer) [TCP]
        self._udp_connections = {}     # conn_id -> (transport, UDPLocalProtocol) [UDP]
        self._pending_conns = {}       # conn_id -> [buffered_data]  建立中的连接缓冲
        self._write_lock = asyncio.Lock()

    async def _send(self, msg_type, body):
        async with self._write_lock:
            data = pack_message(msg_type, body)
            self.writer.write(data)
            await self.writer.drain()

    async def connect(self):
        """连接到服务器"""
        logger.info(f'正在连接服务器 {self.server_addr}:{self.server_port}...')

        self.reader, self.writer = await asyncio.open_connection(
            self.server_addr, self.server_port
        )

        # 发送认证
        auth_data = json.dumps({
            'client_name': self.client_name,
            'token': self.token,
        }).encode()
        await self._send(MSG_AUTH, auth_data)

        # 等待认证响应
        msg_type, body = await asyncio.wait_for(
            read_message(self.reader), timeout=10
        )
        if msg_type != MSG_AUTH_RESP:
            raise Exception(f'期望认证响应，收到: {msg_type}')

        resp = json.loads(body)
        if not resp.get('success'):
            raise Exception(f'认证失败: {resp.get("message")}')

        logger.info(f'认证成功！客户端名称: {self.client_name}')
        return True

    async def register_tunnels(self):
        """注册配置文件中的所有隧道"""
        for tunnel_cfg in self.tunnels:
            remote_port = tunnel_cfg['remote_port']
            local_addr = tunnel_cfg.get('local_addr', '127.0.0.1')
            local_port = tunnel_cfg['local_port']
            protocol = tunnel_cfg.get('protocol', 'tcp').lower()

            req = json.dumps({
                'remote_port': remote_port,
                'local_addr': local_addr,
                'local_port': local_port,
                'protocol': protocol,
            }).encode()
            await self._send(MSG_NEW_TUNNEL, req)

            # 等待响应
            msg_type, body = await asyncio.wait_for(
                read_message(self.reader), timeout=10
            )
            if msg_type == MSG_NEW_TUNNEL_RESP:
                resp = json.loads(body)
                if resp.get('success'):
                    logger.info(
                        f'隧道注册成功: :{remote_port} -> {local_addr}:{local_port} '
                        f'(ID: {resp.get("tunnel_id")})'
                    )
                else:
                    logger.error(
                        f'隧道注册失败: :{remote_port} -> {local_addr}:{local_port} '
                        f'原因: {resp.get("message")}'
                    )

    async def run(self):
        """运行客户端主循环"""
        self._running = True

        # 启动心跳
        asyncio.create_task(self._heartbeat_loop())

        # 消息循环
        try:
            while self._running:
                msg_type, body = await read_message(self.reader)

                if msg_type == MSG_HEARTBEAT_RESP:
                    pass  # 心跳响应

                elif msg_type == MSG_DATA:
                    await self._handle_data(body)

                elif msg_type == MSG_UDP_DATA:
                    await self._handle_udp_data(body)

                elif msg_type == MSG_CLOSE_CONN:
                    await self._handle_close_conn(body)

                elif msg_type == MSG_NEW_TUNNEL_RESP:
                    resp = json.loads(body)
                    logger.info(f'隧道响应: {resp.get("message")}')

                elif msg_type == MSG_LIST_TUNNELS_RESP:
                    resp = json.loads(body)
                    self._print_tunnels(resp.get('tunnels', []))

        except (asyncio.IncompleteReadError, ConnectionError) as e:
            logger.warning(f'与服务器连接断开: {e}')
        except Exception as e:
            logger.error(f'客户端异常: {e}', exc_info=True)
        finally:
            self._running = False

    async def _heartbeat_loop(self):
        """心跳循环"""
        interval = self.config.get('heartbeat_interval', 30)
        while self._running:
            await asyncio.sleep(interval)
            try:
                await self._send(MSG_HEARTBEAT, b'')
            except Exception:
                self._running = False
                break

    async def _handle_data(self, body: bytes):
        """处理从服务器收到的数据"""
        conn_id, data = unpack_data_header(body)

        if data[:12] == b'__NEW_CONN__':
            # 新连接请求，解析tunnel信息
            try:
                tunnel_info = json.loads(data[12:])
                local_addr = tunnel_info.get('local_addr', '127.0.0.1')
                local_port = tunnel_info.get('local_port')
            except (json.JSONDecodeError, KeyError):
                local_addr = '127.0.0.1'
                local_port = self.tunnels[0]['local_port'] if self.tunnels else None
            # 先初始化缓冲再启动任务，避免竞争条件
            self._pending_conns[conn_id] = []
            asyncio.create_task(self._establish_local_conn(conn_id, local_addr, local_port))
            return

        # 如果本地连接尚在建立中，将数据入缓冲并等待
        if conn_id in self._pending_conns:
            self._pending_conns[conn_id].append(data)
            return

        # 转发数据到本地连接
        local = self._local_connections.get(conn_id)
        if local:
            local_reader, local_writer = local
            try:
                local_writer.write(data)
                await local_writer.drain()
            except (ConnectionError, OSError):
                self._local_connections.pop(conn_id, None)
                try:
                    local_writer.close()
                except Exception:
                    pass
                close_body = json.dumps({'conn_id': conn_id}).encode()
                await self._send(MSG_CLOSE_CONN, close_body)

    async def _establish_local_conn(self, conn_id, local_addr='127.0.0.1', local_port=None):
        """建立到本地服务的连接"""
        if not local_port:
            logger.error(f'无法为 conn {conn_id} 找到本地端口')
            self._pending_conns.pop(conn_id, None)
            return

        try:
            local_reader, local_writer = await asyncio.open_connection(
                local_addr, local_port
            )
            self._local_connections[conn_id] = (local_reader, local_writer)

            # 通知服务端连接已建立
            await self._send(MSG_DATA, pack_data_header(conn_id, b'__CONN_READY__'))

            # 充偿建立期间缓存的数据并发送给本地服务
            buffered = self._pending_conns.pop(conn_id, [])
            if buffered:
                for buf_data in buffered:
                    local_writer.write(buf_data)
                await local_writer.drain()

            # 开始从本地读数据并发送给服务端
            asyncio.create_task(
                self._relay_local_to_server(local_reader, conn_id)
            )
        except Exception as e:
            logger.error(f'连接本地服务失败 {local_addr}:{local_port}: {e}')
            self._pending_conns.pop(conn_id, None)
            close_body = json.dumps({'conn_id': conn_id}).encode()
            await self._send(MSG_CLOSE_CONN, close_body)

    async def _relay_local_to_server(self, local_reader, conn_id):
        """将本地数据转发给服务端"""
        try:
            while self._running:
                data = await local_reader.read(65536)
                if not data:
                    break
                payload = pack_data_header(conn_id, data)
                await self._send(MSG_DATA, payload)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as e:
            logger.debug(f'本地读取异常: {e}')
        finally:
            self._local_connections.pop(conn_id, None)
            try:
                close_body = json.dumps({'conn_id': conn_id}).encode()
                await self._send(MSG_CLOSE_CONN, close_body)
            except Exception:
                pass

    async def _handle_close_conn(self, body: bytes):
        """处理关闭连接"""
        data = json.loads(body)
        conn_id = data.get('conn_id')
        # TCP
        local = self._local_connections.pop(conn_id, None)
        if local:
            _, local_writer = local
            try:
                local_writer.close()
            except Exception:
                pass
            return
        # UDP
        conn = self._udp_connections.pop(conn_id, None)
        if conn:
            transport, _ = conn
            try:
                transport.close()
            except Exception:
                pass

    async def _handle_udp_data(self, body: bytes):
        """处理UDP数据报（服务端转发来的UDP数据）"""
        conn_id, data = unpack_data_header(body)

        if data[:16] == b'__NEW_UDP_CONN__':
            # 新虚拟UDP连接
            try:
                info = json.loads(data[16:])
                local_addr = info.get('local_addr', '127.0.0.1')
                local_port = info.get('local_port')
            except Exception:
                return
            if not local_port:
                return
            try:
                loop = asyncio.get_event_loop()
                proto = _UDPLocalProtocol(conn_id, self)
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: proto,
                    remote_addr=(local_addr, int(local_port))
                )
                self._udp_connections[conn_id] = (transport, proto)
                logger.debug(f'UDP虚拟连接已建立: {conn_id} -> {local_addr}:{local_port}')
            except Exception as e:
                logger.error(f'建立本地UDP连接失败 {local_addr}:{local_port}: {e}')
            return

        # 转发UDP数据报到本地服务
        conn = self._udp_connections.get(conn_id)
        if conn:
            transport, _ = conn
            try:
                transport.sendto(data)
            except Exception:
                self._udp_connections.pop(conn_id, None)

    def _print_tunnels(self, tunnels):
        """打印隧道列表"""
        if not tunnels:
            logger.info('当前没有活跃的隧道')
            return
        logger.info('=== 活跃隧道 ===')
        for t in tunnels:
            logger.info(
                f'  {t["tunnel_id"]}: :{t["remote_port"]} -> '
                f'{t["local_addr"]}:{t["local_port"]} '
                f'(连接数: {t["connections"]}, '
                f'入流量: {t["bytes_in"]}, 出流量: {t["bytes_out"]})'
            )


async def main():
    config = load_config()

    retry_interval = config.get('retry_interval', 5)
    max_retries = config.get('max_retries', 0)  # 0 = 无限重试
    retries = 0

    while True:
        client = NATTunnelClient(config)
        try:
            await client.connect()
            await client.register_tunnels()
            retries = 0  # 连接成功后重置重试计数
            await client.run()
        except KeyboardInterrupt:
            logger.info('客户端正在关闭...')
            break
        except Exception as e:
            logger.error(f'连接失败: {e}')

        retries += 1
        if max_retries > 0 and retries >= max_retries:
            logger.error(f'已达最大重试次数 {max_retries}，退出')
            break

        logger.info(f'{retry_interval} 秒后重新连接... (重试 #{retries})')
        await asyncio.sleep(retry_interval)


if __name__ == '__main__':
    # Windows需要设置事件循环策略
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('客户端已退出')
