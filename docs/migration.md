# 从散装目录迁移

新项目维护独立身份和已验证的媒体下载功能。旧散装目录继续保存历史探针、反编译资料和旧账号实验，原有路径保持不变。

本次迁移不复制身份、manifest 或录音。原使用者可以通过 --identity、--state-dir 和 --output 显式指向旧的私有位置；新电脑自行生成全新身份。

根目录 oconnect_client.py 和 independent_identity.py 是薄兼容入口，安装项目后可继续使用原脚本形式。正式入口为 oconnect-client；身份生成也支持 oconnect-identity。

私有工作区若有音频处理管线，使用 --output 指定投放目录，不将工作区绝对路径写进公开源码。旧的 --transport transfer、--adb-port-discovery 与账号认证不属于此项目，仍保留在研究档案。
