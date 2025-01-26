# s2s
kechangdev/s2s 是一个带用户名/密码鉴权的 SOCKS5 代理，其出站流量会转发给目标IP目标端口的不带鉴权的 socks5 服务。
该项目的使用场景是目标 Socks5 代理已经被封装好（不方便修改），且不带鉴权，但是用户又希望安全地将其暴露到公网使用。

Inbound（入站）：标准 SOCKS5 协议 + 用户名/密码鉴权。
Outbound（出站）：通过指定的 SOCKS5 服务(无鉴权)转发流量。

# Quick Start
```
docker run -d --network host \
  --name tailscale-s2s \
  -e SOCKS5_USERNAME="username" \
  -e SOCKS5_PASSWORD="password" \
  -e TS_SOCKS5_HOST="127.0.0.1" \
  -e TS_SOCKS5_PORT="1055" \
  -e INBOUND_PORT="45675" \
  kechangdev/s2s:latest
```
- `SOCKS5_USERNAME`: 带鉴权的 Socks5 用户名
- `SOCKS5_PASSWORD`: 带鉴权的 Socks5 密码
- `TS_SOCKS5_HOST`: 出站 Socks5 的 IPv4 地址
- `TS_SOCKS5_PORT`: 出站 Socks5 的端口
- `INBOUND_PORT`: 带鉴权的 Socks5 暴露的端口

# 实际的例子
tailscale 只能暴露一个不带鉴权（账号密码）的 Socks5 端口：

```
docker run -d \
  --name tailscale-socks5 \
  --restart=unless-stopped \
  --cap-add=NET_ADMIN \
  -e TS_USERSPACE=true \
  -e TS_SOCKS5_SERVER=0.0.0.0:1055 \
  -p 127.0.0.1:1055:1055 \
  -v /var/www/tailscale-socks5:/var/lib/tailscale \
  tailscale/tailscale:latest tailscaled --tun=userspace-networking --socks5-server=0.0.0.0:1055
```

如果直接将其暴露到公网的话很危险，所以可以考虑加上鉴权：

```
docker run -d --network host \
  --name tailscale-s2s \
  -e SOCKS5_USERNAME="username" \
  -e SOCKS5_PASSWORD="password" \
  -e TS_SOCKS5_HOST="127.0.0.1" \
  -e TS_SOCKS5_PORT="1055" \
  -e INBOUND_PORT="45675" \
  kechangdev/s2s:latest
```

容器部署完成后测试是否能达成效果：
```
curl -v --socks5 127.0.0.1:45675 -U username:password http://tailscale内网地址
```
