#!/bin/bash
# NAT Tunnel - VPN 服务器一键部署脚本
# 支持三种 VPN 类型：IKEv2/MSCHAPv2、IPSec PSK（Cisco IPSec）、IPSec RSA
# 兼容：Ubuntu 20.04/22.04、Debian 10/11/12、CentOS 7/8
# 使用方式: sudo bash setup_vpn.sh

set -e

# ──────────────────────────────────────────────
# 可修改的默认参数
# ──────────────────────────────────────────────
VPN_SERVER_IP=""                     # 留空则自动检测公网IP
IKEV2_USERNAME="vpnuser"
IKEV2_PASSWORD="vpn_$(tr -dc 'a-zA-Z0-9' </dev/urandom 2>/dev/null | head -c 12 || echo 'Password123')"
PSK_SECRET="psk_$(tr -dc 'a-zA-Z0-9' </dev/urandom 2>/dev/null | head -c 16 || echo 'PskSecret123456')"
PSK_USERNAME="vpnuser"
PSK_PASSWORD="psk_$(tr -dc 'a-zA-Z0-9' </dev/urandom 2>/dev/null | head -c 12 || echo 'PskPass123')"
P12_PASSWORD="1234"                  # 客户端 P12 证书密码
VPN_CLIENT_IP_POOL_IKEV2="10.10.10.0/24"
VPN_CLIENT_IP_POOL_PSK="10.10.11.0/24"
VPN_CLIENT_IP_POOL_RSA="10.10.12.0/24"
DNS1="8.8.8.8"
DNS2="8.8.4.4"
# ──────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; exit 1; }
step()  { echo -e "\n${BLUE}[→]${NC} $*"; }

# ──────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  NAT Tunnel VPN 服务器部署脚本"
echo "  支持 IKEv2/MSCHAPv2、IPSec PSK、IPSec RSA"
echo "=============================================="
echo ""

# 权限检查
[[ "$EUID" -ne 0 ]] && error "请使用 root 权限运行：sudo bash $0"

# 确保 /usr/sbin 在 PATH 中（RHEL/CentOS 有时缺失）
export PATH=/usr/sbin:/usr/local/sbin:/usr/bin:/usr/local/bin:$PATH

# ── 检测系统 ──────────────────────────────────
step "检测操作系统..."
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_ID="$ID"
    OS_VER="$VERSION_ID"
else
    error "无法检测操作系统类型"
fi

PKG_MANAGER=""
if command -v apt-get &>/dev/null; then
    PKG_MANAGER="apt"
elif command -v yum &>/dev/null; then
    PKG_MANAGER="yum"
elif command -v dnf &>/dev/null; then
    PKG_MANAGER="dnf"
else
    error "未找到支持的包管理器（apt/yum/dnf）"
fi
info "系统: $OS_ID $OS_VER, 包管理器: $PKG_MANAGER"

# ── 获取公网 IP ───────────────────────────────
step "获取服务器公网IP..."
if [ -z "$VPN_SERVER_IP" ]; then
    VPN_SERVER_IP=$(curl -4 -fsSL --max-time 10 https://api.ipify.org 2>/dev/null \
                 || curl -4 -fsSL --max-time 10 https://ipecho.net/plain 2>/dev/null \
                 || ip route get 1 | awk '{print $7; exit}' 2>/dev/null \
                 || echo "")
fi
[ -z "$VPN_SERVER_IP" ] && error "无法获取公网IP，请手动设置脚本顶部的 VPN_SERVER_IP 变量"
info "服务器IP: $VPN_SERVER_IP"

# ── 安装依赖 ──────────────────────────────────
step "安装 StrongSwan 和依赖..."
if [ "$PKG_MANAGER" = "apt" ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq \
        strongswan strongswan-pki \
        libcharon-extra-plugins libcharon-extauth-plugins \
        libstrongswan-extra-plugins \
        iptables-persistent openssl
elif [ "$PKG_MANAGER" = "yum" ] || [ "$PKG_MANAGER" = "dnf" ]; then
    $PKG_MANAGER install -y epel-release 2>/dev/null || true
    $PKG_MANAGER install -y \
        strongswan strongswan-charon strongswan-libipsec \
        openssl iptables-services 2>/dev/null || \
    $PKG_MANAGER install -y strongswan openssl iptables-services
fi
info "StrongSwan 安装完成"

# ── 检测 StrongSwan 配置目录（Ubuntu/Debian: /etc， RHEL: /etc/strongswan） ──
if [ -d /etc/strongswan/ipsec.d ]; then
    SW_CONF="/etc/strongswan"
elif [ -d /etc/strongswan ] && [ ! -L /etc/ipsec.conf ]; then
    SW_CONF="/etc/strongswan"
else
    SW_CONF="/etc"
fi
CERT_DIR="$SW_CONF/ipsec.d"
mkdir -p "$CERT_DIR"/{cacerts,certs,private,p12}

# 如果是 RHEL，创建 /etc/ipsec.conf、/etc/ipsec.d、/etc/ipsec.secrets 的副本/软链接
if [ "$SW_CONF" != "/etc" ]; then
    [ ! -f /etc/ipsec.conf ] && ln -sf "$SW_CONF/ipsec.conf" /etc/ipsec.conf 2>/dev/null || true
    [ ! -L /etc/ipsec.d ]   && ln -sfn "$CERT_DIR"           /etc/ipsec.d   2>/dev/null || true
    [ ! -f /etc/ipsec.secrets ] && ln -sf "$SW_CONF/ipsec.secrets" /etc/ipsec.secrets 2>/dev/null || true
fi
info "配置目录: $SW_CONF"

# CA 私钥 + 证书
if [ ! -f "$CERT_DIR/private/ca.key.pem" ]; then
    ipsec pki --gen --type rsa --size 4096 \
        --outform pem > "$CERT_DIR/private/ca.key.pem"
    chmod 600 "$CERT_DIR/private/ca.key.pem"
    ipsec pki --self --ca --lifetime 3650 \
        --in "$CERT_DIR/private/ca.key.pem" --type rsa \
        --dn "CN=NAT Tunnel CA" \
        --outform pem > "$CERT_DIR/cacerts/ca.crt"
    info "CA 根证书已生成: $CERT_DIR/cacerts/ca.crt"
else
    info "CA 根证书已存在，跳过生成"
fi

# 服务器私钥 + 证书
if [ ! -f "$CERT_DIR/private/server.key.pem" ]; then
    ipsec pki --gen --type rsa --size 2048 \
        --outform pem > "$CERT_DIR/private/server.key.pem"
    chmod 600 "$CERT_DIR/private/server.key.pem"
    ipsec pki --pub --in "$CERT_DIR/private/server.key.pem" --type rsa \
        | ipsec pki --issue --lifetime 1825 \
            --cacert "$CERT_DIR/cacerts/ca.crt" \
            --cakey  "$CERT_DIR/private/ca.key.pem" \
            --dn "CN=$VPN_SERVER_IP" \
            --san "$VPN_SERVER_IP" \
            --flag serverAuth --flag ikeIntermediate \
            --outform pem > "$CERT_DIR/certs/server.crt"
    info "服务器证书已生成: $CERT_DIR/certs/server.crt"
else
    info "服务器证书已存在，跳过生成"
fi

# 客户端证书 (用于 RSA 认证)
if [ ! -f "$CERT_DIR/private/client.key.pem" ]; then
    ipsec pki --gen --type rsa --size 2048 \
        --outform pem > "$CERT_DIR/private/client.key.pem"
    chmod 600 "$CERT_DIR/private/client.key.pem"
    ipsec pki --pub --in "$CERT_DIR/private/client.key.pem" --type rsa \
        | ipsec pki --issue --lifetime 1825 \
            --cacert "$CERT_DIR/cacerts/ca.crt" \
            --cakey  "$CERT_DIR/private/ca.key.pem" \
            --dn "CN=vpn-client" \
            --outform pem > "$CERT_DIR/certs/client.crt"
    # 打包为 P12（手机安装用）
    openssl pkcs12 -export \
        -in  "$CERT_DIR/certs/client.crt" \
        -inkey "$CERT_DIR/private/client.key.pem" \
        -certfile "$CERT_DIR/cacerts/ca.crt" \
        -name "vpn-client" \
        -passout "pass:$P12_PASSWORD" \
        -out "$CERT_DIR/p12/client.p12"
    info "客户端证书 P12 已生成: $CERT_DIR/p12/client.p12  (密码: $P12_PASSWORD)"
else
    info "客户端证书已存在，跳过生成"
fi

# ── 配置 ipsec.conf ───────────────────────────
step "配置 ipsec.conf..."
cat > "$SW_CONF/ipsec.conf" << EOF
# NAT Tunnel VPN - ipsec.conf
# 自动生成于 $(date '+%Y-%m-%d %H:%M:%S')

config setup
    charondebug="ike 1, knl 1, cfg 0"
    uniqueids=no

conn %default
    ikelifetime=60m
    keylife=20m
    rekeymargin=3m
    keyingtries=3
    dpdaction=clear
    dpddelay=300s

# ── IKEv2 + EAP-MSCHAPv2 (用户名/密码认证) ────
# 手机端「IPSec标识符」填写：$VPN_SERVER_IP
conn ikev2-eap-mschapv2
    keyexchange=ikev2
    # 宽泛密码套件，兼容 iOS 16+/Android 10+ 及各品牌手机
    ike=aes256gcm16-sha256-ecp256,aes256gcm16-sha256-modp2048,aes256-sha256-modp2048,aes256-sha256-ecp256,aes128gcm16-sha256-modp2048,aes128-sha256-modp2048
    esp=aes256gcm16-sha256,aes256-sha256,aes128gcm16-sha256,aes128-sha256
    left=%any
    leftid=$VPN_SERVER_IP
    leftauth=pubkey
    leftcert=server.crt
    leftsendcert=always
    leftsubnet=0.0.0.0/0
    right=%any
    rightauth=eap-mschapv2
    rightsourceip=$VPN_CLIENT_IP_POOL_IKEV2
    rightdns=$DNS1,$DNS2
    eap_identity=%any
    reauth=no
    fragmentation=yes
    compress=no
    auto=add

# ── IPSec PSK / Cisco IPSec (IKEv1 + XAuth) ──
conn ipsec-psk
    keyexchange=ikev1
    ike=aes256-sha256-modp2048,aes256-sha1-modp2048,aes128-sha256-modp2048,aes128-sha1-modp2048
    esp=aes256-sha256,aes256-sha1,aes128-sha256,aes128-sha1
    left=%any
    leftauth=psk
    leftsubnet=0.0.0.0/0
    right=%any
    rightauth=psk
    rightauth2=xauth
    rightsourceip=$VPN_CLIENT_IP_POOL_PSK
    rightdns=$DNS1,$DNS2
    xauth=server
    fragmentation=yes
    auto=add

# ── IKEv2 + RSA 证书认证 ──────────────────────
conn ikev2-rsa
    keyexchange=ikev2
    ike=aes256gcm16-sha256-ecp256,aes256gcm16-sha256-modp2048,aes256-sha256-modp2048,aes128gcm16-sha256-modp2048,aes128-sha256-modp2048
    esp=aes256gcm16-sha256,aes256-sha256,aes128gcm16-sha256,aes128-sha256
    left=%any
    leftid=$VPN_SERVER_IP
    leftauth=pubkey
    leftcert=server.crt
    leftsendcert=always
    leftsubnet=0.0.0.0/0
    right=%any
    rightauth=pubkey
    rightsourceip=$VPN_CLIENT_IP_POOL_RSA
    rightdns=$DNS1,$DNS2
    fragmentation=yes
    auto=add
EOF
info "ipsec.conf 已写入"
# RHEL 路径处理
[ "$SW_CONF" != "/etc" ] && (cp -f "$SW_CONF/ipsec.conf" /etc/ipsec.conf 2>/dev/null || true)

# ── 配置 ipsec.secrets ────────────────────────
step "配置 ipsec.secrets..."
cat > "$SW_CONF/ipsec.secrets" << EOF
# NAT Tunnel VPN - ipsec.secrets
# 自动生成于 $(date '+%Y-%m-%d %H:%M:%S')

# 服务器 RSA 私钥
: RSA server.key.pem

# IPSec PSK（Cisco IPSec 预共享密钥）
%any %any : PSK "$PSK_SECRET"

# IKEv2/EAP-MSCHAPv2 用户（格式：用户名 : EAP "密码"）
$IKEV2_USERNAME : EAP "$IKEV2_PASSWORD"

# IPSec PSK XAuth 用户（格式：用户名 : XAUTH "密码"）
$PSK_USERNAME : XAUTH "$PSK_PASSWORD"
EOF
chmod 600 "$SW_CONF/ipsec.secrets"
# RHEL 路径处理
[ "$SW_CONF" != "/etc" ] && (cp -f "$SW_CONF/ipsec.secrets" /etc/ipsec.secrets 2>/dev/null || true)
info "ipsec.secrets 已写入"

# ── 配置 charon EAP-MSCHAPv2 插件 ─────────────
step "配置 StrongSwan charon 插件..."
mkdir -p /etc/strongswan.d/charon
# 确保 EAP-MSCHAPv2 和 EAP-Identity 插件加载
for plugin in eap-mschapv2 eap-identity; do
    conf_file="/etc/strongswan.d/charon/${plugin}.conf"
    if [ ! -f "$conf_file" ]; then
        echo -e "${plugin} {\n    load = yes\n}" > "$conf_file"
    else
        # 确保 load = yes
        sed -i 's/load = no/load = yes/g' "$conf_file" 2>/dev/null || true
    fi
done
info "charon 插件配置完成"
step "开启 IP 转发..."
sed -i '/^#\?net\.ipv4\.ip_forward/d' /etc/sysctl.conf
echo "net.ipv4.ip_forward = 1" >> /etc/sysctl.conf
sed -i '/^#\?net\.ipv4\.conf\.all\.accept_redirects/d' /etc/sysctl.conf
echo "net.ipv4.conf.all.accept_redirects = 0" >> /etc/sysctl.conf
sed -i '/^#\?net\.ipv4\.conf\.all\.send_redirects/d' /etc/sysctl.conf
echo "net.ipv4.conf.all.send_redirects = 0" >> /etc/sysctl.conf
sysctl -p -q
info "IP 转发已开启"

# ── 配置防火墙（NAT + 允许 VPN 端口）────────
step "配置 iptables 规则..."
# 检测出口网卡
OUTIF=$(ip route | awk '/^default/{print $5; exit}')
if [ -z "$OUTIF" ]; then
    warn "无法自动检测出口网卡，请手动检查 iptables NAT 规则"
    OUTIF="eth0"
fi

# 清理旧规则（避免重复）
iptables -t nat -D POSTROUTING -s "$VPN_CLIENT_IP_POOL_IKEV2" -o "$OUTIF" -j MASQUERADE 2>/dev/null || true
iptables -t nat -D POSTROUTING -s "$VPN_CLIENT_IP_POOL_PSK"   -o "$OUTIF" -j MASQUERADE 2>/dev/null || true
iptables -t nat -D POSTROUTING -s "$VPN_CLIENT_IP_POOL_RSA"   -o "$OUTIF" -j MASQUERADE 2>/dev/null || true

# 添加 NAT 转发
iptables -t nat -A POSTROUTING -s "$VPN_CLIENT_IP_POOL_IKEV2" -o "$OUTIF" -j MASQUERADE
iptables -t nat -A POSTROUTING -s "$VPN_CLIENT_IP_POOL_PSK"   -o "$OUTIF" -j MASQUERADE
iptables -t nat -A POSTROUTING -s "$VPN_CLIENT_IP_POOL_RSA"   -o "$OUTIF" -j MASQUERADE

# 允许转发
iptables -P FORWARD ACCEPT

# 允许 IKE、ESP、NAT-T 端口
iptables -I INPUT -p udp --dport 500  -j ACCEPT 2>/dev/null || true
iptables -I INPUT -p udp --dport 4500 -j ACCEPT 2>/dev/null || true
iptables -I INPUT -p 50 -j ACCEPT 2>/dev/null || true   # ESP

# 保存规则
if command -v netfilter-persistent &>/dev/null; then
    netfilter-persistent save
elif command -v iptables-save &>/dev/null; then
    iptables-save > /etc/iptables/rules.v4 2>/dev/null || \
    iptables-save > /etc/sysconfig/iptables 2>/dev/null || true
fi
info "防火墙规则已配置（出口网卡: $OUTIF）"

# ── 启动 StrongSwan ───────────────────────────
step "启动 StrongSwan 服务..."
# 尝试各种服务名
STARTED=0
for SVC in strongswan-starter strongswan; do
    if systemctl list-unit-files "${SVC}.service" 2>/dev/null | grep -q "$SVC"; then
        systemctl enable "$SVC" 2>/dev/null || true
        systemctl restart "$SVC" 2>/dev/null && STARTED=1 && break
    fi
done

if [ "$STARTED" -eq 0 ]; then
    # 尝试直接调用 ipsec
    IPSEC_BIN=$(command -v ipsec 2>/dev/null || echo "")
    if [ -n "$IPSEC_BIN" ]; then
        $IPSEC_BIN restart 2>/dev/null && STARTED=1
    fi
fi

[ "$STARTED" -eq 0 ] && warn "StrongSwan 启动失败，请手动运行：systemctl restart strongswan"
sleep 2

IPSEC_BIN=$(command -v ipsec 2>/dev/null || echo "")
if [ -n "$IPSEC_BIN" ] && $IPSEC_BIN status &>/dev/null; then
    info "StrongSwan 运行正常"
else
    warn "StrongSwan 可能未完全启动，请运行：systemctl status strongswan 或 ipsec status 检查"
fi

# ── 输出连接参数 ──────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo -e "${GREEN}  VPN 部署完成！以下是手机端连接参数${NC}"
echo "══════════════════════════════════════════════════"
echo ""
echo -e "${BLUE}▶ 方式一：IKEv2/IPSec MSCHAPv2（推荐）${NC}"
echo "   连接类型   : IKEv2/IPSec MSCHAPv2"
echo "   服务器地址 : $VPN_SERVER_IP"
echo "   IPSec标识符: $VPN_SERVER_IP  ← 手机此字段必须填写"
echo "   用户名     : $IKEV2_USERNAME"
echo "   密码       : $IKEV2_PASSWORD"
echo "   IPSec CA证书: 不验证服务器（或留空）"
echo "   IPSec服务器证书: 来自服务器"
echo "   本地ID     : 留空"
echo ""
echo -e "${BLUE}▶ 方式二：IPSec PSK / Cisco IPSec${NC}"
echo "   服务器地址 : $VPN_SERVER_IP"
echo "   连接类型   : IPSec（Cisco IPSec / Xauth PSK）"
echo "   预共享密钥 : $PSK_SECRET"
echo "   用户名     : $PSK_USERNAME"
echo "   密码       : $PSK_PASSWORD"
echo ""
echo -e "${BLUE}▶ 方式三：IKEv2/IPSec RSA 证书认证${NC}"
echo "   服务器地址 : $VPN_SERVER_IP"
echo "   连接类型   : IKEv2"
echo "   认证方式   : 证书"
echo "   CA 证书    : $CERT_DIR/cacerts/ca.crt"
echo "   客户端P12  : $CERT_DIR/p12/client.p12"
echo "   P12 密码   : $P12_PASSWORD"
echo "   → 将 ca.crt 和 client.p12 传输到手机安装"
echo ""
echo "══════════════════════════════════════════════════"
echo -e "${YELLOW}  提示：请将以上参数填入 Web 管理面板 → VPN配置${NC}"
echo "══════════════════════════════════════════════════"
echo ""

# 同步参数到 NAT Tunnel 配置（如果存在）
NAT_CONFIG="/www/server/nat-tunnel/server/config.yaml"
[ ! -f "$NAT_CONFIG" ] && NAT_CONFIG="$(dirname "$(readlink -f "$0")")/../server/config.yaml"
if [ -f "$NAT_CONFIG" ]; then
    step "同步VPN参数到 NAT Tunnel 配置..."
    python3 - <<PYEOF
import re
path = "$NAT_CONFIG"
try:
    txt = open(path, encoding='utf-8').read()
    updates = {
        'server_host': '$VPN_SERVER_IP',
        'vpn_ikev2_username': '$IKEV2_USERNAME',
        'vpn_ikev2_password': '$IKEV2_PASSWORD',
        'vpn_psk_secret': '$PSK_SECRET',
        'vpn_psk_username': '$PSK_USERNAME',
        'vpn_psk_password': '$PSK_PASSWORD',
        'vpn_rsa_ca_cert': '$CERT_DIR/cacerts/ca.crt',
        'vpn_rsa_client_p12': '$CERT_DIR/p12/client.p12',
    }
    for k, v in updates.items():
        v_esc = v.replace('\\\\', '\\\\\\\\').replace('"', '\\\\"')
        new_line = '{}: "{}"'.format(k, v_esc)
        txt2, n = re.subn(r'^{}:.*$'.format(re.escape(k)), new_line, txt, flags=re.MULTILINE)
        if n == 0:
            txt = txt.rstrip('\\n') + '\\n' + new_line + '\\n'
        else:
            txt = txt2
    open(path, 'w', encoding='utf-8').write(txt)
    print("NAT Tunnel config.yaml 已同步更新")
except Exception as e:
    print("同步失败（不影响VPN使用）:", e)
PYEOF
fi
