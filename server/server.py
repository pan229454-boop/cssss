#!/usr/bin/env python3
"""
NAT Tunnel Server - 内网穿透服务端
部署在公网服务器上，接收客户端注册并转发流量
"""

import asyncio
import json
import logging
import hashlib
import hmac
import os
import ssl
import time
import struct
import signal
from collections import defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

try:
    from secrets import token_hex
except ImportError:
    import binascii
    def token_hex(n=32):
        return binascii.hexlify(os.urandom(n)).decode()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('nat-tunnel-server')

# 协议常量
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

# 头部: version(1) + msg_type(1) + body_length(4) = 6 bytes
HEADER_SIZE = 6
MAX_BODY_SIZE = 1024 * 1024  # 1MB


def load_config():
    config_path = Path(__file__).parent / 'config.yaml'
    if config_path.exists() and yaml:
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    elif config_path.exists():
        # 无yaml模块时用json备选
        json_path = Path(__file__).parent / 'config.json'
        if json_path.exists():
            with open(json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    return {
        'control_port': 7000,
        'auth_token': 'change_me_to_a_secure_token',
        'web_port': 7500,
        'web_password': 'admin123',
        'allowed_ports': [10000, 60000],
        'max_tunnels_per_client': 10,
        'heartbeat_interval': 30,
        'heartbeat_timeout': 90,
    }


def pack_message(msg_type: int, body: bytes) -> bytes:
    """打包消息: version(1) + type(1) + length(4) + body"""
    header = struct.pack('!BBL', PROTO_VERSION, msg_type, len(body))
    return header + body


async def read_message(reader: asyncio.StreamReader):
    """读取一条完整消息"""
    header = await reader.readexactly(HEADER_SIZE)
    version, msg_type, body_length = struct.unpack('!BBL', header)
    if version != PROTO_VERSION:
        raise ValueError(f'协议版本不匹配: {version}')
    if body_length > MAX_BODY_SIZE:
        raise ValueError(f'消息体过大: {body_length}')
    body = await reader.readexactly(body_length) if body_length > 0 else b''
    return msg_type, body


class TunnelInfo:
    """隧道信息"""
    def __init__(self, tunnel_id, client_name, remote_port, local_addr, local_port):
        self.tunnel_id = tunnel_id
        self.client_name = client_name
        self.remote_port = remote_port
        self.local_addr = local_addr
        self.local_port = local_port
        self.created_at = time.time()
        self.connections = 0
        self.bytes_in = 0
        self.bytes_out = 0
        self.listener_server = None


class ClientSession:
    """客户端会话"""
    def __init__(self, client_name, reader, writer):
        self.client_name = client_name
        self.reader = reader
        self.writer = writer
        self.tunnels = {}  # tunnel_id -> TunnelInfo
        self.last_heartbeat = time.time()
        self.connected_at = time.time()
        self.pending_connections = {}  # conn_id -> (visitor_reader, visitor_writer)
        self._lock = asyncio.Lock()

    async def send(self, msg_type, body):
        async with self._lock:
            data = pack_message(msg_type, body)
            self.writer.write(data)
            await self.writer.drain()


class NATTunnelServer:
    """NAT隧道服务器"""

    def __init__(self, config):
        self.config = config
        self.clients = {}  # client_name -> ClientSession
        self.tunnels = {}  # tunnel_id -> TunnelInfo
        self.port_to_tunnel = {}  # remote_port -> tunnel_id
        self._running = False
        self._tunnel_counter = 0
        self._conn_counter = 0
        self._stats = {
            'total_connections': 0,
            'total_bytes_in': 0,
            'total_bytes_out': 0,
            'start_time': time.time(),
        }

    def _next_tunnel_id(self):
        self._tunnel_counter += 1
        return f"tunnel_{self._tunnel_counter}"

    def _next_conn_id(self):
        self._conn_counter += 1
        return f"conn_{self._conn_counter}"

    def _verify_token(self, token):
        """验证客户端token"""
        expected = self.config['auth_token']
        return hmac.compare_digest(token, expected)

    def _is_port_allowed(self, port):
        """检查端口是否在允许范围内"""
        port_range = self.config.get('allowed_ports', [10000, 60000])
        return port_range[0] <= port <= port_range[1]

    async def start(self):
        """启动服务器"""
        self._running = True
        control_port = self.config['control_port']

        server = await asyncio.start_server(
            self._handle_client,
            '0.0.0.0',
            control_port
        )
        logger.info('NAT隧道服务器启动在端口 {}'.format(control_port))

        # 启动心跳检测
        asyncio.ensure_future(self._heartbeat_checker())

        # 兼容Python 3.6（无serve_forever）
        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            server.close()
            await server.wait_closed()

    async def _heartbeat_checker(self):
        """检查客户端心跳"""
        while self._running:
            await asyncio.sleep(10)
            timeout = self.config.get('heartbeat_timeout', 90)
            now = time.time()
            dead_clients = []
            for name, session in list(self.clients.items()):
                if now - session.last_heartbeat > timeout:
                    logger.warning(f'客户端 {name} 心跳超时，断开连接')
                    dead_clients.append(name)
            for name in dead_clients:
                await self._remove_client(name)

    async def _handle_client(self, reader, writer):
        """处理新的客户端连接"""
        addr = writer.get_extra_info('peername')
        logger.info(f'新连接来自: {addr}')

        try:
            # 第一条消息必须是认证
            msg_type, body = await asyncio.wait_for(
                read_message(reader), timeout=10
            )
            if msg_type != MSG_AUTH:
                logger.warning(f'期望认证消息，收到: {msg_type}')
                writer.close()
                return

            auth_data = json.loads(body)
            client_name = auth_data.get('client_name', '')
            token = auth_data.get('token', '')

            # 验证
            if not client_name or not self._verify_token(token):
                resp = json.dumps({'success': False, 'message': '认证失败'}).encode()
                writer.write(pack_message(MSG_AUTH_RESP, resp))
                await writer.drain()
                writer.close()
                logger.warning(f'客户端 {addr} 认证失败')
                return

            # 如果已有同名客户端，先断开旧的
            if client_name in self.clients:
                logger.info(f'客户端 {client_name} 重连，断开旧连接')
                await self._remove_client(client_name)

            session = ClientSession(client_name, reader, writer)
            self.clients[client_name] = session

            resp = json.dumps({'success': True, 'message': '认证成功'}).encode()
            writer.write(pack_message(MSG_AUTH_RESP, resp))
            await writer.drain()
            logger.info(f'客户端 {client_name} 认证成功 ({addr})')

            # 进入消息循环
            await self._client_message_loop(session)

        except asyncio.TimeoutError:
            logger.warning(f'{addr} 认证超时')
        except (asyncio.IncompleteReadError, ConnectionError) as e:
            logger.info(f'连接中断: {addr} - {e}')
        except Exception as e:
            logger.error(f'处理客户端异常 {addr}: {e}', exc_info=True)
        finally:
            # 清理
            for name, session in list(self.clients.items()):
                if session.writer is writer:
                    await self._remove_client(name)
                    break
            try:
                writer.close()
            except Exception:
                pass

    async def _client_message_loop(self, session: ClientSession):
        """客户端消息循环"""
        while self._running:
            msg_type, body = await read_message(session.reader)

            if msg_type == MSG_HEARTBEAT:
                session.last_heartbeat = time.time()
                await session.send(MSG_HEARTBEAT_RESP, b'')

            elif msg_type == MSG_NEW_TUNNEL:
                await self._handle_new_tunnel(session, body)

            elif msg_type == MSG_CLOSE_TUNNEL:
                await self._handle_close_tunnel(session, body)

            elif msg_type == MSG_DATA:
                await self._handle_data(session, body)

            elif msg_type == MSG_CLOSE_CONN:
                await self._handle_close_conn(session, body)

            elif msg_type == MSG_LIST_TUNNELS:
                await self._handle_list_tunnels(session)

    async def _handle_new_tunnel(self, session: ClientSession, body: bytes):
        """处理新建隧道请求"""
        data = json.loads(body)
        remote_port = data.get('remote_port')
        local_addr = data.get('local_addr', '127.0.0.1')
        local_port = data.get('local_port')

        # 检验
        if not remote_port or not local_port:
            resp = json.dumps({
                'success': False, 'message': '缺少端口参数'
            }).encode()
            await session.send(MSG_NEW_TUNNEL_RESP, resp)
            return

        if not self._is_port_allowed(remote_port):
            resp = json.dumps({
                'success': False,
                'message': f'端口 {remote_port} 不在允许范围内'
            }).encode()
            await session.send(MSG_NEW_TUNNEL_RESP, resp)
            return

        if remote_port in self.port_to_tunnel:
            resp = json.dumps({
                'success': False,
                'message': f'端口 {remote_port} 已被占用'
            }).encode()
            await session.send(MSG_NEW_TUNNEL_RESP, resp)
            return

        max_tunnels = self.config.get('max_tunnels_per_client', 10)
        if len(session.tunnels) >= max_tunnels:
            resp = json.dumps({
                'success': False,
                'message': f'每个客户端最多 {max_tunnels} 条隧道'
            }).encode()
            await session.send(MSG_NEW_TUNNEL_RESP, resp)
            return

        tunnel_id = self._next_tunnel_id()
        tunnel = TunnelInfo(
            tunnel_id, session.client_name,
            remote_port, local_addr, local_port
        )

        # 在远程端口启动监听
        try:
            listener = await asyncio.start_server(
                lambda r, w: self._handle_visitor(r, w, tunnel_id),
                '0.0.0.0', remote_port
            )
            tunnel.listener_server = listener
        except OSError as e:
            resp = json.dumps({
                'success': False,
                'message': f'无法监听端口 {remote_port}: {e}'
            }).encode()
            await session.send(MSG_NEW_TUNNEL_RESP, resp)
            return

        session.tunnels[tunnel_id] = tunnel
        self.tunnels[tunnel_id] = tunnel
        self.port_to_tunnel[remote_port] = tunnel_id

        resp = json.dumps({
            'success': True,
            'tunnel_id': tunnel_id,
            'message': f'隧道已建立 :{remote_port} -> {local_addr}:{local_port}'
        }).encode()
        await session.send(MSG_NEW_TUNNEL_RESP, resp)
        logger.info(
            f'隧道建立: {tunnel_id} | {session.client_name} | '
            f':{remote_port} -> {local_addr}:{local_port}'
        )

    async def _handle_visitor(self, visitor_reader, visitor_writer, tunnel_id):
        """处理访问者连接（通过公网端口进来的连接）"""
        tunnel = self.tunnels.get(tunnel_id)
        if not tunnel:
            visitor_writer.close()
            return

        client_session = self.clients.get(tunnel.client_name)
        if not client_session:
            visitor_writer.close()
            return

        conn_id = self._next_conn_id()
        addr = visitor_writer.get_extra_info('peername')
        logger.info(f'访问者连接: {addr} -> tunnel {tunnel_id} (conn: {conn_id})')

        tunnel.connections += 1
        self._stats['total_connections'] += 1

        # 保存访问者连接
        client_session.pending_connections[conn_id] = (visitor_reader, visitor_writer)

        # 通知客户端有新连接（携带tunnel信息以便客户端知道连接到哪个本地端口）
        new_conn_info = json.dumps({
            'conn_id': conn_id,
            'tunnel_id': tunnel_id,
            'local_addr': tunnel.local_addr,
            'local_port': tunnel.local_port,
        }).encode()

        try:
            await client_session.send(MSG_DATA, self._pack_data_header(conn_id, b'__NEW_CONN__' + new_conn_info))
            # 开始从访问者读数据并转发给客户端
            asyncio.ensure_future(
                self._relay_visitor_to_client(visitor_reader, client_session, conn_id, tunnel)
            )
        except Exception as e:
            logger.error(f'通知客户端失败: {e}')
            client_session.pending_connections.pop(conn_id, None)
            visitor_writer.close()

    def _pack_data_header(self, conn_id: str, data: bytes) -> bytes:
        """打包数据消息: conn_id_len(2) + conn_id + data"""
        conn_id_bytes = conn_id.encode()
        return struct.pack('!H', len(conn_id_bytes)) + conn_id_bytes + data

    def _unpack_data_header(self, body: bytes):
        """解包数据消息"""
        conn_id_len = struct.unpack('!H', body[:2])[0]
        conn_id = body[2:2 + conn_id_len].decode()
        data = body[2 + conn_id_len:]
        return conn_id, data

    async def _relay_visitor_to_client(self, visitor_reader, client_session, conn_id, tunnel):
        """将访问者数据转发给客户端"""
        try:
            while True:
                data = await visitor_reader.read(65536)
                if not data:
                    break
                tunnel.bytes_in += len(data)
                self._stats['total_bytes_in'] += len(data)
                payload = self._pack_data_header(conn_id, data)
                await client_session.send(MSG_DATA, payload)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as e:
            logger.debug(f'访问者读取异常: {e}')
        finally:
            # 通知客户端关闭连接
            try:
                close_body = json.dumps({'conn_id': conn_id}).encode()
                await client_session.send(MSG_CLOSE_CONN, close_body)
            except Exception:
                pass
            client_session.pending_connections.pop(conn_id, None)

    async def _handle_data(self, session: ClientSession, body: bytes):
        """处理客户端返回的数据，转发给访问者"""
        conn_id, data = self._unpack_data_header(body)

        if data == b'__CONN_READY__':
            # 客户端已准备好接收数据
            return

        conn = session.pending_connections.get(conn_id)
        if not conn:
            return

        visitor_reader, visitor_writer = conn
        try:
            # 更新统计
            for tunnel in session.tunnels.values():
                tunnel.bytes_out += len(data)
            self._stats['total_bytes_out'] += len(data)

            visitor_writer.write(data)
            await visitor_writer.drain()
        except (ConnectionError, OSError):
            session.pending_connections.pop(conn_id, None)
            try:
                visitor_writer.close()
            except Exception:
                pass

    async def _handle_close_conn(self, session: ClientSession, body: bytes):
        """处理关闭连接请求"""
        data = json.loads(body)
        conn_id = data.get('conn_id')
        conn = session.pending_connections.pop(conn_id, None)
        if conn:
            _, visitor_writer = conn
            try:
                visitor_writer.close()
            except Exception:
                pass

    async def _handle_close_tunnel(self, session: ClientSession, body: bytes):
        """处理关闭隧道请求"""
        data = json.loads(body)
        tunnel_id = data.get('tunnel_id')
        await self._remove_tunnel(tunnel_id)

    async def _handle_list_tunnels(self, session: ClientSession):
        """列出当前客户端的所有隧道"""
        tunnels_info = []
        for tid, t in session.tunnels.items():
            tunnels_info.append({
                'tunnel_id': tid,
                'remote_port': t.remote_port,
                'local_addr': t.local_addr,
                'local_port': t.local_port,
                'connections': t.connections,
                'bytes_in': t.bytes_in,
                'bytes_out': t.bytes_out,
            })
        resp = json.dumps({'tunnels': tunnels_info}).encode()
        await session.send(MSG_LIST_TUNNELS_RESP, resp)

    async def _remove_tunnel(self, tunnel_id):
        """移除一条隧道"""
        tunnel = self.tunnels.pop(tunnel_id, None)
        if not tunnel:
            return
        self.port_to_tunnel.pop(tunnel.remote_port, None)
        if tunnel.listener_server:
            tunnel.listener_server.close()
        client = self.clients.get(tunnel.client_name)
        if client:
            client.tunnels.pop(tunnel_id, None)
        logger.info(f'隧道已移除: {tunnel_id} (port:{tunnel.remote_port})')

    async def _remove_client(self, client_name):
        """移除客户端和其所有隧道"""
        session = self.clients.pop(client_name, None)
        if not session:
            return

        # 关闭所有隧道
        for tunnel_id in list(session.tunnels.keys()):
            await self._remove_tunnel(tunnel_id)

        # 关闭所有pending连接
        for conn_id, (_, writer) in session.pending_connections.items():
            try:
                writer.close()
            except Exception:
                pass
        session.pending_connections.clear()

        try:
            session.writer.close()
        except Exception:
            pass
        logger.info(f'客户端 {client_name} 已移除')

    def get_status(self):
        """获取服务器状态（供Web面板使用）"""
        clients_info = {}
        for name, session in self.clients.items():
            tunnels = []
            for tid, t in session.tunnels.items():
                tunnels.append({
                    'tunnel_id': tid,
                    'remote_port': t.remote_port,
                    'local_addr': t.local_addr,
                    'local_port': t.local_port,
                    'connections': t.connections,
                    'bytes_in': t.bytes_in,
                    'bytes_out': t.bytes_out,
                    'created_at': t.created_at,
                })
            clients_info[name] = {
                'connected_at': session.connected_at,
                'last_heartbeat': session.last_heartbeat,
                'tunnel_count': len(session.tunnels),
                'tunnels': tunnels,
            }
        stats = dict(self._stats)
        stats['uptime'] = time.time() - self._stats['start_time']
        stats['active_clients'] = len(self.clients)
        stats['active_tunnels'] = len(self.tunnels)
        return {
            'clients': clients_info,
            'stats': stats,
        }


async def main():
    config = load_config()
    server = NATTunnelServer(config)

    # 启动Web管理面板
    from web_admin import WebAdmin
    web = WebAdmin(server, config)
    asyncio.ensure_future(web.start())

    await server.start()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info('服务器正在关闭...')
    finally:
        loop.close()
