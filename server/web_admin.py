#!/usr/bin/env python3
"""
NAT Tunnel Web Admin - Web管理面板
提供HTTP API和Web界面用于监控和管理隧道
仅使用Python标准库，兼容Python 3.6+
"""

import asyncio
import json
import hmac
import re
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
        self.config_path = Path(__file__).parent / 'config.yaml'

    def _create_token(self):
        token = token_hex(32)
        self._active_tokens[token] = time.time() + 86400
        now = time.time()
        expired = [k for k, v in self._active_tokens.items() if v <= now]
        for k in expired:
            del self._active_tokens[k]
        return token

    def _update_config_field(self, field, value):
        """更新配置文件中的指定字段，保留注释"""
        try:
            content = self.config_path.read_text(encoding='utf-8')
            if isinstance(value, str):
                escaped = value.replace('\\', '\\\\').replace('"', '\\"')
                new_line = '{}: "{}"'.format(field, escaped)
            else:
                new_line = '{}: {}'.format(field, json.dumps(value))
            pattern = r'^{}:.*$'.format(re.escape(field))
            new_content, n = re.subn(pattern, new_line, content, flags=re.MULTILINE)
            if n == 0:
                new_content = content.rstrip('\n') + '\n{}\n'.format(new_line)
            self.config_path.write_text(new_content, encoding='utf-8')
            self.config[field] = value
            return True
        except Exception:
            return False

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
            elif path == '/api/settings' and method == 'GET':
                await self._api_get_settings(writer, headers)
            elif path == '/api/settings/password' and method == 'POST':
                await self._api_change_password(writer, headers, body)
            elif path == '/api/settings/token' and method == 'POST':
                await self._api_change_token(writer, headers, body)
            elif path == '/api/settings/address' and method == 'POST':
                await self._api_change_address(writer, headers, body)
            elif path == '/api/vpn' and method == 'GET':
                await self._api_get_vpn(writer, headers)
            elif path == '/api/vpn/ikev2' and method == 'POST':
                await self._api_set_vpn_ikev2(writer, headers, body)
            elif path == '/api/vpn/psk' and method == 'POST':
                await self._api_set_vpn_psk(writer, headers, body)
            elif path == '/api/vpn/rsa' and method == 'POST':
                await self._api_set_vpn_rsa(writer, headers, body)
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

    async def _api_get_settings(self, writer, headers):
        if not self._check_token(headers):
            await self._send_response(writer, 401, {'error': '未授权'})
            return
        settings = {
            'auth_token': self.config.get('auth_token', ''),
            'server_host': self.config.get('server_host', ''),
            'control_port': self.config.get('control_port', 7000),
        }
        await self._send_response(writer, 200, settings)

    async def _api_change_password(self, writer, headers, body):
        if not self._check_token(headers):
            await self._send_response(writer, 401, {'error': '未授权'})
            return
        try:
            data = json.loads(body)
        except Exception:
            await self._send_response(writer, 400, {'success': False, 'message': '无效请求'})
            return
        old_pwd = data.get('old_password', '')
        new_pwd = data.get('new_password', '')
        if not hmac.compare_digest(old_pwd, self.password):
            await self._send_response(writer, 403, {'success': False, 'message': '当前密码错误'})
            return
        if not new_pwd or len(new_pwd) < 6:
            await self._send_response(writer, 400, {'success': False, 'message': '新密码至少需要6个字符'})
            return
        if self._update_config_field('web_password', new_pwd):
            self.password = new_pwd
            # 使所有已登录的token失效，要求重新登录
            self._active_tokens.clear()
            await self._send_response(writer, 200, {'success': True, 'message': '密码已修改，请重新登录'})
        else:
            await self._send_response(writer, 500, {'success': False, 'message': '保存配置失败'})

    async def _api_change_token(self, writer, headers, body):
        if not self._check_token(headers):
            await self._send_response(writer, 401, {'error': '未授权'})
            return
        try:
            data = json.loads(body)
        except Exception:
            await self._send_response(writer, 400, {'success': False, 'message': '无效请求'})
            return
        new_token = data.get('new_token', '').strip()
        if not new_token or len(new_token) < 8:
            await self._send_response(writer, 400, {'success': False, 'message': '新Token至少需要8个字符'})
            return
        if self._update_config_field('auth_token', new_token):
            self.tunnel_server.config['auth_token'] = new_token
            await self._send_response(writer, 200, {'success': True, 'message': 'Token已更新，新客户端连接将使用新Token'})
        else:
            await self._send_response(writer, 500, {'success': False, 'message': '保存配置失败'})

    async def _api_change_address(self, writer, headers, body):
        if not self._check_token(headers):
            await self._send_response(writer, 401, {'error': '未授权'})
            return
        try:
            data = json.loads(body)
        except Exception:
            await self._send_response(writer, 400, {'success': False, 'message': '无效请求'})
            return
        server_host = data.get('server_host', '').strip()
        if not server_host:
            await self._send_response(writer, 400, {'success': False, 'message': '服务器地址不能为空'})
            return
        if self._update_config_field('server_host', server_host):
            await self._send_response(writer, 200, {'success': True, 'message': '服务器地址已更新'})
        else:
            await self._send_response(writer, 500, {'success': False, 'message': '保存配置失败'})

    async def _api_get_vpn(self, writer, headers):
        if not self._check_token(headers):
            await self._send_response(writer, 401, {'error': '未授权'})
            return
        vpn = {
            'server_host': self.config.get('server_host', ''),
            'ikev2_identifier': self.config.get('server_host', ''),  # 手机端"IPSec标识符"填此值
            'ikev2_username': self.config.get('vpn_ikev2_username', 'vpnuser'),
            'ikev2_password': self.config.get('vpn_ikev2_password', ''),
            'psk_secret': self.config.get('vpn_psk_secret', ''),
            'psk_username': self.config.get('vpn_psk_username', 'vpnuser'),
            'psk_password': self.config.get('vpn_psk_password', ''),
            'rsa_ca_cert': self.config.get('vpn_rsa_ca_cert', '/etc/ipsec.d/cacerts/ca.crt'),
            'rsa_client_p12': self.config.get('vpn_rsa_client_p12', '/etc/ipsec.d/private/client.p12'),
        }
        await self._send_response(writer, 200, vpn)

    async def _api_set_vpn_ikev2(self, writer, headers, body):
        if not self._check_token(headers):
            await self._send_response(writer, 401, {'error': '未授权'})
            return
        try:
            data = json.loads(body)
        except Exception:
            await self._send_response(writer, 400, {'success': False, 'message': '无效请求'})
            return
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        if not username or not password:
            await self._send_response(writer, 400, {'success': False, 'message': '用户名和密码不能为空'})
            return
        ok1 = self._update_config_field('vpn_ikev2_username', username)
        ok2 = self._update_config_field('vpn_ikev2_password', password)
        if ok1 and ok2:
            self._sync_vpn_strongswan('ikev2', username, password)
            await self._send_response(writer, 200, {'success': True, 'message': 'IKEv2/MSCHAPv2 配置已保存'})
        else:
            await self._send_response(writer, 500, {'success': False, 'message': '保存配置失败'})

    async def _api_set_vpn_psk(self, writer, headers, body):
        if not self._check_token(headers):
            await self._send_response(writer, 401, {'error': '未授权'})
            return
        try:
            data = json.loads(body)
        except Exception:
            await self._send_response(writer, 400, {'success': False, 'message': '无效请求'})
            return
        psk = data.get('psk_secret', '').strip()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        if not psk or not username or not password:
            await self._send_response(writer, 400, {'success': False, 'message': 'PSK、用户名和密码均不能为空'})
            return
        ok = (self._update_config_field('vpn_psk_secret', psk) and
              self._update_config_field('vpn_psk_username', username) and
              self._update_config_field('vpn_psk_password', password))
        if ok:
            self._sync_vpn_strongswan('psk', username, password, psk)
            await self._send_response(writer, 200, {'success': True, 'message': 'IPSec PSK 配置已保存'})
        else:
            await self._send_response(writer, 500, {'success': False, 'message': '保存配置失败'})

    async def _api_set_vpn_rsa(self, writer, headers, body):
        if not self._check_token(headers):
            await self._send_response(writer, 401, {'error': '未授权'})
            return
        try:
            data = json.loads(body)
        except Exception:
            await self._send_response(writer, 400, {'success': False, 'message': '无效请求'})
            return
        ca_cert = data.get('ca_cert', '').strip()
        client_p12 = data.get('client_p12', '').strip()
        if not ca_cert:
            await self._send_response(writer, 400, {'success': False, 'message': 'CA证书路径不能为空'})
            return
        ok = (self._update_config_field('vpn_rsa_ca_cert', ca_cert) and
              self._update_config_field('vpn_rsa_client_p12', client_p12))
        if ok:
            await self._send_response(writer, 200, {'success': True, 'message': 'IPSec RSA 证书路径已保存'})
        else:
            await self._send_response(writer, 500, {'success': False, 'message': '保存配置失败'})

    def _sync_vpn_strongswan(self, vpn_type, username, password, psk=None):
        """若 StrongSwan 已安装，同步更新 ipsec.secrets"""
        secrets_path = Path('/etc/ipsec.secrets')
        if not secrets_path.exists():
            return  # StrongSwan 未安装，跳过
        try:
            content = secrets_path.read_text(encoding='utf-8')
            if vpn_type == 'ikev2':
                pattern = r'^{}\s*:\s*EAP\s+".+"'.format(re.escape(username))
                new_line = '{} : EAP "{}"'.format(username, password)
                new_content, n = re.subn(pattern, new_line, content, flags=re.MULTILINE)
                if n == 0:
                    new_content = content.rstrip() + '\n{} : EAP "{}"\n'.format(username, password)
            elif vpn_type == 'psk':
                # Update PSK line and XAUTH user
                psk_pattern = r'^%any\s+%any\s*:\s*PSK\s+".+"'
                new_psk_line = '%any %any : PSK "{}"'.format(psk)
                new_content, n = re.subn(psk_pattern, new_psk_line, content, flags=re.MULTILINE)
                if n == 0:
                    new_content = content.rstrip() + '\n%any %any : PSK "{}"\n'.format(psk)
                else:
                    new_content = new_content
                xauth_pattern = r'^{}\s*:\s*XAUTH\s+".+"'.format(re.escape(username))
                xauth_line = '{} : XAUTH "{}"'.format(username, password)
                new_content2, n2 = re.subn(xauth_pattern, xauth_line, new_content, flags=re.MULTILINE)
                if n2 == 0:
                    new_content = new_content.rstrip() + '\n{} : XAUTH "{}"\n'.format(username, password)
                else:
                    new_content = new_content2
            else:
                return
            secrets_path.write_text(new_content, encoding='utf-8')
            os.system('ipsec reload secrets 2>/dev/null || true')
        except Exception:
            pass

    async def start(self):
        server = await asyncio.start_server(
            self._handle_http, '0.0.0.0', self.web_port
        )
        from server import logger
        logger.info('Web管理面板启动在端口 {}'.format(self.web_port))
