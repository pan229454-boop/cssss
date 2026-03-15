# 🔗 NAT Tunnel - 内网穿透工具

轻量级内网穿透工具，**无需 frp/ngrok**，纯 Python 实现，将公网服务器的端口映射到内网设备。

**支持平台：** 🖥️ Windows（图形界面）· 📱 Android（Termux）· 🐧 Linux

```
外部访问者  ────▶  公网服务器:10022  ────▶  内网设备:22 (SSH)
外部访问者  ────▶  公网服务器:18080  ────▶  内网设备:8080 (Web)
```

---

## 🚀 服务端部署（3步完成）

> **前提:** 一台有公网IP的Linux服务器（宝塔面板、纯命令行均可）

### 第一步：上传项目

选择以下任意一种方式把项目放到服务器上：

**方式A — 宝塔面板文件管理器（最简单）**
1. 打开宝塔面板 → 文件 → 进入 `/www/wwwroot/`
2. 新建文件夹 `nat-tunnel`
3. 点击上传 → 将项目文件夹内所有文件上传至 `/www/wwwroot/nat-tunnel/`

**方式B — SSH 命令行**
```bash
# 方式B1：git 克隆（推荐）
git clone https://github.com/pan229454-boop/cssss /www/wwwroot/nat-tunnel

# 方式B2：本地上传（scp）
scp -r ./nat-tunnel root@你的服务器IP:/www/wwwroot/
```

上传后目录结构如下：
```
/www/wwwroot/nat-tunnel/
├── server/          ← 服务端程序和配置
├── client/          ← 客户端程序
├── web/             ← 管理面板页面
├── deploy/          ← 部署脚本
└── android/         ← 安卓客户端
```

### 第二步：运行一键部署脚本

```bash
sudo bash /www/wwwroot/nat-tunnel/deploy/install.sh
```

脚本会自动完成：检查Python → 安装依赖 → 生成随机密钥 → 注册系统服务 → 配置防火墙 → 启动服务

### 第三步：开放端口（宝塔面板）

宝塔面板 → **安全** → **防火墙** → 添加规则：

| 端口 | 协议 | 用途 |
|------|------|------|
| 7000 | TCP | 客户端连接（控制端口）|
| 7500 | TCP | Web 管理面板 |
| 10000-60000 | TCP | 隧道映射端口 |

> 使用纯命令行服务器（无宝塔）时脚本会自动配置防火墙，无需手动操作。

**完成！** 访问 `http://服务器IP:7500` 打开管理面板，密钥和密码在脚本输出中已显示。

---

## 💻 客户端使用

### Windows 电脑（推荐图形界面）

1. 把 `client/` 文件夹复制到 Windows 电脑
2. 安装 [Python 3.7+](https://www.python.org/downloads/)（勾选 **Add Python to PATH**）
3. 双击 **`启动客户端.bat`**
4. 在界面中填写：服务器IP、Token（从管理面板→设置→查看）、设备名称
5. 添加隧道规则（公网端口 → 本机端口），点击连接

**不想装Python？** 双击 `打包为exe.bat` 生成单文件 exe，可复制到任何 Windows 运行。

**常见隧道配置：**

| 用途 | 公网端口 | 本机端口 |
|------|----------|----------|
| 远程桌面 RDP | 13389 | 3389 |
| SSH | 10022 | 22 |
| Web 开发服务 | 18080 | 8080 |
| MC 游戏服务器 | 25565 | 25565 |
| 局域网 NAS | 15000 | （填NAS的内网IP:端口）|

### Android 手机（Termux）

```bash
# 1. 安装 Termux（从 F-Droid 下载：https://f-droid.org/packages/com.termux/）

# 2. 在 Termux 中执行（一键安装）
pkg install git -y
git clone https://github.com/pan229454-boop/cssss ~/nat-tunnel-src
bash ~/nat-tunnel-src/android/setup_android.sh

# 3. 编辑配置
nano ~/nat-tunnel/config.yaml
# 填写 server_addr（服务器IP）、auth_token（与服务端一致）

# 4. 启动
bash ~/nat-tunnel/start.sh
```

安装 **Termux:Boot**（F-Droid）后可实现开机自启。

### Linux 服务器/设备

```bash
pip3 install pyyaml
# 编辑配置
vim client/config.yaml
# 运行
python3 client/client.py
```

后台运行：
```bash
nohup python3 client/client.py > /tmp/nat-client.log 2>&1 &
```

---

## ⚙️ 配置说明

部署后可在 **Web 管理面板 → ⚙️ 设置** 中直接修改所有配置，无需编辑文件。

也可以手动编辑 `/www/wwwroot/nat-tunnel/server/config.yaml`：

```yaml
control_port: 7000             # 客户端连接端口
auth_token: "你的密钥"          # 与客户端一致（必须修改！）
web_port: 7500                 # 管理面板端口
web_password: "你的密码"        # 管理面板登录密码（必须修改！）
allowed_ports: [10000, 60000]  # 允许映射的端口范围
max_tunnels_per_client: 10     # 每客户端最大隧道数
```

客户端配置 `client/config.yaml`：

```yaml
server_addr: "服务器IP或域名"
server_port: 7000
client_name: "我的电脑"
auth_token: "与服务端一致的密钥"

tunnels:
  - remote_port: 10022        # 公网映射端口
    local_addr: "127.0.0.1"
    local_port: 22            # 本机端口
  - remote_port: 18080
    local_addr: "127.0.0.1"
    local_port: 8080
```

---

## 🌐 Web 管理面板

访问 `http://服务器IP:7500`，功能：

- 📊 **实时状态** — 运行时间、在线客户端数、活跃隧道数、流量统计
- 🔗 **隧道管理** — 查看所有隧道，一键关闭
- ❌ **客户端管理** — 踢出指定客户端
- ⚙️ **系统设置** — 在线修改管理密码、认证Token、服务器地址（无需重启）
- 📱 **VPN配置** — 查看和管理 IKEv2/IPSec 手机VPN连接参数

---

## 📱 手机VPN穿透（可选）

除了客户端程序外，还支持手机**系统内置VPN**直接接入（无需安装任何App）：

```bash
# 在服务器上一键安装 StrongSwan VPN
sudo bash /www/wwwroot/nat-tunnel/deploy/setup_vpn.sh
```

支持三种类型（均可在管理面板中查看参数）：

| 类型 | 适用场景 | iOS | Android | Windows |
|------|---------|-----|---------|---------|
| IKEv2/MSCHAPv2 | 日常使用，推荐 | ✅ | ✅ | ✅ |
| IPSec PSK | 最广泛兼容 | ✅ | ✅ | ✅ |
| IPSec RSA 证书 | 高安全性 | ✅ | ✅ | — |

---

## 🛠 常用管理命令

```bash
systemctl status nat-tunnel           # 查看服务状态
systemctl restart nat-tunnel          # 重启服务
systemctl stop nat-tunnel             # 停止服务
tail -f /www/wwwroot/nat-tunnel/logs/server.log   # 实时日志
```

---

## ❓ 常见问题

**Q: 客户端连不上服务器？**
检查宝塔防火墙是否放行了 **7000** 端口，以及云服务器安全组（阿里云/腾讯云等）是否也放行。

**Q: 隧道建立了但访问不通？**
检查使用的公网端口是否在 `allowed_ports` 范围内（默认 10000-60000），以及防火墙是否放行了该端口。

**Q: 如何更新程序？**
```bash
cd /www/wwwroot/nat-tunnel
git pull                         # 拉取最新代码
systemctl restart nat-tunnel     # 重启服务
```

**Q: 忘记管理面板密码？**
查看或修改配置文件中的 `web_password`：
```bash
cat /www/wwwroot/nat-tunnel/server/config.yaml | grep web_password
```

**Q: 支持UDP吗？**
当前版本仅支持 TCP 端口转发。

---

## 📁 项目结构

```
nat-tunnel/
├── server/
│   ├── server.py        # 服务端主程序
│   ├── web_admin.py     # Web管理面板后端
│   └── config.yaml      # 服务端配置
├── client/
│   ├── client.py        # 命令行客户端
│   ├── client_gui.py    # Windows 图形界面
│   ├── config.yaml      # 客户端配置
│   ├── 启动客户端.bat    # Windows 一键启动
│   └── 打包为exe.bat    # 打包为单文件exe
├── android/
│   └── setup_android.sh # Termux 一键安装
├── web/
│   └── index.html       # 管理面板页面
└── deploy/
    ├── install.sh        # 服务端一键部署脚本
    ├── setup_vpn.sh      # VPN 一键部署脚本
    └── nginx.conf        # Nginx 反向代理配置（可选）
```
