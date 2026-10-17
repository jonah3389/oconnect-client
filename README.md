# oconnect-client

使用每台电脑自己生成的身份，通过局域网自动采集手机中的 OPPO 手表录音。电脑端无需安装或登录 O+ Connect，手机端使用系统内置互联服务。首次手机解锁同意，日常可在锁屏状态下增量同步。

## 安装与使用

```sh
git clone https://github.com/jonah3389/oconnect-client.git
cd oconnect-client
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

oconnect-client --identity ./private/identity.json init --name '我的录音电脑'
# 将文档测试地址替换为手机真实 Wi-Fi 地址；首次在手机上同意连接
oconnect-client --identity ./private/identity.json --host 192.0.2.1 pair
oconnect-client --identity ./private/identity.json --host 192.0.2.1 sync --output ./recordings
oconnect-client --identity ./private/identity.json --host 192.0.2.1 watch --output ./recordings --interval 60
```

每台电脑各自执行 init。身份文件包含该电脑的私钥和手机授权材料，保留在本机；复制身份文件会复制身份。首次配对需要本机 TCP 46888 可供手机连接，官方后台若占用端口，应先停止它。日常同步不需要 ADB、USB 或重复确认，不删除手机源文件。

输出只包含 `Music/Recordings/Watch Recordings/` 下的录音；目录保留手表子目录。未指定输出时保存到用户主目录 `Recordings/OPPO/`。运行状态默认保存在用户主目录 `.oconnect_client/state/`，可用 `--state-dir` 改写。

## 开发与验证

```sh
python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
python -m pytest
python scripts/privacy_check.py
python -m build
```

默认测试不连接手机。显式实机测试见 [docs/test.md](docs/test.md)，协议和信任边界见 [docs/rfc.md](docs/rfc.md)，原目录迁移说明见 [docs/migration.md](docs/migration.md)。

## 验收与边界

独立身份已在开发电脑的隔离代码包中完成手机同意、原始录音下载、锁屏重连及增量去重，文件哈希与先前完整文件一致。新项目的验证记录见 [docs/working.md](docs/working.md)。首次配对沿用已验证的单一反向控制连接，避免同一身份的两条默认连接互相影响。

未完成的实机范围包括第二台实体电脑、Windows、独立身份下的手机重启恢复、过夜省电和 IP 漂移。这里只提供录音访问，不提供投屏、远控或剪贴板；不会安装开机启动服务。

## Privacy

This repository is designed to be publishable with only fake examples.

公开源码和测试不包含真实账号、设备身份、录音、官方二进制、抓包或反编译产物。示例中的 `192.0.2.1` 是文档测试地址，不能直接连接真实手机。本项目是独立协议客户端，与相关设备厂商没有隶属关系。

## 许可证

[MIT](LICENSE)。
