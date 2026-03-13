#!/usr/bin/env python3
"""
NAT Tunnel Windows GUI 客户端
带图形界面的内网穿透客户端，适用于Windows电脑
"""

import asyncio
import json
import logging
import struct
import time
import sys
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from pathlib import Path

# ============== 协议部分（与服务端一致）==============
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

HEADER_SIZE = 6
MAX_BODY_SIZE = 1024 * 1024


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


# ============== 客户端核心 ==============
class NATTunnelClient:
    def __init__(self, config, log_callback=None):
        self.server_addr = config['server_addr']
        self.server_port = int(config.get('server_port', 7000))
        self.client_name = config['client_name']
        self.token = config['auth_token']
        self.tunnels_config = config.get('tunnels', [])
        self.heartbeat_interval = int(config.get('heartbeat_interval', 30))
        self.reader = None
        self.writer = None
        self._running = False
        self._local_connections = {}
        self._write_lock = asyncio.Lock()
        self._log = log_callback or (lambda msg: None)

    async def _send(self, msg_type, body):
        async with self._write_lock:
            data = pack_message(msg_type, body)
            self.writer.write(data)
            await self.writer.drain()

    async def connect(self):
        self._log(f'正在连接 {self.server_addr}:{self.server_port}...')
        self.reader, self.writer = await asyncio.open_connection(
            self.server_addr, self.server_port
        )
        auth_data = json.dumps({
            'client_name': self.client_name,
            'token': self.token,
        }).encode()
        await self._send(MSG_AUTH, auth_data)

        msg_type, body = await asyncio.wait_for(read_message(self.reader), timeout=10)
        if msg_type != MSG_AUTH_RESP:
            raise Exception(f'期望认证响应，收到: {msg_type}')
        resp = json.loads(body)
        if not resp.get('success'):
            raise Exception(f'认证失败: {resp.get("message")}')
        self._log('✓ 认证成功！')
        return True

    async def register_tunnels(self):
        results = []
        for t in self.tunnels_config:
            remote_port = int(t['remote_port'])
            local_addr = t.get('local_addr', '127.0.0.1')
            local_port = int(t['local_port'])
            req = json.dumps({
                'remote_port': remote_port,
                'local_addr': local_addr,
                'local_port': local_port,
            }).encode()
            await self._send(MSG_NEW_TUNNEL, req)
            msg_type, body = await asyncio.wait_for(read_message(self.reader), timeout=10)
            if msg_type == MSG_NEW_TUNNEL_RESP:
                resp = json.loads(body)
                if resp.get('success'):
                    self._log(f'✓ 隧道: :{remote_port} → {local_addr}:{local_port}')
                    results.append(True)
                else:
                    self._log(f'✗ 隧道失败 :{remote_port} - {resp.get("message")}')
                    results.append(False)
        return results

    async def run(self):
        self._running = True
        asyncio.create_task(self._heartbeat_loop())
        try:
            while self._running:
                msg_type, body = await read_message(self.reader)
                if msg_type == MSG_HEARTBEAT_RESP:
                    pass
                elif msg_type == MSG_DATA:
                    await self._handle_data(body)
                elif msg_type == MSG_CLOSE_CONN:
                    await self._handle_close_conn(body)
                elif msg_type == MSG_NEW_TUNNEL_RESP:
                    resp = json.loads(body)
                    self._log(f'隧道响应: {resp.get("message")}')
        except (asyncio.IncompleteReadError, ConnectionError):
            self._log('⚠ 与服务器连接断开')
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._log(f'✗ 异常: {e}')
        finally:
            self._running = False

    def stop(self):
        self._running = False
        if self.writer:
            try:
                self.writer.close()
            except Exception:
                pass

    async def _heartbeat_loop(self):
        while self._running:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                await self._send(MSG_HEARTBEAT, b'')
            except Exception:
                self._running = False
                break

    async def _handle_data(self, body):
        conn_id, data = unpack_data_header(body)
        if data[:12] == b'__NEW_CONN__':
            try:
                info = json.loads(data[12:])
                local_addr = info.get('local_addr', '127.0.0.1')
                local_port = info.get('local_port')
            except (json.JSONDecodeError, KeyError):
                local_addr = '127.0.0.1'
                local_port = self.tunnels_config[0]['local_port'] if self.tunnels_config else None
            asyncio.create_task(self._establish_local(conn_id, local_addr, local_port))
            return
        local = self._local_connections.get(conn_id)
        if local:
            _, lw = local
            try:
                lw.write(data)
                await lw.drain()
            except (ConnectionError, OSError):
                self._local_connections.pop(conn_id, None)
                try:
                    lw.close()
                except Exception:
                    pass
                await self._send(MSG_CLOSE_CONN, json.dumps({'conn_id': conn_id}).encode())

    async def _establish_local(self, conn_id, local_addr, local_port):
        if not local_port:
            return
        try:
            lr, lw = await asyncio.open_connection(local_addr, int(local_port))
            self._local_connections[conn_id] = (lr, lw)
            await self._send(MSG_DATA, pack_data_header(conn_id, b'__CONN_READY__'))
            asyncio.create_task(self._relay_local(lr, conn_id))
        except Exception as e:
            self._log(f'连接本地 {local_addr}:{local_port} 失败: {e}')
            await self._send(MSG_CLOSE_CONN, json.dumps({'conn_id': conn_id}).encode())

    async def _relay_local(self, lr, conn_id):
        try:
            while self._running:
                data = await lr.read(65536)
                if not data:
                    break
                await self._send(MSG_DATA, pack_data_header(conn_id, data))
        except Exception:
            pass
        finally:
            self._local_connections.pop(conn_id, None)
            try:
                await self._send(MSG_CLOSE_CONN, json.dumps({'conn_id': conn_id}).encode())
            except Exception:
                pass

    async def _handle_close_conn(self, body):
        data = json.loads(body)
        conn_id = data.get('conn_id')
        local = self._local_connections.pop(conn_id, None)
        if local:
            try:
                local[1].close()
            except Exception:
                pass


# ============== GUI 界面 ==============
class TunnelRow:
    """隧道配置行"""
    def __init__(self, parent, row_idx, on_delete):
        self.frame = ttk.Frame(parent)
        self.frame.grid(row=row_idx, column=0, sticky='ew', pady=2)

        ttk.Label(self.frame, text='公网端口:').grid(row=0, column=0, padx=(0, 4))
        self.remote_port = ttk.Entry(self.frame, width=8)
        self.remote_port.grid(row=0, column=1, padx=(0, 10))

        ttk.Label(self.frame, text='内网地址:').grid(row=0, column=2, padx=(0, 4))
        self.local_addr = ttk.Entry(self.frame, width=15)
        self.local_addr.insert(0, '127.0.0.1')
        self.local_addr.grid(row=0, column=3, padx=(0, 10))

        ttk.Label(self.frame, text='内网端口:').grid(row=0, column=4, padx=(0, 4))
        self.local_port = ttk.Entry(self.frame, width=8)
        self.local_port.grid(row=0, column=5, padx=(0, 10))

        self.del_btn = ttk.Button(self.frame, text='删除', width=4, command=on_delete)
        self.del_btn.grid(row=0, column=6)

    def get_config(self):
        rp = self.remote_port.get().strip()
        la = self.local_addr.get().strip()
        lp = self.local_port.get().strip()
        if not rp or not lp:
            return None
        return {
            'remote_port': int(rp),
            'local_addr': la or '127.0.0.1',
            'local_port': int(lp),
        }

    def destroy(self):
        self.frame.destroy()


CONFIG_FILE = Path(__file__).parent / 'config.json'


class NATTunnelGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('NAT Tunnel - 内网穿透客户端')
        self.root.geometry('680x620')
        self.root.resizable(True, True)

        self._client = None
        self._loop = None
        self._thread = None
        self._connected = False
        self._tunnel_rows = []

        self._build_ui()
        self._load_config()

    def _build_ui(self):
        style = ttk.Style()
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 14, 'bold'))
        style.configure('Status.TLabel', font=('Microsoft YaHei UI', 10))

        # 标题
        title_frame = ttk.Frame(self.root, padding=10)
        title_frame.pack(fill='x')
        ttk.Label(title_frame, text='🔗 NAT Tunnel 内网穿透', style='Title.TLabel').pack(side='left')
        self.status_label = ttk.Label(title_frame, text='● 未连接', style='Status.TLabel', foreground='gray')
        self.status_label.pack(side='right')

        # 服务器配置
        server_frame = ttk.LabelFrame(self.root, text='服务器配置', padding=10)
        server_frame.pack(fill='x', padx=10, pady=(0, 5))

        row1 = ttk.Frame(server_frame)
        row1.pack(fill='x', pady=2)
        ttk.Label(row1, text='服务器地址:').pack(side='left')
        self.server_addr = ttk.Entry(row1, width=25)
        self.server_addr.pack(side='left', padx=(4, 15))
        ttk.Label(row1, text='端口:').pack(side='left')
        self.server_port = ttk.Entry(row1, width=8)
        self.server_port.insert(0, '7000')
        self.server_port.pack(side='left', padx=4)

        row2 = ttk.Frame(server_frame)
        row2.pack(fill='x', pady=2)
        ttk.Label(row2, text='设备名称:  ').pack(side='left')
        self.client_name = ttk.Entry(row2, width=20)
        self.client_name.insert(0, 'my-windows-pc')
        self.client_name.pack(side='left', padx=(4, 15))
        ttk.Label(row2, text='认证密钥:').pack(side='left')
        self.auth_token = ttk.Entry(row2, width=25, show='*')
        self.auth_token.pack(side='left', padx=4)

        # 隧道配置
        tunnel_frame = ttk.LabelFrame(self.root, text='隧道配置（将本机/内网端口映射到公网）', padding=10)
        tunnel_frame.pack(fill='x', padx=10, pady=5)

        self.tunnels_container = ttk.Frame(tunnel_frame)
        self.tunnels_container.pack(fill='x')

        add_btn_frame = ttk.Frame(tunnel_frame)
        add_btn_frame.pack(fill='x', pady=(5, 0))
        ttk.Button(add_btn_frame, text='+ 添加隧道', command=self._add_tunnel_row).pack(side='left')

        # 按钮区
        btn_frame = ttk.Frame(self.root, padding=10)
        btn_frame.pack(fill='x')

        self.connect_btn = ttk.Button(btn_frame, text='连接', command=self._toggle_connection)
        self.connect_btn.pack(side='left', padx=(0, 10))

        ttk.Button(btn_frame, text='保存配置', command=self._save_config).pack(side='left')

        # 日志区
        log_frame = ttk.LabelFrame(self.root, text='运行日志', padding=5)
        log_frame.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=10, state='disabled',
                                                   font=('Consolas', 9), wrap='word')
        self.log_text.pack(fill='both', expand=True)

    def _add_tunnel_row(self, remote_port='', local_addr='127.0.0.1', local_port=''):
        idx = len(self._tunnel_rows)
        row = TunnelRow(self.tunnels_container, idx, lambda r=None: self._del_tunnel_row(r))
        # 用闭包绑定正确的row
        row.del_btn.config(command=lambda r=row: self._del_tunnel_row(r))
        if remote_port:
            row.remote_port.insert(0, str(remote_port))
        if local_addr and local_addr != '127.0.0.1':
            row.local_addr.delete(0, 'end')
            row.local_addr.insert(0, local_addr)
        if local_port:
            row.local_port.insert(0, str(local_port))
        self._tunnel_rows.append(row)

    def _del_tunnel_row(self, row):
        if row in self._tunnel_rows:
            self._tunnel_rows.remove(row)
            row.destroy()

    def _get_config(self):
        tunnels = []
        for row in self._tunnel_rows:
            cfg = row.get_config()
            if cfg:
                tunnels.append(cfg)
        return {
            'server_addr': self.server_addr.get().strip(),
            'server_port': int(self.server_port.get().strip() or 7000),
            'client_name': self.client_name.get().strip() or 'my-windows-pc',
            'auth_token': self.auth_token.get().strip(),
            'heartbeat_interval': 30,
            'tunnels': tunnels,
        }

    def _save_config(self):
        config = self._get_config()
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            self._log('配置已保存')
        except Exception as e:
            self._log(f'保存配置失败: {e}')

    def _load_config(self):
        if not CONFIG_FILE.exists():
            self._add_tunnel_row()
            return
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.server_addr.insert(0, config.get('server_addr', ''))
            self.server_port.delete(0, 'end')
            self.server_port.insert(0, str(config.get('server_port', 7000)))
            self.client_name.delete(0, 'end')
            self.client_name.insert(0, config.get('client_name', 'my-windows-pc'))
            self.auth_token.insert(0, config.get('auth_token', ''))
            for t in config.get('tunnels', []):
                self._add_tunnel_row(
                    t.get('remote_port', ''),
                    t.get('local_addr', '127.0.0.1'),
                    t.get('local_port', ''),
                )
            if not self._tunnel_rows:
                self._add_tunnel_row()
            self._log('配置已加载')
        except Exception as e:
            self._log(f'加载配置失败: {e}')
            self._add_tunnel_row()

    def _log(self, msg):
        timestamp = time.strftime('%H:%M:%S')
        def _update():
            self.log_text.config(state='normal')
            self.log_text.insert('end', f'[{timestamp}] {msg}\n')
            self.log_text.see('end')
            self.log_text.config(state='disabled')
        self.root.after(0, _update)

    def _set_status(self, connected):
        self._connected = connected
        def _update():
            if connected:
                self.status_label.config(text='● 已连接', foreground='green')
                self.connect_btn.config(text='断开')
            else:
                self.status_label.config(text='● 未连接', foreground='gray')
                self.connect_btn.config(text='连接')
        self.root.after(0, _update)

    def _toggle_connection(self):
        if self._connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        config = self._get_config()
        if not config['server_addr']:
            messagebox.showwarning('提示', '请填写服务器地址')
            return
        if not config['auth_token']:
            messagebox.showwarning('提示', '请填写认证密钥')
            return
        if not config['tunnels']:
            messagebox.showwarning('提示', '请至少添加一条隧道')
            return

        self._save_config()
        self._log('正在启动连接...')

        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._async_connect(config))

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    async def _async_connect(self, config):
        retry_interval = 5
        while True:
            self._client = NATTunnelClient(config, self._log)
            try:
                await self._client.connect()
                self._set_status(True)
                await self._client.register_tunnels()
                await self._client.run()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log(f'连接失败: {e}')

            self._set_status(False)
            if not self._client._running and self._loop and self._loop.is_running():
                # 检查是否是主动断开
                if not hasattr(self, '_stop_requested'):
                    self._log(f'{retry_interval}秒后重连...')
                    await asyncio.sleep(retry_interval)
                else:
                    break
            else:
                break

    def _disconnect(self):
        self._stop_requested = True
        self._log('正在断开连接...')
        if self._client:
            self._client.stop()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._set_status(False)
        self._log('已断开')
        if hasattr(self, '_stop_requested'):
            del self._stop_requested

    def run(self):
        self.root.mainloop()


def main():
    # Windows下需要设置事件循环策略
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    app = NATTunnelGUI()
    app.run()


if __name__ == '__main__':
    main()
