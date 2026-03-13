# 🔗 NAT Tunnel - 内网穿透工具

轻量级内网穿透工具，**专为 Windows 电脑和 Android 手机穿透设计**。服务端部署在宝塔面板服务器，通过服务器公网IP实现NAT端口转发，让外网访问内网设备。

**支持平台：**
- 🖥️ **Windows** — 图形界面客户端（双击运行）+ 命令行模式 + 一键打包exe
- 📱 **Android** — Termux一键安装 + 开机自启
- 🐧 **Linux** — 命令行客户端

## 📌 架构说明

```
                    公网                          内网
┌──────────┐     ┌──────────────────┐     ┌──────────────┐
│  访问者   │────▶│  NAT Tunnel 服务端 │◀────│ NAT Tunnel   │
│(浏览器等) │     │  (公网服务器)       │     │   客户端      │
│          │     │  端口: 10022      │     │  (内网设备)    │
└──────────┘     └──────────────────┘     └──────┬───────┘
                                                  │
                                           ┌──────▼───────┐
                                           │  内网服务      │
                                           │  SSH :22      │
                                           │  Web :8080    │
                                           └──────────────┘
```

**工作原理：**
1. 客户端从内网主动连接到公网服务器，建立控制通道
2. 客户端注册隧道，服务端在指定公网端口开始监听
3. 外部访问者连接服务器的公网端口
4. 服务端将流量通过控制通道转发给客户端
5. 客户端将流量转发到本地/内网服务

## 📁 项目结构

```
├── server/                  # 服务端（部署在公网服务器）
│   ├── server.py            # 服务端主程序
│   ├── web_admin.py         # Web管理面板后端
│   ├── config.yaml          # 服务端配置
│   └── requirements.txt     # Python依赖
├── client/                  # 客户端（Windows / Linux 通用）
│   ├── client.py            # 命令行客户端
│   ├── client_gui.py        # Windows GUI图形界面客户端
│   ├── config.yaml          # 命令行模式配置
│   ├── requirements.txt     # Python依赖
│   ├── 启动客户端.bat        # Windows双击启动（GUI模式）
│   ├── 命令行启动.bat        # Windows命令行模式
│   └── 打包为exe.bat        # 一键打包为exe（无需Python）
├── android/                 # Android (Termux) 客户端
│   ├── setup_android.sh     # 一键安装脚本
│   └── config_android.yaml  # Android默认配置
├── web/                     # Web管理面板前端
│   └── index.html           # 管理界面
└── deploy/                  # 服务端部署相关
    ├── install.sh           # 宝塔一键部署脚本
    ├── nat-tunnel.service   # systemd服务文件
    └── nginx.conf           # Nginx反向代理配置（可选）
```

## 🚀 快速部署

### 一、服务端部署（宝塔面板服务器）

#### 方式1：一键部署脚本

```bash
# 1. 上传项目到服务器
git clone <仓库地址> /tmp/nat-tunnel
# 或手动上传文件

# 2. 运行一键部署脚本
cd /tmp/nat-tunnel
bash deploy/install.sh

# 3. 修改配置文件（重要！）
vim /www/server/nat-tunnel/server/config.yaml

# 4. 启动服务
systemctl start nat-tunnel
```

#### 方式2：手动部署

```bash
# 1. 安装依赖
pip3 install pyyaml aiohttp

# 2. 创建目录
mkdir -p /www/server/nat-tunnel/{server,web,logs}

# 3. 复制文件
cp server/* /www/server/nat-tunnel/server/
cp web/* /www/server/nat-tunnel/web/

# 4. 修改配置
vim /www/server/nat-tunnel/server/config.yaml

# 5. 复制systemd服务文件
cp deploy/nat-tunnel.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable nat-tunnel
systemctl start nat-tunnel
```

#### 宝塔面板防火墙设置

在宝塔面板中放行以下端口：
- **7000** - 控制端口（客户端连接用）
- **7500** - Web管理面板端口
- **10000-60000** - 隧道映射端口范围

操作路径：宝塔面板 → 安全 → 防火墙 → 添加端口规则

### 二、Windows 电脑客户端

#### 方式1：图形界面（推荐）

1. 安装 [Python 3.7+](https://www.python.org/downloads/)（安装时勾选 **Add Python to PATH**）
2. 把 `client/` 文件夹复制到Windows电脑
3. 双击 `启动客户端.bat`
4. 在图形界面中填写：
   - 服务器地址和端口
   - 设备名称和认证密钥
   - 添加隧道规则（公网端口 → 本机端口）
5. 点击「连接」

![GUI示意](https://img.shields.io/badge/界面-图形化配置_一键连接-blue)

#### 方式2：打包为exe（免安装Python）

```
1. 在装有Python的电脑上运行 "打包为exe.bat"
2. 生成 dist/NAT穿透客户端.exe
3. 把exe复制到任意Windows电脑即可直接运行
```

#### 方式3：命令行模式

```bash
# 安装Python后
pip install pyyaml
# 编辑 config.yaml 填写服务器信息
python client.py
```

#### Windows 常见穿透场景

| 场景 | 公网端口 | 内网地址 | 内网端口 |
|------|----------|----------|----------|
| 远程桌面(RDP) | 13389 | 127.0.0.1 | 3389 |
| Web开发服务 | 18080 | 127.0.0.1 | 8080 |
| 本机SSH | 10022 | 127.0.0.1 | 22 |
| 文件共享(SMB) | 10445 | 127.0.0.1 | 445 |
| MC服务器 | 25565 | 127.0.0.1 | 25565 |
| 局域网NAS | 15000 | 192.168.1.100 | 5000 |

### 三、Android 手机客户端

#### 一键安装（Termux）

1. 安装 [Termux](https://f-droid.org/packages/com.termux/) （从F-Droid下载最新版）
2. 把项目上传到手机或直接克隆：

```bash
# 在Termux中执行
pkg install git
git clone <仓库地址> ~/nat-tunnel-src
bash ~/nat-tunnel-src/android/setup_android.sh
```

3. 编辑配置：

```bash
nano ~/nat-tunnel/config.yaml
# 填写: 服务器IP、认证密钥、隧道端口
```

4. 启动：

```bash
# 前台运行
bash ~/nat-tunnel/start.sh

# 后台运行
bash ~/nat-tunnel/start_bg.sh
```

#### Android 开机自启

安装 **Termux:Boot** 应用（从F-Droid下载），安装脚本已自动配置开机自启。

#### Android 常见穿透场景

| 场景 | 公网端口 | 内网端口 | 说明 |
|------|----------|----------|------|
| Termux SSH | 10022 | 8022 | 先运行 `sshd` |
| 手机Web服务 | 18080 | 8080 | Termux中运行的服务 |
| ADB调试 | 15555 | 5555 | 远程ADB调试 |

#### Android SSH远程连接示例

```bash
# 在Termux上启动SSH
pkg install openssh
sshd    # 启动SSH服务（端口8022）

# 客户端配置映射 8022 -> 公网 10022
# 然后从任何地方:
ssh -p 10022 user@你的服务器IP
```

### 四、Linux 客户端

```bash
# 1. 安装依赖
pip3 install pyyaml

# 2. 编辑配置
vim client/config.yaml

# 3. 运行
python3 client/client.py
```

后台运行：
```bash
nohup python3 client.py > /dev/null 2>&1 &

# 或创建systemd服务
sudo tee /etc/systemd/system/nat-tunnel-client.service << EOF
[Unit]
Description=NAT Tunnel Client
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/你的用户名/nat-tunnel
ExecStart=/usr/bin/python3 /home/你的用户名/nat-tunnel/client.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now nat-tunnel-client
```

## ⚙️ 配置说明

### 服务端配置 (server/config.yaml)

```yaml
control_port: 7000          # 控制端口
auth_token: "你的安全密钥"    # 认证token（必须修改！）
web_port: 7500              # Web管理端口
web_password: "你的管理密码"  # Web面板密码（必须修改！）
allowed_ports: [10000, 60000]  # 允许映射的端口范围
max_tunnels_per_client: 10     # 每客户端最大隧道数
heartbeat_interval: 30         # 心跳间隔(秒)
heartbeat_timeout: 90          # 心跳超时(秒)
```

### 客户端配置 (client/config.yaml)

```yaml
server_addr: "你的服务器IP"     # 公网服务器地址
server_port: 7000              # 服务器控制端口
client_name: "my-device"       # 客户端名称
auth_token: "你的安全密钥"      # 与服务端一致的token

tunnels:
  - remote_port: 10022         # 公网映射端口
    local_addr: "127.0.0.1"   # 本地地址
    local_port: 22             # 本地端口(SSH)
    
  - remote_port: 18080
    local_addr: "127.0.0.1"
    local_port: 8080           # 本地端口(Web)
```

## 🌐 Web管理面板

部署完成后，访问 `http://服务器IP:7500` 进入管理面板。

功能：
- 📊 实时查看服务器状态（运行时间、连接数、流量统计）
- 📱 查看在线客户端列表
- 🔗 查看所有活跃隧道及其流量
- ❌ 远程关闭隧道或踢出客户端
- 🔄 每5秒自动刷新数据

## 📖 使用示例

### 示例1：Windows远程桌面（RDP）

**场景：** 从外面远程控制家里的Windows电脑

在家里电脑上运行GUI客户端，添加隧道：
- 公网端口: `13389` → 内网端口: `3389`

从外面连接：
- 打开Windows远程桌面连接 → 输入 `服务器IP:13389`

### 示例2：Android手机SSH远程访问

**场景：** 远程连接Termux命令行

在手机Termux上：
```bash
sshd    # 启动SSH服务
bash ~/nat-tunnel/start.sh  # 启动穿透
# 配置: 公网10022 -> 本机8022
```

从任何地方：
```bash
ssh -p 10022 user@服务器IP
```

### 示例3：Windows开发环境穿透

**场景：** 别人要访问你Windows上跑的开发服务

GUI客户端中添加隧道：
- 公网端口: `18080` → 内网端口: `3000`（你的Node/React等服务）

别人浏览器打开 `http://服务器IP:18080` 即可访问

### 示例4：穿透内网中的其他设备

**场景：** Windows电脑和NAS在同一内网，想从外面访问NAS

在Windows上配置穿透（local_addr填NAS的内网IP）：
- 公网端口: `15000` → 内网地址: `192.168.1.100:5000`

从外面访问 `http://服务器IP:15000` = 访问NAS的5000端口

## 🔒 安全建议

1. **修改默认token和密码** - 部署后第一时间修改 `auth_token` 和 `web_password`
2. **限制端口范围** - 配置 `allowed_ports` 只开放必要的端口范围
3. **使用防火墙** - 仅开放需要的端口，避免全部开放
4. **定期检查** - 通过Web面板监控活跃隧道，及时关闭不需要的
5. **日志审查** - 定期查看日志文件排查异常连接

## 🛠 常用命令

```bash
# 服务管理
systemctl start nat-tunnel      # 启动
systemctl stop nat-tunnel       # 停止
systemctl restart nat-tunnel    # 重启
systemctl status nat-tunnel     # 状态

# 日志查看
tail -f /www/server/nat-tunnel/logs/server.log   # 实时日志
tail -f /www/server/nat-tunnel/logs/error.log     # 错误日志

# 检查端口监听
ss -tlnp | grep python3
```

## 📋 系统要求

- **服务端**: Python 3.7+, 公网IP, 宝塔面板（可选）
- **客户端**: Python 3.7+
- **网络**: 客户端能访问服务端的控制端口(默认7000)

## ❓ 常见问题

**Q: 客户端连不上服务器？**
A: 检查服务端防火墙/宝塔安全设置是否放行了7000端口。

**Q: 隧道建立了但访问不通？**  
A: 检查映射端口是否在允许范围内，防火墙是否放行了该端口。

**Q: 客户端断线后怎么办？**  
A: 客户端内置自动重连机制，断线后会自动尝试重连。

**Q: 支持映射UDP端口吗？**  
A: 当前版本仅支持TCP端口转发。
