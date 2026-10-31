# 从散装目录迁移

新项目维护独立身份和已验证的媒体下载功能。旧散装目录继续保存历史探针、反编译资料和旧账号实验，原有路径保持不变。

本次迁移不复制身份、manifest 或录音。原使用者可以通过 --identity、--state-dir 和 --output 显式指向旧的私有位置；新电脑自行生成全新身份。

scripts/oconnect_client.py 和 scripts/independent_identity.py 是薄兼容入口，安装项目后可继续使用原脚本形式。正式入口为 oconnect-client；身份生成也支持 oconnect-identity。

私有工作区若有音频处理管线，使用 --output 指定投放目录，不将工作区绝对路径写进公开源码。旧的 --transport transfer、--adb-port-discovery 与账号认证不属于此项目，仍保留在研究档案。


## 从 0.1.x 升级到 0.2.0

在原项目和 Python 3.12 环境中更新代码后，重新执行 `python -m pip install -e .`，安装包含网卡枚举依赖的新版本。保留原有身份文件、state 和录音目录，不需要重新 init 或 pair。

日常 list、sync、watch 命令可以去掉 `--host`，继续使用原来的 `--identity`、`--state-dir` 和 `--output`。旧 host 在没有地址缓存时提供首次连接提示；客户端改为按设备 ID 选择密钥，地址失效后自动发现。认证成功后在 state 下创建 addresses.json，原有 manifest 和录音去重记录继续使用。

只有一台已配对手机时自动选择；多台时增加 `--peer <手机设备ID>`。如果只允许使用某个 LAN 网卡，增加 `--interface <网卡名>`；若不希望执行子网兜底查询，增加 `--no-subnet`。这些选项放在 list、sync 或 watch 子命令之前。

`--host` 仍可作为优先尝试的地址；`--port` 仅用于首次配对的 SCP 端口。连接进度现在写入 stderr，list 的 stdout 保持 JSON，调用脚本应分别处理两者。
