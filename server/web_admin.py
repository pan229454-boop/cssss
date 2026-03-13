#!/usr/bin/env python3
"""
NAT Tunnel Web Admin - Web管理面板
提供HTTP API和Web界面用于监控和管理隧道
仅使用Python标准库，兼容Python 3.6+
"""

import asyncio
import json
import hmac
import time
import os
import struct
import mimetypes
from http.cookies import SimpleCookie
from pathlib import Path

# 生成随机token（兼容Python 3.6没有secrets的情况）
try:
    from secrets import token_hex
except ImportError:
    import binascii
    def token_hex(n=32):
        return binascii.hexlify(os.urandom(n)).decode()


class WebAdmin:
    """Web管理面板 - 纯标准库HTTP服务器（asyncio实现）"""

    def __init__(self, tunnel_server, config):
        self.tunnel_server = tunnel_server
        self.config = config
        self.web_port = config.get('web_port', 7500)
        self.password = config.get('web_password', 'admin123')
        self._active_tokens = {}  # token -> expire_time

    def _create_token(self):
        token = token_hex(32)
        self._active_tokens[token] = time.time() + 86400
        now = time.time()
        expired = [k for k, v in self._active_tokens.items() if v <= now]
        for k in expired:
            del self._active_tokens[k]
        return token

    def _check_token(self, headers):
        auth = headers.get('authorization', '')
        if auth.startswith('Bearer '):
            t = auth[7:]
            exp = self._active_tokens.get(t)
            if exp and exp > time.time():
                return True
        cookie_str = headers.get('cookie', '')
        if 'nat_token=' in cookie_str:
            for part in cookie_str.split(';'):
                part = part.strip()
                if part.startswith('nat_token='):
                    t = part[len('nat_token='):]
                    exp = self._active_tokens.get(t)
                    if exp and exp > time.time():
                        return True
        return False

    async def _handle_http(self, reader, writer):
        """处理一个HTTP连接"""
        try:
            # 读取请求行
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not request_line:
                writer.close()
                return
            request_line = request_line.decode('utf-8', errors='replace').strip()
            parts = request_line.split(' ')
            if len(parts) < 2:
                writer.close()
                return

            method = parts[0].upper()
            path = parts[1]

            # 读取headers
            headers = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                line = line.decode('utf-8', errors='replace').strip()
                if not line:
                    break
                if ':' in line:
                    key, val = line.split(':', 1)
                    headers[key.strip().lower()] = val.strip()

            # 读取body
            body = b''
            content_length = int(headers.get('content-length', 0))
            if content_length > 0 and content_length < 1048576:
                body = await asyncio.wait_for(reader.readexactly(content_length), timeout=10)

            # 路由
            if path == '/' and method == 'GET':
                await self._serve_index(writer)
            elif path.startswith('/static/') and method == 'GET':
                filename = path[len('/static/'):]
                await self._serve_static(writer, filename)
            elif path == '/api/login' and method == 'POST':
                await self._api_login(writer, body)
            elif path == '/api/status' and method == 'GET':
                await self._api_status(writer, headers)
            elif path == '/api/tunnel/close' and method == 'POST':
                await self._api_close_tunnel(writer, headers, body)
            elif path == '/api/client/kick' and method == 'POST':
                await self._api_kick_client(writer, headers, body)
            else:
                await self._send_response(writer, 404, {'error': 'Not Found'})

        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as e:
            try:
                await self._send_response(writer, 500, {'error': str(e)})
            except Exception:
                pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _send_response(self, writer, status, data, extra_headers=None):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        status_text = {200: 'OK', 400: 'Bad Request', 401: 'Unauthorized',
                       403: 'Forbidden', 404: 'Not Found', 500: 'Internal Server Error'}
        lines = [
            'HTTP/1.1 {} {}'.format(status, status_text.get(status, 'Unknown')),
            'Content-Type: application/json; charset=utf-8',
            'Content-Length: {}'.format(len(body)),
            'Access-Control-Allow-Origin: *',
            'Connection: close',
        ]
        if extra_headers:
            lines.extend(extra_headers)
        lines.append('')
        lines.append('')
        header_str = '\r\n'.join(lines)
        writer.write(header_str.encode('utf-8') + body)
        await writer.drain()

    async def _send_file(self, writer, status, content, content_type='text/html'):
        if isinstance(content, str):
            content = content.encode('utf-8')
        lines = [
            'HTTP/1.1 {} OK'.format(status),
            'Content-Type: {}; charset=utf-8'.format(content_type),
            'Content-Length: {}'.format(len(content)),
            'Connection: close',
            '',
            '',
        ]
        writer.write('\r\n'.join(lines).encode('utf-8') + content)
        await writer.drain()

    async def _serve_index(self, writer):
        web_dir = Path(__file__).parent.parent / 'web'
        index_path = web_dir / 'index.html'
        if index_path.exists():
            content = index_path.read_bytes()
            await self._send_file(writer, 200, content, 'text/html')
        else:
            await self._send_response(writer, 404, {'error': 'Web面板文件未找到'})

    async def _serve_static(self, writer, filename):
        if '..' in filename or filename.startswith('/'):
            await self._send_response(writer, 403, {'error': 'Forbidden'})
            return
        web_dir = Path(__file__).parent.parent / 'web'
        filepath = web_dir / filename
        if filepath.exists() and filepath.is_file():
            ctype, _ = mimetypes.guess_type(str(filepath))
            if not ctype:
                ctype = 'application/octet-stream'
            content = filepath.read_bytes()
            await self._send_file(writer, 200, content, ctype)
        else:
            await self._send_response(writer, 404, {'error': 'Not Found'})

    async def _api_login(self, writer, body):
        try:
            data = json.loads(body)
        except Exception:
            await self._send_response(writer, 400, {'success': False, 'message': '无效请求'})
            return
        password = data.get('password', '')
        if hmac.compare_digest(password, self.password):
            tok = self._create_token()
            extra = ['Set-Cookie: nat_token={}; Max-Age=86400; HttpOnly; Path=/'.format(tok)]
            await self._send_response(writer, 200,
                {'success': True, 'token': tok, 'message': '登录成功'}, extra)
        else:
            await self._send_response(writer, 401, {'success': False, 'message': '密码错误'})

    async def _api_status(self, writer, headers):
        if not self._check_token(headers):
            await self._send_response(writer, 401, {'error': '未授权'})
            return
        status = self.tunnel_server.get_status()
        await self._send_response(writer, 200, status)

    async def _api_close_tunnel(self, writer, headers, body):
        if not self._check_token(headers):
            await self._send_response(writer, 401, {'error': '未授权'})
            return
        try:
            data = json.loads(body)
        except Exception:
            await self._send_response(writer, 400, {'success': False, 'message': '无效请求'})
            return
        tunnel_id = data.get('tunnel_id')
        if tunnel_id and tunnel_id in self.tunnel_server.tunnels:
            await self.tunnel_server._remove_tunnel(tunnel_id)
            await self._send_response(writer, 200, {'success': True, 'message': '隧道已关闭'})
        else:
            await self._send_response(writer, 404, {'success': False, 'message': '隧道不存在'})

    async def _api_kick_client(self, writer, headers, body):
        if not self._check_token(headers):
            await self._send_response(writer, 401, {'error': '未授权'})
            return
        try:
            data = json.loads(body)
        except Exception:
            await self._send_response(writer, 400, {'success': False, 'message': '无效请求'})
            return
        client_name = data.get('client_name')
        if client_name and client_name in self.tunnel_server.clients:
            await self.tunnel_server._remove_client(client_name)
            await self._send_response(writer, 200, {'success': True, 'message': '客户端已断开'})
        else:
            await self._send_response(writer, 404, {'success': False, 'message': '客户端不存在'})

    async def start(self):
        server = await asyncio.start_server(
            self._handle_http, '0.0.0.0', self.web_port
        )
        from server import logger
        logger.info('Web管理面板启动在端口 {}'.format(self.web_port))
