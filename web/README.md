# Web UI

本目录提供一个零前端构建、零新增依赖的本地 Web 控制台。

```bash
python web/server.py --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000` 后可以使用：

- Web 聊天会话
- 会话历史列表
- `memory/*.md` 记忆文件浏览
- `/hybrid`、`/chat`、`/coding` 模式切换

## 快捷键

- `Enter`：发送消息
- `Shift+Enter`：输入换行
- `Ctrl/Cmd+K`：新建会话
- `Ctrl/Cmd+1`：切到 Hybrid
- `Ctrl/Cmd+2`：切到 Chat
- `Ctrl/Cmd+3`：切到 Coding

聊天功能复用 `core.bootstrap.build_runtime()`，所以仍需要原 CLI 所需的 `DEEPSEEK_API_KEY` 和相关 Python 依赖。

## 部署到云服务器

推荐用一台 Ubuntu/Debian VPS，后端只监听 `127.0.0.1:8000`，再用 Nginx 反向代理到公网。不要直接把 `8000` 端口暴露出去，因为这个控制台能触发 agent 工具。

### 1. 上传代码并安装依赖

```bash
git clone <your-repo-url> /opt/agent-console
cd /opt/agent-console
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

如果你不是用 Git，也可以用 `scp`/`rsync` 把整个目录上传到 `/opt/agent-console`。

### 2. 配置环境变量

在项目根目录创建 `.env`：

```bash
DEEPSEEK_API_KEY=你的 key
DEEPSEEK_BASE_URL=https://api.deepseek.com
USE_LOCAL_PROXY=0
WEB_USERNAME=agent
WEB_PASSWORD=换成强密码
```

`WEB_PASSWORD` 设置后，浏览器会要求登录。云服务器通常不需要本机代理，所以这里设置 `USE_LOCAL_PROXY=0`。

### 3. 用 systemd 托管服务

创建 `/etc/systemd/system/agent-console.service`：

```ini
[Unit]
Description=Agent Console Web UI
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/agent-console
EnvironmentFile=/opt/agent-console/.env
ExecStart=/opt/agent-console/.venv/bin/python web/server.py --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agent-console
sudo systemctl status agent-console
```

### 4. 配置 Nginx 反向代理

创建 `/etc/nginx/sites-available/agent-console`：

```nginx
server {
    listen 80;
    server_name your-domain.example;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

启用配置：

```bash
sudo ln -s /etc/nginx/sites-available/agent-console /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 5. 开 HTTPS

域名解析到服务器后，用 Certbot 给 Nginx 自动配置证书：

```bash
sudo certbot --nginx -d your-domain.example
```

### 6. 常用排查

```bash
sudo journalctl -u agent-console -f
curl http://127.0.0.1:8000/api/health
sudo nginx -t
```

参考文档：

- Python `venv`: https://docs.python.org/3/library/venv.html
- systemd service: https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html
- Nginx reverse proxy: https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy/
- Certbot Nginx instructions: https://certbot.eff.org/instructions
