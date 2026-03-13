#!/usr/bin/env python3
"""
NAT Tunnel Web Admin - Web管理面板
提供HTTP API和Web界面用于监控和管理隧道
"""

import asyncio
import json
import hashlib
import hmac
import time
import secrets
import os
from aiohttp import web
from pathlib import Path

class WebAdmin:
    """Web管理面板"""

    def __init__(self, tunnel_server, config):
        self.tunnel_server = tunnel_server
        self.config = config
        self.web_port = config.get('web_port', 7500)
        self.password = config.get('web_password', 'admin123')
        # 生成随机session key
        self._session_secret = secrets.token_hex(32)
        self._active_tokens = {}  # token -> expire_time

    def _create_token(self):
        """创建登录token"""
        token = secrets.token_hex(32)
        self._active_tokens[token] = time.time() + 86400  # 24小时有效
        # 清理过期token
        now = time.time()
        self._active_tokens = {
            k: v for k, v in self._active_tokens.items() if v > now
        }
        return token

    def _verify_token(self, request):
        """验证请求中的token"""
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            token = auth[7:]
            expire = self._active_tokens.get(token)
            if expire and expire > time.time():
                return True
        # 也检查cookie
        token = request.cookies.get('nat_token', '')
        expire = self._active_tokens.get(token)
        if expire and expire > time.time():
            return True
        return False

    async def handle_login(self, request):
        """登录接口"""
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'success': False, 'message': '无效请求'}, status=400)

        password = data.get('password', '')
        if hmac.compare_digest(password, self.password):
            token = self._create_token()
            resp = web.json_response({
                'success': True,
                'token': token,
                'message': '登录成功'
            })
            resp.set_cookie('nat_token', token, max_age=86400, httponly=True)
            return resp
        return web.json_response({
            'success': False, 'message': '密码错误'
        }, status=401)

    async def handle_status(self, request):
        """获取服务器状态"""
        if not self._verify_token(request):
            return web.json_response({'error': '未授权'}, status=401)
        status = self.tunnel_server.get_status()
        return web.json_response(status)

    async def handle_close_tunnel(self, request):
        """关闭指定隧道"""
        if not self._verify_token(request):
            return web.json_response({'error': '未授权'}, status=401)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'success': False, 'message': '无效请求'}, status=400)

        tunnel_id = data.get('tunnel_id')
        if tunnel_id and tunnel_id in self.tunnel_server.tunnels:
            await self.tunnel_server._remove_tunnel(tunnel_id)
            return web.json_response({'success': True, 'message': '隧道已关闭'})
        return web.json_response({'success': False, 'message': '隧道不存在'}, status=404)

    async def handle_kick_client(self, request):
        """踢出指定客户端"""
        if not self._verify_token(request):
            return web.json_response({'error': '未授权'}, status=401)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'success': False, 'message': '无效请求'}, status=400)

        client_name = data.get('client_name')
        if client_name and client_name in self.tunnel_server.clients:
            await self.tunnel_server._remove_client(client_name)
            return web.json_response({'success': True, 'message': '客户端已断开'})
        return web.json_response({'success': False, 'message': '客户端不存在'}, status=404)

    async def handle_index(self, request):
        """返回Web管理面板页面"""
        web_dir = Path(__file__).parent.parent / 'web'
        index_path = web_dir / 'index.html'
        if index_path.exists():
            return web.FileResponse(index_path)
        return web.Response(text='Web面板文件未找到', status=404)

    async def handle_static(self, request):
        """返回静态文件"""
        filename = request.match_info.get('filename', '')
        # 防止路径穿越
        if '..' in filename or filename.startswith('/'):
            return web.Response(text='Forbidden', status=403)
        web_dir = Path(__file__).parent.parent / 'web'
        filepath = web_dir / filename
        if filepath.exists() and filepath.is_file():
            return web.FileResponse(filepath)
        return web.Response(text='Not Found', status=404)

    async def start(self):
        """启动Web服务器"""
        app = web.Application()
        app.router.add_post('/api/login', self.handle_login)
        app.router.add_get('/api/status', self.handle_status)
        app.router.add_post('/api/tunnel/close', self.handle_close_tunnel)
        app.router.add_post('/api/client/kick', self.handle_kick_client)
        app.router.add_get('/static/{filename}', self.handle_static)
        app.router.add_get('/', self.handle_index)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', self.web_port)
        await site.start()
        from server import logger
        logger.info(f'Web管理面板启动在端口 {self.web_port}')
