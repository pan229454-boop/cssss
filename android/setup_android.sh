#!/data/data/com.termux/files/usr/bin/bash
# NAT Tunnel Android 安装脚本 (Termux)
# 使用方法: 在Termux中运行 bash setup_android.sh

echo "============================================"
echo "  NAT Tunnel 内网穿透 - Android 安装"
echo "============================================"
echo ""

# 更新并安装Python
echo "[1/4] 安装Python..."
pkg update -y
pkg install -y python

# 安装pip依赖
echo ""
echo "[2/4] 安装依赖..."
pip install pyyaml

# 创建工作目录
echo ""
echo "[3/4] 设置文件..."
INSTALL_DIR="$HOME/nat-tunnel"
mkdir -p "$INSTALL_DIR"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 复制客户端文件
cp "$SCRIPT_DIR/../client/client.py" "$INSTALL_DIR/"

# 如果没有配置文件，创建默认配置
if [ ! -f "$INSTALL_DIR/config.yaml" ]; then
    cp "$SCRIPT_DIR/config_android.yaml" "$INSTALL_DIR/config.yaml" 2>/dev/null || \
    cat > "$INSTALL_DIR/config.yaml" << 'YAML'
# NAT Tunnel Android 客户端配置
# 请修改以下配置后运行

# 服务器地址
server_addr: "你的服务器IP"
server_port: 7000

# 客户端名称
client_name: "my-android"

# 认证密钥（与服务端一致）
auth_token: "change_me_to_a_secure_token"

# 心跳
heartbeat_interval: 30
retry_interval: 5
max_retries: 0

# 隧道列表
tunnels:
  # 示例: 将手机的Termux SSH端口映射到公网
  - remote_port: 10022
    local_addr: "127.0.0.1"
    local_port: 8022

  # 示例: 将手机上的Web服务映射到公网
  # - remote_port: 18080
  #   local_addr: "127.0.0.1"
  #   local_port: 8080
YAML
    echo "  => 已创建默认配置: $INSTALL_DIR/config.yaml"
fi

# 创建快捷启动脚本
cat > "$INSTALL_DIR/start.sh" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")"
echo "NAT Tunnel 客户端启动中..."
python client.py
EOF
chmod +x "$INSTALL_DIR/start.sh"

# 创建后台运行脚本
cat > "$INSTALL_DIR/start_bg.sh" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")"
nohup python client.py > nat-tunnel.log 2>&1 &
echo "NAT Tunnel 已在后台启动 (PID: $!)"
echo "查看日志: tail -f ~/nat-tunnel/nat-tunnel.log"
echo "停止运行: kill $!"
EOF
chmod +x "$INSTALL_DIR/start_bg.sh"

# 创建Termux:Boot 自启动脚本（可选）
BOOT_DIR="$HOME/.termux/boot"
mkdir -p "$BOOT_DIR"
cat > "$BOOT_DIR/nat-tunnel" << EOF
#!/data/data/com.termux/files/usr/bin/bash
# 开机自启动 NAT Tunnel
# 需要安装 Termux:Boot 应用
termux-wake-lock
cd "$INSTALL_DIR"
python client.py >> nat-tunnel.log 2>&1
EOF
chmod +x "$BOOT_DIR/nat-tunnel"

echo ""
echo "[4/4] 安装完成！"
echo ""
echo "============================================"
echo "  安装完成！"
echo "============================================"
echo ""
echo "文件位置: $INSTALL_DIR"
echo ""
echo "使用步骤:"
echo "  1. 编辑配置:  nano $INSTALL_DIR/config.yaml"
echo "  2. 启动客户端: bash $INSTALL_DIR/start.sh"
echo "  3. 后台运行:   bash $INSTALL_DIR/start_bg.sh"
echo ""
echo "Termux常用端口:"
echo "  SSH (sshd):     8022"
echo "  HTTP Server:    8080"
echo "  FTP Server:     8021"
echo ""
echo "提示: 安装 Termux:Boot 可实现开机自启动"
echo "============================================"
