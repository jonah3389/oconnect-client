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
oconnect-client --identity ./private/identity.json sync --output ./recordings
oconnect-client --identity ./private/identity.json watch --output ./recordings --interval 60
```

每台电脑各自执行 init。身份文件包含该电脑的私钥和手机授权材料，保留在本机；复制身份文件会复制身份。首次配对需要本机 TCP 46888 可供手机连接，官方后台若占用端口，应先停止它。日常同步不需要 ADB、USB 或重复确认，不删除手机源文件。

输出只包含 `Music/Recordings/Watch Recordings/` 下的录音；目录保留手表子目录。未指定输出时保存到用户主目录 `Recordings/OPPO/`。运行状态默认保存在用户主目录 `.oconnect_client/state/`，可用 `--state-dir` 改写。

## IP 自动发现与已配对设备选择

日常 `list` / `sync` / `watch` 可省略 `--host`。先尝试认证成功的缓存地址；失效后向 `239.255.255.250:10150/UDP` 发送原生 QUERY，按配对设备 ID 筛选 ANNOUNCE，再用原配对密钥认证。仍无可用连接时，在当前 LAN 的小子网逐地址发送同一 QUERY。使用回复的来源 IP，不使用报文中的虚拟地址。

`addresses.json` 保存在 `--state-dir`，以本机身份 ID 和手机设备 ID 分开记录地址，只有完成认证后才更新。旧身份文件的 host 仅在尚无地址缓存时提供首次连接提示；换 IP 无需重写身份文件或重新 pair。多个已配对手机时必须通过 `--peer` 指定设备 ID；单台自动选择。

```sh
# 示例 ID 需要替换成身份文件 peers 中目标手机的 ID
oconnect-client --identity ./private/identity.json --peer 010203040506 sync
# 指定LAN网卡，关闭子网兜底，仅保留缓存与组播
oconnect-client --identity ./private/identity.json --interface en0 --no-subnet list
```

组播发送3轮，间隔200ms；回复使用每次临时绑定的UDP端口，避免占用官方固定端口。子网兜底从真实网卡地址和掩码计算，每个子网总地址数最多256个，约每10ms发送一个查询；更大网段跳过兜底。默认排除常见VPN、隧道、虚拟与回环网卡，可用 `--interface` 限定网卡。

`--host` 仍可提供优先尝试地址，失败后继续发现；`--port` 仅影响首次 pair 的 SCP 端口，日常文件连接使用 TCP 46888。进度与连接失败原因输出至 stderr，`list` 的 stdout 保持纯 JSON。`watch` 每轮重新获取接口状态，手机离线时保留配对关系并按原间隔重试。

发现消息是候选线索，设备 ID、名称和端口开放都不等于授权。未知手机不自动配对，认证失败的地址不写入缓存。锁屏与休眠可能影响发现，不能保证手机服务关闭或长时间休眠时仍能连接。

## 互联协议研究所用 APK

以下为本项目研究时使用的手机端 APK 样本，包名、版本号和版本代码均从各 APK 的 `AndroidManifest.xml` 核实。

| APK 文件 | 应用名称 | 包名 | 版本号（versionName） | 版本代码（versionCode） |
| --- | --- | --- | --- | --- |
| `AccessoryFramework.apk` | 设备快连 | `com.heytap.accessory` | `17.6.16` | `170060016` |
| `DSF.apk` | DSF | `com.oplus.ndsf` | `17.3.0` | `1700030000` |
| `OShare.apk` | OPPO 互传 | `com.coloros.oshare` | `17.2.36` | `17002036` |

这些版本用于记录研究样本，不代表其他版本已经通过兼容性验证。APK 原件及反编译产物保留在本地研究目录，不随源码或发行包分发。

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

未完成的实机范围包括第二台实体电脑、Windows、独立身份下的手机重启恢复、过夜省电和真实 DHCP 换址恢复。这里只提供录音访问，不提供投屏、远控或剪贴板；不会安装开机启动服务。

## Privacy

This repository is designed to be publishable with only fake examples.

公开源码和测试不包含真实账号、设备身份、录音、官方二进制、抓包或反编译产物。示例中的 `192.0.2.1` 是文档测试地址，不能直接连接真实手机。本项目是独立协议客户端，与相关设备厂商没有隶属关系。

## 许可证

[MIT](LICENSE)。
