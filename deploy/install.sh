#!/bin/bash
# NAT Tunnel 宝塔面板一键部署脚本
# 使用方式: bash install.sh

set -e

echo "============================================"
echo "  NAT Tunnel 内网穿透 - 宝塔面板部署脚本"
echo "============================================"

# 检查是否为root
if [ "$EUID" -ne 0 ]; then
    echo "请使用 root 权限运行此脚本"
    exit 1
fi

# 安装目录
INSTALL_DIR="/www/server/nat-tunnel"
SERVICE_NAME="nat-tunnel"

echo ""
echo "[1/5] 安装Python依赖..."
pip3 install --quiet pyyaml 2>/dev/null || pip install --quiet pyyaml 2>/dev/null || {
    echo "  => pyyaml安装失败，请手动执行: pip3 install pyyaml"
}

echo ""
echo "[2/5] 创建安装目录..."
mkdir -p "$INSTALL_DIR/server"
mkdir -p "$INSTALL_DIR/web"
mkdir -p "$INSTALL_DIR/logs"

echo ""
echo "[3/5] 复制文件..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cp "$PROJECT_DIR/server/server.py" "$INSTALL_DIR/server/"
cp "$PROJECT_DIR/server/web_admin.py" "$INSTALL_DIR/server/"
cp "$PROJECT_DIR/web/index.html" "$INSTALL_DIR/web/"

# 如果没有配置文件，复制默认配置
if [ ! -f "$INSTALL_DIR/server/config.yaml" ]; then
    cp "$PROJECT_DIR/server/config.yaml" "$INSTALL_DIR/server/"
    echo "  => 已创建默认配置文件，请修改: $INSTALL_DIR/server/config.yaml"
else
    echo "  => 配置文件已存在，跳过覆盖"
fi

echo ""
echo "[4/5] 创建systemd服务..."
cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=NAT Tunnel Server - 内网穿透服务
After=network.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}/server
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/server/server.py
Restart=always
RestartSec=5
StandardOutput=append:${INSTALL_DIR}/logs/server.log
StandardError=append:${INSTALL_DIR}/logs/error.log
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ${SERVICE_NAME}

echo ""
echo "[5/5] 配置防火墙..."
# 开放控制端口和Web端口
if command -v firewall-cmd &> /dev/null; then
    firewall-cmd --permanent --add-port=7000/tcp 2>/dev/null || true
    firewall-cmd --permanent --add-port=7500/tcp 2>/dev/null || true
    firewall-cmd --permanent --add-port=10000-60000/tcp 2>/dev/null || true
    firewall-cmd --reload 2>/dev/null || true
    echo "  => firewalld规则已添加"
elif command -v ufw &> /dev/null; then
    ufw allow 7000/tcp 2>/dev/null || true
    ufw allow 7500/tcp 2>/dev/null || true
    ufw allow 10000:60000/tcp 2>/dev/null || true
    echo "  => ufw规则已添加"
else
    echo "  => 未检测到防火墙，请手动开放端口: 7000, 7500, 10000-60000"
fi

echo ""
echo "============================================"
echo "  部署完成！"
echo "============================================"
echo ""
echo "配置文件: $INSTALL_DIR/server/config.yaml"
echo "日志目录: $INSTALL_DIR/logs/"
echo ""
echo "重要: 请先修改配置文件中的 auth_token 和 web_password！"
echo ""
echo "常用命令:"
echo "  启动服务:   systemctl start ${SERVICE_NAME}"
echo "  停止服务:   systemctl stop ${SERVICE_NAME}"
echo "  重启服务:   systemctl restart ${SERVICE_NAME}"
echo "  查看状态:   systemctl status ${SERVICE_NAME}"
echo "  查看日志:   tail -f ${INSTALL_DIR}/logs/server.log"
echo ""
echo "Web管理面板: http://你的服务器IP:7500"
echo ""
echo "在宝塔面板中也可以添加此服务进行管理"
echo "============================================"
