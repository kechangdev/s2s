import os
import sys
import socket
import logging
import threading
import struct
import ipaddress

import socks  # 用于 outbound 连接 (pysocks)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ============ 读取环境变量 ============
SOCKS5_USERNAME = os.getenv('SOCKS5_USERNAME', 'username')
SOCKS5_PASSWORD = os.getenv('SOCKS5_PASSWORD', 'password')
INBOUND_PORT = int(os.getenv('INBOUND_PORT', '45675'))

# 原 TS_ 改为 T_ 
T_SOCKS5_HOST = os.getenv('T_SOCKS5_HOST', '127.0.0.1')
T_SOCKS5_PORT = int(os.getenv('T_SOCKS5_PORT', '1055'))

# 新增：可配置的“允许代理的 IP 网段”，多个网段以分号分隔，默认允许所有
VALID_CIDR = os.getenv('VALID_CIDR', '0.0.0.0/0')

if not SOCKS5_USERNAME or not SOCKS5_PASSWORD:
    logging.error("请设置环境变量 SOCKS5_USERNAME、SOCKS5_PASSWORD。")
    sys.exit(1)

# 将 VALID_CIDR 拆成多个网段对象
valid_networks = []
try:
    for cidr_str in VALID_CIDR.split(';'):
        cidr_str = cidr_str.strip()
        if cidr_str:
            net = ipaddress.ip_network(cidr_str, strict=False)
            valid_networks.append(net)
except Exception as e:
    logging.error(f"解析 VALID_CIDR={VALID_CIDR} 失败: {e}")
    sys.exit(1)

# ============ 常量 ============
SOCKS_VERSION = 5
BUFFER_SIZE = 4096

def is_in_valid_cidr(ip_str):
    """检查给定 IP 是否落在 valid_networks 列表中的任意网段内。"""
    try:
        ip_obj = ipaddress.ip_address(ip_str)
        for net in valid_networks:
            if ip_obj in net:
                return True
        return False
    except ValueError:
        return False

def handle_socks5_client(client_socket, client_address):
    """
    处理单个 SOCKS5 客户端连接，完成 handshake、鉴权，
    验证目标地址是否在 VALID_CIDR，若通过则交由 T_SOCKS5_HOST:T_SOCKS5_PORT 转发。
    """
    proxy_socket = None
    try:
        # 1. SOCKS5 握手
        initial_data = client_socket.recv(2)
        if len(initial_data) < 2:
            raise Exception("SOCKS handshake: initial data too short.")
        version, nmethods = initial_data[0], initial_data[1]

        if version != SOCKS_VERSION:
            raise Exception(f"Unsupported SOCKS version: {version}")

        methods = client_socket.recv(nmethods)
        if len(methods) != nmethods:
            raise Exception("SOCKS handshake: methods data length mismatch.")

        # 2. 强制使用用户名密码鉴权 (0x02)
        if 2 not in methods:
            client_socket.sendall(struct.pack('BB', SOCKS_VERSION, 0xFF))
            raise Exception("Client does not support USERNAME/PASSWORD authentication.")
        client_socket.sendall(struct.pack('BB', SOCKS_VERSION, 0x02))

        # 3. 用户名/密码鉴权子协商
        auth_header = client_socket.recv(2)
        if len(auth_header) < 2:
            raise Exception("SOCKS auth: header too short.")
        auth_version = auth_header[0]
        if auth_version != 1:
            raise Exception(f"Unsupported auth version: {auth_version}")

        uname_len = auth_header[1]
        username = client_socket.recv(uname_len).decode('utf-8', errors='ignore')

        passwd_len_data = client_socket.recv(1)
        if len(passwd_len_data) < 1:
            raise Exception("SOCKS auth: password length byte missing.")
        passwd_len = passwd_len_data[0]
        password = client_socket.recv(passwd_len).decode('utf-8', errors='ignore')

        if username == SOCKS5_USERNAME and password == SOCKS5_PASSWORD:
            # 鉴权成功
            client_socket.sendall(struct.pack('BB', 0x01, 0x00))
        else:
            # 鉴权失败
            client_socket.sendall(struct.pack('BB', 0x01, 0x01))
            raise Exception("Authentication failed.")

        # 4. 读取 CONNECT 请求
        request_header = client_socket.recv(4)
        if len(request_header) < 4:
            raise Exception("SOCKS connect: request header too short.")

        req_version, req_cmd, req_rsv, req_atyp = request_header
        if req_version != SOCKS_VERSION or req_cmd != 0x01:
            raise Exception("Only SOCKS5 CONNECT is supported.")

        # 解析目标地址
        address = None

        if req_atyp == 0x01:  # IPv4
            addr_raw = client_socket.recv(4)
            if len(addr_raw) < 4:
                raise Exception("Invalid IPv4 in request.")
            address = socket.inet_ntoa(addr_raw)

        elif req_atyp == 0x03:  # Domain
            domain_len_data = client_socket.recv(1)
            if len(domain_len_data) < 1:
                raise Exception("Domain length byte missing.")
            domain_len = domain_len_data[0]
            domain_data = client_socket.recv(domain_len)
            domain_str = domain_data.decode('utf-8', errors='ignore')

            # 本地解析 domain
            info_list = socket.getaddrinfo(domain_str, 0, socket.AF_UNSPEC, socket.SOCK_STREAM)
            resolved_ip = None
            for info in info_list:
                ip_str = info[4][0]
                if is_in_valid_cidr(ip_str):
                    resolved_ip = ip_str
                    break
            if not resolved_ip:
                raise Exception(f"Domain '{domain_str}' not resolved in valid CIDR")
            address = resolved_ip

        elif req_atyp == 0x04:  # IPv6
            addr_raw = client_socket.recv(16)
            if len(addr_raw) < 16:
                raise Exception("Invalid IPv6 in request.")
            address = socket.inet_ntop(socket.AF_INET6, addr_raw)

        else:
            raise Exception("Unsupported ATYP.")

        port_raw = client_socket.recv(2)
        if len(port_raw) < 2:
            raise Exception("DST.PORT missing.")
        dst_port = struct.unpack('>H', port_raw)[0]

        logging.info(f"[{client_address}] wants to connect to {address}:{dst_port}")

        # 再次检查 IP 是否在 valid cidr（如 domain 已解析则已检查过，但这里多一道保险）
        if not is_in_valid_cidr(address):
            logging.warning(f"Address {address} not in VALID_CIDR, refuse proxy.")
            reply = generate_socks5_reply(0x05)  # 0x05 = Connection refused
            client_socket.sendall(reply)
            return

        # 5. 与外部无鉴权 SOCKS5 建立连接
        socks.set_default_proxy(
            socks.SOCKS5,
            addr=T_SOCKS5_HOST,
            port=T_SOCKS5_PORT,
            rdns=False
        )
        proxy_socket = socks.socksocket()
        try:
            proxy_socket.connect((address, dst_port))
        except Exception as e:
            logging.error(f"Failed to connect via {T_SOCKS5_HOST}:{T_SOCKS5_PORT}: {e}")
            reply = generate_socks5_reply(0x05)
            client_socket.sendall(reply)
            return

        # 连接成功，返回成功应答
        reply = generate_socks5_reply(0x00)
        client_socket.sendall(reply)

        # 6. 双向转发
        def forward(src, dst):
            try:
                while True:
                    data = src.recv(BUFFER_SIZE)
                    if not data:
                        break
                    dst.sendall(data)
            except:
                pass
            finally:
                src.close()
                dst.close()

        t1 = threading.Thread(target=forward, args=(client_socket, proxy_socket))
        t2 = threading.Thread(target=forward, args=(proxy_socket, client_socket))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    except Exception as e:
        logging.error(f"Error in handle_socks5_client: {e}")
        try:
            reply = generate_socks5_reply(0x05)
            client_socket.sendall(reply)
        except:
            pass
    finally:
        if proxy_socket:
            proxy_socket.close()
        client_socket.close()

def generate_socks5_reply(rep_code):
    """
    生成 SOCKS5 响应包: VER REP RSV ATYP BND.ADDR BND.PORT
    我们实际发回 BND.ADDR=0.0.0.0, BND.PORT=0。
    """
    return struct.pack("BBBB", 5, rep_code, 0, 1) + \
           socket.inet_aton("0.0.0.0") + \
           struct.pack(">H", 0)

def start_socks5_server():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("0.0.0.0", INBOUND_PORT))
    server_socket.listen(128)
    logging.info(f"S2S server listening on 0.0.0.0:{INBOUND_PORT} (valid_cidr={VALID_CIDR})")

    while True:
        client_socket, addr = server_socket.accept()
        logging.info(f"Accepted connection from {addr}")
        t = threading.Thread(target=handle_socks5_client, args=(client_socket, addr))
        t.start()

if __name__ == "__main__":
    start_socks5_server()
