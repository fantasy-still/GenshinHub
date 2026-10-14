# 聊天客户端维护规则

本目录是应用代码，不是剧情资料。Python为后端与桌面宿主，静态HTML/CSS/JavaScript为界面。

- 项目编号资料目录与`update_20261009`已冻结，清单在`knowledge.lock.json`。不得为应用开发修改正文、别名、校订清单或`knowledge.sqlite3`，不得运行旧索引build、采集器或阅读层重建。需要资料变更时先取得用户新的明确要求。
- 应用可在`.local/retrieval`内构建与冻结清单对应的检索缓存，校准旧路径和当前正文分块；禁止写回冻结的原索引。缓存完成后以只读方式打开。
- 应用查询知识索引只用SQLite URI `mode=ro&immutable=1`及`query_only`；模型只能调用白名单中的只读工具。
- 聊天、记忆、设置放在`.local`，测试数据放在`test-results`。可读取`DEEPSEEK_API_KEY`环境变量；应用中的密钥仅存在进程内存或操作系统凭据存储，不写进聊天、JSON配置、日志、源码或测试夹具。
- 请求直达`https://api.deepseek.com`。默认用官方`deepseek-flash`别名；更改模型规则前核实官方文档，不凭旧记忆回退模型。
- 保留完整wire消息、`reasoning_content`、工具参数与调用ID。默认省略采样、思考强度和输出上限参数，遵循API默认。不静默裁剪、摘要或丢弃历史。
- 测试：在本目录运行`.venv/Scripts/python.exe -m pytest -q`。界面测试使用`tests/ui_server.py`的隔离模拟接口，不使用用户真实密钥。
- 最后运行`python -m twwiki.freeze verify`，确认锁定数据未改动。新增功能测试应覆盖真实风险，勿用模拟响应冒充线上模型能力验证。

- 默认不使用嵌入、向量服务或额外重排API；用户已明确要求避免这类付费依赖。检索改进应先从词项扩展、标题、现有关系和原文回查着手。
- 专业复核为同一Flash模型的独立审阅与修订；主对话全量保留，审阅输入保存在消息meta中。不得把同模型自查当正确性保证，失败评测也应保留。
