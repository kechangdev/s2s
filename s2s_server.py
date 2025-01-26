import os
import socket
import logging
import threading
import struct
import sys

import socks  # 用于 outbound 连接 (pysocks)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ============ 读取环境变量 ============
SOCKS5_USERNAME = os.getenv('SOCKS5_USERNAME', 'username')
SOCKS5_PASSWORD = os.getenv('SOCKS5_PASSWORD', 'password')
INBOUND_PORT = int(os.getenv('INBOUND_PORT', '45675'))

# tailscale 本地 socks5
TS_SOCKS5_HOST = os.getenv('TS_SOCKS5_HOST', '127.0.0.1')
TS_SOCKS5_PORT = int(os.getenv('TS_SOCKS5_PORT', '1055'))

# ============ 简单的校验 ============
if not SOCKS5_USERNAME or not SOCKS5_PASSWORD:
    logging.error("请设置环境变量 SOCKS5_USERNAME、SOCKS5_PASSWORD。")
    sys.exit(1)

# ============ 常量 ============
SOCKS_VERSION = 5
BUFFER_SIZE = 4096

def handle_socks5_client(client_socket, client_address):
    """处理单个 SOCKS5 客户端连接，完成 handshake、鉴权，并将数据转发给 tailscale 的 socks5。"""
    try:
        # 1. 读取客户端发来的握手数据 (VERSION, NMETHODS, METHODS)
        initial_data = client_socket.recv(2)
        if len(initial_data) < 2:
            raise Exception("SOCKS handshake: initial data too short.")
        version, nmethods = initial_data[0], initial_data[1]

        if version != SOCKS_VERSION:
            raise Exception(f"Unsupported SOCKS version: {version}")

        # 读取 nmethods 个字节，代表客户端支持的认证方式
        methods = client_socket.recv(nmethods)
        if len(methods) != nmethods:
            raise Exception("SOCKS handshake: methods data length mismatch.")

        # 2. 服务器选择认证方式
        #    如果我们需要用户名密码鉴权，则返回 0x02（USER/PASS），
        #    否则可以返回 0x00（NO AUTH）。这里我们强制用用户名密码鉴权。
        if 2 not in methods:
            # 表示客户端不支持用户名密码鉴权
            # 这里可以协商失败，返回 0xFF
            client_socket.sendall(struct.pack('BB', SOCKS_VERSION, 0xFF))
            raise Exception("Client does not support USERNAME/PASSWORD authentication.")

        # 告诉客户端，我们用 0x02 号鉴权
        client_socket.sendall(struct.pack('BB', SOCKS_VERSION, 0x02))

        # 3. 进行用户名密码鉴权子协商
        #    客户端会先发送一个版本号（0x01），再发送用户名密码
        auth_header = client_socket.recv(2)
        if len(auth_header) < 2:
            raise Exception("SOCKS auth: header too short.")
        auth_version = auth_header[0]
        if auth_version != 1:
            raise Exception(f"Unsupported auth version: {auth_version}")
        uname_len = auth_header[1]
        username = client_socket.recv(uname_len).decode('utf-8')
        passwd_len = client_socket.recv(1)[0]
        password = client_socket.recv(passwd_len).decode('utf-8')

        # 校验用户名密码
        if username == SOCKS5_USERNAME and password == SOCKS5_PASSWORD:
            # 鉴权成功
            client_socket.sendall(struct.pack('BB', 0x01, 0x00))
        else:
            # 鉴权失败，返回 0x01
            client_socket.sendall(struct.pack('BB', 0x01, 0x01))
            raise Exception("Authentication failed.")

        # 4. 读取 SOCKS5 CONNECT 请求 (VERSION=5, CMD=1, RSV=0, ATYP, DST.ADDR, DST.PORT)
        request_header = client_socket.recv(4)
        if len(request_header) < 4:
            raise Exception("SOCKS connect: request header too short.")

        req_version, req_cmd, req_rsv, req_atyp = request_header
        if req_version != SOCKS_VERSION or req_cmd != 0x01:
            # 这里只实现 CONNECT 命令
            raise Exception("Only SOCKS5 CONNECT is supported.")

        if req_atyp == 0x01:
            # IPv4
            addr_raw = client_socket.recv(4)
            address = socket.inet_ntoa(addr_raw)
        elif req_atyp == 0x03:
            # domain
            domain_len = client_socket.recv(1)[0]
            domain = client_socket.recv(domain_len)
            address = domain.decode('utf-8')
        elif req_atyp == 0x04:
            # IPv6
            addr_raw = client_socket.recv(16)
            address = socket.inet_ntop(socket.AF_INET6, addr_raw)
        else:
            raise Exception("Unsupported ATYP.")

        port_raw = client_socket.recv(2)
        dst_port = struct.unpack('>H', port_raw)[0]

        logging.info(f"[{client_address}] wants to connect to {address}:{dst_port}")

        # 5. 和 tailscale 的 socks5 建立连接 -> 由它去连目标 address:dst_port
        #    配置默认代理: tailscale 的 127.0.0.1:1055 (无鉴权)
        socks.set_default_proxy(
            socks.SOCKS5, 
            addr=TS_SOCKS5_HOST, 
            port=TS_SOCKS5_PORT,
            rdns=True
        )
        proxy_socket = socks.socksocket()
        try:
            proxy_socket.connect((address, dst_port))
        except Exception as e:
            # 如果连接目标失败，需要告诉客户端
            logging.error(f"Failed to connect via tailscale-proxy: {e}")
            reply = generate_socks5_reply(0x05)  # 0x05 = Connection refused等错误
            client_socket.sendall(reply)
            client_socket.close()
            return

        # 连接成功后，告诉客户端：连接成功
        reply = generate_socks5_reply(0x00)  # 0x00 = succeeded
        client_socket.sendall(reply)

        # 6. 双向转发：client_socket <--> proxy_socket
        #    通常用两个线程来做双向转发
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
                # 只要其中一方断开，就关闭双端
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
    finally:
        try:
            client_socket.close()
        except:
            pass

def generate_socks5_reply(rep_code):
    """
    生成 SOCKS5 响应包: VER REP RSV ATYP(BND.ADDR) BND.PORT
    我们实际发回的 BND.ADDR = 0.0.0.0, BND.PORT = 0 即可。
    """
    # VER = 5, REP = rep_code, RSV = 0, ATYP = 1(IPv4)
    # BND.ADDR = 0.0.0.0, BND.PORT = 0
    return struct.pack("BBBB", 5, rep_code, 0, 1) + \
           socket.inet_aton("0.0.0.0") + \
           struct.pack(">H", 0)

def start_socks5_server():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("0.0.0.0", INBOUND_PORT))
    server_socket.listen(128)

    logging.info(f"S2S server listening on 0.0.0.0:{INBOUND_PORT}")

    while True:
        client_socket, addr = server_socket.accept()
        logging.info(f"Accepted connection from {addr}")
        t = threading.Thread(target=handle_socks5_client, args=(client_socket, addr))
        t.start()

if __name__ == "__main__":
    start_socks5_server()
