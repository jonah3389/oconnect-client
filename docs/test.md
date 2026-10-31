# 验证策略

## 默认离线测试

覆盖身份不覆盖、公私钥一致性、UKEY2 消息承诺与 HKDF、protobuf 截断、TCP 拆包、反向 socket 所有权交接、MAC/加密完整性、CAPEX、Range 边界、短响应不发布、分段重试、内容去重和轮询失败重连。测试只生成临时合成身份，不读取已有用户密钥。

## 显式实机测试

复制 .env.example 的变量名，在本地 shell 中手动设置真实值。只有 OCONNECT_LIVE_TEST=1 才连接手机。

```sh
export OCONNECT_LIVE_TEST=1
export OCONNECT_TEST_HOST=192.0.2.1
export OCONNECT_TEST_IDENTITY=./private/identity.json
export OCONNECT_TEST_OUTPUT=./private/downloads
python -m pytest tests/integration -m live -v
```

测试地址需替换为真实手机地址，身份须提前配对。该测试只连接已授权设备并下载一条录音，不弹出新的配对请求、不删除源文件。需要完整 live 输出时保存到被忽略的私有目录。

## 人工验收

首次配对确认手机 UI 成功；锁屏后新建连接下载文件；空闲后重连；再次同步应跳过原文件。检查文件大小、已知原文件 SHA-256 和 manifest。新设备、重启及长期睡眠是独立验收项，未运行时不得复用其他身份的结果。

## 发行验证

Ruff → 离线测试 → 可选 live → 隐私扫描 → sdist/wheel 构建与内容检查。发行物必须只包含实现与公开文档，不能包含身份、运行状态、音频或逆向原件。

## 地址发现实机验收

新增 `test_roaming_discovery_cache_and_subnet_fallback`：在隔离state中注入一个显式指定的失效地址，验证自动发现并下载最小录音、再次缓存命中且跳过重复下载，再仅模拟组播无应答，保留真实子网查询和认证。测试确认原身份文件逐字节不变，证据保存在输出目录的 `roaming/live_evidence.json`，该目录应放在private下。

除上述live变量外，设置 `OCONNECT_TEST_STALE_HOST` 为确认失效的旧手机地址。可选 `OCONNECT_TEST_PEER` 指定已配对手机ID，`OCONNECT_TEST_INTERFACE` 限定LAN网卡。默认测试仍全部离线；实验不主动改变手机Wi-Fi或DHCP租约。旧下载测试改用filePath哈希命名，不假定目录接口提供fileId。
