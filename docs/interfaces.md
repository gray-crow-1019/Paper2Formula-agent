# 接口契约草案

所有接口均为设计约定，当前尚未实现。资源 ID 由服务器生成，身份来自认证上下文，禁止由请求 owner_id 决定授权。

## REST API

统一前缀 `/api/v1`。

| 方法与路径 | 作用 | 关键返回 |
|---|---|---|
| POST /documents | multipart 上传 PDF/DOCX，流式检查大小和真实类型 | 201：document_id、status |
| GET /documents/{id} | 获取自己文档的元数据 | 类型、版本、处理能力 |
| DELETE /documents/{id} | 撤销下载、取消处理并异步删除关联数据 | 202：deleting |
| POST /documents/{id}/tasks | 创建解析任务；支持 Idempotency-Key | 202：task_id、queued |
| GET /tasks/{id} | 查询进度，首版采用轮询 | status、node、revision、error |
| POST /tasks/{id}/cancel | 请求取消，支持幂等 | 202：cancel_requested |
| POST /tasks/{id}/retry | 重试可恢复失败任务 | 202：task_id；非可重试失败返回 409 |
| GET /tasks/{id}/reviews | 分页读取复核项 | 候选、证据、原因、revision |
| POST /tasks/{id}/reviews/{review_id}/decisions | 提交确认/修订/保留未知 | expected_revision、action、patch、evidence_anchor_ids；201 |
| GET /tasks/{id}/results | 查询指定 published 结果版本 | JSON 摘要、manifest |
| GET /documents/{id}/anchors/{anchor_id} | 获取指定版本的来源位置 | page、bbox、quads、quote、rendition_id |
| GET /tasks/{id}/artifacts/{kind} | 授权下载指定 published 版本 | 302：短时下载链接；kind 为 markdown/json/annotated_pdf |

创建接口的 Idempotency-Key 按用户、路径和请求摘要存储；同 key 不同请求返回 409。复核 revision 冲突返回 409，不能静默覆盖。结果/锚点/下载均要求明确 result_version 或对应版本字段；“最新版本”查询先解析到确定版本再返回。

错误格式：`{"error":{"code":"UNSUPPORTED_SCAN","message":"首版不支持扫描件","retryable":false,"request_id":"req_demo"}}`。常见错误为 INVALID_FILE、ENCRYPTED_DOCUMENT、PAGE_LIMIT、CONVERSION_FAILED、MODEL_TIMEOUT、SOURCE_MISMATCH、REVISION_CONFLICT、EXPORT_FAILED。无权访问的资源统一 404；未认证返回 401，超限返回 413/429，服务暂时不可用返回 503。

## MCP Tools

工具共用 document_id、document_version、request_id；有副作用工具增加 idempotency_key 和 result_version。可信主体来自连接认证或本地受控进程上下文，不是工具参数。返回结构化结果及可读摘要，业务错误通过 MCP 的工具错误语义返回，包含 code 和 retryable。

| 工具 | 输入补充 | 输出 |
|---|---|---|
| inspect_document | document_id | 类型、页数、扫描/加密状态；章节目录若未解析则为 null |
| normalize_document | document_id、converter_config_version | rendition_id、hash、转换元数据 |
| extract_document_blocks | rendition_id、page_range、cursor | blocks、sections、候选、anchors、next_cursor |
| recognize_equations | candidate_ids、recognizer_version | equation 候选、latex、识别质量、anchors |
| find_symbol_mentions | equation_id、symbol、section_id、scope、cursor | 原文候选及排序理由；不直接判定定义成立 |
| get_source_anchor | anchor_id、result_version | 页码、区域、摘录、规范 PDF 版本 |
| build_equation_relations | equation_ids、result_version | 有证据的关系边与歧义 |
| validate_equation | equation_id、result_version | syntax/definitions/units 各自 pass/fail/unknown、reasons |
| create_annotated_pdf | 冻结 result_version | 临时 artifact_id、hash、批注数量 |
| export_markdown_report | 冻结 result_version | 临时 artifact_id、hash |
| export_structured_json | 冻结 result_version、schema_version | 临时 artifact_id、hash |

后三类工具仅生成临时产物，由工作流调用领域服务发布统一 manifest。工具输入不能携带任意文件系统路径、URL 或对象存储 bucket。批量操作设上限；全文通过分块与 cursor 获取，避免工具响应撑满模型上下文。

与原方案相比，补充规范化、公式识别、关系整理和 JSON 导出工具，让全部主流程节点都有明确能力边界；复核与成果发布仍由 API/领域服务控制。

## MCP Resources

资源 URI 保留 `paper://{doc_id}/sections`、`paper://{doc_id}/equations`、`paper://{doc_id}/symbols`，增加 `?version={result_version}` 固定版本。无 version 时返回元数据与当前版本，客户端随后读取固定版本。Resource 与 Tool 使用相同归属检查，资源只读，分页/大内容通过工具读取。

## 输出 JSON 约束

顶层包含 schema_version、document_id、rendition_id、result_version、status、anchors、equations、review_items。事实的 evidence_anchor_ids 必须指向本版本锚点；引用外部版本为错误。符号意义、值、单位分别记录来源，不能用公式本身的坐标冒充参数定义证据。

值保留 raw_value 与 normalized_value，未知为 null；confidence 可为 null，质量说明必须包含 reasons。包含 inferred/derived 的数据应声明前提与推导来源。所有输出通过同一冻结结构生成，示例见 [result.json](../examples/result.json)。
