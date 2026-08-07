# 外网访问：Tunnel 后台 + 账号门禁

## 后台 cloudflared

```bash
python deploy/ctl.py dashboard start
python deploy/ctl.py tunnel start
# 或
python deploy/ctl.py start-all --with-tunnel
```

公网 URL：

```bash
grep -oE 'https://[a-zA-Z0-9.-]+.trycloudflare.com' results/tunnel.log | tail -1
```

```bash
python deploy/ctl.py tunnel stop
```

## 账号门禁

```bash
cp config/users.yaml.example config/users.yaml
python -c "from quant_trading_system.dashboard.auth import hash_password; print(hash_password('你的强密码'))"
```

`enabled: true`，写入 password_hash。无 users.yaml 或 enabled:false 时不拦截。
