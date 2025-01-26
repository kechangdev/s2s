# s2s

[![Docker Pulls](https://img.shields.io/docker/pulls/kechangdev/s2s?style=flat-square)](https://hub.docker.com/r/kechangdev/s2s)

**kechangdev/s2s** 是一个带用户名/密码鉴权的 SOCKS5 代理，其 **出站流量** 会转发给目标 IP/端口的不带鉴权 SOCKS5 服务。  
主要用途是：当已有的目标 SOCKS5 服务无法进行用户名/密码鉴权配置，但你又想安全地在公网使用它时，可以通过本容器来“包裹”一层安全鉴权。

## 功能特性

1. **带用户名/密码的 SOCKS5 入站**：外部连接需要正确的用户名/密码才能使用代理。  
2. **无鉴权的 SOCKS5 出站**：将所有请求转发给指定的 SOCKS5 代理（不支持或未开启鉴权）。  
3. **适用于 Tailscale**：常见场景是在一台服务器上只跑一个 **无鉴权** 的 Tailscale SOCKS5（例如 `127.0.0.1:1055`），再用本容器为它添加安全鉴权。

---

## 快速开始

以下示例会启动本容器，监听本机 `45675` 端口，用户名/密码均设置为 `username` / `password`，并将流量转发给本机的无鉴权 SOCKS5 代理 `127.0.0.1:1055`。

```bash
docker run -d --network host \
  --name tailscale-s2s \
  -e SOCKS5_USERNAME="username" \
  -e SOCKS5_PASSWORD="password" \
  -e TS_SOCKS5_HOST="127.0.0.1" \
  -e TS_SOCKS5_PORT="1055" \
  -e INBOUND_PORT="45675" \
  kechangdev/s2s:latest
```

> **说明**：  
> - `--network host` 通常用于让容器与宿主机共享网络命名空间，方便连接到本机的 Tailscale SOCKS5。也可根据需要使用其他网络模式，只要容器能访问 `TS_SOCKS5_HOST:TS_SOCKS5_PORT` 即可。  
> - 如果想修改监听端口，只需在启动时更改 `INBOUND_PORT` 并相应映射端口。  

---

## 环境变量

| 变量名            | 默认值       | 说明                                                         |
|:------------------|:------------|:------------------------------------------------------------|
| `SOCKS5_USERNAME` | `username`  | **入站 SOCKS5** 鉴权使用的用户名                            |
| `SOCKS5_PASSWORD` | `password`  | **入站 SOCKS5** 鉴权使用的密码                              |
| `TS_SOCKS5_HOST`  | `127.0.0.1` | **出站 SOCKS5** 的地址（例如 Tailscale 提供的 socks5）      |
| `TS_SOCKS5_PORT`  | `1055`      | **出站 SOCKS5** 的端口                                      |
| `INBOUND_PORT`    | `45675`     | 本容器对外暴露的 SOCKS5 端口（带用户名/密码鉴权）            |

---

## 实际示例：使用代理软件访问 Tailscale 内网

你是否还在困扰 tailscale 客户端与 Clash 或 Quantumult X 等代理软件同一时刻只能运行一个吗？

[tailscale Userspace networking mode](https://tailscale.com/kb/1112/userspace-networking) 只能使用 Tailscale 自带的 SOCKS5 服务（无鉴权），如果直接暴露在公网的话不够安全。所以我们考虑将端口映射到 `127.0.0.1:1055`，并通过 `kechangdev/s2s` 来暴露一个带鉴权的 Socks5 端口。

1. **运行 Tailscale 容器**（提供无鉴权 Socks5）：
   ```bash
   docker run -d \
     --name tailscale-socks5 \
     --restart=unless-stopped \
     --cap-add=NET_ADMIN \
     -e TS_USERSPACE=true \
     -e TS_SOCKS5_SERVER=0.0.0.0:1055 \
     -p 127.0.0.1:1055:1055 \
     -v /var/www/tailscale-socks5:/var/lib/tailscale \
     tailscale/tailscale:latest tailscaled \
       --tun=userspace-networking \
       --socks5-server=0.0.0.0:1055
   ```
   - 这样在宿主机 `127.0.0.1:1055` 就能访问到一个无鉴权的 Socks5 代理。
测试：
```
curl --socks5 127.0.0.1:1055 http://tailscale 内网服务
```

2. **运行本项目容器 `kechangdev/s2s`**：
   ```bash
   docker run -d --network host \
     --name tailscale-s2s \
     -e SOCKS5_USERNAME="username" \
     -e SOCKS5_PASSWORD="password" \
     -e TS_SOCKS5_HOST="127.0.0.1" \
     -e TS_SOCKS5_PORT="1055" \
     -e INBOUND_PORT="45675" \
     kechangdev/s2s:latest
   ```
   - `s2s` 会监听 `45675` 端口，并要求用户名/密码才可使用代理。

3. **测试**：
   ```bash
   curl -v --socks5 127.0.0.1:45675 -U username:password http://tailscale 内网服务
   ```
   若能正确返回目标页面内容，则说明整个代理链路正常。

4. **代理软件配置**
   在代理软件上配置 socks5 节点即可。
---

## 常见问题

1. **端口冲突或网络不通**  
   - 请确保 `INBOUND_PORT` 未被占用。  
   - 若容器与宿主机网络隔离，需要保证容器能够访问 `TS_SOCKS5_HOST:TS_SOCKS5_PORT`（例如使用 `--network host` 或 Docker Compose 配置好网络）。

2. **如何在公网使用？**  
   - 确保服务器能被外网访问相应端口（如 `45675`）。  
   - 强烈建议使用 **TLS/SSH 隧道** 或其他方式加固安全。仅使用 SOCKS5 + 用户名/密码在公网暴露仍存在潜在风险（暴力穷举、抓包等）。
